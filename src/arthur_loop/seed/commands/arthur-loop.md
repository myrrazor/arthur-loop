---
name: arthur-loop
description: Create or run an Arthur Loop — pick advisor, executor, tracker, and the next hop
---

You are operating an Arthur Loop instance. There is no drag-and-drop graph composer.

## What to do

1. Read `agent-setup/skill/SKILL.md` or the installed `arthur-loop` skill if you have not already.
2. Run `arthur status` or call MCP `arthur.status` (`arthur_status` on Grok).
3. Interview the human, one topic at a time:
   - Project id (`SHOUTY_SNAKE`) and one-line goal
   - **Advisor** (plans/reviews): `chatgpt-browser`, `claude-code`, `codex`, `grok`, or `manual`
   - **Executor** (implements): `claude-code`, `codex`, `grok`, or `manual`
   - **Tracker**: `atlas-tasker` (board-aware when `tracker` is installed), `command`, or `none`
4. Create the loop with MCP `arthur.loop.create` or:

   ```bash
   arthur loop create --project-id PROJECT --advisor ADVISOR --executor EXECUTOR --tracker TRACKER
   ```

5. Follow the loop — do not wait for a human to type every CLI hop:
   - MCP `arthur.follow.run` / `arthur follow --once`
   - or claim → submit/capture → `arthur.gate.implementation` yourself
   - `arthur.decision.open` when a human is required

If Atlas Tasker is the tracker and `tracker` is on PATH, walk ready work with
`arthur.tracker.next` / `arthur tracker walk` (not just `board --json`). `in_review`
is not ready work. Pass Atlas `--project` keys via `tracker.project_map`
(`arthur loop create` writes `MY_APP` → `MY`; do not walk an empty map).

## Hard rules

- Never hand-edit `queue/*.jsonl`.
- Never claim a job on a project paused by an open human decision.
- Save untrusted output with `arthur capture` before acting on it. Exit 3 means stop that project.
- A written MCP config is **written**, not connected — the human may need to restart this client.
