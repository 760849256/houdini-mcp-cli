"""Compare immutable Bridge roots over stdio/HTTP/real GUI Houdini."""
from __future__ import annotations
import argparse, json, statistics, time
from pathlib import Path
import acceptance_connection as harness

def stats(values):
    ordered=sorted(values)
    return {"p50":round(statistics.median(ordered),3),
            "p95":round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],3),
            "max":round(max(ordered),3)}

def workload(samples,sizes,output):
    def exercise(client,root,info,environment,command):
        result={"samples_per_scenario":samples,"sizes":[],"scope":"model latency excluded; same operations on each revision"}
        def save():
            output.write_text(json.dumps(result,indent=2),encoding="utf-8")
        def tool(name,args,timeout=60):
            reply=client.request("tools/call",{"name":name,"arguments":args},timeout)
            body=reply["structuredContent"]
            if body.get("pending") or (body.get("result", {}).get("status") in {"queued", "running"} and body["result"].get("job_id")):
                job_id=body["result"]["job_id"]
                end=time.monotonic()+120
                while time.monotonic()<end:
                    status=client.request("tools/call",{"name":"houdini_job","arguments":{"job_id":job_id}})["structuredContent"]["result"]
                    if status["status"] in {"succeeded","failed","partial","cancelled","expired","outcome_unknown"}:
                        body=status.get("response") or {"ok":status["status"]=="succeeded","result":status}
                        break
                    time.sleep(.05)
            assert body.get("ok"),body
            assert not reply.get("isError"),reply
            return reply,body
        for size in sizes:
            code="""import time,json
begin=time.perf_counter()
parent=hou.node('/obj/BRIDGE_ACCEPTANCE')
for child in parent.children(): child.destroy()
for i in range(20): parent.createNode('xform','BENCH_%02d'%i,run_init_scripts=False)
for i in range(SIZE-20): parent.createNode('null','FILL_%05d'%i,run_init_scripts=False)
print(json.dumps({'setup_seconds':time.perf_counter()-begin,'node_count':len(parent.children())}))
""".replace("SIZE",str(size))
            _,setup=tool("houdini_execute",{"code":code,"wait_ms":30000})
            from blib_hou_bridge.connection import Connection
            harness.wait_for(lambda:Connection(instance=info["session_id"]).snapshot().get("state")=="ready",15)
            paths=["/obj/BRIDGE_ACCEPTANCE/BENCH_%02d"%i for i in range(20)]
            row={"nodes":size,"setup":setup.get("result",{}).get("stdout"),"scenarios":{}}
            for scenario in ("context","inspect_20","edit_20"):
                times=[];bytes_out=[];queue=[];host=[]
                for sample in range(samples+3):
                    if scenario=="context":
                        name="houdini_context";args={"fields":["hip_name","current_network","context_token"]}
                    elif scenario=="inspect_20":
                        name="houdini_inspect";args={"kind":"node","targets":paths}
                    else:
                        name="houdini_edit";args={"steps":[{"command":"set_parm","payload":{
                            "node":path,"parm":"tx","value":float(sample%5)}} for path in paths]}
                    began=time.perf_counter();reply,body=tool(name,args)
                    elapsed=(time.perf_counter()-began)*1000
                    if scenario=="edit_20":assert body["result"]["verified"],body
                    if sample>=3:
                        times.append(elapsed)
                        bytes_out.append(len(json.dumps(reply,ensure_ascii=False).encode("utf-8")))
                        receipt=body.get("task",{})
                        if receipt.get("execute_ms") is not None:host.append(receipt["execute_ms"])
                        if receipt.get("queue_ms") is not None:queue.append(receipt["queue_ms"])
                row["scenarios"][scenario]={"latency_ms":stats(times),"response_bytes":stats(bytes_out),
                    "host_execute_ms":stats(host) if host else None,"queue_ms":stats(queue) if queue else None}
                result["current_nodes"]=size;result["current_scenario"]=scenario
                save()
            if "durable_tasks_v1" in Connection(instance=info["session_id"])._probe(
                    Connection(instance=info["session_id"])._resolve()).get("result",{}).get("capabilities",[]):
                reply,_=tool("houdini_inspect",{"kind":"node","targets":paths,"fields":["path","type","flags"]})
                row["focused_inspect_response_bytes"]=len(json.dumps(reply).encode())
            _,native=tool("houdini_execute",{"code":"""import time,json,statistics
paths=PATHS
read=[];write=[]
for s in range(100):
 b=time.perf_counter()
 for p in paths: hou.node(p).parm('tx').eval()
 read.append((time.perf_counter()-b)*1000)
 b=time.perf_counter()
 for p in paths: hou.node(p).parm('tx').set(float(s%5))
 write.append((time.perf_counter()-b)*1000)
print(json.dumps({'native_20_parm_read_p50_ms':statistics.median(read),'native_20_parm_write_p50_ms':statistics.median(write)}))
""".replace("PATHS",repr(paths)),"wait_ms":10000})
            row["native_reference"]=native.get("result",{}).get("stdout")
            result["sizes"].append(row);save()
        result["ok"]=True;save()
        return {"benchmark_file":str(output),"ok":True}
    return exercise

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--houdini",type=Path,required=True)
    parser.add_argument("--bridge-root",type=Path,default=harness.ROOT)
    parser.add_argument("--samples",type=int,default=100)
    parser.add_argument("--sizes",type=int,nargs="+",default=[200,2000,10000])
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    harness.ROOT=args.bridge_root.resolve()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    harness.run(args.houdini,True,5,args.output.with_suffix(".integration.json"),
                exercise=workload(args.samples,args.sizes,args.output))
if __name__=="__main__":main()
