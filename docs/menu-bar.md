# The macOS menu bar

ArthurBar is a **Mac-only** extra. It puts the loop's **quota meter** in the
[menu bar](https://support.apple.com/guide/mac-help/whats-in-the-menu-bar-mchlp1446/mac)
so you can see how much subscription the agents have left without opening the
console. Percent remaining, reset time, GREEN / YELLOW / RED. At the reserve
line the loop checkpoints instead of starting new work. The same popover shows
workers, the queue, and a badge when a human decision or stale job needs you.

<p align="center">
  <img src="assets/arthurbar-demo.png" alt="ArthurBar macOS menu extra: quota at 62 percent left, SAMPLE_APP blocked, workers and a ready queue job" width="440">
</p>

pipx, uv, and `./install.sh` **do not** install ArthurBar.

## Install (macOS 14+ only)

You need `arthur` already installed, plus Xcode or Command Line Tools. There is
no Homebrew cask or signed `.app` yet — this installer compiles ArthurBar on
your Mac.

```bash
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/menubar/install.sh | sh
ArthurBar --root ~/my-loop
```

From a checkout:

```bash
./menubar/install.sh --root ~/my-loop
./menubar/install.sh --root ~/my-loop --login   # start at login
```

The binary lands at `~/.local/bin/ArthurBar`. Uninstall:

```bash
rm ~/.local/bin/ArthurBar
rm ~/Library/LaunchAgents/dev.arthurloop.arthurbar.plist   # if you used --login
launchctl bootout "gui/$(id -u)/dev.arthurloop.arthurbar" 2>/dev/null
```

`--root` writes `~/.config/arthurbar/config.json` so later you can run
`ArthurBar` with no flags. Manual `swift build` still works; see
[menubar/ArthurBar/README.md](../menubar/ArthurBar/README.md).

## Quota

The quota bar is the loop's governor, not a decoration. It is the same meter
`arthur status` prints. Point the governor at
[CodexBar](https://github.com/steipete/CodexBar) (Codex, Claude, and the other
subscriptions CodexBar already reads), or at a command or file. The snapshot
can carry a primary and a secondary remaining percent; ArthurBar shows the
primary bar. At 5% remaining (the default reserve) the tick answers
`BLOCKED_BY_QUOTA` and new work does not start.

## What else you see

- **Badge.** Open decisions plus stale jobs. Zero hides the number. Hover for
  the loop state (`POLL_DUE`, `HUMAN_INPUT_REQUIRED`, `BLOCKED_BY_QUOTA`, …).
- **Workers, queue, projects.** Reporting sessions, ready or overdue jobs,
  which project is blocked.
- **Needs you.** Open decisions by title.
- **Browser lock.** Free, held, or stale.

It reads `arthur status --json` and does not change loop state.

## Alerts

The menu extra is the glance surface. For banners, `arthur watch` posts to
Notification Center when a decision opens, quota blocks, work goes due, or a
job goes stale — never on repeats.

```bash
arthur watch
arthur watch --once --no-desktop
```
