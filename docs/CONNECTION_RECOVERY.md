# Connection reliability candidate: 0.3.0a1

This candidate implements the first development batch of the 2026-11-17 optimization plan. The complete roadmap is still in progress.

## What changed

- Every running Houdini process has an instance descriptor under the Bridge runtime directory's `instances/` folder. A compatibility `session.json` remains for older clients.
- A descriptor includes the process creation identity, service generation, lease and cached HIP basename. Reused PIDs, exited processes, stopped services and expired leases are excluded from normal discovery.
- One MCP operation pins its descriptor. An ordinary edit's internal calls cannot silently follow another process that updates the compatibility pointer.
- The existing `houdini_context` tool accepts an optional `instance` ID. Ambiguous discovery returns candidates. An explicit selection binds subsequent operations without adding a seventh daily tool.
- `GET /health` observes service liveness and a main-thread timer independently. It does not inspect the scene. A delayed timer reports `busy_or_unresponsive`, which is not proof of a crash.
- Preflight liveness GETs have bounded retries. No scene write is automatically replayed. If the service restarts, a fresh read is required before further writes.
- Request metadata has correlated operation/request IDs and separate discovery, transport, queue, execution and serialization timings where available.
- Allow-listed JSONL diagnostics rotate locally. Credentials, Python source, payloads and free-form error messages are excluded from persistent diagnostic bundles.
- The control panel has a diagnostic export button and distinguishes a busy main thread. User-selected client instance/profile arguments survive shelf registration.
- Stop retires only its own generation and makes delayed RPC dispatches from that generation fail before starting. Full job lifecycle handling is the next batch.

## Use the new runtime

Restart Bridge in the intended Houdini and restart/reload the MCP process so both sides load this candidate. Existing running Python modules do not update merely because files on disk changed.

For multiple Houdini instances, start Bridge in each intended instance and use the returned instance ID with `houdini_context(instance="...")`. If the bound process exits, another process is not chosen automatically. Select the new target explicitly.

Read-only command-line diagnostics:

```text
blib-hou instances
blib-hou connection
blib-hou diagnostics
blib-hou --instance INSTANCE_ID context

blib-hou-mcp --instances
blib-hou-mcp --connection
blib-hou-mcp --diagnostics
blib-hou-mcp --instance INSTANCE_ID
```

Lightweight MCP resources are `houdini://instances`, `houdini://connection/current` and `houdini://diagnostics/current`. The older `houdini://adapter/status` and legacy `--status` retain full diagnostic behavior for compatibility and may inspect scene/workflow state.

The default runtime directory is `%TEMP%/blib_hou_bridge`; `BLIB_HOU_RUNTIME_DIR` can select a private directory shared by the intended service and client. A manually specified legacy CLI `--session` path remains a compatibility escape hatch; use new registry discovery for creation-identity and lease validation.

## Evidence and limits

The regression and isolated GUI acceptance reports are recorded with this development batch. The harness starts only its own Houdini processes, uses empty private preference directories and a private registry, verifies child PIDs, and modifies only the owned unsaved fixture scene.

Acceptance covers the actual stdio -> HTTP -> GUI main-thread path, a Python edit with structural readback, same-instance service restart, a six-second main-thread wait, and a second actual Houdini process. Timing excludes model latency and measures a small, otherwise empty fixture scene. It is not a production-scene benchmark or evidence of an eight-hour soak.

The first Houdini 22 run encountered a liveness preflight timeout. That failure report was retained. Bounded preflight recovery was then added; subsequent runs passed. Longer observation is still required to establish recurrence rates.

Remaining work includes durable jobs for ordinary edits, a bounded main-thread queue with deadlines and cancellation, responsive MCP message handling during waits, fewer edit/inspect round trips, broader screenshot checks, and sustained production-style validation. Ordinary RPCs can still return an unknown outcome if a submitted operation outlives its response timeout; inspect the scene or query an existing Python job before retrying.

## Reproduce isolated acceptance

```text
python tools/acceptance_connection.py --houdini PATH_TO_HOUDINI_EXE --samples 100 --output REPORT_JSON
python tools/acceptance_connection.py --hython PATH_TO_HYTHON_EXE --samples 100 --output REPORT_JSON
```

The GUI launch uses SideFX's documented [waitforui startup argument](https://www.sidefx.com/docs/houdini/ref/commandline.html). Do not pass an existing production session to this fixture harness.
