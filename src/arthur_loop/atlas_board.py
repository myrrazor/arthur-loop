"""Atlas Tasker board read path — JSON first, argv templates as fallback."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from arthur_loop.config import load_config
from arthur_loop.loop_ops import _next_job_id, _slug, seed_first_prompt
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.tracker import run_action as tracker_run_action


# Atlas board columns that mean "this ticket is work an agent can open a job from"
READY_STATUSES = ("ready", "in_progress")
TERMINAL_STATUSES = ("done", "canceled", "cancelled")

Runner = Callable[..., subprocess.CompletedProcess]


def tracker_binary() -> str | None:
    return shutil.which("tracker")


def run_tracker_json(
    args: list[str],
    *,
    cwd: Path,
    runner: Runner | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Run `tracker … --json` from the instance root and parse stdout."""

    binary = tracker_binary()
    if runner is None and not binary:
        raise FileNotFoundError(
            "Atlas Tasker `tracker` CLI is not on PATH. Install it, or keep the "
            "argv-template fallback (`arthur tracker open_decision`)."
        )
    cmd = [binary or "tracker", *args]
    if "--json" not in cmd:
        cmd.append("--json")
    run = runner or subprocess.run
    try:
        proc = run(cmd, text=True, capture_output=True, check=False, cwd=str(cwd), timeout=timeout)
    except TypeError:
        # test doubles may not accept timeout=
        proc = run(cmd, text=True, capture_output=True, check=False, cwd=str(cwd))
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"tracker {' '.join(args)} failed: {err}")
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("tracker returned empty JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tracker JSON was not parseable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("tracker JSON must be an object")
    return payload


def _ticket_id(ticket: dict[str, Any]) -> str:
    return str(ticket.get("id") or ticket.get("ticket_id") or ticket.get("key") or "").strip()


def _ticket_status(ticket: dict[str, Any]) -> str:
    return str(ticket.get("status") or "").strip().lower()


def _ticket_assignee(ticket: dict[str, Any]) -> str:
    raw = ticket.get("assignee") or ""
    if isinstance(raw, dict):
        return str(raw.get("id") or raw.get("name") or "").strip()
    return str(raw).strip()


def tickets_from_board(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `tracker board --json` columns into ticket dicts."""

    columns = payload.get("columns")
    tickets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(ticket: Any, column: str | None = None) -> None:
        if not isinstance(ticket, dict):
            return
        ticket_id = _ticket_id(ticket)
        if not ticket_id or ticket_id in seen:
            return
        seen.add(ticket_id)
        row = dict(ticket)
        if column and not row.get("status"):
            row["status"] = column
        tickets.append(row)

    if isinstance(columns, dict):
        for name, items in columns.items():
            if isinstance(items, list):
                for item in items:
                    add(item, str(name))
    elif isinstance(columns, list):
        for column in columns:
            if not isinstance(column, dict):
                continue
            name = str(column.get("status") or column.get("id") or column.get("name") or "")
            for item in column.get("tickets") or column.get("items") or []:
                add(item, name)

    if not tickets:
        for item in payload.get("items") or []:
            add(item)
    return tickets


def is_ready_or_assigned(ticket: dict[str, Any]) -> bool:
    """Ready/in-progress work, or assigned work that is not finished."""

    status = _ticket_status(ticket)
    if status in TERMINAL_STATUSES:
        return False
    if status in READY_STATUSES:
        return True
    if _ticket_assignee(ticket) and status not in {"backlog"}:
        return True
    # Atlas "assigned" sometimes means ready work with an assignee, still in ready
    if _ticket_assignee(ticket) and status in {"ready", "in_progress", ""}:
        return True
    return False


def read_board(
    root: Path,
    *,
    project: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Primary Atlas path: `tracker board --json` from the instance root."""

    args = ["board"]
    if project:
        args.extend(["--project", project])
    payload = run_tracker_json(args, cwd=root, runner=runner)
    tickets = tickets_from_board(payload)
    ready = [ticket for ticket in tickets if is_ready_or_assigned(ticket)]
    return {
        "source": "tracker board --json",
        "cwd": str(root),
        "ticket_count": len(tickets),
        "ready_count": len(ready),
        "tickets": tickets,
        "ready": ready,
        "raw_kind": payload.get("kind"),
    }


def open_jobs_from_board(
    root: Path,
    *,
    project: str | None = None,
    limit: int = 20,
    dry_run: bool = False,
    runner: Runner | None = None,
    actor: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Open queue jobs for Atlas ready/assigned tickets that are not already queued.

    Idempotency key is `atlas:<ticket_id>` so a second pass will not fork the queue.
    """

    board = read_board(root, project=project, runner=runner)
    ledger = QueueLedger(root)
    existing_keys = {
        job.idempotency_key: job.job_id
        for job in ledger.latest_jobs().values()
        if job.idempotency_key
    }
    reserved_ids = set(ledger.latest_jobs())
    opened: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ticket in board["ready"][: max(0, limit)]:
        ticket_id = _ticket_id(ticket)
        key = f"atlas:{ticket_id}"
        if key in existing_keys:
            skipped.append({"ticket_id": ticket_id, "reason": "already queued", "job_id": existing_keys[key]})
            continue
        project_id = str(ticket.get("project") or project or ticket_id.split("-")[0] or "ATLAS").strip()
        job_id = _next_job_id(root, project_id, reserved_ids)
        reserved_ids.add(job_id)
        # if we already reserved this project id in this pass, bump again after create
        marker = f"ATLAS_{_slug(ticket_id)}"
        title = str(ticket.get("title") or ticket_id)
        record = {
            "ticket_id": ticket_id,
            "project_id": project_id,
            "job_id": job_id,
            "title": title,
            "status": _ticket_status(ticket),
            "assignee": _ticket_assignee(ticket),
            "idempotency_key": key,
        }
        if dry_run:
            opened.append(record)
            existing_keys[key] = job_id
            continue
        prompt_path = seed_first_prompt(
            root,
            project_id=project_id,
            job_id=job_id,
            marker=marker,
            idempotency_key=key,
        )
        job = QueueJob(
            job_id=job_id,
            project_id=project_id,
            target_chat_title=title,
            target_chat_url="atlas-tasker",
            prompt_path=prompt_path,
            expected_marker=marker,
            idempotency_key=key,
        )
        QueueLedger(root).create_job(job)
        if actor or reason:
            QueueLedger(root).append_event(
                job_id,
                "opened_from_atlas_board",
                {"ticket_id": ticket_id, "actor": actor, "reason": reason},
            )
        existing_keys[key] = job_id
        record["job"] = job.to_record()
        opened.append(record)

    return {
        "source": board["source"],
        "dry_run": dry_run,
        "opened": opened,
        "skipped": skipped,
        "ready_seen": board["ready_count"],
    }


def tracker_or_fallback(
    root: Path,
    action: str,
    *,
    dry_run: bool = False,
    **values: str,
) -> dict[str, Any]:
    """Keep the three argv templates as the fallback for decision/sprint hooks."""

    return tracker_run_action(load_config(root), action, dry_run=dry_run, cwd=root, **values)
