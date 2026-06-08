"""Small stdio MCP adapter for the local Blib Houdini Bridge.

This module intentionally does not import ``hou``. It reads the bridge session
file, calls the existing BlibHouBridge HTTP RPC endpoint, and exposes a compact
Model Context Protocol facade for external AI clients.
"""

from __future__ import annotations

import json
import os
import hashlib
import sys
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from blib_hou_bridge import auth, protocol, workflow_templates


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "blib-houdini-bridge"
SERVER_VERSION = protocol.BRIDGE_VERSION
ADAPTER_VERSION = "0.1.0"

READ_TOOL_COMMANDS = frozenset(
    {
        "context",
        "selected",
        "scene_snapshot",
        "node_info",
        "node_parms",
        "network",
        "upstream",
        "downstream",
        "viewport_screenshot",
        "review_plan",
        "validate_plan",
        "verify_plan",
        "rpc_log",
        "edit_mode",
        "find_nodes",
    }
)

DIRECT_EDIT_TOOL_COMMANDS = protocol.DIRECT_EDIT_COMMANDS
PLAN_TOOL_COMMANDS = frozenset({"run_plan"})
EXPOSED_COMMANDS = READ_TOOL_COMMANDS | DIRECT_EDIT_TOOL_COMMANDS | PLAN_TOOL_COMMANDS
LOCAL_TOOL_NAMES = frozenset({"houdini_template_plan"})

RESOURCE_PAYLOADS = {
    "houdini://session/current": None,
    "houdini://scene/current": ("scene_snapshot", {}),
    "houdini://selection/current": ("selected", {}),
    "houdini://manifest": ("manifest", {}),
    "houdini://rpc-log/recent": ("rpc_log", {"limit": 50}),
}

LOCAL_RESOURCE_PAYLOADS = {
    "houdini://safety/policy": protocol.safety_policy,
    "houdini://workflow-templates/catalog": workflow_templates.template_catalog,
}


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)


class BridgeMCPAdapter:
    def __init__(
        self,
        session_path: str | None = None,
        workflow_root: str | os.PathLike | None = None,
        session_loader: Callable[[str | None], dict[str, Any] | None] | None = None,
        poster: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    ):
        self.session_path = session_path
        self.workflow_root = Path(workflow_root) if workflow_root is not None else _default_workflow_root()
        self._session_loader = session_loader or auth.load_session
        self._poster = poster or self._post_to_bridge

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        try:
            if method == "initialize":
                result = self.initialize()
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                params = self._params(message)
                result = self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
            elif method == "resources/list":
                result = {"resources": self.list_resources()}
            elif method == "resources/read":
                params = self._params(message)
                result = self.read_resource(str(params.get("uri") or ""))
            elif method == "ping":
                result = {}
            elif isinstance(method, str) and method.startswith("notifications/"):
                return None
            else:
                raise McpError(-32601, "Unknown MCP method: %s" % method)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except McpError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}}

    def initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def status(self, include_health: bool = True) -> dict[str, Any]:
        session = self._sanitized_session()
        tools = self.list_tools()
        workflow_index = self._workflow_index()
        workflow_evidence = _workflow_evidence_status(workflow_index)
        tool_names = {tool["name"] for tool in tools}
        direct_tools = sorted(_tool_name(command) for command in DIRECT_EDIT_TOOL_COMMANDS if _tool_name(command) in tool_names)
        plan_required_tools = sorted(
            _tool_name(command) for command in PLAN_TOOL_COMMANDS if _tool_name(command) in tool_names
        )
        hidden_plan_required_commands = sorted(
            command for command in protocol.PLAN_REQUIRED_EDIT_COMMANDS if _tool_name(command) not in tool_names
        )
        exposed_plan_required_commands = sorted(
            command for command in protocol.PLAN_REQUIRED_EDIT_COMMANDS if _tool_name(command) in tool_names
        )
        unexpected_direct_edit_commands = sorted(
            _command_from_tool(tool["name"])
            for tool in tools
            if tool.get("_meta", {}).get("exposure") == "direct_edit"
            and _command_from_tool(tool["name"]) not in DIRECT_EDIT_TOOL_COMMANDS
        )
        policy_audit = _tool_policy_audit(tools)
        safety = {
            "imports_hou": False,
            "bridge_rpc_only": True,
            "token_exposed": False,
            "direct_edits_require_bridge_edit_mode": True,
            "plan_required_commands_not_direct_tools": not exposed_plan_required_commands,
            "direct_edit_tools_match_policy": not unexpected_direct_edit_commands,
            "tool_policy_contract_ok": bool(policy_audit.get("ok")),
            "tool_policy_issues": policy_audit.get("issues", []),
            "exposed_plan_required_commands": exposed_plan_required_commands,
            "unexpected_direct_edit_commands": unexpected_direct_edit_commands,
        }
        bridge_health = None
        scene_routing = _scene_routing_status(None)
        if include_health:
            bridge_health = self._rpc("health", {}) if session.get("connected") else _bridge_error(
                "offline",
                "No active Blib Houdini Bridge session was found. Start the Houdini bridge shelf tool first.",
            )
            if session.get("connected") and isinstance(bridge_health, dict) and bridge_health.get("ok"):
                scene_routing = _scene_routing_status(
                    self._rpc(
                        "scene_snapshot",
                        {"trace_depth": 1, "max_selected": 3, "include_viewport": False},
                    )
                )
        readiness = _adapter_readiness(session, bridge_health, workflow_evidence, safety, scene_routing)
        client_bootstrap = _client_bootstrap(readiness, workflow_evidence, scene_routing, safety)
        result = {
            "adapter": {
                "name": SERVER_NAME,
                "adapter_version": ADAPTER_VERSION,
                "mcp_protocol_version": MCP_PROTOCOL_VERSION,
                "bridge_protocol_version": protocol.BRIDGE_VERSION,
            },
            "session": session,
            "tools": {
                "count": len(tools),
                "read_count": len([tool for tool in tools if tool.get("_meta", {}).get("exposure") == "read"]),
                "direct_edit_count": len(direct_tools),
                "plan_transaction_count": len(plan_required_tools),
                "direct_edit_tools": direct_tools,
                "plan_transaction_tools": plan_required_tools,
                "hidden_plan_required_commands": hidden_plan_required_commands,
                "policy_contract": policy_audit,
            },
            "resources": {
                "static_count": len(RESOURCE_PAYLOADS) + len(LOCAL_RESOURCE_PAYLOADS) + 3,
                "workflow_count": len(self._workflow_resources()),
            },
            "workflow_evidence": workflow_evidence,
            "scene_routing": scene_routing,
            "safety": safety,
            "readiness": readiness,
            "client_bootstrap": client_bootstrap,
            "success_gate": _success_gate(readiness, workflow_evidence, safety),
        }
        if bridge_health is not None:
            result["bridge_health"] = bridge_health
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        manifest = protocol.command_manifest()
        commands = manifest.get("commands", {})
        tools = []
        for command in sorted(EXPOSED_COMMANDS):
            if command not in commands:
                continue
            metadata = commands[command]
            tools.append(
                {
                    "name": _tool_name(command),
                    "description": _tool_description(command, metadata),
                    "inputSchema": _tool_input_schema(command, metadata),
                    "outputSchema": _tool_output_schema(command, metadata),
                    "_meta": {
                        "bridgeCommand": command,
                        "permission": metadata.get("permission"),
                        "exposure": metadata.get("exposure"),
                        "resultSchema": _tool_output_schema(command, metadata),
                        "requiresEditMode": command in protocol.EDIT_COMMANDS,
                        "safety": _tool_safety_contract(command, metadata),
                    },
                }
            )
        tools.append(
            {
                "name": "houdini_template_plan",
                "description": "Expand a local bridge workflow template into a reviewable JSON command plan. Local read-only; does not contact Houdini.",
                "inputSchema": _template_plan_schema(),
                "outputSchema": _template_plan_output_schema(),
                "_meta": {
                    "bridgeCommand": None,
                    "permission": "read",
                    "exposure": "read",
                    "requiresEditMode": False,
                    "local": True,
                    "resultSchema": _template_plan_output_schema(),
                    "safety": _local_template_tool_safety_contract(),
                },
            }
        )
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name in LOCAL_TOOL_NAMES:
            if not isinstance(arguments, dict):
                raise McpError(-32602, "Tool arguments must be a JSON object.")
            return self._call_local_tool(tool_name, arguments)
        command = _command_from_tool(tool_name)
        if command not in EXPOSED_COMMANDS:
            raise McpError(-32602, "MCP tool is not exposed by this adapter: %s" % tool_name)
        if not isinstance(arguments, dict):
            raise McpError(-32602, "Tool arguments must be a JSON object.")
        prepared = _prepare_tool_arguments(command, arguments)
        if prepared.get("ok") is False:
            return _tool_result(prepared, is_error=True)
        response = self._rpc(command, prepared.get("payload", arguments))
        if command == "run_plan":
            response = _annotate_run_plan_response(response, prepared)
        elif command in DIRECT_EDIT_TOOL_COMMANDS:
            response = _annotate_direct_edit_response(command, response)
        return _tool_result(response, is_error=not bool(response.get("ok")))

    def _call_local_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "houdini_template_plan":
            raise McpError(-32602, "Unknown local MCP tool: %s" % tool_name)
        response = _template_plan(arguments)
        return _tool_result(response, is_error=not bool(response.get("ok")))

    def list_resources(self) -> list[dict[str, Any]]:
        resources = [
            {
                "uri": "houdini://adapter/status",
                "name": "Blib Houdini MCP adapter status",
                "description": "Adapter version, sanitized session state, exposed tool policy, resources, and bridge health.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://session/current",
                "name": "Current Houdini bridge session",
                "description": "Sanitized local session metadata. The bridge token is never exposed.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://scene/current",
                "name": "Current Houdini scene snapshot",
                "description": "Compact scene, selection, network, trace, and edit-mode context.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://selection/current",
                "name": "Current Houdini selection",
                "description": "Selected Houdini nodes.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://manifest",
                "name": "Blib bridge command manifest",
                "description": "Bridge commands, permissions, schemas, exposure levels, and danger command reservations.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://safety/policy",
                "name": "Blib bridge safety policy",
                "description": "Local safety policy for read/edit gates, direct edits, plan-required edits, blocked commands, and evidence expectations.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://rpc-log/recent",
                "name": "Recent bridge RPC log",
                "description": "Recent BlibHouBridge RPC events.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://workflow-templates/catalog",
                "name": "Blib bridge workflow template catalog",
                "description": "Local workflow template names, categories, supported options, presets, and defaults.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://workflow-templates/risk-domains",
                "name": "Blib bridge workflow template risk domains",
                "description": "Local index mapping scene risk domains to workflow templates, safe read tools, required review flow, and evidence expectations.",
                "mimeType": "application/json",
            },
            {
                "uri": "houdini://workflow/index",
                "name": "Blib bridge workflow evidence index",
                "description": "Local index of workflow evidence directories, proof verdicts, and MCP resource URIs.",
                "mimeType": "application/json",
            },
        ]
        resources.extend(self._workflow_resources())
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "houdini://workflow/index":
            payload = {"ok": True, "result": self._workflow_index()}
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    }
                ]
            }
        workflow_resource = self._read_workflow_resource(uri)
        if workflow_resource is not None:
            return workflow_resource
        if uri in LOCAL_RESOURCE_PAYLOADS:
            payload = {"ok": True, "result": LOCAL_RESOURCE_PAYLOADS[uri]()}
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    }
                ]
            }
        if uri == "houdini://workflow-templates/risk-domains":
            payload = {"ok": True, "result": _risk_domain_template_index()}
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    }
                ]
            }
        if uri == "houdini://adapter/status":
            payload = {"ok": True, "result": self.status(include_health=True)}
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                    }
                ]
            }
        if uri not in RESOURCE_PAYLOADS:
            raise McpError(-32602, "Unknown Houdini MCP resource: %s" % uri)
        spec = RESOURCE_PAYLOADS[uri]
        if spec is None:
            payload = {"ok": True, "result": self._sanitized_session()}
        else:
            command, arguments = spec
            payload = self._rpc(command, arguments)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                }
            ]
        }

    def _rpc(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session_loader(self.session_path)
        if session is None:
            return {
                "ok": False,
                "command": command,
                "result": {},
                "error": {
                    "code": "offline",
                    "message": "No active Blib Houdini Bridge session was found. Start the Houdini bridge shelf tool first.",
                },
            }
        try:
            request = protocol.make_request(command, payload, token=session["token"])
        except Exception as exc:
            return {
                "ok": False,
                "command": command,
                "result": {},
                "error": {"code": "bad_request", "message": str(exc)},
            }
        return self._poster(session, request)

    def _post_to_bridge(self, session: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(request).encode("utf-8")
        http_request = urllib.request.Request(
            "http://%s:%s/rpc" % (session["host"], session["port"]),
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Blib-Bridge-Token": session["token"],
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:
                return _bridge_error("http_error", "Bridge HTTP error: %s" % exc)
        except Exception as exc:
            return _bridge_error("unreachable", "Could not reach Blib Houdini Bridge: %s" % exc)

    def _sanitized_session(self) -> dict[str, Any]:
        session = self._session_loader(self.session_path)
        if session is None:
            return {"connected": False, "session_path": str(auth.default_session_path())}
        return {
            "connected": True,
            "host": session.get("host"),
            "port": session.get("port"),
            "pid": session.get("pid"),
            "started_at": session.get("started_at"),
            "has_token": bool(session.get("token")),
        }

    def _workflow_resources(self) -> list[dict[str, Any]]:
        resources = []
        if not self.workflow_root.exists():
            return resources
        for child in sorted(self.workflow_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or not _safe_workflow_name(child.name):
                continue
            resources.extend(
                [
                    _workflow_resource_descriptor(child.name, "evidence-manifest", "application/json", "Workflow evidence manifest"),
                    _workflow_resource_descriptor(child.name, "evidence-checklist", "application/json", "Workflow evidence completeness checklist"),
                    _workflow_resource_descriptor(child.name, "proof-report", "application/json", "Workflow compact proof verdict for AI clients"),
                    _workflow_resource_descriptor(child.name, "summary", "text/markdown", "Workflow human-readable summary"),
                    _workflow_resource_descriptor(child.name, "rollback-plan", "application/json", "Workflow rollback draft plan"),
                    _workflow_resource_descriptor(child.name, "visual-evidence", "application/json", "Workflow visual evidence capture summary"),
                ]
            )
        return resources

    def _workflow_index(self) -> dict[str, Any]:
        workflows = []
        if self.workflow_root.exists():
            for child in sorted(self.workflow_root.iterdir(), key=lambda item: item.name.lower()):
                if child.is_dir() and _safe_workflow_name(child.name):
                    workflows.append(_workflow_index_entry(child))
        return {
            "version": 1,
            "workflow_root": str(self.workflow_root),
            "count": len(workflows),
            "workflows": workflows,
            "resource_kinds": sorted(_workflow_resource_kinds()),
            "note": "This is a local evidence index. It reads workflow artifacts only and does not contact Houdini.",
        }

    def _read_workflow_resource(self, uri: str) -> dict[str, Any] | None:
        prefix = "houdini://workflow/"
        if not uri.startswith(prefix):
            return None
        parts = uri[len(prefix) :].strip("/").split("/")
        if len(parts) != 2 or not _safe_workflow_name(parts[0]):
            raise McpError(-32602, "Workflow resource URI must be houdini://workflow/NAME/KIND.")
        name, kind = parts
        filename, mime_type = _workflow_resource_file(kind)
        path = self.workflow_root / name / filename
        if not path.exists() or not path.is_file():
            raise McpError(-32602, "Workflow resource was not found: %s" % uri)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise McpError(-32603, "Could not read workflow resource %s: %s" % (uri, exc)) from exc
        return {"contents": [{"uri": uri, "mimeType": mime_type, "text": text}]}

    @staticmethod
    def _params(message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params") or {}
        if not isinstance(params, dict):
            raise McpError(-32602, "MCP params must be a JSON object.")
        return params


def run_stdio(adapter: BridgeMCPAdapter | None = None) -> int:
    adapter = adapter or BridgeMCPAdapter()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise McpError(-32600, "MCP message must be a JSON object.")
            response = adapter.handle_message(message)
        except McpError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    session_path = _option_value(argv, "--session")
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(
            "Usage: blib_hou_mcp.py [--session PATH] [--print-config] [--print-codex-config] [--status|--doctor]\n"
            "\n"
            "Runs a stdio MCP adapter for the local Blib Houdini Bridge.\n"
        )
        return 0
    if "--print-codex-config" in argv:
        script = _default_cli_path()
        args = [script]
        if session_path:
            args.extend(["--session", session_path])
        sys.stdout.write(_codex_config_toml(sys.executable, args) + "\n")
        return 0
    if "--print-config" in argv:
        script = _default_cli_path()
        args = [script]
        if session_path:
            args.extend(["--session", session_path])
        config = {
            "mcpServers": {
                "blib-houdini-bridge": {
                    "command": sys.executable,
                    "args": args,
                }
            }
        }
        sys.stdout.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        return 0
    if "--status" in argv or "--doctor" in argv:
        adapter = BridgeMCPAdapter(session_path=session_path)
        sys.stdout.write(json.dumps(adapter.status(include_health=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0
    return run_stdio(BridgeMCPAdapter(session_path=session_path))


def _codex_config_toml(command: str, args: list[str]) -> str:
    lines = [
        "[mcp_servers.blib-houdini-bridge]",
        "command = %s" % json.dumps(command),
        "args = [",
    ]
    lines.extend("  %s," % json.dumps(arg) for arg in args)
    lines.append("]")
    return "\n".join(lines)


def _tool_name(command: str) -> str:
    return "houdini_%s" % command


def _command_from_tool(tool_name: str) -> str:
    if not tool_name.startswith("houdini_"):
        return ""
    return protocol.normalize_command(tool_name[len("houdini_") :])


def _tool_description(command: str, metadata: dict[str, Any]) -> str:
    exposure = metadata.get("exposure") or protocol.command_exposure(command)
    suffix = {
        "read": "Read-only.",
        "direct_edit": "Direct edit; requires Houdini bridge edit mode.",
        "plan_required": "Plan transaction entry; use review/validate/verify for high-risk edits.",
    }.get(exposure, "Bridge tool.")
    return "%s %s" % (metadata.get("description", "Call a Houdini bridge command."), suffix)


def _tool_safety_contract(command: str, metadata: dict[str, Any]) -> dict[str, Any]:
    exposure = str(metadata.get("exposure") or protocol.command_exposure(command))
    requires_edit_mode = command in protocol.EDIT_COMMANDS
    direct_call_allowed = command in READ_TOOL_COMMANDS or command in DIRECT_EDIT_TOOL_COMMANDS or command in PLAN_TOOL_COMMANDS
    plan_required = exposure == "plan_required"
    contract = {
        "version": 1,
        "bridge_command": command,
        "exposure": exposure,
        "permission": metadata.get("permission") or ("edit" if requires_edit_mode else "read"),
        "direct_call_allowed": bool(direct_call_allowed),
        "requires_bridge_edit_mode": bool(requires_edit_mode),
        "requires_user_approval_for_writes": bool(requires_edit_mode),
        "requires_review_flow": bool(plan_required),
        "required_review_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"]
        if plan_required
        else [],
        "may_execute_without_review": exposure in {"read", "direct_edit"},
        "may_report_success": False,
        "success_requires_evidence": True,
        "note": "Tool metadata is an audit contract only; bridge RPC and Houdini edit mode remain authoritative.",
    }
    verification = metadata.get("verification") if isinstance(metadata.get("verification"), dict) else None
    if verification is None and command in DIRECT_EDIT_TOOL_COMMANDS:
        verification = protocol.direct_edit_verification_contract(command)
    if verification is not None:
        contract["verification"] = verification
    return contract


def _local_template_tool_safety_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "bridge_command": None,
        "exposure": "read",
        "permission": "read",
        "direct_call_allowed": True,
        "requires_bridge_edit_mode": False,
        "requires_user_approval_for_writes": False,
        "requires_review_flow": False,
        "required_review_flow": [],
        "may_execute_without_review": True,
        "may_report_success": False,
        "success_requires_evidence": True,
        "local_generation_only": True,
        "does_not_contact_houdini": True,
        "does_not_execute": True,
        "note": "Template planning creates draft command lists only; generated plans still require review, validation, run, and verification.",
    }


def _tool_policy_audit(tools: list[dict[str, Any]]) -> dict[str, Any]:
    issues = []
    exposed_commands = []
    direct_edit_tools = []
    plan_transaction_tools = []
    read_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        meta = tool.get("_meta", {}) if isinstance(tool.get("_meta"), dict) else {}
        safety = meta.get("safety", {}) if isinstance(meta.get("safety"), dict) else {}
        command = meta.get("bridgeCommand")
        exposure = meta.get("exposure")
        name = str(tool.get("name") or "")
        if command:
            exposed_commands.append(str(command))
        if exposure == "read":
            read_tools.append(name)
        elif exposure == "direct_edit":
            direct_edit_tools.append(name)
            if not safety.get("requires_bridge_edit_mode"):
                issues.append({"tool": name, "kind": "direct_edit_without_edit_mode_contract"})
            if safety.get("requires_review_flow"):
                issues.append({"tool": name, "kind": "direct_edit_marked_plan_required"})
            verification = safety.get("verification") if isinstance(safety.get("verification"), dict) else {}
            if not verification:
                issues.append({"tool": name, "kind": "direct_edit_missing_verification_contract"})
            else:
                if verification.get("requires_readback") is not True:
                    issues.append({"tool": name, "kind": "direct_edit_verification_without_readback"})
                if verification.get("may_report_success_from_rpc_ok") is not False:
                    issues.append({"tool": name, "kind": "direct_edit_verification_allows_rpc_success_claim"})
                verification_read_tools = verification.get("read_tools") if isinstance(verification.get("read_tools"), list) else []
                verification_mcp_read_tools = verification.get("mcp_read_tools") if isinstance(verification.get("mcp_read_tools"), list) else []
                success_criteria = verification.get("success_criteria") if isinstance(verification.get("success_criteria"), list) else []
                if not verification_read_tools:
                    issues.append({"tool": name, "kind": "direct_edit_verification_without_read_tools"})
                if not verification_mcp_read_tools:
                    issues.append({"tool": name, "kind": "direct_edit_verification_without_mcp_read_tools"})
                if not success_criteria:
                    issues.append({"tool": name, "kind": "direct_edit_verification_without_success_criteria"})
                for read_tool in verification_read_tools:
                    if read_tool not in protocol.READ_COMMANDS:
                        issues.append({"tool": name, "kind": "direct_edit_verification_unknown_read_tool", "read_tool": read_tool})
                exposed_read_tool_names = {_tool_name(read_tool) for read_tool in READ_TOOL_COMMANDS}
                for mcp_read_tool in verification_mcp_read_tools:
                    if mcp_read_tool not in exposed_read_tool_names:
                        issues.append({"tool": name, "kind": "direct_edit_verification_unexposed_mcp_read_tool", "mcp_read_tool": mcp_read_tool})
        elif exposure == "plan_required":
            plan_transaction_tools.append(name)
            if not safety.get("requires_review_flow"):
                issues.append({"tool": name, "kind": "plan_tool_without_review_flow_contract"})
        else:
            issues.append({"tool": name, "kind": "unknown_exposure", "exposure": exposure})
        if command in protocol.PLAN_REQUIRED_EDIT_COMMANDS:
            issues.append({"tool": name, "kind": "plan_required_edit_exposed_directly", "command": command})
        if safety and safety.get("may_report_success"):
            issues.append({"tool": name, "kind": "tool_contract_grants_success_claim"})
    hidden_plan_required = sorted(
        command for command in protocol.PLAN_REQUIRED_EDIT_COMMANDS if command not in set(exposed_commands)
    )
    exposed_plan_required = sorted(
        command for command in protocol.PLAN_REQUIRED_EDIT_COMMANDS if command in set(exposed_commands)
    )
    return {
        "ok": not issues and not exposed_plan_required,
        "issue_count": len(issues) + len(exposed_plan_required),
        "issues": issues,
        "read_tool_count": len(read_tools),
        "direct_edit_tool_count": len(direct_edit_tools),
        "plan_transaction_tool_count": len(plan_transaction_tools),
        "hidden_plan_required_commands": hidden_plan_required,
        "exposed_plan_required_commands": exposed_plan_required,
        "required_review_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        "note": "This audit compares MCP tool metadata with bridge protocol exposure policy.",
    }


def _tool_input_schema(command: str, metadata: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(metadata.get("payload_schema") or {"type": "object"})
    if command != "run_plan":
        return schema
    properties = dict(schema.get("properties", {}))
    properties["validation"] = {
        "type": "object",
        "description": "Result object from houdini_validate_plan for the same steps.",
    }
    properties["review"] = {
        "type": "object",
        "description": "Result object from houdini_review_plan for the same steps.",
    }
    properties["confirmed_required_confirmations"] = {
        "type": "array",
        "description": "Exact required_confirmations strings from houdini_review_plan that the user/client explicitly approved.",
        "items": {"type": "string"},
        "default": [],
    }
    schema["properties"] = properties
    required = list(schema.get("required", []))
    for key in ("validation", "review"):
        if key not in required:
            required.append(key)
    schema["required"] = required
    return schema


def _tool_output_schema(command: str, metadata: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(metadata.get("result_schema") or {"type": "object"})
    properties = dict(schema.get("properties", {}))
    if command == "run_plan":
        properties["mcp_preflight"] = _mcp_run_plan_preflight_schema()
        properties["next_required_tool"] = {
            "type": "string",
            "description": "Next MCP tool required before clients may claim the plan succeeded.",
        }
    elif command in DIRECT_EDIT_TOOL_COMMANDS:
        properties["mcp_postflight"] = _mcp_direct_edit_postflight_schema()
    if properties:
        schema["properties"] = properties
    return schema


def _mcp_run_plan_preflight_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "MCP-local run_plan preflight evidence proving review, validation, confirmations, and steps matched before bridge RPC.",
        "properties": {
            "step_count": {"type": "integer"},
            "steps_sha256": {"type": "string"},
            "validation": {"type": "object"},
            "review": {"type": "object"},
            "required_workflow": {"type": "array", "items": {"type": "string"}},
        },
    }


def _mcp_direct_edit_postflight_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "MCP-local postflight guidance for direct edits; RPC success still requires read-back evidence before reporting task success.",
        "properties": {
            "version": {"type": "integer"},
            "command": {"type": "string"},
            "may_report_success": {"type": "boolean"},
            "success_requires_evidence": {"type": "boolean"},
            "next_required_step": {"type": "string"},
            "next_required_tools": {"type": "array", "items": {"type": "string"}},
            "changed_paths": {"type": "array", "items": {"type": "string"}},
            "touched_paths": {"type": "array", "items": {"type": "string"}},
            "created_paths": {"type": "array", "items": {"type": "string"}},
            "deleted_paths": {"type": "array", "items": {"type": "string"}},
            "suggested_read_payloads": {"type": "array", "items": {"type": "object"}},
            "verification": {"type": "object"},
            "note": {"type": "string"},
        },
    }


def _prepare_tool_arguments(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if command != "run_plan":
        return {"ok": True, "payload": arguments}
    steps = arguments.get("steps")
    validation = _bridge_result(arguments.get("validation"))
    review = _bridge_result(arguments.get("review"))
    confirmed_required_confirmations = arguments.get("confirmed_required_confirmations", [])
    issues: list[str] = []
    blocked_by: list[str] = []
    steps_sha256 = _steps_sha256(steps) if isinstance(steps, list) else ""
    step_count = len(steps) if isinstance(steps, list) else None
    if not isinstance(steps, list):
        issues.append("run_plan requires a steps array.")
        blocked_by.append("missing_steps")
    if not validation:
        issues.append("houdini_run_plan requires validation from houdini_validate_plan.")
        blocked_by.append("missing_validation")
    elif not validation.get("valid", False) or not validation.get("ready_to_run", False):
        issues.append("Validation is not ready to run.")
        blocked_by.append("validation_not_ready")
    if validation and isinstance(step_count, int):
        validation_step_count = validation.get("step_count")
        if isinstance(validation_step_count, int) and validation_step_count != step_count:
            issues.append("Validation step_count does not match run_plan steps.")
            blocked_by.append("validation_step_count_mismatch")
        validation_steps_sha256 = validation.get("steps_sha256")
        if validation_steps_sha256 and validation_steps_sha256 != steps_sha256:
            issues.append("Validation steps_sha256 does not match run_plan steps.")
            blocked_by.append("validation_steps_sha256_mismatch")
    if not review:
        issues.append("houdini_run_plan requires review from houdini_review_plan.")
        blocked_by.append("missing_review")
    elif review.get("level") == "blocked":
        issues.append("Review is blocked.")
        blocked_by.append("review_blocked")
    if review and isinstance(step_count, int):
        review_validation = review.get("validation") if isinstance(review.get("validation"), dict) else {}
        review_step_count = review.get("step_count")
        if isinstance(review_step_count, int) and review_step_count != step_count:
            issues.append("Review step_count does not match run_plan steps.")
            blocked_by.append("review_step_count_mismatch")
        review_steps_sha256 = review.get("steps_sha256") or review_validation.get("steps_sha256")
        if review_steps_sha256 and review_steps_sha256 != steps_sha256:
            issues.append("Review steps_sha256 does not match run_plan steps.")
            blocked_by.append("review_steps_sha256_mismatch")
        if review_validation:
            nested_validation_step_count = review_validation.get("step_count")
            if isinstance(nested_validation_step_count, int) and nested_validation_step_count != step_count:
                issues.append("Review validation step_count does not match run_plan steps.")
                blocked_by.append("review_validation_step_count_mismatch")
    required_confirmations = _string_list(review.get("required_confirmations")) if review else []
    confirmed_confirmations = _string_list(confirmed_required_confirmations)
    missing_confirmations = [item for item in required_confirmations if item not in confirmed_confirmations]
    if required_confirmations and not isinstance(confirmed_required_confirmations, list):
        issues.append("confirmed_required_confirmations must be an array when review requires confirmations.")
        blocked_by.append("invalid_confirmed_required_confirmations")
    if missing_confirmations:
        issues.append("Required review confirmations were not explicitly confirmed.")
        blocked_by.append("missing_required_confirmations")
    if issues:
        return {
            "ok": False,
            "command": command,
            "result": {
                "required_workflow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
                "blocked_before_bridge_rpc": True,
                "blocked_by": _unique_ordered(blocked_by),
                "step_count": step_count,
                "steps_sha256": steps_sha256,
                "required_confirmations": required_confirmations,
                "confirmed_required_confirmations": confirmed_confirmations,
                "missing_required_confirmations": missing_confirmations,
            },
            "error": {
                "code": "plan_preflight_required",
                "message": " ".join(issues),
            },
        }
    payload = {key: value for key, value in arguments.items() if key not in {"validation", "review", "confirmed_required_confirmations"}}
    return {
        "ok": True,
        "payload": payload,
        "preflight": {
            "step_count": step_count,
            "steps_sha256": steps_sha256,
            "validation": {
                "valid": validation.get("valid"),
                "ready_to_run": validation.get("ready_to_run"),
                "step_count": validation.get("step_count"),
                "would_require_edit": validation.get("would_require_edit"),
            },
            "review": {
                "level": review.get("level"),
                "confidence": review.get("confidence"),
                "required_confirmations": required_confirmations,
                "confirmed_required_confirmations": confirmed_confirmations,
            },
            "required_workflow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        },
    }


def _steps_sha256(steps: list[Any]) -> str:
    text = json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bridge_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = value.get("result")
    if isinstance(result, dict):
        return result
    structured = value.get("structuredContent")
    if isinstance(structured, dict):
        nested_result = structured.get("result")
        if isinstance(nested_result, dict):
            return nested_result
    return value


def _template_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["template", "input"],
        "properties": {
            "template": {"type": "string", "enum": list(workflow_templates.TEMPLATE_NAMES)},
            "input": {"type": "string", "format": "houdini_absolute_node_path"},
            "options": {"type": "object", "default": {}},
        },
        "additionalProperties": False,
    }


def _template_plan_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Local template expansion result. The returned plan is reviewable JSON and is not executed.",
        "properties": {
            "template": {"type": "string", "description": "Normalized workflow template name."},
            "input": {"type": "string", "format": "houdini_absolute_node_path"},
            "options": {"type": "object", "description": "Sanitized template options used to generate the plan."},
            "catalog": {
                "type": "object",
                "description": "Template catalog entry, including category, presets, optional flags, execution policy, risk domains, evidence expectations, and verification focus.",
            },
            "workflow_policy": {
                "type": "object",
                "description": "Shared template workflow policy. Templates generate plans locally, do not contact Houdini, do not execute, and require review/validate/run/verify.",
                "properties": {
                    "local_generation_only": {"type": "boolean"},
                    "does_not_contact_houdini": {"type": "boolean"},
                    "does_not_execute": {"type": "boolean"},
                    "plan_is_reviewable_json": {"type": "boolean"},
                    "required_flow": {"type": "array", "items": {"type": "string"}},
                    "evidence_expectations": {"type": "array", "items": {"type": "string"}},
                    "verification_focus_required": {"type": "boolean"},
                },
            },
            "plan": {"type": "array", "description": "Generated bridge command steps. Review and validate before running."},
            "step_count": {"type": "integer"},
            "workflow_contract": {
                "type": "object",
                "description": "Client-facing execution contract for this draft. The plan is not executed and cannot be reported as success until review, validation, run, and verification complete.",
                "properties": {
                    "state": {"type": "string"},
                    "local_generation_only": {"type": "boolean"},
                    "does_not_contact_houdini": {"type": "boolean"},
                    "does_not_execute": {"type": "boolean"},
                    "requires_review": {"type": "boolean"},
                    "requires_validation": {"type": "boolean"},
                    "requires_bridge_edit_mode_to_run": {"type": "boolean"},
                    "required_flow": {"type": "array", "items": {"type": "string"}},
                    "evidence_expectations": {"type": "array", "items": {"type": "string"}},
                    "verification_focus": {"type": "object"},
                    "cannot_report_success_before": {"type": "array", "items": {"type": "string"}},
                },
            },
            "evidence_expectations": {"type": "array", "items": {"type": "string"}},
            "verification_focus": {
                "type": "object",
                "description": "Template-specific verification focus: read tools, success criteria, evidence artifacts, and notes to use after execution.",
            },
            "next_required_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "MCP tools required before and after execution.",
            },
            "client_guidance": {
                "type": "object",
                "description": "Next-step guidance for MCP clients consuming a local template draft.",
                "properties": {
                    "next_action": {"type": "string"},
                    "suggested_tools": {"type": "array", "items": {"type": "string"}},
                    "read_resources": {"type": "array", "items": {"type": "string"}},
                    "risk_domains": {"type": "array", "items": {"type": "string"}},
                    "verification_focus": {"type": "object"},
                    "requires_user_approval_for_writes": {"type": "boolean"},
                    "may_execute": {"type": "boolean"},
                    "instruction": {"type": "string"},
                },
            },
            "note": {"type": "string"},
        },
    }


def _template_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    template = arguments.get("template")
    input_path = arguments.get("input")
    options = arguments.get("options", {})
    issues = []
    if not isinstance(template, str) or not template.strip():
        issues.append("template is required.")
    elif template.strip().lower().replace("_", "-") not in workflow_templates.TEMPLATE_NAMES:
        issues.append("Unknown workflow template: %s." % template)
    if not isinstance(input_path, str) or not input_path.startswith("/") or input_path.rstrip("/") == "":
        issues.append("input must be an absolute Houdini node path.")
    if not isinstance(options, dict):
        issues.append("options must be a JSON object when provided.")
    if issues:
        return {
            "ok": False,
            "command": "template_plan",
            "result": {},
            "error": {"code": "bad_template_plan", "message": " ".join(issues)},
        }
    normalized_template = template.strip().lower().replace("_", "-")
    sanitized_options = deepcopy(options)
    try:
        plan = workflow_templates.build_plan(normalized_template, input_path, sanitized_options)
    except Exception as exc:
        return {
            "ok": False,
            "command": "template_plan",
            "result": {},
            "error": {"code": "bad_template_plan", "message": str(exc)},
        }
    catalog = workflow_templates.template_catalog()
    template_catalog_entry = catalog.get("templates", {}).get(normalized_template, {})
    workflow_policy = catalog.get("workflow_policy", {})
    required_flow = workflow_policy.get("required_flow", workflow_templates.TEMPLATE_REQUIRED_FLOW)
    if not isinstance(required_flow, list) or not required_flow:
        required_flow = list(workflow_templates.TEMPLATE_REQUIRED_FLOW)
    evidence_expectations = workflow_policy.get("evidence_expectations", workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)
    if not isinstance(evidence_expectations, list) or not evidence_expectations:
        evidence_expectations = list(workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)
    risk_domains = template_catalog_entry.get("risk_domains", []) if isinstance(template_catalog_entry, dict) else []
    if not isinstance(risk_domains, list):
        risk_domains = []
    verification_focus = (
        template_catalog_entry.get("verification_focus", {})
        if isinstance(template_catalog_entry, dict) and isinstance(template_catalog_entry.get("verification_focus"), dict)
        else {}
    )
    workflow_contract = {
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
    }
    return {
        "ok": True,
        "command": "template_plan",
        "result": {
            "template": normalized_template,
            "input": input_path,
            "options": sanitized_options,
            "catalog": template_catalog_entry,
            "workflow_policy": workflow_policy,
            "plan": plan,
            "step_count": len(plan),
            "workflow_contract": workflow_contract,
            "evidence_expectations": list(evidence_expectations),
            "verification_focus": verification_focus,
            "next_required_tools": list(required_flow),
            "client_guidance": {
                "next_action": "review_template_plan",
                "suggested_tools": list(required_flow),
                "read_resources": [
                    "houdini://workflow-templates/risk-domains",
                    "houdini://safety/policy",
                ],
                "risk_domains": [str(domain) for domain in risk_domains if isinstance(domain, str) and domain],
                "verification_focus": verification_focus,
                "requires_user_approval_for_writes": True,
                "may_execute": False,
                "instruction": "Treat this as a local draft only. Review, validate, run through houdini_run_plan, and verify before reporting success.",
            },
            "note": "Generated locally; this is a draft plan, not executed work.",
        },
    }


def _risk_domain_template_index() -> dict[str, Any]:
    catalog = workflow_templates.template_catalog()
    templates = catalog.get("templates", {}) if isinstance(catalog.get("templates"), dict) else {}
    workflow_policy = catalog.get("workflow_policy", {}) if isinstance(catalog.get("workflow_policy"), dict) else {}
    required_flow = workflow_policy.get("required_flow", workflow_templates.TEMPLATE_REQUIRED_FLOW)
    evidence = workflow_policy.get("evidence_expectations", workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS)
    domains: dict[str, dict[str, Any]] = {}
    for template_name, template in sorted(templates.items()):
        if not isinstance(template, dict):
            continue
        risk_domains = template.get("risk_domains") if isinstance(template.get("risk_domains"), list) else []
        for domain in risk_domains:
            if not isinstance(domain, str) or not domain:
                continue
            entry = domains.setdefault(
                domain,
                {
                    "domain": domain,
                    "templates": [],
                    "template_count": 0,
                    "categories": [],
                    "safe_read_tools": _risk_domain_read_tools(domain),
                    "required_flow": list(required_flow) if isinstance(required_flow, list) else list(workflow_templates.TEMPLATE_REQUIRED_FLOW),
                    "evidence_expectations": list(evidence) if isinstance(evidence, list) else list(workflow_templates.TEMPLATE_EVIDENCE_EXPECTATIONS),
                    "requires_plan_review": True,
                    "local_generation_only": True,
                    "does_not_contact_houdini": True,
                    "does_not_execute": True,
                    "next_actions": [
                        "Inspect this domain with safe read tools before planning edits.",
                        "Use houdini_template_plan only to draft a plan, then review/validate/run/verify.",
                    ],
                },
            )
            category = str(template.get("category") or "")
            if category and category not in entry["categories"]:
                entry["categories"].append(category)
            entry["templates"].append(
                {
                    "template": str(template_name),
                    "category": category,
                    "description": str(template.get("description") or ""),
                    "presets": list(template.get("presets", [])) if isinstance(template.get("presets"), list) else [],
                    "optional_flags": list(template.get("optional_flags", [])) if isinstance(template.get("optional_flags"), list) else [],
                    "mcp_tool": "houdini_template_plan",
                }
            )
    ordered_domains = []
    for domain in sorted(domains):
        entry = domains[domain]
        entry["templates"] = sorted(entry["templates"], key=lambda item: item["template"])
        entry["template_count"] = len(entry["templates"])
        entry["categories"] = sorted(entry["categories"])
        ordered_domains.append(entry)
    return {
        "version": 1,
        "local_only": True,
        "resource": "houdini://workflow-templates/risk-domains",
        "domain_count": len(ordered_domains),
        "domains": {entry["domain"]: entry for entry in ordered_domains},
        "domain_names": [entry["domain"] for entry in ordered_domains],
        "workflow_policy": workflow_policy,
        "template_catalog_resource": "houdini://workflow-templates/catalog",
        "scene_routing_source": "houdini://adapter/status",
        "note": "Risk-domain index is generated locally from workflow template metadata. It does not contact Houdini or execute plans.",
    }


def _risk_domain_read_tools(domain: str) -> list[str]:
    if domain in {"node_errors", "node_warnings", "network_output"}:
        return ["houdini_network", "houdini_node_info"]
    if domain in {"cache_output", "file_path", "render_settings", "camera_material_review"}:
        return ["houdini_node_info", "houdini_node_parms", "houdini_upstream"]
    if domain in {"simulation_settings", "cache_strategy", "cook_cost", "volume_resolution"}:
        return ["houdini_node_info", "houdini_node_parms", "houdini_upstream"]
    return ["houdini_scene_snapshot"]


def _annotate_run_plan_response(response: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    annotated = deepcopy(response)
    result = annotated.setdefault("result", {})
    if isinstance(result, dict):
        result["mcp_preflight"] = prepared.get("preflight", {})
        result["next_required_tool"] = "houdini_verify_plan"
    return annotated


def _annotate_direct_edit_response(command: str, response: dict[str, Any]) -> dict[str, Any]:
    annotated = deepcopy(response)
    if not annotated.get("ok"):
        return annotated
    result = annotated.setdefault("result", {})
    if not isinstance(result, dict):
        return annotated
    result["mcp_postflight"] = _direct_edit_postflight(command, result)
    return annotated


def _direct_edit_postflight(command: str, result: dict[str, Any]) -> dict[str, Any]:
    touched = _paths_from_result(result, "touched")
    created = _created_paths_from_result(result)
    deleted = _paths_from_result(result, "deleted")
    target_paths = _target_paths_from_result(result)
    changed = _unique_ordered(touched + created + target_paths)
    verification_tools = _direct_edit_verification_tools(command, touched, created, deleted)
    return {
        "version": 1,
        "command": command,
        "may_report_success": False,
        "success_requires_evidence": True,
        "next_required_step": "read_back_changed_state",
        "next_required_tools": verification_tools,
        "changed_paths": changed,
        "touched_paths": touched,
        "created_paths": created,
        "deleted_paths": deleted,
        "suggested_read_payloads": _direct_edit_read_payloads(command, changed, deleted),
        "verification": protocol.direct_edit_verification_contract(command),
        "note": "Direct edit RPC success only proves the bridge command returned ok; read back changed Houdini state before reporting task success.",
    }


def _paths_from_result(result: dict[str, Any], key: str) -> list[str]:
    value = result.get(key)
    if isinstance(value, list):
        return _unique_ordered(value)
    if isinstance(value, str):
        return _unique_ordered([value])
    return []


def _created_paths_from_result(result: dict[str, Any]) -> list[str]:
    created = result.get("created")
    if isinstance(created, dict):
        return _unique_ordered([created.get("path")])
    if isinstance(created, list):
        paths = [item.get("path") if isinstance(item, dict) else item for item in created]
        return _unique_ordered(paths)
    if isinstance(created, str):
        return _unique_ordered([created])
    replacement = result.get("replacement")
    if isinstance(replacement, dict):
        return _unique_ordered([replacement.get("path")])
    return []


def _target_paths_from_result(result: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("node", "path", "dst", "src", "old_path"):
        values.append(result.get(key))
    moved = result.get("moved")
    if isinstance(moved, dict):
        values.append(moved.get("path"))
    return _unique_ordered(values)


def _direct_edit_verification_tools(command: str, touched: list[str], created: list[str], deleted: list[str]) -> list[str]:
    tools = ["houdini_rpc_log"]
    contract = protocol.direct_edit_verification_contract(command)
    contract_tools = contract.get("mcp_read_tools") if isinstance(contract.get("mcp_read_tools"), list) else []
    if contract_tools:
        tools.extend(str(tool) for tool in contract_tools if isinstance(tool, str))
    elif command in {"connect", "set_input", "disconnect"}:
        tools.extend(["houdini_network", "houdini_upstream", "houdini_downstream"])
    elif command in {"layout", "select"}:
        tools.extend(["houdini_scene_snapshot", "houdini_network"])
    elif command in {"set_parm", "batch_set_parms", "set_comment", "set_flags", "set_position", "set_node_color", "bypass_node"}:
        tools.extend(["houdini_node_info", "houdini_node_parms"])
    elif created:
        tools.extend(["houdini_node_info", "houdini_network"])
    elif deleted:
        tools.extend(["houdini_network", "houdini_scene_snapshot"])
    else:
        tools.extend(["houdini_scene_snapshot", "houdini_node_info"])
    return _unique_ordered(tools)


def _direct_edit_read_payloads(command: str, changed: list[str], deleted: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    primary = changed[0] if changed else ""
    parent = _parent_path(primary) if primary else ""
    contract = protocol.direct_edit_verification_contract(command)
    for tool in contract.get("mcp_read_tools", []):
        if not isinstance(tool, str):
            continue
        if tool in {"houdini_node_info", "houdini_node_parms", "houdini_upstream", "houdini_downstream"} and primary:
            arguments = {"path": primary}
            if tool in {"houdini_upstream", "houdini_downstream"}:
                arguments["depth"] = 2
            payloads.append({"tool": tool, "arguments": arguments})
        elif tool == "houdini_network":
            network_path = primary if command == "layout" else parent
            if network_path:
                payloads.append({"tool": tool, "arguments": {"path": network_path}})
        elif tool == "houdini_scene_snapshot":
            arguments = {"path": primary} if primary and command == "layout" else {}
            payloads.append({"tool": tool, "arguments": arguments})
        elif tool == "houdini_selected":
            payloads.append({"tool": tool, "arguments": {}})
    if not payloads and deleted:
        deleted_parent = _parent_path(deleted[0])
        if deleted_parent:
            payloads.append({"tool": "houdini_network", "arguments": {"path": deleted_parent}})
    payloads.append({"tool": "houdini_rpc_log", "arguments": {"limit": 20}})
    return payloads


def _parent_path(path: str) -> str:
    if not isinstance(path, str) or "/" not in path.strip("/"):
        return ""
    return path.rsplit("/", 1)[0] or "/"


def _tool_result(response: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": response,
        "isError": bool(is_error),
    }


def _bridge_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "result": {}, "error": {"code": code, "message": message}}


def _default_cli_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(root, "scripts", "cli", "blib_hou_mcp.py")


def _option_value(argv: list[str], option: str) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        raise McpError(-32602, "%s requires a value." % option)
    return argv[index + 1]


def _default_workflow_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / ".blib_hou_workflows"


def _safe_workflow_name(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and all(ch.isalnum() or ch in "._-" for ch in value)


def _workflow_resource_descriptor(name: str, kind: str, mime_type: str, description: str) -> dict[str, str]:
    return {
        "uri": "houdini://workflow/%s/%s" % (name, kind),
        "name": "%s %s" % (name, kind.replace("-", " ")),
        "description": description,
        "mimeType": mime_type,
    }


def _workflow_resource_file(kind: str) -> tuple[str, str]:
    mapping = {
        "evidence-manifest": ("evidence_manifest.json", "application/json"),
        "evidence-checklist": ("evidence_checklist.json", "application/json"),
        "proof-report": ("proof_report.json", "application/json"),
        "summary": ("summary.md", "text/markdown"),
        "rollback-plan": ("rollback_plan.json", "application/json"),
        "visual-evidence": ("visual_evidence.json", "application/json"),
    }
    if kind not in mapping:
        raise McpError(-32602, "Unknown workflow resource kind: %s" % kind)
    return mapping[kind]


def _workflow_resource_kinds() -> set[str]:
    return {
        "evidence-manifest",
        "evidence-checklist",
        "proof-report",
        "summary",
        "rollback-plan",
        "visual-evidence",
    }


def _workflow_visual_digest(
    proof_report: dict[str, Any],
    manifest: dict[str, Any],
    visual_evidence: dict[str, Any],
    workflow_name: str,
) -> dict[str, Any]:
    proof_visual = proof_report.get("visual") if isinstance(proof_report, dict) and isinstance(proof_report.get("visual"), dict) else {}
    guidance = proof_report.get("client_guidance") if isinstance(proof_report, dict) and isinstance(proof_report.get("client_guidance"), dict) else {}
    visual_guidance = guidance.get("visual_guidance") if isinstance(guidance.get("visual_guidance"), dict) else {}
    manifest_visual = manifest.get("visual") if isinstance(manifest, dict) and isinstance(manifest.get("visual"), dict) else {}
    raw = visual_evidence if isinstance(visual_evidence, dict) else {}
    exists = bool(proof_visual or manifest_visual or raw or visual_guidance)
    captured = bool(proof_visual.get("captured") or manifest_visual.get("captured") or raw.get("captured") or visual_guidance.get("captured"))
    semantic_verdict = (
        proof_visual.get("semantic_verdict")
        or visual_guidance.get("semantic_verdict")
        or manifest_visual.get("semantic_verdict")
        or raw.get("semantic_verdict")
        or ("not_judged" if captured else "missing")
    )
    proof_role = (
        proof_visual.get("proof_role")
        or visual_guidance.get("proof_role")
        or manifest_visual.get("proof_role")
        or raw.get("proof_role")
        or ("supporting_capture_only" if captured else "none")
    )
    may_report_visual_success = bool(
        proof_visual.get("may_report_visual_success")
        or visual_guidance.get("may_report_visual_success")
        or manifest_visual.get("may_report_visual_success")
        or raw.get("may_report_visual_success")
    ) and semantic_verdict == "pass"
    return {
        "exists": exists,
        "captured": captured,
        "status": proof_visual.get("status") or manifest_visual.get("status") or raw.get("status") or ("captured" if captured else "missing"),
        "path": proof_visual.get("path") or manifest_visual.get("path") or raw.get("path") or "",
        "proof_role": proof_role,
        "semantic_verdict": semantic_verdict,
        "requires_visual_judgment": bool(
            proof_visual.get("requires_visual_judgment")
            or visual_guidance.get("requires_visual_judgment")
            or manifest_visual.get("requires_visual_judgment")
            or raw.get("requires_visual_judgment")
            or (captured and semantic_verdict == "not_judged")
        ),
        "may_report_visual_success": may_report_visual_success,
        "visual_success_claim_allowed": bool(
            proof_visual.get("visual_success_claim_allowed")
            or visual_guidance.get("visual_success_claim_allowed")
            or manifest_visual.get("visual_success_claim_allowed")
            or raw.get("visual_success_claim_allowed")
        ) and semantic_verdict == "pass",
        "resource": visual_guidance.get("resource") or _workflow_resource_uri(workflow_name, "visual-evidence"),
        "note": "Screenshot capture is supporting evidence; semantic_verdict=pass is required before reporting visual success.",
    }


def _workflow_direct_edit_readback_digest(
    proof_report: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    proof_value = proof_report.get("direct_edit_readback") if isinstance(proof_report, dict) else {}
    manifest_proof = (
        manifest.get("proof_report", {})
        if isinstance(manifest, dict) and isinstance(manifest.get("proof_report"), dict)
        else {}
    )
    manifest_value = (
        manifest_proof.get("direct_edit_readback")
        if isinstance(manifest_proof.get("direct_edit_readback"), dict)
        else {}
    )
    source = proof_value if isinstance(proof_value, dict) and proof_value else manifest_value if isinstance(manifest_value, dict) else {}
    if not source:
        return {
            "exists": False,
            "proof_ready": True,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "inconclusive": 0,
            "commands": [],
            "failed_commands": [],
            "inconclusive_commands": [],
            "source": "missing",
        }
    failed = _int_value(source.get("failed"))
    inconclusive = _int_value(source.get("inconclusive"))
    total = _int_value(source.get("total"))
    proof_ready = bool(source.get("proof_ready")) and failed == 0 and inconclusive == 0
    return {
        "exists": True,
        "proof_ready": proof_ready,
        "total": total,
        "passed": _int_value(source.get("passed")),
        "failed": failed,
        "inconclusive": inconclusive,
        "commands": _string_list(source.get("commands")),
        "failed_commands": _string_list(source.get("failed_commands")),
        "inconclusive_commands": _string_list(source.get("inconclusive_commands")),
        "source": "proof_report" if source is proof_value else "evidence_manifest",
        "note": "When present, direct edit readback must be proof_ready with zero failed or inconclusive checks before success can be reported.",
    }


def _workflow_scene_evidence_digest(manifest: dict[str, Any]) -> dict[str, Any]:
    scene = manifest.get("scene_evidence") if isinstance(manifest, dict) and isinstance(manifest.get("scene_evidence"), dict) else {}
    if not scene:
        return {
            "exists": False,
            "before": {"exists": False},
            "after": {"exists": False},
            "transition": {},
            "may_execute": False,
            "safe_to_run_direct_edits": False,
            "requires_user_approval_for_writes": True,
        }
    before = scene.get("before") if isinstance(scene.get("before"), dict) else {}
    after = scene.get("after") if isinstance(scene.get("after"), dict) else {}
    transition = scene.get("transition") if isinstance(scene.get("transition"), dict) else {}
    return {
        "exists": bool(scene.get("exists")),
        "before": _workflow_scene_route_digest(before),
        "after": _workflow_scene_route_digest(after),
        "transition": {
            "inferred_purpose_changed": bool(transition.get("inferred_purpose_changed")),
            "primary_risk_domain_changed": bool(transition.get("primary_risk_domain_changed")),
            "risk_domains_added": transition.get("risk_domains_added", []) if isinstance(transition.get("risk_domains_added"), list) else [],
            "risk_domains_removed": transition.get("risk_domains_removed", []) if isinstance(transition.get("risk_domains_removed"), list) else [],
            "node_count_delta": transition.get("node_count_delta"),
            "wire_count_delta": transition.get("wire_count_delta"),
        },
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "requires_user_approval_for_writes": True,
        "note": "Scene evidence is read-only routing and risk context; it never grants edit permission.",
    }


def _workflow_scene_route_digest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("exists"):
        return {
            "exists": False,
            "network_path": "",
            "inferred_purpose": "unknown",
            "primary_risk_domain": "none",
            "primary_focus_path": "",
            "risk_domain_count": 0,
            "first_read_tools": [],
            "suggested_templates": [],
        }
    understanding = value.get("scene_understanding") if isinstance(value.get("scene_understanding"), dict) else {}
    risk_domains = value.get("risk_domains") if isinstance(value.get("risk_domains"), list) else []
    primary = understanding.get("primary_risk_domain") or "none"
    if primary == "none":
        for item in risk_domains:
            if isinstance(item, dict) and item.get("domain"):
                primary = str(item.get("domain"))
                break
    return {
        "exists": True,
        "network_path": str(value.get("network_path") or ""),
        "inferred_purpose": str(value.get("inferred_purpose") or "unknown"),
        "scene_understanding_state": str(understanding.get("state") or "unknown"),
        "primary_risk_domain": str(primary),
        "primary_focus_path": str(understanding.get("primary_focus_path") or ""),
        "risk_domain_count": int(value.get("risk_domain_count") or len(risk_domains)),
        "risk_domains": [_workflow_risk_domain_digest(item) for item in risk_domains[:6] if isinstance(item, dict)],
        "first_read_tools": [str(tool) for tool in (value.get("first_read_tools", []) if isinstance(value.get("first_read_tools"), list) else [])[:8] if isinstance(tool, str)],
        "suggested_templates": [str(template) for template in (value.get("suggested_templates", []) if isinstance(value.get("suggested_templates"), list) else [])[:8] if isinstance(template, str)],
        "may_execute": bool(understanding.get("may_execute")),
        "safe_to_run_direct_edits": bool(understanding.get("safe_to_run_direct_edits")),
    }


def _workflow_risk_domain_digest(value: dict[str, Any]) -> dict[str, Any]:
    paths = value.get("paths") if isinstance(value.get("paths"), list) else []
    return {
        "domain": str(value.get("domain") or ""),
        "priority": str(value.get("priority") or "unknown"),
        "path_count": int(value.get("path_count") or len(paths)),
        "paths": [str(path) for path in paths[:5] if isinstance(path, str)],
        "suggested_tools": [str(tool) for tool in (value.get("suggested_tools", []) if isinstance(value.get("suggested_tools"), list) else [])[:6] if isinstance(tool, str)],
        "workflow_templates": [str(template) for template in (value.get("workflow_templates", []) if isinstance(value.get("workflow_templates"), list) else [])[:6] if isinstance(template, str)],
    }


def _workflow_index_entry(path: Path) -> dict[str, Any]:
    name = path.name
    proof_report = _read_json_file(path / "proof_report.json")
    checklist = _read_json_file(path / "evidence_checklist.json")
    manifest = _read_json_file(path / "evidence_manifest.json")
    visual_evidence = _read_json_file(path / "visual_evidence.json")
    manifest_verification = _verify_manifest_artifacts(path, manifest)
    template_focus = manifest.get("template_verification_focus", {}) if isinstance(manifest, dict) else {}
    client_guidance = proof_report.get("client_guidance", {}) if isinstance(proof_report, dict) else {}
    visual = _workflow_visual_digest(proof_report, manifest, visual_evidence, path.name)
    scene_evidence = _workflow_scene_evidence_digest(manifest)
    direct_edit_readback = _workflow_direct_edit_readback_digest(proof_report, manifest)
    summary_path = path / "summary.md"
    existing_resources = []
    for kind in sorted(_workflow_resource_kinds()):
        filename, _mime_type = _workflow_resource_file(kind)
        if (path / filename).exists():
            existing_resources.append(kind)
    resources = {
        kind: _workflow_resource_uri(name, kind)
        for kind in existing_resources
    }
    proof = {
        "exists": bool(proof_report),
        "verdict": proof_report.get("verdict") if isinstance(proof_report, dict) else None,
        "proof_ready": bool(proof_report.get("proof_ready")) if isinstance(proof_report, dict) else False,
        "next_action": proof_report.get("next_action") if isinstance(proof_report, dict) else None,
        "rollback_recommended": bool(proof_report.get("rollback_recommended")) if isinstance(proof_report, dict) else False,
        "template_verification_focus": proof_report.get("template_verification_focus", {}) if isinstance(proof_report, dict) else {},
        "visual": visual,
        "scene_evidence": scene_evidence,
        "direct_edit_readback": direct_edit_readback,
        "rollback_guidance": _rollback_guidance_digest(client_guidance),
        "repair_guidance": _repair_guidance_digest(client_guidance),
    }
    evidence = {
        "checklist_exists": bool(checklist),
        "checklist_status": checklist.get("status") if isinstance(checklist, dict) else None,
        "required_passed": _nested_get(checklist, ["summary", "required_passed"]),
        "required_total": _nested_get(checklist, ["summary", "required_total"]),
        "warning_count": _nested_get(checklist, ["summary", "warning_count"]),
        "manifest_exists": bool(manifest),
        "summary_exists": summary_path.exists(),
        "artifact_integrity": manifest.get("artifact_integrity", {}) if isinstance(manifest, dict) else {},
        "manifest_verification": manifest_verification,
        "template_verification_focus": template_focus if isinstance(template_focus, dict) else {},
        "visual": visual,
        "scene_evidence": scene_evidence,
        "direct_edit_readback": direct_edit_readback,
    }
    next_client_step = _workflow_next_client_step(name, proof, client_guidance, resources)
    client_state = _workflow_client_state(proof, evidence, next_client_step)
    return {
        "name": name,
        "path": str(path),
        "resources": resources,
        "proof": proof,
        "evidence": evidence,
        "client_guidance": client_guidance,
        "next_client_step": next_client_step,
        "client_state": client_state,
        "success_gate": _workflow_success_gate(name, proof, evidence, next_client_step, resources, client_state),
    }


def _workflow_evidence_status(index: dict[str, Any]) -> dict[str, Any]:
    workflows = index.get("workflows", []) if isinstance(index, dict) else []
    if not isinstance(workflows, list):
        workflows = []
    verdict_counts = {"proven": 0, "failed": 0, "incomplete": 0, "missing": 0, "unknown": 0}
    client_state_counts = {
        "proven": 0,
        "rollback_recommended": 0,
        "failed": 0,
        "incomplete": 0,
        "missing_proof": 0,
        "unknown": 0,
    }
    proof_ready_count = 0
    proven = []
    success_gate_counts = {"can_report": 0, "blocked": 0, "unknown": 0}
    success_gate_blockers: dict[str, int] = {}
    direct_edit_readback = {
        "workflow_count": 0,
        "proof_ready_count": 0,
        "failed_count": 0,
        "inconclusive_count": 0,
        "total_checks": 0,
        "passed_checks": 0,
        "failed_checks": 0,
        "inconclusive_checks": 0,
        "commands": [],
        "failed_commands": [],
        "inconclusive_commands": [],
        "needs_attention": [],
    }
    rollback_recommended = []
    needs_attention = []
    for item in workflows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
        guidance = proof.get("rollback_guidance") if isinstance(proof.get("rollback_guidance"), dict) else {}
        next_step = item.get("next_client_step") if isinstance(item.get("next_client_step"), dict) else {}
        verdict = proof.get("verdict") or ("missing" if not proof.get("exists") else "unknown")
        if verdict not in verdict_counts:
            verdict = "unknown"
        verdict_counts[verdict] += 1
        client_state = item.get("client_state") if isinstance(item.get("client_state"), dict) else {}
        client_state_status = client_state.get("status") or "unknown"
        if client_state_status not in client_state_counts:
            client_state_status = "unknown"
        client_state_counts[client_state_status] += 1
        readback = proof.get("direct_edit_readback") if isinstance(proof.get("direct_edit_readback"), dict) else {}
        readback_blocks_success = bool(readback.get("exists")) and not bool(readback.get("proof_ready"))
        if proof.get("proof_ready"):
            proof_ready_count += 1
        if proof.get("proof_ready") and verdict == "proven" and not readback_blocks_success:
            proven.append(
                {
                    "name": name,
                    "resource": _workflow_resource_uri(name, "proof-report"),
                    "next_client_step": next_step,
                }
            )
        if readback.get("exists"):
            direct_edit_readback["workflow_count"] += 1
            direct_edit_readback["total_checks"] += _int_value(readback.get("total"))
            direct_edit_readback["passed_checks"] += _int_value(readback.get("passed"))
            direct_edit_readback["failed_checks"] += _int_value(readback.get("failed"))
            direct_edit_readback["inconclusive_checks"] += _int_value(readback.get("inconclusive"))
            direct_edit_readback["commands"].extend(_string_list(readback.get("commands")))
            direct_edit_readback["failed_commands"].extend(_string_list(readback.get("failed_commands")))
            direct_edit_readback["inconclusive_commands"].extend(_string_list(readback.get("inconclusive_commands")))
            if readback.get("proof_ready"):
                direct_edit_readback["proof_ready_count"] += 1
            else:
                direct_edit_readback["needs_attention"].append(
                    {
                        "name": name,
                        "resource": _workflow_resource_uri(name, "proof-report"),
                        "failed": _int_value(readback.get("failed")),
                        "inconclusive": _int_value(readback.get("inconclusive")),
                        "failed_commands": _string_list(readback.get("failed_commands")),
                        "inconclusive_commands": _string_list(readback.get("inconclusive_commands")),
                        "next_client_step": next_step,
                    }
                )
            if _int_value(readback.get("failed")) > 0:
                direct_edit_readback["failed_count"] += 1
            if _int_value(readback.get("inconclusive")) > 0:
                direct_edit_readback["inconclusive_count"] += 1
        success_gate = item.get("success_gate") if isinstance(item.get("success_gate"), dict) else {}
        if success_gate.get("can_report_success_now") is True:
            success_gate_counts["can_report"] += 1
        elif success_gate:
            success_gate_counts["blocked"] += 1
            blockers = success_gate.get("blocked_by") if isinstance(success_gate.get("blocked_by"), list) else []
            for blocker in blockers:
                if not isinstance(blocker, str) or not blocker:
                    continue
                success_gate_blockers[blocker] = success_gate_blockers.get(blocker, 0) + 1
        else:
            success_gate_counts["unknown"] += 1
        if proof.get("rollback_recommended") or guidance.get("recommended"):
            rollback_recommended.append(
                {
                    "name": name,
                    "resource": guidance.get("resource") or _workflow_resource_uri(name, "rollback-plan"),
                    "auto_execute": bool(guidance.get("auto_execute")),
                    "required_review_flow": guidance.get("required_review_flow", [])
                    if isinstance(guidance.get("required_review_flow"), list)
                    else [],
                    "next_client_step": next_step,
                }
            )
        if verdict in {"failed", "incomplete", "missing"} or readback_blocks_success:
            needs_attention.append(
                {
                    "name": name,
                    "verdict": "direct_edit_readback_failed" if readback_blocks_success else verdict,
                    "next_action": "inspect_direct_edit_readback" if readback_blocks_success else proof.get("next_action") or "",
                    "resource": _workflow_resource_uri(name, "proof-report"),
                    "next_client_step": next_step,
                }
            )
    direct_edit_readback["commands"] = _unique_ordered(direct_edit_readback["commands"])
    direct_edit_readback["failed_commands"] = _unique_ordered(direct_edit_readback["failed_commands"])
    direct_edit_readback["inconclusive_commands"] = _unique_ordered(direct_edit_readback["inconclusive_commands"])
    direct_edit_readback["needs_attention"] = direct_edit_readback["needs_attention"][:10]
    direct_edit_readback["proof_ready"] = (
        direct_edit_readback["workflow_count"] == 0
        or (
            direct_edit_readback["proof_ready_count"] == direct_edit_readback["workflow_count"]
            and direct_edit_readback["failed_checks"] == 0
            and direct_edit_readback["inconclusive_checks"] == 0
        )
    )
    return {
        "local_only": True,
        "resource": "houdini://workflow/index",
        "workflow_root": index.get("workflow_root", "") if isinstance(index, dict) else "",
        "workflow_count": len(workflows),
        "proof_ready_count": proof_ready_count,
        "proven": proven[:10],
        "success_gate_counts": success_gate_counts,
        "success_gate_blockers": success_gate_blockers,
        "direct_edit_readback": direct_edit_readback,
        "verdict_counts": verdict_counts,
        "client_state_counts": client_state_counts,
        "needs_attention_count": len(needs_attention),
        "needs_attention": needs_attention[:10],
        "rollback_recommended_count": len(rollback_recommended),
        "rollback_recommended": rollback_recommended[:10],
        "note": "This summary is derived from local workflow evidence artifacts and does not contact Houdini.",
    }


def _scene_routing_status(snapshot_response: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot_response is None:
        return {
            "queried": False,
            "available": False,
            "risk_domain_count": 0,
            "primary_risk_domain": "none",
            "risk_domains": [],
            "workflow_suggestions": [],
            "first_read_tools": ["houdini_scene_snapshot"],
            "next_actions": ["Use houdini_scene_snapshot to gather scene semantics after connecting."],
            "note": "Scene routing is populated from a read-only scene_snapshot only when the bridge is connected and healthy.",
        }
    if not isinstance(snapshot_response, dict) or not snapshot_response.get("ok"):
        error = snapshot_response.get("error") if isinstance(snapshot_response, dict) and isinstance(snapshot_response.get("error"), dict) else {}
        return {
            "queried": True,
            "available": False,
            "risk_domain_count": 0,
            "primary_risk_domain": "unknown",
            "risk_domains": [],
            "workflow_suggestions": [],
            "first_read_tools": ["houdini_scene_snapshot", "houdini_context", "houdini_rpc_log"],
            "next_actions": ["Run houdini_scene_snapshot directly, then fall back to focused read tools if it fails."],
            "error": {
                "code": error.get("code") or "scene_snapshot_failed",
                "message": error.get("message") or "Could not read scene_snapshot for routing.",
            },
            "note": "Scene routing failure does not grant or remove edit permission.",
        }

    result = snapshot_response.get("result") if isinstance(snapshot_response.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    semantics = result.get("semantics") if isinstance(result.get("semantics"), dict) else {}
    understanding = semantics.get("scene_understanding") if isinstance(semantics.get("scene_understanding"), dict) else {}
    risk_domains = semantics.get("risk_domains") if isinstance(semantics.get("risk_domains"), list) else []
    suggestions = semantics.get("workflow_suggestions") if isinstance(semantics.get("workflow_suggestions"), list) else []
    digested_domains = [_risk_domain_digest(item) for item in risk_domains if isinstance(item, dict)]
    digested_domains = [item for item in digested_domains if item.get("domain")]
    primary = digested_domains[0] if digested_domains else {}
    understanding_tools = understanding.get("first_read_tools") if isinstance(understanding.get("first_read_tools"), list) else []
    primary_tools = primary.get("suggested_tools") if isinstance(primary.get("suggested_tools"), list) else []
    first_read_tools = list(understanding_tools or primary_tools)
    first_read_tools.extend(["houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"])
    next_actions = list(understanding.get("next_actions") or [])
    if primary.get("domain"):
        next_actions.append(
            "Inspect scene risk domain `%s` before planning edits." % primary.get("domain")
        )
    if suggestions:
        next_actions.append("Use houdini_template_plan only as a local draft, then review/validate/run/verify.")
    if not next_actions:
        next_actions.append("Use houdini_scene_snapshot and focus_candidates as the first scene read.")
    return {
        "queried": True,
        "available": True,
        "network_path": summary.get("network_path") or result.get("path") or "",
        "inferred_purpose": summary.get("inferred_purpose") or semantics.get("inferred_purpose") or "unknown",
        "scene_understanding": _scene_understanding_digest(understanding),
        "risk_domain_count": len(digested_domains),
        "primary_risk_domain": understanding.get("primary_risk_domain") or primary.get("domain") or "none",
        "primary_priority": understanding.get("primary_risk_priority") or primary.get("priority") or "none",
        "risk_domains": digested_domains[:8],
        "workflow_suggestions": [_workflow_suggestion_digest(item) for item in suggestions[:6] if isinstance(item, dict)],
        "first_read_tools": _unique_ordered(first_read_tools),
        "next_actions": _unique_ordered(next_actions),
        "note": "Scene routing is read-only guidance derived from scene_snapshot semantics; all write gates still apply.",
    }


def _scene_understanding_digest(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict) or not item:
        return {"exists": False}
    first_read_tools = item.get("first_read_tools") if isinstance(item.get("first_read_tools"), list) else []
    read_targets = item.get("read_targets") if isinstance(item.get("read_targets"), list) else []
    suggested_templates = item.get("suggested_templates") if isinstance(item.get("suggested_templates"), list) else []
    return {
        "exists": True,
        "state": str(item.get("state") or "unknown"),
        "primary_risk_domain": str(item.get("primary_risk_domain") or "none"),
        "primary_focus_path": str(item.get("primary_focus_path") or ""),
        "first_read_tools": [str(tool) for tool in first_read_tools[:8] if isinstance(tool, str)],
        "read_targets": read_targets[:5],
        "suggested_templates": suggested_templates[:4],
        "required_write_flow": item.get("required_write_flow", []) if isinstance(item.get("required_write_flow"), list) else [],
        "may_execute": bool(item.get("may_execute")),
        "safe_to_run_direct_edits": bool(item.get("safe_to_run_direct_edits")),
    }


def _risk_domain_digest(item: dict[str, Any]) -> dict[str, Any]:
    paths = item.get("paths") if isinstance(item.get("paths"), list) else []
    tools = item.get("suggested_tools") if isinstance(item.get("suggested_tools"), list) else []
    templates = item.get("workflow_templates") if isinstance(item.get("workflow_templates"), list) else []
    reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
    return {
        "domain": str(item.get("domain") or ""),
        "priority": str(item.get("priority") or "unknown"),
        "path_count": int(item.get("path_count") or len(paths)),
        "paths": [str(path) for path in paths[:5] if isinstance(path, str) and path],
        "suggested_tools": [str(tool) for tool in tools[:6] if isinstance(tool, str) and tool],
        "workflow_templates": [str(template) for template in templates[:6] if isinstance(template, str) and template],
        "reasons": [str(reason) for reason in reasons[:2] if isinstance(reason, str) and reason],
    }


def _workflow_suggestion_digest(item: dict[str, Any]) -> dict[str, Any]:
    risk_domains = item.get("risk_domains") if isinstance(item.get("risk_domains"), list) else []
    next_tools = item.get("suggested_next_tools") if isinstance(item.get("suggested_next_tools"), list) else []
    return {
        "template": str(item.get("template") or ""),
        "category": str(item.get("category") or ""),
        "priority": str(item.get("priority") or ""),
        "input_path": str(item.get("input_path") or ""),
        "mcp_tool": str(item.get("mcp_tool") or "houdini_template_plan"),
        "risk_domains": [str(domain) for domain in risk_domains if isinstance(domain, str) and domain],
        "suggested_next_tools": [str(tool) for tool in next_tools if isinstance(tool, str) and tool],
        "local_generation_only": bool(item.get("local_generation_only")),
    }


def _adapter_readiness(
    session: dict[str, Any],
    bridge_health: dict[str, Any] | None,
    workflow_evidence: dict[str, Any],
    safety: dict[str, Any],
    scene_routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues = []
    next_actions = []
    status = "ready"
    if not session.get("connected"):
        status = "offline"
        issues.append("No active Blib Houdini Bridge session was found.")
        next_actions.append("Start Houdini and run the Blib_hou_bridge shelf tool.")
    elif bridge_health is not None and not bridge_health.get("ok"):
        status = "offline"
        error = bridge_health.get("error") if isinstance(bridge_health.get("error"), dict) else {}
        issues.append(error.get("message") or "Bridge health check failed.")
        next_actions.append("Restart or reload the Houdini bridge, then rerun MCP --status.")
    if (
        not safety.get("bridge_rpc_only")
        or safety.get("imports_hou")
        or safety.get("token_exposed")
        or not safety.get("plan_required_commands_not_direct_tools")
        or not safety.get("direct_edit_tools_match_policy")
        or not safety.get("tool_policy_contract_ok")
    ):
        status = "unsafe"
        issues.append("MCP adapter safety invariants are not satisfied.")
        next_actions.append("Do not connect AI clients until the MCP adapter safety policy is repaired.")
    attention_count = int(workflow_evidence.get("needs_attention_count") or 0) if isinstance(workflow_evidence, dict) else 0
    rollback_count = int(workflow_evidence.get("rollback_recommended_count") or 0) if isinstance(workflow_evidence, dict) else 0
    client_state_counts = (
        workflow_evidence.get("client_state_counts", {})
        if isinstance(workflow_evidence.get("client_state_counts"), dict)
        else {}
    ) if isinstance(workflow_evidence, dict) else {}
    priority_client_state = _priority_client_state(client_state_counts)
    if status == "ready" and attention_count:
        status = "degraded"
        issues.append("Some local workflow evidence needs attention.")
        next_actions.append("Read houdini://workflow/index and follow each workflow next_client_step.")
    if rollback_count:
        next_actions.append("Review rollback recommendations, but do not execute rollback without user approval.")
    scene_routing = scene_routing if isinstance(scene_routing, dict) else {}
    scene_risk_domain_count = int(scene_routing.get("risk_domain_count") or 0)
    primary_scene_risk_domain = str(scene_routing.get("primary_risk_domain") or "none")
    if scene_routing.get("queried") and not scene_routing.get("available"):
        issues.append("Scene routing snapshot is unavailable.")
        next_actions.extend(scene_routing.get("next_actions", []) if isinstance(scene_routing.get("next_actions"), list) else [])
    elif scene_risk_domain_count:
        next_actions.extend(scene_routing.get("next_actions", []) if isinstance(scene_routing.get("next_actions"), list) else [])
    if not next_actions:
        next_actions.append("Use houdini_context or houdini_scene_snapshot as the first read tool.")
    first_read_tools = ["houdini_context", "houdini_scene_snapshot", "houdini_edit_mode"]
    if isinstance(scene_routing.get("first_read_tools"), list):
        first_read_tools.extend(str(tool) for tool in scene_routing["first_read_tools"] if isinstance(tool, str))
    return {
        "status": status,
        "connected": bool(session.get("connected")),
        "bridge_ok": bool(bridge_health.get("ok")) if isinstance(bridge_health, dict) else None,
        "safe_to_connect_client": status in {"ready", "degraded"},
        "safe_to_run_direct_edits": False,
        "requires_user_approval_for_writes": True,
        "workflow_attention_count": attention_count,
        "rollback_recommended_count": rollback_count,
        "workflow_client_state_counts": client_state_counts,
        "workflow_priority_client_state": priority_client_state,
        "scene_risk_domain_count": scene_risk_domain_count,
        "primary_scene_risk_domain": primary_scene_risk_domain,
        "issues": issues,
        "next_actions": _unique_ordered(next_actions),
        "first_read_resources": ["houdini://adapter/status", "houdini://workflow/index"],
        "first_read_tools": _unique_ordered(first_read_tools),
        "note": "Readiness is a diagnostic summary only. It never grants edit permission; bridge edit mode and plan review gates still apply.",
    }


def _client_bootstrap(
    readiness: dict[str, Any],
    workflow_evidence: dict[str, Any],
    scene_routing: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    status = str(readiness.get("status") or "unknown") if isinstance(readiness, dict) else "unknown"
    base_resources = ["houdini://adapter/status"]
    if status == "unsafe" or not _safety_invariants_ok(safety):
        return {
            "action": "repair_adapter_safety",
            "state": "unsafe",
            "priority": "critical",
            "reason": "MCP adapter safety invariants are not satisfied.",
            "read_resources": _unique_ordered(base_resources + ["houdini://safety/policy", "houdini://manifest"]),
            "suggested_tools": [],
            "requires_user_approval": False,
            "may_execute": False,
            "safe_to_connect_client": False,
            "safe_to_run_direct_edits": False,
            "instruction": "Do not connect AI clients or run write tools until the adapter safety policy is repaired.",
        }

    workflow_evidence = workflow_evidence if isinstance(workflow_evidence, dict) else {}
    rollback_items = workflow_evidence.get("rollback_recommended", [])
    if isinstance(rollback_items, list) and rollback_items:
        first = rollback_items[0] if isinstance(rollback_items[0], dict) else {}
        next_step = first.get("next_client_step") if isinstance(first.get("next_client_step"), dict) else {}
        read_resources = next_step.get("read_resources") if isinstance(next_step.get("read_resources"), list) else []
        suggested_tools = next_step.get("suggested_tools") if isinstance(next_step.get("suggested_tools"), list) else []
        return {
            "action": "review_rollback_plan",
            "state": "rollback_recommended",
            "priority": "high",
            "reason": "Local workflow evidence recommends rollback review before new Houdini edits.",
            "workflow": first.get("name") or "",
            "read_resources": _unique_ordered(base_resources + ["houdini://workflow/index"] + read_resources + [first.get("resource") or ""]),
            "suggested_tools": _unique_ordered(suggested_tools or ["houdini_review_plan", "houdini_validate_plan"]),
            "required_review_flow": first.get("required_review_flow", []) if isinstance(first.get("required_review_flow"), list) else [],
            "requires_user_approval": True,
            "may_execute": False,
            "safe_to_connect_client": bool(readiness.get("safe_to_connect_client")) if isinstance(readiness, dict) else False,
            "safe_to_run_direct_edits": False,
            "instruction": "Read the rollback plan and proof report, review and validate only, then ask the user before any write.",
        }

    attention_items = workflow_evidence.get("needs_attention", [])
    if isinstance(attention_items, list) and attention_items:
        first = attention_items[0] if isinstance(attention_items[0], dict) else {}
        next_step = first.get("next_client_step") if isinstance(first.get("next_client_step"), dict) else {}
        read_resources = next_step.get("read_resources") if isinstance(next_step.get("read_resources"), list) else []
        suggested_tools = next_step.get("suggested_tools") if isinstance(next_step.get("suggested_tools"), list) else []
        return {
            "action": next_step.get("action") or "inspect_workflow_evidence",
            "state": first.get("verdict") or "needs_attention",
            "priority": "high",
            "reason": first.get("next_action") or next_step.get("reason") or "Local workflow evidence needs attention.",
            "workflow": first.get("name") or "",
            "read_resources": _unique_ordered(base_resources + ["houdini://workflow/index"] + read_resources + [first.get("resource") or ""]),
            "suggested_tools": _unique_ordered(suggested_tools),
            "requires_user_approval": bool(next_step.get("requires_user_approval")),
            "may_execute": False,
            "safe_to_connect_client": bool(readiness.get("safe_to_connect_client")) if isinstance(readiness, dict) else False,
            "safe_to_run_direct_edits": False,
            "instruction": next_step.get("instruction") or "Read workflow evidence before making a success claim or proposing edits.",
        }

    if status == "offline":
        return {
            "action": "start_bridge",
            "state": "offline",
            "priority": "high",
            "reason": "No active Blib Houdini Bridge session is connected.",
            "read_resources": _unique_ordered(base_resources + ["houdini://session/current", "houdini://workflow/index"]),
            "suggested_tools": [],
            "requires_user_approval": False,
            "may_execute": False,
            "safe_to_connect_client": False,
            "safe_to_run_direct_edits": False,
            "instruction": "Start Houdini and run the Blib_hou_bridge shelf tool, then read houdini://adapter/status again.",
        }

    scene_routing = scene_routing if isinstance(scene_routing, dict) else {}
    if scene_routing.get("queried") and not scene_routing.get("available"):
        return {
            "action": "recover_scene_routing",
            "state": "scene_routing_unavailable",
            "priority": "medium",
            "reason": "Bridge is connected, but the read-only scene routing snapshot failed.",
            "read_resources": _unique_ordered(base_resources + ["houdini://session/current", "houdini://rpc-log/recent"]),
            "suggested_tools": _unique_ordered(readiness.get("first_read_tools", []) if isinstance(readiness.get("first_read_tools"), list) else []),
            "requires_user_approval": False,
            "may_execute": False,
            "safe_to_connect_client": bool(readiness.get("safe_to_connect_client")) if isinstance(readiness, dict) else False,
            "safe_to_run_direct_edits": False,
            "instruction": "Run read-only scene tools and inspect the RPC log before planning edits.",
        }

    if scene_routing.get("available") and int(scene_routing.get("risk_domain_count") or 0) > 0:
        primary = str(scene_routing.get("primary_risk_domain") or "unknown")
        return {
            "action": "inspect_scene_risk_domain",
            "state": "scene_risk_detected",
            "priority": "medium",
            "reason": "Scene snapshot routing found `%s` as the primary risk domain." % primary,
            "primary_scene_risk_domain": primary,
            "read_resources": _unique_ordered(base_resources + ["houdini://scene/current", "houdini://workflow-templates/risk-domains"]),
            "suggested_tools": _unique_ordered(readiness.get("first_read_tools", []) if isinstance(readiness.get("first_read_tools"), list) else []),
            "requires_user_approval": False,
            "may_execute": False,
            "safe_to_connect_client": bool(readiness.get("safe_to_connect_client")) if isinstance(readiness, dict) else False,
            "safe_to_run_direct_edits": False,
            "instruction": "Use read-only focused tools for the primary risk domain before drafting any plan.",
        }

    return {
        "action": "read_scene_context",
        "state": status,
        "priority": "normal",
        "reason": "Adapter is connected enough for read-only scene inspection.",
        "read_resources": _unique_ordered(base_resources + ["houdini://scene/current", "houdini://selection/current"]),
        "suggested_tools": _unique_ordered(readiness.get("first_read_tools", []) if isinstance(readiness.get("first_read_tools"), list) else []),
        "requires_user_approval": False,
        "may_execute": False,
        "safe_to_connect_client": bool(readiness.get("safe_to_connect_client")) if isinstance(readiness, dict) else False,
        "safe_to_run_direct_edits": False,
        "instruction": "Start with read-only context and scene snapshot tools; use review/validate/run/verify for writes.",
    }


def _success_gate(
    readiness: dict[str, Any],
    workflow_evidence: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    workflow_evidence = workflow_evidence if isinstance(workflow_evidence, dict) else {}
    readiness = readiness if isinstance(readiness, dict) else {}
    blocked_by = []
    read_resources = ["houdini://adapter/status", "houdini://workflow/index"]
    if not _safety_invariants_ok(safety):
        blocked_by.append("adapter_safety")
        read_resources.extend(["houdini://safety/policy", "houdini://manifest"])

    rollback_count = int(workflow_evidence.get("rollback_recommended_count") or 0)
    attention_count = int(workflow_evidence.get("needs_attention_count") or 0)
    proof_ready_count = int(workflow_evidence.get("proof_ready_count") or 0)
    proven_items = workflow_evidence.get("proven", []) if isinstance(workflow_evidence.get("proven"), list) else []
    proven_workflow_count = len(proven_items)
    success_gate_counts = (
        workflow_evidence.get("success_gate_counts", {})
        if isinstance(workflow_evidence.get("success_gate_counts"), dict)
        else {}
    )
    reportable_workflow_count = int(success_gate_counts.get("can_report") or 0)
    blocked_workflow_gate_count = int(success_gate_counts.get("blocked") or 0)
    unknown_workflow_gate_count = int(success_gate_counts.get("unknown") or 0)
    success_gate_blockers = (
        workflow_evidence.get("success_gate_blockers", {})
        if isinstance(workflow_evidence.get("success_gate_blockers"), dict)
        else {}
    )
    if rollback_count:
        blocked_by.append("rollback_recommended")
        for item in workflow_evidence.get("rollback_recommended", []) if isinstance(workflow_evidence.get("rollback_recommended"), list) else []:
            if isinstance(item, dict):
                read_resources.append(str(item.get("resource") or ""))
                next_step = item.get("next_client_step") if isinstance(item.get("next_client_step"), dict) else {}
                resources = next_step.get("read_resources") if isinstance(next_step.get("read_resources"), list) else []
                read_resources.extend(resources)
    if attention_count:
        blocked_by.append("workflow_evidence_needs_attention")
        for item in workflow_evidence.get("needs_attention", []) if isinstance(workflow_evidence.get("needs_attention"), list) else []:
            if isinstance(item, dict):
                read_resources.append(str(item.get("resource") or ""))
                next_step = item.get("next_client_step") if isinstance(item.get("next_client_step"), dict) else {}
                resources = next_step.get("read_resources") if isinstance(next_step.get("read_resources"), list) else []
                read_resources.extend(resources)
    if not proven_workflow_count:
        blocked_by.append("no_proven_workflow")
    if not reportable_workflow_count:
        blocked_by.append("no_reportable_workflow")
    if blocked_workflow_gate_count:
        blocked_by.append("workflow_success_gate_blocked")
    if unknown_workflow_gate_count:
        blocked_by.append("workflow_success_gate_unknown")
    for item in proven_items:
        if not isinstance(item, dict):
            continue
        read_resources.append(str(item.get("resource") or ""))
        next_step = item.get("next_client_step") if isinstance(item.get("next_client_step"), dict) else {}
        resources = next_step.get("read_resources") if isinstance(next_step.get("read_resources"), list) else []
        read_resources.extend(resources)

    blocked_by = _unique_ordered(blocked_by)
    can_report = not blocked_by and reportable_workflow_count > 0 and proof_ready_count > 0
    proven_workflow = ""
    if proven_items and isinstance(proven_items[0], dict):
        proven_workflow = str(proven_items[0].get("name") or "")
    return {
        "can_report_success_now": can_report,
        "state": "proven" if can_report else "blocked",
        "proven_workflow": proven_workflow,
        "proven_workflow_count": proven_workflow_count,
        "reportable_workflow_count": reportable_workflow_count,
        "blocked_workflow_gate_count": blocked_workflow_gate_count,
        "unknown_workflow_gate_count": unknown_workflow_gate_count,
        "success_gate_blockers": success_gate_blockers,
        "proof_ready_count": proof_ready_count,
        "workflow_attention_count": attention_count,
        "rollback_recommended_count": rollback_count,
        "blocked_by": blocked_by,
        "read_resources": _unique_ordered(read_resources),
        "suggested_tools": ["houdini_scene_snapshot"] if can_report and readiness.get("connected") else [],
        "requires_proof_report": True,
        "requires_evidence_checklist": True,
        "requires_summary": True,
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "instruction": (
            "Read proof-report, evidence-checklist, and summary before reporting success."
            if can_report
            else "Do not report success until blockers are resolved and a workflow proof report is proven with proof_ready=true."
        ),
        "note": "This gate governs success claims only. It never grants edit permission.",
    }


def _safety_invariants_ok(safety: dict[str, Any]) -> bool:
    if not isinstance(safety, dict):
        return False
    return (
        bool(safety.get("bridge_rpc_only"))
        and not bool(safety.get("imports_hou"))
        and not bool(safety.get("token_exposed"))
        and bool(safety.get("plan_required_commands_not_direct_tools"))
        and bool(safety.get("direct_edit_tools_match_policy"))
        and bool(safety.get("tool_policy_contract_ok"))
    )


def _priority_client_state(client_state_counts: dict[str, Any]) -> str:
    if not isinstance(client_state_counts, dict):
        return "none"
    for state_name in ("rollback_recommended", "failed", "incomplete", "missing_proof", "unknown", "proven"):
        try:
            count = int(client_state_counts.get(state_name) or 0)
        except Exception:
            count = 0
        if count > 0:
            return state_name
    return "none"


def _workflow_next_client_step(
    name: str,
    proof: dict[str, Any],
    client_guidance: dict[str, Any],
    resources: dict[str, str],
) -> dict[str, Any]:
    verdict = proof.get("verdict") or ("missing" if not proof.get("exists") else "unknown")
    rollback = proof.get("rollback_guidance") if isinstance(proof.get("rollback_guidance"), dict) else {}
    suggested_tools = client_guidance.get("suggested_tools", []) if isinstance(client_guidance.get("suggested_tools"), list) else []
    mcp_resources = client_guidance.get("mcp_resources", []) if isinstance(client_guidance.get("mcp_resources"), list) else []
    if not mcp_resources:
        mcp_resources = _workflow_default_read_resources(resources)

    direct_edit_readback = proof.get("direct_edit_readback") if isinstance(proof.get("direct_edit_readback"), dict) else {}
    if direct_edit_readback.get("exists") and not direct_edit_readback.get("proof_ready"):
        return {
            "action": "inspect_failed_checks",
            "reason": "Direct edit readback is not proof-ready; inspect failed or inconclusive edit checks before reporting success.",
            "read_resources": _unique_ordered(mcp_resources or _workflow_default_read_resources(resources)),
            "suggested_tools": _unique_ordered(
                suggested_tools
                or ["houdini_verify_plan", "houdini_rpc_log", "houdini_node_info", "houdini_node_parms"]
            ),
            "direct_edit_readback": direct_edit_readback,
            "requires_user_approval": False,
            "may_execute": False,
            "instruction": "Read direct_edit_readback failed_commands/inconclusive_commands, then inspect current Houdini state with read-only tools before proposing any repair.",
        }

    if proof.get("proof_ready") and verdict == "proven":
        return {
            "action": "report_success",
            "reason": "Workflow proof report says the run is proven.",
            "read_resources": _unique_ordered(mcp_resources or _workflow_default_read_resources(resources)),
            "suggested_tools": suggested_tools or ["houdini_scene_snapshot"],
            "requires_user_approval": False,
            "may_execute": False,
            "instruction": "Read proof-report and summary before reporting success; optionally refresh scene_snapshot if Houdini is connected.",
        }
    if rollback.get("recommended"):
        required_flow = rollback.get("required_review_flow", [])
        if not isinstance(required_flow, list) or not required_flow:
            required_flow = ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"]
        suggested_first_tools = rollback.get("suggested_first_tools", [])
        if not isinstance(suggested_first_tools, list) or not suggested_first_tools:
            suggested_first_tools = ["houdini_review_plan", "houdini_validate_plan"]
        repair_guidance = _repair_guidance_digest(client_guidance)
        return {
            "action": "review_rollback_plan",
            "reason": "Workflow failed and rollback is recommended by the proof report.",
            "read_resources": _unique_ordered(
                [
                    resources.get("proof-report") or _workflow_resource_uri(name, "proof-report"),
                    rollback.get("resource") or resources.get("rollback-plan") or _workflow_resource_uri(name, "rollback-plan"),
                    resources.get("evidence-checklist") or _workflow_resource_uri(name, "evidence-checklist"),
                    resources.get("summary") or _workflow_resource_uri(name, "summary"),
                ]
            ),
            "suggested_tools": suggested_first_tools,
            "required_review_flow": required_flow,
            "repair_guidance": repair_guidance,
            "requires_user_approval": True,
            "may_execute": False,
            "instruction": "Read the rollback plan, review and validate it, then ask for user approval before any rollback execution.",
        }
    if verdict == "failed":
        repair_guidance = _repair_guidance_digest(client_guidance)
        return {
            "action": "inspect_failed_checks",
            "reason": "Workflow proof report is failed; inspect evidence before proposing repair or rollback.",
            "read_resources": _unique_ordered(mcp_resources or _workflow_default_read_resources(resources)),
            "suggested_tools": suggested_tools or ["houdini_rpc_log", "houdini_scene_snapshot", "houdini_node_info", "houdini_node_parms"],
            "repair_guidance": repair_guidance,
            "requires_user_approval": False,
            "may_execute": False,
            "instruction": "Read failed checks and current scene state before proposing any edit plan.",
        }
    if verdict == "incomplete":
        return {
            "action": "collect_missing_evidence",
            "reason": "Workflow proof is incomplete; required evidence is missing or inconclusive.",
            "read_resources": _unique_ordered(mcp_resources or _workflow_default_read_resources(resources)),
            "suggested_tools": suggested_tools or ["houdini_scene_snapshot", "houdini_rpc_log", "houdini_verify_plan"],
            "requires_user_approval": False,
            "may_execute": False,
            "instruction": "Collect missing required evidence before claiming success.",
        }
    return {
        "action": "read_workflow_evidence",
        "reason": "Workflow has no proof report yet or the proof verdict is unknown.",
        "read_resources": _workflow_default_read_resources(resources),
        "suggested_tools": [],
        "requires_user_approval": False,
        "may_execute": False,
        "instruction": "Read available workflow evidence and generate or refresh proof-report before making a success claim.",
    }


def _workflow_client_state(
    proof: dict[str, Any],
    evidence: dict[str, Any],
    next_client_step: dict[str, Any],
) -> dict[str, Any]:
    verdict = proof.get("verdict") or ("missing" if not proof.get("exists") else "unknown")
    rollback = proof.get("rollback_guidance") if isinstance(proof.get("rollback_guidance"), dict) else {}
    action = next_client_step.get("action") if isinstance(next_client_step, dict) else ""
    evidence_complete = _evidence_complete(evidence)
    direct_edit_readback = proof.get("direct_edit_readback") if isinstance(proof.get("direct_edit_readback"), dict) else {}
    if direct_edit_readback.get("exists") and not direct_edit_readback.get("proof_ready"):
        status = "failed"
        reason = "Direct edit readback is not proof-ready; inspect failed or inconclusive direct edit checks."
    elif proof.get("proof_ready") and verdict == "proven":
        status = "proven"
        reason = "Proof report is proven and proof_ready is true."
    elif proof.get("rollback_recommended") or rollback.get("recommended") or action == "review_rollback_plan":
        status = "rollback_recommended"
        reason = "Proof report recommends reviewing a rollback plan."
    elif verdict == "failed":
        status = "failed"
        reason = "Proof report failed; inspect failed checks before proposing repairs."
    elif verdict == "incomplete":
        status = "incomplete"
        reason = "Proof report is incomplete or required evidence is missing."
    elif not proof.get("exists") or verdict == "missing":
        status = "missing_proof"
        reason = "Workflow has no proof_report.json yet."
    else:
        status = "unknown"
        reason = "Workflow proof state is unknown; read available evidence first."
    return {
        "status": status,
        "verdict": verdict,
        "proof_ready": bool(proof.get("proof_ready")),
        "evidence_complete": evidence_complete,
        "next_action": action,
        "requires_user_approval": bool(next_client_step.get("requires_user_approval")) if isinstance(next_client_step, dict) else False,
        "may_execute": False,
        "reason": reason,
    }


def _workflow_success_gate(
    name: str,
    proof: dict[str, Any],
    evidence: dict[str, Any],
    next_client_step: dict[str, Any],
    resources: dict[str, str],
    client_state: dict[str, Any],
) -> dict[str, Any]:
    proof = proof if isinstance(proof, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    next_client_step = next_client_step if isinstance(next_client_step, dict) else {}
    resources = resources if isinstance(resources, dict) else {}
    client_state = client_state if isinstance(client_state, dict) else {}
    verdict = str(proof.get("verdict") or ("missing" if not proof.get("exists") else "unknown"))
    state = str(client_state.get("status") or "unknown")
    blocked_by = []
    if not proof.get("exists"):
        blocked_by.append("missing_proof_report")
    if verdict != "proven":
        blocked_by.append("verdict_%s" % verdict)
    if not proof.get("proof_ready"):
        blocked_by.append("proof_not_ready")
    if proof.get("rollback_recommended") or state == "rollback_recommended":
        blocked_by.append("rollback_recommended")
    if not evidence.get("checklist_exists"):
        blocked_by.append("missing_evidence_checklist")
    if not evidence.get("summary_exists"):
        blocked_by.append("missing_summary")
    if not evidence.get("manifest_exists"):
        blocked_by.append("missing_evidence_manifest")
    artifact_integrity = evidence.get("artifact_integrity") if isinstance(evidence.get("artifact_integrity"), dict) else {}
    if artifact_integrity and not artifact_integrity.get("all_existing_hashed"):
        blocked_by.append("artifact_integrity_unverified")
    manifest_verification = (
        evidence.get("manifest_verification")
        if isinstance(evidence.get("manifest_verification"), dict)
        else {}
    )
    if manifest_verification and manifest_verification.get("verification_required"):
        if manifest_verification.get("unsafe_paths"):
            blocked_by.append("manifest_artifact_unsafe_path")
        if manifest_verification.get("missing"):
            blocked_by.append("manifest_artifact_missing")
        if manifest_verification.get("mismatched"):
            blocked_by.append("manifest_artifact_hash_mismatch")
        if not manifest_verification.get("all_manifest_artifacts_verified"):
            blocked_by.append("manifest_artifact_verification_failed")
    if not _evidence_complete(evidence):
        blocked_by.append("evidence_incomplete")
    direct_edit_readback = (
        proof.get("direct_edit_readback")
        if isinstance(proof.get("direct_edit_readback"), dict)
        else evidence.get("direct_edit_readback") if isinstance(evidence.get("direct_edit_readback"), dict) else {}
    )
    if direct_edit_readback.get("exists") and not direct_edit_readback.get("proof_ready"):
        blocked_by.append("direct_edit_readback_not_ready")
    if _int_value(direct_edit_readback.get("failed")) > 0:
        blocked_by.append("direct_edit_readback_failed")
    if _int_value(direct_edit_readback.get("inconclusive")) > 0:
        blocked_by.append("direct_edit_readback_inconclusive")

    read_resources = _workflow_default_read_resources(resources)
    if next_client_step.get("read_resources") and isinstance(next_client_step.get("read_resources"), list):
        read_resources.extend(next_client_step.get("read_resources", []))
    if proof.get("rollback_recommended"):
        read_resources.append(resources.get("rollback-plan") or _workflow_resource_uri(name, "rollback-plan"))

    blocked_by = _unique_ordered(blocked_by)
    can_report = not blocked_by and verdict == "proven" and bool(proof.get("proof_ready"))
    return {
        "can_report_success_now": can_report,
        "state": "proven" if can_report else "blocked",
        "workflow": name,
        "verdict": verdict,
        "proof_ready": bool(proof.get("proof_ready")),
        "evidence_complete": _evidence_complete(evidence),
        "direct_edit_readback": direct_edit_readback,
        "blocked_by": blocked_by,
        "read_resources": _unique_ordered(read_resources),
        "suggested_tools": next_client_step.get("suggested_tools", [])
        if isinstance(next_client_step.get("suggested_tools"), list)
        else [],
        "requires_proof_report": True,
        "requires_evidence_checklist": True,
        "requires_summary": True,
        "requires_evidence_manifest": True,
        "requires_artifact_integrity": True,
        "requires_manifest_artifact_verification": True,
        "requires_direct_edit_readback_when_present": True,
        "requires_user_approval": bool(next_client_step.get("requires_user_approval")),
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "instruction": (
            "Read this workflow's proof-report, evidence-checklist, and summary before reporting success."
            if can_report
            else "Do not report this workflow as successful until its proof report is proven with proof_ready=true and blockers are resolved."
        ),
        "note": "This gate applies to this workflow entry only and never grants edit permission.",
    }


def _evidence_complete(evidence: dict[str, Any]) -> bool:
    if not isinstance(evidence, dict):
        return False
    required_passed = evidence.get("required_passed")
    required_total = evidence.get("required_total")
    if isinstance(required_passed, int) and isinstance(required_total, int) and required_total > 0:
        return required_passed >= required_total
    checklist_status = evidence.get("checklist_status")
    if checklist_status in {"pass", "passed"}:
        return True
    return False


def _workflow_default_read_resources(resources: dict[str, str]) -> list[str]:
    preferred = ["proof-report", "evidence-checklist", "summary", "evidence-manifest"]
    return _unique_ordered([resources.get(kind, "") for kind in preferred if resources.get(kind)])


def _unique_ordered(values: list[Any]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _rollback_guidance_digest(client_guidance: dict[str, Any]) -> dict[str, Any]:
    guidance = client_guidance.get("rollback_guidance") if isinstance(client_guidance, dict) else {}
    if not isinstance(guidance, dict):
        guidance = {}
    direct_edit_readback = guidance.get("direct_edit_readback") if isinstance(guidance.get("direct_edit_readback"), dict) else {}
    return {
        "recommended": bool(guidance.get("recommended")),
        "resource": guidance.get("resource") or "",
        "auto_execute": bool(guidance.get("auto_execute")),
        "direct_edit_readback": {
            "exists": bool(direct_edit_readback.get("exists")),
            "proof_ready": bool(direct_edit_readback.get("proof_ready")),
            "failed_commands": _unique_ordered(_string_list(direct_edit_readback.get("failed_commands"))),
            "inconclusive_commands": _unique_ordered(_string_list(direct_edit_readback.get("inconclusive_commands"))),
        },
        "required_review_flow": guidance.get("required_review_flow", [])
        if isinstance(guidance.get("required_review_flow"), list)
        else [],
    }


def _repair_guidance_digest(client_guidance: dict[str, Any]) -> dict[str, Any]:
    guidance = client_guidance.get("repair_guidance") if isinstance(client_guidance, dict) else {}
    if not isinstance(guidance, dict):
        guidance = {}
    direct_edit_readback = guidance.get("direct_edit_readback") if isinstance(guidance.get("direct_edit_readback"), dict) else {}
    return {
        "recommended": bool(guidance.get("recommended")),
        "action": guidance.get("action") or "",
        "auto_execute": bool(guidance.get("auto_execute")),
        "may_execute": bool(guidance.get("may_execute")),
        "requires_user_approval": bool(guidance.get("requires_user_approval")),
        "read_resources": guidance.get("read_resources", [])
        if isinstance(guidance.get("read_resources"), list)
        else [],
        "diagnostic_read_tools": guidance.get("diagnostic_read_tools", [])
        if isinstance(guidance.get("diagnostic_read_tools"), list)
        else [],
        "required_review_flow": guidance.get("required_review_flow", [])
        if isinstance(guidance.get("required_review_flow"), list)
        else [],
        "failed_check_kinds": guidance.get("failed_check_kinds", [])
        if isinstance(guidance.get("failed_check_kinds"), list)
        else [],
        "inconclusive_check_kinds": guidance.get("inconclusive_check_kinds", [])
        if isinstance(guidance.get("inconclusive_check_kinds"), list)
        else [],
        "direct_edit_readback": {
            "exists": bool(direct_edit_readback.get("exists")),
            "proof_ready": bool(direct_edit_readback.get("proof_ready")),
            "commands": _unique_ordered(_string_list(direct_edit_readback.get("commands"))),
            "failed_commands": _unique_ordered(_string_list(direct_edit_readback.get("failed_commands"))),
            "inconclusive_commands": _unique_ordered(_string_list(direct_edit_readback.get("inconclusive_commands"))),
        },
        "direct_edit_failed_commands": _unique_ordered(_string_list(guidance.get("direct_edit_failed_commands"))),
        "direct_edit_inconclusive_commands": _unique_ordered(_string_list(guidance.get("direct_edit_inconclusive_commands"))),
        "missing_artifacts": guidance.get("missing_artifacts", [])
        if isinstance(guidance.get("missing_artifacts"), list)
        else [],
        "instruction": guidance.get("instruction") or "",
    }


def _workflow_resource_uri(name: str, kind: str) -> str:
    if not name:
        return ""
    return "houdini://workflow/%s/%s" % (name, kind)


def _verify_manifest_artifacts(workflow_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "missing_count": 0,
        "mismatched": [],
        "missing": [],
        "unsafe_paths": [],
        "all_manifest_artifacts_verified": True,
        "verification_required": False,
    }
    if not isinstance(manifest, dict):
        return result
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return result

    result["verification_required"] = True
    workflow_root = workflow_dir.resolve()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not artifact.get("exists"):
            continue
        key = str(artifact.get("key") or "")
        expected_path = str(artifact.get("path") or "")
        expected_bytes = artifact.get("bytes")
        expected_sha = str(artifact.get("sha256") or "")
        result["checked_count"] += 1

        resolved = _resolve_manifest_artifact_path(workflow_dir, expected_path)
        if not _path_is_within(resolved, workflow_root):
            result["unsafe_paths"].append({"key": key, "path": expected_path})
            result["failed_count"] += 1
            continue
        if not resolved.exists() or not resolved.is_file():
            result["missing"].append({"key": key, "path": expected_path})
            result["missing_count"] += 1
            result["failed_count"] += 1
            continue

        actual_bytes = resolved.stat().st_size
        actual_sha = _file_sha256(resolved)
        reasons = []
        if isinstance(expected_bytes, int) and actual_bytes != expected_bytes:
            reasons.append("bytes")
        if len(expected_sha) == 64 and actual_sha != expected_sha:
            reasons.append("sha256")
        elif len(expected_sha) != 64:
            reasons.append("sha256_missing")
        if reasons:
            result["mismatched"].append(
                {
                    "key": key,
                    "path": expected_path,
                    "reasons": reasons,
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                }
            )
            result["failed_count"] += 1
        else:
            result["passed_count"] += 1

    result["all_manifest_artifacts_verified"] = (
        result["checked_count"] > 0
        and result["failed_count"] == 0
        and result["missing_count"] == 0
        and not result["mismatched"]
        and not result["unsafe_paths"]
    )
    return result


def _resolve_manifest_artifact_path(workflow_dir: Path, value: str) -> Path:
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [workflow_dir / raw, Path.cwd() / raw]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve(strict=False)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _nested_get(value: Any, keys: list[str]) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
