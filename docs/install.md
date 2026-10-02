# Install honesty

The latest tagged release is **v0.1.1**. `arthur --version` reports `arthur 0.1.1`. v0.1.1 includes coding-agent integrations (skills + MCP registration), the stdio MCP server, `arthur follow`, assignable roles, Atlas `next`/`walk`, and the web create-loop wizard. See [RELEASE_NOTES_v0.1.1.md](../RELEASE_NOTES_v0.1.1.md).

The three default git install commands on the README and launch site track `main`, which is v0.1.1 or newer. `arthur --version` reports the version in `pyproject.toml`, so a `main` install between tags still says `arthur 0.1.1`.

Canonical site: [https://arthurloop.com/](https://arthurloop.com/). Do not use `arthur-loop.vercel.app` as canonical.

`install.ps1` is an experimental Windows installer. Run it from a checkout. Windows is not a supported platform yet.

## Pin the tag

```bash
pipx install 'git+https://github.com/myrrazor/arthur-loop.git@v0.1.1'
# or
uv tool install 'git+https://github.com/myrrazor/arthur-loop.git@v0.1.1'
# or
curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/v0.1.1/install.sh \
  | ARTHUR_LOOP_REF=v0.1.1 sh
```

Check the tree with `arthur --version`, `arthur follow --help`, and `arthur mcp --help`.

Do not run `arthur follow` unattended against untrusted input yet. Known gaps are listed in the release notes and are slated for v0.1.2.

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
