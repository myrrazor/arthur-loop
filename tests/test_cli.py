from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from pathlib import Path

from arthur_loop.cli import main


INIT_ARGS = [
    "init", "--yes", "--main-agent", "none", "--advisor", "manual",
    "--executor", "manual", "--tracker", "none", "--no-governor", "--no-integrations",
]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_version_matches_package_metadata(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exit_info, redirect_stdout(output):
            main(["--version"])

        self.assertEqual(exit_info.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"arthur {version('arthur-loop')}")


class CaptureSourceFileTests(unittest.TestCase):
    def test_source_file_refuses_paths_outside_the_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run([*INIT_ARGS, "--root", tmp])[0], 0)
            code, out, err = _run([
                "capture", "--root", tmp,
                "--project-id", "DEMO_APP",
                "--job-id", "BQ-DEMO_APP-001",
                "--kind", "next-plan-request",
                "--source-chat-title", "manual",
                "--source-file", "/etc/passwd",
            ])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("escapes the instance root", err)
            artifacts = Path(tmp) / "projects/DEMO_APP/artifacts"
            self.assertFalse(artifacts.exists())

    def test_source_file_reads_an_instance_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run([*INIT_ARGS, "--root", tmp])[0], 0)
            reply = Path(tmp) / "runtime/advisor-reply.md"
            reply.parent.mkdir(parents=True, exist_ok=True)
            reply.write_text("# reply\n\nsafe\n", encoding="utf-8")
            code, out, err = _run([
                "capture", "--root", tmp,
                "--project-id", "DEMO_APP",
                "--job-id", "BQ-DEMO_APP-001",
                "--kind", "next-plan-request",
                "--source-chat-title", "manual",
                "--source-file", "runtime/advisor-reply.md",
            ])
            self.assertEqual(code, 3, err)
            self.assertIn("safe", out)
            self.assertNotIn("root:x:", out)
