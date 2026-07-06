---
name: arthur-loop
description: Browser-mediated Codex and ChatGPT Pro project orchestration. Use when coordinating Arthur Loop workflows, including ChatGPT browser review loops, Codex coding-session handoffs, durable browser queue jobs, human-decision escalation, quota/usage snapshots, polling cadence, and placeholder prompt templates for multi-project agent loops.
---

# Arthur Loop

Use Arthur Loop to coordinate one project sprint at a time through ChatGPT Pro in the browser and Codex coding sessions without using the OpenAI API.

## Workflow

1. Read `references/workflow.md` for the role map and state handoff.
2. Read `references/control-blocks.md` before creating or parsing browser/Codex messages.
3. Read `references/prompt-placeholders.md` when preparing scenario prompts.
4. Read `projects/<PROJECT_ID>/artifacts/chatgpt/index.md` before opening full ChatGPT response artifacts.
5. Run `scripts/arthur-tick.py --dry-run` at the start of orchestration turns.
6. Use the repo scripts for queue work, quota snapshots, and dashboards instead of estimating or editing JSONL manually.

## Hard Rules

- Do not use the OpenAI API for ChatGPT Pro review/research loops.
- Keep browser access serialized through the Browser Queue Manager.
- Treat ChatGPT browser history and Codex chat memory as recoverable context, not source of truth.
- Store durable state in queue ledgers, project state files, human-decision files, Atlas Tasker, and artifacts.
- Save browser-produced ChatGPT text as a project artifact before handing it to Codex.
- Stop before implementation if ChatGPT or Codex returns human input required.
- Keep a 5 percent Codex reserve; at or below reserve, only checkpoint and report.

## Scripts

From the Arthur Loop repo root:

```bash
PYTHONPATH=src scripts/record-usage-snapshot.py --snapshot-id before-task
PYTHONPATH=src scripts/record-task-usage.py --task-id task --project-id PROJECT --role "Master Orchestrator" --task-label "Task label" --before before-task --after after-task
PYTHONPATH=src scripts/render-usage-dashboard.py
PYTHONPATH=src scripts/render-queue-timing.py
PYTHONPATH=src scripts/arthur-tick.py --dry-run
PYTHONPATH=src scripts/queue-job.py due
PYTHONPATH=src scripts/capture-chatgpt-artifact.py --project-id PROJECT --job-id JOB --kind next-plan-request --source-chat-title "Chat title" --source-file response.md
```
