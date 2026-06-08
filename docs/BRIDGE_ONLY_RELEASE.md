# Blib Houdini Bridge Release

This document defines the bridge-only release boundary for the Blib Houdini
Bridge. It is intentionally narrower than the full Blib Tools repository.

## Product Identity

Blib Houdini Bridge is a local, auditable Houdini control layer for external
tools and AI clients.

It has three parts:

- Bridge server: runs inside Houdini from `toolbar/Blib_Houdini_Bridge.shelf`.
- CLI client: `blib-hou`, backed by `scripts/cli/blib_hou.py`.
- MCP adapter: `blib-hou-mcp`, backed by `scripts/python/blib_hou_mcp`.

The product is the bridge. The CLI and MCP adapter are client entry points.

## Release Boundary

Include these paths in a bridge-only source release:

- `Blib_Houdini_Bridge.json`
- `LICENSE`
- `CONTRIBUTING.md`
- `README.md`
- `toolbar/Blib_Houdini_Bridge.shelf`
- `scripts/python/blib_hou_bridge/`
- `scripts/python/blib_hou_mcp/`
- `scripts/python/blib_hou.py`
- `scripts/cli/blib_hou.py`
- `scripts/cli/blib_hou_mcp.py`
- `docs/HOUDINI_MCP.md`
- `docs/BRIDGE_ONLY_RELEASE.md`
- `docs/COMPATIBILITY.md`
- `SECURITY.md`
- `pyproject.toml`
- `tools/acceptance_smoke.py`
- `tools/clean_release_artifacts.py`
- `tools/install_houdini_package.py`
- `tools/write_houdini_package.py`
- `tools/validate_bridge_release.py`
- `tests/test_blib_hou_bridge.py`
- `tests/test_blib_hou_mcp.py`

Exclude unrelated Blib production tools, HDA libraries, personal workflow
artifacts, caches, old backups, and local Codex skill mirrors unless the release
is intentionally the full Blib Tools package.

## Install From Source

1. Clone or unpack the release.
2. Install the Python entry points:

```powershell
python -m pip install -e .
```

3. Install or generate a package file for the receiving machine:

```powershell
python tools\install_houdini_package.py
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

   `tools\install_houdini_package.py` tries to write
   `Blib_Houdini_Bridge.json` into a Houdini user packages directory. If no
   package directory is found, use `tools\write_houdini_package.py`, then put
   the generated package in a Houdini package directory and rename it to
   `Blib_Houdini_Bridge.json` if needed. The checked-in
   `Blib_Houdini_Bridge.json` is a template; do not install it until
   `BLIB_HOUDINI_BRIDGE` points at the receiver's local release root.
4. Start Houdini and open the `Blib Bridge` shelf.
5. Click `Bridge` to start the local server. Keep edit mode off until writes are
   intentional.

## CLI Smoke

With Houdini running and the Bridge shelf server started:

```powershell
blib-hou doctor
blib-hou status
blib-hou manifest
blib-hou scene-snapshot --path /obj
```

The bridge writes its active session to `%TEMP%\blib_hou_bridge\session.json`.
The CLI reads that file before trying any RPC call.

## MCP Smoke

Print a client config snippet:

```powershell
blib-hou-mcp --print-config
```

Check adapter readiness:

```powershell
blib-hou-mcp --status
```

Expected healthy state:

- `readiness.status` is `ready` or `degraded`.
- `safety.tool_policy_contract_ok` is `true`.
- `safety.token_exposed` is `false`.
- `scene_routing.available` is `true` when the current scene can be read.
- `success_gate.can_report_success_now` stays `false` until workflow proof
  evidence exists.

## Safety Contract

The bridge binds to `127.0.0.1`, requires a per-session token, and separates
read commands from edit commands. Edit commands require explicit Houdini-side
edit mode.

MCP direct edit tools expose only low-risk bridge commands and still require
bridge edit mode. High-risk graph edits must go through:

```text
review_plan -> validate_plan -> run_plan -> verify_plan
```

MCP status and resources are guidance and evidence only. They never grant write
permission.

## Release Gates

Run these checks before publishing:

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
python tools/clean_release_artifacts.py
python tools/validate_bridge_release.py
python tools/validate_bridge_release.py --strict
python tools/validate_bridge_release.py --public --strict
```

`tools/clean_release_artifacts.py` removes runtime caches and local workflow
evidence directories from the release tree. Review any acceptance smoke evidence
you need before running the cleaner. The default strict validation gate is for
internal handoff to another Houdini user; public distribution should also pass
`--public --strict`.

For a live Houdini smoke, also run:

```powershell
blib-hou doctor
blib-hou-mcp --status
python tools/acceptance_smoke.py
python tools/acceptance_smoke.py --include-write
```

Use `docs/HOUDINI_MCP.md` for MCP client behavior details.

## Recipient Acceptance

A receiver can accept the bridge-only package when these checks are true:

- Houdini loads only the `Blib Bridge` shelf from this package, without the main
  Blib Tools shelves.
- `blib-hou doctor` finds `%TEMP%\blib_hou_bridge\session.json` and passes RPC
  health after the shelf server starts.
- `blib-hou scene-snapshot --path /obj` returns scene context without enabling
  edit mode.
- A write workflow uses `review_plan -> validate_plan -> run_plan -> verify_plan`
  and writes `summary.md`, `evidence_checklist.json`, `proof_report.json`, and
  `evidence_manifest.json` before success is claimed.
- `blib-hou-mcp --status` reports the adapter safety policy, hides the bridge
  token, and keeps `success_gate.can_report_success_now=false` until workflow
  proof evidence exists.
- `python tools/acceptance_smoke.py` passes read-only CLI/MCP checks, and
  `python tools/acceptance_smoke.py --include-write` passes the controlled
  workflow evidence check after edit mode is intentionally enabled.
- Offline, stale-session, token, validation, verification, and evidence failures
  report actionable next actions instead of silent success.

## Failure Diagnostics

Use this table during recipient acceptance. A failure is acceptable only when it
points to a concrete next action.

| Symptom | Likely cause | Next action |
| --- | --- | --- |
| `session file missing` from `blib-hou doctor` | Houdini is not running, or the Bridge shelf server has not been started. | Start Houdini, open the `Blib Bridge` shelf, click `Bridge`, then rerun `blib-hou doctor`. |
| RPC health fails after a valid session file | The saved session is stale, or the Houdini-side server stopped. | Click `Bridge` in Houdini to restart the server, then rerun `blib-hou doctor`. |
| `token or authorization failure` | The CLI/MCP client is using an old session token. | Reread `%TEMP%\blib_hou_bridge\session.json`; do not paste or log the token. |
| `edit mode` blocks a write | Bridge is intentionally read-only. | Review and validate the plan first, then enable `Edit On` from the shelf only for the intended write. |
| `workflow_review_blocked` or validation is not ready | The command list has missing paths, schema errors, or requires edit mode. | Read `review.json` and `validation.json`, fix `plan.json`, then rerun workflow review. |
| `verification failed` | The post-run scene state does not match the reviewed plan. | Read `proof_report.json`, failed `verification.json` checks, and `rollback_plan.json` if present; draft a repair or rollback plan but do not auto-execute it. |
| `evidence incomplete` | Required proof artifacts are missing. | Run `blib-hou workflow report <dir>` and collect the missing artifacts named in `evidence_checklist.json`. |
| `adapter/status` is `unsafe` | MCP tool exposure no longer matches the bridge safety contract. | Do not connect a client; run unit tests and inspect `safety.tool_policy_issues`. |

After any `--include-write` acceptance run, confirm `blib-hou edit-mode status`
reports read mode before handing the scene back to artists.

## Compatibility

See `docs/COMPATIBILITY.md`. Windows with Houdini 21.0.440 is the first
validated live-smoke environment. macOS, Linux, older Houdini versions, and
public PyPI installation remain unvalidated until release notes say otherwise.
