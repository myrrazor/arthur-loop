from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.config import KNOWN_ADVISORS, KNOWN_EXECUTORS
from arthur_loop.status import collect_status


BASE_ARGS = [
    "--yes",
    "--main-agent", "none",
    "--advisor", "manual",
    "--executor", "manual",
    "--tracker", "none",
    "--no-governor",
    "--no-integrations",
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
                    "--no-integrations",
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
                    "--no-integrations",
                ]
            )
            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["advisor"]["adapter"], "claude-code")
            self.assertEqual(config["executor"]["adapter"], "claude-code")
            self.assertTrue((root / ".claude/skills/arthur-loop/SKILL.md").exists())

    def test_guided_preset_without_shipped_adapter_starts_manual_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
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
            self.assertEqual(config["flow"]["preset"], "guided")
            self.assertIn("no shipped adapter packs yet", out.getvalue())
            self.assertTrue((root / "GEMINI.md").exists())
            self.assertTrue((root / "agent-setup/KICKOFF.md").exists())

    def test_solo_preset_with_grok_wires_the_shipped_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "grok",
                    "--preset", "solo",
                    "--tracker", "none",
                    "--no-governor",
                    "--no-integrations",
                ]
            )
            self.assertEqual(code, 0)
            config = json.loads((Path(tmp) / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertEqual(config["advisor"]["adapter"], "grok")
            self.assertEqual(config["executor"]["adapter"], "grok")
            self.assertTrue((Path(tmp) / "adapters/advisor/ADAPTER.md").is_file())
            self.assertIn("Grok", (Path(tmp) / "adapters/advisor/ADAPTER.md").read_text(encoding="utf-8"))

    def test_solo_and_pair_refuse_agents_without_shipped_packs(self) -> None:
        # Gemini is detected, but no pack ships for it: the preset must not quietly become `manual`
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (
                ["--main-agent", "gemini", "--preset", "solo"],
                ["--main-agent", "gemini", "--preset", "pair", "--second-agent", "codex"],
                ["--main-agent", "codex", "--preset", "pair", "--second-agent", "gemini"],
                ["--main-agent", "codex", "--preset", "pair"],
            ):
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    code = main(["init", "--root", tmp, "--yes", "--tracker", "none", "--no-governor", *argv])
                self.assertEqual(code, 2, argv)
                self.assertIn("preset", err.getvalue(), argv)
                self.assertIn("--preset guided", err.getvalue(), argv)
                self.assertFalse((Path(tmp) / "config/arthur-loop.json").exists(), argv)

    def test_api_model_is_not_a_choice_anymore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            main(["init", "--root", tmp, "--yes", "--main-agent", "none", "--advisor", "api-model", "--executor", "manual"])
        self.assertEqual(ctx.exception.code, 2)

    def test_every_known_adapter_ships_a_pack_with_prompts(self) -> None:
        from importlib import resources

        adapters = resources.files("arthur_loop") / "adapters"
        for advisor in KNOWN_ADVISORS:
            pack = adapters / "advisors" / advisor
            self.assertTrue((pack / "ADAPTER.md").is_file(), advisor)
            for prompt in ("next-plan-request.md", "plan-approval-review.md", "sprint-review.md"):
                self.assertTrue((pack / "prompts" / prompt).is_file(), f"{advisor}/{prompt}")
        for executor in KNOWN_EXECUTORS:
            pack = adapters / "executors" / executor
            self.assertTrue((pack / "ADAPTER.md").is_file(), executor)
            for prompt in ("plan-only.md", "implementation-handoff.md"):
                self.assertTrue((pack / "prompts" / prompt).is_file(), f"{executor}/{prompt}")

    def test_without_a_terminal_init_fails_fast_unless_yes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with patch("arthur_loop.init_cli.sys.stdin") as stdin, redirect_stdout(io.StringIO()), redirect_stderr(err):
                stdin.isatty.return_value = False
                code = main(["init", "--root", tmp, "--demo"])
            self.assertEqual(code, 2)
            self.assertIn("--yes", err.getvalue())
            self.assertFalse((Path(tmp) / "config/arthur-loop.json").exists())

            with patch("arthur_loop.init_cli.sys.stdin") as stdin, redirect_stdout(io.StringIO()):
                stdin.isatty.return_value = False
                code = main(["init", "--root", tmp, "--yes", "--demo"])
            self.assertEqual(code, 0)

    def test_eof_mid_interview_is_a_clear_error_not_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with patch("arthur_loop.init_cli.sys.stdin") as stdin, patch(
                "arthur_loop.init_cli.input", side_effect=EOFError
            ), redirect_stdout(io.StringIO()), redirect_stderr(err):
                stdin.isatty.return_value = True
                code = main(["init", "--root", tmp, "--main-agent", "none"])
            self.assertEqual(code, 2)
            self.assertIn("--yes", err.getvalue())

    def test_demo_turns_the_governor_on_with_a_working_file_quota_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                code = main(["init", "--root", tmp, "--yes", "--main-agent", "none", "--tracker", "none", "--demo"])
            self.assertEqual(code, 0)
            root = Path(tmp)
            config = json.loads((root / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertTrue(config["components"]["resource_governor"])
            self.assertEqual(config["quota"]["provider"], "file")
            self.assertTrue((root / config["quota"]["path"]).is_file())

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["status", "--root", tmp, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["quota"]["left_percent"], 62)

            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(["usage", "snapshot", "--root", tmp, "--snapshot-id", "again"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["primary_left_percent"], 62)

    def test_no_governor_still_wins_over_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                main(["init", "--root", tmp, *BASE_ARGS, "--demo"])
            config = json.loads((Path(tmp) / "config/arthur-loop.json").read_text(encoding="utf-8"))
            self.assertFalse(config["components"]["resource_governor"])

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
                    "--no-integrations",
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
