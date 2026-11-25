"""Execute API-supplied Python inside the active Houdini process."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from copy import deepcopy
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from typing import Any

from . import execution, state

_MAX_JOBS = 4096
_MAX_FULL_RESULTS = 128
_MAX_OUTPUT = 65536
_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.RLock()


def submit(code: str, job_id: str, hou: Any, inputs=None) -> dict[str, Any]:
    try:
        job_id = str(uuid.UUID(job_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("job_id must be a UUID") from exc
    if not isinstance(code, str) or not code.strip() or len(code) > 65536:
        raise ValueError("code must be a non-empty string of at most 65536 characters")
    inputs = deepcopy(inputs or {})
    signature = json.dumps([code, inputs], sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    with _lock:
        old = _jobs.get(job_id)
        if old is not None:
            if old["code_sha256"] != digest:
                raise ValueError("job_id already exists with different code or inputs")
            return dict(old)
        if not state.edit_enabled() or not state.python_enabled():
            raise PermissionError("Switch out of read-only mode before submitting Python jobs")
        if any(j["status"] in ("queued", "running") for j in _jobs.values()):
            raise RuntimeError("A Python job is already queued or running")
        if len(_jobs) >= _MAX_JOBS:
            raise RuntimeError("Python job history is full; restart Houdini to reset job history")
        job = {"job_id": job_id, "code_sha256": digest, "status": "queued", "submitted_at": time.time()}
        _jobs[job_id] = job
        job["permission_epoch"] = state.permission_epoch()
        worker = threading.Thread(target=_run, args=(job, code, hou, inputs), name="BlibHouPython", daemon=True)
        try:
            worker.start()
        except Exception:
            _jobs.pop(job_id, None)
            raise
        return dict(job)


def status(job_id: str) -> dict[str, Any]:
    try:
        job_id = str(uuid.UUID(job_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("job_id must be a UUID") from exc
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else {"job_id": job_id, "status": "not_found"}


def _compact_results() -> None:
    """Keep job IDs/statuses for deduplication, but bound stored output memory.

    Caller holds _lock. IDs are never evicted during this Houdini process.
    """
    completed = [job for job in _jobs.values() if job.get("status") in {"succeeded", "failed", "cancelled"}]
    completed.sort(key=lambda job: job.get("finished_at", 0), reverse=True)
    for job in completed[_MAX_FULL_RESULTS:]:
        if not job.get("output_expired"):
            job.pop("stdout", None)
            job.pop("stderr", None)
            job.pop("error", None)
            job["output_expired"] = True


class _BoundedOutput(io.TextIOBase):
    def __init__(self):
        self._value = ""

    def write(self, value: str) -> int:
        if not isinstance(value, str):
            raise TypeError("write() argument must be str")
        self._value = (self._value + value[-_MAX_OUTPUT:])[-_MAX_OUTPUT:]
        return len(value)

    def getvalue(self) -> str:
        return self._value


def _run(job: dict[str, Any], code: str, hou: Any, inputs) -> None:
    def perform() -> None:
        with _lock:
            if not state.edit_enabled() or not state.python_enabled() or job.get("permission_epoch") != state.permission_epoch():
                job.update(status="cancelled", finished_at=time.time(), error="Python or edit mode disabled before execution")
                _compact_results()
                return
            job.update(status="running", started_at=time.time())
        out, err = _BoundedOutput(), _BoundedOutput()
        namespace = {"hou": hou, "__name__": "__blib_houdini_mcp__", "bridge_inputs": inputs}
        failure = None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compile(code, "<blib-houdini-python>", "exec"), namespace, namespace)
        except BaseException:
            failure = traceback.format_exc(limit=20)
        with _lock:
            job.update(status="failed" if failure else "succeeded", finished_at=time.time(),
                       stdout=out.getvalue()[-_MAX_OUTPUT:], stderr=err.getvalue()[-_MAX_OUTPUT:],
                       **(_structured_output(namespace) if not failure else {}))
            if failure:
                job["error"] = failure[-_MAX_OUTPUT:]
            _compact_results()
    try:
        try:
            import hdefereval  # type: ignore
        except ImportError:
            perform()
        else:
            hdefereval.executeInMainThreadWithResult(perform)
    except BaseException:
        with _lock:
            job.update(status="failed", finished_at=time.time(), error=traceback.format_exc(limit=20)[-_MAX_OUTPUT:])
            _compact_results()

def active():
    with _lock:
        return [dict(job) for job in _jobs.values() if job["status"] in {"queued", "running"}]


def _structured_output(namespace):
    if "bridge_result" not in namespace:
        return {}
    try:
        serialized = json.dumps(namespace["bridge_result"], ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > 32768:
            return {"data_error": "bridge_result exceeds 32 KiB."}
        return {"data": json.loads(serialized)}
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return {"data_error": "bridge_result must be valid JSON."}


def execute_inline(code, hou, context, inputs=None):
    """Run in the scheduler's host thread; no detached Python worker is created."""
    if not state.edit_enabled() or not state.python_enabled():
        raise PermissionError("Switch out of read-only mode before Python execution.")
    compiled = compile(code, "<blib-houdini-python>", "exec")
    context.checkpoint()
    out, err = _BoundedOutput(), _BoundedOutput()
    namespace = {"hou": hou, "__name__": "__blib_houdini_mcp__",
                 "bridge_checkpoint": context.checkpoint, "bridge_inputs": deepcopy(inputs or {})}
    context.start_effects()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compiled, namespace, namespace)
    except execution.TaskCancelled:
        context.partial = {"stdout": out.getvalue(), "stderr": err.getvalue()}
        raise
    except BaseException:
        return {"ok": False, "result": {"status": "failed", "stdout": out.getvalue(),
                                       "stderr": err.getvalue(), "error": traceback.format_exc(limit=20)[-_MAX_OUTPUT:]},
                "error": {"code": "python_failed", "message": "Python raised an exception; inspect output and possible scene effects."}}
    return {"ok": True, "result": {"status": "succeeded", "stdout": out.getvalue(),
                                   "stderr": err.getvalue(), **_structured_output(namespace)}, "error": None}
