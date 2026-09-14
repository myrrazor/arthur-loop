# Arthur Loop MCP

`arthur mcp serve` is a local stdio MCP server. It is not a second source of truth. Every tool calls the same Python functions as the CLI.

```bash
arthur mcp serve --root ~/my-loop
arthur mcp serve --tool-name-style portable   # Grok: arthur_status, not arthur.status
arthur mcp tools --json
arthur mcp call arthur.status
```

Stdio accepts newline-delimited JSON-RPC and LSP-style `Content-Length` frames.

## What is shipped

**Read:** `arthur.status`, `arthur.tick`, `arthur.queue.show`, `arthur.queue.due`, `arthur.gate.implementation`, `arthur.decision.list`, `arthur.loop.list`, `arthur.board`.

**Gated write:** `arthur.queue.create`, `arthur.queue.claim`, `arthur.queue.submit`, `arthur.queue.poll_result`, `arthur.queue.recover`, `arthur.capture`, `arthur.decision.open`, `arthur.decision.answer`, `arthur.loop.create`, `arthur.board.open_jobs`.

Writes go through the same gates as the CLI (queue state machine, paused-project claim refusal, empty idempotency keys, marker collision, implementation gate on capture). This is not Atlas's high-impact approval ledger. There is no `approve-operation` flow.

The `arthur-loop` MCP prompt (and the `/arthur-loop` slash command) interviews for advisor / executor / tracker and tells the agent to call `arthur.loop.create`.

## What is not shipped

- Resources / subscriptions / MCP Apps HTML boards
- High-impact approval IDs
- Claiming that a written client config means the server is connected

`arthur integrations install` writes the client config. Restart the client. Check that client's MCP UI.
