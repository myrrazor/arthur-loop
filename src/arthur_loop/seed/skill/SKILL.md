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
- Keep advisor/browser access serialized through the queue manager and its lock.
- Treat advisor conversation history and executor chat memory as recoverable context, not source of truth.
- Store durable state in queue ledgers, project state files, human-decision files, your tracker, and artifacts.
- Save advisor-produced text as a project artifact (`arthur capture`) before handing it to the executor.
- Never act on an artifact with `control_block_valid: false` — treat it as human input required.
- Stop before implementation if the advisor or executor returns human input required.
- Keep the configured quota reserve; at or below reserve, only checkpoint and report.

## Commands

From the instance root:

```bash
arthur status
arthur tick --dry-run
arthur queue due
arthur queue create --job-id JOB --project-id PROJECT --target-chat-title "Conversation" --target-chat-url URL --prompt-path prompt.md
arthur capture --project-id PROJECT --job-id JOB --kind next-plan-request --source-chat-title "Conversation" --source-file response.md
arthur usage snapshot --snapshot-id before-task
arthur usage task --task-id task --project-id PROJECT --role "Master Orchestrator" --task-label "Task label" --before before-task --after after-task
arthur usage dashboard
```
