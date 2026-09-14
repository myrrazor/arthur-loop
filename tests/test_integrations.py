from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.integrations import (
    detect_targets,
    install_target,
    install_targets,
    parse_targets,
)


class ParseTargetTests(unittest.TestCase):
    def test_parses_and_rejects_unknown(self) -> None:
        self.assertEqual(parse_targets("claude, grok"), ["claude", "grok"])
        with self.assertRaises(ValueError):
            parse_targets("hal")


class DetectTests(unittest.TestCase):
    def test_detects_from_workspace_markers_without_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / "CLAUDE.md").write_text("# hi\n", encoding="utf-8")
            (root / ".cursor").mkdir()
            with patch("arthur_loop.integrations.shutil.which", return_value=None):
                found = {item.target: item for item in detect_targets(root, home=root / "home")}
            self.assertTrue(found["claude"].found)
            self.assertTrue(found["cursor"].found)
            self.assertFalse(found["generic"].found)


class InstallTests(unittest.TestCase):
    def test_writes_skills_slash_commands_and_mcp_for_each_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = {item.target: item for item in install_targets(root, ["claude", "codex", "cursor", "grok", "generic"])}

            self.assertTrue((root / ".claude/skills/arthur-loop/SKILL.md").is_file())
            self.assertTrue((root / ".claude/commands/arthur-loop.md").is_file())
            mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(mcp["mcpServers"]["arthur-loop"]["command"], "arthur")
            self.assertEqual(mcp["mcpServers"]["arthur-loop"]["args"], ["mcp", "serve"])

            self.assertTrue((root / ".codex/skills/arthur-loop/SKILL.md").is_file())
            self.assertIn("[mcp_servers.arthur-loop]", (root / ".codex/config.toml").read_text(encoding="utf-8"))

            cursor = json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(cursor["mcpServers"]["arthur-loop"]["args"], ["mcp", "serve"])
            self.assertTrue((root / ".cursor/skills/arthur-loop/SKILL.md").is_file())

            grok_toml = (root / ".grok/config.toml").read_text(encoding="utf-8")
            self.assertIn("portable", grok_toml)
            self.assertTrue((root / ".arthur/integrations/grok-agent-skill/SKILL.md").is_file())
            self.assertIn("arthur-loop:grok:begin", (root / "AGENTS.md").read_text(encoding="utf-8"))

            portable = json.loads((root / ".arthur/integrations/arthur-mcp.json").read_text(encoding="utf-8"))
            self.assertIn("arthur-loop", portable["mcpServers"])
            self.assertEqual(results["grok"].status, "written")

    def test_reinstall_updates_managed_block_and_preserves_house_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("# House rules\n\nDo not delete me.\n", encoding="utf-8")
            install_target(root, "codex")
            install_target(root, "codex")
            text = (root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Do not delete me.", text)
            self.assertEqual(text.count("arthur-loop:codex:begin"), 1)

    def test_json_mcp_merge_keeps_other_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cursor").mkdir()
            (root / ".cursor/mcp.json").write_text(
                json.dumps({"mcpServers": {"other": {"command": "echo"}}}) + "\n",
                encoding="utf-8",
            )
            install_target(root, "cursor")
            data = json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["other"]["command"], "echo")
            self.assertIn("arthur-loop", data["mcpServers"])

    def test_grok_init_writes_the_skill_at_the_atlas_shaped_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "grok",
                    "--preset", "solo",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            self.assertEqual(code, 0)
            skill = Path(tmp) / ".arthur/integrations/grok-agent-skill/SKILL.md"
            self.assertTrue(skill.is_file())
            self.assertFalse((Path(tmp) / ".arthur/integrations/grok-agent-skill/arthur-loop").exists())
            self.assertTrue((Path(tmp) / ".grok/config.toml").is_file())

    def test_init_installs_claude_integration_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "init", "--root", tmp, "--yes",
                    "--main-agent", "claude-code",
                    "--preset", "solo",
                    "--tracker", "none",
                    "--no-governor",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / ".claude/skills/arthur-loop/SKILL.md").is_file())
            self.assertTrue((Path(tmp) / ".claude/commands/arthur-loop.md").is_file())
            self.assertTrue((Path(tmp) / ".mcp.json").is_file())


if __name__ == "__main__":
    unittest.main()
