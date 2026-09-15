#!/usr/bin/env bash
# Rebuild docs/assets/how-it-works.mp4 from title cards + shipped screenshots.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/assets/how-it-works.mp4"
WORK="$(mktemp -d)"
FONT="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
trap 'rm -rf "$WORK"' EXIT

card() {
  local name="$1" seconds="$2"
  shift 2
  printf '%s\n' "$@" > "$WORK/${name}.txt"
  ffmpeg -y -hide_banner -loglevel error -f lavfi -i "color=c=0x0d1117:s=1280x720:d=${seconds}" \
    -vf "drawtext=fontfile=${FONT}:textfile=${WORK}/${name}.txt:fontsize=36:fontcolor=0xf0f6fc:line_spacing=12:x=(w-text_w)/2:y=(h-text_h)/2" \
    -pix_fmt yuv420p "$WORK/${name}.mp4"
}

still() {
  local name="$1" src="$2" seconds="$3"
  ffmpeg -y -hide_banner -loglevel error -loop 1 -t "$seconds" -i "$src" \
    -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x0d1117" \
    -pix_fmt yuv420p "$WORK/${name}.mp4"
}

card intro 3 "Arthur Loop" "How it works"
card grok 4 "1. Grok actually loads Arthur" ".grok/skills/arthur-loop" "grok mcp add --scope project" "arthur_status  /  arthur_follow_run"
still wizard "$ROOT/docs/assets/create-loop-wizard.png" 4
card follow 4 "2. Auto-follow" "arthur follow --once" "claim → invoke → submit → capture → gate"
card atlas 4 "3. Atlas next / walk" "arthur tracker next --json" "arthur tracker walk" "in_review is not ready work"
still console "$ROOT/docs/assets/web-console.png" 4
card honest 4 "4–5. Honest install + this video" "v0.1.0 is the last tag" "this branch is unreleased" "human gates: decisions, browser, NO-GO"

cat > "$WORK/list.txt" <<EOF
file 'intro.mp4'
file 'grok.mp4'
file 'wizard.mp4'
file 'follow.mp4'
file 'atlas.mp4'
file 'console.mp4'
file 'honest.mp4'
EOF

ffmpeg -y -hide_banner -loglevel error -f concat -safe 0 -i "$WORK/list.txt" \
  -c:v libx264 -pix_fmt yuv420p -crf 28 -preset veryfast -movflags +faststart \
  "$OUT"
ls -la "$OUT"
