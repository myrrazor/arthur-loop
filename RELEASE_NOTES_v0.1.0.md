# Arthur Loop v0.1.0

Arthur Loop is a file-first control plane for long-running AI development loops. One agent can plan and review, another can implement, and the handoffs stay durable: queued work, saved artifacts, explicit approval gates, project-scoped human decisions, and a scheduler that can explain what happens next.

This is the first public alpha.

## Highlights

- Append-only JSONL queue with idempotency keys, retry timing, stale-job detection, and recovery commands.
- Artifact capture before action, with strict parsing of the final control block and quarantine for invalid decisions.
- Interactive `arthur init` with agent detection, guided/solo/pair/browser/custom presets, demo state, and seeded kickoff files.
- Local web console with the live advisor-to-human canvas, queue/board views, artifact reading, activity history, and five human-owned actions.
- `arthur watch` and desktop notifications for human decisions, quota blocks, due work, and stale jobs.
- Optional quota sources: CodexBar auto-detection, a custom command, a JSON file, or none.
- ArthurBar, a read-only SwiftUI menu bar view for macOS 14 and newer.

## Install

```bash
pipx install git+https://github.com/myrrazor/arthur-loop.git

# or
uv tool install git+https://github.com/myrrazor/arthur-loop.git

# or
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/install.sh | sh
```

Then run `arthur init`. Python 3.9 or newer is required.

## Known edges

- This is an alpha release. The file formats are intentionally plain, but command names and adapter contracts may still tighten before 1.0.
- The Python CLI is exercised on macOS and Linux. Windows locks and browser adapters are not yet tested.
- The browser advisor is an optional runbook for a user-controlled session; browser UIs change, and users should review the terms of any service they automate.
- ArthurBar ships as source in this release. It is not a signed or notarized app bundle.

The release-prep gate passed 108 Python tests and 3 Swift tests. See [CHANGELOG.md](CHANGELOG.md) for the user-facing change list.
