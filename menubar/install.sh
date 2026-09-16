#!/bin/sh
# ArthurBar — macOS menu extra installer (not the arthur CLI).
#
#   curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/menubar/install.sh | sh
#
# From a checkout:  ./menubar/install.sh
#
# Builds ArthurBar from source and links ~/.local/bin/ArthurBar.
# pipx / uv / ./install.sh do not install this. You still need `arthur` first.
#
# Flags: --root PATH   write ~/.config/arthurbar/config.json for that instance
#        --login       start at login (LaunchAgent)
#        --no-start    install the binary only; do not print a run command
#
# Env: ARTHUR_LOOP_REPO, ARTHUR_LOOP_REF, ARTHURBAR_BIN (default ~/.local/bin),
#      ARTHURBAR_HOME (clone dest when not in a checkout; default ~/.arthurbar).
set -eu

REPO="${ARTHUR_LOOP_REPO:-https://github.com/myrrazor/arthur-loop.git}"
REF="${ARTHUR_LOOP_REF:-}"
BIN_DIR="${ARTHURBAR_BIN:-$HOME/.local/bin}"
CLONE="${ARTHURBAR_HOME:-$HOME/.arthurbar}"
ROOT_FLAG=""
LOGIN=0
NO_START=0

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --root) [ $# -ge 2 ] || fail "--root needs a path"; ROOT_FLAG="$2"; shift 2 ;;
    --login) LOGIN=1; shift ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage ;;
    *) fail "unknown argument: $1 (try --help)" ;;
  esac
done

[ "$(uname -s)" = Darwin ] || fail "ArthurBar is macOS 14+ only (found $(uname -s))"

command -v swift >/dev/null 2>&1 || fail "swift is required. Install Xcode or Command Line Tools: xcode-select --install"
xcode-select -p >/dev/null 2>&1 || fail "Xcode or Command Line Tools missing. Run: xcode-select --install"

# checkout with the Swift package? use it. else reuse a CLI curl clone. else fetch.
if [ -f menubar/ArthurBar/Package.swift ]; then
  SRC="$(pwd)"
  say "Building ArthurBar from this checkout: $SRC"
elif [ -f "$HOME/.arthur-loop/src/menubar/ArthurBar/Package.swift" ]; then
  SRC="$HOME/.arthur-loop/src"
  say "Building ArthurBar from $SRC"
else
  command -v git >/dev/null 2>&1 || fail "git is required to fetch the repo"
  if [ -d "$CLONE/src/.git" ]; then
    say "Updating existing checkout at $CLONE/src"
    git -C "$CLONE/src" pull --ff-only --quiet || say "warning: could not fast-forward $CLONE/src; building what's there"
  else
    say "Cloning $REPO${REF:+ (ref $REF)} -> $CLONE/src"
    mkdir -p "$CLONE"
    if [ -n "$REF" ]; then
      git clone --depth 1 --branch "$REF" --quiet "$REPO" "$CLONE/src"
    else
      git clone --depth 1 --quiet "$REPO" "$CLONE/src"
    fi
  fi
  SRC="$CLONE/src"
fi

PKG="$SRC/menubar/ArthurBar"
[ -f "$PKG/Package.swift" ] || fail "no ArthurBar package at $PKG"

say "swift build -c release (first run downloads the toolchain bits; later runs are short)"
swift build -c release --package-path "$PKG"
BAR="$(swift build -c release --package-path "$PKG" --show-bin-path)/ArthurBar"
[ -x "$BAR" ] || fail "build finished but $BAR is missing"

mkdir -p "$BIN_DIR"
install -m 755 "$BAR" "$BIN_DIR/ArthurBar"
[ -x "$BIN_DIR/ArthurBar" ] || fail "install finished but $BIN_DIR/ArthurBar is missing"

if [ -n "$ROOT_FLAG" ]; then
  mkdir -p "$HOME/.config/arthurbar"
  # keep this a tiny JSON file the app already knows how to read
  printf '%s\n' "{
  \"instances\": [
    { \"name\": \"$(basename "$ROOT_FLAG")\", \"root\": \"$ROOT_FLAG\" }
  ],
  \"refresh_seconds\": 30
}" > "$HOME/.config/arthurbar/config.json"
  say "Wrote $HOME/.config/arthurbar/config.json -> $ROOT_FLAG"
fi

if [ "$LOGIN" -eq 1 ]; then
  PLIST="$HOME/Library/LaunchAgents/dev.arthurloop.arthurbar.plist"
  ABS="$BIN_DIR/ArthurBar"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.arthurloop.arthurbar</string>
  <key>ProgramArguments</key><array><string>$ABS</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)/dev.arthurloop.arthurbar" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  say "Start-at-login: $PLIST"
fi

say ""
say "ArthurBar installed: $BIN_DIR/ArthurBar"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    say ""
    say "note: $BIN_DIR is not on your PATH. Add it, e.g.:"
    say "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

command -v arthur >/dev/null 2>&1 || [ -x "$HOME/.local/bin/arthur" ] || {
  say ""
  say "note: arthur is not on PATH. ArthurBar reads \`arthur status --json\`."
  say "      Install the CLI first (pipx, uv, or ./install.sh), then rerun ArthurBar."
}

if [ "$NO_START" -eq 0 ]; then
  say ""
  say "Next (macOS only — this is not in the pipx/uv/curl CLI install):"
  if [ -n "$ROOT_FLAG" ]; then
    say "  ArthurBar"
  else
    say "  ArthurBar --root ~/my-loop"
  fi
  say "An ∞ icon appears in the menu bar. Click it for quota, workers, and the queue."
fi
