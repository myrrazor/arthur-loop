from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from arthur_loop.cli import main


ROOT = Path(__file__).resolve().parents[1]

# the exact command lines the README quickstart documents; keep them in sync
QUICKSTART = [
    "arthur init --yes --main-agent none --advisor manual --executor manual --tracker none --no-governor",
    "arthur queue create --job-id BQ-MY_APP-001 --project-id MY_APP "
    "--target-chat-title \"MY_APP planning\" --target-chat-url manual "
    "--expected-marker HELLO_LOOP --idempotency-key my-app-001",
    "arthur queue claim --job-id BQ-MY_APP-001",
    "arthur queue submit --job-id BQ-MY_APP-001",
    "arthur capture --project-id MY_APP --job-id BQ-MY_APP-001 --kind next-plan-request "
    "--source-chat-title manual --source-file queue/manual/done/BQ-MY_APP-001.md",
    "arthur queue poll-result --job-id BQ-MY_APP-001 --marker-found true --status completed",
]

ADVISOR_REPLY = """# Next plan

Received Marker: HELLO_LOOP

Plan-only prompt for the executor: scaffold the project and write the first test.

```text
PROJECT_ID: MY_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
IDEMPOTENCY_KEY: my-app-001
```
"""


def _argv(command: str) -> list[str]:
    import shlex

    parts = shlex.split(command)
    assert parts[0] == "arthur"
    return parts[1:]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class QuickstartTests(unittest.TestCase):
    """The README's manual loop, executed literally and non-interactively."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self) -> None:
        os.chdir(self._previous)
        self._tmp.cleanup()

    def test_readme_documents_exactly_these_commands(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8").replace(" \\\n  ", " ")
        for command in QUICKSTART:
            self.assertTrue(command in readme, f"README quickstart is missing: {command}")

    def test_manual_loop_runs_end_to_end_without_a_terminal(self) -> None:
        root = Path(self._tmp.name)
        code, _, err = _run(_argv(QUICKSTART[0]))
        self.assertEqual(code, 0, err)
        self.assertTrue((root / "adapters/advisor/prompts/next-plan-request.md").exists())

        code, _, err = _run(_argv(QUICKSTART[1]))
        self.assertEqual(code, 0, err)
        code, out, _ = _run(["status", "--json"])
        self.assertEqual(json.loads(out)["state"], "POLL_DUE")

        code, _, err = _run(_argv(QUICKSTART[2]))
        self.assertEqual(code, 0, err)
        (root / "queue/manual/pending/BQ-MY_APP-001.md").write_text("prompt\n", encoding="utf-8")

        code, _, err = _run(_argv(QUICKSTART[3]))
        self.assertEqual(code, 0, err)
        code, out, _ = _run(["status", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["state"], "WAIT")
        self.assertEqual(payload["queue"][0]["status"], "submitted")
        self.assertIsNone(payload["browser_lock"], "submit releases the lease by default")

        (root / "queue/manual/done/BQ-MY_APP-001.md").write_text(ADVISOR_REPLY, encoding="utf-8")
        code, out, err = _run(_argv(QUICKSTART[4]))
        self.assertEqual(code, 0, err)
        artifact = json.loads(out)
        self.assertTrue(artifact["control_block_valid"])
        self.assertEqual(artifact["approval_decision"], "REQUEST_CODEX_PLAN")
        self.assertTrue((root / artifact["path"]).exists())
        self.assertTrue((root / "projects/MY_APP/artifacts/chatgpt/index.md").exists())

        code, out, err = _run(_argv(QUICKSTART[5]))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "completed")
        self.assertIn(artifact["path"], json.loads(out)["output_artifact_paths"])

        code, out, _ = _run(["status", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["state"], "WAIT")
        self.assertEqual(payload["hidden_terminal_jobs"], 1)
        self.assertEqual(payload["decisions"], [])

        # the same create again is refused on both the job id and the idempotency key
        code, _, err = _run(_argv(QUICKSTART[1]))
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)


if __name__ == "__main__":
    unittest.main()
