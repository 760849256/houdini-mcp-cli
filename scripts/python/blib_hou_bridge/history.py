"""In-memory request history for the local Houdini bridge."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


MAX_EVENTS = 200

_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_lock = threading.Lock()


def record(event: dict[str, Any]) -> dict[str, Any]:
    item = dict(event)
    item.setdefault("timestamp", time.time())
    if "error_message" in item and item["error_message"] is not None:
        item["error_message"] = str(item["error_message"])[:500]
    with _lock:
        _events.append(item)
    return item


def snapshot(limit: int = 50) -> dict[str, Any]:
    limit = max(0, min(int(limit), MAX_EVENTS))
    with _lock:
        events = list(_events)[-limit:] if limit else []
    return {
        "count": len(events),
        "limit": limit,
        "max_events": MAX_EVENTS,
        "events": events,
    }


def clear() -> None:
    with _lock:
        _events.clear()
