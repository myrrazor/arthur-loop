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
mints a per-process session token. `arthur web` prints the one URL that carries
it (`http://127.0.0.1:7433/?token=…`); the page loads only through that URL and
every `/api/*` read and write must present the token (`X-Arthur-Token`). A bare
`GET /` — from another local user, a browser tab you did not open yourself, or a
guessing script — gets a 403 and never sees the token. There is no login, no
multi-user, no remote hosting by design — it is a cockpit for the person
sitting at the machine, not a hosted dashboard. The console refuses to serve a
directory that is not an Arthur Loop instance.

## What it shows

- **Loop canvas** — a **status map** of the fixed pipeline (advisor → queue →
  in flight → executor → review gate → human), with counts and badges flowing
  over those nodes. The human-decision node is the loudest thing on screen.
  Drag to pan, scroll to zoom, `Fit` to recenter. It is not a graph composer
  and never draws one node per job; work is traffic on the map.
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

The console exposes exactly six write actions, each a thin wrapper over the same
durable-state paths the CLI uses:

1. **Answer a decision** — records the answer into `human-decisions/open.md`,
   flips the section to `ANSWERED`, and unblocks the project. Same code path as
   `arthur decision answer`.
2. **Recover a job** — parks a stale job as `needs_recovery`, optionally requeues,
   and releases the browser lease of the manager that claimed it. Same code path
   as `arthur queue recover`. Finished jobs are refused.
3. **Create a loop** — the `arthur loop create` wizard: project id, advisor /
   executor / tracker, optional first queue job. Same code path as the CLI and
   MCP. The copy on the form says this is not a drag-drop graph composer.
4. **Create a job** — the `arthur queue create` form, with the same dedupe guard
   on job id, idempotency key, and colliding markers.
5. **Clear a session** — drops a finished session from the dashboard.
6. **Break a stale lock** — removes a browser lock whose holder went quiet past
   its TTL. A fresh lock is refused; you can't yank the browser from a live agent
   (the CLI's `arthur lock break --force` can, for a manager you know is dead).

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
