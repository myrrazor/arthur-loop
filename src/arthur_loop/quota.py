from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# how the loop learns quota state, in preference order for "auto"
PROVIDERS = ("auto", "codexbar", "command", "file", "none")
DEFAULT_CODEXBAR_PROVIDER = "codex"


@dataclass(frozen=True)
class QuotaFetch:
    """One attempt to read quota: the raw codexbar-schema payload plus provenance."""

    payload: Any | None
    source: str
    warnings: list[str] = field(default_factory=list)


def quota_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return the quota section of a config, defaults applied by the caller's loader."""

    return dict(config.get("quota") or {})


def resolve_provider(config: dict[str, Any]) -> str:
    """Turn the configured provider (possibly 'auto') into a concrete one."""

    provider = quota_settings(config).get("provider", "auto")
    if provider not in PROVIDERS:
        raise ValueError(f"unknown quota provider {provider!r} — expected one of {list(PROVIDERS)}")
    if provider == "auto":
        # codexbar already handles the OAuth/keychain/web mess for many
        # subscription providers; reimplementing that is deliberately out of scope
        return "codexbar" if shutil.which("codexbar") else "none"
    return provider


def fetch_quota_payload(
    config: dict[str, Any],
    *,
    codexbar_provider: str | None = None,
    input_json: str | None = None,
    root: Path | None = None,
) -> QuotaFetch:
    """Fetch a codexbar-schema usage payload through the configured provider.

    Every provider must yield the same JSON shape codexbar prints
    (`codexbar usage --format json`), so downstream normalization stays single-path.
    Relative `quota.path` values and `quota.command` runs resolve against `root`
    (the instance), never the operator's current directory.
    """

    if input_json:
        return QuotaFetch(
            payload=json.loads(Path(input_json).read_text(encoding="utf-8")),
            source=f"file:{input_json}",
        )

    provider = resolve_provider(config)
    settings = quota_settings(config)

    if provider == "none":
        return QuotaFetch(
            payload=None,
            source="none",
            warnings=[
                "no quota source configured — install codexbar or set quota.provider "
                "to command/file (see docs/workflow-runbook.md, Quota Policy)"
            ],
        )

    if provider == "file":
        path = settings.get("path")
        if not path:
            raise ValueError("quota.provider is 'file' but quota.path is not set")
        resolved = Path(path)
        if not resolved.is_absolute() and root is not None:
            resolved = root / resolved
        return QuotaFetch(
            payload=json.loads(resolved.read_text(encoding="utf-8")),
            source=f"file:{resolved}",
        )

    if provider == "command":
        template = settings.get("command")
        if not template:
            raise ValueError("quota.provider is 'command' but quota.command is not set")
        proc = subprocess.run(
            shlex.split(template), text=True, capture_output=True, check=False, cwd=root
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            return QuotaFetch(
                payload=None,
                source="command",
                warnings=[proc.stderr.strip() or f"quota command exited {proc.returncode}"],
            )
        warnings = [f"quota command exited {proc.returncode}"] if proc.returncode else []
        return QuotaFetch(payload=json.loads(proc.stdout), source="command", warnings=warnings)

    # codexbar
    wanted = codexbar_provider or settings.get("codexbar_provider") or DEFAULT_CODEXBAR_PROVIDER
    proc = subprocess.run(
        ["codexbar", "usage", "--provider", wanted, "--source", "web", "--format", "json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return QuotaFetch(
            payload=None,
            source=f"codexbar:{wanted}",
            warnings=[proc.stderr.strip() or f"codexbar exited {proc.returncode}"],
        )
    warnings = [f"codexbar exited {proc.returncode}"] if proc.returncode else []
    return QuotaFetch(payload=json.loads(proc.stdout), source=f"codexbar:{wanted}", warnings=warnings)
