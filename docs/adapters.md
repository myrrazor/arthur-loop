# Adapter Contract

Arthur Loop's core is transport-agnostic: a durable job queue, an artifact store with control-block validation, a tick classifier, a status dashboard, a browser/advisor lock, optional quota, and tracker hooks. Adapters supply the transport — and an adapter is a runbook plus a prompt pack, not code.

## Advisor adapter

Consumes: a rendered prompt (markdown) for a queue job.
Produces: a saved artifact (`arthur capture --kind next-plan-request|plan-review|sprint-review`) whose text ends in a control block (see `src/arthur_loop/seed/skill/references/control-blocks.md`).
Must: respect the lock, respect the polling cadence, save the raw response before acting on it, and stop the project when `capture` exits `3` (the artifact was quarantined or asked for a human; a decision is already open).

Shipped: `chatgpt-browser` (the original transport — a user-controlled browser session, fragile by nature because UIs change), `codex`, `claude-code`, and `grok` (headless when the CLI supports it: `codex exec` / `claude -p` / `grok --always-approve -p`), `manual` (a human and two folders — also the quickest way to feel the loop). All five ship the same three prompts; only the runbook differs.

## Executor adapter

Consumes: an approved packet (plan-only or implementation handoff).
Produces: a plan (`--kind plan`) or implementation-handoff (`--kind implementation-handoff`) artifact ending in a control block.
Must: run `arthur gate implementation --project-id <ID>` and proceed only on GO; one packet per run; tests and evidence before the handoff.

What the core enforces regardless of the runbook: a plan whose block says `IMPLEMENTATION_STARTED: true` is quarantined, and an implementation handoff captured while the gate is NO-GO is quarantined. Both open a human decision that pauses the project.

Shipped: `codex`, `claude-code`, `grok`, `manual` (all with `prompts/plan-only.md` and `prompts/implementation-handoff.md`).

Not shipped: packs for Gemini, Goose, or Cursor. `arthur agents` detects those CLIs so `arthur init` can seed instruction files and integrations; the wizard wires `manual` for both roles and says so; `--preset solo`/`pair` refuse them. Cursor is a first-class **integration** target (skills + MCP), not an advisor/executor pack — there is no honest headless `cursor exec` in this release.

## Tracker adapter

Two paths, in this order:

1. **Board read (primary, Atlas Tasker).** `arthur tracker board` and MCP `arthur.board` run `tracker board --json` from the instance root and flatten `columns`. `arthur tracker open-jobs` / `arthur.board.open_jobs` create queue jobs for ready/assigned (`ready`, `in_progress`) tickets. Idempotency key is `atlas:<ticket_id>`, so a second pass will not fork the queue. Requires the `tracker` binary on PATH.
2. **Argv templates (fallback).** Three command templates, rendered to argv (never a shell) and run from the instance root: `open_decision`, `close_decision`, `sprint_gate`. Placeholders: `{project}`, `{title}`, `{reason}`, `{ticket_id}`. This is not an MCP server and not where gate logic lives.

- `atlas-tasker` — board JSON when `tracker` is installed; argv presets for [Atlas Tasker](https://github.com/myrrazor/atlas-tasker) ticket create/move remain for decision hooks. Verify flags against your installed release.
- `command` — you supply the templates in config; any ticket tool with a CLI works.
- `none` — tracker sync disabled; decisions live in `human-decisions/open.md` alone.

Who calls what: `arthur decision open` still runs the `open_decision` template (skip with `--no-tracker`). Agents should prefer `arthur tracker board` / `open-jobs` for work intake.

Try a template without running it: `arthur tracker open_decision --value project=X --value "title=Pick one" --dry-run`.

## Agent integrations (skills + MCP)

`arthur integrations install` writes skills where each client loads them and registers `arthur mcp serve` in that client's real config. It does not claim the client is connected. See [integrations.md](integrations.md) and [mcp.md](mcp.md).

## Adding an adapter

Copy the closest existing ADAPTER.md, keep the control-block contract identical, and put prompt wording in a `prompts/` directory next to it (`tests/test_init.py` checks that every advertised adapter ships its prompts). If it needs new core code — and it shouldn't — open an issue first. Adapters live inside the package (`src/arthur_loop/adapters/`) so `arthur init` can copy your pack into any instance.
