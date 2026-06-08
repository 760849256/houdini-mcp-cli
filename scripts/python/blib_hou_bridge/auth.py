"""Session token helpers for the local-only Houdini bridge."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any


APP_NAME = "blib_hou_bridge"
SESSION_FILENAME = "session.json"


def default_session_path() -> Path:
    return Path(tempfile.gettempdir()) / APP_NAME / SESSION_FILENAME


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def save_session(
    host: str,
    port: int,
    token: str,
    path: str | os.PathLike | None = None,
    pid: int | None = None,
) -> Path:
    session_path = Path(path) if path else default_session_path()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "host": host,
        "port": int(port),
        "token": token,
        "started_at": time.time(),
    }
    if pid is not None:
        payload["pid"] = int(pid)
    tmp_path = session_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, session_path)
    return session_path


def load_session(path: str | os.PathLike | None = None) -> dict[str, Any] | None:
    session_path = Path(path) if path else default_session_path()
    if not session_path.exists():
        return None
    try:
        with open(session_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    host = payload.get("host")
    port = payload.get("port")
    token = payload.get("token")
    if host != "127.0.0.1" or not isinstance(port, int) or not isinstance(token, str):
        return None
    return payload


def clear_session(path: str | os.PathLike | None = None) -> bool:
    session_path = Path(path) if path else default_session_path()
    try:
        session_path.unlink()
        return True
    except FileNotFoundError:
        return False
