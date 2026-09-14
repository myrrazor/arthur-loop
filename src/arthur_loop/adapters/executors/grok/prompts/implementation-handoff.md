# Grok Implementation Handoff Placeholder

```text
Arthur Loop implementation handoff for {{PROJECT_ID}}.

The advisor approved this implementation prompt:
{{CHATGPT_RESPONSE_ARTIFACT}}

Before editing any file, run `arthur gate implementation --project-id {{PROJECT_ID}}` from the Arthur Loop instance root. If it prints NO-GO, stop and report that instead of implementing; the loop quarantines handoffs made while the gate is closed.

Implement one approved sprint/task only. Follow repository AGENTS.md. Do not move to the next sprint after tests pass; stop for review.

Before final response:
- run required tests
- capture TEST_STDOUT.log
- summarize files changed
- summarize assumptions and known gaps
- include final control block

Final control block:
PROJECT_ID: {{PROJECT_ID}}
SPRINT_ID: {{SPRINT_ID}}
REVIEW_TYPE: CODEX_IMPLEMENTATION_HANDOFF
IMPLEMENTATION_STATUS: COMPLETE, BLOCKED, or HUMAN_INPUT_REQUIRED
TESTS_RUN: true/false
READY_FOR_CHATGPT_REVIEW: true/false
```
