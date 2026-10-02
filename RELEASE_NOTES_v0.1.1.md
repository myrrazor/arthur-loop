# Arthur Loop v0.1.1

Arthur Loop is a file-first control plane for long-running AI development loops. One agent can plan and review, another can implement, and the handoffs stay durable: queued work, saved artifacts, explicit approval gates, project-scoped human decisions, and a scheduler that can explain what happens next.

v0.1.1 is the second alpha. It is the first tag that installs into your coding agents and can drive a loop hop by hop. It is still an alpha: read the known edges below before you leave it running.

## Highlights since v0.1.0

- `arthur init` writes skills, slash commands, and MCP config for Claude Code, Codex, Cursor, and Grok Build (`--no-integrations` skips). `arthur integrations detect|install|status|probe` manages them later.
- Stdio MCP server (`arthur mcp serve`) with read tools and gated writes, plus the `/arthur-loop` prompt that interviews for roles and creates the loop.
- Assignable roles: planner, implementer, reviewer, QA. Bare `arthur`, `arthur run`, and `arthur follow` claim the next hop, invoke the assigned agent's CLI, capture, gate, and enqueue the next hop. The web console has matching **Roles** and **Run next** controls.
- `arthur loop create` and the web **+ Loop** wizard create a project and its first queue job. Not a graph composer.
- Atlas Tasker: `arthur tracker next` / `walk` turn ready tickets into queue jobs.
- The queue is a state machine. Illegal transitions, duplicate job ids, and reused idempotency keys are refused, and ledger writes take a file lock.
- Capture validates the control block and opens a human decision (pausing that project) when it is invalid or asks for input. `arthur decision` and `arthur gate implementation` are CLI paths for both.
- Stability fixes from the last review pass: web Run next returns 409 instead of 500 on lock contention, hop prompts carry the expected marker so follow no longer stalls waiting for it, agent invoke waits 45 minutes by default (was 120 seconds; `--timeout` or `polling_policy.invoke_timeout_seconds`), and a second follower or Run next stops with `follow_in_flight` instead of running the same hop twice. Plus API/CLI edge cases and narrow-window layout fixes.

## Install

```bash
pipx install 'git+https://github.com/myrrazor/arthur-loop.git@v0.1.1'

# or
uv tool install 'git+https://github.com/myrrazor/arthur-loop.git@v0.1.1'

# or
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/v0.1.1/install.sh | ARTHUR_LOOP_REF=v0.1.1 sh
```

Drop `@v0.1.1` (and the `ARTHUR_LOOP_REF`) to track `main`. Then run `arthur init` and restart your coding agent so MCP connects. Python 3.9 or newer is required. `arthur --version` prints `arthur 0.1.1`.

ArthurBar, the macOS 14+ menu bar view, is a separate install: `curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/menubar/install.sh | sh`.

## Known edges

- This is an alpha. File formats are plain, but command names, MCP tool names, and adapter contracts may still change before 1.0.
- Do not run `arthur follow` (or MCP `arthur.follow.run`) unattended against untrusted input yet. Eight medium-severity gaps are known and slated for v0.1.2:
  - the artifacts `project` parameter is not confined to the instance;
  - MCP follow `max_steps` has no upper cap;
  - `queue create` does not validate `project_id`;
  - poll-result can force-complete a job;
  - a job with no `expected_marker` can auto-complete;
  - CLI and web accept a `prompt_path` outside the instance;
  - follow consuming an escaped `prompt_path` can leave the job claimed;
  - a torn JSONL line (for example after a crash mid-write) fails closed: the whole queue refuses to load until the line is repaired.
- Follow does not skip human gates: open decisions, ChatGPT-browser and manual replies, and a NO-GO implementation gate still wait for you.
- The Python CLI is exercised on macOS and Linux. `install.ps1` is experimental; Windows is not supported yet.
- ArthurBar ships as source. It is not a signed or notarized app bundle.

The release-prep gate passed 267 Python tests (`unittest` and `pytest`, fresh venv). ArthurBar Swift tests run in CI on macOS. See [CHANGELOG.md](CHANGELOG.md) for the full change list.
