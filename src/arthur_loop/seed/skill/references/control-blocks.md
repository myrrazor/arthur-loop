# Control Blocks

Every cross-agent message should end with a compact control block. The headings below keep their historical ChatGPT/Codex names; read them as "advisor" and "executor". Each block is captured with the `arthur capture --kind` shown.

## Advisor Next Plan (`--kind next-plan-request`)

```text
PROJECT_ID: <id>
REVIEW_TYPE: NEXT_PLAN_REQUEST
APPROVAL_DECISION: REQUEST_CODEX_PLAN | HUMAN_INPUT_REQUIRED
HAS_P0_P1: true | false
IDEMPOTENCY_KEY: <key>
```

## Executor Plan (`--kind plan`)

```text
PROJECT_ID: <id>
REVIEW_TYPE: CODEX_PLAN
PLAN_STATUS: READY_FOR_CHATGPT_REVIEW | HUMAN_INPUT_REQUIRED
IMPLEMENTATION_STARTED: false
```

## Advisor Plan Approval (`--kind plan-review`)

```text
PROJECT_ID: <id>
REVIEW_TYPE: PLAN_APPROVAL
APPROVAL_DECISION: APPROVE_PLAN | REVISE_PLAN | HUMAN_INPUT_REQUIRED
PLAN_HAS_P0_P1: true | false
IDEMPOTENCY_KEY: <key>
```

## Executor Implementation Handoff (`--kind implementation-handoff`)

```text
PROJECT_ID: <id>
SPRINT_ID: <id>
REVIEW_TYPE: CODEX_IMPLEMENTATION_HANDOFF
IMPLEMENTATION_STATUS: COMPLETE | BLOCKED | HUMAN_INPUT_REQUIRED
TESTS_RUN: true | false
READY_FOR_CHATGPT_REVIEW: true | false
```

## Advisor Sprint Review (`--kind sprint-review`)

```text
PROJECT_ID: <id>
SPRINT_ID: <id>
REVIEW_TYPE: SPRINT_REVIEW
APPROVAL_DECISION: APPROVE_SPRINT | FIX_REQUIRED | RELEASE_READY | HUMAN_INPUT_REQUIRED
HAS_P0_P1: true | false
IDEMPOTENCY_KEY: <key>
```

## Parsing Rules

The response must put the control block in the final fenced block, exactly once. Do not repeat control-block syntax in prose or examples.

Arthur Loop parses only:

1. the final fenced block, or
2. the final contiguous run of `KEY: value` lines when no fenced block exists.

The block is invalid — the artifact is quarantined, a human decision opens for the project, and `arthur capture` exits `3` — when any of these hold:

- a value is outside its enum (exact uppercase decisions, lowercase `true`/`false`);
- `REVIEW_TYPE` does not match the kind the artifact was captured as;
- `PROJECT_ID` does not match the project it was captured for;
- the kind's decision field (`APPROVAL_DECISION`, `PLAN_STATUS`, or `IMPLEMENTATION_STATUS`) is missing;
- a plan says `IMPLEMENTATION_STARTED: true`;
- an implementation handoff arrives while `arthur gate implementation` is NO-GO.

A valid block whose decision is `HUMAN_INPUT_REQUIRED` also opens a human decision. Parse control blocks before acting. Human input and P0/P1 findings block the affected project.
