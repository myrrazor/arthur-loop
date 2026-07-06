# Arthur Loop Workflow Reference

## Roles

- Master Orchestrator: prioritizes projects, checks quota, asks human decisions, and reports status.
- Resource Governor: records quota snapshots, classifies reserve state, and blocks new work below reserve.
- Browser Queue Manager: serializes ChatGPT browser access, submits prompts, polls, captures artifacts, and updates queue state.
- Project Loop Manager: owns one project state, Atlas Tasker mapping, sprint gates, and handoffs.
- Codex Coding Session: plans or implements one approved packet, then stops for review.
- ChatGPT Pro Browser Session: handles research, high-level review, plan approval, and release/human-input decisions.

## Normal Sprint Loop

1. Snapshot quota before starting a project task.
2. Recover the project state from durable artifacts and the Codex coding session.
3. Submit a next-plan request through the Browser Queue Manager.
4. Poll once after one minute, then every five minutes while ChatGPT is thinking.
5. Save ChatGPT output under `projects/<PROJECT_ID>/artifacts/chatgpt/`.
6. Read/update the small `index.md`; future agents should consult it before full artifacts.
7. Send plan-only prompt to Codex with the artifact path and a short excerpt.
8. Send Codex plan back to ChatGPT for approval.
9. If approved, ask Codex to implement exactly one approved sprint/task.
10. Send Codex implementation handoff back to ChatGPT for review.
11. Repeat until ChatGPT says release-ready or human input is required.

## Stop Conditions

- Human input required.
- Quota at or below reserve.
- ChatGPT reports P0/P1 issues.
- Browser queue cannot verify marker/control block.
- Codex implementation started when the task was plan-only.
- Source-of-truth state is ambiguous after restart.
