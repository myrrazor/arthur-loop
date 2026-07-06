from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


def parse_iso(value: str | None) -> datetime | None:
    """Parse compact UTC timestamps used by Arthur Loop ledgers."""

    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def minutes_between(start: str | None, end: str | None) -> float | None:
    """Return elapsed minutes between two ledger timestamps."""

    start_dt = parse_iso(start)
    end_dt = parse_iso(end)
    if not start_dt or not end_dt:
        return None
    return round((end_dt - start_dt).total_seconds() / 60, 3)


@dataclass(frozen=True)
class PollTimingRow:
    """Measured queue polling timing for one browser job."""

    job_id: str
    project_id: str
    submitted_at: str | None
    scheduled_first_poll_at: str | None
    first_poll_at: str | None
    completed_at: str | None
    scheduled_delay_minutes: float | None
    actual_first_poll_delay_minutes: float | None
    poll_drift_seconds: float | None
    response_latency_minutes: float | None
    status: str

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable timing row."""

        return asdict(self)


def _latest_jobs(job_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in job_records:
        latest[record["job_id"]] = record
    return latest


def _events_by_job(event_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in event_records:
        grouped.setdefault(record["job_id"], []).append(record)
    return grouped


def _first_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    for event in events:
        if event.get("event_type") == event_type:
            return event
    return None


def poll_timing_rows(
    job_records: list[dict[str, Any]],
    event_records: list[dict[str, Any]],
) -> list[PollTimingRow]:
    """Compute first-poll timing deltas from queue ledgers."""

    latest = _latest_jobs(job_records)
    events_by_job = _events_by_job(event_records)
    rows: list[PollTimingRow] = []

    for job_id, job in sorted(latest.items()):
        events = events_by_job.get(job_id, [])
        submitted_event = _first_event(events, "attempt_submitted")
        first_poll_event = _first_event(events, "first_poll_result")
        scheduled_first_poll_at = None
        if submitted_event:
            scheduled_first_poll_at = (submitted_event.get("data") or {}).get("next_poll_at")

        first_poll_at = first_poll_event.get("at") if first_poll_event else None
        drift_seconds = None
        scheduled_dt = parse_iso(scheduled_first_poll_at)
        actual_dt = parse_iso(first_poll_at)
        if scheduled_dt and actual_dt:
            drift_seconds = round((actual_dt - scheduled_dt).total_seconds(), 3)

        rows.append(
            PollTimingRow(
                job_id=job_id,
                project_id=job.get("project_id", ""),
                submitted_at=job.get("submitted_at"),
                scheduled_first_poll_at=scheduled_first_poll_at,
                first_poll_at=first_poll_at,
                completed_at=job.get("completed_at"),
                scheduled_delay_minutes=minutes_between(job.get("submitted_at"), scheduled_first_poll_at),
                actual_first_poll_delay_minutes=minutes_between(job.get("submitted_at"), first_poll_at),
                poll_drift_seconds=drift_seconds,
                response_latency_minutes=minutes_between(job.get("submitted_at"), job.get("completed_at")),
                status=job.get("status", ""),
            )
        )
    return rows


def render_poll_timing_table(rows: list[PollTimingRow]) -> str:
    """Render queue polling timing rows as markdown."""

    lines = [
        "| Job | Project | Scheduled Delay | Actual First Poll | Drift | Response Latency | Status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| _none_ | _none_ | _none_ | _none_ | _none_ | _none_ | _none_ |")
        return "\n".join(lines) + "\n"

    for row in rows:
        lines.append(
            "| {job} | {project} | {scheduled} min | {actual} min | {drift} sec | {latency} min | {status} |".format(
                job=row.job_id,
                project=row.project_id,
                scheduled=_fmt(row.scheduled_delay_minutes),
                actual=_fmt(row.actual_first_poll_delay_minutes),
                drift=_fmt(row.poll_drift_seconds),
                latency=_fmt(row.response_latency_minutes),
                status=row.status,
            )
        )
    return "\n".join(lines) + "\n"


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:g}"
