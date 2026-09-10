# Arthur Loop Workflow Reference

## Roles

- Master Orchestrator: prioritizes projects, checks quota, asks human decisions, and reports status.
- Resource Governor (optional): records quota snapshots, classifies reserve state, and blocks new work below reserve.
- Advisor Queue Manager: serializes advisor (browser) access, submits prompts, polls, captures artifacts, and updates queue state.
- Project Loop Manager: owns one project state, tracker mapping, sprint gates, and handoffs.
- Executor Session (Codex, Claude Code, ...): plans or implements one approved packet, then stops for review.
- Advisor Session (ChatGPT Pro browser, Claude Code, a human, ...): handles research, high-level review, plan approval, and release/human-input decisions.

## Normal Sprint Loop

1. Snapshot quota before starting a project task (if the governor is enabled).
2. Recover the project state from durable artifacts and the executor session.
3. Submit a next-plan request through the queue manager (`arthur queue create … --idempotency-key`, `claim`, `submit`).
4. Poll once after one minute, then every five minutes while the advisor is thinking (`arthur queue poll-result`).
5. Save advisor output with `arthur capture --kind next-plan-request` (it lands under `projects/<PROJECT_ID>/artifacts/chatgpt/`).
6. Read/update the small `index.md`; future agents should consult it before full artifacts. Rows marked QUARANTINED are never acted on.
7. Send the plan-only prompt to the executor with the artifact path and a short excerpt; capture the plan with `--kind plan`.
8. Send the executor's plan back to the advisor for approval; capture the verdict with `--kind plan-review`.
9. Run `arthur gate implementation --project-id <ID>`. On GO, ask the executor to implement exactly one approved sprint/task.
10. Capture the handoff with `--kind implementation-handoff`, send it to the advisor for review, capture the review with `--kind sprint-review`.
11. Repeat until the advisor says release-ready or human input is required.

## Stop Conditions

Each of these is also enforced by the core where it can be (the tick reports `HUMAN_INPUT_REQUIRED` for the project; `arthur capture` exits `3`):

- Human input required — a decision is open for the project (`arthur decision list`).
- Quota at or below reserve.
- The advisor reports P0/P1 issues.
- The queue manager cannot verify the marker/control block, or the artifact says `control_block_valid: false` (quarantined; a decision was opened).
- Implementation started when the packet was plan-only (`IMPLEMENTATION_STARTED: true` quarantines the plan; a handoff without a GO gate is quarantined).
- Source-of-truth state is ambiguous after restart — `arthur queue recover` for abandoned jobs.
