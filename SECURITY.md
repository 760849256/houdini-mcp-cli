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

The bridge does not provide shell execution, arbitrary Python execution, package
installation, HIP file save/load, or unrestricted file writes through the public
RPC command surface.

## Reporting Issues

For public releases, use GitHub Security Advisories when available. If the
advisory channel is not enabled, open a minimal public issue that asks for a
private contact path and do not include tokens, private scene paths, production
file paths, or exploit details in the public issue.

## Operator Guidance

Keep edit mode off unless you intend to allow writes. After using an AI client
or an external automation, return the bridge to read-only mode with the Houdini
`Edit Off` shelf tool.
