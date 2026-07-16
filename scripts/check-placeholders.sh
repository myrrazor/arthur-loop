#!/usr/bin/env bash
# Launch-readiness gate: no unfilled placeholders may reach production.
#
# Scans a directory (default: current) for the two placeholder markers the
# product-website / product-launch skills use, and blocks launch until both are clear:
#
#   {{TOKEN}}        a REQUIRED blank. Must be filled. Any remaining → hard fail.
#   TODO(launch):    a working SAMPLE you must confirm (donate URL, favicon, accent).
#                    Resolve each by replacing it OR deleting the TODO(launch) line
#                    to acknowledge you're keeping the sample. Any remaining → fail.
#
# Exit codes:  0 = clean (safe to launch)   1 = unresolved TODO(launch)   2 = unfilled {{}}
#
# Usage:  ./check-placeholders.sh [dir-or-file]
# Meant to run against your built site / product repo (e.g. docs/), not this skills repo.
set -uo pipefail

target="${1:-.}"

# where to look; skip the junk that would create noise or false hits
common_excludes=(--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=dist
  --exclude-dir=.next --exclude-dir=vendor --exclude=check-placeholders.sh
  --exclude=*.png --exclude=*.jpg --exclude=*.jpeg --exclude=*.gif --exclude=*.webp
  --exclude=*.ico --exclude=*.pdf --exclude=*.lock)

# {{TOKEN}} = uppercase token in double braces; deliberately does NOT match
# Go templates ({{ .Version }}), JSX ({{color:...}}) etc. — those aren't all-caps tokens.
blank_re='\{\{[A-Z][A-Z0-9_]*\}\}'
todo_re='TODO\(launch\)'

blanks="$(grep -rInE "${common_excludes[@]}" "$blank_re" "$target" 2>/dev/null || true)"
todos="$(grep -rInE "${common_excludes[@]}" "$todo_re" "$target" 2>/dev/null || true)"

fail=0

if [ -n "$blanks" ]; then
  echo "✗ UNFILLED PLACEHOLDERS — these {{TOKENS}} must be filled before launch:"
  echo "$blanks" | sed 's/^/    /'
  echo
  fail=2
fi

if [ -n "$todos" ]; then
  echo "⚠ UNCONFIRMED SAMPLES — resolve each (replace it, or delete the TODO(launch) line):"
  echo "$todos" | sed 's/^/    /'
  echo
  [ "$fail" -eq 0 ] && fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "✓ no placeholders or unconfirmed samples in '$target' — clear to launch."
else
  echo "Not launch-ready. Fix the items above and re-run: $0 $target"
fi
exit "$fail"
