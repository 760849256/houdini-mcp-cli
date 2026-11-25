"""Task acceptance in an owned, empty Houdini GUI session."""
from __future__ import annotations
import argparse, json, time, uuid
from pathlib import Path
from unittest.mock import patch
from acceptance_connection import run, StdioClient, wait_for
from blib_hou_bridge import tasks
from blib_hou_bridge.connection import Connection


def exercise(client, root, info, environment, command):
    connection = Connection(instance=info["session_id"])
    class Progress(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            (root / "task_progress.json").write_text(json.dumps(self), encoding="utf-8")
    results = Progress()

    def tool(name, arguments, allow_error=False):
        value = client.request("tools/call", {"name": name, "arguments": arguments})
        if not allow_error:
            assert not value.get("isError"), value
        response = value["structuredContent"]
        if response.get("pending") and arguments.get("wait_ms") != 0:
            record = wait_for(lambda: terminal(response["result"]["job_id"]), 15)
            assert record.get("response"), record
            response = record["response"]
            if not allow_error:
                assert response.get("ok"), response
        return response

    def terminal(job_id):
        reply = connection.task_status(job_id)
        if not reply.get("ok"):
            return None
        record = reply["result"]
        return record if record["status"] in tasks.TERMINAL else None

    def python(code, **options):
        return tool("houdini_execute", dict(code=code, **options))

    def send(method, params):
        client.sequence += 1
        number = client.sequence
        client.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}) + "\n")
        client.process.stdin.flush()
        return number

    def receive(number, timeout=10):
        response = client.responses.get(timeout=timeout)
        assert response.get("id") == number, response
        return response

    target = "/obj/BRIDGE_ACCEPTANCE"
    context = client.context()["result"]["context_token"]
    edit_id = str(uuid.uuid4())
    arguments = {"job_id": edit_id, "context_token": context,
                 "steps": [{"command": "create_node", "payload": {
                     "parent": target, "type": "null", "name": "TASK_NODE_%02d" % i}} for i in range(20)]}
    edited = tool("houdini_edit", arguments)
    assert edited["result"]["verified"], edited
    record = connection.task_status(edit_id)["result"]
    assert record["command"] == "edit_batch" and record["status"] == "succeeded", record
    duplicate = tool("houdini_edit", arguments)
    assert duplicate["result"]["verified"], duplicate
    results["ordinary_edit_and_duplicate"] = True
    results["durable_job_id"] = edit_id

    # Drop the response after it reached the real host, then query the original ID.
    lost_id = str(uuid.uuid4())
    payload = {"expected_context": client.context()["result"]["context_token"], "steps": [
        {"command": "create_node", "payload": {"parent": target, "type": "null", "name": "ONCE_RECEIPT"}}]}
    original = connection._task_request
    dropped = []
    def interrupted(session, method, path, body=None):
        reply = original(session, method, path, body)
        if method == "POST" and path == "/tasks" and not dropped:
            dropped.append(True)
            return {"ok": False, "error": {"code": "timeout_unknown"}}
        return reply
    with patch.object(connection, "_task_request", side_effect=interrupted):
        uncertain = connection.submit_task("edit_batch", payload, job_id=lost_id, wait_ms=0)
    assert uncertain["result"]["job_id"] == lost_id, uncertain
    recovered = wait_for(lambda: terminal(lost_id), 10)
    assert recovered["status"] == "succeeded", recovered
    assert connection.submit_task("edit_batch", payload, job_id=lost_id)["result"]["status"] == "succeeded"
    python("assert [n.name() for n in hou.node(%r).children() if n.name().startswith('ONCE_RECEIPT')] == ['ONCE_RECEIPT']" % target)
    results["lost_reply_no_duplicate_node"] = True

    # Hold the host main thread while independent MCP/control requests proceed.
    running_id = str(uuid.uuid4())
    waiting = send("tools/call", {"name": "houdini_execute", "arguments": {
        "job_id": running_id, "code": "import time; time.sleep(2)", "wait_ms": 10000}})
    wait_for(lambda: connection.task_status(running_id).get("result", {}).get("execution_started"), 5)
    began = time.perf_counter()
    ping = send("ping", {})
    assert "error" not in receive(ping), "ping failed"
    results["ping_during_main_thread_wait_ms"] = round((time.perf_counter() - began) * 1000, 3)
    began = time.perf_counter()
    query = send("tools/call", {"name": "houdini_job", "arguments": {"job_id": running_id}})
    queried = receive(query)
    assert queried["result"]["structuredContent"]["result"]["status"] == "running", queried
    results["job_query_during_wait_ms"] = round((time.perf_counter() - began) * 1000, 3)
    assert "error" not in receive(waiting)

    # Expire and cancel queued writes without allowing a later scene change.
    context = client.context()["result"]["context_token"]
    block_id = str(uuid.uuid4())
    tool("houdini_execute", {"job_id": block_id, "code": "import time; time.sleep(2)", "wait_ms": 0})
    wait_for(lambda: connection.task_status(block_id).get("result", {}).get("execution_started"), 5)
    expired_id, cancelled_id = str(uuid.uuid4()), str(uuid.uuid4())
    expired = connection.submit_task("set_comment", {"node": target, "comment": "EXPIRED_MUST_NOT_APPLY"},
                                    job_id=expired_id, queue_timeout_ms=30, wait_ms=0)
    assert expired["ok"], expired
    request_id = send("tools/call", {"name": "houdini_edit", "arguments": {
        "job_id": cancelled_id, "context_token": context, "wait_ms": 10000,
        "steps": [{"command": "set_comment", "payload": {"node": target, "comment": "CANCELLED_MUST_NOT_APPLY"}}]}})
    wait_for(lambda: connection.task_status(cancelled_id).get("result", {}).get("status") == "queued", 5)
    client.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                          "params": {"requestId": request_id}}) + "\n")
    client.process.stdin.flush()
    cancelled_reply = receive(request_id)
    assert cancelled_reply.get("error", {}).get("code") == -32800, cancelled_reply
    assert wait_for(lambda: terminal(cancelled_id), 5)["status"] == "cancelled"
    assert wait_for(lambda: terminal(expired_id), 5)["status"] == "expired"
    wait_for(lambda: terminal(block_id), 5)
    python("assert hou.node(%r).comment() not in ('EXPIRED_MUST_NOT_APPLY', 'CANCELLED_MUST_NOT_APPLY')" % target)
    results["queued_expiry_and_notification_cancel"] = True

    # Cancel between two batch steps; preserve the first effect and report partial.
    partial_id = str(uuid.uuid4())
    setup = (
        "from blib_hou_bridge import server\n"
        "def _bridge_cancel_first(node, event_type, **kwargs):\n"
        "    if node.parm('tx').eval() == 100:\n"
        "        server._server.tasks.cancel(%r)\n"
        "hou.node(%r).addEventCallback((hou.nodeEventType.ParmTupleChanged,), _bridge_cancel_first)\n"
    ) % (partial_id, target)
    python(setup)
    partial = tool("houdini_edit", {"job_id": partial_id, "steps": [
        {"command": "set_parm", "payload": {"node": target, "parm": "tx", "value": 100}},
        {"command": "set_parm", "payload": {"node": target, "parm": "tx", "value": 200}}]}, allow_error=True)
    assert not partial["ok"], partial
    partial_record = connection.task_status(partial_id)["result"]
    assert partial_record["status"] == "partial" and partial_record["progress"]["completed"] == 1, partial_record
    python("node=hou.node(%r); assert node.parm('tx').eval() == 100; node.removeAllEventCallbacks()" % target)
    results["partial_batch_cancel_preserves_evidence"] = True

    # An unexpectedly closed MCP process detaches its waiter, not the host job.
    background_id = str(uuid.uuid4())
    with (root / "alternate_mcp.log").open("w", encoding="utf-8") as stderr:
        alternate = StdioClient(info["session_id"], environment, stderr)
        try:
            alternate.request("initialize", {})
            alternate.sequence += 1
            alternate.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": alternate.sequence, "method": "tools/call",
                "params": {"name": "houdini_execute", "arguments": {"job_id": background_id,
                    "code": "import time; time.sleep(1); hou.node(%r).setComment('BACKGROUND_FINISHED')" % target,
                    "wait_ms": 10000}}}) + "\n")
            alternate.process.stdin.flush()
            wait_for(lambda: connection.task_status(background_id).get("result", {}).get("execution_started"), 5)
        finally:
            alternate.close()
        assert wait_for(lambda: terminal(background_id), 5)["status"] == "succeeded"
        restarted = StdioClient(info["session_id"], environment, stderr)
        try:
            restarted.request("initialize", {})
            value = restarted.request("tools/call", {"name": "houdini_job", "arguments": {"job_id": background_id}})
            assert value["structuredContent"]["result"]["status"] == "succeeded", value
        finally:
            restarted.close()
    python("assert hou.node(%r).comment() == 'BACKGROUND_FINISHED'" % target)
    results["mcp_restart_recovers_background_task"] = True
    repeated = tool("houdini_edit", {"details": True, "steps": [
        {"command": "set_parm", "payload": {"node": target, "parm": "tx", "value": i}}
        for i in range(20)]})
    checks = repeated["result"]["verification"]["checks"]
    assert sum(c.get("scope") == "intermediate_readback" for c in checks) == 19, checks
    python("assert hou.node(%r).parm('tx').eval() == 19" % target)
    results["repeated_parameter_intermediate_and_final_readback"] = True
    inspection = tool("houdini_inspect", {"targets": [target], "fields": ["path", "type", "flags"]})
    identity = inspection["result"]["target_ids"][target]
    guarded = tool("houdini_edit", {"expected_nodes": {target: identity + 1}, "steps": [
        {"command": "set_parm", "payload": {"node": target, "parm": "tx", "value": -999}}]}, allow_error=True)
    assert guarded["error"]["code"] == "target_changed" and not guarded["task"]["execution_started"], guarded
    results["replaced_target_rejected_before_effects"] = True
    command(root, "modal")
    wait_for(lambda: (root / "modal_started").exists(), 5)
    modal_job = str(uuid.uuid4())
    queued = tool("houdini_edit", {"job_id": modal_job, "wait_ms": 0, "queue_timeout_ms": 200,
        "steps": [{"command": "set_parm", "payload": {"node": target, "parm": "tx", "value": -999}}]})
    modal_record = wait_for(lambda: terminal(modal_job), 5)
    assert modal_record["status"] == "expired" and not modal_record["execution_started"], modal_record
    wait_for(lambda: (root / "modal_finished").exists(), 5)
    python("assert hou.node(%r).parm('tx').eval() == 19" % target)
    results["modal_dialog_defers_and_expires_queued_write"] = True
    # Capture via the actual GUI API. Structural restoration is verified even
    # when a hidden window cannot produce a usable preview.
    before = python("import json\np=hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)\nprint(json.dumps({'pwd':p.pwd().path(),'bounds':list(p.visibleBounds().min())+list(p.visibleBounds().max()),'frame':hou.frame()}))")
    capture = tool("houdini_look", {"mode": "network", "path": target, "fit": True}, allow_error=True)
    after = python("import json\np=hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)\nprint(json.dumps({'pwd':p.pwd().path(),'bounds':list(p.visibleBounds().min())+list(p.visibleBounds().max()),'frame':hou.frame()}))")
    assert before["result"]["stdout"] == after["result"]["stdout"], (before, after)
    results["network_capture_restores_pane"] = True
    results["network_capture"] = {"ok": capture["ok"], "error": capture.get("error"),
                                   "image_status": capture.get("result", {}).get("image_status")}
    if capture.get("ok") and capture.get("result", {}).get("path"):
        import shutil
        destination = Path(__file__).resolve().parents[2] / "artifacts" / "bridge_acceptance" / ("network_" + info["houdini"] + ".png")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(capture["result"]["path"], destination)
        results["network_capture"]["review_artifact"] = str(destination)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--houdini", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    run(options.houdini, True, options.samples, options.output, exercise=exercise)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ": " + str(error)[:500])
        raise SystemExit(1)
