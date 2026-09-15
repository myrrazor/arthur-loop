from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.grok_client import (
    folder_trust_recorded,
    grant_folder_trust,
    mcp_add_argv,
    mcp_add_command,
    parse_toml_server,
    probe,
    prompt_argv,
    register_mcp,
    trust_folder_command,
)
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
if argv[:1] == ["--trust"]:
    print("trusted")
    raise SystemExit(0)
if argv[:2] == ["--always-approve", "-p"]:
    print("MCP tools: arthur_status arthur_loop_create arthur_follow_run")
    print("called arthur_status: ok")
    raise SystemExit(0)
if argv[:1] == ["-p"]:
    print("wrong flag order: Grok 1.0.30 wants --always-approve -p", file=sys.stderr)
    raise SystemExit(2)
print("unexpected", argv, file=sys.stderr)
raise SystemExit(2)
'''


class GrokLoadTests(unittest.TestCase):
    def test_prompt_argv_is_always_approve_then_dash_p(self) -> None:
        self.assertEqual(prompt_argv("pong"), ["grok", "--always-approve", "-p", "pong"])
        self.assertEqual(trust_folder_command(), ["grok", "--trust"])
        with patch("arthur_loop.grok_client.grok_binary", return_value=None):
            deferred = grant_folder_trust(Path("."))
        self.assertEqual(deferred["status"], "deferred")
        self.assertIn("untrusted", deferred["note"].lower())

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
                with patch("arthur_loop.grok_client.grok_binary", return_value=str(grok)):
                    result = install_target(root, "grok")
                    self.assertTrue((root / ".grok/skills/arthur-loop/SKILL.md").is_file())
                    self.assertEqual(result.status, "trusted")
                    self.assertTrue(any("trusted" in note.lower() for note in result.notes))
                    native = register_mcp(root)
                    self.assertTrue(native["trusted"])
                    report = probe(root, live=True)
                self.assertTrue(report["skill_present"])
                self.assertTrue(report["trusted"])
                self.assertEqual(report["commands"]["prompt"][:3], ["grok", "--always-approve", "-p"])
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
            with patch("arthur_loop.grok_client.grok_binary", return_value=None):
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
            self.assertFalse(report.get("grok_binary"))
            self.assertIn("not on PATH", report["note"])

    def test_parse_toml_server_accepts_multiline_args(self) -> None:
        text = (
            "[mcp_servers.arthur-loop]\n"
            'command = "arthur"\n'
            "args = [\n"
            '  "mcp",\n'
            '  "serve",\n'
            '  "--tool-name-style",\n'
            '  "portable",\n'
            "]\n"
        )
        entry = parse_toml_server(text)
        self.assertEqual(entry["command"], "arthur")
        self.assertEqual(entry["args"], ["mcp", "serve", "--tool-name-style", "portable"])

    def test_probe_does_not_crash_on_multiline_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / ".grok/skills/arthur-loop"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (root / ".grok/config.toml").write_text(
                "[mcp_servers.arthur-loop]\n"
                'command = "arthur"\n'
                "args = [\n"
                '  "mcp",\n'
                '  "serve",\n'
                '  "--tool-name-style",\n'
                '  "portable",\n'
                "]\n",
                encoding="utf-8",
            )
            with patch("arthur_loop.grok_client.grok_binary", return_value=None):
                report = probe(root)
            self.assertNotIn("error", report.get("mcp_entry") or {})
            self.assertEqual(report["mcp_entry"]["command"], "arthur")
            self.assertTrue(report["config_matches"])

    def test_trust_enxio_after_grant_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "instance"
            home = Path(tmp) / "home"
            root.mkdir()
            (home / ".grok").mkdir(parents=True)

            def boom(*_args, **_kwargs):
                (home / ".grok/trusted_folders.toml").write_text(
                    f'trusted = ["{root.resolve()}"]\n', encoding="utf-8"
                )
                raise OSError(6, "No such device or address")

            with patch("arthur_loop.grok_client.grok_binary", return_value="/bin/grok"):
                with patch("arthur_loop.grok_client.Path.home", return_value=home):
                    result = grant_folder_trust(root, runner=boom)
            self.assertTrue(result["trusted_folder"])
            self.assertEqual(result["status"], "trusted_folder")
            self.assertIn("grant", result["note"].lower())
            self.assertTrue(folder_trust_recorded(root, home=home))


if __name__ == "__main__":
    unittest.main()
