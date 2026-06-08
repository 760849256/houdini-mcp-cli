"""Parameter profile registry for bridge workflow dynamics templates."""

from __future__ import annotations

from typing import Any


PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "rbd-fracture-preview": {
        "label": "RBD Material Fracture Preview",
        "parameters": {
            "detail_size": {
                "candidates": ["fracturelevel", "detail_size", "piecesize", "chippingratio"],
                "type": "float",
                "default": 0.2,
                "range": [0.001, 10.0],
                "required": False,
                "unit": "scene units",
            },
            "material_type": {
                "candidates": ["materialtype", "material_type", "fracturestyle"],
                "type": "string",
                "default": "concrete",
                "required": False,
            },
        },
    },
    "rbd-configure-preview": {
        "label": "RBD Configure Preview",
        "parameters": {
            "active": {"candidates": ["active", "i_active"], "type": "bool", "default": True, "required": False},
            "density": {"candidates": ["density", "f_density"], "type": "float", "default": 1000.0, "range": [0.001, 1000000.0], "required": False},
        },
    },
    "rbd-constraint-preview": {
        "label": "RBD Constraint Preview",
        "parameters": {
            "search_radius": {
                "candidates": ["searchradius", "search_radius", "radius", "maxdist"],
                "type": "float",
                "default": 0.1,
                "range": [0.0001, 1000.0],
                "required": False,
            },
            "constraint_type": {
                "candidates": ["constrainttype", "constraint_type", "type"],
                "type": "string",
                "default": "glue",
                "required": False,
            },
            "constraint_strength": {
                "candidates": ["strength", "constraint_strength", "gluestrength"],
                "type": "float",
                "default": 1000.0,
                "range": [0.0, 1000000000.0],
                "required": False,
            },
        },
    },
    "rbd-bullet-solver-preview": {
        "label": "RBD Bullet Solver Preview",
        "parameters": {
            "start_frame": {"candidates": ["startframe", "start_frame", "f1"], "type": "int", "default": 1, "range": [-100000, 1000000], "required": False},
            "substeps": {"candidates": ["substeps", "minsubsteps"], "type": "int", "default": 2, "range": [1, 50], "required": False},
        },
    },
    "vellum-grains-constraints-preview": {
        "label": "Vellum Grains Constraints Preview",
        "parameters": {
            "particle_size": {
                "candidates": ["particlesize", "particle_size", "grainsize", "pscale"],
                "type": "float",
                "default": 0.05,
                "range": [0.0001, 100.0],
                "required": False,
            },
            "friction": {"candidates": ["friction", "staticfriction"], "type": "float", "default": 0.5, "range": [0.0, 10.0], "required": False},
        },
    },
    "vellum-cloth-constraints-preview": {
        "label": "Vellum Cloth Constraints Preview",
        "parameters": {
            "bend_stiffness": {
                "candidates": ["bendstiffness", "bend_stiffness"],
                "type": "float",
                "default": 0.1,
                "range": [0.0, 1000000000.0],
                "required": False,
            },
            "stretch_stiffness": {
                "candidates": ["stretchstiffness", "stretch_stiffness"],
                "type": "float",
                "default": 1000.0,
                "range": [0.0, 1000000000.0],
                "required": False,
            },
            "friction": {"candidates": ["friction"], "type": "float", "default": 0.4, "range": [0.0, 10.0], "required": False},
        },
    },
    "vellum-solver-preview": {
        "label": "Vellum Solver Preview",
        "parameters": {
            "start_frame": {"candidates": ["startframe", "start_frame", "f1"], "type": "int", "default": 1, "range": [-100000, 1000000], "required": False},
            "substeps": {"candidates": ["substeps"], "type": "int", "default": 2, "range": [1, 50], "required": False},
            "collision_passes": {
                "candidates": ["collisionpasses", "collision_passes"],
                "type": "int",
                "default": 2,
                "range": [1, 100],
                "required": False,
            },
        },
    },
    "vellum-post-preview": {
        "label": "Vellum Postprocess Preview",
        "parameters": {
            "thickness": {"candidates": ["thickness", "visualizethickness"], "type": "float", "default": 0.01, "range": [0.0, 100.0], "required": False},
        },
    },
    "pyro-source-preview": {
        "label": "Pyro Source Preview",
        "parameters": {
            "voxel_size": {"candidates": ["voxelsize", "voxel_size"], "type": "float", "default": 0.08, "range": [0.001, 100.0], "required": False},
            "density_scale": {"candidates": ["densityscale", "density_scale", "scale"], "type": "float", "default": 1.0, "range": [0.0, 1000.0], "required": False},
        },
    },
    "pyro-solver-preview": {
        "label": "Pyro Solver Preview",
        "parameters": {
            "start_frame": {"candidates": ["startframe", "start_frame", "f1"], "type": "int", "default": 1, "range": [-100000, 1000000], "required": False},
            "substeps": {"candidates": ["substeps"], "type": "int", "default": 1, "range": [1, 50], "required": False},
            "buoyancy": {"candidates": ["buoyancy", "buoyancyscale"], "type": "float", "default": 1.0, "range": [-1000.0, 1000.0], "required": False},
            "cooling": {"candidates": ["cooling", "coolrate"], "type": "float", "default": 0.25, "range": [0.0, 100.0], "required": False},
            "dissipation": {"candidates": ["dissipation", "dissipationrate"], "type": "float", "default": 0.1, "range": [0.0, 100.0], "required": False},
            "disturbance": {"candidates": ["disturbance", "disturbancestrength"], "type": "float", "default": 0.25, "range": [0.0, 1000.0], "required": False},
            "turbulence": {"candidates": ["turbulence", "turbstrength", "turbulence_strength"], "type": "float", "default": 0.25, "range": [0.0, 1000.0], "required": False},
        },
    },
}

PROFILE_NAMES = tuple(sorted(PROFILE_DEFINITIONS))


def get_profile(name: str) -> dict[str, Any] | None:
    return PROFILE_DEFINITIONS.get(normalize_profile_name(name))


def normalize_profile_name(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def apply_values(profile_name: str, values: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    profile = get_profile(profile_name)
    if profile is None:
        raise ValueError("Unknown parameter profile: %s" % profile_name)
    overrides = values or {}
    result: dict[str, dict[str, Any]] = {}
    for logical_name, spec in profile.get("parameters", {}).items():
        value = overrides.get(logical_name, spec.get("default"))
        coerced = _coerce_value(value, spec)
        clamped = _clamp_value(coerced, spec)
        result[logical_name] = {
            "value": clamped,
            "input": value,
            "clamped": clamped != coerced,
            "spec": spec,
        }
    return result


def resolve_profile(
    profile_name: str,
    values: dict[str, Any] | None,
    available_parms: list[str],
    strict: bool = False,
) -> dict[str, Any]:
    normalized = normalize_profile_name(profile_name)
    profile = get_profile(normalized)
    if profile is None:
        raise ValueError("Unknown parameter profile: %s" % profile_name)
    available = [name for name in available_parms if isinstance(name, str)]
    available_set = set(available)
    resolved_values = apply_values(normalized, values or {})
    matched = []
    skipped = []
    unresolved = []
    clamped = []
    for logical_name, resolved in resolved_values.items():
        spec = resolved["spec"]
        candidates = [name for name in spec.get("candidates", []) or [] if isinstance(name, str)]
        parm_name = next((name for name in candidates if name in available_set), None)
        if parm_name is None:
            entry = {"parameter": logical_name, "candidates": candidates, "required": bool(spec.get("required", False))}
            if strict or spec.get("required", False):
                unresolved.append(entry)
            else:
                skipped.append(entry)
            continue
        entry = {
            "parameter": logical_name,
            "parm": parm_name,
            "value": resolved.get("value"),
            "input": resolved.get("input"),
            "candidates": candidates,
            "clamped": bool(resolved.get("clamped")),
        }
        matched.append(entry)
        if resolved.get("clamped"):
            clamped.append({"parameter": logical_name, "parm": parm_name, "input": resolved.get("input"), "value": resolved.get("value")})
    return {
        "profile": normalized,
        "matched": matched,
        "skipped": skipped,
        "unresolved": unresolved,
        "clamped": clamped,
        "available_parms": available,
        "match_count": len(matched),
        "parameter_count": len(resolved_values),
    }


def candidate_names(profile_name: str) -> list[str]:
    profile = get_profile(profile_name)
    if profile is None:
        return []
    names: list[str] = []
    for spec in profile.get("parameters", {}).values():
        for name in spec.get("candidates", []) or []:
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


def manifest() -> dict[str, Any]:
    return {"profiles": PROFILE_DEFINITIONS, "profile_names": list(PROFILE_NAMES)}


def _coerce_value(value: Any, spec: dict[str, Any]) -> Any:
    parm_type = spec.get("type")
    if parm_type == "bool":
        return bool(value)
    if parm_type == "int":
        return int(value)
    if parm_type == "float":
        return float(value)
    if parm_type == "string":
        return str(value)
    return value


def _clamp_value(value: Any, spec: dict[str, Any]) -> Any:
    value_range = spec.get("range")
    if not isinstance(value_range, list) or len(value_range) != 2:
        return value
    if not isinstance(value, (int, float)):
        return value
    low, high = value_range
    return max(low, min(high, value))
