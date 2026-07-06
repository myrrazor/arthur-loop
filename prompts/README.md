# Prompt Templates

Prompt packs live with their adapters:

- Advisor prompts: `src/arthur_loop/adapters/advisors/<adapter>/prompts/`
- Executor prompts: `src/arthur_loop/adapters/executors/<adapter>/prompts/`
- Cross-role prompts (this directory): `prompts/master/`

`arthur init` copies your chosen adapter's pack into the instance under `adapters/advisor/prompts/` and `adapters/executor/prompts/` — tune the wording there, not in the package.

Rules:

- Keep scenario prompts versioned.
- Keep control blocks machine-readable: one fenced block at the very end, exact uppercase decision values, lowercase true/false, never repeat control-block syntax in prose.
- Keep browser-submitted prompts single-line when the input path is fragile.
- Keep project source, secrets, and private browser history out of advisor packets unless explicitly approved.

Common placeholders:

- `{{PROJECT_ID}}`
- `{{SPRINT_ID}}`
- `{{JOB_ID}}`
- `{{CURRENT_STATE_SUMMARY}}`
- `{{CODEX_THREAD_SUMMARY}}`
- `{{CHATGPT_RESPONSE_ARTIFACT}}`
- `{{CHATGPT_RESPONSE_ARTIFACT_PATH}}`
- `{{CODEX_PLAN_ARTIFACT}}`
- `{{HUMAN_DECISIONS}}`
- `{{EXPECTED_MARKER}}`
- `{{IDEMPOTENCY_KEY}}`

(The `CODEX_`/`CHATGPT_` placeholder names are historical — they mean "executor" and "advisor". They stay stable so existing prompt packs keep working.)
