from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from arthur_loop.browser_lock import acquire_lock, read_lock
from arthur_loop.cli import main
from arthur_loop.queue_ledger import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    VALID_STATUSES,
    IllegalTransition,
    QueueJob,
    QueueLedger,
    read_jsonl,
)
from arthur_loop.recovery import recover_job
from arthur_loop.tick import classify_tick


UTC = timezone.utc
T0 = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _job(job_id: str = "BQ-SM-001", **extra) -> QueueJob:
    return QueueJob(
        job_id=job_id,
        project_id="SM",
        target_chat_title="SM planning",
        target_chat_url="manual",
        **extra,
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class StateMachineTests(unittest.TestCase):
    def test_every_status_has_an_entry_and_terminal_states_are_dead_ends(self) -> None:
        self.assertEqual(set(ALLOWED_TRANSITIONS), VALID_STATUSES)
        for status in TERMINAL_STATUSES:
            self.assertEqual(ALLOWED_TRANSITIONS[status], set(), status)
        for targets in ALLOWED_TRANSITIONS.values():
            self.assertTrue(targets <= VALID_STATUSES)

    def test_completing_work_that_was_never_submitted_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job())

            for target in ("completed", "completed_with_warnings", "waiting_for_chatgpt", "stopped_no_output"):
                with self.assertRaises(IllegalTransition, msg=target):
                    ledger.transition("BQ-SM-001", target, now=T0)

            # nothing was written by the refused attempts
            self.assertEqual(ledger.latest_jobs()["BQ-SM-001"].status, "queued")
            self.assertEqual(
                [event["event_type"] for event in read_jsonl(Path(tmp) / "queue/events.jsonl")],
                ["job_snapshot"],
            )

    def test_finished_jobs_never_move_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job())
            ledger.transition("BQ-SM-001", "claimed", now=T0, holder="m1")
            ledger.transition("BQ-SM-001", "submitted", now=T0)
            ledger.transition("BQ-SM-001", "completed", now=T0)

            for target in ("claimed", "submitted", "queued", "needs_recovery", "failed"):
                with self.assertRaises(IllegalTransition, msg=target) as ctx:
                    ledger.transition("BQ-SM-001", target, now=T0)
                self.assertIn("terminal", str(ctx.exception))

    def test_poll_result_on_an_unsubmitted_job_leaves_no_orphan_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job())

            with self.assertRaises(IllegalTransition):
                ledger.record_poll_result("BQ-SM-001", marker_found=True, status="completed", now=T0)

            events = [event["event_type"] for event in read_jsonl(Path(tmp) / "queue/events.jsonl")]
            self.assertNotIn("first_poll_result", events)

    def test_retry_after_silent_stop_and_reclaim_are_legal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job())
            ledger.transition("BQ-SM-001", "claimed", now=T0, holder="m1")
            ledger.transition("BQ-SM-001", "claimed", now=T0, holder="m1")  # crash-retry of claim
            ledger.transition("BQ-SM-001", "submitted", now=T0)
            ledger.transition("BQ-SM-001", "stopped_no_output", now=T0)
            retried = ledger.transition("BQ-SM-001", "submitted", now=T0)

            self.assertEqual(retried.attempt_count, 2)
            self.assertEqual(retried.claimed_by, "m1")

    def test_cli_refuses_illegal_moves_with_exit_2_and_a_readable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = ["queue", "--root", tmp]
            code, _, _ = _run(base + ["create", "--job-id", "BQ-SM-001", "--project-id", "SM",
                                     "--target-chat-title", "t", "--target-chat-url", "manual"])
            self.assertEqual(code, 0)

            code, _, err = _run(base + ["complete", "--job-id", "BQ-SM-001"])
            self.assertEqual(code, 2)
            self.assertIn("illegal queue transition", err)
            self.assertIn("queued -> completed", err)

            for step in (["claim"], ["submit"], ["complete"]):
                code, _, err = _run(base + step + ["--job-id", "BQ-SM-001"])
                self.assertEqual(code, 0, err)

            code, _, err = _run(base + ["claim", "--job-id", "BQ-SM-001"])
            self.assertEqual(code, 2)
            self.assertIn("completed -> claimed", err)
            # the refused claim must not have taken the browser lease
            self.assertIsNone(read_lock(Path(tmp)))

    def test_cancel_is_a_first_class_terminal_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = ["queue", "--root", tmp]
            _run(base + ["create", "--job-id", "BQ-SM-001", "--project-id", "SM",
                         "--target-chat-title", "t", "--target-chat-url", "manual"])
            code, out, _ = _run(base + ["cancel", "--job-id", "BQ-SM-001", "--reason", "superseded"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["status"], "cancelled")
            code, _, _ = _run(base + ["claim", "--job-id", "BQ-SM-001"])
            self.assertEqual(code, 2)


class IdempotencyTests(unittest.TestCase):
    def test_reused_idempotency_key_on_a_new_job_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job("BQ-SM-001", idempotency_key="sm-plan-1"))

            with self.assertRaises(ValueError) as ctx:
                ledger.create_job(_job("BQ-SM-002", idempotency_key="sm-plan-1"))
            self.assertIn("sm-plan-1", str(ctx.exception))
            self.assertIn("BQ-SM-001", str(ctx.exception))
            self.assertEqual(list(ledger.latest_jobs()), ["BQ-SM-001"])

    def test_force_requeues_the_same_job_but_never_forks_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job("BQ-SM-001", idempotency_key="k"))
            ledger.transition("BQ-SM-001", "failed", now=T0, error="boom")

            requeued = ledger.create_job(_job("BQ-SM-001", idempotency_key="k"), force=True)
            self.assertEqual(requeued.status, "queued")

            with self.assertRaises(ValueError):
                ledger.create_job(_job("BQ-SM-002", idempotency_key="k"), force=True)

    def test_cli_duplicate_key_exits_2_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            create = ["queue", "--root", tmp, "create", "--project-id", "SM",
                      "--target-chat-title", "t", "--target-chat-url", "manual", "--idempotency-key", "K1"]
            self.assertEqual(_run(create + ["--job-id", "BQ-SM-001"])[0], 0)

            code, out, err = _run(create + ["--job-id", "BQ-SM-002"])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("already used by BQ-SM-001", err)

            code, out, _ = _run(["queue", "--root", tmp, "show"])
            self.assertEqual(list(json.loads(out)), ["BQ-SM-001"])


class CrashRecoveryTests(unittest.TestCase):
    def test_recover_releases_the_lease_of_the_manager_that_claimed_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-SM-001"))
            ledger.create_job(_job("BQ-SM-002"))
            # manager claims 001 and is SIGKILLed: lease stays fresh, job stays claimed
            acquire_lock(root, "browser-queue-manager", now=T0)
            ledger.transition("BQ-SM-001", "claimed", now=T0, holder="browser-queue-manager")
            blocked = classify_tick(root, now=datetime(2026, 7, 2, 12, 1, tzinfo=UTC))
            self.assertEqual(blocked.status, "BLOCKED_BY_BROWSER_LOCK")

            result = recover_job(root, "BQ-SM-001", requeue=True, error="manager died", now=datetime(2026, 7, 2, 12, 2, tzinfo=UTC))

            self.assertEqual(result.job.status, "queued")
            self.assertEqual(result.released_lock.holder, "browser-queue-manager")
            self.assertIsNone(read_lock(root))
            after = classify_tick(root, now=datetime(2026, 7, 2, 12, 3, tzinfo=UTC))
            self.assertEqual(after.status, "POLL_DUE")
            events = [event["event_type"] for event in read_jsonl(root / "queue/events.jsonl")]
            self.assertIn("lock_broken", events)
            self.assertIn("job_recovered", events)

    def test_recover_leaves_a_lease_held_by_someone_else_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-SM-001"))
            ledger.transition("BQ-SM-001", "claimed", now=T0, holder="manager-a")
            acquire_lock(root, "manager-b", now=T0)

            result = recover_job(root, "BQ-SM-001", requeue=False, now=T0, holder_hint="manager-a")

            self.assertIsNone(result.released_lock)
            self.assertEqual(read_lock(root).holder, "manager-b")
            self.assertIn("did not claim", result.lock_note)

    def test_recover_uses_the_holder_hint_for_ledgers_without_claimed_by(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-SM-001"))
            ledger.transition("BQ-SM-001", "claimed", now=T0)  # no holder recorded (old ledgers)
            acquire_lock(root, "browser-queue-manager", now=T0)

            result = recover_job(root, "BQ-SM-001", now=T0, holder_hint="browser-queue-manager")

            self.assertEqual(result.released_lock.holder, "browser-queue-manager")

    def test_cli_recover_after_sigkill_unblocks_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = ["queue", "--root", tmp]
            _run(base + ["create", "--job-id", "BQ-SM-001", "--project-id", "SM",
                         "--target-chat-title", "t", "--target-chat-url", "manual"])
            _run(base + ["create", "--job-id", "BQ-SM-002", "--project-id", "SM",
                         "--target-chat-title", "t", "--target-chat-url", "manual"])
            self.assertEqual(_run(base + ["claim", "--job-id", "BQ-SM-001"])[0], 0)
            # (process dies here; nothing releases the lease)

            code, out, _ = _run(["tick", "--root", tmp, "--dry-run", "--format", "json"])
            self.assertEqual(json.loads(out)["status"], "BLOCKED_BY_BROWSER_LOCK")

            code, out, _ = _run(base + ["recover", "--job-id", "BQ-SM-001", "--requeue", "--error", "killed"])
            self.assertEqual(code, 0)
            record = json.loads(out)
            self.assertEqual(record["status"], "queued")
            self.assertEqual(record["released_lock_holder"], "browser-queue-manager")

            code, out, _ = _run(["tick", "--root", tmp, "--dry-run", "--format", "json"])
            self.assertEqual(json.loads(out)["status"], "POLL_DUE")

    def test_cli_recover_keep_lock_and_lock_break(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = ["queue", "--root", tmp]
            _run(base + ["create", "--job-id", "BQ-SM-001", "--project-id", "SM",
                         "--target-chat-title", "t", "--target-chat-url", "manual"])
            _run(base + ["claim", "--job-id", "BQ-SM-001"])

            code, out, _ = _run(base + ["recover", "--job-id", "BQ-SM-001", "--keep-lock"])
            self.assertEqual(code, 0)
            self.assertIsNone(json.loads(out)["released_lock_holder"])
            self.assertIsNotNone(read_lock(Path(tmp)))

            code, _, err = _run(["lock", "break", "--root", tmp])
            self.assertEqual(code, 2)
            self.assertIn("still fresh", err)

            code, out, _ = _run(["lock", "break", "--root", tmp, "--force"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["broken"])
            self.assertIsNone(read_lock(Path(tmp)))

    def test_recover_refuses_finished_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-SM-001"))
            ledger.transition("BQ-SM-001", "submitted", now=T0)
            ledger.transition("BQ-SM-001", "completed", now=T0)

            with self.assertRaises(ValueError):
                recover_job(root, "BQ-SM-001", now=T0)


if __name__ == "__main__":
    unittest.main()
