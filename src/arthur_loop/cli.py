from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from arthur_loop.agents import KNOWN_AGENTS, detect_agents
from arthur_loop.artifact_store import save_chatgpt_artifact
from arthur_loop.browser_lock import (
    BrowserLockError,
    acquire_lock,
    is_fresh,
    read_lock,
    release_lock,
)
from arthur_loop.config import load_config
from arthur_loop.init_cli import add_init_parser
from arthur_loop.queue_ledger import QueueJob, QueueLedger, parse_ledger_time
from arthur_loop.status import (
    VALID_SESSION_STATES,
    build_console,
    clear_session,
    collect_status,
    record_session,
    render_status,
    status_to_dict,
)
from arthur_loop.tick import classify_tick, render_tick_markdown, write_tick_state
from arthur_loop.tracker import ACTIONS as TRACKER_ACTIONS
from arthur_loop.tracker import run_action as tracker_run_action
from arthur_loop.usage_attribution import (
    append_snapshot,
    append_task_usage,
    estimate_task_usage,
    latest_snapshots,
    render_usage_dashboard,
    snapshot_from_codexbar_json,
    task_usage_records,
)


DEFAULT_HOLDER = "browser-queue-manager"


# ---------------------------------------------------------------------------
# shared helpers (same semantics as the original per-role scripts)


def bool_arg(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def parse_data_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    data = json.loads(value)
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("--data-json must be a JSON object")
    return data


def parse_at(value: str | None):
    return parse_ledger_time(value) if value else None


def print_record(record: Any) -> None:
    if hasattr(record, "to_record"):
        record = record.to_record()
    print(json.dumps(record, indent=2, sort_keys=True))


def require_lock(root: Path, holder: str) -> None:
    lock = read_lock(root)
    if not lock:
        raise BrowserLockError("browser lock is not held; run `arthur queue claim` or acquire a lock first")
    if lock.holder != holder:
        raise BrowserLockError(f"browser lock is held by {lock.holder}, not {holder}")


# ---------------------------------------------------------------------------
# queue


def cmd_queue_create(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    ledger = QueueLedger(root)
    if not args.force and args.job_id in ledger.latest_jobs():
        raise ValueError(
            f"queue job {args.job_id} already exists; use --force to append a fresh queued snapshot"
        )
    job = QueueJob(
        job_id=args.job_id,
        project_id=args.project_id,
        target_chat_title=args.target_chat_title,
        target_chat_url=args.target_chat_url,
        prompt_path=args.prompt_path,
        expected_marker=args.expected_marker,
        priority=args.priority,
        idempotency_key=args.idempotency_key,
    )
    ledger.record_job(job)
    print_record(job)
    return 0


def cmd_queue_claim(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    acquire_lock(root, args.holder, ttl_minutes=args.ttl_minutes, now=parse_at(args.at))
    job = QueueLedger(root).transition(args.job_id, "claimed", now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_queue_submit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    require_lock(root, args.holder)
    job = QueueLedger(root).transition(args.job_id, "submitted", now=parse_at(args.at))
    if not args.keep_lock:
        release_lock(root, args.holder, now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_queue_poll_result(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    acquire_lock(root, args.holder, ttl_minutes=args.ttl_minutes, now=parse_at(args.at))
    status = args.status or ("completed" if args.marker_found else "waiting_for_chatgpt")
    output_paths = args.output_artifact_path or None
    job = QueueLedger(root).record_poll_result(
        args.job_id,
        marker_found=args.marker_found,
        status=status,
        now=parse_at(args.at),
        data=parse_data_json(args.data_json),
        error=args.error,
        output_artifact_paths=output_paths,
    )
    if status in {"completed", "completed_with_warnings"}:
        QueueLedger(root).append_event(
            args.job_id,
            "job_completed",
            {"status": status, "artifact_paths": output_paths or []},
            at=parse_at(args.at),
        )
    if not args.keep_lock:
        release_lock(root, args.holder, now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_queue_complete(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    status = "completed_with_warnings" if args.warning else "completed"
    job = QueueLedger(root).transition(
        args.job_id,
        status,
        now=parse_at(args.at),
        error=args.warning,
        output_artifact_paths=args.output_artifact_path or None,
    )
    QueueLedger(root).append_event(
        args.job_id,
        "job_completed",
        {"status": status, "artifact_paths": args.output_artifact_path or []},
        at=parse_at(args.at),
    )
    print_record(job)
    return 0


def cmd_queue_fail(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    job = QueueLedger(root).transition(args.job_id, "failed", now=parse_at(args.at), error=args.error)
    print_record(job)
    return 0


def cmd_queue_recover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    ledger = QueueLedger(root)
    job = ledger.transition(args.job_id, "needs_recovery", now=parse_at(args.at), error=args.error)
    if args.requeue:
        job = ledger.transition(args.job_id, "queued", now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_queue_due(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    jobs = [job.to_record() for job in QueueLedger(root).due_jobs(parse_at(args.at))]
    print_record(jobs)
    return 0


def cmd_queue_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    jobs = QueueLedger(root).latest_jobs()
    if args.job_id:
        print_record(jobs[args.job_id])
    else:
        print_record({job_id: job.to_record() for job_id, job in sorted(jobs.items())})
    return 0


def _build_queue_parser(subparsers: Any) -> None:
    queue = subparsers.add_parser("queue", help="Create and update advisor queue jobs")
    queue.add_argument("--root", default=".", help="Arthur Loop instance root")
    actions = queue.add_subparsers(dest="queue_action", required=True)

    create = actions.add_parser("create")
    create.add_argument("--job-id", required=True)
    create.add_argument("--project-id", required=True)
    create.add_argument("--target-chat-title", required=True)
    create.add_argument("--target-chat-url", required=True)
    create.add_argument("--prompt-path")
    create.add_argument("--expected-marker")
    create.add_argument("--idempotency-key")
    create.add_argument("--priority", type=int, default=100)
    create.add_argument("--force", action="store_true", help="append a fresh queued snapshot even if the job id already exists")
    create.set_defaults(func=cmd_queue_create)

    claim = actions.add_parser("claim")
    claim.add_argument("--job-id", required=True)
    claim.add_argument("--holder", default=DEFAULT_HOLDER)
    claim.add_argument("--ttl-minutes", type=int, default=15)
    claim.add_argument("--at")
    claim.set_defaults(func=cmd_queue_claim)

    submit = actions.add_parser("submit")
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--holder", default=DEFAULT_HOLDER)
    submit.add_argument("--at")
    submit.add_argument("--keep-lock", action="store_true")
    submit.set_defaults(func=cmd_queue_submit)

    poll = actions.add_parser("poll-result")
    poll.add_argument("--job-id", required=True)
    poll.add_argument("--marker-found", required=True, type=bool_arg)
    poll.add_argument("--status")
    poll.add_argument("--holder", default=DEFAULT_HOLDER)
    poll.add_argument("--ttl-minutes", type=int, default=15)
    poll.add_argument("--at")
    poll.add_argument("--error")
    poll.add_argument("--data-json")
    poll.add_argument("--output-artifact-path", action="append")
    poll.add_argument("--keep-lock", action="store_true")
    poll.set_defaults(func=cmd_queue_poll_result)

    complete = actions.add_parser("complete")
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--at")
    complete.add_argument("--warning")
    complete.add_argument("--output-artifact-path", action="append")
    complete.set_defaults(func=cmd_queue_complete)

    fail = actions.add_parser("fail")
    fail.add_argument("--job-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--at")
    fail.set_defaults(func=cmd_queue_fail)

    recover = actions.add_parser("recover")
    recover.add_argument("--job-id", required=True)
    recover.add_argument("--error")
    recover.add_argument("--requeue", action="store_true", help="send the job back to queued after parking it")
    recover.add_argument("--at")
    recover.set_defaults(func=cmd_queue_recover)

    due = actions.add_parser("due")
    due.add_argument("--at")
    due.set_defaults(func=cmd_queue_due)

    show = actions.add_parser("show")
    show.add_argument("--job-id")
    show.set_defaults(func=cmd_queue_show)


# ---------------------------------------------------------------------------
# lock


def cmd_lock(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.lock_action == "acquire":
        lock = acquire_lock(root, args.holder, ttl_minutes=args.ttl_minutes, now=parse_at(args.at))
        print_record(lock)
        return 0
    if args.lock_action == "release":
        released = release_lock(root, args.holder, now=parse_at(args.at))
        print_record({"holder": args.holder, "released": released})
        return 0
    lock = read_lock(root)
    print_record(
        {
            "lock": lock.to_record() if lock else None,
            "fresh": bool(lock and is_fresh(lock)),
        }
    )
    return 0


def _build_lock_parser(subparsers: Any) -> None:
    lock = subparsers.add_parser("lock", help="Inspect or manage the shared browser lease")
    lock.add_argument("lock_action", choices=["acquire", "release", "status"])
    lock.add_argument("--root", default=".", help="Arthur Loop instance root")
    lock.add_argument("--holder", default=DEFAULT_HOLDER)
    lock.add_argument("--ttl-minutes", type=int, default=15)
    lock.add_argument("--at")
    lock.set_defaults(func=cmd_lock)


# ---------------------------------------------------------------------------
# tick


def _reserve_from(args: argparse.Namespace, config: dict[str, Any]) -> float:
    if args.reserve_percent is not None:
        return args.reserve_percent
    return float(config["reserve_policy"]["minimum_reserve_percent"])


def cmd_tick(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(root)
    result = classify_tick(
        root,
        now=parse_ledger_time(args.at) if args.at else None,
        dry_run=args.dry_run,
        reserve_percent=_reserve_from(args, config),
        stale_after_minutes=args.stale_after_minutes,
        quota_enabled=bool(config["components"]["resource_governor"]),
    )
    if not args.dry_run:
        write_tick_state(root, result)

    if args.format in {"json", "both"}:
        print(json.dumps(result.to_record(), indent=2, sort_keys=True))
    if args.format == "both":
        print()
    if args.format in {"markdown", "both"}:
        print(render_tick_markdown(result), end="")
    return 0


def _build_tick_parser(subparsers: Any) -> None:
    tick = subparsers.add_parser("tick", help="Classify the next scheduler action")
    tick.add_argument("--root", default=".", help="Arthur Loop instance root")
    tick.add_argument("--dry-run", action="store_true", help="Do not write runtime/tick-state.json or queue events")
    tick.add_argument("--at", help="Override current time for tests, e.g. 2026-07-02T12:00:00Z")
    tick.add_argument("--format", choices=["json", "markdown", "both"], default="both")
    tick.add_argument("--reserve-percent", type=float, default=None, help="Overrides reserve_policy.minimum_reserve_percent from config")
    tick.add_argument("--stale-after-minutes", type=int, default=30)
    tick.set_defaults(func=cmd_tick)


# ---------------------------------------------------------------------------
# status


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    at = parse_ledger_time(args.at) if getattr(args, "at", None) else None

    if args.status_action == "set":
        record = record_session(
            root,
            session_id=args.session_id,
            role=args.role,
            state=args.state,
            activity=args.activity,
            project_id=args.project_id,
            now=at,
        )
        print(json.dumps(record.to_record(), indent=2, sort_keys=True))
        return 0

    if args.status_action == "clear":
        cleared = clear_session(root, args.session_id, now=at)
        print(json.dumps({"session_id": args.session_id, "cleared": cleared}, indent=2, sort_keys=True))
        return 0

    config = load_config(root)
    snapshot = collect_status(
        root,
        now=at,
        reserve_percent=_reserve_from(args, config),
        stale_after_minutes=args.stale_after_minutes,
        session_stale_minutes=args.session_stale_minutes,
        quota_enabled=bool(config["components"]["resource_governor"]),
    )
    if args.json:
        print(json.dumps(status_to_dict(snapshot), indent=2, sort_keys=True))
        return 0

    render_status(snapshot, build_console(no_color=args.no_color))
    return 0


def _add_status_show_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable status and exit")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--at", help="Override current time, e.g. 2026-07-02T12:00:00Z")
    parser.add_argument("--reserve-percent", type=float, default=None, help="Overrides reserve_policy.minimum_reserve_percent from config")
    parser.add_argument("--stale-after-minutes", type=int, default=30)
    parser.add_argument("--session-stale-minutes", type=int, default=60)


def _build_status_parser(subparsers: Any) -> None:
    status = subparsers.add_parser("status", help="Render the status dashboard")
    status.add_argument("--root", default=".", help="Arthur Loop instance root")
    _add_status_show_args(status)
    status.set_defaults(func=cmd_status, status_action=None)
    actions = status.add_subparsers(dest="status_action")

    show = actions.add_parser("show", help="Render the dashboard (default)")
    show.add_argument("--root", default=".", help="Arthur Loop instance root")
    _add_status_show_args(show)
    show.set_defaults(func=cmd_status)

    setter = actions.add_parser("set", help="Report what this agent session is doing")
    setter.add_argument("--root", default=".", help="Arthur Loop instance root")
    setter.add_argument("--session-id", required=True)
    setter.add_argument("--role", required=True, help="master, queue-manager, project-loop, executor, governor, ...")
    setter.add_argument("--activity", required=True, help="Short human-readable description of current work")
    setter.add_argument("--state", default="working", choices=sorted(VALID_SESSION_STATES))
    setter.add_argument("--project-id")
    setter.add_argument("--at")
    setter.set_defaults(func=cmd_status)

    clear = actions.add_parser("clear", help="Drop this session from the dashboard")
    clear.add_argument("--root", default=".", help="Arthur Loop instance root")
    clear.add_argument("--session-id", required=True)
    clear.add_argument("--at")
    clear.set_defaults(func=cmd_status)


# ---------------------------------------------------------------------------
# agents


def cmd_agents(args: argparse.Namespace) -> int:
    detected = detect_agents(with_versions=not args.no_versions)
    if args.json:
        print(json.dumps([item.to_record() for item in detected], indent=2, sort_keys=True))
        return 0
    if not detected:
        binaries = ", ".join(agent.binary for agent in KNOWN_AGENTS)
        print(f"No known agent CLIs found on PATH (looked for: {binaries}).")
        return 0
    for item in detected:
        version = f"  ({item.version})" if item.version else ""
        print(f"{item.agent.name:<12} {item.path}{version}")
    return 0


def _build_agents_parser(subparsers: Any) -> None:
    agents = subparsers.add_parser("agents", help="Detect coding-agent CLIs available on this machine")
    agents.add_argument("--json", action="store_true")
    agents.add_argument("--no-versions", action="store_true", help="Skip the (slower) --version probes")
    agents.set_defaults(func=cmd_agents)


# ---------------------------------------------------------------------------
# tracker


def cmd_tracker(args: argparse.Namespace) -> int:
    values: dict[str, str] = {}
    for pair in args.value or []:
        key, sep, val = pair.partition("=")
        if not sep:
            raise ValueError(f"--value expects key=value, got {pair!r}")
        values[key] = val

    result = tracker_run_action(
        load_config(Path(args.root).resolve()),
        args.tracker_action,
        dry_run=args.dry_run,
        **values,
    )
    print_record(result)
    return 0 if result["status"] in ("ok", "dry_run", "skipped") else 2


def _build_tracker_parser(subparsers: Any) -> None:
    tracker = subparsers.add_parser("tracker", help="Run a tracker adapter action")
    tracker.add_argument("tracker_action", choices=list(TRACKER_ACTIONS))
    tracker.add_argument("--root", default=".", help="Arthur Loop instance root")
    tracker.add_argument("--value", action="append", help="key=value template inputs (repeatable)")
    tracker.add_argument("--dry-run", action="store_true")
    tracker.set_defaults(func=cmd_tracker)


# ---------------------------------------------------------------------------
# capture


def cmd_capture(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.source_file:
        text = Path(args.source_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    artifact = save_chatgpt_artifact(
        root,
        project_id=args.project_id,
        job_id=args.job_id,
        kind=args.kind,
        source_chat_title=args.source_chat_title,
        text=text,
        created_at=args.created_at,
        title=args.title,
        link_queue=not args.no_link_queue,
    )
    print(json.dumps(artifact.to_record(), indent=2, sort_keys=True))
    return 0


def _build_capture_parser(subparsers: Any) -> None:
    capture = subparsers.add_parser("capture", help="Save advisor text as a project-local artifact")
    capture.add_argument("--root", default=".", help="Arthur Loop instance root")
    capture.add_argument("--project-id", required=True)
    capture.add_argument("--job-id", required=True)
    capture.add_argument("--kind", required=True, help="next-plan-request, plan-review, sprint-review, etc.")
    capture.add_argument("--source-chat-title", required=True)
    capture.add_argument("--source-file", help="Markdown/text file to capture. Reads stdin when omitted.")
    capture.add_argument("--created-at", help="Override capture timestamp")
    capture.add_argument("--title", help="Human title for the artifact")
    capture.add_argument("--no-link-queue", action="store_true", help="Do not append artifact path to queue job state")
    capture.set_defaults(func=cmd_capture)


# ---------------------------------------------------------------------------
# usage


def cmd_usage_snapshot(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.input_json:
        payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    else:
        proc = subprocess.run(
            ["codexbar", "usage", "--provider", args.provider, "--source", "web", "--format", "json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            sys.stderr.write(proc.stderr)
            return proc.returncode
        payload = json.loads(proc.stdout)

    snapshot = snapshot_from_codexbar_json(payload, snapshot_id=args.snapshot_id, provider=args.provider)
    append_snapshot(root, snapshot)
    print(json.dumps(snapshot.to_record(), indent=2, sort_keys=True))
    return 0


def cmd_usage_task(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    snapshots = latest_snapshots(root)
    missing = [snapshot_id for snapshot_id in (args.before, args.after) if snapshot_id not in snapshots]
    if missing:
        available = ", ".join(sorted(snapshots)) or "none"
        raise SystemExit(f"unknown snapshot id(s): {', '.join(missing)}; available: {available}")

    estimate = estimate_task_usage(
        snapshots[args.before],
        snapshots[args.after],
        task_id=args.task_id,
        project_id=args.project_id,
        role=args.role,
        task_label=args.task_label,
        active_codex_tasks=args.active_codex_tasks,
        background_activity_possible=args.background_activity_possible,
        notes=args.note,
    )
    append_task_usage(root, estimate)

    output_path = root / "outputs/usage-dashboard.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_usage_dashboard(task_usage_records(root)), encoding="utf-8")

    print(json.dumps(estimate.to_record(), indent=2, sort_keys=True))
    print(f"dashboard: {output_path}")
    return 0


def cmd_usage_dashboard(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_usage_dashboard(task_usage_records(root)), encoding="utf-8")
    print(output)
    return 0


def _build_usage_parser(subparsers: Any) -> None:
    usage = subparsers.add_parser("usage", help="Quota snapshots and task attribution")
    actions = usage.add_subparsers(dest="usage_action", required=True)

    snapshot = actions.add_parser("snapshot", help="Record a normalized codexbar usage snapshot")
    snapshot.add_argument("--root", default=".", help="Arthur Loop instance root")
    snapshot.add_argument("--snapshot-id", required=True, help="Stable snapshot id, e.g. before-bq-demo-plan-001")
    snapshot.add_argument("--input-json", help="Read codexbar JSON from this file instead of running codexbar")
    snapshot.add_argument("--provider", default="codex", help="Provider to normalize from codexbar JSON")
    snapshot.set_defaults(func=cmd_usage_snapshot)

    task = actions.add_parser("task", help="Estimate task usage from two quota snapshots")
    task.add_argument("--root", default=".", help="Arthur Loop instance root")
    task.add_argument("--task-id", required=True)
    task.add_argument("--project-id", required=True)
    task.add_argument("--role", required=True, help="Master Orchestrator, Browser Queue Manager, Executor Session, etc.")
    task.add_argument("--task-label", required=True)
    task.add_argument("--before", required=True, help="Before snapshot id")
    task.add_argument("--after", required=True, help="After snapshot id")
    task.add_argument("--active-codex-tasks", type=int, default=1)
    task.add_argument("--background-activity-possible", action="store_true")
    task.add_argument("--note", action="append", default=[])
    task.set_defaults(func=cmd_usage_task)

    dashboard = actions.add_parser("dashboard", help="Render the task usage dashboard")
    dashboard.add_argument("--root", default=".", help="Arthur Loop instance root")
    dashboard.add_argument("--output", default="outputs/usage-dashboard.md")
    dashboard.set_defaults(func=cmd_usage_dashboard)


# ---------------------------------------------------------------------------
# entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arthur", description="Arthur Loop — file-first control plane for AI dev loops.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_init_parser(subparsers)
    _build_agents_parser(subparsers)
    _build_queue_parser(subparsers)
    _build_tick_parser(subparsers)
    _build_status_parser(subparsers)
    _build_lock_parser(subparsers)
    _build_tracker_parser(subparsers)
    _build_capture_parser(subparsers)
    _build_usage_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BrowserLockError, KeyError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
