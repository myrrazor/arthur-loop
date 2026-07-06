from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from arthur_loop.config import KNOWN_ADVISORS, KNOWN_EXECUTORS, KNOWN_TRACKERS, config_path
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.status import record_session
from arthur_loop.usage_attribution import append_snapshot, snapshot_from_codexbar_json


ATLAS_INSTALL_CMD = (
    "curl -fsSL https://raw.githubusercontent.com/myrrazor/atlas-tasker/main/scripts/install.sh | sh"
)

INSTANCE_DIRS = [
    "queue/prompts",
    "projects",
    "human-decisions",
    "runtime",
    "usage",
    "outputs",
    "config",
    "adapters",
]

INSTANCE_GITIGNORE = """.DS_Store
usage/*.jsonl
runtime/
TEST_STDOUT.log
"""

OPEN_DECISIONS_STUB = """# Open Human Decisions

None right now. The loop appends here when a project needs you.
"""


def _ask(prompt: str, default: str, assume_yes: bool) -> str:
    if assume_yes:
        return default
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


def _ask_choice(prompt: str, choices: list[str], default: str, assume_yes: bool) -> str:
    if assume_yes:
        return default
    while True:
        raw = _ask(f"{prompt} ({'/'.join(choices)})", default, assume_yes=False)
        if raw in choices:
            return raw
        print(f"  pick one of: {', '.join(choices)}")


def _ask_bool(prompt: str, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        return default
    raw = _ask(f"{prompt} (y/n)", "y" if default else "n", assume_yes=False).lower()
    return raw.startswith("y")


def _copy_tree(source: Any, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            target.write_bytes(item.read_bytes())


def run_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    yes = args.yes

    if config_path(root).exists() and not args.force:
        print(f"error: {config_path(root)} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    print("Arthur Loop setup — pick the pieces of your loop.\n")

    advisor = args.advisor or _ask_choice(
        "Who plans and reviews? (your advisor)", sorted(KNOWN_ADVISORS), "chatgpt-browser", yes
    )
    executor = args.executor or _ask_choice(
        "Who implements? (your executor)", sorted(KNOWN_EXECUTORS), "codex", yes
    )
    tracker = args.tracker or _ask_choice("Ticket tracker?", sorted(KNOWN_TRACKERS), "none", yes)

    if args.no_governor:
        governor = False
    else:
        codexbar_found = shutil.which("codexbar") is not None
        governor = _ask_bool(
            "Enable the quota governor? (needs `codexbar`; detected: %s)"
            % ("yes" if codexbar_found else "no"),
            codexbar_found,
            yes,
        )
    heartbeat = not args.no_heartbeat and _ask_bool("Enable heartbeat state files (runtime/)?", True, yes)

    projects: list[dict[str, str]] = []
    while not yes and _ask_bool("Add a project now?", not projects, False):
        project_id = _ask("  project id (SHOUTY_SNAKE)", "MY_APP", False)
        title = _ask("  advisor conversation/target title", f"{project_id} planning", False)
        url = _ask("  advisor target URL (blank if n/a)", "", False)
        projects.append(
            {
                "project_id": project_id,
                "advisor_target_title": title,
                "advisor_target_url": url,
                "state_path": f"projects/{project_id}/state.md",
            }
        )

    if tracker == "atlas-tasker" and shutil.which("tracker") is None:
        print("\nAtlas Tasker's `tracker` CLI is not on PATH. Install it from the open-source repo")
        print("(review the script first — it downloads a release binary):")
        print(f"  {ATLAS_INSTALL_CMD}")
        print("Tracker calls will report errors until it is installed.")

    config = {
        "version": 2,
        "advisor": {"adapter": advisor},
        "executor": {"adapter": executor},
        "tracker": {"adapter": tracker},
        "components": {"resource_governor": governor, "heartbeat": heartbeat},
        "reserve_policy": {"minimum_reserve_percent": float(args.reserve_percent)},
        "polling_policy": {
            "first_poll_minutes": 1,
            "steady_poll_minutes": 5,
            "max_retries_after_stopped_no_output": 1,
        },
        "projects": projects,
    }

    for rel in INSTANCE_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    if advisor == "manual":
        (root / "queue/manual/pending").mkdir(parents=True, exist_ok=True)
        (root / "queue/manual/done").mkdir(parents=True, exist_ok=True)
    config_path(root).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    decisions = root / "human-decisions/open.md"
    if not decisions.exists():
        decisions.write_text(OPEN_DECISIONS_STUB, encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(INSTANCE_GITIGNORE, encoding="utf-8")
    for project in projects:
        state = root / project["state_path"]
        state.parent.mkdir(parents=True, exist_ok=True)
        if not state.exists():
            state.write_text(f"# {project['project_id']} State\n\nNot started.\n", encoding="utf-8")

    adapters_pkg = resources.files("arthur_loop") / "adapters"
    _copy_tree(adapters_pkg / "advisors" / advisor, root / "adapters/advisor")
    _copy_tree(adapters_pkg / "executors" / executor, root / "adapters/executor")

    if args.demo:
        seed_demo(root)

    print("\nDone. Your loop:")
    print(f"  advisor:  {advisor}   (runbook: adapters/advisor/ADAPTER.md)")
    print(f"  executor: {executor}   (runbook: adapters/executor/ADAPTER.md)")
    print(f"  tracker:  {tracker}")
    print(f"  governor: {'on' if governor else 'off'}   heartbeat: {'on' if heartbeat else 'off'}")
    print("\nNext steps:")
    print("  1. Read adapters/advisor/ADAPTER.md and tune the prompts in adapters/advisor/prompts/.")
    print("  2. Create your first job:   arthur queue create --job-id BQ-<PROJECT>-001 ...")
    print("  3. See the whole loop:      arthur status")
    if args.demo:
        print("\nDemo state is seeded — run `arthur status` right now to see a busy loop.")
    return 0


def seed_demo(root: Path) -> None:
    """Populate a believable instance so the first `arthur status` has a story to tell."""

    demo_states = {
        "DEMO_APP": "Sprint 2 implementing — the advisor approved the plan, executor is mid-sprint.",
        "SAMPLE_APP": "Paused — waiting on a human decision about auth scope.",
    }
    for project_id, summary in demo_states.items():
        state = root / "projects" / project_id / "state.md"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(f"# {project_id} State\n\n{summary}\n", encoding="utf-8")

    prompt_path = root / "queue/prompts/bq-demo_app-002.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "# BQ-DEMO_APP-002 Prompt\n\nArthur Loop demo job. Ask the advisor for the sprint 2 review.\n",
        encoding="utf-8",
    )
    ledger = QueueLedger(root)
    if "BQ-DEMO_APP-002" not in ledger.latest_jobs():
        ledger.record_job(
            QueueJob(
                job_id="BQ-DEMO_APP-002",
                project_id="DEMO_APP",
                target_chat_title="Demo App Planning",
                target_chat_url="https://chatgpt.com/c/your-conversation-id",
                prompt_path="queue/prompts/bq-demo_app-002.md",
                expected_marker="DEMO_LOOP_SPRINT_2",
                idempotency_key="bq-demo_app-002-demo",
            )
        )

    (root / "human-decisions").mkdir(parents=True, exist_ok=True)
    (root / "human-decisions/open.md").write_text(
        "# Open Human Decisions\n"
        "\n"
        "## SAMPLE_APP Auth Scope Gate\n"
        "\n"
        "Status: `OPEN`\n"
        "\n"
        "Should sessions live for 24 hours or 7 days? The advisor wants a human call\n"
        "before planning continues. Other projects keep moving while this is open.\n",
        encoding="utf-8",
    )

    record_session(
        root,
        session_id="master",
        role="master",
        state="working",
        activity="orchestrating the demo loop",
    )
    record_session(
        root,
        session_id="demo-app-executor",
        role="executor",
        project_id="DEMO_APP",
        state="working",
        activity="implementing sprint 2",
    )

    append_snapshot(
        root,
        snapshot_from_codexbar_json(
            [
                {
                    "provider": "codex",
                    "usage": {
                        "primary": {
                            "usedPercent": 38,
                            "windowMinutes": 300,
                            "resetDescription": "Resets 4:00 AM",
                        }
                    },
                }
            ],
            snapshot_id="demo-seed",
        ),
    )


def add_init_parser(subparsers: Any) -> None:
    init = subparsers.add_parser("init", help="Interactive setup — pick your advisor, executor, and tracker")
    init.add_argument("--root", default=".", help="Directory to initialize as an Arthur Loop instance")
    init.add_argument("--yes", action="store_true", help="Accept defaults / provided flags, ask nothing")
    init.add_argument("--advisor", choices=sorted(KNOWN_ADVISORS))
    init.add_argument("--executor", choices=sorted(KNOWN_EXECUTORS))
    init.add_argument("--tracker", choices=sorted(KNOWN_TRACKERS))
    init.add_argument("--no-governor", action="store_true")
    init.add_argument("--no-heartbeat", action="store_true")
    init.add_argument("--reserve-percent", type=float, default=5.0)
    init.add_argument("--demo", action="store_true", help="Seed sample projects, a job, sessions, and a decision")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=run_init)
