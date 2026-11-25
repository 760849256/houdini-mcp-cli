"""Bounded memory history plus rotating, allow-listed local diagnostic events."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from . import auth

MAX_EVENTS = 200
MAX_LOG_BYTES = 512 * 1024
LOG_BACKUPS = 2
_events = deque(maxlen=MAX_EVENTS)
_lock = threading.RLock()
_FIELDS = frozenset({"event", "source", "timestamp", "trace_id", "request_id", "operation_id", "job_id",
                     "session_id", "generation", "command", "ok", "status", "error_code",
                     "duration_ms", "discovery_ms", "transport_ms", "queue_ms", "execute_ms",
                     "serialize_ms", "delivery", "main_thread_status", "reason"})


def _safe(event):
    result = {}
    for key, value in event.items():
        if key not in _FIELDS:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            result[key] = value
        elif key == "command":
            # Unknown commands may contain arbitrary submitted text.
            from . import protocol
            result[key] = value if value in protocol.READ_COMMANDS | protocol.EDIT_COMMANDS else "<invalid>"
        elif isinstance(value, str):
            result[key] = value[:128]
    return result


def _log_path(item):
    source = "server" if item.get("source") == "server" else "client"
    identity = item.get("generation") or str(os.getpid())
    identity = "".join(c for c in str(identity) if c.isalnum())[:40] or str(os.getpid())
    return auth.runtime_dir() / "logs" / ("%s-%s.jsonl" % (source, identity))


def record(event: dict[str, Any]) -> dict[str, Any]:
    item = dict(event)
    item.setdefault("timestamp", time.time())
    if item.get("error_message") is not None:
        item["error_message"] = str(item["error_message"])[:500]
    with _lock:
        _events.append(item)
        try:
            safe = _safe(item)
            path = _log_path(safe)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
                for index in range(LOG_BACKUPS, 0, -1):
                    previous = path if index == 1 else path.with_suffix(".jsonl.%s" % (index - 1))
                    if previous.exists():
                        os.replace(previous, path.with_suffix(".jsonl.%s" % index))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, ValueError):
            # A diagnostic disk error must never turn a successful edit into failure.
            pass
    return item


def snapshot(limit=50):
    limit = max(0, min(int(limit), MAX_EVENTS))
    with _lock:
        events = list(_events)[-limit:] if limit else []
    return {"count": len(events), "limit": limit, "max_events": MAX_EVENTS, "events": events}


def persisted_snapshot(limit=100):
    limit = max(0, min(int(limit), 500))
    events = []
    # Bound both file count and bytes read, including when a workstation has many old runs.
    candidates = []
    for path in (auth.runtime_dir() / "logs").glob("*.jsonl*"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    paths = [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:8]]
    for path in paths:
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 128 * 1024))
                lines = handle.read(128 * 1024).decode("utf-8", errors="replace").splitlines()
            for line in lines:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        events.append(_safe(value))
                except ValueError:
                    continue
        except OSError:
            continue
    events.sort(key=lambda item: item.get("timestamp", 0))
    return {"events": events[-limit:] if limit else [], "limit": limit,
            "persistence": "local_allowlist", "error_messages_omitted": True}


def clear():
    with _lock:
        _events.clear()
