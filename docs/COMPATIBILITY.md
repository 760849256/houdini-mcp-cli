# Compatibility

Blib Houdini Bridge is developed as a local Houdini control layer with a
Windows-first validation path.

## Validated

| Area | Version or environment | Evidence |
| --- | --- | --- |
| Houdini | 21.0.440 on Windows | `tools/acceptance_smoke.py` and `tools/acceptance_smoke.py --include-write` passed against a live Houdini session. |
| Python | 3.12 in the Codex bundled runtime | `python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp` passed. |
| Bridge transport | `127.0.0.1` local RPC with per-session token | Unit tests and live smoke verified localhost binding, token hiding, CLI doctor, and MCP status. |
| MCP adapter | stdio adapter, MCP protocol `2025-06-18` | `blib-hou-mcp --status` passed during live smoke. |

## Not Yet Validated

- macOS
- Linux
- Houdini versions earlier than 21.0
- Public PyPI installation flow

Mark unvalidated platforms clearly in release notes until a maintainer runs the
unit tests, release validation, and live acceptance smoke on that platform.
