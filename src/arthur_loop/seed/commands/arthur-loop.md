---
name: arthur-loop
description: Create or run an Arthur Loop — assign agents to roles, then run the next hop
---

You are operating an Arthur Loop instance. There is no drag-and-drop graph composer.

## What to do

1. Read the installed `arthur-loop` skill if you have not already.
2. Run `arthur status` or call MCP `arthur.status` (`arthur_status` on Grok).
3. Interview the human, one topic at a time:
   - Project id (`SHOUTY_SNAKE`), one-line goal, optional ticket id
   - **Planner** agent (and optional model): `chatgpt-browser`, `claude-code`, `codex`, `grok`, or `manual`
   - **Implementer** agent (and optional model): `claude-code`, `codex`, `grok`, or `manual`
   - **Reviewer** agent (and optional model): `chatgpt-browser`, `claude-code`, `codex`, `grok`, or `manual`
   - **QA** agent (optional): same list, or `none` to skip QA
   - **Tracker**: `atlas-tasker`, `command`, or `none`
4. Create the loop with MCP `arthur.loop.create` or:

   ```bash
   arthur loop create --project-id PROJECT \
     --role planner=grok --role implementer=claude-code \
     --role reviewer=claude-code:opus --from-ticket AUTH-2
   ```

   Arthur formulates the default hop sequence and hands each hop to the assigned role.

5. Run the next agent — do not wait for a human to type every CLI hop:
   - `arthur` or `arthur run` (terminal)
   - MCP `arthur.run` / `arthur.follow.run`
   - Web console: **Run next**

## Hard rules

- Never hand-edit `queue/*.jsonl`.
- Never claim a job on a project paused by an open human decision.
- Save untrusted output with `arthur capture` before acting on it. Exit 3 means stop that project.
- A written MCP config is **written**, not connected — the human may need to restart this client.
