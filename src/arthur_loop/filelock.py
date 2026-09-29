from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


def flock_supported() -> bool:
    """True when this platform can take an advisory exclusive lock on a file."""

    return fcntl is not None


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on `path` for the duration of the block.

    Serializes read-modify-append sequences across processes on POSIX so two
    `arthur queue` invocations cannot both pass a uniqueness check and then
    both append. On platforms without fcntl the block runs unlocked — the
    files stay valid, only the cross-process race guarantee is lost.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def instance_lock_path(root: Path, name: str) -> Path:
    """Where the per-instance lock files live (ignored by git via runtime/)."""

    return root / "runtime" / f".{name}.lock"


# flock is per-process, so two threads in `arthur web` would both "acquire" it.
# The threading lock closes that gap; flock still covers two processes.
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(key: str) -> threading.Lock:
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


@contextmanager
def try_exclusive(path: Path) -> Iterator[bool]:
    """Try to take `path` without waiting. Yield False if another caller holds it.

    Holds both a per-path thread lock and a POSIX flock, so overlapping
    threads in one process and overlapping processes both lose the race
    instead of both entering the critical section.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(str(path.resolve()))
    if not thread_lock.acquire(blocking=False):
        yield False
        return

    acquired = False
    handle = None
    try:
        handle = open(path, "a+b")
        if fcntl is None:
            acquired = True
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if handle is not None:
            try:
                if acquired and fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        thread_lock.release()
