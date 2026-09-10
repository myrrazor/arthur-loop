# Adapter Contract

Arthur Loop's core is transport-agnostic: a durable job queue, an artifact store with control-block validation, a tick classifier, a status dashboard, a browser/advisor lock, and optional quota + tracker hooks. Adapters supply the transport — and an adapter is a runbook plus a prompt pack, not code.

## Advisor adapter

Consumes: a rendered prompt (markdown) for a queue job.
Produces: a saved artifact (`arthur capture --kind next-plan-request|plan-review|sprint-review`) whose text ends in a control block (see `src/arthur_loop/seed/skill/references/control-blocks.md`).
Must: respect the lock, respect the polling cadence, save the raw response before acting on it, and stop the project when `capture` exits `3` (the artifact was quarantined or asked for a human; a decision is already open).

Shipped: `chatgpt-browser` (the original transport — a user-controlled browser session, fragile by nature because UIs change), `codex` and `claude-code` (headless, synchronous: `codex exec` / `claude -p`), `manual` (a human and two folders — also the quickest way to feel the loop). All four ship the same three prompts; only the runbook differs.

## Executor adapter

Consumes: an approved packet (plan-only or implementation handoff).
Produces: a plan (`--kind plan`) or implementation-handoff (`--kind implementation-handoff`) artifact ending in a control block.
Must: run `arthur gate implementation --project-id <ID>` and proceed only on GO; one packet per run; tests and evidence before the handoff.

What the core enforces regardless of the runbook: a plan whose block says `IMPLEMENTATION_STARTED: true` is quarantined, and an implementation handoff captured while the gate is NO-GO is quarantined. Both open a human decision that pauses the project.

Shipped: `codex`, `claude-code`, `manual` (all with `prompts/plan-only.md` and `prompts/implementation-handoff.md`).

Not shipped: packs for Gemini, Grok, or Goose. `arthur agents` detects those CLIs so `arthur init` can seed their instruction files, but the wizard wires `manual` for both roles and says so; `--preset solo`/`pair` refuse them.

## Tracker adapter

Honest scope: a tracker adapter is three command templates, rendered to argv (never a shell) and run from the **instance root** — not an MCP server, not a webhook, and not where gate logic lives. Operations: `open_decision`, `close_decision`, `sprint_gate`. Placeholders: `{project}`, `{title}`, `{reason}`, `{ticket_id}`.

- `atlas-tasker` — presets for [Atlas Tasker](https://github.com/myrrazor/atlas-tasker)'s `tracker` CLI (`tracker ticket create` / `tracker ticket move`). Verify the flags against your installed release.
- `command` — you supply the templates in config; any ticket tool with a CLI works:

      "tracker": {
        "adapter": "command",
        "command_templates": {
          "open_decision": "mytool add --project {project} --name {title}"
        }
      }

- `none` — tracker sync disabled; decisions live in `human-decisions/open.md` alone.

Who calls what: `arthur decision open` runs `open_decision` automatically (skip with `--no-tracker`); `close_decision` and `sprint_gate` are for your agents to call through `arthur tracker <action> --value key=value`. Arthur Loop does not parse tracker output or store ticket ids — pass `{ticket_id}` yourself.

Try a template without running it: `arthur tracker open_decision --value project=X --value "title=Pick one" --dry-run`.

## Adding an adapter

Copy the closest existing ADAPTER.md, keep the control-block contract identical, and put prompt wording in a `prompts/` directory next to it (`tests/test_init.py` checks that every advertised adapter ships its prompts). If it needs new core code — and it shouldn't — open an issue first. Adapters live inside the package (`src/arthur_loop/adapters/`) so `arthur init` can copy your pack into any instance.
