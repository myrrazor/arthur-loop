from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from arthur_loop.cli import main
from arthur_loop.grok_client import mcp_add_argv, mcp_add_command, probe, register_mcp
from arthur_loop.integrations import install_target


FAKE_GROK = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path

argv = sys.argv[1:]
cwd = Path.cwd()
if argv[:1] == ["version"]:
    print("grok 0.0-test")
    raise SystemExit(0)
if argv[:2] == ["mcp", "add"]:
    # grok mcp add --scope project arthur-loop -- arthur mcp serve --tool-name-style portable
    grok = cwd / ".grok"
    grok.mkdir(parents=True, exist_ok=True)
    (grok / "config.toml").write_text(
        '[mcp_servers.arthur-loop]\n'
        'command = "arthur"\n'
        'args = ["mcp", "serve", "--tool-name-style", "portable"]\n',
        encoding="utf-8",
    )
    print("added arthur-loop")
    raise SystemExit(0)
if argv[:2] == ["mcp", "list"]:
    print("arthur-loop")
    raise SystemExit(0)
if argv[:2] == ["mcp", "doctor"]:
    print(json.dumps({
        "servers": [{"name": "arthur-loop", "trusted": True, "tools": ["arthur_status", "arthur_loop_create", "arthur_follow_run"]}]
    }))
    raise SystemExit(0)
if argv[:1] == ["inspect"]:
    print(json.dumps({
        "mcpServers": [{"name": "arthur-loop", "tools": ["arthur_status", "arthur_loop_create", "arthur_follow_run"]}]
    }))
    raise SystemExit(0)
if argv[:1] == ["-p"]:
    print("MCP tools: arthur_status arthur_loop_create arthur_follow_run")
    print("called arthur_status: ok")
    raise SystemExit(0)
print("unexpected", argv, file=sys.stderr)
raise SystemExit(2)
'''


class GrokLoadTests(unittest.TestCase):
    def test_mcp_add_argv_is_atlas_shaped_and_portable(self) -> None:
        argv = mcp_add_argv()
        self.assertEqual(argv[:4], ["mcp", "add", "--scope", "project"])
        self.assertIn("arthur-loop", argv)
        self.assertIn("--", argv)
        self.assertIn("--tool-name-style", argv)
        self.assertIn("portable", argv)
        self.assertEqual(mcp_add_command()[0], "grok")

    def test_install_plus_fake_grok_trusts_mcp_and_probe_sees_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "instance"
            bin_dir = Path(tmp) / "bin"
            root.mkdir()
            bin_dir.mkdir()
            grok = bin_dir / "grok"
            grok.write_text(FAKE_GROK, encoding="utf-8")
            grok.chmod(grok.stat().st_mode | stat.S_IEXEC)
            env_path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            old = os.environ.get("PATH")
            os.environ["PATH"] = env_path
            try:
                result = install_target(root, "grok")
                self.assertTrue((root / ".grok/skills/arthur-loop/SKILL.md").is_file())
                self.assertEqual(result.status, "trusted")
                self.assertTrue(any("trusted" in note.lower() for note in result.notes))
                native = register_mcp(root)
                self.assertTrue(native["trusted"])
                report = probe(root, live=True)
                self.assertTrue(report["skill_present"])
                self.assertTrue(report["trusted"])
                self.assertTrue(report["probes"]["mcp_list"]["mentions_arthur"])
                self.assertTrue(report["probes"]["inspect"]["mentions_arthur"])
                self.assertTrue(report["probes"]["prompt"]["mentions_arthur_status"])
            finally:
                if old is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old

    def test_cli_probe_without_grok_is_honest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "init", "--root", tmp, "--yes", "--main-agent", "none",
                    "--advisor", "manual", "--executor", "manual", "--tracker", "none",
                    "--no-governor", "--no-integrations",
                ]
            )
            self.assertEqual(code, 0)
            install_target(Path(tmp), "grok")
            from io import StringIO
            from contextlib import redirect_stdout

            out = StringIO()
            with redirect_stdout(out):
                code = main(["integrations", "--root", tmp, "probe", "--target", "grok"])
            self.assertEqual(code, 0)
            report = json.loads(out.getvalue())
            self.assertTrue(report["skill_present"])
            self.assertIn(".grok/skills/arthur-loop", report["skill_path"])
            if not report.get("grok_binary"):
                self.assertIn("not on PATH", report["note"])


if __name__ == "__main__":
    unittest.main()
