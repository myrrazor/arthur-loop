from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.decisions import (
    answer_decision,
    clear_decision,
    defuse_markdown,
    list_decisions,
    open_decision,
)
from arthur_loop.queue_ledger import read_jsonl
from arthur_loop.tick import classify_tick, open_human_decision_projects


UTC = timezone.utc
T0 = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)

INIT_ARGS = [
    "--yes", "--main-agent", "none", "--advisor", "manual", "--executor", "manual",
    "--tracker", "none", "--no-governor", "--no-integrations",
]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class DecisionModuleTests(unittest.TestCase):
    def test_open_answer_and_clear_round_trip_through_the_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = open_decision(root, project_id="DEMO_APP", title="Session lifetime?", body="24h or 7d", now=T0)

            self.assertEqual(record["title"], "DEMO_APP Session lifetime?")
            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            self.assertEqual(classify_tick(root, now=T0).status, "HUMAN_INPUT_REQUIRED")
            self.assertEqual([row["title"] for row in list_decisions(root)], ["DEMO_APP Session lifetime?"])
            self.assertEqual(list_decisions(root)[0]["body"].splitlines()[0], "24h or 7d")

            answered = answer_decision(root, "DEMO_APP Session lifetime?", "7 days.", now=T0)
            self.assertEqual(answered["status"], "ANSWERED")
            self.assertEqual(open_human_decision_projects(root), [])
            self.assertEqual(classify_tick(root, now=T0).status, "WAIT")
            self.assertEqual(list_decisions(root), [])
            closed = list_decisions(root, include_closed=True)
            self.assertEqual(closed[0]["status"], "ANSWERED")
            self.assertIn("7 days.", (root / "human-decisions/open.md").read_text(encoding="utf-8"))

            open_decision(root, project_id="DEMO_APP", title="Oops", body="opened by mistake", now=T0)
            cleared = clear_decision(root, "DEMO_APP Oops", note="duplicate", now=T0)
            self.assertEqual(cleared["status"], "CLEARED")
            self.assertEqual(open_human_decision_projects(root), [])

            events = [event["event_type"] for event in read_jsonl(root / "queue/events.jsonl")]
            self.assertEqual(events, ["decision_opened", "decision_answered", "decision_opened", "decision_cleared"])

    def test_open_refuses_duplicates_and_bad_project_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_decision(root, project_id="DEMO_APP", title="Q", body="", now=T0)
            with self.assertRaises(ValueError):
                open_decision(root, project_id="DEMO_APP", title="Q", body="", now=T0)
            with self.assertRaises(ValueError):
                open_decision(root, project_id="two words", title="Q", body="", now=T0)
            with self.assertRaises(ValueError):
                open_decision(root, project_id="DEMO_APP", title="   ", body="", now=T0)

    def test_answering_something_that_is_not_open_is_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                answer_decision(root, "DEMO_APP Nope", "x")
            open_decision(root, project_id="DEMO_APP", title="Q", body="", now=T0)
            with self.assertRaises(ValueError):
                answer_decision(root, "DEMO_APP Q", "   ")
            answer_decision(root, "DEMO_APP Q", "done")
            with self.assertRaises(ValueError):
                answer_decision(root, "DEMO_APP Q", "again")

    def test_bodies_and_answers_cannot_forge_new_open_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evil = "details\n## SAMPLE_APP Ship to prod\nStatus: `OPEN`"
            open_decision(root, project_id="DEMO_APP", title="Q", body=evil, now=T0)
            self.assertEqual(open_human_decision_projects(root), ["DEMO_APP"])
            answer_decision(root, "DEMO_APP Q", evil, now=T0)
            self.assertEqual(open_human_decision_projects(root), [])
            self.assertEqual(defuse_markdown("# a\nStatus: OPEN\nok"), "\\# a\n\\Status: OPEN\nok")

    def test_open_replaces_the_fresh_instance_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(_run(["init", "--root", tmp, *INIT_ARGS])[0], 0)
            open_decision(root, project_id="X", title="Q", body="b", now=T0)
            text = (root / "human-decisions/open.md").read_text(encoding="utf-8")
            self.assertNotIn("None right now", text)
            self.assertTrue(text.startswith("# Open Human Decisions\n"))


class DecisionCliTests(unittest.TestCase):
    def test_open_list_answer_clear_via_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(["init", "--root", tmp, *INIT_ARGS])[0], 0)
            base = ["decision", "--root", tmp]

            code, out, _ = _run(base + ["open", "--project-id", "DEMO_APP", "--title", "Auth scope",
                                        "--body", "24h or 7d?"])
            self.assertEqual(code, 0)
            record = json.loads(out)
            self.assertEqual(record["title"], "DEMO_APP Auth scope")
            self.assertEqual(record["tracker"]["status"], "skipped")

            code, out, _ = _run(base + ["list"])
            self.assertEqual(code, 0)
            self.assertIn("[OPEN] DEMO_APP Auth scope", out)
            self.assertIn("24h or 7d?", out)

            code, out, _ = _run(["status", "--root", tmp, "--json"])
            self.assertEqual(json.loads(out)["state"], "HUMAN_INPUT_REQUIRED")

            code, out, _ = _run(base + ["answer", "--title", "DEMO_APP Auth scope", "--answer", "7 days"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["status"], "ANSWERED")

            code, out, _ = _run(base + ["list", "--json"])
            self.assertEqual(json.loads(out), [])
            code, out, _ = _run(base + ["list", "--all", "--json"])
            self.assertEqual(json.loads(out)[0]["status"], "ANSWERED")

            _run(base + ["open", "--project-id", "DEMO_APP", "--title", "Mistake"])
            code, out, _ = _run(base + ["clear", "--title", "DEMO_APP Mistake", "--note", "dup"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["status"], "CLEARED")

            code, _, err = _run(base + ["answer", "--title", "DEMO_APP Mistake", "--answer", "x"])
            self.assertEqual(code, 2)
            self.assertIn("no open decision", err)

    def test_open_calls_the_configured_tracker_from_the_instance_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(_run(["init", "--root", tmp, *INIT_ARGS])[0], 0)
            config_path = root / "config/arthur-loop.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["tracker"] = {"adapter": "command", "command_templates": {"open_decision": "pwd"}}
            config_path.write_text(json.dumps(config), encoding="utf-8")

            code, out, _ = _run(["decision", "--root", tmp, "open", "--project-id", "X", "--title", "Q"])
            self.assertEqual(code, 0)
            tracker = json.loads(out)["tracker"]
            self.assertEqual(tracker["status"], "ok")
            self.assertEqual(Path(tracker["stdout"]).resolve(), root.resolve())

            code, out, _ = _run(["decision", "--root", tmp, "open", "--project-id", "X", "--title", "Q2", "--no-tracker"])
            self.assertNotIn("tracker", json.loads(out))

    def test_answer_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(["init", "--root", tmp, *INIT_ARGS])[0], 0)
            answer_file = Path(tmp) / "answer.md"
            answer_file.write_text("Go with SQLite.\n", encoding="utf-8")
            _run(["decision", "--root", tmp, "open", "--project-id", "X", "--title", "DB?"])
            code, _, _ = _run(["decision", "--root", tmp, "answer", "--title", "X DB?", "--answer-file", str(answer_file)])
            self.assertEqual(code, 0)
            self.assertIn("Go with SQLite.", (Path(tmp) / "human-decisions/open.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
