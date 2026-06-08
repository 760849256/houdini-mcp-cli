"""Bridge-native workflow recipe contracts and plan review helpers."""

from __future__ import annotations

from typing import Any


RECIPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "build_rbd_fracture_setup": {
        "required_inputs": ["input"],
        "creates": [
            "rbdmaterialfracture",
            "rbdconfigure",
            "rbdpack",
            "connectadjacentpieces",
            "rbdconstraintproperties",
            "rbdbulletsolver",
            "rbdunpack",
            "null",
        ],
        "output": "OUT_RBD",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "detail_size": {"type": "float", "min": 0.01, "max": 2.0},
            "constraint_strength": {"type": "float", "min": 0.0},
            "search_radius": {"type": "float", "min": 0.0},
            "include_solver": {"type": "bool"},
            "start_frame": {"type": "float"},
            "cache": {"type": "bool"},
        },
    },
    "build_vellum_grain_setup": {
        "required_inputs": ["input"],
        "creates": ["vellumconstraints_grain", "vellumsolver", "vellumpostprocess", "null"],
        "output": "OUT_GRAINS",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "particle_size": {"type": "float", "min": 0.001, "max": 1.0},
            "friction": {"type": "float", "min": 0.0},
            "include_solver": {"type": "bool"},
            "start_frame": {"type": "float"},
            "cache": {"type": "bool"},
        },
    },
    "build_vellum_cloth_setup": {
        "required_inputs": ["input"],
        "creates": ["vellumconstraints", "vellumsolver", "vellumpostprocess", "null"],
        "output": "OUT_CLOTH",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "bend_stiffness": {"type": "float", "min": 0.0},
            "stretch_stiffness": {"type": "float", "min": 0.0},
            "include_solver": {"type": "bool"},
            "start_frame": {"type": "float"},
            "cache": {"type": "bool"},
        },
    },
    "build_scatter_instance_setup": {
        "required_inputs": ["input", "instance"],
        "creates": ["scatter", "copytopoints", "pack", "null"],
        "output": "OUT_INSTANCES",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "count": {"type": "int", "min": 1, "max": 1000000},
            "seed": {"type": "int"},
        },
    },
    "build_sop_cleanup_setup": {
        "required_inputs": ["input"],
        "creates": ["clean", "fuse", "normal", "null"],
        "output": "OUT_CLEAN",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "fuse_distance": {"type": "float", "min": 0.0},
        },
    },
    "build_vdb_sdf_setup": {
        "required_inputs": ["input"],
        "creates": ["vdbfrompolygons", "vdbreshapesdf", "vdbsmoothsdf", "null"],
        "output": "OUT_VDB",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "voxel_size": {"type": "float", "min": 0.001, "max": 10.0},
            "offset": {"type": "float"},
        },
    },
    "build_modeling_output_setup": {
        "required_inputs": ["input"],
        "creates": ["polybevel::3.0", "normal", "matchsize", "null"],
        "output": "OUT_MODEL",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "bevel_distance": {"type": "float", "min": 0.0},
            "scale_to_unit": {"type": "bool"},
        },
    },
    "build_cache_output_setup": {
        "required_inputs": ["input"],
        "creates": ["filecache", "null"],
        "output": "OUT_CACHE",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "path": {"type": "string"},
        },
    },
    "build_fx_prep_project": {
        "required_inputs": ["input"],
        "creates": [
            "clean",
            "fuse",
            "normal",
            "polybevel::3.0",
            "matchsize",
            "vdbfrompolygons",
            "vdbreshapesdf",
            "vdbsmoothsdf",
            "filecache",
            "null",
        ],
        "output": "OUT_FX",
        "presets": ["preview", "production"],
        "parameters": {"preset": {"type": "menu", "values": ["preview", "production"]}, "cache": {"type": "bool"}},
    },
    "build_rbd_project": {
        "required_inputs": ["input"],
        "creates": [
            "clean",
            "fuse",
            "normal",
            "rbdmaterialfracture",
            "rbdconfigure",
            "rbdpack",
            "connectadjacentpieces",
            "rbdconstraintproperties",
            "rbdbulletsolver",
            "rbdunpack",
            "filecache",
            "null",
        ],
        "output": "OUT_RBD",
        "presets": ["preview", "production"],
        "parameters": {
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "include_solver": {"type": "bool"},
            "start_frame": {"type": "float"},
            "cache": {"type": "bool"},
        },
    },
    "build_vellum_project": {
        "required_inputs": ["input"],
        "creates": [
            "clean",
            "fuse",
            "normal",
            "vellumconstraints_grain",
            "vellumconstraints",
            "vellumsolver",
            "vellumpostprocess",
            "filecache",
            "null",
        ],
        "output": "OUT_VELLUM",
        "presets": ["preview", "production"],
        "parameters": {
            "kind": {"type": "menu", "values": ["grains", "cloth"]},
            "preset": {"type": "menu", "values": ["preview", "production"]},
            "include_solver": {"type": "bool"},
            "start_frame": {"type": "float"},
            "cache": {"type": "bool"},
        },
    },
}


RECIPE_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "preview": {
        "rbd": {"detail_size": 0.2, "constraint_strength": 1000, "search_radius": 0.1, "include_solver": True, "substeps": 1, "cache": False},
        "grains": {"particle_size": 0.05, "friction": 0.5, "include_solver": True, "substeps": 2, "cache": False},
        "cloth": {"bend_stiffness": 0.1, "stretch_stiffness": 1000, "include_solver": True, "substeps": 2, "cache": False},
        "scatter": {"count": 100, "seed": 7},
        "cleanup": {"fuse_distance": 0.001},
        "model": {"bevel_distance": 0.02, "scale_to_unit": False},
        "vdb": {"voxel_size": 0.05, "offset": 0.0},
    },
    "production": {
        "rbd": {"detail_size": 0.08, "constraint_strength": 5000, "search_radius": 0.05, "include_solver": True, "substeps": 4, "cache": True},
        "grains": {"particle_size": 0.025, "friction": 0.7, "include_solver": True, "substeps": 4, "cache": True},
        "cloth": {"bend_stiffness": 0.5, "stretch_stiffness": 5000, "include_solver": True, "substeps": 4, "cache": True},
        "scatter": {"count": 1000, "seed": 7},
        "cleanup": {"fuse_distance": 0.0001},
        "model": {"bevel_distance": 0.01, "scale_to_unit": False},
        "vdb": {"voxel_size": 0.02, "offset": 0.0},
    },
}


RECIPE_PRESET_ALIASES = {
    "draft": "preview",
    "fast": "preview",
    "quick": "preview",
    "low": "preview",
    "previz": "preview",
    "prod": "production",
    "final": "production",
    "high": "production",
}


def recipe_manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "contracts": RECIPE_CONTRACTS,
        "presets": RECIPE_PRESETS,
        "preset_aliases": RECIPE_PRESET_ALIASES,
        "note": "These contracts are bridge-native references; they do not execute Agent actions.",
    }


def review_plan(steps: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, Any]:
    impact = _impact_from_validation(validation)
    blockers = _blockers_from_validation(validation)
    warnings = _review_warnings(steps, validation, impact)
    confirmations = _required_confirmations(steps)
    suggestions = _suggestions(steps, impact)
    recipe_hints = _recipe_hints(steps)
    rollback_hints = _rollback_hints(validation)
    risk_notes = _risk_notes(steps, validation)

    if blockers:
        level = "blocked"
    elif warnings or confirmations or risk_notes:
        level = "warning"
    else:
        level = "ok"

    confidence = 0.88
    confidence -= min(0.35, 0.08 * len(blockers))
    confidence -= min(0.20, 0.04 * len(warnings))
    confidence -= min(0.12, 0.03 * len(confirmations))
    confidence -= min(0.10, 0.02 * len(risk_notes))
    confidence = max(0.0, min(0.98, confidence))

    return {
        "level": level,
        "confidence": round(confidence, 3),
        "blockers": blockers,
        "warnings": warnings,
        "suggestions": suggestions,
        "required_confirmations": confirmations,
        "rollback_hints": rollback_hints,
        "risk_notes": risk_notes,
        "impact": impact,
        "recipe_hints": recipe_hints,
        "validation": {
            "valid": validation.get("valid", False),
            "ready_to_run": validation.get("ready_to_run", False),
            "would_require_edit": validation.get("would_require_edit", False),
            "blocked_by_edit_mode": validation.get("blocked_by_edit_mode", False),
            "step_count": validation.get("step_count", 0),
            "steps_sha256": validation.get("steps_sha256", ""),
        },
    }


def _impact_from_validation(validation: dict[str, Any]) -> dict[str, list[str]]:
    created: list[str] = []
    touched: list[str] = []
    deleted: list[str] = []
    parms: list[str] = []
    for report in validation.get("steps", []) or []:
        created.extend([item for item in report.get("creates", []) or [] if isinstance(item, str)])
        touched.extend([item for item in report.get("touches", []) or [] if isinstance(item, str)])
        deleted.extend([item for item in report.get("deletes", []) or [] if isinstance(item, str)])
        if report.get("command") in {"set_parm", "set_parm_any", "batch_set_parms", "apply_parm_profile"}:
            payload = report.get("payload", {}) or {}
            if payload.get("node") and payload.get("parm"):
                parms.append("%s/%s" % (payload["node"], payload["parm"]))
            elif payload.get("node") and payload.get("parms"):
                parms.append("%s/%s" % (payload["node"], "|".join(payload["parms"])))
            elif payload.get("node") and payload.get("values"):
                parms.append("%s/%s" % (payload["node"], "|".join(payload["values"].keys())))
            elif payload.get("node") and payload.get("profile"):
                parms.append("%s/<%s>" % (payload["node"], payload["profile"]))
    return {
        "created": _unique_nonempty(created),
        "touched": _unique_nonempty(touched),
        "deleted": _unique_nonempty(deleted),
        "parms": _unique_nonempty(parms),
    }


def _blockers_from_validation(validation: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for report in validation.get("steps", []) or []:
        for issue in report.get("issues", []) or []:
            blockers.append("Step %s %s: %s" % (report.get("index"), report.get("command"), issue))
    if validation.get("blocked_by_edit_mode"):
        blockers.append("Plan contains edit commands while bridge edit mode is off.")
    return _unique_nonempty(blockers)


def _review_warnings(steps: list[dict[str, Any]], validation: dict[str, Any], impact: dict[str, list[str]]) -> list[str]:
    warnings: list[str] = []
    commands = [_command_name(step) for step in steps]
    edit_count = sum(1 for name in commands if name not in {"health", "manifest", "context", "selected", "scene_snapshot", "find_nodes", "node_info", "node_parms", "profile_manifest", "probe_parm_profile", "rpc_log", "network", "upstream", "downstream", "edit_mode", "validate_plan", "recipe_manifest", "review_plan"})
    if edit_count >= 3 and "layout" not in commands:
        warnings.append("Plan creates or edits multiple nodes but has no layout step.")
    if commands.count("connect") >= 3:
        warnings.append("Plan contains several wiring changes; verify node flow before running.")
    if commands.count("set_parm") + commands.count("set_parm_any") + commands.count("batch_set_parms") + commands.count("apply_parm_profile") >= 4:
        warnings.append("Plan changes many parameters; inspect values carefully before running.")
    if any(_step_risk_kind(step) == "cache" for step in steps):
        warnings.append("Plan touches cache/export setup; confirm paths, frame range, and disk impact before running.")
    if any(_step_risk_kind(step) == "simulation" for step in steps):
        warnings.append("Plan touches simulation/solver setup; verify substeps, start frame, and cache strategy before running.")
    if any(_step_risk_kind(step) == "render" for step in steps):
        warnings.append("Plan touches render/Solaris setup; verify camera, render path, resolution, and renderer before running.")
    if impact.get("deleted"):
        warnings.append("Plan deletes nodes; verify cleanup targets before running.")
    if len(impact.get("created", [])) and not any(str(path).rsplit("/", 1)[-1].upper().startswith("OUT") for path in impact.get("created", [])):
        warnings.append("Plan creates nodes but no obvious OUT null.")
    for report in validation.get("steps", []) or []:
        warnings.extend(report.get("warnings", []) or [])
    return _unique_nonempty(warnings)


def _required_confirmations(steps: list[dict[str, Any]]) -> list[str]:
    confirmations: list[str] = []
    for index, step in enumerate(steps, 1):
        command = _command_name(step)
        payload = step.get("payload", {}) if isinstance(step, dict) else {}
        if command in {"create_node", "create_network_box", "create_sticky_note"} and payload.get("parent") in {"", None}:
            confirmations.append("Step %s creates under an unspecified parent." % index)
        if command in {"set_parm", "set_parm_any", "batch_set_parms", "apply_parm_profile"}:
            parm_names = [str(payload.get("parm", ""))]
            if command == "set_parm_any":
                parm_names = [str(item) for item in payload.get("parms", []) or []]
            if command == "batch_set_parms":
                parm_names = [str(item) for item in (payload.get("values", {}) or {}).keys()]
            if command == "apply_parm_profile":
                parm_names = [str(payload.get("profile", ""))]
            if any(name in {"file", "path", "soppath", "output", "output_name", "sopoutput"} for name in parm_names) or payload.get("value") in {"", None}:
                confirmations.append("Step %s set_parm touches an output/path-like value." % index)
        if command == "create_node" and "filecache" in str(payload.get("type", "")).lower():
            confirmations.append("Step %s creates a cache node; confirm cache path and frame range." % index)
        if command == "create_node" and _node_type_risk(str(payload.get("type", ""))) == "simulation":
            confirmations.append("Step %s creates a simulation/solver node; confirm solver settings and expected cook cost." % index)
        if command == "create_node" and _node_type_risk(str(payload.get("type", ""))) == "render":
            confirmations.append("Step %s creates a render/Solaris node; confirm this only prepares rendering and does not execute it." % index)
        if command == "delete_node":
            confirmations.append("Step %s deletes `%s`; confirm it is disposable cleanup." % (index, payload.get("node", "")))
        if command == "replace_node" and payload.get("delete_old", False):
            confirmations.append("Step %s replaces and deletes `%s`; confirm input/output reconnection." % (index, payload.get("node", "")))
    return _unique_nonempty(confirmations)


def _suggestions(steps: list[dict[str, Any]], impact: dict[str, list[str]]) -> list[str]:
    commands = [_command_name(step) for step in steps]
    suggestions: list[str] = []
    if impact.get("created") and "layout" not in commands:
        suggestions.append("Add a layout step for the edited network before running.")
    if len(steps) > 0 and commands[-1] not in {"select", "scene_snapshot", "rpc_log"}:
        suggestions.append("End with select, scene_snapshot, or rpc_log so the result is easy to inspect.")
    if any(_step_risk_kind(step) == "render" for step in steps):
        suggestions.append("After render/Solaris setup, verify render nodes with scene_snapshot or network before rendering manually.")
    if any(_step_risk_kind(step) == "cache" for step in steps):
        suggestions.append("For cache/export setup, capture full evidence or inspect filecache parameters before cooking.")
    return _unique_nonempty(suggestions)


def _risk_notes(steps: list[dict[str, Any]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    validation_reports = {
        report.get("index"): report
        for report in validation.get("steps", []) or []
        if isinstance(report, dict)
    }
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        command = _command_name(step)
        payload = step.get("payload", {}) if isinstance(step.get("payload"), dict) else {}
        kind = _step_risk_kind(step)
        if not kind:
            continue
        target = payload.get("node") or payload.get("dst") or payload.get("parent") or ""
        report = validation_reports.get(index, {})
        touched = report.get("touches", []) if isinstance(report.get("touches"), list) else []
        creates = report.get("creates", []) if isinstance(report.get("creates"), list) else []
        if kind == "cache":
            notes.append(
                {
                    "index": index,
                    "kind": "cache",
                    "severity": "medium",
                    "command": command,
                    "path": target,
                    "message": "Cache/export setup can write large files or stale frame ranges when cooked later.",
                    "verify": "Inspect cache path, frame range, and filecache parameters before cooking.",
                    "touched": _unique_nonempty([item for item in touched + creates if isinstance(item, str)]),
                }
            )
        elif kind == "simulation":
            notes.append(
                {
                    "index": index,
                    "kind": "simulation",
                    "severity": "medium",
                    "command": command,
                    "path": target,
                    "message": "Simulation/solver setup can increase cook cost and depends on start frame, substeps, and cache policy.",
                    "verify": "Check solver parameters, timeline assumptions, and cache strategy before running heavy cooks.",
                    "touched": _unique_nonempty([item for item in touched + creates if isinstance(item, str)]),
                }
            )
        elif kind == "render":
            notes.append(
                {
                    "index": index,
                    "kind": "render",
                    "severity": "medium",
                    "command": command,
                    "path": target,
                    "message": "Render/Solaris setup prepares render output; verify camera, render path, resolution, and renderer before manual render.",
                    "verify": "Use network or scene_snapshot to inspect LOP/render nodes before executing any render.",
                    "touched": _unique_nonempty([item for item in touched + creates if isinstance(item, str)]),
                }
            )
    return _unique_risk_notes(notes)


def _rollback_hints(validation: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for report in validation.get("steps", []) or []:
        if not isinstance(report, dict):
            continue
        index = report.get("index")
        command = _command_name({"command": report.get("command", "")})
        payload = report.get("payload", {}) if isinstance(report.get("payload"), dict) else {}
        for path in report.get("creates", []) or []:
            if isinstance(path, str) and path and "<auto " not in path:
                hints.append(
                    {
                        "index": index,
                        "kind": "delete_created_node",
                        "command": command,
                        "path": path,
                        "message": "Rollback can delete the created node if it is still disposable.",
                    }
                )
        for path in report.get("deletes", []) or []:
            if isinstance(path, str) and path and "<auto " not in path:
                hints.append(
                    {
                        "index": index,
                        "kind": "destructive_delete",
                        "command": command,
                        "path": path,
                        "message": "Rollback cannot restore deleted nodes without a pre-run scene snapshot, backup, or manual rebuild.",
                    }
                )
        if command in {"set_parm", "set_parm_any", "batch_set_parms", "apply_parm_profile"} and payload.get("node"):
            hints.append(
                {
                    "index": index,
                    "kind": "restore_parameters",
                    "command": command,
                    "path": payload.get("node"),
                    "message": "Rollback should restore parameter values from snapshot_before or node_parms captured before running.",
                }
            )
        if command in {"connect", "set_input", "disconnect", "move_node", "replace_node"}:
            target = payload.get("dst") or payload.get("node") or payload.get("parent")
            hints.append(
                {
                    "index": index,
                    "kind": "restore_wiring",
                    "command": command,
                    "path": target or "",
                    "message": "Rollback should restore node wiring and locations from snapshot_before network data.",
                }
            )
    return _unique_hint_dicts(hints)


def _recipe_hints(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created_types = {
        str((step.get("payload", {}) or {}).get("type", "")).lower()
        for step in steps
        if isinstance(step, dict) and _command_name(step) == "create_node"
    }
    hints: list[dict[str, Any]] = []
    for name, contract in RECIPE_CONTRACTS.items():
        contract_types = {str(item).lower() for item in contract.get("creates", []) or []}
        overlap = sorted(created_types & contract_types)
        if len(overlap) >= 2:
            hints.append({"recipe": name, "matched_types": overlap, "output": contract.get("output", "")})
    return hints


def _step_risk_kind(step: dict[str, Any]) -> str:
    if not isinstance(step, dict):
        return ""
    command = _command_name(step)
    payload = step.get("payload", {}) if isinstance(step.get("payload"), dict) else {}
    if command == "create_node":
        return _node_type_risk(str(payload.get("type", "")))
    if command in {"set_parm", "set_parm_any", "batch_set_parms"}:
        names: list[str] = []
        if command == "set_parm":
            names = [str(payload.get("parm", ""))]
        elif command == "set_parm_any":
            names = [str(item) for item in payload.get("parms", []) or []]
        elif command == "batch_set_parms":
            names = [str(item) for item in (payload.get("values", {}) or {}).keys()]
        target = " ".join(names + [str(payload.get("node", "")), str(payload.get("value", ""))]).lower()
        if any(term in target for term in ("filecache", "cache", "sopoutput")):
            return "cache"
        if any(term in target for term in ("camera", "render", "renderer", "resolution", "picture", "outputimage")):
            return "render"
    if command == "apply_parm_profile":
        profile = str(payload.get("profile", "")).lower()
        if any(term in profile for term in ("rbd", "vellum", "pyro", "solver")):
            return "simulation"
    return ""


def _node_type_risk(node_type: str) -> str:
    node_type = str(node_type or "").lower()
    if any(term in node_type for term in ("filecache", "rop_geometry", "rop_alembic")):
        return "cache"
    if any(term in node_type for term in ("solver", "rbd", "vellum", "pyro", "dopnet", "popnet")):
        return "simulation"
    if any(term in node_type for term in ("karma", "render", "usd", "lop", "materiallibrary", "sopimport")):
        return "render"
    return ""


def _command_name(step: dict[str, Any]) -> str:
    return str((step or {}).get("command", "")).strip().replace("-", "_").lower()


def _unique_nonempty(values: list[str]) -> list[str]:
    return [item for item in dict.fromkeys(values) if item]


def _unique_hint_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for item in values:
        key = (item.get("index"), item.get("kind"), item.get("path"), item.get("command"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _unique_risk_notes(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for item in values:
        key = (item.get("index"), item.get("kind"), item.get("command"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
