# Screen brief — launch site

## Mode

Shape → revamp → harden, followed by anti-slop gate mode.

## User and job

- **User:** Developer coordinating coding agents.
- **Question:** Will this keep my loop recoverable and reviewable without locking me into a hosted stack?
- **Primary action:** Copy an install command.
- **Success:** Understand the control mechanism, choose an install path, reach `arthur init`.
- **Failure cost:** A broken command or inflated claim spends launch-day trust.
- **Inputs:** Keyboard, pointer, touch, screen reader.

## Hierarchy

1. Arthur Loop holds long-running agent work to a written contract.
2. It stores state as files and works with the user’s existing tools.
3. The terminal, web-console, and macOS menu-bar captures prove the current product.
4. pipx, uv, and curl are equal install paths.
5. FAQ answers storage, platform, menu bar, update, and scope objections.

## Responsive behavior

- **Compact:** Single-column hero, hidden secondary nav links, stacked feature/install rows, locally scrolling commands.
- **Medium:** Single-column content with wider contract and media.
- **Expanded:** Two-column hero and section split; screenshots use the available reading width without exceeding 75rem.

## State matrix

| State | Behavior | Recovery |
| --- | --- | --- |
| Static load | Complete content and local media render without JavaScript | None required |
| Missing media | Descriptive alt text preserves meaning | Fix deployment asset path |
| Copy success | Button reads “Copied” for 1.6 seconds | Label restores automatically |
| Clipboard unavailable | Legacy copy fallback runs | Command text is selected if copying still fails |
| Reduced motion | Smooth scrolling and transitions are removed | All status remains visible |
| Long command | Scrolls inside the command row | Page never scrolls horizontally |

## Acceptance criteria

- Real content and media; no fabricated proof.
- Valid semantic landmarks, one H1, native FAQ controls, visible focus.
- 390px and 1280px screenshots reviewed in dark and light schemes.
- No console errors, failed local requests, unresolved launch placeholders, or anti-slop strict findings.
- HTML, JSON-LD, sitemap, robots, and llms files parse or validate locally.
