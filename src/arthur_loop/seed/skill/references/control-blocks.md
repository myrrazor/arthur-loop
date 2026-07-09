# Control Blocks

Every cross-agent message should end with a compact control block.

## ChatGPT Next Plan

```text
PROJECT_ID: <id>
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN | HUMAN_INPUT_REQUIRED
HAS_P0_P1: true | false
IDEMPOTENCY_KEY: <key>
```

## Codex Plan

```text
PROJECT_ID: <id>
REVIEW_TYPE: CODEX_PLAN
PLAN_STATUS: READY_FOR_CHATGPT_REVIEW | HUMAN_INPUT_REQUIRED
IMPLEMENTATION_STARTED: false
```

## ChatGPT Plan Approval

```text
PROJECT_ID: <id>
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: APPROVE_PLAN | REVISE_PLAN | HUMAN_INPUT_REQUIRED
PLAN_HAS_P0_P1: true | false
IDEMPOTENCY_KEY: <key>
```

## Codex Implementation

```text
PROJECT_ID: <id>
SPRINT_ID: <id>
REVIEW_TYPE: CODEX_IMPLEMENTATION_HANDOFF
IMPLEMENTATION_STATUS: COMPLETE | BLOCKED | HUMAN_INPUT_REQUIRED
TESTS_RUN: true | false
READY_FOR_CHATGPT_REVIEW: true | false
```

## Parsing Rules

The response must put the control block in the final fenced block, exactly once. Do not repeat control-block syntax in prose or examples.

Arthur Loop parses only:

1. the final fenced block, or
2. the final contiguous run of `KEY: value` lines when no fenced block exists.

Automation-driving values are validated against the enums shown above. Invalid or ambiguous values are saved in the artifact, but they must not be treated as approval.

Parse control blocks before acting. Human input and P0/P1 findings block the affected project.
