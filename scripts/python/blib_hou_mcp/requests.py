"""Cancellation state for one MCP request, independent of host job lifetime."""
from __future__ import annotations
import threading


class RequestControl:
    def __init__(self):
        self.stop_waiting = threading.Event()
        self.cancel_host = threading.Event()
        self._lock = threading.Lock()
        self._jobs = {}

    def add_job(self, connection, session, job_id):
        key = (session.get("session_id"), job_id)
        with self._lock:
            self._jobs[key] = (connection, dict(session), job_id)

    def handles(self):
        with self._lock:
            return [{"session_id": s.get("session_id"), "generation": s.get("generation"), "job_id": job_id}
                    for _, s, job_id in self._jobs.values()]

    def cancel(self):
        self.cancel_host.set()
        self.stop_waiting.set()
        with self._lock:
            jobs = list(self._jobs.values())
        for connection, session, job_id in jobs:
            connection.cancel_task(job_id, session)

    def detach(self):
        # Client loss ends local waiting; accepted host jobs continue.
        self.stop_waiting.set()
