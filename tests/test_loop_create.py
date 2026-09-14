from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.loop_ops import create_loop, list_loops
from arthur_loop.web import make_server


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


class LoopCreateTests(unittest.TestCase):
    def test_cli_creates_project_job_and_honest_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            code, out, err = _run(
                [
                    "loop", "--root", tmp, "create",
                    "--project-id", "SHOP",
                    "--advisor", "grok",
                    "--executor", "claude-code",
                    "--tracker", "atlas-tasker",
                    "--goal", "Checkout should not lie",
                ]
            )
            self.assertEqual(code, 0, err)
            record = json.loads(out)
            self.assertEqual(record["advisor"], "grok")
            self.assertEqual(record["executor"], "claude-code")
            self.assertEqual(record["tracker"], "atlas-tasker")
            self.assertIn("not a drag-drop", record["honest_copy"].lower())
            self.assertEqual(record["job"]["project_id"], "SHOP")
            self.assertTrue((Path(tmp) / "projects/SHOP/state.md").is_file())
            self.assertIn("Checkout should not lie", (Path(tmp) / "projects/SHOP/state.md").read_text(encoding="utf-8"))
            self.assertTrue((Path(tmp) / "adapters/advisor/ADAPTER.md").is_file())
            self.assertIn("Grok", (Path(tmp) / "adapters/advisor/ADAPTER.md").read_text(encoding="utf-8"))

            listed = list_loops(Path(tmp))
            self.assertEqual(listed["projects"][0]["project_id"], "SHOP")

    def test_second_loop_on_same_project_bumps_job_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init(tmp)
            first = create_loop(Path(tmp), project_id="SHOP")
            second = create_loop(Path(tmp), project_id="SHOP")
            self.assertNotEqual(first["job"]["job_id"], second["job"]["job_id"])
            self.assertNotEqual(first["job"]["expected_marker"], second["job"]["expected_marker"])


class WebWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        _init(cls._tmp.name)
        cls.root = Path(cls._tmp.name)
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

    def _post(self, path: str, body: dict):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Arthur-Token": self.token},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_status_exposes_wizard_copy_and_choices(self) -> None:
        with self._get("/api/status") as response:
            payload = json.load(response)
        wizard = payload["loopWizard"]
        self.assertIn("drag-and-drop", wizard["copy"])
        self.assertIn("grok", wizard["advisors"])
        self.assertIn("atlas-tasker", wizard["trackers"])
        self.assertEqual(payload["server"]["tracker"], "none")

    def test_create_loop_action_writes_project_and_job(self) -> None:
        with self._post(
            "/api/actions/create-loop",
            {
                "project_id": "WEBAPP",
                "advisor": "manual",
                "executor": "manual",
                "tracker": "none",
                "goal": "Wizard path",
            },
        ) as response:
            record = json.load(response)
        self.assertEqual(record["job"]["project_id"], "WEBAPP")
        self.assertTrue((self.root / "projects/WEBAPP/state.md").is_file())

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/actions/create-loop", {"project_id": ""})
        self.assertEqual(ctx.exception.code, 400)

    def test_ui_has_loop_wizard_not_a_composer_claim(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "src/arthur_loop/webui/index.html").read_text(encoding="utf-8")
        js = (Path(__file__).resolve().parents[1] / "src/arthur_loop/webui/app.js").read_text(encoding="utf-8")
        self.assertIn('data-action="new-loop"', html)
        self.assertIn("openLoopWizard", js)
        self.assertIn("not a graph composer", js)
        self.assertNotIn("drag-and-drop builder", js.lower())
        self.assertNotIn("drag-drop builder", js.lower())


if __name__ == "__main__":
    unittest.main()
