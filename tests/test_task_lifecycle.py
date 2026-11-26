"""Task lifecycle regressions: effects, recovery and cancellation boundaries."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))
from blib_hou_bridge import auth, execution, protocol, tasks


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"BLIB_HOU_RUNTIME_DIR": self.temporary.name})
        self.env.start()
        self.managers = []
        self.executed = []

    def tearDown(self):
        for manager in reversed(self.managers):
            if manager._running is None:
                manager.stop()
        self.env.stop()
        self.temporary.cleanup()

    def manager(self, executor=None, identity=None, max_pending=32):
        def perform(command, payload, context):
            self.executed.append(payload)
            return protocol.ok_response(context.job_id, command, {"value": len(self.executed)})
        manager = tasks.TaskManager(identity or str(uuid.uuid4()), uuid.uuid4().hex,
                                    executor or perform, max_pending)
        self.managers.append(manager)
        return manager

    def submit(self, manager, job_id=None, **kwargs):
        return manager.submit("context", {}, job_id or str(uuid.uuid4()), **kwargs)

    def test_receipt_is_durable_before_execution_and_payload_is_not_persisted(self):
        manager = self.manager()
        marker = "PRIVATE-PYTHON-SOURCE-42"
        job_id = str(uuid.uuid4())
        manager.submit("python_submit", {"job_id": job_id, "code": marker}, job_id)
        record = tasks.local_status(manager.session_id, job_id)
        self.assertEqual(record["status"], "queued")
        self.assertFalse(record["execution_started"])
        self.assertNotIn(marker, manager.store.path.read_bytes().decode("utf-8", errors="ignore"))
        self.assertEqual(self.executed, [])

    def test_duplicate_submission_runs_once_and_different_request_is_rejected(self):
        manager = self.manager()
        record = self.submit(manager)
        self.submit(manager, record["job_id"])
        with self.assertRaises(tasks.TaskError) as caught:
            manager.submit("selected", {}, record["job_id"])
        self.assertEqual(caught.exception.code, "job_conflict")
        manager.drain_one()
        self.submit(manager, record["job_id"])
        self.assertEqual(len(self.executed), 1)

    def test_queue_capacity_is_bounded_without_losing_accepted_jobs(self):
        manager = self.manager(max_pending=2)
        first, second = self.submit(manager), self.submit(manager)
        with self.assertRaises(tasks.TaskError) as caught:
            self.submit(manager)
        self.assertEqual(caught.exception.code, "queue_full")
        self.assertEqual(manager.get(first["job_id"])["queue_position"], 1)
        self.assertEqual(manager.get(second["job_id"])["queue_position"], 2)

    def test_expired_queued_task_never_executes(self):
        manager = self.manager()
        record = self.submit(manager, queue_timeout_ms=1)
        with patch.object(tasks.time, "monotonic", return_value=time.monotonic() + 1):
            self.assertEqual(manager.get(record["job_id"])["status"], "expired")
        manager.drain_one()
        self.assertEqual(self.executed, [])

    def test_queued_cancel_never_executes_and_id_remains_reserved(self):
        manager = self.manager()
        record = self.submit(manager)
        manager.cancel(record["job_id"])
        repeated = self.submit(manager, record["job_id"])
        self.assertEqual(repeated["status"], "cancelled")
        self.assertFalse(repeated["execution_started"])
        manager.drain_one()
        self.assertEqual(self.executed, [])

    def test_cancellation_before_admission_fences_a_late_post(self):
        manager = self.manager()
        job_id = str(uuid.uuid4())
        manager.cancel(job_id)
        self.assertEqual(self.submit(manager, job_id)["status"], "cancelled")
        manager.drain_one()
        self.assertEqual(self.executed, [])

    def test_running_arbitrary_code_is_not_falsely_reported_cancelled(self):
        entered, release = threading.Event(), threading.Event()
        def perform(command, payload, context):
            context.effects_started = True
            entered.set()
            release.wait(2)
            return protocol.ok_response(context.job_id, command, {})
        manager = self.manager(perform)
        record = self.submit(manager)
        worker = threading.Thread(target=manager.drain_one)
        worker.start()
        self.assertTrue(entered.wait(1))
        cancelled = manager.cancel(record["job_id"])
        self.assertEqual(cancelled["status"], "running")
        self.assertTrue(cancelled["cancel_requested"])
        release.set()
        worker.join(3)
        final = manager.get(record["job_id"])
        self.assertEqual(final["status"], "succeeded")
        self.assertTrue(final["execution_started"])

    def test_host_exit_fences_queued_tasks_and_preserves_running_effects(self):
        def perform(command, payload, context):
            context.start_effects()
            with self.assertRaises(tasks.TaskError) as caught:
                manager.stop()
            self.assertEqual(caught.exception.code, "task_running")
            manager.stop(host_exiting=True)
            running = manager.wait(context.job_id, wait_ms=3000)
            self.assertEqual(running["status"], "running")
            self.assertTrue(running["cancel_requested"])
            context.checkpoint()

        manager = self.manager(perform)
        active, queued = self.submit(manager), self.submit(manager)
        manager.drain_one()

        final = manager.get(active["job_id"])
        self.assertEqual(final["status"], "partial")
        self.assertTrue(final["execution_started"])
        unused = manager.get(queued["job_id"])
        self.assertEqual(unused["status"], "cancelled")
        self.assertFalse(unused["execution_started"])
        with self.assertRaises(tasks.TaskError):
            self.submit(manager)

    def test_cooperative_cancel_reports_already_applied_effects(self):
        entered, release = threading.Event(), threading.Event()
        def perform(command, payload, context):
            context.effects_started = True
            context.progress(1, 3, {"created": ["/obj/already_created"]})
            entered.set()
            release.wait(2)
            context.checkpoint()
            raise AssertionError("must stop at checkpoint")
        manager = self.manager(perform)
        record = self.submit(manager)
        worker = threading.Thread(target=manager.drain_one)
        worker.start()
        self.assertTrue(entered.wait(1))
        manager.cancel(record["job_id"])
        release.set()
        worker.join(3)
        final = manager.get(record["job_id"])
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["response"]["result"]["partial_run"]["created"], ["/obj/already_created"])

    def test_host_status_and_cancel_do_not_wait_for_running_scene_work(self):
        entered, release = threading.Event(), threading.Event()
        def perform(command, payload, context):
            entered.set()
            release.wait(2)
            return protocol.ok_response(context.job_id, command, {})
        manager = self.manager(perform)
        record = self.submit(manager)
        worker = threading.Thread(target=manager.drain_one)
        worker.start()
        self.assertTrue(entered.wait(1))
        began = time.perf_counter()
        self.assertEqual(manager.get(record["job_id"])["status"], "running")
        manager.cancel(record["job_id"])
        self.assertLess(time.perf_counter() - began, 0.2)
        with self.assertRaises(tasks.TaskError):
            manager.stop()
        release.set()
        worker.join(3)

    def test_new_generation_does_not_replay_queued_or_running_jobs(self):
        first = self.manager()
        queued = self.submit(first)
        running = self.submit(first)
        record = first.store.get(running["job_id"])
        record.update(status="running", execution_started=True)
        first.store.put(record)
        recovered = self.manager(identity=first.session_id)
        self.assertEqual(recovered.get(queued["job_id"])["status"], "cancelled")
        self.assertEqual(recovered.get(running["job_id"])["status"], "outcome_unknown")
        self.assertEqual(self.submit(recovered, running["job_id"])["status"], "outcome_unknown")
        self.assertEqual(self.executed, [])
        # The simulated lost manager is no longer an active service.
        first._queue.clear()
        first._payloads.clear()

    def test_completed_result_survives_service_restart(self):
        first = self.manager()
        record = self.submit(first)
        first.drain_one()
        first.stop()
        recovered = self.manager(identity=first.session_id)
        result = recovered.get(record["job_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["response"]["result"]["value"], 1)
        self.submit(recovered, record["job_id"])
        self.assertEqual(len(self.executed), 1)

    def test_old_results_expire_without_allowing_duplicate_execution(self):
        manager = self.manager()
        with patch.object(tasks, "MAX_FULL_RESULTS", 1):
            first = self.submit(manager)
            manager.drain_one()
            self.submit(manager)
            manager.drain_one()
        expired = manager.get(first["job_id"])
        self.assertTrue(expired["result_expired"])
        self.assertIsNone(expired["response"])
        self.submit(manager, first["job_id"])
        self.assertEqual(len(self.executed), 2)

    def test_dead_owner_running_receipt_is_unknown_without_attaching_elsewhere(self):
        manager = self.manager()
        item = self.submit(manager)
        record = manager.store.get(item["job_id"])
        record.update(status="running")
        manager.store.put(record)
        with patch.object(auth, "process_probe", return_value=(False, None)):
            evidence = tasks.local_status(manager.session_id, item["job_id"])
        self.assertEqual(evidence["status"], "outcome_unknown")
        self.assertEqual(evidence["source"], "local_journal")
        self.assertEqual(self.executed, [])
        manager._queue.clear()
        manager._payloads.clear()

    def test_guard_rechecks_scene_and_permission_before_effects(self):
        manager = self.manager()
        def guard():
            raise execution.TaskRejected("context_changed", "Scene changed while queued")
        record = self.submit(manager, guard=guard)
        manager.drain_one()
        final = manager.get(record["job_id"])
        self.assertEqual(final["status"], "failed")
        self.assertFalse(final["execution_started"])
        self.assertEqual(final["response"]["error"]["code"], "context_changed")
        self.assertEqual(self.executed, [])

    def test_stop_cancels_pending_jobs_and_preserves_query_evidence(self):
        manager = self.manager()
        record = self.submit(manager)
        manager.stop()
        self.assertEqual(manager.get(record["job_id"])["status"], "cancelled")
        self.assertFalse(manager.drain_one())

    def test_disk_failure_before_running_never_executes(self):
        manager = self.manager()
        record = self.submit(manager)
        with patch.object(manager.store, "put", side_effect=sqlite3.OperationalError("disk unavailable")):
            with self.assertRaises(tasks.TaskError):
                manager.drain_one()
        self.assertTrue(manager.storage_error)
        self.assertEqual(self.executed, [])
        self.assertEqual(manager.get(record["job_id"])["status"], "queued")

    def test_result_commit_failure_keeps_memory_result_and_blocks_new_admission(self):
        manager = self.manager()
        record = self.submit(manager)
        original = manager.store.put
        def write(value):
            if value["status"] == "succeeded":
                raise sqlite3.OperationalError("disk unavailable")
            return original(value)
        with patch.object(manager.store, "put", side_effect=write):
            manager.drain_one()
        self.assertEqual(len(self.executed), 1)
        result = manager.get(record["job_id"])
        self.assertEqual(result["durability"], "memory_only")
        with self.assertRaises(tasks.TaskError):
            self.submit(manager)
        self.submit(manager, record["job_id"])
        self.assertEqual(len(self.executed), 1)

    def test_retention_capacity_preserves_existing_receipts(self):
        manager = self.manager()
        with patch.object(tasks, "MAX_RECORDS", 1):
            record = self.submit(manager)
            manager.drain_one()
            with self.assertRaises(tasks.TaskError) as caught:
                self.submit(manager)
        self.assertEqual(caught.exception.code, "history_full")
        self.assertEqual(manager.get(record["job_id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
