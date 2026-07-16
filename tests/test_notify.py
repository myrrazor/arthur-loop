from __future__ import annotations

import tempfile
import unittest
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
        self.assertEqual(result.detail, "dry run")


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


class WatchCliTests(unittest.TestCase):
    def test_watch_once_on_empty_root_exits_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["watch", "--root", tmp, "--once", "--no-desktop"])
        self.assertEqual(code, 0)

    def test_notify_dry_run_exits_zero(self) -> None:
        code = main(["notify", "--message", "hello from tests", "--dry-run"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
