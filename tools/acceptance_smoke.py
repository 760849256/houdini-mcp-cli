"""Run recipient-facing acceptance smoke checks for the bridge-only package."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "cli" / "blib_hou.py"
MCP_CLI = ROOT / "scripts" / "cli" / "blib_hou_mcp.py"
WORKFLOW_ROOT = ROOT / ".blib_hou_workflows"
ACCEPTANCE_WORKFLOW = "acceptance_smoke"
REQUIRED_PROOF_FILES = [
    "summary.md",
    "evidence_checklist.json",
    "proof_report.json",
    "evidence_manifest.json",
]


def _run(args: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    payload: object = {}
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"raw_stdout": completed.stdout}
    return {
        "args": [sys.executable, *args],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": payload,
        "ok": completed.returncode == 0,
    }


def _step(name: str, args: list[str], required: bool = True) -> dict:
    result = _run(args)
    status = "pass" if result["ok"] else ("fail" if required else "warn")
    message = "ok" if result["ok"] else _diagnostic_message(name, result)
    return {
        "name": name,
        "status": status,
        "required": required,
        "command": " ".join(args),
        "returncode": result["returncode"],
        "message": message,
        "result": result["json"],
    }


def _diagnostic_message(name: str, result: dict) -> str:
    payload = result.get("json")
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code == "offline":
        return "Bridge is offline. Start Houdini, click the Blib Bridge shelf tool, then rerun acceptance smoke."
    if code in {"unauthorized", "token_invalid"}:
        return "Bridge token or authorization failed. Reread the current session file and retry."
    if name == "workflow_run":
        return "Workflow did not produce a proven run. Read proof_report.json and evidence_checklist.json."
    stderr = str(result.get("stderr") or "").strip()
    return stderr or "Command failed; inspect the JSON result for details."


def _write_acceptance_plan(workflow_dir: Path, parent: str, node_name: str) -> None:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    plan = [
        {
            "command": "create-node",
            "payload": {
                "parent": parent,
                "type": "null",
                "name": node_name,
            },
        },
        {
            "command": "set-comment",
            "payload": {
                "node": "%s/%s" % (parent.rstrip("/"), node_name),
                "comment": "Blib Bridge acceptance smoke proof node.",
            },
        },
    ]
    (workflow_dir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def _workflow_evidence_step(workflow_dir: Path) -> dict:
    missing = [name for name in REQUIRED_PROOF_FILES if not (workflow_dir / name).exists()]
    if missing:
        return {
            "name": "workflow_evidence",
            "status": "fail",
            "required": True,
            "message": "Missing proof artifacts: %s" % ", ".join(missing),
            "missing": missing,
        }
    proof = json.loads((workflow_dir / "proof_report.json").read_text(encoding="utf-8"))
    checklist = json.loads((workflow_dir / "evidence_checklist.json").read_text(encoding="utf-8"))
    proof_ready = bool(proof.get("proof_ready"))
    checklist_ready = bool(checklist.get("proof_ready"))
    return {
        "name": "workflow_evidence",
        "status": "pass" if proof_ready and checklist_ready else "fail",
        "required": True,
        "message": "proof_ready=%s checklist_ready=%s" % (proof_ready, checklist_ready),
        "proof_report": {
            "verdict": proof.get("verdict"),
            "proof_ready": proof.get("proof_ready"),
            "next_action": proof.get("next_action"),
        },
        "evidence_checklist": {
            "status": checklist.get("status"),
            "proof_ready": checklist.get("proof_ready"),
        },
    }


def run_acceptance(include_write: bool = False, parent: str = "/obj", node_name: str = "BLIB_BRIDGE_ACCEPTANCE") -> dict:
    if include_write and node_name == "BLIB_BRIDGE_ACCEPTANCE":
        node_name = "BLIB_BRIDGE_ACCEPTANCE_%s" % time.strftime("%Y%m%d_%H%M%S")
    steps = [
        _step("doctor", [str(CLI), "doctor"]),
        _step("scene_snapshot", [str(CLI), "scene-snapshot", "--path", parent]),
        _step("mcp_status", [str(MCP_CLI), "--status"]),
    ]

    workflow_dir = WORKFLOW_ROOT / ACCEPTANCE_WORKFLOW
    if include_write:
        _write_acceptance_plan(workflow_dir, parent, node_name)
        steps.append(_step("workflow_preflight_review", [str(CLI), "workflow", "review", str(workflow_dir)], required=False))
        steps.append(
            _step(
                "workflow_run",
                [str(CLI), "workflow", "run", str(workflow_dir), "--enable-edit-mode", "--evidence", "standard"],
            )
        )
        steps.append(_step("workflow_report", [str(CLI), "workflow", "report", str(workflow_dir)]))
        steps.append(_workflow_evidence_step(workflow_dir))
        steps.append(_step("edit_mode_off", [str(CLI), "edit-mode", "off"], required=False))
    else:
        steps.append(
            {
                "name": "controlled_write",
                "status": "skipped",
                "required": False,
                "message": "Pass --include-write to run the review/validate/run/verify workflow acceptance edit.",
            }
        )

    required_failures = [step for step in steps if step.get("required") and step.get("status") != "pass"]
    return {
        "ok": not required_failures,
        "bridge_root": str(ROOT),
        "include_write": include_write,
        "acceptance_node": "%s/%s" % (parent.rstrip("/"), node_name) if include_write else None,
        "workflow_dir": str(workflow_dir) if include_write else None,
        "steps": steps,
        "summary": {
            "required": len([step for step in steps if step.get("required")]),
            "failed": len(required_failures),
            "skipped": len([step for step in steps if step.get("status") == "skipped"]),
        },
        "next_action": "accepted" if not required_failures else "read_failed_step_messages",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acceptance_smoke", description="Run recipient-facing Bridge acceptance smoke checks.")
    parser.add_argument("--include-write", action="store_true", help="Run a controlled workflow edit through review/validate/run/verify.")
    parser.add_argument("--parent", default="/obj", help="Parent network path for read smoke and optional write workflow.")
    parser.add_argument("--node-name", default="BLIB_BRIDGE_ACCEPTANCE", help="Node name for the optional write workflow.")
    args = parser.parse_args(argv)

    report = run_acceptance(include_write=args.include_write, parent=args.parent, node_name=args.node_name)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
