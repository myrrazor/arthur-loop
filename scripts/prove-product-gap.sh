#!/usr/bin/env bash
# Hostile-retest proof commands for the five remaining product gaps.
# Run from a throwaway instance after: pip install -e .  (or this branch)
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

echo "== 1. Grok load =="
echo "Skill must be at .grok/skills/arthur-loop (not .arthur/integrations/):"
test -f .grok/skills/arthur-loop/SKILL.md
arthur integrations status --json | python3 -c "import json,sys; rows=json.load(sys.stdin); g=next(r for r in rows if r['target']=='grok'); print(g); assert g['skill_path'].startswith('.grok/skills')"
echo "Native add argv:"
python3 -c "from arthur_loop.grok_client import mcp_add_command; print(' '.join(mcp_add_command()))"
if command -v grok >/dev/null; then
  arthur integrations probe --target grok || true
  grok mcp list || true
  grok inspect --json || true
  echo "Live session (needs login):"
  echo "  grok --always-approve -p 'List MCP tools named arthur_* then call arthur_status'"
  echo "  grok --trust   # untrusted folder = project MCP disconnected"
else
  echo "grok not on PATH — files are on the scan path; run grok mcp add after install."
fi

echo "== 2. Auto-follow =="
arthur loop create --project-id PROVE --advisor manual --executor manual --goal prove || true
arthur follow --once --dry-run
echo "With a CLI adapter (grok/claude/codex) follow invokes it. Manual writes runtime/follow/*.inbox.md"

echo "== 3. Atlas next/walk =="
echo "arthur tracker next --json"
echo "arthur tracker queue --json"
echo "arthur tracker walk --dry-run"
echo "Decision Atlas key: init --project-id / loop create writes MY_APP → MY; or set tracker.project_map"
arthur tracker next --json || true

echo "== 4. Install honesty =="
arthur --version
echo "This branch: pipx install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'"
echo "--force must keep AGENTS.md house rules (see tests/test_integrations.py)"

echo "== 5. Video + wizard =="
test -f "$(dirname "$0")/../docs/assets/how-it-works.mp4" || echo "how-it-works.mp4 missing until docs/assets is built"
test -f "$(dirname "$0")/../site/create-loop-wizard.png"

echo "OK (commands above are the prove surface)."
