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
from arthur_loop.decisions import open_decision
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.recovery import recover_job


UTC = timezone.utc
T0 = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _job(job_id: str = "BQ-KN-001", **extra) -> QueueJob:
    return QueueJob(
        job_id=job_id,
        project_id="KN",
        target_chat_title="KN planning",
        target_chat_url="manual",
        **extra,
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _init(tmp: str) -> None:
    code, _, err = _run(
        [
            "init", "--root", tmp, "--yes", "--main-agent", "none",
            "--advisor", "manual", "--executor", "manual", "--tracker", "none",
            "--no-governor", "--no-integrations",
        ]
    )
    assert code == 0, err


class EmptyIdempotencyTests(unittest.TestCase):
    def test_whitespace_key_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            with self.assertRaises(ValueError) as ctx:
                ledger.create_job(_job(idempotency_key="   "))
            self.assertIn("empty", str(ctx.exception))
            self.assertEqual(ledger.latest_jobs(), {})

    def test_cli_empty_key_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run(
                [
                    "queue", "--root", tmp, "create",
                    "--job-id", "BQ-KN-001", "--project-id", "KN",
                    "--target-chat-title", "t", "--target-chat-url", "manual",
                    "--idempotency-key", "",
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("empty", err)


class MarkerCollisionTests(unittest.TestCase):
    def test_duplicate_marker_on_a_new_job_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            ledger.create_job(_job("BQ-KN-001", expected_marker="HELLO"))
            with self.assertRaises(ValueError) as ctx:
                ledger.create_job(_job("BQ-KN-002", expected_marker="HELLO"))
            self.assertIn("HELLO", str(ctx.exception))
            self.assertIn("BQ-KN-001", str(ctx.exception))
            self.assertEqual(list(ledger.latest_jobs()), ["BQ-KN-001"])

    def test_blank_marker_is_treated_as_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = QueueLedger(Path(tmp))
            job = ledger.create_job(_job("BQ-KN-001", expected_marker="  "))
            self.assertIsNone(job.expected_marker)


class ClaimOnPausedTests(unittest.TestCase):
    def test_claim_is_refused_when_the_project_has_an_open_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            _run(
                [
                    "queue", "--root", tmp, "create",
                    "--job-id", "BQ-KN-001", "--project-id", "KN",
                    "--target-chat-title", "t", "--target-chat-url", "manual",
                ]
            )
            open_decision(Path(tmp), project_id="KN", title="Pause this?", body="yes")
            code, _, err = _run(["queue", "--root", tmp, "claim", "--job-id", "BQ-KN-001"])
            self.assertEqual(code, 2)
            self.assertIn("paused", err)
            self.assertEqual(QueueLedger(Path(tmp)).latest_jobs()["BQ-KN-001"].status, "queued")
            self.assertIsNone(read_lock(Path(tmp)))


class RecoverLockStealTests(unittest.TestCase):
    def test_recover_of_a_queued_job_does_not_steal_another_manager_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-KN-001"))
            ledger.create_job(_job("BQ-KN-002"))
            acquire_lock(root, "browser-queue-manager", now=T0)
            ledger.transition("BQ-KN-001", "claimed", now=T0, holder="browser-queue-manager")

            result = recover_job(
                root,
                "BQ-KN-002",
                requeue=False,
                now=T0,
                holder_hint="browser-queue-manager",
            )

            self.assertIsNone(result.released_lock)
            self.assertEqual(read_lock(root).holder, "browser-queue-manager")
            self.assertIn("did not claim", result.lock_note)

    def test_recover_still_releases_the_claimer_of_the_abandoned_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.create_job(_job("BQ-KN-001"))
            acquire_lock(root, "browser-queue-manager", now=T0)
            ledger.transition("BQ-KN-001", "claimed", now=T0, holder="browser-queue-manager")

            result = recover_job(root, "BQ-KN-001", requeue=True, now=T0)
            self.assertEqual(result.released_lock.holder, "browser-queue-manager")
            self.assertIsNone(read_lock(root))


if __name__ == "__main__":
    unittest.main()
