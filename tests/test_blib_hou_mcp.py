import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(__file__))
PYTHON_DIR = os.path.join(ROOT, "scripts", "python")
CLI_PATH = os.path.join(ROOT, "scripts", "cli")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from blib_hou_bridge import commands, protocol  # noqa: E402
from blib_hou_mcp.server import BridgeMCPAdapter, main  # noqa: E402
from tests.test_blib_hou_bridge import FakeHou  # noqa: E402


SESSION = {"host": "127.0.0.1", "port": 12345, "token": "token", "pid": 999, "started_at": 1.5}


class BridgeMCPAdapterTests(unittest.TestCase):
    def test_mcp_package_import_survives_cli_path_first(self):
        code = (
            "import sys; "
            "sys.path.insert(0, %r); "
            "sys.path.insert(0, %r); "
            "from blib_hou_mcp.server import BridgeMCPAdapter; "
            "print(BridgeMCPAdapter.__name__)"
        ) % (PYTHON_DIR, CLI_PATH)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "BridgeMCPAdapter")

    def test_initialize_lists_tools_and_resources(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)

        init = adapter.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "blib-houdini-bridge")

        tools = adapter.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})["result"]["tools"]
        tool_names = {tool["name"] for tool in tools}
        self.assertIn("houdini_context", tool_names)
        self.assertIn("houdini_find_nodes", tool_names)
        self.assertIn("houdini_scene_snapshot", tool_names)
        self.assertIn("houdini_set_comment", tool_names)
        self.assertIn("houdini_run_plan", tool_names)
        self.assertIn("houdini_template_plan", tool_names)
        for command in protocol.PLAN_REQUIRED_EDIT_COMMANDS:
            self.assertNotIn("houdini_%s" % command, tool_names)
        self.assertEqual(
            next(tool for tool in tools if tool["name"] == "houdini_set_comment")["_meta"]["exposure"],
            "direct_edit",
        )
        set_comment_safety = next(tool for tool in tools if tool["name"] == "houdini_set_comment")["_meta"]["safety"]
        self.assertTrue(set_comment_safety["direct_call_allowed"])
        self.assertTrue(set_comment_safety["requires_bridge_edit_mode"])
        self.assertTrue(set_comment_safety["requires_user_approval_for_writes"])
        self.assertFalse(set_comment_safety["requires_review_flow"])
        self.assertFalse(set_comment_safety["may_report_success"])
        self.assertEqual(set_comment_safety["verification"]["read_tools"], ["node_info"])
        self.assertEqual(set_comment_safety["verification"]["mcp_read_tools"], ["houdini_node_info"])
        self.assertFalse(set_comment_safety["verification"]["may_report_success_from_rpc_ok"])
        set_comment_schema = next(tool for tool in tools if tool["name"] == "houdini_set_comment")["outputSchema"]
        self.assertIn("mcp_postflight", set_comment_schema["properties"])
        self.assertIn("next_required_tools", set_comment_schema["properties"]["mcp_postflight"]["properties"])
        self.assertIn("suggested_read_payloads", set_comment_schema["properties"]["mcp_postflight"]["properties"])
        self.assertIn("verification", set_comment_schema["properties"]["mcp_postflight"]["properties"])
        self.assertEqual(
            next(tool for tool in tools if tool["name"] == "houdini_run_plan")["_meta"]["exposure"],
            "plan_required",
        )
        run_plan_safety = next(tool for tool in tools if tool["name"] == "houdini_run_plan")["_meta"]["safety"]
        self.assertTrue(run_plan_safety["direct_call_allowed"])
        self.assertTrue(run_plan_safety["requires_bridge_edit_mode"])
        self.assertTrue(run_plan_safety["requires_review_flow"])
        self.assertEqual(
            run_plan_safety["required_review_flow"],
            ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        )
        self.assertFalse(run_plan_safety["may_report_success"])
        run_plan_schema = next(tool for tool in tools if tool["name"] == "houdini_run_plan")["inputSchema"]
        self.assertIn("validation", run_plan_schema["required"])
        self.assertIn("review", run_plan_schema["required"])
        self.assertIn("confirmed_required_confirmations", run_plan_schema["properties"])
        run_plan_output_schema = next(tool for tool in tools if tool["name"] == "houdini_run_plan")["outputSchema"]
        self.assertIn("mcp_preflight", run_plan_output_schema["properties"])
        self.assertIn("next_required_tool", run_plan_output_schema["properties"])
        template_tool = next(tool for tool in tools if tool["name"] == "houdini_template_plan")
        self.assertTrue(template_tool["_meta"]["local"])
        self.assertTrue(template_tool["_meta"]["safety"]["local_generation_only"])
        self.assertTrue(template_tool["_meta"]["safety"]["does_not_execute"])
        self.assertFalse(template_tool["_meta"]["safety"]["may_report_success"])
        self.assertIn("template", template_tool["inputSchema"]["required"])
        self.assertIn("input", template_tool["inputSchema"]["required"])
        self.assertIn("outputSchema", template_tool)
        self.assertIn("workflow_policy", template_tool["outputSchema"]["properties"])
        self.assertIn("plan", template_tool["outputSchema"]["properties"])
        self.assertIn("workflow_contract", template_tool["outputSchema"]["properties"])
        self.assertIn("client_guidance", template_tool["outputSchema"]["properties"])
        self.assertIn("evidence_expectations", template_tool["outputSchema"]["properties"])
        self.assertIn("next_required_tools", template_tool["outputSchema"]["properties"])
        self.assertIn("workflow_policy", template_tool["_meta"]["resultSchema"]["properties"])
        self.assertEqual(
            next(tool for tool in tools if tool["name"] == "houdini_context")["inputSchema"],
            protocol.command_manifest()["commands"]["context"]["payload_schema"],
        )
        self.assertEqual(
            next(tool for tool in tools if tool["name"] == "houdini_find_nodes")["inputSchema"],
            protocol.command_manifest()["commands"]["find_nodes"]["payload_schema"],
        )
        scene_tool = next(tool for tool in tools if tool["name"] == "houdini_scene_snapshot")
        self.assertIn("outputSchema", scene_tool)
        self.assertIn("focus_candidates", scene_tool["outputSchema"]["properties"]["semantics"]["properties"])
        self.assertIn("scene_understanding", scene_tool["outputSchema"]["properties"]["semantics"]["properties"])
        self.assertIn("volume_nodes", scene_tool["outputSchema"]["properties"]["semantics"]["properties"])
        self.assertIn("risk_domains", scene_tool["outputSchema"]["properties"]["semantics"]["properties"])
        self.assertIn("workflow_suggestions", scene_tool["outputSchema"]["properties"]["semantics"]["properties"])
        for key in ("cache_node_count", "simulation_node_count", "volume_node_count", "render_node_count"):
            self.assertIn(key, scene_tool["outputSchema"]["properties"]["summary"]["properties"])
        self.assertIn("risk_domain_count", scene_tool["outputSchema"]["properties"]["summary"]["properties"])
        self.assertIn("workflow_suggestion_count", scene_tool["outputSchema"]["properties"]["summary"]["properties"])
        self.assertIn("focus_candidates", scene_tool["_meta"]["resultSchema"]["properties"]["semantics"]["properties"])
        self.assertIn("scene_understanding", scene_tool["_meta"]["resultSchema"]["properties"]["semantics"]["properties"])
        validate_tool = next(tool for tool in tools if tool["name"] == "houdini_validate_plan")
        self.assertIn("ready_to_run", validate_tool["outputSchema"]["properties"])
        self.assertIn("blocked_by_edit_mode", validate_tool["outputSchema"]["properties"])
        review_tool = next(tool for tool in tools if tool["name"] == "houdini_review_plan")
        self.assertIn("required_confirmations", review_tool["outputSchema"]["properties"])
        self.assertIn("risk_notes", review_tool["_meta"]["resultSchema"]["properties"])
        run_tool = next(tool for tool in tools if tool["name"] == "houdini_run_plan")
        self.assertIn("results", run_tool["outputSchema"]["properties"])
        verify_tool = next(tool for tool in tools if tool["name"] == "houdini_verify_plan")
        self.assertIn("verified", verify_tool["outputSchema"]["properties"])
        self.assertIn("status", verify_tool["outputSchema"]["properties"])
        self.assertIn("checks", verify_tool["_meta"]["resultSchema"]["properties"])

        resources = adapter.handle_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})["result"]["resources"]
        resource_uris = {resource["uri"] for resource in resources}
        self.assertIn("houdini://manifest", resource_uris)
        self.assertIn("houdini://safety/policy", resource_uris)
        self.assertIn("houdini://workflow-templates/catalog", resource_uris)
        self.assertIn("houdini://workflow-templates/risk-domains", resource_uris)
        self.assertIn("houdini://workflow/index", resource_uris)

    def test_offline_session_returns_tool_error(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=_ok_poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "houdini_context", "arguments": {}},
            }
        )

        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "offline")

    def test_tool_call_posts_bridge_rpc(self):
        calls = []

        def poster(session, request):
            calls.append((session, request))
            return {"ok": True, "command": request["command"], "result": {"current_network": "/obj"}}

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "houdini_context", "arguments": {}},
            }
        )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(calls[0][0], SESSION)
        self.assertEqual(calls[0][1]["command"], "context")
        self.assertEqual(calls[0][1]["token"], "token")

    def test_direct_edit_still_reports_edit_mode_gate_failure(self):
        def poster(session, request):
            return {
                "ok": False,
                "command": request["command"],
                "result": {},
                "error": {"code": "command_failed", "message": "set_comment requires bridge edit mode."},
            }

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_set_comment",
                    "arguments": {"node": "/obj/geo1/OUT", "comment": "preview"},
                },
            }
        )

        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["command"], "set_comment")

    def test_direct_edit_success_includes_postflight_evidence_guidance(self):
        def poster(session, request):
            return {
                "ok": True,
                "command": request["command"],
                "result": {"touched": ["/obj/geo1/OUT"], "comment": "preview"},
            }

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_set_comment",
                    "arguments": {"node": "/obj/geo1/OUT", "comment": "preview"},
                },
            }
        )

        self.assertFalse(response["result"]["isError"])
        result = response["result"]["structuredContent"]["result"]
        postflight = result["mcp_postflight"]
        self.assertFalse(postflight["may_report_success"])
        self.assertTrue(postflight["success_requires_evidence"])
        self.assertEqual(postflight["next_required_step"], "read_back_changed_state")
        self.assertEqual(postflight["changed_paths"], ["/obj/geo1/OUT"])
        self.assertIn("houdini_node_info", postflight["next_required_tools"])
        self.assertIn("houdini_rpc_log", postflight["next_required_tools"])
        self.assertEqual(postflight["verification"]["read_tools"], ["node_info"])
        self.assertEqual(postflight["verification"]["mcp_read_tools"], ["houdini_node_info"])
        self.assertFalse(postflight["verification"]["may_report_success_from_rpc_ok"])
        self.assertIn({"tool": "houdini_node_info", "arguments": {"path": "/obj/geo1/OUT"}}, postflight["suggested_read_payloads"])
        self.assertIn({"tool": "houdini_rpc_log", "arguments": {"limit": 20}}, postflight["suggested_read_payloads"])

    def test_plan_required_commands_are_not_direct_mcp_tools(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_delete_node",
                    "arguments": {"node": "/obj/geo1/BAD", "confirm": True},
                },
            }
        )

        self.assertEqual(response["error"]["code"], -32602)

    def test_resources_read_manifest_and_sanitized_session(self):
        def poster(session, request):
            if request["command"] == "manifest":
                return {"ok": True, "command": "manifest", "result": protocol.command_manifest()}
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)

        session_response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://session/current"},
            }
        )
        session_payload = json.loads(session_response["result"]["contents"][0]["text"])["result"]
        self.assertTrue(session_payload["has_token"])
        self.assertNotIn("token", session_payload)

        manifest_response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": "houdini://manifest"},
            }
        )
        manifest_payload = json.loads(manifest_response["result"]["contents"][0]["text"])
        self.assertEqual(manifest_payload["command"], "manifest")
        scene_result_schema = manifest_payload["result"]["commands"]["scene_snapshot"]["result_schema"]
        self.assertIn("focus_candidates", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("scene_understanding", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("volume_nodes", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("risk_domains", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("workflow_suggestions", scene_result_schema["properties"]["semantics"]["properties"])
        self.assertIn("volume_node_count", scene_result_schema["properties"]["summary"]["properties"])
        self.assertIn("risk_domain_count", scene_result_schema["properties"]["summary"]["properties"])
        self.assertIn("focus_candidate_count", scene_result_schema["properties"]["summary"]["properties"])

    def test_template_catalog_resource_is_local_and_readonly(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://workflow-templates/catalog"},
            }
        )

        payload = json.loads(response["result"]["contents"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertIn("sop-cleanup", payload["result"]["template_names"])
        self.assertEqual(payload["result"]["templates"]["sop-cleanup"]["category"], "cleanup")
        self.assertIn("karma-solaris-preview", payload["result"]["template_names"])
        self.assertEqual(payload["result"]["templates"]["karma-solaris-preview"]["category"], "render")
        self.assertTrue(payload["result"]["workflow_policy"]["local_generation_only"])
        self.assertTrue(payload["result"]["workflow_policy"]["does_not_execute"])
        self.assertEqual(
            payload["result"]["workflow_policy"]["required_flow"],
            ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        )
        self.assertIn("render_settings", payload["result"]["templates"]["karma-solaris-preview"]["risk_domains"])
        self.assertTrue(payload["result"]["workflow_policy"]["verification_focus_required"])
        self.assertIn("verification_focus", payload["result"]["templates"]["karma-solaris-preview"])
        self.assertIn("render_rop_prepared", payload["result"]["templates"]["karma-solaris-preview"]["verification_focus"]["success_criteria"])
        self.assertIn("houdini_node_parms", payload["result"]["templates"]["rbd-preview"]["verification_focus"]["read_tools"])
        self.assertIn("default_karma_render_path", payload["result"])
        self.assertEqual(calls, [])

    def test_template_risk_domain_resource_is_local_and_readonly(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://workflow-templates/risk-domains"},
            }
        )

        payload = json.loads(response["result"]["contents"][0]["text"])["result"]
        self.assertTrue(payload["local_only"])
        self.assertEqual(payload["resource"], "houdini://workflow-templates/risk-domains")
        self.assertIn("simulation_settings", payload["domain_names"])
        self.assertIn("volume_resolution", payload["domain_names"])
        self.assertIn("cache_output", payload["domain_names"])
        simulation = payload["domains"]["simulation_settings"]
        self.assertTrue(simulation["requires_plan_review"])
        self.assertTrue(simulation["local_generation_only"])
        self.assertTrue(simulation["does_not_contact_houdini"])
        self.assertTrue(simulation["does_not_execute"])
        self.assertIn("houdini_node_parms", simulation["safe_read_tools"])
        self.assertEqual(
            simulation["required_flow"],
            ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        )
        self.assertIn("verification", simulation["evidence_expectations"])
        self.assertIn("rbd-preview", [item["template"] for item in simulation["templates"]])
        self.assertIn("vellum-cloth-preview", [item["template"] for item in simulation["templates"]])
        volume = payload["domains"]["volume_resolution"]
        self.assertIn("vdb-sdf-preview", [item["template"] for item in volume["templates"]])
        cache = payload["domains"]["cache_output"]
        self.assertIn("cache-output", [item["template"] for item in cache["templates"]])
        self.assertEqual(payload["template_catalog_resource"], "houdini://workflow-templates/catalog")
        self.assertEqual(payload["scene_routing_source"], "houdini://adapter/status")
        self.assertEqual(calls, [])

    def test_safety_policy_resource_is_local_and_readonly(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://safety/policy"},
            }
        )

        payload = json.loads(response["result"]["contents"][0]["text"])["result"]
        self.assertEqual(payload["transport"]["host"], "127.0.0.1")
        self.assertTrue(payload["transport"]["token_required"])
        self.assertFalse(payload["transport"]["mcp_imports_hou"])
        self.assertIn("create_node", payload["edit_gate"]["direct_edit_commands"])
        self.assertIn("delete_node", payload["edit_gate"]["plan_required_edit_commands"])
        self.assertEqual(payload["edit_gate"]["required_plan_flow"], ["review_plan", "validate_plan", "run_plan", "verify_plan"])
        self.assertIn("run_python", payload["blocked"]["danger_commands"])
        self.assertEqual(calls, [])

    def test_scene_snapshot_inspection_hints_reference_exposed_mcp_tools(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)
        tool_names = {
            tool["name"]
            for tool in adapter.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]
        }
        snapshot = commands.execute("scene-snapshot", {"path": "/obj/geo1", "trace_depth": 1}, hou_module=FakeHou())
        hinted_tools = {item["mcp_tool"] for item in snapshot["semantics"]["inspection_hints"]}

        self.assertTrue(hinted_tools)
        self.assertLessEqual(hinted_tools, tool_names)

    def test_template_plan_tool_works_offline_and_does_not_post(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_template_plan",
                    "arguments": {
                        "template": "karma-solaris-preview",
                        "input": "/obj/geo1/OUT",
                        "options": {
                            "name": "shot",
                            "render_path": "$HIP/render/shot/beauty.$F4.exr",
                            "resolution": "2048x1152",
                        },
                    },
                },
            }
        )

        result = response["result"]
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]["result"]
        self.assertEqual(payload["template"], "karma-solaris-preview")
        self.assertEqual(payload["input"], "/obj/geo1/OUT")
        self.assertEqual(payload["catalog"]["category"], "render")
        self.assertTrue(payload["workflow_policy"]["local_generation_only"])
        self.assertTrue(payload["workflow_policy"]["does_not_execute"])
        self.assertEqual(payload["workflow_policy"]["required_flow"], payload["next_required_tools"])
        self.assertEqual(payload["workflow_contract"]["state"], "draft_unreviewed")
        self.assertTrue(payload["workflow_contract"]["local_generation_only"])
        self.assertTrue(payload["workflow_contract"]["does_not_contact_houdini"])
        self.assertTrue(payload["workflow_contract"]["does_not_execute"])
        self.assertTrue(payload["workflow_contract"]["requires_review"])
        self.assertTrue(payload["workflow_contract"]["requires_validation"])
        self.assertTrue(payload["workflow_contract"]["requires_bridge_edit_mode_to_run"])
        self.assertEqual(payload["workflow_contract"]["required_flow"], payload["next_required_tools"])
        self.assertIn("verification", payload["workflow_contract"]["evidence_expectations"])
        self.assertIn("houdini_verify_plan", payload["workflow_contract"]["cannot_report_success_before"])
        self.assertEqual(payload["evidence_expectations"], payload["workflow_contract"]["evidence_expectations"])
        self.assertEqual(payload["verification_focus"], payload["workflow_contract"]["verification_focus"])
        self.assertEqual(payload["verification_focus"], payload["client_guidance"]["verification_focus"])
        self.assertIn("render_settings_match_options", payload["verification_focus"]["success_criteria"])
        self.assertIn("no_render_execution_claimed", payload["verification_focus"]["success_criteria"])
        self.assertIn("houdini_node_parms", payload["verification_focus"]["read_tools"])
        self.assertEqual(payload["client_guidance"]["next_action"], "review_template_plan")
        self.assertFalse(payload["client_guidance"]["may_execute"])
        self.assertTrue(payload["client_guidance"]["requires_user_approval_for_writes"])
        self.assertIn("houdini_review_plan", payload["client_guidance"]["suggested_tools"])
        self.assertIn("render_settings", payload["client_guidance"]["risk_domains"])
        self.assertIn("houdini://workflow-templates/risk-domains", payload["client_guidance"]["read_resources"])
        self.assertIn("draft", payload["note"])
        self.assertIn("houdini_review_plan", payload["next_required_tools"])
        self.assertIn("houdini_validate_plan", payload["next_required_tools"])
        self.assertIn("houdini_run_plan", payload["next_required_tools"])
        self.assertIn("houdini_verify_plan", payload["next_required_tools"])
        self.assertTrue(any(step.get("payload", {}).get("node") == "/obj/SHOT_LOPNET/SHOT_USD_RENDER_ROP" for step in payload["plan"]))
        self.assertEqual(payload["step_count"], len(payload["plan"]))
        self.assertEqual(calls, [])

    def test_template_plan_tool_rejects_invalid_arguments_without_posting(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_template_plan",
                    "arguments": {
                        "template": "bad-template",
                        "input": "obj/geo1/OUT",
                        "options": [],
                    },
                },
            }
        )

        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "bad_template_plan")
        self.assertEqual(calls, [])

    def test_adapter_status_resource_reports_policy_and_hides_token(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            if request["command"] == "scene_snapshot":
                return {
                    "ok": True,
                    "command": "scene_snapshot",
                    "result": {
                        "summary": {"network_path": "/obj/geo1", "inferred_purpose": "simulation_with_cache"},
                        "semantics": {
                            "scene_understanding": {
                                "version": 1,
                                "state": "risk_domain_detected",
                                "network_path": "/obj/geo1",
                                "inferred_purpose": "simulation_with_cache",
                                "confidence": "medium",
                                "primary_risk_domain": "simulation_settings",
                                "primary_risk_priority": "high",
                                "primary_focus_path": "/obj/geo1/RBD_SOLVER",
                                "first_read_tools": ["houdini_node_parms", "houdini_upstream", "houdini_scene_snapshot"],
                                "read_targets": [
                                    {
                                        "path": "/obj/geo1/RBD_SOLVER",
                                        "priority": "high",
                                        "kinds": ["simulation"],
                                        "mcp_tools": ["houdini_node_parms", "houdini_upstream"],
                                    }
                                ],
                                "suggested_templates": [
                                    {
                                        "template": "rbd-preview",
                                        "priority": "high",
                                        "input": "/obj/geo1/RBD_SOLVER",
                                        "mcp_tool": "houdini_template_plan",
                                        "required_flow": [
                                            "houdini_review_plan",
                                            "houdini_validate_plan",
                                            "houdini_run_plan",
                                            "houdini_verify_plan",
                                        ],
                                        "local_generation_only": True,
                                    }
                                ],
                                "required_write_flow": [
                                    "houdini_review_plan",
                                    "houdini_validate_plan",
                                    "houdini_run_plan",
                                    "houdini_verify_plan",
                                ],
                                "may_execute": False,
                                "safe_to_run_direct_edits": False,
                                "requires_user_approval_for_writes": True,
                                "next_actions": ["Inspect primary risk domain `simulation_settings` with read-only tools before drafting edits."],
                            },
                            "risk_domains": [
                                {
                                    "domain": "simulation_settings",
                                    "priority": "high",
                                    "paths": ["/obj/geo1/RBD_SOLVER"],
                                    "path_count": 1,
                                    "suggested_tools": ["houdini_node_parms", "houdini_upstream"],
                                    "workflow_templates": ["rbd-preview"],
                                    "reasons": ["Simulation node is present."],
                                }
                            ],
                            "workflow_suggestions": [
                                {
                                    "template": "rbd-preview",
                                    "category": "dynamics",
                                    "priority": "high",
                                    "input_path": "/obj/geo1/RBD_SOLVER",
                                    "mcp_tool": "houdini_template_plan",
                                    "risk_domains": ["simulation_settings"],
                                    "suggested_next_tools": ["houdini_template_plan", "houdini_review_plan"],
                                    "local_generation_only": True,
                                }
                            ],
                        },
                    },
                }
            return {"ok": True, "command": request["command"], "result": {"status": "ok"}}

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://adapter/status"},
            }
        )

        payload = json.loads(response["result"]["contents"][0]["text"])["result"]
        self.assertTrue(payload["session"]["connected"])
        self.assertTrue(payload["session"]["has_token"])
        self.assertNotIn("token", payload["session"])
        self.assertFalse(payload["safety"]["imports_hou"])
        self.assertTrue(payload["safety"]["plan_required_commands_not_direct_tools"])
        self.assertTrue(payload["safety"]["direct_edit_tools_match_policy"])
        self.assertTrue(payload["safety"]["tool_policy_contract_ok"])
        self.assertEqual(payload["safety"]["tool_policy_issues"], [])
        self.assertEqual(payload["safety"]["exposed_plan_required_commands"], [])
        self.assertEqual(payload["safety"]["unexpected_direct_edit_commands"], [])
        self.assertTrue(payload["tools"]["policy_contract"]["ok"])
        self.assertEqual(payload["tools"]["policy_contract"]["issue_count"], 0)
        self.assertIn("delete_node", payload["tools"]["policy_contract"]["hidden_plan_required_commands"])
        self.assertIn("houdini_set_comment", payload["tools"]["direct_edit_tools"])
        self.assertIn("houdini_run_plan", payload["tools"]["plan_transaction_tools"])
        self.assertIn("delete_node", payload["tools"]["hidden_plan_required_commands"])
        self.assertTrue(payload["readiness"]["connected"])
        self.assertTrue(payload["readiness"]["bridge_ok"])
        self.assertTrue(payload["readiness"]["safe_to_connect_client"])
        self.assertFalse(payload["readiness"]["safe_to_run_direct_edits"])
        self.assertTrue(payload["readiness"]["requires_user_approval_for_writes"])
        self.assertTrue(payload["scene_routing"]["queried"])
        self.assertTrue(payload["scene_routing"]["available"])
        self.assertTrue(payload["scene_routing"]["scene_understanding"]["exists"])
        self.assertEqual(payload["scene_routing"]["scene_understanding"]["state"], "risk_domain_detected")
        self.assertEqual(payload["scene_routing"]["scene_understanding"]["primary_focus_path"], "/obj/geo1/RBD_SOLVER")
        self.assertFalse(payload["scene_routing"]["scene_understanding"]["may_execute"])
        self.assertFalse(payload["scene_routing"]["scene_understanding"]["safe_to_run_direct_edits"])
        self.assertEqual(
            payload["scene_routing"]["scene_understanding"]["required_write_flow"],
            ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
        )
        self.assertEqual(payload["scene_routing"]["primary_risk_domain"], "simulation_settings")
        self.assertEqual(payload["scene_routing"]["risk_domain_count"], 1)
        self.assertIn("houdini_node_parms", payload["scene_routing"]["first_read_tools"])
        self.assertIn("rbd-preview", payload["scene_routing"]["risk_domains"][0]["workflow_templates"])
        self.assertEqual(payload["readiness"]["primary_scene_risk_domain"], "simulation_settings")
        self.assertEqual(payload["readiness"]["scene_risk_domain_count"], 1)
        self.assertIn("houdini_node_parms", payload["readiness"]["first_read_tools"])
        self.assertEqual(payload["client_bootstrap"]["action"], "inspect_scene_risk_domain")
        self.assertEqual(payload["client_bootstrap"]["state"], "scene_risk_detected")
        self.assertEqual(payload["client_bootstrap"]["primary_scene_risk_domain"], "simulation_settings")
        self.assertIn("houdini://workflow-templates/risk-domains", payload["client_bootstrap"]["read_resources"])
        self.assertIn("houdini_node_parms", payload["client_bootstrap"]["suggested_tools"])
        self.assertFalse(payload["client_bootstrap"]["may_execute"])
        self.assertFalse(payload["client_bootstrap"]["safe_to_run_direct_edits"])
        self.assertEqual(calls[0]["command"], "health")
        self.assertEqual(calls[1]["command"], "scene_snapshot")
        self.assertFalse(calls[1]["payload"]["include_viewport"])

    def test_adapter_status_marks_unsafe_when_tool_policy_contract_breaks(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)
        tools = adapter.list_tools()
        for tool in tools:
            if tool.get("name") == "houdini_set_comment":
                tool["_meta"]["safety"]["requires_bridge_edit_mode"] = False
                break
        adapter.list_tools = lambda: tools

        status = adapter.status(include_health=False)

        self.assertEqual(status["readiness"]["status"], "unsafe")
        self.assertFalse(status["readiness"]["safe_to_connect_client"])
        self.assertFalse(status["safety"]["tool_policy_contract_ok"])
        self.assertEqual(
            status["safety"]["tool_policy_issues"][0]["kind"],
            "direct_edit_without_edit_mode_contract",
        )
        self.assertFalse(status["success_gate"]["can_report_success_now"])
        self.assertIn("adapter_safety", status["success_gate"]["blocked_by"])

    def test_adapter_status_marks_unsafe_when_direct_edit_verification_contract_breaks(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)
        tools = adapter.list_tools()
        for tool in tools:
            if tool.get("name") == "houdini_set_comment":
                tool["_meta"]["safety"]["verification"] = {
                    "requires_readback": False,
                    "may_report_success_from_rpc_ok": True,
                    "read_tools": ["missing_read_tool"],
                    "mcp_read_tools": ["houdini_missing_read_tool"],
                    "success_criteria": [],
                }
                break
        adapter.list_tools = lambda: tools

        status = adapter.status(include_health=False)

        self.assertEqual(status["readiness"]["status"], "unsafe")
        issue_kinds = {issue["kind"] for issue in status["safety"]["tool_policy_issues"]}
        self.assertIn("direct_edit_verification_without_readback", issue_kinds)
        self.assertIn("direct_edit_verification_allows_rpc_success_claim", issue_kinds)
        self.assertIn("direct_edit_verification_without_success_criteria", issue_kinds)
        self.assertIn("direct_edit_verification_unknown_read_tool", issue_kinds)
        self.assertIn("direct_edit_verification_unexposed_mcp_read_tool", issue_kinds)
        self.assertFalse(status["success_gate"]["can_report_success_now"])
        self.assertIn("adapter_safety", status["success_gate"]["blocked_by"])

    def test_adapter_status_scene_routing_failure_is_diagnostic_only(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            if request["command"] == "scene_snapshot":
                return {
                    "ok": False,
                    "command": "scene_snapshot",
                    "result": {},
                    "error": {"code": "snapshot_failed", "message": "Scene snapshot failed."},
                }
            return {"ok": True, "command": request["command"], "result": {"status": "ok"}}

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        status = adapter.status(include_health=True)

        self.assertEqual(status["readiness"]["status"], "ready")
        self.assertTrue(status["readiness"]["safe_to_connect_client"])
        self.assertFalse(status["readiness"]["safe_to_run_direct_edits"])
        self.assertTrue(status["scene_routing"]["queried"])
        self.assertFalse(status["scene_routing"]["available"])
        self.assertEqual(status["scene_routing"]["error"]["code"], "snapshot_failed")
        self.assertEqual(status["readiness"]["primary_scene_risk_domain"], "unknown")
        self.assertIn("Scene routing snapshot is unavailable.", status["readiness"]["issues"])
        self.assertIn("houdini_rpc_log", status["readiness"]["first_read_tools"])
        self.assertEqual(status["client_bootstrap"]["action"], "recover_scene_routing")
        self.assertEqual(status["client_bootstrap"]["state"], "scene_routing_unavailable")
        self.assertIn("houdini_rpc_log", status["client_bootstrap"]["suggested_tools"])
        self.assertFalse(status["client_bootstrap"]["may_execute"])
        self.assertEqual([item["command"] for item in calls], ["health", "scene_snapshot"])

    def test_adapter_status_offline_reports_bridge_health_without_posting(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=poster)
        status = adapter.status(include_health=True)

        self.assertFalse(status["session"]["connected"])
        self.assertEqual(status["bridge_health"]["error"]["code"], "offline")
        self.assertEqual(status["readiness"]["status"], "offline")
        self.assertFalse(status["readiness"]["safe_to_connect_client"])
        self.assertFalse(status["readiness"]["safe_to_run_direct_edits"])
        self.assertIn("Start Houdini", status["readiness"]["next_actions"][0])
        self.assertFalse(status["scene_routing"]["queried"])
        self.assertEqual(status["readiness"]["scene_risk_domain_count"], 0)
        self.assertEqual(status["readiness"]["primary_scene_risk_domain"], "none")
        self.assertEqual(status["client_bootstrap"]["action"], "start_bridge")
        self.assertEqual(status["client_bootstrap"]["state"], "offline")
        self.assertIn("houdini://session/current", status["client_bootstrap"]["read_resources"])
        self.assertFalse(status["client_bootstrap"]["safe_to_connect_client"])
        self.assertFalse(status["client_bootstrap"]["safe_to_run_direct_edits"])
        self.assertEqual(calls, [])

    def test_adapter_status_summarizes_local_workflow_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            proven_dir = workflow_root / "proven"
            failed_dir = workflow_root / "failed"
            missing_dir = workflow_root / "missing"
            proven_dir.mkdir()
            failed_dir.mkdir()
            missing_dir.mkdir()
            (proven_dir / "proof_report.json").write_text(
                json.dumps({"verdict": "proven", "proof_ready": True, "next_action": "report_success"}),
                encoding="utf-8",
            )
            (proven_dir / "evidence_checklist.json").write_text(
                json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 4, "required_total": 4}}),
                encoding="utf-8",
            )
            (proven_dir / "summary.md").write_text("# Proven\n", encoding="utf-8")
            (proven_dir / "evidence_manifest.json").write_text(
                json.dumps({"artifact_integrity": {"all_existing_hashed": True, "existing_count": 3, "hashed_count": 3}}),
                encoding="utf-8",
            )
            (failed_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "failed",
                        "proof_ready": False,
                        "next_action": "review_failed_checks",
                        "rollback_recommended": True,
                        "client_guidance": {
                            "rollback_guidance": {
                                "recommended": True,
                                "resource": "houdini://workflow/failed/rollback-plan",
                                "auto_execute": False,
                                "required_review_flow": [
                                    "houdini_review_plan",
                                    "houdini_validate_plan",
                                    "houdini_run_plan",
                                    "houdini_verify_plan",
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(session_loader=lambda path=None: None, poster=_ok_poster, workflow_root=workflow_root)
            status = adapter.status(include_health=True)

            evidence = status["workflow_evidence"]
            self.assertEqual(status["readiness"]["status"], "offline")
            self.assertEqual(status["readiness"]["workflow_attention_count"], 2)
            self.assertEqual(status["readiness"]["rollback_recommended_count"], 1)
            self.assertEqual(status["readiness"]["workflow_priority_client_state"], "rollback_recommended")
            self.assertEqual(status["readiness"]["workflow_client_state_counts"]["proven"], 1)
            self.assertEqual(status["readiness"]["workflow_client_state_counts"]["rollback_recommended"], 1)
            self.assertEqual(status["readiness"]["workflow_client_state_counts"]["missing_proof"], 1)
            self.assertTrue(
                any("rollback" in item.lower() for item in status["readiness"]["next_actions"])
            )
            self.assertTrue(evidence["local_only"])
            self.assertEqual(evidence["resource"], "houdini://workflow/index")
            self.assertEqual(evidence["workflow_count"], 3)
            self.assertEqual(evidence["proof_ready_count"], 1)
            self.assertEqual(evidence["proven"][0]["name"], "proven")
            self.assertEqual(evidence["proven"][0]["resource"], "houdini://workflow/proven/proof-report")
            self.assertEqual(evidence["success_gate_counts"]["can_report"], 1)
            self.assertEqual(evidence["success_gate_counts"]["blocked"], 2)
            self.assertEqual(evidence["success_gate_blockers"]["rollback_recommended"], 1)
            self.assertEqual(evidence["success_gate_blockers"]["missing_proof_report"], 1)
            self.assertEqual(evidence["verdict_counts"]["proven"], 1)
            self.assertEqual(evidence["verdict_counts"]["failed"], 1)
            self.assertEqual(evidence["verdict_counts"]["missing"], 1)
            self.assertEqual(evidence["client_state_counts"]["proven"], 1)
            self.assertEqual(evidence["client_state_counts"]["rollback_recommended"], 1)
            self.assertEqual(evidence["client_state_counts"]["missing_proof"], 1)
            self.assertEqual(evidence["needs_attention_count"], 2)
            self.assertEqual(evidence["rollback_recommended_count"], 1)
            self.assertEqual(evidence["rollback_recommended"][0]["name"], "failed")
            self.assertFalse(evidence["rollback_recommended"][0]["auto_execute"])
            self.assertEqual(
                evidence["rollback_recommended"][0]["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            self.assertEqual(evidence["rollback_recommended"][0]["next_client_step"]["action"], "review_rollback_plan")
            self.assertFalse(evidence["rollback_recommended"][0]["next_client_step"]["may_execute"])
            self.assertTrue(evidence["rollback_recommended"][0]["next_client_step"]["requires_user_approval"])
            self.assertIn("houdini_validate_plan", evidence["rollback_recommended"][0]["next_client_step"]["suggested_tools"])
            self.assertEqual(status["client_bootstrap"]["action"], "review_rollback_plan")
            self.assertEqual(status["client_bootstrap"]["state"], "rollback_recommended")
            self.assertEqual(status["client_bootstrap"]["workflow"], "failed")
            self.assertIn("houdini://workflow/index", status["client_bootstrap"]["read_resources"])
            self.assertIn("houdini://workflow/failed/rollback-plan", status["client_bootstrap"]["read_resources"])
            self.assertTrue(status["client_bootstrap"]["requires_user_approval"])
            self.assertFalse(status["client_bootstrap"]["may_execute"])
            self.assertFalse(status["success_gate"]["can_report_success_now"])
            self.assertEqual(status["success_gate"]["state"], "blocked")
            self.assertEqual(status["success_gate"]["proven_workflow_count"], 1)
            self.assertEqual(status["success_gate"]["proof_ready_count"], 1)
            self.assertIn("rollback_recommended", status["success_gate"]["blocked_by"])
            self.assertIn("workflow_evidence_needs_attention", status["success_gate"]["blocked_by"])
            self.assertIn("houdini://workflow/proven/proof-report", status["success_gate"]["read_resources"])
            self.assertIn("houdini://workflow/failed/rollback-plan", status["success_gate"]["read_resources"])
            self.assertFalse(status["success_gate"]["may_execute"])
            self.assertFalse(status["success_gate"]["safe_to_run_direct_edits"])
            attention_by_name = {item["name"]: item for item in evidence["needs_attention"]}
            self.assertEqual(attention_by_name["failed"]["next_client_step"]["action"], "review_rollback_plan")
            self.assertEqual(attention_by_name["missing"]["next_client_step"]["action"], "read_workflow_evidence")

    def test_adapter_status_success_gate_allows_only_proven_clean_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            proven_dir = workflow_root / "proven"
            proven_dir.mkdir()
            (proven_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "proven",
                        "proof_ready": True,
                        "next_action": "report_success",
                        "client_guidance": {
                            "mcp_resources": [
                                "houdini://workflow/proven/proof-report",
                                "houdini://workflow/proven/evidence-checklist",
                                "houdini://workflow/proven/summary",
                            ],
                            "suggested_tools": ["houdini_scene_snapshot"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (proven_dir / "evidence_checklist.json").write_text(
                json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 4, "required_total": 4}}),
                encoding="utf-8",
            )
            (proven_dir / "summary.md").write_text("# Proven\n", encoding="utf-8")
            (proven_dir / "evidence_manifest.json").write_text(
                json.dumps({"artifact_integrity": {"all_existing_hashed": True, "existing_count": 3, "hashed_count": 3}}),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["readiness"]["status"], "ready")
            self.assertEqual(status["workflow_evidence"]["proof_ready_count"], 1)
            self.assertEqual(status["workflow_evidence"]["needs_attention_count"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["can_report"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["blocked"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"], {})
            self.assertTrue(status["workflow_evidence"]["direct_edit_readback"]["proof_ready"])
            self.assertEqual(status["workflow_evidence"]["direct_edit_readback"]["workflow_count"], 0)
            self.assertEqual(status["success_gate"]["state"], "proven")
            self.assertTrue(status["success_gate"]["can_report_success_now"])
            self.assertEqual(status["success_gate"]["proven_workflow"], "proven")
            self.assertEqual(status["success_gate"]["reportable_workflow_count"], 1)
            self.assertEqual(status["success_gate"]["blocked_workflow_gate_count"], 0)
            self.assertEqual(status["success_gate"]["blocked_by"], [])
            self.assertIn("houdini://workflow/proven/proof-report", status["success_gate"]["read_resources"])
            self.assertIn("houdini://workflow/proven/evidence-checklist", status["success_gate"]["read_resources"])
            self.assertIn("houdini://workflow/proven/summary", status["success_gate"]["read_resources"])
            self.assertIn("houdini_scene_snapshot", status["success_gate"]["suggested_tools"])
            self.assertFalse(status["success_gate"]["may_execute"])
            self.assertFalse(status["success_gate"]["safe_to_run_direct_edits"])

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            index_payload = json.loads(index_response["result"]["contents"][0]["text"])["result"]
            workflow_gate = index_payload["workflows"][0]["success_gate"]
            self.assertTrue(workflow_gate["can_report_success_now"])
            self.assertEqual(workflow_gate["state"], "proven")
            self.assertEqual(workflow_gate["workflow"], "proven")
            self.assertEqual(workflow_gate["blocked_by"], [])
            self.assertIn("houdini://workflow/proven/proof-report", workflow_gate["read_resources"])
            self.assertIn("houdini://workflow/proven/evidence-checklist", workflow_gate["read_resources"])
            self.assertIn("houdini://workflow/proven/summary", workflow_gate["read_resources"])
            self.assertFalse(workflow_gate["may_execute"])
            self.assertFalse(workflow_gate["safe_to_run_direct_edits"])

    def test_adapter_status_success_gate_blocks_proven_without_required_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            incomplete_dir = workflow_root / "incomplete_proven"
            incomplete_dir.mkdir()
            (incomplete_dir / "proof_report.json").write_text(
                json.dumps({"verdict": "proven", "proof_ready": True, "next_action": "report_success"}),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["proof_ready_count"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["can_report"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["blocked"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["missing_evidence_checklist"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["missing_summary"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["missing_evidence_manifest"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["evidence_incomplete"], 1)
            self.assertFalse(status["success_gate"]["can_report_success_now"])
            self.assertEqual(status["success_gate"]["state"], "blocked")
            self.assertEqual(status["success_gate"]["proven_workflow_count"], 1)
            self.assertEqual(status["success_gate"]["reportable_workflow_count"], 0)
            self.assertEqual(status["success_gate"]["blocked_workflow_gate_count"], 1)
            self.assertIn("no_reportable_workflow", status["success_gate"]["blocked_by"])
            self.assertIn("workflow_success_gate_blocked", status["success_gate"]["blocked_by"])
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["missing_evidence_checklist"], 1)
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["missing_summary"], 1)
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["missing_evidence_manifest"], 1)
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["evidence_incomplete"], 1)

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            index_payload = json.loads(index_response["result"]["contents"][0]["text"])["result"]
            workflow_gate = index_payload["workflows"][0]["success_gate"]
            self.assertFalse(workflow_gate["can_report_success_now"])
            self.assertEqual(workflow_gate["state"], "blocked")
            self.assertIn("missing_evidence_checklist", workflow_gate["blocked_by"])
            self.assertIn("missing_summary", workflow_gate["blocked_by"])
            self.assertIn("missing_evidence_manifest", workflow_gate["blocked_by"])
            self.assertIn("evidence_incomplete", workflow_gate["blocked_by"])

    def test_adapter_status_success_gate_blocks_unverified_artifact_integrity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            unverified_dir = workflow_root / "unverified"
            unverified_dir.mkdir()
            (unverified_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "proven",
                        "proof_ready": True,
                        "next_action": "report_success",
                        "client_guidance": {
                            "mcp_resources": [
                                "houdini://workflow/unverified/proof-report",
                                "houdini://workflow/unverified/evidence-checklist",
                                "houdini://workflow/unverified/summary",
                                "houdini://workflow/unverified/evidence-manifest",
                            ],
                            "suggested_tools": ["houdini_scene_snapshot"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (unverified_dir / "evidence_checklist.json").write_text(
                json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 4, "required_total": 4}}),
                encoding="utf-8",
            )
            (unverified_dir / "summary.md").write_text("# Unverified\n", encoding="utf-8")
            (unverified_dir / "evidence_manifest.json").write_text(
                json.dumps(
                    {
                        "artifact_integrity": {
                            "all_existing_hashed": False,
                            "existing_count": 3,
                            "hashed_count": 2,
                            "unhashed_artifacts": ["summary"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["proof_ready_count"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["can_report"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["blocked"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["artifact_integrity_unverified"], 1)
            self.assertFalse(status["success_gate"]["can_report_success_now"])
            self.assertEqual(status["success_gate"]["state"], "blocked")
            self.assertEqual(status["success_gate"]["reportable_workflow_count"], 0)
            self.assertEqual(status["success_gate"]["blocked_workflow_gate_count"], 1)
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["artifact_integrity_unverified"], 1)
            self.assertIn("workflow_success_gate_blocked", status["success_gate"]["blocked_by"])

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            index_payload = json.loads(index_response["result"]["contents"][0]["text"])["result"]
            workflow = index_payload["workflows"][0]
            self.assertFalse(workflow["success_gate"]["can_report_success_now"])
            self.assertIn("artifact_integrity_unverified", workflow["success_gate"]["blocked_by"])
            self.assertFalse(workflow["evidence"]["artifact_integrity"]["all_existing_hashed"])
            self.assertEqual(workflow["evidence"]["artifact_integrity"]["unhashed_artifacts"], ["summary"])

    def test_adapter_status_success_gate_blocks_failed_direct_edit_readback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            workflow_dir = workflow_root / "direct_edit_failed"
            workflow_dir.mkdir()
            (workflow_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "proven",
                        "proof_ready": True,
                        "next_action": "report_success",
                        "direct_edit_readback": {
                            "exists": True,
                            "proof_ready": False,
                            "total": 2,
                            "passed": 1,
                            "failed": 1,
                            "inconclusive": 0,
                            "commands": ["create_node", "set_comment"],
                            "failed_commands": ["set_comment"],
                        },
                        "client_guidance": {
                            "mcp_resources": [
                                "houdini://workflow/direct_edit_failed/proof-report",
                                "houdini://workflow/direct_edit_failed/evidence-checklist",
                                "houdini://workflow/direct_edit_failed/summary",
                                "houdini://workflow/direct_edit_failed/evidence-manifest",
                            ],
                            "suggested_tools": ["houdini_verify_plan", "houdini_node_info"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (workflow_dir / "evidence_checklist.json").write_text(
                json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 4, "required_total": 4}}),
                encoding="utf-8",
            )
            (workflow_dir / "summary.md").write_text("# Direct edit failed\n", encoding="utf-8")
            (workflow_dir / "evidence_manifest.json").write_text(
                json.dumps({"artifact_integrity": {"all_existing_hashed": True, "existing_count": 3, "hashed_count": 3}}),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["proven"], [])
            self.assertEqual(status["workflow_evidence"]["needs_attention_count"], 1)
            self.assertEqual(status["workflow_evidence"]["needs_attention"][0]["verdict"], "direct_edit_readback_failed")
            self.assertEqual(status["workflow_evidence"]["needs_attention"][0]["next_client_step"]["action"], "inspect_failed_checks")
            self.assertEqual(status["workflow_evidence"]["client_state_counts"]["failed"], 1)
            self.assertEqual(status["readiness"]["workflow_priority_client_state"], "failed")
            self.assertEqual(status["client_bootstrap"]["action"], "inspect_failed_checks")
            self.assertEqual(status["client_bootstrap"]["state"], "direct_edit_readback_failed")
            self.assertEqual(status["client_bootstrap"]["workflow"], "direct_edit_failed")
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["can_report"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["blocked"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["direct_edit_readback_not_ready"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["direct_edit_readback_failed"], 1)
            readback_status = status["workflow_evidence"]["direct_edit_readback"]
            self.assertFalse(readback_status["proof_ready"])
            self.assertEqual(readback_status["workflow_count"], 1)
            self.assertEqual(readback_status["proof_ready_count"], 0)
            self.assertEqual(readback_status["failed_count"], 1)
            self.assertEqual(readback_status["inconclusive_count"], 0)
            self.assertEqual(readback_status["total_checks"], 2)
            self.assertEqual(readback_status["passed_checks"], 1)
            self.assertEqual(readback_status["failed_checks"], 1)
            self.assertEqual(readback_status["inconclusive_checks"], 0)
            self.assertEqual(readback_status["commands"], ["create_node", "set_comment"])
            self.assertEqual(readback_status["failed_commands"], ["set_comment"])
            self.assertEqual(readback_status["inconclusive_commands"], [])
            self.assertEqual(readback_status["needs_attention"][0]["name"], "direct_edit_failed")
            self.assertEqual(readback_status["needs_attention"][0]["failed_commands"], ["set_comment"])
            self.assertFalse(status["success_gate"]["can_report_success_now"])
            self.assertIn("workflow_success_gate_blocked", status["success_gate"]["blocked_by"])
            self.assertIn("no_proven_workflow", status["success_gate"]["blocked_by"])
            self.assertIn("workflow_evidence_needs_attention", status["success_gate"]["blocked_by"])
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["direct_edit_readback_failed"], 1)

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            workflow = json.loads(index_response["result"]["contents"][0]["text"])["result"]["workflows"][0]
            self.assertFalse(workflow["success_gate"]["can_report_success_now"])
            self.assertIn("direct_edit_readback_not_ready", workflow["success_gate"]["blocked_by"])
            self.assertIn("direct_edit_readback_failed", workflow["success_gate"]["blocked_by"])
            self.assertFalse(workflow["success_gate"]["direct_edit_readback"]["proof_ready"])
            self.assertEqual(workflow["next_client_step"]["action"], "inspect_failed_checks")
            self.assertIn("Direct edit readback", workflow["next_client_step"]["reason"])
            self.assertEqual(workflow["client_state"]["status"], "failed")
            self.assertEqual(workflow["proof"]["direct_edit_readback"]["failed_commands"], ["set_comment"])
            self.assertEqual(workflow["evidence"]["direct_edit_readback"]["commands"], ["create_node", "set_comment"])

    def test_adapter_status_success_gate_verifies_manifest_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            _write_verified_workflow(workflow_root)

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            workflow = json.loads(index_response["result"]["contents"][0]["text"])["result"]["workflows"][0]

            self.assertTrue(workflow["success_gate"]["can_report_success_now"])
            self.assertTrue(workflow["proof"]["template_verification_focus"]["ready"])
            self.assertEqual(workflow["proof"]["template_verification_focus"]["template"], "sop-cleanup")
            self.assertIn("output_flags_set", workflow["proof"]["template_verification_focus"]["success_criteria"])
            self.assertTrue(workflow["evidence"]["template_verification_focus"]["ready"])
            self.assertIn("houdini_network", workflow["evidence"]["template_verification_focus"]["read_tools"])
            self.assertTrue(workflow["proof"]["scene_evidence"]["exists"])
            self.assertEqual(workflow["proof"]["scene_evidence"]["before"]["primary_risk_domain"], "none")
            self.assertEqual(workflow["proof"]["scene_evidence"]["after"]["primary_risk_domain"], "render_settings")
            self.assertEqual(workflow["proof"]["scene_evidence"]["after"]["primary_focus_path"], "/obj/geo1/OUT")
            self.assertIn("render_settings", workflow["proof"]["scene_evidence"]["transition"]["risk_domains_added"])
            self.assertFalse(workflow["proof"]["scene_evidence"]["may_execute"])
            self.assertFalse(workflow["proof"]["scene_evidence"]["safe_to_run_direct_edits"])
            self.assertTrue(workflow["proof"]["scene_evidence"]["requires_user_approval_for_writes"])
            self.assertEqual(workflow["evidence"]["scene_evidence"]["after"]["risk_domains"][0]["domain"], "render_settings")
            self.assertIn("karma-solaris-preview", workflow["evidence"]["scene_evidence"]["after"]["suggested_templates"])
            verification = workflow["evidence"]["manifest_verification"]
            self.assertTrue(verification["all_manifest_artifacts_verified"])
            self.assertEqual(verification["checked_count"], 3)
            self.assertEqual(verification["passed_count"], 3)
            self.assertEqual(verification["failed_count"], 0)

    def test_adapter_status_success_gate_blocks_tampered_manifest_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            workflow_dir = _write_verified_workflow(workflow_root)
            (workflow_dir / "summary.md").write_text("# Tampered\n", encoding="utf-8")

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["success_gate_counts"]["can_report"], 0)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_hash_mismatch"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_verification_failed"], 1)
            self.assertFalse(status["success_gate"]["can_report_success_now"])
            self.assertEqual(status["success_gate"]["success_gate_blockers"]["manifest_artifact_hash_mismatch"], 1)

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            workflow = json.loads(index_response["result"]["contents"][0]["text"])["result"]["workflows"][0]
            self.assertIn("manifest_artifact_hash_mismatch", workflow["success_gate"]["blocked_by"])
            self.assertIn("manifest_artifact_verification_failed", workflow["success_gate"]["blocked_by"])
            self.assertEqual(workflow["evidence"]["manifest_verification"]["failed_count"], 1)
            self.assertEqual(workflow["evidence"]["manifest_verification"]["mismatched"][0]["key"], "summary")

    def test_adapter_status_success_gate_blocks_missing_manifest_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            workflow_dir = _write_verified_workflow(workflow_root)
            (workflow_dir / "summary.md").unlink()

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_missing"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_verification_failed"], 1)
            self.assertFalse(status["success_gate"]["can_report_success_now"])

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            workflow = json.loads(index_response["result"]["contents"][0]["text"])["result"]["workflows"][0]
            self.assertIn("manifest_artifact_missing", workflow["success_gate"]["blocked_by"])
            self.assertEqual(workflow["evidence"]["manifest_verification"]["missing"][0]["key"], "summary")

    def test_adapter_status_success_gate_blocks_unsafe_manifest_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            workflow_dir = _write_verified_workflow(workflow_root)
            manifest_path = workflow_dir / "evidence_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            outside_path = workflow_root / "outside.txt"
            outside_path.write_text("outside\n", encoding="utf-8")
            manifest["artifacts"][0]["path"] = str(outside_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_unsafe_path"], 1)
            self.assertEqual(status["workflow_evidence"]["success_gate_blockers"]["manifest_artifact_verification_failed"], 1)
            self.assertFalse(status["success_gate"]["can_report_success_now"])

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            workflow = json.loads(index_response["result"]["contents"][0]["text"])["result"]["workflows"][0]
            self.assertIn("manifest_artifact_unsafe_path", workflow["success_gate"]["blocked_by"])
            self.assertEqual(workflow["evidence"]["manifest_verification"]["unsafe_paths"][0]["key"], "proof_report")

    def test_adapter_readiness_degrades_when_connected_with_workflow_attention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            failed_dir = workflow_root / "failed"
            failed_dir.mkdir()
            (failed_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "failed",
                        "proof_ready": False,
                        "next_action": "review_failed_checks",
                    }
                ),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(
                session_loader=lambda path=None: SESSION,
                poster=lambda session, request: {"ok": True, "command": request["command"], "result": {"status": "ok"}},
                workflow_root=workflow_root,
            )
            status = adapter.status(include_health=True)

            self.assertEqual(status["readiness"]["status"], "degraded")
            self.assertTrue(status["readiness"]["connected"])
            self.assertTrue(status["readiness"]["bridge_ok"])
            self.assertTrue(status["readiness"]["safe_to_connect_client"])
            self.assertFalse(status["readiness"]["safe_to_run_direct_edits"])
            self.assertEqual(status["readiness"]["workflow_attention_count"], 1)
            self.assertEqual(status["readiness"]["workflow_priority_client_state"], "failed")
            self.assertEqual(status["readiness"]["workflow_client_state_counts"]["failed"], 1)
            self.assertIn(
                "Read houdini://workflow/index and follow each workflow next_client_step.",
                status["readiness"]["next_actions"],
            )
            self.assertEqual(status["client_bootstrap"]["action"], "inspect_failed_checks")
            self.assertEqual(status["client_bootstrap"]["state"], "failed")
            self.assertEqual(status["client_bootstrap"]["workflow"], "failed")
            self.assertIn("houdini://workflow/index", status["client_bootstrap"]["read_resources"])
            self.assertFalse(status["client_bootstrap"]["may_execute"])

    def test_workflow_resources_are_listed_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_root = Path(tmpdir)
            workflow_dir = workflow_root / "unit"
            workflow_dir.mkdir()
            (workflow_dir / "evidence_manifest.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workflow": "unit",
                        "artifact_integrity": {
                            "artifact_count": 12,
                            "existing_count": 6,
                            "missing_count": 6,
                            "hashed_count": 6,
                            "all_existing_hashed": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (workflow_dir / "evidence_checklist.json").write_text(
                json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 7, "required_total": 7, "warning_count": 0}}),
                encoding="utf-8",
            )
            (workflow_dir / "proof_report.json").write_text(
                json.dumps(
                    {
                        "verdict": "failed",
                        "proof_ready": False,
                        "next_action": "review_failed_checks",
                        "rollback_recommended": True,
                        "client_guidance": {
                            "mcp_resources": [
                                "houdini://workflow/unit/proof-report",
                                "houdini://workflow/unit/rollback-plan",
                            ],
                            "suggested_tools": ["houdini_rpc_log", "houdini_scene_snapshot"],
                            "repair_guidance": {
                                "recommended": True,
                                "action": "draft_repair_plan",
                                "auto_execute": False,
                                "may_execute": False,
                                "requires_user_approval": True,
                                "read_resources": [
                                    "houdini://workflow/unit/proof-report",
                                    "houdini://workflow/unit/evidence-checklist",
                                    "houdini://workflow/unit/summary",
                                ],
                                "diagnostic_read_tools": [
                                    "houdini_rpc_log",
                                    "houdini_scene_snapshot",
                                    "houdini_node_info",
                                    "houdini_node_parms",
                                ],
                                "required_review_flow": [
                                    "houdini_review_plan",
                                    "houdini_validate_plan",
                                    "houdini_run_plan",
                                    "houdini_verify_plan",
                                ],
                                "failed_check_kinds": ["created_path"],
                                "inconclusive_check_kinds": [],
                                "direct_edit_readback": {
                                    "exists": True,
                                    "proof_ready": False,
                                    "commands": ["create_node", "set_comment"],
                                    "failed_commands": ["set_comment"],
                                    "inconclusive_commands": [],
                                },
                                "direct_edit_failed_commands": ["set_comment"],
                                "direct_edit_inconclusive_commands": [],
                                "missing_artifacts": [],
                            },
                            "rollback_guidance": {
                                "recommended": True,
                                "resource": "houdini://workflow/unit/rollback-plan",
                                "auto_execute": False,
                                "direct_edit_readback": {
                                    "exists": True,
                                    "proof_ready": False,
                                    "failed_commands": ["set_comment"],
                                    "inconclusive_commands": [],
                                },
                                "required_review_flow": [
                                    "houdini_review_plan",
                                    "houdini_validate_plan",
                                    "houdini_run_plan",
                                    "houdini_verify_plan",
                                ],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (workflow_dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
            (workflow_dir / "rollback_plan.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workflow_contract": {
                            "state": "draft_unreviewed",
                            "evidence_only": True,
                            "does_not_execute": True,
                            "auto_execute": False,
                            "requires_review": True,
                            "requires_validation": True,
                            "requires_user_approval": True,
                            "required_flow": [
                                "houdini_review_plan",
                                "houdini_validate_plan",
                                "houdini_run_plan",
                                "houdini_verify_plan",
                            ],
                            "may_execute": False,
                            "safe_to_run_direct_edits": False,
                        },
                        "client_guidance": {
                            "next_action": "review_rollback_plan",
                            "requires_user_approval": True,
                            "may_execute": False,
                            "safe_to_run_direct_edits": False,
                        },
                        "steps": [],
                        "unresolved": [],
                    }
                ),
                encoding="utf-8",
            )
            (workflow_dir / "visual_evidence.json").write_text(
                json.dumps(
                    {
                        "status": "captured",
                        "captured": True,
                        "path": "C:/Temp/unit.png",
                        "proof_role": "supporting_capture_only",
                        "semantic_verdict": "not_judged",
                        "requires_visual_judgment": True,
                        "may_report_visual_success": False,
                        "visual_success_claim_allowed": False,
                    }
                ),
                encoding="utf-8",
            )

            adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster, workflow_root=workflow_root)
            resources = adapter.handle_message({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})["result"]["resources"]
            uris = {resource["uri"] for resource in resources}
            self.assertIn("houdini://workflow/unit/evidence-manifest", uris)
            self.assertIn("houdini://workflow/unit/evidence-checklist", uris)
            self.assertIn("houdini://workflow/unit/proof-report", uris)
            self.assertIn("houdini://workflow/unit/summary", uris)
            self.assertIn("houdini://workflow/unit/rollback-plan", uris)
            self.assertIn("houdini://workflow/unit/visual-evidence", uris)
            self.assertIn("houdini://workflow/index", uris)

            index_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/index"},
                }
            )
            index_payload = json.loads(index_response["result"]["contents"][0]["text"])["result"]
            self.assertEqual(index_payload["count"], 1)
            self.assertEqual(index_payload["workflows"][0]["name"], "unit")
            self.assertEqual(index_payload["workflows"][0]["proof"]["verdict"], "failed")
            self.assertFalse(index_payload["workflows"][0]["proof"]["proof_ready"])
            self.assertEqual(index_payload["workflows"][0]["proof"]["visual"]["semantic_verdict"], "not_judged")
            self.assertEqual(index_payload["workflows"][0]["proof"]["visual"]["proof_role"], "supporting_capture_only")
            self.assertTrue(index_payload["workflows"][0]["proof"]["visual"]["requires_visual_judgment"])
            self.assertFalse(index_payload["workflows"][0]["proof"]["visual"]["may_report_visual_success"])
            self.assertEqual(index_payload["workflows"][0]["evidence"]["visual"]["semantic_verdict"], "not_judged")
            self.assertFalse(index_payload["workflows"][0]["evidence"]["visual"]["visual_success_claim_allowed"])
            self.assertEqual(index_payload["workflows"][0]["client_state"]["status"], "rollback_recommended")
            self.assertEqual(index_payload["workflows"][0]["client_state"]["next_action"], "review_rollback_plan")
            self.assertTrue(index_payload["workflows"][0]["client_state"]["requires_user_approval"])
            self.assertFalse(index_payload["workflows"][0]["client_state"]["may_execute"])
            self.assertTrue(index_payload["workflows"][0]["client_state"]["evidence_complete"])
            self.assertFalse(index_payload["workflows"][0]["success_gate"]["can_report_success_now"])
            self.assertEqual(index_payload["workflows"][0]["success_gate"]["state"], "blocked")
            self.assertEqual(index_payload["workflows"][0]["success_gate"]["workflow"], "unit")
            self.assertEqual(index_payload["workflows"][0]["success_gate"]["verdict"], "failed")
            self.assertIn("verdict_failed", index_payload["workflows"][0]["success_gate"]["blocked_by"])
            self.assertIn("proof_not_ready", index_payload["workflows"][0]["success_gate"]["blocked_by"])
            self.assertIn("rollback_recommended", index_payload["workflows"][0]["success_gate"]["blocked_by"])
            self.assertIn("houdini://workflow/unit/proof-report", index_payload["workflows"][0]["success_gate"]["read_resources"])
            self.assertIn("houdini://workflow/unit/rollback-plan", index_payload["workflows"][0]["success_gate"]["read_resources"])
            self.assertFalse(index_payload["workflows"][0]["success_gate"]["may_execute"])
            self.assertFalse(index_payload["workflows"][0]["success_gate"]["safe_to_run_direct_edits"])
            self.assertEqual(index_payload["workflows"][0]["proof"]["next_action"], "review_failed_checks")
            self.assertTrue(index_payload["workflows"][0]["proof"]["rollback_recommended"])
            self.assertTrue(index_payload["workflows"][0]["proof"]["rollback_guidance"]["recommended"])
            self.assertFalse(index_payload["workflows"][0]["proof"]["rollback_guidance"]["auto_execute"])
            self.assertEqual(
                index_payload["workflows"][0]["proof"]["rollback_guidance"]["direct_edit_readback"]["failed_commands"],
                ["set_comment"],
            )
            self.assertEqual(
                index_payload["workflows"][0]["proof"]["rollback_guidance"]["resource"],
                "houdini://workflow/unit/rollback-plan",
            )
            self.assertEqual(
                index_payload["workflows"][0]["proof"]["rollback_guidance"]["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            repair_guidance = index_payload["workflows"][0]["proof"]["repair_guidance"]
            self.assertTrue(repair_guidance["recommended"])
            self.assertEqual(repair_guidance["action"], "draft_repair_plan")
            self.assertFalse(repair_guidance["auto_execute"])
            self.assertFalse(repair_guidance["may_execute"])
            self.assertTrue(repair_guidance["requires_user_approval"])
            self.assertIn("houdini_node_parms", repair_guidance["diagnostic_read_tools"])
            self.assertEqual(repair_guidance["failed_check_kinds"], ["created_path"])
            self.assertEqual(repair_guidance["direct_edit_failed_commands"], ["set_comment"])
            self.assertEqual(repair_guidance["direct_edit_readback"]["failed_commands"], ["set_comment"])
            self.assertEqual(index_payload["workflows"][0]["evidence"]["required_passed"], 7)
            self.assertTrue(index_payload["workflows"][0]["evidence"]["artifact_integrity"]["all_existing_hashed"])
            self.assertEqual(index_payload["workflows"][0]["evidence"]["artifact_integrity"]["hashed_count"], 6)
            self.assertIn("proof-report", index_payload["workflows"][0]["resources"])
            self.assertIn("houdini_rpc_log", index_payload["workflows"][0]["client_guidance"]["suggested_tools"])
            self.assertFalse(index_payload["workflows"][0]["client_guidance"]["rollback_guidance"]["auto_execute"])
            self.assertFalse(index_payload["workflows"][0]["client_guidance"]["repair_guidance"]["may_execute"])
            next_step = index_payload["workflows"][0]["next_client_step"]
            self.assertEqual(next_step["action"], "review_rollback_plan")
            self.assertTrue(next_step["requires_user_approval"])
            self.assertFalse(next_step["may_execute"])
            self.assertTrue(next_step["repair_guidance"]["recommended"])
            self.assertFalse(next_step["repair_guidance"]["auto_execute"])
            self.assertFalse(next_step["repair_guidance"]["may_execute"])
            self.assertEqual(next_step["repair_guidance"]["direct_edit_failed_commands"], ["set_comment"])
            self.assertIn("houdini_node_info", next_step["repair_guidance"]["diagnostic_read_tools"])
            self.assertIn("houdini://workflow/unit/rollback-plan", next_step["read_resources"])
            self.assertEqual(
                next_step["required_review_flow"],
                ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
            )
            self.assertIn("houdini_validate_plan", next_step["suggested_tools"])

            manifest_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/evidence-manifest"},
                }
            )
            manifest_payload = json.loads(manifest_response["result"]["contents"][0]["text"])
            self.assertEqual(manifest_payload["version"], 1)

            checklist_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/evidence-checklist"},
                }
            )
            checklist_payload = json.loads(checklist_response["result"]["contents"][0]["text"])
            self.assertTrue(checklist_payload["proof_ready"])

            proof_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/proof-report"},
                }
            )
            proof_payload = json.loads(proof_response["result"]["contents"][0]["text"])
            self.assertEqual(proof_payload["verdict"], "failed")
            self.assertFalse(proof_payload["proof_ready"])

            summary_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/summary"},
                }
            )
            self.assertEqual(summary_response["result"]["contents"][0]["mimeType"], "text/markdown")
            self.assertIn("# Summary", summary_response["result"]["contents"][0]["text"])

            visual_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/visual-evidence"},
                }
            )
            visual_payload = json.loads(visual_response["result"]["contents"][0]["text"])
            self.assertEqual(visual_payload["status"], "captured")
            self.assertEqual(visual_payload["semantic_verdict"], "not_judged")
            self.assertFalse(visual_payload["may_report_visual_success"])

            rollback_response = adapter.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "resources/read",
                    "params": {"uri": "houdini://workflow/unit/rollback-plan"},
                }
            )
            rollback_payload = json.loads(rollback_response["result"]["contents"][0]["text"])
            self.assertEqual(rollback_payload["workflow_contract"]["state"], "draft_unreviewed")
            self.assertTrue(rollback_payload["workflow_contract"]["evidence_only"])
            self.assertTrue(rollback_payload["workflow_contract"]["does_not_execute"])
            self.assertFalse(rollback_payload["workflow_contract"]["auto_execute"])
            self.assertTrue(rollback_payload["workflow_contract"]["requires_user_approval"])
            self.assertFalse(rollback_payload["workflow_contract"]["may_execute"])
            self.assertFalse(rollback_payload["client_guidance"]["may_execute"])

    def test_workflow_resource_rejects_unsafe_name(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)

        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "houdini://workflow/../summary"},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)

    def test_run_plan_exposed_as_transaction_entry(self):
        calls = []
        steps = [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}]
        steps_sha256 = hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        def poster(session, request):
            calls.append(request)
            return {"ok": True, "command": request["command"], "result": {"ok": True, "ran": 1}}

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {
                        "steps": steps,
                        "validation": {
                            "valid": True,
                            "ready_to_run": True,
                            "step_count": 1,
                            "steps_sha256": steps_sha256,
                            "would_require_edit": True,
                        },
                        "review": {
                            "level": "warning",
                            "confidence": 0.75,
                            "required_confirmations": ["delete_node"],
                            "validation": {"step_count": 1, "steps_sha256": steps_sha256},
                        },
                        "confirmed_required_confirmations": ["delete_node"],
                    },
                },
            }
        )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(calls[0]["command"], "run_plan")
        self.assertNotIn("validation", calls[0]["payload"])
        self.assertNotIn("review", calls[0]["payload"])
        self.assertNotIn("confirmed_required_confirmations", calls[0]["payload"])
        result = response["result"]["structuredContent"]["result"]
        self.assertEqual(result["mcp_preflight"]["step_count"], 1)
        self.assertEqual(result["mcp_preflight"]["steps_sha256"], steps_sha256)
        self.assertEqual(result["mcp_preflight"]["validation"]["step_count"], 1)
        self.assertEqual(result["mcp_preflight"]["review"]["level"], "warning")
        self.assertEqual(result["mcp_preflight"]["review"]["confirmed_required_confirmations"], ["delete_node"])
        self.assertEqual(result["next_required_tool"], "houdini_verify_plan")

    def test_run_plan_requires_review_and_validation_before_bridge_rpc(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {"steps": [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}]},
                },
            }
        )

        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "plan_preflight_required")
        self.assertTrue(result["structuredContent"]["result"]["blocked_before_bridge_rpc"])
        self.assertIn("missing_validation", result["structuredContent"]["result"]["blocked_by"])
        self.assertIn("missing_review", result["structuredContent"]["result"]["blocked_by"])
        self.assertEqual(calls, [])

    def test_run_plan_rejects_mismatched_plan_evidence_before_bridge_rpc(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        steps = [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}]
        response = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster).handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {
                        "steps": steps,
                        "validation": {"valid": True, "ready_to_run": True, "step_count": 2},
                        "review": {"level": "warning", "confidence": 0.75, "validation": {"step_count": 2}},
                    },
                },
            }
        )

        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "plan_preflight_required")
        self.assertIn("validation_step_count_mismatch", result["result"]["blocked_by"])
        self.assertIn("review_validation_step_count_mismatch", result["result"]["blocked_by"])
        self.assertEqual(result["result"]["step_count"], 1)
        self.assertEqual(calls, [])

    def test_run_plan_requires_explicit_review_confirmations_before_bridge_rpc(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        steps = [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}]
        steps_sha256 = hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        response = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster).handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {
                        "steps": steps,
                        "validation": {"valid": True, "ready_to_run": True, "step_count": 1, "steps_sha256": steps_sha256},
                        "review": {
                            "level": "warning",
                            "confidence": 0.75,
                            "required_confirmations": ["Step 1 deletes `/obj/geo1/BAD`; confirm it is disposable cleanup."],
                            "validation": {"step_count": 1, "steps_sha256": steps_sha256},
                        },
                    },
                },
            }
        )

        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "plan_preflight_required")
        self.assertIn("missing_required_confirmations", result["result"]["blocked_by"])
        self.assertEqual(
            result["result"]["missing_required_confirmations"],
            ["Step 1 deletes `/obj/geo1/BAD`; confirm it is disposable cleanup."],
        )
        self.assertEqual(calls, [])

    def test_run_plan_rejects_mismatched_steps_hash_before_bridge_rpc(self):
        calls = []

        def poster(session, request):
            calls.append(request)
            return _ok_poster(session, request)

        steps = [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}]
        wrong_sha = hashlib.sha256(b"other-plan").hexdigest()
        response = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=poster).handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {
                        "steps": steps,
                        "validation": {"valid": True, "ready_to_run": True, "step_count": 1, "steps_sha256": wrong_sha},
                        "review": {"level": "warning", "confidence": 0.75, "validation": {"step_count": 1, "steps_sha256": wrong_sha}},
                    },
                },
            }
        )

        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "plan_preflight_required")
        self.assertIn("validation_steps_sha256_mismatch", result["result"]["blocked_by"])
        self.assertIn("review_steps_sha256_mismatch", result["result"]["blocked_by"])
        self.assertEqual(calls, [])

    def test_run_plan_rejects_blocked_or_not_ready_preflight(self):
        adapter = BridgeMCPAdapter(session_loader=lambda path=None: SESSION, poster=_ok_poster)
        response = adapter.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "houdini_run_plan",
                    "arguments": {
                        "steps": [{"command": "delete_node", "payload": {"node": "/obj/geo1/BAD", "confirm": True}}],
                        "validation": {"valid": True, "ready_to_run": False, "step_count": 1},
                        "review": {"level": "blocked", "confidence": 0.1},
                    },
                },
            }
        )

        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "plan_preflight_required")
        self.assertIn("Validation is not ready", result["error"]["message"])
        self.assertIn("Review is blocked", result["error"]["message"])

    def test_cli_status_prints_adapter_diagnostics(self):
        with _capture_stdout() as output:
            result = main(["--status"])

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["adapter"]["name"], "blib-houdini-bridge")
        self.assertIn("direct_edit_tools", payload["tools"])
        self.assertIn("readiness", payload)
        self.assertIn(payload["readiness"]["status"], {"ready", "degraded", "offline", "unsafe"})
        self.assertIn("workflow_client_state_counts", payload["readiness"])
        self.assertIn("workflow_priority_client_state", payload["readiness"])
        self.assertIn("client_bootstrap", payload)
        self.assertIn(payload["client_bootstrap"]["action"], {"start_bridge", "read_scene_context", "inspect_scene_risk_domain", "recover_scene_routing", "review_rollback_plan", "inspect_failed_checks", "collect_missing_evidence", "read_workflow_evidence", "repair_adapter_safety"})
        self.assertFalse(payload["client_bootstrap"]["safe_to_run_direct_edits"])
        self.assertIn("success_gate", payload)
        self.assertFalse(payload["success_gate"]["safe_to_run_direct_edits"])
        self.assertFalse(payload["success_gate"]["may_execute"])
        self.assertNotIn('"token":', output.getvalue())

    def test_cli_print_config_preserves_custom_session_path(self):
        session_path = "C:/Temp/blib_hou_bridge/custom-session.json"
        with _capture_stdout() as output:
            result = main(["--session", session_path, "--print-config"])

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        server = payload["mcpServers"]["blib-houdini-bridge"]
        self.assertEqual(server["command"], sys.executable)
        self.assertIn("blib_hou_mcp.py", server["args"][0])
        self.assertEqual(server["args"][-2:], ["--session", session_path])
        self.assertNotIn("token", output.getvalue().lower())

    def test_cli_print_codex_config_outputs_toml(self):
        session_path = "C:/Temp/blib_hou_bridge/custom-session.json"
        with _capture_stdout() as output:
            result = main(["--session", session_path, "--print-codex-config"])

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("[mcp_servers.blib-houdini-bridge]", text)
        self.assertIn("command =", text)
        self.assertIn("args = [", text)
        self.assertIn("blib_hou_mcp.py", text)
        self.assertIn("--session", text)
        self.assertIn(session_path, text)
        self.assertNotIn("token", text.lower())


def _ok_poster(session, request):
    return {"ok": True, "command": request["command"], "result": {}}


def _write_verified_workflow(workflow_root: Path, name: str = "verified") -> Path:
    workflow_dir = workflow_root / name
    workflow_dir.mkdir()
    template_focus = {
        "exists": True,
        "required": True,
        "ready": True,
        "template": "sop-cleanup",
        "read_tools": ["houdini_verify_plan", "houdini_network", "houdini_node_info"],
        "success_criteria": ["output_null_exists", "output_flags_set"],
        "evidence_artifacts": ["verification", "snapshot_after", "summary"],
        "notes": ["Verify structure before viewport appearance."],
        "read_tool_count": 3,
        "success_criteria_count": 2,
        "evidence_artifact_count": 3,
    }
    (workflow_dir / "proof_report.json").write_text(
        json.dumps(
            {
                "verdict": "proven",
                "proof_ready": True,
                "next_action": "report_success",
                "template_verification_focus": template_focus,
                "client_guidance": {
                    "mcp_resources": [
                        "houdini://workflow/%s/proof-report" % name,
                        "houdini://workflow/%s/evidence-checklist" % name,
                        "houdini://workflow/%s/summary" % name,
                        "houdini://workflow/%s/evidence-manifest" % name,
                    ],
                    "suggested_tools": ["houdini_scene_snapshot", "houdini_verify_plan", "houdini_network"],
                    "template_verification_focus": template_focus,
                },
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "evidence_checklist.json").write_text(
        json.dumps({"status": "pass", "proof_ready": True, "summary": {"required_passed": 4, "required_total": 4}}),
        encoding="utf-8",
    )
    (workflow_dir / "summary.md").write_text("# Verified\n", encoding="utf-8")
    artifacts = [
        _manifest_artifact(workflow_dir, "proof_report", "proof_report.json"),
        _manifest_artifact(workflow_dir, "evidence_checklist", "evidence_checklist.json"),
        _manifest_artifact(workflow_dir, "summary", "summary.md"),
        {"key": "snapshot_after", "path": str(workflow_dir / "snapshot_after.json"), "exists": False, "bytes": 0, "sha256": ""},
    ]
    existing = [item for item in artifacts if item["exists"]]
    scene_evidence = {
        "version": 1,
        "exists": True,
        "before": {
            "exists": True,
            "network_path": "/obj/geo1",
            "inferred_purpose": "sop_or_general_node_network",
            "scene_understanding": {
                "exists": True,
                "state": "network_context",
                "primary_risk_domain": "none",
                "primary_focus_path": "",
                "first_read_tools": ["houdini_scene_snapshot"],
                "suggested_templates": [],
                "required_write_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
                "may_execute": False,
                "safe_to_run_direct_edits": False,
            },
            "risk_domains": [],
            "risk_domain_count": 0,
            "first_read_tools": ["houdini_scene_snapshot"],
            "suggested_templates": [],
        },
        "after": {
            "exists": True,
            "network_path": "/obj/geo1",
            "inferred_purpose": "sop_or_general_node_network",
            "scene_understanding": {
                "exists": True,
                "state": "risk_domain_detected",
                "primary_risk_domain": "render_settings",
                "primary_focus_path": "/obj/geo1/OUT",
                "first_read_tools": ["houdini_node_info", "houdini_node_parms"],
                "suggested_templates": ["karma-solaris-preview"],
                "required_write_flow": ["houdini_review_plan", "houdini_validate_plan", "houdini_run_plan", "houdini_verify_plan"],
                "may_execute": False,
                "safe_to_run_direct_edits": False,
            },
            "risk_domains": [
                {
                    "domain": "render_settings",
                    "priority": "medium",
                    "paths": ["/obj/geo1/OUT"],
                    "path_count": 1,
                    "suggested_tools": ["houdini_node_info", "houdini_node_parms"],
                    "workflow_templates": ["karma-solaris-preview"],
                }
            ],
            "risk_domain_count": 1,
            "first_read_tools": ["houdini_node_info", "houdini_node_parms", "houdini_scene_snapshot"],
            "suggested_templates": ["karma-solaris-preview"],
        },
        "transition": {
            "inferred_purpose_changed": False,
            "primary_risk_domain_changed": True,
            "risk_domains_added": ["render_settings"],
            "risk_domains_removed": [],
            "node_count_delta": 1,
            "wire_count_delta": 1,
        },
        "may_execute": False,
        "safe_to_run_direct_edits": False,
        "requires_user_approval_for_writes": True,
    }
    (workflow_dir / "evidence_manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "workflow": name,
                "artifacts": artifacts,
                "template_verification_focus": template_focus,
                "scene_evidence": scene_evidence,
                "artifact_integrity": {
                    "artifact_count": len(artifacts),
                    "existing_count": len(existing),
                    "missing_count": len(artifacts) - len(existing),
                    "hashed_count": len(existing),
                    "all_existing_hashed": True,
                    "missing_artifacts": ["snapshot_after"],
                    "unhashed_artifacts": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return workflow_dir


def _manifest_artifact(workflow_dir: Path, key: str, filename: str) -> dict:
    path = workflow_dir / filename
    return {
        "key": key,
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": _test_file_sha256(path),
    }


def _test_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _capture_stdout:
    def __enter__(self):
        import io

        self._old = sys.stdout
        self._buffer = io.StringIO()
        sys.stdout = self._buffer
        return self._buffer

    def __exit__(self, exc_type, exc, tb):
        sys.stdout = self._old
        return False


if __name__ == "__main__":
    unittest.main()
