# Coding-agent integrations

Install the `arthur` CLI, then in an instance:

```bash
arthur init                  # detects clients and writes skills + MCP unless --no-integrations
arthur integrations detect
arthur integrations install --targets claude,codex,cursor,grok
arthur integrations status
```

This is the Atlas Tasker-shaped path: detect CLIs, write skills where those tools actually load them, and register MCP. Copying a markdown skill into `agent-setup/` alone is not enough.

## What each target writes

| Target | Instruction file | Skill / command | MCP config |
| --- | --- | --- | --- |
| `claude` | `CLAUDE.md` (managed block) | `.claude/skills/arthur-loop/`, `.claude/commands/arthur-loop.md` (`/arthur-loop`) | `.mcp.json` |
| `codex` | `AGENTS.md` (codex markers) | `.codex/skills/arthur-loop/` | `.codex/config.toml` `[mcp_servers.arthur-loop]` |
| `cursor` | `AGENTS.md` (cursor markers) | `.cursor/skills/arthur-loop/` | `.cursor/mcp.json` |
| `grok` | `AGENTS.md` (grok markers) | `.arthur/integrations/grok-agent-skill/` | `.grok/config.toml` with `--tool-name-style portable` |
| `generic` | `AGENTS.md` (generic markers) | `.arthur/integrations/generic-agent-skill/` | `.arthur/integrations/arthur-mcp.json` (portable descriptor) |

Re-running install refreshes Arthur-owned files and managed blocks. House rules outside the markers stay put. `--force` replaces the whole instruction file.

A written MCP entry is **written**, not connected. Restart Claude Code / Codex / Cursor / Grok and confirm in that client's MCP UI.

## Cursor honesty

Cursor is an integration target (skills + MCP). It is not a shipped advisor/executor pack. `solo`/`pair` refuse it the same way they refuse Gemini.

## Grok honesty

Grok Build has a shipped advisor and executor pack. MCP registration uses portable tool names because Grok skips dotted names (`arthur.status` → `arthur_status`).
