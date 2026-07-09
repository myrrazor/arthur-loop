# Arthur Loop Workflow Runbook

Date: 2026-06-22

## Purpose

This runbook describes the durable workflow Arthur Loop follows once your prompts are tuned.

The goal is not to make the advisor and executor talk forever. The goal is to keep each project moving in controlled sprints, with durable state, quota awareness, human-decision escalation, and explicit review gates. Throughout: the **advisor** plans and reviews (e.g. ChatGPT Pro in the browser, Claude Code, a human), the **executor** implements (e.g. Codex, Claude Code). Adapter specifics live in `docs/adapters.md`.

## Control Plane Roles

| Role | Writes State | Reads State | Main Job |
| --- | --- | --- | --- |
| Master Orchestrator | `human-decisions/`, status reports, usage records | all project state, queue state, usage state | Prioritize, coordinate, escalate, report. |
| Resource Governor (optional) | `usage/snapshots.jsonl`, `usage/task-usage.jsonl` | `codexbar usage`, active job list | Keep the reserve and estimate task usage. |
| Advisor Queue Manager | `queue/jobs.jsonl`, `queue/events.jsonl`, advisor artifacts | prompt templates, queue state | Serialize advisor (browser) access. |
| Project Loop Manager | `projects/<id>/state.md`, tracker tickets | project artifacts, advisor/executor outputs | Own one project loop. |
| Executor Session (Codex, Claude Code, ...) | repo code, test logs, handoff reports | approved implementation prompt | Plan or implement one packet only. |
| Advisor Session (ChatGPT Pro browser, ...) | advisor response artifacts | compact project packets | Review, research, approve, reject, or escalate. |

## State Files

Durable local state:

- `queue/jobs.jsonl`: append-only queue snapshots.
- `queue/events.jsonl`: append-only queue events.
- `runtime/browser-lock.json`: current browser-control lease, ignored by git.
- `runtime/tick-state.json`: latest local scheduler tick, ignored by git.
- `runtime/sessions.jsonl`: append-only self-reported agent-session activity, ignored by git.
- `projects/<PROJECT_ID>/state.md`: latest project loop state.
- `projects/<PROJECT_ID>/artifacts/chatgpt/index.md`: cheap index of saved advisor responses (the directory name is historical and stays stable for compatibility).
- `human-decisions/open.md`: human input queue.
- `usage/snapshots.jsonl`: quota snapshots.
- `usage/task-usage.jsonl`: task usage attribution.
- `outputs/`: generated reports and captured advisor/executor artifacts.
- `prompts/`: scenario prompt templates.

Future source of truth:

- Your tracker (Atlas Tasker, or any CLI tracker via the `command` adapter) can own project tickets, sprint tasks, review gates, and human-decision tickets.
- A private repository can back up tracker and Arthur Loop state on a schedule.

## Queue Lifecycle

Recommended queue statuses:

| Status | Meaning | Next Action |
| --- | --- | --- |
| `queued` | Job is ready but not claimed. | Queue manager may claim. |
| `claimed` | Queue manager owns the job. | Confirm the target advisor conversation. |
| `submitted` | Prompt was sent. | First poll after one minute. |
| `waiting_for_chatgpt` | The advisor is still thinking (status name is historical). | Poll again after five minutes. |
| `stopped_no_output` | The advisor stopped without useful output. | Retry once after five minutes or fail cleanly. |
| `needs_recovery` | Job is stale or ambiguous. | Master/queue manager must inspect before retrying. |
| `completed` | Marker/control block captured. | Hand artifact to project loop. |
| `completed_with_warnings` | Marker captured, but retrieval degraded. | Proceed only if artifact is sufficient. |
| `failed` | Job cannot be trusted. | Escalate to master. |
| `cancelled` | Job intentionally stopped. | No action. |

Use `arthur queue` for queue updates. Do not hand-edit `queue/jobs.jsonl`.

Common commands:

```bash
arthur queue create \
  --job-id <JOB_ID> \
  --project-id <PROJECT_ID> \
  --target-chat-title "<advisor conversation title>" \
  --target-chat-url "<advisor conversation URL>" \
  --prompt-path <prompt.md>

arthur queue claim --job-id <JOB_ID>
arthur queue submit --job-id <JOB_ID>
arthur queue poll-result --job-id <JOB_ID> --marker-found true --status completed
arthur queue recover --job-id <JOB_ID> --requeue
arthur queue due
```

`claim`, `submit`, and `poll-result` use the browser lock so only one queue manager controls the shared browser at a time. (Working from a source checkout without installing? Every command also exists as a `PYTHONPATH=src scripts/*.py` shim.)

## Advisor Artifact Capture

Rule: save advisor-produced text before handing it to the executor.

This applies to:

- markdown copied from the browser,
- text reconstructed from accessibility/visible UI,
- downloaded markdown files,
- ZIP contents that need to be summarized for the executor.

Store text artifacts under (the `chatgpt` directory name is historical and stays stable):

```text
projects/<PROJECT_ID>/artifacts/chatgpt/
```

Future agents should read `index.md` first and only open full artifacts when needed. The index is intentionally small so it can be used as the default lookup path.

Capture command:

```bash
arthur capture \
  --project-id <PROJECT_ID> \
  --job-id <JOB_ID> \
  --kind <next-plan-request|plan-review|sprint-review|human-decision> \
  --source-chat-title "<advisor conversation title>" \
  --source-file <captured-response.md>
```

The capture script writes small frontmatter, updates `index.md` and `index.jsonl`, and links the artifact path back to the queue job when possible.

Control blocks are parsed only from the final fenced block, falling back to the final contiguous run of `KEY: value` lines when no fence exists. Invalid enum values are saved as warnings and must not be treated as approval.

## Browser Policy

Use this order:

1. A browser your agent can already control, logged in to the advisor service.
2. A dedicated scriptable browser profile only once its authenticated session is stable.
3. Fallback copy from visible/accessibility text when download/copy fails.

Do not use the OpenAI API for the ChatGPT Pro browser review loop.

## Polling Policy

Default:

- First poll: 1 minute after submit.
- Steady poll: every 5 minutes.
- Slow advisor response: keep polling without changing the prompt.
- Stopped/no output: record `stopped_no_output`, wait 5 minutes, retry once with same idempotency key and clearer instruction.
- Stale active job: `arthur tick --dry-run` should surface it; `arthur queue recover` parks or requeues it.

Measured fields:

| Field | Formula |
| --- | --- |
| `scheduled_delay_minutes` | `scheduled_first_poll_at - submitted_at` |
| `actual_first_poll_delay_minutes` | `first_poll_at - submitted_at` |
| `poll_drift_seconds` | `first_poll_at - scheduled_first_poll_at` |
| `response_latency_minutes` | `completed_at - submitted_at` |

## Heartbeat / Tick

Run tick at the start of each master orchestration turn:

```bash
arthur tick --dry-run
```

The dry run only reads state and classifies the next action:

- `WAIT`
- `POLL_DUE`
- `BLOCKED_BY_BROWSER_LOCK`
- `BLOCKED_BY_QUOTA`
- `HUMAN_INPUT_REQUIRED`

Non-dry-run mode writes `runtime/tick-state.json` and appends small queue events such as `tick`, `poll_due`, `stale_job`, or `quota_paused`. It does not control the browser, message executor sessions, or call a model.

## Status Dashboard

Tick gates; status displays. The master session renders the dashboard at the start of every orchestration turn:

```bash
arthur status
```

It shows the headline tick state, self-reported agent sessions, active queue jobs, project summaries with decision-block flags, open human decisions, the quota bar, and the browser lock. `--json` prints the same data machine-readable — that's the hook for external notifiers (Discord bots, dashboards, whatever polls it).

Every manager or executor session reports what it is doing when it picks up work and on major transitions:

```bash
arthur status set \
  --session-id demo-app-loop --role project-loop --project-id DEMO_APP \
  --state working --activity "revising the sprint plan for advisor review"
arthur status clear --session-id demo-app-loop
```

States: `working`, `waiting`, `blocked`, `idle`, `done`. Suggested roles: `master`, `queue-manager`, `project-loop`, `executor`, `governor`. Sessions that have not reported for 60 minutes render dimmed with a `(stale)` marker — a stale `working` row usually means a session died mid-task and its work needs the recovery path.

## Quota Policy

Run quota snapshots:

- Before a master orchestration segment.
- After a master orchestration segment.
- Before and after a long executor coding task.
- Before starting any new project sprint.
- After any unexpectedly expensive browser or planning loop.

Resource Governor state:

| Executor Quota Remaining | State | Behavior |
| --- | --- | --- |
| `> 5%` | GREEN | Start new work if project gates allow it. |
| `= 5%` | YELLOW | Checkpoint and report only. |
| `< 5%` | RED | Stop new work until reset. |

Task attribution confidence:

| Active Executor Tasks | Background Activity | Confidence |
| --- | --- | --- |
| 1 | no | HIGH |
| 1 | yes | MEDIUM |
| 2-3 | any | LOW |
| 4+ | any | VERY_LOW |
| reset crossed or missing data | any | UNKNOWN |

## Planning Loop

1. Create current-state summary.
2. Run `arthur tick --dry-run` and `arthur status`.
3. Create a `NEXT_PLAN_REQUEST` advisor job through `arthur queue create`.
4. The advisor returns `REQUEST_CODEX_PLAN` or `HUMAN_INPUT_REQUIRED`.
5. Save the advisor response with `arthur capture`.
6. If human input required, write `human-decisions/open.md` and stop that project only.
7. If a plan was requested, send the executor pack's `plan-only.md` to the project's executor session with the artifact path and a short excerpt.
8. The executor returns `READY_FOR_CHATGPT_REVIEW` or `HUMAN_INPUT_REQUIRED`.
9. If ready, send the plan back to the advisor using the advisor pack's `plan-approval-review.md`.
10. Iterate until `APPROVE_PLAN`, `REVISE_PLAN`, or `HUMAN_INPUT_REQUIRED`.

## Implementation Loop

1. Only begin from an advisor-approved implementation prompt.
2. Send one implementation handoff to the executor session.
3. The executor works one sprint/task only.
4. The executor runs tests, captures `TEST_STDOUT.log`, and returns a structured handoff.
5. The advisor reviews using the advisor pack's `sprint-review.md`.
6. Fix required issues in the executor session, then re-review.
7. Stop on release-ready or human input required.

## Human Decision Escalation

Human questions should not block unrelated projects.

When a project needs input:

1. Write/update `human-decisions/open.md`.
2. Mark affected project as blocked in `projects/<PROJECT_ID>/state.md`.
3. Keep unrelated project queue jobs eligible if quota allows.
4. Include the decision in the next master status report.

## Skill Packaging

The agent-facing skill ships inside the package at `src/arthur_loop/seed/skill/` and `arthur init` copies it into every instance under `agent-setup/skill/` (plus your main agent's native location — `.claude/skills/` for Claude Code, an `AGENTS.md`/`GEMINI.md` pointer for others).

Tune the instance copy; treat the packaged one as the template.
