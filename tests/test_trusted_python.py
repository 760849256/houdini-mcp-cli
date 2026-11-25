import threading
import os
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import commands, protocol, python_executor, server, state
from blib_hou_mcp.server import BridgeMCPAdapter


class FakeHou:
    pass


class TrustedPythonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"BLIB_HOU_RUNTIME_DIR": self.temporary.name})
        self.environment.start()
        state.set_edit_enabled(False)
        with python_executor._lock:
            python_executor._jobs.clear()
        self.hou = FakeHou()

    def tearDown(self):
        state.set_edit_enabled(False)
        self.environment.stop()
        self.temporary.cleanup()

    def wait_for_terminal(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = commands.execute("python_job_status", {"job_id": job_id})
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_default_denied_and_modes_required(self):
        job_id = str(uuid.uuid4())
        payload = {"job_id": job_id, "code": "print('safe test')"}
        with self.assertRaises(Exception):
            commands.execute("python_submit", payload, hou_module=self.hou)
        state.set_edit_enabled(True)
        with self.assertRaises(commands.BridgeCommandError):
            commands.execute("python_submit", payload, hou_module=self.hou)
        self.assertEqual(python_executor.status(job_id)["status"], "not_found")
        state.set_python_enabled(True)
        commands.execute("python_submit", payload, hou_module=self.hou)
        self.assertEqual(self.wait_for_terminal(job_id)["stdout"].strip(), "safe test")
        state.set_edit_enabled(False)
        self.assertFalse(state.python_enabled())

    def test_deduplication_and_error_reporting(self):
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        job_id = str(uuid.uuid4())
        code = "hou.value = getattr(hou, 'value', 0) + 1\nprint(hou.value)"
        commands.execute("python_submit", {"job_id": job_id, "code": code}, hou_module=self.hou)
        self.assertEqual(self.wait_for_terminal(job_id)["status"], "succeeded")
        commands.execute("python_submit", {"job_id": job_id, "code": code}, hou_module=self.hou)
        self.assertEqual(self.hou.value, 1)
        with self.assertRaises(ValueError):
            commands.execute("python_submit", {"job_id": job_id, "code": "print('different')"}, hou_module=self.hou)
        failed_id = str(uuid.uuid4())
        commands.execute("python_submit", {"job_id": failed_id, "code": "raise ValueError('example')"}, hou_module=self.hou)
        failed = self.wait_for_terminal(failed_id)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("ValueError: example", failed["error"])

    def test_status_remains_available_during_running_job(self):
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        self.hou.gate = threading.Event()
        self.hou.started = threading.Event()
        job_id = str(uuid.uuid4())
        code = "hou.started.set()\nhou.gate.wait(2)"
        try:
            commands.execute("python_submit", {"job_id": job_id, "code": code}, hou_module=self.hou)
            self.assertTrue(self.hou.started.wait(1))
            self.assertEqual(commands.execute_in_houdini("python_job_status", {"job_id": job_id})["status"], "running")
            with self.assertRaises(RuntimeError):
                commands.execute("python_submit", {"job_id": str(uuid.uuid4()), "code": "pass"}, hou_module=self.hou)
        finally:
            self.hou.gate.set()
        self.assertEqual(self.wait_for_terminal(job_id)["status"], "succeeded")

    def test_mcp_http_job_roundtrip(self):
        bridge = server.BridgeServer(port=0, token="test-token")
        bridge.start()
        session = {"host": "127.0.0.1", "port": bridge.port, "token": "test-token"}
        adapter = BridgeMCPAdapter(session_loader=lambda path: session)
        job_id = str(uuid.uuid4())
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        try:
            with patch("blib_hou_bridge.commands._import_hou", return_value=self.hou):
                result = adapter.call_tool("houdini_python_submit", {"job_id": job_id, "code": "hou.value = 42\nprint('done')"})
                self.assertTrue(result["structuredContent"]["ok"])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    response = adapter.call_tool("houdini_python_job_status", {"job_id": job_id})
                    job = response["structuredContent"]["result"]
                    if job["status"] in {"succeeded", "failed", "cancelled"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(job["status"], "succeeded")
                self.assertEqual(job["stdout"].strip(), "done")
                self.assertEqual(self.hou.value, 42)
        finally:
            bridge.stop()

    def test_short_job_returns_completion_in_one_call(self):
        bridge = server.BridgeServer(port=0, token="test-token")
        bridge.start()
        adapter = BridgeMCPAdapter(session_loader=lambda path: {"host": "127.0.0.1", "port": bridge.port, "token": "test-token"})
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        try:
            with patch("blib_hou_bridge.commands._import_hou", return_value=self.hou):
                result = adapter.call_tool("houdini_python_submit", {"job_id": str(uuid.uuid4()), "code": "print('done')", "wait_ms": 1000})
                self.assertEqual(result["structuredContent"]["result"]["status"], "succeeded")
                self.assertEqual(result["structuredContent"]["result"]["stdout"].strip(), "done")
                failed = adapter.call_tool("houdini_python_submit", {"job_id": str(uuid.uuid4()), "code": "raise ValueError('bad')", "wait_ms": 1000})
                self.assertTrue(failed["isError"])
                self.assertEqual(failed["structuredContent"]["result"]["status"], "failed")
        finally:
            bridge.stop()

    def test_no_wait_does_not_poll_or_repeat_submission(self):
        calls = []
        job_id = str(uuid.uuid4())
        def poster(session, request):
            calls.append(request)
            return {"ok": True, "result": {"job_id": job_id, "status": "queued"}}
        adapter = BridgeMCPAdapter(session_loader=lambda path: {"host": "127.0.0.1", "port": 1, "token": "t"}, poster=poster)
        result = adapter.call_tool("houdini_python_submit", {"job_id": job_id, "code": "pass", "wait_ms": 0})
        self.assertEqual(result["structuredContent"]["result"]["status"], "queued")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("wait_ms", calls[0]["payload"])
        invalid = adapter.call_tool("houdini_python_submit", {"job_id": job_id, "code": "pass", "wait_ms": 3000})
        self.assertTrue(invalid["isError"])
        self.assertEqual(len(calls), 1)

    def test_timeout_reports_unknown_outcome(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path: None)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("late")):
            result = adapter._post_to_bridge({"host": "127.0.0.1", "port": 1, "token": "t"}, {"command": "python_submit"})
        self.assertEqual(result["error"]["code"], "timeout_unknown")
        self.assertIn("Do not repeat", result["error"]["message"])

    def test_old_output_compacted_but_job_id_is_not_reexecuted(self):
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        code = "hou.value = getattr(hou, 'value', 0) + 1\nprint(hou.value)"
        with patch("blib_hou_bridge.python_executor._MAX_FULL_RESULTS", 2):
            for job_id in ids:
                commands.execute("python_submit", {"job_id": job_id, "code": code}, hou_module=self.hou)
                self.assertEqual(self.wait_for_terminal(job_id)["status"], "succeeded")
            first = python_executor.status(ids[0])
            self.assertTrue(first["output_expired"])
            self.assertNotIn("stdout", first)
            self.assertEqual(python_executor.status(ids[-1])["stdout"].strip(), "3")
            commands.execute("python_submit", {"job_id": ids[0], "code": code}, hou_module=self.hou)
            self.assertEqual(self.hou.value, 3)

    def test_large_output_is_bounded(self):
        state.set_edit_enabled(True)
        state.set_python_enabled(True)
        job_id = str(uuid.uuid4())
        commands.execute("python_submit", {"job_id": job_id, "code": "print('x' * 100000)"}, hou_module=self.hou)
        self.assertLessEqual(len(self.wait_for_terminal(job_id)["stdout"]), 65536)

    def test_schema_tool_exposure_and_plan_rejection(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path: None)
        names = {t["name"] for t in adapter.list_tools()}
        self.assertIn("houdini_python_submit", names)
        self.assertIn("houdini_python_job_status", names)
        self.assertTrue(adapter.status(include_health=False)["tools"]["policy_contract"]["ok"])
        with self.assertRaises(protocol.BridgeProtocolError):
            protocol.validate_command("python_submit", {"job_id": "bad", "code": "pass"})
        state.set_edit_enabled(True)
        step = {"command": "python_submit", "payload": {"job_id": str(uuid.uuid4()), "code": "pass"}}
        report = commands.validate_plan(self.hou, {"steps": [step]})
        self.assertFalse(report["valid"])
        self.assertIn("cannot run inside", report["steps"][0]["issues"][0])


if __name__ == "__main__":
    unittest.main()
