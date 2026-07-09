from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from arthur_loop.tick import TickResult


# states worth interrupting a human for; WAIT transitions stay silent
ALERT_STATES = {
    "POLL_DUE",
    "BLOCKED_BY_BROWSER_LOCK",
    "BLOCKED_BY_QUOTA",
    "HUMAN_INPUT_REQUIRED",
}

STATE_MESSAGES = {
    "POLL_DUE": "Queue work is ready — a job needs submitting or polling.",
    "BLOCKED_BY_BROWSER_LOCK": "Due work is waiting on the browser lock.",
    "BLOCKED_BY_QUOTA": "Quota is at or below reserve — the loop is checkpointing.",
    "HUMAN_INPUT_REQUIRED": "A human decision is the only thing moving the loop forward.",
}


@dataclass(frozen=True)
class NotifyResult:
    """What happened when we tried to reach the human's desktop."""

    sent: bool
    method: str
    command: list[str] | None = None
    detail: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "method": self.method,
            "command": self.command,
            "detail": self.detail,
        }


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def notification_command(title: str, message: str, platform: str | None = None) -> list[str] | None:
    """Build the desktop-notification argv for this platform, or None if unsupported."""

    platform = platform or sys.platform
    if platform == "darwin":
        script = (
            f'display notification "{_escape_applescript(message)}" '
            f'with title "{_escape_applescript(title)}"'
        )
        return ["osascript", "-e", script]
    if platform.startswith("linux"):
        if shutil.which("notify-send"):
            return ["notify-send", "--app-name=Arthur Loop", title, message]
        return None
    return None


def send_notification(
    title: str,
    message: str,
    *,
    dry_run: bool = False,
    platform: str | None = None,
) -> NotifyResult:
    """Fire a desktop notification (macOS osascript / Linux notify-send)."""

    command = notification_command(title, message, platform)
    if command is None:
        return NotifyResult(
            sent=False,
            method="unsupported",
            detail="no desktop notifier found (macOS needs osascript; Linux needs notify-send)",
        )
    if dry_run:
        return NotifyResult(sent=False, method=command[0], command=command, detail="dry run")

    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return NotifyResult(
            sent=False,
            method=command[0],
            command=command,
            detail=proc.stderr.strip() or f"{command[0]} exited {proc.returncode}",
        )
    return NotifyResult(sent=True, method=command[0], command=command)


def watch_events(
    previous: TickResult | None,
    current: TickResult,
    previous_decisions: list[str],
    current_decisions: list[str],
) -> list[tuple[str, str]]:
    """Diff two observations into (headline, message) notifications.

    Pure function so the alerting rules are trivially testable: notify on
    transitions into alert states (including the first observation), on newly
    opened human decisions, and on newly stale jobs — never on repeats.
    """

    events: list[tuple[str, str]] = []

    state_changed = previous is None or previous.status != current.status
    if state_changed and current.status in ALERT_STATES:
        events.append((current.status.replace("_", " "), STATE_MESSAGES.get(current.status, current.status)))

    for title in current_decisions:
        if title not in previous_decisions:
            events.append(("HUMAN DECISION", f"Open decision: {title}"))

    previous_stale = set(previous.stale_job_ids or []) if previous else set()
    for job_id in current.stale_job_ids or []:
        if job_id not in previous_stale:
            events.append(
                ("STALE JOB", f"{job_id} looks abandoned — arthur queue recover --job-id {job_id}")
            )

    return events
