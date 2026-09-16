# ArthurBar

A macOS menu extra for Arthur Loop: the whole loop — workers, queue, projects, human decisions, quota, browser lock — one click away from the menu bar, styled after [CodexBar](https://github.com/steipete/CodexBar) (whose MIT-licensed progress-bar and card patterns this app follows; thanks, Peter). Product write-up: [docs/menu-bar.md](../../docs/menu-bar.md).

## Build & run

Requires macOS 14+ and a Swift toolchain (Xcode or Command Line Tools):

```bash
cd menubar/ArthurBar
swift build -c release
.build/release/ArthurBar --root ~/my-loop
```

An `∞` icon appears in the menu bar; the number next to it is how many things need a human (open decisions + stale jobs). Click for the status card. It refreshes every 30 seconds, on open, and via the ↻ button.

## Configuration

`~/.config/arthurbar/config.json`:

```json
{
  "instances": [
    { "name": "my-loop", "root": "~/my-loop", "arthur": "~/.local/bin/arthur" }
  ],
  "refresh_seconds": 30
}
```

`arthur` is optional — the app probes `~/.local/bin`, `/opt/homebrew/bin`, and `/usr/local/bin` (GUI apps don't inherit your shell PATH). The first instance is shown; `--root`/`--name`/`--arthur` flags override for one-off runs.

## Start at login

```bash
cp .build/release/ArthurBar /usr/local/bin/  # or anywhere stable
cat > ~/Library/LaunchAgents/dev.arthurloop.arthurbar.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.arthurloop.arthurbar</string>
  <key>ProgramArguments</key><array><string>/usr/local/bin/ArthurBar</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/dev.arthurloop.arthurbar.plist
```

## Screenshots without permissions

`ArthurBar --snapshot out.png --root <instance>` renders the exact popover to a PNG via SwiftUI's ImageRenderer — no screen-recording permission needed. That's how the README screenshot is produced, so it can never drift from the real UI.

## Notes

- Data comes from `arthur status --json`; the app never touches loop state.
- `swift test` needs full Xcode (Command Line Tools ship without XCTest); the model tests run in CI or any Xcode install.
- Multi-instance display is a planned follow-up; the config format already accepts a list.
