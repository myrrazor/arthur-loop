#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arthur_loop.queue_ledger import parse_ledger_time
from arthur_loop.status import (
    VALID_SESSION_STATES,
    build_console,
    clear_session,
    collect_status,
    record_session,
    render_status,
    status_to_dict,
)


def _add_show_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable status and exit")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--at", help="Override current time, e.g. 2026-07-02T12:00:00Z")
    parser.add_argument("--reserve-percent", type=float, default=5.0)
    parser.add_argument("--stale-after-minutes", type=int, default=30)
    parser.add_argument("--session-stale-minutes", type=int, default=60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the Arthur Loop status dashboard.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    _add_show_args(parser)
    subparsers = parser.add_subparsers(dest="action")

    show = subparsers.add_parser("show", help="Render the dashboard (default)")
    _add_show_args(show)

    setter = subparsers.add_parser("set", help="Report what this agent session is doing")
    setter.add_argument("--session-id", required=True)
    setter.add_argument("--role", required=True, help="master, queue-manager, project-loop, executor, governor, ...")
    setter.add_argument("--activity", required=True, help="Short human-readable description of current work")
    setter.add_argument("--state", default="working", choices=sorted(VALID_SESSION_STATES))
    setter.add_argument("--project-id")
    setter.add_argument("--at")

    clear = subparsers.add_parser("clear", help="Drop this session from the dashboard")
    clear.add_argument("--session-id", required=True)
    clear.add_argument("--at")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    at = parse_ledger_time(args.at) if getattr(args, "at", None) else None

    if args.action == "set":
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

    if args.action == "clear":
        cleared = clear_session(root, args.session_id, now=at)
        print(json.dumps({"session_id": args.session_id, "cleared": cleared}, indent=2, sort_keys=True))
        return 0

    snapshot = collect_status(
        root,
        now=at,
        reserve_percent=args.reserve_percent,
        stale_after_minutes=args.stale_after_minutes,
        session_stale_minutes=args.session_stale_minutes,
    )
    if args.json:
        print(json.dumps(status_to_dict(snapshot), indent=2, sort_keys=True))
        return 0

    render_status(snapshot, build_console(no_color=args.no_color))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2)
