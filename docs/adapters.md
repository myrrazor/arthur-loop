# Adapter Contract

Arthur Loop's core is transport-agnostic: a durable job queue, an artifact store with control-block validation, a tick classifier, a status dashboard, a browser/advisor lock, and optional quota + tracker hooks. Adapters supply the transport — and an adapter is a runbook plus a prompt pack, not code.

## Advisor adapter

Consumes: a rendered prompt (markdown) for a queue job.
Produces: a saved artifact (`arthur capture`) whose text ends in a control block (see `src/arthur_loop/seed/skill/references/control-blocks.md`).
Must: respect the lock, respect the polling cadence, save the raw response before acting on it, and never treat a decision as trusted when the artifact says `control_block_valid: false`.

Shipped: `chatgpt-browser` (the flagship, battle-tested, fragile-by-nature), `claude-code` (headless, synchronous), `manual` (a human and two folders — also the quickest way to feel the loop).

## Executor adapter

Consumes: an approved packet (plan-only or implementation handoff).
Produces: a plan or implementation-handoff artifact ending in a control block.
Must: refuse implementation from a plan-only packet; one packet per run; tests and evidence before the handoff.

Shipped: `codex`, `claude-code`, `manual`.

## Tracker adapter

Three operations, each a command template rendered to argv (never a shell): `open_decision`, `close_decision`, `sprint_gate`. Placeholders: `{project}`, `{title}`, `{reason}`, `{ticket_id}`.

- `atlas-tasker` — presets for [Atlas Tasker](https://github.com/myrrazor/atlas-tasker)'s `tracker` CLI.
- `command` — you supply the templates in config; any ticket tool with a CLI works:

      "tracker": {
        "adapter": "command",
        "command_templates": {
          "open_decision": "mytool add --project {project} --name {title}"
        }
      }

- `none` — tracker sync disabled; decisions live in `human-decisions/open.md` alone.

Try a template without running it: `arthur tracker open_decision --value project=X --value "title=Pick one" --dry-run`.

## Adding an adapter

Copy the closest existing ADAPTER.md, keep the control-block contract identical, and put prompt wording in a `prompts/` directory next to it. If it needs new core code — and it shouldn't — open an issue first. Adapters live inside the package (`src/arthur_loop/adapters/`) so `arthur init` can copy your pack into any instance.
