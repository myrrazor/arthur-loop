from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from arthur_loop.cli import main
from arthur_loop.notify import notification_command, send_notification, watch_events
from arthur_loop.tick import TickResult


def _tick(status: str, stale: list[str] | None = None) -> TickResult:
    return TickResult(
        status=status,
        generated_at="2026-07-09T12:00:00Z",
        dry_run=True,
        stale_job_ids=stale or [],
    )


class NotificationCommandTests(unittest.TestCase):
    def test_macos_uses_osascript_with_escaped_quotes(self) -> None:
        command = notification_command('He said "go"', 'C:\\path "quoted"', platform="darwin")

        self.assertEqual(command[0], "osascript")
        self.assertIn('display notification "C:\\\\path \\"quoted\\""', command[2])
        self.assertIn('with title "He said \\"go\\""', command[2])

    def test_linux_uses_notify_send_when_available(self) -> None:
        with patch("arthur_loop.notify.shutil.which", return_value="/usr/bin/notify-send"):
            command = notification_command("t", "m", platform="linux")
        self.assertEqual(command[0], "notify-send")

    def test_linux_without_notify_send_is_unsupported(self) -> None:
        with patch("arthur_loop.notify.shutil.which", return_value=None):
            self.assertIsNone(notification_command("t", "m", platform="linux"))
        self.assertIsNone(notification_command("t", "m", platform="win32"))

    def test_dry_run_builds_but_does_not_send(self) -> None:
        result = send_notification("t", "m", dry_run=True, platform="darwin")

        self.assertFalse(result.sent)
        self.assertEqual(result.method, "osascript")
        self.assertIn("dry run", result.detail)
        self.assertIn("osascript", result.detail)

    def test_unsupported_platform_explains_itself(self) -> None:
        with patch("arthur_loop.notify.shutil.which", return_value=None):
            result = send_notification("t", "m", dry_run=True, platform="linux")

        self.assertFalse(result.sent)
        self.assertEqual(result.method, "unsupported")
        self.assertIsNone(result.command)
        self.assertIn("notify-send", result.detail)
        self.assertIn("--no-desktop", result.detail)


class WatchEventTests(unittest.TestCase):
    def test_first_observation_of_alert_state_notifies(self) -> None:
        events = watch_events(None, _tick("HUMAN_INPUT_REQUIRED"), [], [])

        self.assertEqual(len(events), 1)
        self.assertIn("HUMAN INPUT REQUIRED", events[0][0])

    def test_unchanged_state_stays_silent(self) -> None:
        previous = _tick("HUMAN_INPUT_REQUIRED")
        self.assertEqual(watch_events(previous, _tick("HUMAN_INPUT_REQUIRED"), [], []), [])

    def test_transition_to_wait_stays_silent(self) -> None:
        self.assertEqual(watch_events(_tick("POLL_DUE"), _tick("WAIT"), [], []), [])

    def test_new_decision_notifies_once(self) -> None:
        first = watch_events(_tick("WAIT"), _tick("WAIT"), [], ["APP Auth Gate"])
        again = watch_events(_tick("WAIT"), _tick("WAIT"), ["APP Auth Gate"], ["APP Auth Gate"])

        self.assertEqual(len(first), 1)
        self.assertIn("APP Auth Gate", first[0][1])
        self.assertEqual(again, [])

    def test_new_stale_job_points_at_recovery_command(self) -> None:
        events = watch_events(_tick("WAIT"), _tick("WAIT", stale=["BQ-X-001"]), [], [])

        self.assertEqual(len(events), 1)
        self.assertIn("arthur queue recover --job-id BQ-X-001", events[0][1])


INIT_ARGS = [
    "--yes", "--main-agent", "none", "--advisor", "manual", "--executor", "manual",
    "--tracker", "none", "--no-governor", "--no-integrations",
]


class WatchCliTests(unittest.TestCase):
    def test_watch_once_on_fresh_instance_exits_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["init", "--root", tmp, *INIT_ARGS]), 0)
            code = main(["watch", "--root", tmp, "--once", "--no-desktop"])
        self.assertEqual(code, 0)

    def test_watch_refuses_a_directory_that_is_not_an_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()) as err:
            code = main(["watch", "--root", tmp, "--once", "--no-desktop"])
        self.assertEqual(code, 2)
        self.assertIn("not an Arthur Loop instance", err.getvalue())

    def test_watch_once_does_not_refire_an_unchanged_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["init", "--root", tmp, *INIT_ARGS]), 0)
            main(
                [
                    "queue", "--root", tmp, "create", "--job-id", "BQ-W-001", "--project-id", "W",
                    "--target-chat-title", "W", "--target-chat-url", "manual",
                ]
            )
            first, second = io.StringIO(), io.StringIO()
            with redirect_stdout(first):
                main(["watch", "--root", tmp, "--once", "--no-desktop", "--quiet"])
            with redirect_stdout(second):
                main(["watch", "--root", tmp, "--once", "--no-desktop", "--quiet"])

        self.assertIn("POLL DUE", first.getvalue())
        self.assertEqual(second.getvalue().strip(), "", "the second cron run must stay silent")

    def test_notify_dry_run_exits_zero_when_a_notifier_exists(self) -> None:
        with patch("arthur_loop.notify.sys.platform", "darwin"), redirect_stdout(io.StringIO()):
            code = main(["notify", "--message", "hello from tests", "--dry-run"])
        self.assertEqual(code, 0)

    def test_notify_without_a_notifier_is_an_honest_error(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with patch("arthur_loop.notify.sys.platform", "linux"), patch(
            "arthur_loop.notify.shutil.which", return_value=None
        ), redirect_stdout(out), redirect_stderr(err):
            code = main(["notify", "--message", "hello", "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("notify-send", err.getvalue())
        self.assertIn('"method": "unsupported"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
