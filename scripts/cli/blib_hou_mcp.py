"""Stdio MCP entrypoint for the Blib Houdini Bridge adapter."""

from __future__ import annotations

import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON_DIR = os.path.join(ROOT, "scripts", "python")
PACKAGE_DIR = os.path.join(PYTHON_DIR, "blib_hou_mcp")


def _prepend_sys_path(path: str) -> None:
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


_prepend_sys_path(PYTHON_DIR)

# If scripts/cli is ahead of scripts/python, Python can import this shim as the
# top-level ``blib_hou_mcp`` module. Make that accidental import behave like a
# package so ``blib_hou_mcp.server`` still resolves to the real adapter module.
if __name__ == "blib_hou_mcp" and os.path.isdir(PACKAGE_DIR):
    __path__ = [PACKAGE_DIR]  # type: ignore[name-defined]
    if __spec__ is not None:
        __spec__.submodule_search_locations = [PACKAGE_DIR]

from blib_hou_mcp.server import BridgeMCPAdapter, main  # noqa: E402

__all__ = ["BridgeMCPAdapter", "main"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
