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
3. Submit a next-plan request through the queue manager.
4. Poll once after one minute, then every five minutes while the advisor is thinking.
5. Save advisor output under `projects/<PROJECT_ID>/artifacts/chatgpt/`.
6. Read/update the small `index.md`; future agents should consult it before full artifacts.
7. Send the plan-only prompt to the executor with the artifact path and a short excerpt.
8. Send the executor's plan back to the advisor for approval.
9. If approved, ask the executor to implement exactly one approved sprint/task.
10. Send the executor's implementation handoff back to the advisor for review.
11. Repeat until the advisor says release-ready or human input is required.

## Stop Conditions

- Human input required.
- Quota at or below reserve.
- The advisor reports P0/P1 issues.
- The queue manager cannot verify the marker/control block, or the artifact says `control_block_valid: false`.
- Implementation started when the packet was plan-only.
- Source-of-truth state is ambiguous after restart.
