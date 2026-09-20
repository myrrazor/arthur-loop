# Changelog

All notable changes to Arthur Loop will be documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Product-gap pass after the hardening review: Arthur Loop now installs into coding agents the way Atlas Tasker does (skills where those clients load them, plus MCP registration), agents can create and drive a loop without a human typing every hop, Atlas boards are a first-class read path, and the web console has an honest create-loop wizard.

Hostile re-test of tip `aad65fa` was ALMOST. This revision closes Grok load, auto-follow, Atlas next/walk, install honesty, and the how-it-works video.

Hostile re-test of tip `33decbf` was ALMOST. This revision owns public-surface honesty and the remaining blockers: live-site overclaims (no fake v0.2.0, no MCP/follow on curl-main, arthurloop.com canonical instead of Vercel 404, install.ps1 shipped here and not linked on `/main`), `grok --trust` ENXIO-after-grant, probe multiline TOML, usable `tracker.project_map` after init/loop create, and pause blocking poll/complete/fail.

### Added

- Launch site, README, and docs cover ArthurBar again: quota in the menu bar, attention badge, and a **separate macOS installer** (`menubar/install.sh`). pipx/uv/CLI curl do not install it. Footer is a Star on GitHub button; the leftover v0.1.0 “file cockpit” line is gone.
- Assignable roles: planner, implementer, reviewer, QA. `arthur roles set reviewer=claude-code:opus`. Bare `arthur` and `arthur run` invoke the next assigned agent. Same **Run next** / **Roles** controls in `arthur web`. Default hop sequence is formulated from the project or ticket, with QA inserted only when assigned.
- Stdio MCP server (`arthur mcp serve`) with read tools (status, tick, queue, gate, decision, loop, board) and gated writes (queue, capture, decision, loop create, board open-jobs). Dotted names plus Grok-safe portable names (`arthur_status`). `/arthur-loop` prompt.
- `arthur integrations detect|install|status|probe` — writes skills, slash commands, and MCP config for Claude Code, Codex, Cursor, and Grok Build. Grok skill is `.grok/skills/arthur-loop`. `grok mcp add` when `grok` is on PATH (trusted, not just written toml).
- `arthur follow` / MCP `arthur.follow.run` — auto-follow: claim → invoke → submit → capture → gate → next hop.
- `arthur loop create|list` — project + first queue job. Shared by CLI, web wizard, and MCP. Not a graph composer.
- Atlas board adapter: `arthur tracker next` / `queue` / `walk` / `board` / `open-jobs`. Ready/in_progress only (`in_review` is not opened). Arthur `project_id` maps to an Atlas key via `tracker.project_map`.
- How-it-works video (`docs/assets/how-it-works.mp4`) and create-loop wizard shot on the launch site.
- [docs/install.md](docs/install.md) — version honesty and how to install this branch.
- Grok Build advisor and executor adapter packs.
- Web console **+ Loop** wizard and `POST /api/actions/create-loop`.
- Cursor as an integration target (skills + MCP). No advisor/executor pack — `solo`/`pair` still refuse it.
- Docs: [how-it-works](docs/how-it-works.md), [MCP](docs/mcp.md), [integrations](docs/integrations.md).
- `arthur decision open|answer|clear|list` — the CLI path for human decisions; `open` also runs the tracker's `open_decision` template.
- `arthur gate implementation --project-id X` — GO/NO-GO computed from saved artifacts (exit 0/3).
- `arthur queue cancel` and `arthur lock break [--force]`.
- `arthur watch --quiet`; `watch` persists its last observation so `--once` from cron only reports changes.
- `manual` advisor and executor packs now ship prompt files.
- Regression tests for the items below; the README quickstart runs end to end in `tests/test_quickstart.py`.

### Fixed

- `arthur capture --source-file` now uses the same instance-root path guard as MCP capture, so host files such as `/etc/passwd` are refused instead of being copied into project artifacts.
- `arthur mcp serve` answers `Content-Length: abc` / `Content-Length: -1` with JSON-RPC `-32700` and keeps serving; those frames used to raise and kill the stdio process.
- Empty or whitespace idempotency keys are refused (they used to bypass uniqueness).
- Duplicate `expected_marker` across different job ids is refused; a blank marker is treated as omitted.
- `queue claim` and `queue submit` (CLI and MCP) refuse a project paused by an open human decision.
- Grok follow/probe argv is `grok --always-approve -p PROMPT` (1.0.30). `-p --always-approve` does not run the prompt.
- Public README/site/FAQ no longer claim that the three main / v0.1.0 install commands produce MCP, `arthur follow`, or Atlas walk.
- Tests that write Grok integrations isolate `grok_binary` so a host `grok` on PATH cannot change assertions.
- `arthur init` always writes `tracker.project_map`. With no projects it is `{}` and init says so. `--project-id`, `--demo` + atlas-tasker, and `arthur loop create` write a usable default (`MY_APP` → `MY`).
- `arthur integrations install` documents Grok folder trust and runs `grok --trust` unless `--no-trust-folder` (untrusted folder = project MCP disconnected). A grant that lands then ENXIO on a missing TTY is success, not a failed trust. Probe parses multiline TOML `args`.
- `queue poll-result` / `complete` / `fail` (CLI and MCP poll) refuse a project paused by an open human decision.
- Launch site canonical / robots / sitemap / OG / JSON-LD use `https://arthurloop.com/`. Docs pages spell public main vs this branch. Experimental `install.ps1` ships on this branch; `/main/install.ps1` is documented as a 404.
- `arthur integrations install --force` no longer replaces the whole `AGENTS.md` (managed blocks only).
- Integration detect no longer treats `~/.cursor` / `~/.grok` as "found" without the binary.
- `arthur tracker` RuntimeError from a failed `tracker --json` is `error:` exit 2, not a traceback.
- `queue recover` of a queued/parked job no longer steals another manager's live lease via the default `--holder` hint.

### Changed

- `arthur init` writes client integrations by default (`--no-integrations` skips).
- Web console: six human actions (the wizard is the sixth). Canvas copy says status map, not graph composer.
- The queue is a state machine: illegal transitions (complete from `queued`, claim a finished job, revive a terminal job) are refused with exit 2.
- `queue create` refuses a reused `--idempotency-key`, not just a duplicate job id. Ledger and lease writes take a POSIX `flock` so concurrent CLI runs cannot both pass a uniqueness check.
- `queue claim` records `claimed_by`; `queue recover` releases the lease of the manager that claimed the job (crash after claim no longer blocks the loop until the TTL expires). A refused `claim`/`poll-result` never takes the lease.
- `arthur capture --kind` is a fixed vocabulary (`next-plan-request`, `plan`, `plan-review`, `implementation-handoff`, `sprint-review`, `human-decision`). A control block is invalid when its `REVIEW_TYPE` or `PROJECT_ID` does not match, its decision field is missing, a plan says `IMPLEMENTATION_STARTED: true`, or an implementation handoff arrives while the gate is NO-GO. Invalid or `HUMAN_INPUT_REQUIRED` artifacts open a human decision that pauses the project; `capture` exits 3.
- `--root` is a global option that means the same thing before or after any subcommand; `status set`/`clear` no longer silently write to the current directory.
- `arthur init` fails fast with a clear message when stdin is not a terminal and `--yes` was not given. `--preset solo`/`pair` refuse agents without shipped adapter packs instead of silently wiring `manual`; the wizard only offers viable presets. `--demo` turns the governor on and points it at `usage/demo-quota.json` so the demo quota bar is real.
- `arthur tracker` runs the command from the instance root, not the caller's cwd.
- `arthur notify` exits 2 with a platform-specific explanation when no desktop notifier exists (dry run included).
- `arthur status`, `tick`, `watch`, `web`, `decision`, `gate`, and `capture` refuse a directory that is not an instance instead of rendering a calm `WAIT`.
- Web console: `GET /` requires the session token in the URL `arthur web` prints, and every `/api/*` read requires it too; answer/recover/break-lock share the CLI code paths; the artifact index is advisor-neutral.

### Removed

- The `api-model` advisor option, which crashed `init` because no pack ships for it.

## [0.1.0] - 2026-07-16

### Added

- File-first queues, artifact capture, approval gates, human-decision escalation, and quota-aware scheduling.
- Adapter packs for browser, CLI, and manual advisors and executors.
- `arthur init` agent detection, loop presets, demo seeding, and the one-command installer.
- Local web console with a live loop canvas, queue tools, artifact reading, and human-only recovery actions.
- `arthur watch` and desktop notifications for decisions, quota blocks, due work, and stale jobs.
- Pluggable quota providers with CodexBar support kept optional.
- ArthurBar, a read-only native macOS menu bar view of loop status.

[Unreleased]: https://github.com/myrrazor/arthur-loop/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/myrrazor/arthur-loop/releases/tag/v0.1.0
