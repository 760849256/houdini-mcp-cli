"""Command line client for the Blib Houdini Bridge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON_DIR = os.path.join(ROOT, "scripts", "python")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from blib_hou_bridge import auth, dynamics_profiles, protocol, workflow_templates  # noqa: E402


HEALTH_CACHE_TTL_SECONDS = 10.0
ROLLBACK_REQUIRED_FLOW = ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blib-hou", description="Client for the local Blib Houdini Bridge.")
    parser.add_argument("--session", default=None, help="Path to a bridge session JSON file.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Diagnose session loading and bridge RPC health.")
    subparsers.add_parser("status", help="Check whether the bridge is reachable.")
    subparsers.add_parser("manifest", help="Read the bridge command manifest.")
    subparsers.add_parser("recipe-manifest", help="Read bridge-native workflow recipe contracts and presets.")
    subparsers.add_parser("profile-manifest", help="Read dynamics parameter profile definitions.")
    subparsers.add_parser("context", help="Read Houdini session context.")
    subparsers.add_parser("selected", help="Read selected Houdini nodes.")
    snapshot_parser = subparsers.add_parser("scene-snapshot", help="Read a compact scene context bundle.")
    snapshot_parser.add_argument("--path", default=None, help="Network path to summarize. Defaults to the current network.")
    snapshot_parser.add_argument("--trace-depth", type=int, default=1, help="Selected-node trace depth, 0-4.")
    snapshot_parser.add_argument("--max-selected", type=int, default=3, help="Maximum selected nodes to detail, 0-20.")
    snapshot_parser.add_argument("--include-viewport", action="store_true", help="Capture the current Scene Viewer viewport.")
    snapshot_parser.add_argument("--width", type=int, default=1280, help="Viewport screenshot width, 64-4096.")
    snapshot_parser.add_argument("--height", type=int, default=720, help="Viewport screenshot height, 64-4096.")
    snapshot_parser.add_argument("--prefix", default="scene_snapshot", help="Safe screenshot filename prefix.")
    find_parser = subparsers.add_parser("find-nodes", help="Find nodes below a root path.")
    find_parser.add_argument("--root", default="/obj", help="Root network path.")
    find_parser.add_argument("--name", default=None, help="Case-insensitive node name contains filter.")
    find_parser.add_argument("--type", default=None, dest="node_type", help="Case-insensitive node type contains filter.")
    find_parser.add_argument("--category", default=None, help="Case-insensitive category contains filter.")
    find_parser.add_argument("--path", default=None, dest="path_filter", help="Case-insensitive path contains filter.")
    find_parser.add_argument("--limit", type=int, default=100, help="Maximum matches, 1-500.")
    log_parser = subparsers.add_parser("rpc-log", help="Read recent bridge RPC request history.")
    log_parser.add_argument("--limit", type=int, default=50, help="Number of recent events, 0-200.")
    node_info_parser = subparsers.add_parser("node-info", help="Read one Houdini node.")
    node_info_parser.add_argument("path", help="Absolute Houdini node path.")
    node_parms_parser = subparsers.add_parser("node-parms", help="Read parameters for one Houdini node.")
    node_parms_parser.add_argument("path", help="Absolute Houdini node path.")
    screenshot_parser = subparsers.add_parser("viewport-screenshot", help="Capture the current Scene Viewer viewport.")
    screenshot_parser.add_argument("--width", type=int, default=1280, help="Screenshot width, 64-4096.")
    screenshot_parser.add_argument("--height", type=int, default=720, help="Screenshot height, 64-4096.")
    screenshot_parser.add_argument("--prefix", default="viewport", help="Safe output filename prefix.")
    network_parser = subparsers.add_parser("network", help="Read direct children and wiring inside a network.")
    network_parser.add_argument("path", help="Absolute Houdini network path.")
    upstream_parser = subparsers.add_parser("upstream", help="Trace input dependencies for a node.")
    upstream_parser.add_argument("path", help="Absolute Houdini node path.")
    upstream_parser.add_argument("--depth", type=int, default=4, help="Traversal depth, 0-12.")
    downstream_parser = subparsers.add_parser("downstream", help="Trace output consumers for a node.")
    downstream_parser.add_argument("path", help="Absolute Houdini node path.")
    downstream_parser.add_argument("--depth", type=int, default=4, help="Traversal depth, 0-12.")
    edit_mode_parser = subparsers.add_parser("edit-mode", help="Read or change the bridge edit gate.")
    edit_mode_parser.add_argument("state", choices=("status", "on", "off"), help="Edit gate state.")
    create_parser = subparsers.add_parser("create-node", help="Create one node when bridge edit mode is enabled.")
    create_parser.add_argument("--parent", required=True, help="Parent network path.")
    create_parser.add_argument("--type", required=True, dest="node_type", help="Houdini node type.")
    create_parser.add_argument("--name", default=None, help="Optional node name.")
    rename_parser = subparsers.add_parser("rename-node", help="Rename one node when bridge edit mode is enabled.")
    rename_parser.add_argument("node", help="Node path.")
    rename_parser.add_argument("name", help="New simple node name.")
    rename_parser.add_argument("--no-unique", action="store_true", help="Do not ask Houdini to uniquify conflicting names.")
    color_parser = subparsers.add_parser("set-node-color", help="Set one node color when bridge edit mode is enabled.")
    color_parser.add_argument("node", help="Node path.")
    color_parser.add_argument("r", type=float, help="Red channel, 0-1.")
    color_parser.add_argument("g", type=float, help="Green channel, 0-1.")
    color_parser.add_argument("b", type=float, help="Blue channel, 0-1.")
    bypass_parser = subparsers.add_parser("bypass-node", help="Set one node bypass state when bridge edit mode is enabled.")
    bypass_parser.add_argument("node", help="Node path.")
    bypass_parser.add_argument("state", choices=("on", "off"), help="Bypass state.")
    box_parser = subparsers.add_parser("create-network-box", help="Create one network box when bridge edit mode is enabled.")
    box_parser.add_argument("--parent", required=True, help="Parent network path.")
    box_parser.add_argument("--name", required=True, help="Simple network box name.")
    box_parser.add_argument("--comment", default="", help="Optional network box comment.")
    box_parser.add_argument("--node", action="append", dest="nodes", default=[], help="Node path to add; repeatable.")
    box_parser.add_argument("--color", nargs=3, type=float, default=None, metavar=("R", "G", "B"), help="Optional RGB color, 0-1.")
    note_parser = subparsers.add_parser("create-sticky-note", help="Create one sticky note when bridge edit mode is enabled.")
    note_parser.add_argument("--parent", required=True, help="Parent network path.")
    note_parser.add_argument("--text", required=True, help="Sticky note text.")
    note_parser.add_argument("--name", default=None, help="Optional simple sticky note name.")
    note_parser.add_argument("--x", type=float, default=None, help="Optional network editor X position.")
    note_parser.add_argument("--y", type=float, default=None, help="Optional network editor Y position.")
    note_parser.add_argument("--color", nargs=3, type=float, default=None, metavar=("R", "G", "B"), help="Optional RGB color, 0-1.")
    set_parm_parser = subparsers.add_parser("set-parm", help="Set one parameter when bridge edit mode is enabled.")
    set_parm_parser.add_argument("node", help="Node path.")
    set_parm_parser.add_argument("parm", help="Parameter name.")
    set_parm_parser.add_argument("value", help="JSON value, number, bool, or string.")
    set_parm_any_parser = subparsers.add_parser("set-parm-any", help="Set the first existing parameter from candidates when edit mode is enabled.")
    set_parm_any_parser.add_argument("node", help="Node path.")
    set_parm_any_parser.add_argument("value", help="JSON value, number, bool, or string.")
    set_parm_any_parser.add_argument("--parm", action="append", dest="parms", required=True, help="Candidate parameter name; repeatable.")
    set_parm_any_parser.add_argument("--required", action="store_true", help="Fail if none of the candidate parameters exists.")
    batch_set_parms_parser = subparsers.add_parser("batch-set-parms", help="Set several parameters on one node when edit mode is enabled.")
    batch_set_parms_parser.add_argument("node", help="Node path.")
    batch_set_parms_parser.add_argument("--value", action="append", dest="parm_values", required=True, help="Parameter assignment as name=JSON_VALUE; repeatable.")
    batch_set_parms_parser.add_argument("--optional", action="store_true", help="Skip missing parameters instead of failing.")
    profile_parser = subparsers.add_parser("apply-parm-profile", help="Apply a dynamics parameter profile to matching parameters when edit mode is enabled.")
    profile_parser.add_argument("node", help="Node path.")
    profile_parser.add_argument("profile", choices=dynamics_profiles.PROFILE_NAMES, help="Parameter profile name.")
    profile_parser.add_argument("--value", action="append", dest="profile_values", default=[], help="Logical parameter override as name=JSON_VALUE; repeatable.")
    profile_parser.add_argument("--strict", action="store_true", help="Fail if profile candidates are missing.")
    probe_profile_parser = subparsers.add_parser("probe-parm-profile", help="Probe a dynamics parameter profile without changing the scene.")
    probe_profile_parser.add_argument("node", help="Node path.")
    probe_profile_parser.add_argument("profile", choices=dynamics_profiles.PROFILE_NAMES, help="Parameter profile name.")
    probe_profile_parser.add_argument("--value", action="append", dest="profile_values", default=[], help="Logical parameter override as name=JSON_VALUE; repeatable.")
    probe_profile_parser.add_argument("--strict", action="store_true", help="Report missing candidates as unresolved.")
    set_comment_parser = subparsers.add_parser("set-comment", help="Set one node comment when bridge edit mode is enabled.")
    set_comment_parser.add_argument("node", help="Node path.")
    set_comment_parser.add_argument("comment", help="Comment text.")
    set_flags_parser = subparsers.add_parser("set-flags", help="Set node display or render flags when edit mode is enabled.")
    set_flags_parser.add_argument("node", help="Node path.")
    set_flags_parser.add_argument("--display", choices=("on", "off"), default=None, help="Display flag.")
    set_flags_parser.add_argument("--render", choices=("on", "off"), default=None, help="Render flag.")
    set_position_parser = subparsers.add_parser("set-position", help="Set node position when bridge edit mode is enabled.")
    set_position_parser.add_argument("node", help="Node path.")
    set_position_parser.add_argument("x", type=float, help="Network editor X position.")
    set_position_parser.add_argument("y", type=float, help="Network editor Y position.")
    ensure_parm_parser = subparsers.add_parser("ensure-parm", help="Create one simple spare parameter when edit mode is enabled.")
    ensure_parm_parser.add_argument("node", help="Node path.")
    ensure_parm_parser.add_argument("name", help="Parameter name.")
    ensure_parm_parser.add_argument("--type", choices=("float", "int"), required=True, dest="parm_type", help="Parameter type.")
    ensure_parm_parser.add_argument("--label", default=None, help="Parameter label.")
    ensure_parm_parser.add_argument("--default", type=float, required=True, help="Default value.")
    connect_parser = subparsers.add_parser("connect", help="Connect nodes when bridge edit mode is enabled.")
    connect_parser.add_argument("src", help="Source node path.")
    connect_parser.add_argument("dst", help="Destination node path.")
    connect_parser.add_argument("--input-index", type=int, default=0, help="Destination input index.")
    set_input_parser = subparsers.add_parser("set-input", help="Set or clear one destination input when edit mode is enabled.")
    set_input_parser.add_argument("dst", help="Destination node path.")
    set_input_parser.add_argument("input_index", type=int, help="Destination input index.")
    set_input_group = set_input_parser.add_mutually_exclusive_group(required=True)
    set_input_group.add_argument("--src", default=None, help="Source node path.")
    set_input_group.add_argument("--clear", action="store_true", help="Clear this input.")
    disconnect_parser = subparsers.add_parser("disconnect", help="Disconnect node inputs when edit mode is enabled.")
    disconnect_parser.add_argument("node", help="Destination node path.")
    disconnect_group = disconnect_parser.add_mutually_exclusive_group(required=True)
    disconnect_group.add_argument("--input-index", type=int, default=None, help="Disconnect this input index.")
    disconnect_group.add_argument("--src", default=None, help="Disconnect inputs connected from this source node.")
    disconnect_group.add_argument("--all", action="store_true", help="Disconnect all inputs.")
    move_node_parser = subparsers.add_parser("move-node", help="Move one node to another parent network when edit mode is enabled.")
    move_node_parser.add_argument("node", help="Node path.")
    move_node_parser.add_argument("parent", help="Destination parent network path.")
    move_node_parser.add_argument("--name", default=None, help="Optional simple node name after moving.")
    copy_node_parser = subparsers.add_parser("copy-node", help="Copy one node to a parent network when edit mode is enabled.")
    copy_node_parser.add_argument("node", help="Node path.")
    copy_node_parser.add_argument("parent", help="Destination parent network path.")
    copy_node_parser.add_argument("--name", default=None, help="Optional simple copy name.")
    copy_node_parser.add_argument("--no-unique", action="store_true", help="Do not ask Houdini to uniquify conflicting names.")
    shape_parser = subparsers.add_parser("set-node-shape", help="Set one node shape when bridge edit mode is enabled.")
    shape_parser.add_argument("node", help="Node path.")
    shape_parser.add_argument("shape", help="Houdini network editor node shape name.")
    replace_parser = subparsers.add_parser("replace-node", help="Create a replacement sibling node when edit mode is enabled.")
    replace_parser.add_argument("node", help="Node path to replace.")
    replace_parser.add_argument("--type", required=True, dest="node_type", help="Replacement Houdini node type.")
    replace_parser.add_argument("--name", default=None, help="Optional replacement node name.")
    replace_parser.add_argument("--no-reconnect-inputs", action="store_true", help="Do not connect the old inputs to the replacement.")
    replace_parser.add_argument("--no-reconnect-outputs", action="store_true", help="Do not reconnect downstream nodes to the replacement.")
    replace_parser.add_argument("--delete-old", action="store_true", help="Delete the old node after creating the replacement.")
    replace_parser.add_argument("--confirm", action="store_true", help="Required when --delete-old is used.")
    delete_parser = subparsers.add_parser("delete-node", help="Delete one explicitly confirmed node when edit mode is enabled.")
    delete_parser.add_argument("node", help="Node path to delete.")
    delete_parser.add_argument("--confirm", action="store_true", help="Required confirmation for deletion.")
    delete_parser.add_argument("--delete-contents", action="store_true", help="Record that nested contents may be discarded.")
    layout_parser = subparsers.add_parser("layout", help="Layout one network when bridge edit mode is enabled.")
    layout_parser.add_argument("path", help="Network path.")
    select_parser = subparsers.add_parser("select", help="Select one node when bridge edit mode is enabled.")
    select_parser.add_argument("path", help="Node path.")
    validate_parser = subparsers.add_parser("validate-plan", help="Validate a JSON list of bridge commands without scene edits.")
    validate_parser.add_argument("path", help="Path to a commands JSON file.")
    review_parser = subparsers.add_parser("review-plan", help="Review a JSON list of bridge commands before running it.")
    review_parser.add_argument("path", help="Path to a commands JSON file.")
    verify_parser = subparsers.add_parser("verify-plan", help="Verify a command list against the current post-run scene state.")
    verify_parser.add_argument("path", help="Path to a commands JSON file.")
    verify_parser.add_argument("--validation", default=None, help="Optional validation.json captured before running.")
    verify_parser.add_argument("--run-result", default=None, help="Optional run_result.json captured from workflow run.")
    run_parser = subparsers.add_parser("run", help="Run a JSON list of bridge commands.")
    run_parser.add_argument("path", help="Path to a commands JSON file.")
    run_parser.add_argument("--continue-on-error", action="store_true", help="Continue running commands after a failed step.")
    workflow_parser = subparsers.add_parser("workflow", help="Create and run reproducible bridge JSON workflows.")
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command", required=True)
    workflow_start = workflow_subparsers.add_parser("start", help="Create a workflow evidence directory and empty plan.")
    workflow_start.add_argument("name", help="Workflow name. Creates .blib_hou_workflows/NAME.")
    workflow_start.add_argument("--path", default="/obj", help="Network path for the initial scene snapshot.")
    workflow_start.add_argument("--template", choices=workflow_templates.TEMPLATE_NAMES, default=None, help="Generate plan.json from a bridge workflow template.")
    workflow_start.add_argument("--input", dest="template_input", default=None, help="Template input node path. Defaults to a single selected node in the snapshot.")
    workflow_start.add_argument("--preset", choices=("preview", "production"), default="preview", help="Template preset.")
    workflow_start.add_argument("--output-name", default=None, help="Template output null name.")
    workflow_start.add_argument("--name", dest="template_name", default=None, help="Template node name prefix.")
    workflow_start.add_argument("--cache-path", default=None, help="Template cache path for cache/filecache nodes.")
    workflow_start.add_argument("--render-path", default=None, help="Template render output path for Solaris/Karma templates.")
    workflow_start.add_argument("--camera-path", default=None, help="Template camera primitive or node path for Solaris/Karma templates.")
    workflow_start.add_argument("--resolution", default=None, help="Template render resolution as WIDTHxHEIGHT for Solaris/Karma templates.")
    workflow_start.add_argument("--samples", type=int, default=None, help="Template render samples for Solaris/Karma templates.")
    workflow_start.add_argument("--lopnet-name", default=None, help="Template LOP network name for Solaris/Karma templates.")
    workflow_start.add_argument("--no-solver", action="store_true", help="Omit optional solver nodes in supported templates.")
    workflow_start.add_argument("--cache", action="store_true", help="Include optional cache nodes in supported templates.")
    workflow_start.add_argument("--fuse-distance", type=float, default=None, help="Override cleanup fuse distance.")
    workflow_start.add_argument("--voxel-size", type=float, default=None, help="Override VDB voxel size.")
    workflow_start.add_argument("--particle-size", type=float, default=None, help="Override grains particle size.")
    workflow_start.add_argument("--substeps", type=int, default=None, help="Override solver substeps in supported dynamics templates.")
    workflow_start.add_argument("--start-frame", type=int, default=None, help="Override solver start frame in supported dynamics templates.")
    workflow_start.add_argument("--constraint-strength", type=float, default=None, help="Override RBD constraint strength.")
    workflow_start.add_argument("--dissipation", type=float, default=None, help="Override Pyro dissipation.")
    workflow_review = workflow_subparsers.add_parser("review", help="Validate and review a workflow plan.")
    workflow_review.add_argument("workflow_dir", help="Workflow directory containing plan.json.")
    workflow_run = workflow_subparsers.add_parser("run", help="Review, optionally enable edit mode, run, and collect evidence.")
    workflow_run.add_argument("workflow_dir", help="Workflow directory containing plan.json.")
    workflow_run.add_argument("--enable-edit-mode", action="store_true", help="Explicitly allow workflow run to enable bridge edit mode.")
    workflow_run.add_argument("--evidence", choices=("minimal", "standard", "full"), default="standard", help="Evidence collection level after the run.")
    workflow_rollback = workflow_subparsers.add_parser("rollback", help="Draft rollback_plan.json from workflow evidence without executing it.")
    workflow_rollback.add_argument("workflow_dir", help="Workflow directory containing review/evidence artifacts.")
    workflow_report = workflow_subparsers.add_parser("report", help="Generate summary.md from existing workflow artifacts.")
    workflow_report.add_argument("workflow_dir", help="Workflow directory to summarize.")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        response = _doctor(args.session)
        _print_json(response)
        return 0 if response.get("ok") else 2

    if args.command == "workflow" and args.workflow_command in {"report", "rollback"}:
        response = _workflow_rollback(args.workflow_dir) if args.workflow_command == "rollback" else _workflow_report(args.workflow_dir)
        _print_json(response)
        return 0 if response.get("ok") else 1

    session = auth.load_session(args.session)
    if session is None:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": "offline",
                    "message": "No active Blib Houdini Bridge session was found.",
                },
            }
        )
        return 2

    if args.command == "run":
        response = _run_commands_file(session, args.path, continue_on_error=args.continue_on_error)
        _print_json(response)
        return 0 if response.get("ok") else 1
    if args.command == "workflow":
        response = _workflow_command(session, args)
        _print_json(response)
        return 0 if response.get("ok") else 1

    command = _protocol_command(args.command)
    payload = {}
    if command in {"node_info", "node_parms", "network", "upstream", "downstream"}:
        payload["path"] = args.path
    if command == "rpc_log":
        payload["limit"] = args.limit
    if command == "find_nodes":
        payload["root"] = args.root
        payload["limit"] = args.limit
        if args.name:
            payload["name"] = args.name
        if args.node_type:
            payload["type"] = args.node_type
        if args.category:
            payload["category"] = args.category
        if args.path_filter:
            payload["path"] = args.path_filter
    if command == "viewport_screenshot":
        payload.update({"width": args.width, "height": args.height, "prefix": args.prefix})
    if command == "scene_snapshot":
        payload.update(
            {
                "trace_depth": args.trace_depth,
                "max_selected": args.max_selected,
                "include_viewport": args.include_viewport,
            }
        )
        if args.path:
            payload["path"] = args.path
        if args.include_viewport:
            payload.update({"width": args.width, "height": args.height, "prefix": args.prefix})
    if command in {"validate_plan", "review_plan", "verify_plan"}:
        try:
            payload["steps"] = _load_commands(args.path)
            if command == "verify_plan":
                for arg_name, payload_name in (("validation", "validation"), ("run_result", "run_result")):
                    artifact_path = getattr(args, arg_name)
                    if not artifact_path:
                        continue
                    artifact = _read_json(Path(artifact_path))
                    if not isinstance(artifact, dict):
                        raise ValueError("%s must contain a JSON object." % artifact_path)
                    payload[payload_name] = artifact
        except Exception as exc:
            _print_json(_batch_error("load_failed", str(exc)))
            return 1
    if command in {"upstream", "downstream"}:
        payload["depth"] = args.depth
    if command == "edit_mode" and args.state != "status":
        payload["enabled"] = args.state == "on"
    if command == "create_node":
        payload.update({"parent": args.parent, "type": args.node_type})
        if args.name:
            payload["name"] = args.name
    elif command == "rename_node":
        payload.update({"node": args.node, "name": args.name, "unique": not args.no_unique})
    elif command == "set_node_color":
        payload.update({"node": args.node, "color": [args.r, args.g, args.b]})
    elif command == "bypass_node":
        payload.update({"node": args.node, "bypass": args.state == "on"})
    elif command == "create_network_box":
        payload.update({"parent": args.parent, "name": args.name})
        if args.comment:
            payload["comment"] = args.comment
        if args.nodes:
            payload["nodes"] = args.nodes
        if args.color:
            payload["color"] = args.color
    elif command == "create_sticky_note":
        payload.update({"parent": args.parent, "text": args.text})
        if args.name:
            payload["name"] = args.name
        if args.x is not None or args.y is not None:
            payload.update({"x": args.x, "y": args.y})
        if args.color:
            payload["color"] = args.color
    elif command == "set_parm":
        payload.update({"node": args.node, "parm": args.parm, "value": _parse_value(args.value)})
    elif command == "set_parm_any":
        payload.update({"node": args.node, "parms": args.parms, "value": _parse_value(args.value), "required": args.required})
    elif command == "batch_set_parms":
        try:
            values = _parse_key_values(args.parm_values)
        except ValueError as exc:
            _print_json(_batch_error("bad_parm_values", str(exc)))
            return 1
        payload.update({"node": args.node, "values": values, "required": not args.optional})
    elif command in {"apply_parm_profile", "probe_parm_profile"}:
        try:
            values = _parse_key_values(args.profile_values)
        except ValueError as exc:
            _print_json(_batch_error("bad_profile_values", str(exc)))
            return 1
        payload.update({"node": args.node, "profile": args.profile, "values": values, "strict": args.strict})
    elif command == "set_comment":
        payload.update({"node": args.node, "comment": args.comment})
    elif command == "set_flags":
        payload["node"] = args.node
        if args.display is not None:
            payload["display"] = args.display == "on"
        if args.render is not None:
            payload["render"] = args.render == "on"
    elif command == "set_position":
        payload.update({"node": args.node, "x": args.x, "y": args.y})
    elif command == "ensure_parm":
        payload.update({"node": args.node, "name": args.name, "type": args.parm_type, "default": args.default})
        if args.label:
            payload["label"] = args.label
    elif command == "connect":
        payload.update({"src": args.src, "dst": args.dst, "input_index": args.input_index})
    elif command == "set_input":
        payload.update({"dst": args.dst, "input_index": args.input_index})
        if args.clear:
            payload["clear"] = True
        else:
            payload["src"] = args.src
    elif command == "disconnect":
        payload["node"] = args.node
        if args.all:
            payload["all"] = True
        elif args.src:
            payload["src"] = args.src
        else:
            payload["input_index"] = args.input_index
    elif command == "move_node":
        payload.update({"node": args.node, "parent": args.parent})
        if args.name:
            payload["name"] = args.name
    elif command == "copy_node":
        payload.update({"node": args.node, "parent": args.parent, "unique": not args.no_unique})
        if args.name:
            payload["name"] = args.name
    elif command == "set_node_shape":
        payload.update({"node": args.node, "shape": args.shape})
    elif command == "replace_node":
        payload.update(
            {
                "node": args.node,
                "type": args.node_type,
                "reconnect_inputs": not args.no_reconnect_inputs,
                "reconnect_outputs": not args.no_reconnect_outputs,
                "delete_old": args.delete_old,
                "confirm": args.confirm,
            }
        )
        if args.name:
            payload["name"] = args.name
    elif command == "delete_node":
        payload.update({"node": args.node, "confirm": args.confirm, "delete_contents": args.delete_contents})
    elif command in {"layout", "select"}:
        payload["path"] = args.path
    request = protocol.make_request(command, payload, token=session["token"])
    response = _post(session["host"], session["port"], request, session["token"])
    _print_json(response)
    return 0 if response.get("ok") else 1


def _run_commands_file(session: dict, path: str, continue_on_error: bool = False) -> dict:
    try:
        steps = _load_commands(path)
    except Exception as exc:
        return _batch_error("load_failed", str(exc))

    results = []
    ok = True
    for index, step in enumerate(steps):
        try:
            command, payload = _normalize_batch_step(step)
            request = protocol.make_request(command, payload, token=session["token"])
        except Exception as exc:
            response = {
                "ok": False,
                "command": step.get("command") if isinstance(step, dict) else "",
                "error": {"code": "bad_step", "message": str(exc)},
                "result": {},
            }
        else:
            response = _post(session["host"], session["port"], request, session["token"])
        results.append({"index": index, "response": response})
        if not response.get("ok"):
            ok = False
            if not continue_on_error:
                break
    return {
        "ok": ok,
        "count": len(steps),
        "ran": len(results),
        "stopped": (not ok and not continue_on_error),
        "results": results,
    }


WORKFLOW_ROOT = ".blib_hou_workflows"
WORKFLOW_FILES = {
    "plan": "plan.json",
    "snapshot_before": "snapshot_before.json",
    "validation": "validation.json",
    "review": "review.json",
    "run_result": "run_result.json",
    "verification": "verification.json",
    "profile_report": "profile_report.json",
    "rpc_log": "rpc_log.json",
    "snapshot_after": "snapshot_after.json",
    "template_provenance": "template_provenance.json",
    "visual_evidence": "visual_evidence.json",
    "evidence_checklist": "evidence_checklist.json",
    "evidence_manifest": "evidence_manifest.json",
    "proof_report": "proof_report.json",
    "rollback_plan": "rollback_plan.json",
    "summary": "summary.md",
}


def _workflow_command(session: dict, args: argparse.Namespace) -> dict:
    if args.workflow_command == "start":
        return _workflow_start(
            session,
            args.name,
            args.path,
            template=args.template,
            template_input=args.template_input,
            template_options=_workflow_template_options(args),
        )
    if args.workflow_command == "review":
        return _workflow_review(session, args.workflow_dir)
    if args.workflow_command == "run":
        return _workflow_run(session, args.workflow_dir, enable_edit_mode=args.enable_edit_mode, evidence=args.evidence)
    if args.workflow_command == "rollback":
        return _workflow_rollback(args.workflow_dir)
    return _batch_error("bad_workflow_command", "Unknown workflow command: %s" % args.workflow_command)


def _workflow_start(
    session: dict,
    name: str,
    path: str,
    template: str | None = None,
    template_input: str | None = None,
    template_options: dict | None = None,
) -> dict:
    timings: list[dict] = []
    try:
        workflow_dir = _workflow_dir_for_name(name)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        snapshot = _post_command(session, "scene_snapshot", {"path": path}, timings=timings, stage="snapshot_before")
        _write_json(workflow_dir / WORKFLOW_FILES["snapshot_before"], snapshot)
        plan_path = workflow_dir / WORKFLOW_FILES["plan"]
        if not plan_path.exists():
            _write_json(plan_path, [])
        template_report = None
        if template:
            resolved_input = template_input or _infer_template_input(snapshot)
            if not resolved_input:
                template_report = _workflow_error(
                    "template_input_missing",
                    "Template input was not provided and the snapshot did not contain exactly one selected node.",
                )
            else:
                plan = workflow_templates.build_plan(template, resolved_input, template_options or {})
                _write_json(plan_path, plan)
                provenance = _workflow_template_provenance(template, resolved_input, template_options or {}, plan_path, plan)
                _write_json(workflow_dir / WORKFLOW_FILES["template_provenance"], provenance)
                template_report = {
                    "ok": True,
                    "template": provenance["template"],
                    "input": resolved_input,
                    "step_count": len(plan),
                    "provenance_path": str(workflow_dir / WORKFLOW_FILES["template_provenance"]),
                }
        report = _workflow_report(str(workflow_dir))
        if template_report and not template_report.get("ok"):
            template_report.update(
                {
                    "workflow_dir": str(workflow_dir),
                    "plan_path": str(plan_path),
                    "snapshot_before": snapshot,
                    "summary_path": report.get("summary_path"),
                    "performance": _performance_report(timings),
                }
            )
            return template_report
        return {
            "ok": bool(snapshot.get("ok")),
            "workflow_dir": str(workflow_dir),
            "plan_path": str(plan_path),
            "snapshot_before": snapshot,
            "template": template_report,
            "summary_path": report.get("summary_path"),
            "performance": _performance_report(timings),
        }
    except Exception as exc:
        response = _workflow_error("workflow_start_failed", str(exc))
        response["performance"] = _performance_report(timings)
        return response


def _workflow_template_options(args: argparse.Namespace) -> dict:
    return {
        "preset": args.preset,
        "name": args.template_name,
        "output_name": args.output_name,
        "cache_path": args.cache_path,
        "render_path": args.render_path,
        "camera_path": args.camera_path,
        "resolution": args.resolution,
        "samples": args.samples,
        "lopnet_name": args.lopnet_name,
        "no_solver": args.no_solver,
        "cache": args.cache,
        "fuse_distance": args.fuse_distance,
        "voxel_size": args.voxel_size,
        "particle_size": args.particle_size,
        "substeps": args.substeps,
        "start_frame": args.start_frame,
        "constraint_strength": args.constraint_strength,
        "dissipation": args.dissipation,
    }


def _workflow_template_provenance(template: str, input_path: str, options: dict, plan_path: Path, plan: list) -> dict:
    normalized = str(template or "").strip().lower().replace("_", "-")
    catalog = workflow_templates.template_catalog()
    catalog_entry = dict(catalog.get("templates", {}).get(normalized, {}))
    workflow_policy = catalog.get("workflow_policy", {}) if isinstance(catalog.get("workflow_policy"), dict) else {}
    required_flow = workflow_policy.get("required_flow", workflow_templates.TEMPLATE_REQUIRED_FLOW)
    if not isinstance(required_flow, list) or not required_flow:
        required_flow = list(workflow_templates.TEMPLATE_REQUIRED_FLOW)
    evidence_expectations = workflow_policy.get("evidence_expectations", workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)
    if not isinstance(evidence_expectations, list) or not evidence_expectations:
        evidence_expectations = list(workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)
    verification_focus = (
        catalog_entry.get("verification_focus", {})
        if isinstance(catalog_entry.get("verification_focus"), dict)
        else {}
    )
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "template": normalized,
        "input": input_path,
        "options": _template_options_digest(options),
        "catalog": catalog_entry,
        "workflow_policy": workflow_policy,
        "workflow_contract": {
            "state": "draft_unreviewed",
            "local_generation_only": True,
            "does_not_contact_houdini": True,
            "does_not_execute": True,
            "requires_review": True,
            "requires_validation": True,
            "requires_bridge_edit_mode_to_run": True,
            "required_flow": list(required_flow),
            "evidence_expectations": list(evidence_expectations),
            "verification_focus": verification_focus,
            "cannot_report_success_before": ["houdini_run_plan", "houdini_verify_plan"],
        },
        "client_guidance": {
            "next_action": "review_template_plan",
            "suggested_cli_steps": ["workflow review", "workflow run", "workflow report"],
            "suggested_mcp_tools": list(required_flow),
            "risk_domains": list(catalog_entry.get("risk_domains", [])) if isinstance(catalog_entry.get("risk_domains"), list) else [],
            "verification_focus": verification_focus,
            "requires_user_approval_for_writes": True,
            "may_execute": False,
            "instruction": "Treat this template output as a draft. Review, validate, run, verify, and collect evidence before reporting success.",
        },
        "plan": {
            "path": str(plan_path),
            "step_count": len(plan) if isinstance(plan, list) else 0,
            "sha256": _json_sha256(plan),
        },
    }


def _template_options_digest(options: object) -> dict:
    if not isinstance(options, dict):
        return {}
    cleaned = {}
    for key, value in sorted(options.items()):
        if value is None:
            continue
        if isinstance(value, bool) and value is False:
            continue
        cleaned[str(key)] = value
    return cleaned


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _infer_template_input(snapshot: object) -> str:
    result = _response_result(snapshot)
    selected = result.get("selected", {}) if isinstance(result.get("selected"), dict) else {}
    nodes = selected.get("nodes", []) if isinstance(selected, dict) else []
    paths = [str(node.get("path")) for node in nodes if isinstance(node, dict) and node.get("path")]
    return paths[0] if len(paths) == 1 else ""


def _workflow_review(session: dict, workflow_dir: str, timings: list[dict] | None = None) -> dict:
    owned_timings = timings is None
    timings = timings if timings is not None else []
    try:
        root = Path(workflow_dir)
        steps = _load_commands(str(root / WORKFLOW_FILES["plan"]))
        validation = _post_command(session, "validate_plan", {"steps": steps}, timings=timings, stage="validate_plan")
        review = _post_command(session, "review_plan", {"steps": steps}, timings=timings, stage="review_plan")
        _write_json(root / WORKFLOW_FILES["validation"], validation)
        _write_json(root / WORKFLOW_FILES["review"], review)
        report = _workflow_report(str(root))
        validation_result = _response_result(validation)
        review_result = _response_result(review)
        ok = bool(validation.get("ok")) and bool(review.get("ok"))
        ok = ok and bool(validation_result.get("valid"))
        ok = ok and not bool(review_result.get("blockers"))
        return {
            "ok": ok,
            "workflow_dir": str(root),
            "validation": validation,
            "review": review,
            "summary_path": report.get("summary_path"),
            "performance": _performance_report(timings) if owned_timings else _performance_report(timings),
        }
    except Exception as exc:
        response = _workflow_error("workflow_review_failed", str(exc))
        response["performance"] = _performance_report(timings)
        _write_json(Path(workflow_dir) / WORKFLOW_FILES["validation"], response)
        _workflow_report(workflow_dir)
        return response


def _workflow_run(session: dict, workflow_dir: str, enable_edit_mode: bool = False, evidence: str = "standard") -> dict:
    timings: list[dict] = []
    root = Path(workflow_dir)
    steps = _load_commands(str(root / WORKFLOW_FILES["plan"]))
    _workflow_collect_before(session, root, timings=timings)
    review_response = _workflow_review(session, workflow_dir, timings=timings)
    validation_result = _response_result(review_response.get("validation", {}))
    if validation_result.get("would_require_edit") and not enable_edit_mode:
        response = _workflow_error("edit_mode_not_confirmed", "Pass --enable-edit-mode to run a workflow with edit commands.")
        response["review"] = review_response
        _workflow_collect_after(session, root, evidence=evidence, timings=timings)
        response["performance"] = _performance_report(timings)
        _write_json(root / WORKFLOW_FILES["run_result"], response)
        report = _workflow_report(workflow_dir)
        response["summary_path"] = report.get("summary_path")
        return response

    edit_response = {"ok": True, "result": {"edit_enabled": None}}
    if validation_result.get("blocked_by_edit_mode"):
        edit_response = _post_command(session, "edit_mode", {"enabled": True}, timings=timings, stage="edit_mode")
        if not edit_response.get("ok"):
            response = _workflow_error("edit_mode_failed", "Could not enable bridge edit mode.")
            response["edit_mode"] = edit_response
            _workflow_collect_after(session, root, evidence=evidence, timings=timings)
            response["performance"] = _performance_report(timings)
            _write_json(root / WORKFLOW_FILES["run_result"], response)
            report = _workflow_report(workflow_dir)
            response["summary_path"] = report.get("summary_path")
            return response
        review_response = _workflow_review(session, workflow_dir, timings=timings)
        validation_result = _response_result(review_response.get("validation", {}))

    if not review_response.get("ok") or not validation_result.get("ready_to_run", False):
        response = _workflow_error("workflow_review_blocked", "Workflow review did not pass; run was not executed.")
        response["review"] = review_response
        _workflow_collect_after(session, root, evidence=evidence, timings=timings)
        response["performance"] = _performance_report(timings)
        _write_json(root / WORKFLOW_FILES["run_result"], response)
        report = _workflow_report(workflow_dir)
        response["summary_path"] = report.get("summary_path")
        return response

    run_result = _post_command(
        session,
        "run_plan",
        {"steps": steps, "continue_on_error": False, "undo_label": "Blib Bridge workflow %s" % root.name},
        timings=timings,
        stage="run_plan",
    )
    validation_response = review_response.get("validation", {}) if isinstance(review_response, dict) else {}
    verification = _post_command(
        session,
        "verify_plan",
        {"steps": steps, "validation": validation_response, "run_result": run_result},
        timings=timings,
        stage="verify_plan",
    )
    _write_json(root / WORKFLOW_FILES["verification"], verification)
    verification_result = _response_result(verification)
    verification_failed = (not bool(verification.get("ok"))) or verification_result.get("status") == "failed"
    response = {
        "ok": bool(run_result.get("ok")) and bool(_response_result(run_result).get("ok", True)) and not verification_failed,
        "workflow_dir": str(root),
        "edit_mode": edit_response,
        "run": run_result,
        "verification": verification,
        "verification_status": verification_result.get("status"),
        "verified": bool(verification_result.get("verified")),
        "evidence": evidence,
    }
    _write_json(root / WORKFLOW_FILES["profile_report"], _workflow_profile_report(steps, run_result))
    _workflow_collect_after(session, root, evidence=evidence, timings=timings)
    response["performance"] = _performance_report(timings)
    _write_json(root / WORKFLOW_FILES["run_result"], response)
    report = _workflow_report(workflow_dir)
    response["summary_path"] = report.get("summary_path")
    return response


def _workflow_collect_before(session: dict, workflow_dir: Path, timings: list[dict] | None = None) -> None:
    path = workflow_dir / WORKFLOW_FILES["snapshot_before"]
    existing = _read_json(path)
    if isinstance(existing, dict) and existing:
        return
    _write_json(path, _post_command(session, "scene_snapshot", {"path": "/obj"}, timings=timings, stage="snapshot_before"))


def _workflow_collect_after(session: dict, workflow_dir: Path, evidence: str = "standard", timings: list[dict] | None = None) -> None:
    _write_json(workflow_dir / WORKFLOW_FILES["rpc_log"], _post_command(session, "rpc_log", {"limit": 50}, timings=timings, stage="rpc_log"))
    if evidence == "minimal":
        return
    try:
        before = _read_json(workflow_dir / WORKFLOW_FILES["snapshot_before"])
        network_path = _network_path_from_snapshot(before) or "/obj"
    except Exception:
        network_path = "/obj"
    snapshot_payload = {"path": network_path}
    if evidence == "full":
        snapshot_payload["include_viewport"] = True
    snapshot_after = _post_command(session, "scene_snapshot", snapshot_payload, timings=timings, stage="snapshot_after")
    _write_json(workflow_dir / WORKFLOW_FILES["snapshot_after"], snapshot_after)
    if evidence == "full":
        _write_json(workflow_dir / WORKFLOW_FILES["visual_evidence"], _workflow_visual_evidence(snapshot_after))


def _workflow_report(workflow_dir: str) -> dict:
    root = Path(workflow_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {
        key: _read_json(root / filename)
        for key, filename in WORKFLOW_FILES.items()
        if filename.endswith(".json") and key not in {"evidence_manifest", "evidence_checklist"}
    }
    auto_rollback_path = _workflow_auto_rollback(root, artifacts)
    if auto_rollback_path is not None:
        artifacts["rollback_plan"] = _read_json(auto_rollback_path)
    checklist = _workflow_evidence_checklist(root, artifacts)
    artifacts["evidence_checklist"] = checklist
    proof_report = _workflow_proof_report(root, artifacts)
    artifacts["proof_report"] = proof_report
    proof_report_path = root / WORKFLOW_FILES["proof_report"]
    _write_json(proof_report_path, proof_report)
    summary = _workflow_summary_markdown(root, artifacts)
    summary_path = root / WORKFLOW_FILES["summary"]
    summary_path.write_text(summary, encoding="utf-8")
    _write_json(root / WORKFLOW_FILES["evidence_checklist"], checklist)
    evidence_manifest = _workflow_evidence_manifest(root, artifacts, summary_path)
    evidence_manifest_path = root / WORKFLOW_FILES["evidence_manifest"]
    _write_json(evidence_manifest_path, evidence_manifest)
    return {
        "ok": True,
        "workflow_dir": str(root),
        "summary_path": str(summary_path),
        "auto_rollback_plan_path": str(auto_rollback_path) if auto_rollback_path is not None else None,
        "evidence_checklist_path": str(root / WORKFLOW_FILES["evidence_checklist"]),
        "evidence_manifest_path": str(evidence_manifest_path),
        "proof_report_path": str(proof_report_path),
    }


def _workflow_rollback(workflow_dir: str) -> dict:
    root = Path(workflow_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {
        key: _read_json(root / filename)
        for key, filename in WORKFLOW_FILES.items()
        if filename.endswith(".json") and key not in {"evidence_manifest", "rollback_plan"}
    }
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    plan = _draft_rollback_plan(review)
    rollback_path = root / WORKFLOW_FILES["rollback_plan"]
    _write_json(rollback_path, plan)
    report = _workflow_report(str(root))
    return {
        "ok": True,
        "workflow_dir": str(root),
        "rollback_plan_path": str(rollback_path),
        "step_count": len(plan.get("steps", []) if isinstance(plan.get("steps"), list) else []),
        "unresolved_count": len(plan.get("unresolved", []) if isinstance(plan.get("unresolved"), list) else []),
        "summary_path": report.get("summary_path"),
        "evidence_manifest_path": report.get("evidence_manifest_path"),
    }


def _workflow_auto_rollback(workflow_dir: Path, artifacts: dict[str, object]) -> Path | None:
    existing = artifacts.get("rollback_plan")
    if isinstance(existing, dict) and existing:
        return None
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    if not _review_needs_rollback(review):
        return None
    plan = _draft_rollback_plan(review)
    rollback_path = workflow_dir / WORKFLOW_FILES["rollback_plan"]
    _write_json(rollback_path, plan)
    return rollback_path


def _review_needs_rollback(review: dict) -> bool:
    if not isinstance(review, dict) or not review:
        return False
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    if any(_safe_len(impact.get(key)) for key in ("created", "touched", "deleted", "parms")):
        return True
    return bool(review.get("rollback_hints"))


def _draft_rollback_plan(review: dict) -> dict:
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    created = [path for path in impact.get("created", []) or [] if isinstance(path, str) and path and "<auto " not in path]
    deleted = {path for path in impact.get("deleted", []) or [] if isinstance(path, str)}
    steps = [
        {
            "command": "delete-node",
            "payload": {"node": path, "confirm": True},
            "reason": "Rollback removes a node created by the workflow.",
        }
        for path in reversed(created)
        if path not in deleted
    ]
    unresolved = []
    for hint in review.get("rollback_hints", []) or []:
        if not isinstance(hint, dict):
            continue
        kind = hint.get("kind")
        path = hint.get("path")
        if kind == "delete_created_node" and path not in deleted:
            continue
        unresolved.append(
            {
                "kind": kind or "unknown",
                "path": path or "",
                "command": hint.get("command", ""),
                "message": hint.get("message", "Manual rollback review is required."),
            }
        )
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "This rollback plan is a draft. Review and validate before running it through the bridge.",
        "workflow_contract": _rollback_workflow_contract(),
        "client_guidance": _rollback_plan_client_guidance(),
        "steps": steps,
        "unresolved": unresolved,
    }


def _rollback_workflow_contract() -> dict:
    return {
        "state": "draft_unreviewed",
        "local_generation_only": True,
        "evidence_only": True,
        "does_not_contact_houdini": True,
        "does_not_execute": True,
        "auto_execute": False,
        "requires_review": True,
        "requires_validation": True,
        "requires_user_approval": True,
        "requires_bridge_edit_mode_to_run": True,
        "required_flow": list(ROLLBACK_REQUIRED_FLOW),
        "cannot_report_success_before": ["houdini_run_plan", "houdini_verify_plan"],
        "may_execute": False,
        "safe_to_run_direct_edits": False,
    }


def _rollback_plan_client_guidance() -> dict:
    return {
        "next_action": "review_rollback_plan",
        "suggested_tools": list(ROLLBACK_REQUIRED_FLOW),
        "required_review_flow": list(ROLLBACK_REQUIRED_FLOW),
        "requires_user_approval": True,
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "instruction": "Treat rollback_plan.json as a draft. Read it, review and validate it, ask for user approval, run it through houdini_run_plan, and verify afterward.",
    }


def _workflow_evidence_manifest(workflow_dir: Path, artifacts: dict[str, object], summary_path: Path) -> dict:
    validation = _response_result(artifacts.get("validation") if isinstance(artifacts.get("validation"), dict) else {})
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    run_result = artifacts.get("run_result") if isinstance(artifacts.get("run_result"), dict) else {}
    verification = _response_result(artifacts.get("verification") if isinstance(artifacts.get("verification"), dict) else {})
    before = _response_result(artifacts.get("snapshot_before") if isinstance(artifacts.get("snapshot_before"), dict) else {})
    after = _response_result(artifacts.get("snapshot_after") if isinstance(artifacts.get("snapshot_after"), dict) else {})
    template_provenance = artifacts.get("template_provenance") if isinstance(artifacts.get("template_provenance"), dict) else {}
    template_focus = _template_verification_focus_digest(template_provenance)
    visual_evidence = artifacts.get("visual_evidence") if isinstance(artifacts.get("visual_evidence"), dict) else {}
    evidence_checklist = artifacts.get("evidence_checklist") if isinstance(artifacts.get("evidence_checklist"), dict) else {}
    proof_report = artifacts.get("proof_report") if isinstance(artifacts.get("proof_report"), dict) else {}
    visual_digest = _visual_evidence_digest(visual_evidence)
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    artifact_index = _workflow_artifact_index(workflow_dir, summary_path)
    return {
        "version": 1,
        "workflow_dir": str(workflow_dir),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": artifact_index,
        "artifact_integrity": _artifact_integrity_digest(artifact_index),
        "status": {
            "validation_valid": validation.get("valid"),
            "ready_to_run": validation.get("ready_to_run"),
            "run_ok": run_result.get("ok") if isinstance(run_result, dict) else None,
            "verification_status": verification.get("status"),
            "verified": verification.get("verified"),
            "review_level": review.get("level"),
        },
        "impact": {
            "created": impact.get("created", []) if isinstance(impact.get("created", []), list) else [],
            "touched": impact.get("touched", []) if isinstance(impact.get("touched", []), list) else [],
            "deleted": impact.get("deleted", []) if isinstance(impact.get("deleted", []), list) else [],
            "parms": impact.get("parms", []) if isinstance(impact.get("parms", []), list) else [],
        },
        "rollback_hints": review.get("rollback_hints", []) if isinstance(review.get("rollback_hints", []), list) else [],
        "risk_notes": review.get("risk_notes", []) if isinstance(review.get("risk_notes", []), list) else [],
        "semantics": {
            "before": _snapshot_semantic_digest(before),
            "after": _snapshot_semantic_digest(after),
        },
        "scene_evidence": _workflow_scene_evidence_digest(before, after),
        "evidence": {
            "has_before_snapshot": bool(before),
            "has_after_snapshot": bool(after),
            "has_verification": bool(verification),
            "has_rpc_log": isinstance(artifacts.get("rpc_log"), dict),
            "has_rollback_plan": isinstance(artifacts.get("rollback_plan"), dict),
            "has_template_provenance": bool(template_provenance),
            "has_visual_evidence": bool(visual_evidence),
            "has_evidence_checklist": bool(evidence_checklist),
            "has_proof_report": bool(proof_report),
            "has_screenshot": bool(visual_digest.get("path")),
            "has_summary": summary_path.exists(),
        },
        "evidence_checklist": _evidence_checklist_digest(evidence_checklist),
        "proof_report": _proof_report_digest(proof_report),
        "template": _template_provenance_digest(template_provenance),
        "template_verification_focus": _template_verification_focus_digest(template_provenance),
        "visual": visual_digest,
        "rollback_plan": _rollback_plan_digest(artifacts.get("rollback_plan")),
    }


def _workflow_artifact_index(workflow_dir: Path, summary_path: Path) -> list[dict]:
    artifacts = []
    for key, filename in WORKFLOW_FILES.items():
        if key == "evidence_manifest":
            continue
        path = workflow_dir / filename
        if key == "summary":
            path = summary_path
        exists = path.exists()
        artifacts.append(
            {
                "key": key,
                "path": str(path),
                "exists": exists,
                "bytes": path.stat().st_size if exists else 0,
                "sha256": _file_sha256(path) if exists else "",
            }
        )
    return artifacts


def _artifact_integrity_digest(artifacts: list[dict]) -> dict:
    existing = [item for item in artifacts if isinstance(item, dict) and item.get("exists")]
    hashed = [item for item in existing if isinstance(item.get("sha256"), str) and len(item.get("sha256", "")) == 64]
    missing = [item for item in artifacts if isinstance(item, dict) and not item.get("exists")]
    return {
        "artifact_count": len(artifacts),
        "existing_count": len(existing),
        "missing_count": len(missing),
        "hashed_count": len(hashed),
        "all_existing_hashed": len(existing) == len(hashed),
        "missing_artifacts": [item.get("key", "") for item in missing],
        "unhashed_artifacts": [item.get("key", "") for item in existing if item not in hashed],
        "note": "SHA256 values fingerprint evidence files present when the manifest was generated.",
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_semantic_digest(snapshot_result: dict) -> dict:
    summary = snapshot_result.get("summary", {}) if isinstance(snapshot_result.get("summary"), dict) else {}
    semantics = snapshot_result.get("semantics", {}) if isinstance(snapshot_result.get("semantics"), dict) else {}
    network_shape = semantics.get("network_shape", {}) if isinstance(semantics.get("network_shape"), dict) else {}
    return {
        "network_path": summary.get("network_path") or _network_path_from_snapshot(snapshot_result),
        "inferred_purpose": summary.get("inferred_purpose") or semantics.get("inferred_purpose"),
        "node_count": summary.get("network_node_count") or network_shape.get("node_count"),
        "wire_count": network_shape.get("wire_count"),
        "key_output_count": summary.get("key_output_count"),
        "risk_count": summary.get("risk_count"),
        "cache_count": _safe_len(semantics.get("cache_nodes")),
        "simulation_count": _safe_len(semantics.get("simulation_nodes")),
        "render_count": _safe_len(semantics.get("render_nodes")),
    }


def _workflow_scene_evidence_digest(before: dict, after: dict) -> dict:
    before_digest = _snapshot_scene_route_digest(before)
    after_digest = _snapshot_scene_route_digest(after)
    before_domains = before_digest.get("risk_domains", []) if isinstance(before_digest.get("risk_domains"), list) else []
    after_domains = after_digest.get("risk_domains", []) if isinstance(after_digest.get("risk_domains"), list) else []
    before_domain_names = [item.get("domain") for item in before_domains if isinstance(item, dict) and item.get("domain")]
    after_domain_names = [item.get("domain") for item in after_domains if isinstance(item, dict) and item.get("domain")]
    before_semantic = _snapshot_semantic_digest(before) if isinstance(before, dict) else {}
    after_semantic = _snapshot_semantic_digest(after) if isinstance(after, dict) else {}
    before_primary = before_digest.get("scene_understanding", {}).get("primary_risk_domain") if isinstance(before_digest.get("scene_understanding"), dict) else ""
    after_primary = after_digest.get("scene_understanding", {}).get("primary_risk_domain") if isinstance(after_digest.get("scene_understanding"), dict) else ""
    return {
        "version": 1,
        "exists": bool(before_digest.get("exists") or after_digest.get("exists")),
        "before": before_digest,
        "after": after_digest,
        "transition": {
            "inferred_purpose_changed": bool(before_semantic.get("inferred_purpose") != after_semantic.get("inferred_purpose")) if before_semantic and after_semantic else False,
            "primary_risk_domain_changed": bool(before_primary and after_primary and before_primary != after_primary),
            "risk_domains_added": [domain for domain in after_domain_names if domain not in before_domain_names],
            "risk_domains_removed": [domain for domain in before_domain_names if domain not in after_domain_names],
            "node_count_delta": _numeric_delta(before_semantic.get("node_count"), after_semantic.get("node_count")),
            "wire_count_delta": _numeric_delta(before_semantic.get("wire_count"), after_semantic.get("wire_count")),
        },
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "requires_user_approval_for_writes": True,
        "note": "Scene evidence is read-only routing and risk context; it does not grant write permission.",
    }


def _snapshot_scene_route_digest(snapshot_result: dict) -> dict:
    if not isinstance(snapshot_result, dict) or not snapshot_result:
        return {
            "exists": False,
            "network_path": "",
            "inferred_purpose": "unknown",
            "scene_understanding": {"exists": False},
            "risk_domains": [],
            "risk_domain_count": 0,
            "first_read_tools": ["houdini_scene_snapshot"],
            "suggested_templates": [],
        }
    summary = snapshot_result.get("summary", {}) if isinstance(snapshot_result.get("summary"), dict) else {}
    semantics = snapshot_result.get("semantics", {}) if isinstance(snapshot_result.get("semantics"), dict) else {}
    risk_domains = semantics.get("risk_domains", []) if isinstance(semantics.get("risk_domains"), list) else []
    understanding = semantics.get("scene_understanding", {}) if isinstance(semantics.get("scene_understanding"), dict) else {}
    workflow_suggestions = semantics.get("workflow_suggestions", []) if isinstance(semantics.get("workflow_suggestions"), list) else []
    first_read_tools = _scene_first_read_tools(understanding, risk_domains)
    return {
        "exists": True,
        "network_path": summary.get("network_path") or _network_path_from_snapshot(snapshot_result),
        "inferred_purpose": summary.get("inferred_purpose") or semantics.get("inferred_purpose") or "unknown",
        "scene_understanding": _scene_understanding_digest(understanding),
        "risk_domains": [_risk_domain_digest(item) for item in risk_domains if isinstance(item, dict)][:8],
        "risk_domain_count": len(risk_domains),
        "first_read_tools": first_read_tools,
        "suggested_templates": _unique_strings(
            [
                item.get("template")
                for item in workflow_suggestions
                if isinstance(item, dict) and isinstance(item.get("template"), str)
            ]
        ),
    }


def _scene_understanding_digest(value: object) -> dict:
    if not isinstance(value, dict) or not value:
        return {
            "exists": False,
            "state": "missing",
            "primary_risk_domain": "none",
            "primary_focus_path": "",
            "first_read_tools": [],
            "read_targets": [],
            "suggested_templates": [],
            "required_write_flow": [],
            "may_execute": False,
            "safe_to_run_direct_edits": False,
        }
    return {
        "exists": True,
        "state": value.get("state") or "unknown",
        "primary_risk_domain": value.get("primary_risk_domain") or "none",
        "primary_focus_path": value.get("primary_focus_path") or "",
        "first_read_tools": _unique_strings(value.get("first_read_tools", []) if isinstance(value.get("first_read_tools"), list) else []),
        "read_targets": value.get("read_targets", [])[:5] if isinstance(value.get("read_targets"), list) else [],
        "suggested_templates": _unique_strings(value.get("suggested_templates", []) if isinstance(value.get("suggested_templates"), list) else []),
        "required_write_flow": _unique_strings(value.get("required_write_flow", []) if isinstance(value.get("required_write_flow"), list) else []),
        "may_execute": bool(value.get("may_execute")),
        "safe_to_run_direct_edits": bool(value.get("safe_to_run_direct_edits")),
    }


def _risk_domain_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {"domain": "", "priority": "unknown", "path_count": 0, "paths": [], "suggested_tools": [], "workflow_templates": []}
    paths = value.get("paths", []) if isinstance(value.get("paths"), list) else []
    return {
        "domain": value.get("domain") or "",
        "priority": value.get("priority") or "unknown",
        "path_count": int(value.get("path_count") or len(paths)),
        "paths": [path for path in paths[:5] if isinstance(path, str)],
        "suggested_tools": _unique_strings(value.get("suggested_tools", []) if isinstance(value.get("suggested_tools"), list) else []),
        "workflow_templates": _unique_strings(value.get("workflow_templates", []) if isinstance(value.get("workflow_templates"), list) else []),
    }


def _scene_first_read_tools(understanding: dict, risk_domains: list) -> list[str]:
    tools = []
    if isinstance(understanding, dict):
        tools.extend(understanding.get("first_read_tools", []) if isinstance(understanding.get("first_read_tools"), list) else [])
    for item in risk_domains:
        if isinstance(item, dict) and isinstance(item.get("suggested_tools"), list):
            tools.extend(item.get("suggested_tools", []))
    tools.extend(["houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"])
    return _unique_strings([tool for tool in tools if isinstance(tool, str)])


def _numeric_delta(before_value: object, after_value: object) -> int | None:
    if not isinstance(before_value, (int, float)) or not isinstance(after_value, (int, float)):
        return None
    return int(after_value - before_value)


def _rollback_plan_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {"exists": False, "step_count": 0, "unresolved_count": 0}
    contract = value.get("workflow_contract") if isinstance(value.get("workflow_contract"), dict) else {}
    guidance = value.get("client_guidance") if isinstance(value.get("client_guidance"), dict) else {}
    return {
        "exists": True,
        "step_count": _safe_len(value.get("steps")),
        "unresolved_count": _safe_len(value.get("unresolved")),
        "contract_state": contract.get("state") or "",
        "evidence_only": bool(contract.get("evidence_only")),
        "does_not_execute": bool(contract.get("does_not_execute")),
        "auto_execute": bool(contract.get("auto_execute")),
        "requires_review": bool(contract.get("requires_review")),
        "requires_validation": bool(contract.get("requires_validation")),
        "requires_user_approval": bool(contract.get("requires_user_approval")),
        "may_execute": bool(contract.get("may_execute")),
        "safe_to_run_direct_edits": bool(contract.get("safe_to_run_direct_edits")),
        "required_flow": _unique_strings(contract.get("required_flow", []) if isinstance(contract.get("required_flow"), list) else []),
        "client_next_action": guidance.get("next_action") or "",
    }


def _workflow_evidence_checklist(workflow_dir: Path, artifacts: dict[str, object]) -> dict:
    plan = artifacts.get("plan")
    snapshot_before = artifacts.get("snapshot_before") if isinstance(artifacts.get("snapshot_before"), dict) else {}
    validation = _response_result(artifacts.get("validation") if isinstance(artifacts.get("validation"), dict) else {})
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    run_result = artifacts.get("run_result") if isinstance(artifacts.get("run_result"), dict) else {}
    run_payload = _workflow_run_payload(run_result)
    verification = _response_result(artifacts.get("verification") if isinstance(artifacts.get("verification"), dict) else {})
    rpc_log = _response_result(artifacts.get("rpc_log") if isinstance(artifacts.get("rpc_log"), dict) else {})
    snapshot_after = artifacts.get("snapshot_after") if isinstance(artifacts.get("snapshot_after"), dict) else {}
    rollback_plan = artifacts.get("rollback_plan") if isinstance(artifacts.get("rollback_plan"), dict) else {}
    template_provenance = artifacts.get("template_provenance") if isinstance(artifacts.get("template_provenance"), dict) else {}
    visual_evidence = artifacts.get("visual_evidence") if isinstance(artifacts.get("visual_evidence"), dict) else {}
    template_focus = _template_verification_focus_digest(template_provenance)
    direct_edit_readback = _direct_edit_readback_digest(verification)
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    has_edit_impact = any(_safe_len(impact.get(key)) for key in ("created", "touched", "deleted", "parms"))
    has_rollback_hints = bool(review.get("rollback_hints"))
    items = [
        _check_item("before_snapshot", "required", bool(snapshot_before), "snapshot_before.json", "Initial scene snapshot exists."),
        _check_item("plan", "required", isinstance(plan, list), "plan.json", "Plan is a JSON command list."),
        _check_item("validation", "required", bool(validation.get("valid")), "validation.json", "Plan validation passed."),
        _check_item("review", "required", isinstance(review.get("blockers", []), list) and not review.get("blockers"), "review.json", "Plan review has no blockers."),
        _check_item("run_result", "required", bool(run_result.get("ok")) and bool(run_payload.get("ok", True)), "run_result.json", "Workflow run completed without command failure."),
        _check_item("verification", "required", bool(verification.get("verified")) and verification.get("status") == "pass", "verification.json", "Post-run structural verification passed."),
        _check_item(
            "direct_edit_readback",
            "required",
            (not direct_edit_readback.get("exists")) or bool(direct_edit_readback.get("proof_ready")),
            "verification.json",
            "Direct edit readback must be proof-ready when present." if direct_edit_readback.get("exists") else "No direct edit readback checks were present.",
        ),
        _check_item("rpc_log", "required", isinstance(rpc_log.get("events", []), list), "rpc_log.json", "Recent RPC log was captured."),
        _check_item("after_snapshot", "recommended", bool(snapshot_after), "snapshot_after.json", "Post-run scene snapshot exists."),
        _check_item(
            "rollback_plan",
            "recommended",
            (not has_edit_impact and not has_rollback_hints) or (bool(rollback_plan) and isinstance(rollback_plan.get("steps", []), list)),
            "rollback_plan.json",
            "Rollback draft exists when edit impact needs one." if has_edit_impact or has_rollback_hints else "No edit rollback draft needed.",
        ),
        _check_item(
            "visual_capture",
            "recommended",
            not visual_evidence or bool(visual_evidence.get("captured")),
            "visual_evidence.json",
            "Viewport capture is available when full evidence requested." if visual_evidence else "No visual capture requested.",
        ),
        _check_item(
            "template_provenance",
            "optional",
            not template_provenance
            or (
                bool(template_provenance.get("template"))
                and isinstance(template_provenance.get("workflow_contract"), dict)
                and bool(template_provenance.get("workflow_contract", {}).get("does_not_execute"))
            ),
            "template_provenance.json",
            "Template provenance records template identity and draft workflow contract." if template_provenance else "No template provenance needed.",
        ),
        _check_item(
            "template_verification_focus",
            "recommended" if template_provenance else "optional",
            not template_provenance or bool(template_focus.get("ready")),
            "template_provenance.json",
            "Template verification focus is available for post-run proof." if template_provenance else "No template verification focus needed.",
        ),
    ]
    required = [item for item in items if item["level"] == "required"]
    warnings = [item for item in items if item["level"] == "recommended" and item["status"] != "pass"]
    failures = [item for item in required if item["status"] != "pass"]
    proof_ready = not failures
    status = "fail" if failures else ("warn" if warnings else "pass")
    return {
        "version": 1,
        "workflow_dir": str(workflow_dir),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "proof_ready": proof_ready,
        "summary": {
            "required_total": len(required),
            "required_passed": len([item for item in required if item["status"] == "pass"]),
            "warning_count": len(warnings),
            "failure_count": len(failures),
        },
        "items": items,
        "note": "Proof readiness is based on required structural evidence and direct edit readback when present. Visual capture is supporting evidence, not semantic visual judgment.",
    }


def _workflow_proof_report(workflow_dir: Path, artifacts: dict[str, object]) -> dict:
    checklist = artifacts.get("evidence_checklist") if isinstance(artifacts.get("evidence_checklist"), dict) else {}
    verification = _response_result(artifacts.get("verification") if isinstance(artifacts.get("verification"), dict) else {})
    run_result = artifacts.get("run_result") if isinstance(artifacts.get("run_result"), dict) else {}
    run_payload = _workflow_run_payload(run_result)
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    rollback_plan = artifacts.get("rollback_plan") if isinstance(artifacts.get("rollback_plan"), dict) else {}
    visual_evidence = artifacts.get("visual_evidence") if isinstance(artifacts.get("visual_evidence"), dict) else {}
    visual_digest = _visual_evidence_digest(visual_evidence)
    template_provenance = artifacts.get("template_provenance") if isinstance(artifacts.get("template_provenance"), dict) else {}
    template_focus = _template_verification_focus_digest(template_provenance)
    required_failures = _evidence_items(checklist, "fail")
    warnings = _evidence_items(checklist, "warn")
    failed_checks = _verification_items(verification, "failed")
    inconclusive_checks = _verification_items(verification, "inconclusive")
    direct_edit_readback = _direct_edit_readback_digest(verification)
    run_failed = bool(run_result) and (not bool(run_result.get("ok")) or not bool(run_payload.get("ok", True)))
    verification_failed = verification.get("status") == "failed" or bool(failed_checks)
    direct_edit_readback_not_ready = bool(direct_edit_readback.get("exists")) and not bool(direct_edit_readback.get("proof_ready"))
    direct_edit_readback_failed = int(direct_edit_readback.get("failed") or 0) > 0
    direct_edit_readback_inconclusive = int(direct_edit_readback.get("inconclusive") or 0) > 0
    proof_ready = (
        bool(checklist.get("proof_ready"))
        and verification.get("verified") is True
        and verification.get("status") == "pass"
        and not direct_edit_readback_not_ready
    )

    if run_failed or verification_failed or direct_edit_readback_failed:
        verdict = "failed"
        next_action = "review_failed_checks"
    elif direct_edit_readback_not_ready or direct_edit_readback_inconclusive:
        verdict = "incomplete"
        next_action = "collect_missing_evidence"
    elif proof_ready:
        verdict = "proven"
        next_action = "report_success"
    else:
        verdict = "incomplete"
        next_action = "collect_missing_evidence"

    rollback_digest = _rollback_plan_digest(rollback_plan)
    rollback_recommended = verdict == "failed" and rollback_digest.get("exists") and rollback_digest.get("step_count", 0) > 0
    reasons = []
    for item in required_failures:
        reasons.append(
            {
                "kind": "missing_required_evidence",
                "artifact": item.get("artifact", ""),
                "message": item.get("message", ""),
            }
        )
    for item in failed_checks:
        reasons.append(
            {
                "kind": "verification_failed",
                "check": item.get("kind", "check"),
                "message": item.get("message", ""),
                "step_index": item.get("step_index"),
            }
        )
    if run_failed:
        reasons.append({"kind": "run_failed", "message": "Workflow run_result reported failure."})
    if direct_edit_readback_failed:
        reasons.append(
            {
                "kind": "direct_edit_readback_failed",
                "message": "Direct edit readback has failed command checks.",
                "commands": direct_edit_readback.get("failed_commands", []),
            }
        )
    if direct_edit_readback_inconclusive:
        reasons.append(
            {
                "kind": "direct_edit_readback_inconclusive",
                "message": "Direct edit readback has inconclusive command checks.",
                "commands": direct_edit_readback.get("inconclusive_commands", []),
            }
        )

    return {
        "version": 1,
        "workflow_dir": str(workflow_dir),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "proof_ready": proof_ready,
        "next_action": next_action,
        "rollback_recommended": rollback_recommended,
        "summary": {
            "step_count": _safe_len(artifacts.get("plan")),
            "run_ok": run_result.get("ok") if isinstance(run_result, dict) else None,
            "verification_status": verification.get("status"),
            "verified": verification.get("verified"),
            "checklist_status": checklist.get("status"),
            "warning_count": len(warnings),
            "reason_count": len(reasons),
            "direct_edit_readback_total": direct_edit_readback.get("total", 0),
            "direct_edit_readback_failed": direct_edit_readback.get("failed", 0),
            "direct_edit_readback_inconclusive": direct_edit_readback.get("inconclusive", 0),
        },
        "impact": {
            "created_count": _safe_len(impact.get("created")) if isinstance(impact, dict) else 0,
            "touched_count": _safe_len(impact.get("touched")) if isinstance(impact, dict) else 0,
            "deleted_count": _safe_len(impact.get("deleted")) if isinstance(impact, dict) else 0,
            "parm_count": _safe_len(impact.get("parms")) if isinstance(impact, dict) else 0,
        },
        "reasons": reasons,
        "warnings": [
            {"kind": "recommended_evidence_missing", "artifact": item.get("artifact", ""), "message": item.get("message", "")}
            for item in warnings
        ],
        "inconclusive_checks": [
            {"kind": item.get("kind", "check"), "message": item.get("message", ""), "step_index": item.get("step_index")}
            for item in inconclusive_checks
        ],
        "key_artifacts": {
            key: str(workflow_dir / filename)
            for key, filename in WORKFLOW_FILES.items()
            if key
            in {
                "summary",
                "evidence_manifest",
                "evidence_checklist",
                "verification",
                "run_result",
                "rpc_log",
                "rollback_plan",
                "visual_evidence",
            }
        },
        "rollback_plan": rollback_digest,
        "visual": visual_digest,
        "direct_edit_readback": direct_edit_readback,
        "template_verification_focus": template_focus,
        "client_guidance": _workflow_client_guidance(
            workflow_dir,
            verdict,
            next_action,
            proof_ready,
            rollback_recommended,
            required_failures,
            warnings,
            failed_checks,
            inconclusive_checks,
            template_focus,
            visual_digest,
            direct_edit_readback,
        ),
        "note": "This proof report is a compact client verdict. Use evidence_checklist.json and summary.md for full details.",
    }


def _workflow_client_guidance(
    workflow_dir: Path,
    verdict: str,
    next_action: str,
    proof_ready: bool,
    rollback_recommended: bool,
    required_failures: list[dict],
    warnings: list[dict],
    failed_checks: list[dict],
    inconclusive_checks: list[dict],
    template_focus: dict | None = None,
    visual_digest: dict | None = None,
    direct_edit_readback: dict | None = None,
) -> dict:
    workflow_name = workflow_dir.name
    base = "houdini://workflow/%s" % workflow_name
    resources = [
        "%s/proof-report" % base,
        "%s/evidence-checklist" % base,
        "%s/summary" % base,
        "%s/evidence-manifest" % base,
    ]
    if rollback_recommended:
        resources.append("%s/rollback-plan" % base)
    visual_digest = visual_digest if isinstance(visual_digest, dict) else {"exists": False}
    if warnings or visual_digest.get("exists"):
        resources.append("%s/visual-evidence" % base)
    read_order = ["proof-report", "evidence-checklist", "summary", "evidence-manifest"]
    if visual_digest.get("exists"):
        read_order.append("visual-evidence")

    if verdict == "proven":
        tools = ["houdini_scene_snapshot"]
        instruction = "Report success after reading proof-report and summary; optionally refresh scene_snapshot if the live Houdini session is still connected."
    elif next_action == "review_failed_checks":
        tools = ["houdini_rpc_log", "houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"]
        instruction = "Inspect failed verification checks and current scene state before proposing a repair or rollback plan."
    else:
        tools = ["houdini_scene_snapshot", "houdini_rpc_log", "houdini_verify_plan"]
        instruction = "Collect missing required evidence before claiming success."

    template_focus = template_focus if isinstance(template_focus, dict) else {"exists": False}
    if template_focus.get("exists"):
        tools = _unique_strings(tools + template_focus.get("read_tools", []))
    direct_edit_readback = direct_edit_readback if isinstance(direct_edit_readback, dict) else {"exists": False}

    return {
        "next_action": next_action,
        "proof_ready": proof_ready,
        "mcp_resources": _unique_strings(resources),
        "suggested_tools": tools,
        "read_order": read_order,
        "blocked_by": [item.get("id") or item.get("artifact", "") for item in required_failures if isinstance(item, dict)],
        "warning_items": [item.get("id") or item.get("artifact", "") for item in warnings if isinstance(item, dict)],
        "failed_check_kinds": [item.get("kind", "check") for item in failed_checks if isinstance(item, dict)],
        "inconclusive_check_kinds": [item.get("kind", "check") for item in inconclusive_checks if isinstance(item, dict)],
        "template_verification_focus": template_focus,
        "visual_guidance": _visual_client_guidance(base, visual_digest),
        "repair_guidance": _repair_client_guidance(
            base,
            next_action,
            required_failures,
            failed_checks,
            inconclusive_checks,
            direct_edit_readback,
        ),
        "rollback_guidance": _rollback_client_guidance(base, rollback_recommended, direct_edit_readback),
        "instruction": instruction,
    }


def _visual_client_guidance(base_resource: str, visual_digest: dict) -> dict:
    exists = bool(visual_digest.get("exists"))
    captured = bool(visual_digest.get("captured"))
    semantic_verdict = visual_digest.get("semantic_verdict") or ("not_judged" if captured else "missing")
    return {
        "exists": exists,
        "captured": captured,
        "resource": "%s/visual-evidence" % base_resource,
        "proof_role": visual_digest.get("proof_role") or ("supporting_capture_only" if exists else "none"),
        "semantic_verdict": semantic_verdict,
        "requires_visual_judgment": bool(visual_digest.get("requires_visual_judgment")),
        "may_report_visual_success": bool(visual_digest.get("may_report_visual_success")),
        "visual_success_claim_allowed": bool(visual_digest.get("visual_success_claim_allowed")),
        "required_verdict_for_visual_success": "pass",
        "instruction": (
            "Treat the screenshot as capture-only evidence until a human or visual model records a semantic verdict."
            if captured and semantic_verdict == "not_judged"
            else "Use structural proof first; visual evidence is optional unless the task has an explicit visual expectation."
        ),
    }


def _repair_client_guidance(
    base_resource: str,
    next_action: str,
    required_failures: list[dict],
    failed_checks: list[dict],
    inconclusive_checks: list[dict],
    direct_edit_readback: dict | None = None,
) -> dict:
    failed_kinds = [item.get("kind", "check") for item in failed_checks if isinstance(item, dict)]
    inconclusive_kinds = [item.get("kind", "check") for item in inconclusive_checks if isinstance(item, dict)]
    missing_artifacts = [item.get("artifact", "") for item in required_failures if isinstance(item, dict)]
    direct_edit_readback = direct_edit_readback if isinstance(direct_edit_readback, dict) else {"exists": False}
    failed_commands = _unique_strings(direct_edit_readback.get("failed_commands", []) if isinstance(direct_edit_readback.get("failed_commands"), list) else [])
    inconclusive_commands = _unique_strings(direct_edit_readback.get("inconclusive_commands", []) if isinstance(direct_edit_readback.get("inconclusive_commands"), list) else [])
    readback_needs_attention = bool(direct_edit_readback.get("exists")) and not bool(direct_edit_readback.get("proof_ready"))
    recommended = next_action == "review_failed_checks" or bool(failed_kinds) or bool(inconclusive_kinds) or readback_needs_attention
    if not recommended:
        return {"recommended": False, "auto_execute": False}
    return {
        "recommended": True,
        "action": "draft_repair_plan",
        "auto_execute": False,
        "may_execute": False,
        "requires_user_approval": True,
        "read_resources": _unique_strings(
            [
                "%s/proof-report" % base_resource,
                "%s/evidence-checklist" % base_resource,
                "%s/summary" % base_resource,
                "%s/evidence-manifest" % base_resource,
            ]
        ),
        "diagnostic_read_tools": ["houdini_rpc_log", "houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"],
        "required_review_flow": list(ROLLBACK_REQUIRED_FLOW),
        "failed_check_kinds": _unique_strings(failed_kinds),
        "inconclusive_check_kinds": _unique_strings(inconclusive_kinds),
        "direct_edit_readback": {
            "exists": bool(direct_edit_readback.get("exists")),
            "proof_ready": bool(direct_edit_readback.get("proof_ready")),
            "failed_commands": failed_commands,
            "inconclusive_commands": inconclusive_commands,
            "commands": _unique_strings(direct_edit_readback.get("commands", []) if isinstance(direct_edit_readback.get("commands"), list) else []),
        },
        "direct_edit_failed_commands": failed_commands,
        "direct_edit_inconclusive_commands": inconclusive_commands,
        "missing_artifacts": _unique_strings(missing_artifacts),
        "instruction": "Use read-only diagnostics to draft a repair plan, then review, validate, ask for user approval, run, and verify. Do not execute repairs directly.",
    }


def _rollback_client_guidance(base_resource: str, rollback_recommended: bool, direct_edit_readback: dict | None = None) -> dict:
    if not rollback_recommended:
        return {"recommended": False}
    direct_edit_readback = direct_edit_readback if isinstance(direct_edit_readback, dict) else {"exists": False}
    return {
        "recommended": True,
        "resource": "%s/rollback-plan" % base_resource,
        "required_review_flow": list(ROLLBACK_REQUIRED_FLOW),
        "suggested_first_tools": ["houdini_review_plan", "houdini_validate_plan"],
        "direct_edit_readback": {
            "exists": bool(direct_edit_readback.get("exists")),
            "proof_ready": bool(direct_edit_readback.get("proof_ready")),
            "failed_commands": _unique_strings(direct_edit_readback.get("failed_commands", []) if isinstance(direct_edit_readback.get("failed_commands"), list) else []),
            "inconclusive_commands": _unique_strings(direct_edit_readback.get("inconclusive_commands", []) if isinstance(direct_edit_readback.get("inconclusive_commands"), list) else []),
        },
        "instruction": "Read the rollback plan, review and validate it, then ask for user approval before any rollback execution.",
        "auto_execute": False,
    }


def _proof_report_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {"exists": False, "verdict": "missing", "proof_ready": False}
    guidance = value.get("client_guidance") if isinstance(value.get("client_guidance"), dict) else {}
    rollback_guidance = guidance.get("rollback_guidance") if isinstance(guidance.get("rollback_guidance"), dict) else {}
    repair_guidance = guidance.get("repair_guidance") if isinstance(guidance.get("repair_guidance"), dict) else {}
    visual_guidance = guidance.get("visual_guidance") if isinstance(guidance.get("visual_guidance"), dict) else {}
    template_focus = value.get("template_verification_focus")
    if not isinstance(template_focus, dict):
        template_focus = guidance.get("template_verification_focus") if isinstance(guidance.get("template_verification_focus"), dict) else {}
    return {
        "exists": True,
        "verdict": value.get("verdict") or "unknown",
        "proof_ready": bool(value.get("proof_ready")),
        "next_action": value.get("next_action") or "",
        "rollback_recommended": bool(value.get("rollback_recommended")),
        "suggested_tools": guidance.get("suggested_tools", []) if isinstance(guidance.get("suggested_tools"), list) else [],
        "mcp_resources": guidance.get("mcp_resources", []) if isinstance(guidance.get("mcp_resources"), list) else [],
        "template_verification_focus": template_focus if isinstance(template_focus, dict) else {},
        "direct_edit_readback": value.get("direct_edit_readback", {}) if isinstance(value.get("direct_edit_readback"), dict) else {},
        "visual_guidance": {
            "exists": bool(visual_guidance.get("exists")),
            "captured": bool(visual_guidance.get("captured")),
            "proof_role": visual_guidance.get("proof_role") or "none",
            "semantic_verdict": visual_guidance.get("semantic_verdict") or "missing",
            "requires_visual_judgment": bool(visual_guidance.get("requires_visual_judgment")),
            "may_report_visual_success": bool(visual_guidance.get("may_report_visual_success")),
            "visual_success_claim_allowed": bool(visual_guidance.get("visual_success_claim_allowed")),
            "resource": visual_guidance.get("resource") or "",
        },
        "rollback_guidance": {
            "recommended": bool(rollback_guidance.get("recommended")),
            "resource": rollback_guidance.get("resource") or "",
            "auto_execute": bool(rollback_guidance.get("auto_execute")),
            "direct_edit_readback": rollback_guidance.get("direct_edit_readback", {})
            if isinstance(rollback_guidance.get("direct_edit_readback"), dict)
            else {},
            "required_review_flow": rollback_guidance.get("required_review_flow", [])
            if isinstance(rollback_guidance.get("required_review_flow"), list)
            else [],
        },
        "repair_guidance": {
            "recommended": bool(repair_guidance.get("recommended")),
            "action": repair_guidance.get("action") or "",
            "auto_execute": bool(repair_guidance.get("auto_execute")),
            "may_execute": bool(repair_guidance.get("may_execute")),
            "requires_user_approval": bool(repair_guidance.get("requires_user_approval")),
            "diagnostic_read_tools": repair_guidance.get("diagnostic_read_tools", [])
            if isinstance(repair_guidance.get("diagnostic_read_tools"), list)
            else [],
            "direct_edit_readback": repair_guidance.get("direct_edit_readback", {})
            if isinstance(repair_guidance.get("direct_edit_readback"), dict)
            else {},
            "direct_edit_failed_commands": repair_guidance.get("direct_edit_failed_commands", [])
            if isinstance(repair_guidance.get("direct_edit_failed_commands"), list)
            else [],
            "direct_edit_inconclusive_commands": repair_guidance.get("direct_edit_inconclusive_commands", [])
            if isinstance(repair_guidance.get("direct_edit_inconclusive_commands"), list)
            else [],
            "required_review_flow": repair_guidance.get("required_review_flow", [])
            if isinstance(repair_guidance.get("required_review_flow"), list)
            else [],
        },
    }


def _evidence_items(checklist: object, status: str) -> list[dict]:
    if not isinstance(checklist, dict):
        return []
    return [item for item in checklist.get("items", []) or [] if isinstance(item, dict) and item.get("status") == status]


def _verification_items(verification: object, status: str) -> list[dict]:
    if not isinstance(verification, dict):
        return []
    return [item for item in verification.get("checks", []) or [] if isinstance(item, dict) and item.get("status") == status]


def _direct_edit_readback_digest(verification: object) -> dict:
    if not isinstance(verification, dict):
        return {"exists": False, "total": 0, "passed": 0, "failed": 0, "inconclusive": 0, "commands": []}
    summary = verification.get("summary", {}) if isinstance(verification.get("summary"), dict) else {}
    readback = summary.get("direct_edit_readback", {}) if isinstance(summary.get("direct_edit_readback"), dict) else {}
    checks = [
        item
        for item in verification.get("checks", []) or []
        if isinstance(item, dict) and isinstance(item.get("satisfies_direct_edit_contract"), str)
    ]
    commands = _unique_strings(
        [str(command) for command in readback.get("commands", []) if isinstance(command, str)]
        + [str(item.get("satisfies_direct_edit_contract")) for item in checks]
    )
    failed_commands = _unique_strings(
        [str(command) for command in readback.get("failed_commands", []) if isinstance(command, str)]
        + [str(item.get("satisfies_direct_edit_contract")) for item in checks if item.get("status") == "failed"]
    )
    inconclusive_commands = _unique_strings(
        [str(command) for command in readback.get("inconclusive_commands", []) if isinstance(command, str)]
        + [str(item.get("satisfies_direct_edit_contract")) for item in checks if item.get("status") == "inconclusive"]
    )
    total = int(readback.get("total", len(checks)) or 0)
    failed = int(readback.get("failed", len([item for item in checks if item.get("status") == "failed"])) or 0)
    inconclusive = int(readback.get("inconclusive", len([item for item in checks if item.get("status") == "inconclusive"])) or 0)
    passed = int(readback.get("passed", len([item for item in checks if item.get("status") == "pass"])) or 0)
    return {
        "exists": bool(total or commands),
        "total": total,
        "passed": passed,
        "failed": failed,
        "inconclusive": inconclusive,
        "commands": commands,
        "failed_commands": failed_commands,
        "inconclusive_commands": inconclusive_commands,
        "proof_ready": bool(total or commands) and failed == 0 and inconclusive == 0,
    }


def _check_item(item_id: str, level: str, ok: bool, artifact: str, message: str) -> dict:
    return {
        "id": item_id,
        "level": level,
        "status": "pass" if ok else ("fail" if level == "required" else "warn"),
        "artifact": artifact,
        "message": message,
    }


def _evidence_checklist_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {"exists": False, "status": "missing", "proof_ready": False}
    summary = value.get("summary", {}) if isinstance(value.get("summary"), dict) else {}
    return {
        "exists": True,
        "status": value.get("status") or "unknown",
        "proof_ready": bool(value.get("proof_ready")),
        "required_total": summary.get("required_total", 0),
        "required_passed": summary.get("required_passed", 0),
        "warning_count": summary.get("warning_count", 0),
        "failure_count": summary.get("failure_count", 0),
    }


def _unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _workflow_visual_evidence(snapshot_after: object) -> dict:
    result = _response_result(snapshot_after)
    viewport = result.get("viewport", {}) if isinstance(result.get("viewport"), dict) else {}
    included = bool(viewport.get("included"))
    captured = included and bool(viewport.get("ok", False)) and bool(viewport.get("path"))
    status = "captured" if captured else ("failed" if included else "missing")
    proof_role = "supporting_capture_only" if captured else "none"
    semantic_verdict = "not_judged" if captured else "missing"
    return {
        "version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "snapshot_after",
        "status": status,
        "included": included,
        "captured": captured,
        "path": viewport.get("path", ""),
        "width": viewport.get("width"),
        "height": viewport.get("height"),
        "bytes": viewport.get("bytes"),
        "viewport": viewport.get("viewport", ""),
        "error": viewport.get("error", ""),
        "proof_role": proof_role,
        "semantic_verdict": semantic_verdict,
        "verdict_source": "none",
        "requires_visual_judgment": captured,
        "may_report_visual_success": False,
        "visual_success_claim_allowed": False,
        "required_verdict_for_visual_success": "pass",
        "judgment_note": (
            "Screenshot capture succeeded, but no human or visual model has judged the requested visual feature."
            if captured
            else "No screenshot is available for visual judgment."
        ),
        "note": "Capture success is visual evidence availability, not a semantic visual verdict.",
    }


def _visual_evidence_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {
            "exists": False,
            "captured": False,
            "status": "missing",
            "proof_role": "none",
            "semantic_verdict": "missing",
            "requires_visual_judgment": False,
            "may_report_visual_success": False,
            "visual_success_claim_allowed": False,
        }
    captured = bool(value.get("captured"))
    semantic_verdict = value.get("semantic_verdict") or ("not_judged" if captured else "missing")
    return {
        "exists": True,
        "captured": captured,
        "status": value.get("status") or "unknown",
        "path": value.get("path") or "",
        "width": value.get("width"),
        "height": value.get("height"),
        "bytes": value.get("bytes"),
        "viewport": value.get("viewport") or "",
        "error": value.get("error") or "",
        "proof_role": value.get("proof_role") or ("supporting_capture_only" if captured else "none"),
        "semantic_verdict": semantic_verdict,
        "verdict_source": value.get("verdict_source") or "none",
        "requires_visual_judgment": bool(value.get("requires_visual_judgment")) or (captured and semantic_verdict == "not_judged"),
        "may_report_visual_success": bool(value.get("may_report_visual_success")) and semantic_verdict == "pass",
        "visual_success_claim_allowed": bool(value.get("visual_success_claim_allowed")) and semantic_verdict == "pass",
        "required_verdict_for_visual_success": value.get("required_verdict_for_visual_success") or "pass",
    }


def _template_verification_focus_digest(value: object) -> dict:
    if not isinstance(value, dict) or not value:
        return {
            "exists": False,
            "required": False,
            "ready": False,
            "read_tools": [],
            "success_criteria": [],
            "evidence_artifacts": [],
            "notes": [],
            "read_tool_count": 0,
            "success_criteria_count": 0,
            "evidence_artifact_count": 0,
        }
    contract = value.get("workflow_contract", {}) if isinstance(value.get("workflow_contract"), dict) else {}
    focus = contract.get("verification_focus")
    if not isinstance(focus, dict):
        guidance = value.get("client_guidance", {}) if isinstance(value.get("client_guidance"), dict) else {}
        focus = guidance.get("verification_focus") if isinstance(guidance.get("verification_focus"), dict) else {}
    read_tools = _unique_strings(focus.get("read_tools", []) if isinstance(focus.get("read_tools"), list) else [])
    success_criteria = _unique_strings(focus.get("success_criteria", []) if isinstance(focus.get("success_criteria"), list) else [])
    evidence_artifacts = _unique_strings(focus.get("evidence_artifacts", []) if isinstance(focus.get("evidence_artifacts"), list) else [])
    notes = _unique_strings(focus.get("notes", []) if isinstance(focus.get("notes"), list) else [])
    return {
        "exists": bool(focus),
        "required": True,
        "ready": bool(read_tools and success_criteria),
        "template": value.get("template") or "",
        "read_tools": read_tools,
        "success_criteria": success_criteria,
        "evidence_artifacts": evidence_artifacts,
        "notes": notes,
        "read_tool_count": len(read_tools),
        "success_criteria_count": len(success_criteria),
        "evidence_artifact_count": len(evidence_artifacts),
        "note": "Template verification focus guides post-run proof only; it does not grant execution permission.",
    }


def _template_provenance_digest(value: object) -> dict:
    if not isinstance(value, dict):
        return {"exists": False}
    plan = value.get("plan", {}) if isinstance(value.get("plan"), dict) else {}
    catalog = value.get("catalog", {}) if isinstance(value.get("catalog"), dict) else {}
    contract = value.get("workflow_contract", {}) if isinstance(value.get("workflow_contract"), dict) else {}
    guidance = value.get("client_guidance", {}) if isinstance(value.get("client_guidance"), dict) else {}
    return {
        "exists": True,
        "template": value.get("template"),
        "input": value.get("input"),
        "category": catalog.get("category"),
        "risk_domains": catalog.get("risk_domains", []) if isinstance(catalog.get("risk_domains"), list) else [],
        "verification_focus": contract.get("verification_focus", {})
        if isinstance(contract.get("verification_focus"), dict)
        else {},
        "verification_focus_digest": _template_verification_focus_digest(value),
        "workflow_contract_state": contract.get("state") or "",
        "does_not_execute": bool(contract.get("does_not_execute")),
        "required_flow": contract.get("required_flow", []) if isinstance(contract.get("required_flow"), list) else [],
        "evidence_expectations": contract.get("evidence_expectations", [])
        if isinstance(contract.get("evidence_expectations"), list)
        else [],
        "cannot_report_success_before": contract.get("cannot_report_success_before", [])
        if isinstance(contract.get("cannot_report_success_before"), list)
        else [],
        "client_next_action": guidance.get("next_action") or "",
        "client_may_execute": bool(guidance.get("may_execute")),
        "step_count": plan.get("step_count"),
        "plan_sha256": plan.get("sha256"),
        "options": value.get("options", {}) if isinstance(value.get("options"), dict) else {},
    }


def _workflow_profile_report(steps: list, run_result: object) -> dict:
    run_payload = _workflow_run_payload(run_result)
    results = run_payload.get("results", []) if isinstance(run_payload, dict) else []
    profile_steps = []
    clamped = []
    unresolved = []
    applied_count = 0
    skipped_count = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        response = item.get("response", {}) if isinstance(item.get("response"), dict) else {}
        command = protocol.normalize_command(response.get("command"))
        if command != "apply_parm_profile":
            continue
        result = response.get("result", {}) if isinstance(response.get("result"), dict) else {}
        plan_step = steps[index] if isinstance(index, int) and index < len(steps) else {}
        payload = plan_step.get("payload", {}) if isinstance(plan_step, dict) else {}
        entry = {
            "index": index,
            "node": result.get("touched", [payload.get("node")])[0] if (result.get("touched") or payload.get("node")) else "",
            "profile": result.get("profile") or payload.get("profile", ""),
            "ok": bool(response.get("ok")),
            "duration_ms": response.get("duration_ms"),
            "applied": result.get("applied", []),
            "matched": result.get("matched", []),
            "skipped": result.get("skipped", []),
            "clamped": result.get("clamped", []),
            "unresolved": result.get("unresolved", []),
        }
        profile_steps.append(entry)
        applied_count += _safe_len(entry["applied"])
        skipped_count += _safe_len(entry["skipped"])
        for profile_item in entry["clamped"]:
            if isinstance(profile_item, dict):
                clamped.append({"index": index, "profile": entry["profile"], **profile_item})
        for profile_item in entry["unresolved"]:
            if isinstance(profile_item, dict):
                unresolved.append({"index": index, "profile": entry["profile"], **profile_item})
    return {
        "ok": True,
        "profile_step_count": len(profile_steps),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "clamped_count": len(clamped),
        "unresolved_count": len(unresolved),
        "steps": profile_steps,
        "clamped": clamped,
        "unresolved": unresolved,
        "slow_steps": _slow_steps_from_run_result(run_result),
    }


def _workflow_summary_markdown(workflow_dir: Path, artifacts: dict[str, object]) -> str:
    before = artifacts.get("snapshot_before") if isinstance(artifacts.get("snapshot_before"), dict) else {}
    validation = _response_result(artifacts.get("validation") if isinstance(artifacts.get("validation"), dict) else {})
    review = _response_result(artifacts.get("review") if isinstance(artifacts.get("review"), dict) else {})
    run_result = artifacts.get("run_result") if isinstance(artifacts.get("run_result"), dict) else {}
    verification = _response_result(artifacts.get("verification") if isinstance(artifacts.get("verification"), dict) else {})
    profile_report = artifacts.get("profile_report") if isinstance(artifacts.get("profile_report"), dict) else {}
    rpc_log = _response_result(artifacts.get("rpc_log") if isinstance(artifacts.get("rpc_log"), dict) else {})
    template_provenance = artifacts.get("template_provenance") if isinstance(artifacts.get("template_provenance"), dict) else {}
    template_focus = _template_verification_focus_digest(template_provenance)
    visual_evidence = artifacts.get("visual_evidence") if isinstance(artifacts.get("visual_evidence"), dict) else {}
    evidence_checklist = artifacts.get("evidence_checklist") if isinstance(artifacts.get("evidence_checklist"), dict) else {}
    proof_report = artifacts.get("proof_report") if isinstance(artifacts.get("proof_report"), dict) else {}
    scene_evidence = _workflow_scene_evidence_digest(_response_result(before), _response_result(artifacts.get("snapshot_after") if isinstance(artifacts.get("snapshot_after"), dict) else {}))
    network_path = _network_path_from_snapshot(before) or "unknown"
    edit_enabled = _edit_enabled_from_snapshot(before)
    run_ok = run_result.get("ok") if isinstance(run_result, dict) else None
    step_count = validation.get("step_count", _safe_len(_read_json(workflow_dir / WORKFLOW_FILES["plan"])))
    impact = review.get("impact", {}) if isinstance(review.get("impact"), dict) else {}
    verification_summary = verification.get("summary", {}) if isinstance(verification.get("summary"), dict) else {}
    direct_edit_readback = _direct_edit_readback_digest(verification)
    lines = [
        "# Blib Houdini Bridge Workflow Summary",
        "",
        "- Workflow: `%s`" % workflow_dir,
        "- Generated: %s" % datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "- Network path: `%s`" % network_path,
        "- Edit mode in initial snapshot: %s" % _format_bool(edit_enabled),
        "- Step count: %s" % step_count,
        "- Run ok: %s" % _format_bool(run_ok),
        "- Verification status: `%s`" % (verification.get("status") or "unknown"),
        "- Verified: %s" % _format_bool(verification.get("verified")),
        "- Rollback plan: %s" % _format_rollback_plan_status(artifacts.get("rollback_plan")),
        "- Template: %s" % _format_template_provenance(template_provenance),
        "- Template verification focus: %s" % _format_template_verification_focus(template_focus),
        "- Visual evidence: %s" % _format_visual_evidence(visual_evidence),
        "- Scene evidence: %s" % _format_scene_evidence(scene_evidence),
        "- Direct edit readback: %s" % _format_direct_edit_readback(direct_edit_readback),
        "- Evidence readiness: %s" % _format_evidence_checklist(evidence_checklist),
        "- Proof verdict: %s" % _format_proof_report(proof_report),
        "",
        "## Scene Evidence",
        "",
        "- Before: %s" % _format_scene_route(scene_evidence.get("before") if isinstance(scene_evidence, dict) else {}),
        "- After: %s" % _format_scene_route(scene_evidence.get("after") if isinstance(scene_evidence, dict) else {}),
        "- Transition: %s" % _format_scene_transition(scene_evidence.get("transition") if isinstance(scene_evidence, dict) else {}),
        "- Permission: may_execute=%s safe_direct_edits=%s user_approval=%s"
        % (
            _format_bool(scene_evidence.get("may_execute") if isinstance(scene_evidence, dict) else None),
            _format_bool(scene_evidence.get("safe_to_run_direct_edits") if isinstance(scene_evidence, dict) else None),
            _format_bool(scene_evidence.get("requires_user_approval_for_writes") if isinstance(scene_evidence, dict) else None),
        ),
        "",
        "## Runtime",
        "",
        "- Slow steps: %s" % _format_slow_steps(_slow_steps_from_run_result(run_result)),
        "- Workflow phases: %s" % _format_timing_steps(_performance_from_run_result(run_result)),
        "",
        "## Profile Calibration",
        "",
        "- Profile steps: %s" % profile_report.get("profile_step_count", 0),
        "- Applied parameters: %s" % profile_report.get("applied_count", 0),
        "- Skipped parameters: %s" % profile_report.get("skipped_count", 0),
        "- Clamped parameters: %s" % _format_profile_items(profile_report.get("clamped", [])),
        "- Unresolved parameters: %s" % _format_profile_items(profile_report.get("unresolved", [])),
        "",
        "## Verification",
        "",
        "- Status: `%s`" % (verification.get("status") or "unknown"),
        "- Checks: total=%s passed=%s failed=%s inconclusive=%s"
        % (
            verification_summary.get("total", 0),
            verification_summary.get("passed", 0),
            verification_summary.get("failed", 0),
            verification_summary.get("inconclusive", 0),
        ),
        "- Direct edit readback: %s" % _format_direct_edit_readback(direct_edit_readback),
        "- Failed checks: %s" % _format_verification_checks(verification.get("checks", []), "failed"),
        "- Inconclusive checks: %s" % _format_verification_checks(verification.get("checks", []), "inconclusive"),
        "",
        "## Evidence Checklist",
        "",
        "- Status: %s" % _format_evidence_checklist(evidence_checklist),
        "- Proof verdict: %s" % _format_proof_report(proof_report),
        "- Missing required: %s" % _format_evidence_items(evidence_checklist, "fail"),
        "- Warnings: %s" % _format_evidence_items(evidence_checklist, "warn"),
        "",
        "## Impact",
        "",
        "- Created: %s" % _format_list(impact.get("created", [])),
        "- Touched: %s" % _format_list(impact.get("touched", [])),
        "- Deleted: %s" % _format_list(impact.get("deleted", [])),
        "- Parameters: %s" % _format_list(impact.get("parms", [])),
        "",
        "## Review",
        "",
        "- Blockers: %s" % _format_list(review.get("blockers", [])),
        "- Warnings: %s" % _format_list(review.get("warnings", [])),
        "- Suggestions: %s" % _format_list(review.get("suggestions", [])),
        "- Risk notes: %s" % _format_risk_notes(review.get("risk_notes", [])),
        "",
        "## Rollback Hints",
        "",
        "- Hints: %s" % _format_rollback_hints(review.get("rollback_hints", [])),
        "- Draft rollback plan: %s" % _format_rollback_plan_status(artifacts.get("rollback_plan")),
        "",
        "## RPC Log",
        "",
    ]
    events = rpc_log.get("events", []) if isinstance(rpc_log, dict) else []
    if events:
        for event in events[-5:]:
            if not isinstance(event, dict):
                continue
            lines.append(
                "- {command}: ok={ok} status={status} duration={duration}".format(
                    command=event.get("command", ""),
                    ok=event.get("ok", ""),
                    status=event.get("status", ""),
                    duration=event.get("duration_ms", event.get("duration", "")),
                )
            )
    else:
        lines.append("- No RPC events recorded.")
    lines.append("")
    return "\n".join(lines)


def _post_command(
    session: dict,
    command: str,
    payload: dict | None = None,
    timings: list[dict] | None = None,
    stage: str | None = None,
) -> dict:
    request = protocol.make_request(command, payload or {}, token=session["token"])
    started = time.perf_counter()
    response = _post(session["host"], session["port"], request, session["token"])
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    if timings is not None:
        timings.append(
            {
                "stage": stage or command,
                "command": protocol.normalize_command(command),
                "ok": bool(response.get("ok")) if isinstance(response, dict) else False,
                "duration_ms": duration_ms,
            }
        )
    return response


def _performance_report(timings: list[dict] | None) -> dict:
    items = list(timings or [])
    total = round(sum(float(item.get("duration_ms") or 0.0) for item in items), 3)
    return {
        "total_ms": total,
        "stages": items,
        "slowest": sorted(items, key=lambda item: float(item.get("duration_ms") or 0.0), reverse=True)[:5],
    }


def _performance_from_run_result(run_result: object) -> dict:
    if isinstance(run_result, dict):
        performance = run_result.get("performance")
        if isinstance(performance, dict):
            return performance
    return {}


def _workflow_dir_for_name(name: str) -> Path:
    if not name or any(char in name for char in ("/", "\\", ":")) or name in {".", ".."}:
        raise ValueError("Workflow name must be a simple directory name.")
    return Path(WORKFLOW_ROOT) / name


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": {"code": "read_failed", "message": str(exc)}}


def _response_result(response: object) -> dict:
    if not isinstance(response, dict):
        return {}
    result = response.get("result")
    if isinstance(result, dict):
        return result
    return response


def _workflow_run_payload(run_result: object) -> dict:
    if not isinstance(run_result, dict):
        return {}
    run = run_result.get("run")
    if isinstance(run, dict):
        return _response_result(run)
    return _response_result(run_result)


def _slow_steps_from_run_result(run_result: object, limit: int = 5) -> list[dict]:
    payload = _workflow_run_payload(run_result)
    results = payload.get("results", []) if isinstance(payload, dict) else []
    slow_steps = []
    for item in results:
        if not isinstance(item, dict):
            continue
        response = item.get("response", {}) if isinstance(item.get("response"), dict) else {}
        duration = response.get("duration_ms")
        if not isinstance(duration, (int, float)):
            continue
        slow_steps.append(
            {
                "index": item.get("index"),
                "command": response.get("command", ""),
                "duration_ms": duration,
                "ok": response.get("ok"),
            }
        )
    return sorted(slow_steps, key=lambda item: item["duration_ms"], reverse=True)[:limit]


def _network_path_from_snapshot(snapshot: object) -> str:
    result = _response_result(snapshot)
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    if summary.get("network_path"):
        return str(summary["network_path"])
    network = result.get("network", {}) if isinstance(result.get("network"), dict) else {}
    return str(network.get("path") or "")


def _edit_enabled_from_snapshot(snapshot: object):
    result = _response_result(snapshot)
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    return summary.get("edit_enabled")


def _safe_len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _format_bool(value) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _format_list(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    return ", ".join("`%s`" % item for item in value)


def _format_slow_steps(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        parts.append("#%s `%s` %sms ok=%s" % (item.get("index"), item.get("command", ""), item.get("duration_ms", ""), item.get("ok")))
    return "; ".join(parts) if parts else "none"


def _format_timing_steps(value: object) -> str:
    if not isinstance(value, dict):
        return "none"
    stages = value.get("stages", [])
    if not isinstance(stages, list) or not stages:
        return "none"
    parts = []
    for item in stages:
        if not isinstance(item, dict):
            continue
        parts.append(
            "{stage} `{command}` {duration}ms ok={ok}".format(
                stage=item.get("stage", ""),
                command=item.get("command", ""),
                duration=item.get("duration_ms", ""),
                ok=item.get("ok", ""),
            )
        )
    return "; ".join(parts) if parts else "none"


def _format_profile_items(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = "%s/%s" % (item.get("profile", ""), item.get("parameter", ""))
        if item.get("parm"):
            label += " -> %s" % item.get("parm")
        parts.append("`%s`" % label.strip("/"))
    return ", ".join(parts) if parts else "none"


def _format_verification_checks(value: object, status: str, limit: int = 5) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    parts = []
    for item in value:
        if not isinstance(item, dict) or item.get("status") != status:
            continue
        step = "#%s " % item.get("step_index") if item.get("step_index") is not None else ""
        kind = item.get("kind", "check")
        message = str(item.get("message", "")).strip()
        parts.append("`%s%s`: %s" % (step, kind, message))
        if len(parts) >= limit:
            break
    return "; ".join(parts) if parts else "none"


def _format_rollback_hints(value: object, limit: int = 8) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        path = str(item.get("path", "")).strip()
        message = str(item.get("message", "")).strip()
        label = kind or "rollback"
        if path:
            label = "%s `%s`" % (label, path)
        if message:
            label = "%s: %s" % (label, message)
        parts.append(label)
        if len(parts) >= limit:
            break
    if len(value) > limit:
        parts.append("... %s more" % (len(value) - limit))
    return "; ".join(parts) if parts else "none"


def _format_risk_notes(value: object, limit: int = 8) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip() or "risk"
        path = str(item.get("path", "")).strip()
        verify = str(item.get("verify", "")).strip()
        label = kind
        if path:
            label = "%s `%s`" % (label, path)
        if verify:
            label = "%s: %s" % (label, verify)
        parts.append(label)
        if len(parts) >= limit:
            break
    if len(value) > limit:
        parts.append("... %s more" % (len(value) - limit))
    return "; ".join(parts) if parts else "none"


def _format_rollback_plan_status(value: object) -> str:
    if not isinstance(value, dict):
        return "none"
    return "steps=%s unresolved=%s" % (_safe_len(value.get("steps")), _safe_len(value.get("unresolved")))


def _format_template_provenance(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    template = str(value.get("template") or "unknown")
    input_path = str(value.get("input") or "")
    plan = value.get("plan", {}) if isinstance(value.get("plan"), dict) else {}
    catalog = value.get("catalog", {}) if isinstance(value.get("catalog"), dict) else {}
    label = "`%s`" % template
    if catalog.get("category"):
        label += " category=%s" % catalog.get("category")
    if input_path:
        label += " input=`%s`" % input_path
    label += " steps=%s" % plan.get("step_count", 0)
    if plan.get("sha256"):
        label += " sha256=%s" % str(plan.get("sha256"))[:12]
    contract = value.get("workflow_contract", {}) if isinstance(value.get("workflow_contract"), dict) else {}
    if contract.get("state"):
        label += " contract=%s" % contract.get("state")
    if contract.get("does_not_execute") is not None:
        label += " executes=%s" % ("no" if contract.get("does_not_execute") else "yes")
    return label


def _format_template_verification_focus(value: object) -> str:
    if not isinstance(value, dict) or not value.get("exists"):
        return "none"
    criteria = value.get("success_criteria", []) if isinstance(value.get("success_criteria"), list) else []
    artifacts = value.get("evidence_artifacts", []) if isinstance(value.get("evidence_artifacts"), list) else []
    tools = value.get("read_tools", []) if isinstance(value.get("read_tools"), list) else []
    return "ready=%s read_tools=%s criteria=%s artifacts=%s" % (
        "yes" if value.get("ready") else "no",
        _format_list(tools[:4]),
        _format_list(criteria[:5]),
        _format_list(artifacts[:5]),
    )


def _format_visual_evidence(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    status = str(value.get("status") or "unknown")
    path = str(value.get("path") or "")
    label = status
    if value.get("captured"):
        label += " %sx%s" % (value.get("width") or "?", value.get("height") or "?")
    if value.get("semantic_verdict"):
        label += " semantic=%s" % value.get("semantic_verdict")
    if value.get("may_report_visual_success") is not None:
        label += " visual_success=%s" % ("yes" if value.get("may_report_visual_success") else "no")
    if path:
        label += " `%s`" % path
    if value.get("error"):
        label += ": %s" % value.get("error")
    return label


def _format_scene_evidence(value: object) -> str:
    if not isinstance(value, dict) or not value.get("exists"):
        return "none"
    before = value.get("before") if isinstance(value.get("before"), dict) else {}
    after = value.get("after") if isinstance(value.get("after"), dict) else {}
    transition = value.get("transition") if isinstance(value.get("transition"), dict) else {}
    primary_before = _scene_primary_domain(before)
    primary_after = _scene_primary_domain(after)
    return "before=%s after=%s added=%s removed=%s" % (
        primary_before or "none",
        primary_after or "none",
        _format_list(transition.get("risk_domains_added", [])),
        _format_list(transition.get("risk_domains_removed", [])),
    )


def _format_scene_route(value: object) -> str:
    if not isinstance(value, dict) or not value.get("exists"):
        return "missing"
    understanding = value.get("scene_understanding") if isinstance(value.get("scene_understanding"), dict) else {}
    return "purpose=%s primary=%s focus=%s reads=%s templates=%s" % (
        value.get("inferred_purpose") or "unknown",
        _scene_primary_domain(value) or "none",
        understanding.get("primary_focus_path") or "none",
        _format_list(value.get("first_read_tools", [])[:5] if isinstance(value.get("first_read_tools"), list) else []),
        _format_list(value.get("suggested_templates", [])[:5] if isinstance(value.get("suggested_templates"), list) else []),
    )


def _format_scene_transition(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    return "purpose_changed=%s primary_changed=%s node_delta=%s wire_delta=%s added=%s removed=%s" % (
        _format_bool(value.get("inferred_purpose_changed")),
        _format_bool(value.get("primary_risk_domain_changed")),
        value.get("node_count_delta") if value.get("node_count_delta") is not None else "unknown",
        value.get("wire_count_delta") if value.get("wire_count_delta") is not None else "unknown",
        _format_list(value.get("risk_domains_added", [])),
        _format_list(value.get("risk_domains_removed", [])),
    )


def _scene_primary_domain(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    understanding = value.get("scene_understanding") if isinstance(value.get("scene_understanding"), dict) else {}
    primary = understanding.get("primary_risk_domain") if isinstance(understanding, dict) else ""
    if primary and primary != "none":
        return str(primary)
    risk_domains = value.get("risk_domains") if isinstance(value.get("risk_domains"), list) else []
    for item in risk_domains:
        if isinstance(item, dict) and item.get("domain"):
            return str(item.get("domain"))
    return ""


def _format_evidence_checklist(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "missing"
    summary = value.get("summary", {}) if isinstance(value.get("summary"), dict) else {}
    return "%s proof_ready=%s required=%s/%s warnings=%s failures=%s" % (
        value.get("status") or "unknown",
        "yes" if value.get("proof_ready") else "no",
        summary.get("required_passed", 0),
        summary.get("required_total", 0),
        summary.get("warning_count", 0),
        summary.get("failure_count", 0),
    )


def _format_proof_report(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "missing"
    summary = value.get("summary", {}) if isinstance(value.get("summary"), dict) else {}
    return "%s proof_ready=%s next=%s reasons=%s rollback=%s" % (
        value.get("verdict") or "unknown",
        "yes" if value.get("proof_ready") else "no",
        value.get("next_action") or "unknown",
        summary.get("reason_count", 0),
        "yes" if value.get("rollback_recommended") else "no",
    )


def _format_direct_edit_readback(value: object) -> str:
    if not isinstance(value, dict) or not value.get("exists"):
        return "none"
    text = "proof_ready=%s total=%s passed=%s failed=%s inconclusive=%s commands=%s" % (
        "yes" if value.get("proof_ready") else "no",
        value.get("total", 0),
        value.get("passed", 0),
        value.get("failed", 0),
        value.get("inconclusive", 0),
        _format_list(value.get("commands", [])),
    )
    if value.get("failed_commands"):
        text += " failed_commands=%s" % _format_list(value.get("failed_commands", []))
    if value.get("inconclusive_commands"):
        text += " inconclusive_commands=%s" % _format_list(value.get("inconclusive_commands", []))
    return text


def _format_evidence_items(value: object, status: str, limit: int = 6) -> str:
    if not isinstance(value, dict):
        return "none"
    items = value.get("items", [])
    if not isinstance(items, list):
        return "none"
    parts = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") != status:
            continue
        parts.append("`%s`: %s" % (item.get("id", "item"), item.get("message", "")))
        if len(parts) >= limit:
            break
    return "; ".join(parts) if parts else "none"


def _workflow_error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _load_commands(path: str) -> list:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Commands file must contain a JSON list.")
    return payload


def _normalize_batch_step(step) -> tuple[str, dict]:
    if not isinstance(step, dict):
        raise ValueError("Each command step must be a JSON object.")
    command = protocol.normalize_command(step.get("command"))
    payload = step.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("Command payload must be a JSON object.")
    return command, payload


def _batch_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "count": 0,
        "ran": 0,
        "stopped": True,
        "error": {"code": code, "message": message},
        "results": [],
    }


def _doctor(session_path: str | None = None, use_cache: bool = True) -> dict:
    timings: list[dict] = []
    started = time.perf_counter()
    resolved_path = os.path.abspath(session_path) if session_path else str(auth.default_session_path())
    checks = []
    session_exists = os.path.exists(resolved_path)
    checks.append(
        {
            "name": "session_file",
            "ok": session_exists,
            "message": "Session file found." if session_exists else "Session file was not found.",
        }
    )

    session = auth.load_session(session_path)
    timings.append({"stage": "load_session", "command": "local", "ok": session is not None, "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
    session_ok = session is not None
    checks.append(
        {
            "name": "session_json",
            "ok": session_ok,
            "message": "Session JSON is valid." if session_ok else "Session JSON is missing or invalid.",
        }
    )
    if session is None:
        return {
            "ok": False,
            "session_path": resolved_path,
            "checks": checks,
            "error": {"code": "offline", "message": "No usable Blib Houdini Bridge session was found."},
            "performance": _performance_report(timings),
        }

    cached_health = _load_cached_health(resolved_path, session) if use_cache else None
    if cached_health is not None:
        checks.append(
            {
                "name": "rpc_health",
                "ok": True,
                "message": "Bridge RPC health check reused from short-lived cache.",
                "cached": True,
            }
        )
        return {
            "ok": True,
            "session_path": resolved_path,
            "session": _safe_session_summary(session),
            "checks": checks,
            "health": cached_health,
            "cached": True,
            "performance": _performance_report(timings),
        }

    request = protocol.make_request("health", {}, token=session["token"])
    health_started = time.perf_counter()
    health = _post(session["host"], session["port"], request, session["token"])
    timings.append({"stage": "rpc_health", "command": "health", "ok": bool(health.get("ok")), "duration_ms": round((time.perf_counter() - health_started) * 1000, 3)})
    health_ok = bool(health.get("ok"))
    if health_ok:
        _save_cached_health(resolved_path, session, health)
    checks.append(
        {
            "name": "rpc_health",
            "ok": health_ok,
            "message": "Bridge RPC health check passed." if health_ok else "Bridge RPC health check failed.",
        }
    )
    return {
        "ok": health_ok,
        "session_path": resolved_path,
        "session": _safe_session_summary(session),
        "checks": checks,
        "health": health,
        "cached": False,
        "performance": _performance_report(timings),
    }


def _safe_session_summary(session: dict) -> dict:
    return {
        "host": session.get("host"),
        "port": session.get("port"),
        "pid": session.get("pid"),
        "token_present": bool(session.get("token")),
        "started_at": session.get("started_at"),
    }


def _health_cache_path() -> Path:
    return auth.default_session_path().with_name("health_cache.json")


def _health_cache_key(session_path: str, session: dict) -> str:
    token_hash = hashlib.sha256(str(session.get("token", "")).encode("utf-8")).hexdigest()[:16]
    parts = [
        os.path.abspath(session_path),
        str(session.get("host", "")),
        str(session.get("port", "")),
        str(session.get("pid", "")),
        str(session.get("started_at", "")),
        token_hash,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _load_cached_health(session_path: str, session: dict) -> dict | None:
    path = _health_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("key") != _health_cache_key(session_path, session):
        return None
    if time.time() - float(payload.get("created_at", 0.0) or 0.0) > HEALTH_CACHE_TTL_SECONDS:
        return None
    health = payload.get("health")
    return health if isinstance(health, dict) and health.get("ok") else None


def _save_cached_health(session_path: str, session: dict, health: dict) -> None:
    path = _health_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "key": _health_cache_key(session_path, session),
                    "created_at": time.time(),
                    "ttl_seconds": HEALTH_CACHE_TTL_SECONDS,
                    "health": health,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _post(host: str, port: int, payload: dict, token: str) -> dict:
    url = "http://%s:%s/rpc" % (host, port)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Blib-Bridge-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return json.loads(body)
        except Exception:
            return {
                "ok": False,
                "error": {
                    "code": "http_error",
                    "message": body or str(exc),
                },
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "connection_failed",
                "message": str(exc),
            },
        }


def _protocol_command(cli_command: str) -> str:
    command = protocol.normalize_command(cli_command)
    if command == "status":
        return "health"
    return command


def _parse_value(value: str):
    try:
        return json.loads(value)
    except Exception:
        return value


def _parse_key_values(values: list[str]) -> dict:
    parsed = {}
    for item in values:
        if "=" not in item:
            raise ValueError("Expected name=value, got: %s" % item)
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Profile value name cannot be empty.")
        parsed[key] = _parse_value(value)
    return parsed


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
