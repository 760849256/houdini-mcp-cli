"""Local-only HTTP service with instance identity and independent liveness."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from . import auth, commands, execution, history, protocol, python_executor, state, tasks

HOST = "127.0.0.1"
DEFAULT_PORT = 0
MAX_BODY_BYTES = 1024 * 1024
_server: "BridgeServer | None" = None
_control_code = globals().get("_control_code") or auth.generate_control_code()


class InstanceMismatch(RuntimeError):
    pass


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                body = b'{"ok":false,"result":{"execution_started":false},"error":{"code":"transport_busy","message":"HTTP capacity is full."}}'
                request.settimeout(0.05)
                request.sendall(b"HTTP/1.0 503 Busy\r\nContent-Type: application/json\r\nContent-Length: "
                                + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


class BridgeServer:
    def __init__(self, host=HOST, port=DEFAULT_PORT, token=None, *, control_code=None):
        if host != HOST:
            raise ValueError("Blib Houdini Bridge only listens on 127.0.0.1.")
        self.host = host
        self.token = token or auth.generate_token()
        self.session_id = auth.instance_id()
        self.control_code = control_code or auth.generate_control_code()
        self.generation = uuid.uuid4().hex
        self.httpd = LocalHTTPServer((host, int(port)), self._handler_class())
        self.thread = None
        self.session_path = None
        self._stopping = threading.Event()
        self._lease_stop = threading.Event()
        self._lease_thread = None
        self._lease_lock = threading.Lock()
        self._probe_timer = None
        self._probe_mode = "unobserved"
        self._last_tick = None
        self._hip_name = ""
        self._houdini_version = ""
        self._context_stamp = None
        self._context_snapshot = None
        self._started = time.time()
        self.tasks = None
        self._task_timer = None
        self._host_ui = False
        self._blocked_reason = None

    @property
    def port(self):
        return int(self.httpd.server_address[1])

    def _install_main_probe(self):
        try:
            import hou
        except ImportError:
            self._probe_mode = "not_applicable"
            return
        if not hou.isUIAvailable():
            self._probe_mode = "not_applicable"
            return
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            try:
                from PySide6 import QtCore
            except ImportError:
                from PySide2 import QtCore
            timer = QtCore.QTimer()
            timer.setInterval(500)
            timer.timeout.connect(self._main_tick)
            timer.start()
            self._probe_timer = timer
            self._probe_mode = "observed"
            self._main_tick()
        except (ImportError, RuntimeError):
            self._probe_mode = "unobserved"

    def _main_tick(self):
        if self._stopping.is_set():
            return
        self._last_tick = time.monotonic()
        try:
            import hou
            self._hip_name = hou.hipFile.basename()
            context = commands.context(hou)
            self._houdini_version = context["application"]["version"]
            self._context_stamp = context["context_token"]
            self._context_snapshot = (context, time.monotonic(), time.time(), state.scene_epoch())
        except (ImportError, AttributeError, RuntimeError):
            pass

    def _heartbeat(self):
        while not self._lease_stop.wait(2.0):
            try:
                with self._lease_lock:
                    if self._lease_stop.is_set():
                        return
                    auth.refresh_session(self.session_path, self.generation, {"hip_name": self._hip_name, "houdini_version": self._houdini_version})
            except OSError:
                self._event("registry_error", reason="heartbeat_write_failed")

    def cached_context(self, payload):
        snapshot = self._context_snapshot
        if snapshot is None:
            return None
        value, sampled, wall_time, epoch = snapshot
        age_ms = (time.monotonic() - sampled) * 1000
        if age_ms > 1000 or epoch != state.scene_epoch():
            return None
        result = dict(value, context_cache={"age_ms": round(age_ms, 3), "sampled_at": wall_time,
                       "revision": value["context_token"], "source": "main_thread_cache"})
        projected = commands._project_response("context", result, payload)
        projected["context_cache"] = result["context_cache"]
        return projected

    def _event(self, event, **fields):
        history.record(dict(event=event, source="server", session_id=self.session_id,
                            generation=self.generation, **fields))

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.info()
        if self._stopping.is_set():
            raise RuntimeError("A stopped BridgeServer cannot be restarted; create a new service generation.")
        self._install_main_probe()
        if self._context_snapshot is None:
            self._main_tick()
        try:
            with auth.control_code_lock():
                if auth.control_code_in_use(self.control_code, self.session_id):
                    raise ValueError("control_code_in_use")
                self.session_path = auth.save_session(
                    self.host, self.port, self.token, pid=os.getpid(),
                    session_id=self.session_id, generation=self.generation,
                    metadata={"version": protocol.BRIDGE_VERSION, "hip_name": self._hip_name,
                              "houdini_version": self._houdini_version, "control_code": self.control_code})
            self._start_tasks()
        except Exception:
            self.stop()
            raise
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.1}, name="BlibHouBridge", daemon=True)
        self.thread.start()
        self._lease_thread = threading.Thread(target=self._heartbeat, name="BlibHouLease", daemon=True)
        self._lease_thread.start()
        self._event("service_started")
        return self.info()

    def stop(self):
        if self._stopping.is_set():
            return
        if any(job["status"] == "running" for job in python_executor.active()):
            raise tasks.TaskError("task_running", "A legacy Python job is running; wait for it before reload.")
        if self.tasks is not None:
            self.tasks.stop()
        self._stopping.set()
        self._lease_stop.set()
        if self._task_timer is not None:
            self._task_timer.stop()
            self._task_timer.deleteLater()
            self._task_timer = None
        if self._probe_timer is not None:
            self._probe_timer.stop()
            self._probe_timer.deleteLater()
            self._probe_timer = None
        if self.thread and self.thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()
        if self._lease_thread:
            self._lease_thread.join(timeout=1.0)
        if self.session_path:
            try:
                with self._lease_lock:
                    auth.retire_session(self.session_path, self.generation)
            except OSError:
                self._event("registry_error", reason="retire_write_failed")
        self._event("service_stopped")

    def info(self):
        return {"running": bool(self.thread and self.thread.is_alive() and not self._stopping.is_set()),
                "host": self.host, "port": self.port, "mode": "edit" if state.edit_enabled() else "read",
                "version": protocol.BRIDGE_VERSION, "session_id": self.session_id,
                "generation": self.generation, "control_code": self.control_code,
                "hip_name": self._hip_name,
                "houdini_version": self._houdini_version,
                "session_path": str(self.session_path or auth.default_session_path()),
                "main_thread": self.liveness()["main_thread"]}

    def liveness(self):
        age = None if self._last_tick is None else max(0, time.monotonic() - self._last_tick)
        main_status = self._probe_mode
        if main_status == "observed":
            main_status = "responsive" if age is not None and age <= 3 else "busy_or_unresponsive"
        return {"status": "stopped" if self._stopping.is_set() else "ok",
                "session_id": self.session_id, "generation": self.generation,
                "version": protocol.BRIDGE_VERSION, "pid": os.getpid(),
                "main_thread": {"status": main_status, "last_tick_age_ms": None if age is None else round(age * 1000, 3)},
                "capabilities": ["instance_binding_v1", "liveness_v1", "local_diagnostics_v1", "durable_tasks_v1"],
                "tasks": {"running": self.tasks._running, "queued": len(self.tasks._queue),
                          "blocked_reason": self._blocked_reason,
                          "storage_error": self.tasks.storage_error} if self.tasks else {},
                "uptime_seconds": round(time.time() - self._started, 3)}

    def _guard(self):
        if self._stopping.is_set():
            raise InstanceMismatch("This service generation stopped before execution; no command was started.")

    def _start_tasks(self):
        try:
            import hou
            self._host_ui = hou.isUIAvailable()
            state.ensure_scene_events(hou)
            self._main_tick()
        except ImportError:
            self._host_ui = False
        self.tasks = tasks.TaskManager(self.session_id, self.generation, self._execute_task)
        if self._host_ui:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("Start the GUI task scheduler on Houdini's main thread.")
            try:
                from PySide6 import QtCore
            except ImportError:
                from PySide2 import QtCore
            self._task_timer = QtCore.QTimer()
            self._task_timer.setInterval(20)
            self._task_timer.timeout.connect(self._drain_tasks)
            self._task_timer.start()
        self.tasks.start(background_consumer=not self._host_ui)

    def _drain_tasks(self):
        try:
            if not self.tasks._queue:
                self._blocked_reason = None
                return
            try:
                from PySide6 import QtWidgets
            except ImportError:
                from PySide2 import QtWidgets
            if QtWidgets.QApplication.activeModalWidget() is not None:
                self._blocked_reason = "modal_dialog"
                return
            self._blocked_reason = None
            self.tasks.drain_one()
        except Exception:
            self._event("task_scheduler_error", reason="journal_unavailable")

    def submit_task(self, command, payload, job_id, queue_timeout_ms=15000, operation_id=None):
        write = command in protocol.EDIT_COMMANDS
        existing = self.tasks.store.get(tasks.normalize_id(job_id))
        can_edit, can_run_python, permission_epoch = state.access_snapshot()
        enabling = command == "edit_mode" and payload.get("enabled") is True
        if (enabling and not can_edit) or (
            existing is None and write and (not can_edit or (command == "python_submit" and not can_run_python))
        ):
            raise execution.TaskRejected("read_only", "Switch out of read-only mode in the Houdini Bridge panel.")
        scene_epoch = state.scene_epoch()
        request_payload = payload
        if command == "edit_batch" and not payload.get("expected_context"):
            from .workflows import needs_context
            if needs_context(payload):
                if not self._context_stamp:
                    raise execution.TaskRejected("context_unavailable", "No recent host context is available; inspect context before editing.")
                payload = dict(payload, expected_context=self._context_stamp)
        def guard():
            self._guard()
            if state.scene_epoch() != scene_epoch:
                raise execution.TaskRejected("scene_changed", "The HIP changed while this task was queued.")
            edit_now, python_now, current_epoch = state.access_snapshot()
            if enabling and not edit_now:
                raise execution.TaskRejected("read_only", "Switch out of read-only mode in the Houdini Bridge panel.")
            if write and (current_epoch != permission_epoch or not edit_now or (command == "python_submit" and not python_now)):
                raise execution.TaskRejected("permission_changed", "Bridge access changed while this task was queued.")
        return self.tasks.submit(command, payload, job_id, queue_timeout_ms, guard, operation_id,
                                 request_payload=request_payload)

    def _edit_preview_payload(self, request):
        if request.get("session_id") and request["session_id"] != self.session_id:
            raise execution.TaskRejected("instance_changed", "Bind the client to the Houdini instance that executed the edit.")
        edit_id = tasks.normalize_id(request["edit_job_id"])
        record = self.tasks.get(edit_id)
        result = (record.get("response") or {}).get("result") or {}
        preview = result.get("preview") or {}
        if record.get("command") != "edit_batch" or record.get("status") != "succeeded" or not result.get("verified") or not preview.get("request"):
            raise execution.TaskRejected("preview_unavailable", "No verified edit with a saved preview request was found for this job ID.")
        if preview.get("scene_epoch") != state.scene_epoch():
            raise execution.TaskRejected("scene_changed", "The original edit belongs to a different Houdini scene.")
        return edit_id, dict(preview["request"])

    def _execute_task(self, command, payload, context):
        context.checkpoint()
        source_edit = None
        if command == "look_batch" and "edit_job_id" in payload:
            source_edit, payload = self._edit_preview_payload(payload)
        if command == "python_submit":
            try:
                return python_executor.execute_inline(payload["code"], commands._import_hou(), context, payload.get("inputs"))
            finally:
                self._main_tick()
        if command in protocol.EDIT_COMMANDS and command not in {"run_plan", "edit_batch"}:
            context.start_effects()
        # A GUI scheduler is already on the main thread. Headless hosts use
        # one consumer, while standalone test dispatch keeps its injection seam.
        try:
            import hou
        except ImportError:
            result = commands.execute_in_houdini(command, payload, before_execute=self._guard)
        else:
            try:
                result = commands.execute(command, payload, hou_module=hou)
            finally:
                self._main_tick()
        if command in {"edit_batch", "inspect_batch", "look_batch"}:
            if source_edit is not None:
                result.setdefault("result", {})["preview_of"] = source_edit
            return result
        if command == "run_plan" and not result["ok"]:
            response = protocol.error_response(context.job_id, command,
                "A plan step failed. Inspect the step results and any changes already applied.",
                "plan_step_failed")
            response["result"] = result
            return response
        return protocol.ok_response(context.job_id, command, result)

    def python_job_status(self, job_id):
        record = self.tasks.get(job_id)
        if record["status"] == "not_found":
            return python_executor.status(job_id)
        result = (record.get("response") or {}).get("result") or {}
        return dict(record, **{k: v for k, v in result.items() if k in {"stdout", "stderr", "error", "data", "data_error"}})

    def _handler_class(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "BlibHouBridge/%s" % protocol.BRIDGE_VERSION

            def setup(self):
                super().setup()
                self.connection.settimeout(3.0)

            def do_GET(self):
                parsed = urlparse(self.path)
                try:
                    protected = parsed.path == "/diagnostics" or parsed.path.startswith("/tasks")
                    supplied = self.headers.get("X-Blib-Bridge-Token")
                    if protected or supplied is not None:
                        if not _token_matches(supplied, bridge.token):
                            raise PermissionError("Invalid bridge token.")
                    self._require_identity()
                    if parsed.path == "/health":
                        result = bridge.liveness()
                    elif parsed.path == "/diagnostics":
                        result = {"connection": bridge.liveness(),
                                  "instances": [auth.public_session(s) for s in auth.discover_sessions()],
                                  "history": history.persisted_snapshot(),
                                  "tasks": [{"job_id": j["job_id"], "status": j["status"], "command": j.get("command")}
                                            for j in bridge.tasks.list(20)]}
                    elif parsed.path == "/tasks":
                        query = parse_qs(parsed.query)
                        limit = int(query.get("limit", ["20"])[0])
                        result = {"jobs": bridge.tasks.list(limit, query.get("active", ["false"])[0] == "true")}
                    elif parsed.path.startswith("/tasks/"):
                        result = bridge.tasks.get(parsed.path.removeprefix("/tasks/"))
                    else:
                        self._send(protocol.error_response("", "", "Not found", code="not_found"), 404)
                        return
                    self._send(protocol.ok_response(protocol.new_request_id(), "health", result))
                except PermissionError as exc:
                    self._send(protocol.error_response("", "", str(exc), "unauthorized"), 401)
                except InstanceMismatch as exc:
                    self._send(protocol.error_response("", "", str(exc), "session_changed"), 409)
                except (tasks.TaskError, ValueError) as exc:
                    self._send(protocol.error_response("", "", str(exc), getattr(exc, "code", "bad_request")), 400)
                except Exception:
                    self._send(protocol.error_response("", "", "Task evidence is temporarily unavailable.", "task_storage_error"), 503)

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path not in {"/rpc", "/tasks"} and not (parsed.path.startswith("/tasks/") and parsed.path.endswith("/cancel")):
                    self._send(protocol.error_response("", "", "Not found", code="not_found"), 404)
                    return
                started = time.perf_counter()
                request_id, command, operation_id = "", "", ""
                response, status = None, 200
                try:
                    raw = self._read_json()
                    self._require_token(raw.get("token"))
                    self._require_identity(raw)
                    bridge._guard()
                    if parsed.path.startswith("/tasks/") and parsed.path.endswith("/cancel"):
                        job_id = parsed.path[len("/tasks/"):-len("/cancel")]
                        result = bridge.tasks.cancel(job_id)
                        response = protocol.ok_response(protocol.new_request_id(), "task_cancel", result)
                    else:
                        request = protocol.parse_request(raw)
                        request_id, command = request["request_id"], request["command"]
                        operation_id = str(uuid.UUID(raw.get("operation_id") or request_id))
                        bridge._event("rpc_received", request_id=request_id, trace_id=request_id,
                                      operation_id=operation_id, command=command)
                        if command == "health" and parsed.path == "/rpc":
                            response = protocol.ok_response(request_id, command, dict(commands.health(), connection=bridge.liveness()))
                        elif command == "context" and parsed.path == "/rpc" and bridge._context_snapshot is not None:
                            cached = bridge.cached_context(request["payload"])
                            if cached is not None:
                                response = protocol.ok_response(request_id, command, cached)
                            else:
                                bridge.submit_task(command, request["payload"], request_id, operation_id=operation_id)
                                response = tasks.task_response(bridge.tasks.wait(request_id, 1500))
                        elif command == "python_job_status" and parsed.path == "/rpc":
                            response = protocol.ok_response(request_id, command, bridge.python_job_status(request["payload"]["job_id"]))
                        elif command in {"manifest", "recipe_manifest", "profile_manifest", "rpc_log"} and parsed.path == "/rpc":
                            response = protocol.ok_response(request_id, command, commands.execute(command, request["payload"]))
                        else:
                            if command in protocol.EDIT_COMMANDS and self.headers.get("X-Blib-Task-Protocol") != "1":
                                raise tasks.TaskError("upgrade_required", "Reload the task-aware MCP/CLI client before submitting scene writes.")
                            if parsed.path == "/tasks" and not (self.headers.get("X-Blib-Session-Id") and self.headers.get("X-Blib-Generation")):
                                raise InstanceMismatch("Task submission requires an explicit instance and generation.")
                            job_id = raw.get("job_id") or (request["payload"].get("job_id") if command == "python_submit" else request_id)
                            wait_ms = raw.get("wait_ms", 0 if parsed.path == "/tasks" else 1500)
                            if type(wait_ms) is not int or not 0 <= wait_ms <= 3000:
                                raise tasks.TaskError("bad_request", "wait_ms must be an integer from 0 to 3000")
                            bridge.submit_task(command, request["payload"], job_id,
                                               raw.get("queue_timeout_ms", 15000), operation_id)
                            job = bridge.tasks.wait(job_id, wait_ms)
                            response = (protocol.ok_response(request_id, "task_submit", job)
                                        if parsed.path == "/tasks" else tasks.task_response(job))
                except protocol.BridgeProtocolError as exc:
                    status = 400
                    response = protocol.error_response(request_id, command, str(exc), "bad_request")
                except PermissionError as exc:
                    status = 401
                    response = protocol.error_response(request_id, command, str(exc), "unauthorized")
                except InstanceMismatch as exc:
                    status = 409
                    response = protocol.error_response(request_id, command, str(exc), "session_changed")
                except execution.TaskRejected as exc:
                    status = 409
                    response = protocol.error_response(request_id, command, str(exc), exc.code)
                    response["result"] = {"execution_started": False}
                except tasks.TaskError as exc:
                    status = 503 if exc.code in {"queue_full", "history_full", "task_running"} else 400
                    response = protocol.error_response(request_id, command, str(exc), exc.code)
                    response["result"] = {"execution_started": False}
                except (ValueError, TypeError) as exc:
                    status = 400
                    response = protocol.error_response(request_id, command, str(exc), "bad_request")
                except Exception as exc:
                    status = 500
                    response = protocol.error_response(request_id, command, str(exc), "command_failed")
                delivered = self._send(response, status)
                bridge._event("rpc_finished", request_id=request_id, trace_id=request_id, operation_id=operation_id,
                              command=command, status=status, ok=bool((response or {}).get("ok")),
                              error_code=((response or {}).get("error") or {}).get("code"),
                              duration_ms=round((time.perf_counter() - started) * 1000, 3),
                              delivery="sent" if delivered else "client_disconnected",
                              serialize_ms=getattr(self, "_serialize_ms", 0.0))

            def log_message(self, format, *args):
                return

            def _read_json(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError as exc:
                    raise protocol.BridgeProtocolError("Invalid Content-Length header.") from exc
                if not 0 < length <= MAX_BODY_BYTES:
                    raise protocol.BridgeProtocolError("Request body must contain at most 1 MiB.")
                try:
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise protocol.BridgeProtocolError("Request body must be valid JSON.") from exc
                if not isinstance(raw, dict):
                    raise protocol.BridgeProtocolError("Request body must be an object.")
                return raw

            def _require_token(self, body_token):
                if not _token_matches(self.headers.get("X-Blib-Bridge-Token"), bridge.token) or not _token_matches(body_token, bridge.token):
                    raise PermissionError("Invalid bridge token.")

            def _require_identity(self, body=None):
                body = body or {}
                for key, header, expected in (
                        ("session_id", "X-Blib-Session-Id", bridge.session_id),
                        ("generation", "X-Blib-Generation", bridge.generation)):
                    for supplied in (body.get(key), self.headers.get(header)):
                        if supplied is not None and supplied != expected:
                            raise InstanceMismatch("Bridge instance or generation changed; inspect context again.")

            def _send(self, payload, status=200):
                began = time.perf_counter()
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self._serialize_ms = round((time.perf_counter() - began) * 1000, 3)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return True
                except OSError:
                    return False

        return Handler


def start_server(port=DEFAULT_PORT, *, read_only=False):
    global _server
    if _server is not None and _server.info()["running"]:
        return _server.info()
    service = BridgeServer(port=port, control_code=_control_code)
    state.set_read_only(read_only)
    try:
        result = service.start()
    except Exception:
        state.set_read_only(True)
        raise
    _server = service
    return result


def stop_server():
    global _server
    if _server is None:
        return False
    _server.stop()
    state.set_read_only(True)
    _server = None
    return True


def set_control_code(value):
    global _control_code
    code = auth.normalize_control_code(value)
    with auth.control_code_lock():
        service = _server
        session_id = service.session_id if service is not None else auth.instance_id()
        if auth.control_code_in_use(code, session_id):
            raise ValueError("control_code_in_use")
        if service is not None:
            with service._lease_lock:
                if not auth.refresh_session(service.session_path, service.generation, {"control_code": code}):
                    raise RuntimeError("Bridge session changed. Refresh the control center and try again.")
                service.control_code = code
        _control_code = code
    return code


def status():
    if _server is None:
        return {"running": False, "host": HOST, "mode": "read", "control_code": _control_code,
                "version": protocol.BRIDGE_VERSION, "session_path": str(auth.default_session_path())}
    return _server.info()


def _token_matches(value: Any, expected: str):
    return isinstance(value, str) and hmac.compare_digest(value, expected)
