# ChatGPT Next Plan Request Placeholder

```text
Arthur Loop next-plan request for {{PROJECT_ID}}.

Current state:
{{CURRENT_STATE_SUMMARY}}

Task:
Review the current state and return the next plan-only instruction for Codex. Do not request implementation yet. If a human decision is required before planning, say HUMAN_INPUT_REQUIRED.

Return concise markdown with sections:
- Received Marker
- Context Check
- Human Input Required
- Plan-Only Prompt For Codex
- Approval Criteria
- Control Block

Include marker {{EXPECTED_MARKER}}.

Control Block must include:
PROJECT_ID: {{PROJECT_ID}}
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN or HUMAN_INPUT_REQUIRED
HAS_P0_P1: true/false
IDEMPOTENCY_KEY: {{IDEMPOTENCY_KEY}}
```
