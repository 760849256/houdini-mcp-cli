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

## 0.3.0a1 connection candidate (2026-11-17)

Isolated GUI acceptance passed on Windows with Houdini 21.0.440 and 22.0.459,
using tools/acceptance_connection.py. It covers real stdio/HTTP/main-thread calls,
service restart, main-thread busy recovery and two actual Houdini processes.
These are short integration checks; production scenes, long cooks, screenshot
quality and an eight-hour soak remain separate acceptance work. See
[connection recovery](CONNECTION_RECOVERY.md) for limits, including the retained
initial Houdini 22 handshake failure and subsequent passing rechecks.

## 0.3.0a3 task and performance candidate

Windows GUI task acceptance has passed on Houdini 21.0.440 / Python 3.11.7 and
22.0.459 / Python 3.13.10. Source regressions: 255 passing checks.
It covers stdio cancellation, reply loss, queued expiry, same-ID deduplication,
MCP restart, service restart, modal deferral, target identity and repeated-write readback.
Hidden-window screen-region captures may be black; these are reported as unusable,
and are not accepted as visual evidence. Endurance, sleep/wake and actual working-day
observation are separate release gates. See [task contracts](TASKS.md).
