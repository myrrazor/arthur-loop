from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_FIRST_POLL_MINUTES = 1
DEFAULT_POLL_MINUTES = 5
VALID_STATUSES = {
    "queued",
    "claimed",
    "submitted",
    "waiting_for_chatgpt",
    "stopped_no_output",
    "needs_recovery",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
}
TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}
UTC = timezone.utc


def utc_now() -> datetime:
    """Return the current UTC time with timezone info."""

    return datetime.now(UTC)


def isoformat(dt: datetime | None = None) -> str:
    """Serialize a datetime in a compact UTC format."""

    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records, ignoring blank lines."""

    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"bad JSONL in {path}:{line_no}: {exc}") from exc
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON record to a JSONL file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
        handle.write("\n")


def next_poll_at(
    now: datetime | None = None,
    *,
    first: bool = False,
    first_poll_minutes: int = DEFAULT_FIRST_POLL_MINUTES,
    poll_minutes: int = DEFAULT_POLL_MINUTES,
) -> str:
    """Return the next poll time: first check quickly, then use the 5-minute cadence."""

    now = now or utc_now()
    delay = first_poll_minutes if first else poll_minutes
    return isoformat(now + timedelta(minutes=delay))


def parse_ledger_time(value: str | None) -> datetime | None:
    """Parse an Arthur Loop ledger timestamp."""

    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_status(status: str) -> None:
    """Reject new queue statuses that the runbook and code do not understand."""

    if status not in VALID_STATUSES:
        allowed = ", ".join(sorted(VALID_STATUSES))
        raise ValueError(f"unknown queue status {status!r}; expected one of: {allowed}")


@dataclass
class QueueJob:
    """A browser queue job snapshot stored in the append-only ledger."""

    job_id: str
    project_id: str
    target_chat_title: str
    target_chat_url: str
    prompt_path: str | None = None
    expected_marker: str | None = None
    status: str = "queued"
    priority: int = 100
    created_at: str = field(default_factory=isoformat)
    claimed_at: str | None = None
    submitted_at: str | None = None
    next_poll_at: str | None = None
    completed_at: str | None = None
    attempt_count: int = 0
    last_error: str | None = None
    output_artifact_paths: list[str] = field(default_factory=list)
    idempotency_key: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable job snapshot."""

        return asdict(self)


class QueueLedger:
    """Append-only JSONL queue state for browser jobs."""

    def __init__(self, root: Path):
        self.root = root
        self.jobs_path = root / "queue/jobs.jsonl"
        self.events_path = root / "queue/events.jsonl"

    def record_job(self, job: QueueJob) -> None:
        """Append a new or updated job snapshot and a matching event."""

        validate_status(job.status)
        append_jsonl(self.jobs_path, job.to_record())
        self.append_event(job.job_id, "job_snapshot", {"status": job.status})

    def append_event(
        self,
        job_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        at: datetime | str | None = None,
    ) -> None:
        """Append a queue event for audit and recovery."""

        event_at = at if isinstance(at, str) else isoformat(at)
        append_jsonl(
            self.events_path,
            {
                "at": event_at,
                "job_id": job_id,
                "event_type": event_type,
                "data": data or {},
            },
        )

    def latest_jobs(self) -> dict[str, QueueJob]:
        """Fold job snapshots by job id and return the latest state for each job."""

        latest: dict[str, QueueJob] = {}
        for record in read_jsonl(self.jobs_path):
            latest[record["job_id"]] = QueueJob(**record)
        return latest

    def first_event(self, job_id: str, event_type: str) -> dict[str, Any] | None:
        """Return the first matching event for a job."""

        for event in read_jsonl(self.events_path):
            if event.get("job_id") == job_id and event.get("event_type") == event_type:
                return event
        return None

    def transition(
        self,
        job_id: str,
        status: str,
        *,
        now: datetime | None = None,
        error: str | None = None,
        output_artifact_paths: list[str] | None = None,
    ) -> QueueJob:
        """Create a new job snapshot with an updated status."""

        validate_status(status)
        jobs = self.latest_jobs()
        if job_id not in jobs:
            raise KeyError(f"unknown queue job: {job_id}")

        now = now or utc_now()
        job = jobs[job_id]
        job.status = status
        job.last_error = error

        if status == "claimed":
            job.claimed_at = isoformat(now)
        elif status == "submitted":
            job.submitted_at = isoformat(now)
            job.attempt_count += 1
            job.next_poll_at = next_poll_at(now, first=True)
        elif status == "waiting_for_chatgpt":
            job.next_poll_at = next_poll_at(now)
        elif status == "stopped_no_output":
            job.next_poll_at = isoformat(now + timedelta(minutes=DEFAULT_POLL_MINUTES))
        elif status == "needs_recovery":
            # parked for a human/agent decision — must not look pollable
            job.next_poll_at = None
        elif status in TERMINAL_STATUSES:
            job.completed_at = isoformat(now)
            job.next_poll_at = None

        if output_artifact_paths is not None:
            job.output_artifact_paths = output_artifact_paths

        self.record_job(job)
        self.append_event(job_id, "transition", {"status": status, "error": error}, at=now)
        if status == "submitted":
            self.append_event(
                job_id,
                "attempt_submitted",
                {
                    "attempt": job.attempt_count,
                    "next_poll_at": job.next_poll_at,
                    "prompt_path": job.prompt_path,
                    "status": status,
                },
                at=now,
            )
        return job

    def record_poll_result(
        self,
        job_id: str,
        *,
        marker_found: bool,
        status: str,
        now: datetime | None = None,
        data: dict[str, Any] | None = None,
        error: str | None = None,
        output_artifact_paths: list[str] | None = None,
    ) -> QueueJob:
        """Record a browser poll result and transition the queue job."""

        validate_status(status)
        now = now or utc_now()
        event_type = "poll_result" if self.first_event(job_id, "first_poll_result") else "first_poll_result"
        event_data = dict(data or {})
        event_data.update({"marker_found": marker_found, "status": status})
        self.append_event(job_id, event_type, event_data, at=now)
        return self.transition(
            job_id,
            status,
            now=now,
            error=error,
            output_artifact_paths=output_artifact_paths,
        )

    def due_jobs(self, now: datetime | None = None) -> list[QueueJob]:
        """Return jobs that are ready to submit or poll."""

        now = now or utc_now()
        due: list[QueueJob] = []
        for job in self.latest_jobs().values():
            if job.status == "queued":
                due.append(job)
                continue
            if not job.next_poll_at or job.status in TERMINAL_STATUSES:
                continue
            poll_time = parse_ledger_time(job.next_poll_at)
            if not poll_time:
                continue
            if poll_time <= now:
                due.append(job)
        return sorted(due, key=lambda job: (job.priority, job.created_at, job.job_id))

    def stale_jobs(
        self,
        now: datetime | None = None,
        *,
        stale_after_minutes: int = 30,
    ) -> list[QueueJob]:
        """Return active jobs that appear abandoned by their owner."""

        now = now or utc_now()
        cutoff = now - timedelta(minutes=stale_after_minutes)
        stale: list[QueueJob] = []
        for job in self.latest_jobs().values():
            if job.status == "queued" or job.status in TERMINAL_STATUSES:
                continue
            timestamps = [
                parse_ledger_time(job.claimed_at),
                parse_ledger_time(job.submitted_at),
                parse_ledger_time(job.next_poll_at),
            ]
            newest = max((item for item in timestamps if item is not None), default=None)
            if newest and newest < cutoff:
                stale.append(job)
        return sorted(stale, key=lambda job: (job.priority, job.created_at, job.job_id))
