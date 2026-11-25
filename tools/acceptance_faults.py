"""Real HIP changes and owned-process loss; extends the GUI task checks."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time,uuid
from pathlib import Path
from acceptance_connection import run,wait_for
from acceptance_tasks import exercise as task_exercise
from blib_hou_bridge import tasks
from blib_hou_bridge.connection import Connection

def exercise(client,root,info,environment,command):
    results=task_exercise(client,root,info,environment,command)
    connection=Connection(instance=info["session_id"])
    def tool(name,args):
        value=client.request("tools/call",{"name":name,"arguments":args},timeout=30)
        assert not value.get("isError"),value
        return value["structuredContent"]
    def python(code,wait_ms=10000):
        return tool("houdini_execute",{"code":code,"wait_ms":wait_ms})
    def terminal(identifier):
        value=connection.task_status(identifier)
        record=value.get("result",{})
        return record if record.get("status") in tasks.TERMINAL else None
    # Queue a write while another main-thread job changes the HIP itself.
    saved=root/"isolated-reload.hip"
    python("hou.hipFile.save(%r)"%str(saved))
    for kind,action in (
        ("load","hou.hipFile.load(%r,suppress_save_prompt=True,ignore_load_warnings=True)"%str(saved)),
        ("new","hou.hipFile.clear(suppress_save_prompt=True)")):
        marker=root/("hip-"+kind+"-started")
        background=python("import time\nfrom pathlib import Path\nPath(%r).write_text('started')\ntime.sleep(1)\n%s"%(str(marker),action),0)
        wait_for(lambda:marker.exists(),5)
        queued=tool("houdini_edit",{"wait_ms":0,"steps":[{"command":"set_comment",
            "payload":{"node":"/obj/BRIDGE_ACCEPTANCE","comment":"must not cross HIP epoch"}}]})
        identifier=queued["result"]["job_id"]
        record=wait_for(lambda:terminal(identifier),15)
        assert record["status"]=="failed" and not record["execution_started"],record
        assert record["response"]["error"]["code"]=="scene_changed",record
        wait_for(lambda:terminal(background["result"]["job_id"]),15)
        results["queued_write_rejected_after_hip_"+kind]=True

    # Use a separate owned headless process so forced loss cannot touch the GUI.
    owner_root=root/"owned-crash";owner_root.mkdir()
    source=owner_root/"fixture.py"
    python_root=Path(__file__).resolve().parents[1]/"scripts"/"python"
    source.write_text("import sys,os,time,json\nfrom pathlib import Path\nsys.path.insert(0,%r)\nimport hou\nfrom blib_hou_bridge import server,state,auth\nstate.set_edit_enabled(True)\nstate.set_python_enabled(True)\ns=server.start_server()\nauth.atomic_json(Path(%r),server.status())\nwhile True:time.sleep(.1)\n"%(str(python_root),str(owner_root/"ready.json")),encoding="utf-8")
    executable=Path(os.environ.get("HFS", ""))/"bin"/"hython.exe"
    # Derive from the validated GUI executable recorded by the wrapper.
    executable=Path(environment["BLIB_ACCEPTANCE_HYTHON"])
    child_env=dict(environment,HOUDINI_USER_PREF_DIR=str(owner_root/"prefs"))
    stderr=open(owner_root/"child.log","w",encoding="utf-8")
    process=subprocess.Popen([str(executable),str(source)],env=child_env,stdout=stderr,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        ready=wait_for(lambda:json.loads((owner_root/"ready.json").read_text()) if (owner_root/"ready.json").exists() else None,60)
        assert ready["session_id"]!=info["session_id"],ready
        other=Connection(instance=ready["session_id"])
        job=str(uuid.uuid4());marker=owner_root/"effect-applied"
        reply=other.submit_task("python_submit",{"job_id":job,"code":
            "from pathlib import Path\nimport time\nhou.node('/obj').createNode('geo','ONCE_BEFORE_CRASH')\nPath(%r).write_text('effect recorded')\ntime.sleep(120)"%str(marker)},
            job_id=job,wait_ms=0)
        assert reply["ok"],reply
        wait_for(lambda:marker.exists(),15)
        process.terminate();process.wait(10)
        receipt=tasks.local_status(ready["session_id"],job)
        assert receipt["status"]=="outcome_unknown" and receipt["execution_started"],receipt
        assert connection.snapshot()["ok"]
        results["owned_process_loss_preserves_unknown_outcome"]=True
        results["owner_loss_does_not_attach_to_other_houdini"]=True
    finally:
        if process.poll() is None:process.terminate();process.wait(10)
        stderr.close()
    # A genuine blocking SOP cook; protocol controls continue independently.
    python("p=hou.node('/obj').createNode('geo','COOK_FIXTURE')\ns=p.createNode('box','INPUT')\nn=p.createNode('python','WAITING_COOK')\nn.setInput(0,s)")
    marker=root/"cook-started"
    # Houdini expands backslashes in the Python SOP's string parameter.
    sop_code="from pathlib import Path\nimport time\nPath(%r).write_text('cooking')\ntime.sleep(3)"%marker.as_posix()
    code="n=hou.node('/obj/COOK_FIXTURE/WAITING_COOK')\nn.parm('python').set(%r)\ntry:\n n.cook(force=True)\nexcept Exception:\n raise RuntimeError(str(n.errors()))\nassert not n.errors(),n.errors()\nn.geometry()"%sop_code
    submitted=python(code,0)
    try:
        wait_for(lambda:marker.exists(),10)
    except TimeoutError:
        raise AssertionError("SOP cook did not enter its callback: " + str(connection.task_status(submitted["result"]["job_id"])))
    began=time.perf_counter();client.request("ping",{})
    results["ping_during_real_sop_cook_ms"]=round((time.perf_counter()-began)*1000,3)
    began=time.perf_counter();connection.task_status(submitted["result"]["job_id"])
    results["query_during_real_sop_cook_ms"]=round((time.perf_counter()-began)*1000,3)
    cooked=wait_for(lambda:terminal(submitted["result"]["job_id"]),15)
    assert cooked["status"]=="succeeded",cooked
    results["real_sop_cook_controls_responsive"]=True
    return results

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--houdini",type=Path,required=True)
    parser.add_argument("--samples",type=int,default=100)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    os.environ["BLIB_ACCEPTANCE_HYTHON"]=str(args.houdini.parent/"hython.exe")
    run(args.houdini,True,args.samples,args.output,exercise=exercise)
if __name__=="__main__":
    try:main()
    except Exception as exc:
        print(type(exc).__name__+": "+str(exc)[:500])
        raise SystemExit(1)
