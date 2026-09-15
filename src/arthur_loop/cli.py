from __future__ import annotations

import argparse
import json
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from arthur_loop.agents import KNOWN_AGENTS, detect_agents
from arthur_loop.artifact_store import ARTIFACT_KINDS, implementation_gate, save_chatgpt_artifact
from arthur_loop.browser_lock import (
    BrowserLockError,
    acquire_lock,
    break_lock,
    is_fresh,
    read_lock,
    release_lock,
)
from arthur_loop.config import (
    KNOWN_ADVISORS,
    KNOWN_EXECUTORS,
    KNOWN_TRACKERS,
    is_instance,
    load_config,
    require_instance,
)
from arthur_loop.roles import LOOP_ROLES, parse_role_updates, roles_payload
from arthur_loop.decisions import answer_decision, clear_decision, list_decisions, open_decision
from arthur_loop.init_cli import add_init_parser
from arthur_loop.notify import (
    load_watch_state,
    notification_command,
    save_watch_state,
    send_notification,
    unsupported_detail,
    watch_events,
)
from arthur_loop.queue_ledger import QueueJob, QueueLedger, parse_ledger_time
from arthur_loop.quota import fetch_quota_payload, quota_settings
from arthur_loop.recovery import recover_job
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
from arthur_loop.web import add_web_parser
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

ROOT_HELP = "Arthur Loop instance root (default: current directory)"

# exit codes shared by every subcommand
EXIT_OK = 0
EXIT_ERROR = 2          # bad input, refused transition, lock conflict, missing file
EXIT_NEEDS_HUMAN = 3    # work was recorded but the loop must stop for a human (capture, gate)


# ---------------------------------------------------------------------------
# shared helpers


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    """Attach `--root` to a (sub)command without shadowing a value given higher up.

    argparse parses each subcommand into a fresh namespace and copies every
    attribute over the parent's, so a subcommand default of "." would silently
    erase `arthur status --root X set ...`. SUPPRESS means "only if given".
    """

    parser.add_argument("--root", default=argparse.SUPPRESS, help=ROOT_HELP)


def resolve_root(args: argparse.Namespace) -> Path:
    """The instance root, wherever `--root` was written on the command line."""

    return Path(getattr(args, "root", None) or ".").resolve()


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
    root = resolve_root(args)
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
    QueueLedger(root).create_job(job, force=args.force)
    print_record(job)
    return EXIT_OK


def cmd_queue_claim(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import claim_job

    root = resolve_root(args)
    # refuse before touching the lease so an illegal claim never leaves a lock behind
    job = claim_job(
        root,
        args.job_id,
        holder=args.holder,
        ttl_minutes=args.ttl_minutes,
        now=parse_at(args.at),
    )
    print_record(job)
    return EXIT_OK


def cmd_queue_submit(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import submit_job

    job = submit_job(
        resolve_root(args),
        args.job_id,
        holder=args.holder,
        keep_lock=args.keep_lock,
        now=parse_at(args.at),
    )
    print_record(job)
    return EXIT_OK


def cmd_queue_poll_result(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import poll_job

    job = poll_job(
        resolve_root(args),
        args.job_id,
        marker_found=args.marker_found,
        status=args.status,
        holder=args.holder,
        ttl_minutes=args.ttl_minutes,
        keep_lock=args.keep_lock,
        now=parse_at(args.at),
        data=parse_data_json(args.data_json),
        error=args.error,
        output_artifact_paths=args.output_artifact_path or None,
    )
    print_record(job)
    return EXIT_OK


def cmd_queue_complete(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import complete_job

    job = complete_job(
        resolve_root(args),
        args.job_id,
        now=parse_at(args.at),
        warning=args.warning,
        output_artifact_paths=args.output_artifact_path or None,
    )
    print_record(job)
    return EXIT_OK


def cmd_queue_fail(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import fail_job

    job = fail_job(
        resolve_root(args),
        args.job_id,
        error=args.error,
        now=parse_at(args.at),
    )
    print_record(job)
    return EXIT_OK


def cmd_queue_cancel(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    job = QueueLedger(root).transition(args.job_id, "cancelled", now=parse_at(args.at), error=args.reason)
    print_record(job)
    return EXIT_OK


def cmd_queue_recover(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    result = recover_job(
        root,
        args.job_id,
        requeue=args.requeue,
        error=args.error,
        now=parse_at(args.at),
        holder_hint=args.holder,
        release_lease=not args.keep_lock,
    )
    print_record(result)
    return EXIT_OK


def cmd_queue_due(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    jobs = [job.to_record() for job in QueueLedger(root).due_jobs(parse_at(args.at))]
    print_record(jobs)
    return EXIT_OK


def cmd_queue_show(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    jobs = QueueLedger(root).latest_jobs()
    if args.job_id:
        if args.job_id not in jobs:
            raise KeyError(f"unknown queue job: {args.job_id}")
        print_record(jobs[args.job_id])
    else:
        print_record({job_id: job.to_record() for job_id, job in sorted(jobs.items())})
    return EXIT_OK


def _build_queue_parser(subparsers: Any) -> None:
    queue = subparsers.add_parser(
        "queue",
        help="Create and move advisor queue jobs (queued → claimed → submitted → poll → done)",
        description=(
            "Queue jobs follow a fixed state machine; illegal moves (completing unsubmitted work, "
            "touching a finished job) are refused with exit code 2."
        ),
    )
    add_root_argument(queue)
    actions = queue.add_subparsers(dest="queue_action", required=True)

    create = actions.add_parser("create", help="Add a queued job (duplicate ids and reused idempotency keys are refused)")
    add_root_argument(create)
    create.add_argument("--job-id", required=True)
    create.add_argument("--project-id", required=True)
    create.add_argument("--target-chat-title", required=True)
    create.add_argument("--target-chat-url", required=True, help="advisor conversation URL, or `manual`")
    create.add_argument("--prompt-path", help="instance-relative path of the rendered prompt")
    create.add_argument("--expected-marker", help="marker the advisor must echo back")
    create.add_argument("--idempotency-key", help="stable key so a retried create cannot fork the queue")
    create.add_argument("--priority", type=int, default=100)
    create.add_argument("--force", action="store_true", help="re-queue an existing job id with a fresh snapshot")
    create.set_defaults(func=cmd_queue_create)

    claim = actions.add_parser("claim", help="Take the browser/advisor lease and own a queued job")
    add_root_argument(claim)
    claim.add_argument("--job-id", required=True)
    claim.add_argument("--holder", default=DEFAULT_HOLDER, help="lease holder name recorded on the job")
    claim.add_argument("--ttl-minutes", type=int, default=15)
    claim.add_argument("--at")
    claim.set_defaults(func=cmd_queue_claim)

    submit = actions.add_parser("submit", help="Record that the prompt was sent; first poll is due in 1 minute")
    add_root_argument(submit)
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--holder", default=DEFAULT_HOLDER)
    submit.add_argument("--at")
    submit.add_argument("--keep-lock", action="store_true", help="keep the lease (you are about to poll)")
    submit.set_defaults(func=cmd_queue_submit)

    poll = actions.add_parser("poll-result", help="Record a poll: --marker-found true → completed, false → waiting")
    add_root_argument(poll)
    poll.add_argument("--job-id", required=True)
    poll.add_argument("--marker-found", required=True, type=bool_arg)
    poll.add_argument(
        "--status",
        choices=["completed", "completed_with_warnings", "waiting_for_chatgpt", "stopped_no_output", "failed"],
        help="override the status implied by --marker-found",
    )
    poll.add_argument("--holder", default=DEFAULT_HOLDER)
    poll.add_argument("--ttl-minutes", type=int, default=15)
    poll.add_argument("--at")
    poll.add_argument("--error")
    poll.add_argument("--data-json")
    poll.add_argument("--output-artifact-path", action="append")
    poll.add_argument("--keep-lock", action="store_true")
    poll.set_defaults(func=cmd_queue_poll_result)

    complete = actions.add_parser("complete", help="Finish a submitted job without a poll record")
    add_root_argument(complete)
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--at")
    complete.add_argument("--warning")
    complete.add_argument("--output-artifact-path", action="append")
    complete.set_defaults(func=cmd_queue_complete)

    fail = actions.add_parser("fail", help="Mark a job failed (terminal)")
    add_root_argument(fail)
    fail.add_argument("--job-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--at")
    fail.set_defaults(func=cmd_queue_fail)

    cancel = actions.add_parser("cancel", help="Stop a job on purpose (terminal)")
    add_root_argument(cancel)
    cancel.add_argument("--job-id", required=True)
    cancel.add_argument("--reason")
    cancel.add_argument("--at")
    cancel.set_defaults(func=cmd_queue_cancel)

    recover = actions.add_parser(
        "recover",
        help="Park an abandoned job as needs_recovery and release its dead manager's lease",
    )
    add_root_argument(recover)
    recover.add_argument("--job-id", required=True)
    recover.add_argument("--error")
    recover.add_argument("--requeue", action="store_true", help="send the job back to queued after parking it")
    recover.add_argument(
        "--holder",
        default=DEFAULT_HOLDER,
        help="lease holder to release when the job predates claimed_by tracking",
    )
    recover.add_argument("--keep-lock", action="store_true", help="never touch the browser lease")
    recover.add_argument("--at")
    recover.set_defaults(func=cmd_queue_recover)

    due = actions.add_parser("due", help="List jobs ready to submit or poll")
    add_root_argument(due)
    due.add_argument("--at")
    due.set_defaults(func=cmd_queue_due)

    show = actions.add_parser("show", help="Print the latest snapshot of one or all jobs")
    add_root_argument(show)
    show.add_argument("--job-id")
    show.set_defaults(func=cmd_queue_show)


# ---------------------------------------------------------------------------
# lock


def cmd_lock(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    if args.lock_action == "acquire":
        lock = acquire_lock(root, args.holder, ttl_minutes=args.ttl_minutes, now=parse_at(args.at))
        print_record(lock)
        return EXIT_OK
    if args.lock_action == "release":
        released = release_lock(root, args.holder, now=parse_at(args.at))
        print_record({"holder": args.holder, "released": released})
        return EXIT_OK
    if args.lock_action == "break":
        broken = break_lock(root, force=args.force, via="cli", now=parse_at(args.at))
        print_record({"broken": broken is not None, "holder": broken.holder if broken else None})
        return EXIT_OK
    lock = read_lock(root)
    print_record(
        {
            "lock": lock.to_record() if lock else None,
            "fresh": bool(lock and is_fresh(lock, parse_at(args.at))),
        }
    )
    return EXIT_OK


def _build_lock_parser(subparsers: Any) -> None:
    lock = subparsers.add_parser(
        "lock",
        help="Inspect or manage the shared browser/advisor lease",
        description=(
            "`break` removes a stale lease; add --force to remove a fresh one whose holder you know is dead "
            "(crash after claim). `arthur queue recover` does this for you when the job is known."
        ),
    )
    lock.add_argument("lock_action", choices=["acquire", "release", "status", "break"])
    add_root_argument(lock)
    lock.add_argument("--holder", default=DEFAULT_HOLDER)
    lock.add_argument("--ttl-minutes", type=int, default=15)
    lock.add_argument("--force", action="store_true", help="with `break`: remove even a fresh lease")
    lock.add_argument("--at")
    lock.set_defaults(func=cmd_lock)


# ---------------------------------------------------------------------------
# tick


def _reserve_from(args: argparse.Namespace, config: dict[str, Any]) -> float:
    if getattr(args, "reserve_percent", None) is not None:
        return args.reserve_percent
    return float(config["reserve_policy"]["minimum_reserve_percent"])


def cmd_tick(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
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
    return EXIT_OK


def _build_tick_parser(subparsers: Any) -> None:
    tick = subparsers.add_parser("tick", help="Classify the next scheduler action from durable state")
    add_root_argument(tick)
    tick.add_argument("--dry-run", action="store_true", help="Do not write runtime/tick-state.json or queue events")
    tick.add_argument("--at", help="Override current time for tests, e.g. 2026-07-02T12:00:00Z")
    tick.add_argument("--format", choices=["json", "markdown", "both"], default="both")
    tick.add_argument("--reserve-percent", type=float, default=None, help="Overrides reserve_policy.minimum_reserve_percent from config")
    tick.add_argument("--stale-after-minutes", type=int, default=30)
    tick.set_defaults(func=cmd_tick)


# ---------------------------------------------------------------------------
# status


def cmd_status(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
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
        print(json.dumps({**record.to_record(), "root": str(root)}, indent=2, sort_keys=True))
        return EXIT_OK

    if args.status_action == "clear":
        cleared = clear_session(root, args.session_id, now=at)
        print(json.dumps({"session_id": args.session_id, "cleared": cleared, "root": str(root)}, indent=2, sort_keys=True))
        return EXIT_OK

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
        return EXIT_OK

    render_status(snapshot, build_console(no_color=args.no_color))
    return EXIT_OK


def _add_status_show_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable status and exit")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--at", help="Override current time, e.g. 2026-07-02T12:00:00Z")
    parser.add_argument("--reserve-percent", type=float, default=None, help="Overrides reserve_policy.minimum_reserve_percent from config")
    parser.add_argument("--stale-after-minutes", type=int, default=30)
    parser.add_argument("--session-stale-minutes", type=int, default=60)


def _build_status_parser(subparsers: Any) -> None:
    status = subparsers.add_parser("status", help="Render the status dashboard; `set`/`clear` report agent sessions")
    add_root_argument(status)
    _add_status_show_args(status)
    status.set_defaults(func=cmd_status, status_action=None)
    actions = status.add_subparsers(dest="status_action")

    show = actions.add_parser("show", help="Render the dashboard (default)")
    add_root_argument(show)
    _add_status_show_args(show)
    show.set_defaults(func=cmd_status)

    setter = actions.add_parser("set", help="Report what this agent session is doing")
    add_root_argument(setter)
    setter.add_argument("--session-id", required=True)
    setter.add_argument("--role", required=True, help="master, queue-manager, project-loop, executor, governor, ...")
    setter.add_argument("--activity", required=True, help="Short human-readable description of current work")
    setter.add_argument("--state", default="working", choices=sorted(VALID_SESSION_STATES))
    setter.add_argument("--project-id")
    setter.add_argument("--at")
    setter.set_defaults(func=cmd_status)

    clear = actions.add_parser("clear", help="Drop this session from the dashboard")
    add_root_argument(clear)
    clear.add_argument("--session-id", required=True)
    clear.add_argument("--at")
    clear.set_defaults(func=cmd_status)


# ---------------------------------------------------------------------------
# agents


def cmd_agents(args: argparse.Namespace) -> int:
    detected = detect_agents(with_versions=not args.no_versions)
    if args.json:
        print(json.dumps([item.to_record() for item in detected], indent=2, sort_keys=True))
        return EXIT_OK
    if not detected:
        binaries = ", ".join(agent.binary for agent in KNOWN_AGENTS)
        print(f"No known agent CLIs found on PATH (looked for: {binaries}).")
        return EXIT_OK
    for item in detected:
        agent = item.agent
        version_note = f"  ({item.version})" if item.version else ""
        packs = []
        if agent.advisor_adapter:
            packs.append(f"advisor={agent.advisor_adapter}")
        if agent.executor_adapter:
            packs.append(f"executor={agent.executor_adapter}")
        pack_note = ", ".join(packs) if packs else "no shipped adapter pack — guided/custom presets only"
        print(f"{agent.name:<12} {item.path}{version_note}  [{pack_note}]")
    return EXIT_OK


def _build_agents_parser(subparsers: Any) -> None:
    agents = subparsers.add_parser(
        "agents",
        help="Detect coding-agent CLIs on this machine and show which have shipped adapter packs",
    )
    agents.add_argument("--json", action="store_true")
    agents.add_argument("--no-versions", action="store_true", help="Skip the (slower) --version probes")
    agents.set_defaults(func=cmd_agents)


# ---------------------------------------------------------------------------
# tracker


def _parse_values(pairs: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, val = pair.partition("=")
        if not sep:
            raise ValueError(f"--value expects key=value, got {pair!r}")
        values[key] = val
    return values


def cmd_tracker(args: argparse.Namespace) -> int:
    from arthur_loop.atlas_board import (
        open_jobs_from_board,
        read_board,
        read_next,
        read_queue,
        resolve_atlas_project,
        tracker_or_fallback,
        walk_next,
    )

    root = resolve_root(args)
    config = load_config(root) if (root / "config/arthur-loop.json").exists() else {}
    project = args.project or resolve_atlas_project(config, None)
    if args.tracker_action == "board":
        record = read_board(root, project=project)
        print_record(record)
        return EXIT_OK
    if args.tracker_action == "next":
        print_record(read_next(root, actor=getattr(args, "atlas_actor", None)))
        return EXIT_OK
    if args.tracker_action == "queue":
        print_record(read_queue(root, actor=getattr(args, "atlas_actor", None)))
        return EXIT_OK
    if args.tracker_action == "walk":
        print_record(
            walk_next(
                root,
                actor=getattr(args, "atlas_actor", None),
                project=project,
                limit=args.limit,
                dry_run=args.dry_run,
                open_jobs=not getattr(args, "no_open", False),
                actor_audit=args.actor,
                reason=args.reason,
            )
        )
        return EXIT_OK
    if args.tracker_action == "open-jobs":
        record = open_jobs_from_board(
            root,
            project=project,
            limit=args.limit,
            dry_run=args.dry_run,
            actor=args.actor,
            reason=args.reason,
        )
        print_record(record)
        return EXIT_OK
    values = _parse_values(args.value)
    if args.project and "project" not in values:
        values["project"] = args.project
    result = tracker_or_fallback(
        root,
        args.tracker_action,
        dry_run=args.dry_run,
        **values,
    )
    print_record(result)
    return EXIT_OK if result["status"] in ("ok", "dry_run", "skipped") else EXIT_ERROR


def _build_tracker_parser(subparsers: Any) -> None:
    tracker = subparsers.add_parser(
        "tracker",
        help="Atlas board JSON (primary) or one argv template (fallback), always from the instance root",
        description=(
            "`next`, `queue`, `walk`, `board`, and `open-jobs` call the Atlas `tracker` CLI "
            "(`tracker next --json` / `queue` / `board`). open_decision / close_decision / "
            "sprint_gate still use the three argv templates. Arthur project_id is mapped to "
            "an Atlas key via tracker.project_map / tracker.project_key — it is not passed through."
        ),
    )
    tracker.add_argument(
        "tracker_action",
        choices=list(TRACKER_ACTIONS) + ["board", "open-jobs", "next", "queue", "walk"],
    )
    add_root_argument(tracker)
    tracker.add_argument("--value", action="append", help="key=value template inputs (repeatable)")
    tracker.add_argument("--dry-run", action="store_true")
    tracker.add_argument("--project", help="Atlas project key (not the Arthur SHOUTY_SNAKE id)")
    tracker.add_argument("--atlas-actor", help="Atlas actor for next / queue / walk")
    tracker.add_argument("--no-open", action="store_true", help="walk: list only, do not open jobs")
    tracker.add_argument("--limit", type=int, default=20, help="Max tickets for open-jobs / walk")
    tracker.add_argument(
        "--json",
        action="store_true",
        help="Accepted for `tracker board --json` muscle memory; Arthur already prints JSON",
    )
    tracker.add_argument("--actor")
    tracker.add_argument("--reason")
    tracker.set_defaults(func=cmd_tracker)


# ---------------------------------------------------------------------------
# decisions


def cmd_decision(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
    at = parse_at(getattr(args, "at", None))

    if args.decision_action == "list":
        rows = list_decisions(root, include_closed=args.all)
        if args.json:
            print_record(rows)
            return EXIT_OK
        if not rows:
            print("no open human decisions")
            return EXIT_OK
        for row in rows:
            print(f"[{row['status'] or 'OPEN'}] {row['title']}")
            if row["body"]:
                first = row["body"].strip().splitlines()[0]
                print(f"    {first[:100]}")
        return EXIT_OK

    if args.decision_action == "open":
        body = args.body or ""
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        record = open_decision(root, project_id=args.project_id, title=args.title, body=body, now=at)
        if not args.no_tracker:
            from arthur_loop.atlas_board import resolve_atlas_project, tracker_or_fallback

            config = load_config(root)
            atlas_project = args.project or resolve_atlas_project(config, args.project_id)
            if atlas_project:
                record["atlas_project"] = atlas_project
                record["tracker"] = tracker_or_fallback(
                    root,
                    "open_decision",
                    project=atlas_project,
                    title=record["title"],
                    reason="human decision opened by arthur-loop",
                )
            else:
                record["tracker"] = {
                    "status": "skipped",
                    "reason": (
                        f"Arthur project_id {args.project_id!r} is not an Atlas project key. "
                        "Set tracker.project_map "
                        f'(e.g. {{"{args.project_id}": "{args.project_id.split("_", 1)[0]}"}}) '
                        "or tracker.project_key or pass --project."
                    ),
                }
            if record["tracker"]["status"] == "error":
                sys.stderr.write("note: the decision is open in human-decisions/open.md, but the tracker call failed\n")
        print_record(record)
        return EXIT_OK

    if args.decision_action == "answer":
        answer = args.answer or ""
        if args.answer_file:
            answer = Path(args.answer_file).read_text(encoding="utf-8")
        print_record(answer_decision(root, args.title, answer, now=at))
        return EXIT_OK

    print_record(clear_decision(root, args.title, note=args.note or "", now=at))
    return EXIT_OK


def _build_decision_parser(subparsers: Any) -> None:
    decision = subparsers.add_parser(
        "decision",
        help="Open, answer, clear, or list human decisions (an open decision pauses its project)",
        description=(
            "Human decisions live in human-decisions/open.md, one `## PROJECT_ID title` section each. "
            "While a section says Status: OPEN the tick reports HUMAN_INPUT_REQUIRED for that project "
            "and its jobs are not handed out. Titles are shown by `arthur status` and `arthur decision list`."
        ),
    )
    add_root_argument(decision)
    actions = decision.add_subparsers(dest="decision_action", required=True)

    listing = actions.add_parser("list", help="Show open decisions (--all includes answered/cleared)")
    add_root_argument(listing)
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_decision)

    opener = actions.add_parser("open", help="Open a decision and pause its project")
    add_root_argument(opener)
    opener.add_argument("--project-id", required=True)
    opener.add_argument("--title", required=True, help="short question, e.g. 'Session lifetime: 24h or 7d?'")
    opener.add_argument("--body", help="details, options, what is blocked")
    opener.add_argument("--body-file", help="read the details from a file instead")
    opener.add_argument(
        "--project",
        help="Atlas project key (defaults to tracker.project_map[project_id] or tracker.project_key)",
    )
    opener.add_argument("--no-tracker", action="store_true", help="skip the tracker open_decision call")
    opener.add_argument("--at")
    opener.set_defaults(func=cmd_decision)

    answer = actions.add_parser("answer", help="Answer an open decision and unpause its project")
    add_root_argument(answer)
    answer.add_argument("--title", required=True, help="full title as shown by `arthur decision list`")
    answer.add_argument("--answer", help="the human's call")
    answer.add_argument("--answer-file", help="read the answer from a file instead")
    answer.add_argument("--at")
    answer.set_defaults(func=cmd_decision)

    clear = actions.add_parser("clear", help="Withdraw an open decision without answering it")
    add_root_argument(clear)
    clear.add_argument("--title", required=True)
    clear.add_argument("--note", help="why it is being withdrawn")
    clear.add_argument("--at")
    clear.set_defaults(func=cmd_decision)


# ---------------------------------------------------------------------------
# gates


def cmd_gate(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
    result = implementation_gate(root, args.project_id)
    if args.json:
        print_record(result)
    else:
        verdict = "GO" if result.go else "NO-GO"
        print(f"{verdict}: implementation gate for {args.project_id}")
        if result.go:
            print(f"  approved by {result.approval_artifact} at {result.approved_at}")
        for reason in result.reasons:
            print(f"  - {reason}")
    return EXIT_OK if result.go else EXIT_NEEDS_HUMAN


def _build_gate_parser(subparsers: Any) -> None:
    gate = subparsers.add_parser(
        "gate",
        help="Evaluate an approval gate from saved artifacts (exit 0 GO, 3 NO-GO)",
        description=(
            "Gates are computed from durable state, never from prompts. `implementation` is GO only when the "
            "newest plan-review is a valid APPROVE_PLAN, nothing since re-opened planning or closed the sprint, "
            "and the project has no open human decision. `arthur capture --kind implementation-handoff` "
            "quarantines handoffs that arrive while this gate is NO-GO."
        ),
    )
    add_root_argument(gate)
    gate.add_argument("gate_name", choices=["implementation"])
    gate.add_argument("--project-id", required=True)
    gate.add_argument("--json", action="store_true")
    gate.set_defaults(func=cmd_gate)


# ---------------------------------------------------------------------------
# notify + watch


def cmd_notify(args: argparse.Namespace) -> int:
    result = send_notification(args.title, args.message, dry_run=args.dry_run)
    print_record(result.to_record())
    if result.method == "unsupported":
        sys.stderr.write(f"error: {result.detail}\n")
        return EXIT_ERROR
    return EXIT_OK if (result.sent or args.dry_run) else EXIT_ERROR


def cmd_watch(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
    config = load_config(root)
    reserve = _reserve_from(args, config)
    quota_enabled = bool(config["components"]["resource_governor"])
    label = root.name or "arthur-loop"

    desktop = not args.no_desktop
    if desktop and notification_command("Arthur Loop", "probe") is None:
        sys.stderr.write(f"note: {unsupported_detail()}; printing events instead\n")
        desktop = False

    previous, previous_decisions = load_watch_state(root)
    try:
        while True:
            current = classify_tick(
                root,
                dry_run=True,
                reserve_percent=reserve,
                stale_after_minutes=args.stale_after_minutes,
                quota_enabled=quota_enabled,
            )
            decisions = [item["title"] for item in list_decisions(root)]

            for headline, message in watch_events(previous, current, previous_decisions, decisions):
                line = f"notify: {headline} — {message}"
                if not desktop:
                    print(line)
                else:
                    result = send_notification(f"Arthur Loop ({label}) — {headline}", message)
                    suffix = "" if result.sent else f"  [not delivered: {result.detail}]"
                    print(line + suffix)

            if not args.quiet:
                stale = len(current.stale_job_ids or [])
                blocked = ", ".join(current.blocked_projects or []) or "-"
                print(
                    f"[{current.generated_at}] {current.status:<24} "
                    f"blocked: {blocked} · stale: {stale}",
                    flush=True,
                )

            save_watch_state(root, current, decisions)
            previous, previous_decisions = current, decisions
            if args.once:
                return EXIT_OK
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nwatch stopped")
        return EXIT_OK


def _build_notify_parser(subparsers: Any) -> None:
    notify = subparsers.add_parser(
        "notify",
        help="Send a desktop notification (macOS osascript / Linux notify-send); exit 2 when unsupported",
    )
    notify.add_argument("--title", default="Arthur Loop")
    notify.add_argument("--message", required=True)
    notify.add_argument("--dry-run", action="store_true", help="Print the command that would run without sending")
    notify.set_defaults(func=cmd_notify)

    watch = subparsers.add_parser(
        "watch",
        help="Poll the loop and notify on human-input, blocks, due work, and stale jobs (never on repeats)",
        description=(
            "The last observation is kept in runtime/watch-state.json, so `watch --once` from cron only "
            "reports changes since the previous run. Without a desktop notifier events are printed."
        ),
    )
    add_root_argument(watch)
    watch.add_argument("--interval", type=float, default=30.0, help="Seconds between checks")
    watch.add_argument("--once", action="store_true", help="Check once and exit (cron-friendly)")
    watch.add_argument("--no-desktop", action="store_true", help="Print events instead of notifying")
    watch.add_argument("--quiet", action="store_true", help="Print only events, not the per-check heartbeat line")
    watch.add_argument("--reserve-percent", type=float, default=None)
    watch.add_argument("--stale-after-minutes", type=int, default=30)
    watch.set_defaults(func=cmd_watch)


# ---------------------------------------------------------------------------
# capture


def cmd_capture(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    require_instance(root)
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
        escalate=not args.no_escalate,
    )
    print(json.dumps(artifact.to_record(), indent=2, sort_keys=True))
    if artifact.needs_human:
        what = "control block failed validation" if not artifact.control_block_valid else "HUMAN_INPUT_REQUIRED"
        sys.stderr.write(f"saved, but {what}: stop this project and get a human")
        if artifact.escalated_decision:
            sys.stderr.write(f" — decision opened: {artifact.escalated_decision!r}")
        sys.stderr.write("\n")
        return EXIT_NEEDS_HUMAN
    return EXIT_OK


def _build_capture_parser(subparsers: Any) -> None:
    capture = subparsers.add_parser(
        "capture",
        help="Save advisor/executor text as a project artifact and validate its control block",
        description=(
            "Exit 0: saved and trusted. Exit 3: saved but quarantined or HUMAN_INPUT_REQUIRED — a human "
            "decision was opened and the project is paused. Exit 2: nothing saved (bad arguments, missing file)."
        ),
    )
    add_root_argument(capture)
    capture.add_argument("--project-id", required=True)
    capture.add_argument("--job-id", required=True)
    capture.add_argument("--kind", required=True, choices=list(ARTIFACT_KINDS), help="which hop of the loop this text is")
    capture.add_argument("--source-chat-title", required=True, help="conversation title, or the adapter name (manual, codex, ...)")
    capture.add_argument("--source-file", help="Markdown/text file to capture. Reads stdin when omitted.")
    capture.add_argument("--created-at", help="Override capture timestamp")
    capture.add_argument("--title", help="Human title for the artifact")
    capture.add_argument("--no-link-queue", action="store_true", help="Do not append artifact path to queue job state")
    capture.add_argument("--no-escalate", action="store_true", help="Quarantine without opening a human decision")
    capture.set_defaults(func=cmd_capture)


# ---------------------------------------------------------------------------
# usage


def cmd_usage_snapshot(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    config = load_config(root)
    result = fetch_quota_payload(config, codexbar_provider=args.provider, input_json=args.input_json, root=root)
    for warning in result.warnings:
        sys.stderr.write(f"note: {warning}\n")
    if result.payload is None:
        sys.stderr.write(f"error: no quota payload available (source: {result.source})\n")
        return 3

    wanted = args.provider or quota_settings(config).get("codexbar_provider") or "codex"
    snapshot = snapshot_from_codexbar_json(result.payload, snapshot_id=args.snapshot_id, provider=wanted)
    append_snapshot(root, snapshot)
    print(json.dumps(snapshot.to_record(), indent=2, sort_keys=True))
    return EXIT_OK


def cmd_usage_task(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    snapshots = latest_snapshots(root)
    missing = [snapshot_id for snapshot_id in (args.before, args.after) if snapshot_id not in snapshots]
    if missing:
        available = ", ".join(sorted(snapshots)) or "none"
        raise ValueError(f"unknown snapshot id(s): {', '.join(missing)}; available: {available}")

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
    return EXIT_OK


def cmd_usage_dashboard(args: argparse.Namespace) -> int:
    root = resolve_root(args)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_usage_dashboard(task_usage_records(root)), encoding="utf-8")
    print(output)
    return EXIT_OK


def _build_usage_parser(subparsers: Any) -> None:
    usage = subparsers.add_parser("usage", help="Quota snapshots and task attribution")
    add_root_argument(usage)
    actions = usage.add_subparsers(dest="usage_action", required=True)

    snapshot = actions.add_parser("snapshot", help="Record a normalized codexbar usage snapshot")
    add_root_argument(snapshot)
    snapshot.add_argument("--snapshot-id", required=True, help="Stable snapshot id, e.g. before-bq-demo-plan-001")
    snapshot.add_argument("--input-json", help="Read a codexbar-schema JSON file instead of the configured quota provider")
    snapshot.add_argument("--provider", default=None, help="codexbar provider to fetch/normalize (default: quota.codexbar_provider from config)")
    snapshot.set_defaults(func=cmd_usage_snapshot)

    task = actions.add_parser("task", help="Estimate task usage from two quota snapshots")
    add_root_argument(task)
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
    add_root_argument(dashboard)
    dashboard.add_argument("--output", default="outputs/usage-dashboard.md")
    dashboard.set_defaults(func=cmd_usage_dashboard)


# ---------------------------------------------------------------------------
# mcp / integrations / loop


def cmd_mcp(args: argparse.Namespace) -> int:
    from arthur_loop.mcp import dispatch_tool, serve, tool_catalog

    root = resolve_root(args)
    style = getattr(args, "tool_name_style", "dotted")
    if args.mcp_action == "serve":
        require_instance(root)
        return serve(root, tool_name_style=style)
    if args.mcp_action == "tools":
        print_record(tool_catalog(style))
        return EXIT_OK
    if args.mcp_action == "schema":
        print_record({"tools": tool_catalog(style), "tool_name_style": style})
        return EXIT_OK
    payload = dispatch_tool(root, args.tool, parse_data_json(args.arguments), tool_name_style=style)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return EXIT_OK if not payload.get("isError") else EXIT_ERROR


def _build_mcp_parser(subparsers: Any) -> None:
    mcp = subparsers.add_parser(
        "mcp",
        help="Stdio MCP server and tool catalog for coding agents (status/queue/tick/gate/decision/loop/board)",
    )
    add_root_argument(mcp)
    actions = mcp.add_subparsers(dest="mcp_action", required=True)
    serve = actions.add_parser("serve", help="Serve MCP on stdio (NDJSON or Content-Length)")
    add_root_argument(serve)
    serve.add_argument("--tool-name-style", choices=["dotted", "portable"], default="dotted")
    serve.set_defaults(func=cmd_mcp)
    tools = actions.add_parser("tools", help="List MCP tool names and schemas")
    add_root_argument(tools)
    tools.add_argument("--tool-name-style", choices=["dotted", "portable"], default="dotted")
    tools.add_argument("--json", action="store_true", help="Accepted; output is always JSON")
    tools.set_defaults(func=cmd_mcp)
    schema = actions.add_parser("schema", help="Same catalog as tools, wrapped for agents")
    add_root_argument(schema)
    schema.add_argument("--tool-name-style", choices=["dotted", "portable"], default="dotted")
    schema.add_argument("--json", action="store_true", help="Accepted; output is always JSON")
    schema.set_defaults(func=cmd_mcp)
    call = actions.add_parser("call", help="Call one MCP tool in-process (debugging)")
    add_root_argument(call)
    call.add_argument("tool")
    call.add_argument("--arguments", dest="arguments")
    call.add_argument("--tool-name-style", choices=["dotted", "portable"], default="dotted")
    call.set_defaults(func=cmd_mcp)


def cmd_integrations(args: argparse.Namespace) -> int:
    from arthur_loop.integrations import (
        default_install_targets,
        detect_targets,
        install_targets,
        integration_status,
        parse_targets,
    )

    root = resolve_root(args)
    if args.integrations_action == "detect":
        rows = [item.to_record() for item in detect_targets(root)]
        if args.json:
            print_record(rows)
        else:
            for row in rows:
                mark = "found" if row["found"] else "absent"
                why = f"  ({', '.join(row['reasons'])})" if row["reasons"] else ""
                print(f"{row['target']:<10} {mark}{why}")
        return EXIT_OK
    if args.integrations_action == "status":
        rows = integration_status(root)
        if args.json:
            print_record(rows)
            return EXIT_OK
        for row in rows:
            print(f"{row['target']:<10} {row['state']:<8} skill={row['skill_present']} mcp={row['mcp_present']}")
        return EXIT_OK
    if args.integrations_action == "probe":
        from arthur_loop.integrations import probe_target

        print_record(probe_target(root, args.target, live=args.live))
        return EXIT_OK

    targets = parse_targets(args.targets)
    if not targets:
        targets = default_install_targets(root)
    if not targets:
        print("error: no coding-agent clients detected; pass --targets claude,codex,cursor,grok", file=sys.stderr)
        return EXIT_ERROR
    results = install_targets(
        root,
        targets,
        force=args.force,
        include_mcp=not args.no_mcp,
        trust_folder=not getattr(args, "no_trust_folder", False),
    )
    print_record([item.to_record() for item in results])
    return EXIT_OK


def _build_integrations_parser(subparsers: Any) -> None:
    integ = subparsers.add_parser(
        "integrations",
        help="Detect coding agents and write skills + MCP where those clients actually load them",
    )
    add_root_argument(integ)
    actions = integ.add_subparsers(dest="integrations_action", required=True)
    detect = actions.add_parser("detect", help="Read-only detection (PATH binaries and workspace markers)")
    add_root_argument(detect)
    detect.add_argument("--json", action="store_true")
    detect.set_defaults(func=cmd_integrations)
    install = actions.add_parser(
        "install",
        help=(
            "Write skills, slash commands, and MCP config; Grok also runs grok mcp add "
            "and grok --trust when grok is on PATH (untrusted folder = MCP disconnected)"
        ),
    )
    add_root_argument(install)
    install.add_argument("--targets", help="comma list: claude,codex,cursor,grok,generic")
    install.add_argument(
        "--force",
        action="store_true",
        help="refresh managed instruction blocks (never wipes AGENTS.md house rules)",
    )
    install.add_argument("--no-mcp", action="store_true", help="write skills only")
    install.add_argument(
        "--no-trust-folder",
        action="store_true",
        help="do not run grok --trust (untrusted folder = project MCP disconnected)",
    )
    install.set_defaults(func=cmd_integrations)
    status = actions.add_parser("status", help="Which integration files exist in this instance")
    add_root_argument(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_integrations)
    probe = actions.add_parser(
        "probe",
        help="Prove a client can see Arthur (Grok: mcp list / inspect / optional --always-approve -p)",
    )
    add_root_argument(probe)
    probe.add_argument("--target", default="grok", help="currently: grok")
    probe.add_argument(
        "--live",
        action="store_true",
        help="also run grok --always-approve -p (Grok Build 1.0.30 flag order; needs login)",
    )
    probe.set_defaults(func=cmd_integrations)


def cmd_loop(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import create_loop, list_loops

    root = resolve_root(args)
    if args.loop_action == "list":
        print_record(list_loops(root))
        return EXIT_OK
    roles = None
    if getattr(args, "role", None):
        roles = parse_role_updates(args.role)
    record = create_loop(
        root,
        project_id=args.project_id,
        advisor=args.advisor,
        executor=args.executor,
        tracker=args.tracker,
        roles=roles,
        title=args.title,
        target_chat_url=args.target_chat_url,
        goal=args.goal or "",
        seed_job=not args.no_job,
        actor=args.actor,
        reason=args.reason,
        ticket=getattr(args, "from_ticket", None),
    )
    print_record(record)
    return EXIT_OK


def _build_loop_parser(subparsers: Any) -> None:
    loop = subparsers.add_parser(
        "loop",
        help="Create or list a project loop (wizard: roles + first job — not a graph composer)",
    )
    add_root_argument(loop)
    actions = loop.add_subparsers(dest="loop_action", required=True)
    create = actions.add_parser("create", help="Create a project and the first queue job")
    add_root_argument(create)
    create.add_argument("--project-id", required=True)
    create.add_argument("--advisor", choices=sorted(KNOWN_ADVISORS))
    create.add_argument("--executor", choices=sorted(KNOWN_EXECUTORS))
    create.add_argument("--tracker", choices=sorted(KNOWN_TRACKERS))
    create.add_argument("--title", help="Advisor conversation title")
    create.add_argument("--target-chat-url", default="manual")
    create.add_argument("--goal", help="One-line project goal written into state.md")
    create.add_argument("--from-ticket", help="Ticket id to formulate the default loop from")
    create.add_argument(
        "--role",
        action="append",
        default=[],
        metavar="ROLE=AGENT[:MODEL]",
        help="Assign a role, e.g. reviewer=claude-code:opus (repeatable)",
    )
    create.add_argument("--no-job", action="store_true", help="Skip the first queue job")
    create.add_argument("--actor")
    create.add_argument("--reason")
    create.set_defaults(func=cmd_loop)
    listing = actions.add_parser("list", help="Show projects and current roles")
    add_root_argument(listing)
    listing.set_defaults(func=cmd_loop)


def cmd_follow(args: argparse.Namespace) -> int:
    from arthur_loop.follow import follow_loop

    root = resolve_root(args)
    require_instance(root)
    record = follow_loop(
        root,
        once=args.once,
        max_steps=args.max_steps,
        project_id=args.project_id,
        holder=args.holder,
        chain=not args.no_chain,
        dry_run=args.dry_run,
    )
    print_record(record)
    stopped = record.get("stopped")
    if stopped in {"needs_human", "gate_no_go"}:
        return EXIT_NEEDS_HUMAN
    if stopped in {"invoke_failed"}:
        return EXIT_ERROR
    return EXIT_OK


def _build_follow_parser(subparsers: Any) -> None:
    follow = subparsers.add_parser(
        "follow",
        help="Drive the loop: claim → invoke → submit → capture → gate (auto-follow)",
        description=(
            "Agents follow a created loop without a human typing every hop. "
            "CLI adapters (grok --always-approve -p, claude -p, codex exec) are invoked. "
            "Manual and ChatGPT-browser hops write an inbox and stop. "
            "Open human decisions and implementation-gate NO-GO stay human-gated."
        ),
    )
    add_root_argument(follow)
    follow.add_argument("--once", action="store_true", help="One step, then exit")
    follow.add_argument("--max-steps", type=int, default=12)
    follow.add_argument("--project-id", help="Limit to one Arthur project")
    follow.add_argument("--holder", default="arthur-follow")
    follow.add_argument("--no-chain", action="store_true", help="Do not enqueue the next hop from the control block")
    follow.add_argument("--dry-run", action="store_true")
    follow.set_defaults(func=cmd_follow)


def cmd_roles(args: argparse.Namespace) -> int:
    from arthur_loop.loop_ops import apply_role_updates

    root = resolve_root(args)
    require_instance(root)
    if args.roles_action == "set":
        updates = parse_role_updates(args.assignment)
        apply_role_updates(root, roles=updates)
    print_record(roles_payload(load_config(root)))
    return EXIT_OK


def _build_roles_parser(subparsers: Any) -> None:
    roles = subparsers.add_parser(
        "roles",
        help="Show or assign loop roles (planner, implementer, reviewer, qa)",
    )
    add_root_argument(roles)
    actions = roles.add_subparsers(dest="roles_action")
    listing = actions.add_parser("list", help="Show current role assignments (default)")
    add_root_argument(listing)
    listing.set_defaults(func=cmd_roles, roles_action="list")
    setter = actions.add_parser("set", help="Assign agents: reviewer=claude-code:opus")
    add_root_argument(setter)
    setter.add_argument(
        "assignment",
        nargs="+",
        metavar="ROLE=AGENT[:MODEL]",
        help=f"One of {', '.join(LOOP_ROLES)}",
    )
    setter.set_defaults(func=cmd_roles, roles_action="set")
    roles.set_defaults(func=cmd_roles, roles_action="list")


def cmd_run(args: argparse.Namespace) -> int:
    """Invoke the next assigned agent. Same as `arthur follow --once` unless --all."""

    args.once = not getattr(args, "all", False)
    if getattr(args, "all", False):
        args.once = False
    args.max_steps = getattr(args, "max_steps", 12)
    args.project_id = getattr(args, "project_id", None)
    args.holder = getattr(args, "holder", None) or "arthur-follow"
    args.no_chain = getattr(args, "no_chain", False)
    args.dry_run = getattr(args, "dry_run", False)
    return cmd_follow(args)


def _build_run_parser(subparsers: Any) -> None:
    run = subparsers.add_parser(
        "run",
        help="Call the next assigned agent (one hop). Same as bare `arthur` inside an instance.",
        description=(
            "Run the next hop and hand off to the next role. "
            "Inside an instance, bare `arthur` does the same thing."
        ),
    )
    add_root_argument(run)
    run.add_argument("--all", action="store_true", help="Keep going until idle, gated, or --max-steps")
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--project-id", help="Limit to one Arthur project")
    run.add_argument("--holder", default="arthur-follow")
    run.add_argument("--no-chain", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run, once=True)


# ---------------------------------------------------------------------------
# entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arthur",
        description=(
            "Arthur Loop — assign agents to roles, then run the next hop. "
            "Inside an instance, `arthur` with no subcommand invokes the next role."
        ),
        epilog=(
            "--root may be given before or after any subcommand; the most specific one wins. "
            "Exit codes: 0 ok · 2 refused/error · 3 the loop must stop for a human (capture, gate)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('arthur-loop')}")
    parser.add_argument("--root", default=None, help=ROOT_HELP)
    subparsers = parser.add_subparsers(dest="command", required=False)
    add_init_parser(subparsers, add_root_argument)
    _build_agents_parser(subparsers)
    _build_integrations_parser(subparsers)
    _build_mcp_parser(subparsers)
    _build_loop_parser(subparsers)
    _build_roles_parser(subparsers)
    _build_run_parser(subparsers)
    _build_follow_parser(subparsers)
    _build_queue_parser(subparsers)
    _build_tick_parser(subparsers)
    _build_status_parser(subparsers)
    _build_decision_parser(subparsers)
    _build_gate_parser(subparsers)
    _build_lock_parser(subparsers)
    _build_notify_parser(subparsers)
    _build_tracker_parser(subparsers)
    add_web_parser(subparsers, add_root_argument)
    _build_capture_parser(subparsers)
    _build_usage_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        root = resolve_root(args)
        if is_instance(root):
            args.once = True
            args.max_steps = 12
            args.project_id = None
            args.holder = "arthur-follow"
            args.no_chain = False
            args.dry_run = False
            try:
                return cmd_follow(args)
            except (BrowserLockError, KeyError, ValueError, OSError, RuntimeError) as exc:
                message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
                sys.stderr.write(f"error: {message}\n")
                return EXIT_ERROR
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except (BrowserLockError, KeyError, ValueError, OSError, RuntimeError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        sys.stderr.write(f"error: {message}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
