# How it works

Public `main` / last tag **v0.1.0** is a file cockpit: install `arthur`, `init`, then queue / capture / gate / status / `arthur web`. That is **not** a download-to-working-auto-follow. Those three default git commands do not ship `arthur follow`, MCP, or Atlas walk.

This branch adds coding-agent install, MCP, `arthur follow`, and Atlas next/walk. See [install.md](install.md) for the two install stories.

1. **Install this branch** (not the three default git commands on the README/site). Last tag is still v0.1.0; `arthur --version` will say that until the next tag.
2. **`arthur init`** in an empty directory. It detects Claude Code, Codex, Cursor, and Grok Build on **PATH** (not `~/.cursor` alone); writes adapter packs; writes skills where those clients load them. Grok's skill lands in `.grok/skills/arthur-loop`. When `grok` is on PATH, install runs `grok mcp add` and `grok --trust` (untrusted folder = project MCP does not spawn).
3. **Create a loop** — terminal (`arthur loop create`), web wizard (`arthur web` → + Loop), or `/arthur-loop` / MCP `arthur.loop.create`. This creates a project and the first queue job. It is not a drag-drop graph composer.
4. **Auto-follow** — `arthur follow` / MCP `arthur.follow.run` claims, invokes the CLI adapter, captures, gates, and enqueues the next hop. Grok's transport is `grok --always-approve -p PROMPT` (Grok Build 1.0.30; `-p --always-approve` is the wrong order). Humans still answer decisions and supply ChatGPT-browser / manual replies. See [follow.md](follow.md).
5. **Atlas next/walk** — `arthur tracker next --json` / `arthur tracker walk` (or MCP `arthur.tracker.next`). Ready/in_progress only; `in_review` is not opened. Arthur `project_id` is mapped to an Atlas key via `tracker.project_map` (empty `{}` after init until you fill it).

## Video and shots

- How-it-works video: [assets/how-it-works.mp4](assets/how-it-works.mp4)
- Terminal demo: [arthur-loop-demo.gif](assets/arthur-loop-demo.gif)
- Web console status map: [web-console.png](assets/web-console.png)
- Create-loop wizard: [create-loop-wizard.png](assets/create-loop-wizard.png)

See the [workflow runbook](workflow-runbook.md), [MCP](mcp.md), [integrations](integrations.md), and [install](install.md).
