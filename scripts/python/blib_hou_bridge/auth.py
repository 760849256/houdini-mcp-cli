"""Local credentials and an atomically updated per-instance registry."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

APP_NAME = "blib_hou_bridge"
SESSION_FILENAME = "session.json"
LEASE_SECONDS = 15.0


def runtime_dir() -> Path:
    configured = os.environ.get("BLIB_HOU_RUNTIME_DIR")
    return Path(configured) if configured else Path(tempfile.gettempdir()) / APP_NAME


def default_session_path() -> Path:
    return runtime_dir() / SESSION_FILENAME


def generate_token() -> str:
    return secrets.token_urlsafe(32)


_CONTROL_CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_control_code() -> str:
    return "".join(secrets.choice(_CONTROL_CODE_CHARS) for _ in range(8))


def normalize_control_code(value: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}", value):
        raise ValueError("Control codes need 3–32 characters: letters, digits, underscores or hyphens, starting with a letter or digit.")
    return value.upper()


@contextlib.contextmanager
def control_code_lock():
    """Serialize code assignment across Houdini processes in this registry."""
    path = runtime_dir() / "control-codes.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def control_code_in_use(code: str, session_id: str) -> bool:
    return any(record.get("control_code") == code
               and session_key(record) != session_id
               and record["availability"] not in {"stopped", "process_exited", "process_reused"}
               for record in discover_sessions())


def process_probe(pid: int) -> tuple[bool | None, str | None]:
    """Check creation identity as well as liveness; access denied is unknown."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False, None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return (None if ctypes.get_last_error() == 5 else False), None
        try:
            code = wintypes.DWORD()
            alive = None
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                alive = code.value == 259
            times = [wintypes.FILETIME() for _ in range(4)]
            created = None
            if kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            return alive, created
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return None, None
    try:
        fields = Path("/proc/%s/stat" % pid).read_text().rsplit(")", 1)[1].split()
        return True, fields[19]
    except (OSError, IndexError):
        return True, None


def instance_id(pid: int | None = None) -> str:
    pid = os.getpid() if pid is None else pid
    _, created = process_probe(pid)
    # The process start identity makes PID reuse a different instance.
    return str(uuid.uuid5(uuid.NAMESPACE_OID, "blib:%s:%s" % (pid, created or _FALLBACK_START)))


_FALLBACK_START = time.time_ns()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/other atomic writers can briefly hold the destination.
                if attempt == 7:
                    raise
                time.sleep(min(0.005 * (2 ** attempt), 0.08))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def load_session(path: str | os.PathLike | None = None) -> dict[str, Any] | None:
    """Read a descriptor only. Clients must use discover_sessions to validate it."""
    session_path = Path(path) if path else default_session_path()
    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    host, port, token = payload.get("host"), payload.get("port"), payload.get("token")
    if host != "127.0.0.1" or type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not token:
        return None
    if "registry_version" in payload:
        try:
            uuid.UUID(payload["session_id"])
            if not isinstance(payload["generation"], str) or not payload["generation"]:
                return None
        except (KeyError, ValueError, TypeError, AttributeError):
            return None
    return payload


def session_key(session: dict[str, Any]) -> str:
    return str(session.get("session_id") or "legacy-" + hashlib.sha256(
        ("%s:%s:%s" % (session.get("pid"), session.get("started_at"), session.get("token"))).encode()
    ).hexdigest()[:24])


def registry_path(session_id: str) -> Path:
    return runtime_dir() / "instances" / (str(uuid.UUID(session_id)) + ".json")


def save_session(host: str, port: int, token: str, path=None, pid=None,
                 session_id=None, generation=None, metadata=None) -> Path:
    pid = os.getpid() if pid is None else int(pid)
    _, created = process_probe(pid)
    payload = {
        "host": host, "port": int(port), "token": token, "pid": pid,
        "started_at": time.time(), "process_start": created,
        "session_id": session_id or instance_id(pid),
        "generation": generation or uuid.uuid4().hex,
        "registry_version": 1, "heartbeat_at": time.time(),
        "lease_seconds": LEASE_SECONDS, "state": "running",
    }
    payload.update({k: v for k, v in (metadata or {}).items() if k in {"version", "hip_name", "houdini_version", "control_code"}})
    target = Path(path) if path else registry_path(payload["session_id"])
    atomic_json(target, payload)
    if path is None:
        # Full descriptor remains readable by older CLI clients.
        atomic_json(default_session_path(), payload)
    return target


def refresh_session(path: Path, generation: str, metadata=None) -> bool:
    payload = load_session(path)
    if not payload or payload.get("generation") != generation or payload.get("state") != "running":
        return False
    payload["heartbeat_at"] = time.time()
    payload.update({k: v for k, v in (metadata or {}).items() if k in {"hip_name", "houdini_version", "control_code"}})
    atomic_json(Path(path), payload)
    return True


def retire_session(path: Path, generation: str) -> None:
    payload = load_session(path)
    if not payload or payload.get("generation") != generation:
        return
    payload.update(state="stopped", heartbeat_at=time.time())
    atomic_json(Path(path), payload)
    pointer = load_session()
    if pointer and session_key(pointer) == session_key(payload) and pointer.get("generation") == generation:
        # A tombstone is safer than unlinking a pointer another instance may update.
        atomic_json(default_session_path(), payload)


def availability(session: dict[str, Any], now: float | None = None) -> str:
    if session.get("state", "running") != "running":
        return "stopped"
    alive, created = process_probe(session.get("pid"))
    if alive is False:
        return "process_exited"
    expected = session.get("process_start")
    if expected and created and str(expected) != created:
        return "process_reused"
    if "heartbeat_at" in session:
        try:
            age = (time.time() if now is None else now) - float(session["heartbeat_at"])
            if age < -5 or age > min(60.0, max(1.0, float(session.get("lease_seconds", LEASE_SECONDS)))):
                return "lease_expired"
        except (ValueError, TypeError):
            return "invalid_lease"
    return "available"


def discover_sessions(path=None) -> list[dict[str, Any]]:
    if path:
        paths = [Path(path)]
    else:
        paths = sorted((runtime_dir() / "instances").glob("*.json"))
        paths.append(default_session_path())
    found = {}
    for candidate in paths:
        payload = load_session(candidate)
        if payload is None:
            continue
        key = session_key(payload)
        # Registry entries take precedence over the compatibility pointer.
        if key in found:
            continue
        found[key] = dict(payload, descriptor_path=str(candidate), availability=availability(payload))
    return list(found.values())


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    allowed = {"host", "port", "pid", "started_at", "process_start", "session_id", "generation",
               "version", "hip_name", "houdini_version", "state", "heartbeat_at", "lease_seconds", "availability",
               "descriptor_path", "registry_version", "control_code"}
    result = {k: v for k, v in session.items() if k in allowed}
    result["session_id"] = session_key(session)
    result["has_token"] = bool(session.get("token"))
    return result


def clear_session(path=None) -> bool:
    try:
        (Path(path) if path else default_session_path()).unlink()
        return True
    except FileNotFoundError:
        return False
