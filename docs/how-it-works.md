# How it works

Latest tag **v0.1.1** (and current `main`) adds coding-agent install, MCP, `arthur follow`, assignable roles, and Atlas next/walk on top of the v0.1.0 file-based control plane (queue / capture / gate / status / `arthur web`). See [install.md](install.md) for pinning the tag.

1. **Install** with any of the three commands on the README/site (they track `main`), or pin `@v0.1.1`. `arthur --version` prints `arthur 0.1.1`.
2. **`arthur init`** in an empty directory. It detects Claude Code, Codex, Cursor, and Grok Build on **PATH** (not `~/.cursor` alone); writes adapter packs; writes skills where those clients load them. Grok's skill lands in `.grok/skills/arthur-loop`. When `grok` is on PATH, install runs `grok mcp add` and `grok --trust` (untrusted folder = project MCP does not spawn).
3. **Create a loop** — terminal (`arthur loop create`), web wizard (`arthur web` → + Loop), or `/arthur-loop` / MCP `arthur.loop.create`. This creates a project and the first queue job. It is not a drag-drop graph composer.
4. **Auto-follow** — `arthur follow` / MCP `arthur.follow.run` claims, invokes the CLI adapter, captures, gates, and enqueues the next hop. Grok's transport is `grok --always-approve -p PROMPT` (Grok Build 1.0.30; `-p --always-approve` is the wrong order). Humans still answer decisions and supply ChatGPT-browser / manual replies. See [follow.md](follow.md).
5. **Atlas next/walk** — `arthur tracker next --json` / `arthur tracker walk` (or MCP `arthur.tracker.next`). Ready/in_progress only; `in_review` is not opened. Arthur `project_id` maps to an Atlas key via `tracker.project_map`. Init with `--project-id` / `--demo` and `arthur loop create` write a default (`MY_APP` → `MY`). An empty map after init-with-no-projects is intentional — fill it before walk; the CLI prints that.

## Video and shots

- How-it-works video: [assets/how-it-works.mp4](assets/how-it-works.mp4)
- Terminal demo: [arthur-loop-demo.gif](assets/arthur-loop-demo.gif)
- Web console status map: [web-console.png](assets/web-console.png)
- Create-loop wizard: [create-loop-wizard.png](assets/create-loop-wizard.png)
- macOS menu extra: [arthurbar-demo.png](../assets/arthurbar-demo.png) — see [menu-bar.md](menu-bar.md)

See the [workflow runbook](workflow-runbook.md), [MCP](mcp.md), [integrations](integrations.md), and [install](install.md).
