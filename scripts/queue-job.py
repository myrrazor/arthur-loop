#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from arthur_loop.browser_lock import BrowserLockError, acquire_lock, read_lock, release_lock
from arthur_loop.queue_ledger import QueueJob, QueueLedger, parse_ledger_time


DEFAULT_HOLDER = "browser-queue-manager"


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
        raise BrowserLockError("browser lock is not held; run queue-job.py claim or acquire a lock first")
    if lock.holder != holder:
        raise BrowserLockError(f"browser lock is held by {lock.holder}, not {holder}")


def cmd_create(args: argparse.Namespace) -> int:
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


def cmd_claim(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    acquire_lock(root, args.holder, ttl_minutes=args.ttl_minutes, now=parse_at(args.at))
    job = QueueLedger(root).transition(args.job_id, "claimed", now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    require_lock(root, args.holder)
    job = QueueLedger(root).transition(args.job_id, "submitted", now=parse_at(args.at))
    if not args.keep_lock:
        release_lock(root, args.holder, now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_poll_result(args: argparse.Namespace) -> int:
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


def cmd_complete(args: argparse.Namespace) -> int:
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


def cmd_fail(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    job = QueueLedger(root).transition(args.job_id, "failed", now=parse_at(args.at), error=args.error)
    print_record(job)
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    ledger = QueueLedger(root)
    job = ledger.transition(args.job_id, "needs_recovery", now=parse_at(args.at), error=args.error)
    if args.requeue:
        job = ledger.transition(args.job_id, "queued", now=parse_at(args.at))
    print_record(job)
    return 0


def cmd_due(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    jobs = [job.to_record() for job in QueueLedger(root).due_jobs(parse_at(args.at))]
    print_record(jobs)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    jobs = QueueLedger(root).latest_jobs()
    if args.job_id:
        print_record(jobs[args.job_id])
    else:
        print_record({job_id: job.to_record() for job_id, job in sorted(jobs.items())})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and update Arthur Loop browser queue jobs.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--job-id", required=True)
    create.add_argument("--project-id", required=True)
    create.add_argument("--target-chat-title", required=True)
    create.add_argument("--target-chat-url", required=True)
    create.add_argument("--prompt-path")
    create.add_argument("--expected-marker")
    create.add_argument("--idempotency-key")
    create.add_argument("--priority", type=int, default=100)
    create.add_argument("--force", action="store_true", help="append a fresh queued snapshot even if the job id already exists")
    create.set_defaults(func=cmd_create)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--job-id", required=True)
    claim.add_argument("--holder", default=DEFAULT_HOLDER)
    claim.add_argument("--ttl-minutes", type=int, default=15)
    claim.add_argument("--at")
    claim.set_defaults(func=cmd_claim)

    submit = subparsers.add_parser("submit")
    submit.add_argument("--job-id", required=True)
    submit.add_argument("--holder", default=DEFAULT_HOLDER)
    submit.add_argument("--at")
    submit.add_argument("--keep-lock", action="store_true")
    submit.set_defaults(func=cmd_submit)

    poll = subparsers.add_parser("poll-result")
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
    poll.set_defaults(func=cmd_poll_result)

    complete = subparsers.add_parser("complete")
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--at")
    complete.add_argument("--warning")
    complete.add_argument("--output-artifact-path", action="append")
    complete.set_defaults(func=cmd_complete)

    fail = subparsers.add_parser("fail")
    fail.add_argument("--job-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--at")
    fail.set_defaults(func=cmd_fail)

    recover = subparsers.add_parser("recover")
    recover.add_argument("--job-id", required=True)
    recover.add_argument("--error")
    recover.add_argument("--requeue", action="store_true", help="send the job back to queued after parking it")
    recover.add_argument("--at")
    recover.set_defaults(func=cmd_recover)

    due = subparsers.add_parser("due")
    due.add_argument("--at")
    due.set_defaults(func=cmd_due)

    show = subparsers.add_parser("show")
    show.add_argument("--job-id")
    show.set_defaults(func=cmd_show)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (BrowserLockError, KeyError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
