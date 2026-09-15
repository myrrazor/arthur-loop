# Coding-agent integrations

Install the `arthur` CLI, then in an instance:

```bash
arthur init                  # detects clients and writes skills + MCP unless --no-integrations
arthur integrations detect
arthur integrations install --targets claude,codex,cursor,grok
arthur integrations status
arthur integrations probe --target grok
```

This is the Atlas Tasker-shaped path: detect CLIs on PATH, write skills where those tools actually load them, and register MCP. For Grok, registration is `grok mcp add` (trusted), not only a hand-written toml. Folder trust is a second gate: `grok --trust` (install runs it unless `--no-trust-folder`). An untrusted folder does not spawn project MCP.

## What each target writes

| Target | Instruction file | Skill / command | MCP |
| --- | --- | --- | --- |
| `claude` | `CLAUDE.md` (managed block) | `.claude/skills/arthur-loop/`, `.claude/commands/arthur-loop.md` (`/arthur-loop`) | `.mcp.json` |
| `codex` | `AGENTS.md` (codex markers) | `.codex/skills/arthur-loop/` | `.codex/config.toml` `[mcp_servers.arthur-loop]` |
| `cursor` | `AGENTS.md` (cursor markers) | `.cursor/skills/arthur-loop/` plus `.cursor/commands/arthur-loop.md` | `.cursor/mcp.json` |
| `grok` | `AGENTS.md` (grok markers) | **`.grok/skills/arthur-loop/`** (Grok Build's scan path) | `grok mcp add --scope project` when `grok` is on PATH; `.grok/config.toml` as fallback. Portable names: `arthur_status`. |
| `generic` | `AGENTS.md` (generic markers) | `.arthur/integrations/generic-agent-skill/` | `.arthur/integrations/arthur-mcp.json` |

Re-running install refreshes Arthur-owned files and managed blocks. House rules outside the markers stay put. `--force` does **not** wipe `AGENTS.md`.

Detection uses the binary on PATH and workspace markers. A home `~/.cursor` / `~/.grok` directory alone is not "found".

## Grok honesty

Grok Build has a shipped advisor and executor pack. Official load roots: `./.grok/skills/` (walked to the repo root) and `~/.grok/skills/`. Arthur does not put the skill under `.arthur/integrations/` and hope Grok finds it.

MCP: Grok skips dotted tool names. Install uses `--tool-name-style portable`. **Written toml ≠ trusted.** `grok mcp add` is what marks the server trusted. Prove:

```bash
arthur integrations probe --target grok --live
grok --trust
grok mcp list
grok --always-approve -p "List MCP tools named arthur_* then call arthur_status"
```

## Cursor honesty

Cursor is an integration target (skills + MCP). It is not a shipped advisor/executor pack. `solo`/`pair` refuse it the same way they refuse Gemini.
