from __future__ import annotations

from pathlib import Path
from typing import Collection


READABLE_SUFFIXES = frozenset({".md", ".jsonl", ".txt", ".log", ".json"})


def resolve_instance_file(
    root: Path,
    raw_path: str,
    *,
    suffixes: Collection[str] = READABLE_SUFFIXES,
) -> Path:
    """Resolve a readable file while refusing paths outside the instance root."""

    raw_path = raw_path.strip()
    if not raw_path:
        raise ValueError("file path is required")
    resolved_root = root.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PermissionError("path escapes the instance root") from exc
    if candidate.suffix.lower() not in suffixes:
        raise PermissionError("only loop text files can be read")
    if not candidate.is_file():
        raise FileNotFoundError(raw_path)
    return candidate


def instance_file_reference(root: Path, raw_path: str) -> str:
    """Return a normalized instance-relative reference for a safe input file."""

    return resolve_instance_file(root, raw_path).relative_to(root.resolve()).as_posix()
