from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from arthur_loop.cli import build_parser, main


INIT_ARGS = [
    "--yes", "--main-agent", "none", "--advisor", "manual", "--executor", "manual",
    "--tracker", "none", "--no-governor", "--no-integrations",
]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


@contextmanager
def _cwd(path: Path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class RootPlacementTests(unittest.TestCase):
    """`--root` must mean the same thing wherever it lands on the command line."""

    def test_root_before_and_after_the_subcommand_resolve_identically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parser = build_parser()
            variants = [
                ["--root", tmp, "status", "set", "--session-id", "s", "--role", "r", "--activity", "a"],
                ["status", "--root", tmp, "set", "--session-id", "s", "--role", "r", "--activity", "a"],
                ["status", "set", "--root", tmp, "--session-id", "s", "--role", "r", "--activity", "a"],
                ["--root", tmp, "queue", "create", "--job-id", "j", "--project-id", "p", "--target-chat-title", "t", "--target-chat-url", "u"],
                ["queue", "--root", tmp, "create", "--job-id", "j", "--project-id", "p", "--target-chat-title", "t", "--target-chat-url", "u"],
                ["queue", "create", "--job-id", "j", "--project-id", "p", "--target-chat-title", "t", "--target-chat-url", "u", "--root", tmp],
                ["--root", tmp, "init", "--yes"],
                ["init", "--root", tmp, "--yes"],
                ["web", "--root", tmp, "--no-open"],
                ["--root", tmp, "web", "--no-open"],
            ]
            for argv in variants:
                args = parser.parse_args(argv)
                self.assertEqual(args.root, tmp, argv)

    def test_status_set_and_clear_write_into_the_given_root_not_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as instance, tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(_run(["init", "--root", instance, *INIT_ARGS])[0], 0)
            with _cwd(Path(elsewhere)):
                code, out, _ = _run(["status", "--root", instance, "set", "--session-id", "loop",
                                     "--role", "project-loop", "--activity", "planning"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["root"], str(Path(instance).resolve()))
                code, out, _ = _run(["status", "--root", instance, "clear", "--session-id", "loop"])
                self.assertEqual(code, 0)
                self.assertTrue(json.loads(out)["cleared"])

            self.assertTrue((Path(instance) / "runtime/sessions.jsonl").exists())
            self.assertFalse((Path(elsewhere) / "runtime").exists(), "nothing may leak into the cwd")

    def test_queue_create_accepts_root_after_the_action(self) -> None:
        with tempfile.TemporaryDirectory() as instance, tempfile.TemporaryDirectory() as elsewhere:
            with _cwd(Path(elsewhere)):
                code, _, err = _run([
                    "queue", "create", "--job-id", "BQ-R-001", "--project-id", "R",
                    "--target-chat-title", "t", "--target-chat-url", "manual", "--root", instance,
                ])
            self.assertEqual(code, 0, err)
            self.assertTrue((Path(instance) / "queue/jobs.jsonl").exists())
            self.assertFalse((Path(elsewhere) / "queue").exists())

    def test_default_root_is_the_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as instance:
            with _cwd(Path(instance)):
                self.assertEqual(_run(["init", *INIT_ARGS])[0], 0)
                code, out, _ = _run(["status", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["root"], str(Path(instance).resolve()))


class NonInstanceTests(unittest.TestCase):
    def test_dashboard_commands_refuse_a_random_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (
                ["status", "--root", tmp],
                ["status", "--root", tmp, "--json"],
                ["tick", "--root", tmp, "--dry-run"],
                ["decision", "--root", tmp, "list"],
                ["gate", "--root", tmp, "implementation", "--project-id", "X"],
            ):
                code, out, err = _run(argv)
                self.assertEqual(code, 2, argv)
                self.assertEqual(out, "", argv)
                self.assertIn("not an Arthur Loop instance", err, argv)
                self.assertIn("arthur init", err, argv)

    def test_a_queue_alone_counts_as_an_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run(["queue", "--root", tmp, "create", "--job-id", "BQ-R-001", "--project-id", "R",
                  "--target-chat-title", "t", "--target-chat-url", "manual"])
            code, out, _ = _run(["status", "--root", tmp, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["state"], "POLL_DUE")


class HelpTests(unittest.TestCase):
    def test_top_level_help_lists_every_subcommand_and_the_exit_codes(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(out):
            main(["--help"])
        text = out.getvalue()
        for name in ("init", "agents", "queue", "tick", "status", "decision", "gate", "lock",
                     "notify", "watch", "tracker", "web", "capture", "usage"):
            self.assertIn(f"    {name} ", text.replace("\n", "\n "), name)
        self.assertIn("--root", text)
        self.assertIn("Exit codes", text)

    def test_capture_help_offers_only_the_known_kinds_and_init_help_documents_yes(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(out):
            main(["capture", "--help"])
        for kind in ("next-plan-request", "plan", "plan-review", "implementation-handoff", "sprint-review"):
            self.assertIn(kind, out.getvalue())

        out = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(out):
            main(["init", "--help"])
        self.assertIn("--yes", out.getvalue())
        self.assertNotIn("api-model", out.getvalue())


if __name__ == "__main__":
    unittest.main()
