from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any


ACTIONS = ("open_decision", "close_decision", "sprint_gate")

# Presets for Atlas Tasker's `tracker` CLI (https://github.com/myrrazor/atlas-tasker).
# This is the whole "integration": three command templates rendered to argv and
# run from the instance root. No MCP, no callbacks, no gate logic lives here.
# NOTE: verify these flags against the current tracker release before relying on
# them — they follow the README quickstart surface (ticket create / ticket move).
ATLAS_TEMPLATES = {
    "open_decision": (
        "tracker ticket create --project {project} --title {title} "
        "--type task --actor agent:arthur-loop --reason {reason}"
    ),
    "close_decision": "tracker ticket move {ticket_id} done --actor agent:arthur-loop --reason {reason}",
    "sprint_gate": (
        "tracker ticket create --project {project} --title {title} "
        "--type task --actor agent:arthur-loop --reason {reason}"
    ),
}

DEFAULT_VALUES = {"reason": "arthur-loop automation"}


def templates_for(config: dict[str, Any]) -> dict[str, str] | None:
    """Resolve the command templates for the configured tracker adapter."""

    tracker = config.get("tracker") or {}
    adapter = tracker.get("adapter", "none")
    if adapter == "none":
        return None
    overrides = tracker.get("command_templates") or {}
    if adapter == "atlas-tasker":
        return {**ATLAS_TEMPLATES, **overrides}
    # adapter == "command": the user brings every template
    return dict(overrides)


def render_command(template: str, values: dict[str, str]) -> list[str]:
    """Split the template shell-style, then substitute {placeholders} per token.

    Substitution happens after splitting, so values with spaces stay one argv
    token and nothing is ever interpreted by a shell.
    """

    cmd: list[str] = []
    for token in shlex.split(template):
        try:
            cmd.append(token.format(**values))
        except KeyError as exc:
            raise ValueError(
                f"template needs {{{exc.args[0]}}} but it was not provided "
                f"(have: {', '.join(sorted(values)) or 'nothing'})"
            ) from exc
    return cmd


def run_action(
    config: dict[str, Any],
    action: str,
    dry_run: bool = False,
    cwd: Path | None = None,
    **values: str,
) -> dict[str, Any]:
    """Run one tracker operation through the configured adapter.

    `cwd` should be the instance root: tracker CLIs (Atlas Tasker included)
    resolve their own workspace from the working directory, so running from
    wherever the operator happened to be would file tickets in the wrong place.
    """

    if action not in ACTIONS:
        raise ValueError(f"unknown tracker action {action!r} (valid: {ACTIONS})")

    templates = templates_for(config)
    if templates is None:
        return {"status": "skipped", "reason": "tracker adapter is none"}
    template = templates.get(action)
    if not template:
        return {"status": "skipped", "reason": f"no template configured for {action}"}

    cmd = render_command(template, {**DEFAULT_VALUES, **values})
    if dry_run:
        return {"status": "dry_run", "cmd": cmd, "cwd": str(cwd) if cwd else None}

    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False, cwd=cwd)
    except OSError as exc:
        return {"status": "error", "cmd": cmd, "cwd": str(cwd) if cwd else None, "returncode": None, "stdout": "", "stderr": str(exc)}
    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
