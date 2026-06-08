"""Houdini command handlers for the local bridge."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
from typing import Any

from . import dynamics_profiles, history, protocol, recipes, state, workflow_templates


class BridgeCommandError(RuntimeError):
    """Raised for command-level errors that should be returned to the CLI."""


_PLAN_UNDO_DEPTH = 0


def execute_in_houdini(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a bridge command on Houdini's main thread when available."""
    try:
        import hdefereval  # type: ignore
    except ImportError:
        return execute(command, payload)
    return hdefereval.executeInMainThreadWithResult(lambda: execute(command, payload))


def execute(command: str, payload: dict[str, Any] | None = None, hou_module: Any | None = None) -> dict[str, Any]:
    command = protocol.normalize_command(command)
    payload = payload or {}
    protocol.validate_command(command, payload)
    if command == "health":
        return health()
    if command == "manifest":
        return manifest()
    if command == "recipe_manifest":
        return recipes.recipe_manifest()
    if command == "profile_manifest":
        return dynamics_profiles.manifest()
    if command == "rpc_log":
        return rpc_log(payload)

    hou = hou_module or _import_hou()
    if command == "context":
        return context(hou)
    if command == "selected":
        return selected(hou)
    if command == "scene_snapshot":
        return scene_snapshot(hou, payload)
    if command == "find_nodes":
        return find_nodes(hou, payload)
    if command == "node_info":
        return node_info(hou, payload["path"])
    if command == "node_parms":
        return node_parms(hou, payload["path"])
    if command == "viewport_screenshot":
        return viewport_screenshot(hou, payload)
    if command == "network":
        return network(hou, payload["path"])
    if command == "upstream":
        return upstream(hou, payload["path"], depth=payload.get("depth", 4))
    if command == "downstream":
        return downstream(hou, payload["path"], depth=payload.get("depth", 4))
    if command == "edit_mode":
        return edit_mode(payload)
    if command == "validate_plan":
        return validate_plan(hou, payload)
    if command == "verify_plan":
        return verify_plan(hou, payload)
    if command == "review_plan":
        return review_plan(hou, payload)
    if command == "probe_parm_profile":
        return probe_parm_profile(hou, payload)
    if command in protocol.EDIT_COMMANDS and command != "run_plan":
        _require_edit_enabled(command)
    if command == "create_node":
        return create_node(hou, payload)
    if command == "rename_node":
        return rename_node(hou, payload)
    if command == "set_node_color":
        return set_node_color(hou, payload)
    if command == "bypass_node":
        return bypass_node(hou, payload)
    if command == "create_network_box":
        return create_network_box(hou, payload)
    if command == "create_sticky_note":
        return create_sticky_note(hou, payload)
    if command == "set_parm":
        return set_parm(hou, payload)
    if command == "set_parm_any":
        return set_parm_any(hou, payload)
    if command == "batch_set_parms":
        return batch_set_parms(hou, payload)
    if command == "apply_parm_profile":
        return apply_parm_profile(hou, payload)
    if command == "run_plan":
        return run_plan(hou, payload)
    if command == "set_comment":
        return set_comment(hou, payload)
    if command == "set_flags":
        return set_flags(hou, payload)
    if command == "set_position":
        return set_position(hou, payload)
    if command == "ensure_parm":
        return ensure_parm(hou, payload)
    if command == "connect":
        return connect(hou, payload)
    if command == "set_input":
        return set_input(hou, payload)
    if command == "disconnect":
        return disconnect(hou, payload)
    if command == "move_node":
        return move_node(hou, payload)
    if command == "copy_node":
        return copy_node(hou, payload)
    if command == "set_node_shape":
        return set_node_shape(hou, payload)
    if command == "replace_node":
        return replace_node(hou, payload)
    if command == "delete_node":
        return delete_node(hou, payload)
    if command == "layout":
        return layout(hou, payload["path"])
    if command == "select":
        return select(hou, payload["path"])
    raise BridgeCommandError("Unknown command: %s" % command)


def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "edit" if state.edit_enabled() else "read",
        "host": "127.0.0.1",
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "time": time.time(),
        "version": protocol.BRIDGE_VERSION,
        "commands": sorted(protocol.READ_COMMANDS | protocol.EDIT_COMMANDS),
        "edit_enabled": state.edit_enabled(),
    }


def manifest() -> dict[str, Any]:
    return protocol.command_manifest()


def rpc_log(payload: dict[str, Any]) -> dict[str, Any]:
    return history.snapshot(limit=payload.get("limit", 50))


def context(hou: Any) -> dict[str, Any]:
    selection = [_node_summary(node) for node in _safe_call(hou.selectedNodes, default=[])]
    current_network = _current_network_path(hou)
    hip_path = _safe_call(lambda: hou.hipFile.path(), default="")
    hip_name = _safe_call(lambda: hou.hipFile.name(), default="")
    return {
        "hip_path": hip_path,
        "hip_name": hip_name,
        "current_network": current_network,
        "selection": selection,
        "timeline": {
            "start": _safe_call(lambda: hou.playbar.frameRange()[0], default=None),
            "end": _safe_call(lambda: hou.playbar.frameRange()[1], default=None),
            "current": _safe_call(lambda: hou.frame(), default=None),
        },
        "application": {
            "name": "Houdini",
            "version": _safe_call(hou.applicationVersionString, default=""),
        },
    }


def selected(hou: Any) -> dict[str, Any]:
    nodes = [_node_summary(node) for node in _safe_call(hou.selectedNodes, default=[])]
    return {
        "count": len(nodes),
        "nodes": nodes,
    }


def scene_snapshot(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx = context(hou)
    selected_result = selected(hou)
    network_path = payload.get("path") or ctx.get("current_network") or _selection_parent_path(selected_result) or "/obj"
    trace_depth = int(payload.get("trace_depth", 1))
    max_selected = int(payload.get("max_selected", 3))

    network_result = network(hou, network_path)
    selected_details = []
    traces = []
    for summary in selected_result["nodes"][:max_selected]:
        path = summary.get("path", "")
        if not path:
            continue
        selected_details.append(node_info(hou, path))
        if trace_depth > 0:
            traces.append(
                {
                    "path": path,
                    "upstream": upstream(hou, path, depth=trace_depth),
                    "downstream": downstream(hou, path, depth=trace_depth),
                }
            )

    viewport = {"included": False}
    if payload.get("include_viewport", False):
        try:
            viewport = {
                "included": True,
                "ok": True,
                **viewport_screenshot(
                    hou,
                    {
                        "width": payload.get("width", 1280),
                        "height": payload.get("height", 720),
                        "prefix": payload.get("prefix", "scene_snapshot"),
                    },
                ),
            }
        except BridgeCommandError as exc:
            viewport = {"included": True, "ok": False, "error": str(exc)}

    semantics = _scene_semantics(network_result, selected_result, traces, viewport)

    return {
        "context": ctx,
        "selected": selected_result,
        "network": network_result,
        "selected_details": selected_details,
        "traces": traces,
        "viewport": viewport,
        "semantics": semantics,
        "summary": {
            "network_path": network_result["path"],
            "network_node_count": network_result["node_count"],
            "selection_count": selected_result["count"],
            "message_count": len(network_result.get("messages", [])),
            "trace_count": len(traces),
            "edit_enabled": state.edit_enabled(),
            "inferred_purpose": semantics["inferred_purpose"],
            "key_output_count": len(semantics["key_outputs"]),
            "cache_node_count": len(semantics.get("cache_nodes", [])),
            "simulation_node_count": len(semantics.get("simulation_nodes", [])),
            "volume_node_count": len(semantics.get("volume_nodes", [])),
            "render_node_count": len(semantics.get("render_nodes", [])),
            "focus_candidate_count": len(semantics["focus_candidates"]),
            "risk_count": len(semantics["risk_notes"]),
            "risk_domain_count": len(semantics.get("risk_domains", [])),
            "inspection_hint_count": len(semantics["inspection_hints"]),
            "workflow_suggestion_count": len(semantics.get("workflow_suggestions", [])),
        },
    }


def find_nodes(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    root_path = payload["root"]
    root = hou.node(root_path)
    if root is None:
        raise BridgeCommandError("Root node not found: %s" % root_path)
    limit = int(payload.get("limit", 100))
    filters = {
        key: str(payload[key]).lower()
        for key in ("name", "type", "category", "path")
        if payload.get(key) is not None and str(payload[key])
    }
    matches = []
    visited = 0
    for node in _walk_nodes(root):
        visited += 1
        summary = _node_summary(node)
        if _node_matches(summary, filters):
            matches.append(summary)
            if len(matches) >= limit:
                break
    return {
        "root": _safe_call(root.path, default=root_path),
        "count": len(matches),
        "visited": visited,
        "limit": limit,
        "truncated": len(matches) >= limit,
        "filters": filters,
        "nodes": matches,
    }


def node_info(hou: Any, path: str) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    return _node_details(node)


def node_parms(hou: Any, path: str) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    parms = [_parm_details(parm) for parm in _safe_call(node.parms, default=[])]
    return {
        "node": _node_summary(node),
        "parm_count": len(parms),
        "parms": parms,
    }


def viewport_screenshot(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ui = getattr(hou, "ui", None)
    pane_type = getattr(getattr(hou, "paneTabType", None), "SceneViewer", None)
    if ui is None or pane_type is None:
        raise BridgeCommandError("Houdini UI Scene Viewer is not available.")
    viewer = _safe_call(lambda: ui.paneTabOfType(pane_type), default=None)
    if viewer is None:
        raise BridgeCommandError("No Houdini Scene Viewer pane was found.")
    viewport = _safe_call(viewer.curViewport, default=None)
    if viewport is None:
        raise BridgeCommandError("No active Houdini viewport was found.")

    width = int(payload.get("width", 1280))
    height = int(payload.get("height", 720))
    prefix = payload.get("prefix", "viewport")
    directory = os.path.join(tempfile.gettempdir(), "blib_hou_bridge", "screenshots")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "%s_%s.png" % (prefix, time.strftime("%Y%m%d_%H%M%S")))

    settings_func = getattr(viewport, "settings", None)
    settings = _safe_call(settings_func, default=None) if settings_func is not None else None
    saved_size = None
    if settings is not None and hasattr(settings, "resolution") and hasattr(settings, "setResolution"):
        saved_size = _safe_call(settings.resolution, default=None)
        _safe_call(lambda: settings.setResolution((width, height)), default=None)

    try:
        if hasattr(viewport, "saveViewToImage"):
            viewport.saveViewToImage(path)
        elif hasattr(viewer, "saveViewToImage"):
            viewer.saveViewToImage(path)
        elif hasattr(viewer, "flipbook"):
            _capture_flipbook(hou, viewer, path, width, height)
        else:
            raise BridgeCommandError("This Houdini build does not expose a supported viewport screenshot API.")
    finally:
        if settings is not None and saved_size is not None and hasattr(settings, "setResolution"):
            _safe_call(lambda: settings.setResolution(saved_size), default=None)

    if not os.path.exists(path):
        raise BridgeCommandError("Viewport screenshot did not create an image file.")
    return {
        "path": path,
        "width": width,
        "height": height,
        "bytes": os.path.getsize(path),
        "viewport": _safe_call(viewport.name, default=""),
    }


def network(hou: Any, path: str) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Network not found: %s" % path)
    children = list(_safe_call(node.children, default=[]))
    child_paths = {_safe_call(child.path, default="") for child in children}
    wires = []
    display_nodes = []
    render_nodes = []
    messages = []
    for child in children:
        child_path = _safe_call(child.path, default="")
        if _safe_node_method(child, "isDisplayFlagSet", default=False):
            display_nodes.append(child_path)
        if _safe_node_method(child, "isRenderFlagSet", default=False):
            render_nodes.append(child_path)
        errors = list(_safe_call(child.errors, default=[]))
        warnings = list(_safe_call(child.warnings, default=[]))
        if errors or warnings:
            messages.append({"path": child_path, "errors": errors, "warnings": warnings})
        for index, input_node in enumerate(_safe_call(child.inputs, default=[])):
            if input_node is None:
                continue
            src_path = _safe_call(input_node.path, default="")
            if src_path in child_paths:
                wires.append({"src": src_path, "dst": child_path, "input_index": index})
    return {
        "path": _safe_call(node.path, default=path),
        "node_count": len(children),
        "nodes": [_node_summary(child) for child in children],
        "wires": wires,
        "display_nodes": display_nodes,
        "render_nodes": render_nodes,
        "messages": messages,
        "network_boxes": _network_boxes(node),
    }


def upstream(hou: Any, path: str, depth: int = 4) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    return _trace_graph(node, direction="upstream", depth=depth)


def downstream(hou: Any, path: str, depth: int = 4) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    return _trace_graph(node, direction="downstream", depth=depth)


def edit_mode(payload: dict[str, Any]) -> dict[str, Any]:
    if "enabled" in payload:
        state.set_edit_enabled(payload["enabled"])
    return {"edit_enabled": state.edit_enabled(), "mode": "edit" if state.edit_enabled() else "read"}


def validate_plan(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps", [])
    reports = []
    valid = True
    would_require_edit = False
    planned_paths: set[str] = set()
    aliases: dict[str, str] = {}
    layout_seen_by_parent: set[str] = set()
    for index, step in enumerate(steps):
        report = _validate_plan_step(hou, index, step, planned_paths, aliases)
        _apply_network_box_order_rule(report, layout_seen_by_parent, aliases)
        reports.append(report)
        valid = valid and report["valid"]
        would_require_edit = would_require_edit or report["permission"] == "edit"
        if report["valid"]:
            for created in report.get("creates", []):
                if created and "<auto " not in created:
                    planned_paths.add(created)
            for deleted in report.get("deletes", []):
                if deleted and "<auto " not in deleted:
                    planned_paths.discard(deleted)
                    for alias_key, alias_value in list(aliases.items()):
                        if alias_key == deleted or alias_value == deleted:
                            aliases.pop(alias_key, None)
            for old_path, new_path in report.get("aliases", {}).items():
                aliases[old_path] = new_path
                planned_paths.discard(old_path)
                planned_paths.add(new_path)
            if report["command"] == "layout":
                layout_path = report.get("payload", {}).get("path")
                if isinstance(layout_path, str):
                    layout_seen_by_parent.add(_resolved_path(layout_path, aliases))
    blocked_by_edit_mode = would_require_edit and not state.edit_enabled()
    return {
        "valid": valid,
        "ready_to_run": valid and not blocked_by_edit_mode,
        "step_count": len(reports),
        "steps_sha256": _steps_sha256(steps),
        "would_require_edit": would_require_edit,
        "edit_enabled": state.edit_enabled(),
        "blocked_by_edit_mode": blocked_by_edit_mode,
        "steps": reports,
    }


def review_plan(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_plan(hou, payload)
    return recipes.review_plan(payload.get("steps", []), validation)


def _steps_sha256(steps: list[Any]) -> str:
    text = json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_plan(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps", []) or []
    validation = _verify_payload_result(payload.get("validation"))
    validation_source = "payload"
    if not validation.get("steps"):
        validation = validate_plan(hou, {"steps": steps})
        validation_source = "current_scene"
    run_payload = _verify_run_payload(payload.get("run_result"))
    checks: list[dict[str, Any]] = []

    if run_payload:
        run_ok = bool(run_payload.get("ok", True))
        checks.append(
            _verification_check(
                "run_result",
                "run_plan",
                "pass" if run_ok else "failed",
                "run_plan reported ok=true." if run_ok else "run_plan reported ok=false.",
                expected=True,
                actual=run_payload.get("ok"),
            )
        )
        if run_payload.get("stopped"):
            checks.append(
                _verification_check(
                    "run_stopped",
                    "run_plan",
                    "failed",
                    "run_plan stopped before all steps completed.",
                    expected=False,
                    actual=True,
                )
            )

    reports = [report for report in (validation.get("steps", []) or []) if isinstance(report, dict)]
    final_deleted_paths = {
        path
        for report in reports
        for path in (report.get("deletes", []) or [])
        if isinstance(path, str) and "<auto " not in path
    }
    aliases: dict[str, str] = {}
    run_results = _run_results_by_index(run_payload)
    for report in reports:
        if not isinstance(report, dict):
            continue
        index = report.get("index")
        command = protocol.normalize_command(report.get("command"))
        payload_step = _verification_payload(report.get("payload", {}), aliases)
        step_result = _step_result(run_results, index)
        checks.extend(_verify_plan_step(hou, index, command, payload_step, report, step_result, final_deleted_paths))
        for old_path, new_path in (report.get("aliases", {}) or {}).items():
            if isinstance(old_path, str) and isinstance(new_path, str):
                aliases[old_path] = new_path

    summary = _verification_summary(checks)
    status = "failed" if summary["failed"] else "pass"
    if not checks or summary["inconclusive"]:
        status = "failed" if summary["failed"] else "inconclusive"
    return {
        "ok": status != "failed",
        "verified": status == "pass",
        "status": status,
        "summary": summary,
        "validation_source": validation_source,
        "validation": {
            "valid": validation.get("valid"),
            "ready_to_run": validation.get("ready_to_run"),
            "step_count": validation.get("step_count", len(validation.get("steps", []) or [])),
        },
        "run": {
            "ok": run_payload.get("ok") if run_payload else None,
            "ran": run_payload.get("ran") if run_payload else None,
            "stopped": run_payload.get("stopped") if run_payload else None,
        },
        "checks": checks,
    }


def _verify_payload_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = value.get("result")
    if isinstance(result, dict):
        return result
    return value


def _verify_run_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    run = value.get("run")
    if isinstance(run, dict):
        return _verify_payload_result(run)
    return _verify_payload_result(value)


def _run_results_by_index(run_payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for item in run_payload.get("results", []) if isinstance(run_payload, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            continue
        response = item.get("response")
        results[item["index"]] = response if isinstance(response, dict) else {}
    return results


def _step_result(results: dict[int, dict[str, Any]], index: Any) -> dict[str, Any]:
    return results.get(index, {}) if isinstance(index, int) else {}


def _verification_payload(payload: Any, aliases: dict[str, str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    resolved = dict(payload)
    for key in ("node", "path", "parent", "src", "dst", "root"):
        if isinstance(resolved.get(key), str):
            resolved[key] = _resolved_path(resolved[key], aliases)
    if isinstance(resolved.get("nodes"), list):
        resolved["nodes"] = [_resolved_path(item, aliases) if isinstance(item, str) else item for item in resolved["nodes"]]
    return resolved


def _verify_plan_step(
    hou: Any,
    index: Any,
    command: str,
    payload: dict[str, Any],
    report: dict[str, Any],
    step_result: dict[str, Any],
    final_deleted_paths: set[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    label = "step %s %s" % (index, command)
    if step_result:
        ok = bool(step_result.get("ok"))
        checks.append(
            _verification_check(
                "step_result",
                label,
                "pass" if ok else "failed",
                "Step reported ok=true." if ok else _step_error_message(step_result),
                expected=True,
                actual=step_result.get("ok"),
                step_index=index,
            )
        )

    for path in report.get("creates", []) or []:
        if not isinstance(path, str) or "<auto " in path:
            actual_path = _first_created_path({"creates": []}, step_result)
            if actual_path:
                node = hou.node(actual_path)
                checks.append(
                    _verification_check(
                        "created_path",
                        label,
                        "pass" if node is not None else "failed",
                        "Auto-created node exists: %s" % actual_path if node is not None else "Auto-created node from run result is missing: %s" % actual_path,
                        expected=path,
                        actual=_node_path(node, None) if node is not None else actual_path,
                        path=actual_path,
                        step_index=index,
                    )
                )
                continue
            checks.append(
                _verification_check(
                    "created_path",
                    label,
                    "inconclusive",
                    "Created path was auto-named and cannot be verified by exact path.",
                    expected=path,
                    actual=None,
                    step_index=index,
                )
            )
            continue
        if path in final_deleted_paths:
            step_ok = bool(step_result.get("ok")) if step_result else False
            checks.append(
                _verification_check(
                    "created_path",
                    label,
                    "pass" if step_ok else "inconclusive",
                    "Created node is planned to be deleted later and the step reported ok=true."
                    if step_ok
                    else "Created node is planned to be deleted later; final scene cannot prove it existed.",
                    expected=path,
                    actual=None,
                    path=path,
                    step_index=index,
                )
            )
            continue
        node = hou.node(path)
        checks.append(
            _verification_check(
                "created_path",
                label,
                "pass" if node is not None else "failed",
                "Created node exists: %s" % path if node is not None else "Expected created node is missing: %s" % path,
                expected=path,
                actual=_node_path(node, None) if node is not None else None,
                path=path,
                step_index=index,
                **_direct_edit_contract_extra(command),
            )
        )

    for path in report.get("deletes", []) or []:
        if not isinstance(path, str) or "<auto " in path:
            continue
        node = hou.node(path)
        checks.append(
            _verification_check(
                "deleted_path",
                label,
                "pass" if node is None else "failed",
                "Deleted node is gone: %s" % path if node is None else "Expected deleted node still exists: %s" % path,
                expected=None,
                actual=_node_path(node, path) if node is not None else None,
                path=path,
                step_index=index,
            )
        )

    checks.extend(_verify_command_specific(hou, index, command, payload, report, step_result, label, final_deleted_paths))
    return checks


def _verify_command_specific(
    hou: Any,
    index: Any,
    command: str,
    payload: dict[str, Any],
    report: dict[str, Any],
    step_result: dict[str, Any],
    label: str,
    final_deleted_paths: set[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if command == "create_node":
        path = _first_created_path(report, step_result)
        if path and path not in final_deleted_paths:
            checks.extend(_verify_node_type(hou, path, payload.get("type"), label, index))
    elif _command_target_final_deleted(command, payload, final_deleted_paths):
        target = payload.get("dst") or payload.get("node") or payload.get("path")
        checks.append(
            _verification_check(
                "final_state_skipped",
                label,
                "pass",
                "Target is planned to be deleted later; final deletion is verified separately.",
                expected="deleted later",
                actual=target,
                path=target,
                step_index=index,
            )
        )
    elif command in {"set_parm", "set_parm_any"}:
        parm_name = payload.get("parm") if command == "set_parm" else _result_value(step_result, "parm")
        expected_value = payload.get("value")
        if parm_name:
            checks.append(_verify_parm_value(hou, payload.get("node"), parm_name, expected_value, label, index, command))
        elif command == "set_parm_any" and payload.get("required", False):
            checks.append(_verification_check("parm_value", label, "failed", "Required set_parm_any did not report a matched parameter.", step_index=index))
    elif command == "batch_set_parms":
        for parm_name, expected_value in (payload.get("values", {}) or {}).items():
            applied = _result_list_contains(step_result, "applied", "parm", parm_name)
            if applied or payload.get("required", True):
                checks.append(_verify_parm_value(hou, payload.get("node"), parm_name, expected_value, label, index, command))
    elif command == "apply_parm_profile":
        for item in _result_value(step_result, "applied", []) or []:
            if isinstance(item, dict) and item.get("parm"):
                checks.append(_verify_parm_value(hou, payload.get("node"), item.get("parm"), item.get("value"), label, index, command))
        if not _result_value(step_result, "applied", []) and payload.get("strict", False):
            checks.append(_verification_check("parm_profile", label, "failed", "Strict profile did not report applied parameters.", step_index=index))
    elif command in {"connect", "set_input"}:
        dst = payload.get("dst")
        input_index = int(payload.get("input_index", 0))
        expected_src = None if payload.get("clear", False) else payload.get("src")
        checks.append(_verify_input_connection(hou, dst, input_index, expected_src, label, index, command))
    elif command == "disconnect":
        node_path = payload.get("node")
        if payload.get("input_index") is not None:
            checks.append(_verify_input_connection(hou, node_path, int(payload.get("input_index")), None, label, index, command))
        elif payload.get("all", False):
            node = hou.node(node_path)
            remaining = [src for src in _safe_call(node.inputs, default=[]) if src is not None] if node is not None else []
            checks.append(
                _verification_check(
                    "disconnect_all",
                    label,
                    "pass" if node is not None and not remaining else "failed",
                    "All inputs are disconnected." if node is not None and not remaining else "Node still has connected inputs.",
                    expected=[],
                    actual=[_node_path(src, "") for src in remaining],
                    path=node_path,
                    step_index=index,
                    **_direct_edit_contract_extra(command),
                )
            )
        elif payload.get("src"):
            checks.append(_verify_no_input_from_source(hou, node_path, payload.get("src"), label, index))
    elif command in {"move_node", "copy_node", "replace_node"}:
        for path in report.get("creates", []) or []:
            if isinstance(path, str) and "<auto " not in path and path not in final_deleted_paths:
                checks.extend(_verify_node_type(hou, path, payload.get("type") if command == "replace_node" else None, label, index))
    elif command == "set_flags":
        node = hou.node(payload.get("node"))
        for flag_name, getter in (("display", "isDisplayFlagSet"), ("render", "isRenderFlagSet")):
            if flag_name not in payload:
                continue
            actual = _safe_call(getattr(node, getter, None), default=None) if node is not None else None
            checks.append(
                _verification_check(
                    "%s_flag" % flag_name,
                    label,
                    "pass" if actual == payload[flag_name] else "failed",
                    "%s flag matches." % flag_name if actual == payload[flag_name] else "%s flag mismatch." % flag_name,
                    expected=payload[flag_name],
                    actual=actual,
                    path=payload.get("node"),
                    step_index=index,
                    **_direct_edit_contract_extra(command),
                )
            )
    elif command == "set_comment":
        checks.append(_verify_node_state_value(hou, payload.get("node"), "comment", "comment", payload.get("comment"), label, index))
    elif command == "bypass_node":
        checks.append(_verify_node_state_value(hou, payload.get("node"), "bypass", "isBypassed", payload.get("bypass"), label, index))
    elif command == "set_position":
        checks.append(_verify_node_state_value(hou, payload.get("node"), "position", "position", [payload.get("x"), payload.get("y")], label, index))
    elif command == "set_node_color":
        checks.append(_verify_node_state_value(hou, payload.get("node"), "node_color", "color", payload.get("color"), label, index))
    elif command == "layout":
        checks.append(_verify_network_readable(hou, payload.get("path"), label, index))
    elif command == "select":
        node = hou.node(payload.get("path"))
        actual = _safe_call(node.isSelected, default=None) if node is not None else None
        checks.append(
            _verification_check(
                "selected",
                label,
                "pass" if actual is True else "failed",
                "Node is selected." if actual is True else "Node is not selected.",
                expected=True,
                actual=actual,
                path=payload.get("path"),
                step_index=index,
                **_direct_edit_contract_extra(command),
            )
        )
    return checks


def _command_target_final_deleted(command: str, payload: dict[str, Any], final_deleted_paths: set[str]) -> bool:
    final_state_commands = {
        "set_parm",
        "set_parm_any",
        "batch_set_parms",
        "apply_parm_profile",
        "connect",
        "set_input",
        "disconnect",
        "set_flags",
        "set_comment",
        "bypass_node",
        "set_position",
        "set_node_color",
        "set_node_shape",
        "select",
    }
    if command not in final_state_commands:
        return False
    if command in {"connect", "set_input"}:
        target = payload.get("dst")
    elif command in {"layout", "select"}:
        target = payload.get("path")
    else:
        target = payload.get("node")
    return isinstance(target, str) and target in final_deleted_paths


def _verification_check(kind: str, label: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    check = {"kind": kind, "label": label, "status": status, "message": message}
    for key, value in extra.items():
        if value is not None:
            check[key] = _jsonable(value)
    return check


def _verification_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    direct_edit_contract_checks = [
        check
        for check in checks
        if isinstance(check.get("satisfies_direct_edit_contract"), str)
    ]
    direct_edit_commands = sorted(
        {
            str(check.get("satisfies_direct_edit_contract"))
            for check in direct_edit_contract_checks
            if isinstance(check.get("satisfies_direct_edit_contract"), str)
        }
    )
    failed_direct_edit_commands = sorted(
        {
            str(check.get("satisfies_direct_edit_contract"))
            for check in direct_edit_contract_checks
            if check.get("status") == "failed" and isinstance(check.get("satisfies_direct_edit_contract"), str)
        }
    )
    inconclusive_direct_edit_commands = sorted(
        {
            str(check.get("satisfies_direct_edit_contract"))
            for check in direct_edit_contract_checks
            if check.get("status") == "inconclusive" and isinstance(check.get("satisfies_direct_edit_contract"), str)
        }
    )
    direct_edit_failed = sum(1 for check in direct_edit_contract_checks if check.get("status") == "failed")
    direct_edit_inconclusive = sum(1 for check in direct_edit_contract_checks if check.get("status") == "inconclusive")
    return {
        "total": len(checks),
        "passed": sum(1 for check in checks if check.get("status") == "pass"),
        "failed": sum(1 for check in checks if check.get("status") == "failed"),
        "inconclusive": sum(1 for check in checks if check.get("status") == "inconclusive"),
        "direct_edit_readback": {
            "total": len(direct_edit_contract_checks),
            "passed": sum(1 for check in direct_edit_contract_checks if check.get("status") == "pass"),
            "failed": direct_edit_failed,
            "inconclusive": direct_edit_inconclusive,
            "commands": direct_edit_commands,
            "failed_commands": failed_direct_edit_commands,
            "inconclusive_commands": inconclusive_direct_edit_commands,
            "proof_ready": bool(direct_edit_contract_checks) and direct_edit_failed == 0 and direct_edit_inconclusive == 0,
        },
    }


def _direct_edit_contract_extra(command: str) -> dict[str, Any]:
    normalized = protocol.normalize_command(command)
    if normalized not in protocol.DIRECT_EDIT_COMMANDS:
        return {}
    contract = protocol.direct_edit_verification_contract(normalized)
    return {
        "satisfies_direct_edit_contract": normalized,
        "direct_edit_contract_read_tools": contract.get("read_tools", []),
        "direct_edit_contract_mcp_read_tools": contract.get("mcp_read_tools", []),
    }


def _step_error_message(step_result: dict[str, Any]) -> str:
    error = step_result.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return "Step reported ok=false."


def _first_created_path(report: dict[str, Any], step_result: dict[str, Any]) -> str:
    for path in report.get("creates", []) or []:
        if isinstance(path, str) and "<auto " not in path:
            return path
    created = _result_value(step_result, "created")
    if isinstance(created, dict) and isinstance(created.get("path"), str):
        return created["path"]
    replacement = _result_value(step_result, "replacement")
    if isinstance(replacement, dict) and isinstance(replacement.get("path"), str):
        return replacement["path"]
    moved = _result_value(step_result, "moved")
    if isinstance(moved, dict) and isinstance(moved.get("path"), str):
        return moved["path"]
    return ""


def _result_value(step_result: dict[str, Any], key: str, default: Any = None) -> Any:
    result = step_result.get("result") if isinstance(step_result, dict) else {}
    if isinstance(result, dict):
        return result.get(key, default)
    return default


def _result_list_contains(step_result: dict[str, Any], list_key: str, item_key: str, expected: Any) -> bool:
    items = _result_value(step_result, list_key, [])
    return any(isinstance(item, dict) and item.get(item_key) == expected for item in items)


def _verify_node_type(hou: Any, path: str, expected_type: Any, label: str, index: Any) -> list[dict[str, Any]]:
    if not expected_type:
        return []
    node = hou.node(path)
    actual_type = _safe_call(lambda: node.type().name(), default=None) if node is not None else None
    return [
        _verification_check(
            "node_type",
            label,
            "pass" if actual_type == expected_type else "failed",
            "Node type matches." if actual_type == expected_type else "Node type mismatch.",
            expected=expected_type,
            actual=actual_type,
            path=path,
            step_index=index,
        )
    ]


def _verify_parm_value(
    hou: Any,
    node_path: Any,
    parm_name: Any,
    expected_value: Any,
    label: str,
    index: Any,
    contract_command: str = "",
) -> dict[str, Any]:
    node = hou.node(node_path) if isinstance(node_path, str) else None
    parm = node.parm(parm_name) if node is not None and isinstance(parm_name, str) else None
    actual = _jsonable(_safe_call(parm.eval, default=None)) if parm is not None else None
    return _verification_check(
        "parm_value",
        label,
        "pass" if parm is not None and actual == expected_value else "failed",
        "Parameter value matches." if parm is not None and actual == expected_value else "Parameter value mismatch or parameter missing.",
        expected=expected_value,
        actual=actual,
        path=node_path,
        parm=parm_name,
        step_index=index,
        **_direct_edit_contract_extra(contract_command),
    )


def _verify_node_state_value(
    hou: Any,
    node_path: Any,
    kind: str,
    getter_name: str,
    expected_value: Any,
    label: str,
    index: Any,
) -> dict[str, Any]:
    node = hou.node(node_path) if isinstance(node_path, str) else None
    getter = getattr(node, getter_name, None) if node is not None else None
    actual = _jsonable(_safe_call(getter, default=None)) if callable(getter) else None
    expected = _jsonable(expected_value)
    return _verification_check(
        kind,
        label,
        "pass" if node is not None and actual == expected else "failed",
        "Node %s matches." % kind if node is not None and actual == expected else "Node %s mismatch or node missing." % kind,
        expected=expected,
        actual=actual,
        path=node_path,
        step_index=index,
        **_direct_edit_contract_extra(_contract_command_for_check(kind)),
    )


def _verify_input_connection(
    hou: Any,
    dst_path: Any,
    input_index: int,
    expected_src_path: Any,
    label: str,
    index: Any,
    contract_command: str = "",
) -> dict[str, Any]:
    dst = hou.node(dst_path) if isinstance(dst_path, str) else None
    actual_src = _input_at(dst, input_index) if dst is not None else None
    actual_path = _node_path(actual_src, None) if actual_src is not None else None
    return _verification_check(
        "input_connection",
        label,
        "pass" if actual_path == expected_src_path else "failed",
        "Input connection matches." if actual_path == expected_src_path else "Input connection mismatch.",
        expected=expected_src_path,
        actual=actual_path,
        path=dst_path,
        input_index=input_index,
        step_index=index,
        **_direct_edit_contract_extra(contract_command),
    )


def _verify_no_input_from_source(hou: Any, dst_path: Any, src_path: Any, label: str, index: Any) -> dict[str, Any]:
    dst = hou.node(dst_path) if isinstance(dst_path, str) else None
    connected = []
    if dst is not None:
        connected = [_node_path(src, "") for src in _safe_call(dst.inputs, default=[]) if src is not None]
    return _verification_check(
        "source_disconnected",
        label,
        "pass" if src_path not in connected else "failed",
        "No inputs remain connected from source." if src_path not in connected else "A matching source connection remains.",
        expected="not %s" % src_path,
        actual=connected,
        path=dst_path,
        step_index=index,
        **_direct_edit_contract_extra("disconnect"),
    )


def _verify_network_readable(hou: Any, path: Any, label: str, index: Any) -> dict[str, Any]:
    node = hou.node(path) if isinstance(path, str) else None
    children = _safe_call(node.children, default=None) if node is not None else None
    child_count = len(children) if isinstance(children, (list, tuple)) else None
    readable = node is not None and isinstance(children, (list, tuple))
    return _verification_check(
        "layout_network_readable",
        label,
        "pass" if readable else "failed",
        "Target network remains readable after layout." if readable else "Target network is missing or children cannot be read after layout.",
        expected="readable network",
        actual={"exists": node is not None, "child_count": child_count},
        path=path,
        step_index=index,
        **_direct_edit_contract_extra("layout"),
    )


def _contract_command_for_check(kind: str) -> str:
    if kind == "comment":
        return "set_comment"
    if kind == "bypass":
        return "bypass_node"
    if kind == "position":
        return "set_position"
    if kind == "node_color":
        return "set_node_color"
    return ""


def _apply_network_box_order_rule(report: dict[str, Any], layout_seen_by_parent: set[str], aliases: dict[str, str]) -> None:
    if report.get("command") != "create_network_box" or not report.get("valid", False):
        return
    payload = report.get("payload", {}) or {}
    parent = payload.get("parent")
    if not isinstance(parent, str):
        return
    resolved_parent = _resolved_path(parent, aliases)
    if resolved_parent not in layout_seen_by_parent:
        report.setdefault("issues", []).append(
            "create_network_box must run after a layout step for the same parent network."
        )
        report["valid"] = False


def create_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    parent = _require_node(hou, payload["parent"], "Parent not found")
    node_type = payload["type"].strip()
    name = payload.get("name")

    def _action():
        node = parent.createNode(node_type, node_name=name) if name else parent.createNode(node_type)
        return {"created": _node_summary(node)}

    return _with_undo(hou, "Blib Bridge create node", _action)


def rename_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    name = payload["name"]
    unique = bool(payload.get("unique", True))

    def _action():
        old_path = _safe_call(node.path, default=payload["node"])
        node.setName(name, unique_name=unique)
        return {"old_path": old_path, "renamed": _node_summary(node), "touched": [_safe_call(node.path, default=old_path)]}

    return _with_undo(hou, "Blib Bridge rename node", _action)


def set_node_color(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    color = [float(value) for value in payload["color"]]

    def _action():
        node.setColor(hou.Color(tuple(color)))
        return {"touched": [_safe_call(node.path, default=payload["node"])], "color": color}

    return _with_undo(hou, "Blib Bridge set node color", _action)


def bypass_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    bypass = bool(payload["bypass"])

    def _action():
        if hasattr(node, "bypass"):
            node.bypass(bypass)
        elif hasattr(node, "setBypass"):
            node.setBypass(bypass)
        else:
            raise BridgeCommandError("Node does not support bypass: %s" % payload["node"])
        return {
            "touched": [_safe_call(node.path, default=payload["node"])],
            "bypass": _safe_node_method(node, "isBypassed", default=bypass),
        }

    return _with_undo(hou, "Blib Bridge set node bypass", _action)


def create_network_box(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    parent = _require_node(hou, payload["parent"], "Parent network not found")
    nodes = [_require_node(hou, path, "Network box node not found") for path in payload.get("nodes", [])]
    color = [float(value) for value in payload["color"]] if "color" in payload else None

    def _action():
        box = parent.createNetworkBox()
        if payload.get("name") and hasattr(box, "setName"):
            box.setName(payload["name"])
        if hasattr(box, "setComment"):
            box.setComment(payload.get("comment") or payload.get("name") or "")
        if color and hasattr(box, "setColor"):
            box.setColor(hou.Color(tuple(color)))
        contained = []
        for node in nodes:
            box.addItem(node)
            contained.append(_safe_call(node.path, default=""))
        if hasattr(box, "fitAroundContents"):
            _safe_call(box.fitAroundContents, default=None)
        box_name = _safe_call(box.name, default=payload.get("name", "network_box"))
        box_path = "%s/%s" % (_safe_call(parent.path, default=payload["parent"]).rstrip("/"), box_name)
        return {
            "created": {"path": box_path, "name": box_name, "type": "network_box"},
            "touched": [_safe_call(parent.path, default=payload["parent"])] + contained,
            "nodes": contained,
        }

    return _with_undo(hou, "Blib Bridge create network box", _action)


def create_sticky_note(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    parent = _require_node(hou, payload["parent"], "Parent network not found")
    color = [float(value) for value in payload["color"]] if "color" in payload else None

    def _action():
        note = parent.createStickyNote()
        if hasattr(note, "setText"):
            note.setText(payload["text"])
        if payload.get("name") and hasattr(note, "setName"):
            note.setName(payload["name"])
        if "x" in payload and hasattr(note, "setPosition"):
            note.setPosition(hou.Vector2(float(payload["x"]), float(payload["y"])))
        if color and hasattr(note, "setColor"):
            note.setColor(hou.Color(tuple(color)))
        note_name = _safe_call(note.name, default=payload.get("name", "sticky_note"))
        note_path = "%s/%s" % (_safe_call(parent.path, default=payload["parent"]).rstrip("/"), note_name)
        return {
            "created": {"path": note_path, "name": note_name, "type": "sticky_note"},
            "touched": [_safe_call(parent.path, default=payload["parent"])],
            "text": payload["text"],
        }

    return _with_undo(hou, "Blib Bridge create sticky note", _action)


def set_parm(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parm_name = payload["parm"]
    parm = node.parm(parm_name)
    if parm is None:
        raise BridgeCommandError("Parameter not found: %s/%s" % (payload["node"], parm_name))

    def _action():
        parm.set(payload.get("value"))
        return {"touched": [_safe_call(node.path, default=payload["node"])], "parm": parm_name}

    return _with_undo(hou, "Blib Bridge set parm", _action)


def set_parm_any(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parms = list(payload.get("parms") or [])
    required = bool(payload.get("required", False))
    for parm_name in parms:
        parm = node.parm(parm_name)
        if parm is None:
            continue

        def _action(parm=parm, parm_name=parm_name):
            parm.set(payload.get("value"))
            return {
                "touched": [_safe_call(node.path, default=payload["node"])],
                "parm": parm_name,
                "matched": True,
                "skipped": False,
            }

        return _with_undo(hou, "Blib Bridge set first matching parm", _action)
    if required:
        raise BridgeCommandError("No candidate parameter found on %s: %s" % (payload["node"], ", ".join(parms)))
    return {
        "touched": [_safe_call(node.path, default=payload["node"])],
        "parm": None,
        "matched": False,
        "skipped": True,
    }


def batch_set_parms(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    values = payload.get("values", {}) or {}
    required = bool(payload.get("required", True))
    resolved = []
    missing = []
    for parm_name, value in values.items():
        parm = node.parm(parm_name)
        if parm is None:
            missing.append(parm_name)
            continue
        resolved.append((parm_name, parm, value))
    if missing and required:
        raise BridgeCommandError("Parameters not found on %s: %s" % (payload["node"], ", ".join(missing)))

    def _action():
        applied = []
        for parm_name, parm, value in resolved:
            parm.set(value)
            applied.append({"parm": parm_name, "value": value})
        return {
            "touched": [_safe_call(node.path, default=payload["node"])],
            "applied": applied,
            "missing": missing,
            "skipped": [{"parm": name} for name in missing],
        }

    return _with_undo(hou, "Blib Bridge batch set parms", _action)


def apply_parm_profile(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    profile_name = dynamics_profiles.normalize_profile_name(payload["profile"])
    strict = bool(payload.get("strict", False))
    values = payload.get("values", {}) or {}
    try:
        resolved = _resolve_parm_profile(node, profile_name, values, strict)
    except Exception as exc:
        raise BridgeCommandError(str(exc)) from exc

    parm_lookup = {parm.name(): parm for parm in _safe_call(node.parms, default=[])}
    if resolved["unresolved"]:
        raise BridgeCommandError("Required profile parameters were not found on %s: %s" % (payload["node"], ", ".join(item["parameter"] for item in resolved["unresolved"])))

    def _action():
        applied = []
        for item in resolved["matched"]:
            parm_lookup[item["parm"]].set(item["value"])
            applied.append({"parameter": item["parameter"], "parm": item["parm"], "value": item["value"]})
        return {
            "touched": [_safe_call(node.path, default=payload["node"])],
            "profile": profile_name,
            "applied": applied,
            "matched": resolved["matched"],
            "skipped": resolved["skipped"],
            "unresolved": resolved["unresolved"],
            "clamped": resolved["clamped"],
            "available_parms": resolved["available_parms"],
        }

    return _with_undo(hou, "Blib Bridge apply parameter profile", _action)


def probe_parm_profile(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    profile_name = dynamics_profiles.normalize_profile_name(payload["profile"])
    try:
        resolved = _resolve_parm_profile(node, profile_name, payload.get("values", {}) or {}, bool(payload.get("strict", False)))
    except Exception as exc:
        raise BridgeCommandError(str(exc)) from exc
    resolved["node"] = _node_summary(node)
    resolved["touched"] = []
    return resolved


def run_plan(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps", []) or []
    continue_on_error = bool(payload.get("continue_on_error", False))
    undo_label = payload.get("undo_label") or "Blib Bridge plan"
    validation = validate_plan(hou, payload)
    order_issues = []
    for report in validation.get("steps", []) or []:
        if report.get("command") != "create_network_box":
            continue
        for issue in report.get("issues", []) or []:
            if "after a layout step" in issue:
                order_issues.append("step %s %s: %s" % (report.get("index"), report.get("command"), issue))
    if order_issues:
        raise BridgeCommandError("Plan validation failed: %s" % "; ".join(order_issues))
    would_edit = any(protocol.normalize_command(step.get("command")) in protocol.EDIT_COMMANDS for step in steps if isinstance(step, dict))
    if would_edit:
        _require_edit_enabled("run_plan")

    def _action():
        global _PLAN_UNDO_DEPTH
        results = []
        ok = True
        failed_step = None
        plan_started = time.time()
        _PLAN_UNDO_DEPTH += 1
        try:
            for index, step in enumerate(steps):
                step_started = time.time()
                try:
                    command = protocol.normalize_command(step.get("command"))
                    step_payload = step.get("payload", {}) or {}
                    if command == "run_plan":
                        raise BridgeCommandError("run_plan cannot contain nested run_plan steps.")
                    result = execute(command, step_payload, hou_module=hou)
                    response = {
                        "ok": True,
                        "command": command,
                        "result": result,
                        "error": None,
                        "duration_ms": round((time.time() - step_started) * 1000, 3),
                    }
                except Exception as exc:
                    ok = False
                    response = {
                        "ok": False,
                        "command": protocol.normalize_command(step.get("command")) if isinstance(step, dict) else "",
                        "result": {},
                        "error": {"code": "step_failed", "message": str(exc)},
                        "duration_ms": round((time.time() - step_started) * 1000, 3),
                    }
                    failed_step = {"index": index, "command": response["command"], "error": response["error"]}
                results.append({"index": index, "response": response})
                if not response["ok"] and not continue_on_error:
                    break
        finally:
            _PLAN_UNDO_DEPTH -= 1
        return {
            "ok": ok,
            "count": len(steps),
            "ran": len(results),
            "stopped": (not ok and not continue_on_error),
            "failed_step": failed_step,
            "duration_ms": round((time.time() - plan_started) * 1000, 3),
            "results": results,
            "edit_enabled": state.edit_enabled(),
        }

    return _with_undo(hou, str(undo_label), _action)


def set_comment(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    comment = payload["comment"]

    def _action():
        node.setComment(comment)
        if hasattr(node, "setGenericFlag") and hasattr(hou, "nodeFlag"):
            node.setGenericFlag(hou.nodeFlag.DisplayComment, bool(comment.strip()))
        return {"touched": [_safe_call(node.path, default=payload["node"])], "comment": comment}

    return _with_undo(hou, "Blib Bridge set node comment", _action)


def set_flags(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")

    def _action():
        if "display" in payload:
            node.setDisplayFlag(payload["display"])
        if "render" in payload:
            node.setRenderFlag(payload["render"])
        return {
            "touched": [_safe_call(node.path, default=payload["node"])],
            "display": _safe_node_method(node, "isDisplayFlagSet", default=False),
            "render": _safe_node_method(node, "isRenderFlagSet", default=False),
        }

    return _with_undo(hou, "Blib Bridge set node flags", _action)


def set_position(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")

    def _action():
        node.setPosition(hou.Vector2(float(payload["x"]), float(payload["y"])))
        return {"touched": [_safe_call(node.path, default=payload["node"])], "position": [payload["x"], payload["y"]]}

    return _with_undo(hou, "Blib Bridge set node position", _action)


def ensure_parm(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    name = payload["name"]
    if node.parm(name) is not None:
        return {"touched": [_safe_call(node.path, default=payload["node"])], "parm": name, "created": False}

    def _action():
        group = node.parmTemplateGroup()
        label = payload.get("label") or name
        default = payload.get("default", 0)
        if payload["type"] == "int":
            template = hou.IntParmTemplate(name, label, 1, default_value=(int(default),))
        else:
            template = hou.FloatParmTemplate(name, label, 1, default_value=(float(default),))
        group.append(template)
        node.setParmTemplateGroup(group)
        return {"touched": [_safe_call(node.path, default=payload["node"])], "parm": name, "created": True}

    return _with_undo(hou, "Blib Bridge ensure spare parm", _action)


def connect(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    src = _require_node(hou, payload["src"], "Source node not found")
    dst = _require_node(hou, payload["dst"], "Destination node not found")
    input_index = payload.get("input_index", 0)

    def _action():
        dst.setInput(input_index, src)
        return {
            "src": _safe_call(src.path, default=payload["src"]),
            "dst": _safe_call(dst.path, default=payload["dst"]),
            "input_index": input_index,
        }

    return _with_undo(hou, "Blib Bridge connect nodes", _action)


def set_input(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    dst = _require_node(hou, payload["dst"], "Destination node not found")
    input_index = int(payload["input_index"])
    src = None if payload.get("clear", False) else _require_node(hou, payload["src"], "Source node not found")

    def _action():
        previous = _input_at(dst, input_index)
        _set_input(dst, input_index, src)
        return {
            "dst": _node_path(dst, payload["dst"]),
            "src": _node_path(src, payload.get("src", "")) if src is not None else None,
            "previous_src": _node_path(previous, "") if previous is not None else None,
            "input_index": input_index,
            "cleared": src is None,
            "touched": [_node_path(dst, payload["dst"])],
        }

    return _with_undo(hou, "Blib Bridge set node input", _action)


def disconnect(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    src = _require_node(hou, payload["src"], "Source node not found") if payload.get("src") else None
    src_path = _node_path(src, payload.get("src", "")) if src is not None else ""

    def _action():
        connections = _input_connections(node)
        target_indices: list[int] = []
        if payload.get("all", False):
            target_indices = [item["input_index"] for item in connections]
        elif payload.get("input_index") is not None:
            target_indices = [int(payload["input_index"])]
        else:
            target_indices = [item["input_index"] for item in connections if item["src_path"] == src_path]

        disconnected = []
        seen: set[int] = set()
        for input_index in target_indices:
            if input_index in seen:
                continue
            seen.add(input_index)
            previous = _input_at(node, input_index)
            if previous is None:
                continue
            _set_input(node, input_index, None)
            disconnected.append(
                {
                    "src": _node_path(previous, ""),
                    "dst": _node_path(node, payload["node"]),
                    "input_index": input_index,
                }
            )
        return {
            "node": _node_path(node, payload["node"]),
            "disconnected": disconnected,
            "count": len(disconnected),
            "touched": [_node_path(node, payload["node"])],
        }

    return _with_undo(hou, "Blib Bridge disconnect node inputs", _action)


def move_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parent = _require_node(hou, payload["parent"], "Parent network not found")
    name = payload.get("name")
    move_nodes_to = getattr(hou, "moveNodesTo", None)
    if move_nodes_to is None and not hasattr(node, "moveTo"):
        raise BridgeCommandError("Node does not support moving between parents: %s" % payload["node"])

    def _action():
        old_path = _node_path(node, payload["node"])
        moved_node = node
        if move_nodes_to is not None:
            moved = move_nodes_to([node], parent)
            if moved:
                moved_node = list(moved)[0]
        else:
            node.moveTo(parent)
        if name:
            _set_node_name(moved_node, name, unique=True)
        if hasattr(moved_node, "moveToGoodPosition"):
            _safe_call(moved_node.moveToGoodPosition, default=None)
        return {
            "old_path": old_path,
            "moved": _node_summary(moved_node),
            "parent": _node_path(parent, payload["parent"]),
            "touched": [old_path, _node_path(moved_node, old_path), _node_path(parent, payload["parent"])],
        }

    return _with_undo(hou, "Blib Bridge move node", _action)


def copy_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parent = _require_node(hou, payload["parent"], "Parent network not found")
    name = payload.get("name")
    unique = bool(payload.get("unique", True))
    if not hasattr(node, "copyTo"):
        raise BridgeCommandError("Node does not support copying: %s" % payload["node"])

    def _action():
        copied = node.copyTo(parent)
        if name:
            _set_node_name(copied, name, unique=unique)
        if hasattr(copied, "moveToGoodPosition"):
            _safe_call(copied.moveToGoodPosition, default=None)
        return {
            "source": _node_path(node, payload["node"]),
            "created": _node_summary(copied),
            "touched": [_node_path(parent, payload["parent"])],
        }

    return _with_undo(hou, "Blib Bridge copy node", _action)


def set_node_shape(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    shape = payload["shape"].strip()
    if not hasattr(node, "setUserData"):
        raise BridgeCommandError("Node does not support network editor user data: %s" % payload["node"])

    def _action():
        node.setUserData("nodeshape", shape)
        return {"touched": [_node_path(node, payload["node"])], "shape": shape}

    return _with_undo(hou, "Blib Bridge set node shape", _action)


def replace_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    old_node = _require_node(hou, payload["node"], "Node not found")
    parent = old_node.parent()
    if parent is None:
        raise BridgeCommandError("Cannot replace a root node: %s" % payload["node"])
    node_type = payload["type"].strip()
    name = payload.get("name")
    reconnect_inputs = bool(payload.get("reconnect_inputs", True))
    reconnect_outputs = bool(payload.get("reconnect_outputs", True))
    delete_old = bool(payload.get("delete_old", False))
    if delete_old and _safe_call(old_node.children, default=[]):
        raise BridgeCommandError("Refusing to replace-delete a node with children: %s" % payload["node"])

    def _action():
        old_path = _node_path(old_node, payload["node"])
        input_connections = _input_connections(old_node)
        output_connections = _output_connections(old_node)
        new_node = parent.createNode(node_type, node_name=name) if name else parent.createNode(node_type)
        if reconnect_inputs:
            for item in input_connections:
                _set_input(new_node, item["input_index"], item["src"])
        if reconnect_outputs:
            for item in output_connections:
                _set_input(item["dst"], item["input_index"], new_node)
        deleted = None
        if delete_old:
            _destroy_node(old_node)
            deleted = old_path
        if hasattr(new_node, "moveToGoodPosition"):
            _safe_call(new_node.moveToGoodPosition, default=None)
        touched = [old_path, _node_path(new_node, ""), _node_path(parent, "")]
        touched.extend(item["dst_path"] for item in output_connections if reconnect_outputs)
        return {
            "old_path": old_path,
            "replacement": _node_summary(new_node),
            "reconnected_inputs": reconnect_inputs,
            "reconnected_outputs": reconnect_outputs,
            "deleted_old": deleted,
            "input_count": len(input_connections) if reconnect_inputs else 0,
            "output_count": len(output_connections) if reconnect_outputs else 0,
            "touched": _unique_paths(touched),
        }

    return _with_undo(hou, "Blib Bridge replace node", _action)


def delete_node(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parent = node.parent()
    if parent is None:
        raise BridgeCommandError("Cannot delete a root node: %s" % payload["node"])
    children = list(_safe_call(node.children, default=[]))
    if children and not payload.get("delete_contents", False):
        raise BridgeCommandError(
            "Refusing to delete node with children without delete_contents=true: %s" % payload["node"]
        )

    def _action():
        path = _node_path(node, payload["node"])
        parent_path = _node_path(parent, "")
        _destroy_node(node)
        return {
            "deleted": path,
            "delete_contents": bool(payload.get("delete_contents", False)),
            "touched": [parent_path],
        }

    return _with_undo(hou, "Blib Bridge delete node", _action)


def layout(hou: Any, path: str) -> dict[str, Any]:
    node = _require_node(hou, path, "Network not found")

    def _action():
        node.layoutChildren()
        return {"touched": [path]}

    return _with_undo(hou, "Blib Bridge layout network", _action)


def select(hou: Any, path: str) -> dict[str, Any]:
    node = _require_node(hou, path, "Node not found")

    def _action():
        parent = node.parent()
        if parent is not None:
            for child in _safe_call(parent.children, default=[]):
                _safe_call(lambda child=child: child.setSelected(False), default=None)
        node.setSelected(True)
        return {"selected": _node_summary(node)}

    return _with_undo(hou, "Blib Bridge select node", _action)


def _capture_flipbook(hou: Any, viewer: Any, path: str, width: int, height: int) -> None:
    settings = viewer.flipbookSettings().stash()
    settings.output(path)
    settings.frameRange((hou.frame(), hou.frame()))
    settings.resolution((width, height))
    viewer.flipbook(viewer.curViewport(), settings)


def _import_hou() -> Any:
    try:
        import hou  # type: ignore
    except ImportError as exc:
        raise BridgeCommandError("This command must run inside Houdini.") from exc
    return hou


def _require_edit_enabled(command: str) -> None:
    if not state.edit_enabled():
        raise BridgeCommandError(
            "%s requires edit mode. Enable it from the Blib Bridge shelf before running write commands." % command
        )


def _require_node(hou: Any, path: str, message: str) -> Any:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("%s: %s" % (message, path))
    return node


def _node_path(node: Any, fallback: str = "") -> str:
    if node is None:
        return fallback
    return _safe_call(node.path, default=fallback)


def _input_at(node: Any, input_index: int) -> Any | None:
    inputs = list(_safe_call(node.inputs, default=[]))
    if input_index < 0 or input_index >= len(inputs):
        return None
    return inputs[input_index]


def _set_input(dst: Any, input_index: int, src: Any | None) -> None:
    try:
        dst.setInput(input_index, src)
    except TypeError:
        dst.setInput(input_index, src, 0)


def _input_connections(node: Any) -> list[dict[str, Any]]:
    connections = []
    for input_index, src in enumerate(_safe_call(node.inputs, default=[])):
        if src is None:
            continue
        connections.append(
            {
                "src": src,
                "src_path": _node_path(src, ""),
                "dst": node,
                "dst_path": _node_path(node, ""),
                "input_index": input_index,
            }
        )
    return connections


def _output_connections(node: Any) -> list[dict[str, Any]]:
    node_path = _node_path(node, "")
    connections = []
    for dst in _safe_call(node.outputs, default=[]):
        dst_path = _node_path(dst, "")
        for input_index, src in enumerate(_safe_call(dst.inputs, default=[])):
            if src is not None and _node_path(src, "") == node_path:
                connections.append(
                    {
                        "src": node,
                        "src_path": node_path,
                        "dst": dst,
                        "dst_path": dst_path,
                        "input_index": input_index,
                    }
                )
    return connections


def _set_node_name(node: Any, name: str, unique: bool = True) -> None:
    try:
        node.setName(name, unique_name=unique)
    except TypeError:
        try:
            node.setName(name, unique)
        except TypeError:
            node.setName(name)


def _destroy_node(node: Any) -> None:
    try:
        node.destroy()
    except TypeError:
        node.destroy(False)


def _unique_paths(paths: list[str]) -> list[str]:
    return [path for path in dict.fromkeys(paths) if path]


def _resolve_parm_profile(node: Any, profile_name: str, values: dict[str, Any], strict: bool = False) -> dict[str, Any]:
    available = [parm.name() for parm in _safe_call(node.parms, default=[])]
    return dynamics_profiles.resolve_profile(profile_name, values, available, strict=strict)


def _validate_plan_step(
    hou: Any,
    index: int,
    step: dict[str, Any],
    planned_paths: set[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    command = protocol.normalize_command(step.get("command"))
    payload = step.get("payload", {}) or {}
    permission = "edit" if command in protocol.EDIT_COMMANDS else "read"
    issues = []
    warnings = []
    touches = []
    creates = []
    deletes = []
    step_aliases: dict[str, str] = {}
    try:
        protocol.validate_command(command, payload)
    except Exception as exc:
        issues.append(str(exc))
    if not issues:
        report = _inspect_plan_step(hou, command, payload, planned_paths or set(), aliases or {})
        issues.extend(report.get("issues", []))
        warnings.extend(report.get("warnings", []))
        touches.extend(report.get("touches", []))
        creates.extend(report.get("creates", []))
        deletes.extend(report.get("deletes", []))
        step_aliases.update(report.get("aliases", {}))
    return {
        "index": index,
        "command": command,
        "payload": payload,
        "permission": permission,
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
        "touches": touches,
        "creates": creates,
        "deletes": deletes,
        "aliases": step_aliases,
    }


def _inspect_plan_step(
    hou: Any,
    command: str,
    payload: dict[str, Any],
    planned_paths: set[str],
    aliases: dict[str, str],
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    touches: list[str] = []
    creates: list[str] = []
    deletes: list[str] = []
    step_aliases: dict[str, str] = {}

    if command in {"node_info", "node_parms", "network", "upstream", "downstream", "layout", "select"}:
        _plan_require_node(hou, payload.get("path"), issues, planned_paths, aliases)
        touches.extend([payload["path"]] if command in {"layout", "select"} and "path" in payload else [])
    elif command == "find_nodes":
        _plan_require_node(hou, payload.get("root"), issues, planned_paths, aliases)
    elif command == "scene_snapshot":
        if payload.get("path"):
            _plan_require_node(hou, payload.get("path"), issues, planned_paths, aliases)
    elif command == "create_node":
        parent_path = payload.get("parent")
        _plan_require_node(hou, parent_path, issues, planned_paths, aliases)
        name = payload.get("name")
        if isinstance(parent_path, str) and name:
            target = "%s/%s" % (parent_path.rstrip("/"), name)
            creates.append(target)
            if hou.node(target) is not None or target in planned_paths:
                warnings.append("Target node already exists: %s" % target)
        elif isinstance(parent_path, str):
            creates.append("%s/<auto %s>" % (parent_path.rstrip("/"), payload.get("type", "node")))
    elif command == "set_parm":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        parm_name = payload.get("parm")
        if node is not None and node.parm(parm_name) is None:
            issues.append("Parameter not found: %s/%s" % (payload.get("node"), parm_name))
        elif node is None and _resolved_path(payload.get("node"), aliases) in planned_paths:
            warnings.append("Parameter existence cannot be checked on planned node: %s/%s" % (payload.get("node"), parm_name))
        if payload.get("node"):
            touches.append(payload["node"])
    elif command == "set_parm_any":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        parms = payload.get("parms", []) or []
        if node is not None:
            matched = [parm_name for parm_name in parms if node.parm(parm_name) is not None]
            if not matched and payload.get("required", False):
                issues.append("No candidate parameter found: %s/%s" % (payload.get("node"), ", ".join(parms)))
            elif not matched:
                warnings.append("No candidate parameter currently exists: %s/%s" % (payload.get("node"), ", ".join(parms)))
        elif node is None and _resolved_path(payload.get("node"), aliases) in planned_paths:
            warnings.append("Parameter candidates cannot be checked on planned node: %s/%s" % (payload.get("node"), ", ".join(parms)))
        if payload.get("node"):
            touches.append(payload["node"])
    elif command == "batch_set_parms":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        values = payload.get("values", {}) or {}
        missing = []
        if node is not None:
            missing = [parm_name for parm_name in values if node.parm(parm_name) is None]
            if missing and payload.get("required", True):
                issues.append("Parameters not found: %s/%s" % (payload.get("node"), ", ".join(missing)))
            elif missing:
                warnings.append("Some parameters currently do not exist: %s/%s" % (payload.get("node"), ", ".join(missing)))
        elif node is None and _resolved_path(payload.get("node"), aliases) in planned_paths:
            warnings.append("Parameter existence cannot be checked on planned node: %s" % payload.get("node"))
        if payload.get("node"):
            touches.append(payload["node"])
    elif command == "apply_parm_profile":
        profile_name = dynamics_profiles.normalize_profile_name(payload.get("profile", ""))
        profile = dynamics_profiles.get_profile(profile_name)
        if profile is None:
            issues.append("Unknown parameter profile: %s" % payload.get("profile"))
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        candidates = dynamics_profiles.candidate_names(profile_name)
        if node is not None and profile is not None:
            node_parms = {parm.name() for parm in _safe_call(node.parms, default=[])}
            matched = [parm_name for parm_name in candidates if parm_name in node_parms]
            if not matched:
                message = "No profile parameter candidates currently exist: %s/%s" % (payload.get("node"), profile_name)
                if payload.get("strict", False):
                    issues.append(message)
                else:
                    warnings.append(message)
        elif node is None and _resolved_path(payload.get("node"), aliases) in planned_paths:
            warnings.append("Profile parameters cannot be checked on planned node: %s/%s" % (payload.get("node"), profile_name))
        if payload.get("node"):
            touches.append(payload["node"])
    elif command == "probe_parm_profile":
        profile_name = dynamics_profiles.normalize_profile_name(payload.get("profile", ""))
        if dynamics_profiles.get_profile(profile_name) is None:
            issues.append("Unknown parameter profile: %s" % payload.get("profile"))
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        if node is not None:
            try:
                probe = _resolve_parm_profile(node, profile_name, payload.get("values", {}) or {}, bool(payload.get("strict", False)))
            except Exception as exc:
                issues.append(str(exc))
            else:
                if probe.get("unresolved"):
                    issues.append("Profile parameters unresolved: %s" % ", ".join(item["parameter"] for item in probe["unresolved"]))
                elif probe.get("skipped"):
                    warnings.append("Some profile parameters would be skipped: %s" % ", ".join(item["parameter"] for item in probe["skipped"]))
    elif command in {"rename_node", "set_node_color", "bypass_node", "set_comment", "set_flags", "set_position", "ensure_parm", "set_node_shape"}:
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        if payload.get("node"):
            touches.append(payload["node"])
        if command == "ensure_parm" and node is not None and node.parm(payload.get("name")) is not None:
            warnings.append("Parameter already exists: %s/%s" % (payload.get("node"), payload.get("name")))
        if command == "rename_node":
            old_path = _resolved_path(payload.get("node"), aliases)
            parent_path = ""
            if node is not None:
                parent_path = _safe_call(lambda: node.parent().path() if node.parent() else "", default="")
            elif isinstance(old_path, str) and "/" in old_path:
                parent_path = old_path.rsplit("/", 1)[0]
            if parent_path and payload.get("name"):
                new_path = "%s/%s" % (parent_path.rstrip("/"), payload.get("name"))
                creates.append(new_path)
                step_aliases[str(payload.get("node"))] = new_path
                step_aliases[str(old_path)] = new_path
                if hou.node(new_path) is not None or new_path in planned_paths:
                    warnings.append("Target sibling name already exists: %s" % new_path)
    elif command == "create_network_box":
        _plan_require_node(hou, payload.get("parent"), issues, planned_paths, aliases)
        for path in payload.get("nodes", []) or []:
            _plan_require_node(hou, path, issues, planned_paths, aliases)
        if payload.get("parent"):
            touches.append(payload["parent"])
        if payload.get("name") and payload.get("parent"):
            creates.append("%s/%s" % (payload["parent"].rstrip("/"), payload["name"]))
    elif command == "create_sticky_note":
        _plan_require_node(hou, payload.get("parent"), issues, planned_paths, aliases)
        if payload.get("parent"):
            touches.append(payload["parent"])
        if payload.get("name") and payload.get("parent"):
            creates.append("%s/%s" % (payload["parent"].rstrip("/"), payload["name"]))
    elif command == "connect":
        _plan_require_node(hou, payload.get("src"), issues, planned_paths, aliases)
        _plan_require_node(hou, payload.get("dst"), issues, planned_paths, aliases)
        if payload.get("dst"):
            touches.append(payload["dst"])
    elif command == "set_input":
        if not payload.get("clear", False):
            _plan_require_node(hou, payload.get("src"), issues, planned_paths, aliases)
        _plan_require_node(hou, payload.get("dst"), issues, planned_paths, aliases)
        if payload.get("dst"):
            touches.append(payload["dst"])
    elif command == "disconnect":
        _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        if payload.get("src"):
            _plan_require_node(hou, payload.get("src"), issues, planned_paths, aliases)
        if payload.get("node"):
            touches.append(payload["node"])
    elif command == "move_node":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        _plan_require_node(hou, payload.get("parent"), issues, planned_paths, aliases)
        old_path = _resolved_path(payload.get("node"), aliases)
        new_path = _planned_node_path(payload.get("parent"), payload.get("name") or _planned_node_name(node, old_path), aliases)
        if new_path:
            creates.append(new_path)
            step_aliases[str(payload.get("node"))] = new_path
            step_aliases[str(old_path)] = new_path
            if hou.node(new_path) is not None or new_path in planned_paths:
                warnings.append("Target node already exists: %s" % new_path)
        if payload.get("node"):
            touches.append(payload["node"])
        if payload.get("parent"):
            touches.append(payload["parent"])
    elif command == "copy_node":
        _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        _plan_require_node(hou, payload.get("parent"), issues, planned_paths, aliases)
        new_path = _planned_node_path(payload.get("parent"), payload.get("name"), aliases)
        if new_path:
            creates.append(new_path)
            if hou.node(new_path) is not None or new_path in planned_paths:
                warnings.append("Target node already exists: %s" % new_path)
        elif isinstance(payload.get("parent"), str):
            creates.append("%s/<auto copy>" % _resolved_path(payload["parent"], aliases).rstrip("/"))
        if payload.get("node"):
            touches.append(payload["node"])
        if payload.get("parent"):
            touches.append(payload["parent"])
    elif command == "replace_node":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        old_path = _resolved_path(payload.get("node"), aliases)
        parent_path = ""
        if node is not None:
            parent_path = _safe_call(lambda: node.parent().path() if node.parent() else "", default="")
        elif isinstance(old_path, str) and "/" in old_path:
            parent_path = old_path.rsplit("/", 1)[0]
        target_name = payload.get("name")
        new_path = _planned_node_path(parent_path, target_name, aliases)
        if not new_path and parent_path:
            new_path = "%s/<auto %s>" % (_resolved_path(parent_path, aliases).rstrip("/"), payload.get("type", "node"))
        if new_path:
            creates.append(new_path)
            if payload.get("delete_old", False) and "<auto " not in new_path:
                step_aliases[str(payload.get("node"))] = new_path
                step_aliases[str(old_path)] = new_path
            if (hou.node(new_path) is not None or new_path in planned_paths) and new_path != old_path:
                warnings.append("Replacement target already exists: %s" % new_path)
            elif new_path == old_path and not payload.get("delete_old", False):
                warnings.append("Replacement name matches the old node and delete_old is false: %s" % new_path)
        if payload.get("delete_old", False) and node is not None and _safe_call(node.children, default=[]):
            issues.append("Refusing to replace-delete a node with children: %s" % old_path)
        if payload.get("delete_old", False) and isinstance(old_path, str):
            deletes.append(old_path)
        if payload.get("node"):
            touches.append(payload["node"])
        if parent_path:
            touches.append(parent_path)
        for output_node in _safe_call(node.outputs, default=[]) if node is not None else []:
            touches.append(_safe_call(output_node.path, default=""))
    elif command == "delete_node":
        node = _plan_require_node(hou, payload.get("node"), issues, planned_paths, aliases)
        if payload.get("node"):
            touches.append(payload["node"])
            deletes.append(_resolved_path(payload["node"], aliases))
        if node is not None and _safe_call(node.children, default=[]) and not payload.get("delete_contents", False):
            issues.append("Refusing to delete node with children without delete_contents=true: %s" % payload.get("node"))
        parent_path = _safe_call(lambda: node.parent().path() if node is not None and node.parent() else "", default="")
        if parent_path:
            touches.append(parent_path)
    elif command == "edit_mode":
        touches.append("<bridge_state>")
    elif command == "validate_plan":
        warnings.append("Nested validate_plan steps are checked by protocol only.")
    elif command == "run_plan":
        nested = validate_plan(hou, {"steps": payload.get("steps", [])})
        if not nested.get("valid", False):
            issues.append("Nested run_plan steps are invalid.")
        warnings.append("Nested run_plan steps are checked as a batch.")
        for report in nested.get("steps", []) or []:
            touches.extend(report.get("touches", []) or [])
            creates.extend(report.get("creates", []) or [])
            deletes.extend(report.get("deletes", []) or [])

    return {"issues": issues, "warnings": warnings, "touches": touches, "creates": creates, "deletes": deletes, "aliases": step_aliases}


def _planned_node_path(parent_path: Any, name: Any, aliases: dict[str, str]) -> str:
    if not isinstance(parent_path, str) or not isinstance(name, str) or not name:
        return ""
    return "%s/%s" % (_resolved_path(parent_path, aliases).rstrip("/"), name)


def _planned_node_name(node: Any | None, path: Any) -> str:
    if node is not None:
        return _safe_call(node.name, default="")
    if isinstance(path, str) and "/" in path:
        return path.rsplit("/", 1)[-1]
    return ""


def _plan_require_node(
    hou: Any,
    path: Any,
    issues: list[str],
    planned_paths: set[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> Any | None:
    if not isinstance(path, str):
        issues.append("Node path is missing.")
        return None
    resolved = _resolved_path(path, aliases or {})
    if resolved in (planned_paths or set()):
        return None
    node = hou.node(resolved)
    if node is None:
        issues.append("Node not found: %s" % resolved)
    return node


def _resolved_path(path: Any, aliases: dict[str, str]) -> Any:
    if not isinstance(path, str):
        return path
    seen = set()
    current = path
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def _with_undo(hou: Any, label: str, action: Any) -> dict[str, Any]:
    undos = getattr(hou, "undos", None)
    group = getattr(undos, "group", None) if undos is not None else None
    if group is None or _PLAN_UNDO_DEPTH > 0:
        result = action()
    else:
        with group(label):
            result = action()
    result["edit_enabled"] = state.edit_enabled()
    return result


def _node_summary(node: Any) -> dict[str, Any]:
    return {
        "path": _safe_call(node.path, default=""),
        "name": _safe_call(node.name, default=""),
        "type": _safe_call(lambda: node.type().name(), default=""),
        "category": _safe_call(lambda: node.type().category().name(), default=""),
        "parent": _safe_call(lambda: node.parent().path() if node.parent() else "", default=""),
    }


def _scene_semantics(
    network_result: dict[str, Any],
    selected_result: dict[str, Any],
    traces: list[dict[str, Any]],
    viewport: dict[str, Any],
) -> dict[str, Any]:
    nodes = [node for node in network_result.get("nodes", []) if isinstance(node, dict)]
    wires = [wire for wire in network_result.get("wires", []) if isinstance(wire, dict)]
    display_nodes = set(network_result.get("display_nodes", []) or [])
    render_nodes = set(network_result.get("render_nodes", []) or [])
    selected_paths = {node.get("path") for node in selected_result.get("nodes", []) or [] if isinstance(node, dict)}
    outgoing = _wire_count_by_key(wires, "src")
    incoming = _wire_count_by_key(wires, "dst")

    key_outputs = []
    cache_nodes = []
    simulation_nodes = []
    volume_nodes = []
    render_nodes_semantic = []
    risk_notes = []
    type_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}

    for node in nodes:
        path = str(node.get("path") or "")
        name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        category = str(node.get("category") or "")
        type_key = node_type.lower()
        name_key = name.lower()
        if node_type:
            type_counts[node_type] = type_counts.get(node_type, 0) + 1
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

        reasons = []
        if path in display_nodes:
            reasons.append("display_flag")
        if path in render_nodes:
            reasons.append("render_flag")
        if _looks_like_output(name_key, type_key):
            reasons.append("output_name")
        if outgoing.get(path, 0) == 0 and incoming.get(path, 0) > 0:
            reasons.append("terminal_node")
        if reasons:
            key_outputs.append({"path": path, "name": name, "type": node_type, "reasons": sorted(set(reasons))})

        if _looks_like_cache(name_key, type_key):
            cache_nodes.append({"path": path, "name": name, "type": node_type})
        if _looks_like_simulation(name_key, type_key):
            simulation_nodes.append({"path": path, "name": name, "type": node_type})
        if _looks_like_volume(name_key, type_key):
            volume_nodes.append({"path": path, "name": name, "type": node_type})
        if _looks_like_render(name_key, type_key, category.lower()):
            render_nodes_semantic.append({"path": path, "name": name, "type": node_type, "category": category})

    for message in network_result.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        path = message.get("path", "")
        for error in message.get("errors", []) or []:
            risk_notes.append({"kind": "node_error", "path": path, "message": str(error)})
        for warning in message.get("warnings", []) or []:
            risk_notes.append({"kind": "node_warning", "path": path, "message": str(warning)})
    if selected_result.get("count", 0) == 0:
        risk_notes.append({"kind": "no_selection", "message": "No Houdini nodes are selected."})
    if not key_outputs:
        risk_notes.append({"kind": "no_key_output", "message": "No display/render/output/terminal node was inferred in the current network."})
    if viewport.get("included") and not viewport.get("ok", True):
        risk_notes.append({"kind": "viewport_capture_failed", "message": str(viewport.get("error") or "")})

    key_outputs = _unique_by_path(key_outputs, limit=12)
    selected_focus = _selected_focus(selected_paths, traces)
    cache_nodes = _unique_by_path(cache_nodes, limit=12)
    simulation_nodes = _unique_by_path(simulation_nodes, limit=12)
    volume_nodes = _unique_by_path(volume_nodes, limit=12)
    render_nodes_semantic = _unique_by_path(render_nodes_semantic, limit=12)
    risk_notes = risk_notes[:20]
    focus_candidates = _focus_candidates(
        nodes,
        selected_paths,
        key_outputs,
        cache_nodes,
        simulation_nodes,
        volume_nodes,
        render_nodes_semantic,
        risk_notes,
        display_nodes,
        render_nodes,
        incoming,
        outgoing,
    )

    inferred_purpose = _infer_network_purpose(nodes, cache_nodes, simulation_nodes, volume_nodes, render_nodes_semantic)
    workflow_suggestions = _workflow_suggestions(
        inferred_purpose,
        selected_paths,
        key_outputs,
        cache_nodes,
        simulation_nodes,
        volume_nodes,
        render_nodes_semantic,
    )
    risk_domains = _scene_risk_domains(cache_nodes, simulation_nodes, volume_nodes, render_nodes_semantic, risk_notes, workflow_suggestions)
    inspection_hints = _inspection_hints(
        network_result.get("path", ""),
        selected_focus,
        key_outputs,
        cache_nodes,
        simulation_nodes,
        volume_nodes,
        render_nodes_semantic,
        risk_notes,
    )
    scene_understanding = _scene_understanding_route(
        network_result.get("path", ""),
        inferred_purpose,
        _semantic_confidence(nodes, key_outputs, risk_notes),
        focus_candidates,
        risk_domains,
        inspection_hints,
        workflow_suggestions,
        risk_notes,
    )

    return {
        "inferred_purpose": inferred_purpose,
        "confidence": scene_understanding["confidence"],
        "key_outputs": key_outputs,
        "focus_candidates": focus_candidates,
        "selected_focus": selected_focus,
        "cache_nodes": cache_nodes,
        "simulation_nodes": simulation_nodes,
        "volume_nodes": volume_nodes,
        "render_nodes": render_nodes_semantic,
        "risk_notes": risk_notes,
        "risk_domains": risk_domains,
        "inspection_hints": inspection_hints,
        "workflow_suggestions": workflow_suggestions,
        "scene_understanding": scene_understanding,
        "network_shape": {
            "node_count": len(nodes),
            "wire_count": len(wires),
            "terminal_count": len([path for path, count in outgoing.items() if count == 0 and incoming.get(path, 0) > 0]),
            "branch_count": len([path for path, count in outgoing.items() if count > 1]),
            "display_count": len(display_nodes),
            "render_count": len(render_nodes),
            "type_counts": dict(sorted(type_counts.items())),
            "category_counts": dict(sorted(category_counts.items())),
        },
    }


def _wire_count_by_key(wires: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for wire in wires:
        path = wire.get(key)
        if isinstance(path, str) and path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _looks_like_output(name: str, node_type: str) -> bool:
    return name.startswith(("out", "output", "render", "final")) or node_type in {"null", "output", "suboutput"}


def _looks_like_cache(name: str, node_type: str) -> bool:
    cache_terms = ("cache", "filecache", "rop_geometry", "dopio", "dopimport", "geometryrop")
    return any(term in name or term in node_type for term in cache_terms)


def _looks_like_simulation(name: str, node_type: str) -> bool:
    sim_terms = (
        "solver",
        "dop",
        "rbd",
        "vellum",
        "pyro",
        "flip",
        "pop",
        "grain",
        "constraint",
        "fracture",
    )
    return any(term in name or term in node_type for term in sim_terms)


def _looks_like_volume(name: str, node_type: str) -> bool:
    if node_type in {"null", "output", "suboutput"}:
        return False
    volume_terms = (
        "vdb",
        "volume",
        "sdf",
        "fog",
        "density",
        "volumerasterize",
        "rasterizeattributes",
        "vdbfrompolygons",
        "vdbreshape",
        "vdbsmooth",
    )
    return any(term in name or term in node_type for term in volume_terms)


def _looks_like_render(name: str, node_type: str, category: str) -> bool:
    render_terms = ("karma", "render", "rop", "usd", "lop", "materiallibrary")
    return any(term in name or term in node_type or term in category for term in render_terms)


def _infer_network_purpose(
    nodes: list[dict[str, Any]],
    cache_nodes: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
) -> str:
    if not nodes:
        return "empty_network"
    if render_nodes:
        return "render_or_solaris_setup"
    if simulation_nodes and cache_nodes:
        return "simulation_with_cache"
    if simulation_nodes:
        return "simulation_setup"
    if volume_nodes:
        return "volume_or_vdb_setup"
    if cache_nodes:
        return "cache_or_export_setup"
    return "sop_or_general_node_network"


def _semantic_confidence(
    nodes: list[dict[str, Any]],
    key_outputs: list[dict[str, Any]],
    risk_notes: list[dict[str, Any]],
) -> str:
    if not nodes:
        return "low"
    if key_outputs and len(risk_notes) <= 1:
        return "medium"
    if key_outputs:
        return "low"
    return "low"


def _scene_risk_domains(
    cache_nodes: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
    risk_notes: list[dict[str, Any]],
    workflow_suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    domains: dict[str, dict[str, Any]] = {}

    def add(domain: str, priority_score: int, reason: str, paths: list[str] | None = None, template: str = "") -> None:
        item = domains.setdefault(
            domain,
            {
                "domain": domain,
                "_score": 0,
                "reasons": [],
                "paths": [],
                "workflow_templates": [],
            },
        )
        item["_score"] = max(int(item.get("_score", 0)), priority_score)
        if reason and reason not in item["reasons"]:
            item["reasons"].append(reason)
        for path in paths or []:
            if isinstance(path, str) and path and path not in item["paths"]:
                item["paths"].append(path)
        if template and template not in item["workflow_templates"]:
            item["workflow_templates"].append(template)

    cache_paths = _risk_domain_paths(cache_nodes)
    simulation_paths = _risk_domain_paths(simulation_nodes)
    volume_paths = _risk_domain_paths(volume_nodes)
    render_paths = _risk_domain_paths(render_nodes)
    if cache_nodes:
        add("cache_output", 32, "Cache/export nodes are present; inspect paths, frame ranges, and cook impact.", cache_paths)
        add("file_path", 24, "Cache/export workflows often depend on output file paths.", cache_paths)
        add("cook_cost", 20, "Cache/export nodes may trigger expensive cooks.", cache_paths)
    if simulation_nodes:
        add("simulation_settings", 34, "Simulation or solver nodes are present; inspect substeps, start frame, and solver parameters.", simulation_paths)
        add("cache_strategy", 26, "Simulation work should be paired with an explicit cache strategy.", simulation_paths)
        add("cook_cost", 24, "Simulation nodes may increase cook cost.", simulation_paths)
    if volume_nodes:
        add("volume_resolution", 30, "VDB/volume nodes are present; inspect voxel size, active bounds, and resolution.", volume_paths)
        add("cook_cost", 22, "VDB/volume operations may increase cook cost.", volume_paths)
    if render_nodes:
        add("render_settings", 32, "Render/Solaris nodes are present; inspect camera, render path, resolution, and renderer.", render_paths)
        add("file_path", 24, "Render workflows often depend on output image or USD file paths.", render_paths)
        add("camera_material_review", 22, "Render setup should be reviewed for camera and material assumptions.", render_paths)

    for note in risk_notes:
        if not isinstance(note, dict):
            continue
        kind = note.get("kind")
        path = str(note.get("path") or "")
        if kind == "node_error":
            add("node_errors", 40, "One or more nodes report errors.", [path] if path else [])
        elif kind == "node_warning":
            add("node_warnings", 28, "One or more nodes report warnings.", [path] if path else [])
        elif kind == "no_key_output":
            add("network_output", 18, "No display/render/output/terminal node was inferred.", [])

    for suggestion in workflow_suggestions:
        if not isinstance(suggestion, dict):
            continue
        template = str(suggestion.get("template") or "")
        paths = [str(suggestion.get("input_path") or "")]
        for domain in suggestion.get("risk_domains", []) or []:
            if isinstance(domain, str) and domain:
                add(domain, 18, "Suggested workflow template references this risk domain.", paths, template=template)

    ordered = sorted(domains.values(), key=lambda item: (-int(item.get("_score", 0)), item.get("domain", "")))
    result = []
    for item in ordered[:12]:
        score = int(item.pop("_score", 0))
        item["priority"] = "high" if score >= 32 else ("medium" if score >= 22 else "low")
        item["paths"] = item["paths"][:8]
        item["path_count"] = len(item["paths"])
        item["workflow_templates"] = sorted(item["workflow_templates"])
        item["suggested_tools"] = _risk_domain_tools(str(item.get("domain") or ""))
        result.append(item)
    return result


def _risk_domain_paths(nodes: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("path") or "") for item in nodes if isinstance(item, dict) and item.get("path")]


def _risk_domain_tools(domain: str) -> list[str]:
    if domain in {"node_errors", "node_warnings", "network_output"}:
        return ["houdini_network", "houdini_node_info"]
    if domain in {"cache_output", "file_path", "render_settings", "camera_material_review"}:
        return ["houdini_node_info", "houdini_node_parms", "houdini_upstream"]
    if domain in {"simulation_settings", "cache_strategy", "cook_cost", "volume_resolution"}:
        return ["houdini_node_info", "houdini_node_parms", "houdini_upstream"]
    return ["houdini_scene_snapshot"]


def _workflow_suggestions(
    inferred_purpose: str,
    selected_paths: set[Any],
    key_outputs: list[dict[str, Any]],
    cache_nodes: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    input_path = _workflow_input_path(selected_paths, key_outputs, simulation_nodes, volume_nodes, cache_nodes, render_nodes)
    if not input_path:
        return suggestions
    catalog = workflow_templates.template_catalog()
    templates = catalog.get("templates", {}) if isinstance(catalog, dict) else {}
    workflow_policy = catalog.get("workflow_policy", {}) if isinstance(catalog, dict) else {}
    required_flow = workflow_policy.get("required_flow", workflow_templates.TEMPLATE_REQUIRED_FLOW)
    evidence_expectations = workflow_policy.get("evidence_expectations", workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)

    if render_nodes or inferred_purpose == "render_or_solaris_setup":
        _add_workflow_suggestion(
            suggestions,
            templates,
            "karma-solaris-preview",
            input_path,
            "high",
            "Render/Solaris context detected; a Karma/Solaris preview template can prepare a reviewable render setup without executing a render.",
            required_flow,
            evidence_expectations,
        )
    if volume_nodes or inferred_purpose == "volume_or_vdb_setup":
        _add_workflow_suggestion(
            suggestions,
            templates,
            "vdb-sdf-preview",
            input_path,
            "medium",
            "Volume/VDB context detected; generate a reviewable VDB SDF preview plan only after inspecting voxel size and cook cost.",
            required_flow,
            evidence_expectations,
        )
    if simulation_nodes:
        sim_text = " ".join("%s %s" % (item.get("name", ""), item.get("type", "")) for item in simulation_nodes).lower()
        if "rbd" in sim_text or "fracture" in sim_text or "bullet" in sim_text:
            _add_workflow_suggestion(
                suggestions,
                templates,
                "rbd-preview",
                input_path,
                "high",
                "RBD or fracture simulation context detected; use the template only to generate a reviewable preview plan.",
                required_flow,
                evidence_expectations,
            )
        if "grain" in sim_text:
            _add_workflow_suggestion(
                suggestions,
                templates,
                "vellum-grains-preview",
                input_path,
                "medium",
                "Grain/Vellum context detected; generate a reviewable grains preview plan if the current setup needs rebuilding.",
                required_flow,
                evidence_expectations,
            )
        elif "vellum" in sim_text or "cloth" in sim_text:
            _add_workflow_suggestion(
                suggestions,
                templates,
                "vellum-cloth-preview",
                input_path,
                "medium",
                "Vellum/cloth context detected; generate a reviewable cloth preview plan if the current setup needs rebuilding.",
                required_flow,
                evidence_expectations,
            )
        if "pyro" in sim_text or "smoke" in sim_text or "volume" in sim_text:
            _add_workflow_suggestion(
                suggestions,
                templates,
                "pyro-source-preview",
                input_path,
                "medium",
                "Pyro or volume simulation context detected; generate a reviewable source/solver preview plan only after inspection.",
                required_flow,
                evidence_expectations,
            )
    if cache_nodes or inferred_purpose in {"cache_or_export_setup", "simulation_with_cache"}:
        _add_workflow_suggestion(
            suggestions,
            templates,
            "cache-output",
            input_path,
            "medium",
            "Cache/export context detected; use the template to draft a filecache handoff plan after checking existing paths.",
            required_flow,
            evidence_expectations,
        )
    if inferred_purpose == "sop_or_general_node_network":
        _add_workflow_suggestion(
            suggestions,
            templates,
            "sop-cleanup",
            input_path,
            "low",
            "General SOP network detected; cleanup template may be useful when the user asks for geometry cleanup.",
            required_flow,
            evidence_expectations,
        )
    return suggestions[:6]


def _workflow_input_path(
    selected_paths: set[Any],
    key_outputs: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    cache_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
) -> str:
    for values in (
        sorted(path for path in selected_paths if isinstance(path, str) and path.startswith("/")),
        [str(item.get("path") or "") for item in key_outputs],
        [str(item.get("path") or "") for item in simulation_nodes],
        [str(item.get("path") or "") for item in volume_nodes],
        [str(item.get("path") or "") for item in cache_nodes],
        [str(item.get("path") or "") for item in render_nodes],
    ):
        for path in values:
            if path.startswith("/"):
                return path
    return ""


def _add_workflow_suggestion(
    suggestions: list[dict[str, Any]],
    templates: dict[str, Any],
    template: str,
    input_path: str,
    priority: str,
    reason: str,
    required_flow: Any,
    evidence_expectations: Any,
) -> None:
    if any(item.get("template") == template for item in suggestions):
        return
    catalog_entry = templates.get(template, {}) if isinstance(templates, dict) else {}
    category = str(catalog_entry.get("category") or "")
    suggestions.append(
        {
            "template": template,
            "category": category,
            "priority": priority,
            "reason": reason,
            "input_path": input_path,
            "mcp_tool": "houdini_template_plan",
            "template_arguments": {
                "template": template,
                "input": input_path,
                "options": {"preset": "preview"},
            },
            "required_flow": list(required_flow) if isinstance(required_flow, list) else list(workflow_templates.TEMPLATE_REQUIRED_FLOW),
            "risk_domains": list(catalog_entry.get("risk_domains", [])) if isinstance(catalog_entry, dict) else [],
            "evidence_expectations": list(evidence_expectations)
            if isinstance(evidence_expectations, list)
            else list(workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS),
            "suggested_next_tools": ["houdini_template_plan", "houdini_review_plan", "houdini_validate_plan"],
            "local_generation_only": True,
        }
    )


def _scene_understanding_route(
    network_path: str,
    inferred_purpose: str,
    confidence: str,
    focus_candidates: list[dict[str, Any]],
    risk_domains: list[dict[str, Any]],
    inspection_hints: list[dict[str, Any]],
    workflow_suggestions: list[dict[str, Any]],
    risk_notes: list[dict[str, Any]],
) -> dict[str, Any]:
    def unique_strings(values: list[Any]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if not isinstance(value, str) or not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    primary_risk = risk_domains[0] if risk_domains else {}
    primary_focus = focus_candidates[0] if focus_candidates else {}
    read_targets = []
    for item in focus_candidates[:5]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        read_targets.append(
            {
                "path": path,
                "priority": item.get("priority") or "unknown",
                "kinds": item.get("kinds", []) if isinstance(item.get("kinds"), list) else [],
                "mcp_tools": item.get("mcp_tools", []) if isinstance(item.get("mcp_tools"), list) else [],
            }
        )
    first_read_tools: list[Any] = []
    if isinstance(primary_risk.get("suggested_tools"), list):
        first_read_tools.extend(primary_risk["suggested_tools"])
    if isinstance(primary_focus.get("mcp_tools"), list):
        first_read_tools.extend(primary_focus["mcp_tools"])
    first_read_tools.extend(
        item.get("mcp_tool") or ""
        for item in inspection_hints[:8]
        if isinstance(item, dict)
    )
    first_read_tools.extend(["houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"])
    suggested_templates = []
    for item in workflow_suggestions[:4]:
        if not isinstance(item, dict):
            continue
        suggested_templates.append(
            {
                "template": item.get("template") or "",
                "priority": item.get("priority") or "",
                "input": item.get("input_path") or "",
                "mcp_tool": item.get("mcp_tool") or "houdini_template_plan",
                "required_flow": item.get("required_flow", []) if isinstance(item.get("required_flow"), list) else [],
                "local_generation_only": bool(item.get("local_generation_only")),
            }
        )
    route_state = "needs_user_selection" if any(item.get("kind") == "no_selection" for item in risk_notes if isinstance(item, dict)) else "ready_for_read_only_inspection"
    if primary_risk.get("domain"):
        route_state = "risk_domain_detected"
    if not focus_candidates and not risk_domains:
        route_state = "low_information"
    next_actions = []
    if primary_risk.get("domain"):
        next_actions.append("Inspect primary risk domain `%s` with read-only tools before drafting edits." % primary_risk.get("domain"))
    if primary_focus.get("path"):
        next_actions.append("Inspect focus node `%s` before planning changes." % primary_focus.get("path"))
    if suggested_templates:
        next_actions.append("Use template suggestions only as local drafts, then review, validate, run, and verify.")
    if not next_actions:
        next_actions.append("Gather scene context with read-only tools before planning any edits.")
    return {
        "version": 1,
        "state": route_state,
        "network_path": network_path or "",
        "inferred_purpose": inferred_purpose,
        "confidence": confidence,
        "primary_risk_domain": primary_risk.get("domain") or "none",
        "primary_risk_priority": primary_risk.get("priority") or "none",
        "primary_focus_path": primary_focus.get("path") or "",
        "first_read_tools": unique_strings(first_read_tools),
        "read_targets": read_targets,
        "suggested_templates": suggested_templates,
        "required_write_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "requires_user_approval_for_writes": True,
        "next_actions": next_actions,
        "note": "Scene understanding is read-only routing guidance; it never grants edit permission or success claims.",
    }


def _selected_focus(selected_paths: set[Any], traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    focus = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        path = trace.get("path")
        if path not in selected_paths:
            continue
        upstream_data = trace.get("upstream") if isinstance(trace.get("upstream"), dict) else {}
        downstream_data = trace.get("downstream") if isinstance(trace.get("downstream"), dict) else {}
        focus.append(
            {
                "path": path,
                "upstream_node_count": upstream_data.get("node_count", 0),
                "downstream_node_count": downstream_data.get("node_count", 0),
                "upstream_wire_count": len(upstream_data.get("wires", []) or []),
                "downstream_wire_count": len(downstream_data.get("wires", []) or []),
            }
        )
    return focus


def _focus_candidates(
    nodes: list[dict[str, Any]],
    selected_paths: set[Any],
    key_outputs: list[dict[str, Any]],
    cache_nodes: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
    risk_notes: list[dict[str, Any]],
    display_nodes: set[str],
    render_flag_nodes: set[str],
    incoming: dict[str, int],
    outgoing: dict[str, int],
) -> list[dict[str, Any]]:
    node_by_path = {
        str(node.get("path") or ""): node
        for node in nodes
        if isinstance(node, dict) and node.get("path")
    }
    candidates: dict[str, dict[str, Any]] = {}

    def add(path: str, kind: str, reason: str, score: int) -> None:
        if not path:
            return
        node = node_by_path.get(path, {"path": path})
        item = candidates.setdefault(
            path,
            {
                "path": path,
                "name": node.get("name", ""),
                "type": node.get("type", ""),
                "category": node.get("category", ""),
                "_score": 0,
                "kinds": [],
                "reasons": [],
            },
        )
        item["_score"] += score
        if kind not in item["kinds"]:
            item["kinds"].append(kind)
        if reason not in item["reasons"]:
            item["reasons"].append(reason)

    for path in sorted(path for path in selected_paths if isinstance(path, str)):
        add(path, "selected", "currently_selected", 40)
    for item in key_outputs:
        path = str(item.get("path") or "")
        add(path, "output", "inferred_key_output", 35)
        for reason in item.get("reasons", []) or []:
            add(path, "output", str(reason), 5)
    for item in simulation_nodes:
        add(str(item.get("path") or ""), "simulation", "simulation_or_solver_node", 30)
    for item in volume_nodes:
        add(str(item.get("path") or ""), "volume", "volume_or_vdb_node", 26)
    for item in cache_nodes:
        add(str(item.get("path") or ""), "cache", "cache_or_export_node", 28)
    for item in render_nodes:
        add(str(item.get("path") or ""), "render", "render_or_solaris_node", 28)
    for note in risk_notes:
        if not isinstance(note, dict):
            continue
        path = str(note.get("path") or "")
        if note.get("kind") == "node_error":
            add(path, "risk", "has_error", 45)
        elif note.get("kind") == "node_warning":
            add(path, "risk", "has_warning", 32)
    for path in display_nodes:
        add(path, "flagged", "display_flag", 14)
    for path in render_flag_nodes:
        add(path, "flagged", "render_flag", 14)
    for path, count in outgoing.items():
        if count > 1:
            add(path, "branch", "branches_to_multiple_outputs", 12)
    for path, count in incoming.items():
        if count > 0 and outgoing.get(path, 0) == 0:
            add(path, "terminal", "terminal_node", 12)

    ordered = sorted(candidates.values(), key=lambda item: (-int(item.get("_score", 0)), item.get("path", "")))
    result = []
    for item in ordered[:12]:
        score = int(item.pop("_score", 0))
        item["priority"] = "high" if score >= 40 else ("medium" if score >= 24 else "low")
        item["kinds"] = sorted(item["kinds"])
        item["reasons"] = sorted(item["reasons"])
        item["suggested_tools"] = _focus_candidate_tools(item["kinds"])
        item["mcp_tools"] = ["houdini_%s" % command for command in item["suggested_tools"]]
        result.append(item)
    return result


def _focus_candidate_tools(kinds: list[str]) -> list[str]:
    tools = ["node_info", "node_parms"]
    if any(kind in kinds for kind in ("selected", "output", "cache", "simulation", "volume", "render", "risk")):
        tools.append("upstream")
    if any(kind in kinds for kind in ("selected", "branch", "risk")):
        tools.append("downstream")
    return tools


def _inspection_hints(
    network_path: str,
    selected_focus: list[dict[str, Any]],
    key_outputs: list[dict[str, Any]],
    cache_nodes: list[dict[str, Any]],
    simulation_nodes: list[dict[str, Any]],
    volume_nodes: list[dict[str, Any]],
    render_nodes: list[dict[str, Any]],
    risk_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for item in selected_focus[:3]:
        path = item.get("path", "")
        if path:
            _add_inspection_hint(hints, "node_info", {"path": path}, "Inspect selected node metadata.", "high")
            _add_inspection_hint(hints, "node_parms", {"path": path}, "Inspect selected node parameters.", "high")
            _add_inspection_hint(hints, "upstream", {"path": path, "depth": 4}, "Trace selected node inputs.", "medium")
            _add_inspection_hint(hints, "downstream", {"path": path, "depth": 4}, "Trace selected node consumers.", "medium")
    for item in key_outputs[:3]:
        path = item.get("path", "")
        if path:
            _add_inspection_hint(hints, "node_info", {"path": path}, "Inspect inferred output node metadata.", "high")
            _add_inspection_hint(hints, "node_parms", {"path": path}, "Inspect inferred output node parameters and flags.", "medium")
    for kind, items, reason in (
        ("cache", cache_nodes, "Inspect cache/export path, frame range, and cook impact."),
        ("simulation", simulation_nodes, "Inspect solver settings, substeps, start frame, and cache strategy."),
        ("volume", volume_nodes, "Inspect VDB/volume voxel size, active bounds, and cook cost."),
        ("render", render_nodes, "Inspect render camera, output path, resolution, and renderer."),
    ):
        for item in items[:3]:
            path = item.get("path", "")
            if path:
                _add_inspection_hint(hints, "node_info", {"path": path}, reason, "high", kind=kind)
                _add_inspection_hint(hints, "node_parms", {"path": path}, reason, "high", kind=kind)
                _add_inspection_hint(hints, "upstream", {"path": path, "depth": 3}, "Trace dependencies for %s node." % kind, "medium", kind=kind)
    risk_kinds = {item.get("kind") for item in risk_notes if isinstance(item, dict)}
    if "no_selection" in risk_kinds and network_path:
        _add_inspection_hint(hints, "find_nodes", {"root": network_path, "type": "null", "limit": 50}, "Find likely OUT/null targets when nothing is selected.", "medium")
    if "no_key_output" in risk_kinds and network_path:
        _add_inspection_hint(hints, "network", {"path": network_path}, "Review network children and wiring because no key output was inferred.", "high")
    return hints[:24]


def _add_inspection_hint(
    hints: list[dict[str, Any]],
    command: str,
    payload: dict[str, Any],
    reason: str,
    priority: str,
    kind: str = "general",
) -> None:
    for hint in hints:
        if hint.get("command") == command and hint.get("payload") == payload:
            return
    hints.append(
        {
            "command": command,
            "mcp_tool": "houdini_%s" % command,
            "payload": payload,
            "priority": priority,
            "kind": kind,
            "reason": reason,
        }
    )


def _unique_by_path(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for item in items:
        path = item.get("path")
        if not path or path in seen:
            continue
        seen.add(path)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _selection_parent_path(selected_result: dict[str, Any]) -> str:
    for node in selected_result.get("nodes", []):
        parent = node.get("parent")
        if parent:
            return parent
    return ""


def _node_details(node: Any) -> dict[str, Any]:
    details = _node_summary(node)
    inputs = []
    for input_node in _safe_call(node.inputs, default=[]):
        inputs.append(_node_summary(input_node) if input_node else None)
    outputs = [_node_summary(output) for output in _safe_call(node.outputs, default=[])]
    children = [_node_summary(child) for child in _safe_call(node.children, default=[])]
    parms = [_parm_summary(parm) for parm in _safe_call(node.parms, default=[])]
    details.update(
        {
            "inputs": inputs,
            "outputs": outputs,
            "children": children,
            "parms": parms,
            "flags": {
                "display": _safe_node_method(node, "isDisplayFlagSet", default=False),
                "render": _safe_node_method(node, "isRenderFlagSet", default=False),
                "bypass": _safe_node_method(node, "isBypassed", default=False),
                "selected": _safe_node_method(node, "isSelected", default=False),
            },
            "comment": _safe_call(node.comment, default=""),
            "position": _jsonable(_safe_call(node.position, default=[])),
            "color": _jsonable(_safe_call(node.color, default=None)),
            "messages": {
                "errors": list(_safe_call(node.errors, default=[])),
                "warnings": list(_safe_call(node.warnings, default=[])),
            },
        }
    )
    return details


def _trace_graph(node: Any, direction: str, depth: int = 4) -> dict[str, Any]:
    root_path = _safe_call(node.path, default="")
    queue = [(node, 0)]
    seen = {root_path}
    nodes = []
    wires = []
    while queue:
        current, level = queue.pop(0)
        current_path = _safe_call(current.path, default="")
        nodes.append({**_node_summary(current), "depth": level})
        if level >= depth:
            continue
        if direction == "upstream":
            neighbors = [
                (input_node, input_index)
                for input_index, input_node in enumerate(_safe_call(current.inputs, default=[]))
                if input_node is not None
            ]
            for input_node, input_index in neighbors:
                input_path = _safe_call(input_node.path, default="")
                wires.append({"src": input_path, "dst": current_path, "input_index": input_index})
                if input_path not in seen:
                    seen.add(input_path)
                    queue.append((input_node, level + 1))
        else:
            for output_node in _safe_call(current.outputs, default=[]):
                output_path = _safe_call(output_node.path, default="")
                input_index = _input_index(output_node, current)
                wires.append({"src": current_path, "dst": output_path, "input_index": input_index})
                if output_path not in seen:
                    seen.add(output_path)
                    queue.append((output_node, level + 1))
    return {
        "root": root_path,
        "direction": direction,
        "depth": depth,
        "node_count": len(nodes),
        "nodes": nodes,
        "wires": wires,
    }


def _input_index(node: Any, input_node: Any) -> int | None:
    input_path = _safe_call(input_node.path, default="")
    for index, candidate in enumerate(_safe_call(node.inputs, default=[])):
        if candidate is not None and _safe_call(candidate.path, default="") == input_path:
            return index
    return None


def _walk_nodes(root: Any):
    stack = [root]
    while stack:
        node = stack.pop(0)
        yield node
        stack.extend(list(_safe_call(node.children, default=[])))


def _node_matches(summary: dict[str, Any], filters: dict[str, str]) -> bool:
    if not filters:
        return True
    for key, needle in filters.items():
        if needle not in str(summary.get(key, "")).lower():
            return False
    return True


def _network_boxes(node: Any) -> list[dict[str, Any]]:
    boxes = []
    for box in _safe_call(node.networkBoxes, default=[]):
        boxes.append(
            {
                "name": _safe_call(box.name, default=""),
                "nodes": [_safe_call(child.path, default="") for child in _safe_call(box.nodes, default=[])],
            }
        )
    return boxes


def _parm_summary(parm: Any) -> dict[str, Any]:
    template = _safe_call(parm.parmTemplate, default=None)
    return {
        "name": _safe_call(parm.name, default=""),
        "label": _safe_call(template.label, default="") if template else "",
        "type": _safe_call(lambda: template.type().name(), default="") if template else "",
        "disabled": _safe_call(parm.isDisabled, default=False),
    }


def _parm_details(parm: Any) -> dict[str, Any]:
    summary = _parm_summary(parm)
    expression = _safe_call(parm.expression, default=None)
    if expression == "":
        expression = None
    keyframes = _safe_call(parm.keyframes, default=[])
    summary.update(
        {
            "value": _jsonable(_safe_call(parm.eval, default=None)),
            "raw_value": _safe_call(parm.rawValue, default=None),
            "expression": expression,
            "locked": _safe_call(parm.isLocked, default=False),
            "has_keyframes": bool(keyframes),
            "keyframe_count": len(keyframes) if keyframes is not None else 0,
        }
    )
    return summary


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value)
        return value
    except Exception:
        try:
            return [_jsonable(item) for item in value]
        except Exception:
            pass
        return repr(value)


def _current_network_path(hou: Any) -> str:
    pane_type = getattr(getattr(hou, "paneTabType", None), "NetworkEditor", None)
    ui = getattr(hou, "ui", None)
    if ui is not None and pane_type is not None:
        pane = _safe_call(lambda: ui.paneTabOfType(pane_type), default=None)
        if pane is not None:
            pwd = _safe_call(pane.pwd, default=None)
            if pwd is not None:
                return _safe_call(pwd.path, default="")
    pwd = _safe_call(hou.pwd, default=None)
    if pwd is not None:
        return _safe_call(pwd.path, default="")
    return ""


def _safe_call(func: Any, default: Any = None) -> Any:
    try:
        return func()
    except Exception:
        return default


def _safe_node_method(node: Any, method_name: str, default: Any = None) -> Any:
    method = getattr(node, method_name, None)
    if method is None:
        return default
    return _safe_call(method, default=default)
