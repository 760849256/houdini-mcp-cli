"""JSON protocol for the Blib Houdini Bridge.

The bridge defaults to read-only. Edit commands exist only behind Houdini-side
edit mode, and danger command names are reserved so tests keep them blocked.
"""

from __future__ import annotations

import time
import uuid
from typing import Any


BRIDGE_VERSION = "0.2.0"

READ_COMMANDS = frozenset(
    {
        "health",
        "context",
        "downstream",
        "edit_mode",
        "find_nodes",
        "manifest",
        "network",
        "node_parms",
        "profile_manifest",
        "probe_parm_profile",
        "recipe_manifest",
        "review_plan",
        "rpc_log",
        "scene_snapshot",
        "upstream",
        "selected",
        "node_info",
        "validate_plan",
        "verify_plan",
        "viewport_screenshot",
    }
)

EDIT_COMMANDS = frozenset(
    {
        "batch_set_parms",
        "connect",
        "bypass_node",
        "copy_node",
        "create_network_box",
        "create_node",
        "create_sticky_note",
        "delete_node",
        "disconnect",
        "ensure_parm",
        "layout",
        "move_node",
        "rename_node",
        "replace_node",
        "select",
        "set_comment",
        "set_flags",
        "set_input",
        "set_node_color",
        "set_node_shape",
        "set_parm_any",
        "apply_parm_profile",
        "run_plan",
        "set_parm",
        "set_position",
    }
)

DIRECT_EDIT_COMMANDS = frozenset(
    {
        "batch_set_parms",
        "bypass_node",
        "connect",
        "create_node",
        "disconnect",
        "layout",
        "select",
        "set_comment",
        "set_flags",
        "set_input",
        "set_node_color",
        "set_parm",
        "set_position",
    }
)

PLAN_REQUIRED_EDIT_COMMANDS = EDIT_COMMANDS - DIRECT_EDIT_COMMANDS - {"run_plan"}

DIRECT_EDIT_VERIFICATION_CONTRACTS = {
    "batch_set_parms": {
        "read_tools": ["node_parms", "node_info"],
        "success_criteria": ["Changed parameter values are readable from node_parms.", "The touched node still exists."],
    },
    "bypass_node": {
        "read_tools": ["node_info"],
        "success_criteria": ["node_info.flags.bypass matches the requested bypass state."],
    },
    "connect": {
        "read_tools": ["network", "upstream", "downstream"],
        "success_criteria": ["The destination input is wired to the requested source node."],
    },
    "create_node": {
        "read_tools": ["node_info", "network"],
        "success_criteria": ["The created node path exists.", "The parent network lists the created node."],
    },
    "disconnect": {
        "read_tools": ["network", "upstream", "downstream"],
        "success_criteria": ["The removed input or source connection is absent from the read-back wiring."],
    },
    "layout": {
        "read_tools": ["network", "scene_snapshot"],
        "success_criteria": ["The target network remains readable after layout."],
    },
    "select": {
        "read_tools": ["selected", "node_info", "scene_snapshot"],
        "success_criteria": ["The requested node appears in the current selection or node_info.flags.selected is true."],
    },
    "set_comment": {
        "read_tools": ["node_info"],
        "success_criteria": ["node_info.comment matches the requested comment text."],
    },
    "set_flags": {
        "read_tools": ["node_info"],
        "success_criteria": ["node_info.flags contains the requested display/render flag state."],
    },
    "set_input": {
        "read_tools": ["network", "upstream", "downstream"],
        "success_criteria": ["The destination input index points at the requested source node."],
    },
    "set_node_color": {
        "read_tools": ["node_info"],
        "success_criteria": ["node_info.color matches the requested RGB color."],
    },
    "set_parm": {
        "read_tools": ["node_parms", "node_info"],
        "success_criteria": ["node_parms reports the requested parameter value or expression.", "The touched node still exists."],
    },
    "set_position": {
        "read_tools": ["node_info"],
        "success_criteria": ["node_info.position matches the requested network-editor position."],
    },
}

DANGER_COMMANDS = frozenset(
    {
        "save_hip",
        "save_as",
        "load_hip",
        "run_python",
        "run_hscript",
        "open_file",
        "write_file",
        "shell_command",
        "install_package",
    }
)

COMMANDS = {
    "health": {
        "permission": "read",
        "description": "Report whether the Houdini bridge is reachable.",
    },
    "manifest": {
        "permission": "read",
        "description": "Describe bridge commands, permissions, and expected payload shapes.",
    },
    "recipe_manifest": {
        "permission": "read",
        "description": "Describe bridge-native workflow recipe contracts and presets for manual JSON planning.",
    },
    "profile_manifest": {
        "permission": "read",
        "description": "Describe safe dynamics parameter profiles and candidate Houdini parameter names.",
    },
    "probe_parm_profile": {
        "permission": "read",
        "description": "Probe how a dynamics parameter profile would match an existing node without changing the scene.",
    },
    "review_plan": {
        "permission": "read",
        "description": "Review a JSON command list for impact, blockers, confirmations, and tuning suggestions.",
    },
    "verify_plan": {
        "permission": "read",
        "description": "Verify post-run scene state against a bridge command list and optional pre-run validation.",
    },
    "context": {
        "permission": "read",
        "description": "Read basic session, timeline, network, and selection context.",
    },
    "selected": {
        "permission": "read",
        "description": "Read the current Houdini node selection.",
    },
    "scene_snapshot": {
        "permission": "read",
        "description": "Read a compact scene context bundle for external planning.",
    },
    "find_nodes": {
        "permission": "read",
        "description": "Find nodes below a root path by name, type, category, or path text.",
    },
    "node_info": {
        "permission": "read",
        "description": "Read metadata for a single node path.",
    },
    "node_parms": {
        "permission": "read",
        "description": "Read parameter values, expressions, locks, and keyframe state for one node.",
    },
    "rpc_log": {
        "permission": "read",
        "description": "Read recent bridge RPC request history from the Houdini session.",
    },
    "viewport_screenshot": {
        "permission": "read",
        "description": "Capture the current Houdini Scene Viewer viewport to a temporary image file.",
    },
    "network": {
        "permission": "read",
        "description": "Read direct children and wiring inside a Houdini network.",
    },
    "upstream": {
        "permission": "read",
        "description": "Trace input dependencies for a Houdini node.",
    },
    "downstream": {
        "permission": "read",
        "description": "Trace output consumers for a Houdini node.",
    },
    "edit_mode": {
        "permission": "read",
        "description": "Read or change the Houdini-side bridge edit gate.",
    },
    "validate_plan": {
        "permission": "read",
        "description": "Validate a JSON list of bridge commands without changing the Houdini scene.",
    },
    "create_node": {
        "permission": "edit",
        "description": "Create one node under a parent network when edit mode is enabled.",
    },
    "rename_node": {
        "permission": "edit",
        "description": "Rename one node when edit mode is enabled.",
    },
    "set_node_color": {
        "permission": "edit",
        "description": "Set one node color when edit mode is enabled.",
    },
    "bypass_node": {
        "permission": "edit",
        "description": "Set one node bypass state when edit mode is enabled.",
    },
    "create_network_box": {
        "permission": "edit",
        "description": "Create one network box and optionally add nodes when edit mode is enabled.",
    },
    "create_sticky_note": {
        "permission": "edit",
        "description": "Create one sticky note in a network when edit mode is enabled.",
    },
    "set_parm": {
        "permission": "edit",
        "description": "Set one parameter value when edit mode is enabled.",
    },
    "set_parm_any": {
        "permission": "edit",
        "description": "Set the first existing parameter from a safe candidate list when edit mode is enabled.",
    },
    "apply_parm_profile": {
        "permission": "edit",
        "description": "Apply a safe dynamics parameter profile to matching existing parameters when edit mode is enabled.",
    },
    "run_plan": {
        "permission": "edit",
        "description": "Run a validated bridge command list in one Houdini-side RPC when edit mode is enabled.",
    },
    "batch_set_parms": {
        "permission": "edit",
        "description": "Set several existing parameters on one node in a single edit-mode command.",
    },
    "connect": {
        "permission": "edit",
        "description": "Connect one source node into one destination input when edit mode is enabled.",
    },
    "set_input": {
        "permission": "edit",
        "description": "Set or clear one destination input when edit mode is enabled.",
    },
    "disconnect": {
        "permission": "edit",
        "description": "Disconnect one destination input, matching source connection, or all inputs on a node.",
    },
    "move_node": {
        "permission": "edit",
        "description": "Move one node to another parent network, optionally renaming it.",
    },
    "copy_node": {
        "permission": "edit",
        "description": "Copy one node to a parent network, optionally renaming the copy.",
    },
    "set_node_shape": {
        "permission": "edit",
        "description": "Set one network editor node shape using Houdini node user data.",
    },
    "replace_node": {
        "permission": "edit",
        "description": "Create a replacement sibling node and optionally reconnect inputs, outputs, and delete the old node.",
    },
    "delete_node": {
        "permission": "edit",
        "description": "Delete one explicitly confirmed node when edit mode is enabled.",
    },
    "ensure_parm": {
        "permission": "edit",
        "description": "Create one simple spare parameter on a node when edit mode is enabled.",
    },
    "layout": {
        "permission": "edit",
        "description": "Layout children in one network when edit mode is enabled.",
    },
    "select": {
        "permission": "edit",
        "description": "Select one node when edit mode is enabled.",
    },
    "set_comment": {
        "permission": "edit",
        "description": "Set one node comment when edit mode is enabled.",
    },
    "set_flags": {
        "permission": "edit",
        "description": "Set display or render flags on one node when edit mode is enabled.",
    },
    "set_position": {
        "permission": "edit",
        "description": "Set one network node position when edit mode is enabled.",
    },
}

COMMAND_PAYLOAD_SCHEMAS = {
    "health": {"type": "object", "properties": {}, "additionalProperties": False},
    "manifest": {"type": "object", "properties": {}, "additionalProperties": False},
    "recipe_manifest": {"type": "object", "properties": {}, "additionalProperties": False},
    "profile_manifest": {"type": "object", "properties": {}, "additionalProperties": False},
    "probe_parm_profile": {
        "type": "object",
        "required": ["node", "profile"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "profile": {"type": "string", "minLength": 1},
            "values": {"type": "object", "default": {}},
            "strict": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "review_plan": {
        "type": "object",
        "required": ["steps"],
        "properties": {"steps": {"type": "array"}},
        "additionalProperties": False,
    },
    "context": {"type": "object", "properties": {}, "additionalProperties": False},
    "selected": {"type": "object", "properties": {}, "additionalProperties": False},
    "scene_snapshot": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "format": "houdini_absolute_node_path"},
            "trace_depth": {"type": "integer", "minimum": 0, "maximum": 4, "default": 1},
            "max_selected": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3},
            "include_viewport": {"type": "boolean", "default": False},
            "width": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 1280},
            "height": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 720},
            "prefix": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$", "default": "scene_snapshot"},
        },
        "additionalProperties": False,
    },
    "find_nodes": {
        "type": "object",
        "required": ["root"],
        "properties": {
            "root": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string"},
            "type": {"type": "string"},
            "category": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
        "additionalProperties": False,
    },
    "node_info": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string", "format": "houdini_absolute_node_path"}},
        "additionalProperties": False,
    },
    "node_parms": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string", "format": "houdini_absolute_node_path"}},
        "additionalProperties": False,
    },
    "rpc_log": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 0, "maximum": 200, "default": 50}},
        "additionalProperties": False,
    },
    "viewport_screenshot": {
        "type": "object",
        "properties": {
            "width": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 1280},
            "height": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 720},
            "prefix": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$", "default": "viewport"},
        },
        "additionalProperties": False,
    },
    "network": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string", "format": "houdini_absolute_node_path"}},
        "additionalProperties": False,
    },
    "upstream": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "format": "houdini_absolute_node_path"},
            "depth": {"type": "integer", "minimum": 0, "maximum": 12, "default": 4},
        },
        "additionalProperties": False,
    },
    "downstream": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "format": "houdini_absolute_node_path"},
            "depth": {"type": "integer", "minimum": 0, "maximum": 12, "default": 4},
        },
        "additionalProperties": False,
    },
    "edit_mode": {
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
        "additionalProperties": False,
    },
    "validate_plan": {
        "type": "object",
        "required": ["steps"],
        "properties": {"steps": {"type": "array"}},
        "additionalProperties": False,
    },
    "verify_plan": {
        "type": "object",
        "required": ["steps"],
        "properties": {
            "steps": {"type": "array"},
            "validation": {"type": "object"},
            "run_result": {"type": "object"},
        },
        "additionalProperties": False,
    },
    "create_node": {
        "type": "object",
        "required": ["parent", "type"],
        "properties": {
            "parent": {"type": "string", "format": "houdini_absolute_node_path"},
            "type": {"type": "string", "minLength": 1},
            "name": {"type": "string", "format": "houdini_simple_name"},
        },
        "additionalProperties": False,
    },
    "set_parm": {
        "type": "object",
        "required": ["node", "parm", "value"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "parm": {"type": "string", "format": "houdini_simple_name"},
            "value": {},
        },
        "additionalProperties": False,
    },
    "set_parm_any": {
        "type": "object",
        "required": ["node", "parms", "value"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "parms": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "format": "houdini_simple_name"}},
            "value": {},
            "required": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "batch_set_parms": {
        "type": "object",
        "required": ["node", "values"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "values": {"type": "object"},
            "required": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    "apply_parm_profile": {
        "type": "object",
        "required": ["node", "profile"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "profile": {"type": "string", "minLength": 1},
            "values": {"type": "object", "default": {}},
            "strict": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "run_plan": {
        "type": "object",
        "required": ["steps"],
        "properties": {
            "steps": {"type": "array"},
            "continue_on_error": {"type": "boolean", "default": False},
            "undo_label": {"type": "string", "maxLength": 120, "default": "Blib Bridge plan"},
        },
        "additionalProperties": False,
    },
    "rename_node": {
        "type": "object",
        "required": ["node", "name"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "unique": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    "set_node_color": {
        "type": "object",
        "required": ["node", "color"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "color": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0, "maximum": 1}},
        },
        "additionalProperties": False,
    },
    "bypass_node": {
        "type": "object",
        "required": ["node", "bypass"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "bypass": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "create_network_box": {
        "type": "object",
        "required": ["parent", "name"],
        "properties": {
            "parent": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "comment": {"type": "string", "maxLength": 2000},
            "nodes": {"type": "array", "items": {"type": "string", "format": "houdini_absolute_node_path"}, "maxItems": 100},
            "color": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0, "maximum": 1}},
        },
        "additionalProperties": False,
    },
    "create_sticky_note": {
        "type": "object",
        "required": ["parent", "text"],
        "properties": {
            "parent": {"type": "string", "format": "houdini_absolute_node_path"},
            "text": {"type": "string", "maxLength": 4000},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "color": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number", "minimum": 0, "maximum": 1}},
        },
        "additionalProperties": False,
    },
    "connect": {
        "type": "object",
        "required": ["src", "dst"],
        "properties": {
            "src": {"type": "string", "format": "houdini_absolute_node_path"},
            "dst": {"type": "string", "format": "houdini_absolute_node_path"},
            "input_index": {"type": "integer", "minimum": 0, "maximum": 99, "default": 0},
        },
        "additionalProperties": False,
    },
    "set_input": {
        "type": "object",
        "required": ["dst", "input_index"],
        "properties": {
            "dst": {"type": "string", "format": "houdini_absolute_node_path"},
            "input_index": {"type": "integer", "minimum": 0, "maximum": 99},
            "src": {"type": "string", "format": "houdini_absolute_node_path"},
            "clear": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "disconnect": {
        "type": "object",
        "required": ["node"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "input_index": {"type": "integer", "minimum": 0, "maximum": 99},
            "src": {"type": "string", "format": "houdini_absolute_node_path"},
            "all": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "move_node": {
        "type": "object",
        "required": ["node", "parent"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "parent": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string", "format": "houdini_simple_name"},
        },
        "additionalProperties": False,
    },
    "copy_node": {
        "type": "object",
        "required": ["node", "parent"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "parent": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "unique": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    },
    "set_node_shape": {
        "type": "object",
        "required": ["node", "shape"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "shape": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "additionalProperties": False,
    },
    "replace_node": {
        "type": "object",
        "required": ["node", "type"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "type": {"type": "string", "minLength": 1},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "reconnect_inputs": {"type": "boolean", "default": True},
            "reconnect_outputs": {"type": "boolean", "default": True},
            "delete_old": {"type": "boolean", "default": False},
            "confirm": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "delete_node": {
        "type": "object",
        "required": ["node", "confirm"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "confirm": {"type": "boolean"},
            "delete_contents": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "ensure_parm": {
        "type": "object",
        "required": ["node", "name", "type", "default"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "name": {"type": "string", "format": "houdini_simple_name"},
            "type": {"enum": ["float", "int"]},
            "label": {"type": "string", "maxLength": 80},
            "default": {"type": "number"},
        },
        "additionalProperties": False,
    },
    "layout": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string", "format": "houdini_absolute_node_path"}},
        "additionalProperties": False,
    },
    "select": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string", "format": "houdini_absolute_node_path"}},
        "additionalProperties": False,
    },
    "set_comment": {
        "type": "object",
        "required": ["node", "comment"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "comment": {"type": "string", "maxLength": 2000},
        },
        "additionalProperties": False,
    },
    "set_flags": {
        "type": "object",
        "required": ["node"],
        "required_any": ["display", "render"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "display": {"type": "boolean"},
            "render": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "set_position": {
        "type": "object",
        "required": ["node", "x", "y"],
        "properties": {
            "node": {"type": "string", "format": "houdini_absolute_node_path"},
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "additionalProperties": False,
    },
}

DEFAULT_RESULT_SCHEMA = {
    "type": "object",
    "description": "Bridge command result object.",
}

COMMAND_RESULT_SCHEMAS = {
    "node_info": {
        "type": "object",
        "description": "Read-only metadata and direct-edit-verifiable state for one Houdini node.",
        "properties": {
            "path": {"type": "string", "description": "Absolute Houdini node path."},
            "name": {"type": "string", "description": "Node name."},
            "type": {"type": "string", "description": "Node type name."},
            "category": {"type": "string", "description": "Houdini node category name."},
            "parent": {"type": "string", "description": "Absolute parent network path."},
            "inputs": {"type": "array", "description": "Input node summaries, preserving empty inputs as null."},
            "outputs": {"type": "array", "description": "Output node summaries."},
            "children": {"type": "array", "description": "Child node summaries."},
            "parms": {"type": "array", "description": "Parameter summaries including value, raw value, expression, lock, and keyframe state."},
            "flags": {
                "type": "object",
                "description": "Node flags useful for structural verification after direct edits.",
                "properties": {
                    "display": {"type": "boolean"},
                    "render": {"type": "boolean"},
                    "bypass": {"type": "boolean"},
                    "selected": {"type": "boolean"},
                },
            },
            "comment": {"type": "string", "description": "Node comment text, useful for set_comment readback."},
            "position": {
                "type": "array",
                "description": "Network editor node position as [x, y], useful for set_position readback.",
                "items": {"type": "number"},
            },
            "color": {
                "type": ["array", "null"],
                "description": "Node color as [r, g, b] when available, useful for set_node_color readback.",
                "items": {"type": "number"},
            },
            "messages": {
                "type": "object",
                "description": "Current node errors and warnings.",
                "properties": {
                    "errors": {"type": "array"},
                    "warnings": {"type": "array"},
                },
            },
        },
    },
    "validate_plan": {
        "type": "object",
        "description": "Read-only preflight result for a bridge command plan.",
        "properties": {
            "valid": {"type": "boolean", "description": "True when every step passes protocol and scene-aware validation."},
            "ready_to_run": {"type": "boolean", "description": "True when the plan is valid and not blocked by the current edit-mode gate."},
            "step_count": {"type": "integer", "description": "Number of validated plan steps."},
            "steps_sha256": {"type": "string", "description": "SHA256 fingerprint of the canonical JSON steps array validated by this report."},
            "would_require_edit": {"type": "boolean", "description": "True when at least one step is an edit command."},
            "edit_enabled": {"type": "boolean", "description": "Current Houdini-side bridge edit-mode state."},
            "blocked_by_edit_mode": {"type": "boolean", "description": "True when the plan would edit the scene but edit mode is disabled."},
            "steps": {
                "type": "array",
                "description": "Per-step validation reports including command, permission, issues, warnings, and expected path effects.",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "command": {"type": "string"},
                        "permission": {"type": "string", "enum": ["read", "edit"]},
                        "valid": {"type": "boolean"},
                        "payload": {"type": "object"},
                        "issues": {"type": "array"},
                        "warnings": {"type": "array"},
                        "creates": {"type": "array"},
                        "touches": {"type": "array"},
                        "deletes": {"type": "array"},
                        "aliases": {"type": "object"},
                    },
                },
            },
        },
    },
    "review_plan": {
        "type": "object",
        "description": "Risk, impact, and confirmation review for a bridge command plan.",
        "properties": {
            "level": {"type": "string", "enum": ["ok", "warning", "blocked"], "description": "Highest review severity for the plan."},
            "confidence": {"type": "number", "description": "Heuristic confidence that the plan is understandable and safe to run after validation."},
            "blockers": {"type": "array", "description": "Blocking issues that should prevent execution."},
            "warnings": {"type": "array", "description": "Non-blocking risks and review warnings."},
            "suggestions": {"type": "array", "description": "Optional plan improvements before execution."},
            "required_confirmations": {"type": "array", "description": "Human confirmations expected before risky edits are run."},
            "rollback_hints": {"type": "array", "description": "Hints useful for drafting a rollback plan."},
            "risk_notes": {"type": "array", "description": "Structured cache, simulation, render, or cleanup risk notes."},
            "impact": {
                "type": "object",
                "description": "Expected created, touched, deleted, and parameter paths.",
                "properties": {
                    "created": {"type": "array"},
                    "touched": {"type": "array"},
                    "deleted": {"type": "array"},
                    "parms": {"type": "array"},
                },
            },
            "recipe_hints": {"type": "array", "description": "Bridge-native recipe or workflow hints inferred from the plan."},
            "validation": {
                "type": "object",
                "description": "Compact validate_plan summary used by this review, including the same steps_sha256 fingerprint.",
            },
        },
    },
    "run_plan": {
        "type": "object",
        "description": "Execution result for a validated bridge command plan.",
        "properties": {
            "ok": {"type": "boolean", "description": "True when every executed step reported success."},
            "count": {"type": "integer", "description": "Total number of requested steps."},
            "ran": {"type": "integer", "description": "Number of steps actually executed before completion or stop."},
            "stopped": {"type": "boolean", "description": "True when execution stopped after a failed step."},
            "failed_step": {"type": ["object", "null"], "description": "Failed step summary when execution did not complete cleanly."},
            "duration_ms": {"type": "number", "description": "Total plan execution duration in milliseconds."},
            "results": {
                "type": "array",
                "description": "Per-step bridge responses, preserving indexes for verify_plan.",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "response": {
                            "type": "object",
                            "properties": {
                                "ok": {"type": "boolean"},
                                "command": {"type": "string"},
                                "result": {"type": "object"},
                                "error": {"type": ["object", "null"]},
                                "duration_ms": {"type": "number"},
                            },
                        },
                    },
                },
            },
            "edit_enabled": {"type": "boolean", "description": "Bridge edit-mode state during execution."},
        },
    },
    "verify_plan": {
        "type": "object",
        "description": "Post-run structural verification result for a bridge command plan.",
        "properties": {
            "ok": {"type": "boolean", "description": "False when verification has failed checks."},
            "verified": {"type": "boolean", "description": "True only when verification status is pass."},
            "status": {"type": "string", "enum": ["pass", "failed", "inconclusive"], "description": "Compact verification verdict."},
            "summary": {
                "type": "object",
                "description": "Counts of pass, failed, and inconclusive checks.",
                "properties": {
                    "total": {"type": "integer"},
                    "passed": {"type": "integer"},
                    "failed": {"type": "integer"},
                    "inconclusive": {"type": "integer"},
                    "direct_edit_readback": {
                        "type": "object",
                        "description": "Counts of checks that satisfy direct-edit readback contracts.",
                        "properties": {
                            "total": {"type": "integer"},
                            "passed": {"type": "integer"},
                            "failed": {"type": "integer"},
                            "inconclusive": {"type": "integer"},
                            "commands": {"type": "array", "items": {"type": "string"}},
                            "failed_commands": {"type": "array", "items": {"type": "string"}},
                            "inconclusive_commands": {"type": "array", "items": {"type": "string"}},
                            "proof_ready": {"type": "boolean"},
                        },
                    },
                },
            },
            "validation_source": {"type": "string", "description": "Whether validation came from the payload or current scene fallback."},
            "validation": {"type": "object", "description": "Compact validation summary used for verification."},
            "run": {"type": "object", "description": "Compact run_plan result summary used for verification."},
            "checks": {
                "type": "array",
                "description": "Structural checks for run result, created/deleted paths, node types, parms, flags, comment, bypass, position, color, selection, and wiring.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "label": {"type": "string"},
                        "status": {"type": "string", "enum": ["pass", "failed", "inconclusive"]},
                        "message": {"type": "string"},
                        "expected": {},
                        "actual": {},
                        "path": {"type": "string"},
                        "step_index": {"type": "integer"},
                        "satisfies_direct_edit_contract": {"type": "string"},
                        "direct_edit_contract_read_tools": {"type": "array", "items": {"type": "string"}},
                        "direct_edit_contract_mcp_read_tools": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
    "scene_snapshot": {
        "type": "object",
        "description": "Read-only scene context bundle for AI planning and verification.",
        "properties": {
            "context": {"type": "object", "description": "HIP, timeline, application, current network, and selection context."},
            "selected": {"type": "object", "description": "Current Houdini selection summary."},
            "network": {"type": "object", "description": "Direct children, wiring, flags, messages, and network boxes for the inspected network."},
            "selected_details": {"type": "array", "description": "Focused node_info records for selected nodes."},
            "traces": {"type": "array", "description": "Optional upstream/downstream traces for selected nodes."},
            "viewport": {"type": "object", "description": "Viewport capture result when include_viewport is true."},
            "semantics": {
                "type": "object",
                "description": "Derived scene understanding fields for safe AI planning.",
                "properties": {
                    "inferred_purpose": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "key_outputs": {"type": "array", "description": "Likely output/display/render/terminal nodes."},
                    "focus_candidates": {
                        "type": "array",
                        "description": "Prioritized nodes to inspect first, with kinds, reasons, suggested bridge commands, and MCP tool names.",
                    },
                    "selected_focus": {"type": "array", "description": "Selected nodes with upstream/downstream counts."},
                    "cache_nodes": {"type": "array", "description": "Likely cache/export nodes."},
                    "simulation_nodes": {"type": "array", "description": "Likely simulation, solver, DOP, RBD, Vellum, Pyro, FLIP, or POP nodes."},
                    "volume_nodes": {"type": "array", "description": "Likely VDB, SDF, fog, density, rasterize, or volume-processing nodes."},
                    "render_nodes": {"type": "array", "description": "Likely render, Solaris, Karma, ROP, USD, or material nodes."},
                    "risk_notes": {"type": "array", "description": "Read-only risk notes such as node errors, warnings, no selection, or no key output."},
                    "risk_domains": {
                        "type": "array",
                        "description": "Scene-level routing summary for cache, simulation, volume, render, node-error, path, cook-cost, and template-related risk domains.",
                    },
                    "inspection_hints": {"type": "array", "description": "Suggested safe read-only follow-up commands and MCP tool names."},
                    "workflow_suggestions": {
                        "type": "array",
                        "description": "Read-only local workflow template suggestions inferred from scene semantics. These point at houdini_template_plan and still require review, validation, run, and verification.",
                    },
                    "scene_understanding": {
                        "type": "object",
                        "description": "Compact read-only route contract for what an AI client should inspect first, which risk domain is primary, and which review flow is required before writes.",
                    },
                    "network_shape": {"type": "object", "description": "Node, wire, branch, terminal, flag, type, and category counts."},
                },
            },
            "summary": {
                "type": "object",
                "description": "Compact counts and high-level scene summary.",
                "properties": {
                    "network_path": {"type": "string"},
                    "network_node_count": {"type": "integer"},
                    "selection_count": {"type": "integer"},
                    "trace_count": {"type": "integer"},
                    "edit_enabled": {"type": "boolean"},
                    "inferred_purpose": {"type": "string"},
                    "key_output_count": {"type": "integer"},
                    "cache_node_count": {"type": "integer"},
                    "simulation_node_count": {"type": "integer"},
                    "volume_node_count": {"type": "integer"},
                    "render_node_count": {"type": "integer"},
                    "focus_candidate_count": {"type": "integer"},
                    "risk_count": {"type": "integer"},
                    "risk_domain_count": {"type": "integer"},
                    "inspection_hint_count": {"type": "integer"},
                    "workflow_suggestion_count": {"type": "integer"},
                },
            },
        },
    },
}


class BridgeProtocolError(ValueError):
    """Raised when a bridge request is malformed or not allowed."""


def normalize_command(command: str) -> str:
    return str(command or "").strip().replace("-", "_").lower()


def new_request_id() -> str:
    return uuid.uuid4().hex


def parse_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BridgeProtocolError("Request body must be a JSON object.")
    command = normalize_command(raw.get("command"))
    payload = raw.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise BridgeProtocolError("Request payload must be a JSON object.")
    request_id = str(raw.get("request_id") or new_request_id())
    token = raw.get("token")
    validate_command(command, payload)
    return {
        "request_id": request_id,
        "command": command,
        "payload": payload,
        "token": token,
    }


def validate_command(command: str, payload: dict[str, Any] | None = None) -> None:
    normalized = normalize_command(command)
    if not normalized:
        raise BridgeProtocolError("Command is required.")
    if normalized in DANGER_COMMANDS:
        raise BridgeProtocolError("Danger commands are not available in bridge v0.2.")
    if normalized not in READ_COMMANDS and normalized not in EDIT_COMMANDS:
        raise BridgeProtocolError("Unknown bridge command: %s" % normalized)
    payload = payload or {}
    if normalized in {"node_info", "node_parms", "network", "upstream", "downstream"}:
        node_path = payload.get("path")
        if not isinstance(node_path, str) or not node_path.startswith("/"):
            raise BridgeProtocolError("%s requires an absolute Houdini node path." % normalized)

    if normalized == "scene_snapshot":
        path = payload.get("path")
        if path is not None and (not isinstance(path, str) or not path.startswith("/")):
            raise BridgeProtocolError("scene_snapshot path must be an absolute Houdini node path when provided.")
        trace_depth = payload.get("trace_depth", 1)
        if not isinstance(trace_depth, int) or trace_depth < 0 or trace_depth > 4:
            raise BridgeProtocolError("scene_snapshot trace_depth must be an integer between 0 and 4.")
        max_selected = payload.get("max_selected", 3)
        if not isinstance(max_selected, int) or max_selected < 0 or max_selected > 20:
            raise BridgeProtocolError("scene_snapshot max_selected must be an integer between 0 and 20.")
        include_viewport = payload.get("include_viewport", False)
        if not isinstance(include_viewport, bool):
            raise BridgeProtocolError("scene_snapshot include_viewport must be boolean when provided.")
        _validate_viewport_payload(payload, "scene_snapshot")

    if normalized == "viewport_screenshot":
        _validate_viewport_payload(payload, "viewport_screenshot")

    if normalized in {"validate_plan", "review_plan", "run_plan", "verify_plan"}:
        steps = payload.get("steps")
        if not isinstance(steps, list):
            raise BridgeProtocolError("%s requires a steps list." % normalized)
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise BridgeProtocolError("%s step %s must be a JSON object." % (normalized, index))
            if not isinstance(step.get("command"), str) or not step["command"].strip():
                raise BridgeProtocolError("%s step %s requires a command string." % (normalized, index))
            step_payload = step.get("payload", {})
            if step_payload is None:
                step_payload = {}
            if not isinstance(step_payload, dict):
                raise BridgeProtocolError("%s step %s payload must be a JSON object." % (normalized, index))
            if normalized == "run_plan":
                step_command = normalize_command(step.get("command"))
                if step_command == "run_plan":
                    raise BridgeProtocolError("run_plan cannot contain nested run_plan steps.")
                validate_command(step_command, step_payload)
        if normalized == "verify_plan":
            for key in ("validation", "run_result"):
                if key in payload and not isinstance(payload.get(key), dict):
                    raise BridgeProtocolError("verify_plan %s must be an object when provided." % key)
        if normalized == "run_plan":
            continue_on_error = payload.get("continue_on_error", False)
            if not isinstance(continue_on_error, bool):
                raise BridgeProtocolError("run_plan continue_on_error must be boolean when provided.")
            undo_label = payload.get("undo_label", "Blib Bridge plan")
            if not isinstance(undo_label, str) or len(undo_label) > 120:
                raise BridgeProtocolError("run_plan undo_label must be a string up to 120 characters.")

    if normalized in {"upstream", "downstream"}:
        depth = payload.get("depth", 4)
        if not isinstance(depth, int) or depth < 0 or depth > 12:
            raise BridgeProtocolError("%s depth must be an integer between 0 and 12." % normalized)

    if normalized == "rpc_log":
        limit = payload.get("limit", 50)
        if not isinstance(limit, int) or limit < 0 or limit > 200:
            raise BridgeProtocolError("rpc_log limit must be an integer between 0 and 200.")

    if normalized == "find_nodes":
        _require_abs_path(payload, "root", normalized)
        for key in ("name", "type", "category", "path"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise BridgeProtocolError("find_nodes %s filter must be a string when provided." % key)
        limit = payload.get("limit", 100)
        if not isinstance(limit, int) or limit < 1 or limit > 500:
            raise BridgeProtocolError("find_nodes limit must be an integer between 1 and 500.")

    if normalized == "edit_mode":
        enabled = payload.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise BridgeProtocolError("edit_mode enabled must be a boolean when provided.")

    if normalized == "create_node":
        _require_abs_path(payload, "parent", normalized)
        if not isinstance(payload.get("type"), str) or not payload["type"].strip():
            raise BridgeProtocolError("create_node requires a node type.")
        name = payload.get("name")
        if name is not None and not _valid_node_name(name):
            raise BridgeProtocolError("create_node name must be a simple Houdini node name.")
    elif normalized == "rename_node":
        _require_abs_path(payload, "node", normalized)
        if not _valid_node_name(payload.get("name")):
            raise BridgeProtocolError("rename_node requires a simple Houdini node name.")
        unique = payload.get("unique", True)
        if not isinstance(unique, bool):
            raise BridgeProtocolError("rename_node unique must be boolean when provided.")
    elif normalized == "set_node_color":
        _require_abs_path(payload, "node", normalized)
        _require_rgb(payload, "color", normalized)
    elif normalized == "bypass_node":
        _require_abs_path(payload, "node", normalized)
        if not isinstance(payload.get("bypass"), bool):
            raise BridgeProtocolError("bypass_node bypass must be boolean.")
    elif normalized == "create_network_box":
        _require_abs_path(payload, "parent", normalized)
        if not _valid_node_name(payload.get("name")):
            raise BridgeProtocolError("create_network_box requires a simple Houdini name.")
        comment = payload.get("comment", "")
        if not isinstance(comment, str) or len(comment) > 2000:
            raise BridgeProtocolError("create_network_box comment must be a string up to 2000 characters.")
        _require_abs_path_list(payload, "nodes", normalized, required=False, limit=100)
        if "color" in payload:
            _require_rgb(payload, "color", normalized)
    elif normalized == "create_sticky_note":
        _require_abs_path(payload, "parent", normalized)
        text = payload.get("text")
        if not isinstance(text, str) or len(text) > 4000:
            raise BridgeProtocolError("create_sticky_note requires text up to 4000 characters.")
        name = payload.get("name")
        if name is not None and not _valid_node_name(name):
            raise BridgeProtocolError("create_sticky_note name must be a simple Houdini name.")
        if ("x" in payload) != ("y" in payload):
            raise BridgeProtocolError("create_sticky_note requires both x and y when positioning.")
        if "x" in payload:
            _require_number(payload, "x", normalized)
            _require_number(payload, "y", normalized)
        if "color" in payload:
            _require_rgb(payload, "color", normalized)
    elif normalized == "set_parm":
        _require_abs_path(payload, "node", normalized)
        if not _valid_node_name(payload.get("parm")):
            raise BridgeProtocolError("set_parm requires a simple parameter name.")
    elif normalized == "set_parm_any":
        _require_abs_path(payload, "node", normalized)
        parms = payload.get("parms")
        if not isinstance(parms, list) or not parms or len(parms) > 20:
            raise BridgeProtocolError("set_parm_any requires 1-20 candidate parameter names.")
        for parm_name in parms:
            if not _valid_node_name(parm_name):
                raise BridgeProtocolError("set_parm_any parameter candidates must be simple names.")
        required = payload.get("required", False)
        if not isinstance(required, bool):
            raise BridgeProtocolError("set_parm_any required must be boolean when provided.")
    elif normalized == "batch_set_parms":
        _require_abs_path(payload, "node", normalized)
        values = payload.get("values")
        if not isinstance(values, dict) or not values:
            raise BridgeProtocolError("batch_set_parms requires a non-empty values object.")
        if len(values) > 50:
            raise BridgeProtocolError("batch_set_parms supports at most 50 parameters.")
        for parm_name in values:
            if not _valid_node_name(parm_name):
                raise BridgeProtocolError("batch_set_parms parameter names must be simple names.")
        required = payload.get("required", True)
        if not isinstance(required, bool):
            raise BridgeProtocolError("batch_set_parms required must be boolean when provided.")
    elif normalized in {"apply_parm_profile", "probe_parm_profile"}:
        _require_abs_path(payload, "node", normalized)
        profile = payload.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            raise BridgeProtocolError("%s requires a profile name." % normalized)
        values = payload.get("values", {})
        if not isinstance(values, dict):
            raise BridgeProtocolError("%s values must be an object when provided." % normalized)
        strict = payload.get("strict", False)
        if not isinstance(strict, bool):
            raise BridgeProtocolError("%s strict must be boolean when provided." % normalized)
    elif normalized == "set_comment":
        _require_abs_path(payload, "node", normalized)
        comment = payload.get("comment")
        if not isinstance(comment, str) or len(comment) > 2000:
            raise BridgeProtocolError("set_comment requires a comment string up to 2000 characters.")
    elif normalized == "set_flags":
        _require_abs_path(payload, "node", normalized)
        for key in ("display", "render"):
            value = payload.get(key)
            if value is not None and not isinstance(value, bool):
                raise BridgeProtocolError("set_flags %s must be boolean when provided." % key)
        if "display" not in payload and "render" not in payload:
            raise BridgeProtocolError("set_flags requires display or render.")
    elif normalized == "set_position":
        _require_abs_path(payload, "node", normalized)
        _require_number(payload, "x", normalized)
        _require_number(payload, "y", normalized)
    elif normalized == "ensure_parm":
        _require_abs_path(payload, "node", normalized)
        if not _valid_node_name(payload.get("name")):
            raise BridgeProtocolError("ensure_parm requires a simple parameter name.")
        parm_type = payload.get("type")
        if parm_type not in {"float", "int"}:
            raise BridgeProtocolError("ensure_parm type must be float or int.")
        label = payload.get("label", payload.get("name"))
        if not isinstance(label, str) or len(label) > 80:
            raise BridgeProtocolError("ensure_parm label must be a short string.")
        _require_number(payload, "default", normalized)
    elif normalized == "connect":
        _require_abs_path(payload, "src", normalized)
        _require_abs_path(payload, "dst", normalized)
        input_index = payload.get("input_index", 0)
        if not isinstance(input_index, int) or input_index < 0 or input_index > 99:
            raise BridgeProtocolError("connect input_index must be an integer between 0 and 99.")
    elif normalized == "set_input":
        _require_abs_path(payload, "dst", normalized)
        input_index = payload.get("input_index")
        if not isinstance(input_index, int) or input_index < 0 or input_index > 99:
            raise BridgeProtocolError("set_input input_index must be an integer between 0 and 99.")
        clear = payload.get("clear", False)
        if not isinstance(clear, bool):
            raise BridgeProtocolError("set_input clear must be boolean when provided.")
        if clear:
            if payload.get("src") is not None:
                raise BridgeProtocolError("set_input cannot include src when clear is true.")
        else:
            _require_abs_path(payload, "src", normalized)
    elif normalized == "disconnect":
        _require_abs_path(payload, "node", normalized)
        all_inputs = payload.get("all", False)
        if not isinstance(all_inputs, bool):
            raise BridgeProtocolError("disconnect all must be boolean when provided.")
        input_index = payload.get("input_index")
        if input_index is not None and (not isinstance(input_index, int) or input_index < 0 or input_index > 99):
            raise BridgeProtocolError("disconnect input_index must be an integer between 0 and 99.")
        if payload.get("src") is not None:
            _require_abs_path(payload, "src", normalized)
        if all_inputs and (input_index is not None or payload.get("src") is not None):
            raise BridgeProtocolError("disconnect all cannot be combined with src or input_index.")
        if not all_inputs and input_index is None and payload.get("src") is None:
            raise BridgeProtocolError("disconnect requires all, src, or input_index.")
    elif normalized in {"move_node", "copy_node"}:
        _require_abs_path(payload, "node", normalized)
        _require_abs_path(payload, "parent", normalized)
        name = payload.get("name")
        if name is not None and not _valid_node_name(name):
            raise BridgeProtocolError("%s name must be a simple Houdini node name." % normalized)
        unique = payload.get("unique", True)
        if normalized == "copy_node" and not isinstance(unique, bool):
            raise BridgeProtocolError("copy_node unique must be boolean when provided.")
    elif normalized == "set_node_shape":
        _require_abs_path(payload, "node", normalized)
        shape = payload.get("shape")
        if not isinstance(shape, str) or not shape.strip() or len(shape) > 80 or any(ch in shape for ch in "/\\\r\n\t"):
            raise BridgeProtocolError("set_node_shape requires a simple shape name.")
    elif normalized == "replace_node":
        _require_abs_path(payload, "node", normalized)
        if not isinstance(payload.get("type"), str) or not payload["type"].strip():
            raise BridgeProtocolError("replace_node requires a node type.")
        name = payload.get("name")
        if name is not None and not _valid_node_name(name):
            raise BridgeProtocolError("replace_node name must be a simple Houdini node name.")
        for key in ("reconnect_inputs", "reconnect_outputs", "delete_old", "confirm"):
            value = payload.get(key, False if key in {"delete_old", "confirm"} else True)
            if not isinstance(value, bool):
                raise BridgeProtocolError("replace_node %s must be boolean when provided." % key)
        if payload.get("delete_old", False) and payload.get("confirm") is not True:
            raise BridgeProtocolError("replace_node delete_old requires confirm=true.")
    elif normalized == "delete_node":
        _require_abs_path(payload, "node", normalized)
        if payload.get("confirm") is not True:
            raise BridgeProtocolError("delete_node requires confirm=true.")
        delete_contents = payload.get("delete_contents", False)
        if not isinstance(delete_contents, bool):
            raise BridgeProtocolError("delete_node delete_contents must be boolean when provided.")
    elif normalized in {"layout", "select"}:
        _require_abs_path(payload, "path", normalized)


def make_request(command: str, payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
    normalized = normalize_command(command)
    body = {
        "version": BRIDGE_VERSION,
        "request_id": new_request_id(),
        "command": normalized,
        "payload": payload or {},
    }
    if token:
        body["token"] = token
    validate_command(normalized, body["payload"])
    return body


def command_manifest() -> dict[str, Any]:
    commands: dict[str, Any] = {}
    for name in sorted(READ_COMMANDS | EDIT_COMMANDS):
        metadata = dict(COMMANDS[name])
        metadata["payload_schema"] = COMMAND_PAYLOAD_SCHEMAS.get(name, {"type": "object"})
        metadata["result_schema"] = COMMAND_RESULT_SCHEMAS.get(name, DEFAULT_RESULT_SCHEMA)
        metadata["exposure"] = command_exposure(name)
        metadata["mcp_tool_name"] = "houdini_%s" % name
        if name in DIRECT_EDIT_COMMANDS:
            metadata["verification"] = direct_edit_verification_contract(name)
        commands[name] = metadata
    return {
        "version": BRIDGE_VERSION,
        "commands": commands,
        "danger_commands": sorted(DANGER_COMMANDS),
        "direct_edit_commands": sorted(DIRECT_EDIT_COMMANDS),
        "plan_required_edit_commands": sorted(PLAN_REQUIRED_EDIT_COMMANDS),
        "safety_policy": safety_policy(),
    }


def safety_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "summary": "Local-only bridge control with read/default, edit-mode gate, direct low-risk edits, and plan-required high-risk edits.",
        "transport": {
            "host": "127.0.0.1",
            "session_file_required": True,
            "token_required": True,
            "mcp_imports_hou": False,
            "mcp_calls_bridge_rpc_only": True,
        },
        "edit_gate": {
            "default_mode": "read",
            "houdini_edit_mode_required": True,
            "direct_edit_commands": sorted(DIRECT_EDIT_COMMANDS),
            "direct_edit_verification": direct_edit_verification_contracts(),
            "plan_required_edit_commands": sorted(PLAN_REQUIRED_EDIT_COMMANDS),
            "plan_transaction_command": "run_plan",
            "required_plan_flow": ["review_plan", "validate_plan", "run_plan", "verify_plan"],
        },
        "blocked": {
            "danger_commands": sorted(DANGER_COMMANDS),
            "rules": [
                "No shell command execution.",
                "No arbitrary Python or HScript execution.",
                "No HIP save/load operations.",
                "No arbitrary file writes or file opens.",
                "No package installation.",
            ],
        },
        "evidence": {
            "required_for_important_edits": [
                "snapshot_before",
                "plan",
                "review",
                "validation",
                "run_result",
                "verification",
                "rpc_log",
                "summary",
            ],
            "recommended": ["snapshot_after", "proof_report", "evidence_manifest", "rollback_plan", "visual_evidence"],
            "visual_is_supporting_evidence": True,
        },
    }


def direct_edit_verification_contract(command: str) -> dict[str, Any]:
    normalized = normalize_command(command)
    contract = DIRECT_EDIT_VERIFICATION_CONTRACTS.get(normalized, {})
    read_tools = [str(tool) for tool in contract.get("read_tools", []) if isinstance(tool, str)]
    success_criteria = [str(item) for item in contract.get("success_criteria", []) if isinstance(item, str)]
    return {
        "version": 1,
        "command": normalized,
        "requires_readback": normalized in DIRECT_EDIT_COMMANDS,
        "may_report_success_from_rpc_ok": False,
        "read_tools": read_tools,
        "mcp_read_tools": ["houdini_%s" % tool for tool in read_tools],
        "success_criteria": success_criteria,
        "note": "Direct edit RPC success is not final proof; read back changed Houdini state before reporting task success.",
    }


def direct_edit_verification_contracts() -> dict[str, Any]:
    return {command: direct_edit_verification_contract(command) for command in sorted(DIRECT_EDIT_COMMANDS)}


def command_exposure(command: str) -> str:
    normalized = normalize_command(command)
    if normalized in READ_COMMANDS:
        return "read"
    if normalized in DIRECT_EDIT_COMMANDS:
        return "direct_edit"
    if normalized == "run_plan" or normalized in PLAN_REQUIRED_EDIT_COMMANDS:
        return "plan_required"
    return "blocked"


def ok_response(request_id: str, command: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "version": BRIDGE_VERSION,
        "request_id": request_id,
        "command": normalize_command(command),
        "result": result or {},
        "error": None,
        "timestamp": time.time(),
    }


def error_response(
    request_id: str | None,
    command: str | None,
    message: str,
    code: str = "bridge_error",
) -> dict[str, Any]:
    return {
        "ok": False,
        "version": BRIDGE_VERSION,
        "request_id": request_id or "",
        "command": normalize_command(command or ""),
        "result": {},
        "error": {
            "code": code,
            "message": str(message),
        },
        "timestamp": time.time(),
    }


def _require_abs_path(payload: dict[str, Any], key: str, command: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.startswith("/"):
        raise BridgeProtocolError("%s requires an absolute Houdini path for %s." % (command, key))


def _valid_node_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return all(ch.isalnum() or ch == "_" for ch in value)


def _valid_filename_prefix(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(ch.isalnum() or ch in "._-" for ch in value)


def _require_number(payload: dict[str, Any], key: str, command: str) -> None:
    value = payload.get(key)
    if not isinstance(value, (int, float)):
        raise BridgeProtocolError("%s requires numeric %s." % (command, key))


def _require_abs_path_list(
    payload: dict[str, Any],
    key: str,
    command: str,
    required: bool = True,
    limit: int = 100,
) -> None:
    values = payload.get(key)
    if values is None:
        if required:
            raise BridgeProtocolError("%s requires %s." % (command, key))
        return
    if not isinstance(values, list) or len(values) > limit:
        raise BridgeProtocolError("%s %s must be a list with at most %s items." % (command, key, limit))
    for value in values:
        if not isinstance(value, str) or not value.startswith("/"):
            raise BridgeProtocolError("%s %s entries must be absolute Houdini node paths." % (command, key))


def _require_rgb(payload: dict[str, Any], key: str, command: str) -> None:
    color = payload.get(key)
    if not isinstance(color, list) or len(color) != 3:
        raise BridgeProtocolError("%s %s must be a three-number RGB list." % (command, key))
    for value in color:
        if not isinstance(value, (int, float)) or value < 0 or value > 1:
            raise BridgeProtocolError("%s %s values must be numbers between 0 and 1." % (command, key))


def _validate_viewport_payload(payload: dict[str, Any], command: str) -> None:
    width = payload.get("width", 1280)
    height = payload.get("height", 720)
    if not isinstance(width, int) or width < 64 or width > 4096:
        raise BridgeProtocolError("%s width must be an integer between 64 and 4096." % command)
    if not isinstance(height, int) or height < 64 or height > 4096:
        raise BridgeProtocolError("%s height must be an integer between 64 and 4096." % command)
    prefix = payload.get("prefix", "viewport")
    if not isinstance(prefix, str) or not _valid_filename_prefix(prefix):
        raise BridgeProtocolError("%s prefix must contain only letters, numbers, dot, dash, or underscore." % command)
