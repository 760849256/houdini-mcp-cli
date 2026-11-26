"""Convenience MCP workflows; the bridge remains the authority for edits."""
from __future__ import annotations

import uuid
from copy import deepcopy

from blib_hou_bridge import protocol, recipes
from blib_hou_bridge.targets import TargetError, _resolve_edit_step, _resolve_path, _selected_path

NAMES = frozenset({"houdini_context", "houdini_inspect", "houdini_edit", "houdini_execute", "houdini_look", "houdini_job"})
EDIT_COMMANDS = protocol.DIRECT_EDIT_COMMANDS - {"python_submit"}


def tool_definitions():
    from .server import _tool_safety_contract
    manifest = protocol.command_manifest()["commands"]
    definitions = []
    entries = [
        ("houdini_edit", "run_plan", "Apply and structurally verify ordinary authorized edits in one task. For edit-and-show requests, pass preview to get the image in the same call. Read structural and preview statuses separately. If the preview fails, use houdini_job(action=preview) with the original edit ID. Read-only mode blocks edits. Common payload keys: create_node(parent,type,name), connect(src,dst,input_index), set_parm(node,parm,value), set_flags(node,display), layout(path).", {
            "steps": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object", "required": ["command", "payload"], "properties": {"command": {"type": "string", "enum": sorted(EDIT_COMMANDS)}, "payload": {"type": "object"}}, "additionalProperties": False}},
            "details": {"type": "boolean", "default": False},
            "preview": {"type": "object", "description": "Capture one image after verification. Provide target for a SOP, camera for a camera, or path for a network.",
                "properties": {
                    "mode": {"type": "string", "enum": ["viewport", "camera", "network"], "default": "viewport"},
                    "path": {"type": "string", "description": "Network path or alias for network mode."},
                    "fit": {"type": "boolean"},
                    **{key: deepcopy(value) for key, value in manifest["viewport_screenshot"]["payload_schema"]["properties"].items() if key != "prefix"},
                }, "additionalProperties": False},
            "expected_nodes": {"type": "object", "description": "Absolute path to session ID from inspect.target_ids; reject replaced targets."},
            "expected_parms": {"type": "object", "description": "Absolute path to parameter name/value map; require unchanged values before writing."},
            "context_token": {"type": "string", "description": "Token from context/inspect; reject stale context. Node fields may use @selected, @network, @selected_parent, and suffixes. Omitted node targets use a single selection; omitted create parent uses its parent or current network."},
        }, ["steps"]),
        ("houdini_execute", "python_submit", "Run custom Python in editable mode with Houdini process privileges. Use bridge_inputs and assign JSON to bridge_result. Waits up to 10 seconds by default, then returns a job_id for houdini_job. Never resubmit an uncertain job with a new ID. Python completion does not prove requested scene effects.", {
            "code": {"type": "string", "minLength": 1, "maxLength": 65536},
            "inputs": {"type": "object", "description": "JSON arguments exposed as bridge_inputs."},
            "job_id": {"type": "string", "format": "uuid", "description": "Reuse for retries of the same code. Generated if omitted."},
            "wait_ms": {"type": "integer", "minimum": 0, "maximum": 30000, "default": 10000},
        }, ["code"]),
        ("houdini_look", "viewport_screenshot", "See Houdini inline: viewport, camera, network, pane, both, angles (three directions), or frames (timeline strip). Target frames SOP/object bounds and may cook. Captures restore viewport, timeline and network state.", {
            "mode": {"type": "string", "enum": ["viewport", "network", "pane", "both", "camera", "angles", "frames"], "default": "viewport"},
            "path": {"type": "string", "description": "Network mode only; omit to capture the current editor."},
            "pane_type": {"type": "string", "default": "NetworkEditor"},
            "fit": {"type": "boolean", "default": False},
            "views": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": ["front", "right", "top"]}},
            "frames": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "number"}},
            **{key: deepcopy(value) for key, value in manifest["viewport_screenshot"]["payload_schema"]["properties"].items() if key != "prefix"},
        }, []),
        ("houdini_inspect", "multi_read", "Read a compact summary of selected nodes or the current network. Use root/query to focus, parm_names for values, kind=errors for messages and direct inputs, or details=true for full node data. Geometry requires cook=true.", {
            "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "kind": {"type": "string", "enum": ["node", "summary", "parms", "schema", "network", "upstream", "downstream", "errors", "geometry"], "default": "node"},
            "cook": {"type": "boolean", "default": False},
            "root": {"type": "string", "description": "Network path or @network."},
            "query": {"type": "string", "description": "Case-insensitive substring of a node name."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 20},
            "details": {"type": "boolean", "default": False},
            "fields": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string"}},
            "parm_names": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string"}},
        }, []),
        ("houdini_job", "python_job_status", "Read a Python job by its original UUID. Does not resubmit or cancel the job. No scene effects are inferred from Python completion.", {
            "job_id": {"type": "string", "format": "uuid"},
        }, ["job_id"]),
    ]
    controls = {"job_id": {"type": "string", "format": "uuid"},
                "wait_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
                "queue_timeout_ms": {"type": "integer", "minimum": 1, "maximum": 120000}}
    for name, command, description, properties, required in entries:
        if name != "houdini_job":
            properties = dict(properties, **{k: v for k, v in controls.items() if k not in properties})
        else:
            properties = dict(properties,
                action={"type": "string", "enum": ["status", "cancel", "list", "preview"], "default": "status"},
                session_id={"type": "string", "description": "Original instance ID for historical lookup or preview recovery."},
                preview_job_id={"type": "string", "format": "uuid", "description": "Optional ID for the read-only preview job. Distinct from the edit ID."},
                wait_ms={"type": "integer", "minimum": 0, "maximum": 30000},
                limit={"type": "integer", "minimum": 1, "maximum": 100},
                active_only={"type": "boolean"})
            required = []
            description = "Query, list or cooperatively cancel durable jobs. action=preview captures again from a verified edit's saved request in the original Houdini scene. Provide the original edit job_id. This action only captures an image."
        safety = _tool_safety_contract(command, manifest[command])
        safety["requires_user_approval_for_writes"] = False
        safety["authorization_note"] = "Use within the user's authorized task; do not request redundant approval. Existing bridge gates remain authoritative."
        if name == "houdini_edit":
            safety["review_flow_internal"] = True
        definitions.append({"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
            "_meta": {"bridgeCommand": command, "permission": safety["permission"], "exposure": safety["exposure"], "requiresEditMode": safety["requires_bridge_edit_mode"], "safety": safety}})
    return definitions


def call(adapter, name, arguments):
    from .server import _bridge_error, _tool_result
    try:
        if not isinstance(arguments, dict):
            raise ValueError("Arguments must be an object.")
        if name == "houdini_context":
            return _context(adapter, arguments)
        definition = next(t for t in tool_definitions() if t["name"] == name)
        unknown = set(arguments) - set(definition["inputSchema"]["properties"])
        if unknown:
            raise ValueError("Unknown arguments: %s" % ", ".join(sorted(unknown)))
        if name == "houdini_edit":
            return _edit(adapter, arguments)
        if name == "houdini_execute":
            return _execute(adapter, arguments)
        if name == "houdini_inspect":
            return _inspect(adapter, arguments)
        if name == "houdini_job":
            return _job(adapter, arguments)
        return _look(adapter, arguments)
    except TargetError as exc:
        return _tool_result({"ok": False, "result": {"status": "not_executed", "candidates": exc.candidates}, "error": {"code": "target_required", "message": str(exc)}}, is_error=True)
    except (ValueError, TypeError, protocol.BridgeProtocolError) as exc:
        return _tool_result(_bridge_error("bad_request", str(exc)), is_error=True)


def _edit_legacy(adapter, arguments):
    from .server import _tool_result
    steps = arguments.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("steps must contain 1 to 100 command objects.")
    if not isinstance(arguments.get("details", False), bool):
        raise ValueError("details must be boolean.")
    steps = deepcopy(steps)
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"command", "payload"}:
            raise ValueError("Each step requires only command and payload.")
        if not isinstance(step["command"], str) or step["command"] not in EDIT_COMMANDS:
            raise ValueError("Use the advanced plan workflow for this command: %s" % step.get("command"))
        if not isinstance(step["payload"], dict):
            raise ValueError("Each step payload must be an object.")
    context_response = adapter._rpc("context", {})
    if not context_response.get("ok"):
        return _tool_result(context_response, is_error=True)
    ctx = context_response["result"]
    token = ctx.get("context_token")
    if not token:
        raise ValueError("Reload the Houdini Bridge to enable guarded edits.")
    if arguments.get("context_token", token) != token:
        raise TargetError("Context changed. Inspect the current selection before editing.", ctx.get("selection", []))
    for step in steps:
        _resolve_edit_step(step, ctx)
        protocol.validate_command(step["command"], step["payload"])
    response = adapter._rpc("validate_plan", {"steps": steps})
    if not response.get("ok"):
        return _tool_result(response, is_error=True)
    validation = response["result"]
    review = recipes.review_plan(steps, validation)
    if not validation.get("ready_to_run") or review.get("level") == "blocked" or review.get("required_confirmations"):
        return _tool_result({"ok": False, "result": {"status": "not_executed", "review": review, "validation": validation},
            "error": {"code": "edit_preflight", "message": "No changes made. Resolve validation blockers or use the advanced review/validate/run/verify workflow for required confirmations."}}, is_error=True)
    run = adapter.call_tool("houdini_run_plan", {"steps": steps, "validation": validation, "review": review, "expected_context": token})["structuredContent"]
    if not run.get("ok"):
        # A transport failure may have happened after the write. Do not retry.
        return _tool_result(run, is_error=True)
    verified = adapter._rpc("verify_plan", {"steps": steps, "validation": validation, "run_result": run})
    proof = verified.get("result", {})
    executed = run.get("result", {})
    ok = bool(executed.get("ok") and verified.get("ok") and proof.get("verified"))
    result = {"status": "verified" if ok else "needs_inspection", "verified": ok,
        "ran": executed.get("ran"), "step_count": len(steps), "failed_step": executed.get("failed_step"),
        "impact": review.get("impact"), "warnings": review.get("warnings", []),
        "verification": proof if arguments.get("details") else {"status": proof.get("status"), "summary": proof.get("summary"), "checks": [c for c in proof.get("checks", []) if c.get("status") != "pass"]},
        "note": "Structural readback only; use houdini_look for visual inspection. Failed batches may have applied earlier steps; do not blindly repeat."}
    if arguments.get("details"):
        result["run"] = executed
    return _tool_result({"ok": ok, "result": result, "error": None if ok else verified.get("error") or {"code": "edit_not_verified", "message": "Inspect the partial run and readback before further edits."}}, is_error=not ok)


def _execute_legacy(adapter, arguments):
    from .server import _tool_result
    wait_ms = arguments.get("wait_ms", 10000)
    if isinstance(wait_ms, bool) or not isinstance(wait_ms, int) or not 0 <= wait_ms <= 30000:
        raise ValueError("wait_ms must be an integer from 0 to 30000.")
    payload = {"job_id": arguments.get("job_id", str(uuid.uuid4())), "code": arguments.get("code")}
    if "inputs" in arguments:
        payload["inputs"] = arguments["inputs"]
    protocol.validate_command("python_submit", payload)
    response = adapter._rpc("python_submit", payload)
    if response.get("ok"):
        response = adapter._wait_for_python_job(response, wait_ms)
    response.setdefault("result", {}).setdefault("job_id", payload["job_id"])
    job = response["result"]
    job["next_step"] = "inspect_requested_effects" if job.get("status") == "succeeded" else "query_job_status_before_retry"
    if job.get("status") in {"failed", "cancelled"}:
        response["ok"] = False
        response["error"] = {"code": "python_" + job["status"], "message": "Inspect the job output and scene before retrying."}
    return _tool_result(response, is_error=not response.get("ok"))


def _look_legacy(adapter, arguments):
    from .server import _append_viewport_image, _tool_result
    payload = dict(arguments)
    mode = payload.pop("mode", "viewport")
    if mode == "both":
        network_keys = {"path", "fit"}
        if "pane_type" in payload:
            raise ValueError("pane_type is supported only in pane mode.")
        viewport_args = {k: v for k, v in payload.items() if k not in network_keys}
        network_args = {k: v for k, v in payload.items() if k in network_keys}
        results = [_look_legacy(adapter, dict(viewport_args, mode="viewport")), _look_legacy(adapter, dict(network_args, mode="network"))]
        ok = all(not item["isError"] for item in results)
        combined = _tool_result({"ok": ok, "result": {"captures": [item["structuredContent"] for item in results]}}, is_error=not ok)
        for item in results:
            combined["content"].extend(block for block in item["content"] if block.get("type") == "image")
        return combined
    if mode == "viewport":
        command = "viewport_screenshot"
        payload.setdefault("width", 768)
        payload.setdefault("height", 432)
    elif mode == "network":
        if "path" in payload:
            command = "network_screenshot"
            payload.setdefault("fit", False)
        else:
            command = "pane_screenshot"
            payload["type"] = "NetworkEditor"
    elif mode == "pane":
        command = "pane_screenshot"
        payload["type"] = payload.pop("pane_type", "NetworkEditor")
    else:
        raise ValueError("mode must be viewport, network or pane.")
    allowed = set(protocol.command_manifest()["commands"][command]["payload_schema"]["properties"])
    if set(payload) - allowed:
        raise ValueError("Arguments not supported in %s mode: %s" % (mode, ", ".join(sorted(set(payload) - allowed))))
    if command in {"network_screenshot", "pane_screenshot"}:
        payload["restore"] = True
    path_keys = [key for key in ("path", "target", "camera") if key in payload]
    if any(isinstance(payload[key], str) and payload[key].startswith("@") for key in path_keys):
        response = adapter._rpc("context", {})
        if not response.get("ok"):
            return _tool_result(response, is_error=True)
        for key in path_keys:
            payload[key] = _resolve_path(payload[key], response["result"])
    response = adapter._rpc(command, payload)
    if response.get("ok") and response.get("result", {}).get("image_status") in {"all_black", "unreadable"}:
        response["ok"] = False
        response["error"] = {"code": "unusable_preview", "message": "The viewport preview is all black or unreadable; it is not usable visual evidence. Check the visible Scene View and capture again."}
    if not response.get("ok"):
        message = str((response.get("error") or {}).get("message", "")).lower()
        if "no houdini" in message or "not available" in message:
            category = "window_unavailable"
        elif (response.get("error") or {}).get("code") == "timeout_unknown":
            category = "capture_timeout"
        else:
            category = "capture_failed"
        response.setdefault("result", {})["capture_status"] = category
    result = _tool_result(response, is_error=not response.get("ok"))
    if response.get("ok"):
        _append_viewport_image(result, response)
        if not any(block.get("type") == "image" for block in result["content"]):
            response["ok"] = False
            response["error"] = {"code": "image_unavailable", "message": "Capture returned but the image could not be attached."}
            return _tool_result(response, is_error=True)
    return result


def _context(adapter, arguments):
    from .server import _tool_result
    arguments = deepcopy(arguments)
    instance = arguments.pop("instance", None)
    if instance is not None:
        from blib_hou_bridge.connection import ConnectionError, failure
        try:
            adapter._connection.bind(instance)
        except ConnectionError as exc:
            return _tool_result(failure(exc.code, str(exc), candidates=exc.candidates, executed=False), is_error=True)
    response = adapter._rpc("context", arguments)
    if response.get("ok"):
        ctx = response["result"]
        if adapter._connection.bound_instance:
            ctx["session_id"] = adapter._connection.bound_instance
        if adapter._connection.port is not None:
            ctx["port"] = adapter._connection.port
        if adapter._connection.control_code is not None:
            ctx["control_code"] = adapter._connection.control_code
        if not arguments.get("fields"):
            selection = ctx.get("selection", [])
            ctx["routing"] = {"target": selection[0].get("path") if len(selection) == 1 else None,
                "network": ctx.get("current_network"), "selection_count": len(selection),
                "edit_target_status": "single_selection" if len(selection) == 1 else "explicit_target_required"}
    return _tool_result(response, is_error=not response.get("ok"))


def _inspect_legacy(adapter, arguments):
    from .server import _tool_result
    kind = arguments.get("kind", "node")
    mapping = {"node": "node_info", "parms": "node_parms", "schema": "parameter_schema", "network": "network", "upstream": "upstream", "downstream": "downstream", "errors": "find_error_nodes", "geometry": "geometry_summary"}
    if kind not in mapping or not isinstance(arguments.get("cook", False), bool):
        raise ValueError("Invalid inspect kind or cook flag.")
    if kind == "geometry" and not arguments.get("cook"):
        raise ValueError("Geometry inspection may cook; set cook=true explicitly.")
    targets = arguments.get("targets")
    if targets is not None and (not isinstance(targets, list) or not 1 <= len(targets) <= 20 or not all(isinstance(p, str) for p in targets)):
        raise ValueError("targets must contain 1 to 20 paths.")
    response = adapter._rpc("context", {})
    if not response.get("ok"):
        return _tool_result(response, is_error=True)
    ctx = response["result"]
    if targets is None:
        targets = [n["path"] for n in ctx.get("selection", [])]
        if not targets:
            targets = ["@network"]
            if kind == "node":
                kind = "network"
    if len(targets) > 20:
        raise TargetError("Too many selected nodes. Supply up to 20 targets.", ctx.get("selection", []))
    paths = [_resolve_path(p, ctx) for p in targets]
    key = "root" if kind == "errors" else "path"
    steps = [{"command": mapping[kind], "payload": {key: path}} for path in paths]
    response = adapter._rpc("multi_read", {"steps": steps})
    if response.get("ok"):
        response["result"]["context_token"] = ctx.get("context_token")
        response["result"]["resolved_targets"] = paths
        if response["result"].get("ok") is False:
            response["ok"] = False
            response["error"] = {"code": "partial_read", "message": "Some targets could not be inspected; see per-target results."}
    return _tool_result(response, is_error=not response.get("ok"))


def _controls(arguments, default_wait=1000):
    payload = deepcopy(arguments)
    job_id = str(uuid.UUID(payload.pop("job_id", None) or str(uuid.uuid4())))
    wait_ms = payload.pop("wait_ms", default_wait)
    queue_ms = payload.pop("queue_timeout_ms", 15000)
    if type(wait_ms) is not int or not 0 <= wait_ms <= 30000:
        raise ValueError("wait_ms must be an integer from 0 to 30000.")
    if type(queue_ms) is not int or not 1 <= queue_ms <= 120000:
        raise ValueError("queue_timeout_ms must be an integer from 1 to 120000.")
    return payload, job_id, wait_ms, queue_ms


def _pending(response):
    return bool(response.get("pending"))


def _edit(adapter, arguments):
    if adapter._connection._custom:
        if "preview" in arguments:
            return _tool_result_legacy_feature("Editing with a preview requires the task-aware Bridge service.")
        return _edit_legacy(adapter, arguments)
    from .server import _tool_result
    payload, job_id, wait_ms, queue_ms = _controls(arguments, 1500 if "preview" in arguments else 1000)
    expected = payload.pop("context_token", None)
    if expected is not None:
        payload["expected_context"] = expected
    response = adapter._task_call("edit_batch", payload, job_id, wait_ms, queue_ms)
    result = _tool_result(response, is_error=not response.get("ok"))
    preview = (response.get("result") or {}).get("preview") or {}
    if preview.get("status") != "captured":
        return result
    _attach_images(result, preview["capture"])
    if any(block.get("type") == "image" for block in result["content"]):
        return result
    preview["delivery_status"] = "unavailable"
    return _tool_result(response, is_error=False)


def _execute(adapter, arguments):
    if adapter._connection._custom:
        return _execute_legacy(adapter, arguments)
    from .server import _tool_result
    payload, job_id, wait_ms, queue_ms = _controls(arguments, 10000)
    payload["job_id"] = job_id
    response = adapter._task_call("python_submit", payload, job_id, wait_ms, queue_ms)
    response.setdefault("result", {}).setdefault("job_id", job_id)
    return _tool_result(response, is_error=not response.get("ok"))


def _inspect(adapter, arguments):
    if adapter._connection._custom:
        if arguments.get("kind") == "summary" or {"root", "query", "limit"} & arguments.keys():
            return _tool_result_legacy_feature("Scoped inspection requires the task-aware Bridge service.")
        return _inspect_legacy(adapter, arguments)
    from .server import _tool_result
    payload, job_id, wait_ms, queue_ms = _controls(arguments)
    response = adapter._task_call("inspect_batch", payload, job_id, wait_ms, queue_ms)
    return _tool_result(response, is_error=not response.get("ok"))


def _tool_result_legacy_feature(message):
    from .server import _bridge_error, _tool_result
    return _tool_result(_bridge_error("upgrade_required", message), is_error=True)


def _attach_images(result, response):
    from .server import _append_viewport_image
    data = response.get("result") or {}
    if data.get("sheet"):
        _append_viewport_image(result, {"result": data["sheet"]})
        return
    captures = data.get("captures")
    if isinstance(captures, list):
        for capture in captures:
            _attach_images(result, capture)
    elif response.get("ok"):
        _append_viewport_image(result, response)


def _look(adapter, arguments):
    if adapter._connection._custom:
        if arguments.get("mode") in {"angles", "frames", "camera"}:
            return _tool_result_legacy_feature("This preview mode requires the task-aware Bridge service.")
        return _look_legacy(adapter, arguments)
    payload, job_id, wait_ms, queue_ms = _controls(arguments, 1500)
    response = adapter._task_call("look_batch", payload, job_id, wait_ms, queue_ms)
    return _look_result(response)


def _look_result(response):
    from .server import _tool_result
    if response.get("result", {}).get("image_status") in {"all_black", "unreadable"}:
        response.update(ok=False, error={"code": "unusable_preview", "message": "The captured preview is black or unreadable."})
    result = _tool_result(response, is_error=not response.get("ok"))
    if not _pending(response):
        _attach_images(result, response)
        if response.get("ok") and not any(block.get("type") == "image" for block in result["content"]):
            response.update(ok=False, error={"code": "image_unavailable", "message": "The capture finished but the image could not be attached."})
            return _tool_result(response, is_error=True)
    return result


def _job(adapter, arguments):
    from .server import _tool_result
    action = arguments.get("action", "status")
    if adapter._connection._custom:
        if action != "status":
            raise ValueError("This legacy transport supports Python status only.")
        response = adapter._rpc("python_job_status", {"job_id": arguments.get("job_id")})
    elif action == "list":
        response = adapter._connection.list_tasks(arguments.get("limit", 20), arguments.get("active_only", False))
    elif action == "cancel":
        response = adapter._connection.cancel_task(arguments.get("job_id"))
    elif action == "status":
        response = adapter._connection.task_status(arguments.get("job_id"), arguments.get("session_id"))
    elif action == "preview":
        original = str(uuid.UUID(arguments.get("job_id") or ""))
        retry = {"job_id": arguments.get("preview_job_id"), "edit_job_id": original}
        for key in ("session_id", "wait_ms"):
            if key in arguments:
                retry[key] = arguments[key]
        payload, job_id, wait_ms, queue_ms = _controls(retry, 1500)
        if job_id == original:
            raise ValueError("preview_job_id must differ from the original edit ID.")
        return _look_result(adapter._task_call("look_batch", payload, job_id, wait_ms, queue_ms))
    else:
        raise ValueError("action must be status, cancel, list or preview.")
    result = _tool_result(response, is_error=not response.get("ok"))
    record = response.get("result") or {}
    terminal = record.get("response") or {}
    if record.get("command") == "look_batch" and terminal:
        _attach_images(result, terminal)
    elif record.get("command") == "edit_batch":
        preview = (terminal.get("result") or {}).get("preview") or {}
        if preview.get("status") == "captured":
            _attach_images(result, preview["capture"])
    return result
