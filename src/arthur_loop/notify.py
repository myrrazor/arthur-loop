from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
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


def unsupported_detail(platform: str | None = None) -> str:
    """Honest, platform-specific explanation of why no desktop notification can go out."""

    platform = platform or sys.platform
    if platform == "darwin":
        return "osascript is missing — desktop notifications need the stock macOS toolchain"
    if platform.startswith("linux"):
        return (
            "no desktop notifier found: install libnotify (`notify-send`, e.g. apt install libnotify-bin) "
            "or run `arthur watch --no-desktop` to print events instead"
        )
    return f"desktop notifications are not supported on {platform}; use `arthur watch --no-desktop`"


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
            detail=unsupported_detail(platform),
        )
    if dry_run:
        return NotifyResult(
            sent=False,
            method=command[0],
            command=command,
            detail="dry run — nothing sent; would run: " + " ".join(command),
        )

    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return NotifyResult(
            sent=False,
            method=command[0],
            command=command,
            detail=proc.stderr.strip() or f"{command[0]} exited {proc.returncode}",
        )
    return NotifyResult(sent=True, method=command[0], command=command)


def watch_state_path(root: Path) -> Path:
    """Where `arthur watch` remembers its last observation between runs."""

    return root / "runtime/watch-state.json"


def load_watch_state(root: Path) -> tuple[TickResult | None, list[str]]:
    """Return (previous tick, previous open-decision titles) or (None, []) on a first run.

    Persisting this is what makes `watch --once` cron-safe: a state that has not
    changed since the last run is not news, so it is not re-announced.
    """

    path = watch_state_path(root)
    if not path.exists():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tick = data.get("tick") or {}
        known = {key: value for key, value in tick.items() if key in TickResult.__dataclass_fields__}
        previous = TickResult(**known) if known else None
        decisions = [str(item) for item in data.get("decisions") or []]
    except (ValueError, TypeError):
        return None, []
    return previous, decisions


def save_watch_state(root: Path, tick: TickResult, decisions: list[str]) -> Path:
    """Persist the observation `watch` will diff against next time."""

    path = watch_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"tick": tick.to_record(), "decisions": decisions}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


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
