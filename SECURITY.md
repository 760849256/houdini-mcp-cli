# Security Policy

Blib Houdini Bridge is designed as a local control layer for Houdini. It should
not be exposed to a network.

## Supported Surface

- The bridge HTTP server binds only to `127.0.0.1`.
- The active session is written to `%TEMP%\blib_hou_bridge\session.json`.
- RPC requests require the session token in both the JSON request and the
  `X-Blib-Bridge-Token` header.
- MCP config generation never prints the bridge token.
- Edit commands require Houdini-side edit mode.
- High-risk graph edits are routed through review, validation, run, and
  verification rather than exposed as direct MCP tools.

## Explicitly Out Of Scope

Direct commands for shell execution, HIP file operations, and file writes remain blocked.
The editable startup mode includes `python_submit`. Submitted code runs in
Houdini with full process privileges. The local **只读** switch disables
scene writes and Python together and invalidates queued write tasks.
Already running code can complete or exit through a cooperative checkpoint.

## Reporting Issues

For public releases, use GitHub Security Advisories when available. If the
advisory channel is not enabled, open a minimal public issue that asks for a
private contact path and do not include tokens, private scene paths, production
file paths, or exploit details in the public issue.

## Operator Guidance

Control access with **只读** in the Houdini Bridge panel. The client can
inspect the access mode or reduce it to read-only. Restoring editable mode
is a local Houdini action.
