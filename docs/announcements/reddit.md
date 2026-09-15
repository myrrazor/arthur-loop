# Reddit draft

Suggested communities: r/commandline or r/opensource after checking the current self-promotion rules.

## Title

I built a file-first control plane for long-running AI dev loops — free and open source

## Post

Long coding-agent runs kept exposing the same boring failures: a prompt sent twice after a crash, a plan treated as approved when it was not, a human question freezing unrelated work, or an agent quietly spending quota while nothing useful could move.

Arthur Loop keeps those handoffs as files you can inspect. One agent can plan and review, another can implement, and the loop gets an append-only queue, saved artifacts, strict approval gates, project-scoped human decisions, and a scheduler that explains whether to run, poll, wait, or stop.

What is in v0.1.0:

- Python CLI and Rich status dashboard
- local web console with a real advisor-to-human canvas
- agent detection and guided/solo/pair/browser/custom setup presets
- desktop notifications and optional macOS menu bar view
- optional CodexBar, command, or file-based quota input

The core has no daemon, database, account, or API-key requirement. State is markdown and JSONL under a directory you own. It is MIT licensed and still an alpha; macOS/Linux are exercised, while Windows and browser-adapter edges are documented rather than hand-waved.

Install with uv:

```bash
uv tool install git+https://github.com/myrrazor/arthur-loop.git
```

Demo and install options: https://arthurloop.com/

Source: https://github.com/myrrazor/arthur-loop

If you run multi-agent dev workflows, what state would you need to see before trusting an agent to resume after a crash?
