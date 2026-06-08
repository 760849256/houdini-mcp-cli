# Contributing

Thanks for helping improve Blib Houdini Bridge. This project is intentionally
small and bridge-only: keep changes scoped to the local Houdini control layer,
CLI, MCP adapter, release tools, and tests in this repository.

## Development Setup

Install from the repository root:

```powershell
python -m pip install -e .
```

Run the unit test gate:

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
```

Run the bridge-only release gate before opening a pull request:

```powershell
python tools\clean_release_artifacts.py
python tools\validate_bridge_release.py --public --strict
```

## Live Houdini Smoke

For changes that affect the shelf, CLI, MCP adapter, command protocol, or
workflow proof artifacts, also run a live Houdini smoke:

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
python tools\acceptance_smoke.py
python tools\acceptance_smoke.py --include-write
python scripts\cli\blib_hou.py edit-mode status
```

After a write smoke, confirm edit mode is back to read mode.

## Safety Rules

- Do not add shell execution, arbitrary Python execution, package installation,
  HIP save/load, or unrestricted file writes to the public RPC surface.
- Keep high-risk graph edits behind review, validation, run, and verification.
- Do not print, commit, or paste bridge session tokens.
- Do not commit production HIP paths, client names, private file paths, or local
  workflow evidence.
- Keep `bridge/` independent from the larger Blib Tools package.

## Pull Request Checklist

- Unit tests pass.
- Public strict release validation passes.
- New commands have protocol metadata, tests, and safety documentation.
- User-facing docs are updated when CLI, MCP, install, or release behavior
  changes.
- Local artifacts have been cleaned with `tools\clean_release_artifacts.py`.
