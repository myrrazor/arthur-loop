# X thread draft

Attach `docs/assets/arthur-loop-demo.gif` to post 1.

## 1/5

Long coding-agent loops rarely fail because a model forgot how to code.

They fail at the seams: duplicate prompts after a crash, imaginary approvals, one blocked question freezing every project, quota burned while idle.

So I built Arthur Loop.

## 2/5

Arthur Loop is a file-first control plane for AI dev loops.

One agent plans/reviews. Another implements. Arthur keeps the queue, artifacts, approval gates, human decisions, and next scheduler action durable and visible.

## 3/5

No daemon. No database. No account. No API key required by the core.

State is markdown + JSONL in a directory you own. Bring a browser advisor, CLI agent, API model, manual workflow, custom tracker, or no tracker at all.

## 4/5

The intentionally boring choice was the important one: save advisor output before acting on it, parse only the final control block, quarantine invalid decisions, and never expose agent-owned transitions as shiny dashboard buttons.

The seams stay inspectable.

## 5/5

Arthur Loop v0.1.0 is free and MIT licensed.

Demo + install: https://arthur-loop.vercel.app/

Source: https://github.com/myrrazor/arthur-loop

If it is useful, star it. If a trust boundary looks wrong, tell me.
