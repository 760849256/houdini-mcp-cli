"""End-to-end convenience workflows against the existing fake Houdini scene."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import commands, recipes, state
from blib_hou_mcp.server import BridgeMCPAdapter
from tests.test_blib_hou_bridge import FakeHou


class QuickToolsTests(unittest.TestCase):
    def setUp(self):
        self.hou = FakeHou()
        self.calls = []
        state.set_edit_enabled(True)
        self.adapter = BridgeMCPAdapter(session_loader=lambda _: {"token": "t"}, poster=self.poster)

    def tearDown(self):
        state.set_edit_enabled(False)

    def poster(self, session, request):
        self.calls.append(request["command"])
        return {"ok": True, "result": commands.execute(request["command"], request["payload"], hou_module=self.hou)}

    def edit(self, steps, **kwargs):
        return self.adapter.call_tool("houdini_edit", {"steps": steps, **kwargs})

    def test_create_connect_and_readback_in_one_client_call(self):
        result = self.edit([
            {"command": "create_node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "QUICK_OUT"}},
            {"command": "batch_set_parms", "payload": {"node": "/obj/geo1/QUICK_OUT", "values": {"tx": 3}}},
            {"command": "connect", "payload": {"src": "/obj/geo1/OUT", "dst": "/obj/geo1/QUICK_OUT", "input_index": 0}},
            {"command": "set_flags", "payload": {"node": "/obj/geo1/QUICK_OUT", "display": True}},
            {"command": "layout", "payload": {"path": "/obj/geo1"}},
        ])
        self.assertFalse(result["isError"], result)
        self.assertTrue(result["structuredContent"]["result"]["verified"])
        self.assertEqual(self.calls, ["context", "validate_plan", "run_plan", "verify_plan"])
        self.assertEqual(self.hou.node("/obj/geo1/QUICK_OUT").parm("tx").eval(), 3)

    def test_closed_gate_and_invalid_parameter_make_no_changes(self):
        step = {"command": "set_parm", "payload": {"node": "/obj/geo1/OUT", "parm": "tx", "value": 9}}
        state.set_edit_enabled(False)
        self.assertTrue(self.edit([step])["isError"])
        self.assertEqual(self.calls, ["context", "validate_plan"])
        state.set_edit_enabled(True)
        self.calls.clear()
        step["payload"]["parm"] = "missing_parameter"
        self.assertTrue(self.edit([step])["isError"])
        self.assertEqual(self.calls, ["context", "validate_plan"])

    def test_advanced_and_python_commands_rejected_before_rpc(self):
        for command in ["delete_node", "run_plan", "python_submit", "save_hip"]:
            self.assertTrue(self.edit([{"command": command, "payload": {}}])["isError"])
        self.assertFalse(self.calls)

    def test_confirmation_does_not_write_or_auto_approve(self):
        result = self.edit([{"command": "create_node", "payload": {"parent": "/obj/geo1", "type": "filecache", "name": "cache"}}])
        self.assertTrue(result["isError"])
        self.assertEqual(self.calls, ["context", "validate_plan"])
        self.assertIsNone(self.hou.node("/obj/geo1/cache"))

    def test_review_accepts_numeric_batch_and_vector_without_spurious_confirmation(self):
        for step in [
            {"command": "batch_set_parms", "payload": {"node": "/obj/geo1/OUT", "values": {"tx": 3}}},
            {"command": "set_parm", "payload": {"node": "/obj/geo1/OUT", "parm": "t", "value": [1, 2, 3]}},
        ]:
            self.assertEqual(recipes._required_confirmations([step]), [])
        self.assertTrue(recipes._required_confirmations([{"command": "batch_set_parms", "payload": {"values": {"file": "new.bgeo"}}}]))

    def test_partial_failure_is_not_success_and_is_not_retried(self):
        original = commands.set_comment
        def fail_second(hou, payload):
            if payload["comment"] == "fail":
                raise RuntimeError("injected edit failure")
            return original(hou, payload)
        with patch.object(commands, "set_comment", side_effect=fail_second):
            result = self.edit([
                {"command": "set_parm", "payload": {"node": "/obj/geo1/OUT", "parm": "tx", "value": 7}},
                {"command": "set_comment", "payload": {"node": "/obj/geo1/OUT", "comment": "fail"}},
            ])
        self.assertTrue(result["isError"])
        self.assertEqual(self.hou.out.parm("tx").eval(), 7)
        self.assertEqual(self.calls.count("run_plan"), 1)
        self.assertEqual(result["structuredContent"]["result"]["status"], "needs_inspection")

    def test_readback_mismatch_cannot_report_success(self):
        with patch.object(commands, "set_comment", return_value={}):
            result = self.edit([{"command": "set_comment", "payload": {"node": "/obj/geo1/OUT", "comment": "not applied"}}])
        self.assertTrue(result["isError"])
        self.assertFalse(result["structuredContent"]["result"]["verified"])

    def test_execute_generates_recoverable_id_and_waits_internally(self):
        def rpc(command, payload):
            if command == "python_submit":
                self.job_id = payload["job_id"]
                return {"ok": True, "result": {"job_id": self.job_id, "status": "queued"}}
            return {"ok": True, "result": {"job_id": self.job_id, "status": "succeeded", "stdout": "done"}}
        with patch.object(self.adapter, "_rpc", side_effect=rpc) as mocked:
            result = self.adapter.call_tool("houdini_execute", {"code": "print('done')"})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["stdout"], "done")
        self.assertEqual(mocked.call_count, 2)

    def test_execute_no_wait_and_unknown_outcome_keep_job_id(self):
        for response in [{"ok": True, "result": {"status": "queued"}}, {"ok": False, "result": {}, "error": {"code": "timeout_unknown"}}]:
            with patch.object(self.adapter, "_rpc", return_value=response) as mocked:
                result = self.adapter.call_tool("houdini_execute", {"code": "pass", "wait_ms": 0})
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(result["structuredContent"]["result"]["job_id"], mocked.call_args.args[1]["job_id"])
            self.assertEqual(result["structuredContent"]["result"]["next_step"], "query_job_status_before_retry")

    def test_look_routes_and_attaches_image(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "view.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
            with patch("blib_hou_mcp.server._screenshot_root", return_value=Path(directory)):
                for arguments, expected in [({}, "viewport_screenshot"), ({"mode": "network"}, "pane_screenshot"), ({"mode": "network", "path": "/obj/geo1"}, "network_screenshot"), ({"mode": "pane", "pane_type": "Parm"}, "pane_screenshot")]:
                    with patch.object(self.adapter, "_rpc", return_value={"ok": True, "result": {"path": str(image)}}) as mocked:
                        result = self.adapter.call_tool("houdini_look", arguments)
                    self.assertEqual(mocked.call_args.args[0], expected)
                    self.assertEqual(result["content"][1]["type"], "image")

    def test_malformed_inputs_never_reach_rpc(self):
        with patch.object(self.adapter, "_rpc") as mocked:
            for name, args in [("houdini_edit", {"steps": []}), ("houdini_execute", {"code": "pass", "wait_ms": True}), ("houdini_execute", {"code": "pass", "job_id": "bad"}), ("houdini_look", {"mode": "network", "timeline_frame": 5}), ("houdini_look", {"mode": "bogus"})]:
                self.assertTrue(self.adapter.call_tool(name, args)["isError"])
        mocked.assert_not_called()

    def test_tool_metadata_and_legacy_compatibility(self):
        tools = self.adapter.list_tools()
        self.assertEqual([t["name"] for t in tools[:3]], ["houdini_edit", "houdini_execute", "houdini_look"])
        self.assertTrue(self.adapter.status(include_health=False)["tools"]["policy_contract"]["ok"])
        self.assertTrue(self.adapter.status(include_health=False)["safety"]["direct_edit_tools_match_policy"])
        self.assertIn("houdini_run_plan", {t["name"] for t in tools})


if __name__ == "__main__":
    unittest.main()
