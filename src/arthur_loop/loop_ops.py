"""Create and inspect a project loop — shared by CLI, web wizard, and MCP."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from arthur_loop.config import (
    KNOWN_ADVISORS,
    KNOWN_EXECUTORS,
    KNOWN_TRACKERS,
    config_path,
    load_config,
    require_instance,
)
from arthur_loop.roles import (
    LOOP_ROLES,
    ROLE_AGENTS,
    formulate_default_loop,
    merge_roles,
    resolve_roles,
    roles_payload,
    validate_roles,
)
from arthur_loop.decisions import PROJECT_ID_RE
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.tick import open_human_decision_projects


PROJECT_ID_PATTERN = PROJECT_ID_RE


def ensure_project_not_paused(root: Path, project_id: str) -> None:
    """Refuse work that would move a decision-blocked project."""

    paused = open_human_decision_projects(root)
    if project_id in paused:
        raise ValueError(
            f"project {project_id} is paused by an open human decision; "
            "answer or clear it before claiming, submitting, polling, "
            "completing, or failing work (`arthur decision list`)"
        )


def poll_job(
    root: Path,
    job_id: str,
    *,
    marker_found: bool,
    status: str | None = None,
    holder: str,
    ttl_minutes: int = 15,
    keep_lock: bool = False,
    now=None,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    output_artifact_paths: list[str] | None = None,
):
    """Record a poll. Refused when the project is paused."""

    from arthur_loop.browser_lock import acquire_lock, release_lock

    ledger = QueueLedger(root)
    resolved = status or ("completed" if marker_found else "waiting_for_chatgpt")
    job = ledger.check_transition(job_id, resolved)
    ensure_project_not_paused(root, job.project_id)
    acquire_lock(root, holder, ttl_minutes=ttl_minutes, now=now)
    job = ledger.record_poll_result(
        job_id,
        marker_found=marker_found,
        status=resolved,
        now=now,
        data=data,
        error=error,
        output_artifact_paths=output_artifact_paths,
    )
    if resolved in {"completed", "completed_with_warnings"}:
        ledger.append_event(
            job_id,
            "job_completed",
            {"status": resolved, "artifact_paths": output_artifact_paths or []},
            at=now,
        )
    if not keep_lock:
        release_lock(root, holder, now=now)
    return job


def complete_job(
    root: Path,
    job_id: str,
    *,
    now=None,
    warning: str | None = None,
    output_artifact_paths: list[str] | None = None,
):
    """Finish a submitted job. Refused when the project is paused."""

    ledger = QueueLedger(root)
    status = "completed_with_warnings" if warning else "completed"
    job = ledger.check_transition(job_id, status)
    ensure_project_not_paused(root, job.project_id)
    job = ledger.transition(
        job_id,
        status,
        now=now,
        error=warning,
        output_artifact_paths=output_artifact_paths,
    )
    ledger.append_event(
        job_id,
        "job_completed",
        {"status": status, "artifact_paths": output_artifact_paths or []},
        at=now,
    )
    return job


def fail_job(root: Path, job_id: str, *, error: str, now=None):
    """Mark a job failed. Refused when the project is paused."""

    ledger = QueueLedger(root)
    job = ledger.check_transition(job_id, "failed")
    ensure_project_not_paused(root, job.project_id)
    return ledger.transition(job_id, "failed", now=now, error=error)


def claim_job(
    root: Path,
    job_id: str,
    *,
    holder: str,
    ttl_minutes: int = 15,
    now=None,
):
    """Claim a queued job after the pause gate — used by CLI and MCP."""

    from arthur_loop.browser_lock import acquire_lock

    ledger = QueueLedger(root)
    job = ledger.check_transition(job_id, "claimed")
    ensure_project_not_paused(root, job.project_id)
    acquire_lock(root, holder, ttl_minutes=ttl_minutes, now=now)
    return ledger.transition(job_id, "claimed", now=now, holder=holder)


def submit_job(
    root: Path,
    job_id: str,
    *,
    holder: str,
    keep_lock: bool = False,
    now=None,
):
    """Record that the prompt was sent. Refused when the project is paused."""

    from arthur_loop.browser_lock import BrowserLockError, read_lock, release_lock

    ledger = QueueLedger(root)
    job = ledger.check_transition(job_id, "submitted")
    ensure_project_not_paused(root, job.project_id)
    lock = read_lock(root)
    if not lock:
        raise BrowserLockError("browser lock is not held; claim the job first")
    if lock.holder != holder:
        raise BrowserLockError(f"browser lock is held by {lock.holder}, not {holder}")
    job = ledger.transition(job_id, "submitted", now=now)
    if not keep_lock:
        release_lock(root, holder, now=now)
    return job


def _copy_tree(source: Any, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            target.write_bytes(item.read_bytes())


def _read_user_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config/arthur-loop.json must be a JSON object")
    return data


def write_user_config(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Write the instance config file and return the merged loaded config."""

    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return load_config(root)


def apply_role_updates(
    root: Path,
    *,
    advisor: str | None = None,
    executor: str | None = None,
    tracker: str | None = None,
    roles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update roles (and legacy advisor/executor/tracker) and copy shipped packs."""

    if advisor and advisor not in KNOWN_ADVISORS:
        raise ValueError(f"unknown advisor {advisor!r} — expected one of {sorted(KNOWN_ADVISORS)}")
    if executor and executor not in KNOWN_EXECUTORS:
        raise ValueError(f"unknown executor {executor!r} — expected one of {sorted(KNOWN_EXECUTORS)}")
    if tracker and tracker not in KNOWN_TRACKERS:
        raise ValueError(f"unknown tracker {tracker!r} — expected one of {sorted(KNOWN_TRACKERS)}")

    data = _read_user_config(root)
    current = resolve_roles(load_config(root) if (root / "config/arthur-loop.json").exists() or data else {})
    overlay: dict[str, Any] = {}
    if advisor:
        overlay["planner"] = {"agent": advisor, "model": current["planner"].get("model") or ""}
        overlay["reviewer"] = {"agent": advisor, "model": current["reviewer"].get("model") or ""}
    if executor:
        overlay["implementer"] = {"agent": executor, "model": current["implementer"].get("model") or ""}
    if roles:
        for role, value in roles.items():
            if role not in LOOP_ROLES:
                raise ValueError(f"unknown role {role!r} — expected one of {list(LOOP_ROLES)}")
            if not isinstance(value, dict):
                raise ValueError(f"role {role} must be an object with agent and optional model")
            overlay[role] = {
                "agent": str(value.get("agent") or current[role]["agent"]),
                "model": str(value.get("model") if value.get("model") is not None else current[role].get("model") or ""),
            }
    merged_roles = merge_roles(current, overlay) if overlay else current
    validate_roles(merged_roles)
    data["roles"] = merged_roles
    planner = merged_roles["planner"]["agent"]
    implementer = merged_roles["implementer"]["agent"]
    data.setdefault("advisor", {})["adapter"] = planner
    data.setdefault("executor", {})["adapter"] = implementer

    adapters_pkg = resources.files("arthur_loop") / "adapters"
    if overlay.get("planner") or advisor or "planner" in (roles or {}):
        if planner in KNOWN_ADVISORS:
            _copy_tree(adapters_pkg / "advisors" / planner, root / "adapters/advisor")
    if overlay.get("implementer") or executor or "implementer" in (roles or {}):
        if implementer in KNOWN_EXECUTORS:
            _copy_tree(adapters_pkg / "executors" / implementer, root / "adapters/executor")
    if tracker:
        data.setdefault("tracker", {})["adapter"] = tracker
        if tracker == "atlas-tasker":
            _fill_project_map(data)
    if data:
        write_user_config(root, data)
    return load_config(root)


def _fill_project_map(data: dict[str, Any], atlas_key: str | None = None) -> dict[str, str]:
    """Write a usable Atlas key for every known Arthur project. No silent empty trap."""

    from arthur_loop.atlas_board import default_atlas_key

    tracker = data.setdefault("tracker", {})
    mapping = dict(tracker.get("project_map") or {})
    if not isinstance(mapping, dict):
        mapping = {}
    for item in data.get("projects") or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("project_id") or "").strip()
        if not pid:
            continue
        existing = str(mapping.get(pid) or item.get("atlas_project") or "").strip()
        mapping[pid] = atlas_key or existing or default_atlas_key(pid)
    tracker["project_map"] = mapping
    data["tracker"] = tracker
    return {str(k): str(v) for k, v in mapping.items()}


def _next_job_id(root: Path, project_id: str, reserved: set[str] | None = None) -> str:
    existing = set(QueueLedger(root).latest_jobs())
    if reserved:
        existing |= reserved
    prefix = f"BQ-{project_id}-"
    highest = 0
    for job_id in existing:
        if not job_id.startswith(prefix):
            continue
        tail = job_id[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:03d}"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_") or "LOOP"


def seed_first_prompt(
    root: Path,
    *,
    project_id: str,
    job_id: str,
    marker: str,
    idempotency_key: str,
) -> str:
    """Write a rendered next-plan prompt from the advisor pack when present."""

    dest = root / "queue" / "prompts" / f"{job_id.lower()}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = root / "adapters" / "advisor" / "prompts" / "next-plan-request.md"
    if src.is_file():
        text = src.read_text(encoding="utf-8")
        replacements = {
            "{{PROJECT_ID}}": project_id,
            "{{CURRENT_STATE_SUMMARY}}": f"{project_id} — first hop of a new loop.",
            "{{EXPECTED_MARKER}}": marker,
            "{{IDEMPOTENCY_KEY}}": idempotency_key,
        }
        for token, value in replacements.items():
            text = text.replace(token, value)
    else:
        text = (
            f"# {job_id} Prompt\n\n"
            f"Arthur Loop next-plan request for {project_id}.\n"
            f"Include marker {marker}.\n"
        )
    dest.write_text(text, encoding="utf-8")
    return dest.relative_to(root).as_posix()


def ensure_project(root: Path, project_id: str, *, goal: str = "") -> dict[str, str]:
    """Create project state and register the project in config if missing."""

    if not PROJECT_ID_PATTERN.match(project_id):
        raise ValueError(f"project id {project_id!r} must be a single word like MY_APP")

    state_rel = f"projects/{project_id}/state.md"
    state = root / state_rel
    state.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not state.exists():
        body = f"# {project_id} State\n\n"
        body += (goal.strip() + "\n") if goal.strip() else "Not started.\n"
        state.write_text(body, encoding="utf-8")
        created = True

    data = _read_user_config(root)
    projects = list(data.get("projects") or [])
    if not any(item.get("project_id") == project_id for item in projects if isinstance(item, dict)):
        projects.append(
            {
                "project_id": project_id,
                "advisor_target_title": f"{project_id} planning",
                "advisor_target_url": "manual",
                "state_path": state_rel,
            }
        )
        data["projects"] = projects
    tracker = data.get("tracker") or {}
    if isinstance(tracker, dict) and tracker.get("adapter") == "atlas-tasker":
        _fill_project_map(data)
    write_user_config(root, data)
    return {"project_id": project_id, "state_path": state_rel, "created": created}


def create_loop(
    root: Path,
    *,
    project_id: str,
    advisor: str | None = None,
    executor: str | None = None,
    tracker: str | None = None,
    roles: dict[str, Any] | None = None,
    title: str | None = None,
    target_chat_url: str = "manual",
    goal: str = "",
    seed_job: bool = True,
    actor: str | None = None,
    reason: str | None = None,
    ticket: str | None = None,
) -> dict[str, Any]:
    """Create a project loop: assign roles, formulate the default hop sequence, seed the first job.

    This is a wizard, not a graph composer. It writes the same files `arthur init`
    and `arthur queue create` would. `arthur` / `arthur run` then hands each hop
    to the assigned agent.
    """

    require_instance(root)
    project_id = project_id.strip()
    if not PROJECT_ID_PATTERN.match(project_id):
        raise ValueError(f"project id {project_id!r} must be a single word like MY_APP")

    ticket = (ticket or "").strip() or None
    if ticket and not goal.strip():
        goal = f"Ticket {ticket}"

    config = apply_role_updates(
        root,
        advisor=advisor,
        executor=executor,
        tracker=tracker,
        roles=roles,
    )
    project = ensure_project(root, project_id, goal=goal)
    sequence = formulate_default_loop(config, project_id=project_id, ticket=ticket, goal=goal)
    plan_path = root / "projects" / project_id / "loop-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(sequence, indent=2) + "\n", encoding="utf-8")

    title = (title or f"{project_id} planning").strip()
    url = (target_chat_url or "manual").strip() or "manual"

    job_record: dict[str, Any] | None = None
    if seed_job:
        job_id = _next_job_id(root, project_id)
        marker = f"{project_id}_LOOP_{_slug(job_id)}"
        key = f"loop:{project_id}:{job_id}"
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
            target_chat_url=url,
            prompt_path=prompt_path,
            expected_marker=marker,
            idempotency_key=key,
        )
        ledger = QueueLedger(root)
        ledger.create_job(job)
        first = sequence["hops"][0] if sequence["hops"] else {"kind": "next-plan-request", "role": "planner"}
        ledger.append_event(
            job_id,
            "follow_hop",
            {
                "kind": first["kind"],
                "role": first["role"],
                "actor": actor or "arthur-loop",
                "ticket": ticket,
            },
        )
        if actor or reason:
            ledger.append_event(
                job_id,
                "loop_created",
                {"actor": actor, "reason": reason, "project_id": project_id, "ticket": ticket},
            )
        job_record = job.to_record()

    roles_out = resolve_roles(config)
    return {
        "project": project,
        "advisor": config["advisor"]["adapter"],
        "executor": config["executor"]["adapter"],
        "tracker": config["tracker"]["adapter"],
        "roles": roles_out,
        "sequence": sequence,
        "ticket": ticket,
        "job": job_record,
        "honest_copy": (
            "Created a project and the first queue job. This is not a drag-drop "
            "graph composer. Assigned agents run in order: "
            + " → ".join(f"{hop['role']}({hop['agent']})" for hop in sequence["hops"])
            + ". Run `arthur` or `arthur run` to invoke the next role."
        ),
        "next": [
            "arthur",
            "arthur run",
            "arthur follow --once",
            f"arthur queue claim --job-id {job_record['job_id']}" if job_record else "arthur status",
            "arthur tick --dry-run",
            "arthur gate implementation --project-id " + project_id,
        ],
    }


def list_loops(root: Path) -> dict[str, Any]:
    """Projects plus current roles — the wizard's read model."""

    require_instance(root)
    config = load_config(root)
    jobs = QueueLedger(root).latest_jobs()
    projects = []
    for item in config.get("projects") or []:
        if not isinstance(item, dict) or not item.get("project_id"):
            continue
        pid = item["project_id"]
        projects.append(
            {
                "project_id": pid,
                "title": item.get("advisor_target_title") or pid,
                "jobs": [job.to_record() for job in jobs.values() if job.project_id == pid],
            }
        )
    known_ids = {row["project_id"] for row in projects}
    for job in jobs.values():
        if job.project_id not in known_ids:
            projects.append(
                {
                    "project_id": job.project_id,
                    "title": job.target_chat_title,
                    "jobs": [j.to_record() for j in jobs.values() if j.project_id == job.project_id],
                }
            )
            known_ids.add(job.project_id)
    return {
        "advisor": config["advisor"]["adapter"],
        "executor": config["executor"]["adapter"],
        "tracker": config["tracker"]["adapter"],
        **roles_payload(config),
        "projects": projects,
        "advisors": sorted(KNOWN_ADVISORS),
        "executors": sorted(KNOWN_EXECUTORS),
        "trackers": sorted(KNOWN_TRACKERS),
    }


def wizard_options(root: Path | None = None) -> dict[str, Any]:
    """Copy and choices for the web create-loop wizard."""

    current: dict[str, Any] = {}
    config = None
    if root is not None and config_path(root).exists():
        config = load_config(root)
        current = {
            "advisor": config["advisor"]["adapter"],
            "executor": config["executor"]["adapter"],
            "tracker": config["tracker"]["adapter"],
            "roles": resolve_roles(config),
        }
    payload = {
        "advisors": sorted(KNOWN_ADVISORS),
        "executors": sorted(KNOWN_EXECUTORS),
        "trackers": sorted(KNOWN_TRACKERS),
        "roleAgents": {role: sorted(ROLE_AGENTS[role]) for role in LOOP_ROLES},
        "roleNames": list(LOOP_ROLES),
        "current": current,
        "copy": (
            "Assign an agent (and optional model) to each role. Arthur formulates "
            "the default hop sequence from the project or ticket and hands off to "
            "the next role. This is not a drag-and-drop graph composer."
        ),
    }
    if config is not None:
        payload["sequence"] = formulate_default_loop(config)["hops"]
    return payload
