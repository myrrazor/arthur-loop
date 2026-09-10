# Executor: codex

A Codex coding session implements one approved packet at a time.

- Plan requests use `prompts/plan-only.md` — the PLAN ONLY fence is load-bearing; the session must not touch files.
- Implementation requests use `prompts/implementation-handoff.md` and are only ever sent after `arthur gate implementation --project-id <ID>` prints GO (the advisor approved the plan and nothing re-opened planning since).
- One sprint per handoff. The session runs tests, captures evidence, and ends with the CODEX_IMPLEMENTATION_HANDOFF control block.
- Save the handoff text as an artifact (`arthur capture`) before sending it back to the advisor for review.
