---
name: arthur-loop
description: Assign agents to planner, implementer, reviewer, and QA roles, then run the next hop. Use when coordinating Arthur Loop workflows with durable file state.
---

# Arthur Loop

Use Arthur Loop to assign agents to roles (planner, implementer, reviewer, QA) and run one hop at a time. Durable state stays in files.

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
Cursor: `.cursor/skills/arthur-loop` and `.cursor/commands/arthur-loop.md`, Grok:
`.grok/skills/arthur-loop` — the path Grok Build actually scans — plus `AGENTS.md`).
MCP is registered in that client's real config. Grok install runs
`grok mcp add --scope project arthur-loop -- arthur mcp serve --tool-name-style portable`
when `grok` is on PATH so the server is trusted (prove with `grok --trust` / `grok mcp list` /
`grok --always-approve -p`). Untrusted folder = project MCP does not spawn.
Other clients need a restart before tools appear.

Prefer MCP tools over inventing shell. Dotted names (`arthur.status`, `arthur.loop.create`,
`arthur.follow.run`, `arthur.queue.claim`, `arthur.capture`, `arthur.gate.implementation`,
`arthur.board`, `arthur.tracker.next`) are canonical. Grok registrations use portable names
(`arthur_status`, `arthur_loop_create`, `arthur_follow_run`).

`/arthur-loop` interviews for planner / implementer / reviewer / QA (optional model
per role) and tracker, then calls `arthur.loop.create`. Arthur formulates the default
hop sequence from the project or ticket. Then call `arthur.run` / `arthur` / `arthur.follow.run`
so the next assigned agent is invoked. It is not a drag-drop graph composer.

If the tracker is Atlas Tasker, walk ready work with `arthur.tracker.next` /
`arthur tracker next --json` / `arthur tracker walk`. `open-jobs` only opens
ready/in_progress (not `in_review`). Map Arthur `project_id` to an Atlas key with
`tracker.project_map` (init / `arthur loop create` write `DEMO_APP` → `DEMO`; do not walk an empty map). The old three argv templates remain for decision/sprint hooks.

## Commands

From the instance root (or with `--root DIR` anywhere on the line):

```bash
arthur status
arthur tick --dry-run
arthur roles set reviewer=claude-code:opus qa=grok
arthur loop create --project-id PROJECT --role reviewer=claude-code:opus --from-ticket AUTH-2
arthur
arthur run
arthur follow --once
arthur integrations install --targets claude,codex,cursor,grok
arthur integrations probe --target grok
arthur mcp serve --tool-name-style portable
arthur tracker next --json
arthur tracker walk --dry-run
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

Capture kinds: `next-plan-request`, `plan`, `plan-review`, `implementation-handoff`, `qa-review`, `sprint-review`, `human-decision`.
