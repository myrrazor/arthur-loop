# Prompt Placeholder Reference

Final prompt wording is intentionally not locked yet. Use the repo templates under `prompts/` until the user supplies scenario-specific prompts.

Use these templates:

- `prompts/chatgpt/next-plan-request.md`
- `prompts/chatgpt/plan-approval-review.md`
- `prompts/chatgpt/sprint-review.md`
- `prompts/codex/plan-only.md`
- `prompts/codex/implementation-handoff.md`
- `prompts/master/human-decision-summary.md`

Template variables:

- `{{PROJECT_ID}}`
- `{{SPRINT_ID}}`
- `{{JOB_ID}}`
- `{{CURRENT_STATE_SUMMARY}}`
- `{{CODEX_THREAD_SUMMARY}}`
- `{{CHATGPT_RESPONSE_ARTIFACT}}`
- `{{CODEX_PLAN_ARTIFACT}}`
- `{{HUMAN_DECISIONS}}`
- `{{EXPECTED_MARKER}}`
- `{{IDEMPOTENCY_KEY}}`
