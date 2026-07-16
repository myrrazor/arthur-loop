# Show HN

## Title

Show HN: Arthur Loop – file-first control plane for AI dev loops

## First comment

Hi HN — the problem I wanted to solve was not getting another model to write code. It was keeping long-running agent work honest after a crash, a review handoff, a browser lock, a quota pause, or a question only a human can answer.

Arthur Loop lets one agent plan/review and another implement, while it keeps the control plane in ordinary files: an append-only job queue, saved advisor artifacts, explicit approval gates, project-scoped human decisions, and a scheduler tick that says exactly why work should run or wait. You can use browser, CLI, API, or entirely manual adapters.

Tech: Python 3.9+, Rich for the terminal dashboard, a stdlib HTTP server plus vanilla JS for the localhost console, and an optional SwiftUI menu bar reader. There is no daemon or database, and the core needs no API key.

It is free and open source under MIT. v0.1.0 is an alpha, and Windows/browser-adapter edges are called out plainly in the release notes.

I would especially value feedback on two things: whether the final-control-block trust boundary is strict enough, and whether the file-first state model stays understandable once you run several projects at once.

Site: https://arthur-loop.vercel.app/

Source: https://github.com/myrrazor/arthur-loop
