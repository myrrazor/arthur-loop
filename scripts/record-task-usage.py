#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arthur_loop.usage_attribution import (
    append_task_usage,
    estimate_task_usage,
    latest_snapshots,
    render_usage_dashboard,
    task_usage_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate task usage from two quota snapshots.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--role", required=True, help="Master Orchestrator, Browser Queue Manager, Codex Coding Session, etc.")
    parser.add_argument("--task-label", required=True)
    parser.add_argument("--before", required=True, help="Before snapshot id")
    parser.add_argument("--after", required=True, help="After snapshot id")
    parser.add_argument("--active-codex-tasks", type=int, default=1)
    parser.add_argument("--background-activity-possible", action="store_true")
    parser.add_argument("--note", action="append", default=[])
    args = parser.parse_args()

    root = Path(args.root).resolve()
    snapshots = latest_snapshots(root)
    missing = [snapshot_id for snapshot_id in (args.before, args.after) if snapshot_id not in snapshots]
    if missing:
        available = ", ".join(sorted(snapshots)) or "none"
        raise SystemExit(f"unknown snapshot id(s): {', '.join(missing)}; available: {available}")

    before = snapshots[args.before]
    after = snapshots[args.after]
    estimate = estimate_task_usage(
        before,
        after,
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
    records = task_usage_records(root)
    output_path.write_text(render_usage_dashboard(records), encoding="utf-8")

    print(json.dumps(estimate.to_record(), indent=2, sort_keys=True))
    print(f"dashboard: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
