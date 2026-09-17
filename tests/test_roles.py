from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.config import load_config
from arthur_loop.follow import follow_loop, transport_argv
from arthur_loop.loop_ops import create_loop
from arthur_loop.mcp import dispatch_tool
from arthur_loop.queue_ledger import QueueLedger
from arthur_loop.roles import formulate_default_loop, next_hop_kind, parse_role_updates, resolve_roles
from arthur_loop.web import WebApp, make_server


PLAN_REPLY = """# Plan

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: CODEX_PLAN
PLAN_STATUS: READY_FOR_CHATGPT_REVIEW
PLAN_HAS_P0_P1: false
IMPLEMENTATION_STARTED: false
```
"""

REVIEW_REPLY = """# Review

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: APPROVE_PLAN
HAS_P0_P1: false
```
"""

IMPL_REPLY = """# Done

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: CODEX_IMPLEMENTATION_HANDOFF
IMPLEMENTATION_STATUS: COMPLETE
TESTS_RUN: true
READY_FOR_CHATGPT_REVIEW: true
```
"""

QA_REPLY = """# QA

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: QA_REVIEW
QA_STATUS: QA_PASS
```
"""

REPLIES = {
    "next-plan-request": """# Next

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
```
""",
    "plan": PLAN_REPLY,
    "plan-review": REVIEW_REPLY,
    "implementation-handoff": IMPL_REPLY,
    "qa-review": QA_REPLY,
}


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


def _invoke(root: Path, request: dict) -> dict:
    dest = root / "runtime" / "follow" / f"{request['job_id']}.out.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = str(request.get("expected_marker") or "")
    reply = REPLIES[request["kind"]]
    dest.write_text(reply + (f"\n{marker}\n" if marker else ""), encoding="utf-8")
    return {"status": "ok", "adapter": request["adapter"], "model": request.get("model"), "output": dest.relative_to(root).as_posix()}


class RoleConfigTests(unittest.TestCase):
    def test_missing_roles_derive_from_advisor_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps({"advisor": {"adapter": "grok"}, "executor": {"adapter": "claude-code"}}),
                encoding="utf-8",
            )
            config = load_config(root)
            roles = resolve_roles(config)
            self.assertEqual(roles["planner"]["agent"], "grok")
            self.assertEqual(roles["reviewer"]["agent"], "grok")
            self.assertEqual(roles["implementer"]["agent"], "claude-code")
            self.assertEqual(roles["qa"]["agent"], "none")

    def test_explicit_roles_keep_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps(
                    {
                        "advisor": {"adapter": "grok"},
                        "executor": {"adapter": "grok"},
                        "roles": {"reviewer": {"agent": "claude-code", "model": "opus"}},
                    }
                ),
                encoding="utf-8",
            )
            roles = resolve_roles(load_config(root))
            self.assertEqual(roles["reviewer"], {"agent": "claude-code", "model": "opus"})
            self.assertEqual(roles["planner"]["agent"], "grok")

    def test_unknown_role_agent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config/arthur-loop.json").write_text(
                json.dumps({"roles": {"reviewer": {"agent": "telepathy"}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(root)

    def test_parse_role_updates(self) -> None:
        updates = parse_role_updates(["reviewer=claude-code:opus", "qa=grok"])
        self.assertEqual(updates["reviewer"], {"agent": "claude-code", "model": "opus"})
        self.assertEqual(updates["qa"], {"agent": "grok", "model": ""})


class SequenceTests(unittest.TestCase):
    def test_default_sequence_skips_qa_when_unset(self) -> None:
        hops = formulate_default_loop({"advisor": {"adapter": "grok"}, "executor": {"adapter": "codex"}})["hops"]
        kinds = [hop["kind"] for hop in hops]
        self.assertEqual(
            kinds,
            ["next-plan-request", "plan", "plan-review", "implementation-handoff", "sprint-review"],
        )

    def test_qa_inserted_after_implementation(self) -> None:
        config = {
            "roles": {
                "planner": {"agent": "grok", "model": ""},
                "implementer": {"agent": "grok", "model": ""},
                "reviewer": {"agent": "claude-code", "model": "opus"},
                "qa": {"agent": "grok", "model": ""},
            }
        }
        hops = formulate_default_loop(config)["hops"]
        self.assertIn("qa-review", [hop["kind"] for hop in hops])
        self.assertEqual(next_hop_kind("implementation-handoff", "COMPLETE", config), "qa-review")
        self.assertEqual(next_hop_kind("qa-review", "QA_PASS", config), "sprint-review")

    def test_no_qa_when_none(self) -> None:
        config = {"roles": {"qa": {"agent": "none", "model": ""}}}
        self.assertEqual(next_hop_kind("implementation-handoff", "COMPLETE", config), "sprint-review")


class TransportTests(unittest.TestCase):
    def test_model_flag_is_optional_and_does_not_break_grok_order(self) -> None:
        self.assertEqual(transport_argv("grok", "pong"), ["grok", "--always-approve", "-p", "pong"])
        self.assertEqual(
            transport_argv("grok", "pong", "grok-4"),
            ["grok", "--always-approve", "--model", "grok-4", "-p", "pong"],
        )
        self.assertEqual(
            transport_argv("claude-code", "hi", "opus"),
            ["claude", "--model", "opus", "-p", "hi"],
        )


class RoleCliTests(unittest.TestCase):
    def test_roles_set_and_bare_arthur_runs_next_hop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            code, out, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--role", "planner=manual",
                    "--role", "implementer=manual",
                    "--role", "reviewer=claude-code:opus",
                    "--from-ticket", "AUTH-2",
                ]
            )
            self.assertEqual(code, 0, err)
            created = json.loads(out)
            self.assertEqual(created["roles"]["reviewer"]["agent"], "claude-code")
            self.assertEqual(created["roles"]["reviewer"]["model"], "opus")
            self.assertEqual(created["ticket"], "AUTH-2")
            self.assertEqual(created["sequence"]["hops"][0]["role"], "planner")

            code, out, err = _run(["roles", "--root", tmp, "set", "qa=grok"])
            self.assertEqual(code, 0, err)
            listed = json.loads(out)
            self.assertEqual(listed["roles"]["qa"]["agent"], "grok")

            report = follow_loop(Path(tmp), once=True, invoke=_invoke)
            self.assertEqual(report["stopped"], "advanced")
            self.assertEqual(report["steps"][0]["role"], "planner")
            self.assertEqual(report["steps"][0]["enqueued_kind"], "plan")

            code, out, err = _run(["run", "--root", tmp, "--dry-run"])
            self.assertEqual(code, 0, err)
            dry = json.loads(out)
            self.assertEqual(dry["steps"][0]["role"], "implementer")
            self.assertEqual(dry["steps"][0]["kind"], "plan")

            code, out, err = _run(["--root", tmp])
            self.assertEqual(code, 3, err)
            bare = json.loads(out)
            self.assertEqual(bare["stopped"], "needs_human")

    def test_follow_enqueues_qa_when_assigned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            create_loop(
                root,
                project_id="DEMO_APP",
                roles={
                    "planner": {"agent": "manual", "model": ""},
                    "implementer": {"agent": "manual", "model": ""},
                    "reviewer": {"agent": "manual", "model": ""},
                    "qa": {"agent": "grok", "model": ""},
                },
            )
            kinds = []
            for _ in range(5):
                report = follow_loop(root, once=True, invoke=_invoke)
                step = report["steps"][0]
                kinds.append(step.get("enqueued_kind"))
                if step.get("enqueued_kind") == "qa-review":
                    break
            self.assertIn("qa-review", kinds)
            jobs = QueueLedger(root).latest_jobs()
            qa_jobs = [job for job in jobs.values() if "QA" in (job.idempotency_key or "").upper() or True]
            self.assertTrue(any(job.idempotency_key and "qa-review" in job.idempotency_key for job in jobs.values()))


class RoleMcpAndWebTests(unittest.TestCase):
    def test_web_run_next_does_not_hold_global_write_lock_during_follow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            app = WebApp(Path(tmp))
            observed: list[bool] = []

            def fake_follow(*_args, **_kwargs):
                acquired = app._write_lock.acquire(blocking=False)
                observed.append(acquired)
                if acquired:
                    app._write_lock.release()
                return {"stopped": "idle", "steps": []}

            with patch("arthur_loop.web.follow_loop", side_effect=fake_follow):
                app.run_next({"once": True})
            self.assertEqual(observed, [True])

    def test_mcp_roles_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            dispatch_tool(root, "arthur.loop.create", {"project_id": "DEMO_APP"})
            listed = dispatch_tool(root, "arthur.roles.list")
            self.assertIn("planner", listed["structuredContent"]["roles"])
            set_roles = dispatch_tool(root, "arthur.roles.set", {"reviewer": "claude-code:opus"})
            self.assertEqual(set_roles["structuredContent"]["roles"]["reviewer"]["model"], "opus")
            ran = dispatch_tool(root, "arthur.run", {"dry_run": True})
            self.assertEqual(ran["structuredContent"]["stopped"], "dry_run")
            self.assertEqual(ran["structuredContent"]["steps"][0]["role"], "planner")

    def test_web_set_roles_and_run_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            root = Path(tmp)
            create_loop(root, project_id="DEMO_APP")
            server = make_server(root, port=0)
            token = server.arthur_app.token  # type: ignore[attr-defined]
            base = f"http://127.0.0.1:{server.server_address[1]}"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status_req = urllib.request.Request(base + "/api/status", headers={"X-Arthur-Token": token})
                with urllib.request.urlopen(status_req, timeout=5) as response:
                    status = json.load(response)
                self.assertIn("planner", status["roles"]["roles"])
                self.assertTrue(status["nextRun"]["role"])

                set_req = urllib.request.Request(
                    base + "/api/actions/set-roles",
                    data=json.dumps({"roles": {"reviewer": {"agent": "claude-code", "model": "opus"}}}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Arthur-Token": token},
                    method="POST",
                )
                with urllib.request.urlopen(set_req, timeout=5) as response:
                    saved = json.load(response)
                self.assertEqual(saved["roles"]["reviewer"]["agent"], "claude-code")

                run_req = urllib.request.Request(
                    base + "/api/actions/run-next",
                    data=json.dumps({"once": True, "dry_run": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Arthur-Token": token},
                    method="POST",
                )
                with urllib.request.urlopen(run_req, timeout=5) as response:
                    ran = json.load(response)
                self.assertEqual(ran["stopped"], "dry_run")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
