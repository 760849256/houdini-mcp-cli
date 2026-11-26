"""Exercise the real stdio -> HTTP -> isolated Houdini path, never a user's session.

Use --houdini for GUI validation or --hython for headless validation. The child
uses an empty preference directory and a private Bridge registry.
"""
from __future__ import annotations
import argparse
import json
import os
import queue
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import auth
from blib_hou_bridge.connection import Connection

FIXTURE = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, PYTHON_ROOT)
import hou
root = Path(CONTROL_ROOT)
version = ".".join(map(str, hou.applicationVersion()[:2]))
expected_prefs = (root / ("houdini" + version)).resolve()
actual_prefs = Path(hou.homeHoudiniDirectory()).resolve()
if actual_prefs != expected_prefs:
    error = f"Fixture preferences are {actual_prefs}; expected {expected_prefs}."
    (root / "startup_error.txt").write_text(error, encoding="utf-8")
    raise RuntimeError(error)
from blib_hou_bridge import auth, server, state
service = server.start_server()
def publish():
    auth.atomic_json(root / "ready.json", dict(server.status(), houdini=hou.applicationVersionString(),
                     ui=hou.isUIAvailable(), child_pid=os.getpid(), python=sys.version.split()[0],
                     isolated_preferences=True, python_enabled=state.python_enabled()))
publish()
def control():
    command_path = root / "command.json"
    if not command_path.exists():
        return
    request = json.loads(command_path.read_text())
    command = request["command"]
    command_path.unlink()
    if command == "control_code":
        try:
            server.set_control_code(request["code"])
        except ValueError as error:
            auth.atomic_json(root / "code_error.json", {"message": str(error)})
        publish()
    elif command == "port":
        from unittest.mock import Mock, patch
        from blib_hou_bridge import inspector

        window = inspector.show()
        window.port_edit.selectAll()
        window.port_edit.insert(str(request["port"]))
        window.server_button.click()
        assert not window.more_content.isVisible()
        assert window.copy_connection_button.isVisible()
        clipboard = Mock()
        with patch.object(QtWidgets.QApplication, "clipboard", return_value=clipboard):
            window.copy_connection_button.click()
        auth.atomic_json(root / "port_ui.json", {
            "field": window.port_edit.text(),
            "notice": window.connection_notice.text(),
            "clipboard": clipboard.setText.call_args.args[0],
        })
        window.hide()
        publish()
    elif command == "restart":
        server.stop_server()
        server.start_server()
        publish()
    elif command in {"read_only", "editable"}:
        state.set_read_only(command == "read_only")
        publish()
    elif command == "modal":
        global fixture_dialog
        fixture_dialog = QtWidgets.QDialog()
        fixture_dialog.setModal(True)
        fixture_dialog.setWindowTitle("Bridge isolated modal acceptance")
        fixture_dialog.show()
        (root / "modal_started").write_text("started")
        def finish_modal():
            fixture_dialog.accept()
            (root / "modal_finished").write_text("finished")
        QtCore.QTimer.singleShot(2500, finish_modal)
    elif command == "stop":
        server.stop_server()
        publish()
    elif command == "busy":
        (root / "busy_started").write_text("started")
        time.sleep(6)
        (root / "busy_finished").write_text("finished")
    elif command == "exit":
        server.stop_server()
        if hou.isUIAvailable():
            fixture_timer.stop()
            hou.ui.postEventCallback(lambda: hou.exit(suppress_save_prompt=True))
        else:
            raise SystemExit(0)
if hou.isUIAvailable():
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    fixture_timer = QtCore.QTimer()
    fixture_timer.setInterval(100)
    fixture_timer.timeout.connect(control)
    fixture_timer.start()
else:
    while True:
        control()
        time.sleep(0.05)
'''


def wait_for(check, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError("Isolated fixture did not reach the expected state.")


class StdioClient:
    def __init__(self, instance, env, stderr):
        args = [sys.executable, "-B", str(ROOT / "scripts" / "cli" / "blib_hou_mcp.py")]
        if instance is not None:
            args.extend(["--instance", instance])
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8", env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.responses = queue.Queue()
        self.sequence = 0
        def read():
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    self.responses.put({"unexpected_stdout": line[:200]})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def request(self, method, params=None, timeout=15):
        return self.request_many([(method, params)], timeout)[0]

    def request_many(self, requests, timeout=15):
        ids = []
        for method, params in requests:
            self.sequence += 1
            ids.append(self.sequence)
            message = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params or {}}
            self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        results = {}
        for _ in ids:
            result = self.responses.get(timeout=timeout)
            if result.get("id") not in ids or result["id"] in results or "error" in result:
                raise AssertionError("MCP protocol response failed: %s" % result)
            results[result["id"]] = result["result"]
        return [results[identifier] for identifier in ids]

    def context(self):
        result = self.request("tools/call", {"name": "houdini_context",
                                             "arguments": {"fields": ["hip_name", "current_network", "context_token"]}})
        assert not result.get("isError"), result
        return result["structuredContent"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(5)
        self.process.stdout.close()


def run(executable, gui, samples, report_path, exercise=None):
    report = {"schema": 1, "scope": "isolated stdio/HTTP/Houdini integration; excludes model latency",
              "production_scenes_touched": False, "gui_requested": gui, "samples": samples}
    children, clients, handles = [], [], []
    old_runtime = os.environ.get("BLIB_HOU_RUNTIME_DIR")
    # All credentials and fixture profiles are private, temporary, and removed after children exit.
    with tempfile.TemporaryDirectory(prefix="blib-connection-acceptance-") as temporary:
        base = Path(temporary)
        os.environ["BLIB_HOU_RUNTIME_DIR"] = str(base / "registry")
        env = os.environ.copy()
        env.update(BLIB_HOU_RUNTIME_DIR=str(base / "registry"), HOUDINI_NO_ENV_FILE="1",
                   HOUDINI_PACKAGE_DIR=str(base / "packages"), HOUDINI_PATH=str(base / "empty") + ";&",
                   HOUDINI_NO_SPLASH="1")
        for name in ("PYTHONHOME", "PYTHONPATH", "HFS", "HSITE"):
            env.pop(name, None)
        for name in ("packages", "empty"):
            (base / name).mkdir()
        def launch(name, use_gui):
            root = base / name
            root.mkdir()
            child_env = dict(env, HOUDINI_USER_PREF_DIR=str(root / "houdini__HVER__"))
            script = root / "fixture.py"
            source = "PYTHON_ROOT = %r\nCONTROL_ROOT = %r\n" % (str(ROOT / "scripts" / "python"), str(root))
            script.write_text(source + FIXTURE, encoding="utf-8")
            output = open(root / "child.log", "w", encoding="utf-8")
            handles.append(output)
            args = [str(executable)]
            if use_gui:
                args += ["-foreground", "waitforui"]
            args.append(str(script))
            startup = None
            if os.name == "nt":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
            process = subprocess.Popen(args, env=child_env, stdout=output, stderr=subprocess.STDOUT, startupinfo=startup)
            children.append((process, root))
            def ready():
                startup_error = root / "startup_error.txt"
                if startup_error.is_file():
                    raise RuntimeError(startup_error.read_text(encoding="utf-8"))
                if process.poll() is not None:
                    raise RuntimeError("Isolated Houdini exited: " + (root / "child.log").read_text(errors="replace")[-2000:])
                try:
                    return json.loads((root / "ready.json").read_text())
                except (OSError, ValueError):
                    return None
            info = wait_for(ready, 90)
            assert info["child_pid"] == process.pid, "Fixture identity differs from the owned subprocess."
            assert info["ui"] == use_gui
            return root, info

        def command(root, name, **arguments):
            auth.atomic_json(root / "command.json", {"command": name, **arguments})

        try:
            root, info = launch("primary", gui)
            report.update(houdini=info["houdini"], ui=info["ui"], python=info.get("python"),
                          bridge=info["version"], isolated_preferences=info["isolated_preferences"])
            stderr = open(base / "mcp.log", "w", encoding="utf-8")
            handles.append(stderr)
            client = StdioClient(info["session_id"], env, stderr)
            clients.append(client)
            initialized = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                        "clientInfo": {"name": "Bridge acceptance", "version": "1"}})
            assert initialized["serverInfo"]["name"] == "blib-houdini-bridge"
            about_uri = "houdini://adapter/about"
            assert about_uri in initialized["instructions"]
            about = client.request("resources/read", {"uri": about_uri})
            description = json.loads(about["contents"][0]["text"])["result"]
            assert description["getting_started"]["first_tool"] == "houdini_context"
            assert description["tool_profile"] == "daily"
            report["mcp_self_description"] = True
            client.context()
            durations = []
            for sample_index in range(samples):
                report["samples_completed"] = sample_index
                began = time.perf_counter()
                client.context()
                durations.append((time.perf_counter() - began) * 1000)
            report["samples_completed"] = samples
            ordered = sorted(durations)
            report["context_ms"] = {"p50": round(statistics.median(ordered), 3),
                                    "p95": round(ordered[min(len(ordered)-1, int(len(ordered)*0.95))], 3),
                                    "max": round(max(ordered), 3)}
            # All edits below are in the owned child's unsaved, isolated scene.
            assert info["mode"] == "edit" and info["python_enabled"]
            edited = client.request("tools/call", {"name": "houdini_execute", "arguments": {
                "code": "node = hou.node('/obj').createNode('geo', bridge_inputs['name'])\nbridge_result = {'path': node.path()}",
                "inputs": {"name": "BRIDGE_ACCEPTANCE"}, "wait_ms": 5000}})
            assert not edited.get("isError"), edited
            assert edited["structuredContent"]["result"]["status"] == "succeeded", edited
            assert edited["structuredContent"]["result"]["data"] == {"path": "/obj/BRIDGE_ACCEPTANCE"}
            report["editable_default_and_custom_python"] = True
            command(root, "read_only")
            wait_for(lambda: json.loads((root / "ready.json").read_text())["mode"] == "read", 5)
            for name, arguments in (
                ("houdini_execute", {"code": "raise AssertionError('unexpected execution')"}),
                ("houdini_edit", {"steps": [{"command": "set_comment", "payload": {
                    "node": "/obj/BRIDGE_ACCEPTANCE", "comment": "unexpected edit"}}]}),
            ):
                rejected = client.request("tools/call", {"name": name, "arguments": arguments})
                assert rejected["isError"] and rejected["structuredContent"]["error"]["code"] == "read_only", rejected
            report["read_only_blocks_both_writes"] = True
            command(root, "editable")
            wait_for(lambda: json.loads((root / "ready.json").read_text())["mode"] == "edit", 5)
            inspected = client.request("tools/call", {"name": "houdini_inspect",
                "arguments": {"kind": "node", "targets": ["/obj/BRIDGE_ACCEPTANCE"]}})
            assert not inspected.get("isError"), inspected
            report["isolated_write_readback"] = True
            if exercise is not None:
                report["task_checks"] = exercise(client, root, info, env, command)
            before = info["generation"]
            command(root, "restart")
            def restarted():
                try:
                    value = json.loads((root / "ready.json").read_text())
                    return value if value["generation"] != before else None
                except (OSError, ValueError):
                    return None
            info = wait_for(restarted, 10)
            began = time.perf_counter()
            client.context()
            report["same_instance_reconnect_ms"] = round((time.perf_counter() - began) * 1000, 3)
            if report.get("task_checks", {}).get("durable_job_id"):
                recovered = client.request("tools/call", {"name": "houdini_job", "arguments": {
                    "job_id": report["task_checks"]["durable_job_id"]}})
                record = recovered["structuredContent"]["result"]
                assert record["status"] == "succeeded", record
                assert record["generation"] != info["generation"], record
                report["completed_task_survives_service_restart"] = True
            if gui:
                command(root, "busy")
                wait_for(lambda: (root / "busy_started").exists(), 5)
                busy_client = Connection(instance=info["session_id"])
                wait_for(lambda: busy_client.snapshot()["state"] == "host_busy", 5)
                began = time.perf_counter()
                busy = client.request("tools/call", {"name": "houdini_context", "arguments": {}})
                assert busy["structuredContent"]["error"]["code"] == "host_busy", busy
                assert busy["structuredContent"]["result"]["executed"] is False
                report["busy_rejection_ms"] = round((time.perf_counter() - began) * 1000, 3)
                wait_for(lambda: (root / "busy_finished").exists(), 8)
                wait_for(lambda: busy_client.snapshot()["state"] == "ready", 5)
                client.context()
                report["busy_recovery"] = True
            # A second actual Houdini process proves registry separation.
            headless = executable.parent / "hython.exe" if os.name == "nt" else executable.parent / "hython"
            if headless.exists():
                original = executable
                executable = headless
                other_root, other_info = launch("secondary", False)
                executable = original
                assert other_info["session_id"] != info["session_id"]
                unbound = Connection().snapshot()
                assert unbound["state"] == "instance_required", unbound
                other_client = StdioClient(other_info["session_id"], env, stderr)
                clients.append(other_client)
                other_client.request("initialize", {})
                for peer, owner, foreign in ((client, info, other_info), (other_client, other_info, info)):
                    context = peer.context()
                    assert context["result"]["session_id"] == owner["session_id"]
                    wrong = peer.request("tools/call", {"name": "houdini_context",
                        "arguments": {"instance": foreign["session_id"]}})
                    assert wrong.get("isError") and wrong["structuredContent"]["error"]["code"] == "instance_locked", wrong
                report["explicit_instance_selection"] = True
                setup = other_client.request("tools/call", {"name": "houdini_execute", "arguments": {
                    "code": "hou.node('/obj').createNode('geo', 'BRIDGE_ACCEPTANCE')", "wait_ms": 5000}})
                assert not setup.get("isError") and setup["structuredContent"]["result"]["status"] == "succeeded", setup

                def edit_bound(peer, comment):
                    reply = peer.request("tools/call", {"name": "houdini_edit", "arguments": {
                        "steps": [{"command": "set_comment", "payload": {
                            "node": "/obj/BRIDGE_ACCEPTANCE", "comment": comment}}], "wait_ms": 5000}})
                    assert not reply.get("isError") and reply["structuredContent"]["result"]["verified"], reply

                with ThreadPoolExecutor(max_workers=2) as pool:
                    edits = [pool.submit(edit_bound, peer, comment)
                             for peer, comment in ((client, "primary owner"), (other_client, "secondary owner"))]
                    for edit in edits:
                        edit.result()
                for peer, comment in ((client, "primary owner"), (other_client, "secondary owner")):
                    proof = peer.request("tools/call", {"name": "houdini_inspect", "arguments": {
                        "targets": ["/obj/BRIDGE_ACCEPTANCE"], "fields": ["path", "comment"], "wait_ms": 5000}})
                    assert proof["structuredContent"]["result"]["results"][0]["result"]["comment"] == comment, proof
                report["simultaneous_writes_isolated"] = True
                assert len(info["control_code"]) == 8 and info["control_code"] != other_info["control_code"]
                command(root, "control_code", code="groom-a")
                wait_for(lambda: json.loads((root / "ready.json").read_text())["control_code"] == "GROOM-A", 8)
                if gui:
                    with socket.socket() as reservation:
                        reservation.bind(("127.0.0.1", 0))
                        new_port = reservation.getsockname()[1]
                    command(root, "port", port=new_port)
                    wait_for(lambda: json.loads((root / "ready.json").read_text())["port"] == new_port, 8)
                    ui = json.loads((root / "port_ui.json").read_text(encoding="utf-8"))
                    assert ui["field"] == str(new_port) and ui["notice"] == ""
                    assert f"port={new_port}" in ui["clipboard"] and "target_instance" in ui["clipboard"]
                    report["control_center_edit_and_copy"] = True
                command(other_root, "control_code", code="groom-a")
                conflict = other_root / "code_error.json"
                wait_for(conflict.exists, 8)
                assert json.loads(conflict.read_text())["message"] == "control_code_in_use"
                command(other_root, "control_code", code="cache-b")
                wait_for(lambda: json.loads((other_root / "ready.json").read_text())["control_code"] == "CACHE-B", 8)
                shared = StdioClient(None, env, stderr)
                clients.append(shared)
                shared.request("initialize", {})
                def tool(name, arguments):
                    return "tools/call", {"name": name, "arguments": arguments}
                codes = ("GROOM-A", "CACHE-B")
                contexts = shared.request_many([tool("houdini_context", {"control_code": code}) for code in codes])
                routes = []
                for code, context, owner in zip(codes, contexts, (info, other_info)):
                    assert not context["isError"], context
                    identity = context["structuredContent"]["result"]
                    assert identity["session_id"] == owner["session_id"] and identity["control_code"] == code
                    routes.append({"control_code": code, "target_instance": identity["session_id"]})
                edits = shared.request_many([tool("houdini_edit", dict(route, wait_ms=5000, steps=[
                    {"command": "set_comment", "payload": {"node": "/obj/BRIDGE_ACCEPTANCE",
                                                          "comment": route["control_code"]}}]))
                    for route in routes])
                for edit in edits:
                    assert not edit["isError"] and edit["structuredContent"]["result"]["verified"], edit
                proofs = shared.request_many([tool("houdini_inspect", dict(route, wait_ms=5000,
                    targets=["/obj/BRIDGE_ACCEPTANCE"], fields=["path", "comment"])) for route in routes])
                for proof, code in zip(proofs, codes):
                    assert proof["structuredContent"]["result"]["results"][0]["result"]["comment"] == code, proof
                report["shared_mcp_code_routing"] = True
                pinned = Connection(instance=info["session_id"])
                assert pinned.snapshot()["ok"]
                command(root, "stop")
                wait_for(lambda: pinned.snapshot().get("state") == "instance_offline", 5)
                offline = client.request("tools/call", {"name": "houdini_context", "arguments": {}})
                assert offline["structuredContent"]["error"]["code"] == "instance_offline", offline
                assert other_client.context()["result"]["session_id"] == other_info["session_id"]
                report["multi_houdini_isolation"] = True
                blocked = shared.request(*tool("houdini_execute", dict(routes[0], code="raise AssertionError()")))
                assert blocked["structuredContent"]["error"]["code"] == "control_code_offline", blocked
                command(other_root, "control_code", code="GROOM-A")
                wait_for(lambda: json.loads((other_root / "ready.json").read_text())["control_code"] == "GROOM-A", 8)
                blocked = shared.request(*tool("houdini_execute", dict(routes[0], code="raise AssertionError()")))
                assert blocked["structuredContent"]["error"]["code"] == "control_code_changed", blocked
                report["stale_code_cannot_retarget"] = True
                command(other_root, "exit")
            report["ok"] = True
        except Exception as exc:
            report.update(ok=False, failure=type(exc).__name__ + ": " + str(exc)[:1500])
            # Snapshot before cleanup distinguishes an unexpected process exit
            # from the fixture's later, intentional shutdown.
            report["children_at_failure"] = [
                {"fixture": child_root.name, "pid": child.pid, "exit_code": child.poll()}
                for child, child_root in children
            ]
            try:
                if (root / "task_progress.json").exists():
                    report["task_progress"] = json.loads((root / "task_progress.json").read_text())
            except (UnboundLocalError, OSError, ValueError):
                pass
            try:
                report["diagnostics"] = Connection(instance=info["session_id"]).diagnostics()
            except Exception as diagnostic_error:
                report["diagnostic_failure"] = type(diagnostic_error).__name__
            raise
        finally:
            for client in clients:
                client.close()
            exits = []
            for process, root in children:
                forced = False
                if process.poll() is None:
                    command(root, "exit")
                    try:
                        process.wait(8)
                    except subprocess.TimeoutExpired:
                        forced = True
                        process.terminate()
                        try:
                            process.wait(8)
                        except subprocess.TimeoutExpired:
                            pass
                exits.append({"fixture": root.name, "pid": process.pid,
                              "exit_code": process.poll(), "forced": forced})
            report["fixture_exits"] = exits
            report["fixture_shutdown_clean"] = bool(exits) and all(
                item["exit_code"] == 0 and not item["forced"] for item in exits
            )
            if report.get("ok") and not report["fixture_shutdown_clean"]:
                report.update(ok=False, failure="Owned fixture shutdown was not clean; inspect fixture_exits.")
            for handle in handles:
                handle.close()
            if not report.get("ok"):
                # Temporary fixture logs otherwise disappear on exit. Keep only
                # bounded tails from this owned, synthetic session. Never copy
                # its registry, tokens, saved scenes, or preferences.
                diagnostic_logs = []
                for log in sorted(base.glob("*.log")) + sorted(base.glob("*/*.log")):
                    try:
                        with log.open("rb") as stream:
                            stream.seek(max(0, log.stat().st_size - 16384))
                            tail = stream.read(16384).decode("utf-8", errors="replace")
                        diagnostic_logs.append({"fixture_log": log.relative_to(base).as_posix(), "tail": tail})
                    except OSError:
                        continue
                report["fixture_log_tails"] = diagnostic_logs[-20:]
            if old_runtime is None:
                os.environ.pop("BLIB_HOU_RUNTIME_DIR", None)
            else:
                os.environ["BLIB_HOU_RUNTIME_DIR"] = old_runtime
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({key: value for key, value in report.items() if key not in {"diagnostics", "fixture_log_tails"}}, indent=2))
    if not report.get("ok"):
        raise RuntimeError(report["failure"])
    return report


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--houdini", type=Path)
    group.add_argument("--hython", type=Path)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.samples < 1:
        parser.error("--samples must be positive")
    run(options.houdini or options.hython, bool(options.houdini), options.samples, options.output)


if __name__ == "__main__":
    main()
