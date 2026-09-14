# Install honesty

The last tagged release is **v0.1.0**. `arthur --version` reports that package version until the next tag. This repository's default git install (the three commands on the README and launch site) tracks `main`.

## This branch (Atlas product-gap)

Unreleased work on `cursor/atlas-product-gap-ab6b` is **not** the v0.1.0 tarball. Install this tip:

```bash
pipx install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'
# or
uv tool install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'
# or from a checkout of this branch
./install.sh
```

Then `arthur --version` still prints `arthur 0.1.0` (setuptools metadata). That is the last release number, not a claim that you installed the tagged commit.

## Grok

```bash
cd <instance>
arthur init --yes --main-agent grok --preset solo
# or
arthur integrations install --targets grok
arthur integrations probe --target grok
# when grok is installed and logged in:
grok mcp list
grok inspect --json
grok -p --always-approve "List MCP tools named arthur_* then call arthur_status"
```

Install writes the skill to `.grok/skills/arthur-loop` (what Grok Build scans) and runs `grok mcp add` when `grok` is on PATH.

## `--force`

`arthur integrations install --force` refreshes managed `<!-- arthur-loop:* -->` blocks. It does **not** replace the rest of `AGENTS.md`.
