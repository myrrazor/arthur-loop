#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arthur_loop.queue_ledger import parse_ledger_time
from arthur_loop.tick import classify_tick, render_tick_markdown, write_tick_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify the next Arthur Loop scheduler action.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--dry-run", action="store_true", help="Do not write runtime/tick-state.json or queue events")
    parser.add_argument("--at", help="Override current time for tests, e.g. 2026-07-02T12:00:00Z")
    parser.add_argument("--format", choices=["json", "markdown", "both"], default="both")
    parser.add_argument("--reserve-percent", type=float, default=5.0)
    parser.add_argument("--stale-after-minutes", type=int, default=30)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    result = classify_tick(
        root,
        now=parse_ledger_time(args.at) if args.at else None,
        dry_run=args.dry_run,
        reserve_percent=args.reserve_percent,
        stale_after_minutes=args.stale_after_minutes,
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


if __name__ == "__main__":
    raise SystemExit(main())
