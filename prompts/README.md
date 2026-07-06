# Prompt Templates

These are placeholders for the final Arthur Loop prompt language.

Rules:

- Keep scenario prompts versioned.
- Keep control blocks machine-readable.
- Keep browser prompts single-line when using fragile UI input.
- Keep project source, secrets, and private browser history out of ChatGPT browser packets unless explicitly approved.
- Replace placeholders with the final wording the user provides before turning these into installed skills.

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
