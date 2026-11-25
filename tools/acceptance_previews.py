"""Qualify previews against an already extracted candidate in an isolated GUI."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from collections import Counter
from pathlib import Path


STATE = """
import json
v=hou.ui.paneTabOfType(hou.paneTabType.SceneViewer).curViewport()
p=hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
b=p.visibleBounds()
print(json.dumps([hou.frame(),str(v.type()),[round(x,6) for x in v.viewTransform().asTuple()],
                  p.pwd().path(),list(b.min()),list(b.max())]))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--houdini", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.samples <= 200 or args.output.exists():
        parser.error("choose 1–200 samples and a new report path")
    candidate = args.bridge_root.resolve()
    manifest_hash = hashlib.sha256((candidate / "release_manifest.json").read_bytes()).hexdigest()
    sys.path.insert(0, str(candidate / "tools"))
    from acceptance_connection import run, wait_for
    from blib_hou_bridge.connection import Connection
    from blib_hou_bridge.tasks import TERMINAL

    def exercise(client, root, info, environment, command):
        connection = Connection(instance=info["session_id"])

        def tool(name, arguments):
            response = client.request("tools/call", {"name": name, "arguments": arguments}, timeout=45)["structuredContent"]
            if response.get("pending"):
                job_id = response["result"]["job_id"]

                def terminal():
                    record = connection.task_status(job_id)["result"]
                    return record if record["status"] in TERMINAL else None

                record = wait_for(terminal, 45)
                return record.get("response") or {"ok": False, "error": {"code": record["status"]}}
            return response

        def execute(code):
            response = tool("houdini_execute", {"code": code, "wait_ms": 5000})
            assert response["ok"] and response["result"]["status"] == "succeeded", response
            return response["result"]["stdout"]

        execute("p=hou.node('/obj/BRIDGE_ACCEPTANCE')\nb=p.createNode('box','PREVIEW_BOX')\nb.setDisplayFlag(True)")
        before = execute(STATE)
        counts = {mode: Counter() for mode in ("viewport", "network")}
        examples = {}
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        for index in range(args.samples):
            for mode, params in (
                ("viewport", {"target": "/obj/BRIDGE_ACCEPTANCE/PREVIEW_BOX", "view": "front",
                              "timeline_frame": 9, "width": 384, "height": 256}),
                ("network", {"path": "/obj/BRIDGE_ACCEPTANCE", "fit": True}),
            ):
                response = tool("houdini_look", {"mode": mode, **params})
                result = response.get("result") or {}
                if response["ok"] and result.get("image_status") == "nonblack":
                    counts[mode]["usable"] += 1
                    if mode not in examples:
                        image = output.with_suffix("." + mode + ".png")
                        with image.open("xb") as handle, Path(result["path"]).open("rb") as source:
                            shutil.copyfileobj(source, handle)
                        examples[mode] = str(image)
                else:
                    counts[mode][(response.get("error") or {}).get("code", "invalid_image")] += 1
                assert execute(STATE) == before, "View transform, frame or network pane changed after " + mode
        passed = all(mode["usable"] / args.samples >= .99 for mode in counts.values())
        return {"capture_count_per_mode": args.samples, "counts": counts, "samples": examples,
                "nonblack_rate_gate_passed": passed,
                "source_manifest_sha256": manifest_hash, "state_restored_after_every_capture": True}

    report = run(args.houdini, True, 10, args.output, exercise=exercise)
    if not report["task_checks"]["nonblack_rate_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
