"""Real loopback HTTP regressions for instance discovery and failure recovery."""
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import auth, commands, history, protocol, server
from blib_hou_bridge.connection import Connection
from blib_hou_mcp.server import BridgeMCPAdapter


class ConnectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BLIB_HOU_RUNTIME_DIR": self.temp.name})
        self.environment.start()
        self.services = []

    def tearDown(self):
        for service in reversed(self.services):
            service.stop()
        self.environment.stop()
        self.temp.cleanup()

    def service(self, identity=None, port=0):
        service = server.BridgeServer(port=port)
        service.session_id = identity or str(uuid.uuid4())
        service.start()
        self.services.append(service)
        return service

    def test_process_creation_identity_is_stable_and_self_is_alive(self):
        alive, created = auth.process_probe(os.getpid())
        self.assertTrue(alive)
        self.assertTrue(created)
        self.assertEqual(auth.instance_id(), auth.instance_id())
        self.assertEqual(auth.process_probe(0), (False, None))

    def test_dead_reused_expired_and_stopped_descriptors_are_not_available(self):
        record = {"pid": 123, "process_start": "old", "heartbeat_at": time.time()}
        with patch.object(auth, "process_probe", return_value=(False, None)):
            self.assertEqual(auth.availability(record), "process_exited")
        with patch.object(auth, "process_probe", return_value=(True, "new")):
            self.assertEqual(auth.availability(record), "process_reused")
        with patch.object(auth, "process_probe", return_value=(True, "old")):
            self.assertEqual(auth.availability(dict(record, heartbeat_at=time.time() - 100)), "lease_expired")
            self.assertEqual(auth.availability(dict(record, state="stopped")), "stopped")

    def test_multiple_instances_require_explicit_selection(self):
        first, second = self.service(), self.service()
        response = Connection().snapshot()
        self.assertEqual(response["state"], "instance_required")
        self.assertEqual(len(response["candidates"]), 2)
        self.assertNotIn(first.token, json.dumps(response))
        selected = Connection(instance=first.session_id).snapshot()
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["session"]["port"], first.port)
        self.assertNotEqual(first.port, second.port)

    def test_stopping_old_instance_preserves_other_registration(self):
        first, second = self.service(), self.service()
        first.stop()
        live = [s for s in auth.discover_sessions() if s["availability"] == "available"]
        self.assertEqual([s["session_id"] for s in live], [second.session_id])
        self.assertEqual(auth.load_session()["session_id"], second.session_id)

    def test_bound_instance_does_not_fall_back_to_another(self):
        first = self.service()
        client = Connection()
        self.assertTrue(client.call("health", {})["ok"])
        second = self.service()
        first.stop()
        response = client.call("health", {})
        self.assertEqual(response["error"]["code"], "instance_offline")
        self.assertEqual(client.bound_instance, first.session_id)
        self.assertTrue(Connection(instance=second.session_id).snapshot()["ok"])

    def test_same_process_service_restart_recovers_reads_and_gates_writes(self):
        identity = str(uuid.uuid4())
        first = self.service(identity)
        client = Connection(instance=identity)
        self.assertTrue(client.call("health", {})["ok"])
        first.stop()
        second = self.service(identity)
        blocked = client.call("set_comment", {"node": "/obj/x", "comment": "do not execute"})
        self.assertEqual(blocked["error"]["code"], "session_changed")
        self.assertFalse(blocked["result"]["executed"])
        self.assertTrue(client.call("health", {})["ok"])
        self.assertEqual(client._last_generation, second.generation)

    def test_operation_keeps_original_generation_when_registry_changes(self):
        identity = str(uuid.uuid4())
        first = self.service(identity)
        client = Connection(instance=identity)
        with client.operation():
            self.assertTrue(client.call("health", {})["ok"])
            first.stop()
            second = self.service(identity)
            response = client.call("health", {})
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "session_changed")
            self.assertNotEqual(client._local.session["generation"], second.generation)
        self.assertTrue(client.call("health", {})["ok"])

    def test_busy_health_never_dispatches_or_scans_scene(self):
        service = self.service()
        service._probe_mode = "observed"
        service._last_tick = time.monotonic() - 10
        with patch.object(commands, "execute_in_houdini", side_effect=AssertionError("must not dispatch")):
            client = Connection(instance=service.session_id)
            self.assertEqual(client.snapshot()["state"], "host_busy")
            response = client.call("context", {})
        self.assertEqual(response["error"]["code"], "host_busy")
        self.assertFalse(response["result"]["executed"])

    def test_health_stays_available_during_an_inflight_host_request(self):
        service = self.service()
        entered, release = threading.Event(), threading.Event()
        def waiting(command, payload, **kwargs):
            entered.set()
            release.wait(3)
            return {"done": True}
        with patch.object(commands, "execute_in_houdini", side_effect=waiting):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(Connection(instance=service.session_id).call, "context", {})
                self.assertTrue(entered.wait(2))
                began = time.perf_counter()
                health = Connection(instance=service.session_id).snapshot()
                elapsed = time.perf_counter() - began
                release.set()
                self.assertTrue(future.result(3)["ok"])
        self.assertTrue(health["ok"])
        self.assertLess(elapsed, 0.5)

    def test_wrong_generation_is_rejected_before_dispatch(self):
        service = self.service()
        record = auth.load_session(service.session_path)
        request = protocol.make_request("context", {}, token=service.token)
        request.update(session_id=service.session_id, generation="old")
        with patch.object(commands, "execute_in_houdini", side_effect=AssertionError("must not dispatch")):
            response = Connection()._send(record, request)
        self.assertEqual(response["error"]["code"], "session_changed")

    def test_write_failure_is_never_retried(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        with patch.object(client, "_send", return_value={"ok": False, "result": {}, "error": {"code": "unreachable"}}) as send:
            result = client.call("set_comment", {"node": "/obj/x", "comment": "test"})
        self.assertFalse(result["ok"])
        self.assertEqual(send.call_count, 1)

    def test_timed_out_write_is_not_claimed_unexecuted(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        with patch.object(client, "_send", return_value={"ok": False, "result": {}, "error": {"code": "timeout_unknown"}}):
            result = client.call("set_comment", {"node": "/obj/x", "comment": "test"})
        self.assertEqual(result["error"]["code"], "timeout_unknown")
        self.assertNotEqual(result["result"].get("executed"), False)

    def test_transient_read_retries_once_on_identical_instance(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        responses = [{"ok": False, "error": {"code": "unreachable"}}, {"ok": True, "result": {}}]
        with patch.object(client, "_send", side_effect=responses) as send:
            self.assertTrue(client.call("context", {})["ok"])
        self.assertEqual(send.call_count, 2)

    def test_registry_replacement_during_custom_batch_cannot_retarget(self):
        records = iter([{"host": "127.0.0.1", "port": 1, "token": "a"},
                        {"host": "127.0.0.1", "port": 2, "token": "b"}])
        seen = []
        adapter = BridgeMCPAdapter(session_loader=lambda _: next(records),
                                   poster=lambda s, r: seen.append(s["port"]) or {"ok": True, "result": {}})
        with adapter._connection.operation():
            adapter._rpc("health", {})
            adapter._rpc("health", {})
        self.assertEqual(seen, [1, 1])

    def test_persistent_diagnostics_exclude_payload_token_and_error_text(self):
        service = self.service()
        marker = "PRIVATE-CODE-AND-TOKEN"
        history.record({"event": "rpc_finished", "source": "server", "generation": service.generation,
                        "command": "python_submit", "payload": marker, "code": marker,
                        "token": marker, "error_message": marker, "ok": False})
        result = Connection(instance=service.session_id).diagnostics()
        encoded = json.dumps(result)
        self.assertNotIn(marker, encoded)
        self.assertNotIn(service.token, encoded)
        self.assertIn("rpc_finished", encoded)

    def test_logging_failure_does_not_change_command_success(self):
        service = self.service()
        with patch.object(Path, "open", side_effect=OSError("disk unavailable")):
            result = history.record({"event": "test", "ok": True})
        self.assertTrue(result["ok"])

    def test_rotating_logs_remain_bounded(self):
        with patch.object(history, "MAX_LOG_BYTES", 256):
            for index in range(100):
                history.record({"event": "test", "source": "client", "reason": "x" * 100, "status": index})
        paths = list((Path(self.temp.name) / "logs").glob("client-*.jsonl*"))
        self.assertLessEqual(len(paths), 3)
        self.assertLess(sum(p.stat().st_size for p in paths), 2000)

    def test_atomic_writes_do_not_share_a_temporary_name(self):
        path = Path(self.temp.name) / "concurrent.json"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: auth.atomic_json(path, {"index": index}), range(80)))
        self.assertIn("index", json.loads(path.read_text()))
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_discovery_ignores_corrupt_descriptor(self):
        service = self.service()
        (auth.runtime_dir() / "instances" / "corrupt.json").write_text("{bad")
        self.assertEqual(len(auth.discover_sessions()), 1)
        self.assertEqual(auth.discover_sessions()[0]["session_id"], service.session_id)

    def test_stopped_generation_guard_rejects_delayed_command(self):
        service = self.service()
        service.stop()
        with self.assertRaises(server.InstanceMismatch):
            commands.execute_in_houdini("health", {}, before_execute=service._guard)

    def test_lease_renews_and_stops_after_service_stop(self):
        service = self.service()
        before = auth.load_session(service.session_path)["heartbeat_at"]
        self.assertTrue(auth.refresh_session(service.session_path, service.generation))
        after = auth.load_session(service.session_path)["heartbeat_at"]
        self.assertGreater(after, before)
        service.stop()
        self.assertFalse(auth.refresh_session(service.session_path, service.generation))
        self.assertEqual(auth.load_session(service.session_path)["state"], "stopped")

    def test_diagnostic_resource_and_connection_query_are_scene_free(self):
        service = self.service()
        adapter = BridgeMCPAdapter(instance=service.session_id)
        with patch.object(commands, "execute_in_houdini", side_effect=AssertionError("no scene query")):
            resource = adapter.read_resource("houdini://connection/current")
            diagnostics = adapter.read_resource("houdini://diagnostics/current")
        self.assertTrue(json.loads(resource["contents"][0]["text"])["ok"])
        self.assertNotIn(service.token, json.dumps(diagnostics))


    def test_client_registration_preserves_instance_and_profile_arguments(self):
        from blib_hou_bridge import codex_setup
        path = Path(self.temp.name) / "client.toml"
        script = str(ROOT / "scripts" / "cli" / "blib_hou_mcp.py")
        codex_setup.ensure_codex_mcp_registered(path, sys.executable, script)
        text = path.read_text().replace('args = [', 'args = [').replace(
            '\n]', '\n  "--instance", "chosen-instance", "--tool-profile", "advanced",\n]')
        path.write_text(text)
        result = codex_setup.ensure_codex_mcp_registered(path, sys.executable, script)
        self.assertFalse(result["changed"])
        self.assertIn("chosen-instance", path.read_text())
        self.assertIn("advanced", path.read_text())

    def test_cli_default_discovery_rejects_ambiguous_instances(self):
        import contextlib
        import io
        import blib_hou
        self.service()
        self.service()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = blib_hou.main(["context"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "instance_required")

    def test_client_failure_diagnostics_record_outcome_and_trace(self):
        service = self.service()
        service._probe_mode = "observed"
        service._last_tick = time.monotonic() - 10
        Connection(instance=service.session_id).call("context", {})
        events = history.persisted_snapshot()["events"]
        event = next(item for item in reversed(events) if item.get("event") == "client_rpc")
        self.assertFalse(event["ok"])
        self.assertEqual(event["error_code"], "host_busy")
        self.assertTrue(event["trace_id"])



    def test_liveness_retry_never_sends_scene_command(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        with patch.object(client, "_http", side_effect=[
                {"ok": False, "error": {"code": "timeout_unknown"}},
                {"ok": True, "result": service.liveness()}]) as http:
            with patch.object(client, "_send", return_value={"ok": True, "result": {}}) as send:
                self.assertTrue(client.call("context", {})["ok"])
        self.assertEqual(http.call_count, 2)
        self.assertEqual(send.call_count, 1)

    def test_failed_handshake_is_explicitly_not_submitted(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        with patch.object(client, "_http", return_value={"ok": False, "error": {"code": "timeout_unknown"}}):
            with patch.object(client, "_send", side_effect=AssertionError("must not submit")):
                response = client.call("set_comment", {"node": "/obj/x", "comment": "test"})
        self.assertEqual(response["error"]["code"], "handshake_unavailable")
        self.assertFalse(response["result"]["executed"])



    def test_operation_id_joins_multiple_rpc_events(self):
        service = self.service()
        client = Connection(instance=service.session_id)
        with client.operation():
            self.assertTrue(client.call("health", {})["ok"])
            self.assertTrue(client.call("health", {})["ok"])
        events = [e for e in history.persisted_snapshot()["events"] if e.get("event") == "client_rpc"]
        self.assertEqual(events[-1]["operation_id"], events[-2]["operation_id"])
        self.assertNotEqual(events[-1]["trace_id"], events[-2]["trace_id"])

    def test_failed_start_releases_listener_and_probe(self):
        service = server.BridgeServer()
        with patch.object(auth, "save_session", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                service.start()
        self.assertFalse(service.info()["running"])
        self.assertEqual(service.httpd.socket.fileno(), -1)



    def test_pinned_mcp_rejects_switching_to_another_houdini(self):
        first, second = self.service(), self.service()
        adapter = BridgeMCPAdapter(instance=first.session_id, tool_profile="daily")
        with patch.object(commands, "execute_in_houdini", return_value={"selection": [], "current_network": "/obj"}):
            rejected = adapter.call_tool("houdini_context", {"instance": second.session_id})
            self.assertEqual(rejected["structuredContent"]["error"]["code"], "instance_locked")
            self.assertFalse(adapter.call_tool("houdini_context", {"instance": first.session_id})["isError"])
            self.assertEqual(adapter._connection.bound_instance, first.session_id)

    def test_control_code_replies_and_context_are_scoped_to_each_call(self):
        first, second = self.service(), self.service()
        adapter = BridgeMCPAdapter(tool_profile="daily")
        with patch.object(commands, "execute_in_houdini", return_value={"selection": []}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                replies = [pool.submit(adapter.call_tool, "houdini_context", {"control_code": service.control_code.lower()})
                           for service in (first, second)]
                for service, reply in zip((first, second), replies):
                    result = reply.result()["structuredContent"]["result"]
                    self.assertEqual(result["session_id"], service.session_id)
                    self.assertEqual(result["control_code"], service.control_code)
            self.assertIsNone(adapter._connection.bound_instance)
            without_target = adapter.call_tool("houdini_execute", {"control_code": first.control_code})
            self.assertEqual(without_target["structuredContent"]["error"]["code"], "target_instance_required")
            pinned = BridgeMCPAdapter(instance=first.session_id)
            conflict = pinned.call_tool("houdini_context", {"control_code": second.control_code})
            self.assertEqual(conflict["structuredContent"]["error"]["code"], "instance_locked")

    def test_control_code_reuse_cannot_retarget_an_existing_conversation(self):
        first, second = self.service(), self.service()
        adapter = BridgeMCPAdapter(tool_profile="daily")
        code = first.control_code
        args = {"control_code": code, "target_instance": first.session_id, "code": "raise AssertionError()"}
        first.stop()
        offline = adapter.call_tool("houdini_execute", args)
        self.assertEqual(offline["structuredContent"]["error"]["code"], "control_code_offline")
        self.assertTrue(auth.refresh_session(second.session_path, second.generation, {"control_code": code}))
        stale = adapter.call_tool("houdini_execute", args)
        self.assertEqual(stale["structuredContent"]["error"]["code"], "control_code_changed")

    def test_port_routes_are_isolated_and_reject_reuse_by_another_session(self):
        first, second = self.service(), self.service()
        adapter = BridgeMCPAdapter(tool_profile="daily")
        with patch.object(commands, "execute_in_houdini", return_value={"selection": []}):
            for service in (first, second):
                reply = adapter.call_tool("houdini_context", {"port": service.port})
                self.assertFalse(reply["isError"], reply)
                identity = reply["structuredContent"]["result"]
                self.assertEqual(identity["session_id"], service.session_id)
                self.assertEqual(identity["port"], service.port)
        direct = Connection(port=second.port)
        self.assertTrue(direct.call("health", {})["ok"])
        self.assertEqual(direct.bound_instance, second.session_id)
        write = {"port": first.port, "code": "raise AssertionError()"}
        unsafe = adapter.call_tool("houdini_execute", write)
        self.assertEqual(unsafe["structuredContent"]["error"]["code"], "target_instance_required")
        write["target_instance"] = first.session_id
        first.stop()
        missing = adapter.call_tool("houdini_execute", write)
        self.assertEqual(missing["structuredContent"]["error"]["code"], "port_offline")
        replacement = self.service(port=first.port)
        self.assertNotEqual(replacement.session_id, first.session_id)
        stale = adapter.call_tool("houdini_execute", write)
        self.assertEqual(stale["structuredContent"]["error"]["code"], "port_changed")

    def test_explicit_port_edit_preserves_old_listener_on_conflict_and_keeps_read_only(self):
        occupied = self.service()
        candidate = server.BridgeServer()
        requested_port = candidate.port
        candidate.httpd.server_close()
        with patch.object(server, "_server", None), patch.object(server, "_preferred_port", 0):
            try:
                original = server.start_server(read_only=True)
                client = Connection(instance=original["session_id"], port=original["port"])
                self.assertTrue(client.call("health", {})["ok"])
                with self.assertRaises(OSError):
                    server.set_port(occupied.port)
                self.assertEqual(server.status()["port"], original["port"])
                self.assertEqual(server.status()["generation"], original["generation"])
                self.assertTrue(client.call("health", {})["ok"])
                changed = server.set_port(requested_port)
                self.assertEqual(changed["port"], requested_port)
                self.assertEqual(changed["session_id"], original["session_id"])
                self.assertNotEqual(changed["generation"], original["generation"])
                self.assertEqual(changed["mode"], "read")
                self.assertFalse(client.call("health", {})["ok"])
                new_client = Connection(instance=changed["session_id"], port=requested_port)
                self.assertTrue(new_client.call("health", {})["ok"])
                server.stop_server()
                self.assertEqual(server.start_server()["port"], requested_port)
            finally:
                server.stop_server()

    def test_context_can_explicitly_bind_without_editing_client_configuration(self):
        first, second = self.service(), self.service()
        adapter = BridgeMCPAdapter(tool_profile="daily")
        with patch.object(commands, "execute_in_houdini", return_value={"selection": [], "current_network": "/obj"}):
            ambiguous = adapter.call_tool("houdini_context", {})
            self.assertEqual(ambiguous["structuredContent"]["error"]["code"], "instance_required")
            selected = adapter.call_tool("houdini_context", {"instance": first.session_id})
            self.assertFalse(selected["isError"])
            self.assertEqual(adapter._connection.bound_instance, first.session_id)
            switched = adapter.call_tool("houdini_context", {"instance": second.session_id})
            self.assertFalse(switched["isError"])
            self.assertEqual(adapter._connection.bound_instance, second.session_id)
        schema = next(t for t in adapter.list_tools() if t["name"] == "houdini_context")["inputSchema"]
        self.assertIn("instance", schema["properties"])
        self.assertEqual(len(adapter.list_tools()), 6)


if __name__ == "__main__":
    unittest.main()
