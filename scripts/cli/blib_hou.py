"""Compatibility script entrypoint for the Blib Houdini Bridge CLI."""

from __future__ import annotations

import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON_DIR = os.path.join(ROOT, "scripts", "python")


def _prepend_sys_path(path: str) -> None:
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


_prepend_sys_path(PYTHON_DIR)

from blib_hou import main  # noqa: E402

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
