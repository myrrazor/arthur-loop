# Grok Plan Approval Review Placeholder

```text
Arthur Loop plan approval review for {{PROJECT_ID}}.

Please review the executor-generated plan below. Approve it only if it satisfies the prior advisor instruction, preserves all stop conditions, and has no unresolved P0/P1 concerns.

Executor plan:
{{CODEX_PLAN_ARTIFACT}}

Return concise markdown with sections:
- Received Marker
- Plan Assessment
- Required Fixes
- Human Input Required
- Approved Implementation Prompt
- Control Block

Control Block must include:
PROJECT_ID: {{PROJECT_ID}}
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: APPROVE_PLAN, REVISE_PLAN, or HUMAN_INPUT_REQUIRED
PLAN_HAS_P0_P1: true/false
IDEMPOTENCY_KEY: {{IDEMPOTENCY_KEY}}
```
