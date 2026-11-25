"""Instance-pinned local transport. Never automatically replay a scene write."""
from __future__ import annotations

import contextlib
import functools
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from . import auth, history, protocol


class ConnectionError(RuntimeError):
    def __init__(self, code, message, candidates=None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []


def failure(code, message, **result):
    return {"ok": False, "result": result, "error": {"code": code, "message": message}}


def scoped(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._connection.operation():
            return method(self, *args, **kwargs)
    return wrapper


class Connection:
    def __init__(self, session_path=None, instance=None, session_loader=None, poster=None, *, control_code=None):
        self.session_path = session_path
        self.bound_instance = instance
        self._pinned_instance = instance
        self.control_code = control_code
        self._loader = session_loader
        self._poster = poster
        self._custom = session_loader is not None or poster is not None
        self._local = threading.local()
        self._last_generation = None
        self._health_cache = {}
        self._state = "disconnected"
        self._state_lock = threading.RLock()
        self._active_operations = 0

    @contextlib.contextmanager
    def operation(self):
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            with self._state_lock:
                self._active_operations += 1
            self._local.session = None
            self._local.operation_id = protocol.new_request_id()
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth -= 1
            if depth == 0:
                self._local.session = None
                with self._state_lock:
                    self._active_operations -= 1

    def _transition(self, state, reason=""):
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        history.record({"event": "connection_state", "source": "client", "status": state,
                        "reason": reason, "session_id": self.bound_instance})

    def bind(self, instance):
        """An explicit context request may select a registered instance."""
        if not isinstance(instance, str) or not instance:
            raise ConnectionError("bad_request", "instance must be a registered instance ID.")
        with self._state_lock:
            if self._pinned_instance is not None and instance != self._pinned_instance:
                raise ConnectionError("instance_locked", "This MCP connection is pinned to a different Houdini instance.")
            if instance == self.bound_instance:
                return
            if getattr(self._local, "session", None) is not None or self._active_operations > 1:
                raise ConnectionError("operation_in_progress", "Select the instance before starting an operation.")
            candidates = auth.discover_sessions(self.session_path)
            selected = next((candidate for candidate in candidates
                             if auth.session_key(candidate) == instance and candidate["availability"] == "available"), None)
            if selected is None:
                raise ConnectionError("instance_offline", "The selected instance is unavailable.",
                                      [auth.public_session(candidate) for candidate in candidates])
            self.bound_instance = instance
            self._last_generation = None
            self._health_cache.clear()
            self._transition("connecting", "explicit_instance_selection")

    def match_control_code(self, code, target_instance=None):
        try:
            code = auth.normalize_control_code(code)
        except ValueError as exc:
            raise ConnectionError("invalid_control_code", str(exc)) from exc
        matches = [record for record in auth.discover_sessions(self.session_path)
                   if record.get("control_code") == code and record["availability"] == "available"]
        if not matches:
            raise ConnectionError("control_code_offline", "This control code has no live Houdini. Check it in the Bridge control center.")
        if len(matches) > 1:
            raise ConnectionError("control_code_ambiguous", "Several Houdinis use this control code. Give them distinct codes.")
        instance = auth.session_key(matches[0])
        if self._pinned_instance is not None and self._pinned_instance != instance:
            raise ConnectionError("instance_locked", "This MCP connection is pinned to a different Houdini instance.")
        if target_instance is not None and target_instance != instance:
            raise ConnectionError("control_code_changed", "The control code now points to a different Houdini. Confirm the intended HIP with houdini_context.")
        return code, instance

    def _resolve(self):
        pinned = getattr(self._local, "session", None)
        if pinned is not None:
            return pinned
        if self._loader is not None:
            session = self._loader(self.session_path)
            if session is None:
                raise ConnectionError("offline", "No active Blib Houdini Bridge session was found. Start the Bridge shelf tool.")
        else:
            candidates = auth.discover_sessions(self.session_path)
            live = [s for s in candidates if s["availability"] == "available"]
            if self.bound_instance:
                matches = [s for s in live if auth.session_key(s) == self.bound_instance
                           and (self.control_code is None or s.get("control_code") == self.control_code)]
                if not matches:
                    known = next((s for s in candidates if auth.session_key(s) == self.bound_instance), None)
                    raise ConnectionError("instance_offline", "The bound Houdini instance is unavailable; no other instance was selected.",
                                          [auth.public_session(s) for s in ([known] if known else candidates)])
                session = matches[0]
            elif len(live) > 1:
                raise ConnectionError("instance_required", "Several Houdini Bridges are available. Ask for the control code shown in the intended window, then call houdini_context with control_code.",
                                      [auth.public_session(s) for s in live])
            elif not live:
                raise ConnectionError("offline", "No live registered Houdini Bridge was found. Start Bridge in the intended Houdini.",
                                      [auth.public_session(s) for s in candidates])
            else:
                session = live[0]
        key = auth.session_key(session)
        with self._state_lock:
            if self.bound_instance is None:
                self.bound_instance = key
            elif key != self.bound_instance:
                raise ConnectionError("instance_changed", "The operation cannot switch Houdini instances.")
        if getattr(self._local, "depth", 0):
            self._local.session = dict(session)
        return session

    @staticmethod
    def _headers(session):
        headers = {"X-Blib-Bridge-Token": session["token"], "X-Blib-Task-Protocol": "1"}
        if session.get("generation"):
            headers.update({"X-Blib-Session-Id": session["session_id"],
                            "X-Blib-Generation": session["generation"]})
        return headers

    def _probe(self, session, force=False):
        key = (auth.session_key(session), session.get("generation"))
        cached = self._health_cache.get(key)
        if not force and cached and time.monotonic() - cached[0] < 1.0:
            return cached[1]
        request = urllib.request.Request("http://%s:%s/health" % (session["host"], session["port"]),
                                         headers=self._headers(session))
        # Only the liveness GET is retried here; no scene command has been sent.
        for attempt, budget in enumerate((0.8, 1.5)):
            response = self._http(request, timeout=budget)
            if response.get("ok") or (response.get("error") or {}).get("code") not in {"unreachable", "timeout_unknown"}:
                break
            if attempt == 0:
                time.sleep(random.uniform(0.03, 0.08))
        if response.get("ok"):
            health = response.get("result", {})
            if session.get("generation") and (
                    health.get("session_id") != session.get("session_id") or
                    health.get("generation") != session.get("generation")):
                return failure("session_changed", "The endpoint belongs to a different service generation.")
            if session.get("generation") and "instance_binding_v1" not in health.get("capabilities", []):
                return failure("incompatible", "The service lacks the required instance-binding capability. Reload Bridge.")
            self._health_cache = {key: (time.monotonic(), response)}
        if (response.get("error") or {}).get("code") == "timeout_unknown":
            return failure("handshake_unavailable", "The service liveness probe did not reply within its budget. No scene command was sent.",
                           executed=False)
        return response

    def _send(self, session, request):
        if self._supports_tasks(session) and request["command"] not in {
                "health", "context", "python_job_status", "manifest", "recipe_manifest", "profile_manifest", "rpc_log"}:
            from .tasks import task_response
            response = self.submit_task(request["command"], request["payload"],
                job_id=request["payload"].get("job_id") if request["command"] == "python_submit" else request["request_id"],
                session=session, preflight=False, wait_ms=1500)
            return task_response(response["result"]) if response.get("ok") else response
        return self._legacy_send(session, request)

    def _legacy_send(self, session, request):
        headers = dict(self._headers(session), **{"Content-Type": "application/json"})
        http_request = urllib.request.Request("http://%s:%s/rpc" % (session["host"], session["port"]),
                                              data=json.dumps(request).encode("utf-8"), headers=headers)
        command = request["command"]
        timeout = 0.8 if command == "python_job_status" else 5.0
        if command in {"geometry_summary", "viewport_screenshot", "network_screenshot", "pane_screenshot", "capture_multiple_panes"}:
            timeout = 30.0
        return self._http(http_request, timeout)

    @staticmethod
    def _http(request, timeout):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read(16 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read(1024 * 1024).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return failure("http_error", "Bridge returned HTTP %s." % exc.code)
        except (socket.timeout, TimeoutError):
            return failure("timeout_unknown", "The reply timed out. The operation may have executed. Do not repeat a write.")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return failure("timeout_unknown", "The reply timed out. Do not repeat a write. Inspect the scene or query the original Python job first.")
            return failure("unreachable", "The bound Houdini Bridge could not be reached.")
        except (ValueError, UnicodeDecodeError):
            return failure("invalid_response", "Bridge returned an invalid or oversized JSON response.")
        except OSError:
            return failure("unreachable", "The Bridge connection was interrupted.")

    def call(self, command, payload):
        began = time.perf_counter()
        session = None
        request = None
        discovery_ms = 0.0
        transport_ms = 0.0
        response = {}
        trace_id = protocol.new_request_id()
        def finish(value):
            nonlocal response
            response = value
            return value
        try:
            session = self._resolve()
            discovery_ms = round((time.perf_counter() - began) * 1000, 3)
            generation = session.get("generation")
            is_write = command in protocol.EDIT_COMMANDS or (command == "edit_mode" and "enabled" in payload)
            if not self._custom:
                if is_write and not generation:
                    return finish(failure("upgrade_required", "Reload Bridge before editing; the legacy service has no instance-binding guarantee.", executed=False))
                if is_write and self._last_generation not in (None, generation):
                    return finish(failure("session_changed", "Bridge restarted. Read the current context before editing.", executed=False))
                health = self._probe(session)
                if not health.get("ok"):
                    self._transition("reconnecting", (health.get("error") or {}).get("code", "probe_failed"))
                    return finish(dict(health, result=dict(health.get("result") or {}, executed=False)))
                main = (health.get("result") or {}).get("main_thread", {})
                if main.get("status") == "busy_or_unresponsive" and command not in {"health", "python_job_status"}:
                    self._transition("host_busy")
                    return finish(failure("host_busy", "Houdini is alive but its main thread has not responded recently. This request was not sent.",
                                   executed=False, retry_after_ms=1000, connection=health["result"]))
            request = protocol.make_request(command, payload, token=session["token"])
            request["request_id"] = trace_id
            request["operation_id"] = getattr(self._local, "operation_id", trace_id)
            if generation:
                request.update(session_id=session["session_id"], generation=generation)
            transport_started = time.perf_counter()
            response = self._poster(session, request) if self._poster else self._send(session, request)
            # A transient read can retry only against the identical descriptor.
            code = (response.get("error") or {}).get("code")
            if not self._custom and not is_write and code == "unreachable":
                time.sleep(random.uniform(0.05, 0.15))
                latest = self._fresh_bound()
                if latest and latest.get("generation") == generation and latest.get("port") == session.get("port") and latest.get("token") == session.get("token"):
                    response = self._send(session, request)
                else:
                    response = failure("session_changed", "Bridge changed during this operation. Start a fresh context read.", executed=False)
            transport_ms = round((time.perf_counter() - transport_started) * 1000, 3)
            code = (response.get("error") or {}).get("code")
            if response.get("ok"):
                self._last_generation = generation
                self._transition("host_busy" if not self._custom and main.get("status") == "busy_or_unresponsive" else "ready")
            elif code in {"unreachable", "unauthorized", "session_changed"}:
                self._health_cache.clear()
                self._transition("reconnecting", code)
            return response
        except ConnectionError as exc:
            self._transition("offline", exc.code)
            return finish(failure(exc.code, str(exc), candidates=exc.candidates, executed=False))
        except protocol.BridgeProtocolError as exc:
            return finish(failure("bad_request", str(exc), executed=False))
        finally:
            elapsed = round((time.perf_counter() - began) * 1000, 3)
            history.record({"event": "client_rpc", "source": "client", "command": command,
                            "trace_id": trace_id, "operation_id": getattr(self._local, "operation_id", trace_id),
                            "request_id": (request or {}).get("request_id"),
                            "session_id": auth.session_key(session) if session else self.bound_instance,
                            "generation": (session or {}).get("generation"),
                            "discovery_ms": discovery_ms, "transport_ms": transport_ms, "duration_ms": elapsed,
                            "ok": bool(response.get("ok")), "error_code": (response.get("error") or {}).get("code")})

    def _fresh_bound(self):
        return next((s for s in auth.discover_sessions(self.session_path)
                     if s["availability"] == "available" and auth.session_key(s) == self.bound_instance
                     and (self.control_code is None or s.get("control_code") == self.control_code)), None)

    @property
    def request_control(self):
        return getattr(self._local, "control", None)

    @contextlib.contextmanager
    def request_scope(self, control):
        previous = getattr(self._local, "control", None)
        self._local.control = control
        try:
            yield
        finally:
            self._local.control = previous

    def _task_request(self, session, method, path, body=None):
        headers = dict(self._headers(session), **{"X-Blib-Task-Protocol": "1"})
        data = None
        if body is not None:
            body = dict(body, token=session["token"])
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request("http://%s:%s%s" % (session["host"], session["port"], path),
                                         data=data, headers=headers, method=method)
        return self._http(request, 2.5)

    def _supports_tasks(self, session):
        cached = self._health_cache.get((auth.session_key(session), session.get("generation")))
        return bool(cached and "durable_tasks_v1" in cached[1].get("result", {}).get("capabilities", []))

    def submit_task(self, command, payload, job_id=None, queue_timeout_ms=15000, wait_ms=1000,
                    session=None, preflight=True):
        from . import tasks
        job_id = tasks.normalize_id(job_id or protocol.new_request_id())
        if type(wait_ms) is not int or not 0 <= wait_ms <= 30000:
            return failure("bad_request", "wait_ms must be an integer from 0 to 30000", execution_started=False)
        try:
            session = session or self._resolve()
            if preflight:
                health = self._probe(session)
                if not health.get("ok"):
                    return dict(health, result=dict(health.get("result") or {}, execution_started=False))
            if not self._supports_tasks(session):
                return failure("upgrade_required", "Reload Bridge to enable durable tasks before submitting this operation.",
                               execution_started=False)
            if command in protocol.EDIT_COMMANDS and self._last_generation not in (None, session.get("generation")):
                return failure("session_changed", "Read the current context after Bridge restarts before submitting a write.",
                               execution_started=False)
            request = protocol.make_request(command, payload, token=session["token"])
            request.update(job_id=job_id, session_id=session["session_id"], generation=session["generation"],
                           operation_id=getattr(self._local, "operation_id", job_id),
                           queue_timeout_ms=queue_timeout_ms, wait_ms=min(wait_ms, 200))
            control = getattr(self._local, "control", None)
            if control is not None:
                control.add_job(self, session, job_id)
                if control.cancel_host.is_set():
                    return self.cancel_task(job_id, session=session)
                if control.stop_waiting.is_set():
                    return failure("request_closed", "Client closed before task submission.", execution_started=False, job_id=job_id)
            response = self._task_request(session, "POST", "/tasks", request)
            if not response.get("ok"):
                if (response.get("error") or {}).get("code") in {"timeout_unknown", "unreachable", "invalid_response"}:
                    response = dict(response, result={"job_id": job_id, "session_id": session["session_id"],
                        "generation": session["generation"], "status": "outcome_unknown",
                        "next_step": "query_same_job_id_before_any_new_write"})
                return response
            self._last_generation = session.get("generation")
            record = response["result"]
            deadline = time.monotonic() + wait_ms / 1000
            interval = 0.03
            while record["status"] not in tasks.TERMINAL and time.monotonic() < deadline:
                if control is not None and control.stop_waiting.is_set():
                    if control.cancel_host.is_set():
                        cancelled = self.cancel_task(job_id, session=session)
                        if cancelled.get("ok"):
                            record = cancelled["result"]
                    break
                time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
                polled = self._task_request(session, "GET", "/tasks/" + job_id)
                if not polled.get("ok"):
                    record = dict(record, poll_error=polled.get("error"))
                    break
                record = polled["result"]
                interval = min(0.2, interval * 1.5)
            return protocol.ok_response(job_id, "task_submit", record)
        except ConnectionError as exc:
            return failure(exc.code, str(exc), candidates=exc.candidates, execution_started=False, job_id=job_id)
        except (protocol.BridgeProtocolError, tasks.TaskError) as exc:
            return failure(getattr(exc, "code", "bad_request"), str(exc), execution_started=False, job_id=job_id)

    def task_status(self, job_id, session_id=None):
        from . import tasks
        try:
            job_id = tasks.normalize_id(job_id)
            if session_id is not None and session_id != self.bound_instance:
                local = tasks.local_status(session_id, job_id)
                if local:
                    return protocol.ok_response(job_id, "task_status", local)
                return failure("not_found", "No historical task evidence exists for that instance.")
            session = self._resolve()
            response = self._task_request(session, "GET", "/tasks/" + job_id)
            if response.get("ok"):
                return response
            local = tasks.local_status(auth.session_key(session), job_id)
            return protocol.ok_response(job_id, "task_status", local) if local else response
        except ConnectionError as exc:
            target = session_id or self.bound_instance
            try:
                local = tasks.local_status(target, job_id) if target else None
            except (ValueError, TypeError, AttributeError):
                local = None
            return protocol.ok_response(job_id, "task_status", local) if local else failure(exc.code, str(exc), candidates=exc.candidates)
        except (tasks.TaskError, ValueError, TypeError, AttributeError) as exc:
            return failure("bad_request", str(exc))

    def cancel_task(self, job_id, session=None):
        from . import tasks
        try:
            job_id = tasks.normalize_id(job_id)
            session = session or self._resolve()
            return self._task_request(session, "POST", "/tasks/" + job_id + "/cancel", {})
        except (ConnectionError, tasks.TaskError) as exc:
            return failure(exc.code, str(exc))

    def list_tasks(self, limit=20, active_only=False):
        try:
            session = self._resolve()
            return self._task_request(session, "GET", "/tasks?limit=%s&active=%s" % (
                max(1, min(int(limit), 100)), "true" if active_only else "false"))
        except (ConnectionError, ValueError, TypeError) as exc:
            return failure(getattr(exc, "code", "bad_request"), str(exc))

    def snapshot(self):
        try:
            session = self._resolve()
            health = self._probe(session, force=True) if not self._custom else {"ok": True, "result": {}}
            code = (health.get("error") or {}).get("code")
            if health.get("ok"):
                main = health.get("result", {}).get("main_thread", {}).get("status")
                state = "host_busy" if main == "busy_or_unresponsive" else "ready"
            else:
                state = "reconnecting"
            self._transition(state, code or "")
            return {"ok": bool(health.get("ok")), "state": state,
                    "session": auth.public_session(session), "health": health}
        except ConnectionError as exc:
            self._transition("offline", exc.code)
            return {"ok": False, "state": exc.code, "error": {"code": exc.code, "message": str(exc)},
                    "candidates": exc.candidates}

    def diagnostics(self):
        return {"schema": 1, "collected_at": time.time(), "connection": self.snapshot(),
                "instances": [auth.public_session(s) for s in auth.discover_sessions(self.session_path)],
                "history": history.persisted_snapshot(),
                "note": "Local diagnostic metadata only; credentials, Python source and error messages are omitted."}
