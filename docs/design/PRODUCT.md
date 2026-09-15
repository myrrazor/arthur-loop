# Arthur Loop product context

## Product

- **Purpose:** File-first control plane for AI dev loops — bring your own advisor, executor, and tracker.
- **Maturity:** Alpha, preparing the v0.1.0 public release.
- **Surfaces:** Python CLI, stdio MCP server, localhost web console (status map + create-loop wizard), native macOS menu bar companion, and static launch site.
- **Model:** Free and MIT licensed.

## User and job

The primary user is a developer running long-lived work across one or more coding agents. They know a terminal, care about recoverability, and want to avoid invisible state, skipped reviews, duplicate prompts, and one blocked project freezing everything.

On the launch site, their arrival question is: “Does this solve the trust and handoff failures in my agent loop, and can I inspect what it does?” Success means they understand the mechanism, see real output, and copy a working install command.

## Core objects

| User term | Meaning | Important attributes |
| --- | --- | --- |
| Queue | Durable advisor work ledger | job ID, project, state, next poll, attempts |
| Artifact | Saved advisor or executor output | source, control block, validity, project |
| Gate | A transition that requires explicit review | decision, reviewer, approval state |
| Human decision | A project-scoped blocking question | project, question, answer, open/closed |
| Tick | Scheduler classification | due work, quota, lock, blocked projects |

## Critical journey

1. Understand that Arthur Loop coordinates existing tools rather than replacing them.
2. See the real terminal and web-console state.
3. Pick pipx, uv, or the shell installer.
4. Run `arthur init`. On this branch it writes skills + MCP where those clients load them. Public main / v0.1.0 is a file cockpit only — no follow/MCP/walk. Then create a loop with `arthur loop create`, the web wizard, or `/arthur-loop` (branch).

## Constraints

- The site is plain static HTML with local assets and no build step, external fonts, analytics, or runtime requests.
- Vercel is the deployment target; the production domain is `arthurloop.com`. `arthur-loop.vercel.app` 404s and must not be used as canonical.
- The CLI supports Python 3.9+ and is exercised on macOS and Linux. Windows behavior remains unverified.
- Demo assets stay under 2 MB each. Product claims must be visible in the current source or tests.

## Voice and anti-references

Behavioral character: exact, durable, candid. Use queue, artifact, gate, decision, tick, JSONL, and localhost. Avoid “revolutionary,” “seamless,” invented adoption metrics, fake testimonials, generic AI gradients, glass surfaces, and decorative node diagrams that do not reflect the real loop.

## Open hypothesis

The site assumes developers will trust a control plane faster when the real state model appears before the feature list. Validate after launch through issue feedback and install-path questions, not invented conversion claims.
