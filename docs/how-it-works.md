# How it works

Download → a working multi-agent loop in minutes. No daemon, no hosted dashboard.

1. **Install** `arthur` (pipx, uv, or `install.sh`).
2. **`arthur init`** in an empty directory. It detects Claude Code, Codex, Cursor, and Grok Build; writes adapter packs; writes skills and slash commands where those clients load them; registers `arthur mcp serve` in that client's config.
3. **Restart the coding agent.** A written MCP file is not a live connection.
4. **Create a loop** — terminal (`arthur loop create`), web wizard (`arthur web` → + Loop), or `/arthur-loop` / MCP `arthur.loop.create`. This creates a project and the first queue job. It is not a drag-drop graph composer.
5. **Agents follow the loop** through MCP or the CLI: claim → submit/capture → implementation gate. Humans answer decisions in the web console or `arthur decision answer`.
6. **If Atlas Tasker is installed**, `arthur tracker board --json` / MCP `arthur.board` reads ready/assigned work and `open-jobs` turns those tickets into queue jobs.

See the [workflow runbook](workflow-runbook.md), [MCP](mcp.md), and [integrations](integrations.md). Demo GIF: [arthur-loop-demo.gif](assets/arthur-loop-demo.gif). Web console shot: [web-console.png](assets/web-console.png).
