"""Runtime state for the local Houdini bridge."""

from __future__ import annotations
import threading
import uuid


_access_lock = threading.RLock()
_access = (False, False, uuid.uuid4().hex)
_scene_epoch = uuid.uuid4().hex
_scene_event_registration = globals().get("_scene_event_registration")
_scene_event_types = ()


def access_snapshot() -> tuple[bool, bool, str]:
    return _access


def permission_epoch():
    return _access[2]


def scene_epoch() -> str:
    return _scene_epoch


def _scene_event(event_type, **kwargs):
    global _scene_epoch
    # Houdini can supply old/new HIP paths as additional keyword arguments.
    # Compare actual enum values; their display representation is not an API.
    if event_type in _scene_event_types or str(event_type).split(".")[-1].rstrip(" >'\"") in {
            "BeforeLoad", "AfterLoad", "BeforeClear", "AfterClear", "BeforeMerge", "AfterMerge"}:
        _scene_epoch = uuid.uuid4().hex


def ensure_scene_events(hou):
    global _scene_event_registration, _scene_event_types
    hip = hou.hipFile
    callbacks = getattr(hip, "eventCallbacks", None)
    registration = _scene_event_registration
    if registration and registration[0] is hip and (callbacks is None or registration[1] in callbacks()):
        return
    release_scene_events()
    enumeration = getattr(hou, "hipFileEventType", None)
    _scene_event_types = tuple(getattr(enumeration, name) for name in (
        "BeforeLoad", "AfterLoad", "BeforeClear", "AfterClear", "BeforeMerge", "AfterMerge")
        if hasattr(enumeration, name))
    callback = getattr(hip, "addEventCallback", None)
    if callback is not None:
        callback(_scene_event)
        _scene_event_registration = (hip, _scene_event)


def release_scene_events():
    global _scene_event_registration
    if _scene_event_registration is None:
        return
    hip, callback = _scene_event_registration
    remove = getattr(hip, "removeEventCallback", None)
    callbacks = getattr(hip, "eventCallbacks", None)
    if remove is not None and (callbacks is None or callback in callbacks()):
        remove(callback)
    _scene_event_registration = None


def _set_access(edit: bool, python: bool) -> None:
    global _access
    if _access[:2] != (edit, python):
        _access = (edit, python, uuid.uuid4().hex)


def set_read_only(read_only: bool) -> bool:
    """Change scene edits and Python together for the public Bridge control."""
    enabled = not read_only
    with _access_lock:
        _set_access(enabled, enabled)
    return read_only


def edit_enabled() -> bool:
    return _access[0]


def python_enabled() -> bool:
    return _access[1]


def set_edit_enabled(enabled: bool) -> bool:
    """Compatibility for callers that configure the legacy internal gates."""
    with _access_lock:
        _set_access(bool(enabled), bool(enabled) and _access[1])
        return _access[0]


def set_python_enabled(enabled: bool) -> bool:
    with _access_lock:
        _set_access(_access[0], bool(enabled) and _access[0])
        return _access[1]
