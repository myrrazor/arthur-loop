from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.status import collect_status


BASE_ARGS = [
    "--yes",
    "--advisor", "manual",
    "--executor", "manual",
    "--tracker", "none",
    "--no-governor",
]


class InitTests(unittest.TestCase):
    def test_non_interactive_init_builds_an_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--root", tmp, *BASE_ARGS])
            self.assertEqual(code, 0)

            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["advisor"]["adapter"], "manual")
            self.assertFalse(config["components"]["resource_governor"])

            for expected in [
                "queue/prompts",
                "queue/manual/pending",
                "queue/manual/done",
                "projects",
                "human-decisions/open.md",
                "runtime",
                "usage",
                "outputs",
                ".gitignore",
                "adapters/advisor/ADAPTER.md",
                "adapters/executor/ADAPTER.md",
            ]:
                self.assertTrue((root / expected).exists(), expected)

    def test_refuses_to_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ["init", "--root", tmp, *BASE_ARGS]
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 1)
            self.assertEqual(main([*args, "--force"]), 0)

    def test_chatgpt_browser_pack_ships_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(
                [
                    "init", "--root", tmp, "--yes",
                    "--advisor", "chatgpt-browser",
                    "--executor", "codex",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            root = Path(tmp)
            self.assertTrue((root / "adapters/advisor/prompts/next-plan-request.md").exists())
            self.assertTrue((root / "adapters/executor/prompts/plan-only.md").exists())

    def test_demo_seed_renders_nonempty_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["init", "--root", tmp, *BASE_ARGS, "--demo"])
            self.assertEqual(code, 0)

            snapshot = collect_status(Path(tmp))

            # SAMPLE_APP is decision-blocked, DEMO_APP's job keeps the loop live
            self.assertEqual(snapshot.tick.status, "POLL_DUE")
            self.assertEqual([job.job_id for job in snapshot.jobs], ["BQ-DEMO_APP-002"])
            self.assertEqual(len(snapshot.sessions), 2)
            self.assertEqual(snapshot.open_decisions[0]["project_id"], "SAMPLE_APP")
            by_id = {project.project_id: project for project in snapshot.projects}
            self.assertTrue(by_id["SAMPLE_APP"].blocked_by_decision)
            self.assertEqual(snapshot.quota["left_percent"], 62)


if __name__ == "__main__":
    unittest.main()
