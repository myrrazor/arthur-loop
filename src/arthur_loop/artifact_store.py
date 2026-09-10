from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from arthur_loop.decisions import open_decision
from arthur_loop.queue_ledger import QueueLedger, append_jsonl, isoformat, parse_ledger_time, read_jsonl
from arthur_loop.tick import open_human_decision_projects


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
    "review_type": {
        "NEXT_PLAN_REQUEST",
        "CODEX_PLAN",
        "PLAN_APPROVAL",
        "CODEX_IMPLEMENTATION_HANDOFF",
        "SPRINT_REVIEW",
        "HUMAN_DECISION_ESCALATION",
    },
    "has_p0_p1": {"true", "false"},
    "plan_has_p0_p1": {"true", "false"},
    "implementation_started": {"true", "false"},
    "tests_run": {"true", "false"},
    "ready_for_chatgpt_review": {"true", "false"},
}

# `arthur capture --kind` vocabulary: each kind is one hop of the loop and maps
# to the REVIEW_TYPE its control block must carry plus the field that carries
# the automation-driving decision. A review with no decision is not a decision.
ARTIFACT_KINDS: dict[str, dict[str, str]] = {
    "next-plan-request": {"review_type": "NEXT_PLAN_REQUEST", "decision_field": "approval_decision"},
    "plan": {"review_type": "CODEX_PLAN", "decision_field": "plan_status"},
    "plan-review": {"review_type": "PLAN_APPROVAL", "decision_field": "approval_decision"},
    "implementation-handoff": {
        "review_type": "CODEX_IMPLEMENTATION_HANDOFF",
        "decision_field": "implementation_status",
    },
    "sprint-review": {"review_type": "SPRINT_REVIEW", "decision_field": "approval_decision"},
    "human-decision": {"review_type": "HUMAN_DECISION_ESCALATION", "decision_field": ""},
}

# a saved artifact carrying one of these stops its project until a human answers
HUMAN_INPUT = "HUMAN_INPUT_REQUIRED"


def validate_kind(kind: str) -> None:
    """Reject artifact kinds outside the loop's vocabulary."""

    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}; expected one of: {', '.join(ARTIFACT_KINDS)}")


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


def parse_control_block_result(
    text: str,
    *,
    kind: str | None = None,
    project_id: str | None = None,
) -> ControlBlockParse:
    """Extract and validate the final control block from advisor/executor text.

    `kind` and `project_id` enable the cross-field rules: the block must belong
    to the hop it was captured as, to the project it was captured for, must
    carry that hop's decision field, and a plan must never report that
    implementation already started.
    """

    if kind is not None:
        validate_kind(kind)
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

    if fields.get("implementation_started") == "true" and fields.get("review_type") != "CODEX_IMPLEMENTATION_HANDOFF":
        reasons.append(
            "implementation_started: true — implementation from a plan-only packet is forbidden"
        )

    if project_id and fields.get("project_id") and fields["project_id"] != project_id:
        reasons.append(f"project_id {fields['project_id']} does not match captured project {project_id}")

    if kind is not None and raw_fields:
        spec = ARTIFACT_KINDS[kind]
        review_type = fields.get("review_type")
        if review_type and review_type != spec["review_type"]:
            reasons.append(
                f"review_type {review_type} does not match kind {kind} (expected {spec['review_type']})"
            )
        decision_field = spec["decision_field"]
        if decision_field and decision_field not in raw_fields:
            reasons.append(f"missing {decision_field} for kind {kind}")

    return ControlBlockParse(fields=fields, valid=not reasons, reasons=reasons, source=source)


def parse_control_block(text: str) -> dict[str, str]:
    """Return sanitized fields from the final control block."""

    return parse_control_block_result(text).fields


@dataclass(frozen=True)
class ChatGptArtifact:
    """A saved advisor/executor response (class name is historical; see docs)."""

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
    implementation_status: str | None = None
    # title of the human decision this artifact opened, if it stopped its project
    escalated_decision: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a JSON-serializable index record."""

        return asdict(self)

    @property
    def decision(self) -> str | None:
        """The automation-driving value of this hop, whatever field carries it."""

        return self.approval_decision or self.plan_status or self.implementation_status

    @property
    def needs_human(self) -> bool:
        """True when the loop must stop this project for a human."""

        return not self.control_block_valid or self.decision == HUMAN_INPUT


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
        "implementation_status": artifact.implementation_status,
        "control_block_valid": str(artifact.control_block_valid).lower(),
        "control_block_source": artifact.control_block_source,
    }
    if artifact.control_block_reasons:
        fields["control_block_reasons"] = "; ".join(artifact.control_block_reasons)
    if artifact.escalated_decision:
        fields["escalated_decision"] = artifact.escalated_decision
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
    escalate: bool = True,
) -> ChatGptArtifact:
    """Save advisor/executor text as a project artifact and update the small index.

    Saving never fails on bad content — every response is kept as evidence.
    What changes is trust: an invalid control block, a HUMAN_INPUT_REQUIRED
    decision, or an implementation handoff with no approved plan on record is
    quarantined *and* (unless `escalate` is off) opens a human decision that
    pauses the project until someone answers it.
    """

    validate_kind(kind)
    created_at = created_at or isoformat()
    control = parse_control_block_result(text, kind=kind, project_id=project_id)
    reasons = list(control.reasons)
    if kind == "implementation-handoff":
        # the approval gate is enforced where evidence enters the loop, not by prompt
        gate = implementation_gate(root, project_id)
        if not gate.go:
            reasons.extend(f"implementation gate: {reason}" for reason in gate.reasons)
    valid = not reasons
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
        implementation_status=fields.get("implementation_status"),
        control_block_valid=valid,
        control_block_source=control.source,
        control_block_reasons=reasons,
    )

    if escalate and artifact.needs_human:
        decision = _escalate(root, artifact, now=parse_ledger_time(created_at))
        artifact = ChatGptArtifact(**{**artifact.to_record(), "escalated_decision": decision})

    body = text.rstrip() + "\n"
    path.write_text(frontmatter_for(artifact) + body, encoding="utf-8")
    append_jsonl(artifact_dir / "index.jsonl", artifact.to_record())
    render_project_chatgpt_index(root, project_id)
    if link_queue:
        link_artifact_to_queue(root, job_id, rel_path)
    return artifact


def _escalate(root: Path, artifact: ChatGptArtifact, *, now: datetime | None) -> str | None:
    """Open the human decision that pauses this project; return its title."""

    if artifact.control_block_valid:
        title = f"Human input required — {artifact.kind} {artifact.job_id}"
        body = (
            f"`{artifact.path}` (kind `{artifact.kind}`, job `{artifact.job_id}`) returned "
            f"{HUMAN_INPUT}. Read its Human Input Required section, decide, and answer here — "
            "the project stays paused until you do."
        )
    else:
        title = f"Quarantined artifact — {artifact.kind} {artifact.job_id}"
        body = (
            f"`arthur capture` saved `{artifact.path}` (kind `{artifact.kind}`, job `{artifact.job_id}`) "
            "but will not act on it: "
            + "; ".join(artifact.control_block_reasons or [])
            + ". Re-run the hop with a corrected response, or record the call a human is making, then answer here."
        )
    try:
        record = open_decision(
            root,
            project_id=artifact.project_id,
            title=title,
            body=body,
            now=now,
            source=f"capture:{artifact.artifact_id}",
        )
    except ValueError:
        # already open for this exact hop (a retry captured the same response twice)
        return f"{artifact.project_id} {title}"
    return str(record["title"])


@dataclass(frozen=True)
class GateResult:
    """GO / NO-GO for one gate, derived only from durable state."""

    gate: str
    project_id: str
    go: bool
    reasons: list[str]
    approval_artifact: str | None = None
    approved_at: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {**asdict(self), "verdict": "GO" if self.go else "NO-GO"}


def implementation_gate(root: Path, project_id: str) -> GateResult:
    """May implementation start for this project right now?

    GO requires, in the saved artifacts alone: the newest plan-review is valid
    and says APPROVE_PLAN; nothing since then re-opened planning (a new plan or
    next-plan-request) or closed the sprint (APPROVE_SPRINT / RELEASE_READY);
    and no human decision is open for the project. Prompts still ask executors
    to behave — this is what the loop checks regardless of whether they do.
    """

    reasons: list[str] = []
    # capture order, oldest first: "newer" means captured after, never a timestamp tie-break
    artifacts = artifacts_in_capture_order(root, project_id)
    approval_index = next(
        (index for index in range(len(artifacts) - 1, -1, -1) if artifacts[index].kind == "plan-review"), None
    )
    approval = artifacts[approval_index] if approval_index is not None else None

    if approval is None:
        reasons.append("no plan-review artifact on record — the advisor has not reviewed a plan")
    elif not approval.control_block_valid:
        reasons.append(f"latest plan-review {approval.artifact_id} is quarantined (control block invalid)")
    elif approval.approval_decision != "APPROVE_PLAN":
        reasons.append(
            f"latest plan-review {approval.artifact_id} says {approval.approval_decision}, not APPROVE_PLAN"
        )
    else:
        for item in artifacts[approval_index + 1 :]:
            if item.kind in {"plan", "next-plan-request"}:
                reasons.append(
                    f"{item.kind} {item.artifact_id} is newer than the approval — planning re-opened, "
                    "get a fresh APPROVE_PLAN"
                )
            elif item.kind == "sprint-review" and item.approval_decision in {"APPROVE_SPRINT", "RELEASE_READY"}:
                reasons.append(
                    f"sprint-review {item.artifact_id} closed the approved sprint ({item.approval_decision}) — "
                    "the next sprint needs its own approved plan"
                )
            elif not item.control_block_valid:
                reasons.append(f"{item.kind} {item.artifact_id} is quarantined and newer than the approval")

    if project_id in open_human_decision_projects(root):
        reasons.append(f"{project_id} has an open human decision (see `arthur decision list`)")

    return GateResult(
        gate="implementation",
        project_id=project_id,
        go=not reasons,
        reasons=reasons,
        approval_artifact=approval.artifact_id if approval else None,
        approved_at=approval.created_at if approval else None,
    )


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


def artifacts_in_capture_order(root: Path, project_id: str) -> list[ChatGptArtifact]:
    """Latest record per artifact id, in the order artifacts were first captured."""

    index_path = project_chatgpt_dir(root, project_id) / "index.jsonl"
    latest: dict[str, ChatGptArtifact] = {}
    for record in read_jsonl(index_path):
        # tolerate index rows written before newer optional fields existed
        known = {key: value for key, value in record.items() if key in ChatGptArtifact.__dataclass_fields__}
        latest[record["artifact_id"]] = ChatGptArtifact(**known)
    return list(latest.values())


def latest_artifacts(root: Path, project_id: str) -> list[ChatGptArtifact]:
    """Read the latest artifact record for each artifact id, newest first."""

    return sorted(artifacts_in_capture_order(root, project_id), key=lambda item: item.created_at, reverse=True)


def render_project_chatgpt_index(root: Path, project_id: str) -> Path:
    """Render the small markdown index future agents should read first."""

    artifact_dir = project_chatgpt_dir(root, project_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {project_id} Advisor Artifact Index",
        "",
        "Read this index before opening full advisor/executor response files. It is the cheap lookup table for captured text.",
        "Rows marked QUARANTINED failed control-block validation and opened a human decision; never act on them.",
        "",
        "| Created | Kind | Decision | P0/P1 | Trust | Artifact |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    artifacts = latest_artifacts(root, project_id)
    if not artifacts:
        lines.append("| _none_ | _none_ | _none_ | _none_ | _none_ | _none_ |")
    for artifact in artifacts:
        decision = artifact.decision or ""
        p0 = artifact.has_p0_p1 or ""
        trust = "ok" if artifact.control_block_valid else "QUARANTINED"
        name = Path(artifact.path).name
        lines.append(
            f"| {artifact.created_at} | {artifact.kind} | {decision} | {p0} | {trust} | [{name}]({name}) |"
        )

    path = artifact_dir / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def link_artifact_to_queue(root: Path, job_id: str, artifact_path: str) -> None:
    """Best-effort link from queue job to a saved artifact path."""

    QueueLedger(root).link_artifact(job_id, artifact_path)
