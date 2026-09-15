"""The advertised install path: init → integrate → MCP → create a loop."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.mcp import dispatch_tool, tool_catalog


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class InstallPathTests(unittest.TestCase):
    def test_init_integrate_mcp_and_create_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "arthur_loop.grok_client.grok_binary", return_value=None
        ):
            code, _, err = _run(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "none",
                    "--advisor", "manual", "--executor", "manual",
                    "--tracker", "none", "--no-governor",
                ]
            )
            self.assertEqual(code, 0, err)

            code, out, err = _run(
                ["integrations", "--root", tmp, "install", "--targets", "claude,codex,cursor,grok"]
            )
            self.assertEqual(code, 0, err)
            root = Path(tmp)
            self.assertTrue((root / ".claude/commands/arthur-loop.md").is_file())
            self.assertTrue((root / ".claude/skills/arthur-loop/SKILL.md").is_file())
            self.assertTrue((root / ".mcp.json").is_file())
            self.assertTrue((root / ".codex/skills/arthur-loop/SKILL.md").is_file())
            self.assertIn("mcp_servers.arthur-loop", (root / ".codex/config.toml").read_text(encoding="utf-8"))
            self.assertTrue((root / ".cursor/skills/arthur-loop/SKILL.md").is_file())
            self.assertTrue((root / ".cursor/commands/arthur-loop.md").is_file())
            self.assertTrue((root / ".cursor/mcp.json").is_file())
            self.assertTrue((root / ".grok/skills/arthur-loop/SKILL.md").is_file())
            self.assertIn("portable", (root / ".grok/config.toml").read_text(encoding="utf-8"))

            names = {tool["name"] for tool in tool_catalog("dotted")}
            for required in (
                "arthur.status",
                "arthur.tick",
                "arthur.queue.claim",
                "arthur.gate.implementation",
                "arthur.decision.list",
                "arthur.decision.open",
                "arthur.loop.create",
                "arthur.board",
                "arthur.board.open_jobs",
                "arthur.follow.run",
                "arthur.tracker.next",
                "arthur.tracker.walk",
            ):
                self.assertIn(required, names)
            portable = {tool["name"] for tool in tool_catalog("portable")}
            self.assertIn("arthur_loop_create", portable)
            self.assertTrue(all("." not in name for name in portable))

            created = dispatch_tool(
                root,
                "arthur.loop.create",
                {
                    "project_id": "SMOKE",
                    "advisor": "grok",
                    "executor": "manual",
                    "tracker": "none",
                    "goal": "install path",
                },
            )
            job = created["structuredContent"]["job"]
            self.assertEqual(job["project_id"], "SMOKE")
            self.assertTrue((root / "projects/SMOKE/state.md").is_file())
            self.assertIn("not a drag-drop", created["structuredContent"]["honest_copy"].lower())

            status = dispatch_tool(root, "arthur.status")
            self.assertIn("state", status["structuredContent"])
            gate = dispatch_tool(root, "arthur.gate.implementation", {"project_id": "SMOKE"})
            self.assertFalse(gate["structuredContent"]["go"])

            board = dispatch_tool(root, "arthur.board")
            self.assertTrue(board.get("isError"))
            self.assertIn("tracker", board["content"][0]["text"].lower())
