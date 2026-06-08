"""Runtime state for the local Houdini bridge."""

from __future__ import annotations


_edit_enabled = False


def edit_enabled() -> bool:
    return bool(_edit_enabled)


def set_edit_enabled(enabled: bool) -> bool:
    global _edit_enabled
    _edit_enabled = bool(enabled)
    return _edit_enabled
