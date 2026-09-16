# The macOS menu bar

ArthurBar is a native **menu extra** for macOS 14+. It lives in the
[menu bar](https://support.apple.com/guide/mac-help/whats-in-the-menu-bar-mchlp1446/mac)
— the strip at the top of the screen — so you can watch agent loops without
keeping a terminal or the localhost console in front of you.

<p align="center">
  <img src="assets/arthurbar-demo.png" alt="ArthurBar menu bar popover: workers, queue, a blocked project, a human decision, and quota at 62 percent left" width="440">
</p>

It is read-only. Data comes from `arthur status --json`. The app never writes
queue state.

## What you see

An ∞ icon appears among the other menu extras, typically on the right next to
Control Center and the clock.

- **Badge.** The number next to the icon is how many things need a human: open
  decisions plus stale jobs. Zero hides the number. Hover the item for the loop
  state (`POLL_DUE`, `HUMAN_INPUT_REQUIRED`, `BLOCKED_BY_QUOTA`, …).
- **Workers, queue, projects.** The popover lists reporting sessions, ready or
  overdue jobs, and which project is blocked.
- **Needs you.** Open decisions show up by title so you can see which project
  is waiting without opening the console first.
- **Quota.** The bar is the loop’s governor: percent left, reset time, and
  GREEN / YELLOW / RED. Point the governor at [CodexBar](https://github.com/steipete/CodexBar)
  (Codex, Claude, and the other subscriptions CodexBar already reads), or at a
  command or file. The loop stores a primary and a secondary remaining percent
  when the payload has both; ArthurBar shows the primary bar, the same meter
  `arthur status` prints. At your reserve line (5% by default) the loop
  checkpoints instead of starting new work.
- **Browser lock.** Whether the advisor browser lease is free, held, or stale.

## Alerts

The menu extra is the glance surface. For banners, run `arthur watch` in a spare
pane or from cron with `--once`. That posts to Notification Center (macOS
`osascript`) when a human decision opens, quota blocks, work goes due, or a job
goes stale — never on repeats.

```bash
arthur watch                       # ping me when the loop needs a human
arthur watch --once --no-desktop   # cron-friendly check that only prints
```

## Build and run

Requires macOS 14+ and a Swift toolchain (Xcode or Command Line Tools):

```bash
cd menubar/ArthurBar
swift build -c release
.build/release/ArthurBar --root ~/my-loop
```

ArthurBar ships as source. It is not a signed or notarized app bundle.

Config, login-item setup, and snapshot rendering live in
[menubar/ArthurBar/README.md](../menubar/ArthurBar/README.md).
