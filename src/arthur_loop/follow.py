"""Drive a created loop: claim → invoke → submit → capture → poll → gate.

`arthur follow` is the auto-follow path. Coding-agent CLIs
(`grok --always-approve -p`, `claude -p`, `codex exec`) run the hop. Manual
and ChatGPT-browser hops still need a human to
produce the reply — follow claims, writes the inbox, and stops honestly.

Human gates that remain (minimized, documented):
- open human decisions (the project is paused on purpose)
- ChatGPT browser / manual adapters (no headless browser ships here)
- implementation-gate NO-GO (follow will not invent APPROVE_PLAN)
- missing or unauthenticated agent CLI
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from arthur_loop.artifact_store import ARTIFACT_KINDS, implementation_gate, save_chatgpt_artifact
from arthur_loop.browser_lock import release_lock
from arthur_loop.config import load_config
from arthur_loop.loop_ops import (
    _next_job_id,
    _slug,
    claim_job,
    ensure_project_not_paused,
    seed_first_prompt,
    submit_job,
)
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.tick import classify_tick, open_human_decision_projects


HOLDER = "arthur-follow"
FOLLOW_DIR = Path("runtime/follow")

KIND_BY_REVIEW = {
    "NEXT_PLAN_REQUEST": "next-plan-request",
    "CODEX_PLAN": "plan",
    "PLAN_APPROVAL": "plan-review",
    "CODEX_IMPLEMENTATION_HANDOFF": "implementation-handoff",
    "SPRINT_REVIEW": "sprint-review",
}

PROMPT_FOR_KIND = {
    "next-plan-request": ("advisor", "prompts/next-plan-request.md"),
    "plan": ("executor", "prompts/plan-only.md"),
    "plan-review": ("advisor", "prompts/plan-approval-review.md"),
    "implementation-handoff": ("executor", "prompts/implementation-handoff.md"),
    "sprint-review": ("advisor", "prompts/sprint-review.md"),
}

ROLE_FOR_KIND = {
    "next-plan-request": "advisor",
    "plan": "executor",
    "plan-review": "advisor",
    "implementation-handoff": "executor",
    "sprint-review": "advisor",
}

# after a trusted capture, enqueue the next hop when the control block says so
NEXT_HOP: dict[tuple[str, str], str | None] = {
    ("next-plan-request", "REQUEST_CODEX_PLAN"): "plan",
    ("plan", "READY_FOR_CHATGPT_REVIEW"): "plan-review",
    ("plan-review", "APPROVE_PLAN"): "implementation-handoff",
    ("plan-review", "REVISE_PLAN"): "plan",
    ("implementation-handoff", "COMPLETE"): "sprint-review",
    ("sprint-review", "FIX_REQUIRED"): "implementation-handoff",
    ("sprint-review", "APPROVE_SPRINT"): "next-plan-request",
}

Invoker = Callable[[Path, dict[str, Any]], dict[str, Any]]


def follow_dir(root: Path) -> Path:
    path = root / FOLLOW_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_kind(root: Path, job: QueueJob) -> str:
    """Kind for this hop: follow_hop event, then idempotency prefix, else next-plan-request."""

    for event in _events_for(root, job.job_id):
        kind = (event.get("data") or {}).get("kind")
        if kind in ARTIFACT_KINDS:
            return str(kind)
    key = job.idempotency_key or ""
    if key.startswith("follow:") and key.count(":") >= 2:
        maybe = key.split(":")[2]
        if maybe in ARTIFACT_KINDS:
            return maybe
    return "next-plan-request"


def _events_for(root: Path, job_id: str) -> list[dict[str, Any]]:
    from arthur_loop.queue_ledger import read_jsonl

    return [event for event in read_jsonl(root / "queue/events.jsonl") if event.get("job_id") == job_id]


def adapter_for_role(config: dict[str, Any], role: str) -> str:
    section = config.get(role) or {}
    return str(section.get("adapter") or "manual")


def transport_argv(adapter: str, prompt: str) -> list[str] | None:
    """Non-interactive CLI for one adapter. None means a human must produce the reply."""

    if adapter == "grok":
        from arthur_loop.grok_client import prompt_argv

        return prompt_argv(prompt)
    if adapter == "claude-code":
        return ["claude", "-p", prompt]
    if adapter == "codex":
        return ["codex", "exec", prompt]
    return None


def default_invoke(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Run the configured CLI, or write an inbox and stop for a human."""

    adapter = request["adapter"]
    prompt = request["prompt"]
    job_id = request["job_id"]
    argv = transport_argv(adapter, prompt)
    dest = follow_dir(root) / f"{job_id}.out.md"
    inbox = follow_dir(root) / f"{job_id}.inbox.md"
    if argv is None:
        inbox.write_text(prompt, encoding="utf-8")
        return {
            "status": "needs_human",
            "adapter": adapter,
            "inbox": inbox.relative_to(root).as_posix(),
            "note": (
                f"{adapter} has no non-interactive transport in Arthur. "
                f"Prompt is at {inbox.relative_to(root).as_posix()}. "
                "Paste the reply into that file's sibling .out.md or run capture yourself."
            ),
        }
    binary = argv[0]
    if not shutil.which(binary):
        inbox.write_text(prompt, encoding="utf-8")
        return {
            "status": "needs_human",
            "adapter": adapter,
            "inbox": inbox.relative_to(root).as_posix(),
            "note": f"{binary} is not on PATH; wrote the prompt inbox. Install the CLI or capture a reply by hand.",
        }
    try:
        proc = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            cwd=str(root),
            timeout=float(request.get("timeout") or 120),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "adapter": adapter, "error": str(exc), "argv": argv}
    text = proc.stdout or ""
    dest.write_text(text if text.strip() else (proc.stderr or ""), encoding="utf-8")
    return {
        "status": "ok" if proc.returncode == 0 and dest.stat().st_size else "error",
        "adapter": adapter,
        "argv": argv[:3],
        "returncode": proc.returncode,
        "output": dest.relative_to(root).as_posix(),
        "stderr_head": (proc.stderr or "")[:400],
    }


def _prompt_text(root: Path, job: QueueJob, kind: str) -> str:
    if job.prompt_path:
        path = root / job.prompt_path
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return seed_hop_prompt(root, project_id=job.project_id, job_id=job.job_id, kind=kind)


def seed_hop_prompt(
    root: Path,
    *,
    project_id: str,
    job_id: str,
    kind: str,
    marker: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    """Render a hop prompt from the instance adapter pack when present."""

    dest = root / "queue" / "prompts" / f"{job_id.lower()}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    spec = PROMPT_FOR_KIND.get(kind)
    text = ""
    if spec:
        rel = root / "adapters" / spec[0] / spec[1]
        if rel.is_file():
            text = rel.read_text(encoding="utf-8")
    if not text:
        if kind == "next-plan-request":
            rel = seed_first_prompt(
                root,
                project_id=project_id,
                job_id=job_id,
                marker=marker or f"{project_id}_LOOP_{_slug(job_id)}",
                idempotency_key=idempotency_key or f"follow:{project_id}:{kind}:{job_id}",
            )
            return (root / rel).read_text(encoding="utf-8")
        text = (
            f"# {job_id} — {kind}\n\n"
            f"Arthur Loop {kind} hop for {project_id}.\n"
            f"Return a valid control block for this hop.\n"
        )
    replacements = {
        "{{PROJECT_ID}}": project_id,
        "{{CURRENT_STATE_SUMMARY}}": f"{project_id} — {kind} hop.",
        "{{EXPECTED_MARKER}}": marker or f"{project_id}_{_slug(kind)}_{_slug(job_id)}",
        "{{IDEMPOTENCY_KEY}}": idempotency_key or f"follow:{project_id}:{kind}:{job_id}",
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    dest.write_text(text, encoding="utf-8")
    return dest.read_text(encoding="utf-8")


def enqueue_hop(
    root: Path,
    *,
    project_id: str,
    kind: str,
    title: str | None = None,
    actor: str | None = None,
) -> QueueJob:
    job_id = _next_job_id(root, project_id)
    marker = f"{project_id}_{_slug(kind)}_{_slug(job_id)}"
    key = f"follow:{project_id}:{kind}:{job_id}"
    prompt_path = seed_first_prompt(
        root,
        project_id=project_id,
        job_id=job_id,
        marker=marker,
        idempotency_key=key,
    )
    # overwrite with the hop-specific pack when we have one
    seed_hop_prompt(
        root,
        project_id=project_id,
        job_id=job_id,
        kind=kind,
        marker=marker,
        idempotency_key=key,
    )
    job = QueueJob(
        job_id=job_id,
        project_id=project_id,
        target_chat_title=title or f"{project_id} {kind}",
        target_chat_url="follow",
        prompt_path=prompt_path,
        expected_marker=marker,
        idempotency_key=key,
    )
    ledger = QueueLedger(root)
    ledger.create_job(job)
    ledger.append_event(job_id, "follow_hop", {"kind": kind, "actor": actor or HOLDER})
    return job


def _pick_job(root: Path, project_id: str | None) -> QueueJob | None:
    paused = set(open_human_decision_projects(root))
    jobs = list(QueueLedger(root).latest_jobs().values())
    if project_id:
        jobs = [job for job in jobs if job.project_id == project_id]
    actionable = []
    for job in jobs:
        if job.project_id in paused:
            continue
        if job.status in {"queued", "claimed", "submitted", "waiting_for_chatgpt"}:
            actionable.append(job)
    if not actionable:
        return None
    order = {"queued": 0, "claimed": 1, "submitted": 2, "waiting_for_chatgpt": 3}
    actionable.sort(key=lambda job: (order.get(job.status, 9), job.priority, job.created_at, job.job_id))
    return actionable[0]


def _human_gates(root: Path, project_id: str | None) -> list[str]:
    paused = open_human_decision_projects(root)
    if project_id:
        return [pid for pid in paused if pid == project_id]
    return list(paused)


def follow_step(
    root: Path,
    *,
    project_id: str | None = None,
    holder: str = HOLDER,
    invoke: Invoker | None = None,
    chain: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One auto-follow step. Returns a JSON-serializable record."""

    invoke = invoke or default_invoke
    config = load_config(root)
    tick = classify_tick(root, dry_run=True, quota_enabled=bool(config["components"]["resource_governor"]))
    paused = _human_gates(root, project_id)
    if tick.status == "BLOCKED_BY_QUOTA":
        return {"action": "stop", "reason": "quota", "tick": tick.to_record(), "human_gates": paused}
    if paused and not _pick_job(root, project_id):
        return {
            "action": "stop",
            "reason": "human_decision",
            "human_gates": paused,
            "note": "An open human decision pauses that project. Answer it (`arthur decision answer`) to continue.",
        }

    job = _pick_job(root, project_id)
    if job is None:
        completed = [
            item
            for item in QueueLedger(root).latest_jobs().values()
            if item.status == "completed" and (not project_id or item.project_id == project_id)
        ]
        gate_record = None
        if completed:
            pid = completed[-1].project_id
            gate_record = implementation_gate(root, pid).to_record()
        return {
            "action": "idle",
            "reason": "no_actionable_jobs",
            "gate": gate_record,
            "human_gates": paused,
            "honest_copy": (
                "Nothing to claim. Remaining human gates: open decisions, "
                "NO-GO implementation gates, and hops whose adapter has no CLI transport."
            ),
        }

    kind = job_kind(root, job)
    role = ROLE_FOR_KIND.get(kind, "advisor")
    adapter = adapter_for_role(config, role)
    step: dict[str, Any] = {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "status_before": job.status,
        "kind": kind,
        "role": role,
        "adapter": adapter,
    }
    if dry_run:
        step["action"] = "dry_run"
        return step

    if job.status == "queued":
        ensure_project_not_paused(root, job.project_id)
        job = claim_job(root, job.job_id, holder=holder)
        step["claimed"] = job.to_record()

    if job.status == "claimed":
        prompt = _prompt_text(root, job, kind)
        invoked = invoke(
            root,
            {
                "job_id": job.job_id,
                "project_id": job.project_id,
                "kind": kind,
                "role": role,
                "adapter": adapter,
                "prompt": prompt,
            },
        )
        step["invoke"] = {k: v for k, v in invoked.items() if k != "prompt"}
        if invoked.get("status") == "needs_human":
            step["action"] = "needs_human"
            step["human_gates"] = [
                f"{adapter} transport is human-gated; inbox at {invoked.get('inbox')}"
            ]
            return step
        if invoked.get("status") != "ok":
            step["action"] = "invoke_failed"
            return step
        job = submit_job(root, job.job_id, holder=holder, keep_lock=True)
        step["submitted"] = job.to_record()

    if job.status in {"submitted", "waiting_for_chatgpt"}:
        out_path = follow_dir(root) / f"{job.job_id}.out.md"
        if not out_path.is_file() or not out_path.read_text(encoding="utf-8").strip():
            step["action"] = "waiting_for_output"
            step["note"] = f"write {out_path.relative_to(root).as_posix()} and re-run arthur follow --once"
            return step
        artifact = save_chatgpt_artifact(
            root,
            project_id=job.project_id,
            job_id=job.job_id,
            kind=kind,
            source_chat_title=adapter,
            text=out_path.read_text(encoding="utf-8"),
        )
        step["capture"] = artifact.to_record()
        if artifact.needs_human:
            try:
                release_lock(root, holder)
            except Exception:
                pass
            step["action"] = "needs_human"
            step["human_gates"] = [artifact.escalated_decision or "capture opened a human decision"]
            return step
        marker_found = True
        if job.expected_marker:
            marker_found = job.expected_marker in out_path.read_text(encoding="utf-8")
        from arthur_loop.loop_ops import poll_job

        job = poll_job(
            root,
            job.job_id,
            marker_found=marker_found,
            status="completed",
            holder=holder,
            keep_lock=True,
        )
        try:
            release_lock(root, holder)
        except Exception:
            pass
        step["completed"] = job.to_record()
        gate = implementation_gate(root, job.project_id)
        step["gate"] = gate.to_record()
        decision = (artifact.decision or "").strip()
        next_kind = NEXT_HOP.get((kind, decision)) if chain else None
        if next_kind == "implementation-handoff" and not gate.go:
            step["action"] = "gate_no_go"
            step["note"] = (
                "Plan-review did not produce a GO implementation gate (or a later hop re-opened planning). "
                "Follow will not invent APPROVE_PLAN."
            )
            return step
        if next_kind:
            nxt = enqueue_hop(root, project_id=job.project_id, kind=next_kind, actor=holder)
            step["enqueued"] = nxt.to_record()
            step["enqueued_kind"] = next_kind
        step["action"] = "advanced"
        return step

    step["action"] = "noop"
    return step


def follow_loop(
    root: Path,
    *,
    once: bool = False,
    max_steps: int = 12,
    project_id: str | None = None,
    holder: str = HOLDER,
    invoke: Invoker | None = None,
    chain: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one or more follow steps until idle, human-gated, or max_steps."""

    steps: list[dict[str, Any]] = []
    limit = 1 if once else max(1, max_steps)
    stopped = "max_steps"
    for _ in range(limit):
        step = follow_step(
            root,
            project_id=project_id,
            holder=holder,
            invoke=invoke,
            chain=chain,
            dry_run=dry_run,
        )
        steps.append(step)
        action = str(step.get("action") or "noop")
        if action in {"idle", "stop", "needs_human", "invoke_failed", "waiting_for_output", "gate_no_go"}:
            stopped = action
            break
        if dry_run:
            stopped = "dry_run"
            break
        if once or action != "advanced":
            stopped = action
            break
    log = follow_dir(root) / "log.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"steps": len(steps), "last": steps[-1] if steps else None}) + "\n")
    return {
        "steps": steps,
        "stopped": stopped,
        "once": once,
        "human_gates_remaining": [
            "open human decisions pause their project",
            "chatgpt-browser and manual adapters write an inbox instead of invoking a CLI",
            "implementation gate NO-GO (follow does not forge APPROVE_PLAN)",
            "missing or failing agent CLI",
        ],
        "honest_copy": (
            "Auto-follow drives claim → invoke → submit → capture → complete → gate "
            "and enqueues the next hop from a trusted control block. "
            "It does not browse ChatGPT and it does not skip a human decision."
        ),
    }
