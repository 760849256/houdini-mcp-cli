"""Shelf entrypoint for starting and stopping the local bridge."""

from __future__ import annotations

import importlib

from . import auth, codex_setup, commands, history, inspector, protocol, server, state


def toggle_server() -> dict:
    running = server.status().get("running")
    if not running:
        importlib.reload(protocol)
        importlib.reload(auth)
        importlib.reload(history)
        importlib.reload(codex_setup)
        importlib.reload(state)
        importlib.reload(commands)
        importlib.reload(server)

    try:
        import hou  # type: ignore
    except ImportError:
        hou = None

    status = server.status()
    if status.get("running"):
        result = _running_server_action(hou, status)
        message = result.get("message", "")
    else:
        result = server.start_server()
        setup = _ensure_codex_setup()
        result["codex_setup"] = setup
        result["codex_message"] = _codex_message(setup)
        message = (
            "Blib Houdini Bridge is running in editable mode.\n"
            "Host: {host}\n"
            "Port: {port}\n"
            "Session: {session_path}\n\n"
            "{codex_message}"
        ).format(**result)

    if hou is not None:
        hou.ui.displayMessage(message)
    return result


def set_edit_mode(enabled: bool = True) -> bool:
    state.set_read_only(not enabled)
    try:
        import hou  # type: ignore
    except ImportError:
        hou = None
    message = "Blib Houdini Bridge edit mode: %s" % ("ON" if enabled else "OFF")
    if hou is not None:
        hou.ui.displayMessage(message)
    return state.edit_enabled()


def set_python_mode(enabled: bool = True) -> bool:
    """Compatibility entry point for older shelves."""
    return set_edit_mode(enabled)


def show_inspector():
    setup = None
    code_conflict = False
    if not server.status().get("running"):
        importlib.reload(protocol)
        importlib.reload(auth)
        importlib.reload(history)
        importlib.reload(codex_setup)
        importlib.reload(state)
        importlib.reload(commands)
        importlib.reload(server)
        try:
            server.start_server()
        except ValueError as exc:
            if str(exc) != "control_code_in_use":
                raise
            code_conflict = True
        else:
            setup = _ensure_codex_setup()
    # Keep the existing window alive when the shelf button is clicked again.
    window = getattr(inspector, "_window", None)
    if window is None or not inspector._is_alive(window):
        importlib.reload(inspector)
    window = inspector.show()
    if code_conflict:
        window.set_setup_notice("此控制代码已被另一个 Houdini 使用。请保存新代码，再启动 Bridge。")
    elif setup and (setup.get("changed") or not setup.get("ok")):
        window.set_setup_notice(_codex_message(setup))
    return window


def _ensure_codex_setup() -> dict:
    try:
        return codex_setup.ensure_codex_mcp_registered()
    except Exception as exc:  # noqa: BLE001 - surface setup problems in Houdini UI.
        return {
            "ok": False,
            "changed": False,
            "message": "Codex MCP auto-setup failed: %s" % exc,
        }


def _codex_message(setup: dict | None) -> str:
    if not isinstance(setup, dict):
        return "Codex MCP setup was not checked."
    if setup.get("changed"):
        return "Codex MCP registered. Restart Codex or open a new session."
    return str(setup.get("message") or "Codex MCP setup already exists.")


def _running_server_action(hou, status: dict) -> dict:
    if hou is None:
        server.stop_server()
        return {"running": False, "message": "Blib Houdini Bridge stopped."}

    current_mode = "edit" if state.edit_enabled() else "read-only"
    choice = hou.ui.displayMessage(
        "Blib Houdini Bridge is running.\n"
        "Host: {host}\n"
        "Port: {port}\n"
        "Mode: {mode}".format(
            host=status.get("host", "127.0.0.1"),
            port=status.get("port", "-"),
            mode=current_mode,
        ),
        buttons=("Keep Running", "Edit On", "Edit Off", "Reload", "Stop"),
        default_choice=0,
        close_choice=0,
    )
    if choice == 1:
        state.set_read_only(False)
        return {"running": True, "edit_enabled": True, "message": "Blib Houdini Bridge edit mode: ON"}
    if choice == 2:
        state.set_read_only(True)
        return {"running": True, "edit_enabled": False, "message": "Blib Houdini Bridge edit mode: OFF"}
    if choice == 3:
        read_only = not state.edit_enabled()
        try:
            server.stop_server()
        except Exception as exc:
            return {"running": True, "message": str(exc)}
        importlib.reload(protocol)
        importlib.reload(auth)
        importlib.reload(history)
        importlib.reload(state)
        importlib.reload(commands)
        importlib.reload(server)
        result = server.start_server(read_only=read_only)
        return {
            "running": True,
            "edit_enabled": state.edit_enabled(),
            "message": "Blib Houdini Bridge reloaded.\nHost: {host}\nPort: {port}".format(**result),
        }
    if choice == 4:
        server.stop_server()
        return {"running": False, "message": "Blib Houdini Bridge stopped."}
    return {"running": True, "edit_enabled": state.edit_enabled(), "message": ""}
