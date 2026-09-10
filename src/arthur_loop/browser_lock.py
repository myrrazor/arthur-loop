from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from arthur_loop.filelock import exclusive, instance_lock_path
from arthur_loop.queue_ledger import append_jsonl, isoformat, parse_ledger_time, utc_now


LOCK_EVENT_JOB_ID = "__browser_lock__"


def _exclusive(root: Path):
    """Serialize lease read-check-write across processes (POSIX flock)."""

    return exclusive(instance_lock_path(root, "browser-lease"))


class BrowserLockError(RuntimeError):
    """Raised when browser control is already leased by a fresh holder."""


@dataclass(frozen=True)
class BrowserLock:
    """Small lease record for serializing browser control."""

    holder: str
    acquired_at: str
    stale_after: str

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable lock record."""

        return asdict(self)


def lock_path(root: Path) -> Path:
    """Return the browser lock path for a repo root."""

    return root / "runtime/browser-lock.json"


def read_lock(root: Path) -> BrowserLock | None:
    """Read the current browser lock, if one exists."""

    path = lock_path(root)
    if not path.exists():
        return None
    return BrowserLock(**json.loads(path.read_text(encoding="utf-8")))


def is_fresh(lock: BrowserLock, now: datetime | None = None) -> bool:
    """Return true when the lock has not reached its stale deadline."""

    now = now or utc_now()
    stale_after = parse_ledger_time(lock.stale_after)
    return bool(stale_after and stale_after > now)


def acquire_lock(
    root: Path,
    holder: str,
    *,
    ttl_minutes: int = 15,
    now: datetime | None = None,
) -> BrowserLock:
    """Acquire the browser lease, taking over only if the old lease is stale."""

    now = now or utc_now()
    with _exclusive(root):
        current = read_lock(root)
        if current and is_fresh(current, now) and current.holder != holder:
            raise BrowserLockError(
                f"browser lock held by {current.holder} until {current.stale_after} "
                f"(free it with `arthur queue recover` on its job, or `arthur lock break`)"
            )

        lock = BrowserLock(
            holder=holder,
            acquired_at=isoformat(now),
            stale_after=isoformat(now + timedelta(minutes=ttl_minutes)),
        )
        path = lock_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lock.to_record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

        event_type = "lock_takeover" if current and current.holder != holder else "lock_acquired"
        _append_lock_event(root, event_type, {"holder": holder, "previous_holder": current.holder if current else None}, now)
    return lock


def release_lock(root: Path, holder: str, *, now: datetime | None = None) -> bool:
    """Release the browser lease if the caller currently holds it."""

    now = now or utc_now()
    with _exclusive(root):
        current = read_lock(root)
        if not current:
            _append_lock_event(root, "lock_release_missing", {"holder": holder}, now)
            return False
        if current.holder != holder:
            _append_lock_event(
                root,
                "lock_release_ignored",
                {"holder": holder, "current_holder": current.holder},
                now,
            )
            return False

        lock_path(root).unlink(missing_ok=True)
        _append_lock_event(root, "lock_released", {"holder": holder}, now)
    return True


def break_lock(
    root: Path,
    *,
    force: bool = False,
    via: str,
    now: datetime | None = None,
) -> BrowserLock | None:
    """Remove the lease regardless of holder.

    A stale lease is always removable. A fresh one belongs to a manager that
    may still be alive, so it needs `force` — the human's declaration that the
    holder is dead (crash after claim, SIGKILL mid-poll).
    """

    now = now or utc_now()
    with _exclusive(root):
        current = read_lock(root)
        if current is None:
            return None
        if is_fresh(current, now) and not force:
            raise BrowserLockError(
                f"browser lock held by {current.holder} is still fresh until {current.stale_after}; "
                "pass --force only if you are sure that manager is dead"
            )
        lock_path(root).unlink(missing_ok=True)
        _append_lock_event(
            root,
            "lock_broken",
            {"holder": current.holder, "via": via, "was_fresh": is_fresh(current, now)},
            now,
        )
    return current


def _append_lock_event(root: Path, event_type: str, data: dict[str, Any], at: datetime) -> None:
    append_jsonl(
        root / "queue/events.jsonl",
        {
            "at": isoformat(at),
            "job_id": LOCK_EVENT_JOB_ID,
            "event_type": event_type,
            "data": data,
        },
    )
