#!/bin/sh
# Arthur Loop one-command installer.
#
#   curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/install.sh | sh
#
# Also works from a local checkout:  ./install.sh
#
# Layout: the app lives in an isolated venv under $ARTHUR_LOOP_HOME (default
# ~/.arthur-loop) with a symlink at $ARTHUR_LOOP_BIN/arthur (default
# ~/.local/bin). Uninstall = remove both.
#
# Env overrides: ARTHUR_LOOP_REPO (git URL), ARTHUR_LOOP_HOME, ARTHUR_LOOP_BIN.
set -eu

REPO="${ARTHUR_LOOP_REPO:-https://github.com/myrrazor/arthur-loop.git}"
DEST="${ARTHUR_LOOP_HOME:-$HOME/.arthur-loop}"
BIN_DIR="${ARTHUR_LOOP_BIN:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 is required (3.9 or newer)"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail "python3 >= 3.9 is required (found $(python3 --version 2>&1))"

# running inside a checkout? install from here instead of cloning
if [ -f pyproject.toml ] && grep -q 'name = "arthur-loop"' pyproject.toml 2>/dev/null; then
  SRC="$(pwd)"
  say "Installing Arthur Loop from this checkout: $SRC"
else
  command -v git >/dev/null 2>&1 || fail "git is required to fetch the repo"
  if [ -d "$DEST/src/.git" ]; then
    say "Updating existing checkout at $DEST/src"
    git -C "$DEST/src" pull --ff-only --quiet || say "warning: could not fast-forward $DEST/src; installing what's there"
  else
    say "Cloning $REPO -> $DEST/src"
    mkdir -p "$DEST"
    git clone --depth 1 --quiet "$REPO" "$DEST/src"
  fi
  SRC="$DEST/src"
fi

# an isolated venv sidesteps the stock-macOS `pip --user` PEP 517 bug entirely
VENV="$DEST/venv"
say "Creating venv at $VENV"
python3 -m venv --clear "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null
say "Installing arthur-loop (this pulls one dependency: rich)..."
"$VENV/bin/python" -m pip install --quiet "$SRC"

[ -x "$VENV/bin/arthur" ] || fail "install finished but $VENV/bin/arthur is missing"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/arthur" "$BIN_DIR/arthur"

"$BIN_DIR/arthur" --help >/dev/null 2>&1 || fail "arthur failed its smoke run"

say ""
say "Arthur Loop installed: $BIN_DIR/arthur -> $VENV/bin/arthur"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    say ""
    say "note: $BIN_DIR is not on your PATH. Add it, e.g.:"
    say "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

say ""
say "Next:"
say "  mkdir my-loop && cd my-loop"
say "  arthur init                # detects your agent CLIs, picks a loop preset, seeds your main agent"
say "  arthur init --yes --demo   # or: see a busy example loop immediately (no questions asked)"
say "  arthur status"
