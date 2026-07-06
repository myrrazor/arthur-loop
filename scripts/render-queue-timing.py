#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arthur_loop.queue_ledger import read_jsonl
from arthur_loop.reporting import poll_timing_rows, render_poll_timing_table


def main() -> int:
    parser = argparse.ArgumentParser(description="Render browser queue polling timing deltas.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--output", default="outputs/browser-queue/poll-timing.md")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rows = poll_timing_rows(read_jsonl(root / "queue/jobs.jsonl"), read_jsonl(root / "queue/events.jsonl"))
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# Browser Queue Poll Timing\n\n" + render_poll_timing_table(rows), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
