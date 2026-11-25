"""Host-side guarded daily workflows executed as one queryable task."""
from __future__ import annotations
from copy import deepcopy
from itertools import islice
from uuid import UUID
from . import execution, protocol, recipes, state
from .targets import TargetError, _resolve_edit_step, _resolve_path

EDIT_COMMANDS = protocol.DIRECT_EDIT_COMMANDS - {"python_submit"}
INSPECTION_COMMANDS = {"node": "node_info", "parms": "node_parms", "schema": "parameter_schema",
                       "network": "network", "upstream": "upstream", "downstream": "downstream",
                       "errors": "find_error_nodes", "geometry": "geometry_summary"}


def needs_context(payload):
    preview = payload.get("preview") or {}
    if (preview.get("mode") == "network" and "path" not in preview) or any(
        isinstance(preview.get(key), str) and preview[key].startswith("@")
        for key in ("path", "camera", "target")
    ):
        return True
    for step in payload.get("steps", []):
        command, args = step.get("command"), step.get("payload", {})
        key = {"create_node": "parent", "layout": "path", "select": "path",
               "connect": "src", "set_input": "dst"}.get(command, "node")
        if command != "set_frame" and key not in args:
            return True
        if any(isinstance(args.get(k), str) and args[k].startswith("@")
               for k in ("node", "parent", "path", "src", "dst")):
            return True
    return False


def validate(command, payload):
    if not isinstance(payload, dict):
        raise protocol.BridgeProtocolError("Task payload must be an object.")
    if command == "look_batch":
        if "edit_job_id" in payload:
            if set(payload) - {"edit_job_id", "session_id"}:
                raise protocol.BridgeProtocolError("Edit preview recovery uses only the original edit job ID and optional instance ID.")
            for key in ("edit_job_id", "session_id"):
                if key in payload:
                    try:
                        UUID(payload[key])
                    except (ValueError, TypeError, AttributeError) as exc:
                        raise protocol.BridgeProtocolError(key + " must be a UUID.") from exc
            return
        mode = payload.get("mode", "viewport")
        if mode not in {"viewport", "network", "pane", "both", "camera", "angles", "frames"}:
            raise protocol.BridgeProtocolError("Invalid capture mode.")
        if "views" in payload:
            views = payload["views"]
            if mode != "angles" or not isinstance(views, list) or not 1 <= len(views) <= 3 or not all(
                isinstance(view, str) and view in {"front", "right", "top"} for view in views
            ):
                raise protocol.BridgeProtocolError("angles supports 1 to 3 views: front, right or top.")
        if "frames" in payload:
            frames = payload["frames"]
            if mode != "frames" or not isinstance(frames, list) or not 1 <= len(frames) <= 6 or not all(
                type(frame) in (int, float) and -100000 <= frame <= 100000 for frame in frames
            ):
                raise protocol.BridgeProtocolError("frames supports 1 to 6 valid timeline frame numbers.")
        if mode == "angles" and (payload.get("camera") or "view" in payload):
            raise protocol.BridgeProtocolError("Use views and target for angles.")
        if mode == "frames" and "timeline_frame" in payload:
            raise protocol.BridgeProtocolError("Use frames for a timeline strip.")
        return
    if command == "edit_batch":
        if set(payload) - {"steps", "expected_context", "details", "expected_nodes", "expected_parms", "preview"}:
            raise protocol.BridgeProtocolError("Unexpected edit task fields.")
        if "preview" in payload:
            _edit_preview_request(payload["preview"])
        steps = payload.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise protocol.BridgeProtocolError("Edit tasks require 1 to 100 steps.")
        if "expected_context" in payload and (not isinstance(payload["expected_context"], str) or not payload["expected_context"]):
            raise protocol.BridgeProtocolError("expected_context must be a nonempty token.")
        for field in ("expected_nodes", "expected_parms"):
            value = payload.get(field, {})
            if not isinstance(value, dict) or len(value) > 100 or not all(isinstance(p, str) and p.startswith("/") for p in value):
                raise protocol.BridgeProtocolError(field + " requires at most 100 absolute paths.")
        if not all(type(v) is int for v in payload.get("expected_nodes", {}).values()):
            raise protocol.BridgeProtocolError("expected_nodes values must be node session IDs.")
        if not all(isinstance(v, dict) and len(v) <= 100 for v in payload.get("expected_parms", {}).values()):
            raise protocol.BridgeProtocolError("expected_parms requires bounded parameter maps.")
        for step in steps:
            if not isinstance(step, dict) or set(step) != {"command", "payload"} or step["command"] not in EDIT_COMMANDS or not isinstance(step["payload"], dict):
                raise protocol.BridgeProtocolError("Edit task contains an unsupported command.")
        if type(payload.get("details", False)) is not bool:
            raise protocol.BridgeProtocolError("details must be boolean.")
    else:
        if set(payload) - {"kind", "targets", "cook", "expected_context", "fields", "parm_names",
                           "root", "query", "limit", "details"}:
            raise protocol.BridgeProtocolError("Unexpected inspect task fields.")
        kind = payload.get("kind", "node")
        if kind not in {*INSPECTION_COMMANDS, "summary"} or type(payload.get("cook", False)) is not bool:
            raise protocol.BridgeProtocolError("Invalid inspection kind or cook flag.")
        if type(payload.get("details", False)) is not bool:
            raise protocol.BridgeProtocolError("details must be boolean.")
        for name in ("root", "query"):
            if name in payload and (not isinstance(payload[name], str) or not payload[name].strip()
                                    or len(payload[name]) > 512):
                raise protocol.BridgeProtocolError(name + " must be a nonempty string of at most 512 characters.")
        if payload.get("targets") is not None and "root" in payload:
            raise protocol.BridgeProtocolError("Choose root or explicit targets.")
        if any(name in payload for name in ("root", "query", "limit")) and kind not in {"node", "summary", "errors"}:
            raise protocol.BridgeProtocolError("root, query and limit apply to node, summary or errors.")
        limit = payload.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise protocol.BridgeProtocolError("limit must be from 1 to 20.")
        if payload.get("kind") == "geometry" and not payload.get("cook"):
            raise protocol.BridgeProtocolError("Geometry inspection requires cook=true.")
        targets = payload.get("targets")
        if targets is not None and (not isinstance(targets, list) or not 1 <= len(targets) <= 20 or not all(isinstance(p, str) for p in targets)):
            raise protocol.BridgeProtocolError("Inspection requires at most 20 paths.")
        for field in ("fields", "parm_names"):
            if field in payload and (not isinstance(payload[field], list) or not 1 <= len(payload[field]) <= 100 or not all(isinstance(v, str) and v for v in payload[field])):
                raise protocol.BridgeProtocolError(field + " requires 1 to 100 names.")


def _edit_preview_request(preview, ctx=None):
    if not isinstance(preview, dict):
        raise protocol.BridgeProtocolError("preview must be an object.")
    request = dict(preview)
    mode = request.pop("mode", "viewport")
    if mode not in ("viewport", "camera", "network"):
        raise protocol.BridgeProtocolError("An edit preview is one viewport, camera, or network image.")
    if mode == "network":
        command = "network_screenshot"
        allowed = {"path", "fit"}
        request.setdefault("path", "@network")
    else:
        command = "viewport_screenshot"
        allowed = {"target", "width", "height", "view", "frame", "timeline_frame"}
        if mode == "camera":
            allowed.add("camera")
            if not request.get("camera") or request.get("target") or request.get("view", "current") != "current" or request.get("frame", "current") != "current":
                raise protocol.BridgeProtocolError("Camera preview requires an explicit camera and the current view.")
    if request.keys() - allowed:
        raise protocol.BridgeProtocolError("Unsupported option for this edit preview mode.")
    for key in ("path", "target", "camera"):
        if key not in request:
            continue
        value = request[key]
        if not isinstance(value, str) or (not value.startswith("/") and value.split("/", 1)[0] not in {"@network", "@selected", "@selected_parent"}):
            raise protocol.BridgeProtocolError(key + " must be an absolute Houdini path or a context alias.")
        request[key] = _resolve_path(value, ctx) if ctx is not None else "/obj"
    protocol.validate_command(command, request)
    return dict(request, mode=mode)


def _capture_edit_preview(hou, preview, scene_epoch):
    result = {"request": preview, "scene_epoch": scene_epoch}
    try:
        execution.checkpoint()
        if state.scene_epoch() != scene_epoch:
            raise execution.TaskRejected("scene_changed", "The HIP changed after edit verification.")
        capture = look(hou, preview)
    except execution.TaskCancelled as exc:
        return dict(result, status="cancelled", error={"code": "cancelled", "message": str(exc)})
    except execution.TaskRejected as exc:
        return dict(result, status="failed", error={"code": exc.code, "message": str(exc)})
    except Exception as exc:
        return dict(result, status="failed", error={"code": "capture_failed", "message": str(exc)})
    if capture.get("ok"):
        return dict(result, status="captured", capture=capture)
    return dict(result, status="failed", error=capture.get("error"))


def edit(hou, payload):
    from . import commands
    execution.checkpoint()
    ctx = commands.context(hou)
    if payload.get("expected_context") and payload["expected_context"] != ctx["context_token"]:
        raise execution.TaskRejected("context_changed", "Scene, selection or network changed while the edit was queued.")
    preview = _edit_preview_request(payload["preview"], ctx) if "preview" in payload else None
    scene_epoch = state.scene_epoch()
    for path, identity in payload.get("expected_nodes", {}).items():
        node = hou.node(path)
        if node is None or node.sessionId() != identity:
            raise execution.TaskRejected("target_changed", "A target node was replaced or deleted: " + path)
    for path, values in payload.get("expected_parms", {}).items():
        for name, expected in values.items():
            check = commands._verify_parm_value(hou, path, name, expected, "precondition", None)
            if check["status"] != "pass":
                raise execution.TaskRejected("precondition_changed", "A target parameter changed: " + path + "/" + name)
    steps = deepcopy(payload["steps"])
    for step in steps:
        _resolve_edit_step(step, ctx)
        protocol.validate_command(step["command"], step["payload"])
    validation = commands.validate_plan(hou, {"steps": steps})
    review = recipes.review_plan(steps, validation)
    if not validation.get("ready_to_run") or review.get("level") == "blocked" or review.get("required_confirmations"):
        response = protocol.error_response("", "edit_batch", "Resolve validation blockers or use the advanced plan workflow.", "edit_preflight")
        response["result"] = {"status": "not_executed", "review": review, "validation": validation}
        return response
    execution.checkpoint()
    executed = commands.run_plan(hou, {"steps": steps, "expected_context": ctx["context_token"]}, _validation=validation)
    run = protocol.ok_response("", "run_plan", executed)
    execution.checkpoint()
    proof = commands.verify_plan(hou, {"steps": steps, "validation": validation, "run_result": run})
    ok = bool(executed.get("ok") and proof.get("verified"))
    applied = [item for item in executed.get("results", []) if item["response"].get("ok")]
    changed = set()
    for item in applied:
        step = steps[item["index"]]
        output = item["response"].get("result") or {}
        for key in ("path", "node", "dst"):
            value = output.get(key) or step["payload"].get(key)
            if isinstance(value, str) and value.startswith("/"):
                changed.add(value)
    result = {"status": "verified" if ok else "needs_inspection", "verified": ok,
              "changed_paths": sorted(changed), "applied_steps": len(applied),
              "ran": executed.get("ran"), "step_count": len(steps), "failed_step": executed.get("failed_step"),
              "impact": review.get("impact"), "warnings": review.get("warnings", []),
              "verification": proof if payload.get("details") else {
                  "status": proof.get("status"), "summary": proof.get("summary"),
                  "checks": [c for c in proof.get("checks", []) if c.get("status") != "pass"]},
              "note": "Structural readback only. A failed or cancelled batch may have applied earlier steps."}
    if payload.get("details"):
        result["run"] = executed
    if preview is not None:
        if ok:
            if preview["mode"] != "network" and "timeline_frame" not in preview:
                frame = commands._safe_call(lambda: hou.frame())
                if frame is not None:
                    preview["timeline_frame"] = frame
            result["preview"] = _capture_edit_preview(hou, preview, scene_epoch)
        else:
            result["preview"] = {"status": "skipped"}
    response = protocol.ok_response("", "edit_batch", result)
    if not ok:
        response.update(ok=False, error={"code": "edit_not_verified", "message": "Inspect partial changes and readback before further edits."})
    return response


def inspect(hou, payload):
    from . import commands
    execution.checkpoint()
    targets = payload.get("targets")
    needs_resolution = targets is None or any(not p.startswith("/") for p in targets)
    ctx = commands.context(hou) if needs_resolution else {"context_token": commands._context_token(hou)}
    if payload.get("expected_context") and payload["expected_context"] != ctx["context_token"]:
        raise execution.TaskRejected("context_changed", "Inspection context changed while queued.")
    kind = payload.get("kind", "node")
    selected = [n["path"] for n in ctx.get("selection", [])]
    scoped = any(name in payload for name in ("root", "query", "limit"))
    compact = not payload.get("details") and not payload.get("fields")
    if kind == "summary" or (kind == "node" and (scoped or compact)):
        return _inspect_summary(hou, ctx, payload, targets, selected)
    if kind == "errors" and (scoped or compact):
        return _inspect_errors(hou, ctx, payload, targets)
    if targets is None:
        targets = [payload["root"]] if "root" in payload else selected
        if not targets:
            targets = ["@network"]
            if kind in {"node", "summary"}:
                kind = "network"
    if len(targets) > 20:
        raise TargetError("Too many selected nodes. Supply at most 20 targets.", ctx.get("selection", []))
    paths = [_resolve_path(p, ctx) for p in targets]
    key = "root" if kind == "errors" else "path"
    options = {k: payload[k] for k in ("fields", "parm_names") if k in payload}
    if kind == "summary":
        kind = "node"
    steps = [{"command": INSPECTION_COMMANDS[kind], "payload": {key: path, **options}} for path in paths]
    result = commands.multi_read(hou, {"steps": steps})
    result.update(context_token=ctx["context_token"], resolved_targets=paths)
    result["target_ids"] = {p: hou.node(p).sessionId() for p in paths if hou.node(p) is not None}
    response = protocol.ok_response("", "inspect_batch", result)
    if result.get("ok") is False:
        response.update(ok=False, error={"code": "partial_read", "message": "Some targets could not be inspected."})
    return response


def _inspect_summary(hou, ctx, payload, targets, selected):
    from . import commands
    root_path = ctx.get("current_network")
    query = payload.get("query", "").casefold()
    limit = payload.get("limit", 20)
    scoped = "root" in payload or (targets is None and (query or not selected))
    if scoped:
        root_path = _resolve_path(payload.get("root", "@network"), ctx)
        root = hou.node(root_path)
        if root is None:
            raise TargetError("Network not found: " + root_path, [])
        candidates = (node.path() for node in root.children() if query in node.name().casefold())
    else:
        candidates = (_resolve_path(path, ctx) for path in (targets if targets is not None else selected))
        if query:
            candidates = (path for path in candidates if query in path.rsplit("/", 1)[-1].casefold())
    matches = list(islice(candidates, limit + 1))
    paths = matches[:limit]
    results = []
    summary = []
    identities = {}
    for path in paths:
        execution.checkpoint()
        node = hou.node(path)
        if node is None:
            results.append({"ok": False, "error": {"code": "read_failed", "message": "Node not found: " + path}})
            continue
        identities[path] = node.sessionId()
        details = commands._node_details(node, ("flags", "messages"))
        row = {key: details[key] for key in ("path", "type", "flags")}
        for key in ("errors", "warnings"):
            messages = details["messages"][key]
            if messages:
                row[key] = [str(message)[:160] for message in messages[:2]]
                row[key + "_count"] = len(messages)
        names = payload.get("parm_names", [])[:12]
        if names:
            row["parms"] = {name: commands._safe_call(lambda: str(node.parm(name).eval())[:120], default=None)
                            for name in names}
        fields = payload.get("fields")
        data = row
        if fields or payload.get("details"):
            full = commands._node_details(node, fields)
            data = commands._project_response("node_info", full, {"fields": fields})
        results.append({"ok": True, "result": data})
        markers = [name for name, enabled in row["flags"].items() if enabled]
        if row.get("errors"):
            markers.append("error: " + row["errors"][0][:100])
        elif row.get("warnings"):
            markers.append("warning: " + row["warnings"][0][:100])
        if names:
            markers.extend(f"{name}={value}" for name, value in row["parms"].items())
        summary.append(" | ".join([path, row["type"], *markers]))
    result = {"ok": all(item["ok"] for item in results), "context_token": ctx["context_token"],
              "current_network": root_path, "selection_count": len(selected), "summary": summary,
              "results": results, "resolved_targets": paths, "target_ids": identities,
              "truncated": len(matches) > limit}
    return _inspection_response(result)


def _inspect_errors(hou, ctx, payload, targets):
    from . import commands
    query = payload.get("query", "").casefold()
    limit = payload.get("limit", 20)
    paths = [_resolve_path(path, ctx) for path in (targets or [payload.get("root", "@network")])]
    matches, summary = [], []
    visited = 0
    truncated = False
    for path in paths:
        root = hou.node(path)
        if root is None:
            raise TargetError("Network not found: " + path, [])
        for node in commands._walk_nodes(root):
            if len(matches) >= limit or visited >= 5000:
                truncated = True
                break
            visited += 1
            execution.checkpoint()
            if query and query not in node.name().casefold():
                continue
            messages = {
                "errors": commands._safe_node_method(node, "errors", default=[]),
                "warnings": commands._safe_node_method(node, "warnings", default=[]),
            }
            if not any(messages.values()):
                continue
            data = {key: [str(value)[:200] for value in values[:2]] for key, values in messages.items()}
            inputs = list(islice((n.path() for n in commands._safe_node_method(node, "inputs", default=[]) if n), 8))
            matches.append(dict(data, path=node.path(), type=node.type().name(), inputs=inputs))
            reason = (data["errors"] or data["warnings"])[0]
            summary.append(f"{node.path()} | {reason} | inputs: {', '.join(inputs)}")
    return _inspection_response({"ok": True, "context_token": ctx["context_token"], "roots": paths,
                                 "nodes": matches, "count": len(matches), "visited": visited,
                                 "summary": summary, "truncated": truncated})


def _inspection_response(result):
    response = protocol.ok_response("", "inspect_batch", result)
    if not result["ok"]:
        response.update(ok=False, error={"code": "partial_read", "message": "Some inspection targets are unavailable."})
    return response


def look(hou, payload):
    from . import commands
    mode = payload.get("mode", "viewport")
    if mode in {"angles", "frames"}:
        return _look_sheet(hou, payload)
    if mode == "both":
        if "pane_type" in payload:
            raise ValueError("pane_type is supported only in pane mode.")
        network_keys = {"path", "fit"}
        captures = []
        for part in ("viewport", "network"):
            arguments = {k: v for k, v in payload.items() if k != "mode" and (
                (k in network_keys) if part == "network" else (k not in network_keys))}
            response = look(hou, dict(arguments, mode=part))
            captures.append(response)
            execution.checkpoint()
        response = protocol.ok_response("", "look_batch", {"captures": captures})
        if any(not item.get("ok") for item in captures):
            response.update(ok=False, error={"code": "partial_capture", "message": "Some captures are unavailable; inspect the per-capture results."})
        return response
    arguments = dict(payload)
    arguments.pop("mode", None)
    if mode in {"viewport", "camera"}:
        if mode == "camera" and not arguments.get("camera"):
            viewer = commands._find_pane(hou, "SceneViewer")
            camera = viewer.curViewport().camera() if viewer is not None else None
            if camera is None:
                raise commands.BridgeCommandError("Choose a camera path or look through a camera in the Scene Viewer.")
            arguments["camera"] = camera.path()
        command = "viewport_screenshot"
        arguments.setdefault("width", 768)
        arguments.setdefault("height", 432)
    elif mode == "network":
        if "path" in arguments:
            command = "network_screenshot"
            arguments.setdefault("fit", False)
        else:
            command = "pane_screenshot"
            arguments["type"] = "NetworkEditor"
    elif mode == "pane":
        command = "pane_screenshot"
        arguments["type"] = arguments.pop("pane_type", "NetworkEditor")
    else:
        raise ValueError("Unsupported capture mode.")
    allowed = set(protocol.command_manifest()["commands"][command]["payload_schema"]["properties"])
    if set(arguments) - allowed:
        raise ValueError("Arguments are not supported for this capture mode.")
    if command in {"network_screenshot", "pane_screenshot"}:
        arguments["restore"] = True
    keys = [key for key in ("path", "target", "camera") if key in arguments]
    if any(isinstance(arguments[key], str) and arguments[key].startswith("@") for key in keys):
        context = commands.context(hou)
        for key in keys:
            arguments[key] = _resolve_path(arguments[key], context)
    execution.checkpoint()
    result = commands.execute(command, arguments, hou_module=hou)
    subject = arguments.get("camera") or arguments.get("target") or result.get("node_path") or mode
    result["summary"] = {"mode": mode, "target": subject, "view": result.get("view"),
                         "frame": result.get("timeline_frame"), "source": result.get("capture_source")}
    response = protocol.ok_response("", command, result)
    if result.get("image_status") in {"all_black", "unreadable"}:
        response.update(ok=False, error={"code": "unusable_preview", "message": "The captured preview is black or unreadable."})
    return response


def _look_sheet(hou, payload):
    from . import commands
    mode = payload["mode"]
    arguments = {k: v for k, v in payload.items() if k not in {"mode", "views", "frames"}}
    arguments.setdefault("width", 480)
    arguments.setdefault("height", 300)
    arguments["width"] = min(arguments["width"], 768)
    arguments["height"] = min(arguments["height"], 512)
    if mode == "angles":
        shots = [(name.title(), {"view": name, "frame": payload.get("frame", "all")})
                 for name in payload.get("views", ["front", "right", "top"])]
    else:
        frame_range = list(hou.playbar.playbackRange()) if "frames" not in payload else []
        frames = payload.get("frames") or [frame_range[0], (frame_range[0] + frame_range[1]) / 2, frame_range[1]]
        shots = [(f"Frame {number:g}", {"timeline_frame": number}) for number in dict.fromkeys(frames)]

    captures = []
    if mode == "frames" and arguments.get("camera"):
        camera = _resolve_path(arguments["camera"], commands.context(hou))
        bound = dict(arguments, camera=camera)
        protocol.validate_command("viewport_screenshot", bound)
        if bound.get("target") or bound.get("view", "current") != "current" or bound.get("frame", "current") != "current":
            raise commands.BridgeCommandError("Camera capture cannot use target framing or alternate views.")
        frame_numbers = [shot["timeline_frame"] for _, shot in shots]
        for (label, _), data in zip(shots, commands.capture_camera_series(hou, bound, frame_numbers)):
            image = protocol.ok_response("", "viewport_screenshot", data)
            if data["image_status"] in {"all_black", "unreadable"}:
                image.update(ok=False, error={"code": "unusable_preview", "message": "No usable camera image."})
            captures.append(dict(image, label=label))
    else:
        for label, override in shots:
            execution.checkpoint()
            try:
                image = look(hou, dict(arguments, **override, mode="viewport"))
            except execution.TaskCancelled:
                raise
            except (RuntimeError, ValueError) as exc:
                image = protocol.error_response("", "viewport_screenshot", str(exc), "capture_failed")
            captures.append(dict(image, label=label))
    successful = [item for item in captures if item.get("ok")]
    result = {"mode": mode, "captures": captures,
              "summary": [{"label": item["label"], "ok": item["ok"],
                           "target": (item.get("result") or {}).get("summary", {}).get("target"),
                           "error": item.get("error")} for item in captures]}
    if successful:
        result["sheet"] = commands.preview_sheet(captures, arguments["width"], arguments["height"])
    response = protocol.ok_response("", "look_batch", result)
    if len(successful) != len(captures):
        response.update(ok=False, error={"code": "partial_capture" if successful else "capture_failed",
                                        "message": "Inspect the individual capture results."})
    return response
