"""Durable task receipts and a bounded single-consumer host queue.

Payloads remain in memory and are never replayed after a service restart.
A running receipt left by a lost generation has an unknown outcome.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import deque, OrderedDict
from copy import deepcopy
from pathlib import Path
from . import auth, execution, history, protocol

TERMINAL = frozenset({"succeeded", "failed", "partial", "cancelled", "expired", "outcome_unknown"})
MAX_RECORDS = 50000
MAX_FULL_RESULTS = 128


class TaskError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskError("bad_request", "job_id must be a UUID") from exc


def database_path(session_id):
    return auth.runtime_dir() / "tasks" / (str(uuid.UUID(session_id)) + ".sqlite3")


class TaskStore:
    def __init__(self, session_id, generation, path=None):
        self.path = Path(path) if path else database_path(session_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, digest TEXT, status TEXT, submitted REAL, record TEXT, has_response INTEGER DEFAULT 0)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "has_response" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN has_response INTEGER DEFAULT 0")
                for job_id, text in db.execute("SELECT job_id,record FROM jobs").fetchall():
                    if json.loads(text).get("response") is not None:
                        db.execute("UPDATE jobs SET has_response=1 WHERE job_id=?", (job_id,))
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_submitted ON jobs(submitted DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_results ON jobs(has_response, submitted DESC)")
            owner = {"session_id": session_id, "generation": generation, "pid": os.getpid(),
                     "process_start": auth.process_probe(os.getpid())[1]}
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('owner', ?)", (json.dumps(owner),))

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=2.0)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def get(self, job_id):
        with self._cache_lock:
            if job_id in self._cache:
                self._cache.move_to_end(job_id)
                return deepcopy(self._cache[job_id])
        with self.db() as db:
            row = db.execute("SELECT record FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        record = json.loads(row[0]) if row else None
        if record is not None:
            self.remember(record)
        return record

    def remember(self, record):
        """Bounded live progress cache; critical transitions still use put()."""
        with self._cache_lock:
            self._cache[record["job_id"]] = deepcopy(record)
            self._cache.move_to_end(record["job_id"])
            while len(self._cache) > 256:
                self._cache.popitem(last=False)

    def put(self, record):
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)",
                       (record["job_id"], record.get("request_sha256"), record["status"],
                        record["submitted_at"], encoded, int(record.get("response") is not None)))
        self.remember(record)

    def count(self):
        with self.db() as db:
            return db.execute("SELECT count(*) FROM jobs").fetchone()[0]

    def list(self, limit=20, active_only=False):
        limit = max(1, min(int(limit), 100))
        with self.db() as db:
            if active_only:
                rows = db.execute("SELECT record FROM jobs WHERE status IN ('queued','running') ORDER BY submitted DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT record FROM jobs ORDER BY submitted DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def unfinished(self):
        with self.db() as db:
            rows = db.execute("SELECT record FROM jobs WHERE status IN ('queued','running')").fetchall()
        return [json.loads(row[0]) for row in rows]

    def compact(self):
        with self.db() as db:
            rows = db.execute("SELECT job_id,record FROM jobs WHERE has_response=1 AND status NOT IN ('queued','running') ORDER BY submitted DESC LIMIT -1 OFFSET ?",
                              (MAX_FULL_RESULTS,)).fetchall()
            for job_id, text in rows:
                record = json.loads(text)
                if record.get("response") is not None:
                    record.update(response=None, result_expired=True)
                    db.execute("UPDATE jobs SET record=?,has_response=0 WHERE job_id=?", (json.dumps(record, separators=(",", ":")), job_id))
        with self._cache_lock:
            for job_id, _ in rows:
                self._cache.pop(job_id, None)

    def checkpoint(self):
        with self.db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def local_status(session_id, job_id):
    """Read historical evidence without attaching to another live Houdini."""
    try:
        path = database_path(session_id)
    except (ValueError, TypeError, AttributeError):
        return None
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
        try:
            row = db.execute("SELECT record FROM jobs WHERE job_id=?", (normalize_id(job_id),)).fetchone()
            owner = db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()
        finally:
            db.close()
        if not row:
            return None
        record = json.loads(row[0])
        metadata = json.loads(owner[0]) if owner else {}
        alive, created = auth.process_probe(metadata.get("pid"))
        lost = alive is False or bool(created and metadata.get("process_start") and created != metadata["process_start"])
        if lost and record["status"] not in TERMINAL:
            record.update(status="outcome_unknown" if record["status"] == "running" else "cancelled",
                          reason="owner_process_exited", response=None,
                          execution_started=record["status"] == "running")
        record["source"] = "local_journal"
        record["connection_unavailable"] = True
        return record
    except (sqlite3.Error, OSError, ValueError):
        return None


class TaskContext:
    def __init__(self, manager, job_id, guard):
        self.manager = manager
        self.job_id = job_id
        self.guard = guard
        self.effects_started = False
        self.cancel_seen = False
        self.partial = None
        self.observations = {}
        self._last_progress_commit = 0.0

    def start_effects(self):
        if self.effects_started:
            return
        with self.manager._lock:
            record = self.manager.store.get(self.job_id)
            record["execution_started"] = True
            self.manager.store.put(record)
        self.effects_started = True

    def checkpoint(self):
        with self.manager._lock:
            record = self.manager.store.get(self.job_id)
            if record.get("cancel_requested"):
                self.cancel_seen = True
                raise execution.TaskCancelled("Cancellation reached a cooperative checkpoint.")
        if self.guard:
            self.guard()

    def progress(self, completed, total, partial=None):
        self.partial = partial
        with self.manager._lock:
            record = self.manager.store.get(self.job_id)
            record["progress"] = {"completed": completed, "total": total}
            record["execution_started"] = self.effects_started
            now = time.monotonic()
            if completed == total or now - self._last_progress_commit >= 0.1:
                self.manager.store.put(record)
                self._last_progress_commit = now
            else:
                self.manager.store.remember(record)


class TaskManager:
    def __init__(self, session_id, generation, executor, max_pending=32, path=None):
        self.session_id, self.generation = session_id, generation
        self.executor = executor
        self.max_pending = max_pending
        self.store = TaskStore(session_id, generation, path)
        self._lock = threading.RLock()
        self._queue = deque()
        self._payloads = {}
        self._running = None
        self._accepting = True
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._worker = None
        self._expiry_worker = None
        self._volatile = {}
        self.storage_error = False
        # Never replay payloads belonging to a lost service generation.
        for old in self.store.unfinished():
            prior = old["status"]
            old.update(status="outcome_unknown" if prior == "running" else "cancelled",
                       reason="service_generation_lost", finished_at=time.time(),
                       execution_started=prior == "running", response=None)
            self.store.put(old)

    def _event(self, event, record):
        history.record({"event": event, "source": "server", "job_id": record["job_id"],
                        "operation_id": record.get("operation_id"), "session_id": self.session_id,
                        "generation": self.generation, "command": record.get("command"),
                        "status": record["status"], "queue_ms": record.get("queue_ms"),
                        "execute_ms": record.get("execute_ms")})

    def submit(self, command, payload, job_id, queue_timeout_ms=15000, guard=None, operation_id=None, request_payload=None):
        job_id = normalize_id(job_id)
        if type(queue_timeout_ms) is not int or not 1 <= queue_timeout_ms <= 120000:
            raise TaskError("bad_request", "queue_timeout_ms must be between 1 and 120000")
        protocol.validate_command(command, payload)
        canonical = json.dumps({"command": command, "payload": payload if request_payload is None else request_payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            old = self._volatile.get(job_id) or self.store.get(job_id)
            if old is not None:
                if old.get("request_sha256") not in (None, digest):
                    raise TaskError("job_conflict", "This job_id already belongs to a different request.")
                return self._view(old)
            if self.storage_error:
                raise TaskError("storage_unavailable", "The task journal is unavailable; no new task was admitted.")
            if not self._accepting:
                raise TaskError("service_stopping", "This service generation is no longer accepting tasks.")
            self._expire_locked()
            if len(self._payloads) >= self.max_pending:
                raise TaskError("queue_full", "The bounded Houdini task queue is full; no task was admitted.")
            if self.store.count() >= MAX_RECORDS:
                raise TaskError("history_full", "Task receipt capacity reached; existing IDs remain reserved.")
            now = time.time()
            record = {"job_id": job_id, "session_id": self.session_id, "generation": self.generation,
                      "operation_id": operation_id or job_id, "command": command, "request_sha256": digest,
                      "status": "queued", "submitted_at": now, "queue_deadline_at": now + queue_timeout_ms / 1000,
                      "execution_started": False, "cancel_requested": False, "response": None}
            # Admission is durable before a task can become visible to the consumer.
            self.store.put(record)
            self._payloads[job_id] = (json.loads(json.dumps(payload)), guard, time.monotonic() + queue_timeout_ms / 1000)
            self._queue.append(job_id)
            self._event("task_accepted", record)
            self._wake.set()
            return self._view(record)

    def _view(self, record):
        result = dict(record)
        result.pop("request_sha256", None)
        if record["status"] == "queued":
            pending = list(self._queue)
            result["queue_position"] = pending.index(record["job_id"]) + 1 if record["job_id"] in pending else None
        result["next_step"] = ("inspect_result_and_effects" if record["status"] in TERMINAL else "query_same_job_id")
        return result

    def get(self, job_id):
        job_id = normalize_id(job_id)
        with self._lock:
            self._expire_locked()
            record = self._volatile.get(job_id) or self.store.get(job_id)
            return self._view(record) if record else {"job_id": job_id, "session_id": self.session_id, "status": "not_found"}

    def list(self, limit=20, active_only=False):
        with self._lock:
            self._expire_locked()
            records = []
            for record in self.store.list(limit, active_only):
                view = self._view(record)
                view["result_available"] = view.pop("response", None) is not None
                records.append(view)
            return records

    def cancel(self, job_id):
        job_id = normalize_id(job_id)
        with self._lock:
            record = self.store.get(job_id)
            if record is None:
                if self.store.count() >= MAX_RECORDS:
                    raise TaskError("history_full", "Cannot reserve an additional cancellation receipt.")
                # A cancellation can arrive before the original POST. Reserve its UUID.
                record = {"job_id": job_id, "session_id": self.session_id, "generation": self.generation,
                          "status": "cancelled", "submitted_at": time.time(), "finished_at": time.time(),
                          "execution_started": False, "request_sha256": None, "cancel_requested": True,
                          "reason": "cancelled_before_admission", "response": None}
            elif record["status"] in TERMINAL:
                return self._view(record)
            else:
                record["cancel_requested"] = True
                if record["status"] == "queued":
                    record.update(status="cancelled", finished_at=time.time(), reason="cancelled_while_queued")
                    self._payloads.pop(job_id, None)
                    self._queue = deque(item for item in self._queue if item != job_id)
            self.store.put(record)
            self._event("task_cancel_requested", record)
            return self._view(record)

    def _expire_locked(self):
        now = time.monotonic()
        for job_id in list(self._queue):
            pending = self._payloads.get(job_id)
            if pending is None:
                self._queue.remove(job_id)
            elif now >= pending[2]:
                record = self.store.get(job_id)
                record.update(status="expired", finished_at=time.time(), reason="queue_deadline_exceeded")
                self.store.put(record)
                self._payloads.pop(job_id, None)
                self._queue.remove(job_id)
                self._event("task_expired", record)

    def drain_one(self):
        with self._lock:
            self._expire_locked()
            if self._running is not None or not self._accepting or not self._queue:
                return False
            job_id = self._queue[0]
            payload, guard, deadline = self._payloads[job_id]
            record = self.store.get(job_id)
            if time.monotonic() >= deadline:
                self._expire_locked()
                return False
            self._running = job_id
            record.update(status="running", started_at=time.time(),
                          queue_ms=round((time.time() - record["submitted_at"]) * 1000, 3))
            try:
                self.store.put(record)
            except (sqlite3.Error, OSError):
                self._running = None
                self.storage_error = True
                raise TaskError("storage_unavailable", "The running receipt could not be committed; execution was not started.")
            self._queue.popleft()
        context = TaskContext(self, job_id, guard)
        token = execution._current.set(context)
        began = time.perf_counter()
        response = None
        status, reason = "failed", None
        try:
            context.checkpoint()
            result = self.executor(record["command"], payload, context)
            response = result
            status = "succeeded" if response.get("ok") else ("partial" if context.effects_started else "failed")
        except execution.TaskCancelled as exc:
            status = "partial" if context.effects_started else "cancelled"
            reason = "cooperative_cancel"
            response = protocol.error_response(job_id, record["command"], str(exc), "cancelled")
            response["result"] = {"partial_run": context.partial, "execution_started": context.effects_started}
        except execution.TaskRejected as exc:
            status = "partial" if context.effects_started else "failed"
            reason = exc.code
            response = protocol.error_response(job_id, record["command"], str(exc), exc.code)
        except BaseException as exc:
            # An exception says nothing about already-applied scene effects.
            response = protocol.error_response(job_id, record["command"], str(exc), "task_failed")
            status = "partial" if context.effects_started else "failed"
        finally:
            execution._current.reset(token)
            with self._lock:
                try:
                    latest = self._volatile.get(job_id) or self.store.get(job_id)
                except (sqlite3.Error, OSError):
                    latest = dict(record)
                    self.storage_error = True
                try:
                    json.dumps(response)
                except (TypeError, ValueError):
                    status = "partial" if context.effects_started else "failed"
                    reason = "result_serialization_failed"
                    response = protocol.error_response(job_id, record["command"],
                        "The result could not be serialized. Inspect effects before any new write.", reason)
                latest.update(status=status, finished_at=time.time(), response=response, reason=reason,
                              execution_started=context.effects_started,
                              execute_ms=round((time.perf_counter() - began) * 1000, 3))
                try:
                    self.store.put(latest)
                    self._event("task_finished", latest)
                except (sqlite3.Error, OSError, TypeError, ValueError):
                    latest["durability"] = "memory_only"
                    self._volatile[job_id] = latest
                    self.storage_error = True
                finally:
                    self._running = None
                    self._payloads.pop(job_id, None)
                    self._wake.set()
                if not self.storage_error:
                    try:
                        self.store.compact()
                    except (sqlite3.Error, OSError):
                        self._event("task_retention_deferred", latest)
        return True

    def wait(self, job_id, wait_ms=0):
        deadline = time.monotonic() + max(0, min(wait_ms, 3000)) / 1000
        while True:
            record = self.get(job_id)
            if record["status"] in TERMINAL or time.monotonic() >= deadline:
                return record
            time.sleep(0.02)

    def start(self, background_consumer=False):
        def expire_loop():
            while not self._closed.wait(0.1):
                try:
                    with self._lock:
                        self._expire_locked()
                except (sqlite3.Error, OSError):
                    self.storage_error = True
        self._expiry_worker = threading.Thread(target=expire_loop, name="BlibTaskExpiry", daemon=True)
        self._expiry_worker.start()
        if background_consumer:
            def consume():
                while not self._closed.is_set():
                    self._wake.wait(0.1)
                    self._wake.clear()
                    try:
                        self.drain_one()
                    except (TaskError, sqlite3.Error, OSError):
                        self.storage_error = True
            self._worker = threading.Thread(target=consume, name="BlibTaskConsumer", daemon=True)
            self._worker.start()

    def stop(self):
        with self._lock:
            if self._running is not None:
                raise TaskError("task_running", "A host task is running. Stop/reload is deferred; cancel cooperatively or wait for it.")
            self._accepting = False
            for job_id in list(self._queue):
                self.cancel(job_id)
            self._closed.set()
            self._wake.set()
        for worker in (self._worker, self._expiry_worker):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=1)
        try:
            self.store.checkpoint()
        except (sqlite3.Error, OSError):
            self.storage_error = True

def task_response(record):
    """Expose a pending receipt or a terminal result without claiming no effects."""
    metadata = {k: v for k, v in record.items() if k not in {"response", "request_sha256"}}
    if record["status"] in TERMINAL:
        if record.get("response") is not None:
            response = json.loads(json.dumps(record["response"]))
        else:
            response = protocol.error_response(record["job_id"], record.get("command", ""),
                "Task " + record["status"] + "; inspect its evidence before submitting another write.",
                record["status"])
            response["result"] = dict(metadata)
        response["task"] = metadata
        return response
    response = protocol.ok_response(record["job_id"], record.get("command", ""), metadata)
    response["pending"] = True
    response["task"] = metadata
    return response
