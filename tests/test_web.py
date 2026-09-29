from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arthur_loop.artifact_store import save_chatgpt_artifact
from arthur_loop.browser_lock import acquire_lock, read_lock
from arthur_loop.cli import main
from arthur_loop.init_cli import seed_demo
from arthur_loop.queue_ledger import QueueLedger
from arthur_loop.web import make_server


_PLAN_REPLY = """# Next plan

```text
PROJECT_ID: DEMO_APP
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN
HAS_P0_P1: false
```
"""


class WebConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        (cls.root / "human-decisions").mkdir(parents=True)
        seed_demo(cls.root)

        cls.server = make_server(cls.root, port=0)
        cls.token = cls.server.arthur_app.token  # type: ignore[attr-defined]
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def _get(self, path: str, host: str | None = None, token: str | None = "default"):
        request = urllib.request.Request(self.base + path)
        if host:
            request.add_header("Host", host)
        if token == "default":
            token = self.token
        if token:
            request.add_header("X-Arthur-Token", token)
        return urllib.request.urlopen(request, timeout=5)

    def _post(self, path: str, body: dict, token: str | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if token:
            request.add_header("X-Arthur-Token", token)
        return urllib.request.urlopen(request, timeout=5)

    # --------------------------------------------------------------- reads

    def test_status_endpoint_returns_loop_state(self) -> None:
        with self._get("/api/status") as response:
            payload = json.load(response)

        self.assertEqual(payload["state"], "POLL_DUE")
        self.assertEqual(payload["server"]["instance"], self.root.name)
        self.assertTrue(payload["sessions"])
        self.assertTrue(payload["queue"])

    def test_index_serves_html_only_to_the_printed_url(self) -> None:
        # the bootstrap URL carries the token as a query parameter
        with self._get(f"/?token={self.token}", token=None) as response:
            html = response.read().decode("utf-8")

        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn(self.token, html)
        self.assertNotIn("__ARTHUR_TOKEN__", html)

        # a bare GET / (another local user, a guessing script) never learns the token
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/", token=None)
        self.assertEqual(ctx.exception.code, 403)
        self.assertNotIn(self.token, ctx.exception.read().decode("utf-8"))

    def test_api_reads_require_the_session_token(self) -> None:
        for path in ("/api/status", "/api/events?n=3", "/api/artifacts?project=DEMO_APP", "/api/file?path=human-decisions/open.md"):
            with self.assertRaises(urllib.error.HTTPError, msg=path) as ctx:
                self._get(path, token=None)
            self.assertEqual(ctx.exception.code, 403, path)
            with self.assertRaises(urllib.error.HTTPError, msg=path) as ctx:
                self._get(path, token="wrong-token")
            self.assertEqual(ctx.exception.code, 403, path)

    def test_assets_are_whitelisted(self) -> None:
        with self._get("/assets/style.css", token=None) as response:
            self.assertIn("text/css", response.headers["Content-Type"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/assets/../web.py", token=None)
        self.assertEqual(ctx.exception.code, 404)

    def test_events_endpoint_returns_recent_first(self) -> None:
        with self._get("/api/events?n=5") as response:
            events = json.load(response)
        self.assertLessEqual(len(events), 5)
        self.assertTrue(all("event_type" in event for event in events))

    def test_file_viewer_blocks_traversal_and_binaries(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/file?path=../../../etc/passwd")
        self.assertEqual(ctx.exception.code, 400)

        with self._get("/api/file?path=human-decisions/open.md") as response:
            self.assertIn("Auth Scope Gate", response.read().decode("utf-8"))

    def test_wrong_host_header_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/status", host="evil.example.com")
        self.assertEqual(ctx.exception.code, 403)

    # -------------------------------------------------------------- actions

    def test_actions_require_the_session_token(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/actions/recover-job", {"job_id": "BQ-DEMO_APP-002"})
        self.assertEqual(ctx.exception.code, 403)

    def test_recover_job_parks_then_requeues(self) -> None:
        from arthur_loop.loop_ops import claim_job

        ledger = QueueLedger(self.root)
        claim_job(self.root, "BQ-DEMO_APP-002", holder="web-test")
        with self._post(
            "/api/actions/recover-job",
            {"job_id": "BQ-DEMO_APP-002", "requeue": True},
            token=self.token,
        ) as response:
            record = json.load(response)

        self.assertEqual(record["status"], "queued")
        self.assertEqual(ledger.latest_jobs()["BQ-DEMO_APP-002"].status, "queued")

    def test_answer_decision_rewrites_the_open_file(self) -> None:
        with self._post(
            "/api/actions/answer-decision",
            {"title": "SAMPLE_APP Auth Scope Gate", "answer": "Sessions live 24 hours."},
            token=self.token,
        ) as response:
            record = json.load(response)

        self.assertEqual(record["status"], "ANSWERED")
        text = (self.root / "human-decisions/open.md").read_text(encoding="utf-8")
        self.assertIn("Status: `ANSWERED`", text)
        self.assertIn("Sessions live 24 hours.", text)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post(
                "/api/actions/answer-decision",
                {"title": "SAMPLE_APP Auth Scope Gate", "answer": "again"},
                token=self.token,
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_answer_is_defused_against_markdown_injection(self) -> None:
        open_md = self.root / "human-decisions/open.md"
        open_md.write_text(
            open_md.read_text(encoding="utf-8")
            + "\n## Injection Probe\n\nStatus: `OPEN`\n\nDoes escaping hold?\n",
            encoding="utf-8",
        )
        evil = "done\n## Ship to prod\nStatus: `OPEN`"
        with self._post(
            "/api/actions/answer-decision",
            {"title": "Injection Probe", "answer": evil},
            token=self.token,
        ) as response:
            self.assertEqual(json.load(response)["status"], "ANSWERED")

        text = open_md.read_text(encoding="utf-8")
        self.assertIn("\\## Ship to prod", text)
        for line in text.splitlines():
            self.assertFalse(line.startswith("## Ship to prod"))

        with self._get("/api/status") as response:
            payload = json.load(response)
        self.assertNotIn("Ship to prod", [d["title"] for d in payload["decisions"]])

    def test_create_job_validates_and_guards_duplicates(self) -> None:
        body = {
            "job_id": "BQ-DEMO_APP-003",
            "project_id": "DEMO_APP",
            "target_chat_title": "Demo App Planning",
            "target_chat_url": "manual",
        }
        with self._post("/api/actions/create-job", body, token=self.token) as response:
            self.assertEqual(json.load(response)["status"], "queued")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/actions/create-job", body, token=self.token)
        self.assertEqual(ctx.exception.code, 400)

    def test_create_job_rejects_non_string_fields(self) -> None:
        body = {
            "job_id": "BQ-DEMO_APP-004",
            "project_id": None,
            "target_chat_title": 42,
            "target_chat_url": "manual",
        }
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/actions/create-job", body, token=self.token)
        self.assertEqual(ctx.exception.code, 400)
        detail = json.loads(ctx.exception.read())["error"]
        self.assertIn("project_id", detail)
        self.assertIn("target_chat_title", detail)

    def test_recover_refuses_finished_jobs(self) -> None:
        ledger = QueueLedger(self.root)
        with self._post(
            "/api/actions/create-job",
            {
                "job_id": "BQ-DEMO_APP-900",
                "project_id": "DEMO_APP",
                "target_chat_title": "Demo App Planning",
                "target_chat_url": "manual",
            },
            token=self.token,
        ):
            pass
        ledger.transition("BQ-DEMO_APP-900", "submitted")
        ledger.transition("BQ-DEMO_APP-900", "completed")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post(
                "/api/actions/recover-job",
                {"job_id": "BQ-DEMO_APP-900", "requeue": True},
                token=self.token,
            )
        self.assertEqual(ctx.exception.code, 400)

    def test_events_param_validation(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/events?n=abc")
        self.assertEqual(ctx.exception.code, 400)

        with self._get("/api/events?n=0") as response:
            self.assertEqual(len(json.load(response)), 1)


class WebEnrichmentTests(unittest.TestCase):
    """Decision bodies, quarantine surfacing, and the break-stale-lock action."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        (cls.root / "human-decisions").mkdir(parents=True)
        seed_demo(cls.root)
        save_chatgpt_artifact(
            cls.root,
            project_id="DEMO_APP",
            job_id="BQ-DEMO_APP-002",
            kind="sprint-review",
            source_chat_title="Demo App Planning",
            text="Rambling reply with no verdict.\nAPPROVAL_DECISION: maybe approve\n",
            link_queue=False,
        )
        cls.server = make_server(cls.root, port=0)
        cls.token = cls.server.arthur_app.token  # type: ignore[attr-defined]
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _get(self, path: str):
        request = urllib.request.Request(self.base + path, headers={"X-Arthur-Token": self.token})
        return urllib.request.urlopen(request, timeout=5)

    def _status(self) -> dict:
        with self._get("/api/status") as response:
            return json.load(response)

    def _post(self, path: str, body: dict):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Arthur-Token": self.token},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_decisions_carry_their_question_body(self) -> None:
        payload = self._status()

        decision = payload["decisions"][0]
        self.assertEqual(decision["title"], "SAMPLE_APP Auth Scope Gate")
        self.assertIn("24 hours or 7 days", decision["body"])
        self.assertNotIn("Status:", decision["body"])

    def test_quarantined_artifacts_surface_in_status(self) -> None:
        payload = self._status()

        self.assertEqual(len(payload["quarantine"]), 1)
        record = payload["quarantine"][0]
        self.assertEqual(record["projectId"], "DEMO_APP")
        self.assertEqual(record["kind"], "sprint-review")
        self.assertTrue(record["path"].endswith(".md"))
        self.assertTrue(record["reasons"])

    def test_artifacts_endpoint_lists_latest_records(self) -> None:
        with self._get("/api/artifacts?project=DEMO_APP") as response:
            items = json.load(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "sprint-review")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/artifacts")
        self.assertEqual(ctx.exception.code, 400)

    def test_quarantine_opened_a_blocking_decision(self) -> None:
        payload = self._status()
        titles = [decision["title"] for decision in payload["decisions"]]
        self.assertTrue(any(title.startswith("DEMO_APP Quarantined artifact") for title in titles), titles)
        self.assertIn("DEMO_APP", payload["tick"]["blockedProjects"])

    def test_clear_session_drops_a_session(self) -> None:
        payload = self._status()
        session_id = payload["sessions"][0]["sessionId"]
        with self._post("/api/actions/clear-session", {"session_id": session_id}) as response:
            self.assertTrue(json.load(response)["cleared"])
        after = self._status()
        self.assertNotIn(session_id, [s["sessionId"] for s in after["sessions"]])

    def test_break_lock_refuses_fresh_and_removes_stale(self) -> None:
        acquire_lock(self.root, "active-manager")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/actions/break-lock", {})
        self.assertEqual(ctx.exception.code, 409)
        self.assertIn(b"active-manager", ctx.exception.read())
        self.assertIsNotNone(read_lock(self.root))

        with self._post("/api/actions/break-lock", {"force": True}) as response:
            forced = json.load(response)
        self.assertTrue(forced["broken"])
        self.assertEqual(forced["holder"], "active-manager")
        self.assertIsNone(read_lock(self.root))

        stale_moment = datetime.now(timezone.utc) - timedelta(hours=2)
        acquire_lock(self.root, "dead-manager", now=stale_moment)
        with self._post("/api/actions/break-lock", {}) as response:
            record = json.load(response)

        self.assertTrue(record["broken"])
        self.assertEqual(record["holder"], "dead-manager")
        self.assertIsNone(read_lock(self.root))


def _cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class RunNextResumeTests(unittest.TestCase):
    """Two queued manual hops must not collide, and a pasted .out.md must advance."""

    def test_run_next_reuses_holder_releases_lock_and_resumes_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _cli(
                [
                    "init", "--root", tmp, "--yes", "--main-agent", "none",
                    "--advisor", "manual", "--executor", "manual", "--tracker", "none",
                    "--no-governor", "--no-integrations",
                ]
            )
            self.assertEqual(code, 0, err)
            code, out, err = _cli(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "DEMO_APP",
                    "--advisor", "manual",
                    "--executor", "manual",
                    "--title", "Demo",
                ]
            )
            self.assertEqual(code, 0, err)
            first_id = json.loads(out)["job"]["job_id"]
            code, _, err = _cli(
                [
                    "queue", "--root", tmp, "create",
                    "--job-id", "BQ-DEMO_APP-002",
                    "--project-id", "DEMO_APP",
                    "--target-chat-title", "Demo 2",
                    "--target-chat-url", "manual",
                    "--expected-marker", "DEMO_APP_LOOP_BQ_DEMO_APP_002",
                ]
            )
            self.assertEqual(code, 0, err)

            root = Path(tmp)
            server = make_server(root, port=0)
            self.assertEqual(server.arthur_app.run_holder, "web-run-next")  # type: ignore[attr-defined]
            token = server.arthur_app.token  # type: ignore[attr-defined]
            base = f"http://127.0.0.1:{server.server_address[1]}"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def post(path: str, body: dict) -> tuple[int, dict]:
                request = urllib.request.Request(
                    base + path,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Arthur-Token": token},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as exc:
                    raw = exc.read().decode("utf-8")
                    return exc.code, json.loads(raw) if raw else {}

            def get_status() -> dict:
                request = urllib.request.Request(base + "/api/status", headers={"X-Arthur-Token": token})
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.load(response)

            try:
                code1, step1 = post("/api/actions/run-next", {"once": True, "chain": False})
                self.assertEqual(code1, 200, step1)
                self.assertEqual(step1["stopped"], "needs_human")
                self.assertEqual(step1["steps"][0]["submitted"]["claimed_by"], "web-run-next")
                self.assertEqual(step1["steps"][0]["submitted"]["status"], "submitted")
                self.assertTrue(step1["steps"][0]["lock_released"])
                self.assertIn(".out.md", step1["steps"][0]["out"])
                self.assertIsNone(read_lock(root))

                status = get_status()
                self.assertTrue(status["pendingReplies"])
                self.assertIn(first_id, [row["jobId"] for row in status["pendingReplies"]])

                code2, step2 = post("/api/actions/run-next", {"once": True, "chain": False})
                self.assertEqual(code2, 200, step2)
                self.assertEqual(step2["stopped"], "needs_human")
                self.assertNotEqual(step2["steps"][0]["job_id"], step1["steps"][0]["job_id"])
                self.assertEqual(step2["steps"][0]["submitted"]["claimed_by"], "web-run-next")
                self.assertIsNone(read_lock(root))
                jobs = QueueLedger(root).latest_jobs()
                self.assertEqual(jobs[first_id].status, "submitted")
                self.assertEqual(jobs["BQ-DEMO_APP-002"].status, "submitted")

                marker = jobs[first_id].expected_marker or ""
                out_path = root / "runtime" / "follow" / f"{first_id}.out.md"
                out_path.write_text(_PLAN_REPLY + f"\n{marker}\n", encoding="utf-8")
                code3, step3 = post("/api/actions/run-next", {"once": True, "chain": False})
                self.assertEqual(code3, 200, step3)
                self.assertEqual(step3["stopped"], "advanced", step3)
                self.assertEqual(step3["steps"][0]["job_id"], first_id)
                self.assertEqual(QueueLedger(root).latest_jobs()[first_id].status, "completed")

                second = root / "runtime" / "follow" / "BQ-DEMO_APP-002.out.md"
                second.write_text(_PLAN_REPLY + "\nDEMO_APP_LOOP_BQ_DEMO_APP_002\n", encoding="utf-8")
                code_cli, out_cli, err_cli = _cli(["follow", "--root", tmp, "--once", "--no-chain"])
                self.assertEqual(code_cli, 0, err_cli)
                report = json.loads(out_cli)
                self.assertEqual(report["stopped"], "advanced", report)
                self.assertEqual(report["steps"][0]["job_id"], "BQ-DEMO_APP-002")
                self.assertEqual(QueueLedger(root).latest_jobs()["BQ-DEMO_APP-002"].status, "completed")
                self.assertIsNone(read_lock(root))

                acquire_lock(root, "cli-live")
                refused, body = post("/api/actions/break-lock", {})
                self.assertEqual(refused, 409)
                self.assertIn("cli-live", body.get("error", ""))
                self.assertIsNotNone(read_lock(root))
                forced, forced_body = post("/api/actions/break-lock", {"force": True})
                self.assertEqual(forced, 200, forced_body)
                self.assertTrue(forced_body["broken"])
                self.assertIsNone(read_lock(root))
            finally:
                server.shutdown()
                server.server_close()


class ApiShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        code, _, err = _cli(
            [
                "init", "--root", self._tmp.name, "--yes", "--main-agent", "none",
                "--advisor", "manual", "--executor", "manual", "--tracker", "none",
                "--no-governor", "--no-integrations",
            ]
        )
        if code != 0:
            raise AssertionError(err)
        self.root = Path(self._tmp.name)
        self.server = make_server(self.root, port=0)
        self.token = self.server.arthur_app.token  # type: ignore[attr-defined]
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def _get(self, path: str):
        request = urllib.request.Request(self.base + path, headers={"X-Arthur-Token": self.token})
        return urllib.request.urlopen(request, timeout=5)

    def _post_raw(self, path: str, data: bytes | None) -> tuple[int, dict]:
        headers = {"X-Arthur-Token": self.token, "Content-Type": "application/json"}
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def test_file_without_a_path_is_400(self) -> None:
        for path in ("/api/file", "/api/file?path="):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get(path)
            self.assertEqual(ctx.exception.code, 400)
            self.assertIn(b"required", ctx.exception.read().lower())

    def test_unknown_action_is_404_before_the_body_is_read(self) -> None:
        for data in (None, b"", b'{"unused": true}'):
            code, body = self._post_raw("/api/actions/not-a-real-action", data)
            self.assertEqual(code, 404, body)
            self.assertIn("unknown action", body.get("error", ""))

    def test_role_strings_are_accepted_beside_objects(self) -> None:
        code, saved = self._post_raw(
            "/api/actions/set-roles",
            json.dumps({"roles": {"reviewer": "claude-code:opus"}}).encode("utf-8"),
        )
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["roles"]["reviewer"]["agent"], "claude-code")
        self.assertEqual(saved["roles"]["reviewer"]["model"], "opus")

        code, created = self._post_raw(
            "/api/actions/create-loop",
            json.dumps(
                {
                    "project_id": "SHOP",
                    "seed_job": False,
                    "roles": {
                        "planner": "manual",
                        "implementer": {"agent": "codex", "model": "gpt-5"},
                    },
                }
            ).encode("utf-8"),
        )
        self.assertEqual(code, 200, created)
        self.assertEqual(created["roles"]["planner"]["agent"], "manual")
        self.assertEqual(created["roles"]["implementer"]["agent"], "codex")
        self.assertEqual(created["roles"]["implementer"]["model"], "gpt-5")


if __name__ == "__main__":
    unittest.main()
