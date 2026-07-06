from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from arthur_loop.queue_ledger import QueueJob, QueueLedger, next_poll_at, read_jsonl


UTC = timezone.utc


class QueueLedgerTests(unittest.TestCase):
    def test_next_poll_uses_one_minute_then_five_minutes(self) -> None:
        now = datetime(2026, 6, 22, 4, 0, tzinfo=UTC)

        self.assertEqual(next_poll_at(now, first=True), "2026-06-22T04:01:00Z")
        self.assertEqual(next_poll_at(now), "2026-06-22T04:05:00Z")

    def test_records_transitions_and_due_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            job = QueueJob(
                job_id="BQ-SMOKE-SAMPLE_APP-001",
                project_id="SAMPLE_APP",
                target_chat_title="Sample App Planning",
                target_chat_url="https://chatgpt.com/c/test",
                expected_marker="ARTHUR_LOOP_SAMPLE_APP_QUEUE_TEST",
            )

            ledger.record_job(job)
            submitted = ledger.transition(
                "BQ-SMOKE-SAMPLE_APP-001",
                "submitted",
                now=datetime(2026, 6, 22, 4, 0, tzinfo=UTC),
            )
            self.assertEqual(submitted.attempt_count, 1)
            self.assertEqual(submitted.next_poll_at, "2026-06-22T04:01:00Z")

            due = ledger.due_jobs(datetime(2026, 6, 22, 4, 1, tzinfo=UTC))
            self.assertEqual([item.job_id for item in due], ["BQ-SMOKE-SAMPLE_APP-001"])

            waiting = ledger.transition(
                "BQ-SMOKE-SAMPLE_APP-001",
                "waiting_for_chatgpt",
                now=datetime(2026, 6, 22, 4, 1, tzinfo=UTC),
            )
            self.assertEqual(waiting.next_poll_at, "2026-06-22T04:06:00Z")

            stopped = ledger.transition(
                "BQ-SMOKE-SAMPLE_APP-001",
                "stopped_no_output",
                now=datetime(2026, 6, 22, 4, 1, tzinfo=UTC),
                error="ChatGPT stopped without visible output",
            )
            self.assertEqual(stopped.next_poll_at, "2026-06-22T04:06:00Z")

            not_due = ledger.due_jobs(datetime(2026, 6, 22, 4, 5, tzinfo=UTC))
            self.assertEqual(not_due, [])

            due_after_wait = ledger.due_jobs(datetime(2026, 6, 22, 4, 6, tzinfo=UTC))
            self.assertEqual([item.job_id for item in due_after_wait], ["BQ-SMOKE-SAMPLE_APP-001"])

            completed = ledger.transition(
                "BQ-SMOKE-SAMPLE_APP-001",
                "completed",
                now=datetime(2026, 6, 22, 4, 7, tzinfo=UTC),
            )
            self.assertIsNone(completed.next_poll_at)
            self.assertEqual(
                ledger.due_jobs(datetime(2026, 6, 22, 4, 8, tzinfo=UTC)),
                [],
            )

    def test_rejects_unknown_status_and_finds_stale_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            job = QueueJob(
                job_id="BQ-STUCK-001",
                project_id="SAMPLE_APP",
                target_chat_title="Sample App Planning",
                target_chat_url="https://chatgpt.com/c/test",
            )

            ledger.record_job(job)
            ledger.transition(
                "BQ-STUCK-001",
                "claimed",
                now=datetime(2026, 6, 22, 4, 0, tzinfo=UTC),
            )

            with self.assertRaises(ValueError):
                ledger.transition("BQ-STUCK-001", "in_progress")

            stale = ledger.stale_jobs(datetime(2026, 6, 22, 4, 31, tzinfo=UTC))
            self.assertEqual([item.job_id for item in stale], ["BQ-STUCK-001"])

    def test_needs_recovery_clears_next_poll_and_leaves_due_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            ledger.record_job(
                QueueJob(
                    job_id="BQ-REC-001",
                    project_id="SAMPLE_APP",
                    target_chat_title="Sample App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )
            ledger.transition(
                "BQ-REC-001",
                "submitted",
                now=datetime(2026, 6, 22, 4, 0, tzinfo=UTC),
            )

            recovered = ledger.transition(
                "BQ-REC-001",
                "needs_recovery",
                now=datetime(2026, 6, 22, 4, 45, tzinfo=UTC),
                error="stale claim after manager restart",
            )

            self.assertIsNone(recovered.next_poll_at)
            self.assertEqual(ledger.due_jobs(datetime(2026, 6, 22, 5, 0, tzinfo=UTC)), [])

    def test_queue_cli_emits_timing_events_without_handwritten_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = Path(__file__).resolve().parents[1] / "scripts/queue-job.py"
            env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

            base = [sys.executable, str(script), "--root", str(root)]
            subprocess.run(
                base
                + [
                    "create",
                    "--job-id",
                    "BQ-CLI-001",
                    "--project-id",
                    "DEMO_APP",
                    "--target-chat-title",
                    "Demo App Planning",
                    "--target-chat-url",
                    "https://chatgpt.com/c/test",
                ],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            subprocess.run(base + ["claim", "--job-id", "BQ-CLI-001", "--at", "2026-06-22T04:00:00Z"], check=True, env=env, stdout=subprocess.PIPE, text=True)
            subprocess.run(base + ["submit", "--job-id", "BQ-CLI-001", "--at", "2026-06-22T04:00:30Z"], check=True, env=env, stdout=subprocess.PIPE, text=True)
            subprocess.run(
                base
                + [
                    "poll-result",
                    "--job-id",
                    "BQ-CLI-001",
                    "--marker-found",
                    "true",
                    "--status",
                    "completed",
                    "--at",
                    "2026-06-22T04:01:30Z",
                ],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )

            events = read_jsonl(root / "queue/events.jsonl")
            event_types = [event["event_type"] for event in events]
            self.assertIn("attempt_submitted", event_types)
            self.assertIn("first_poll_result", event_types)

    def test_queue_cli_refuses_duplicate_create_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = Path(__file__).resolve().parents[1] / "scripts/queue-job.py"
            env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            base = [sys.executable, str(script), "--root", str(root)]
            create = base + [
                "create",
                "--job-id",
                "BQ-DUP-001",
                "--project-id",
                "SAMPLE_APP",
                "--target-chat-title",
                "Sample App Planning",
                "--target-chat-url",
                "https://chatgpt.com/c/test",
            ]

            subprocess.run(create, check=True, env=env, stdout=subprocess.PIPE, text=True)

            duplicate = subprocess.run(
                create, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("already exists", duplicate.stderr)

            forced = subprocess.run(
                create + ["--force"], env=env, stdout=subprocess.PIPE, text=True
            )
            self.assertEqual(forced.returncode, 0)

    def test_queue_cli_recover_parks_then_requeues_a_stale_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = Path(__file__).resolve().parents[1] / "scripts/queue-job.py"
            env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            base = [sys.executable, str(script), "--root", str(root)]

            subprocess.run(
                base
                + [
                    "create",
                    "--job-id",
                    "BQ-REC-002",
                    "--project-id",
                    "SAMPLE_APP",
                    "--target-chat-title",
                    "Sample App Planning",
                    "--target-chat-url",
                    "https://chatgpt.com/c/test",
                ],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            subprocess.run(base + ["claim", "--job-id", "BQ-REC-002", "--at", "2026-06-22T04:00:00Z"], check=True, env=env, stdout=subprocess.PIPE, text=True)
            subprocess.run(base + ["submit", "--job-id", "BQ-REC-002", "--at", "2026-06-22T04:00:30Z"], check=True, env=env, stdout=subprocess.PIPE, text=True)

            parked = subprocess.run(
                base + ["recover", "--job-id", "BQ-REC-002", "--error", "manager died mid-poll", "--at", "2026-06-22T05:00:00Z"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            record = json.loads(parked.stdout)
            self.assertEqual(record["status"], "needs_recovery")
            self.assertIsNone(record["next_poll_at"])
            self.assertEqual(
                QueueLedger(root).due_jobs(datetime(2026, 6, 22, 6, 0, tzinfo=UTC)),
                [],
            )

            requeued = subprocess.run(
                base + ["recover", "--job-id", "BQ-REC-002", "--requeue", "--at", "2026-06-22T05:05:00Z"],
                check=True,
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(json.loads(requeued.stdout)["status"], "queued")
            due = QueueLedger(root).due_jobs(datetime(2026, 6, 22, 6, 0, tzinfo=UTC))
            self.assertEqual([job.job_id for job in due], ["BQ-REC-002"])

    def test_completed_with_warnings_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = QueueLedger(root)
            job = QueueJob(
                job_id="BQ-DEMO_APP-PLAN-001",
                project_id="DEMO_APP",
                target_chat_title="Demo App Planning",
                target_chat_url="https://chatgpt.com/c/test",
                expected_marker="ARTHUR_LOOP_DEMO_APP_PLAN_REQUEST",
            )

            ledger.record_job(job)
            ledger.transition(
                "BQ-DEMO_APP-PLAN-001",
                "submitted",
                now=datetime(2026, 6, 22, 5, 5, tzinfo=UTC),
            )

            completed = ledger.transition(
                "BQ-DEMO_APP-PLAN-001",
                "completed_with_warnings",
                now=datetime(2026, 6, 22, 5, 7, tzinfo=UTC),
                error="copy_response_clipboard_empty",
                output_artifact_paths=["outputs/browser-queue/demo-app-chatgpt-next-plan-request.md"],
            )

            self.assertEqual(completed.completed_at, "2026-06-22T05:07:00Z")
            self.assertIsNone(completed.next_poll_at)
            self.assertEqual(
                ledger.due_jobs(datetime(2026, 6, 22, 5, 8, tzinfo=UTC)),
                [],
            )


if __name__ == "__main__":
    unittest.main()
