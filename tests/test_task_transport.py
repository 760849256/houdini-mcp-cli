"""Actual HTTP/stdio failure recovery without production Houdini."""
import json, os, queue, subprocess, sys, tempfile, threading, time, unittest, uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"/"python"))
from blib_hou_bridge import commands, server, state, tasks
from blib_hou_bridge.connection import Connection
from blib_hou_mcp.server import BridgeMCPAdapter
from tests.test_blib_hou_bridge import FakeHou

def until(check, timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=check()
        if value:return value
        time.sleep(.01)
    raise AssertionError("Task state did not settle")

class Pipe:
    def __init__(self, identity):
        self.p=subprocess.Popen([sys.executable,"-B",str(ROOT/"scripts"/"cli"/"blib_hou_mcp.py"),"--instance",identity],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8")
        self.q=queue.Queue()
        def read():
            for line in self.p.stdout:self.q.put(json.loads(line))
        threading.Thread(target=read,daemon=True).start()
        self.send({"jsonrpc":"2.0","id":0,"method":"initialize","params":{}})
        self.receive(0)
    def send(self, message):
        self.p.stdin.write(json.dumps(message)+"\n");self.p.stdin.flush()
    def tool(self, number,name,args):
        self.send({"jsonrpc":"2.0","id":number,"method":"tools/call","params":{"name":name,"arguments":args}})
    def receive(self, number, timeout=5):
        result=self.q.get(timeout=timeout)
        assert result.get("id")==number,result
        return result
    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            try:self.p.wait(5)
            except subprocess.TimeoutExpired:self.p.terminate();self.p.wait(5)
        self.p.stdout.close();self.p.stderr.close()

class TaskTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"BLIB_HOU_RUNTIME_DIR":self.tmp.name});self.env.start()
        self.hou=FakeHou();self.mock=patch.object(commands,"_import_hou",return_value=self.hou);self.mock.start()
        self.bridge=server.BridgeServer();self.bridge.start()
        state.set_edit_enabled(True);state.set_python_enabled(True)
        self.client=Connection(instance=self.bridge.session_id);self.pipes=[];self.releases=[]
    def tearDown(self):
        for event in self.releases:event.set()
        for pipe in self.pipes:pipe.close()
        until(lambda:self.bridge.tasks._running is None)
        self.bridge.stop();state.set_edit_enabled(False);self.mock.stop();self.env.stop();self.tmp.cleanup()
    def pipe(self):
        result=Pipe(self.bridge.session_id);self.pipes.append(result);return result
    def block(self):
        entered,release=threading.Event(),threading.Event();self.releases.append(release)
        original=self.bridge.tasks.executor
        def run(command,payload,context):
            if command=="selected":entered.set();release.wait(3)
            return original(command,payload,context)
        self.bridge.tasks.executor=run
        self.client.submit_task("selected",{},wait_ms=0);self.assertTrue(entered.wait(2))
        return release
    def finished(self, job_id):
        return until(lambda:(r if (r:=self.bridge.tasks.get(job_id))["status"] in tasks.TERMINAL else None))
    def edit(self, job_id, comment, **options):
        return self.client.submit_task("set_comment",{"node":self.hou.box.path(),"comment":comment},job_id=job_id,**options)
    def test_lost_reply_and_duplicate_do_not_repeat_effects(self):
        job=str(uuid.uuid4());original=self.client._task_request;dropped=[]
        def request(session,method,path,body=None):
            result=original(session,method,path,body)
            if method=="POST" and path=="/tasks" and not dropped:
                dropped.append(True);return {"ok":False,"error":{"code":"timeout_unknown"}}
            return result
        with patch.object(self.hou.box,"setComment",wraps=self.hou.box.setComment) as changes:
            with patch.object(self.client,"_task_request",side_effect=request):reply=self.edit(job,"once")
            self.assertEqual(reply["result"]["status"],"outcome_unknown")
            self.finished(job);self.assertEqual(self.client.task_status(job)["result"]["status"],"succeeded")
            self.edit(job,"once");self.assertEqual(changes.call_count,1)

    def test_daily_edit_is_one_queryable_workflow_with_readback(self):
        adapter=BridgeMCPAdapter(instance=self.bridge.session_id)
        response=adapter.call_tool("houdini_edit",{"steps":[
            {"command":"batch_set_parms","payload":{"node":"/obj/geo1/OUT","values":{"tx":7}}},
            {"command":"set_comment","payload":{"node":"/obj/geo1/OUT","comment":"verified"}}]})
        self.assertFalse(response["isError"],response)
        result=response["structuredContent"]["result"]
        self.assertTrue(result["verified"])
        self.assertEqual(self.hou.out.parm("tx").eval(),7)
        job=adapter.call_tool("houdini_job",{"job_id":result["job_id"]})["structuredContent"]["result"]
        self.assertEqual(job["command"],"edit_batch")
        self.assertEqual(job["status"],"succeeded")

    def test_daily_inspection_returns_a_durable_result(self):
        adapter=BridgeMCPAdapter(instance=self.bridge.session_id)
        response=adapter.call_tool("houdini_inspect",{"kind":"node","targets":["/obj/geo1/OUT"]})
        self.assertFalse(response["isError"],response)
        job_id=response["structuredContent"]["result"]["job_id"]
        record=self.bridge.tasks.get(job_id)
        self.assertEqual(record["command"],"inspect_batch")
        self.assertEqual(record["status"],"succeeded")
    def test_expired_and_cancelled_queued_writes_never_apply(self):
        release=self.block();expired,cancelled=str(uuid.uuid4()),str(uuid.uuid4())
        self.edit(expired,"expired",queue_timeout_ms=10,wait_ms=0)
        self.edit(cancelled,"cancelled",wait_ms=0)
        self.assertEqual(self.client.cancel_task(cancelled)["result"]["status"],"cancelled")
        self.assertEqual(self.finished(expired)["status"],"expired")
        release.set();until(lambda:self.bridge.tasks._running is None)
        self.assertNotIn(self.hou.box.comment(),{"expired","cancelled"})
    def test_permission_reenable_does_not_revive_old_queued_write(self):
        release=self.block();job=str(uuid.uuid4());self.edit(job,"unsafe",wait_ms=0)
        state.set_edit_enabled(False);state.set_edit_enabled(True);release.set()
        result=self.finished(job);self.assertFalse(result["execution_started"])
        self.assertEqual(result["response"]["error"]["code"],"permission_changed")
    def test_scene_change_rejects_queued_write(self):
        release=self.block();job=str(uuid.uuid4());self.edit(job,"unsafe",wait_ms=0)
        state._scene_event("AfterLoad");release.set();result=self.finished(job)
        self.assertFalse(result["execution_started"])
        self.assertEqual(result["response"]["error"]["code"],"scene_changed")
    def test_stop_refuses_running_task_but_health_stays_available(self):
        release=self.block()
        with self.assertRaises(tasks.TaskError):self.bridge.stop()
        self.assertTrue(self.bridge.info()["running"]);self.assertTrue(self.client.snapshot()["ok"]);release.set()
    def test_stdio_ping_during_long_execute(self):
        pipe=self.pipe();job=str(uuid.uuid4())
        self.hou.task_gate=threading.Event();self.releases.append(self.hou.task_gate)
        pipe.tool(1,"houdini_execute",{"job_id":job,"code":"hou.task_gate.wait(3)","wait_ms":10000})
        until(lambda:self.bridge.tasks.get(job)["status"]=="running")
        began=time.perf_counter();pipe.send({"jsonrpc":"2.0","id":2,"method":"ping"})
        pipe.receive(2);self.assertLess(time.perf_counter()-began,.5)
        pipe.tool(3,"houdini_job",{"job_id":job})
        self.assertEqual(pipe.receive(3)["result"]["structuredContent"]["result"]["status"],"running")
        self.hou.task_gate.set()
        response=pipe.receive(1);self.assertFalse(response["result"]["isError"])
    def test_client_disconnect_keeps_accepted_task_queryable(self):
        pipe=self.pipe();job=str(uuid.uuid4())
        pipe.tool(1,"houdini_execute",{"job_id":job,"code":"import time; time.sleep(.3); hou.task_value=42","wait_ms":10000})
        until(lambda:self.bridge.tasks.get(job)["status"]=="running")
        pipe.close();result=self.finished(job)
        self.assertEqual(result["status"],"succeeded");self.assertEqual(self.hou.task_value,42)
        new=self.pipe();new.tool(1,"houdini_job",{"job_id":job})
        self.assertEqual(new.receive(1)["result"]["structuredContent"]["result"]["status"],"succeeded")

    def test_cancel_notification_fences_queued_edit(self):
        token=commands.context(self.hou)["context_token"]
        release=self.block();pipe=self.pipe();job=str(uuid.uuid4())
        pipe.tool(1,"houdini_edit",{"job_id":job,"context_token":token,"wait_ms":10000,
            "steps":[{"command":"set_comment","payload":{"node":self.hou.box.path(),"comment":"never"}}]})
        until(lambda:self.bridge.tasks.get(job)["status"]=="queued")
        pipe.send({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}})
        response=pipe.receive(1)
        self.assertEqual(response["error"]["code"],-32800)
        self.assertEqual(response["error"]["data"]["jobs"][0]["job_id"],job)
        self.assertEqual(self.finished(job)["status"],"cancelled")
        release.set();until(lambda:self.bridge.tasks._running is None)
        self.assertNotEqual(self.hou.box.comment(),"never")

    def test_running_uncooperative_python_is_not_falsely_cancelled(self):
        self.hou.task_gate=threading.Event();self.releases.append(self.hou.task_gate)
        job=str(uuid.uuid4())
        self.client.submit_task("python_submit",{"job_id":job,
            "code":"hou.task_gate.wait(3); hou.changed_after_cancel=42"},job_id=job,wait_ms=0)
        until(lambda:self.bridge.tasks.get(job).get("execution_started"))
        cancelled=self.client.cancel_task(job)["result"]
        self.assertEqual(cancelled["status"],"running")
        self.assertTrue(cancelled["cancel_requested"])
        self.hou.task_gate.set();result=self.finished(job)
        self.assertEqual(result["status"],"succeeded")
        self.assertEqual(self.hou.changed_after_cancel,42)

    def test_plan_step_failure_keeps_partial_receipt_and_step_results(self):
        job_id = str(uuid.uuid4())
        steps = [
            {"command": "set_comment", "payload": {"node": self.hou.box.path(), "comment": "applied"}},
            {"command": "set_comment", "payload": {"node": "/obj/missing", "comment": "unused"}},
        ]
        self.client.submit_task("run_plan", {"steps": steps}, job_id=job_id, wait_ms=0)
        self.finished(job_id)
        record = self.client.task_status(job_id)["result"]
        self.assertEqual(self.hou.box.comment(), "applied")
        self.assertEqual(record["status"], "partial")
        self.assertTrue(record["execution_started"])
        self.assertFalse(record["response"]["ok"])
        self.assertEqual(record["response"]["error"]["code"], "plan_step_failed")
        self.assertEqual(record["response"]["result"]["failed_step"]["index"], 1)

    def test_running_cooperative_python_stops_at_checkpoint(self):
        self.hou.task_gate=threading.Event();self.releases.append(self.hou.task_gate)
        job=str(uuid.uuid4())
        self.client.submit_task("python_submit",{"job_id":job,
            "code":"hou.task_gate.wait(3); bridge_checkpoint(); hou.must_not_change=42"},job_id=job,wait_ms=0)
        until(lambda:self.bridge.tasks.get(job).get("execution_started"))
        self.client.cancel_task(job);self.hou.task_gate.set();result=self.finished(job)
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["response"]["error"]["code"],"cancelled")
        self.assertFalse(hasattr(self.hou,"must_not_change"))

if __name__=="__main__":unittest.main()
