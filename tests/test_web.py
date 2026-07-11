from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
