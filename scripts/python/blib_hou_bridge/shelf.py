"""Shelf entrypoint for starting and stopping the local bridge."""

from __future__ import annotations

import importlib

from . import auth, commands, inspector, protocol, server, state


def toggle_server() -> dict:
    running = server.status().get("running")
    if not running:
        importlib.reload(protocol)
        importlib.reload(auth)
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
        message = (
            "Blib Houdini Bridge is running in read-only mode.\n"
            "Host: {host}\n"
            "Port: {port}\n"
            "Session: {session_path}"
        ).format(**result)

    if hou is not None:
        hou.ui.displayMessage(message)
    return result


def set_edit_mode(enabled: bool = True) -> bool:
    state.set_edit_enabled(enabled)
    try:
        import hou  # type: ignore
    except ImportError:
        hou = None
    message = "Blib Houdini Bridge edit mode: %s" % ("ON" if enabled else "OFF")
    if hou is not None:
        hou.ui.displayMessage(message)
    return state.edit_enabled()


def show_inspector():
    if not server.status().get("running"):
        importlib.reload(protocol)
        importlib.reload(auth)
        importlib.reload(state)
        importlib.reload(commands)
        importlib.reload(server)
        server.start_server()
    importlib.reload(inspector)
    return inspector.show()


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
        state.set_edit_enabled(True)
        return {"running": True, "edit_enabled": True, "message": "Blib Houdini Bridge edit mode: ON"}
    if choice == 2:
        state.set_edit_enabled(False)
        return {"running": True, "edit_enabled": False, "message": "Blib Houdini Bridge edit mode: OFF"}
    if choice == 3:
        server.stop_server()
        importlib.reload(protocol)
        importlib.reload(auth)
        importlib.reload(state)
        importlib.reload(commands)
        importlib.reload(server)
        result = server.start_server()
        return {
            "running": True,
            "edit_enabled": state.edit_enabled(),
            "message": "Blib Houdini Bridge reloaded.\nHost: {host}\nPort: {port}".format(**result),
        }
    if choice == 4:
        server.stop_server()
        return {"running": False, "message": "Blib Houdini Bridge stopped."}
    return {"running": True, "edit_enabled": state.edit_enabled(), "message": ""}
