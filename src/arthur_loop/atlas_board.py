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


# Atlas board columns that mean "this ticket is work an agent can open a job from".
# in_review / needs_review are review columns — not ready work. Assigned + in_review
# used to leak into open-jobs; that was a product bug.
READY_STATUSES = ("ready", "in_progress")
TERMINAL_STATUSES = ("done", "canceled", "cancelled")
NOT_OPENABLE_STATUSES = TERMINAL_STATUSES + (
    "in_review",
    "needs_review",
    "review",
    "blocked",
    "backlog",
    "awaiting_owner",
)
WALKABLE_NEXT_CATEGORIES = ("ready_for_me", "unblocked_for_me", "ready", "in_progress")

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
    """Ready/in-progress work. Assigned + in_review/blocked/backlog is not ready."""

    status = _ticket_status(ticket)
    if status in NOT_OPENABLE_STATUSES:
        return False
    if status in READY_STATUSES:
        return True
    # assigned with no status yet (column omitted) can still be work
    if _ticket_assignee(ticket) and status in {"", "assigned"}:
        return True
    return False


def default_atlas_key(project_id: str) -> str:
    """Usable Atlas `--project` key for an Arthur SHOUTY_SNAKE id.

    `DEMO_APP` → `DEMO`, `APP` → `APP`. Init and `loop create` write this into
    `tracker.project_map` so walk is not a silent empty trap.
    """

    value = (project_id or "").strip()
    if not value:
        return ""
    if "_" in value:
        return value.split("_", 1)[0]
    return value


def resolve_atlas_project(config: dict[str, Any], project_id: str | None) -> str | None:
    """Map an Arthur SHOUTY_SNAKE project_id to an Atlas project key.

    Arthur uses `DEMO_APP`. Atlas `--project` wants the short key (`DEMO`, `APP`).
    Passing the Arthur id through unchanged is the mismatch the re-test caught.
    This function does **not** invent a key — the map must already be filled.
    """

    tracker = config.get("tracker") or {}
    mapping = tracker.get("project_map") or {}
    if not isinstance(mapping, dict):
        mapping = {}
    if project_id and project_id in mapping and mapping[project_id]:
        return str(mapping[project_id])
    for item in config.get("projects") or []:
        if not isinstance(item, dict) or item.get("project_id") != project_id:
            continue
        if item.get("atlas_project"):
            return str(item["atlas_project"])
    default = tracker.get("project_key") or tracker.get("atlas_project")
    if default:
        return str(default)
    if not project_id:
        return None
    # already looks like an Atlas key (APP, demo) — no underscore
    if "_" not in project_id:
        return project_id
    return None


def arthur_project_for_atlas(config: dict[str, Any], atlas_key: str, fallback: str) -> str:
    """Reverse of resolve_atlas_project when opening queue jobs from tickets."""

    tracker = config.get("tracker") or {}
    mapping = tracker.get("project_map") or {}
    if isinstance(mapping, dict):
        for arthur_id, key in mapping.items():
            if str(key) == atlas_key:
                return str(arthur_id)
    for item in config.get("projects") or []:
        if isinstance(item, dict) and str(item.get("atlas_project") or "") == atlas_key:
            return str(item.get("project_id") or fallback)
    return fallback


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
    opened = open_jobs_from_tickets(
        root,
        board["ready"][: max(0, limit)],
        project=project,
        dry_run=dry_run,
        actor=actor,
        reason=reason,
    )
    return {
        "source": board["source"],
        "dry_run": dry_run,
        "opened": opened["opened"],
        "skipped": opened["skipped"],
        "ready_seen": board["ready_count"],
    }


def open_jobs_from_tickets(
    root: Path,
    tickets: list[dict[str, Any]],
    *,
    project: str | None = None,
    dry_run: bool = False,
    actor: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Open queue jobs for an explicit ticket list (`atlas:<ticket_id>` keys)."""

    ledger = QueueLedger(root)
    existing_keys = {
        job.idempotency_key: job.job_id
        for job in ledger.latest_jobs().values()
        if job.idempotency_key
    }
    reserved_ids = set(ledger.latest_jobs())
    opened: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ticket in tickets:
        ticket_id = _ticket_id(ticket)
        if not ticket_id:
            continue
        key = f"atlas:{ticket_id}"
        if key in existing_keys:
            skipped.append({"ticket_id": ticket_id, "reason": "already queued", "job_id": existing_keys[key]})
            continue
        atlas_key = str(ticket.get("project") or project or ticket_id.split("-")[0] or "ATLAS").strip()
        project_id = arthur_project_for_atlas(load_config(root), atlas_key, atlas_key)
        job_id = _next_job_id(root, project_id, reserved_ids)
        reserved_ids.add(job_id)
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
    return {"opened": opened, "skipped": skipped}


def tracker_or_fallback(
    root: Path,
    action: str,
    *,
    dry_run: bool = False,
    **values: str,
) -> dict[str, Any]:
    """Keep the three argv templates as the fallback for decision/sprint hooks."""

    config = load_config(root)
    if "project" in values:
        mapped = resolve_atlas_project(config, values["project"])
        if mapped:
            values = {**values, "project": mapped}
        elif "_" in values["project"]:
            return {
                "status": "skipped",
                "reason": (
                    f"Arthur project_id {values['project']!r} is not an Atlas project key. "
                    "Set tracker.project_map "
                    f'(e.g. {{"{values["project"]}": "{default_atlas_key(values["project"])}"}}) '
                    "or tracker.project_key in config/arthur-loop.json, "
                    f"or pass --project {default_atlas_key(values['project'])}."
                ),
            }
    return tracker_run_action(config, action, dry_run=dry_run, cwd=root, **values)


def _ticket_from_next_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    entry = item.get("entry") or item.get("Entry") or item
    if not isinstance(entry, dict):
        return None
    ticket = entry.get("ticket") or entry.get("Ticket") or entry
    if not isinstance(ticket, dict):
        return None
    row = dict(ticket)
    category = str(item.get("category") or item.get("Category") or entry.get("category") or "")
    if category:
        row["category"] = category
    if entry.get("reason") or entry.get("Reason"):
        row["reason"] = entry.get("reason") or entry.get("Reason")
    return row


def next_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `tracker next --json` (NextView) into ticket dicts."""

    raw = payload.get("entries") or payload.get("Entries") or []
    nested = payload.get("next") or payload.get("Next")
    if isinstance(nested, dict):
        raw = nested.get("entries") or nested.get("Entries") or raw
    tickets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        ticket = _ticket_from_next_item(item)
        if not ticket:
            continue
        ticket_id = _ticket_id(ticket)
        if not ticket_id or ticket_id in seen:
            continue
        seen.add(ticket_id)
        tickets.append(ticket)
    return tickets


def queue_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `tracker queue --json` categories into ticket dicts."""

    categories = payload.get("categories") or payload.get("Categories") or {}
    tickets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Any, category: str) -> None:
        ticket = _ticket_from_next_item({"category": category, "entry": item}) if not (
            isinstance(item, dict) and ("ticket" in item or "Ticket" in item)
        ) else _ticket_from_next_item({"category": category, **item})
        if ticket is None and isinstance(item, dict):
            ticket = dict(item)
            ticket["category"] = category
        if not ticket:
            return
        ticket_id = _ticket_id(ticket)
        if not ticket_id or ticket_id in seen:
            return
        seen.add(ticket_id)
        ticket.setdefault("category", category)
        tickets.append(ticket)

    if isinstance(categories, dict):
        for name, items in categories.items():
            if isinstance(items, list):
                for item in items:
                    add(item, str(name))
    return tickets


def is_walkable_next(ticket: dict[str, Any]) -> bool:
    category = str(ticket.get("category") or "").strip().lower()
    if category in WALKABLE_NEXT_CATEGORIES:
        return is_ready_or_assigned(ticket) or _ticket_status(ticket) in {"", "ready", "in_progress", "backlog"}
    if category:
        return False
    return is_ready_or_assigned(ticket)


def read_next(
    root: Path,
    *,
    actor: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Primary walk path: `tracker next --json`."""

    args = ["next"]
    if actor:
        args.extend(["--actor", actor])
    payload = run_tracker_json(args, cwd=root, runner=runner)
    entries = next_entries(payload)
    walkable = [ticket for ticket in entries if is_walkable_next(ticket)]
    return {
        "source": "tracker next --json",
        "cwd": str(root),
        "actor": actor or payload.get("actor"),
        "ticket_count": len(entries),
        "walkable_count": len(walkable),
        "tickets": entries,
        "walkable": walkable,
        "next": walkable[0] if walkable else None,
    }


def read_queue(
    root: Path,
    *,
    actor: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """`tracker queue --json` — actor queue, including unblocked_for_me."""

    args = ["queue"]
    if actor:
        args.extend(["--actor", actor])
    payload = run_tracker_json(args, cwd=root, runner=runner)
    entries = queue_entries(payload)
    walkable = [ticket for ticket in entries if is_walkable_next(ticket)]
    return {
        "source": "tracker queue --json",
        "cwd": str(root),
        "actor": actor or payload.get("actor"),
        "ticket_count": len(entries),
        "walkable_count": len(walkable),
        "tickets": entries,
        "walkable": walkable,
    }


def walk_next(
    root: Path,
    *,
    actor: str | None = None,
    project: str | None = None,
    limit: int = 1,
    dry_run: bool = False,
    open_jobs: bool = True,
    runner: Runner | None = None,
    actor_audit: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Walk ready/assigned work: prefer `tracker next`, fall back to board."""

    source = "tracker next --json"
    tickets: list[dict[str, Any]] = []
    try:
        nxt = read_next(root, actor=actor, runner=runner)
        tickets = list(nxt["walkable"])
        source = nxt["source"]
    except (FileNotFoundError, RuntimeError) as exc:
        board = read_board(root, project=project, runner=runner)
        tickets = list(board["ready"])
        source = f"tracker board --json (next failed: {exc})"
    picked = tickets[: max(0, limit)]
    opened = None
    if open_jobs and picked:
        opened = open_jobs_from_tickets(
            root,
            picked,
            project=project,
            dry_run=dry_run,
            actor=actor_audit,
            reason=reason or "arthur tracker walk",
        )
    return {
        "source": source,
        "dry_run": dry_run,
        "next": picked[0] if picked else None,
        "walkable": picked,
        "opened": opened,
    }
