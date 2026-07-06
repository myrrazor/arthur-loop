from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from arthur_loop.queue_ledger import append_jsonl, isoformat, read_jsonl

ABSOLUTE_RESET_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b.+\b\d{4}\b")

@dataclass(frozen=True)
class QuotaSnapshot:
    """Normalized quota snapshot from `codexbar usage --format json`."""

    snapshot_id: str
    captured_at: str
    provider: str
    source: str
    account_label: str | None
    plan: str | None
    primary_used_percent: float | None
    primary_left_percent: float | None
    primary_window_minutes: int | None
    primary_reset: str | None
    secondary_used_percent: float | None
    secondary_left_percent: float | None
    secondary_window_minutes: int | None
    secondary_reset: str | None
    raw: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""

        return asdict(self)


@dataclass(frozen=True)
class TaskUsageEstimate:
    """Estimated quota delta for one Arthur Loop task window."""

    task_id: str
    project_id: str
    role: str
    task_label: str
    before_snapshot_id: str
    after_snapshot_id: str
    started_at: str
    ended_at: str
    primary_delta_used_percent: float | None
    secondary_delta_used_percent: float | None
    confidence: str
    active_codex_tasks: int
    notes: list[str]

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable task usage estimate."""

        return asdict(self)


def _percent(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _left_from_used(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    return round(100.0 - used_percent, 3)


def _select_provider(payload: Any, provider: str = "codex") -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list) or not payload:
        raise ValueError("codexbar JSON must be an object or non-empty list")

    wanted = provider.lower()
    for item in payload:
        if isinstance(item, dict) and str(item.get("provider", "")).lower() == wanted:
            return item
    for item in payload:
        if isinstance(item, dict):
            return item
    raise ValueError("codexbar JSON did not contain provider objects")


def snapshot_from_codexbar_json(
    payload: Any,
    *,
    snapshot_id: str,
    captured_at: str | None = None,
    provider: str = "codex",
) -> QuotaSnapshot:
    """Normalize a codexbar provider JSON payload for durable attribution."""

    item = _select_provider(payload, provider)
    usage = item.get("usage") or {}
    dashboard = item.get("openaiDashboard") or {}

    primary = usage.get("primary") or dashboard.get("primaryLimit") or {}
    secondary = usage.get("secondary") or dashboard.get("secondaryLimit") or {}
    primary_used = _percent(primary.get("usedPercent"))
    secondary_used = _percent(secondary.get("usedPercent"))

    return QuotaSnapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at or dashboard.get("updatedAt") or usage.get("updatedAt") or isoformat(),
        provider=str(item.get("provider") or provider),
        source=str(item.get("source") or "unknown"),
        account_label=usage.get("accountEmail") or dashboard.get("signedInEmail"),
        plan=usage.get("loginMethod") or dashboard.get("accountPlan"),
        primary_used_percent=primary_used,
        primary_left_percent=_left_from_used(primary_used),
        primary_window_minutes=primary.get("windowMinutes"),
        primary_reset=primary.get("resetDescription"),
        secondary_used_percent=secondary_used,
        secondary_left_percent=_left_from_used(secondary_used),
        secondary_window_minutes=secondary.get("windowMinutes"),
        secondary_reset=secondary.get("resetDescription"),
        raw=item,
    )


def read_snapshot_file(path: Path, *, snapshot_id: str, captured_at: str | None = None) -> QuotaSnapshot:
    """Read a codexbar JSON file and normalize it."""

    return snapshot_from_codexbar_json(
        json.loads(path.read_text(encoding="utf-8")),
        snapshot_id=snapshot_id,
        captured_at=captured_at,
    )


def append_snapshot(root: Path, snapshot: QuotaSnapshot) -> None:
    """Append a quota snapshot to `usage/snapshots.jsonl`."""

    append_jsonl(root / "usage/snapshots.jsonl", snapshot.to_record())


def append_task_usage(root: Path, estimate: TaskUsageEstimate) -> None:
    """Append a task usage estimate to `usage/task-usage.jsonl`."""

    append_jsonl(root / "usage/task-usage.jsonl", estimate.to_record())


def latest_snapshots(root: Path) -> dict[str, QuotaSnapshot]:
    """Fold quota snapshots by id."""

    latest: dict[str, QuotaSnapshot] = {}
    for record in read_jsonl(root / "usage/snapshots.jsonl"):
        latest[record["snapshot_id"]] = QuotaSnapshot(**record)
    return latest


def task_usage_records(root: Path) -> list[TaskUsageEstimate]:
    """Read task usage estimate records."""

    return [TaskUsageEstimate(**record) for record in read_jsonl(root / "usage/task-usage.jsonl")]


def confidence_for_delta(
    *,
    active_codex_tasks: int,
    primary_delta_used_percent: float | None,
    background_activity_possible: bool = False,
    reset_crossed: bool = False,
) -> str:
    """Classify how trustworthy a before/after quota delta is."""

    if reset_crossed or primary_delta_used_percent is None:
        return "UNKNOWN"
    if primary_delta_used_percent < 0:
        return "UNKNOWN"
    if active_codex_tasks <= 1 and not background_activity_possible:
        return "HIGH"
    if active_codex_tasks <= 1:
        return "MEDIUM"
    if active_codex_tasks <= 3:
        return "LOW"
    return "VERY_LOW"


def estimate_task_usage(
    before: QuotaSnapshot,
    after: QuotaSnapshot,
    *,
    task_id: str,
    project_id: str,
    role: str,
    task_label: str,
    active_codex_tasks: int = 1,
    background_activity_possible: bool = False,
    notes: list[str] | None = None,
) -> TaskUsageEstimate:
    """Estimate task usage from two account-level quota snapshots."""

    primary_delta = None
    secondary_delta = None
    if before.primary_used_percent is not None and after.primary_used_percent is not None:
        primary_delta = round(after.primary_used_percent - before.primary_used_percent, 3)
    if before.secondary_used_percent is not None and after.secondary_used_percent is not None:
        secondary_delta = round(after.secondary_used_percent - before.secondary_used_percent, 3)
    reset_crossed = _reset_crossed(before, after, primary_delta)

    task_notes = list(notes or [])
    if reset_crossed:
        task_notes.append("primary quota reset likely crossed during the measurement window")
    elif before.primary_reset != after.primary_reset:
        task_notes.append("primary reset description changed but no reset crossing was confirmed")
    if active_codex_tasks > 1:
        task_notes.append(f"{active_codex_tasks} Codex tasks were active, so attribution is shared")
    if background_activity_possible:
        task_notes.append("background Codex activity may have contributed to the delta")

    return TaskUsageEstimate(
        task_id=task_id,
        project_id=project_id,
        role=role,
        task_label=task_label,
        before_snapshot_id=before.snapshot_id,
        after_snapshot_id=after.snapshot_id,
        started_at=before.captured_at,
        ended_at=after.captured_at,
        primary_delta_used_percent=primary_delta,
        secondary_delta_used_percent=secondary_delta,
        confidence=confidence_for_delta(
            active_codex_tasks=active_codex_tasks,
            primary_delta_used_percent=primary_delta,
            background_activity_possible=background_activity_possible,
            reset_crossed=reset_crossed,
        ),
        active_codex_tasks=active_codex_tasks,
        notes=task_notes,
    )


def _reset_crossed(before: QuotaSnapshot, after: QuotaSnapshot, primary_delta: float | None) -> bool:
    if primary_delta is not None and primary_delta < 0:
        return True

    before_reset = _parse_absolute_reset(before.primary_reset, before.captured_at)
    after_reset = _parse_absolute_reset(after.primary_reset, after.captured_at)
    if before_reset and after_reset and after_reset > before_reset:
        return True
    return False


def _parse_absolute_reset(reset_description: str | None, captured_at: str) -> datetime | None:
    if not reset_description or not ABSOLUTE_RESET_RE.search(reset_description):
        return None
    value = reset_description.removeprefix("Resets ").strip()
    try:
        parsed = datetime.strptime(value, "%b %d, %Y %I:%M %p")
    except ValueError:
        return None

    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if captured.tzinfo is not None:
        return parsed.replace(tzinfo=captured.tzinfo)
    return parsed


def render_usage_dashboard(estimates: list[TaskUsageEstimate]) -> str:
    """Render task usage estimates as a markdown table."""

    lines = [
        "# Arthur Loop Usage Dashboard",
        "",
        "| Project | Session Role | Task | Delta | Confidence | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    if not estimates:
        lines.append("| _none_ | _none_ | _none_ | _none_ | _none_ | _No task usage records yet._ |")
        return "\n".join(lines) + "\n"

    for estimate in estimates:
        delta = (
            "Unknown"
            if estimate.primary_delta_used_percent is None
            else f"{estimate.primary_delta_used_percent:+.3g}%"
        )
        notes = "; ".join(estimate.notes) if estimate.notes else ""
        lines.append(
            "| {project} | {role} | {task} | {delta} | {confidence} | {notes} |".format(
                project=estimate.project_id,
                role=estimate.role,
                task=estimate.task_label,
                delta=delta,
                confidence=estimate.confidence,
                notes=notes,
            )
        )
    return "\n".join(lines) + "\n"
