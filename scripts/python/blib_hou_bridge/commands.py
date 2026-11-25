"""Houdini command handlers for the local bridge."""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from . import dynamics_profiles, execution, history, protocol, python_executor, recipes, state, workflow_templates


class BridgeCommandError(RuntimeError):
    """Raised for command-level errors that should be returned to the CLI."""


_PLAN_UNDO_DEPTH = 0
_NODE_TYPE_CACHE_TTL_SECONDS = 300.0
_NODE_TYPE_CACHE: dict[str, Any] = {"timestamp": 0.0, "items": []}


def execute_in_houdini(command: str, payload: dict[str, Any] | None = None,
                       timings=None, before_execute=None) -> dict[str, Any]:
    """Dispatch scene access with separately measured queue/execution time."""
    queued_at = time.perf_counter()
    def perform():
        if before_execute is not None:
            before_execute()
        began = time.perf_counter()
        if timings is not None:
            timings["queue_ms"] = round((began - queued_at) * 1000, 3)
        try:
            return execute(command, payload)
        finally:
            if timings is not None:
                timings["execute_ms"] = round((time.perf_counter() - began) * 1000, 3)
    if command == "python_job_status":
        return perform()
    try:
        import hdefereval
    except ImportError:
        return perform()
    return hdefereval.executeInMainThreadWithResult(perform)


def execute(command: str, payload: dict[str, Any] | None = None, hou_module: Any | None = None) -> dict[str, Any]:
    command = protocol.normalize_command(command)
    payload = payload or {}
    protocol.validate_command(command, payload)
    if command == "python_job_status":
        return python_executor.status(payload["job_id"])
    if command == "health":
        return health()
    if command == "manifest":
        return manifest()
    if command == "recipe_manifest":
        return recipes.recipe_manifest()
    if command == "profile_manifest":
        return dynamics_profiles.manifest()
    if command == "rpc_log":
        return _project_response("rpc_log", rpc_log(payload), payload)
    if command == "scene_diff":
        return scene_diff(payload)

    hou = hou_module or _import_hou()
    if command in {"edit_batch", "inspect_batch", "look_batch"}:
        from . import workflows
        function = {"edit_batch": workflows.edit, "inspect_batch": workflows.inspect, "look_batch": workflows.look}[command]
        return function(hou, payload)
    if command in {"edit_batch", "inspect_batch"}:
        from . import workflows
        return workflows.edit(hou, payload) if command == "edit_batch" else workflows.inspect(hou, payload)
    if command == "context":
        return _project_response("context", context(hou), payload)
    if command == "diagnostic_bundle":
        return diagnostic_bundle(hou, payload)
    if command == "selected":
        return _project_response("selected", selected(hou), payload)
    if command == "scene_snapshot":
        return _project_response("scene_snapshot", scene_snapshot(hou, payload), payload)
    if command == "find_nodes":
        return _project_response("find_nodes", find_nodes(hou, payload), payload)
    if command == "node_info":
        return _project_response("node_info", node_info(hou, payload["path"], payload), payload)
    if command == "node_parms":
        return _project_response("node_parms", node_parms(hou, payload["path"], payload), payload)
    if command == "parameter_schema":
        return _project_response("parameter_schema", parameter_schema(hou, payload["path"], payload), payload)
    if command == "geometry_summary":
        return _project_response("geometry_summary", geometry_summary(hou, payload["path"], payload.get("include_health", False)), payload)
    if command == "get_houdini_help":
        return _project_response("get_houdini_help", get_houdini_help(hou, payload), payload)
    if command == "list_node_types":
        return _project_response("list_node_types", list_node_types(hou, payload), payload)
    if command == "material_info":
        return _project_response("material_info", material_info(hou, payload), payload)
    if command == "multi_read":
        return _project_response("multi_read", multi_read(hou, payload), payload)
    if command == "find_error_nodes":
        return _project_response("find_error_nodes", find_error_nodes(hou, payload), payload)
    if command == "list_panes":
        return list_panes(hou, payload)
    if command == "pane_screenshot":
        return pane_screenshot(hou, payload)
    if command == "capture_multiple_panes":
        return capture_multiple_panes(hou, payload)
    if command == "network_screenshot":
        return network_screenshot(hou, payload)
    if command == "render_info":
        return _project_response("render_info", render_info(hou, payload), payload)
    if command == "viewport_screenshot":
        return viewport_screenshot(hou, payload)
    if command == "network":
        return _project_response("network", network(hou, payload["path"]), payload)
    if command == "upstream":
        return _project_response("upstream", upstream(hou, payload["path"], depth=payload.get("depth", 4)), payload)
    if command == "downstream":
        return _project_response("downstream", downstream(hou, payload["path"], depth=payload.get("depth", 4)), payload)
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
    if command == "python_submit":
        if not state.python_enabled():
            raise BridgeCommandError("Switch out of read-only mode in the Houdini Bridge panel.")
        return python_executor.submit(payload["code"], payload["job_id"], hou, payload.get("inputs"))
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
    if command == "set_parm_expression":
        return set_parm_expression(hou, payload)
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
    if command == "set_frame":
        return set_frame(hou, payload)
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
        "python_enabled": state.python_enabled(),
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
        "context_token": _context_token(hou),
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


def _context_token(hou: Any) -> str:
    """Cheap identity check; never reads geometry or evaluates parameters."""
    state.ensure_scene_events(hou)
    def identity(node):
        return [node.path(), _safe_node_method(node, "sessionId")]
    value = [os.getpid(), state.scene_epoch(), hou.hipFile.path(),
             _current_network_path(hou),
             [identity(n) for n in hou.selectedNodes()]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def selected(hou: Any) -> dict[str, Any]:
    nodes = [_node_summary(node) for node in _safe_call(hou.selectedNodes, default=[])]
    return {
        "count": len(nodes),
        "nodes": nodes,
    }


def scene_snapshot(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "full")
    ctx = context(hou)
    selected_result = selected(hou)
    network_path = payload.get("path") or ctx.get("current_network") or _selection_parent_path(selected_result) or "/obj"
    trace_depth = int(payload.get("trace_depth", 1 if mode == "full" else 0))
    max_selected = int(payload.get("max_selected", 3 if mode == "full" else 0))

    network_result = network(hou, network_path)
    selected_details = []
    traces = []
    if mode == "full":
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
    if payload.get("include_viewport", False) and mode == "full":
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

    summary = {
        "mode": mode,
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
    }
    if mode == "summary":
        return {
            "context": ctx,
            "selected": selected_result,
            "network": {
                "path": network_result["path"],
                "node_count": network_result["node_count"],
                "wire_count": len(network_result.get("wires", [])),
                "message_count": len(network_result.get("messages", [])),
            },
            "semantics": {
                "inferred_purpose": semantics["inferred_purpose"],
                "primary_risk_domain": semantics.get("scene_understanding", {}).get("primary_risk_domain"),
                "risk_domains": semantics.get("risk_domains", []),
                "first_read_tools": semantics.get("scene_understanding", {}).get("first_read_tools", []),
            },
            "summary": summary,
        }

    return {
        "context": ctx,
        "selected": selected_result,
        "network": network_result,
        "selected_details": selected_details,
        "traces": traces,
        "viewport": viewport,
        "semantics": semantics,
        "summary": summary,
    }


def scene_diff(payload: dict[str, Any]) -> dict[str, Any]:
    before = payload.get("before", {})
    after = payload.get("after", {})
    include_parms = bool(payload.get("include_parms", False))
    max_changes = int(payload.get("max_changes", 200))
    before_nodes = _extract_diff_nodes(before, include_parms=include_parms)
    after_nodes = _extract_diff_nodes(after, include_parms=include_parms)
    before_paths = set(before_nodes)
    after_paths = set(after_nodes)
    added_paths = sorted(after_paths - before_paths)
    removed_paths = sorted(before_paths - after_paths)
    changed = []
    for path in sorted(before_paths & after_paths):
        node_changes = _node_diff(before_nodes[path], after_nodes[path], include_parms=include_parms)
        if node_changes:
            changed.append({"path": path, "changes": node_changes})
    before_wires = _extract_diff_wires(before)
    after_wires = _extract_diff_wires(after)
    added_wires = sorted(after_wires - before_wires)
    removed_wires = sorted(before_wires - after_wires)
    total_changes = len(added_paths) + len(removed_paths) + len(changed) + len(added_wires) + len(removed_wires)
    return {
        "summary": {
            "before_node_count": len(before_nodes),
            "after_node_count": len(after_nodes),
            "added_node_count": len(added_paths),
            "removed_node_count": len(removed_paths),
            "changed_node_count": len(changed),
            "added_wire_count": len(added_wires),
            "removed_wire_count": len(removed_wires),
            "total_change_count": total_changes,
        },
        "nodes": {
            "added": [after_nodes[path] for path in added_paths[:max_changes]],
            "removed": [before_nodes[path] for path in removed_paths[:max_changes]],
            "changed": changed[:max_changes],
        },
        "wires": {
            "added": [_wire_from_key(key) for key in added_wires[:max_changes]],
            "removed": [_wire_from_key(key) for key in removed_wires[:max_changes]],
        },
        "truncated": any(len(items) > max_changes for items in (added_paths, removed_paths, changed, added_wires, removed_wires)),
        "max_changes": max_changes,
        "include_parms": include_parms,
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
    result = {
        "root": _safe_call(root.path, default=root_path),
        "count": len(matches),
        "visited": visited,
        "limit": limit,
        "truncated": len(matches) >= limit,
        "filters": filters,
        "nodes": matches,
    }
    return _with_response_meta("find_nodes", result, item_count=len(matches), total=len(matches), truncated=result["truncated"])


def node_info(hou: Any, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    return _node_details(node, (payload or {}).get("fields"))


def node_parms(hou: Any, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    payload = payload or {}
    available = list(_safe_call(node.parms, default=[]))
    filtered = _filter_parms(available, payload)
    parms = [_parm_details(parm) for parm in filtered]
    return {
        "node": _node_summary(node),
        "parm_count": len(parms),
        "available_parm_count": len(available),
        "filters": _parm_filter_summary(payload),
        "parms": parms,
    }


def parameter_schema(hou: Any, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    payload = payload or {}
    available = list(_safe_call(node.parms, default=[]))
    filtered = _filter_parms(available, payload)
    parameters = [
        _parm_schema_details(
            parm,
            node,
            include_defaults=payload.get("include_defaults", True),
            include_menu=payload.get("include_menu", True),
            include_ranges=payload.get("include_ranges", True),
        )
        for parm in filtered
    ]
    return {
        "node": _node_summary(node),
        "parameter_count": len(parameters),
        "available_parameter_count": len(available),
        "filters": _parm_filter_summary(payload),
        "parameters": parameters,
    }


def geometry_summary(hou: Any, path: str, include_health: bool = False) -> dict[str, Any]:
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Node not found: %s" % path)
    geo = _safe_node_geometry(node)
    if geo is None:
        raise BridgeCommandError("Geometry is not available for node: %s" % path)
    result = {
        "node": _node_summary(node),
        "available": True,
        "counts": _geo_counts(geo),
        "bounds": _geo_bounds(geo),
        "point_attributes": [_attrib_details(attrib) for attrib in _safe_call(geo.pointAttribs, default=[])],
        "primitive_attributes": [_attrib_details(attrib) for attrib in _safe_call(geo.primAttribs, default=[])],
        "vertex_attributes": [_attrib_details(attrib) for attrib in _safe_call(geo.vertexAttribs, default=[])],
        "global_attributes": [_attrib_details(attrib) for attrib in _safe_call(geo.globalAttribs, default=[])],
        "point_groups": [_group_details(group) for group in _safe_call(geo.pointGroups, default=[])],
        "primitive_groups": [_group_details(group) for group in _safe_call(geo.primGroups, default=[])],
    }

    if include_health:
        result["health"] = _geometry_health(hou, geo)
    return result


def _geometry_health(hou: Any, geo: Any, max_primitives: int = 100000, max_edges: int = 300000) -> dict[str, Any]:
    """Inspect polygon edge incidence with bounded work; never treat a partial scan as complete."""
    edges = {}
    scanned = skipped = edge_count = 0
    complete = True
    reason = None
    try:
        iterator = geo.iterPrims()
        polygon_type = hou.primType.Polygon
        for prim in iterator:
            if scanned + skipped >= max_primitives:
                complete, reason = False, "primitive_limit"
                break
            if prim.type() != polygon_type:
                skipped += 1
                continue
            vertices = prim.vertices()
            count = len(vertices)
            closed = prim.isClosed()
            needed = count if closed and count > 1 else max(0, count - 1)
            if edge_count + needed > max_edges:
                complete, reason = False, "edge_limit"
                break
            points = [vertex.point().number() for vertex in vertices]
            for index in range(needed):
                edge = tuple(sorted((points[index], points[(index + 1) % count])))
                edges[edge] = edges.get(edge, 0) + 1
            edge_count += needed
            scanned += 1
    except Exception as exc:
        complete, reason = False, "inspection_error: %s" % exc
    attrs = {}
    for owner, method in (("point", "pointAttribs"), ("vertex", "vertexAttribs"), ("primitive", "primAttribs")):
        attrs[owner] = [str(_safe_call(a.name, default="")) for a in (_safe_call(getattr(geo, method, lambda: []), default=[]) or [])]
    return {
        "complete": complete,
        "reason": reason,
        "polygon_primitives_scanned": scanned,
        "non_polygon_primitives_skipped": skipped,
        "edge_occurrences_scanned": edge_count,
        "boundary_edges": sum(value == 1 for value in edges.values()),
        "non_manifold_edges": sum(value > 2 for value in edges.values()),
        "degenerate_edges": sum(a == b for a, b in edges),
        "normal_attribute_owners": [owner for owner, names in attrs.items() if "N" in names],
        "interpretation": "Counts cover scanned polygons only; open boundaries can be intentional. Normal presence does not establish normal direction or quality.",
    }


def get_houdini_help(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    help_type = str(payload.get("help_type") or "node").lower()
    item_name = str(payload.get("item_name") or "").strip()
    category = str(payload.get("category") or "").strip()
    limit = int(payload.get("limit", 20))
    offset = int(payload.get("offset", 0))
    docs = _houdini_docs_link(help_type, item_name)
    matches = []
    if help_type in {"node", "general"}:
        catalog = _node_type_catalog(hou, refresh=False)
        needle = item_name.lower()
        category_filter = category.lower()
        for item in catalog["items"]:
            haystack = " ".join([str(item.get("name", "")), str(item.get("description", "")), str(item.get("category", ""))]).lower()
            if needle and needle not in haystack:
                continue
            if category_filter and category_filter not in str(item.get("category", "")).lower():
                continue
            match = dict(item)
            match["docs_url"] = _houdini_docs_link("node", str(item.get("name") or ""), str(item.get("category") or "")).get("url")
            matches.append(match)
    page, pagination = _paginate(matches, limit, offset)
    result = {
        "help_type": help_type,
        "item_name": item_name,
        "category": category or None,
        "docs": docs,
        "matches": page,
        "pagination": pagination,
        "fetch": {
            "requested": bool(payload.get("fetch", False)),
            "attempted": False,
            "reason": "Bridge help is link-and-catalog only; it does not fetch web pages from Houdini.",
            "allowed_domain": "sidefx.com",
        },
    }
    return _with_response_meta("get_houdini_help", result, pagination=pagination, item_count=len(page), total=pagination["total"])


def list_node_types(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    catalog = _node_type_catalog(hou, refresh=payload.get("refresh", False))
    category_filter = str(payload.get("category") or "").lower()
    name_filter = str(payload.get("name") or "").lower()
    filtered = []
    for item in catalog["items"]:
        if category_filter and category_filter not in str(item.get("category", "")).lower():
            continue
        if name_filter and name_filter not in str(item.get("name", "")).lower() and name_filter not in str(item.get("description", "")).lower():
            continue
        filtered.append(item)
    limit = int(payload.get("limit", 100))
    offset = int(payload.get("offset", 0))
    page, pagination = _paginate(filtered, limit, offset)
    result = {
        "total": len(filtered),
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "has_more": pagination["has_more"],
        "pagination": pagination,
        "category": payload.get("category"),
        "name": payload.get("name"),
        "cache": {
            "created_at": catalog["timestamp"],
            "age_seconds": round(time.time() - float(catalog["timestamp"]), 3) if catalog["timestamp"] else None,
            "ttl_seconds": _NODE_TYPE_CACHE_TTL_SECONDS,
            "item_count": len(catalog["items"]),
            "refreshed": bool(catalog.get("refreshed")),
        },
        "node_types": page,
    }
    return _with_response_meta("list_node_types", result, pagination=pagination, item_count=len(page), total=len(filtered))


def material_info(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    path = payload.get("path")
    if path:
        node = hou.node(path)
        if node is None:
            raise BridgeCommandError("Material node not found: %s" % path)
        result = {
            "mode": "single",
            "material": _material_details(node),
        }
        return _with_response_meta("material_info", result, item_count=1, total=1)
    root_path = payload.get("root", "/mat")
    root = hou.node(root_path)
    if root is None:
        raise BridgeCommandError("Material root not found: %s" % root_path)
    limit = int(payload.get("limit", 100))
    materials = []
    visited = 0
    for node in _walk_nodes(root):
        visited += 1
        if node is root:
            continue
        if _looks_like_material_node(node):
            materials.append(_material_details(node, include_children=False))
            if len(materials) >= limit:
                break
    result = {
        "mode": "list",
        "root": _safe_call(root.path, default=root_path),
        "visited": visited,
        "count": len(materials),
        "limit": limit,
        "truncated": len(materials) >= limit,
        "materials": materials,
    }
    return _with_response_meta("material_info", result, item_count=len(materials), total=len(materials), truncated=result["truncated"])


def multi_read(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps", [])
    continue_on_error = bool(payload.get("continue_on_error", True))
    results = []
    success_count = 0
    error_count = 0
    stopped = False
    for index, step in enumerate(steps):
        execution.checkpoint()
        command = protocol.normalize_command(step.get("command"))
        step_payload = step.get("payload", {}) or {}
        try:
            result = execute(command, step_payload, hou_module=hou)
            item = {
                "index": index,
                "command": command,
                "ok": True,
                "result": result,
                "response_meta": _response_meta(command, result),
            }
            success_count += 1
        except Exception as exc:
            item = {
                "index": index,
                "command": command,
                "ok": False,
                "error": {
                    "code": "read_failed",
                    "message": str(exc),
                },
            }
            error_count += 1
            if not continue_on_error:
                stopped = True
                results.append(item)
                break
        results.append(item)
    response = {
        "ok": error_count == 0,
        "count": len(results),
        "requested_count": len(steps),
        "success_count": success_count,
        "error_count": error_count,
        "stopped": stopped,
        "continue_on_error": continue_on_error,
        "allowed_commands": sorted(protocol.MULTI_READ_ALLOWED_COMMANDS),
        "results": results,
    }
    return _with_response_meta("multi_read", response, item_count=len(results), total=len(steps), truncated=stopped)


def diagnostic_bundle(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    preset = str(payload.get("preset") or "scene_overview")
    steps = _diagnostic_steps(preset, payload)
    bundle = multi_read(hou, {"steps": steps, "continue_on_error": payload.get("continue_on_error", True)})
    target = {
        "path": payload.get("path"),
        "root": payload.get("root"),
    }
    response = {
        "preset": preset,
        "target": target,
        "steps": steps,
        "bundle": bundle,
        "response_meta": _response_meta("diagnostic_bundle", bundle, item_count=bundle.get("count"), total=len(steps), truncated=bundle.get("stopped", False)),
    }
    return _project_response("diagnostic_bundle", response, payload)


def find_error_nodes(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    root_path = payload["root"]
    root = hou.node(root_path)
    if root is None:
        raise BridgeCommandError("Root node not found: %s" % root_path)
    include_errors = payload.get("include_errors", True)
    include_warnings = payload.get("include_warnings", True)
    limit = int(payload.get("limit", 100))
    matches = []
    visited = 0
    error_count = 0
    warning_count = 0
    for node in _walk_nodes(root):
        visited += 1
        errors = [str(item) for item in _safe_call(node.errors, default=[])] if include_errors else []
        warnings = [str(item) for item in _safe_call(node.warnings, default=[])] if include_warnings else []
        if errors or warnings:
            error_count += len(errors)
            warning_count += len(warnings)
            matches.append({**_node_summary(node), "errors": errors, "warnings": warnings})
            if len(matches) >= limit:
                break
    result = {
        "root": _safe_call(root.path, default=root_path),
        "count": len(matches),
        "error_count": error_count,
        "warning_count": warning_count,
        "visited": visited,
        "limit": limit,
        "truncated": len(matches) >= limit,
        "nodes": matches,
    }
    return _with_response_meta("find_error_nodes", result, item_count=len(matches), total=len(matches), truncated=result["truncated"])


def viewport_screenshot(hou: Any, payload: dict[str, Any], *, _camera=None) -> dict[str, Any]:
    if payload.get("camera"):
        return _camera_screenshot(hou, payload)
    ui = getattr(hou, "ui", None)
    pane_type = getattr(getattr(hou, "paneTabType", None), "SceneViewer", None)
    if ui is None or pane_type is None:
        raise BridgeCommandError("Houdini UI Scene Viewer is not available.")
    viewer = _find_pane(hou, "SceneViewer")
    if viewer is None:
        raise BridgeCommandError("No Houdini Scene Viewer pane was found.")
    viewport = _safe_call(viewer.curViewport, default=None)
    if viewport is None:
        raise BridgeCommandError("No active Houdini viewport was found.")

    width = int(payload.get("width", 1280))
    height = int(payload.get("height", 720))
    prefix = payload.get("prefix", "viewport")
    path = _screenshot_path(prefix)

    frame = payload.get("frame", "current")
    view = payload.get("view", "current")
    timeline_frame = payload.get("timeline_frame")
    saved_frame = hou.frame() if timeline_frame is not None else None
    saved_camera = None
    saved_view_type = None
    if frame != "current" or view != "current" or payload.get("target"):
        if viewport.camera() is not None or viewport.isCameraLockedToView():
            raise BridgeCommandError("Changing the viewport is unavailable while viewing through or locked to a camera; use the current view.")
        saved_camera = viewport.defaultCamera().stash()
        if view != "current":
            saved_view_type = viewport.type()

    settings_func = getattr(viewport, "settings", None)
    settings = _safe_call(settings_func, default=None) if settings_func is not None else None
    saved_size = None
    if settings is not None and hasattr(settings, "resolution") and hasattr(settings, "setResolution"):
        saved_size = _safe_call(settings.resolution, default=None)
        _safe_call(lambda: settings.setResolution((width, height)), default=None)
    try:
        if timeline_frame is not None:
            hou.setFrame(timeline_frame)
        if view != "current":
            viewport.changeType(getattr(hou.geometryViewportType, {"front": "Front", "right": "Right", "top": "Top"}[view]))
        if frame == "selected":
            viewport.frameSelected()
        elif frame == "all":
            viewport.frameAll()
        if payload.get("target"):
            viewport.frameBoundingBox(_target_bounds(hou, payload["target"]))
        if _camera is not None:
            viewport.setCamera(_camera)
            active = viewport.camera()
            if active is None or active.path() != _camera.path():
                raise BridgeCommandError("The Scene Viewer did not adopt the requested camera.")
            viewport.lockCameraToView(True)
        if _camera is not None or frame != "current" or view != "current" or timeline_frame is not None or payload.get("target"):
            viewport.draw()
        if hasattr(viewport, "saveViewToImage"):
            viewport.saveViewToImage(path)
        elif hasattr(viewer, "saveViewToImage"):
            viewer.saveViewToImage(path)
        elif hasattr(viewer, "flipbook"):
            _capture_flipbook(hou, viewer, path, width, height)
        else:
            raise BridgeCommandError("This Houdini build does not expose a supported viewport screenshot API.")
    finally:
        try:
            try:
                if saved_view_type is not None:
                    viewport.changeType(saved_view_type)
            finally:
                if saved_camera is not None:
                    viewport.setDefaultCamera(saved_camera)
        finally:
            try:
                if saved_frame is not None:
                    hou.setFrame(saved_frame)
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
        "image_status": _viewport_image_status(path),
        "view": view,
        "timeline_frame": timeline_frame if timeline_frame is not None else _safe_call(lambda: hou.frame()),
        "target": payload.get("target"),
    }


def preview_sheet(captures, width, height):
    try:
        from PySide6 import QtCore, QtGui
    except ImportError:
        from PySide2 import QtCore, QtGui
    columns = min(3, len(captures))
    rows = (len(captures) + columns - 1) // columns
    caption_height = 26
    tile_height = height + caption_height
    sheet = QtGui.QImage(columns * width, rows * tile_height, QtGui.QImage.Format_RGB32)
    sheet.fill(QtGui.QColor("#222326"))
    painter = QtGui.QPainter(sheet)
    try:
        painter.setPen(QtGui.QColor("#eeeeee"))
        for index, capture in enumerate(captures):
            x, y = (index % columns) * width, (index // columns) * tile_height
            label = capture["label"]
            if capture["ok"]:
                image = QtGui.QImage(capture["result"]["path"])
                if image.isNull():
                    label += " · Image unavailable"
                else:
                    image = image.scaled(width, height, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    painter.drawImage(x + (width - image.width()) // 2, y + (height - image.height()) // 2, image)
            else:
                label += " · Capture failed"
            painter.drawText(QtCore.QRect(x + 8, y + height, width - 16, caption_height),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
    finally:
        painter.end()
    path = _screenshot_path("preview-sheet")
    if not sheet.save(path, "PNG"):
        raise BridgeCommandError("Could not save the preview sheet.")
    return {"path": path, "width": sheet.width(), "height": sheet.height(), "bytes": os.path.getsize(path)}


def _viewport_image_status(path):
    """Reject all-black previews as visual evidence; do not judge scene quality."""
    for package in ("PySide6", "PySide2"):
        try:
            gui = __import__(package + ".QtGui", fromlist=["QImage"])
            image = gui.QImage(path)
            if image.isNull():
                return "unreadable"
            image = image.convertToFormat(gui.QImage.Format_RGB32)
            data = image.constBits()
            if hasattr(data, "setsize"):
                data.setsize(image.sizeInBytes())
            raw = memoryview(data).cast("B")
            # RGB32 is B,G,R,A on the supported Windows host. Ignore alpha.
            return "nonblack" if any(raw[0::4]) or any(raw[1::4]) or any(raw[2::4]) else "all_black"
        except (ImportError, AttributeError, TypeError, ValueError):
            continue
    return "unchecked"


def list_panes(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ui = getattr(hou, "ui", None)
    if ui is None:
        raise BridgeCommandError("Houdini UI is not available.")
    requested_type = payload.get("type")
    current_desktop = _safe_call(lambda: ui.curDesktop(), default=None)
    current_desktop_name = _safe_call(current_desktop.name, default="") if current_desktop is not None else ""
    panes = []
    seen: set[tuple[str, str]] = set()

    desktops = list(_safe_call(lambda: ui.desktops(), default=[]) or [])
    for desktop in desktops:
        desktop_name = _safe_call(desktop.name, default="")
        for pane in list(_safe_call(desktop.paneTabs, default=[]) or []):
            # HOM may return short-lived Python wrappers whose id() is reused.
            key = (desktop_name, _safe_call(pane.name, default=""))
            if key in seen:
                continue
            seen.add(key)
            summary = _pane_summary(pane, desktop_name, current_desktop_name)
            if requested_type and summary.get("type") != requested_type:
                continue
            panes.append(summary)

    if not panes:
        for type_name in _available_pane_types(hou):
            if requested_type and type_name != requested_type:
                continue
            pane_type = _pane_type(hou, type_name)
            if pane_type is None:
                continue
            pane = _safe_call(lambda pt=pane_type: ui.paneTabOfType(pt), default=None)
            key = (current_desktop_name, _safe_call(lambda: pane.name(), default=""))
            if pane is None or key in seen:
                continue
            seen.add(key)
            panes.append(_pane_summary(pane, current_desktop_name, current_desktop_name, fallback_type=type_name))

    panes.sort(key=lambda item: (not item.get("capturable", False), item.get("type", ""), item.get("name", "")))
    return {
        "count": len(panes),
        "capturable_count": sum(1 for pane in panes if pane.get("capturable")),
        "current_desktop": current_desktop_name,
        "available_types": _available_pane_types(hou),
        "panes": panes,
    }


def pane_screenshot(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    pane_type_name = payload.get("type", "NetworkEditor")
    pane = _find_pane(hou, pane_type_name)
    if pane is None:
        raise BridgeCommandError("No Houdini %s pane was found." % pane_type_name)
    with _preserve_network_pane(pane, payload.get("restore", False) and payload.get("fit", False)):
        if payload.get("fit", False):
            _fit_pane(pane)
        path = _screenshot_path(payload.get("prefix", "pane"))
        if pane_type_name == "NetworkEditor":
            return _capture_network_pane(pane, pane.pwd(), path)
        source = _capture_pane_to_path(pane, path)
        return dict(_pane_capture_result(pane, path, pane_type_name), capture_source=source)


def capture_multiple_panes(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    types = payload.get("types") or ["NetworkEditor", "SceneViewer", "Parm"]
    prefix = payload.get("prefix", "panes")
    fit = bool(payload.get("fit", False))
    results = []
    success_count = 0
    for pane_type_name in types:
        try:
            result = pane_screenshot(
                hou,
                {
                    "type": pane_type_name,
                    "prefix": "%s_%s" % (prefix, pane_type_name),
                    "fit": fit,
                },
            )
            result["ok"] = True
            success_count += 1
        except BridgeCommandError as exc:
            result = {
                "ok": False,
                "pane_type": pane_type_name,
                "error": str(exc),
            }
        results.append(result)
    return {
        "count": len(results),
        "success_count": success_count,
        "all_ok": success_count == len(results),
        "results": results,
    }


def network_screenshot(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node_path = payload.get("path", "/obj")
    node = hou.node(node_path)
    if node is None:
        raise BridgeCommandError("Network not found: %s" % node_path)
    pane = _find_pane(hou, "NetworkEditor")
    if pane is None:
        raise BridgeCommandError("No Houdini NetworkEditor pane was found.")
    with _preserve_network_pane(pane, payload.get("restore", False)):
        _navigate_network_pane(pane, node, node_path)
        if pane.pwd().path() != node_path:
            raise BridgeCommandError("Network Editor did not enter the requested network: " + node_path)
        if payload.get("fit", True):
            _fit_pane(pane)
        if hasattr(pane, "redraw"):
            pane.redraw()
        path = _screenshot_path(payload.get("prefix", "network"))
        return _capture_network_pane(pane, node, path)


def _capture_network_pane(pane, node, path):
    try:
        source = _capture_pane_to_path(pane, path)
        captured = _pane_capture_result(pane, path, "NetworkEditor")
    except BridgeCommandError:
        captured = {}
    if captured.get("image_status") in {"all_black", "unreadable"} or not captured:
        shown = _draw_network_graph(node, path)
        captured = _pane_capture_result(pane, path, "NetworkEditor")
        captured["shown_nodes"] = shown
        source = "network_graph"
    return dict(captured, capture_source=source, node_path=node.path(), child_count=len(node.children()))


def _draw_network_graph(network_node, path):
    """Render a bounded diagram from live graph data when pane pixels are unavailable."""
    try:
        from PySide6 import QtCore, QtGui
    except ImportError:
        from PySide2 import QtCore, QtGui

    nodes = list(network_node.children())
    selected = sorted(
        nodes, key=lambda node: (
            not _safe_node_method(node, "isDisplayFlagSet", False),
            not _safe_node_method(node, "isSelected", False),
            not (_safe_node_method(node, "inputs", ()) or _safe_node_method(node, "outputs", ())),
        ),
    )[:40]
    nodes_by_path = {node.path(): node for node in selected}
    dependencies = {}
    for name, node in nodes_by_path.items():
        dependencies[name] = [item.path() for item in _safe_node_method(node, "inputs", ())
                              if item is not None and item.path() in nodes_by_path]
    successors = {name: [] for name in nodes_by_path}
    for name, inputs in dependencies.items():
        for source in inputs:
            successors[source].append(name)
    connected = [name for name in nodes_by_path if dependencies[name] or successors[name]]

    rank = {}
    remaining = set(connected)
    while remaining:
        ready = [name for name in connected if name in remaining
                 and all(parent not in remaining for parent in dependencies[name])]
        if not ready:
            ready = [name for name in connected if name in remaining]
        for name in ready:
            rank[name] = min(max((rank.get(parent, -1) + 1 for parent in dependencies[name]), default=0), 5)
        remaining.difference_update(ready)

    columns = max(4, max(rank.values(), default=0) + 1)
    cell_w, cell_h = 177, 72
    margin, header = 24, 68
    positions = {}
    column_rows = [0] * columns
    for name in connected:
        col = rank[name]
        positions[name] = (margin + col * cell_w, header + column_rows[col] * cell_h)
        column_rows[col] += 1
    isolated = [name for name in nodes_by_path if name not in positions]
    start = max(column_rows) + (1 if connected and isolated else 0)
    for index, name in enumerate(isolated):
        positions[name] = (margin + (index % columns) * cell_w, header + (start + index // columns) * cell_h)

    rows = max(max(column_rows), start + (len(isolated) + columns - 1) // columns, 1)
    width = margin * 2 + columns * cell_w
    height = header + rows * cell_h + margin
    image = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("#26282c"))
    painter = QtGui.QPainter(image)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setFont(QtGui.QFont("Sans Serif", 11))
        painter.setPen(QtGui.QColor("#f2f3f4"))
        painter.drawText(margin, 25, network_node.path())
        painter.setPen(QtGui.QColor("#b0b7c2"))
        painter.drawText(margin, 48, f"Network graph · {len(selected)} / {len(nodes)} nodes")
        painter.setPen(QtGui.QPen(QtGui.QColor("#7c9bbc"), 2))
        box_w, box_h = cell_w - 23, cell_h - 24
        for name, inputs in dependencies.items():
            x, y = positions[name]
            for source in inputs:
                a, b = positions[source]
                start_point = QtCore.QPointF(a + box_w, b + box_h / 2)
                end_point = QtCore.QPointF(x, y + box_h / 2)
                curve = QtGui.QPainterPath(start_point)
                curve.cubicTo(start_point.x() + 20, start_point.y(),
                              end_point.x() - 20, end_point.y(), end_point.x(), end_point.y())
                painter.drawPath(curve)
        for name, node in nodes_by_path.items():
            x, y = positions[name]
            border = "#78d6a2" if _safe_node_method(node, "isDisplayFlagSet", False) else "#596575"
            painter.setPen(QtGui.QPen(QtGui.QColor(border), 1))
            painter.setBrush(QtGui.QColor("#343a42"))
            painter.drawRoundedRect(x, y, box_w, box_h, 5, 5)
            painter.setPen(QtGui.QColor("#f5f6f7"))
            metrics = painter.fontMetrics()
            painter.drawText(x + 8, y + 19, metrics.elidedText(node.name(), QtCore.Qt.ElideRight, box_w - 16))
            painter.setPen(QtGui.QColor("#b0b7c2"))
            kind = node.type().name()
            painter.drawText(x + 8, y + 37, metrics.elidedText(kind, QtCore.Qt.ElideRight, box_w - 16))
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        raise BridgeCommandError("Could not save the network graph preview.")
    return len(selected)


@contextmanager
def _preserve_network_pane(pane, enabled):
    if not enabled:
        yield
        return
    # Fail before navigation if this build cannot restore the editor.
    previous = pane.pwd()
    bounds = pane.visibleBounds()
    try:
        yield
    finally:
        try:
            pane.setPwd(previous)
        finally:
            pane.setVisibleBounds(bounds, 0)


def _camera_screenshot(hou, payload):
    if payload.get("target") or payload.get("frame", "current") != "current" or payload.get("view", "current") != "current":
        raise BridgeCommandError("Camera capture cannot be combined with target framing or alternate view.")
    remaining = dict(payload)
    camera_path = remaining.pop("camera")
    requested_frame = remaining.pop("timeline_frame", None)
    with _camera_view(hou, camera_path) as (viewer, viewport, camera):
        if requested_frame is not None:
            hou.setFrame(requested_frame)
        return dict(viewport_screenshot(hou, remaining, _camera=camera), camera=camera.path())


@contextmanager
def _camera_view(hou, path):
    camera = hou.node(path)
    if camera is None or not hasattr(camera, "worldTransform"):
        raise BridgeCommandError("Camera object not found: " + path)
    viewer = _find_pane(hou, "SceneViewer")
    if viewer is None:
        raise BridgeCommandError("No Houdini Scene Viewer pane was found.")
    viewport = viewer.curViewport()
    previous = viewport.camera()
    locked = viewport.isCameraLockedToView()
    default = viewport.defaultCamera().stash()
    view_type = viewport.type()
    saved_frame = hou.frame()
    try:
        viewport.lockCameraToView(False)
        yield viewer, viewport, camera
    finally:
        try:
            viewport.lockCameraToView(False)
            if hou.frame() != saved_frame:
                hou.setFrame(saved_frame)
            viewport.setCamera(None)
            viewport.changeType(view_type)
            viewport.setDefaultCamera(default)
            if previous is not None:
                viewport.setCamera(previous)
        finally:
            viewport.lockCameraToView(locked)
            viewport.draw()


def capture_camera_series(hou, payload, frames):
    """Keep the camera bound to the viewer across one native flipbook operation."""
    path = payload["camera"]
    width, height = payload["width"], payload["height"]
    ordered = sorted(set(frames))
    interval = math.gcd(*(int(b - a) for a, b in zip(ordered, ordered[1:]))) or 1
    same_grid = all(int(frame) == frame for frame in ordered)
    sequence = same_grid and (ordered[-1] - ordered[0]) / interval < 24
    files = {}
    with _camera_view(hou, path) as (viewer, viewport, camera):
        hou.setFrame(ordered[0])
        viewport.setCamera(camera)
        current = viewport.camera()
        if current is None or current.path() != path:
            raise BridgeCommandError("The Scene Viewer did not adopt the requested camera.")
        viewport.lockCameraToView(True)
        viewport.draw()
        if sequence:
            pattern = _screenshot_path("camera-strip")[:-4] + "-$F.png"
            settings = _flipbook_options(viewer, pattern, width, height, (ordered[0], ordered[-1]))
            settings.frameIncrement(interval)
            viewer.flipbook(viewport, settings)
            files = {frame: pattern.replace("$F", str(int(frame))) for frame in ordered}
        else:
            for frame in ordered:
                execution.checkpoint()
                image_path = _screenshot_path("camera-frame")
                settings = _flipbook_options(viewer, image_path, width, height, (frame, frame))
                viewer.flipbook(viewport, settings)
                files[frame] = image_path

    results = []
    for frame in frames:
        image_path = files[frame]
        exists = os.path.isfile(image_path)
        status = _viewport_image_status(image_path) if exists else "unreadable"
        results.append({"path": image_path, "width": width, "height": height,
                        "bytes": os.path.getsize(image_path) if exists else 0,
                        "camera": path, "view": "camera", "image_status": status,
                        "timeline_frame": frame,
                        "summary": {"mode": "camera", "target": path, "view": "camera", "frame": frame}})
    return results


def _target_bounds(hou, path):
    node = hou.node(path)
    if node is None:
        raise BridgeCommandError("Target not found: %s" % path)
    if not hasattr(node, "geometry") and hasattr(node, "displayNode"):
        node = node.displayNode()
    if node is None or not hasattr(node, "geometry"):
        raise BridgeCommandError("Target must be a SOP or a geometry object.")
    geometry = node.geometry()  # Explicit target capture may cook this node.
    parent = node.parent()
    while parent is not None and not hasattr(parent, "worldTransform"):
        parent = parent.parent()
    transform = parent.worldTransform() if parent is not None else hou.hmath.identityTransform()
    return geometry.boundingBox(transform=transform)


def render_info(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    include_parms = bool(payload.get("include_parms", True))
    limit = int(payload.get("limit", 100))
    offset = int(payload.get("offset", 0))
    explicit_root = payload.get("root")
    root_paths = [explicit_root] if explicit_root else ["/out", "/stage", "/obj"]
    roots = []
    render_nodes = []
    warnings = []
    visited = 0
    for root_path in root_paths:
        if not root_path:
            continue
        root = hou.node(root_path)
        if root is None:
            warnings.append({"kind": "missing_root", "root": root_path})
            continue
        roots.append(_safe_call(root.path, default=root_path))
        for node in _walk_nodes(root):
            visited += 1
            if node is root:
                continue
            if not _looks_like_render_node(node):
                continue
            render_nodes.append(_render_node_details(node, include_parms=include_parms))
    page, pagination = _paginate(render_nodes, limit, offset)
    output_paths = []
    cameras = []
    renderers = []
    for item in render_nodes:
        settings = item.get("settings", {})
        output = settings.get("output_path")
        camera = settings.get("camera")
        renderer = settings.get("renderer")
        if output:
            output_paths.append(output)
        if camera:
            cameras.append(camera)
        if renderer:
            renderers.append(renderer)
    result = {
        "roots": roots,
        "visited": visited,
        "total": len(render_nodes),
        "count": len(page),
        "pagination": pagination,
        "nodes": page,
        "warnings": warnings,
        "summary": {
            "render_node_count": len(render_nodes),
            "output_paths": _unique_paths([str(path) for path in output_paths]),
            "cameras": _unique_paths([str(path) for path in cameras]),
            "renderers": _unique_paths([str(value) for value in renderers]),
            "missing_root_count": len([item for item in warnings if item.get("kind") == "missing_root"]),
            "renders_executed": False,
        },
    }
    return _with_response_meta("render_info", result, pagination=pagination, item_count=len(page), total=len(render_nodes))


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
    if payload.get("enabled") is False:
        state.set_read_only(True)
    elif payload.get("enabled") is True and not state.edit_enabled():
        raise execution.TaskRejected("read_only", "Switch out of read-only mode in the Houdini Bridge panel.")
    editable, python, _ = state.access_snapshot()
    return {"edit_enabled": editable, "python_enabled": python, "mode": "edit" if editable else "read"}


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
        step_checks = _verify_plan_step(hou, index, command, payload_step, report, step_result, final_deleted_paths)
        task = execution.current()
        if task is not None:
            observed = task.observations.get(index, {})
            for position, check in enumerate(step_checks):
                key = (check.get("path"), check.get("parm"))
                evidence = observed.get(key) if check.get("kind") == "parm_value" else None
                later = [i for i, items in task.observations.items()
                         if i > index and key in items and evidence is not None
                         and items[key]["node_id"] == evidence["node_id"]]
                if evidence is not None and later:
                    step_checks[position] = dict(evidence["check"], scope="intermediate_readback",
                                                 superseded_by=max(later))
        checks.extend(step_checks)
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


def _validate_parm_expression(expression: str) -> None:
    if "\n" in expression or "\r" in expression:
        raise BridgeCommandError("Parameter expressions must be single-line.")
    blocked = ("`", ";", "system(", "exec(", "open(", "save", "load", "python", "hou.")
    lowered = expression.lower()
    for token in blocked:
        if token in lowered:
            raise BridgeCommandError("Blocked unsafe expression token: %s" % token)


def set_parm_expression(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    node = _require_node(hou, payload["node"], "Node not found")
    parm_name = payload["parm"]
    parm = node.parm(parm_name)
    if parm is None:
        raise BridgeCommandError("Parameter not found: %s/%s" % (payload["node"], parm_name))
    expression = str(payload["expression"])
    _validate_parm_expression(expression)
    language_name = payload.get("language", "hscript")
    language = hou.exprLanguage.Hscript if language_name == "hscript" else hou.exprLanguage.Python

    def _action():
        parm.setExpression(expression, language=language)
        return {
            "touched": [_safe_call(node.path, default=payload["node"])],
            "parm": parm_name,
            "expression": expression,
            "language": language_name,
        }

    return _with_undo(hou, "Blib Bridge set parm expression", _action)


def set_frame(hou: Any, payload: dict[str, Any]) -> dict[str, Any]:
    frame = float(payload["frame"])

    def _action():
        hou.setFrame(frame)
        return {"frame": hou.frame(), "time": hou.time()}

    return _with_undo(hou, "Blib Bridge set frame", _action)


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


def run_plan(hou: Any, payload: dict[str, Any], _validation=None) -> dict[str, Any]:
    expected_context = payload.get("expected_context")
    if expected_context and expected_context != _context_token(hou):
        raise BridgeCommandError("Scene, network or selection changed. Read context again before editing; no steps executed.")
    steps = payload.get("steps", []) or []
    continue_on_error = bool(payload.get("continue_on_error", False))
    undo_label = payload.get("undo_label") or "Blib Bridge plan"
    validation = _validation if _validation is not None else validate_plan(hou, payload)
    if expected_context and not validation.get("ready_to_run"):
        raise BridgeCommandError("Guarded edit no longer validates; no steps executed. Inspect the current scene.")
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
                execution.checkpoint()
                step_started = time.time()
                try:
                    command = protocol.normalize_command(step.get("command"))
                    step_payload = step.get("payload", {}) or {}
                    if command == "run_plan":
                        raise BridgeCommandError("run_plan cannot contain nested run_plan steps.")
                    if command in protocol.EDIT_COMMANDS:
                        execution.start_effects()
                    result = execute(command, step_payload, hou_module=hou)
                    response = {
                        "ok": True,
                        "command": command,
                        "result": result,
                        "error": None,
                        "duration_ms": round((time.time() - step_started) * 1000, 3),
                    }
                    task = execution.current()
                    if task is not None and command in {"set_parm", "batch_set_parms"}:
                        observation = {}
                        for check in _verify_plan_step(hou, index, command, step_payload, {}, response, set()):
                            if check.get("kind") == "parm_value":
                                node = hou.node(check["path"])
                                observation[(check["path"], check["parm"])] = {
                                    "check": check, "node_id": _safe_node_method(node, "sessionId")}
                        task.observations[index] = observation
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
                execution.progress(len(results), len(steps), {"ran": len(results), "results": list(results)})
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
                _set_input(new_node, item["input_index"], item["src"], item.get("output_index", 0))
        if reconnect_outputs:
            for item in output_connections:
                _set_input(item["dst"], item["input_index"], new_node, item.get("output_index", 0))
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
    frame = hou.frame()
    viewer.flipbook(viewer.curViewport(), _flipbook_options(viewer, path, width, height, (frame, frame)))


def _flipbook_options(viewer, path, width, height, frame_range):
    settings = viewer.flipbookSettings().stash()
    settings.output(path)
    settings.outputToMPlay(False)
    settings.frameRange(frame_range)
    settings.frameIncrement(1)
    settings.renderAllViewports(False)
    settings.leaveFrameAtEnd(False)
    settings.useResolution(True)
    settings.resolution((width, height))
    settings.initializeSimulations(False)
    return settings


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


def _set_input(dst: Any, input_index: int, src: Any | None, output_index: int = 0) -> None:
    try:
        dst.setInput(input_index, src, output_index)
    except TypeError:
        if output_index:
            raise
        dst.setInput(input_index, src)


def _input_connections(node: Any) -> list[dict[str, Any]]:
    if hasattr(node, 'inputConnections') and all(hasattr(c, 'inputItemOutputIndex') for c in node.inputConnections()):
        return [{"src": c.inputItem(), "src_path": _node_path(c.inputItem(), ""),
                 "dst": node, "dst_path": _node_path(node, ""),
                 "input_index": c.inputIndex(), "output_index": c.inputItemOutputIndex()}
                for c in node.inputConnections()]
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
    if hasattr(node, 'outputConnections') and all(hasattr(c, 'inputItemOutputIndex') for c in node.outputConnections()):
        return [{"src": node, "src_path": _node_path(node, ""),
                 "dst": c.outputItem(), "dst_path": _node_path(c.outputItem(), ""),
                 "input_index": c.inputIndex(), "output_index": c.inputItemOutputIndex()}
                for c in node.outputConnections()]
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


def _node_type_catalog(hou: Any, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not refresh
        and _NODE_TYPE_CACHE["items"]
        and now - float(_NODE_TYPE_CACHE.get("timestamp", 0.0)) < _NODE_TYPE_CACHE_TTL_SECONDS
    ):
        return {"timestamp": _NODE_TYPE_CACHE["timestamp"], "items": list(_NODE_TYPE_CACHE["items"]), "refreshed": False}
    categories = _safe_call(hou.nodeTypeCategories, default={}) or {}
    items = []
    for category_name, category in sorted(categories.items(), key=lambda item: str(item[0]).lower()):
        node_types = _safe_call(category.nodeTypes, default={}) or {}
        for type_name, node_type in sorted(node_types.items(), key=lambda item: str(item[0]).lower()):
            description = _safe_call(node_type.description, default="")
            source_path = _safe_call(lambda nt=node_type: nt.sourcePath(), default="")
            items.append(
                {
                    "name": str(type_name),
                    "category": str(category_name),
                    "description": str(description or ""),
                    "source_path": str(source_path or ""),
                }
            )
    _NODE_TYPE_CACHE["timestamp"] = now
    _NODE_TYPE_CACHE["items"] = items
    return {"timestamp": now, "items": list(items), "refreshed": True}


def _looks_like_material_node(node: Any) -> bool:
    summary = _node_summary(node)
    text = " ".join([summary.get("path", ""), summary.get("name", ""), summary.get("type", ""), summary.get("category", "")]).lower()
    terms = (
        "material",
        "shader",
        "principled",
        "mtlx",
        "redshift",
        "arnold",
        "karma",
        "usdpreview",
    )
    return any(term in text for term in terms)


def _material_details(node: Any, include_children: bool = True) -> dict[str, Any]:
    parms = [_parm_details(parm) for parm in _safe_call(node.parms, default=[])]
    child_nodes = [_node_summary(child) for child in _safe_call(node.children, default=[])] if include_children else []
    texture_paths = _likely_texture_paths(parms)
    return {
        "node": _node_summary(node),
        "parm_count": len(parms),
        "parms": parms[:200],
        "texture_paths": texture_paths,
        "texture_count": len(texture_paths),
        "children": child_nodes[:200],
        "child_count": len(child_nodes),
    }


def _likely_texture_paths(parms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    texture_exts = (".exr", ".hdr", ".tx", ".rat", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".tga")
    results = []
    for parm in parms:
        raw = parm.get("raw_value")
        value = parm.get("value")
        text = raw if isinstance(raw, str) else value if isinstance(value, str) else ""
        lowered = text.lower()
        if not text:
            continue
        name = str(parm.get("name", "")).lower()
        if any(ext in lowered for ext in texture_exts) or "texture" in name or "map" in name or "file" in name:
            results.append({"parameter": parm.get("name", ""), "value": text})
    return results


def _looks_like_render_node(node: Any) -> bool:
    summary = _node_summary(node)
    text = " ".join([summary.get("path", ""), summary.get("name", ""), summary.get("type", ""), summary.get("category", "")]).lower()
    terms = (
        "rop",
        "render",
        "karma",
        "mantra",
        "redshift",
        "arnold",
        "renderman",
        "usd",
        "lop",
        "renderproduct",
        "rendersettings",
    )
    return any(term in text for term in terms)


def _render_node_details(node: Any, include_parms: bool = True) -> dict[str, Any]:
    parms = [_parm_details(parm) for parm in _safe_call(node.parms, default=[])]
    settings = _render_settings_from_parms(parms)
    details = {
        "node": _node_summary(node),
        "settings": settings,
        "warnings": [str(item) for item in _safe_call(node.warnings, default=[])],
        "errors": [str(item) for item in _safe_call(node.errors, default=[])],
    }
    if include_parms:
        details["parm_count"] = len(parms)
        details["parms"] = parms[:200]
        details["parms_truncated"] = len(parms) > 200
    return details


def _render_settings_from_parms(parms: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = {
        "camera": ("camera", "camerapath", "cam", "cameraprim"),
        "output_path": ("picture", "vm_picture", "sopoutput", "outputimage", "renderproduct", "productname", "lopoutput", "usdfile"),
        "renderer": ("renderer", "renderengine", "engine", "delegate"),
        "frame_range": ("f1", "f2", "f3", "trange"),
        "resolution_x": ("resx", "resolutionx", "xres"),
        "resolution_y": ("resy", "resolutiony", "yres"),
        "samples": ("samples", "primarysamples", "pixelsamples", "aa_samples", "karma_samples"),
    }
    settings: dict[str, Any] = {}
    values_by_name = {str(parm.get("name") or "").lower(): parm.get("value") for parm in parms}
    raw_by_name = {str(parm.get("name") or "").lower(): parm.get("raw_value") for parm in parms}
    for setting, names in candidates.items():
        if setting == "frame_range":
            frames = {}
            for name in names:
                if name in values_by_name:
                    frames[name] = values_by_name.get(name)
            if frames:
                settings[setting] = frames
            continue
        for name in names:
            if name in values_by_name:
                value = values_by_name.get(name) if setting in {"resolution_x", "resolution_y", "samples"} else raw_by_name.get(name)
                if value in (None, ""):
                    value = values_by_name.get(name)
                settings[setting] = value
                break
    if "resolution_x" in settings or "resolution_y" in settings:
        settings["resolution"] = [settings.get("resolution_x"), settings.get("resolution_y")]
    return settings


def _extract_diff_nodes(payload: dict[str, Any], include_parms: bool = False) -> dict[str, dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload.get("network"), dict):
        candidates.extend(payload["network"].get("nodes", []) or [])
    candidates.extend(payload.get("nodes", []) or [])
    if isinstance(payload.get("selected"), dict):
        candidates.extend(payload["selected"].get("nodes", []) or [])
    candidates.extend(payload.get("selected_details", []) or [])
    nodes: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        summary = item.get("node") if isinstance(item.get("node"), dict) else item
        path = summary.get("path") if isinstance(summary, dict) else None
        if not isinstance(path, str) or not path:
            continue
        record = {key: summary.get(key) for key in ("path", "name", "type", "category", "parent") if key in summary}
        if include_parms:
            parms = item.get("parms") or item.get("parameters")
            if isinstance(parms, list):
                record["parms"] = {
                    str(parm.get("name")): parm.get("value", parm.get("raw_value"))
                    for parm in parms
                    if isinstance(parm, dict) and parm.get("name")
                }
        nodes[path] = record
    return nodes


def _extract_diff_wires(payload: dict[str, Any]) -> set[str]:
    network_payload = payload.get("network") if isinstance(payload.get("network"), dict) else payload
    wires = network_payload.get("wires", []) if isinstance(network_payload, dict) else []
    keys = set()
    for wire in wires:
        if not isinstance(wire, dict):
            continue
        src = wire.get("src")
        dst = wire.get("dst")
        input_index = wire.get("input_index", 0)
        if isinstance(src, str) and isinstance(dst, str):
            keys.add("%s\t%s\t%s" % (src, dst, input_index))
    return keys


def _node_diff(before: dict[str, Any], after: dict[str, Any], include_parms: bool = False) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in ("name", "type", "category", "parent"):
        if before.get(key) != after.get(key):
            changes[key] = {"before": before.get(key), "after": after.get(key)}
    if include_parms:
        before_parms = before.get("parms") if isinstance(before.get("parms"), dict) else {}
        after_parms = after.get("parms") if isinstance(after.get("parms"), dict) else {}
        parm_changes = {}
        for name in sorted(set(before_parms) | set(after_parms)):
            if before_parms.get(name) != after_parms.get(name):
                parm_changes[name] = {"before": before_parms.get(name), "after": after_parms.get(name)}
        if parm_changes:
            changes["parms"] = parm_changes
    return changes


def _wire_from_key(key: str) -> dict[str, Any]:
    src, dst, input_index = (key.split("\t") + ["", "", "0"])[:3]
    try:
        input_index_value: Any = int(input_index)
    except ValueError:
        input_index_value = input_index
    return {"src": src, "dst": dst, "input_index": input_index_value}


def _paginate(items: list[Any], limit: int, offset: int) -> tuple[list[Any], dict[str, Any]]:
    total = len(items)
    page = items[offset : offset + limit]
    return page, {
        "total": total,
        "count": len(page),
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "truncated": offset + limit < total,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def _diagnostic_steps(preset: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    path = str(payload.get("path") or "")
    root = str(payload.get("root") or "")
    if preset == "scene_overview":
        target = root or path or "/obj"
        return [
            {"command": "context", "payload": {"fields": ["hip_file", "current_network", "frame", "edit_enabled"]}},
            {"command": "selected", "payload": {"fields": ["count", "nodes.path", "nodes.type"]}},
            {"command": "scene_snapshot", "payload": {"path": target, "mode": "routing", "fields": ["summary", "semantics.scene_understanding", "semantics.risk_domains", "network.path", "network.node_count"]}},
            {"command": "find_error_nodes", "payload": {"root": target, "limit": 25, "fields": ["count", "error_count", "warning_count", "nodes.path", "nodes.errors", "nodes.warnings", "response_meta"]}},
        ]
    if preset == "node_debug":
        target = path or root or "/obj"
        return [
            {"command": "node_info", "payload": {"path": target, "fields": ["path", "type", "category", "parent", "flags", "messages", "inputs.path", "outputs.path"]}},
            {"command": "node_parms", "payload": {"path": target, "fields": ["node.path", "parm_count", "parms.name", "parms.value", "parms.expression", "parms.locked"]}},
            {"command": "parameter_schema", "payload": {"path": target, "fields": ["node.path", "parameter_count", "parameters.name", "parameters.label", "parameters.type", "parameters.default_value", "parameters.menu_items"]}},
            {"command": "geometry_summary", "payload": {"path": target, "fields": ["node.path", "available", "counts", "bounds", "point_attributes.name", "primitive_attributes.name"]}},
        ]
    if preset == "render_readiness":
        target = root or path or "/out"
        return [
            {"command": "render_info", "payload": {"root": target, "limit": 25, "fields": ["roots", "total", "nodes.node.path", "nodes.settings", "nodes.errors", "nodes.warnings", "summary", "response_meta"]}},
            {"command": "find_error_nodes", "payload": {"root": target, "limit": 25, "fields": ["count", "error_count", "warning_count", "nodes.path", "nodes.errors", "nodes.warnings", "response_meta"]}},
            {"command": "list_node_types", "payload": {"name": "render", "limit": 25, "fields": ["total", "node_types.name", "node_types.category", "node_types.description", "response_meta"]}},
        ]
    if preset == "material_readiness":
        target = root or path or "/mat"
        return [
            {"command": "material_info", "payload": {"root": target, "limit": 25, "fields": ["mode", "root", "count", "materials.node.path", "materials.node.type", "materials.texture_count", "materials.texture_paths", "response_meta"]}},
            {"command": "find_error_nodes", "payload": {"root": target, "limit": 25, "fields": ["count", "error_count", "warning_count", "nodes.path", "nodes.errors", "nodes.warnings", "response_meta"]}},
            {"command": "list_node_types", "payload": {"name": "material", "limit": 25, "fields": ["total", "node_types.name", "node_types.category", "node_types.description", "response_meta"]}},
        ]
    raise BridgeCommandError("Unknown diagnostic preset: %s" % preset)


def _with_response_meta(
    command: str,
    result: dict[str, Any],
    pagination: dict[str, Any] | None = None,
    item_count: int | None = None,
    total: int | None = None,
    truncated: bool | None = None,
) -> dict[str, Any]:
    enriched = dict(result)
    pagination_meta = pagination
    if pagination_meta is None and isinstance(result.get("pagination"), dict):
        pagination_meta = result.get("pagination")
    enriched["response_meta"] = _response_meta(
        command,
        result,
        pagination=pagination_meta,
        item_count=item_count,
        total=total,
        truncated=truncated,
    )
    return enriched


def _response_meta(
    command: str,
    result: dict[str, Any],
    pagination: dict[str, Any] | None = None,
    item_count: int | None = None,
    total: int | None = None,
    truncated: bool | None = None,
) -> dict[str, Any]:
    pagination = pagination or {}
    if item_count is None:
        for key in ("count", "node_count", "parm_count", "success_count"):
            if isinstance(result.get(key), int):
                item_count = int(result[key])
                break
    if total is None:
        total = pagination.get("total") if isinstance(pagination.get("total"), int) else result.get("total")
    if truncated is None:
        truncated = bool(
            result.get("truncated", False)
            or result.get("has_more", False)
            or pagination.get("truncated", False)
            or pagination.get("has_more", False)
        )
    try:
        size_bytes = len(json.dumps(result, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        size_bytes = None
    return {
        "command": command,
        "schema_version": 1,
        "size_bytes": size_bytes,
        "item_count": item_count,
        "total": total,
        "truncated": bool(truncated),
        "has_more": bool(pagination.get("has_more", False)),
        "limit": pagination.get("limit", result.get("limit")),
        "offset": pagination.get("offset", result.get("offset")),
        "next_offset": pagination.get("next_offset"),
    }


_MISSING = object()


def _project_response(command: str, result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields")
    if not fields:
        return result
    original_size = _json_size_bytes(result)
    projected: dict[str, Any] = {}
    for field in fields:
        parts = str(field).split(".")
        value = _extract_projected_value(result, parts)
        if value is _MISSING:
            continue
        _merge_projected_value(projected, parts, value)
    meta = _response_meta(command, projected)
    meta["projected"] = True
    meta["requested_fields"] = list(fields)
    meta["original_size_bytes"] = original_size
    projected["response_meta"] = meta
    return projected


def _json_size_bytes(value: Any) -> int | None:
    try:
        return len(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return None


def _extract_projected_value(value: Any, parts: list[str]) -> Any:
    if not parts:
        return value
    head, tail = parts[0], parts[1:]
    if isinstance(value, dict):
        if head not in value:
            return _MISSING
        return _extract_projected_value(value[head], tail)
    if isinstance(value, list):
        items = []
        for item in value:
            extracted = _extract_projected_value(item, parts)
            if extracted is not _MISSING:
                items.append(extracted)
        return items
    return _MISSING


def _merge_projected_value(target: dict[str, Any], parts: list[str], value: Any) -> None:
    if not parts:
        return
    head, tail = parts[0], parts[1:]
    if not tail:
        target[head] = value
        return
    existing = target.get(head)
    if not isinstance(existing, dict):
        existing = {}
        target[head] = existing
    if isinstance(value, list):
        existing[tail[0]] = value
        return
    _merge_projected_value(existing, tail, value)


def _houdini_docs_link(help_type: str, item_name: str, category: str = "") -> dict[str, Any]:
    item = quote(str(item_name or "").strip().replace(" ", "_"))
    category_name = quote(str(category or "").strip().lower())
    base = "https://www.sidefx.com/docs/houdini"
    if help_type == "node" and item:
        url = "%s/nodes/%s/%s.html" % (base, category_name or "sop", item)
    elif help_type == "expression" and item:
        url = "%s/expressions/%s.html" % (base, item)
    elif help_type == "hscript" and item:
        url = "%s/commands/%s.html" % (base, item)
    elif help_type == "python" and item:
        url = "%s/hom/%s.html" % (base, item)
    else:
        url = "%s/" % base
    return {
        "url": url,
        "domain": "sidefx.com",
        "fetch_required": False,
        "source": "fixed_sidefx_docs_url",
    }


def _screenshot_path(prefix: str) -> str:
    from . import auth
    directory = str(auth.runtime_dir() / "screenshots")
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = hashlib.sha1(("%s-%s" % (prefix, time.time())).encode("utf-8")).hexdigest()[:8]
    return os.path.join(directory, "%s_%s_%s.png" % (prefix, stamp, suffix))


def _available_pane_types(hou: Any) -> list[str]:
    pane_tab_type = getattr(hou, "paneTabType", None)
    if pane_tab_type is None:
        return []
    return sorted(name for name in dir(pane_tab_type) if not name.startswith("_") and name != "thisown")


def _pane_type(hou: Any, type_name: str) -> Any | None:
    return getattr(getattr(hou, "paneTabType", None), type_name, None)


def _find_pane(hou: Any, type_name: str) -> Any | None:
    ui = getattr(hou, "ui", None)
    pane_type = _pane_type(hou, type_name)
    if ui is None or pane_type is None:
        return None
    desktop = _safe_call(lambda: ui.curDesktop(), default=None)
    if desktop is not None and hasattr(desktop, "paneTabs"):
        for pane in desktop.paneTabs():
            kind = _safe_call(lambda: pane.type(), default=None)
            name = _safe_call(kind.name, default="") if hasattr(kind, "name") else str(kind)
            if name != type_name:
                continue
            if not _safe_node_method(pane, "isCurrentTab", True):
                continue
            geometry = _pane_geometry(pane)
            if geometry and geometry["width"] > 0 and geometry["height"] > 0:
                return pane
        # Do not fall through to a hidden tab on a different desktop.
        return None
    return _safe_call(lambda: ui.paneTabOfType(pane_type), default=None)


def _pane_summary(pane: Any, desktop_name: str, current_desktop_name: str, fallback_type: str = "") -> dict[str, Any]:
    geometry = _pane_geometry(pane)
    pane_type = _safe_call(lambda: pane.type(), default=None)
    pane_type_name = _safe_call(pane_type.name, default="") if pane_type is not None and hasattr(pane_type, "name") else str(pane_type or fallback_type)
    if pane_type_name == "None":
        pane_type_name = fallback_type
    visible = bool(geometry and geometry.get("width", 0) > 0 and geometry.get("height", 0) > 0 and _safe_node_method(pane, "isCurrentTab", True))
    return {
        "name": _safe_call(pane.name, default=""),
        "type": pane_type_name,
        "desktop": desktop_name,
        "is_current_desktop": bool(desktop_name and desktop_name == current_desktop_name),
        "is_visible": visible,
        "geometry": geometry if visible else None,
        "capturable": visible and (not current_desktop_name or desktop_name == current_desktop_name),
    }


def _pane_geometry(pane: Any) -> dict[str, int] | None:
    geom = _safe_call(lambda: pane.qtScreenGeometry(), default=None)
    if geom is None:
        return None
    return {
        "x": int(_safe_call(geom.x, default=0)),
        "y": int(_safe_call(geom.y, default=0)),
        "width": int(_safe_call(geom.width, default=0)),
        "height": int(_safe_call(geom.height, default=0)),
    }


def _fit_pane(pane: Any) -> None:
    for method_name in ("homeAll", "homeToSelection", "homeSelected"):
        method = getattr(pane, method_name, None)
        if method is not None:
            _safe_call(method, default=None)
            return


def _navigate_network_pane(pane: Any, node: Any, node_path: str) -> None:
    if hasattr(pane, "setPwd"):
        pane.setPwd(node)
        return
    if hasattr(pane, "cd"):
        pane.cd(node_path)
        return
    raise BridgeCommandError("NetworkEditor pane does not expose a supported navigation API.")


def _capture_pane_to_path(pane: Any, path: str) -> str:
    for method_name in ("saveScreenshot", "saveViewToImage", "saveImage"):
        method = getattr(pane, method_name, None)
        if method is not None:
            method(path)
            if os.path.exists(path):
                return "pane." + method_name
    grab = getattr(pane, "grab", None)
    if grab is not None:
        pixmap = grab()
        save = getattr(pixmap, "save", None)
        if save is not None:
            save(path, "PNG")
            if os.path.exists(path):
                return "pane.grab"
    geometry = _pane_geometry(pane)
    if not geometry or geometry["width"] <= 0 or geometry["height"] <= 0:
        raise BridgeCommandError("Pane has invalid screen geometry and cannot be captured.")
    _capture_qt_pane_widget(pane, path, geometry)
    return "qt_houdini_widget"


def _capture_qt_pane_widget(pane, path, geometry):
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets
    hou = _import_hou()
    window = hou.qt.mainWindow()
    floating = _safe_node_method(pane, "floatingPanel")
    if floating is not None:
        float_window = getattr(hou.qt, "floatingPanelWindow", None)
        window = _safe_call(lambda: float_window(floating)) if float_window else None
        if window is None:
            origin = QtCore.QPoint(geometry["x"], geometry["y"])
            area = QtCore.QRect(origin, QtCore.QSize(geometry["width"], geometry["height"]))
            candidates = [widget for widget in QtWidgets.QApplication.topLevelWidgets()
                          if widget.isVisible() and widget.frameGeometry().contains(area)]
            window = min(candidates, key=lambda item: item.width() * item.height(), default=None)
    if window is None:
        raise BridgeCommandError("Could not resolve the pane to a Houdini window.")
    origin = window.mapFromGlobal(QtCore.QPoint(geometry["x"], geometry["y"]))
    area = QtCore.QRect(origin, QtCore.QSize(geometry["width"], geometry["height"]))
    if not window.rect().contains(area):
        raise BridgeCommandError("The requested pane does not fit inside its Houdini window.")
    pixmap = window.grab(area)
    if pixmap.isNull() or not pixmap.save(path, "PNG"):
        raise BridgeCommandError("Could not capture the Houdini pane widget.")


def _pane_capture_result(pane: Any, path: str, pane_type_name: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise BridgeCommandError("Pane screenshot did not create an image file.")
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "pane_type": pane_type_name,
        "pane_name": _safe_call(pane.name, default=""),
        "image_status": _viewport_image_status(path),
        "geometry": _pane_geometry(pane),
    }


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
    if command == "python_submit":
        issues.append("python_submit cannot run inside a command plan; submit it directly and poll its job status.")
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


def _node_details(node: Any, fields=None) -> dict[str, Any]:
    details = _node_summary(node)
    wanted = {str(f).split(".")[0] for f in fields} if fields else None
    readers = {
            "inputs": lambda: [_node_summary(n) if n else None for n in _safe_call(node.inputs, default=[])],
            "outputs": lambda: [_node_summary(n) for n in _safe_call(node.outputs, default=[])],
            "children": lambda: [_node_summary(n) for n in _safe_call(node.children, default=[])],
            "parms": lambda: [_parm_summary(p) for p in _safe_call(node.parms, default=[])],
            "flags": lambda: {
                "display": _safe_node_method(node, "isDisplayFlagSet", default=False),
                "render": _safe_node_method(node, "isRenderFlagSet", default=False),
                "bypass": _safe_node_method(node, "isBypassed", default=False),
                "selected": _safe_node_method(node, "isSelected", default=False),
            },
            "comment": lambda: _safe_call(node.comment, default=""),
            "position": lambda: _jsonable(_safe_call(node.position, default=[])),
            "color": lambda: _jsonable(_safe_call(node.color, default=None)),
            "messages": lambda: {
                "errors": list(_safe_call(node.errors, default=[])),
                "warnings": list(_safe_call(node.warnings, default=[])),
            },
    }
    for key, read in readers.items():
        if wanted is None or key in wanted:
            details[key] = read()
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


def _filter_parms(parms: list[Any], payload: dict[str, Any]) -> list[Any]:
    requested = payload.get("parm_names")
    requested_set = {str(name).lower() for name in requested} if isinstance(requested, list) else set()
    contains = str(payload.get("name_contains") or "").lower()
    limit = payload.get("limit")
    filtered = []
    for parm in parms:
        name = str(_safe_call(parm.name, default=""))
        label = str(_safe_call(lambda p=parm: p.parmTemplate().label(), default=""))
        haystack = "%s %s" % (name.lower(), label.lower())
        if requested_set and name.lower() not in requested_set:
            continue
        if contains and contains not in haystack:
            continue
        filtered.append(parm)
        if isinstance(limit, int) and len(filtered) >= limit:
            break
    return filtered


def _parm_filter_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "parm_names": list(payload.get("parm_names") or []),
        "name_contains": payload.get("name_contains"),
        "limit": payload.get("limit"),
        "include_defaults": payload.get("include_defaults", True),
        "include_menu": payload.get("include_menu", True),
        "include_ranges": payload.get("include_ranges", True),
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


def _parm_schema_details(
    parm: Any,
    node: Any,
    include_defaults: bool = True,
    include_menu: bool = True,
    include_ranges: bool = True,
) -> dict[str, Any]:
    summary = _parm_summary(parm)
    template = _safe_call(parm.parmTemplate, default=None)
    keyframes = _safe_call(parm.keyframes, default=[])
    expression = _safe_call(parm.expression, default=None)
    if expression == "":
        expression = None
    summary.update(
        {
            "node_type": _safe_call(lambda: node.type().name(), default=""),
            "value": _jsonable(_safe_call(parm.eval, default=None)),
            "raw_value": _safe_call(parm.rawValue, default=None),
            "expression": expression,
            "locked": _safe_call(parm.isLocked, default=False),
            "has_keyframes": bool(keyframes),
            "keyframe_count": len(keyframes) if keyframes is not None else 0,
        }
    )
    if include_defaults:
        summary["default_value"] = _template_default_value(template)
    if include_ranges:
        summary.update(_template_range_details(template))
    if include_menu:
        summary.update(_template_menu_details(template))
    return summary


def _template_default_value(template: Any) -> Any:
    if template is None:
        return None
    for method_name in ("defaultValue", "defaultExpression"):
        method = getattr(template, method_name, None)
        if method is None:
            continue
        value = _safe_call(method, default=None) if callable(method) else method
        if value is not None:
            return _jsonable(value)
    return None


def _template_range_details(template: Any) -> dict[str, Any]:
    if template is None:
        return {}
    details = {}
    for key, method_name in (
        ("min", "minValue"),
        ("max", "maxValue"),
        ("min_strict", "minIsStrict"),
        ("max_strict", "maxIsStrict"),
    ):
        method = getattr(template, method_name, None)
        if method is None:
            continue
        value = _safe_call(method, default=None) if callable(method) else method
        if value is not None:
            details[key] = _jsonable(value)
    return details


def _template_menu_details(template: Any) -> dict[str, Any]:
    if template is None:
        return {}
    items_func = getattr(template, "menuItems", None)
    labels_func = getattr(template, "menuLabels", None)
    items = _safe_call(items_func, default=None) if items_func is not None else None
    labels = _safe_call(labels_func, default=None) if labels_func is not None else None
    if items is None and labels is None:
        return {}
    return {
        "menu_items": _jsonable(items or []),
        "menu_labels": _jsonable(labels or []),
    }


def _safe_node_geometry(node: Any) -> Any:
    geo = _safe_call(node.geometry, default=None)
    if geo is not None:
        return geo
    display_node = _safe_call(getattr(node, "displayNode", lambda: None), default=None)
    if display_node is not None:
        return _safe_call(display_node.geometry, default=None)
    return None


def _geo_counts(geo: Any) -> dict[str, Any]:
    candidates = {
        "points": (("intrinsicValue", "pointcount"), ("points", None)),
        "primitives": (("intrinsicValue", "primitivecount"), ("prims", None)),
        "vertices": (("intrinsicValue", "vertexcount"), ("vertices", None)),
    }
    counts = {}
    for key, methods in candidates.items():
        value = None
        for method_name, argument in methods:
            method = getattr(geo, method_name, None)
            if method is None:
                continue
            if argument is None:
                result = _safe_call(method, default=None)
                value = len(result) if isinstance(result, (list, tuple)) else result
            else:
                value = _safe_call(lambda method=method, argument=argument: method(argument), default=None)
            if value is not None:
                break
        if value is not None:
            counts[key] = value
    return counts


def _geo_bounds(geo: Any) -> Any:
    box = _safe_call(getattr(geo, "boundingBox", lambda: None), default=None)
    if box is None:
        return None
    min_vec = _safe_call(getattr(box, "minvec", lambda: None), default=None)
    max_vec = _safe_call(getattr(box, "maxvec", lambda: None), default=None)
    if min_vec is None and hasattr(box, "min"):
        min_vec = _safe_call(box.min, default=None)
    if max_vec is None and hasattr(box, "max"):
        max_vec = _safe_call(box.max, default=None)
    if min_vec is None or max_vec is None:
        return None
    return [_jsonable(min_vec), _jsonable(max_vec)]


def _attrib_details(attrib: Any) -> dict[str, Any]:
    data_type = _safe_call(lambda: attrib.dataType().name(), default="")
    return {
        "name": _safe_call(attrib.name, default=""),
        "type": data_type,
        "size": _safe_call(attrib.size, default=None),
    }


def _group_details(group: Any) -> dict[str, Any]:
    entries = _safe_call(getattr(group, "entries", lambda: None), default=None)
    size = len(entries) if isinstance(entries, (list, tuple)) else _safe_call(getattr(group, "size", lambda: None), default=None)
    return {
        "name": _safe_call(group.name, default=""),
        "size": size,
    }


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
        pane = _find_pane(hou, "NetworkEditor")
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
