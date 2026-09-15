<p align="center">
  <img src="assets/banner.svg" alt="Arthur Loop — a file-first control plane for AI dev loops" width="100%">
</p>

File-first control plane for AI dev loops — bring your own advisor, executor, and tracker.

<div align="center">
  <a href="https://github.com/myrrazor/arthur-loop/releases/latest"><img alt="Release: v0.1.0" src="https://img.shields.io/badge/release-v0.1.0-3fb950?style=for-the-badge"></a>
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-3fb950?style=for-the-badge">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-58a6ff?style=for-the-badge">
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-d29922?style=for-the-badge">
</div>

<p align="center">
  <img src="docs/assets/arthur-loop-demo.gif" alt="Arthur Loop terminal demo showing a live queue, a blocked human decision, and the scheduler choosing the next due job" width="880">
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#the-status-dashboard">The Dashboard</a> ·
  <a href="#the-web-console">Web Console</a> ·
  <a href="#pick-your-pieces">Pick Your Pieces</a> ·
  <a href="#the-loop-end-to-end">The Loop</a> ·
  <a href="#adapters">Adapters</a> ·
  <a href="#faq">FAQ</a>
</p>

## Install

Pick one. All three install an isolated `arthur` command; Python 3.9 or newer is the only requirement.

```bash
pipx install git+https://github.com/myrrazor/arthur-loop.git

# or with uv
uv tool install git+https://github.com/myrrazor/arthur-loop.git

# or let the installer manage a venv under ~/.arthur-loop
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/install.sh | sh
```

From a checkout, `./install.sh` installs that checkout. Run `arthur --version` to confirm the installed release.

The three commands above track default git HEAD (usually `main`). The last tag is **v0.1.0** — `arthur --version` prints that package version until the next tag. That public tree is a **file cockpit**: `init`, queue, capture, gate, `status`, `arthur web`. It does **not** ship `arthur follow`, the MCP server, or Atlas walk.

To install **this** unreleased branch (integrations, MCP, `arthur follow`, Atlas next/walk):

```bash
pipx install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'
```

See [docs/install.md](docs/install.md). How it works (video + wizard shot): [docs/how-it-works.md](docs/how-it-works.md). On this branch only: `arthur follow --once` (Grok transport is `grok --always-approve -p PROMPT`).

One AI plans and reviews (the **advisor**), another implements (the **executor**), and Arthur Loop keeps the whole thing honest: a queue with a real state machine, saved artifacts, approval gates computed from those artifacts, human-decision escalation, and a scheduler tick that always knows what should happen next.

No daemon. No database. No API keys required by the core. Everything is markdown and JSONL in a directory you own, driven by a small `arthur` CLI. On this branch, a stdio MCP server is also present so Grok Build, Claude Code, Codex, and Cursor can claim, capture, and gate without a human typing every hop. See [docs/how-it-works.md](docs/how-it-works.md).

> **Status: alpha.** The file formats are plain and the CLI is tested on Linux and macOS, but command names and adapter contracts may still tighten before 1.0. Known limitations are listed in the [FAQ](#faq).

<p align="center">
  <img src="assets/status-demo.svg" alt="arthur status — terminal dashboard showing loop state, sessions, queue, projects, decisions, and quota" width="880">
  <br>
  <sub><code>arthur status</code> — the whole loop in one command: agent sessions, queue, projects, human decisions, quota, and the browser lock.</sub>
</p>

## Why

Long AI loops fail at the seams: duplicate prompts after a crash, "approved" plans nobody approved, one stuck question freezing every project, quota burned while idle. Arthur Loop is those seams, solved with boring files:

- **Queue ledger** — append-only JSONL with a fixed state machine: `queued → claimed → submitted → waiting/stopped → completed | failed | cancelled`, plus `needs_recovery`. Illegal moves (completing work that was never submitted, touching a finished job) are refused. Duplicate job ids and reused `--idempotency-key`s are refused. After a crash the ledger replays to the last recorded step; `arthur queue recover` parks or requeues the abandoned job *and* releases the dead manager's browser lease.
- **Artifact store** — every advisor and executor response is saved and indexed *before* it's acted on. Control blocks are parsed only from the final fenced block; values must match the enums, the `REVIEW_TYPE` must match the hop you captured, and a plan that says `IMPLEMENTATION_STARTED: true` is invalid. Anything that fails is **quarantined and opens a human decision**, which pauses that project until a person answers.
- **Approval gates** — `arthur gate implementation --project-id X` answers GO/NO-GO from the saved artifacts alone (newest plan review is a valid `APPROVE_PLAN`, nothing re-opened planning since, no open decision). An implementation handoff captured while the gate is NO-GO is quarantined. Prompts still ask executors to behave; the gate is what the loop checks regardless.
- **Human decisions** — `arthur decision open|answer|clear|list`. A blocking question pauses *its* project, never the others.
- **Tick** — `arthur tick` reads durable state and answers WAIT / POLL_DUE / BLOCKED_BY_BROWSER_LOCK / BLOCKED_BY_QUOTA / HUMAN_INPUT_REQUIRED. Run it from cron if you want a heartbeat; it costs no tokens.
- **Status** — `arthur status` renders all of it as the dashboard above; `--json` feeds bots and notifiers.

## Quickstart

### 1. Feel the loop in five minutes (no agents, no automation)

The `manual` advisor is you and two folders. Every command below runs non-interactively and is exercised by the test suite (`tests/test_quickstart.py`), so it works exactly as written:

```bash
mkdir ~/my-loop && cd ~/my-loop
arthur init --yes --main-agent none --advisor manual --executor manual --tracker none --no-governor

# 1. queue a planning request for a project
arthur queue create --job-id BQ-MY_APP-001 --project-id MY_APP \
  --target-chat-title "MY_APP planning" --target-chat-url manual \
  --expected-marker HELLO_LOOP --idempotency-key my-app-001
arthur status                                   # -> POLL_DUE, one job ready

# 2. claim it (takes the advisor lease), hand the prompt to your advisor, mark it submitted
arthur queue claim --job-id BQ-MY_APP-001
cp adapters/advisor/prompts/next-plan-request.md queue/manual/pending/BQ-MY_APP-001.md   # fill in the placeholders
arthur queue submit --job-id BQ-MY_APP-001      # releases the lease; first poll due in 1 minute

# 3. the advisor (you) answers in queue/manual/done/BQ-MY_APP-001.md, ending with a control block:
#    ```text
#    PROJECT_ID: MY_APP
#    REVIEW_TYPE: NEXT_PLAN_REQUEST
#    APPROVAL_DECISION: REQUEST_CODEX_PLAN
#    HAS_P0_P1: false
#    IDEMPOTENCY_KEY: my-app-001
#    ```

# 4. save the reply BEFORE acting on it, then close the job
arthur capture --project-id MY_APP --job-id BQ-MY_APP-001 --kind next-plan-request \
  --source-chat-title manual --source-file queue/manual/done/BQ-MY_APP-001.md
arthur queue poll-result --job-id BQ-MY_APP-001 --marker-found true --status completed
arthur status                                   # -> WAIT; the artifact is indexed under projects/MY_APP/artifacts/chatgpt/
```

`capture` exits `0` when the control block validates, `3` when the reply was saved but quarantined or asked for a human (a decision is opened and the project pauses — see `arthur decision list`), and `2` when nothing was saved. Run the same `queue create` again and it is refused on both the job id and the idempotency key. That is the whole advisor contract; swap in a real advisor when you're ready.

### 2. Let your coding agent set up the real thing

`arthur init` **detects the agent CLIs on your machine** (see `arthur agents`), asks which one is your **main agent** and which **loop preset** you want:

| Preset | Shape | Needs |
| --- | --- | --- |
| `guided` (default) | Wizard writes a working base with the `manual` adapters; your main agent interviews you and finishes the setup | any agent, or none |
| `solo` | One agent both plans and implements | an agent with shipped packs: Codex, Claude Code, or Grok Build |
| `pair` | Main agent implements, a second agent reviews | shipped packs for both agents |
| `browser-advisor` | A browser AI (e.g. ChatGPT Pro) plans/reviews, your main agent implements | any agent (executor is `manual` without a pack) |
| `custom` | Pick adapters yourself | — |

Shipped adapter packs exist for **Codex**, **Claude Code**, and **Grok Build** (both roles) plus `chatgpt-browser` and `manual`. Gemini, Goose, and Cursor are *detected* so integrations can seed skills/MCP; they have no advisor/executor pack: `solo`/`pair` refuse them with an explanation instead of silently wiring `manual`, and `guided` says plainly that both roles start as `manual` until your agent authors an adapter ([docs/adapters.md](docs/adapters.md)).

`arthur init` also writes skills and slash commands **where those clients actually load them** and registers `arthur mcp serve` in `.mcp.json` / `.codex/config.toml` / `.cursor/mcp.json` / `.grok/config.toml`. A written MCP entry is **written**, not connected — restart the client. Use `--no-integrations` to skip that step. Details: [docs/integrations.md](docs/integrations.md), [docs/mcp.md](docs/mcp.md).

```bash
mkdir ~/my-loop && cd ~/my-loop
arthur init                  # interactive; needs a terminal
```

The wizard leaves `agent-setup/KICKOFF.md` for your main agent. Hand it over there; the agent interviews you, wires the adapters, and creates your projects. Without a terminal (cron, CI, piped stdin) `arthur init` fails fast and tells you to pass `--yes`; with `--yes` the defaults are: main agent = first detected CLI, preset `guided` (or `custom` when no agent is found), tracker `none`, governor on only when `codexbar` is installed, heartbeat on. Every question is also a flag — see `arthur init --help`.

### 3. Or just look at a busy dashboard

```bash
arthur init --yes --demo     # sample projects, a job, sessions, a decision, and a quota bar fed by usage/demo-quota.json
arthur status
```

`--demo` turns the quota governor on and points it at a JSON file inside the instance, so the bar you see is real and `arthur usage snapshot --snapshot-id x` works too.

## The status dashboard

`arthur status` is the loop's cockpit. Agent sessions self-report what they're doing:

```bash
arthur status set --session-id my-app-loop --role project-loop \
  --project-id MY_APP --state working --activity "revising the sprint plan"
arthur status clear --session-id my-app-loop
```

Sessions that stop reporting go dim with a `(stale)` marker after an hour — a stale `working` row is exactly how you spot a session that died mid-task. `arthur status --json` prints the entire snapshot machine-readable, which is the integration point for Discord bots, desktop notifiers, or anything else that should know when the loop needs you.

`--root DIR` works the same before or after any subcommand (`arthur --root ~/loop status set …`, `arthur status --root ~/loop set …`, `arthur status set … --root ~/loop`), and the dashboard refuses to render for a directory that isn't an instance instead of showing a calm, fake `WAIT`.

## Human decisions

When the loop needs a person, that is a file, not a chat message. `human-decisions/open.md` holds one `## PROJECT_ID title` section per decision; while a section says `Status: OPEN`, the tick reports `HUMAN_INPUT_REQUIRED` for that project and hands out none of its jobs. The loop opens decisions itself when an artifact is quarantined or says `HUMAN_INPUT_REQUIRED`; agents and humans open and close them with the CLI:

```bash
arthur decision open --project-id MY_APP --title "Session lifetime?" --body "24 hours or 7 days?"
arthur decision list                     # what is waiting on you
arthur decision answer --title "MY_APP Session lifetime?" --answer "7 days"
arthur decision clear  --title "MY_APP Session lifetime?" --note "asked twice"   # withdraw without answering
```

`open` also runs your tracker's `open_decision` template when one is configured. The web console's "answer" action is the same code path.

## The web console

<p align="center">
  <img src="docs/assets/web-console.png" alt="Arthur Loop web console showing the advisor-to-human status map, the + Loop wizard control, queued work, and an open auth-scope decision" width="100%">
</p>

`arthur web` serves a local, single-operator control surface for one instance — the browser twin of `arthur status`, plus the handful of write actions that genuinely belong to a human. The signature view is a **status map** of the fixed pipeline (advisor → queue → in flight → executor → review gate → human): counts and flow move over those nodes, and the human-decision node is lit the loudest. Drag to pan, scroll to zoom. It is not a graph composer. Alongside it: a kanban board, the dense queue table, an artifact reader, an activity timeline, and an always-on **Needs you** rail where you answer decisions, recover stale jobs, inspect quarantined artifacts, and clear sessions.

```bash
arthur web                 # serve this instance, open the browser
arthur web --root ~/loop --port 8080 --no-open
```

Vanilla JS over a stdlib server — no build step, no framework, no npm; it runs on a machine that only has Python. Localhost-only; `arthur web` prints a URL carrying a per-process session token, and every read and write over the API requires that token, so another local user cannot read your loop or push its buttons. It exposes six human actions (answer a decision, recover a job, **create a loop** via a wizard, create a single job, clear a session, break a stale browser lock) and deliberately withholds the agent-owned ones — no claim/submit/poll buttons, no "approve plan" bypass. The **+ Loop** wizard creates a project and the first queue job. It is not a drag-drop graph composer; the canvas is a status map of the fixed pipeline. Agents own claim/capture/gate through MCP or the CLI. Full details in [docs/web-console.md](docs/web-console.md).

## The menu bar app (macOS)

<p align="center">
  <img src="assets/arthurbar-demo.png" alt="ArthurBar — menu bar popover showing workers, queue, projects, decisions, and quota" width="360">
</p>

[ArthurBar](menubar/ArthurBar/) puts the loop in your menu bar: an `∞` icon with a badge counting the things that need a human, and a one-click card with workers, queue, projects, open decisions, the quota bar, and the browser lock. Native SwiftUI, styled after [CodexBar](https://github.com/steipete/CodexBar), fed by `arthur status --json`, read-only by design.

```bash
cd menubar/ArthurBar && swift build -c release
.build/release/ArthurBar --root ~/my-loop
```

## Desktop notifications

And the loop can come find *you*: `arthur watch` polls the tick and fires **desktop notifications** the moment a human decision opens, quota blocks, work goes due, or a job goes stale — never on repeats. It remembers its last observation in `runtime/watch-state.json`, so `--once` from cron only speaks when something changed. Run it in a spare pane for a live event tray, from cron with `--once --quiet`, or script your own alerts with `arthur notify --message "..."`:

```bash
arthur watch                        # ping me when the loop needs a human
arthur watch --once --quiet         # cron: prints only new events (empty output = nothing new)
arthur notify --message "sprint 2 approved"
```

Notifiers: macOS uses `osascript`; Linux needs `notify-send` (libnotify — `apt install libnotify-bin` or your distro's equivalent). Without one, `watch` says so once and prints events instead, and `arthur notify` exits `2` with the same explanation (`--dry-run` included). Windows and headless servers have no desktop notifier; use `--no-desktop` and pipe the output wherever you want it.

## Pick your pieces

`arthur init` asks; every answer is also a flag.

| Piece | Options |
| --- | --- |
| Advisor (plans, reviews, approves) | `chatgpt-browser` · `claude-code` · `codex` · `grok` · `manual` |
| Executor (implements) | `codex` · `claude-code` · `grok` · `manual` |
| Tracker (tickets) | `atlas-tasker` · `command` (bring your own CLI) · `none` |
| Quota governor | on/off — sources: [codexbar](https://github.com/steipete/CodexBar) (auto-detected) · your own `command` · a JSON `file` |
| Heartbeat state files | on/off |

**Trackers.** For [Atlas Tasker](https://github.com/myrrazor/atlas-tasker) the primary path is the board: `arthur tracker board --json` / MCP `arthur.board` reads `tracker board --json`, and `arthur tracker open-jobs` opens queue jobs from ready/assigned tickets (`atlas:<ticket_id>` idempotency keys). The three argv templates (`open_decision`, `close_decision`, `sprint_gate`) remain as a fallback — rendered to argv, never a shell, run from the instance root. Gate logic still lives in the artifact store, not the tracker. `arthur decision open` still calls `open_decision`. Any other CLI tracker works with your own templates. Or pick `none` and decisions live in `human-decisions/open.md` alone.

**Advisors.** Each adapter is a runbook plus a prompt pack, not code — the shipped packs share the same prompts, only the transport differs. `chatgpt-browser` drives a logged-in ChatGPT Pro conversation through the browser UI via your own agent; it is the original transport and fragile by nature (UIs change; check the terms of any service you automate). `codex`, `claude-code`, and `grok` run the advisor headlessly when the CLI supports it (`codex exec` / `claude -p` / `grok --always-approve -p`). `manual` is a human and two folders. The core never depends on browser automation.

**Quota.** `auto` uses CodexBar when it is installed and otherwise stays out of the way. `command` accepts any executable that prints CodexBar-shaped JSON; `file` reads the same schema from disk (relative paths resolve against the instance root); `none` disables collection. The core never requires CodexBar.

## Create a loop (terminal, web, or slash)

```bash
arthur loop create --project-id MY_APP --advisor grok --executor claude-code --tracker atlas-tasker
arthur web          # + Loop wizard — project + first job, not a graph composer
```

Inside Claude Code, `/arthur-loop` (or the installed skill) interviews for roles and calls MCP `arthur.loop.create`. Agents then follow claim → capture → gate themselves.

## The loop, end to end

1. The advisor returns a next-plan instruction (`arthur capture --kind next-plan-request`, control block `REQUEST_CODEX_PLAN`).
2. The executor produces a plan (`--kind plan`). Plan only: a plan whose control block admits `IMPLEMENTATION_STARTED: true` is quarantined.
3. The advisor approves (`--kind plan-review`, `APPROVE_PLAN`) or sends it back (`REVISE_PLAN`).
4. `arthur gate implementation --project-id X` says GO. The executor implements exactly one approved packet, with tests and evidence (`--kind implementation-handoff`); a handoff captured while the gate is NO-GO is quarantined.
5. The advisor reviews the handoff (`--kind sprint-review`); repeat until `RELEASE_READY` — a `HUMAN_INPUT_REQUIRED` or an invalid control block at any step opens a decision that pauses that project and surfaces on the dashboard.

Every hop is saved to the artifact store first, every decision is validated against an enum, and `arthur tick` tells you where things stand after a crash, a nap, or a quota pause. The full operating manual is [docs/workflow-runbook.md](docs/workflow-runbook.md).

## Adapters

The contract lives in [docs/adapters.md](docs/adapters.md); shipped adapters live in [`src/arthur_loop/adapters/`](src/arthur_loop/adapters/), each with an `ADAPTER.md` runbook and its prompt pack. `arthur init` copies your chosen packs into the instance so you can tune wording without touching the package. Adding an adapter is a docs-and-prompts contribution — if it needs new core code, it's probably not an adapter.

## How it stays honest

- Advisor and executor text is untrusted input: control blocks parse from the final fenced block only, values must match the allowed vocabulary exactly, the block must belong to the hop and project it was captured as, and anything that fails is saved with `control_block_valid: false` *and* opens a human decision that pauses the project. The scheduler cannot ignore it.
- The queue is a state machine, not a log of claims: `arthur queue` refuses transitions the loop never lived, and concurrent CLI runs on the same instance are serialized with a POSIX file lock so two of them cannot both pass a uniqueness check.
- One browser, one lock: `claim`/`submit`/`poll-result` serialize advisor access through a lease file with stale takeover. A refused `claim` never leaves a lease behind; `arthur queue recover` frees the lease of a manager that died after claiming; `arthur lock break --force` is the human's last resort.
- Idempotency keys are enforced at `queue create` — a retried create cannot fork the queue — and ride every prompt so recovery can look for an existing response before resending.
- Quota reserve: at or below your reserve percent, the loop checkpoints and reports instead of starting new work.

What it does **not** do: it cannot stop an executor process from editing files. It makes the executor's output worthless to the loop unless the gate was open, and it opens a human decision when that happens. Treat the gate as a check you run (and require your agents to run) before implementation starts.

## FAQ

**Where does the data live? Does it phone home?** Queue state, artifacts, decisions, and status are ordinary markdown and JSONL under the instance directory you choose. The core does not phone home or require an API key; adapters only call the tools you configure.

**Is it free?** Yes. Arthur Loop is MIT licensed and has no paid tier.

**Does the core call any AI APIs?** No. The core is files and a CLI. Adapters decide how prompts reach an advisor/executor — including entirely manual.

**Does it automate ChatGPT?** Only if you choose the `chatgpt-browser` adapter, and then only via your own agent driving your own logged-in browser. Review the terms of the services you automate; the `manual`, `codex`, and `claude-code` adapters are first-class alternatives. (Some identifiers keep their historical ChatGPT names for compatibility — the `waiting_for_chatgpt` status, the `projects/<ID>/artifacts/chatgpt/` directory, `READY_FOR_CHATGPT_REVIEW` — regardless of which advisor you run.)

**Which platforms work?** The Python CLI is tested on Linux and macOS (CI runs both). Known limitations: the cross-process file locks use POSIX `flock` and degrade to no locking on Windows, where nothing is tested; desktop notifications need `osascript` (macOS) or `notify-send` (Linux) and are otherwise reported as unsupported; ArthurBar is macOS 14+ only and has no Linux or Windows equivalent — use `arthur watch` or `arthur status --json` there; Codex, Claude Code, and Grok Build have shipped adapter packs; Gemini, Goose, and Cursor start as `manual` for those roles (Cursor still gets skills + MCP).

**How do I update or uninstall it?** Repeat your `pipx install --force`, `uv tool install --force`, or curl command to update. For the curl install, remove `~/.arthur-loop` and `~/.local/bin/arthur` to uninstall; pipx and uv have their usual `uninstall arthur-loop` commands.

**Why "Arthur"?** A round table of agents, one loop, and nobody implements without the crown's approval.

## Contributing

Issues and adapter contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the local test commands. Pull requests target `main`.

## License

MIT — see [LICENSE](LICENSE). Release history lives in [CHANGELOG.md](CHANGELOG.md).

---

Maintained at [GitHub](https://github.com/myrrazor/arthur-loop). If Arthur Loop is useful, a ⭐ helps others find it.
