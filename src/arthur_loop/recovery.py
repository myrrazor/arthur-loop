from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from arthur_loop.browser_lock import BrowserLock, break_lock, read_lock
from arthur_loop.queue_ledger import TERMINAL_STATUSES, QueueJob, QueueLedger


@dataclass(frozen=True)
class RecoveryResult:
    """What `recover` did: the job's new snapshot plus any lease it freed."""

    job: QueueJob
    released_lock: BrowserLock | None
    lock_note: str

    def to_record(self) -> dict[str, Any]:
        return {
            **self.job.to_record(),
            "released_lock_holder": self.released_lock.holder if self.released_lock else None,
            "lock_note": self.lock_note,
        }


def recover_job(
    root: Path,
    job_id: str,
    *,
    requeue: bool = False,
    error: str | None = None,
    now: datetime | None = None,
    holder_hint: str | None = None,
    release_lease: bool = True,
) -> RecoveryResult:
    """Park (and optionally requeue) an abandoned job and heal its browser lease.

    A manager that dies after `claim` leaves two things behind: a job that will
    never poll, and a fresh lease that blocks every other manager with
    BLOCKED_BY_BROWSER_LOCK until the TTL expires. Recovery must fix both, so
    the lease is released when it belongs to the manager that claimed this job
    (`claimed_by`, or the caller's holder hint for ledgers written before that
    field existed). A lease held by someone else is left alone and reported.
    """

    ledger = QueueLedger(root)
    current = ledger.require_job(job_id)
    if current.status in TERMINAL_STATUSES:
        raise ValueError(f"{job_id} is {current.status}; finished jobs are not recoverable")

    note = error
    if current.last_error and error and error != current.last_error:
        note = f"{current.last_error} — {error}"
    job = current
    if current.status != "needs_recovery":
        job = ledger.transition(job_id, "needs_recovery", now=now, error=note)
    if requeue:
        job = ledger.transition(job_id, "queued", now=now)

    released: BrowserLock | None = None
    lock = read_lock(root)
    owners = _lock_owners_for(current, holder_hint)
    if lock is None:
        lock_note = "no browser lock held"
    elif not release_lease:
        lock_note = f"browser lock left in place for {lock.holder} (--keep-lock)"
    elif lock.holder in owners:
        released = break_lock(root, force=True, via=f"recover:{job_id}", now=now)
        lock_note = f"released browser lock held by {lock.holder}"
    else:
        lock_note = (
            f"browser lock held by {lock.holder}, which did not claim {job_id}; left in place "
            "(use `arthur lock break --force` if that manager is also dead)"
        )
    ledger.append_event(job_id, "job_recovered", {"requeue": requeue, "lock_note": lock_note}, at=now)
    return RecoveryResult(job=job, released_lock=released, lock_note=lock_note)


# A job that never reached `claimed` cannot own the browser lease. Recovering
# a queued/parked job with the CLI's default `--holder` must not steal a
# live manager's lock (the leftover lock-steal knife).
_LOCK_HOLDING_STATUSES = {
    "claimed",
    "submitted",
    "waiting_for_chatgpt",
    "stopped_no_output",
}


def _lock_owners_for(job: QueueJob, holder_hint: str | None) -> set[str]:
    """Who is allowed to have their lease broken for this job."""

    if job.claimed_by:
        return {job.claimed_by}
    if holder_hint and job.status in _LOCK_HOLDING_STATUSES:
        return {holder_hint}
    return set()
