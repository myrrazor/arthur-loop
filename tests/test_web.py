from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arthur_loop.artifact_store import save_chatgpt_artifact
from arthur_loop.browser_lock import acquire_lock, read_lock, release_lock
from arthur_loop.init_cli import seed_demo
from arthur_loop.queue_ledger import QueueLedger
from arthur_loop.web import make_server


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

    def _get(self, path: str, host: str | None = None):
        request = urllib.request.Request(self.base + path)
        if host:
            request.add_header("Host", host)
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

    def test_index_serves_html_with_session_token(self) -> None:
        with self._get("/") as response:
            html = response.read().decode("utf-8")

        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn(self.token, html)
        self.assertNotIn("__ARTHUR_TOKEN__", html)

    def test_assets_are_whitelisted(self) -> None:
        with self._get("/assets/style.css") as response:
            self.assertIn("text/css", response.headers["Content-Type"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/assets/../web.py")
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
        ledger = QueueLedger(self.root)
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

    def _status(self) -> dict:
        with urllib.request.urlopen(self.base + "/api/status", timeout=5) as response:
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
        with urllib.request.urlopen(self.base + "/api/artifacts?project=DEMO_APP", timeout=5) as response:
            items = json.load(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "sprint-review")

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/api/artifacts", timeout=5)
        self.assertEqual(ctx.exception.code, 400)

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
        self.assertEqual(ctx.exception.code, 400)
        self.assertIsNotNone(read_lock(self.root))
        release_lock(self.root, "active-manager")

        stale_moment = datetime.now(timezone.utc) - timedelta(hours=2)
        acquire_lock(self.root, "dead-manager", now=stale_moment)
        with self._post("/api/actions/break-lock", {}) as response:
            record = json.load(response)

        self.assertTrue(record["broken"])
        self.assertEqual(record["holder"], "dead-manager")
        self.assertIsNone(read_lock(self.root))


if __name__ == "__main__":
    unittest.main()
