from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from arthur_loop.filelock import exclusive, instance_lock_path
from arthur_loop.queue_ledger import QueueLedger, isoformat
from arthur_loop.tick import OPEN_STATUS_RE, SECTION_RE


DECISION_EVENT_JOB_ID = "__decision__"
STATUS_LINE_RE = re.compile(r"^\s*Status:\s*`?([A-Z_]+)`?\s*$", re.IGNORECASE | re.MULTILINE)
PROJECT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

FILE_HEADER = "# Open Human Decisions\n"
EMPTY_STUB = FILE_HEADER + "\nNone right now. The loop appends here when a project needs you.\n"


class DecisionAlreadyOpen(ValueError):
    """Raised when an identical human decision is already open."""


def decisions_path(root: Path) -> Path:
    """The human-decision queue: one markdown file, one `##` section per decision."""

    return root / "human-decisions/open.md"


def _exclusive(root: Path):
    return exclusive(instance_lock_path(root, "decisions"))


def defuse_markdown(text: str) -> str:
    """Backslash-escape lines that could forge sections or status flips in open.md.

    Bodies and answers are often drafted by an agent or pasted from an
    artifact, so a multiline value containing "## Title" / "Status: OPEN"
    would otherwise parse as a brand-new open decision on the next tick.
    """

    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.lower().startswith("status:"):
            indent = line[: len(line) - len(stripped)]
            line = f"{indent}\\{stripped}"
        out.append(line)
    return "\n".join(out)


def _sections(text: str) -> list[dict[str, Any]]:
    matches = list(SECTION_RE.finditer(text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        status_match = STATUS_LINE_RE.search(body)
        status = status_match.group(1).upper() if status_match else None
        title = match.group(1).strip()
        sections.append(
            {
                "title": title,
                "project_id": title.split()[0].strip("`:") if title.split() else "",
                "status": status,
                "open": bool(OPEN_STATUS_RE.search(body)),
                "body": STATUS_LINE_RE.sub("", body).strip(),
                "start": match.start(),
                "end": end,
            }
        )
    return sections


def list_decisions(root: Path, *, include_closed: bool = False) -> list[dict[str, Any]]:
    """Return decision sections as {title, project_id, status, open, body} rows."""

    path = decisions_path(root)
    if not path.exists():
        return []
    rows = []
    for section in _sections(path.read_text(encoding="utf-8")):
        if not section["open"] and not include_closed:
            continue
        rows.append({key: section[key] for key in ("title", "project_id", "status", "open", "body")})
    return rows


def open_decision(
    root: Path,
    *,
    project_id: str,
    title: str,
    body: str,
    now: datetime | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    """Append a new OPEN decision section for a project; this pauses that project.

    The section heading is `## <PROJECT_ID> <title>` because the tick classifier
    reads the first word of every open section as the blocked project id.
    """

    project_id = project_id.strip()
    title = " ".join(title.split())
    if not PROJECT_ID_RE.match(project_id):
        raise ValueError(f"project id {project_id!r} must be a single word like MY_APP")
    if not title:
        raise ValueError("a decision needs a title")
    if title.split()[0] == project_id:
        title = title[len(project_id):].strip()
        if not title:
            raise ValueError("a decision needs a title after the project id")
    full_title = f"{project_id} {title}"
    body = defuse_markdown(body.strip()) or "(no details given)"

    path = decisions_path(root)
    with _exclusive(root):
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for section in _sections(text):
            if section["title"] == full_title and section["open"]:
                raise DecisionAlreadyOpen(f"decision {full_title!r} is already open")
        if not text.strip():
            text = FILE_HEADER
        # the fresh-instance stub reads oddly once real decisions exist
        text = text.replace("\nNone right now. The loop appends here when a project needs you.\n", "\n")
        if not text.endswith("\n"):
            text += "\n"
        text += (
            f"\n## {full_title}\n\nStatus: `OPEN`\n\n{body}\n\n"
            f"_Opened {isoformat(now)} via {source}._\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    QueueLedger(root).append_event(
        DECISION_EVENT_JOB_ID,
        "decision_opened",
        {"title": full_title, "project_id": project_id, "source": source},
        at=now,
    )
    return {"title": full_title, "project_id": project_id, "status": "OPEN"}


def _close_decision(
    root: Path,
    title: str,
    *,
    new_status: str,
    note_label: str,
    note: str,
    now: datetime | None,
    event_type: str,
) -> dict[str, Any]:
    title = " ".join(title.split())
    note = defuse_markdown(note.strip())
    if not title:
        raise ValueError("a decision title is required")

    path = decisions_path(root)
    if not path.exists():
        raise ValueError("human-decisions/open.md does not exist")

    with _exclusive(root):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_section = False
        closed = False
        out: list[str] = []
        for line in lines:
            section = SECTION_RE.match(line)
            if section:
                in_section = section.group(1).strip() == title
            # same regex the tick classifier uses, so nothing can disagree with
            # the loop about which decisions are open
            if in_section and not closed and OPEN_STATUS_RE.match(line):
                out.append(f"Status: `{new_status}`")
                out.append("")
                out.append(f"**{note_label}** ({isoformat(now)}): {note}" if note else f"**{note_label}** ({isoformat(now)})")
                closed = True
                continue
            out.append(line)
        if not closed:
            raise ValueError(f"no open decision titled {title!r} (see `arthur decision list`)")
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    QueueLedger(root).append_event(DECISION_EVENT_JOB_ID, event_type, {"title": title}, at=now)
    return {"title": title, "status": new_status}


def answer_decision(root: Path, title: str, answer: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Mark one open decision ANSWERED, recording the answer inline; unblocks the project."""

    if not answer.strip():
        raise ValueError("an answer is required (use `arthur decision clear` to withdraw without one)")
    return _close_decision(
        root, title, new_status="ANSWERED", note_label="Answer", note=answer, now=now, event_type="decision_answered"
    )


def clear_decision(root: Path, title: str, *, note: str = "", now: datetime | None = None) -> dict[str, Any]:
    """Withdraw an open decision without answering it (opened by mistake, resolved elsewhere)."""

    return _close_decision(
        root,
        title,
        new_status="CLEARED",
        note_label="Cleared",
        note=note or "withdrawn without an answer",
        now=now,
        event_type="decision_cleared",
    )
