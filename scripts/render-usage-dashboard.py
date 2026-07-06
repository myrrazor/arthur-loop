#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from arthur_loop.usage_attribution import render_usage_dashboard, task_usage_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Arthur Loop task usage dashboard.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--output", default="outputs/usage-dashboard.md")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_usage_dashboard(task_usage_records(root)), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
