from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from arthur_loop.browser_lock import read_lock
from arthur_loop.cli import main
from arthur_loop.config import DEFAULT_INVOKE_TIMEOUT_SECONDS, load_config
from arthur_loop.follow import default_invoke, follow_loop, transport_argv
from arthur_loop.mcp import dispatch_tool
from arthur_loop.queue_ledger import QueueLedger, parse_ledger_time


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
    marker = str(request.get("expected_marker") or "")
    dest.write_text(REPLY + (f"\n{marker}\n" if marker else ""), encoding="utf-8")
    return {"status": "ok", "adapter": request["adapter"], "output": dest.relative_to(root).as_posix()}


def _missing_marker_invoke(root: Path, request: dict) -> dict:
    dest = root / "runtime" / "follow" / f"{request['job_id']}.out.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(REPLY, encoding="utf-8")
    return {"status": "ok", "adapter": request["adapter"], "output": dest.relative_to(root).as_posix()}


class FollowTests(unittest.TestCase):
    def test_grok_transport_argv_is_always_approve_then_dash_p(self) -> None:
        self.assertEqual(
            transport_argv("grok", "pong"),
            ["grok", "--always-approve", "-p", "pong"],
        )

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

    def test_missing_marker_stays_non_terminal_and_does_not_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            _, out, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "manual",
                    "--executor", "manual",
                ]
            )
            self.assertFalse(err)
            job_id = json.loads(out)["job"]["job_id"]

            report = follow_loop(root, once=True, invoke=_missing_marker_invoke)

            step = report["steps"][0]
            self.assertEqual(report["stopped"], "waiting_for_marker")
            self.assertEqual(step["action"], "waiting_for_marker")
            self.assertNotIn("completed", step)
            self.assertNotIn("enqueued", step)
            jobs = QueueLedger(root).latest_jobs()
            self.assertEqual(jobs[job_id].status, "waiting_for_chatgpt")
            self.assertEqual(len(jobs), 1)

    def test_lock_release_errors_are_not_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "manual",
                    "--executor", "manual",
                ]
            )
            with patch("arthur_loop.follow.release_lock", side_effect=OSError("release failed")):
                with self.assertRaisesRegex(OSError, "release failed"):
                    follow_loop(root, once=True, invoke=_fake_invoke)

    def test_echo_agent_advances_past_hop_two_without_manual_poll(self) -> None:
        """A non-interactive agent that only echoes its prompt must clear the plan hop.

        The plan pack is stripped of its marker line first, the way an older
        customized prompt looks. Rendering has to put the marker back and must
        not leave a literal {{token}}.
        """

        blocks = {
            "next-plan-request": (
                "```text\n"
                "PROJECT_ID: DEMO_APP\n"
                "REVIEW_TYPE: NEXT_PLAN_REQUEST\n"
                "APPROVAL_DECISION: REQUEST_CODEX_PLAN\n"
                "HAS_P0_P1: false\n"
                "```\n"
            ),
            "plan": (
                "```text\n"
                "PROJECT_ID: DEMO_APP\n"
                "REVIEW_TYPE: CODEX_PLAN\n"
                "PLAN_STATUS: READY_FOR_CHATGPT_REVIEW\n"
                "IMPLEMENTATION_STARTED: false\n"
                "```\n"
            ),
        }

        def _echo(root: Path, request: dict) -> dict:
            dest = root / "runtime" / "follow" / f"{request['job_id']}.out.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(request["prompt"] + "\n" + blocks[request["kind"]], encoding="utf-8")
            return {"status": "ok", "adapter": request["adapter"], "output": dest.relative_to(root).as_posix()}

        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            code, _, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "codex",
                    "--executor", "codex",
                    "--title", "Demo",
                ]
            )
            self.assertEqual(code, 0, err)
            pack = root / "adapters" / "executor" / "prompts" / "plan-only.md"
            stripped = pack.read_text(encoding="utf-8").replace("Include marker {{EXPECTED_MARKER}}.\n", "")
            self.assertNotIn("EXPECTED_MARKER", stripped)
            pack.write_text(stripped + "\n{{NOT_A_REAL_TOKEN}}\n", encoding="utf-8")

            report = follow_loop(root, max_steps=2, invoke=_echo)

            self.assertEqual(len(report["steps"]), 2, report)
            self.assertEqual(report["steps"][0]["action"], "advanced")
            self.assertEqual(report["steps"][0]["kind"], "next-plan-request")
            plan = report["steps"][1]
            self.assertEqual(plan["action"], "advanced", plan)
            self.assertEqual(plan["kind"], "plan")
            self.assertNotEqual(report["stopped"], "waiting_for_marker")
            jobs = QueueLedger(root).latest_jobs()
            plan_job = jobs[plan["job_id"]]
            self.assertEqual(plan_job.status, "completed")
            rendered = (root / plan_job.prompt_path).read_text(encoding="utf-8")
            self.assertIn(plan_job.expected_marker, rendered)
            self.assertNotIn("{{", rendered)
            self.assertNotIn("NOT_A_REAL_TOKEN", rendered)
            self.assertIn("(not available)", rendered)
            self.assertIn(report["steps"][0]["capture"]["path"], rendered)

    def test_invoke_timeout_defaults_long_and_failure_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            self.assertEqual(
                load_config(root)["polling_policy"]["invoke_timeout_seconds"],
                DEFAULT_INVOKE_TIMEOUT_SECONDS,
            )
            code, out, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "codex",
                    "--executor", "codex",
                ]
            )
            self.assertEqual(code, 0, err)
            job_id = json.loads(out)["job"]["job_id"]
            seen: dict = {}

            def _boom(instance: Path, request: dict) -> dict:
                seen["timeout"] = request.get("timeout")
                seen["lock"] = json.loads((instance / "runtime/browser-lock.json").read_text(encoding="utf-8"))
                return {"status": "error", "error": "timed out waiting"}

            with patch("arthur_loop.follow.default_invoke", side_effect=_boom):
                code, _, err = _run(["follow", "--root", tmp, "--once", "--timeout", "0.2"])
            self.assertEqual(code, 2, err)
            self.assertEqual(seen["timeout"], 0.2)
            job = QueueLedger(root).latest_jobs()[job_id]
            self.assertEqual(job.status, "needs_recovery")
            self.assertIn("timed out", job.last_error or "")
            self.assertIsNone(read_lock(root))
            self.assertNotEqual(job.claimed_by, None)

            code, _, err = _run(["queue", "--root", tmp, "recover", "--job-id", job_id, "--requeue"])
            self.assertEqual(code, 0, err)
            follow_loop(root, once=True, invoke=_boom, timeout=3600)
            acquired = parse_ledger_time(seen["lock"]["acquired_at"])
            stale = parse_ledger_time(seen["lock"]["stale_after"])
            self.assertIsNotNone(acquired)
            self.assertIsNotNone(stale)
            self.assertGreaterEqual((stale - acquired).total_seconds(), 3600)
            self.assertEqual(QueueLedger(root).latest_jobs()[job_id].status, "needs_recovery")
            self.assertIsNone(read_lock(root))

            code, _, err = _run(["queue", "--root", tmp, "recover", "--job-id", job_id, "--requeue"])
            self.assertEqual(code, 0, err)
            cfg_path = root / "config" / "arthur-loop.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.setdefault("polling_policy", {})["invoke_timeout_seconds"] = 99
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            follow_loop(root, once=True, invoke=_boom)
            self.assertEqual(seen["timeout"], 99)

            with patch(
                "arthur_loop.follow.transport_argv",
                return_value=[sys.executable, "-c", "import time; time.sleep(30)"],
            ):
                result = default_invoke(
                    root,
                    {"adapter": "codex", "prompt": "ping", "job_id": "BQ-TIMEOUT", "timeout": 0.3, "model": ""},
                )
            self.assertEqual(result["status"], "error")
            self.assertIn("timed out", result["error"].lower())

            for command in ("follow", "run"):
                help_out = io.StringIO()
                with redirect_stdout(help_out):
                    with self.assertRaises(SystemExit) as ctx:
                        main([command, "--help"])
                self.assertEqual(ctx.exception.code, 0)
                self.assertIn("--timeout", help_out.getvalue())

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
