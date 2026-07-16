from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from importlib.metadata import version

from arthur_loop.cli import main


class CliTests(unittest.TestCase):
    def test_version_matches_package_metadata(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exit_info, redirect_stdout(output):
            main(["--version"])

        self.assertEqual(exit_info.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"arthur {version('arthur-loop')}")
