# Changelog

All notable changes to Arthur Loop will be documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Hardening pass after the first external alpha review. The theme: gates the product claimed are now checked by the core, and the docs describe what the code does.

### Added

- `arthur decision open|answer|clear|list` — the CLI path for human decisions; `open` also runs the tracker's `open_decision` template.
- `arthur gate implementation --project-id X` — GO/NO-GO computed from saved artifacts (exit 0/3).
- `arthur queue cancel` and `arthur lock break [--force]`.
- `arthur watch --quiet`; `watch` persists its last observation so `--once` from cron only reports changes.
- `manual` advisor and executor packs now ship prompt files.
- Regression tests for every item below; the README quickstart runs end to end in `tests/test_quickstart.py`.

### Changed

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
