# Launch checklist — Arthur Loop

Run the automated gate anytime:

```bash
./scripts/check-placeholders.sh site/
./scripts/check-placeholders.sh README.md
./scripts/check-placeholders.sh docs/announcements/
```

## Placeholders and identity

- [x] Public launch surfaces contain no unresolved double-brace launch tokens or launch-TODO markers.
- [x] Donate link knowingly keeps the `mayurdarji.com` personal-site fallback.
- [x] Favicon uses the Arthur Loop prompt glyph.
- [x] Accent is the terminal-state green defined in `site/index.html`.
- [x] Product one-liner is byte-identical in the README, site description/hero, and release metadata packet.

## Repo

- [x] MIT `LICENSE` is present and names Mayur Darji.
- [x] Gitleaks scanned all local history with no leaks found.
- [x] `CHANGELOG.md` has a v0.1.0 entry.
- [ ] Set the GitHub description, topics, and Vercel homepage after the public repo exists.

## README and distribution

- [ ] Confirm the GitHub-rendered first screen after push.
- [x] Real terminal GIF is 321 KB; real web-console capture is 96 KB.
- [x] README workflows and clean local install equivalents pass.
- [x] `uv tool install` from a local path and local Git URL produces a working `arthur` command.
- [x] `install.sh` passes from outside the repo against a local Git remote.
- [x] `arthur --version` reports `arthur 0.1.0`.

## Release

- [ ] Push `dev` to the public repository and promote through the owner-approved branch gate.
- [ ] Create and push tag `v0.1.0`.
- [ ] Publish the GitHub release with `RELEASE_NOTES_v0.1.0.md` and prepared Python assets/checksums.
- [ ] Confirm the GitHub install URLs and latest-release URL return 200.

## Website

- [x] Static site, robots, sitemap, llms index, SVG identity, and real OG image are prepared.
- [x] JSON-LD parses locally and FAQ answers match the visible page.
- [ ] Confirm the production Vercel domain is `arthur-loop.vercel.app`; update canonical files if Vercel assigns a different domain.
- [ ] Deploy production and confirm the site, `robots.txt`, `sitemap.xml`, `llms.txt`, and `og.png` return 200.
- [ ] Submit the live domain and sitemap to Google Search Console; check the social card in an OG debugger.

## Announcements

- [x] Show HN, Reddit, and X drafts are complete under `docs/announcements/`.
- [x] Proposed channel sequence and dates are recorded.
- [ ] Recheck each community's self-promotion rules and live links before posting. Posting is the owner's call.

## GitHub-only follow-up

- [ ] Upload `docs/assets/og.png` as the repository social preview.
- [ ] Add repository topics: `python`, `cli`, `developer-tools`, `ai-agents`, `agentic-workflows`, `local-first`, `file-first`, `open-source`, `macos`, `automation`.
- [ ] Replace the personal-site support fallback if a dedicated Buy Me a Coffee URL is chosen later.
