from __future__ import annotations

import json
from pathlib import Path
from typing import Any


KNOWN_ADVISORS = {"chatgpt-browser", "claude-code", "api-model", "manual"}
KNOWN_EXECUTORS = {"codex", "claude-code", "manual"}
KNOWN_TRACKERS = {"atlas-tasker", "command", "none"}

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


def load_config(root: Path) -> dict[str, Any]:
    """Read config/arthur-loop.json merged over defaults. Missing file = pure defaults."""

    merged: dict[str, Any] = {}
    for key, value in DEFAULTS.items():
        merged[key] = dict(value) if isinstance(value, dict) else value

    path = config_path(root)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

    _check(merged["advisor"].get("adapter"), KNOWN_ADVISORS, "advisor")
    _check(merged["executor"].get("adapter"), KNOWN_EXECUTORS, "executor")
    _check(merged["tracker"].get("adapter"), KNOWN_TRACKERS, "tracker")
    return merged


def _check(name: object, known: set[str], role: str) -> None:
    if name not in known:
        raise ValueError(f"unknown {role} adapter {name!r} — expected one of {sorted(known)}")
