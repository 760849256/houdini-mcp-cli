# Blib Houdini MCP Adapter

`blib_hou_mcp.py` exposes the local Blib Houdini Bridge as a stdio MCP server.
It does not import `hou`; it reads the bridge session file and calls the
existing `BlibHouBridge` RPC endpoint.

## Everyday workflow

The stdio adapter now defaults to **daily** mode: it advertises exactly six
tools: `houdini_context`, `houdini_inspect`, `houdini_edit`, `houdini_execute`,
`houdini_look`, and `houdini_job`. Start with context, make the edit, then look.
Use `--tool-profile advanced` (or `BLIB_HOU_TOOL_PROFILE=advanced`) to advertise
the full legacy tool collection. The underlying legacy calls remain compatible.
Embedded Python callers retain the advanced constructor default; pass
`BridgeMCPAdapter(tool_profile="daily")` for the six-tool list.

- `houdini_context`: returns current network, selected nodes and a lightweight
  `context_token` without evaluating geometry. Pass this token to edit when
  operating on a previously inspected context.
- `houdini_inspect`: default to a summary of selected nodes or current network
  children. Use `root`, `query` and `limit` to scope the summary. Set
  `parm_names` to read named values, `details: true` for full node details, or
  `kind: errors` for errors and their direct input paths. Geometry inspection
  requires `cook: true`.

- `houdini_edit`: submit 1–100 ordinary edit steps in one request. Houdini
  validates, applies and verifies the batch against the live context and
  returns the applied-step count, changed paths and any failed check.
  Add `preview` to return one inline image after verification. Use the job ID
  to look up an interrupted submission.
- `houdini_execute`: run custom Python in editable mode. Supply `inputs` as
  JSON and read it as `bridge_inputs`. Assign a JSON value to `bridge_result`
  to return structured `result.data`.
  Defaults to waiting up to 10 seconds (maximum 30); longer jobs return their
  UUID for `houdini_job`. A generated UUID is also returned on
  an uncertain transport outcome. Reuse the UUID; never blindly resubmit.
  `succeeded` means the Python finished, not that the visual task is correct.
- `houdini_look`: returns inline images from viewport, camera, network, pane,
  or both (viewport + network). Use `angles` for a front/right/top sheet and
  `frames` for a labeled strip. Viewport, frame and network state are restored
  after capture. The Houdini **预览** menu uses the same capture implementation.
  If Houdini cannot provide network pane pixels, the result includes a labeled
  graph drawn from the requested network's nodes and connections. Viewport requests default to 768 x 432; request
  larger width/height for detail. `camera` temporarily looks through an object
  camera and restores the previous camera, view and lock state. `target` frames
  SOP/object geometry bounds and may cook; it does not change display flags.
  Camera capture cannot be combined with target/alternate-view framing.
  The tool does not start a render. A partial two-image capture retains the
  successful image while reporting failure. Captures use visible tabs on the
  current desktop, rather than a global first matching pane. All-black or
  unreadable viewport previews are reported as unusable evidence, even when
  the screenshot API successfully wrote a PNG.
- `houdini_job`: reads the status of an existing Python UUID without resubmitting
  it. Cancellation is not offered because arbitrary Python cannot safely be
  interrupted through this bridge.

Node fields in edit accept `@selected`, `@selected_parent`, `@network`, and
suffixes such as `@selected_parent/OUT_PREVIEW`. An omitted `node` uses a single
selection. An omitted creation parent uses that selection's parent, or the
current network if nothing is selected. Multiple selections return candidates
instead of guessing. Connection destinations must always be explicit. Look
path/target/camera and inspect targets accept the same aliases.

Changes of scene, selection or current network invalidate the context token;
scene load/clear invalidates it even if the HIP filename stays the same.
Reload the Houdini-side Bridge before using guarded edits: an older server
without tokens is rejected instead of silently losing the guard.

Example `houdini_edit` arguments (replace paths with inspected scene paths):

```json
{
  "steps": [
    {"command": "create_node", "payload": {"parent": "/obj/geo1", "type": "normal", "name": "normal_preview"}},
    {"command": "create_node", "payload": {"parent": "/obj/geo1", "type": "null", "name": "OUT_PREVIEW"}},
    {"command": "connect", "payload": {"src": "/obj/geo1/box1", "dst": "/obj/geo1/normal_preview", "input_index": 0}},
    {"command": "connect", "payload": {"src": "/obj/geo1/normal_preview", "dst": "/obj/geo1/OUT_PREVIEW", "input_index": 0}},
    {"command": "set_flags", "payload": {"node": "/obj/geo1/OUT_PREVIEW", "display": true}},
    {"command": "layout", "payload": {"path": "/obj/geo1"}}
  ]
}
```

The default edit response contains impact, warnings, counts and failed or
inconclusive checks; `details: true` includes the full run and verification.
It does not cook geometry solely to prove visual quality. Follow with
`houdini_look` when visual inspection is needed.

Python, deletion and other advanced operations are excluded from `houdini_edit`.
Plans requiring additional confirmations return `not_executed` with their
review; use the existing advanced plan workflow for those. An execution or
readback failure returns `needs_inspection` and may leave earlier steps applied.
No automatic retry or rollback is performed. Use these tools within the user's
authorized task. The **只读** switch blocks writes on the Houdini side.

Restart the MCP client to load the new adapter tool list. Reload the Bridge
code in Houdini to use the updated parameter review behavior there too.

### Edit and capture in one call

Add `"preview": {"mode": "viewport", "target": "@selected_parent/OUT_PREVIEW"}`
to an edit request. `mode: camera` takes a `camera` path and
`mode: network` takes a `path`. The host resolves aliases before the edit and
captures after structural verification.

Read `result.verified` and `result.preview.status` separately. An unavailable
preview can accompany a verified edit. To fetch a saved image, call
`houdini_job` with the edit's `job_id`. To capture again, call
`houdini_job` with `action: preview` and the same edit ID. This read-only
task captures the current state of the same live HIP using the original
request. A different instance or a replaced HIP is rejected. If the preview
task is pending, look it up with the separate preview task ID.

### Parameterized Python example

```json
{
  "code": "node = hou.node(bridge_inputs['path'])\nbridge_result = {'type': node.type().name()}",
  "inputs": {"path": "/obj"}
}
```

Pass this object to `houdini_execute`. A successful reply includes
`structuredContent.result.data`. The same script can be reused with different
arguments.

### Verification of this iteration

`tests/test_daily_workflow.py` covers tool profiles, implicit selection routing,
ambiguous selections, stale contexts before and during execution, partial reads,
and restoration after screenshot failures. Run `hython tools/acceptance_daily.py`
in a separate process for real node-chain, parameter, connection-repair and
error-diagnostic checks. This script refuses to run inside a live UI session.

The four tasks passed in isolated Houdini 21.0.440 and 22.0.459 processes.
Timing in the script measures in-process calls only, excluding model/network
latency. On 2026-11-04 the live Houdini 22 connection was restored. The network
image was visually inspected successfully; the viewport returned an all-black
PNG despite reporting success. This exposed global/hidden pane selection and
unreliable Python-wrapper identity deduplication in the pane inventory.
Current-desktop visible-tab selection, stable desktop/name inventory keys, and
black-image rejection were added. The actual captured black image was correctly
classified using Houdini 22's image library. A Houdini-side Reload and repeat
live viewport/camera/restoration checks remain required; these are not claimed
as passed. Windows UI automation still timed out, so it could not perform the
Reload itself. Mock restoration checks do not establish visual capture quality.

## Start Houdini First

1. Open Houdini.
2. Start the `Blib_hou_bridge` shelf tool.
3. Bridge starts editable, including Python. Select **只读** in the panel for
   an inspection and preview session.

## Multiple Houdini instances

Register the standard Blib Houdini Bridge MCP once. In each Houdini window,
open **Bridge → 控制代码**. Each process starts with a random eight-character
code. You can rename it, for example to GROOM-A or CACHE-B, and press **保存**.
Codes are case-insensitive and unique across running Bridges.

Press **复制给 AI** in each Houdini and paste into the corresponding AI chat.
The AI calls houdini_context with control_code to confirm the HIP, then sends
that code and the returned session_id as target_instance on subsequent calls.
Calls share the same MCP entry and route independently. An unavailable code
or mismatched instance rejects the request before sending it to a Houdini.
Bridge stop/start in the same process keeps its code. A new Houdini process
starts with a new random code.

For a fixed MCP entry, **AI 接入** also offers an explicit Houdini process
selection. The resulting --instance configuration only accepts that process.

## MCP Config

Open **Bridge → AI 接入** to see the local Codex registration status and copy
a config for Codex, Antigravity or another stdio MCP client. Select a Houdini
instance to bind the exported config. **测试连接** launches the adapter and
checks the MCP handshake and live host state. In Antigravity IDE,
open **MCP Servers → Manage MCP Servers → View raw config** and merge the Bridge
entry into the existing `mcpServers` object. See the
[Antigravity MCP guide](https://antigravity.google/docs/ide-mcp).

To generate the same formats from a terminal:

```powershell
blib-hou-mcp --print-client-config codex
blib-hou-mcp --print-client-config antigravity
blib-hou-mcp --print-client-config generic
```

MCP initialization includes onboarding instructions. Clients can read
`houdini://adapter/about` even when Houdini is offline to discover the active
tool and response profiles. For live health use `houdini://connection/current`.

For Codex, the least error-prone setup is to let the bridge print the Codex CLI
registration command:

```powershell
blib-hou-mcp --print-codex-add-command
```

Run the printed `codex mcp add blib-houdini-bridge -- ...` command on the
receiving computer, then restart Codex or open a new Codex session.

If you prefer to edit config by hand, print a TOML config block and paste it
into `%UserProfile%\.codex\config.toml`:

```powershell
blib-hou-mcp --print-codex-config
```

For other MCP clients that expect JSON, print a client-ready `mcpServers`
snippet:

```powershell
blib-hou-mcp --print-config
```

If `python` is not on PATH, use the Python executable from your MCP client or
Codex runtime and keep `scripts\cli\blib_hou_mcp.py` as the script argument.
If you use a non-default bridge session file, include it when printing config:

```powershell
blib-hou-mcp --session C:\path\to\session.json --print-codex-add-command
blib-hou-mcp --session C:\path\to\session.json --print-codex-config
blib-hou-mcp --session C:\path\to\session.json --print-config
```

The generated config keeps that `--session` argument but never includes the
bridge token. Do not paste the JSON `mcpServers` snippet directly into Codex;
use `--print-codex-config` for Codex.

If Codex does not list the server, run `codex mcp list` first. A missing
`blib-houdini-bridge` entry means the MCP server is not registered in that
computer's Codex config. If the entry is present but `houdini://adapter/status`
reports `offline`, Codex found the MCP adapter and the next step is to start
Houdini, open the `Blib Bridge` shelf, and click `Bridge`.

## Import Boundary

The stdio entrypoint is a thin CLI shim at `scripts\cli\blib_hou_mcp.py`; the
real adapter package lives under `scripts\python\blib_hou_mcp`. Keep
`scripts\python` ahead of `scripts\cli` in test and client import paths so
`blib_hou_mcp.server` resolves to the package, not the CLI filename. The shim
also repairs this boundary when a client places `scripts\cli` first by
temporarily behaving like a package and pointing submodule imports at the real
adapter directory. The regression gate is:

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
```

Check adapter/session/tool policy before wiring a client:

```powershell
python scripts\cli\blib_hou_mcp.py --status
```

`--doctor` is an alias for the same structured diagnostic. The output reports
the MCP adapter version, bridge protocol version, sanitized session state,
bridge `/health`, exposed direct edit tools, the `houdini_run_plan` transaction
entry, and plan-required bridge commands that are intentionally hidden as direct
MCP tools. It also includes a top-level `readiness` summary for first-screen
triage: `ready`, `degraded`, `offline`, or `unsafe`, plus safe first read tools
and next actions. `readiness.safe_to_run_direct_edits` stays false because this
diagnostic never grants edit permission. The companion `client_bootstrap` block
turns readiness, local workflow evidence, and scene routing into one conservative
next action such as `start_bridge`, `review_rollback_plan`,
`inspect_failed_checks`, `recover_scene_routing`, `inspect_scene_risk_domain`,
or `read_scene_context`. Its `may_execute` and `safe_to_run_direct_edits` fields
stay false, so it is navigation guidance rather than permission. The
`success_gate` block separately answers whether a client may report success from
existing evidence. It only opens when a workflow proof report is `proven` with
`proof_ready=true`, required evidence exists, manifest artifact fingerprints
verify locally, direct edit readback is proof-ready when present, and there are
no rollback or attention blockers; otherwise it lists `blocked_by` reasons and
proof resources that must be read first. `unsafe`
is reported if the adapter's actual tool exposure violates policy, for example
if plan-required bridge commands appear as direct MCP tools. Each MCP tool also
publishes `_meta.safety`, a machine-readable contract with its bridge command,
exposure, edit-mode requirement, review-flow requirement, and proof/success
limits. `adapter/status.tools.policy_contract` audits those contracts against
the bridge protocol; if the contract is broken, `readiness.status` becomes
`unsafe` and success gates stay closed. When the bridge is
connected and healthy, status also performs one read-only `scene_snapshot`
without viewport capture and
adds `scene_routing`: a compact risk-domain summary with
`primary_risk_domain`, prioritized `risk_domains`, template suggestions, and
safe first read tools. Snapshot routing failures are diagnostic only; they do
not grant or remove edit permission. The output also includes a local
`workflow_evidence` summary with proof verdict counts, workflows needing
attention, rollback recommendations, and `client_state_counts` from existing
evidence artifacts. The top-level `readiness` block mirrors the most useful
workflow routing fields as `workflow_client_state_counts` and
`workflow_priority_client_state`, so a client can quickly see whether the first
concern is `rollback_recommended`, `failed`, `incomplete`, `missing_proof`, or a
proven workflow. It also mirrors `scene_risk_domain_count` and
`primary_scene_risk_domain` so clients can decide whether to inspect cache,
simulation, volume, render, file-path, cook-cost, or node-error concerns before
planning edits. The bridge token is never printed.

## Exposed Tools

Read tools include context, selection, scene snapshots, node search, node error
scans, node info, parameter schema reads, geometry summaries, network traces,
node type discovery, material/shader inspection, viewport screenshots, pane
screenshots, multi-pane screenshots, Network Editor screenshots for node paths,
pane listing, SideFX help-link lookup, render settings diagnostics, scene diffs,
safe multi-read diagnostics, plan review, plan validation, plan verification,
RPC log, and edit-mode status.
`houdini_parameter_schema`, `houdini_geometry_summary`, and
`houdini_find_error_nodes` are the first read-only diagnostics pack: they expose
parameter template metadata, compact SOP geometry evidence, and current node
errors or warnings without enabling bridge edit mode.
`houdini_list_panes`, `houdini_pane_screenshot`, and
`houdini_capture_multiple_panes`, and `houdini_network_screenshot` add visual
diagnostics without allowing arbitrary output paths: captures are written only
under the bridge temporary screenshots directory and should be treated as capture
evidence, not proof of visual success unless a later human or visual-model review
records a semantic verdict.
`houdini_list_node_types` exposes a cached, paginated node type catalog for
planning without guessing node names. `houdini_material_info` reads existing
material/shader nodes and likely texture references but does not create or assign
materials. `houdini_get_houdini_help` returns fixed SideFX documentation links and
local node type catalog matches; it does not fetch web pages from Houdini.
`houdini_render_info` reads render-related nodes and key settings such as camera,
output path, renderer, resolution, frame range, and samples without rendering.
`houdini_multi_read` batches a bounded list of safe read-only diagnostics in one
bridge request. It intentionally excludes screenshot/file-capture tools, plan
review/run tools, recursion, and all edit commands. Large-list diagnostics such
as node type discovery, material info, render info, help lookup, node search,
and error scans include `response_meta` with size, count, truncation, and
pagination hints so clients can decide whether to request another page or narrow
the query.
Common read tools also accept a `fields` list for simple dot-path projection,
for example `["path", "flags.display"]` on `houdini_node_info` or
`["preset", "bundle.success_count"]` on `houdini_diagnostic_bundle`. Projection
is a response-size optimization only; field strings are validated dot paths, not
expressions.
`houdini_scene_snapshot` also accepts `mode=full|routing|summary`. `full`
preserves the legacy detailed snapshot, `routing` skips selected-node details
and dependency traces while keeping scene semantics, and `summary` returns a
compact context/network/risk summary. Use `routing` or `summary` for first-pass
triage when a full proof bundle is not needed.
`houdini_node_parms` and `houdini_parameter_schema` support `parm_names`,
`name_contains`, and `limit` filters so clients can avoid evaluating every
parameter on large nodes. `houdini_parameter_schema` can also skip defaults,
menus, or ranges when only parameter names and current values are needed.
`houdini_diagnostic_bundle` exposes built-in safe read presets:
`scene_overview`, `node_debug`, `render_readiness`, and `material_readiness`.
Each preset expands to a bounded `multi_read` plan and keeps the same exclusions
for screenshots, plan tools, recursion, and edit commands.
`houdini_scene_diff` compares two snapshot/network JSON payloads locally so
clients can summarize before/after node and wiring changes without contacting
Houdini.
Bridge-backed MCP tools expose both `inputSchema` and `outputSchema`; bridge
result schemas are used as the base, and MCP-local evidence annotations such as
`mcp_preflight` and `mcp_postflight` are added where the adapter produces them.
For direct edit tools, the bridge manifest also exposes a `verification`
contract with read-back tools, MCP tool names, and success criteria. MCP mirrors
that same contract into `_meta.safety.verification` and successful
`mcp_postflight.verification`, so clients can discover the evidence expectation
before calling a write tool and confirm it again after the bridge RPC returns.
The adapter safety audit treats missing or invalid direct edit verification
contracts as unsafe, including contracts that omit read-back tools, reference
unexposed MCP read tools, lack success criteria, or allow reporting success from
RPC `ok` alone.
For `houdini_scene_snapshot`, `outputSchema` documents
`semantics.focus_candidates`, `risk_domains`, `inspection_hints`,
`workflow_suggestions`, `scene_understanding`, and summary counts so clients can
route cache, simulation, volume, render, file-path, cook-cost, and node-error
concerns to safe follow-up reads or local reviewable templates before planning
edits. `scene_understanding` is the compact route contract: it names the primary
risk domain, first read-only tools, focus targets, suggested draft templates,
and the required review/validate/run/verify write flow while keeping
`may_execute` and `safe_to_run_direct_edits` false.
The proof-chain tools also publish structured result schemas:
`houdini_validate_plan` exposes readiness and edit-gate fields,
`houdini_review_plan` exposes blockers, warnings, confirmations, impact, and
risk notes, `houdini_run_plan` exposes per-step execution results plus
`mcp_preflight`, direct edit tools expose `mcp_postflight`, and
`houdini_verify_plan` exposes the final verification status, summary, and
checks. Clients should use those fields to decide the next workflow step instead
of treating a non-error tool call as proof of success.
The local `houdini_template_plan` tool also exposes an `outputSchema` describing
its `workflow_policy`, template catalog entry, generated plan,
`workflow_contract`, `client_guidance`, evidence expectations,
`verification_focus`, and required review flow.

`houdini_template_plan` is a local read-only tool that expands a workflow
template into a reviewable JSON command plan. It does not read the bridge
session, contact Houdini, or execute anything, so clients can use it while
Houdini is offline. The returned plan should still go through
`houdini_review_plan`, `houdini_validate_plan`, `houdini_run_plan`, and
`houdini_verify_plan` before it is treated as an executed workflow. The returned
`workflow_contract.state` is `draft_unreviewed`, `client_guidance.may_execute`
is false, and `cannot_report_success_before` names the run/verify tools that
must complete before a client can claim success. The template catalog and
generated plan response include `workflow_policy`, risk domains, and evidence
expectations so clients can keep template use inside the reviewable workflow
contract. They also include `verification_focus`: template-specific read tools,
success criteria, evidence artifacts, and notes that clients should use after
execution instead of treating a non-error run as success. The local
`houdini://workflow-templates/risk-domains` resource indexes
the same template catalog by risk domain, so clients can map
`scene_routing.primary_risk_domain` to safe read tools, candidate templates, the
required review flow, and evidence expectations before drafting a plan.
Material create and material assign templates are intentionally plan-only: they
generate `create-node`, `set-parm-any`, `connect`, and evidence-friendly layout
steps, but clients must still review, validate, run, and verify before claiming
that a material was created or assigned.

CLI-generated workflow templates write the same draft contract into
`template_provenance.json` and mirror it into `evidence_manifest.json`, using
MCP tool names in `cannot_report_success_before` so MCP clients and CLI reports
share one proof contract. The same provenance includes `verification_focus`, so
reporting can show the intended post-run success criteria for template-based
workflows.

Direct edit tools are limited to low-risk commands such as `create_node`,
`set_parm`, `batch_set_parms`, `connect`, `set_input`, `disconnect`,
`set_flags`, `set_position`, `set_comment`, `layout`, `select`,
`set_node_color`, and `bypass_node`. They still require Houdini bridge edit
mode. A successful direct edit response includes `mcp_postflight` with changed
paths, suggested read tools, and `may_report_success=false`; clients should read
back the changed state with tools such as `houdini_node_info`,
`houdini_node_parms`, `houdini_network`, or `houdini_rpc_log` before claiming the
task succeeded. `houdini_node_info` includes direct-edit-visible node state such
as comment text, display/render/bypass/selected flags, network-editor position,
and node color, so it can verify common low-risk edits without needing a full
plan transaction. Prefer the command's `verification.success_criteria` over a
generic "tool returned ok" check when deciding whether a direct edit is proven.
When a workflow proof report includes `direct_edit_readback`, clients must treat
it as required evidence: `proof_ready=false`, any failed readback, or any
inconclusive readback keeps `success_gate.can_report_success_now=false` even if
`verify_plan.status` is `pass`.

High-risk edits such as delete, replace, move, copy, and larger graph edits are
not exposed as direct MCP tools. Put them in a plan and run them through
`houdini_review_plan`, `houdini_validate_plan`, `houdini_run_plan`, and
`houdini_verify_plan`.

`houdini_run_plan` is intentionally stricter than the raw bridge RPC command:
MCP clients must pass the matching `validation` result from
`houdini_validate_plan` and `review` result from `houdini_review_plan` along
with `steps`. If validation is not ready or review is blocked, the MCP adapter
rejects the call locally and does not send a bridge RPC. Successful
`validate_plan` reports include `step_count` and `steps_sha256`; `review_plan`
echoes that compact validation fingerprint. `houdini_run_plan` recomputes the
same fingerprint from the submitted `steps` and rejects mismatched
`step_count` or `steps_sha256` evidence before contacting the bridge. If
`review_plan.required_confirmations` is non-empty, clients must pass those exact
strings in `confirmed_required_confirmations`; otherwise `houdini_run_plan`
rejects locally with `blocked_by: ["missing_required_confirmations"]`.
Successful `houdini_run_plan` responses include `mcp_preflight.step_count`,
`mcp_preflight.steps_sha256`, `mcp_preflight.review.confirmed_required_confirmations`,
and `next_required_tool: houdini_verify_plan` so clients can keep the evidence
chain intact.

## Resources

- `houdini://adapter/status`
- `houdini://session/current`
- `houdini://scene/current`
- `houdini://selection/current`
- `houdini://manifest`
- `houdini://safety/policy`
- `houdini://rpc-log/recent`
- `houdini://workflow-templates/catalog`
- `houdini://workflow-templates/risk-domains`
- `houdini://workflow/index`
- `houdini://workflow/<name>/evidence-manifest`
- `houdini://workflow/<name>/evidence-checklist`
- `houdini://workflow/<name>/proof-report`
- `houdini://workflow/<name>/summary`
- `houdini://workflow/<name>/rollback-plan`
- `houdini://workflow/<name>/visual-evidence`

The session resource hides the token and only reports whether one exists.
The adapter status resource is the best first read for MCP clients because it
summarizes connection state, safety policy, bridge health, exposed direct tools,
and hidden plan-required commands.
`houdini://manifest` exposes bridge command metadata, including payload schemas,
result schemas, MCP tool names, permissions, and exposure levels. For example,
`scene_snapshot.result_schema` documents `semantics.focus_candidates`,
`risk_domains`, `inspection_hints`, and the compact summary counts clients
should use before planning edits, while `validate_plan`, `review_plan`,
`run_plan`, and `verify_plan` result schemas document the evidence chain clients
should follow before reporting success.
`houdini://safety/policy` is local to the adapter and mirrors the bridge
manifest safety policy: localhost/session/token requirements, edit-mode gate,
direct edit commands, plan-required commands, blocked danger commands, and
expected evidence artifacts.
Workflow template catalog is local to the adapter and can be read even when the
Houdini bridge is offline. The risk-domain template resource is also local and
maps domains such as `cache_output`, `simulation_settings`,
`volume_resolution`, `render_settings`, `file_path`, and `cook_cost` to safe
read tools, reviewable templates, required flow, evidence expectations, and
template verification focus.
`houdini://workflow/index` is also local and lists workflow evidence directories
with proof verdicts, proof readiness, next action, available resource URIs,
artifact integrity summaries, `client_guidance` summaries, and a compact
`proof.rollback_guidance` digest when rollback is the recommended next step,
plus `proof.repair_guidance` when failed evidence can be used to draft a repair
plan. When `evidence_manifest.json` includes `scene_evidence`, each workflow
entry also exposes a compact before/after scene route digest with
`scene_understanding`, primary risk domain, first read tools, suggested
templates, and risk-domain transition fields. This lets clients explain what
changed in scene risk and which read-only tools should inspect it next.
`scene_evidence.may_execute` and `scene_evidence.safe_to_run_direct_edits` stay
false; it is evidence and routing context, not write permission.
Each workflow entry also includes `client_state` and `next_client_step`.
`client_state.status` is a compact routing summary such as `proven`,
`rollback_recommended`, `failed`, `incomplete`, `missing_proof`, or `unknown`;
`next_client_step` tells clients whether to report success, inspect failed
checks, collect missing evidence, read available evidence, or review a rollback
plan. This is guidance only: `client_state.may_execute` and
`next_client_step.may_execute` are false. Rollback and repair guidance still
require read-only diagnosis, plan review, validation, user approval, run, and
verification before any write tool is called. Each workflow entry also has a
per-workflow `success_gate`; use it to decide whether that one workflow can be
reported as successful after reading its proof resources. The top-level
`adapter/status.success_gate` is stricter because it blocks success claims when
any workflow still needs attention or rollback review. The local
`workflow_evidence` summary also includes `success_gate_counts` and
`success_gate_blockers`, so clients can see how many workflows can report
success and which blocker categories need attention before drilling into
individual entries. A `proof-report` with `verdict=proven` and
`proof_ready=true` is necessary but not sufficient for a success claim; the
per-workflow gate must also pass, which requires evidence such as the checklist
and summary to be present. Missing proof artifacts are reported with explicit
blockers such as `missing_evidence_checklist` and `missing_summary`, not only the
generic `evidence_incomplete`. The gate also requires `evidence_manifest.json`
and verified artifact fingerprints; missing manifests use
`missing_evidence_manifest`, and incomplete hashes use
`artifact_integrity_unverified`. When a manifest includes an `artifacts` list,
the MCP adapter also recomputes each existing artifact's byte size and SHA256
from local disk, then rejects success claims for unsafe paths, missing files, or
hash/size mismatches with blockers such as `manifest_artifact_unsafe_path`,
`manifest_artifact_missing`, `manifest_artifact_hash_mismatch`, and
`manifest_artifact_verification_failed`. Workflow resources expose evidence
already written under `.blib_hou_workflows\<name>\`.
`proof-report` is the compact client verdict: proven, failed, or incomplete,
plus next action, reasons, rollback recommendation, key artifact paths, and
`client_guidance` with MCP resource URIs and suggested follow-up tools for the
next client step. Failed proof reports may also include `repair_guidance`, which
lists read resources, read-only diagnostic tools, failed check kinds, and the
required review flow for a future repair plan. `repair_guidance.auto_execute`
and `repair_guidance.may_execute` stay false; it is a drafting route, not write
permission. Visual evidence reports screenshot capture availability and path,
but it is not a semantic visual pass/fail verdict. New workflow evidence writes
a machine-readable visual proof contract: `proof_role`, `semantic_verdict`,
`requires_visual_judgment`, `may_report_visual_success`, and
`visual_success_claim_allowed`. A captured screenshot defaults to
`proof_role=supporting_capture_only` and `semantic_verdict=not_judged`, so
clients must not claim visual success until a human or visual model records a
`semantic_verdict=pass`.
When workflow evidence contains edit impact or rollback hints, `workflow report`
drafts a local `rollback_plan.json` if one does not already exist. This draft is
evidence only; it is never executed by the MCP adapter. The rollback artifact
also carries its own `workflow_contract` and `client_guidance`: state is
`draft_unreviewed`, `does_not_execute=true`, `auto_execute=false`,
`requires_user_approval=true`, and `may_execute=false`. Even when
`rollback_guidance.recommended` is true, clients must read the rollback plan,
review it, validate it, ask for user approval, run it through `houdini_run_plan`,
and verify it afterward.

The template catalog includes SOP cleanup, cache output, VDB, RBD, Vellum, Pyro,
and `karma-solaris-preview`. The Karma/Solaris template creates a reviewable LOP
network plan with SOP import, material library, Karma render settings, and USD
render ROP setup, but it does not execute a render or save files.

## Inline viewport and batched reads

`houdini_viewport_screenshot` returns a PNG image content block in addition to the existing JSON path when the screenshot can be read locally. Only PNG files directly in the bridge screenshot directory, up to 8 MiB, are attached. If attachment fails, a text message is returned and the original path remains available. `houdini_multi_read` batches up to 20 existing read-only diagnostic calls; it does not accept edits, screenshots, or arbitrary Python.

## Fast Python calls and viewport framing

`houdini_python_submit` waits up to 600 ms by default and returns a completed
job when possible; use `wait_ms` from 0 to 2000 to adjust. Longer jobs retain
the same `job_id` and should be polled with `houdini_python_job_status`. A
`timeout_unknown` response means a request may still have executed; do not
repeat a write blindly. The ordinary RPC timeout remains five seconds.

`houdini_viewport_screenshot` accepts `frame=current|selected|all`. The framed
modes temporarily adjust the free viewport and restore its viewpoint after the
capture, including on failure. Framing refuses viewports looking through or
locked to a camera. A screenshot is evidence to inspect, not proof of quality.

The MCP adapter also exposes `houdini_diagnostic_bundle` (scene overview, node
debug, render readiness, and material readiness), `houdini_material_info`, and
`houdini_render_info` for task-specific checks. These tools inspect current
state; they do not render, assign materials, or judge image quality.


## Focused inspection

`houdini_geometry_summary` accepts `include_health=true` for a bounded polygon
edge-incidence scan. It reports boundary, non-manifold and degenerate edges,
normal attribute owners and whether inspection was complete. Open boundaries
can be intentional; normal presence is not a normal-direction or quality test.
The scan stops at 100,000 primitives or 300,000 edge occurrences and reports
partial results explicitly; non-polygon primitives are counted as skipped.

`houdini_viewport_screenshot` accepts `view=current|front|right|top` and an
optional numeric `timeline_frame`. Repeat calls for multi-angle or multi-frame
inspection. The original timeline frame and free viewport are restored, including
on failure. View changes and auto-framing are refused for camera-linked views;
`view=current` can capture an existing camera view. Switching frames may trigger
cooking, and visual evidence still requires inspection.

The Blib Bridge shelf has one `Bridge` button. It opens the control panel and
starts the service when needed. The panel groups connection, edit and Python
controls and shows their current states; detailed diagnostics can be expanded.
Python execution remains separately gated and enabling it asks for confirmation.
The former individual shelf tools are removed; use the control panel instead.
