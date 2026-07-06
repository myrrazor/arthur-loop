from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arthur_loop.browser_lock import acquire_lock
from arthur_loop.queue_ledger import QueueJob, QueueLedger, read_jsonl
from arthur_loop.tick import classify_tick, open_human_decision_projects, write_tick_state
from arthur_loop.usage_attribution import append_snapshot, snapshot_from_codexbar_json


UTC = timezone.utc


class TickTests(unittest.TestCase):
    def test_poll_due_when_unblocked_job_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            QueueLedger(root).record_job(
                QueueJob(
                    job_id="BQ-SAMPLE_APP-001",
                    project_id="SAMPLE_APP",
                    target_chat_title="Sample App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )

            result = classify_tick(root, now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

            self.assertEqual(result.status, "POLL_DUE")
            self.assertEqual(result.due_job_id, "BQ-SAMPLE_APP-001")

    def test_browser_lock_blocks_due_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            QueueLedger(root).record_job(
                QueueJob(
                    job_id="BQ-SAMPLE_APP-001",
                    project_id="SAMPLE_APP",
                    target_chat_title="Sample App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )
            acquire_lock(root, "other-manager", now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

            result = classify_tick(root, now=datetime(2026, 7, 2, 12, 1, tzinfo=UTC))

            self.assertEqual(result.status, "BLOCKED_BY_BROWSER_LOCK")
            self.assertEqual(result.browser_lock_holder, "other-manager")

    def test_quota_blocks_due_job_at_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            QueueLedger(root).record_job(
                QueueJob(
                    job_id="BQ-SAMPLE_APP-001",
                    project_id="SAMPLE_APP",
                    target_chat_title="Sample App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )
            append_snapshot(
                root,
                snapshot_from_codexbar_json(
                    [{"provider": "codex", "usage": {"primary": {"usedPercent": 95}}}],
                    snapshot_id="latest",
                    captured_at="2026-07-02T12:00:00Z",
                ),
            )

            result = classify_tick(root, now=datetime(2026, 7, 2, 12, 1, tzinfo=UTC))

            self.assertEqual(result.status, "BLOCKED_BY_QUOTA")
            self.assertEqual(result.quota_left_percent, 5)

    def test_human_decision_blocks_only_matching_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "human-decisions/open.md"
            decisions.parent.mkdir(parents=True)
            decisions.write_text(
                "# Open\n\n## DEMO_APP Sprint 14\n\nStatus: `OPEN`\n",
                encoding="utf-8",
            )
            ledger = QueueLedger(root)
            ledger.record_job(
                QueueJob(
                    job_id="BQ-DEMO_APP-001",
                    project_id="DEMO_APP",
                    target_chat_title="Demo App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )
            ledger.record_job(
                QueueJob(
                    job_id="BQ-SAMPLE_APP-001",
                    project_id="SAMPLE_APP",
                    target_chat_title="Sample App Planning",
                    target_chat_url="https://chatgpt.com/c/test",
                )
            )

            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            result = classify_tick(root, now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

            self.assertEqual(result.status, "POLL_DUE")
            self.assertEqual(result.due_project_id, "SAMPLE_APP")

    def test_wait_writes_tick_state_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = classify_tick(root, now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC), dry_run=False)

            self.assertEqual(result.status, "WAIT")
            path = write_tick_state(root, result)
            self.assertTrue(path.exists())

            events = read_jsonl(root / "queue/events.jsonl")
            self.assertEqual(events[0]["event_type"], "tick")


if __name__ == "__main__":
    unittest.main()
