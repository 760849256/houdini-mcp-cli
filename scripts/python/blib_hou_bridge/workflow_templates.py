"""Bridge workflow template expansion for editable JSON command plans."""

from __future__ import annotations

import re
from typing import Any


DEFAULT_CACHE_PATH = "$HIP/cache/$HIPNAME.$OS/$F4.bgeo.sc"
DEFAULT_KARMA_RENDER_PATH = "$HIP/render/$HIPNAME.$OS/$OS.$F4.exr"
TEMPLATE_NAMES = (
    "sop-cleanup",
    "cache-output",
    "karma-solaris-preview",
    "vdb-sdf-preview",
    "rbd-preview",
    "vellum-grains-preview",
    "vellum-cloth-preview",
    "pyro-source-preview",
)

TEMPLATE_REQUIRED_FLOW = ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"]

TEMPLATE_EVIDENCE_EXPECTATIONS = [
    "snapshot_before",
    "plan",
    "review",
    "validation",
    "run_result",
    "verification",
    "rpc_log",
    "snapshot_after",
    "summary",
]

TEMPLATE_CATEGORY_RISK_DOMAINS = {
    "cleanup": ["geometry_topology"],
    "cache": ["cache_output", "file_path", "cook_cost"],
    "render": ["render_settings", "file_path", "camera_material_review"],
    "volume": ["volume_resolution", "cook_cost"],
    "dynamics": ["simulation_settings", "cook_cost", "cache_strategy"],
}

TEMPLATE_CATEGORY_VERIFICATION_FOCUS = {
    "cleanup": {
        "read_tools": ["houdini_verify_plan", "houdini_network", "houdini_node_info", "houdini_node_parms"],
        "success_criteria": ["output_null_exists", "output_flags_set", "cleanup_chain_connected", "key_parameters_match_plan"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary"],
        "notes": ["Verify structure and parameters before relying on viewport appearance."],
    },
    "cache": {
        "read_tools": ["houdini_verify_plan", "houdini_node_info", "houdini_node_parms", "houdini_rpc_log"],
        "success_criteria": ["filecache_node_exists", "cache_path_parameter_matches", "output_null_exists", "no_cache_execution_claimed"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary", "rpc_log"],
        "notes": ["Template prepares cache output wiring; it does not prove files were written unless a later cache workflow records that evidence."],
    },
    "render": {
        "read_tools": ["houdini_verify_plan", "houdini_network", "houdini_node_info", "houdini_node_parms"],
        "success_criteria": ["lop_network_exists", "sop_import_points_to_input", "render_settings_match_options", "render_rop_prepared", "no_render_execution_claimed"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary"],
        "notes": ["Template prepares Karma/Solaris settings only; rendered images require a separate reviewed render workflow."],
    },
    "volume": {
        "read_tools": ["houdini_verify_plan", "houdini_node_info", "houdini_node_parms", "houdini_viewport_screenshot"],
        "success_criteria": ["vdb_nodes_exist", "voxel_size_matches_plan", "output_flags_set", "visual_evidence_if_available"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary", "visual_evidence"],
        "notes": ["Prefer structural and parameter checks; viewport proof is supporting evidence only."],
    },
    "dynamics": {
        "read_tools": ["houdini_verify_plan", "houdini_node_info", "houdini_node_parms", "houdini_rpc_log"],
        "success_criteria": ["simulation_nodes_exist", "solver_or_no_solver_option_respected", "profile_parameters_match_plan", "cache_option_respected", "output_flags_set"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary", "rpc_log"],
        "notes": ["Template builds a preview setup; simulation playback/cache success needs a separate evidence-producing workflow."],
    },
}

TEMPLATE_CATALOG = {
    "sop-cleanup": {
        "category": "cleanup",
        "description": "Build a conservative SOP cleanup chain ending in an OUT null.",
        "presets": ["preview", "production"],
        "optional_flags": [],
    },
    "cache-output": {
        "category": "cache",
        "description": "Build a filecache plus OUT null for cache/export handoff.",
        "presets": ["preview"],
        "optional_flags": ["cache_path"],
    },
    "karma-solaris-preview": {
        "category": "render",
        "description": "Build a conservative Solaris/Karma LOP network that imports SOP geometry and prepares render settings without executing a render.",
        "presets": ["preview", "production"],
        "optional_flags": ["render_path", "camera_path", "resolution", "samples", "lopnet_name"],
    },
    "vdb-sdf-preview": {
        "category": "volume",
        "description": "Build a VDB SDF preview chain from polygon input.",
        "presets": ["preview", "production"],
        "optional_flags": ["voxel_size"],
    },
    "rbd-preview": {
        "category": "dynamics",
        "description": "Build an RBD fracture/config/solver preview workflow.",
        "presets": ["preview", "production"],
        "optional_flags": ["no_solver", "cache", "cache_path", "substeps", "start_frame", "constraint_strength"],
    },
    "vellum-grains-preview": {
        "category": "dynamics",
        "description": "Build a Vellum grains preview workflow.",
        "presets": ["preview", "production"],
        "optional_flags": ["no_solver", "cache", "cache_path", "particle_size", "substeps", "start_frame"],
    },
    "vellum-cloth-preview": {
        "category": "dynamics",
        "description": "Build a Vellum cloth preview workflow.",
        "presets": ["preview", "production"],
        "optional_flags": ["no_solver", "cache", "cache_path", "substeps", "start_frame"],
    },
    "pyro-source-preview": {
        "category": "dynamics",
        "description": "Build a Pyro source/rasterize/solver preview workflow.",
        "presets": ["preview", "production"],
        "optional_flags": ["no_solver", "cache", "cache_path", "voxel_size", "substeps", "start_frame", "dissipation"],
    },
}

_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "preview": {
        "cleanup": {"fuse_distance": 0.001},
        "vdb": {"voxel_size": 0.05, "offset": 0.0},
        "rbd": {"detail_size": 0.2, "constraint_strength": 1000, "search_radius": 0.1, "start_frame": 1, "substeps": 2, "density": 1000},
        "grains": {"particle_size": 0.05, "friction": 0.5, "start_frame": 1, "substeps": 2, "collision_passes": 2, "thickness": 0.01},
        "cloth": {"bend_stiffness": 0.1, "stretch_stiffness": 1000, "friction": 0.4, "start_frame": 1, "substeps": 2, "collision_passes": 2, "thickness": 0.01},
        "pyro": {"voxel_size": 0.08, "density_scale": 1.0, "start_frame": 1, "substeps": 1, "buoyancy": 1.0, "cooling": 0.25, "dissipation": 0.1, "disturbance": 0.25, "turbulence": 0.25},
        "karma": {"resolution": [1280, 720], "samples": 32},
    },
    "production": {
        "cleanup": {"fuse_distance": 0.0001},
        "vdb": {"voxel_size": 0.02, "offset": 0.0},
        "rbd": {"detail_size": 0.08, "constraint_strength": 5000, "search_radius": 0.05, "start_frame": 1, "substeps": 4, "density": 1000},
        "grains": {"particle_size": 0.025, "friction": 0.7, "start_frame": 1, "substeps": 4, "collision_passes": 4, "thickness": 0.005},
        "cloth": {"bend_stiffness": 0.5, "stretch_stiffness": 5000, "friction": 0.5, "start_frame": 1, "substeps": 4, "collision_passes": 4, "thickness": 0.005},
        "pyro": {"voxel_size": 0.04, "density_scale": 1.0, "start_frame": 1, "substeps": 2, "buoyancy": 1.0, "cooling": 0.2, "dissipation": 0.05, "disturbance": 0.35, "turbulence": 0.4},
        "karma": {"resolution": [1920, 1080], "samples": 128},
    },
}


def template_catalog() -> dict[str, Any]:
    templates = {}
    for name in TEMPLATE_NAMES:
        entry = dict(TEMPLATE_CATALOG[name])
        category = str(entry.get("category") or "")
        entry["generates"] = "reviewable_bridge_command_plan"
        entry["execution"] = {
            "local_generation_only": True,
            "does_not_contact_houdini": True,
            "does_not_execute": True,
            "requires_bridge_edit_mode_to_run": True,
            "required_flow": list(TEMPLATE_REQUIRED_FLOW),
        }
        entry["risk_domains"] = list(TEMPLATE_CATEGORY_RISK_DOMAINS.get(category, []))
        entry["evidence_expectations"] = list(TEMPLATE_EVIDENCE_EXPECTATIONS)
        entry["verification_focus"] = _verification_focus(category)
        templates[name] = entry
    return {
        "version": 1,
        "template_names": list(TEMPLATE_NAMES),
        "templates": templates,
        "workflow_policy": {
            "local_generation_only": True,
            "does_not_contact_houdini": True,
            "does_not_execute": True,
            "plan_is_reviewable_json": True,
            "required_flow": list(TEMPLATE_REQUIRED_FLOW),
            "evidence_expectations": list(TEMPLATE_EVIDENCE_EXPECTATIONS),
            "verification_focus_required": True,
            "note": "Templates generate bridge command plans only. Review, validate, run, and verify before treating them as executed work.",
        },
        "presets": {name: {group: dict(values) for group, values in groups.items()} for name, groups in _PRESETS.items()},
        "default_cache_path": DEFAULT_CACHE_PATH,
        "default_karma_render_path": DEFAULT_KARMA_RENDER_PATH,
    }


def _verification_focus(category: str) -> dict[str, Any]:
    focus = TEMPLATE_CATEGORY_VERIFICATION_FOCUS.get(category, {})
    return {
        "read_tools": list(focus.get("read_tools", [])) if isinstance(focus.get("read_tools"), list) else [],
        "success_criteria": list(focus.get("success_criteria", [])) if isinstance(focus.get("success_criteria"), list) else [],
        "evidence_artifacts": list(focus.get("evidence_artifacts", [])) if isinstance(focus.get("evidence_artifacts"), list) else [],
        "notes": list(focus.get("notes", [])) if isinstance(focus.get("notes"), list) else [],
    }


def build_plan(template: str, input_path: str, options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    template = _normalize_template(template)
    if not isinstance(input_path, str) or not input_path.startswith("/") or input_path.rstrip("/") == "":
        raise ValueError("Template input must be an absolute Houdini node path.")
    options = dict(options or {})
    preset = str(options.get("preset") or "preview").lower()
    if preset not in _PRESETS:
        raise ValueError("Template preset must be preview or production.")

    if template == "sop-cleanup":
        return _sop_cleanup(input_path, options, preset)
    if template == "cache-output":
        return _cache_output(input_path, options)
    if template == "karma-solaris-preview":
        return _karma_solaris(input_path, options, preset)
    if template == "vdb-sdf-preview":
        return _vdb_sdf(input_path, options, preset)
    if template == "rbd-preview":
        return _rbd(input_path, options, preset)
    if template == "vellum-grains-preview":
        return _vellum_grains(input_path, options, preset)
    if template == "vellum-cloth-preview":
        return _vellum_cloth(input_path, options, preset)
    if template == "pyro-source-preview":
        return _pyro_source(input_path, options, preset)
    raise ValueError("Unknown workflow template: %s" % template)


def _sop_cleanup(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    base = _base_name(options, "CLEAN")
    settings = _settings("cleanup", preset, options)
    chain = _Chain(input_path)
    clean = chain.append("clean", _node_name(base, "CLEAN"))
    chain.set_parm_any(clean, ["remove_degenerate", "removedegenerate"], True)
    chain.set_parm_any(clean, ["remove_unused_points", "removeunusedpts"], True)
    fuse = chain.append("fuse", _node_name(base, "FUSE"))
    chain.set_parm_any(fuse, ["dist", "distance", "fuse_distance"], settings["fuse_distance"])
    normal = chain.append("normal", _node_name(base, "NORMAL"))
    out = chain.output(options.get("output_name") or "OUT_CLEAN", [0.25, 0.55, 0.9], "Bridge cleanup output.")
    chain.finish(_box_name(base), [clean, fuse, normal, out], "Cleanup template.")
    return chain.steps


def _cache_output(input_path: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    base = _base_name(options, "CACHE")
    chain = _Chain(input_path)
    cache = chain.append("filecache", _node_name(base, "FILECACHE"))
    chain.set_parm_any(cache, ["file", "sopoutput"], options.get("cache_path") or DEFAULT_CACHE_PATH, required=False)
    out = chain.output(options.get("output_name") or "OUT_CACHE", [0.25, 0.65, 0.45], "Bridge cache output.")
    chain.finish(_box_name(base), [cache, out], "Cache output template.")
    return chain.steps


def _karma_solaris(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    base = _base_name(options, "KARMA")
    settings = _settings("karma", preset, options)
    parent = _obj_parent(input_path)
    lop_name = _safe_name(options.get("lopnet_name") or "%s_LOPNET" % base, "KARMA_LOPNET").upper()
    lopnet = "%s/%s" % (parent.rstrip("/"), lop_name)
    sopimport = "%s/%s" % (lopnet, _node_name(base, "SOPIMPORT"))
    material_library = "%s/%s" % (lopnet, _node_name(base, "MATERIALS"))
    render_settings = "%s/%s" % (lopnet, _node_name(base, "RENDER_SETTINGS"))
    render_rop = "%s/%s" % (lopnet, _node_name(base, "USD_RENDER_ROP"))
    render_path = options.get("render_path") or DEFAULT_KARMA_RENDER_PATH
    resolution = _resolution(options.get("resolution") or settings.get("resolution"))

    steps: list[dict[str, Any]] = [
        {"command": "create-node", "payload": {"parent": parent, "type": "lopnet", "name": lop_name}},
        {"command": "create-node", "payload": {"parent": lopnet, "type": "sopimport", "name": _node_name(base, "SOPIMPORT")}},
        {"command": "set-parm-any", "payload": {"node": sopimport, "parms": ["soppath", "soppath1", "sop_path", "soppath_main"], "value": input_path, "required": False}},
        {"command": "set-comment", "payload": {"node": sopimport, "comment": "Bridge Solaris import from %s." % input_path}},
        {"command": "create-node", "payload": {"parent": lopnet, "type": "materiallibrary", "name": _node_name(base, "MATERIALS")}},
        {"command": "connect", "payload": {"src": sopimport, "dst": material_library, "input_index": 0}},
        {"command": "create-node", "payload": {"parent": lopnet, "type": "karmarendersettings", "name": _node_name(base, "RENDER_SETTINGS")}},
        {"command": "connect", "payload": {"src": material_library, "dst": render_settings, "input_index": 0}},
        {"command": "set-parm-any", "payload": {"node": render_settings, "parms": ["picture", "outputimage", "renderproduct", "productName"], "value": render_path, "required": False}},
        {"command": "set-parm-any", "payload": {"node": render_settings, "parms": ["resx", "resolutionx", "xres"], "value": resolution[0], "required": False}},
        {"command": "set-parm-any", "payload": {"node": render_settings, "parms": ["resy", "resolutiony", "yres"], "value": resolution[1], "required": False}},
        {"command": "set-parm-any", "payload": {"node": render_settings, "parms": ["samples", "primarysamples", "pixelsamples"], "value": settings.get("samples"), "required": False}},
        {"command": "create-node", "payload": {"parent": lopnet, "type": "usdrender_rop", "name": _node_name(base, "USD_RENDER_ROP")}},
        {"command": "connect", "payload": {"src": render_settings, "dst": render_rop, "input_index": 0}},
        {"command": "set-parm-any", "payload": {"node": render_rop, "parms": ["rendersettings", "rendersettingsprim", "render_settings"], "value": "/Render/rendersettings", "required": False}},
        {"command": "set-parm-any", "payload": {"node": render_rop, "parms": ["renderer", "renderengine"], "value": "Karma XPU", "required": False}},
        {"command": "set-parm-any", "payload": {"node": render_rop, "parms": ["picture", "outputimage"], "value": render_path, "required": False}},
        {"command": "set-node-color", "payload": {"node": render_rop, "color": [0.85, 0.55, 0.25]}},
        {"command": "set-comment", "payload": {"node": render_rop, "comment": "Bridge Karma render output is prepared but not executed."}},
        {"command": "create-sticky-note", "payload": {"parent": lopnet, "name": "NOTE_%s_SOLARIS" % base, "text": "Karma/Solaris preview template. Review camera, materials, render path, and settings before rendering."}},
        {"command": "layout", "payload": {"path": lopnet}},
        {"command": "select", "payload": {"path": render_rop}},
    ]
    camera_path = options.get("camera_path")
    if camera_path:
        steps.insert(15, {"command": "set-parm-any", "payload": {"node": render_settings, "parms": ["camera", "cameraprim", "camera_path"], "value": camera_path, "required": False}})
    return steps


def _vdb_sdf(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    base = _base_name(options, "VDB")
    settings = _settings("vdb", preset, options)
    chain = _Chain(input_path)
    convert = chain.append("vdbfrompolygons", _node_name(base, "FROM_POLYGONS"))
    chain.set_parm_any(convert, ["voxelsize", "voxel_size"], settings["voxel_size"])
    reshape = chain.append("vdbreshapesdf", _node_name(base, "RESHAPE"))
    chain.set_parm_any(reshape, ["operation"], "dilate", required=False)
    chain.set_parm_any(reshape, ["offset"], settings["offset"])
    smooth = chain.append("vdbsmoothsdf", _node_name(base, "SMOOTH"))
    chain.set_parm_any(smooth, ["iterations"], 1)
    out = chain.output(options.get("output_name") or "OUT_VDB", [0.35, 0.75, 0.65], "Bridge VDB output.")
    chain.finish(_box_name(base), [convert, reshape, smooth, out], "VDB SDF preview template.")
    return chain.steps


def _rbd(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    base = _base_name(options, "RBD")
    settings = _settings("rbd", preset, options)
    include_solver = not bool(options.get("no_solver", False))
    chain = _Chain(input_path)
    fracture = chain.append("rbdmaterialfracture", _node_name(base, "FRACTURE"))
    chain.apply_profile(fracture, "rbd-fracture-preview", {"detail_size": settings["detail_size"]})
    configure = chain.append("rbdconfigure", _node_name(base, "CONFIG"))
    chain.apply_profile(configure, "rbd-configure-preview", {"active": True, "density": settings["density"]})
    pack = chain.append("rbdpack", _node_name(base, "PACK"))
    constraints = chain.append("connectadjacentpieces", _node_name(base, "CONSTRAINT_LINES"))
    chain.apply_profile(constraints, "rbd-constraint-preview", {"search_radius": settings["search_radius"]})
    props = chain.append("rbdconstraintproperties", _node_name(base, "CONSTRAINT_PROPS"))
    chain.apply_profile(props, "rbd-constraint-preview", {"constraint_strength": settings["constraint_strength"]})
    created = [fracture, configure, pack, constraints, props]
    stream = pack
    if include_solver:
        solver = chain.append("rbdbulletsolver", _node_name(base, "BULLET_SOLVER"), src=stream)
        chain.apply_profile(solver, "rbd-bullet-solver-preview", {"start_frame": settings["start_frame"], "substeps": settings["substeps"]})
        chain.connect(props, solver, input_index=1)
        unpack = chain.append("rbdunpack", _node_name(base, "UNPACK"))
        created.extend([solver, unpack])
        stream = unpack
    chain.stream = stream
    if bool(options.get("cache", False)):
        cache = chain.append("filecache", _node_name(base, "CACHE"))
        chain.set_parm_any(cache, ["file", "sopoutput"], options.get("cache_path") or DEFAULT_CACHE_PATH, required=False)
        created.append(cache)
    out = chain.output(options.get("output_name") or "OUT_RBD", [0.75, 0.35, 0.2], "Bridge RBD output.")
    created.append(out)
    chain.finish(_box_name(base), created, "RBD preview template.")
    return chain.steps


def _vellum_grains(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    settings = _settings("grains", preset, options)
    return _vellum(
        input_path,
        options,
        "GRAINS",
        "vellumconstraints_grain",
        "OUT_GRAINS",
        [0.9, 0.7, 0.25],
        "Bridge Vellum grains output.",
        "vellum-grains-constraints-preview",
        {"particle_size": settings["particle_size"], "friction": settings["friction"]},
        {"start_frame": settings["start_frame"], "substeps": settings["substeps"], "collision_passes": settings["collision_passes"]},
        {"thickness": settings["thickness"]},
    )


def _vellum_cloth(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    settings = _settings("cloth", preset, options)
    return _vellum(
        input_path,
        options,
        "CLOTH",
        "vellumconstraints",
        "OUT_CLOTH",
        [0.55, 0.35, 0.9],
        "Bridge Vellum cloth output.",
        "vellum-cloth-constraints-preview",
        {"bend_stiffness": settings["bend_stiffness"], "stretch_stiffness": settings["stretch_stiffness"], "friction": settings["friction"]},
        {"start_frame": settings["start_frame"], "substeps": settings["substeps"], "collision_passes": settings["collision_passes"]},
        {"thickness": settings["thickness"]},
    )


def _vellum(
    input_path: str,
    options: dict[str, Any],
    fallback_base: str,
    configure_type: str,
    fallback_output: str,
    color: list[float],
    comment: str,
    configure_profile: str,
    configure_values: dict[str, Any],
    solver_values: dict[str, Any],
    post_values: dict[str, Any],
) -> list[dict[str, Any]]:
    base = _base_name(options, fallback_base)
    include_solver = not bool(options.get("no_solver", False))
    chain = _Chain(input_path)
    configure = chain.append(configure_type, _node_name(base, "CONFIGURE"))
    chain.apply_profile(configure, configure_profile, configure_values)
    created = [configure]
    if include_solver:
        solver = chain.append("vellumsolver", _node_name(base, "SOLVER"))
        chain.apply_profile(solver, "vellum-solver-preview", solver_values)
        created.append(solver)
    post = chain.append("vellumpostprocess", _node_name(base, "POST"))
    chain.apply_profile(post, "vellum-post-preview", post_values)
    created.append(post)
    if bool(options.get("cache", False)):
        cache = chain.append("filecache", _node_name(base, "CACHE"))
        chain.set_parm_any(cache, ["file", "sopoutput"], options.get("cache_path") or DEFAULT_CACHE_PATH, required=False)
        created.append(cache)
    out = chain.output(options.get("output_name") or fallback_output, color, comment)
    created.append(out)
    chain.finish(_box_name(base), created, "%s preview template." % fallback_base.title())
    return chain.steps


def _pyro_source(input_path: str, options: dict[str, Any], preset: str) -> list[dict[str, Any]]:
    base = _base_name(options, "PYRO")
    settings = _settings("pyro", preset, options)
    include_solver = not bool(options.get("no_solver", False))
    chain = _Chain(input_path)
    source = chain.append("pyrosource", _node_name(base, "SOURCE"))
    rasterize = chain.append("volumerasterizeattributes", _node_name(base, "RASTERIZE"))
    chain.apply_profile(rasterize, "pyro-source-preview", {"voxel_size": settings["voxel_size"], "density_scale": settings["density_scale"]})
    created = [source, rasterize]
    if include_solver:
        solver = chain.append("pyrosolver", _node_name(base, "SOLVER"))
        chain.apply_profile(
            solver,
            "pyro-solver-preview",
            {
                "start_frame": settings["start_frame"],
                "substeps": settings["substeps"],
                "buoyancy": settings["buoyancy"],
                "cooling": settings["cooling"],
                "dissipation": settings["dissipation"],
                "disturbance": settings["disturbance"],
                "turbulence": settings["turbulence"],
            },
        )
        created.append(solver)
    if bool(options.get("cache", False)):
        cache = chain.append("filecache", _node_name(base, "CACHE"))
        chain.set_parm_any(cache, ["file", "sopoutput"], options.get("cache_path") or DEFAULT_CACHE_PATH, required=False)
        created.append(cache)
    out = chain.output(options.get("output_name") or "OUT_PYRO", [0.95, 0.45, 0.15], "Bridge Pyro preview output.")
    created.append(out)
    chain.finish(_box_name(base), created, "Pyro source preview template.")
    return chain.steps


class _Chain:
    def __init__(self, input_path: str):
        self.input = input_path.rstrip("/")
        self.parent = self.input.rsplit("/", 1)[0] or "/"
        self.stream = self.input
        self.steps: list[dict[str, Any]] = []

    def append(self, node_type: str, name: str, src: str | None = None) -> str:
        path = "%s/%s" % (self.parent.rstrip("/"), name)
        self.steps.append({"command": "create-node", "payload": {"parent": self.parent, "type": node_type, "name": name}})
        self.connect(src or self.stream, path)
        self.stream = path
        return path

    def connect(self, src: str, dst: str, input_index: int = 0) -> None:
        self.steps.append({"command": "connect", "payload": {"src": src, "dst": dst, "input_index": input_index}})

    def set_parm_any(self, node: str, parms: list[str], value: Any, required: bool = False) -> None:
        self.steps.append({"command": "set-parm-any", "payload": {"node": node, "parms": parms, "value": value, "required": required}})

    def apply_profile(self, node: str, profile: str, values: dict[str, Any], strict: bool = False) -> None:
        self.steps.append({"command": "apply-parm-profile", "payload": {"node": node, "profile": profile, "values": values, "strict": strict}})

    def output(self, output_name: str, color: list[float], comment: str) -> str:
        out_name = _safe_name(output_name, "OUT_TEMPLATE").upper()
        out_path = self.append("null", out_name)
        self.steps.append({"command": "set-node-color", "payload": {"node": out_path, "color": color}})
        self.steps.append({"command": "set-comment", "payload": {"node": out_path, "comment": comment}})
        self.steps.append({"command": "set-flags", "payload": {"node": out_path, "display": True, "render": True}})
        return out_path

    def finish(self, box_name: str, nodes: list[str], note: str) -> None:
        self.steps.append({"command": "create-sticky-note", "payload": {"parent": self.parent, "name": "NOTE_%s" % box_name, "text": note}})
        self.steps.append({"command": "layout", "payload": {"path": self.parent}})
        self.steps.append({"command": "select", "payload": {"path": self.stream}})


def _normalize_template(template: str) -> str:
    normalized = str(template or "").strip().lower().replace("_", "-")
    if normalized not in TEMPLATE_NAMES:
        raise ValueError("Unknown workflow template: %s" % template)
    return normalized


def _settings(group: str, preset: str, options: dict[str, Any]) -> dict[str, Any]:
    settings = dict(_PRESETS[preset][group])
    for key in list(settings):
        if options.get(key) is not None:
            settings[key] = options[key]
    return settings


def _obj_parent(input_path: str) -> str:
    parts = input_path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "obj":
        return "/obj"
    return input_path.rsplit("/", 1)[0] or "/"


def _resolution(value: Any) -> list[int]:
    if isinstance(value, str) and "x" in value.lower():
        left, right = value.lower().split("x", 1)
        value = [left, right]
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return [max(64, int(value[0])), max(64, int(value[1]))]
        except Exception:
            pass
    return [1280, 720]


def _base_name(options: dict[str, Any], fallback: str) -> str:
    return _safe_name(options.get("name") or fallback, fallback).upper()


def _node_name(base: str, suffix: str) -> str:
    return _safe_name("%s_%s" % (base, suffix), suffix).upper()


def _box_name(base: str) -> str:
    return _safe_name("BOX_%s_TEMPLATE" % base, "BOX_TEMPLATE").upper()


def _safe_name(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not re.match(r"^[A-Za-z_]", text):
        text = fallback
    return text[:64]
