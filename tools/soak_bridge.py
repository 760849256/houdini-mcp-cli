"""Bounded-duration endurance run in an owned GUI and private runtime.

This is synthetic integration evidence, never evidence of real working days.
"""
from __future__ import annotations
import argparse, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
import acceptance_connection as harness
from blib_hou_bridge import auth

def exercise_for(hours, minimum_calls, output):
    def exercise(first, root, info, environment, command):
        client = first
        extra_clients = []
        began = time.monotonic()
        duration = hours * 3600
        interval = duration / max(minimum_calls, 1)
        progress = {"schema":1,"status":"running","started_at":datetime.now(timezone.utc).isoformat(),
            "target_hours":hours,"minimum_calls":minimum_calls,"calls":0,"failures":[],
            "scope":"isolated stdio/HTTP/GUI; synthetic workload; not real working-day observation",
            "production_scenes_touched":False,"houdini":info["houdini"],"bridge":info["version"],
            "instance_id":info["session_id"],"restarts":0,"client_restarts":0}
        manifest=harness.ROOT/"release_manifest.json"
        progress["source_manifest_sha256"]=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None
        def save():
            progress["elapsed_seconds"]=round(time.monotonic()-began,3)
            progress["updated_at"]=datetime.now(timezone.utc).isoformat()
            auth.atomic_json(output,progress)
        def call(name,args):
            value=client.request("tools/call",{"name":name,"arguments":args},timeout=45)
            body=value["structuredContent"]
            if body.get("pending"):
                identifier=body["result"]["job_id"]
                end=time.monotonic()+120
                while time.monotonic()<end:
                    receipt=client.request("tools/call",{"name":"houdini_job","arguments":{"job_id":identifier}})["structuredContent"]["result"]
                    if receipt["status"] in {"succeeded","partial","failed","expired","cancelled","outcome_unknown"}:
                        body=receipt.get("response") or {"ok":False,"result":receipt}
                        break
                    time.sleep(.05)
            if not body.get("ok"):raise AssertionError(str(body)[:1000])
            return body
        def python(code):
            return call("houdini_execute",{"code":code,"wait_ms":10000})
        try:
            python("n=hou.node('/obj/BRIDGE_ACCEPTANCE')\n[n.createNode('null','SOAK_%02d'%i) for i in range(20)]")
            paths=["/obj/BRIDGE_ACCEPTANCE/SOAK_%02d"%i for i in range(20)]
            stderr=open(root/"soak-mcp-initial.log","w",encoding="utf-8")
            client=harness.StdioClient(info["session_id"],environment,stderr)
            extra_clients.append((client,stderr))
            client.request("initialize",{})
            save()
            while time.monotonic()-began<duration or progress["calls"]<minimum_calls:
                number=progress["calls"]
                if number and number%100==0:
                    previous=json.loads((root/"ready.json").read_text())["generation"]
                    command(root,"restart")
                    harness.wait_for(lambda:json.loads((root/"ready.json").read_text())["generation"]!=previous,15)
                    client.context()
                    progress["restarts"]+=1
                if number and number%200==0:
                    client.close()
                    stderr=open(root/("soak-mcp-%d.log"%number),"w",encoding="utf-8")
                    client=harness.StdioClient(info["session_id"],environment,stderr)
                    extra_clients.append((client,stderr))
                    client.request("initialize",{})
                    client.context()
                    progress["client_restarts"]+=1
                began_call=time.perf_counter()
                kind=number%4
                if kind==0:
                    call("houdini_context",{"fields":["hip_name","current_network","context_token"]})
                elif kind==1:
                    call("houdini_inspect",{"targets":paths,"fields":["path","type","flags"]})
                elif kind==2:
                    call("houdini_edit",{"steps":[{"command":"set_comment","payload":{
                        "node":p,"comment":"soak iteration %d"%number}} for p in paths]})
                else:
                    python("assert len([n for n in hou.node('/obj/BRIDGE_ACCEPTANCE').children() if n.name().startswith('SOAK_')])==20")
                progress["calls"]+=1
                progress["last_call_ms"]=round((time.perf_counter()-began_call)*1000,3)
                save()
                next_due=began+min(duration,progress["calls"]*interval)
                while time.monotonic()<next_due:
                    time.sleep(min(5,next_due-time.monotonic()))
                    save()
            progress["status"]="checking_recovery"
            save()
            return {"soak_report":str(output),"calls":progress["calls"],"hours":progress["elapsed_seconds"]/3600}
        except BaseException as exc:
            progress["status"]="failed"
            progress["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1500]})
            save()
            raise
        finally:
            for child,stderr in extra_clients:
                child.close();stderr.close()
    return exercise

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--houdini",type=Path,required=True)
    parser.add_argument("--bridge-root",type=Path,default=harness.ROOT)
    parser.add_argument("--hours",type=float,default=8)
    parser.add_argument("--minimum-calls",type=int,default=1000)
    parser.add_argument("--output",type=Path,required=True)
    options=parser.parse_args()
    if not .001<=options.hours<=72 or options.minimum_calls<1:parser.error("invalid endurance bounds")
    harness.ROOT=options.bridge_root.resolve()
    options.output.parent.mkdir(parents=True,exist_ok=True)
    integration=options.output.with_suffix(".integration.json")
    if options.output.exists() or integration.exists():parser.error("report already exists; choose a new output")
    initial={"schema":1,"status":"starting","calls":0,"failures":[],"production_scenes_touched":False,
        "target_hours":options.hours,"minimum_calls":options.minimum_calls,"integration_report":str(integration.resolve()),
        "started_at":datetime.now(timezone.utc).isoformat()}
    auth.atomic_json(options.output,initial)
    try:
        report=harness.run(options.houdini,True,10,integration,
            exercise=exercise_for(options.hours,options.minimum_calls,options.output))
        if not report.get("ok"):raise AssertionError("integration and recovery checks did not pass")
    except BaseException as exc:
        progress={**initial,**json.loads(options.output.read_text(encoding="utf-8"))}
        if progress["status"]!="failed":
            progress["failures"].append({"type":type(exc).__name__,"message":str(exc)[:1500]})
        progress.update(status="failed",finished_at=datetime.now(timezone.utc).isoformat())
        auth.atomic_json(options.output,progress)
        raise
    progress={**initial,**json.loads(options.output.read_text(encoding="utf-8"))}
    progress.update(status="passed",finished_at=datetime.now(timezone.utc).isoformat())
    auth.atomic_json(options.output,progress)
if __name__=="__main__":main()
