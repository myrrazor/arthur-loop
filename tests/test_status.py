from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arthur_loop.browser_lock import acquire_lock
from arthur_loop.queue_ledger import QueueJob, QueueLedger, read_jsonl
from arthur_loop.status import (
    build_console,
    clear_session,
    collect_status,
    load_sessions,
    record_session,
    render_status,
    sessions_path,
    status_to_dict,
)
from arthur_loop.usage_attribution import append_snapshot, snapshot_from_codexbar_json


UTC = timezone.utc
T0 = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _seed_busy_root(root: Path) -> None:
    """One due job, one blocked project, quota at 62% left, held lock, one session."""

    QueueLedger(root).record_job(
        QueueJob(
            job_id="BQ-SAMPLE_APP-001",
            project_id="SAMPLE_APP",
            target_chat_title="Sample App Planning",
            target_chat_url="https://chatgpt.com/c/test",
        )
    )

    decisions = root / "human-decisions/open.md"
    decisions.parent.mkdir(parents=True)
    decisions.write_text(
        "# Open\n\n## DEMO_APP Auth Scope Gate\n\nStatus: `OPEN`\n\nPick a scope.\n",
        encoding="utf-8",
    )

    for project_id, summary in [
        ("DEMO_APP", "Sprint 2 paused on the auth-scope decision."),
        ("SAMPLE_APP", "Planning loop active."),
    ]:
        state = root / "projects" / project_id / "state.md"
        state.parent.mkdir(parents=True)
        state.write_text(f"# {project_id} State\n\n{summary}\n", encoding="utf-8")

    append_snapshot(
        root,
        snapshot_from_codexbar_json(
            [{"provider": "codex", "usage": {"primary": {"usedPercent": 38}}}],
            snapshot_id="latest",
            captured_at="2026-07-02T11:30:00Z",
        ),
    )

    acquire_lock(root, "queue-manager", now=T0 - timedelta(minutes=2))
    record_session(
        root,
        session_id="master",
        role="master",
        activity="orchestrating the demo loop",
        now=T0 - timedelta(minutes=5),
    )


class SessionRegistryTests(unittest.TestCase):
    def test_record_session_folds_by_id_and_clear_removes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_session(root, session_id="master", role="master", activity="starting up", now=T0)
            record_session(
                root,
                session_id="master",
                role="master",
                activity="reviewing sprint plan",
                now=T0 + timedelta(minutes=3),
            )

            sessions = load_sessions(root, now=T0 + timedelta(minutes=4))
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].record.activity, "reviewing sprint plan")

            self.assertTrue(clear_session(root, "master", now=T0 + timedelta(minutes=5)))
            self.assertEqual(load_sessions(root, now=T0 + timedelta(minutes=6)), [])
            self.assertFalse(clear_session(root, "master"))

            # append-only: fold hides history, the file keeps it
            self.assertEqual(len(read_jsonl(sessions_path(root))), 3)

    def test_sessions_go_stale_after_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_session(root, session_id="exec-1", role="executor", activity="implementing", now=T0)

            fresh = load_sessions(root, now=T0 + timedelta(minutes=10))
            self.assertFalse(fresh[0].stale)
            self.assertEqual(fresh[0].age_minutes, 10)

            stale = load_sessions(root, now=T0 + timedelta(minutes=61))
            self.assertTrue(stale[0].stale)
            self.assertEqual(stale[0].age_minutes, 61)

    def test_invalid_session_state_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                record_session(
                    Path(tmp),
                    session_id="x",
                    role="master",
                    state="thinking",
                    activity="nope",
                )


class StatusSnapshotTests(unittest.TestCase):
    def test_collect_status_reflects_ledger_decisions_quota_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_busy_root(root)

            snapshot = collect_status(root, now=T0)

            # due SAMPLE_APP job + fresh lock -> blocked by lock, per tick precedence
            self.assertEqual(snapshot.tick.status, "BLOCKED_BY_BROWSER_LOCK")
            self.assertEqual([job.job_id for job in snapshot.jobs], ["BQ-SAMPLE_APP-001"])

            by_id = {project.project_id: project for project in snapshot.projects}
            self.assertTrue(by_id["DEMO_APP"].blocked_by_decision)
            self.assertFalse(by_id["SAMPLE_APP"].blocked_by_decision)

            self.assertEqual(snapshot.quota["left_percent"], 62)
            self.assertEqual(snapshot.quota["state"], "GREEN")
            self.assertEqual(snapshot.browser_lock["holder"], "queue-manager")
            self.assertTrue(snapshot.browser_lock["fresh"])
            self.assertEqual(snapshot.open_decisions[0]["project_id"], "DEMO_APP")

            payload = json.loads(json.dumps(status_to_dict(snapshot), sort_keys=True))
            self.assertEqual(payload["state"], "BLOCKED_BY_BROWSER_LOCK")
            self.assertEqual(payload["sessions"][0]["session_id"], "master")
            self.assertEqual(payload["quota"]["left_percent"], 62)

    def test_quota_disabled_renders_governor_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_busy_root(root)

            snapshot = collect_status(root, now=T0, quota_enabled=False)
            self.assertIsNone(snapshot.quota)

            console = build_console(record=True, width=120)
            render_status(snapshot, console)
            self.assertIn("governor off", console.export_text())


class RenderTests(unittest.TestCase):
    def test_render_plain_text_contains_all_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_busy_root(root)

            console = build_console(record=True, width=120)
            render_status(collect_status(root, now=T0), console)
            text = console.export_text()

            for expected in [
                "ARTHUR LOOP",
                "BLOCKED_BY_BROWSER_LOCK",
                "SESSIONS",
                "orchestrating the demo loop",
                "QUEUE",
                "BQ-SAMPLE_APP-001",
                "PROJECTS",
                "BLOCKED-BY-DECISION",
                "HUMAN DECISIONS",
                "DEMO_APP Auth Scope Gate",
                "62% left",
                "held by queue-manager",
            ]:
                self.assertIn(expected, text)

    def test_render_empty_root_is_calm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            console = build_console(record=True, width=120)
            render_status(collect_status(Path(tmp), now=T0), console)
            text = console.export_text()

            self.assertIn("WAIT", text)
            self.assertIn("no sessions reporting", text)
            self.assertIn("queue is empty", text)
            self.assertIn("no snapshot yet", text)


if __name__ == "__main__":
    unittest.main()
