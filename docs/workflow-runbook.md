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

Also under `runtime/` (ignored by git): `watch-state.json` (last `arthur watch` observation) and the `.*.lock` files that serialize concurrent CLI runs.

Trackers are mirrors, not sources of truth: the `atlas-tasker` and `command` adapters are three argv templates (`open_decision`, `close_decision`, `sprint_gate`) run from the instance root — see `docs/adapters.md`. Gate decisions come from the artifact store, never from the tracker. A private repository can back up tracker and Arthur Loop state on a schedule.

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

The statuses form a state machine and the CLI refuses moves outside it (exit code 2):

| From | May move to |
| --- | --- |
| `queued` | `claimed`, `submitted`, `needs_recovery`, `failed`, `cancelled` |
| `claimed` | `claimed` (retry), `submitted`, `needs_recovery`, `failed`, `cancelled` |
| `submitted` / `waiting_for_chatgpt` | `waiting_for_chatgpt`, `stopped_no_output`, `completed`, `completed_with_warnings`, `needs_recovery`, `failed`, `cancelled` |
| `stopped_no_output` | `submitted` (one retry, same idempotency key), `waiting_for_chatgpt`, `completed`, `completed_with_warnings`, `needs_recovery`, `failed`, `cancelled` |
| `needs_recovery` | `queued`, `failed`, `cancelled` |
| terminal (`completed`, `completed_with_warnings`, `failed`, `cancelled`) | nothing — create a new job |

`arthur queue create` refuses a duplicate `--job-id` and a reused `--idempotency-key` (the key exists so a retried create cannot fork the queue); `--force` re-queues an existing job id. Concurrent `arthur` processes on one instance are serialized with a POSIX file lock (`runtime/.queue-ledger.lock`), so two of them cannot both pass a uniqueness check.

Common commands (`--root DIR` may go before or after any subcommand):

```bash
arthur queue create \
  --job-id <JOB_ID> \
  --project-id <PROJECT_ID> \
  --target-chat-title "<advisor conversation title>" \
  --target-chat-url "<advisor conversation URL or manual>" \
  --prompt-path <prompt.md> \
  --idempotency-key <stable key>

arthur queue claim --job-id <JOB_ID>            # takes the lease; records claimed_by
arthur queue submit --job-id <JOB_ID>           # releases the lease unless --keep-lock
arthur queue poll-result --job-id <JOB_ID> --marker-found false --status waiting_for_chatgpt
arthur queue poll-result --job-id <JOB_ID> --marker-found true --status completed
arthur queue recover --job-id <JOB_ID> --requeue
arthur queue cancel --job-id <JOB_ID> --reason "superseded"
arthur queue due
arthur queue show --job-id <JOB_ID>
```

`claim`, `submit`, and `poll-result` use the browser lock so only one queue manager controls the shared browser at a time. A `claim` or `poll-result` that is refused by the state machine never takes the lease. (Working from a source checkout without installing? Every command also exists as a `PYTHONPATH=src scripts/*.py` shim.)

### Crash after claim

A manager that dies after `claim` leaves a `claimed` job and a fresh lease. Until the lease TTL (15 minutes by default) passes, every other manager sees `BLOCKED_BY_BROWSER_LOCK`; the job itself becomes `stale` after 30 minutes and `arthur status`/`arthur watch` flag it. Recovery is one command:

```bash
arthur queue recover --job-id <JOB_ID> --requeue --error "manager died"
```

`recover` parks (and with `--requeue` re-queues) the job **and releases the lease** when its holder is the manager that claimed the job (`claimed_by`, or `--holder` for ledgers written before that field). A lease held by someone else is left alone and reported; `arthur lock break --force` removes it if that manager is also dead. `--keep-lock` skips the lease entirely.

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
  --kind <next-plan-request|plan|plan-review|implementation-handoff|sprint-review|human-decision> \
  --source-chat-title "<advisor conversation title, or the adapter name>" \
  --source-file <captured-response.md>
```

`--kind` names the hop and is fixed vocabulary:

| Kind | Who wrote it | Expected `REVIEW_TYPE` | Decision field |
| --- | --- | --- | --- |
| `next-plan-request` | advisor | `NEXT_PLAN_REQUEST` | `APPROVAL_DECISION` |
| `plan` | executor | `CODEX_PLAN` | `PLAN_STATUS` |
| `plan-review` | advisor | `PLAN_APPROVAL` | `APPROVAL_DECISION` |
| `implementation-handoff` | executor | `CODEX_IMPLEMENTATION_HANDOFF` | `IMPLEMENTATION_STATUS` |
| `sprint-review` | advisor | `SPRINT_REVIEW` | `APPROVAL_DECISION` |
| `human-decision` | master | `HUMAN_DECISION_ESCALATION` | — |

The capture command writes small frontmatter, updates `index.md` and `index.jsonl`, and links the artifact path back to the queue job when possible.

Control blocks are parsed only from the final fenced block, falling back to the final contiguous run of `KEY: value` lines when no fence exists. The block is **invalid** when any of these hold: a value is outside its enum; `REVIEW_TYPE` does not match the kind; `PROJECT_ID` does not match `--project-id`; the kind's decision field is missing; a plan says `IMPLEMENTATION_STARTED: true`; an `implementation-handoff` arrives while `arthur gate implementation` is NO-GO.

An invalid block, or a valid one whose decision is `HUMAN_INPUT_REQUIRED`, **quarantines the artifact and opens a human decision** for the project (`## <PROJECT_ID> Quarantined artifact — <kind> <job>` / `## <PROJECT_ID> Human input required — <kind> <job>`). The artifact is still saved as evidence, marked `control_block_valid: false` and `QUARANTINED` in the index, and `capture` exits `3`. The tick then reports `HUMAN_INPUT_REQUIRED` for that project until someone runs `arthur decision answer`. `--no-escalate` quarantines without opening the decision, for replaying old material.

Exit codes: `0` saved and trusted · `3` saved but needs a human · `2` nothing saved.

## Approval Gate

Implementation may begin only when the loop, not a prompt, says so:

```bash
arthur gate implementation --project-id <PROJECT_ID>        # GO → exit 0, NO-GO → exit 3 with reasons
arthur gate implementation --project-id <PROJECT_ID> --json
```

GO requires, from the project's saved artifacts alone: the newest `plan-review` is valid and says `APPROVE_PLAN`; nothing captured after it is a `plan` or `next-plan-request` (planning re-opened) or a `sprint-review` saying `APPROVE_SPRINT`/`RELEASE_READY` (that sprint is done; the next one needs its own approval) or a quarantined artifact; and the project has no open human decision. `FIX_REQUIRED` keeps the gate open, since fixes stay inside the approved sprint.

The executor's implementation-handoff prompt asks it to run the gate first. Whether or not it does, `arthur capture --kind implementation-handoff` re-evaluates the gate and quarantines a handoff that arrives while it is NO-GO. The core cannot stop a process from editing files; it can refuse to let ungated work enter the loop as trusted evidence, and it does.

## Human Decisions

```bash
arthur decision list [--all] [--json]
arthur decision open --project-id <PROJECT_ID> --title "<question>" [--body "..."|--body-file f] [--no-tracker]
arthur decision answer --title "<PROJECT_ID> <question>" --answer "..." | --answer-file f
arthur decision clear --title "<PROJECT_ID> <question>" [--note "..."]
```

Decisions live in `human-decisions/open.md`, one `## <PROJECT_ID> <title>` section each with a `Status:` line. `OPEN` pauses that project in the tick; `ANSWERED` and `CLEARED` release it. `open` runs the tracker's `open_decision` template when a tracker is configured. Bodies and answers are escaped so a pasted `## heading` or `Status: OPEN` cannot forge a new decision. The web console's answer action uses the same code.

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

## Notifications

`arthur watch` polls the tick (read-only) and sends a desktop notification on
transitions into `HUMAN_INPUT_REQUIRED`, `BLOCKED_BY_QUOTA`,
`BLOCKED_BY_BROWSER_LOCK`, or `POLL_DUE`, on newly opened human decisions, and
on newly stale jobs — one notification per event, never repeats. Each check
also prints a one-line heartbeat, so a `watch` pane doubles as a terminal
event tray.

```bash
arthur watch --interval 30        # macOS: osascript · Linux: notify-send
arthur watch --once --quiet       # cron/launchd: prints only new events; silent when nothing changed
arthur notify --message "..."     # ad-hoc, for runbooks and agents; exit 2 when no notifier exists
```

`watch` keeps its last observation in `runtime/watch-state.json`, which is what
makes `--once` safe to run every minute. Without a desktop notifier (`osascript`
on macOS, `notify-send` from libnotify on Linux; nothing on Windows or headless
servers) it says so once and prints events instead.

Agents may call `arthur notify` directly at human gates; `watch` exists so
nobody has to remember to.

## Quota Policy

Quota numbers come from a pluggable source configured in `quota.provider`:

| Provider | How it works |
| --- | --- |
| `auto` (default) | Uses `codexbar` when installed, otherwise behaves like `none`. |
| `codexbar` | Shells out to [CodexBar](https://github.com/steipete/CodexBar)'s CLI, which already handles subscription auth (OAuth/keychain/web) for codex, claude, gemini, grok, and dozens more — set `quota.codexbar_provider` to match your executor. Arthur Loop deliberately does not reimplement that authentication. |
| `command` | Runs `quota.command` (argv, never a shell); it must print codexbar-schema JSON. |
| `file` | Reads codexbar-schema JSON from `quota.path` — useful when another tool refreshes it. |
| `none` | Governor has no data; `arthur usage snapshot` explains how to wire a source. |

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
3. Create a `NEXT_PLAN_REQUEST` advisor job through `arthur queue create` (with an `--idempotency-key`).
4. The advisor returns `REQUEST_CODEX_PLAN` or `HUMAN_INPUT_REQUIRED`.
5. Save the advisor response with `arthur capture --kind next-plan-request`. Exit `3` means a decision is already open — stop that project only.
6. If a plan was requested, send the executor pack's `plan-only.md` to the project's executor session with the artifact path and a short excerpt.
7. The executor returns `READY_FOR_CHATGPT_REVIEW` or `HUMAN_INPUT_REQUIRED`; save it with `arthur capture --kind plan`.
8. If ready, send the plan back to the advisor using the advisor pack's `plan-approval-review.md`; save the verdict with `--kind plan-review`.
9. Iterate until `APPROVE_PLAN`, `REVISE_PLAN`, or `HUMAN_INPUT_REQUIRED`.

## Implementation Loop

1. Run `arthur gate implementation --project-id <ID>`; begin only on GO.
2. Send one implementation handoff to the executor session (the prompt tells it to run the gate too).
3. The executor works one sprint/task only.
4. The executor runs tests, captures `TEST_STDOUT.log`, and returns a structured handoff; save it with `arthur capture --kind implementation-handoff` (quarantined if the gate was NO-GO).
5. The advisor reviews using the advisor pack's `sprint-review.md`; save it with `--kind sprint-review`.
6. Fix required issues in the executor session, then re-review (`FIX_REQUIRED` keeps the gate open).
7. Stop on release-ready or human input required. `APPROVE_SPRINT`/`RELEASE_READY` close the gate; the next sprint starts a new planning loop.

## Human Decision Escalation

Human questions should not block unrelated projects.

When a project needs input:

1. `arthur decision open --project-id <ID> --title "<question>" --body "<options, impact>"` (capture does this for you on quarantine / `HUMAN_INPUT_REQUIRED`).
2. Mark affected project as blocked in `projects/<PROJECT_ID>/state.md`.
3. Keep unrelated project queue jobs eligible if quota allows — the tick already does.
4. Include the decision in the next master status report; the human answers with `arthur decision answer` or the web console.

## Skill Packaging

The agent-facing skill ships inside the package at `src/arthur_loop/seed/skill/` and `arthur init` copies it into every instance under `agent-setup/skill/` (plus your main agent's native location — `.claude/skills/` for Claude Code, an `AGENTS.md`/`GEMINI.md` pointer for others).

Tune the instance copy; treat the packaged one as the template.
