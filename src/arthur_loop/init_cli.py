from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from arthur_loop.agents import KNOWN_AGENTS, AgentCLI, DetectedAgent, detect_agents, find_agent
from arthur_loop.config import KNOWN_ADVISORS, KNOWN_EXECUTORS, KNOWN_TRACKERS, config_path
from arthur_loop.queue_ledger import QueueJob, QueueLedger
from arthur_loop.status import record_session
from arthur_loop.usage_attribution import append_snapshot, snapshot_from_codexbar_json


ATLAS_INSTALL_CMD = (
    "curl -fsSL https://raw.githubusercontent.com/myrrazor/atlas-tasker/main/scripts/install.sh | sh"
)

PRESETS = ("guided", "solo", "pair", "browser-advisor", "custom")

DEMO_QUOTA_PATH = "usage/demo-quota.json"
DEMO_QUOTA_PAYLOAD = [
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
]

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

INSTRUCTIONS_POINTER = """# Arthur Loop Instance

This directory is an Arthur Loop instance — a durable control plane for an
advisor/executor dev loop. If you are a coding agent working here, you are a
role in that loop.

Start by reading, in order:

1. `agent-setup/skill/SKILL.md` — how to operate the loop (hard rules included)
2. `agent-setup/KICKOFF.md` — the setup interview, if the loop is not configured yet
3. `docs` in the arthur-loop package, via `arthur --help`, `arthur status`, `arthur tick --dry-run`

Non-negotiables: never hand-edit `queue/*.jsonl` (use `arthur queue`), save
advisor output with `arthur capture` before acting on it, and stop for a human
whenever a control block says HUMAN_INPUT_REQUIRED or fails validation.
"""

KICKOFF_TEMPLATE = """# Arthur Loop Kickoff — read this whole file, then run the interview

You are the **Master Orchestrator** of a brand-new Arthur Loop instance in this
directory. Arthur Loop is a file-first control plane: durable queue, saved
artifacts, validated approval gates, human-decision escalation. Your job right
now is to finish setting it up **with** the human, then run the loop for them.

## Step 0 — learn the system (do this before speaking)

- Read `agent-setup/skill/SKILL.md` and its `references/` (workflow, control blocks).
- Read `adapters/advisor/ADAPTER.md` and `adapters/executor/ADAPTER.md`.
- Run `arthur status` and `arthur tick --dry-run` to see the empty loop.

## Current configuration (written by the wizard)

- preset: {preset}
- main agent: {main_agent}
- advisor adapter: {advisor}
- executor adapter: {executor}
- tracker: {tracker}
- detected agent CLIs on this machine: {detected}

## Step 1 — interview the human

Ask, one topic at a time, and keep it conversational:

1. **The flow.** Who should plan/review, and who should implement? Offer the
   shapes by name: solo (you do both), pair (one agent develops, another
   reviews), browser-advisor (a browser AI like ChatGPT Pro plans/reviews).
   Confirm or change the adapters accordingly in `config/arthur-loop.json`
   (valid advisors: {known_advisors}; executors: {known_executors}). If they
   want an agent with no shipped adapter (e.g. Gemini or Grok), author
   `adapters/advisor/ADAPTER.md` or `adapters/executor/ADAPTER.md` for it
   yourself, following `docs/adapters.md` — the contract is a runbook + the
   standard control blocks, no core code.
2. **Review strictness.** What blocks a sprint: P0/P1 only, or any finding?
   When must a human approve — every implementation handoff, or only flagged
   ones? Record the answers in a new `FLOW.md` at the instance root.
3. **Projects.** Which projects should the loop manage? For each: a
   SHOUTY_SNAKE id, one-line goal, and (if the advisor is browser-based) the
   conversation title/URL. Create `projects/<ID>/state.md` and add each to
   `projects` in `config/arthur-loop.json`.
4. **Cadence and quota.** Confirm polling cadence and the quota reserve in the
   config, and whether the governor should be on.

## Step 2 — make it real

- Write `FLOW.md` summarizing every decision (this is the loop's constitution).
- Update `config/arthur-loop.json` to match. Validate by running `arthur status`.
- Report to your session registry: `arthur status set --session-id master
  --role master --state working --activity "setting up the loop"`.
- If a project is ready, seed its first job with `arthur queue create` and walk
  the human through one full planning cycle per the skill.

## Hard rules (from the skill — these override enthusiasm)

- Implementation never starts from a plan-only packet. Before any implementation
  handoff run `arthur gate implementation --project-id <ID>` and proceed only on
  GO; the loop quarantines handoffs captured while the gate is NO-GO.
- Save advisor/executor output with `arthur capture` before acting on it. Exit
  code 3 means the artifact was quarantined or asked for a human — stop that
  project; a decision is already open (`arthur decision list`).
- Escalate with `arthur decision open`, never by editing markdown by hand.
- One advisor conversation at a time — respect the lock.
- Never hand-edit `queue/*.jsonl`.

When setup is done, print the dashboard (`arthur status`) and tell the human
exactly what will happen next and what you are waiting on.
"""


class NonInteractive(RuntimeError):
    """Raised when the wizard would have to ask a question but nobody is there to answer."""


NON_INTERACTIVE_HELP = (
    "stdin is not a terminal, so the setup wizard cannot ask questions. Pass --yes to accept "
    "defaults (and flags for anything specific), e.g. `arthur init --yes --demo` or "
    "`arthur init --yes --main-agent none --advisor manual --executor manual --tracker none --no-governor`."
)


def _ask(prompt: str, default: str, assume_yes: bool) -> str:
    if assume_yes:
        return default
    try:
        raw = input(f"{prompt} [{default}]: ").strip()
    except EOFError as exc:
        raise NonInteractive(NON_INTERACTIVE_HELP) from exc
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


class PresetUnavailable(ValueError):
    """The requested preset cannot be wired with the chosen agents' shipped adapter packs."""


def viable_presets(main: AgentCLI | None, detected: list[DetectedAgent]) -> list[str]:
    """Presets that can be honestly wired for this main agent (offered by the wizard)."""

    presets = ["guided"]
    if main and main.advisor_adapter and main.executor_adapter:
        presets.append("solo")
    reviewers = [item.agent for item in detected if item.agent.advisor_adapter and (not main or item.agent.agent_id != main.agent_id)]
    if main and main.executor_adapter and reviewers:
        presets.append("pair")
    presets.extend(["browser-advisor", "custom"])
    return presets


def resolve_preset(
    preset: str,
    main: AgentCLI | None,
    second: AgentCLI | None,
) -> tuple[str, str, list[str]]:
    """Map a loop preset to (advisor, executor) adapters, with honest notes.

    `solo` and `pair` are only ever wired with shipped adapter packs. When the
    chosen agents have none, this raises instead of quietly substituting the
    `manual` adapter while still calling the result "solo".
    """

    notes: list[str] = []
    main_exec = main.executor_adapter if main else None
    main_adv = main.advisor_adapter if main else None
    main_name = main.name if main else "no main agent"

    if preset == "solo":
        if main_adv and main_exec:
            return main_adv, main_exec, notes
        raise PresetUnavailable(
            f"solo preset needs a main agent with shipped advisor and executor packs; {main_name} has "
            f"advisor={main_adv or 'none'}, executor={main_exec or 'none'}. Use --preset guided (the kickoff "
            "interview helps your agent author an adapter) or --preset custom with explicit --advisor/--executor."
        )

    if preset == "pair":
        second_adv = second.advisor_adapter if second else None
        if main_exec and second_adv:
            return second_adv, main_exec, notes
        missing = []
        if not main_exec:
            missing.append(f"{main_name} has no shipped executor pack")
        if second is None:
            missing.append("no reviewing agent given (--second-agent)")
        elif not second_adv:
            missing.append(f"{second.name} has no shipped advisor pack")
        raise PresetUnavailable(
            "pair preset cannot be wired: " + "; ".join(missing) + ". Agents with shipped packs: "
            + ", ".join(agent.agent_id for agent in KNOWN_AGENTS if agent.executor_adapter or agent.advisor_adapter)
            + ". Use --preset guided or --preset custom instead."
        )

    if preset == "browser-advisor":
        if main and not main_exec:
            notes.append(
                f"{main.name} has no shipped executor pack; the executor is `manual` until you author one (docs/adapters.md)"
            )
        return "chatgpt-browser", (main_exec or "manual"), notes

    # guided: a working base the kickoff interview will finalize
    if main and not main_exec:
        notes.append(
            f"{main.name} has no shipped adapter packs yet — both roles start as `manual`; the kickoff "
            "interview walks your agent through authoring an adapter (docs/adapters.md)"
        )
    return "manual", (main_exec or "manual"), notes


def seed_agent_kickoff(
    root: Path,
    main: AgentCLI,
    *,
    preset: str,
    advisor: str,
    executor: str,
    tracker: str,
    detected: list[DetectedAgent],
) -> list[str]:
    """Copy the skill into the instance and aim the main agent at the interview."""

    seed_pkg = resources.files("arthur_loop") / "seed" / "skill"
    setup_dir = root / "agent-setup"
    _copy_tree(seed_pkg, setup_dir / "skill")

    detected_line = (
        ", ".join(f"{item.agent.name} ({item.agent.agent_id})" for item in detected) or "none"
    )
    kickoff = KICKOFF_TEMPLATE.format(
        preset=preset,
        main_agent=f"{main.name} ({main.agent_id})",
        advisor=advisor,
        executor=executor,
        tracker=tracker,
        detected=detected_line,
        known_advisors=", ".join(sorted(KNOWN_ADVISORS)),
        known_executors=", ".join(sorted(KNOWN_EXECUTORS)),
    )
    (setup_dir / "KICKOFF.md").write_text(kickoff, encoding="utf-8")

    seeded: list[str] = ["agent-setup/KICKOFF.md", "agent-setup/skill/"]
    if main.skills_dir:
        _copy_tree(seed_pkg, root / main.skills_dir / "arthur-loop")
        seeded.append(f"{main.skills_dir}/arthur-loop/")
    if main.instructions_file:
        pointer = root / main.instructions_file
        if not pointer.exists():
            pointer.write_text(INSTRUCTIONS_POINTER, encoding="utf-8")
            seeded.append(main.instructions_file)
    return seeded


def run_init(args: argparse.Namespace) -> int:
    try:
        return _run_init(args)
    except NonInteractive as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except PresetUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_init(args: argparse.Namespace) -> int:
    root = Path(getattr(args, "root", None) or ".").resolve()
    yes = args.yes

    if config_path(root).exists() and not args.force:
        print(f"error: {config_path(root)} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    if not yes and not sys.stdin.isatty():
        raise NonInteractive(NON_INTERACTIVE_HELP)

    print("Arthur Loop setup — pick the pieces of your loop.\n")

    detected = detect_agents(with_versions=not yes)
    if detected:
        print("Detected agent CLIs:")
        for item in detected:
            version = f"  ({item.version})" if item.version else ""
            packs = "shipped adapter packs" if item.agent.executor_adapter else "no shipped adapter pack (guided/custom only)"
            print(f"  • {item.agent.name:<12} {item.path}{version}  [{packs}]")
    else:
        print("No known agent CLIs detected (looked for: "
              + ", ".join(agent.binary for agent in KNOWN_AGENTS) + ").")
    print()

    # main agent first — everything else hangs off this choice
    agent_choices = [item.agent.agent_id for item in detected] or [a.agent_id for a in KNOWN_AGENTS]
    default_main = args.main_agent or (detected[0].agent.agent_id if detected else "none")
    main_id = args.main_agent or _ask_choice(
        "Main agent (runs the loop and the setup interview)",
        agent_choices + ["none"],
        default_main,
        yes,
    )
    main = find_agent(main_id) if main_id != "none" else None

    offered = viable_presets(main, detected)
    preset = args.preset or _ask_choice("Loop preset", offered, "guided" if main else "custom", yes)

    second: AgentCLI | None = find_agent(args.second_agent) if args.second_agent else None
    if preset == "pair" and second is None and not yes:
        others = [
            item.agent.agent_id
            for item in detected
            if item.agent.advisor_adapter and (not main or item.agent.agent_id != main.agent_id)
        ]
        if others:
            second = find_agent(_ask_choice("Reviewing agent", others, others[0], False))

    if preset == "custom":
        advisor = args.advisor or _ask_choice(
            "Who plans and reviews? (your advisor)", sorted(KNOWN_ADVISORS), "chatgpt-browser", yes
        )
        executor = args.executor or _ask_choice(
            "Who implements? (your executor)", sorted(KNOWN_EXECUTORS), "codex", yes
        )
        preset_notes: list[str] = []
    else:
        advisor, executor, preset_notes = resolve_preset(preset, main, second)
        # explicit flags always win over the preset
        advisor = args.advisor or advisor
        executor = args.executor or executor
    for note in preset_notes:
        print(f"note: {note}")

    adapters_pkg = resources.files("arthur_loop") / "adapters"
    for role, name in (("advisors", advisor), ("executors", executor)):
        if not (adapters_pkg / role / name).is_dir():
            raise PresetUnavailable(f"no shipped {role[:-1]} pack named {name!r}")

    tracker = args.tracker or _ask_choice("Ticket tracker?", sorted(KNOWN_TRACKERS), "none", yes)

    quota_provider = args.quota_provider or "auto"
    quota_path: str | None = None
    if args.no_governor:
        governor = False
    elif args.demo:
        # the demo seeds a quota snapshot; the dashboard shows it only with the governor on,
        # and a file source keeps `arthur usage snapshot` working inside the demo
        governor = True
        quota_provider = "file"
        quota_path = DEMO_QUOTA_PATH
    else:
        codexbar_found = shutil.which("codexbar") is not None
        source_hint = (
            "codexbar detected — quota follows your subscriptions automatically"
            if codexbar_found
            else "no codexbar found — you can point quota.provider at a command or JSON file later"
        )
        governor = _ask_bool(
            f"Enable the quota governor? ({source_hint})",
            codexbar_found or quota_provider not in ("auto", "none"),
            yes,
        )
        if governor and quota_provider == "auto" and not codexbar_found:
            print("note: governor is on but no quota source was found; `arthur usage snapshot` will")
            print("      report how to wire one (codexbar, a custom command, or a JSON file).")
    heartbeat = not args.no_heartbeat and _ask_bool("Enable heartbeat state files (runtime/)?", True, yes)

    projects: list[dict[str, str]] = []
    while not yes and _ask_bool("Add a project now?", False, False):
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
    if not projects and main is not None:
        print("(no projects yet — your main agent's kickoff interview will create them)")

    if tracker == "atlas-tasker" and shutil.which("tracker") is None:
        print("\nAtlas Tasker's `tracker` CLI is not on PATH. Install it from the open-source repo")
        print("(review the script first — it downloads a release binary):")
        print(f"  {ATLAS_INSTALL_CMD}")
        print("Tracker calls will report errors until it is installed.")

    quota_config: dict[str, Any] = {"provider": quota_provider, "codexbar_provider": "codex"}
    if quota_path:
        quota_config["path"] = quota_path
    config = {
        "version": 2,
        "advisor": {"adapter": advisor},
        "executor": {"adapter": executor},
        "tracker": {"adapter": tracker},
        "components": {"resource_governor": governor, "heartbeat": heartbeat},
        "quota": quota_config,
        "reserve_policy": {"minimum_reserve_percent": float(args.reserve_percent)},
        "polling_policy": {
            "first_poll_minutes": 1,
            "steady_poll_minutes": 5,
            "max_retries_after_stopped_no_output": 1,
        },
        "flow": {
            "preset": preset,
            "main_agent": main.agent_id if main else None,
            "reviewer": second.agent_id if second else None,
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

    _copy_tree(adapters_pkg / "advisors" / advisor, root / "adapters/advisor")
    _copy_tree(adapters_pkg / "executors" / executor, root / "adapters/executor")

    seeded: list[str] = []
    if main is not None and not args.no_kickoff:
        seeded = seed_agent_kickoff(
            root,
            main,
            preset=preset,
            advisor=advisor,
            executor=executor,
            tracker=tracker,
            detected=detected,
        )

    if args.demo:
        seed_demo(root)

    print("\nDone. Your loop:")
    print(f"  root:     {root}")
    print(f"  preset:   {preset}" + (f"   main agent: {main.name}" if main else ""))
    print(f"  advisor:  {advisor}   (runbook: adapters/advisor/ADAPTER.md)")
    print(f"  executor: {executor}   (runbook: adapters/executor/ADAPTER.md)")
    print(f"  tracker:  {tracker}")
    governor_note = "on" if governor else "off"
    if governor:
        governor_note += f"   quota source: {quota_provider}" + (f" ({quota_path})" if quota_path else "")
    print(f"  governor: {governor_note}   heartbeat: {'on' if heartbeat else 'off'}")
    if seeded:
        print("\nSeeded for your main agent: " + ", ".join(seeded))
        print("\nHand over to it now — it will interview you and finish the setup:")
        if main and main.launch_hint:
            print(f"  cd {root}")
            print(f"  {main.launch_hint}")
        else:
            print(f"  start {main.name if main else 'your agent'} in {root} and paste agent-setup/KICKOFF.md")
    else:
        print("\nNext steps:")
        print("  1. Read adapters/advisor/ADAPTER.md and tune the prompts in adapters/advisor/prompts/.")
        print("  2. Create your first job:   arthur queue create --job-id BQ-<PROJECT>-001 --project-id <PROJECT> \\")
        print("                                --target-chat-title \"<PROJECT> planning\" --target-chat-url manual")
        print("  3. Walk one hop:            arthur queue claim → submit → arthur capture → arthur queue poll-result")
        print("  4. See the whole loop:      arthur status   (README: Quickstart has the full manual walk-through)")
    if args.demo:
        print("\nDemo state is seeded — run `arthur status` right now to see a busy loop.")
        print("The demo quota bar reads usage/demo-quota.json; edit it and run `arthur usage snapshot --snapshot-id x`.")
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

    quota_file = root / DEMO_QUOTA_PATH
    quota_file.parent.mkdir(parents=True, exist_ok=True)
    quota_file.write_text(json.dumps(DEMO_QUOTA_PAYLOAD, indent=2) + "\n", encoding="utf-8")
    append_snapshot(root, snapshot_from_codexbar_json(DEMO_QUOTA_PAYLOAD, snapshot_id="demo-seed"))


def add_init_parser(subparsers: Any, add_root: Any = None) -> None:
    init = subparsers.add_parser(
        "init",
        help="Set up an instance — pick your agents, flow preset, and tracker (use --yes when not at a terminal)",
        description=(
            "Interactive by default. Without a terminal (cron, CI, piped stdin) pass --yes to accept "
            "defaults; every question also has a flag. Defaults with --yes: main agent = first detected CLI "
            "(else none), preset = guided (custom when no agent), tracker = none, governor on only when codexbar "
            "is installed, heartbeat on."
        ),
    )
    if add_root is not None:
        add_root(init)
    else:
        init.add_argument("--root", default=".", help="Directory to initialize as an Arthur Loop instance")
    init.add_argument("--yes", action="store_true", help="Accept defaults / provided flags, ask nothing")
    init.add_argument("--main-agent", choices=[a.agent_id for a in KNOWN_AGENTS] + ["none"],
                      help="Agent that runs the loop and the setup interview (default: first detected)")
    init.add_argument("--preset", choices=list(PRESETS),
                      help="Loop shape: guided, solo, pair, browser-advisor, or custom")
    init.add_argument("--second-agent", choices=[a.agent_id for a in KNOWN_AGENTS],
                      help="Reviewing agent for the pair preset")
    init.add_argument("--advisor", choices=sorted(KNOWN_ADVISORS), help="Override the advisor adapter")
    init.add_argument("--executor", choices=sorted(KNOWN_EXECUTORS), help="Override the executor adapter")
    init.add_argument("--tracker", choices=sorted(KNOWN_TRACKERS))
    init.add_argument("--no-governor", action="store_true")
    init.add_argument("--quota-provider", choices=["auto", "codexbar", "command", "file", "none"],
                      help="Where quota numbers come from (default auto: codexbar when installed)")
    init.add_argument("--no-heartbeat", action="store_true")
    init.add_argument("--no-kickoff", action="store_true", help="Skip seeding the main agent's setup interview")
    init.add_argument("--reserve-percent", type=float, default=5.0)
    init.add_argument("--demo", action="store_true", help="Seed sample projects, a job, sessions, and a decision")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=run_init)
