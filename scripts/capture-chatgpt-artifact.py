#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arthur_loop.artifact_store import save_chatgpt_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Save ChatGPT browser text as a project-local artifact.")
    parser.add_argument("--root", default=".", help="Arthur Loop repo root")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--kind", required=True, help="next-plan-request, plan-review, sprint-review, etc.")
    parser.add_argument("--source-chat-title", required=True)
    parser.add_argument("--source-file", help="Markdown/text file to capture. Reads stdin when omitted.")
    parser.add_argument("--created-at", help="Override capture timestamp")
    parser.add_argument("--title", help="Human title for the artifact")
    parser.add_argument("--no-link-queue", action="store_true", help="Do not append artifact path to queue job state")
    args = parser.parse_args()

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


if __name__ == "__main__":
    raise SystemExit(main())
