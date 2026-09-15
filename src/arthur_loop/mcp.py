"""Stdio MCP server so coding agents can drive the loop without shelling out."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from arthur_loop.artifact_store import ARTIFACT_KINDS, implementation_gate, save_chatgpt_artifact
from arthur_loop.atlas_board import (
    open_jobs_from_board,
    read_board,
    read_next,
    read_queue,
    resolve_atlas_project,
    walk_next,
)
from arthur_loop.config import load_config, require_instance
from arthur_loop.decisions import answer_decision, list_decisions, open_decision
from arthur_loop.follow import follow_loop
from arthur_loop.loop_ops import claim_job, create_loop, list_loops, submit_job, wizard_options
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.recovery import recover_job
from arthur_loop.status import collect_status, status_to_dict
from arthur_loop.tick import classify_tick


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "arthur-loop"
SERVER_VERSION = "0.1.0"

WRITE_TOOLS = {
    "arthur.queue.create",
    "arthur.queue.claim",
    "arthur.queue.submit",
    "arthur.queue.poll_result",
    "arthur.queue.recover",
    "arthur.capture",
    "arthur.decision.open",
    "arthur.decision.answer",
    "arthur.loop.create",
    "arthur.board.open_jobs",
    "arthur.follow.run",
    "arthur.tracker.walk",
}


def public_tool_name(name: str, style: str) -> str:
    if style == "portable":
        return name.replace(".", "_")
    return name


def canonical_tool_name(name: str) -> str:
    """Accept dotted or portable names."""

    if name in TOOL_HANDLERS:
        return name
    dotted = name.replace("_", ".")
    # arthur.loop.create <-> arthur_loop_create (first two segments stay arthur.*)
    if dotted in TOOL_HANDLERS:
        return dotted
    # portable arthur_queue_poll_result -> arthur.queue.poll_result
    if name.startswith("arthur_"):
        rest = name[len("arthur_") :]
        candidate = "arthur." + rest.replace("_", ".")
        if candidate in TOOL_HANDLERS:
            return candidate
    return name


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _str(desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **extra}


def _bool(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}


def _int(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "arthur.status",
        "description": "Read the loop dashboard (sessions, queue, decisions, quota).",
        "inputSchema": _schema({}),
        "write": False,
    },
    {
        "name": "arthur.tick",
        "description": "Classify the next scheduler action from durable state.",
        "inputSchema": _schema({"dry_run": _bool("Do not write tick-state (default true)")}),
        "write": False,
    },
    {
        "name": "arthur.queue.show",
        "description": "Show one queue job or the whole ledger.",
        "inputSchema": _schema({"job_id": _str("Optional job id")}),
        "write": False,
    },
    {
        "name": "arthur.queue.due",
        "description": "List jobs ready to submit or poll.",
        "inputSchema": _schema({}),
        "write": False,
    },
    {
        "name": "arthur.gate.implementation",
        "description": "GO/NO-GO implementation gate from saved artifacts.",
        "inputSchema": _schema({"project_id": _str("Project id")}, ["project_id"]),
        "write": False,
    },
    {
        "name": "arthur.decision.list",
        "description": "List human decisions. An OPEN decision pauses that project.",
        "inputSchema": _schema({"include_closed": _bool("Include answered/cleared")}),
        "write": False,
    },
    {
        "name": "arthur.board",
        "description": "Read the Atlas Tasker board via `tracker board --json` when tracker is installed.",
        "inputSchema": _schema({"project": _str("Optional Atlas project key (not the Arthur project_id)")}),
        "write": False,
    },
    {
        "name": "arthur.tracker.next",
        "description": "Walk ready work via `tracker next --json` (ready_for_me / unblocked_for_me).",
        "inputSchema": _schema({"actor": _str("Optional Atlas actor")}),
        "write": False,
    },
    {
        "name": "arthur.tracker.queue",
        "description": "Read the Atlas actor queue via `tracker queue --json`.",
        "inputSchema": _schema({"actor": _str("Optional Atlas actor")}),
        "write": False,
    },
    {
        "name": "arthur.loop.list",
        "description": "List projects and current advisor/executor/tracker roles.",
        "inputSchema": _schema({}),
        "write": False,
    },
    {
        "name": "arthur.queue.create",
        "description": "Create a queued job. Duplicate ids, reused idempotency keys, and colliding markers are refused.",
        "inputSchema": _schema(
            {
                "job_id": _str("Job id"),
                "project_id": _str("Project id"),
                "target_chat_title": _str("Advisor conversation title"),
                "target_chat_url": _str("Advisor URL or `manual`"),
                "prompt_path": _str("Optional instance-relative prompt path"),
                "expected_marker": _str("Marker the advisor must echo"),
                "idempotency_key": _str("Stable key so a retry cannot fork the queue"),
                "actor": _str("Who is creating this (audit)"),
                "reason": _str("Why (audit)"),
            },
            ["job_id", "project_id", "target_chat_title", "target_chat_url"],
        ),
        "write": True,
    },
    {
        "name": "arthur.queue.claim",
        "description": "Claim a queued job and take the advisor lease. Refused when the project is paused.",
        "inputSchema": _schema(
            {
                "job_id": _str("Job id"),
                "holder": _str("Lease holder name"),
                "actor": _str("Who is claiming"),
                "reason": _str("Why"),
            },
            ["job_id"],
        ),
        "write": True,
    },
    {
        "name": "arthur.queue.submit",
        "description": "Record that the prompt was sent. Refused when the project is paused.",
        "inputSchema": _schema(
            {
                "job_id": _str("Job id"),
                "holder": _str("Lease holder (must match the claim)"),
                "keep_lock": _bool("Keep the lease if you are about to capture"),
            },
            ["job_id"],
        ),
        "write": True,
    },
    {
        "name": "arthur.queue.poll_result",
        "description": "Record a poll result and transition the job. Refused when the project is paused.",
        "inputSchema": _schema(
            {
                "job_id": _str("Job id"),
                "marker_found": _bool("Whether the expected marker was present"),
                "status": _str("Optional status override"),
                "holder": _str("Lease holder"),
                "keep_lock": _bool("Keep the lease"),
            },
            ["job_id", "marker_found"],
        ),
        "write": True,
    },
    {
        "name": "arthur.queue.recover",
        "description": "Park an abandoned job and release only that manager's lease.",
        "inputSchema": _schema(
            {
                "job_id": _str("Job id"),
                "requeue": _bool("Send the job back to queued after parking"),
                "error": _str("Why it is being recovered"),
            },
            ["job_id"],
        ),
        "write": True,
    },
    {
        "name": "arthur.capture",
        "description": "Save advisor/executor text and validate its control block.",
        "inputSchema": _schema(
            {
                "project_id": _str("Project id"),
                "job_id": _str("Job id"),
                "kind": _str("Hop kind", enum=list(ARTIFACT_KINDS)),
                "source_chat_title": _str("Conversation title or adapter name"),
                "text": _str("Artifact text (required if source_file is omitted)"),
                "source_file": _str("Instance-relative or absolute file to read"),
            },
            ["project_id", "job_id", "kind", "source_chat_title"],
        ),
        "write": True,
    },
    {
        "name": "arthur.decision.open",
        "description": "Open a human decision and pause that project.",
        "inputSchema": _schema(
            {
                "project_id": _str("Project id"),
                "title": _str("Short question"),
                "body": _str("Details"),
            },
            ["project_id", "title"],
        ),
        "write": True,
    },
    {
        "name": "arthur.decision.answer",
        "description": "Answer an open decision and unpause the project.",
        "inputSchema": _schema({"title": _str("Full title from decision.list"), "answer": _str("The human call")}, ["title", "answer"]),
        "write": True,
    },
    {
        "name": "arthur.loop.create",
        "description": "Create a project loop (roles + first queue job). Not a graph composer.",
        "inputSchema": _schema(
            {
                "project_id": _str("SHOUTY_SNAKE project id"),
                "advisor": _str("Advisor adapter"),
                "executor": _str("Executor adapter"),
                "tracker": _str("Tracker adapter"),
                "title": _str("Advisor conversation title"),
                "target_chat_url": _str("Advisor URL or `manual`"),
                "goal": _str("One-line project goal"),
                "seed_job": _bool("Create the first queue job (default true)"),
                "actor": _str("Who is creating the loop"),
                "reason": _str("Why"),
            },
            ["project_id"],
        ),
        "write": True,
    },
    {
        "name": "arthur.board.open_jobs",
        "description": "Open Arthur queue jobs from Atlas ready/in_progress tickets (`tracker board --json`). in_review is not ready.",
        "inputSchema": _schema(
            {
                "project": _str("Optional Atlas project key"),
                "limit": _int("Max tickets to open"),
                "dry_run": _bool("Preview without writing"),
                "actor": _str("Who is opening jobs"),
                "reason": _str("Why"),
            }
        ),
        "write": True,
    },
    {
        "name": "arthur.tracker.walk",
        "description": "Walk tracker next (fallback: board) and open queue jobs for the next ready ticket.",
        "inputSchema": _schema(
            {
                "actor": _str("Atlas actor"),
                "project": _str("Optional Atlas project key"),
                "limit": _int("How many tickets to walk"),
                "dry_run": _bool("Preview without writing"),
                "open_jobs": _bool("Open queue jobs (default true)"),
            }
        ),
        "write": True,
    },
    {
        "name": "arthur.follow.run",
        "description": "Auto-follow: claim → invoke CLI adapter → submit → capture → gate. Stops on human gates.",
        "inputSchema": _schema(
            {
                "once": _bool("One step only"),
                "max_steps": _int("Cap when not once (default 12)"),
                "project_id": _str("Limit to one Arthur project"),
                "chain": _bool("Enqueue the next hop from a trusted control block (default true)"),
                "dry_run": _bool("Classify only"),
            }
        ),
        "write": True,
    },
]

TOOL_HANDLERS: dict[str, Any] = {}


def tool_catalog(style: str = "dotted") -> list[dict[str, Any]]:
    catalog = []
    for tool in TOOL_DEFS:
        item = {
            "name": public_tool_name(tool["name"], style),
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        }
        catalog.append(item)
    return catalog


def _ok(payload: Any) -> dict[str, Any]:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _s(arguments: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = arguments.get(key, default)
    if value is None:
        return default
    return str(value)


class McpContext:
    def __init__(self, root: Path, tool_name_style: str = "dotted"):
        require_instance(root)
        self.root = root
        self.tool_name_style = tool_name_style
        self.config = load_config(root)


def _handle_status(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    snapshot = collect_status(
        ctx.root,
        reserve_percent=float(ctx.config["reserve_policy"]["minimum_reserve_percent"]),
        quota_enabled=bool(ctx.config["components"]["resource_governor"]),
    )
    return _ok(status_to_dict(snapshot))


def _handle_tick(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    dry_run = arguments.get("dry_run", True)
    if not isinstance(dry_run, bool):
        dry_run = True
    result = classify_tick(
        ctx.root,
        dry_run=dry_run,
        reserve_percent=float(ctx.config["reserve_policy"]["minimum_reserve_percent"]),
        quota_enabled=bool(ctx.config["components"]["resource_governor"]),
    )
    return _ok(result.to_record())


def _handle_queue_show(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    jobs = QueueLedger(ctx.root).latest_jobs()
    job_id = _s(arguments, "job_id")
    if job_id:
        if job_id not in jobs:
            raise KeyError(f"unknown queue job: {job_id}")
        return _ok(jobs[job_id].to_record())
    return _ok({job_id: job.to_record() for job_id, job in sorted(jobs.items())})


def _handle_queue_due(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok([job.to_record() for job in QueueLedger(ctx.root).due_jobs()])


def _handle_gate(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    project_id = _s(arguments, "project_id") or ""
    result = implementation_gate(ctx.root, project_id)
    return _ok(result.to_record())


def _handle_decision_list(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok(list_decisions(ctx.root, include_closed=bool(arguments.get("include_closed"))))


def _handle_board(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok(read_board(ctx.root, project=_s(arguments, "project")))


def _handle_loop_list(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok({**list_loops(ctx.root), "wizard": wizard_options(ctx.root)})


def _handle_queue_create(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    job = QueueJob(
        job_id=_s(arguments, "job_id") or "",
        project_id=_s(arguments, "project_id") or "",
        target_chat_title=_s(arguments, "target_chat_title") or "",
        target_chat_url=_s(arguments, "target_chat_url") or "",
        prompt_path=_s(arguments, "prompt_path"),
        expected_marker=_s(arguments, "expected_marker"),
        idempotency_key=_s(arguments, "idempotency_key"),
    )
    QueueLedger(ctx.root).create_job(job)
    return _ok(job.to_record())


def _handle_queue_claim(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    holder = _s(arguments, "holder") or "mcp-agent"
    job = claim_job(ctx.root, _s(arguments, "job_id") or "", holder=holder)
    return _ok(job.to_record())


def _handle_queue_submit(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    holder = _s(arguments, "holder") or "mcp-agent"
    job = submit_job(
        ctx.root,
        _s(arguments, "job_id") or "",
        holder=holder,
        keep_lock=bool(arguments.get("keep_lock")),
    )
    return _ok(job.to_record())


def _handle_queue_poll(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    from arthur_loop.loop_ops import poll_job

    holder = _s(arguments, "holder") or "mcp-agent"
    job_id = _s(arguments, "job_id") or ""
    marker_found = bool(arguments.get("marker_found"))
    job = poll_job(
        ctx.root,
        job_id,
        marker_found=marker_found,
        status=_s(arguments, "status"),
        holder=holder,
        keep_lock=bool(arguments.get("keep_lock")),
    )
    return _ok(job.to_record())


def _handle_queue_recover(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    result = recover_job(
        ctx.root,
        _s(arguments, "job_id") or "",
        requeue=bool(arguments.get("requeue")),
        error=_s(arguments, "error"),
    )
    return _ok(result.to_record())


def _handle_capture(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    text = _s(arguments, "text")
    source_file = _s(arguments, "source_file")
    if source_file:
        path = Path(source_file)
        if not path.is_absolute():
            path = ctx.root / source_file
        text = path.read_text(encoding="utf-8")
    if text is None:
        raise ValueError("capture needs text or source_file")
    artifact = save_chatgpt_artifact(
        ctx.root,
        project_id=_s(arguments, "project_id") or "",
        job_id=_s(arguments, "job_id") or "",
        kind=_s(arguments, "kind") or "",
        source_chat_title=_s(arguments, "source_chat_title") or "",
        text=text,
    )
    return _ok(artifact.to_record())


def _handle_decision_open(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    record = open_decision(
        ctx.root,
        project_id=_s(arguments, "project_id") or "",
        title=_s(arguments, "title") or "",
        body=_s(arguments, "body") or "",
        source="mcp",
    )
    record["atlas_project"] = resolve_atlas_project(ctx.config, record.get("project_id"))
    return _ok(record)


def _handle_tracker_next(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok(read_next(ctx.root, actor=_s(arguments, "actor")))


def _handle_tracker_queue(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok(read_queue(ctx.root, actor=_s(arguments, "actor")))


def _handle_tracker_walk(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = arguments.get("limit", 1)
    try:
        limit_n = int(limit)
    except (TypeError, ValueError):
        limit_n = 1
    open_jobs = arguments.get("open_jobs")
    if open_jobs is None:
        open_jobs = True
    return _ok(
        walk_next(
            ctx.root,
            actor=_s(arguments, "actor"),
            project=_s(arguments, "project") or resolve_atlas_project(ctx.config, None),
            limit=limit_n,
            dry_run=bool(arguments.get("dry_run")),
            open_jobs=bool(open_jobs),
        )
    )


def _handle_follow_run(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    once = arguments.get("once")
    if once is None:
        once = True
    chain = arguments.get("chain")
    if chain is None:
        chain = True
    max_steps = arguments.get("max_steps", 12)
    try:
        max_n = int(max_steps)
    except (TypeError, ValueError):
        max_n = 12
    return _ok(
        follow_loop(
            ctx.root,
            once=bool(once),
            max_steps=max_n,
            project_id=_s(arguments, "project_id"),
            chain=bool(chain),
            dry_run=bool(arguments.get("dry_run")),
        )
    )


def _handle_decision_answer(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return _ok(answer_decision(ctx.root, _s(arguments, "title") or "", _s(arguments, "answer") or ""))


def _handle_loop_create(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    seed = arguments.get("seed_job")
    if seed is None:
        seed = True
    return _ok(
        create_loop(
            ctx.root,
            project_id=_s(arguments, "project_id") or "",
            advisor=_s(arguments, "advisor"),
            executor=_s(arguments, "executor"),
            tracker=_s(arguments, "tracker"),
            title=_s(arguments, "title"),
            target_chat_url=_s(arguments, "target_chat_url") or "manual",
            goal=_s(arguments, "goal") or "",
            seed_job=bool(seed),
            actor=_s(arguments, "actor"),
            reason=_s(arguments, "reason"),
        )
    )


def _handle_board_open(ctx: McpContext, arguments: dict[str, Any]) -> dict[str, Any]:
    limit = arguments.get("limit", 20)
    try:
        limit_n = int(limit)
    except (TypeError, ValueError):
        limit_n = 20
    return _ok(
        open_jobs_from_board(
            ctx.root,
            project=_s(arguments, "project"),
            limit=limit_n,
            dry_run=bool(arguments.get("dry_run")),
            actor=_s(arguments, "actor"),
            reason=_s(arguments, "reason"),
        )
    )


TOOL_HANDLERS.update(
    {
        "arthur.status": _handle_status,
        "arthur.tick": _handle_tick,
        "arthur.queue.show": _handle_queue_show,
        "arthur.queue.due": _handle_queue_due,
        "arthur.gate.implementation": _handle_gate,
        "arthur.decision.list": _handle_decision_list,
        "arthur.board": _handle_board,
        "arthur.loop.list": _handle_loop_list,
        "arthur.queue.create": _handle_queue_create,
        "arthur.queue.claim": _handle_queue_claim,
        "arthur.queue.submit": _handle_queue_submit,
        "arthur.queue.poll_result": _handle_queue_poll,
        "arthur.queue.recover": _handle_queue_recover,
        "arthur.capture": _handle_capture,
        "arthur.decision.open": _handle_decision_open,
        "arthur.decision.answer": _handle_decision_answer,
        "arthur.loop.create": _handle_loop_create,
        "arthur.board.open_jobs": _handle_board_open,
        "arthur.tracker.next": _handle_tracker_next,
        "arthur.tracker.queue": _handle_tracker_queue,
        "arthur.tracker.walk": _handle_tracker_walk,
        "arthur.follow.run": _handle_follow_run,
    }
)


CREATE_LOOP_PROMPT = """You are operating an Arthur Loop instance. There is no drag-drop graph composer.

Interview the human, one topic at a time:
1. Project id (SHOUTY_SNAKE) and one-line goal
2. Advisor (plans/reviews): chatgpt-browser, claude-code, codex, grok, or manual
3. Executor (implements): claude-code, codex, grok, or manual
4. Tracker: atlas-tasker, command, or none
5. Whether to seed the first next-plan-request job

Then call arthur.loop.create (portable: arthur_loop_create) with those answers.
After that, call arthur.follow.run (portable: arthur_follow_run) so claim/capture/gate
are not hand-typed. Remaining human gates: open decisions, ChatGPT-browser/manual
adapters, and implementation-gate NO-GO.
Never hand-edit queue JSONL. Never claim, submit, poll, complete, or fail a job on a project paused by an open human decision.
"""


def prompt_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": "arthur-loop",
            "description": "Pick advisor/executor/tracker and create a project loop",
            "arguments": [
                {"name": "project_id", "description": "Optional SHOUTY_SNAKE id", "required": False},
            ],
        }
    ]


def handle_rpc(ctx: McpContext, message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications return None."""

    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method is None and "id" in message:
        return _rpc_error(msg_id, -32600, "invalid request")

    if method == "initialize":
        return _rpc_result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": tool_catalog(ctx.tool_name_style)})
    if method == "prompts/list":
        return _rpc_result(msg_id, {"prompts": prompt_catalog()})
    if method == "prompts/get":
        name = str(params.get("name") or "")
        if name not in {"arthur-loop", "arthur_loop"}:
            return _rpc_error(msg_id, -32602, f"unknown prompt {name}")
        extra = ""
        args = params.get("arguments") or {}
        if isinstance(args, dict) and args.get("project_id"):
            extra = f"\nSuggested project_id: {args['project_id']}\n"
        return _rpc_result(
            msg_id,
            {
                "description": "Create or run an Arthur Loop",
                "messages": [{"role": "user", "content": {"type": "text", "text": CREATE_LOOP_PROMPT + extra}}],
            },
        )
    if method == "tools/call":
        name = canonical_tool_name(str(params.get("name") or ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object")
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _rpc_error(msg_id, -32601, f"unknown tool {params.get('name')}")
        try:
            result = handler(ctx, arguments)
        except (KeyError, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:  # BrowserLockError is RuntimeError
            message_text = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
            return _rpc_result(msg_id, _err(message_text))
        return _rpc_result(msg_id, result)
    if method == "shutdown":
        return _rpc_result(msg_id, {})
    if msg_id is None:
        return None
    return _rpc_error(msg_id, -32601, f"unknown method {method}")


def _rpc_result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def encode_message(payload: dict[str, Any], *, framing: str) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if framing == "content-length":
        return f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
    return raw + b"\n"


class MessageReader:
    """Accept NDJSON or LSP Content-Length frames on the same stream."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self.framing = "ndjson"

    def read(self) -> dict[str, Any] | None:
        line = self.stream.readline()
        if line == "":
            return None
        if line.lower().startswith("content-length:"):
            self.framing = "content-length"
            length = int(line.split(":", 1)[1].strip())
            # consume remaining headers
            while True:
                header = self.stream.readline()
                if header in ("", "\r\n", "\n"):
                    break
            body = self.stream.read(length)
            return json.loads(body)
        stripped = line.strip()
        if not stripped:
            return self.read()
        return json.loads(stripped)


def serve(
    root: Path,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    tool_name_style: str = "dotted",
) -> int:
    """Serve MCP on stdio until EOF. Returns 0."""

    if tool_name_style not in {"dotted", "portable"}:
        raise ValueError("tool_name_style must be dotted or portable")
    ctx = McpContext(root, tool_name_style=tool_name_style)
    reader = MessageReader(stdin or sys.stdin)
    out = stdout or sys.stdout
    while True:
        try:
            message = reader.read()
        except json.JSONDecodeError as exc:
            out.write(encode_message(_rpc_error(None, -32700, f"parse error: {exc}"), framing=reader.framing).decode("utf-8"))
            out.flush()
            continue
        if message is None:
            return 0
        if not isinstance(message, dict):
            continue
        reply = handle_rpc(ctx, message)
        if reply is None:
            continue
        data = encode_message(reply, framing=reader.framing)
        if getattr(out, "encoding", None) or not hasattr(out, "buffer"):
            out.write(data.decode("utf-8"))
        else:  # pragma: no cover - binary stdout
            out.buffer.write(data)  # type: ignore[attr-defined]
        out.flush()


def dispatch_tool(root: Path, name: str, arguments: dict[str, Any] | None = None, *, tool_name_style: str = "dotted") -> dict[str, Any]:
    """Call one tool in-process (tests and `arthur mcp call`)."""

    ctx = McpContext(root, tool_name_style=tool_name_style)
    canonical = canonical_tool_name(name)
    handler = TOOL_HANDLERS.get(canonical)
    if handler is None:
        raise KeyError(f"unknown tool {name}")
    try:
        return handler(ctx, arguments or {})
    except (KeyError, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        message_text = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        return _err(message_text)
