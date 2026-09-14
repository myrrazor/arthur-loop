# How it works

Download → a working multi-agent loop in minutes. No daemon, no hosted dashboard.

1. **Install** `arthur` (pipx, uv, or `install.sh`). The last tag is v0.1.0; this branch is [docs/install.md](install.md).
2. **`arthur init`** in an empty directory. It detects Claude Code, Codex, Cursor, and Grok Build on **PATH** (not `~/.cursor` alone); writes adapter packs; writes skills where those clients load them. Grok's skill lands in `.grok/skills/arthur-loop`. When `grok` is on PATH, install runs `grok mcp add` so MCP is trusted.
3. **Create a loop** — terminal (`arthur loop create`), web wizard (`arthur web` → + Loop), or `/arthur-loop` / MCP `arthur.loop.create`. This creates a project and the first queue job. It is not a drag-drop graph composer.
4. **Auto-follow** — `arthur follow` / MCP `arthur.follow.run` claims, invokes the CLI adapter, captures, gates, and enqueues the next hop. Humans still answer decisions and supply ChatGPT-browser / manual replies. See [follow.md](follow.md).
5. **Atlas next/walk** — `arthur tracker next --json` / `arthur tracker walk` (or MCP `arthur.tracker.next`). Ready/in_progress only; `in_review` is not opened. Arthur `project_id` is mapped to an Atlas key via `tracker.project_map`.

## Video and shots

- How-it-works video: [assets/how-it-works.mp4](assets/how-it-works.mp4)
- Terminal demo: [arthur-loop-demo.gif](assets/arthur-loop-demo.gif)
- Web console status map: [web-console.png](assets/web-console.png)
- Create-loop wizard: [create-loop-wizard.png](assets/create-loop-wizard.png)

See the [workflow runbook](workflow-runbook.md), [MCP](mcp.md), [integrations](integrations.md), and [install](install.md).
