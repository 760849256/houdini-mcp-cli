"""Regression coverage for guarded batching and useful response limits."""
import json, sqlite3, time, unittest, uuid
from unittest.mock import patch
from tests import test_task_transport as transport, test_task_lifecycle as lifecycle
from blib_hou_bridge import commands, protocol
from blib_hou_mcp.server import BridgeMCPAdapter

class SceneEventTests(unittest.TestCase):
    def test_enum_callback_accepts_file_metadata(self):
        from blib_hou_bridge import state
        marker=object()
        with patch.object(state,"_scene_event_types",(marker,)):
            previous=state.scene_epoch()
            state._scene_event(marker,old_hip_file="same.hip",new_hip_file="same.hip")
            self.assertNotEqual(previous,state.scene_epoch())

    def test_before_load_invalidates_even_if_loading_later_fails(self):
        from blib_hou_bridge import state
        previous=state.scene_epoch()
        state._scene_event("BeforeLoad")
        self.assertNotEqual(previous,state.scene_epoch())

class WorkflowPerformanceTests(unittest.TestCase):
    setUp = transport.TaskTransportTests.setUp
    tearDown = transport.TaskTransportTests.tearDown
    def adapter(self, **kwargs):
        return BridgeMCPAdapter(instance=self.bridge.session_id, **kwargs)

    def test_cached_context_is_labeled_and_skips_task_journal(self):
        snapshot=commands.context(self.hou)
        self.bridge._context_snapshot=(snapshot,time.monotonic(),time.time(),transport.state.scene_epoch())
        with patch.object(self.bridge.tasks,"submit",side_effect=AssertionError("unexpected queue admission")):
            reply=self.client.call("context",{"fields":["context_token"]})
        self.assertTrue(reply["ok"],reply)
        self.assertEqual(reply["result"]["context_token"],snapshot["context_token"])
        self.assertEqual(reply["result"]["context_cache"]["source"],"main_thread_cache")

    def test_stale_context_is_not_returned_as_current(self):
        self.bridge._context_snapshot=({"context_token":"stale"},time.monotonic()-2,time.time()-2,transport.state.scene_epoch())
        reply=self.client.call("context",{})
        self.assertTrue(reply["ok"],reply)
        self.assertEqual(reply["result"]["context_token"],commands._context_token(self.hou))
        self.assertNotIn("context_cache",reply["result"])
    def test_repeated_setters_preserve_all_effects_and_verify_intermediate_reads(self):
        args={"details":True,"steps":[{"command":"set_parm","payload":{
            "node":self.hou.out.path(),"parm":"tx","value":i}} for i in range(20)]}
        with patch.object(self.hou.out.parm("tx"),"set",wraps=self.hou.out.parm("tx").set) as setter:
            reply=self.adapter().call_tool("houdini_edit",args)
        self.assertFalse(reply["isError"],reply)
        self.assertEqual(setter.call_count,20)
        proof=reply["structuredContent"]["result"]["verification"]
        checks=[c for c in proof["checks"] if c["kind"]=="parm_value"]
        self.assertEqual(sum(c.get("scope")=="intermediate_readback" for c in checks),19)
        self.assertEqual(checks[-1]["actual"],19)

    def test_wrong_intermediate_write_is_not_hidden_by_later_success(self):
        parm=self.hou.out.parm("tx"); original=parm.set
        def corrupt(value): original(99 if value==1 else value)
        with patch.object(parm,"set",side_effect=corrupt):
            reply=self.adapter().call_tool("houdini_edit",{"details":True,"steps":[
                {"command":"set_parm","payload":{"node":self.hou.out.path(),"parm":"tx","value":i}}
                for i in (1,2)]})
        self.assertTrue(reply["isError"])
        checks=reply["structuredContent"]["result"]["verification"]["checks"]
        self.assertTrue(any(c.get("scope")=="intermediate_readback" and c["status"]=="failed" for c in checks))

    def test_final_corruption_still_fails_live_readback(self):
        parm=self.hou.out.parm("tx"); original=parm.set
        def corrupt(value): original(99 if value==2 else value)
        with patch.object(parm,"set",side_effect=corrupt):
            reply=self.adapter().call_tool("houdini_edit",{"steps":[
                {"command":"set_parm","payload":{"node":self.hou.out.path(),"parm":"tx","value":i}}
                for i in (1,2)]})
        self.assertTrue(reply["isError"])

    def test_absolute_edit_does_not_request_extra_context(self):
        adapter=self.adapter()
        with patch.object(adapter,"_rpc",side_effect=AssertionError("extra RPC")):
            reply=adapter.call_tool("houdini_edit",{"steps":[{"command":"set_comment",
                "payload":{"node":self.hou.out.path(),"comment":"one submit"}}]})
        self.assertFalse(reply["isError"],reply)

    def test_replaced_target_and_changed_parameter_reject_before_effects(self):
        path=self.hou.out.path()
        for extra,code in (({"expected_nodes":{path:-1}},"target_changed"),
                           ({"expected_parms":{path:{"tx":-999}}},"precondition_changed")):
            reply=self.adapter().call_tool("houdini_edit",dict(extra,steps=[
                {"command":"set_comment","payload":{"node":path,"comment":"must not apply"}}]))
            self.assertTrue(reply["isError"])
            self.assertEqual(reply["structuredContent"]["error"]["code"],code)
            self.assertFalse(reply["structuredContent"]["task"]["execution_started"])

    def test_projected_node_inspect_never_reads_parameters(self):
        with patch.object(self.hou.out,"parms",side_effect=AssertionError("unrequested parameter read")):
            reply=self.adapter().call_tool("houdini_inspect",{
                "targets":[self.hou.out.path()],"fields":["path","type"]})
        self.assertFalse(reply["isError"],reply)
        self.assertEqual(reply["structuredContent"]["result"]["target_ids"][self.hou.out.path()],
                         self.hou.out.sessionId())

    def test_read_only_blocks_python_and_standard_edits(self):
        transport.state.set_read_only(True)
        adapter = self.adapter()
        for tool, arguments in (
            ("houdini_execute", {"code": "raise AssertionError('unexpected write')"}),
            ("houdini_edit", {"steps": [{"command": "set_comment", "payload": {
                "node": self.hou.out.path(), "comment": "unexpected"}}]}),
        ):
            with self.subTest(tool=tool):
                reply = adapter.call_tool(tool, arguments)
                self.assertTrue(reply["isError"], reply)
                self.assertEqual(reply["structuredContent"]["error"]["code"], "read_only")
        access = self.client.call("edit_mode", {"enabled": True})
        self.assertFalse(access["ok"], access)
        self.assertEqual(access["error"]["code"], "read_only")
        self.assertFalse(adapter.call_tool("houdini_context", {})["isError"])

    def test_scoped_summary_reads_rows_without_loading_node_details(self):
        with patch.object(self.hou.out, "parms", side_effect=AssertionError("unexpected parameter list")):
            response = self.adapter().call_tool("houdini_inspect", {
                "root": "/obj/geo1", "query": "out", "parm_names": ["tx"], "limit": 1,
            })
        self.assertFalse(response["isError"], response)
        result = response["structuredContent"]["result"]
        self.assertEqual(result["resolved_targets"], [self.hou.out.path()])
        self.assertIn("tx", result["results"][0]["result"]["parms"])
        self.assertIn(self.hou.out.path(), result["summary"][0])

    def test_error_summary_includes_input_paths(self):
        self.hou.out.setInput(0, self.hou.box)
        with patch.object(self.hou.out, "errors", return_value=("synthetic error",)):
            response = self.adapter().call_tool("houdini_inspect", {
                "kind": "errors", "root": "/obj/geo1", "query": "out",
            })
        self.assertFalse(response["isError"], response)
        rows = response["structuredContent"]["result"]["nodes"]
        self.assertEqual([(row["path"], row["inputs"]) for row in rows],
                         [(self.hou.out.path(), [self.hou.box.path()])])

    def test_edit_preview_returns_inline_image_and_queryable_receipt(self):
        from blib_hou_bridge import auth, workflows
        shot = auth.runtime_dir() / "screenshots" / "test-preview.png"
        shot.parent.mkdir()
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        capture = protocol.ok_response("", "network_screenshot", {"path": str(shot), "image_status": "nonblack"})
        adapter = self.adapter(response_profile="compact")
        with patch.object(workflows, "look", return_value=capture) as look:
            reply = adapter.call_tool("houdini_edit", {
                "steps": [{"command": "set_comment", "payload": {
                    "node": self.hou.out.path(), "comment": "verified edit"}}],
                "preview": {"mode": "network", "path": "/obj/geo1"},
            })
        self.assertFalse(reply["isError"], reply)
        body = reply["structuredContent"]
        self.assertTrue(body["result"]["verified"], body)
        self.assertEqual(body["result"]["preview"]["status"], "captured")
        self.assertEqual([block["type"] for block in reply["content"]], ["text", "image"])
        self.assertEqual(json.loads(reply["content"][0]["text"])["result"]["preview"]["status"], "captured")
        look.assert_called_once()
        history = adapter.call_tool("houdini_job", {"job_id": body["task"]["job_id"]})
        self.assertEqual([block["type"] for block in history["content"]], ["text", "image"])

    def test_preview_failure_preserves_edit_and_retry_is_read_only(self):
        from blib_hou_bridge import auth, workflows
        target = self.hou.out
        adapter = self.adapter()
        edit_id = str(uuid.uuid4())
        request = {"job_id": edit_id, "steps": [{"command": "set_comment", "payload": {
                        "node": target.path(), "comment": "already written"}}],
                   "preview": {"mode": "network", "path": "/obj/geo1"}}
        with patch.object(target, "setComment", wraps=target.setComment) as write:
            with patch.object(workflows, "look", side_effect=RuntimeError("viewport temporarily unavailable")):
                reply = adapter.call_tool("houdini_edit", request)
            self.assertFalse(reply["isError"], reply)
            body = reply["structuredContent"]
            self.assertTrue(body["result"]["verified"])
            self.assertEqual(body["result"]["preview"]["status"], "failed")
            self.assertEqual(body["task"]["status"], "succeeded")
            self.assertEqual(target.comment(), "already written")

            transport.state.set_read_only(True)
            shot = auth.runtime_dir() / "screenshots" / "retry.png"
            shot.parent.mkdir()
            shot.write_bytes(b"\x89PNG\r\n\x1a\n")
            capture = protocol.ok_response("", "network_screenshot", {"path": str(shot)})
            with patch.object(workflows, "look", return_value=capture) as look:
                # The retry runs through the real host dispatch seam, not the edit workflow.
                retry = adapter.call_tool("houdini_job", {
                    "action": "preview", "job_id": edit_id, "session_id": self.bridge.session_id,
                })
            self.assertFalse(retry["isError"], retry)
            self.assertEqual(retry["structuredContent"]["result"]["preview_of"], edit_id)
            self.assertFalse(retry["structuredContent"]["task"]["execution_started"])
            self.assertEqual([block["type"] for block in retry["content"]], ["text", "image"])
            look.assert_called_once()
            write.assert_called_once()

        transport.state._scene_event("BeforeLoad")
        retry = adapter.call_tool("houdini_job", {"action": "preview", "job_id": edit_id})
        self.assertTrue(retry["isError"], retry)
        self.assertEqual(retry["structuredContent"]["error"]["code"], "scene_changed")

    def test_invalid_preview_options_are_rejected_before_edits(self):
        target = self.hou.out
        with patch.object(target, "setComment", wraps=target.setComment) as write:
            reply = self.adapter().call_tool("houdini_edit", {
                "steps": [{"command": "set_comment", "payload": {
                    "node": target.path(), "comment": "must remain unchanged"}}],
                "preview": {"mode": "network", "width": 384},
            })
        self.assertTrue(reply["isError"], reply)
        self.assertEqual(reply["structuredContent"]["error"]["code"], "bad_request")
        write.assert_not_called()

    def test_compact_profile_retains_structured_evidence_and_full_fallback(self):
        arguments={"targets":[self.hou.out.path()]}
        full=self.adapter(response_profile="full").call_tool("houdini_inspect",arguments)
        compact=self.adapter(response_profile="compact").call_tool("houdini_inspect",arguments)
        self.assertIn("results",compact["structuredContent"]["result"])
        self.assertNotIn("results",json.loads(compact["content"][0]["text"])["result"])
        self.assertIn("results",json.loads(full["content"][0]["text"])["result"])
        self.assertLess(len(compact["content"][0]["text"]),len(full["content"][0]["text"]))

class StoreFailureTests(unittest.TestCase):
    setUp = lifecycle.TaskLifecycleTests.setUp
    tearDown = lifecycle.TaskLifecycleTests.tearDown
    manager = lifecycle.TaskLifecycleTests.manager
    submit = lifecycle.TaskLifecycleTests.submit
    def test_failed_receipt_read_keeps_queue_entry(self):
        manager=self.manager(); record=self.submit(manager)
        with patch.object(manager.store,"get",side_effect=sqlite3.OperationalError("unavailable")):
            with self.assertRaises(sqlite3.Error):manager.drain_one()
        self.assertIn(record["job_id"],manager._queue)
        self.assertEqual(self.executed,[])
        manager.drain_one()
        self.assertEqual(len(self.executed),1)

    def test_retention_failure_does_not_lose_durable_result(self):
        manager=self.manager(); record=self.submit(manager)
        with patch.object(manager.store,"compact",side_effect=sqlite3.OperationalError("unavailable")):
            manager.drain_one()
        self.assertEqual(manager.get(record["job_id"])["status"],"succeeded")
        self.assertFalse(manager.storage_error)
        self.assertNotIn("durability",manager.get(record["job_id"]))

    def test_unserializable_result_is_queryable_without_reexecution(self):
        def perform(command,payload,context):
            context.start_effects()
            return {"ok":True,"result":object()}
        manager=self.manager(perform);record=self.submit(manager);manager.drain_one()
        result=manager.get(record["job_id"])
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["reason"],"result_serialization_failed")
        json.dumps(result)
        self.submit(manager,record["job_id"])
