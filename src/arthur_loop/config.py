from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arthur_loop.roles import derive_roles, merge_roles, validate_roles


# every id here has a shipped pack under src/arthur_loop/adapters/ (tests enforce it)
KNOWN_ADVISORS = {"chatgpt-browser", "claude-code", "codex", "grok", "manual"}
KNOWN_EXECUTORS = {"codex", "claude-code", "grok", "manual"}
KNOWN_TRACKERS = {"atlas-tasker", "command", "none"}

# any of these makes a directory an Arthur Loop instance; none of them means a
# dashboard would be describing an empty folder, not a loop
INSTANCE_MARKERS = ("config/arthur-loop.json", "queue", "projects", "human-decisions")

DEFAULTS: dict[str, Any] = {
    "version": 2,
    "advisor": {"adapter": "chatgpt-browser"},
    "executor": {"adapter": "codex"},
    "tracker": {"adapter": "none"},
    "components": {"resource_governor": True, "heartbeat": True},
    "quota": {"provider": "auto", "codexbar_provider": "codex"},
    "reserve_policy": {"minimum_reserve_percent": 5},
    "polling_policy": {
        "first_poll_minutes": 1,
        "steady_poll_minutes": 5,
        "max_retries_after_stopped_no_output": 1,
    },
    "projects": [],
}


def config_path(root: Path) -> Path:
    """Return the instance config path."""

    return root / "config/arthur-loop.json"


def is_instance(root: Path) -> bool:
    """True when `root` holds any Arthur Loop state."""

    return any((root / marker).exists() for marker in INSTANCE_MARKERS)


def require_instance(root: Path) -> None:
    """Raise a clear error instead of rendering a calm dashboard for a random folder."""

    if is_instance(root):
        return
    raise ValueError(
        f"{root} is not an Arthur Loop instance (no config/arthur-loop.json, queue/, projects/ or "
        "human-decisions/). Run `arthur init` there, or point at your loop with --root."
    )


def load_config(root: Path) -> dict[str, Any]:
    """Read config/arthur-loop.json merged over defaults. Missing file = pure defaults."""

    merged: dict[str, Any] = {}
    for key, value in DEFAULTS.items():
        merged[key] = dict(value) if isinstance(value, dict) else value

    data: dict[str, Any] = {}
    path = config_path(root)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key == "roles":
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

    _check(merged["advisor"].get("adapter"), KNOWN_ADVISORS, "advisor")
    _check(merged["executor"].get("adapter"), KNOWN_EXECUTORS, "executor")
    _check(merged["tracker"].get("adapter"), KNOWN_TRACKERS, "tracker")
    file_roles = data.get("roles") if isinstance(data.get("roles"), dict) else None
    merged["roles"] = merge_roles(derive_roles(merged), file_roles)
    validate_roles(merged["roles"])
    return merged


def _check(name: object, known: set[str], role: str) -> None:
    if name not in known:
        raise ValueError(f"unknown {role} adapter {name!r} — expected one of {sorted(known)}")
