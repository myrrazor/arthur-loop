from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.status import collect_status


BASE_ARGS = [
    "--yes",
    "--main-agent", "none",
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

    def test_pair_preset_wires_reviewer_as_advisor_and_seeds_kickoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "codex",
                    "--preset", "pair",
                    "--second-agent", "claude-code",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            self.assertEqual(code, 0)

            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["executor"]["adapter"], "codex")
            self.assertEqual(config["advisor"]["adapter"], "claude-code")
            self.assertEqual(config["flow"], {"preset": "pair", "main_agent": "codex", "reviewer": "claude-code"})

            kickoff = (root / "agent-setup/KICKOFF.md").read_text(encoding="utf-8")
            self.assertIn("Codex CLI (codex)", kickoff)
            self.assertIn("preset: pair", kickoff)
            self.assertTrue((root / "agent-setup/skill/SKILL.md").exists())
            # codex reads AGENTS.md — the wizard leaves a pointer
            self.assertIn("agent-setup/skill/SKILL.md", (root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_solo_preset_with_claude_installs_project_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "claude-code",
                    "--preset", "solo",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["advisor"]["adapter"], "claude-code")
            self.assertEqual(config["executor"]["adapter"], "claude-code")
            self.assertTrue((root / ".claude/skills/arthur-loop/SKILL.md").exists())

    def test_guided_preset_without_shipped_adapter_falls_back_to_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "gemini",
                    "--preset", "guided",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["advisor"]["adapter"], "manual")
            self.assertEqual(config["executor"]["adapter"], "manual")
            self.assertTrue((root / "GEMINI.md").exists())
            self.assertTrue((root / "agent-setup/KICKOFF.md").exists())

    def test_no_kickoff_skips_agent_seeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "claude-code",
                    "--preset", "guided",
                    "--tracker", "none",
                    "--no-governor",
                    "--no-kickoff",
                ]
            )
            root = Path(tmp)
            self.assertFalse((root / "agent-setup").exists())
            self.assertFalse((root / ".claude").exists())

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
