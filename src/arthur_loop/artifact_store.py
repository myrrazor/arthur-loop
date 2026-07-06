from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from arthur_loop.queue_ledger import QueueLedger, append_jsonl, isoformat, read_jsonl


CONTROL_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*:\s*(.*?)\s*$")
FENCED_BLOCK_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.DOTALL)
SLUG_RE = re.compile(r"[^a-z0-9]+")
CONTROL_ENUMS = {
    "approval_decision": {
        "REQUEST_CODEX_PLAN",
        "APPROVE_PLAN",
        "REVISE_PLAN",
        "APPROVE_SPRINT",
        "FIX_REQUIRED",
        "RELEASE_READY",
        "HUMAN_INPUT_REQUIRED",
    },
    "plan_status": {"READY_FOR_CHATGPT_REVIEW", "HUMAN_INPUT_REQUIRED"},
    "implementation_status": {"COMPLETE", "BLOCKED", "HUMAN_INPUT_REQUIRED"},
    "has_p0_p1": {"true", "false"},
    "plan_has_p0_p1": {"true", "false"},
    "implementation_started": {"true", "false"},
    "tests_run": {"true", "false"},
    "ready_for_chatgpt_review": {"true", "false"},
}


def slugify(value: str) -> str:
    """Return a compact filesystem-safe slug."""

    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "artifact"


def compact_timestamp(value: str) -> str:
    """Turn an ISO timestamp into a filename-friendly UTC-ish timestamp."""

    return value.replace("-", "").replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")


@dataclass(frozen=True)
class ControlBlockParse:
    """Validated control-block fields extracted from a response."""

    fields: dict[str, str]
    valid: bool
    reasons: list[str]
    source: str

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable parse record."""

        return asdict(self)


def _last_fenced_block(text: str) -> str | None:
    matches = FENCED_BLOCK_RE.findall(text)
    return matches[-1] if matches else None


def _last_contiguous_control_run(text: str) -> str | None:
    runs: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if CONTROL_LINE_RE.match(line):
            current.append(line)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        return None
    return "\n".join(runs[-1])


def _control_source(text: str) -> tuple[str, str]:
    fenced = _last_fenced_block(text)
    if fenced is not None:
        return fenced, "last_fenced_block"
    run = _last_contiguous_control_run(text)
    if run is not None:
        return run, "last_contiguous_control_run"
    return "", "missing"


def parse_control_block_result(text: str) -> ControlBlockParse:
    """Extract and validate the final control block from ChatGPT/Codex text."""

    block, source = _control_source(text)
    raw_fields: dict[str, str] = {}
    fields: dict[str, str] = {}
    reasons: list[str] = []
    seen: set[str] = set()

    for line in block.splitlines():
        match = CONTROL_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if key in seen:
            reasons.append(f"duplicate control field: {match.group(1)}")
        seen.add(key)
        raw_fields[key] = value

    if not raw_fields:
        reasons.append("no control fields found in final response block")

    for key, value in raw_fields.items():
        allowed = CONTROL_ENUMS.get(key)
        if allowed is not None and value not in allowed:
            reasons.append(f"invalid {key}: {value}")
            continue
        fields[key] = value

    return ControlBlockParse(fields=fields, valid=not reasons, reasons=reasons, source=source)


def parse_control_block(text: str) -> dict[str, str]:
    """Return sanitized fields from the final control block."""

    return parse_control_block_result(text).fields


@dataclass(frozen=True)
class ChatGptArtifact:
    """A lightweight saved ChatGPT browser response."""

    artifact_id: str
    project_id: str
    job_id: str
    kind: str
    source_chat_title: str
    created_at: str
    path: str
    title: str
    review_type: str | None = None
    approval_decision: str | None = None
    has_p0_p1: str | None = None
    plan_status: str | None = None
    control_block_valid: bool = True
    control_block_source: str = ""
    control_block_reasons: list[str] | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable index record."""

        return asdict(self)


def frontmatter_for(artifact: ChatGptArtifact) -> str:
    """Render small frontmatter for a saved artifact."""

    fields = {
        "artifact_id": artifact.artifact_id,
        "project_id": artifact.project_id,
        "job_id": artifact.job_id,
        "kind": artifact.kind,
        "source_chat_title": artifact.source_chat_title,
        "created_at": artifact.created_at,
        "review_type": artifact.review_type,
        "approval_decision": artifact.approval_decision,
        "has_p0_p1": artifact.has_p0_p1,
        "plan_status": artifact.plan_status,
        "control_block_valid": str(artifact.control_block_valid).lower(),
        "control_block_source": artifact.control_block_source,
    }
    if artifact.control_block_reasons:
        fields["control_block_reasons"] = "; ".join(artifact.control_block_reasons)
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def project_chatgpt_dir(root: Path, project_id: str) -> Path:
    """Return the project-local ChatGPT artifact directory."""

    return root / "projects" / project_id / "artifacts" / "chatgpt"


def save_chatgpt_artifact(
    root: Path,
    *,
    project_id: str,
    job_id: str,
    kind: str,
    source_chat_title: str,
    text: str,
    created_at: str | None = None,
    title: str | None = None,
    link_queue: bool = True,
) -> ChatGptArtifact:
    """Save ChatGPT text to a project artifact and update the small index."""

    created_at = created_at or isoformat()
    control = parse_control_block_result(text)
    fields = control.fields
    artifact_title = title or kind.replace("-", " ").title()
    artifact_dir = project_chatgpt_dir(root, project_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    timestamp = compact_timestamp(created_at)
    base_name = f"{timestamp}-{slugify(kind)}"
    path, artifact_id = _unique_artifact_path(artifact_dir, base_name, project_id, kind, timestamp)
    rel_path = path.relative_to(root).as_posix()
    artifact = ChatGptArtifact(
        artifact_id=artifact_id,
        project_id=project_id,
        job_id=job_id,
        kind=kind,
        source_chat_title=source_chat_title,
        created_at=created_at,
        path=rel_path,
        title=artifact_title,
        review_type=fields.get("review_type"),
        approval_decision=fields.get("approval_decision"),
        has_p0_p1=fields.get("has_p0_p1") or fields.get("plan_has_p0_p1"),
        plan_status=fields.get("plan_status"),
        control_block_valid=control.valid,
        control_block_source=control.source,
        control_block_reasons=control.reasons,
    )

    body = text.rstrip() + "\n"
    path.write_text(frontmatter_for(artifact) + body, encoding="utf-8")
    append_jsonl(artifact_dir / "index.jsonl", artifact.to_record())
    render_project_chatgpt_index(root, project_id)
    if link_queue:
        link_artifact_to_queue(root, job_id, rel_path)
    return artifact


def _unique_artifact_path(
    artifact_dir: Path,
    base_name: str,
    project_id: str,
    kind: str,
    timestamp: str,
) -> tuple[Path, str]:
    suffix = 1
    while True:
        suffix_part = "" if suffix == 1 else f"-{suffix}"
        path = artifact_dir / f"{base_name}{suffix_part}.md"
        if not path.exists():
            artifact_id = f"{project_id}-{slugify(kind)}-{timestamp}{suffix_part}"
            return path, artifact_id
        suffix += 1


def latest_artifacts(root: Path, project_id: str) -> list[ChatGptArtifact]:
    """Read the latest artifact record for each artifact id."""

    index_path = project_chatgpt_dir(root, project_id) / "index.jsonl"
    latest: dict[str, ChatGptArtifact] = {}
    for record in read_jsonl(index_path):
        latest[record["artifact_id"]] = ChatGptArtifact(**record)
    return sorted(latest.values(), key=lambda item: item.created_at, reverse=True)


def render_project_chatgpt_index(root: Path, project_id: str) -> Path:
    """Render the small markdown index future agents should read first."""

    artifact_dir = project_chatgpt_dir(root, project_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {project_id} ChatGPT Artifact Index",
        "",
        "Read this index before opening full ChatGPT response files. It is the cheap lookup table for browser-produced text.",
        "",
        "| Created | Kind | Decision | P0/P1 | Artifact |",
        "| --- | --- | --- | --- | --- |",
    ]
    artifacts = latest_artifacts(root, project_id)
    if not artifacts:
        lines.append("| _none_ | _none_ | _none_ | _none_ | _none_ |")
    for artifact in artifacts:
        decision = artifact.approval_decision or artifact.plan_status or ""
        p0 = artifact.has_p0_p1 or ""
        name = Path(artifact.path).name
        lines.append(
            f"| {artifact.created_at} | {artifact.kind} | {decision} | {p0} | [{name}]({name}) |"
        )

    path = artifact_dir / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def link_artifact_to_queue(root: Path, job_id: str, artifact_path: str) -> None:
    """Best-effort link from queue job to a saved artifact path."""

    ledger = QueueLedger(root)
    jobs = ledger.latest_jobs()
    job = jobs.get(job_id)
    if not job:
        return
    if artifact_path not in job.output_artifact_paths:
        job.output_artifact_paths.append(artifact_path)
        ledger.record_job(job)
    ledger.append_event(job_id, "artifact_saved", {"path": artifact_path})
