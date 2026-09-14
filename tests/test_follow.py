from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.follow import follow_loop
from arthur_loop.mcp import dispatch_tool
from arthur_loop.queue_ledger import QueueLedger


REPLY = """# Next plan

Follow hop.

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
```
"""


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


def _fake_invoke(root: Path, request: dict) -> dict:
    dest = root / "runtime" / "follow" / f"{request['job_id']}.out.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(REPLY, encoding="utf-8")
    return {"status": "ok", "adapter": request["adapter"], "output": dest.relative_to(root).as_posix()}


class FollowTests(unittest.TestCase):
    def test_follow_claims_invokes_captures_and_enqueues_next_hop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            code, out, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "manual",
                    "--executor", "manual",
                    "--title", "Demo",
                ]
            )
            self.assertEqual(code, 0, err)
            created = json.loads(out)
            job_id = created["job"]["job_id"]

            report = follow_loop(root, once=True, invoke=_fake_invoke)
            self.assertEqual(report["stopped"], "advanced")
            step = report["steps"][0]
            self.assertEqual(step["action"], "advanced")
            self.assertEqual(step["job_id"], job_id)
            self.assertEqual(step["completed"]["status"], "completed")
            self.assertFalse(step["gate"]["go"])
            self.assertEqual(step["enqueued_kind"], "plan")
            jobs = QueueLedger(root).latest_jobs()
            self.assertEqual(jobs[job_id].status, "completed")
            self.assertEqual(len(jobs), 2)

    def test_manual_without_invoke_stops_honestly_after_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "manual",
                    "--executor", "manual",
                ]
            )
            code, out, err = _run(["follow", "--root", tmp, "--once", "--no-chain"])
            self.assertEqual(code, 3, err)
            report = json.loads(out)
            self.assertEqual(report["stopped"], "needs_human")
            self.assertIn("inbox", report["steps"][0]["invoke"])

    def test_mcp_follow_run_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            dispatch_tool(root, "arthur.loop.create", {"project_id": "DEMO_APP"})
            result = dispatch_tool(root, "arthur.follow.run", {"once": True, "dry_run": True})
            self.assertIn("structuredContent", result)
            self.assertEqual(result["structuredContent"]["stopped"], "dry_run")


if __name__ == "__main__":
    unittest.main()
