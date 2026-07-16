# The Web Console

`arthur web` serves a local, single-operator control surface for one instance.
It is the browser twin of `arthur status`: same data, same discipline, plus the
handful of write actions that genuinely belong to a human.

```bash
arthur web                 # serve this instance, open the browser
arthur web --root ~/loop   # a specific instance
arthur web --port 8080 --no-open
```

The server binds `127.0.0.1` only, refuses any non-localhost `Host` header, and
mints a per-process session token that every write action must carry. There is
no auth, no multi-user, no remote hosting by design — it is a cockpit for the
person sitting at the machine, not a hosted dashboard.

## What it shows

- **Loop canvas** — the pipeline as a live map (advisor → queue → in flight →
  executor → review gate → human), with counts and badges flowing over fixed
  nodes. The human-decision node is the loudest thing on screen. Drag to pan,
  scroll to zoom, `Fit` to recenter. It never draws one node per job; work is
  traffic on the map.
- **Board** — the queue as kanban columns by status.
- **Queue** — the dense table twin of the terminal dashboard, with recover
  actions inline.
- **Artifacts** — per-project index and a reader; artifacts whose control block
  failed validation are flagged.
- **Activity** — the `queue/events.jsonl` tail, newest first.
- **Needs you** (right rail, always on) — open decisions with the question and
  an answer box, stale jobs with recover buttons, quarantined artifacts whose
  control block failed validation, and the live session list.

The top strip carries the **next action** as an imperative with a target
("Answer SAMPLE_APP Auth Scope Gate", "Poll BQ-12 · 4m ago") so one blocked
project never hides due work in another.

## What a human can do here (and what they can't)

The console exposes exactly five write actions, each a thin wrapper over the same
durable-state paths the CLI uses:

1. **Answer a decision** — records the answer into `human-decisions/open.md`,
   flips the section to `ANSWERED`, and unblocks the project.
2. **Recover a job** — parks a stale job as `needs_recovery`, optionally requeues.
   Finished jobs are refused.
3. **Create a job** — the `arthur queue create` form, with the same dedupe guard.
4. **Clear a session** — drops a finished session from the dashboard.
5. **Break a stale lock** — removes a browser lock whose holder went quiet past
   its TTL. A fresh lock is refused; you can't yank the browser from a live agent.

It deliberately does **not** offer claim/submit/poll buttons (those are the
browser-lock lifecycle the queue-manager agent owns), an "approve plan" button
(approval is only valid as a captured artifact whose control block validates),
or any way to hand-edit the ledgers. Agents own the loop; the console is where a
human answers the questions only a human can.

## Notes

- Vanilla JS and a stdlib `http.server` — no build step, no framework, no npm.
  It runs on a machine that only has Python.
- It polls `arthur status` every few seconds and pauses when the tab is hidden.
- `arthur status --json` is still the integration seam for bots and notifiers;
  the web console is the human-facing view of the same data.
