from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from arthur_loop.browser_lock import is_fresh, read_lock
from arthur_loop.queue_ledger import QueueJob, append_jsonl, isoformat, parse_ledger_time, QueueLedger, utc_now
from arthur_loop.usage_attribution import latest_snapshots


RESERVE_PERCENT = 5.0
OPEN_STATUS_RE = re.compile(r"^\s*Status:\s*`?OPEN`?\s*$", re.IGNORECASE | re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class TickResult:
    """One scheduler-tick classification."""

    status: str
    generated_at: str
    dry_run: bool
    next_due_at: str | None = None
    due_job_id: str | None = None
    due_project_id: str | None = None
    blocked_projects: list[str] | None = None
    stale_job_ids: list[str] | None = None
    browser_lock_holder: str | None = None
    browser_lock_fresh: bool = False
    quota_left_percent: float | None = None
    warnings: list[str] | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable tick record."""

        return asdict(self)


def classify_tick(
    root: Path,
    *,
    now=None,
    dry_run: bool = True,
    reserve_percent: float = RESERVE_PERCENT,
    stale_after_minutes: int = 30,
    quota_enabled: bool = True,
) -> TickResult:
    """Classify the next Arthur Loop control-plane action."""

    now = now or utc_now()
    ledger = QueueLedger(root)
    blocked_projects = open_human_decision_projects(root)
    due_jobs = ledger.due_jobs(now)
    stale_jobs = ledger.stale_jobs(now, stale_after_minutes=stale_after_minutes)
    warnings = [f"stale job: {job.job_id}" for job in stale_jobs]

    active_due = [job for job in due_jobs if job.project_id not in blocked_projects]
    next_due_at = _next_due_at(ledger.latest_jobs().values(), blocked_projects)
    # disabled governor means quota can never block, not that quota is zero
    quota_left = _latest_quota_left(root) if quota_enabled else None
    quota_blocked = quota_left is not None and quota_left <= reserve_percent

    lock = read_lock(root)
    lock_fresh = bool(lock and is_fresh(lock, now))

    if quota_blocked and active_due:
        status = "BLOCKED_BY_QUOTA"
        due_job = active_due[0]
    elif active_due and lock_fresh:
        status = "BLOCKED_BY_BROWSER_LOCK"
        due_job = active_due[0]
    elif active_due:
        status = "POLL_DUE"
        due_job = active_due[0]
    elif blocked_projects:
        status = "HUMAN_INPUT_REQUIRED"
        due_job = None
    else:
        status = "WAIT"
        due_job = None

    return TickResult(
        status=status,
        generated_at=isoformat(now),
        dry_run=dry_run,
        next_due_at=next_due_at,
        due_job_id=due_job.job_id if due_job else None,
        due_project_id=due_job.project_id if due_job else None,
        blocked_projects=blocked_projects,
        stale_job_ids=[job.job_id for job in stale_jobs],
        browser_lock_holder=lock.holder if lock else None,
        browser_lock_fresh=lock_fresh,
        quota_left_percent=quota_left,
        warnings=warnings,
    )


def write_tick_state(root: Path, result: TickResult) -> Path:
    """Persist a small tick-state file and append queue events."""

    path = root / "runtime/tick-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    append_jsonl(root / "queue/events.jsonl", {"at": result.generated_at, "job_id": "__tick__", "event_type": "tick", "data": result.to_record()})
    if result.status == "POLL_DUE" and result.due_job_id:
        append_jsonl(root / "queue/events.jsonl", {"at": result.generated_at, "job_id": result.due_job_id, "event_type": "poll_due", "data": result.to_record()})
    if result.status == "BLOCKED_BY_QUOTA":
        append_jsonl(root / "queue/events.jsonl", {"at": result.generated_at, "job_id": "__tick__", "event_type": "quota_paused", "data": result.to_record()})
    for job_id in result.stale_job_ids or []:
        append_jsonl(root / "queue/events.jsonl", {"at": result.generated_at, "job_id": job_id, "event_type": "stale_job", "data": result.to_record()})
    return path


def render_tick_markdown(result: TickResult) -> str:
    """Render a compact human-readable tick summary."""

    lines = [
        "# Arthur Loop Tick",
        "",
        f"- Status: `{result.status}`",
        f"- Generated: `{result.generated_at}`",
    ]
    if result.due_job_id:
        lines.append(f"- Due job: `{result.due_job_id}` (`{result.due_project_id}`)")
    if result.next_due_at:
        lines.append(f"- Next due: `{result.next_due_at}`")
    if result.blocked_projects:
        lines.append(f"- Blocked projects: `{', '.join(result.blocked_projects)}`")
    if result.browser_lock_holder:
        freshness = "fresh" if result.browser_lock_fresh else "stale"
        lines.append(f"- Browser lock: `{result.browser_lock_holder}` ({freshness})")
    if result.quota_left_percent is not None:
        lines.append(f"- Quota left: `{result.quota_left_percent:g}%`")
    if result.warnings:
        lines.append(f"- Warnings: `{'; '.join(result.warnings)}`")
    return "\n".join(lines) + "\n"


def open_human_decision_projects(root: Path) -> list[str]:
    """Return project ids with open human-decision sections."""

    path = root / "human-decisions/open.md"
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    matches = list(SECTION_RE.finditer(text))
    projects: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[start:end]
        if not OPEN_STATUS_RE.search(section):
            continue
        project = match.group(1).split()[0].strip("`:")
        if project and project not in projects:
            projects.append(project)
    return projects


def _latest_quota_left(root: Path) -> float | None:
    snapshots = latest_snapshots(root)
    if not snapshots:
        return None
    latest = max(snapshots.values(), key=lambda snapshot: snapshot.captured_at)
    return latest.primary_left_percent


def _next_due_at(jobs: list[QueueJob] | Any, blocked_projects: list[str]) -> str | None:
    candidates: list[str] = []
    for job in jobs:
        if job.project_id in blocked_projects:
            continue
        if job.status == "queued":
            candidates.append(job.created_at)
        elif job.next_poll_at:
            candidates.append(job.next_poll_at)
    parsed = [(parse_ledger_time(value), value) for value in candidates]
    parsed = [(dt, value) for dt, value in parsed if dt is not None]
    if not parsed:
        return None
    return min(parsed, key=lambda item: item[0])[1]
