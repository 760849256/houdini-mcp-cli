"""Qt inspector for the local Blib Houdini Bridge."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import auth, codex_setup, commands, history, protocol, server, state

try:
    from blib_core.qt import QtCore, QtGui, QtWidgets, main_window
    from blib_core.ui_theme import apply_blib_theme
except Exception:  # noqa: BLE001 - standalone Bridge does not include blib_core.
    apply_blib_theme = None
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        try:
            from PySide2 import QtCore, QtGui, QtWidgets
        except ImportError:
            QtCore = None
            QtWidgets = None

    def main_window():
        try:
            import hou
            return hou.qt.mainWindow()
        except Exception:  # noqa: BLE001 - no graphical Houdini in tests.
            return None


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
    _window.refresh()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


if QtWidgets is not None:

    class BridgeInspectorDialog(QtWidgets.QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Blib Bridge 控制中心")
            self.resize(570, 255)
            if apply_blib_theme is not None:
                apply_blib_theme(self, "QDialog")
            self._build_ui()
            self.refresh()

        def _build_ui(self) -> None:
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(22, 20, 22, 18)
            layout.setSpacing(12)

            title = QtWidgets.QLabel("Blib Bridge")
            title.setStyleSheet("font-size: 20px; font-weight: 600;")
            title.setFixedHeight(28)
            layout.addWidget(title)
            self.status_label = QtWidgets.QLabel("")
            self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.status_label.setStyleSheet("color: #a3a3a3;")
            self.status_label.setFixedHeight(20)
            layout.addWidget(self.status_label)

            panel = QtWidgets.QFrame()
            panel.setStyleSheet("QFrame#bridgePanel { background: #303030; border: 1px solid #484848; border-radius: 8px; }")
            panel.setObjectName("bridgePanel")
            rows = QtWidgets.QVBoxLayout(panel)
            rows.setContentsMargins(16, 10, 16, 10)
            rows.setSpacing(4)
            controls = QtWidgets.QHBoxLayout()
            self.server_state = QtWidgets.QLabel()
            controls.addWidget(self.server_state)
            controls.addStretch(1)
            self.server_button = QtWidgets.QPushButton()
            controls.addWidget(self.server_button)
            self.read_only = QtWidgets.QCheckBox("只读")
            self.read_only.setToolTip("只读时，场景修改和 Python 执行都会暂停接收。")
            controls.addWidget(self.read_only)
            rows.addLayout(controls)
            code_row = QtWidgets.QHBoxLayout()
            code_row.addWidget(QtWidgets.QLabel("控制代码"))
            self.control_code = QtWidgets.QLineEdit()
            self.control_code.setAccessibleName("控制代码")
            self.control_code.setMaxLength(32)
            self.control_code.setToolTip("每个 Houdini 生成独立代码。可改成 3–32 位字母、数字、下划线或短横线，以字母或数字开头。")
            code_row.addWidget(self.control_code, 1)
            self.save_code_button = QtWidgets.QPushButton("保存")
            self.save_code_button.clicked.connect(self._save_control_code)
            self.control_code.returnPressed.connect(self._save_control_code)
            code_row.addWidget(self.save_code_button)
            self.copy_code_button = QtWidgets.QPushButton("复制给 AI")
            self.copy_code_button.clicked.connect(self._copy_control_code)
            code_row.addWidget(self.copy_code_button)
            self.control_code.textEdited.connect(self._refresh_status)
            rows.addLayout(code_row)
            layout.addWidget(panel)

            self.setup_notice = QtWidgets.QLabel("")
            self.setup_notice.setWordWrap(True)
            self.setup_notice.setStyleSheet("color: #e6bc72;")
            self.setup_notice.hide()
            layout.addWidget(self.setup_notice)

            footer = QtWidgets.QHBoxLayout()
            self.ai_access_button = QtWidgets.QPushButton("AI 接入")
            self.ai_access_button.clicked.connect(lambda: self._open_ai_access())
            footer.addWidget(self.ai_access_button)
            self.connection_test_button = QtWidgets.QPushButton("测试连接")
            self.connection_test_button.clicked.connect(lambda: self._open_ai_access(auto_test=True))
            footer.addWidget(self.connection_test_button)
            self.preview_button = QtWidgets.QToolButton()
            self.preview_button.setText("预览")
            self.preview_button.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
            self.preview_button.clicked.connect(lambda: self._capture_preview({"mode": "viewport"}))
            previews = QtWidgets.QMenu(self.preview_button)
            for label, arguments in (
                ("当前视口", {"mode": "viewport"}),
                ("选中节点", {"mode": "viewport", "target": "@selected"}),
                ("当前网络", {"mode": "network", "path": "@network", "fit": True}),
                ("相机", {"mode": "camera"}),
                ("三视角", {"mode": "angles"}),
                ("关键帧", {"mode": "frames"}),
            ):
                previews.addAction(label).triggered.connect(
                    lambda checked=False, args=arguments: self._capture_preview(args))
            self.preview_button.setMenu(previews)
            footer.addWidget(self.preview_button)
            footer.addStretch(1)
            self.details_button = QtWidgets.QPushButton("详细信息")
            self.details_button.setCheckable(True)
            footer.addWidget(self.details_button)
            layout.addLayout(footer)

            details = QtWidgets.QHBoxLayout()
            self.refresh_button = QtWidgets.QPushButton("刷新")
            self.diagnostics_button = QtWidgets.QPushButton("导出诊断")
            self.diagnostics_button.clicked.connect(self._export_diagnostics)
            details.addWidget(self.refresh_button)
            details.addWidget(self.diagnostics_button)
            details.addStretch(1)
            self.details_controls = QtWidgets.QWidget()
            self.details_controls.setLayout(details)
            self.details_controls.hide()
            layout.addWidget(self.details_controls)

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
            self.tabs.hide()

            self.details_button.toggled.connect(self._toggle_details)
            self.refresh_button.clicked.connect(self.refresh)
            self.server_button.clicked.connect(self._toggle_bridge)
            self.read_only.toggled.connect(self._set_read_only)
            self.network_table.itemSelectionChanged.connect(self._show_selected_node_details)
            self._status_timer = QtCore.QTimer(self)
            self._status_timer.setInterval(2000)
            self._status_timer.timeout.connect(self._refresh_status)
            self._status_timer.start()

        def _toggle_details(self, expanded: bool) -> None:
            self.tabs.setVisible(expanded)
            self.details_controls.setVisible(expanded)
            self.details_button.setText("隐藏详情" if expanded else "详细信息")
            self.resize(900 if expanded else 570, 680 if expanded else 255)
            if expanded:
                self.refresh()

        def _capture_preview(self, arguments) -> None:
            self.preview_button.setEnabled(False)
            self.hide()
            QtCore.QTimer.singleShot(75, lambda: self._show_preview(arguments))

        def _show_preview(self, arguments) -> None:
            try:
                response = commands.execute("look_batch", arguments)
                result = response.get("result") or {}
                image = result.get("sheet") or result
                if not image.get("path") or not (response.get("ok") or result.get("sheet")):
                    raise ValueError((response.get("error") or {}).get("message", "未获得可用预览"))
                picture = QtGui.QPixmap(image["path"])
                if picture.isNull():
                    raise ValueError("预览图无法打开")
            except Exception as exc:
                self.set_setup_notice(f"预览失败 · {exc}")
                return
            finally:
                self.preview_button.setEnabled(True)
                self.show()
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Houdini 预览")
            layout = QtWidgets.QVBoxLayout(dialog)
            label = QtWidgets.QLabel()
            label.setPixmap(picture.scaled(920, 600, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            layout.addWidget(label)
            summary = result.get("summary")
            if isinstance(summary, dict):
                caption = [str(summary[key]) for key in ("target", "view", "frame") if summary.get(key) is not None]
                layout.addWidget(QtWidgets.QLabel(" · ".join(caption)))
            if result.get("capture_source") == "network_graph":
                layout.addWidget(QtWidgets.QLabel("按当前节点和连线绘制的网络图"))
            if not response["ok"]:
                layout.addWidget(QtWidgets.QLabel("部分视图捕获失败"))
            dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            dialog.show()

        def _open_ai_access(self, auto_test=False) -> None:
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("AI 接入")
            dialog.resize(640, 400)
            layout = QtWidgets.QVBoxLayout(dialog)
            client = QtWidgets.QComboBox()
            for key, label in codex_setup.MCP_CLIENTS.items():
                client.addItem(label, key)
            client.setAccessibleName("MCP 客户端")
            layout.addWidget(client)
            instances = QtWidgets.QComboBox()
            instances.setAccessibleName("目标 Houdini")
            instances.addItem("所有实例 · 按控制代码匹配", None)
            current = server.status().get("session_id")
            server_names = {None: codex_setup.SERVER_NAME}
            for record in auth.discover_sessions():
                item = auth.public_session(record)
                hip = Path(item.get("hip_name") or "未命名 HIP").name
                label = f"Houdini {item.get('houdini_version', '')} · {hip} · PID {item.get('pid', '-')}"
                instance_id = item["session_id"]
                if instance_id == current:
                    label += "（当前）"
                if item.get("availability") != "available":
                    label += "（离线）"
                instances.addItem(label, instance_id)
                server_names[instance_id] = codex_setup.instance_server_name(instance_id, hip, item.get("pid"))
            layout.addWidget(instances)
            name_row = QtWidgets.QHBoxLayout()
            name_row.addWidget(QtWidgets.QLabel("MCP 名称"))
            mcp_name = QtWidgets.QLineEdit(codex_setup.SERVER_NAME)
            mcp_name.setAccessibleName("MCP 名称")
            mcp_name.setMaxLength(64)
            mcp_name.setEnabled(False)
            name_row.addWidget(mcp_name, 1)
            layout.addLayout(name_row)
            guide = QtWidgets.QLabel()
            guide.setWordWrap(True)
            guide.setTextFormat(QtCore.Qt.PlainText)
            guide.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            layout.addWidget(guide)
            preview = self._read_only_text()
            preview.setAccessibleName("MCP 配置")
            layout.addWidget(preview, 1)

            buttons = QtWidgets.QHBoxLayout()
            register = QtWidgets.QPushButton("注册到 Codex")
            copy = QtWidgets.QPushButton("复制配置")
            close = QtWidgets.QPushButton("关闭")
            test = QtWidgets.QPushButton("测试连接")
            buttons.addWidget(register)
            buttons.addWidget(test)
            buttons.addStretch(1)
            buttons.addWidget(copy)
            buttons.addWidget(close)
            layout.addLayout(buttons)

            def refresh_client():
                key = client.currentData()
                target = instances.currentData()
                register.setVisible(key == "codex" and target is None)
                try:
                    preview.setPlainText(codex_setup.client_config_preview(key, target, mcp_name.text().strip()))
                    copy.setEnabled(True)
                except (OSError, ValueError) as exc:
                    preview.clear()
                    copy.setEnabled(False)
                    guide.setText(f"无法生成 MCP 配置。{exc}")
                    return
                if target is not None:
                    destination = "Codex 的 mcp_servers" if key == "codex" else "客户端的 mcpServers"
                    guide.setText(f"将 {mcp_name.text().strip()} 作为独立条目合并到{destination}。已锁定所选 Houdini 实例。")
                elif key == "codex":
                    try:
                        status = codex_setup.codex_registration_status()
                        label = "已在配置中找到 Bridge" if status["registered"] else "尚未注册 Bridge"
                        guide.setText(f"{label}\n{status['config_path']}")
                    except (OSError, UnicodeError) as exc:
                        guide.setText(f"无法读取 Codex 配置。{exc}")
                elif key == "antigravity":
                    guide.setText("在 Antigravity IDE 打开 MCP Servers → Manage MCP Servers → View raw config。"
                                  "将 Bridge 条目合并到 mcpServers，然后保存。")
                else:
                    guide.setText("将 Bridge 条目合并到客户端配置的 mcpServers。客户端须支持本地 stdio。")

            def register_codex():
                from . import shelf
                result = shelf._ensure_codex_setup()
                guide.setText(shelf._codex_message(result))

            def copy_config():
                QtWidgets.QApplication.clipboard().setText(preview.toPlainText())
                copy.setText("已复制")

            def change_client():
                copy.setText("复制配置")
                refresh_client()

            def change_instance():
                target = instances.currentData()
                mcp_name.setEnabled(target is not None)
                mcp_name.setText(server_names[target])
                change_client()

            client.currentIndexChanged.connect(change_client)
            instances.currentIndexChanged.connect(change_instance)
            mcp_name.textChanged.connect(change_client)
            register.clicked.connect(register_codex)
            copy.clicked.connect(copy_config)
            close.clicked.connect(dialog.accept)
            test.clicked.connect(lambda: self._probe_client(dialog, guide, test, instances.currentData()))
            refresh_client()
            if auto_test:
                QtCore.QTimer.singleShot(0, test.click)
            execute = getattr(dialog, "exec", None) or dialog.exec_
            execute()

        def _probe_client(self, dialog, label, button, instance) -> None:
            process = QtCore.QProcess(dialog)
            timeout = QtCore.QTimer(process)
            timeout.setSingleShot(True)
            button.setEnabled(False)
            label.setText("正在启动 MCP 适配器…")
            pending = True

            def finish(message):
                nonlocal pending
                if not pending:
                    return
                pending = False
                timeout.stop()
                button.setEnabled(True)
                label.setText(message)
                process.closeWriteChannel()

            def send(method, identifier=None, params=None):
                message = {"jsonrpc": "2.0", "method": method}
                if identifier is not None:
                    message.update(id=identifier, params=params or {})
                process.write((json.dumps(message) + "\n").encode("utf-8"))

            def receive():
                while pending and process.canReadLine():
                    try:
                        reply = json.loads(bytes(process.readLine()))
                    except (ValueError, UnicodeError):
                        finish("MCP 适配器返回了无效数据。检查 Python 启动命令。")
                        return
                    if reply.get("id") == 1:
                        if reply.get("result", {}).get("serverInfo", {}).get("name") != codex_setup.SERVER_NAME:
                            finish("MCP 握手失败。")
                            return
                        label.setText("MCP 握手成功。正在检查 Houdini…")
                        send("notifications/initialized")
                        send("resources/read", 2, {"uri": "houdini://connection/current"})
                    elif reply.get("id") == 2:
                        try:
                            result = json.loads(reply["result"]["contents"][0]["text"])
                        except (KeyError, IndexError, TypeError, ValueError):
                            finish("MCP 握手成功，连接状态读取失败。")
                            return
                        session = result.get("session") or {}
                        state_code = result.get("state")
                        if state_code == "ready" and session.get("session_id"):
                            hip = Path(session.get("hip_name") or "未命名 HIP").name
                            finish(f"MCP 已连接 · Houdini {session.get('houdini_version', '')} · {hip} · PID {session.get('pid', '-')}")
                        elif state_code == "host_busy":
                            finish("MCP 握手成功 · Houdini 主线程忙碌。")
                        elif state_code == "instance_required":
                            finish("MCP 握手成功 · 检测到多个 Houdini，请选择目标实例。")
                        else:
                            finish(f"MCP 握手成功 · Houdini 未就绪（{state_code or 'offline'}）。")

            def cancel():
                if process.state() != QtCore.QProcess.NotRunning:
                    process.kill()
                    process.waitForFinished(500)

            process.started.connect(lambda: send("initialize", 1, {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "Blib Houdini Bridge inspector", "version": "1"},
            }))
            process.readyReadStandardOutput.connect(receive)
            process.errorOccurred.connect(lambda *_: finish("MCP 启动失败 · " + process.errorString()))
            process.finished.connect(lambda *_: finish("MCP 适配器在完成检查前退出。"))
            timeout.timeout.connect(lambda: (finish("MCP 连接测试超时。"), cancel()))
            dialog.finished.connect(lambda *_: cancel())
            try:
                program, arguments = codex_setup.client_command(instance)
                process.start(program, arguments)
                timeout.start(12000)
            except (OSError, ValueError) as exc:
                finish(f"MCP 启动失败 · {exc}")

        def _export_diagnostics(self):
            import uuid
            bundle = {"schema": 1, "collected_at": time.time(), "service": server.status(),
                      "instances": [auth.public_session(item) for item in auth.discover_sessions()],
                      "history": history.persisted_snapshot()}
            path = auth.runtime_dir() / "diagnostics" / ("bridge-" + uuid.uuid4().hex[:12] + ".json")
            try:
                auth.atomic_json(path, bundle)
                self.set_setup_notice("诊断已保存：" + str(path))
            except OSError as exc:
                self.set_setup_notice("诊断保存失败：" + str(exc))

        def _refresh_status(self) -> None:
            self._update_status({"server": server.status(), "health": commands.health()})

        def set_setup_notice(self, message: str) -> None:
            self.setup_notice.setText(str(message))
            self.setup_notice.setVisible(bool(message))

        def refresh(self) -> None:
            if not self.details_button.isChecked():
                self._refresh_status()
                return
            snapshot = build_snapshot()
            self._snapshot = snapshot
            self._update_status(snapshot)
            self.overview_text.setPlainText(format_overview(snapshot))
            self.raw_text.setPlainText(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
            self._fill_network(snapshot.get("network") or {})
            self._fill_commands(snapshot.get("manifest") or {})
            self._fill_rpc_log(snapshot.get("rpc_log") or {})
            self._show_selected_node_details()

        def _toggle_bridge(self) -> None:
            if server.status().get("running"):
                try:
                    server.stop_server()
                except Exception as exc:
                    self.set_setup_notice(str(exc))
                    return
            else:
                try:
                    server.start_server()
                except (OSError, ValueError) as exc:
                    self._show_code_error(exc)
                    return
                from . import shelf
                setup = shelf._ensure_codex_setup()
                if setup.get("changed") or not setup.get("ok"):
                    self.set_setup_notice(shelf._codex_message(setup))
            self.refresh()

        def _show_code_error(self, error) -> None:
            self.set_setup_notice({
                "control_code_in_use": "此控制代码已被另一个 Houdini 使用，请换一个代码。",
            }.get(str(error), f"Bridge 设置失败 · {error}"))

        def _save_control_code(self) -> None:
            try:
                code = server.set_control_code(self.control_code.text())
            except ValueError as exc:
                if str(exc) == "control_code_in_use":
                    self._show_code_error(exc)
                else:
                    self.set_setup_notice(self.control_code.toolTip())
                return
            except (OSError, RuntimeError) as exc:
                self._show_code_error(exc)
                return
            self.control_code.setText(code)
            self.control_code.setModified(False)
            self.set_setup_notice("")
            self._refresh_status()

        def _copy_control_code(self) -> None:
            info = server.status()
            if not info["running"] or self.control_code.isModified():
                return
            code = info["control_code"]
            hip = Path(info.get("hip_name") or "未命名 HIP").name
            QtWidgets.QApplication.clipboard().setText(
                f"使用 Houdini 控制代码 {code}（窗口中的 HIP 为 {hip}）。"
                f"先调用 houdini_context 并传入 control_code={code} 核对 HIP。"
                "后续操作沿用这个 control_code，并把查到的 session_id 作为 target_instance。")
            self.copy_code_button.setText("已复制")
            QtCore.QTimer.singleShot(1500, lambda: self.copy_code_button.setText("复制给 AI"))

        def _set_read_only(self, enabled: bool) -> None:
            state.set_read_only(enabled)
            self._refresh_status()

        def _update_status(self, snapshot: dict[str, Any]) -> None:
            info = snapshot.get("server") or {}
            is_running = bool(info.get("running"))
            editable = is_running and state.edit_enabled()
            hip = Path(info.get("hip_name") or "未命名 HIP").name
            main_status = (info.get("main_thread") or {}).get("status")
            suffix = " · 主线程忙碌" if main_status == "busy_or_unresponsive" else ""
            self.status_label.setText(f"{hip}{suffix}" if is_running else "本地服务未运行")
            mode = "可编辑" if editable else "只读"
            self._set_state_label(self.server_state, is_running, f"服务运行中 · {mode}", "已停止")
            self.server_button.setText("停止" if is_running else "启动")
            previous = self.read_only.blockSignals(True)
            self.read_only.setChecked(is_running and not editable)
            self.read_only.blockSignals(previous)
            self.read_only.setEnabled(is_running)
            if not self.control_code.isModified():
                self.control_code.setText(info.get("control_code", ""))
            self.save_code_button.setEnabled(self.control_code.isModified())
            self.copy_code_button.setEnabled(is_running and not self.control_code.isModified())

        def _set_state_label(self, label, enabled, on_text, off_text):
            label.setText(on_text if enabled else off_text)
            label.setStyleSheet("font-weight: 600; color: %s;" % ("#78d6a2" if enabled else "#aeb9c7"))

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
