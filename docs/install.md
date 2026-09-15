# Install honesty

The last tagged release is **v0.1.0**. There is no tagged v0.2.0. `arthur --version` reports `arthur 0.1.0` until the next tag. This repository's default git install (the three commands on the README and launch site) tracks `main`.

That public tree is a **file cockpit**: `arthur init`, queue, capture, gate, `status`, `arthur web`. It does **not** include `arthur follow`, the stdio MCP server, client skills/integrations, or Atlas `next`/`walk`. It is not a working auto-follow in minutes.

Canonical site: [https://arthurloop.com/](https://arthurloop.com/). `arthur-loop.vercel.app` 404s — do not use it as canonical.

`https://raw.githubusercontent.com/myrrazor/arthur-loop/main/install.ps1` is a **404**. This branch ships an experimental `install.ps1` in the repo; run it from a checkout. Do not advertise the `/main/install.ps1` URL.

## This branch (Atlas product-gap)

Unreleased work on `cursor/atlas-product-gap-ab6b` is **not** the v0.1.0 tarball. Install this tip:

```bash
pipx install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'
# or
uv tool install 'git+https://github.com/myrrazor/arthur-loop.git@cursor/atlas-product-gap-ab6b'
# or from a checkout of this branch
./install.sh
```

Then `arthur --version` still prints `arthur 0.1.0` (setuptools metadata). That is the last release number, not a claim that you installed the tagged commit. Prove the tree with `arthur follow --help` / `arthur mcp --help`.

```bash
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/cursor/atlas-product-gap-ab6b/install.sh \
  | ARTHUR_LOOP_REF=cursor/atlas-product-gap-ab6b sh
```

## Grok

Grok Build 1.0.30 prompt flag order is load-bearing: `--always-approve` **before** `-p`. `grok -p --always-approve` does not run the prompt (proved with `pong`).

Folder trust is a second gate. An untrusted workspace does not spawn project MCP (`arthur_status` stays disconnected). `arthur integrations install --targets grok` documents this and, unless you pass `--no-trust-folder`, runs `grok --trust` from the instance root.

```bash
cd <instance>
arthur init --yes --main-agent grok --preset solo
# or
arthur integrations install --targets grok
arthur integrations probe --target grok
# when grok is installed and logged in:
grok --trust
grok mcp list
grok inspect --json
grok --always-approve -p "List MCP tools named arthur_* then call arthur_status"
```

Install writes the skill to `.grok/skills/arthur-loop` (what Grok Build scans) and runs `grok mcp add` when `grok` is on PATH.

## `--force`

`arthur integrations install --force` refreshes managed `<!-- arthur-loop:* -->` blocks. It does **not** replace the rest of `AGENTS.md`.
