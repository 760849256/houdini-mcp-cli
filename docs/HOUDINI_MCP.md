# Blib Houdini MCP Adapter

`blib_hou_mcp.py` exposes the local Blib Houdini Bridge as a stdio MCP server.
It does not import `hou`; it reads the bridge session file and calls the
existing `BlibHouBridge` RPC endpoint.

## Start Houdini First

1. Open Houdini.
2. Start the `Blib_hou_bridge` shelf tool.
3. Enable edit mode only when you intend to allow scene writes.

## MCP Config

For Codex, print a TOML config block and paste it into
`%UserProfile%\.codex\config.toml`:

```powershell
python scripts\cli\blib_hou_mcp.py --print-codex-config
```

For other MCP clients that expect JSON, print a client-ready `mcpServers`
snippet:

```powershell
python scripts\cli\blib_hou_mcp.py --print-config
```

If `python` is not on PATH, use the Python executable from your MCP client or
Codex runtime and keep `scripts\cli\blib_hou_mcp.py` as the script argument.
If you use a non-default bridge session file, include it when printing config:

```powershell
python scripts\cli\blib_hou_mcp.py --session C:\path\to\session.json --print-codex-config
python scripts\cli\blib_hou_mcp.py --session C:\path\to\session.json --print-config
```

The generated config keeps that `--session` argument but never includes the
bridge token. Do not paste the JSON `mcpServers` snippet directly into Codex;
use `--print-codex-config` for Codex.

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

Read tools include context, selection, scene snapshots, node search, node info,
network traces, viewport screenshots, plan review, plan validation, plan
verification, RPC log, and edit-mode status.
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
