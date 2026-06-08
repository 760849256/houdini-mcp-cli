"""Validate the bridge-only release surface."""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT_PLACEHOLDER = "__REPLACE_WITH_ABSOLUTE_BRIDGE_ROOT__"

REQUIRED_PATHS = [
    "Blib_Houdini_Bridge.json",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "toolbar/Blib_Houdini_Bridge.shelf",
    "scripts/python/blib_hou_bridge/__init__.py",
    "scripts/python/blib_hou_bridge/auth.py",
    "scripts/python/blib_hou_bridge/commands.py",
    "scripts/python/blib_hou_bridge/protocol.py",
    "scripts/python/blib_hou_bridge/server.py",
    "scripts/python/blib_hou_bridge/shelf.py",
    "scripts/python/blib_hou_mcp/__init__.py",
    "scripts/python/blib_hou_mcp/server.py",
    "scripts/python/blib_hou.py",
    "scripts/cli/blib_hou.py",
    "scripts/cli/blib_hou_mcp.py",
    "docs/HOUDINI_MCP.md",
    "docs/BRIDGE_ONLY_RELEASE.md",
    "docs/COMPATIBILITY.md",
    "SECURITY.md",
    "pyproject.toml",
    "tools/acceptance_smoke.py",
    "tools/clean_release_artifacts.py",
    "tools/install_houdini_package.py",
    "tools/write_houdini_package.py",
    "tests/test_blib_hou_bridge.py",
    "tests/test_blib_hou_mcp.py",
]

FORBIDDEN_RELEASE_DIRS = [
    ".blib_hou_workflows",
    ".test_home",
    "__pycache__",
]

REQUIRED_DOC_MARKERS = {
    "README.md": [
        "Codex, MCP clients, CLI scripts",
        "connect to Houdini",
        "Fast Path: Connect Codex To Houdini",
        "Use With Codex Or Similar Tools",
        "%UserProfile%\\.codex\\config.toml",
        "[mcp_servers.blib-houdini-bridge]",
        "codex mcp list",
        "python tools\\install_houdini_package.py",
        "python tools\\write_houdini_package.py --output Blib_Houdini_Bridge.local.json",
        "docs/BRIDGE_ONLY_RELEASE.md",
        "python scripts\\cli\\blib_hou.py doctor",
        "python scripts\\cli\\blib_hou.py scene-snapshot --path /obj",
        "python scripts\\cli\\blib_hou_mcp.py --status",
        "python scripts\\cli\\blib_hou_mcp.py --print-codex-config",
        "python scripts\\cli\\blib_hou_mcp.py --print-config",
        "Safety Model",
        "中文说明",
    ],
    "docs/BRIDGE_ONLY_RELEASE.md": [
        "acceptance_smoke.py",
        "clean_release_artifacts.py",
        "install_houdini_package.py",
        "write_houdini_package.py",
        "docs/COMPATIBILITY.md",
        "blib-hou doctor",
        "blib-hou-mcp --status",
        "python tools/validate_bridge_release.py --public --strict",
        "review_plan -> validate_plan -> run_plan -> verify_plan",
        "## Failure Diagnostics",
        "session file missing",
        "token or authorization failure",
        "verification failed",
    ],
}

FORBIDDEN_SHELF_IMPORTS = [
    "blib_agent",
    "Blib_tools",
    "MY_TOOLS",
]

PUBLIC_FORBIDDEN_TEXT = [
    "C:/Users",
    "C:\\Users",
    "D:/houdini_plugins",
    "D:\\houdini_plugins",
    "LOREAL",
    "jianxin",
]

PUBLIC_TEXT_PATHS = [
    "Blib_Houdini_Bridge.json",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "docs/BRIDGE_ONLY_RELEASE.md",
    "docs/HOUDINI_MCP.md",
    "pyproject.toml",
]


def _bridge_root_from_package(package: dict) -> str | None:
    env = package.get("env", [])
    if isinstance(env, list):
        for item in env:
            if isinstance(item, dict) and "BLIB_HOUDINI_BRIDGE" in item:
                value = item["BLIB_HOUDINI_BRIDGE"]
                return value if isinstance(value, str) else None
    return None


def validate_release(public: bool = False) -> tuple[int, int, list[tuple[str, str]]]:
    findings: list[tuple[str, str]] = []
    for rel in REQUIRED_PATHS:
        if not (ROOT / rel).exists():
            findings.append(("ERROR", "Missing required bridge release path: %s" % rel))

    package_path = ROOT / "Blib_Houdini_Bridge.json"
    if package_path.exists():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except Exception as exc:
            findings.append(("ERROR", "Cannot parse Blib_Houdini_Bridge.json: %s" % exc))
        else:
            bridge_root = _bridge_root_from_package(package)
            if not bridge_root:
                findings.append(("ERROR", "Blib_Houdini_Bridge.json must define BLIB_HOUDINI_BRIDGE."))
            elif bridge_root == PACKAGE_ROOT_PLACEHOLDER:
                pass
            else:
                resolved_root = Path(bridge_root).expanduser().resolve()
                if resolved_root != ROOT:
                    findings.append(
                        (
                            "WARN",
                            "Blib_Houdini_Bridge.json points at %s, not this release root %s. Regenerate it with tools/write_houdini_package.py after moving the release."
                            % (bridge_root, ROOT),
                        )
                    )
            if package.get("path") != "$BLIB_HOUDINI_BRIDGE":
                findings.append(("WARN", 'Blib_Houdini_Bridge.json path should be "$BLIB_HOUDINI_BRIDGE".'))

    pyproject = ROOT / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        for needle in ('blib-hou = "blib_hou:main"', 'blib-hou-mcp = "blib_hou_mcp.server:main"'):
            if needle not in text:
                findings.append(("ERROR", "pyproject.toml missing console script: %s" % needle))
        if public and "license" not in text.lower():
            findings.append(("WARN", "pyproject.toml does not declare a project license."))

    if public:
        if not (ROOT / "LICENSE").exists():
            findings.append(("WARN", "LICENSE file is required before public release."))
        for rel in PUBLIC_TEXT_PATHS:
            path = ROOT / rel
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for needle in PUBLIC_FORBIDDEN_TEXT:
                if needle in text:
                    findings.append(("WARN", "Public text contains local/private marker %s in %s" % (needle, rel)))

    shelf_path = ROOT / "toolbar" / "Blib_Houdini_Bridge.shelf"
    if shelf_path.exists():
        shelf_text = shelf_path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SHELF_IMPORTS:
            if needle in shelf_text:
                findings.append(("ERROR", "Bridge shelf must not depend on main Blib Tools surface: %s" % needle))
        for needle in ("blib_hou_bridge", "shelf.toggle_server", "shelf.show_inspector"):
            if needle not in shelf_text:
                findings.append(("ERROR", "Bridge shelf missing expected standalone entrypoint: %s" % needle))

    for rel, markers in REQUIRED_DOC_MARKERS.items():
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                findings.append(("WARN", "%s does not document release gate or install marker: %s" % (rel, marker)))

    for rel in FORBIDDEN_RELEASE_DIRS:
        if (ROOT / rel).exists():
            findings.append(("WARN", "Local-only directory exists and should be excluded from release: %s" % rel))
    pycache_count = sum(1 for _ in ROOT.rglob("__pycache__"))
    pyc_count = sum(1 for _ in ROOT.rglob("*.pyc"))
    if pycache_count:
        findings.append(("WARN", "__pycache__ directories found: %s" % pycache_count))
    if pyc_count:
        findings.append(("WARN", ".pyc files found: %s" % pyc_count))

    error_count = sum(1 for level, _ in findings if level == "ERROR")
    warn_count = sum(1 for level, _ in findings if level == "WARN")
    return error_count, warn_count, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_bridge_release", description="Validate the bridge-only release surface.")
    parser.add_argument("--public", action="store_true", help="Also check public-publication requirements such as license metadata.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as release-blocking failures.")
    args = parser.parse_args(argv)

    error_count, warn_count, findings = validate_release(public=args.public)
    print("Bridge-only release validation root: %s" % ROOT)
    if not findings:
        print("OK: bridge-only release surface looks complete.")
    else:
        for level, message in findings:
            print("%s: %s" % (level, message))
        print("Summary: %s error(s), %s warning(s)" % (error_count, warn_count))
    if args.strict and warn_count:
        print("STRICT: warnings are release-blocking.")
        return 1
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
