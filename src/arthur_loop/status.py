from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from arthur_loop.browser_lock import is_fresh, read_lock
from arthur_loop.queue_ledger import (
    TERMINAL_STATUSES,
    QueueJob,
    QueueLedger,
    append_jsonl,
    isoformat,
    parse_ledger_time,
    read_jsonl,
    utc_now,
)
from arthur_loop.resource_usage import classify_remaining
from arthur_loop.tick import OPEN_STATUS_RE, SECTION_RE, TickResult, classify_tick
from arthur_loop.usage_attribution import latest_snapshots


DEFAULT_RESERVE_PERCENT = 5.0
DEFAULT_SESSION_STALE_MINUTES = 60
VALID_SESSION_STATES = {"working", "waiting", "blocked", "idle", "done"}

QUOTA_BAR_WIDTH = 20

# headline colors follow the tick vocabulary; unknown states fall back to white
STATE_STYLES = {
    "WAIT": "cyan",
    "POLL_DUE": "bold green",
    "BLOCKED_BY_BROWSER_LOCK": "bold yellow",
    "BLOCKED_BY_QUOTA": "bold red",
    "HUMAN_INPUT_REQUIRED": "bold magenta",
}

STATE_HINTS = {
    "WAIT": "nothing due — the loop is resting",
    "POLL_DUE": "queue work is ready — claim it through the queue CLI",
    "BLOCKED_BY_BROWSER_LOCK": "due work is waiting on the browser lock",
    "BLOCKED_BY_QUOTA": "quota at or below reserve — checkpoint and wait for reset",
    "HUMAN_INPUT_REQUIRED": "a human decision is the only thing moving this forward",
}

SESSION_STATE_STYLES = {
    "working": "green",
    "waiting": "yellow",
    "blocked": "bold red",
    "idle": "dim",
    "done": "dim",
}

JOB_STATUS_STYLES = {
    "queued": "cyan",
    "claimed": "yellow",
    "submitted": "yellow",
    "waiting_for_chatgpt": "blue",
    "stopped_no_output": "red",
    "needs_recovery": "bold red",
}


@dataclass(frozen=True)
class SessionRecord:
    """One self-reported agent-session heartbeat."""

    session_id: str
    role: str
    state: str
    activity: str
    project_id: str | None
    at: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionView:
    """A folded session plus freshness computed against a chosen now."""

    record: SessionRecord
    stale: bool
    age_minutes: float | None


@dataclass(frozen=True)
class ProjectStatus:
    """One project row: state summary plus decision-block flag."""

    project_id: str
    summary: str
    blocked_by_decision: bool
    state_path: str


@dataclass(frozen=True)
class StatusSnapshot:
    """Everything the dashboard shows, gathered read-only from durable state."""

    generated_at: str
    root: str
    tick: TickResult
    sessions: list[SessionView]
    jobs: list[QueueJob]
    hidden_terminal_jobs: int
    projects: list[ProjectStatus]
    open_decisions: list[dict[str, str]]
    quota: dict[str, Any] | None
    browser_lock: dict[str, Any] | None
    reserve_percent: float


def sessions_path(root: Path) -> Path:
    """Return the append-only session registry path."""

    return root / "runtime/sessions.jsonl"


def record_session(
    root: Path,
    *,
    session_id: str,
    role: str,
    state: str = "working",
    activity: str,
    project_id: str | None = None,
    now: datetime | None = None,
) -> SessionRecord:
    """Append a session heartbeat so the dashboard knows what this agent is doing."""

    if state not in VALID_SESSION_STATES:
        allowed = ", ".join(sorted(VALID_SESSION_STATES))
        raise ValueError(f"unknown session state {state!r}; expected one of: {allowed}")

    record = SessionRecord(
        session_id=session_id,
        role=role,
        state=state,
        activity=activity,
        project_id=project_id,
        at=isoformat(now),
    )
    append_jsonl(sessions_path(root), record.to_record())
    return record


def clear_session(root: Path, session_id: str, *, now: datetime | None = None) -> bool:
    """Append a cleared tombstone so the session drops off the dashboard."""

    latest = _fold_sessions(root)
    last = latest.get(session_id)
    if last is None or last.state == "cleared":
        return False

    append_jsonl(
        sessions_path(root),
        SessionRecord(
            session_id=session_id,
            role=last.role,
            state="cleared",
            activity="",
            project_id=last.project_id,
            at=isoformat(now),
        ).to_record(),
    )
    return True


def load_sessions(
    root: Path,
    *,
    now: datetime | None = None,
    stale_after_minutes: int = DEFAULT_SESSION_STALE_MINUTES,
) -> list[SessionView]:
    """Return the latest non-cleared session per id, with staleness flags."""

    now = now or utc_now()
    views: list[SessionView] = []
    for record in _fold_sessions(root).values():
        if record.state == "cleared":
            continue
        seen = parse_ledger_time(record.at)
        age = None if seen is None else round((now - seen).total_seconds() / 60, 1)
        stale = age is None or age >= stale_after_minutes
        views.append(SessionView(record=record, stale=stale, age_minutes=age))
    return sorted(views, key=lambda view: (view.record.role, view.record.session_id))


def _fold_sessions(root: Path) -> dict[str, SessionRecord]:
    latest: dict[str, SessionRecord] = {}
    for raw in read_jsonl(sessions_path(root)):
        latest[raw["session_id"]] = SessionRecord(**raw)
    return latest


def project_statuses(root: Path, blocked_projects: list[str]) -> list[ProjectStatus]:
    """Summarize projects/<ID>/state.md files: first content line plus block flag."""

    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []

    statuses: list[ProjectStatus] = []
    for state_path in sorted(projects_dir.glob("*/state.md")):
        project_id = state_path.parent.name
        summary = ""
        for line in state_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                summary = stripped
                break
        if len(summary) > 72:
            summary = summary[:71].rstrip() + "…"
        statuses.append(
            ProjectStatus(
                project_id=project_id,
                summary=summary or "(no state summary)",
                blocked_by_decision=project_id in blocked_projects,
                state_path=state_path.relative_to(root).as_posix(),
            )
        )
    return statuses


def open_decision_items(root: Path) -> list[dict[str, str]]:
    """Return open human-decision sections as {title, project_id} rows."""

    path = root / "human-decisions/open.md"
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    matches = list(SECTION_RE.finditer(text))
    items: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if not OPEN_STATUS_RE.search(text[match.end():end]):
            continue
        title = match.group(1).strip()
        items.append({"title": title, "project_id": title.split()[0].strip("`:")})
    return items


def _quota_summary(root: Path, reserve_percent: float) -> dict[str, Any]:
    snapshots = latest_snapshots(root)
    if not snapshots:
        return {
            "left_percent": None,
            "state": "UNKNOWN",
            "captured_at": None,
            "reset": None,
            "secondary_left_percent": None,
            "provider": None,
        }

    latest = max(snapshots.values(), key=lambda snapshot: snapshot.captured_at)
    left = latest.primary_left_percent
    state = "UNKNOWN" if left is None else classify_remaining(float(left), reserve_percent)[0]
    return {
        "left_percent": left,
        "state": state,
        "captured_at": latest.captured_at,
        "reset": latest.primary_reset,
        "secondary_left_percent": latest.secondary_left_percent,
        "provider": latest.provider,
    }


def collect_status(
    root: Path,
    *,
    now: datetime | None = None,
    reserve_percent: float = DEFAULT_RESERVE_PERCENT,
    stale_after_minutes: int = 30,
    session_stale_minutes: int = DEFAULT_SESSION_STALE_MINUTES,
    quota_enabled: bool = True,
) -> StatusSnapshot:
    """Gather the whole dashboard from durable state. Read-only by design."""

    now = now or utc_now()
    tick = classify_tick(
        root,
        now=now,
        dry_run=True,
        reserve_percent=reserve_percent,
        stale_after_minutes=stale_after_minutes,
        quota_enabled=quota_enabled,
    )

    all_jobs = QueueLedger(root).latest_jobs()
    active_jobs = sorted(
        (job for job in all_jobs.values() if job.status not in TERMINAL_STATUSES),
        key=lambda job: (job.priority, job.created_at, job.job_id),
    )
    blocked = tick.blocked_projects or []

    return StatusSnapshot(
        generated_at=isoformat(now),
        root=str(root),
        tick=tick,
        sessions=load_sessions(root, now=now, stale_after_minutes=session_stale_minutes),
        jobs=active_jobs,
        hidden_terminal_jobs=len(all_jobs) - len(active_jobs),
        projects=project_statuses(root, blocked),
        open_decisions=open_decision_items(root),
        quota=_quota_summary(root, reserve_percent) if quota_enabled else None,
        browser_lock=_lock_summary(root, now),
        reserve_percent=reserve_percent,
    )


def _lock_summary(root: Path, now: datetime) -> dict[str, Any] | None:
    lock = read_lock(root)
    if lock is None:
        return None
    return {
        "holder": lock.holder,
        "fresh": is_fresh(lock, now),
        "acquired_at": lock.acquired_at,
        "stale_after": lock.stale_after,
    }


def status_to_dict(snapshot: StatusSnapshot) -> dict[str, Any]:
    """Stable machine-readable dump — the contract for bots and notifiers."""

    return {
        "generated_at": snapshot.generated_at,
        "root": snapshot.root,
        "state": snapshot.tick.status,
        "tick": snapshot.tick.to_record(),
        "sessions": [
            {**view.record.to_record(), "stale": view.stale, "age_minutes": view.age_minutes}
            for view in snapshot.sessions
        ],
        "queue": [job.to_record() for job in snapshot.jobs],
        "hidden_terminal_jobs": snapshot.hidden_terminal_jobs,
        "projects": [asdict(project) for project in snapshot.projects],
        "decisions": snapshot.open_decisions,
        "quota": snapshot.quota,
        "browser_lock": snapshot.browser_lock,
        "reserve_percent": snapshot.reserve_percent,
    }


def relative_time(value: str | None, now: datetime) -> str:
    """Render a ledger timestamp relative to now: 'in 4m', '12m ago', 'now', '-'."""

    moment = parse_ledger_time(value)
    if moment is None:
        return "-"
    seconds = (moment - now).total_seconds()
    if abs(seconds) < 45:
        return "now"
    span = _span(abs(seconds))
    return f"in {span}" if seconds > 0 else f"{span} ago"


def _span(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    days, rem_hours = divmod(hours, 24)
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"


def build_console(
    *,
    no_color: bool = False,
    record: bool = False,
    width: int | None = None,
    file: Any = None,
) -> Console:
    """Console factory: rich already degrades cleanly when stdout is not a TTY."""

    return Console(no_color=no_color, record=record, width=width, file=file)


def render_status(snapshot: StatusSnapshot, console: Console) -> None:
    """Print the dashboard: headline, sessions, queue, projects, decisions, resources."""

    now = parse_ledger_time(snapshot.generated_at) or utc_now()
    parts: list[Any] = [_headline_panel(snapshot, now), Text("")]
    parts.extend([_sessions_renderable(snapshot, now), Text("")])
    parts.extend([_queue_renderable(snapshot, now), Text("")])
    parts.append(_projects_renderable(snapshot))
    if snapshot.open_decisions:
        parts.extend([Text(""), _decisions_panel(snapshot)])
    parts.extend([Text(""), _resources_panel(snapshot, now)])
    console.print(Group(*parts))


def _headline_panel(snapshot: StatusSnapshot, now: datetime) -> Panel:
    state = snapshot.tick.status
    style = STATE_STYLES.get(state, "white")

    body = Text()
    body.append(state, style=style)
    hint = STATE_HINTS.get(state)
    if hint:
        body.append(f"  {hint}", style="dim")
    for warning in snapshot.tick.warnings or []:
        body.append(f"\n! {warning}", style="yellow")

    subtitle = Text(f"generated {snapshot.generated_at}", style="dim")
    if snapshot.tick.next_due_at:
        subtitle.append(f" · next due {relative_time(snapshot.tick.next_due_at, now)}", style="dim")

    return Panel(
        body,
        title="ARTHUR LOOP",
        subtitle=subtitle,
        box=box.HEAVY,
        border_style=style.replace("bold ", ""),
        padding=(0, 2),
    )


def _section_table(title: str, *columns: str) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        title=title,
        title_justify="left",
        title_style="bold cyan",
        header_style="bold",
        pad_edge=False,
    )
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


def _sessions_renderable(snapshot: StatusSnapshot, now: datetime) -> Any:
    if not snapshot.sessions:
        return Group(
            Text("SESSIONS", style="bold cyan"),
            Text("  no sessions reporting — record one with the status 'set' command", style="dim"),
        )

    table = _section_table("SESSIONS", "SESSION", "ROLE", "PROJECT", "STATE", "ACTIVITY", "SEEN")
    for view in snapshot.sessions:
        record = view.record
        seen = relative_time(record.at, now)
        if view.stale:
            seen += " (stale)"
        table.add_row(
            record.session_id,
            record.role,
            record.project_id or "-",
            Text(record.state, style=SESSION_STATE_STYLES.get(record.state, "white")),
            record.activity,
            seen,
            style="dim" if view.stale else None,
        )
    return table


def _queue_renderable(snapshot: StatusSnapshot, now: datetime) -> Any:
    if not snapshot.jobs:
        note = "queue is empty — create work with the queue 'create' command"
        if snapshot.hidden_terminal_jobs:
            note = f"no active jobs · {snapshot.hidden_terminal_jobs} finished job(s) hidden"
        return Group(Text("QUEUE", style="bold cyan"), Text(f"  {note}", style="dim"))

    table = _section_table("QUEUE", "JOB", "PROJECT", "STATUS", "ATTEMPT", "NEXT POLL")
    if snapshot.hidden_terminal_jobs:
        table.caption = f"{snapshot.hidden_terminal_jobs} finished job(s) hidden"
        table.caption_style = "dim"
        table.caption_justify = "left"
    for job in snapshot.jobs:
        if job.status == "queued":
            eta: Any = Text("ready", style="green")
        else:
            rel = relative_time(job.next_poll_at, now)
            if rel.endswith(" ago"):
                eta = Text(f"overdue {rel[:-4]}", style="bold red")
            else:
                eta = Text(rel)
        table.add_row(
            job.job_id,
            job.project_id,
            Text(job.status, style=JOB_STATUS_STYLES.get(job.status, "white")),
            str(job.attempt_count),
            eta,
        )
    return table


def _projects_renderable(snapshot: StatusSnapshot) -> Any:
    if not snapshot.projects:
        return Group(
            Text("PROJECTS", style="bold cyan"),
            Text("  no projects yet — add projects/<ID>/state.md", style="dim"),
        )

    table = _section_table("PROJECTS", "PROJECT", "STATE", "FLAGS")
    for project in snapshot.projects:
        flag = (
            Text("BLOCKED-BY-DECISION", style="bold red")
            if project.blocked_by_decision
            else Text("-", style="dim")
        )
        table.add_row(project.project_id, project.summary, flag)
    return table


def _decisions_panel(snapshot: StatusSnapshot) -> Panel:
    lines = [Text(f"• {item['title']}", style="magenta") for item in snapshot.open_decisions]
    return Panel(
        Group(*lines),
        title="HUMAN DECISIONS",
        title_align="left",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 2),
    )


def _resources_panel(snapshot: StatusSnapshot, now: datetime) -> Panel:
    return Panel(
        Group(_quota_line(snapshot, now), _lock_line(snapshot, now)),
        title="RESOURCES",
        title_align="left",
        border_style="grey37",
        box=box.ROUNDED,
        padding=(0, 2),
    )


def _quota_line(snapshot: StatusSnapshot, now: datetime) -> Text:
    line = Text()
    line.append("quota         ", style="bold")

    quota = snapshot.quota
    if quota is None:
        line.append("governor off", style="dim")
        return line

    left = quota.get("left_percent")
    if left is None:
        line.append("░" * QUOTA_BAR_WIDTH, style="grey37")
        line.append("  no snapshot yet", style="dim")
        return line

    # the bar colors from quota state alone — a red quota must look red
    # even when the headline is WAIT because nothing is due
    state = quota.get("state", "UNKNOWN")
    color = {"GREEN": "green", "YELLOW": "yellow", "RED": "red"}.get(state, "white")
    filled = max(0, min(QUOTA_BAR_WIDTH, int(round(float(left) / 100 * QUOTA_BAR_WIDTH))))
    line.append("█" * filled, style=color)
    line.append("░" * (QUOTA_BAR_WIDTH - filled), style="grey37")
    line.append(f"  {float(left):g}% left  ")
    line.append(state, style=f"bold {color}")
    line.append(f"  (reserve {snapshot.reserve_percent:g}%)", style="dim")
    if quota.get("captured_at"):
        line.append(f" · captured {relative_time(quota['captured_at'], now)}", style="dim")
    if quota.get("reset"):
        line.append(f" · {quota['reset']}", style="dim")
    return line


def _lock_line(snapshot: StatusSnapshot, now: datetime) -> Text:
    line = Text()
    line.append("browser lock  ", style="bold")

    lock = snapshot.browser_lock
    if lock is None:
        line.append("free", style="dim")
    elif lock.get("fresh"):
        line.append(f"held by {lock['holder']}", style="yellow")
        line.append(f" · stale {relative_time(lock.get('stale_after'), now)}", style="dim")
    else:
        line.append(f"held by {lock['holder']} · stale (takeover allowed)", style="dim")
    return line
