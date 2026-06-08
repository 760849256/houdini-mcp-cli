# Blib Houdini Bridge

Safe local Houdini control for external tools and AI clients, with CLI and MCP
support.

This folder is the bridge-only release. It is independent from the larger Blib
Tools production toolkit.

Start here:

- [Release guide](docs/BRIDGE_ONLY_RELEASE.md)
- [MCP guide](docs/HOUDINI_MCP.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Security policy](SECURITY.md)
- [Contributing guide](CONTRIBUTING.md)
- [License](LICENSE)

## Install Smoke

From this `bridge/` directory, install the CLI entry points:

```powershell
python -m pip install -e .
```

Create a Houdini package JSON for this machine. The checked-in
`Blib_Houdini_Bridge.json` is a template with a placeholder path; generate a
local package file before installing:

```powershell
python tools\write_houdini_package.py --output Blib_Houdini_Bridge.local.json
```

Copy the generated package file into a Houdini packages directory and rename it
to `Blib_Houdini_Bridge.json` if needed.

Start Houdini, open the `Blib Bridge` shelf, and click `Bridge`.

Quick smoke after starting the shelf server:

```powershell
python scripts\cli\blib_hou.py doctor
python scripts\cli\blib_hou.py scene-snapshot --path /obj
python scripts\cli\blib_hou_mcp.py --status
python tools\acceptance_smoke.py
```

To include a controlled write acceptance, enable bridge edit mode from the
shelf, then run:

```powershell
python tools\acceptance_smoke.py --include-write
```

Before publishing or handing the folder to another user, run:

```powershell
python -m unittest tests.test_blib_hou_bridge tests.test_blib_hou_mcp
python tools\clean_release_artifacts.py
python tools\validate_bridge_release.py
python tools\validate_bridge_release.py --strict
python tools\validate_bridge_release.py --public --strict
```

`tools\clean_release_artifacts.py` removes local workflow evidence such as
`.blib_hou_workflows\acceptance_smoke` from the release tree after you have
reviewed the acceptance result. The default validation gate is for internal
handoff to another Houdini user; add `--public` before publishing outside the
team.
