#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from arthur_loop.usage_attribution import append_snapshot, snapshot_from_codexbar_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a normalized codexbar usage snapshot.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--snapshot-id", required=True, help="Stable snapshot id, e.g. before-bq-helioterm-plan-001")
    parser.add_argument("--input-json", help="Read codexbar JSON from this file instead of running codexbar")
    parser.add_argument("--provider", default="codex", help="Provider to normalize from codexbar JSON")
    args = parser.parse_args()

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


if __name__ == "__main__":
    raise SystemExit(main())
