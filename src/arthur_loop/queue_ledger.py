from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arthur_loop.filelock import exclusive, instance_lock_path


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
_RESULT_STATUSES = {"completed", "completed_with_warnings", "failed", "needs_recovery", "cancelled"}

# The queue state machine. A job may only move along these edges; everything
# else (completing work that was never submitted, claiming a finished job,
# reviving a terminal job) is rejected so the ledger cannot tell a story the
# loop never lived. `--force` on create is the one documented override.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"claimed", "submitted", "needs_recovery", "failed", "cancelled"},
    # re-claiming an already claimed job is a harmless retry after a crash
    "claimed": {"claimed", "submitted", "needs_recovery", "failed", "cancelled"},
    "submitted": {"waiting_for_chatgpt", "stopped_no_output"} | _RESULT_STATUSES,
    "waiting_for_chatgpt": {"waiting_for_chatgpt", "stopped_no_output"} | _RESULT_STATUSES,
    # retry after a silent stop re-submits with the same idempotency key
    "stopped_no_output": {"submitted", "waiting_for_chatgpt"} | _RESULT_STATUSES,
    "needs_recovery": {"queued", "failed", "cancelled"},
    "completed": set(),
    "completed_with_warnings": set(),
    "failed": set(),
    "cancelled": set(),
}
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


class IllegalTransition(ValueError):
    """Raised when a job is asked to move along an edge the state machine forbids."""


def normalize_idempotency_key(value: str | None) -> str | None:
    """Return a real key, or None when the caller omitted one.

    An explicit empty or whitespace-only key is a knife: it would bypass the
    uniqueness check (`if key:`) and let two creates fork the queue. Refuse it.
    """

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError(
            "idempotency key is empty; omit --idempotency-key or pass a non-empty key"
        )
    return stripped


def normalize_marker(value: str | None) -> str | None:
    """Treat blank markers as omitted; keep a non-empty marker intact."""

    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_transition(job_id: str, current: str, target: str) -> None:
    """Raise IllegalTransition unless current -> target is a legal queue edge."""

    validate_status(target)
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target in allowed:
        return
    if current in TERMINAL_STATUSES:
        detail = f"{current} is terminal; finished jobs never move again (create a new job)"
    else:
        detail = "allowed next: " + (", ".join(sorted(allowed)) or "none")
    raise IllegalTransition(
        f"illegal queue transition for {job_id}: {current} -> {target} ({detail})"
    )


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
    # lock holder that claimed the job — lets `recover` release the right lease
    claimed_by: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable job snapshot."""

        return asdict(self)


class QueueLedger:
    """Append-only JSONL queue state for browser jobs."""

    def __init__(self, root: Path):
        self.root = root
        self.jobs_path = root / "queue/jobs.jsonl"
        self.events_path = root / "queue/events.jsonl"

    def _exclusive(self):
        """Cross-process lock for read-modify-append sequences on the ledger."""

        return exclusive(instance_lock_path(self.root, "queue-ledger"))

    def record_job(self, job: QueueJob) -> None:
        """Append a new or updated job snapshot and a matching event."""

        validate_status(job.status)
        append_jsonl(self.jobs_path, job.to_record())
        self.append_event(job.job_id, "job_snapshot", {"status": job.status})

    def create_job(self, job: QueueJob, *, force: bool = False) -> QueueJob:
        """Add a new job, refusing duplicate ids and reused idempotency keys.

        `force` re-queues an existing job id with a fresh snapshot (the
        documented escape hatch). A reused idempotency key on a *different* job
        id is always a conflict: the key exists so retries cannot fork the queue.
        """

        job.idempotency_key = normalize_idempotency_key(job.idempotency_key)
        job.expected_marker = normalize_marker(job.expected_marker)

        with self._exclusive():
            jobs = self.latest_jobs()
            if job.idempotency_key:
                for other in jobs.values():
                    if other.job_id != job.job_id and other.idempotency_key == job.idempotency_key:
                        raise ValueError(
                            f"idempotency key {job.idempotency_key!r} is already used by "
                            f"{other.job_id} ({other.status}); reuse the existing job or pick a new key"
                        )
            if job.expected_marker:
                for other in jobs.values():
                    if other.job_id != job.job_id and other.expected_marker == job.expected_marker:
                        raise ValueError(
                            f"expected marker {job.expected_marker!r} is already used by "
                            f"{other.job_id}; reuse that job or pick a unique marker"
                        )
            if job.job_id in jobs and not force:
                existing = jobs[job.job_id]
                raise ValueError(
                    f"queue job {job.job_id} already exists ({existing.status}); "
                    "use --force to append a fresh queued snapshot"
                )
            self.record_job(job)
            if job.job_id in jobs:
                self.append_event(job.job_id, "job_requeued_by_force", {"previous_status": jobs[job.job_id].status})
        return job

    def link_artifact(self, job_id: str, artifact_path: str) -> bool:
        """Attach a saved artifact path to a job snapshot. Returns False for unknown jobs."""

        with self._exclusive():
            job = self.latest_jobs().get(job_id)
            if job is None:
                return False
            if artifact_path not in job.output_artifact_paths:
                job.output_artifact_paths.append(artifact_path)
                self.record_job(job)
            self.append_event(job_id, "artifact_saved", {"path": artifact_path})
        return True

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

    def require_job(self, job_id: str) -> QueueJob:
        """Return the latest snapshot for a job or raise KeyError."""

        jobs = self.latest_jobs()
        if job_id not in jobs:
            raise KeyError(f"unknown queue job: {job_id}")
        return jobs[job_id]

    def check_transition(self, job_id: str, status: str) -> QueueJob:
        """Validate a transition without recording it (for pre-flight checks)."""

        job = self.require_job(job_id)
        validate_transition(job_id, job.status, status)
        return job

    def transition(
        self,
        job_id: str,
        status: str,
        *,
        now: datetime | None = None,
        error: str | None = None,
        output_artifact_paths: list[str] | None = None,
        holder: str | None = None,
    ) -> QueueJob:
        """Create a new job snapshot with an updated status."""

        with self._exclusive():
            return self._transition_locked(
                job_id,
                status,
                now=now,
                error=error,
                output_artifact_paths=output_artifact_paths,
                holder=holder,
            )

    def _transition_locked(
        self,
        job_id: str,
        status: str,
        *,
        now: datetime | None,
        error: str | None,
        output_artifact_paths: list[str] | None,
        holder: str | None,
    ) -> QueueJob:
        job = self.check_transition(job_id, status)

        now = now or utc_now()
        job.status = status
        job.last_error = error

        if status == "claimed":
            job.claimed_at = isoformat(now)
            if holder:
                job.claimed_by = holder
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

        now = now or utc_now()
        with self._exclusive():
            # validate first so an illegal poll leaves no orphan poll event behind
            self.check_transition(job_id, status)
            event_type = "poll_result" if self.first_event(job_id, "first_poll_result") else "first_poll_result"
            event_data = dict(data or {})
            event_data.update({"marker_found": marker_found, "status": status})
            self.append_event(job_id, event_type, event_data, at=now)
            return self._transition_locked(
                job_id,
                status,
                now=now,
                error=error,
                output_artifact_paths=output_artifact_paths,
                holder=None,
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
