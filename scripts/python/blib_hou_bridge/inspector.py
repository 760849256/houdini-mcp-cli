"""Qt inspector for the local Blib Houdini Bridge."""

from __future__ import annotations

import json
import time
from typing import Any

from . import commands, protocol, server, state

try:
    from blib_core.qt import QtCore, QtWidgets, main_window
    from blib_core.ui_theme import apply_blib_theme, set_button_role
except Exception:  # noqa: BLE001 - tests and hython may not have Qt.
    QtCore = None
    QtWidgets = None
    main_window = None
    apply_blib_theme = None
    set_button_role = None


_window = None


def build_snapshot(hou_module: Any | None = None) -> dict[str, Any]:
    """Collect read-only bridge and scene state for the inspector."""
    snapshot: dict[str, Any] = {
        "collected_at": time.time(),
        "server": server.status(),
        "health": commands.health(),
        "manifest": commands.manifest(),
        "rpc_log": commands.rpc_log({"limit": 50}),
        "context": None,
        "selected": None,
        "network": None,
        "errors": [],
    }
    hou = hou_module or _try_import_hou()
    if hou is None:
        snapshot["errors"].append({"command": "hou", "message": "Houdini module is not available."})
        return snapshot

    for command, payload in (("context", {}), ("selected", {})):
        try:
            snapshot[command] = commands.execute(command, payload, hou_module=hou)
        except Exception as exc:  # noqa: BLE001 - inspector should stay open.
            snapshot["errors"].append({"command": command, "message": str(exc)})

    current_network = (snapshot.get("context") or {}).get("current_network")
    if current_network:
        try:
            snapshot["network"] = commands.execute("network", {"path": current_network}, hou_module=hou)
        except Exception as exc:  # noqa: BLE001 - network panes can point at unusual roots.
            snapshot["errors"].append({"command": "network", "message": str(exc)})
    return snapshot


def show() -> Any:
    """Show or raise the singleton Bridge Inspector dialog."""
    if QtWidgets is None:
        raise RuntimeError("Qt is not available in this Houdini Python environment.")
    global _window
    if _window is None or not _is_alive(_window):
        parent = main_window() if main_window is not None else None
        _window = BridgeInspectorDialog(parent)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


if QtWidgets is not None:

    class BridgeInspectorDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Blib Houdini Bridge Inspector")
            self.resize(920, 680)
            if apply_blib_theme is not None:
                apply_blib_theme(self, "QDialog")
            self._build_ui()
            self.refresh()

        def _build_ui(self) -> None:
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)

            self.status_label = QtWidgets.QLabel("")
            self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            layout.addWidget(self.status_label)

            button_row = QtWidgets.QHBoxLayout()
            self.refresh_button = QtWidgets.QPushButton("Refresh")
            self.start_button = QtWidgets.QPushButton("Start")
            self.stop_button = QtWidgets.QPushButton("Stop")
            self.edit_on_button = QtWidgets.QPushButton("Edit On")
            self.edit_off_button = QtWidgets.QPushButton("Edit Off")
            for button in (
                self.refresh_button,
                self.start_button,
                self.stop_button,
                self.edit_on_button,
                self.edit_off_button,
            ):
                if set_button_role is not None:
                    set_button_role(button)
                button_row.addWidget(button)
            button_row.addStretch(1)
            layout.addLayout(button_row)

            self.tabs = QtWidgets.QTabWidget()
            self.overview_text = self._read_only_text()
            self.network_table = QtWidgets.QTableWidget(0, 4)
            self.network_table.setHorizontalHeaderLabels(("Path", "Type", "Display", "Render"))
            self.network_table.horizontalHeader().setStretchLastSection(True)
            self.node_detail_text = self._read_only_text()
            network_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            network_splitter.addWidget(self.network_table)
            network_splitter.addWidget(self.node_detail_text)
            network_splitter.setStretchFactor(0, 3)
            network_splitter.setStretchFactor(1, 2)
            self.commands_table = QtWidgets.QTableWidget(0, 3)
            self.commands_table.setHorizontalHeaderLabels(("Command", "Permission", "Description"))
            self.commands_table.horizontalHeader().setStretchLastSection(True)
            self.rpc_log_table = QtWidgets.QTableWidget(0, 6)
            self.rpc_log_table.setHorizontalHeaderLabels(("Time", "Command", "OK", "Status", "Duration", "Error"))
            self.rpc_log_table.horizontalHeader().setStretchLastSection(True)
            self.raw_text = self._read_only_text()
            self.tabs.addTab(self.overview_text, "Overview")
            self.tabs.addTab(network_splitter, "Network")
            self.tabs.addTab(self.commands_table, "Commands")
            self.tabs.addTab(self.rpc_log_table, "RPC Log")
            self.tabs.addTab(self.raw_text, "Raw")
            layout.addWidget(self.tabs, 1)

            self.refresh_button.clicked.connect(self.refresh)
            self.start_button.clicked.connect(self._start_bridge)
            self.stop_button.clicked.connect(self._stop_bridge)
            self.edit_on_button.clicked.connect(lambda: self._set_edit_mode(True))
            self.edit_off_button.clicked.connect(lambda: self._set_edit_mode(False))
            self.network_table.itemSelectionChanged.connect(self._show_selected_node_details)

        def refresh(self) -> None:
            snapshot = build_snapshot()
            self._snapshot = snapshot
            self._update_status(snapshot)
            self.overview_text.setPlainText(format_overview(snapshot))
            self.raw_text.setPlainText(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
            self._fill_network(snapshot.get("network") or {})
            self._fill_commands(snapshot.get("manifest") or {})
            self._fill_rpc_log(snapshot.get("rpc_log") or {})
            self._show_selected_node_details()

        def _start_bridge(self) -> None:
            if not server.status().get("running"):
                server.start_server()
            self.refresh()

        def _stop_bridge(self) -> None:
            server.stop_server()
            self.refresh()

        def _set_edit_mode(self, enabled: bool) -> None:
            state.set_edit_enabled(enabled)
            self.refresh()

        def _update_status(self, snapshot: dict[str, Any]) -> None:
            info = snapshot.get("server") or {}
            health = snapshot.get("health") or {}
            mode = health.get("mode", "read")
            running = "running" if info.get("running") else "stopped"
            text = "Bridge {running} | {host}:{port} | mode: {mode} | version: {version}".format(
                running=running,
                host=info.get("host", "127.0.0.1"),
                port=info.get("port", "-"),
                mode=mode,
                version=info.get("version", protocol.BRIDGE_VERSION),
            )
            self.status_label.setText(text)

        def _fill_network(self, network: dict[str, Any]) -> None:
            nodes = network.get("nodes") or []
            display_nodes = set(network.get("display_nodes") or [])
            render_nodes = set(network.get("render_nodes") or [])
            self.network_table.setRowCount(len(nodes))
            for row, node in enumerate(nodes):
                path = node.get("path", "")
                values = (
                    path,
                    node.get("type", ""),
                    "yes" if path in display_nodes else "",
                    "yes" if path in render_nodes else "",
                )
                for column, value in enumerate(values):
                    self.network_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            self.network_table.resizeColumnsToContents()

        def _show_selected_node_details(self) -> None:
            items = self.network_table.selectedItems()
            if not items:
                self.node_detail_text.setPlainText("")
                return
            path_item = self.network_table.item(items[0].row(), 0)
            path = path_item.text() if path_item is not None else ""
            if not path:
                self.node_detail_text.setPlainText("")
                return
            hou = _try_import_hou()
            if hou is None:
                self.node_detail_text.setPlainText("Houdini module is not available.")
                return
            payload = {"path": path}
            try:
                details = {
                    "node_info": commands.execute("node_info", payload, hou_module=hou),
                    "node_parms": commands.execute("node_parms", payload, hou_module=hou),
                }
                self.node_detail_text.setPlainText(format_node_details(details))
            except Exception as exc:  # noqa: BLE001 - keep inspector usable.
                self.node_detail_text.setPlainText(str(exc))

        def _fill_commands(self, manifest: dict[str, Any]) -> None:
            items = sorted((manifest.get("commands") or {}).items())
            self.commands_table.setRowCount(len(items))
            for row, (name, metadata) in enumerate(items):
                values = (name, metadata.get("permission", ""), metadata.get("description", ""))
                for column, value in enumerate(values):
                    self.commands_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            self.commands_table.resizeColumnsToContents()

        def _fill_rpc_log(self, rpc_log: dict[str, Any]) -> None:
            events = list(reversed(rpc_log.get("events") or []))
            self.rpc_log_table.setRowCount(len(events))
            for row, event in enumerate(events):
                timestamp = event.get("timestamp")
                time_text = time.strftime("%H:%M:%S", time.localtime(timestamp)) if timestamp else ""
                values = (
                    time_text,
                    event.get("command", ""),
                    "yes" if event.get("ok") else "no",
                    event.get("status", ""),
                    event.get("duration_ms", ""),
                    event.get("error_code") or "",
                )
                for column, value in enumerate(values):
                    self.rpc_log_table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
            self.rpc_log_table.resizeColumnsToContents()

        def _read_only_text(self):
            widget = QtWidgets.QPlainTextEdit()
            widget.setReadOnly(True)
            widget.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            return widget

else:
    BridgeInspectorDialog = None


def format_overview(snapshot: dict[str, Any]) -> str:
    info = snapshot.get("server") or {}
    health = snapshot.get("health") or {}
    context = snapshot.get("context") or {}
    selected = snapshot.get("selected") or {}
    network = snapshot.get("network") or {}
    manifest = snapshot.get("manifest") or {}
    rpc_log = snapshot.get("rpc_log") or {}
    lines = [
        "Bridge",
        "  Running: %s" % bool(info.get("running")),
        "  Endpoint: %s:%s" % (info.get("host", "127.0.0.1"), info.get("port", "-")),
        "  Mode: %s" % health.get("mode", "read"),
        "  Edit enabled: %s" % bool(health.get("edit_enabled")),
        "  Session: %s" % info.get("session_path", ""),
        "",
        "Scene",
        "  HIP: %s" % context.get("hip_path", ""),
        "  Current network: %s" % context.get("current_network", ""),
        "  Frame: %s" % ((context.get("timeline") or {}).get("current")),
        "  Selected nodes: %s" % selected.get("count", 0),
        "  Network nodes: %s" % network.get("node_count", 0),
        "",
        "Protocol",
        "  Version: %s" % manifest.get("version", protocol.BRIDGE_VERSION),
        "  Commands: %s" % len(manifest.get("commands") or {}),
        "  Danger commands reserved: %s" % len(manifest.get("danger_commands") or []),
        "",
        "RPC",
        "  Recent events: %s" % rpc_log.get("count", 0),
    ]
    errors = snapshot.get("errors") or []
    if errors:
        lines.extend(["", "Errors"])
        for error in errors:
            lines.append("  %s: %s" % (error.get("command", ""), error.get("message", "")))
    return "\n".join(lines)


def format_node_details(details: dict[str, Any]) -> str:
    info = details.get("node_info") or {}
    parms = details.get("node_parms") or {}
    flags = info.get("flags") or {}
    messages = info.get("messages") or {}
    lines = [
        "Node",
        "  Path: %s" % info.get("path", ""),
        "  Type: %s" % info.get("type", ""),
        "  Category: %s" % info.get("category", ""),
        "  Parent: %s" % info.get("parent", ""),
        "",
        "Flags",
        "  Display: %s" % flags.get("display"),
        "  Render: %s" % flags.get("render"),
        "  Bypass: %s" % flags.get("bypass"),
        "  Selected: %s" % flags.get("selected"),
        "",
        "Connections",
        "  Inputs: %s" % _compact_paths(info.get("inputs") or []),
        "  Outputs: %s" % _compact_paths(info.get("outputs") or []),
        "",
        "Messages",
        "  Errors: %s" % (messages.get("errors") or []),
        "  Warnings: %s" % (messages.get("warnings") or []),
        "",
        "Parameters (%s)" % parms.get("parm_count", 0),
    ]
    for parm in (parms.get("parms") or [])[:80]:
        value = parm.get("expression") or parm.get("value")
        lines.append("  {name}: {value}".format(name=parm.get("name", ""), value=value))
    return "\n".join(lines)


def _compact_paths(items: list[Any]) -> list[str]:
    paths = []
    for item in items:
        if item is None:
            paths.append("")
        elif isinstance(item, dict):
            paths.append(str(item.get("path", "")))
        else:
            paths.append(str(item))
    return paths


def _try_import_hou() -> Any | None:
    try:
        import hou  # type: ignore

        return hou
    except ImportError:
        return None


def _is_alive(widget: Any) -> bool:
    try:
        widget.windowTitle()
        return True
    except RuntimeError:
        return False
