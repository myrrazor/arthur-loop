from __future__ import annotations

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
