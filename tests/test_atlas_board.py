from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from arthur_loop.atlas_board import (
    is_ready_or_assigned,
    open_jobs_from_board,
    read_board,
    tickets_from_board,
)
from arthur_loop.cli import main
from arthur_loop.queue_ledger import QueueLedger


BOARD = {
    "format_version": "v1",
    "kind": "board",
    "columns": {
        "ready": [
            {"id": "APP-1", "project": "APP", "title": "Ship login", "status": "ready", "assignee": "agent:builder-1"},
            {"id": "APP-2", "project": "APP", "title": "Also ready", "status": "ready"},
        ],
        "in_progress": [
            {"id": "APP-3", "project": "APP", "title": "Mid sprint", "status": "in_progress", "assignee": "agent:builder-1"},
        ],
        "done": [
            {"id": "APP-9", "project": "APP", "title": "Finished", "status": "done"},
        ],
        "backlog": [
            {"id": "APP-8", "project": "APP", "title": "Later", "status": "backlog"},
        ],
    },
}


def _runner(payload):
    def run(cmd, **kwargs):
        self_check = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        assert cmd[1] == "board"
        assert "--json" in cmd
        return self_check

    return run


class BoardParseTests(unittest.TestCase):
    def test_flattens_columns_and_picks_ready_assigned_work(self) -> None:
        tickets = tickets_from_board(BOARD)
        self.assertEqual({t["id"] for t in tickets}, {"APP-1", "APP-2", "APP-3", "APP-9", "APP-8"})
        ready_ids = {_ticket_id(t) for t in tickets if is_ready_or_assigned(t)}
        self.assertEqual(ready_ids, {"APP-1", "APP-2", "APP-3"})
        self.assertFalse(is_ready_or_assigned({"id": "APP-9", "status": "done"}))


def _ticket_id(ticket):
    return ticket["id"]


class BoardReadTests(unittest.TestCase):
    def test_read_and_open_jobs_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(
                [
                    "init", "--root", tmp, "--yes", "--main-agent", "none",
                    "--advisor", "manual", "--executor", "manual", "--tracker", "atlas-tasker",
                    "--no-governor", "--no-integrations",
                ]
            )
            board = read_board(root, runner=_runner(BOARD))
            self.assertEqual(board["ready_count"], 3)
            first = open_jobs_from_board(root, runner=_runner(BOARD), actor="agent:test", reason="open ready work")
            self.assertEqual(len(first["opened"]), 3)
            self.assertEqual(len(first["skipped"]), 0)
            jobs = QueueLedger(root).latest_jobs()
            self.assertEqual(len(jobs), 3)
            keys = {job.idempotency_key for job in jobs.values()}
            self.assertTrue(keys <= {"atlas:APP-1", "atlas:APP-2", "atlas:APP-3"} or keys == {"atlas:APP-1", "atlas:APP-2", "atlas:APP-3"})
            second = open_jobs_from_board(root, runner=_runner(BOARD))
            self.assertEqual(len(second["opened"]), 0)
            self.assertEqual(len(second["skipped"]), 3)
            self.assertEqual(len(QueueLedger(root).latest_jobs()), 3)

    def test_dry_run_does_not_write_and_assigns_distinct_job_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main(
                [
                    "init", "--root", tmp, "--yes", "--main-agent", "none",
                    "--advisor", "manual", "--executor", "manual", "--tracker", "none",
                    "--no-governor", "--no-integrations",
                ]
            )
            preview = open_jobs_from_board(root, runner=_runner(BOARD), dry_run=True)
            ids = [row["job_id"] for row in preview["opened"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(QueueLedger(root).latest_jobs(), {})

    def test_cli_accepts_board_json_flag(self) -> None:
        from arthur_loop.cli import build_parser

        args = build_parser().parse_args(["tracker", "board", "--json", "--project", "APP"])
        self.assertEqual(args.tracker_action, "board")
        self.assertTrue(args.json)
        self.assertEqual(args.project, "APP")

    def test_missing_tracker_is_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch

            with patch("arthur_loop.atlas_board.tracker_binary", return_value=None):
                with self.assertRaises(FileNotFoundError):
                    read_board(Path(tmp))


if __name__ == "__main__":
    unittest.main()
