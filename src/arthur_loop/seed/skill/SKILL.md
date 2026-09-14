---
name: arthur-loop
description: Advisor/executor project orchestration with durable file state. Use when coordinating Arthur Loop workflows, including advisor review loops (e.g. ChatGPT Pro in the browser), executor coding-session handoffs (e.g. Codex, Claude Code), durable queue jobs, human-decision escalation, quota/usage snapshots, polling cadence, and adapter prompt packs for multi-project agent loops.
---

# Arthur Loop

Use Arthur Loop to coordinate one project sprint at a time between an advisor (plans, reviews, approves) and an executor (implements), with all durable state in files.

## Workflow

1. Read `references/workflow.md` for the role map and state handoff.
2. Read `references/control-blocks.md` before creating or parsing advisor/executor messages.
3. Read `references/prompt-placeholders.md` when preparing scenario prompts.
4. Read `projects/<PROJECT_ID>/artifacts/chatgpt/index.md` before opening full advisor response artifacts.
5. Run `arthur tick --dry-run` and `arthur status` at the start of orchestration turns.
6. Report what you are doing with `arthur status set` when you pick up work; `arthur status clear` when you stop.
7. Use the `arthur` CLI for queue work, quota snapshots, and dashboards instead of estimating or editing JSONL manually.

## Hard Rules

- Follow your advisor adapter's transport rules (see `adapters/advisor/ADAPTER.md` in the instance); if it is a browser adapter, do not swap in an API silently.
- Keep advisor/browser access serialized through the queue manager and its lock. Move jobs only with `arthur queue`; the state machine refuses anything else.
- Treat advisor conversation history and executor chat memory as recoverable context, not source of truth.
- Store durable state in queue ledgers, project state files, human-decision files, your tracker, and artifacts.
- Save advisor/executor text as a project artifact (`arthur capture --kind <hop>`) before acting on it. Exit code `3` means it was quarantined or asked for a human: a decision is already open, stop that project.
- Never act on an artifact with `control_block_valid: false`.
- Run `arthur gate implementation --project-id PROJECT` before any implementation handoff; proceed only on GO. Handoffs captured while the gate is NO-GO are quarantined.
- Escalate with `arthur decision open`, never by editing `human-decisions/open.md` by hand.
- Keep the configured quota reserve; at or below reserve, only checkpoint and report.

## MCP and slash

After `arthur init` or `arthur integrations install`, this skill is written where the client
loads skills (Claude: `.claude/skills/arthur-loop`, Codex: `.codex/skills/arthur-loop`,
Cursor: `.cursor/skills/arthur-loop` and `.cursor/commands/arthur-loop.md`, Grok: `.arthur/integrations/grok-agent-skill` plus
`AGENTS.md`). MCP is registered in that client's real config (`.mcp.json`,
`.codex/config.toml`, `.cursor/mcp.json`, `.grok/config.toml`). A written entry is
**written**, not connected — restart the client.

Prefer MCP tools over inventing shell. Dotted names (`arthur.status`, `arthur.loop.create`,
`arthur.queue.claim`, `arthur.capture`, `arthur.gate.implementation`, `arthur.board`) are
canonical. Grok registrations use portable names (`arthur_status`, `arthur_loop_create`).

`/arthur-loop` (Claude project command, or this skill) interviews for advisor / executor /
tracker and calls `arthur.loop.create`. That creates a project and the first queue job.
It is not a drag-drop graph composer.

If the tracker is Atlas Tasker, read the board with `arthur.board` / `arthur tracker board --json`
and open jobs from ready/assigned tickets with `arthur.board.open_jobs`. The old three argv
templates remain as a fallback for `open_decision` / `close_decision` / `sprint_gate`.

## Commands

From the instance root (or with `--root DIR` anywhere on the line):

```bash
arthur status
arthur tick --dry-run
arthur loop create --project-id PROJECT --advisor grok --executor claude-code
arthur integrations install --targets claude,codex,cursor,grok
arthur mcp serve
arthur tracker board --json
arthur tracker open-jobs --dry-run
arthur queue due
arthur queue create --job-id JOB --project-id PROJECT --target-chat-title "Conversation" --target-chat-url URL --prompt-path prompt.md --idempotency-key KEY
arthur queue claim --job-id JOB && arthur queue submit --job-id JOB
arthur queue poll-result --job-id JOB --marker-found true --status completed
arthur queue recover --job-id JOB --requeue          # abandoned job: parks it and frees the dead manager's lease
arthur capture --project-id PROJECT --job-id JOB --kind next-plan-request --source-chat-title "Conversation" --source-file response.md
arthur gate implementation --project-id PROJECT      # exit 0 GO, 3 NO-GO
arthur decision open --project-id PROJECT --title "Question?" --body "Options and impact"
arthur decision list
arthur usage snapshot --snapshot-id before-task
arthur usage task --task-id task --project-id PROJECT --role "Master Orchestrator" --task-label "Task label" --before before-task --after after-task
arthur usage dashboard
```

Capture kinds: `next-plan-request`, `plan`, `plan-review`, `implementation-handoff`, `sprint-review`, `human-decision`.
