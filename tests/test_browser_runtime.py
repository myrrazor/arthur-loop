from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arthur_loop.browser_runtime import expected_headless_shell_path, runtime_report


class BrowserRuntimeTests(unittest.TestCase):
    def test_reports_missing_headless_shell_with_fix_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            browse = home / ".agents/skills/gstack/browse/dist/browse"
            browse.parent.mkdir(parents=True)
            browse.write_text("#!/bin/sh\n", encoding="utf-8")
            browse.chmod(0o755)

            with patch.dict(os.environ, {}, clear=True):
                report = runtime_report(home)

            self.assertFalse(report.ready)
            self.assertTrue(report.browse_binary_exists)
            self.assertFalse(report.headless_shell_exists)
            self.assertIn("playwright-core/cli.js", report.fix_hint)

    def test_unsupported_platform_degrades_to_clean_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("arthur_loop.browser_runtime.shell_subdir", return_value=None):
                report = runtime_report(home)

            self.assertFalse(report.ready)
            self.assertEqual(report.expected_headless_shell, "unsupported-platform")
            self.assertIn("another browser transport", report.fix_hint)

    def test_reports_ready_when_binary_and_shell_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            browse = home / ".agents/skills/gstack/browse/dist/browse"
            browse.parent.mkdir(parents=True)
            browse.write_text("#!/bin/sh\n", encoding="utf-8")
            browse.chmod(0o755)

            shell = expected_headless_shell_path(home)
            shell.parent.mkdir(parents=True)
            shell.write_text("#!/bin/sh\n", encoding="utf-8")
            shell.chmod(0o755)

            with patch.dict(os.environ, {}, clear=True):
                report = runtime_report(home)

            self.assertTrue(report.ready)
            self.assertEqual(report.fix_hint, "ready")


if __name__ == "__main__":
    unittest.main()
