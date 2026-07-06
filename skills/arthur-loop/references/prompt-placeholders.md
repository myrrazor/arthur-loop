# Prompt Placeholder Reference

Final prompt wording is intentionally not locked. Use the adapter prompt packs until the operator supplies scenario-specific wording.

Use these templates (paths inside the package; `arthur init` copies your chosen packs into the instance under `adapters/advisor/prompts/` and `adapters/executor/prompts/`):

- `src/arthur_loop/adapters/advisors/<adapter>/prompts/next-plan-request.md`
- `src/arthur_loop/adapters/advisors/<adapter>/prompts/plan-approval-review.md`
- `src/arthur_loop/adapters/advisors/<adapter>/prompts/sprint-review.md`
- `src/arthur_loop/adapters/executors/<adapter>/prompts/plan-only.md`
- `src/arthur_loop/adapters/executors/<adapter>/prompts/implementation-handoff.md`
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

(The `CODEX_`/`CHATGPT_` names are historical — read them as "executor" and "advisor".)
