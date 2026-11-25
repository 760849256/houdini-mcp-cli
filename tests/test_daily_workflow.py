import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "python"))
from blib_hou_bridge import commands, protocol, state
from blib_hou_mcp.server import BridgeMCPAdapter, main
from tests.test_blib_hou_bridge import FakeHou
from tests.test_viewport_framing import Viewport


class DailyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.hou = FakeHou()
        self.requests = []
        state.set_edit_enabled(True)
        def poster(session, request):
            self.requests.append(request)
            try:
                return {"ok": True, "result": commands.execute(request["command"], request["payload"], hou_module=self.hou)}
            except Exception as exc:
                return {"ok": False, "result": {}, "error": {"message": str(exc)}}
        self.adapter = BridgeMCPAdapter(session_loader=lambda _: {"token": "test"}, poster=poster, tool_profile="daily")

    def tearDown(self):
        state.set_edit_enabled(False)

    def test_daily_has_exactly_six_tools_and_advanced_retains_legacy(self):
        self.assertEqual([t["name"] for t in self.adapter.list_tools()], ["houdini_context", "houdini_inspect", "houdini_edit", "houdini_execute", "houdini_look", "houdini_job"])
        self.assertIn("houdini_set_parm", {t["name"] for t in self.adapter.list_tools(profile="advanced")})
        status = self.adapter.status(include_health=False)
        self.assertEqual(status["tools"]["advertised_count"], 6)
        self.assertTrue(status["safety"]["tool_policy_contract_ok"])

    def test_cli_defaults_to_daily_and_rejects_invalid_profile(self):
        with patch("blib_hou_mcp.server.run_stdio", return_value=0) as runner, patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main([]), 0)
            self.assertEqual(runner.call_args.args[0].tool_profile, "daily")
            self.assertEqual(main(["--tool-profile", "advanced"]), 0)
            self.assertEqual(runner.call_args.args[0].tool_profile, "advanced")
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(main(["--tool-profile", "bad"]), 2)

    def test_print_config_preserves_explicit_profile(self):
        with patch("sys.stdout", new=io.StringIO()) as output:
            main(["--tool-profile", "advanced", "--print-config"])
            self.assertIn("advanced", json.loads(output.getvalue())["mcpServers"]["blib-houdini-bridge"]["args"])

    def test_selected_parameter_edit_requires_no_path(self):
        ctx = self.adapter.call_tool("houdini_context", {})["structuredContent"]["result"]
        self.assertEqual(ctx["routing"]["target"], self.hou.out.path())
        result = self.adapter.call_tool("houdini_edit", {"context_token": ctx["context_token"], "steps": [{"command": "batch_set_parms", "payload": {"values": {"tx": 5}}}]})
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.hou.out.parm("tx").eval(), 5)

    def test_aliases_build_chain_and_source_arguments_are_not_mutated(self):
        steps = [
            {"command": "create_node", "payload": {"type": "null", "name": "DAILY_OUT"}},
            {"command": "connect", "payload": {"dst": "@selected_parent/DAILY_OUT", "input_index": 0}},
        ]
        result = self.adapter.call_tool("houdini_edit", {"steps": steps})
        self.assertFalse(result["isError"], result)
        self.assertNotIn("parent", steps[0]["payload"])
        self.assertEqual(self.hou.node('/obj/geo1/DAILY_OUT').inputs()[0], self.hou.out)

    def test_multi_selection_returns_candidates_without_writes(self):
        self.hou.selectedNodes = lambda: [self.hou.box, self.hou.out]
        result = self.adapter.call_tool("houdini_edit", {"steps": [{"command": "set_comment", "payload": {"comment": "new"}}]})
        self.assertTrue(result["isError"])
        self.assertEqual(len(result["structuredContent"]["result"]["candidates"]), 2)
        self.assertEqual([r["command"] for r in self.requests], ["context"])

    def test_no_selection_inspects_network_without_geometry(self):
        self.hou.selectedNodes = lambda: []
        with patch.object(commands, "geometry_summary", side_effect=AssertionError("unexpected cook")):
            result = self.adapter.call_tool("houdini_inspect", {})
        self.assertFalse(result["isError"])
        self.assertEqual(self.requests[-1]["payload"]["steps"][0]["command"], "network")

    def test_multi_selection_read_batches_and_geometry_needs_opt_in(self):
        self.hou.selectedNodes = lambda: [self.hou.box, self.hou.out]
        result = self.adapter.call_tool("houdini_inspect", {"kind": "parms"})
        self.assertFalse(result["isError"])
        self.assertEqual(len(self.requests[-1]["payload"]["steps"]), 2)
        count = len(self.requests)
        self.assertTrue(self.adapter.call_tool("houdini_inspect", {"kind": "geometry"})["isError"])
        self.assertEqual(len(self.requests), count)

    def test_missing_target_marks_partial_read_failed(self):
        result = self.adapter.call_tool("houdini_inspect", {"targets": ["/obj/geo1/OUT", "/obj/missing"]})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["result"]["success_count"], 1)

    def test_stale_token_rejected_by_adapter_and_execution_side(self):
        token = commands.context(self.hou)["context_token"]
        self.hou.selectedNodes = lambda: [self.hou.box]
        steps = [{"command": "set_comment", "payload": {"node": self.hou.out.path(), "comment": "bad"}}]
        self.assertTrue(self.adapter.call_tool("houdini_edit", {"context_token": token, "steps": steps})["isError"])
        with self.assertRaises(commands.BridgeCommandError):
            commands.run_plan(self.hou, {"steps": steps, "expected_context": token})
        self.assertNotEqual(self.hou.out.comment(), "bad")

    def test_same_filename_reload_changes_scene_identity(self):
        before = commands.context(self.hou)["context_token"]
        state._scene_event("hipFileEventType.AfterLoad")
        self.assertNotEqual(before, commands.context(self.hou)["context_token"])

    def test_selection_change_between_validation_and_run_rejects_write(self):
        original = self.adapter._poster
        def changed(session, request):
            if request['command'] == 'run_plan':
                self.hou.selectedNodes = lambda: [self.hou.box]
            return original(session, request)
        self.adapter._poster = changed
        result = self.adapter.call_tool('houdini_edit', {'steps': [{'command': 'set_comment', 'payload': {'comment': 'must not apply'}}]})
        self.assertTrue(result['isError'])
        self.assertNotEqual(self.hou.out.comment(), 'must not apply')

    def test_guarded_invalid_batch_never_applies_earlier_valid_step(self):
        token = commands.context(self.hou)['context_token']
        with self.assertRaises(commands.BridgeCommandError):
            commands.run_plan(self.hou, {'expected_context': token, 'steps': [
                {'command': 'set_comment', 'payload': {'node': self.hou.out.path(), 'comment': 'must not apply'}},
                {'command': 'set_parm', 'payload': {'node': self.hou.out.path(), 'parm': 'missing', 'value': 1}},
            ]})
        self.assertNotEqual(self.hou.out.comment(), 'must not apply')

    def test_network_restore_on_success_and_failure(self):
        pane = self.hou.ui.network_editor
        pane.visibleBounds = lambda: "original_bounds"
        restored = []
        pane.setVisibleBounds = lambda bounds, duration: restored.append((bounds, duration))
        original = pane.pwd()
        capture = commands._capture_pane_to_path
        for failure in (False, True):
            with patch.object(commands, "_capture_pane_to_path", side_effect=RuntimeError("capture failed") if failure else capture):
                if failure:
                    with self.assertRaises(RuntimeError):
                        commands.network_screenshot(self.hou, {"path": "/obj", "restore": True})
                else:
                    commands.network_screenshot(self.hou, {"path": "/obj", "restore": True})
            self.assertIs(pane.pwd(), original)
            self.assertEqual(restored[-1], ("original_bounds", 0))

    def test_camera_restore_after_capture_failure(self):
        viewport = Viewport()
        current = {"camera": "old_camera", "locked": True}
        viewport.camera = lambda: current["camera"]
        viewport.isCameraLockedToView = lambda: current["locked"]
        viewport.lockCameraToView = lambda value: current.update(locked=value)
        viewport.setCamera = lambda value: current.update(camera=value)
        viewport.type = lambda: "perspective"
        viewport.changeType = lambda value: None
        viewport.fail = True
        self.hou.out.worldTransform = lambda: None
        self.hou.ui.scene_viewer.viewport = viewport
        with self.assertRaises(RuntimeError):
            commands.viewport_screenshot(self.hou, {"camera": self.hou.out.path()})
        self.assertEqual(current, {"camera": "old_camera", "locked": True})

    def test_both_mode_keeps_partial_image_and_reports_failure(self):
        # Route both captures, retaining the successful half on a partial failure.
        with patch.object(self.adapter, "_rpc", side_effect=[{"ok": True, "result": {"path": "preview"}}, {"ok": False, "result": {}, "error": {"message": "No Houdini NetworkEditor pane was found."}}]), patch("blib_hou_mcp.server._append_viewport_image", side_effect=lambda result, response: result["content"].append({"type": "image", "data": "x"})):
            result = self.adapter.call_tool("houdini_look", {"mode": "both"})
        self.assertTrue(result["isError"])
        self.assertEqual(len([c for c in result["content"] if c["type"] == "image"]), 1)


if __name__ == '__main__':
    unittest.main()
