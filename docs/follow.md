# Auto-follow

`arthur follow` (and `arthur` / `arthur run`) drives a created loop so the next assigned role is invoked without typing claim / capture / gate by hand.

```bash
arthur roles set reviewer=claude-code:opus
arthur loop create --project-id DEMO --role planner=grok --role implementer=grok
arthur
arthur run
arthur follow --once
arthur follow --max-steps 12
arthur mcp call arthur.run --arguments '{"once":true}'
```

## What it does

Each step, when the hop's adapter has a CLI:

1. Claim the next queued job (refused if the project is paused).
2. Invoke the adapter (`grok --always-approve -p`, `claude -p`, `codex exec`). Grok Build 1.0.30 flag order is load-bearing.
3. Submit that the prompt was sent (also refused if the project is paused).
4. Capture the reply and complete the job.
5. Run the implementation gate.
6. Enqueue the next hop from a trusted control block (`REQUEST_CODEX_PLAN` → plan, and so on).

MCP: `arthur.follow.run` / portable `arthur_follow_run`.

## What stays human-gated (honest)

- An **open human decision** pauses that project. Answer it; follow will not skip it. Pause also refuses `queue poll-result`, `complete`, and `fail` (exit 2).
- **chatgpt-browser** and **manual** adapters have no headless transport. Follow claims, writes `runtime/follow/<job>.inbox.md`, and exits 3.
- **Implementation-gate NO-GO.** Follow will not invent `APPROVE_PLAN`.
- A missing or failing agent CLI (not on PATH, not logged in).
