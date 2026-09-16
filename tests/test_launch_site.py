from __future__ import annotations

import html
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONE_LINER = (
    "File-first control plane for AI dev loops — bring your own advisor, executor, "
    "and tracker."
)
INSTALL_COMMANDS = (
    "pipx install git+https://github.com/myrrazor/arthur-loop.git",
    "uv tool install git+https://github.com/myrrazor/arthur-loop.git",
    "curl -fsSL https://raw.githubusercontent.com/myrrazor/arthur-loop/main/install.sh | sh",
)


class _LaunchPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.local_assets: set[str] = set()
        self.json_ld: list[str] = []
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("type") == "application/ld+json":
            self._json_parts = []

        for name in ("href", "src"):
            value = values.get(name)
            if not value or value.startswith(("http://", "https://", "mailto:")):
                continue
            value = value.split("?", 1)[0].split("#", 1)[0]
            if not value or value == "/":
                continue
            if value.startswith("/"):
                value = value[1:]
            self.local_assets.add(value)

    def handle_data(self, data: str) -> None:
        if self._json_parts is not None:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_parts is not None:
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = None


class LaunchSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "site/index.html").is_file():
            raise unittest.SkipTest("launch-site sources are not part of the Python sdist")
        cls.index = (ROOT / "site/index.html").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.parser = _LaunchPageParser()
        cls.parser.feed(cls.index)

    def test_site_has_valid_structured_data_and_matching_faq(self) -> None:
        documents = [json.loads(value) for value in self.parser.json_ld]
        faq_page = next(item for item in documents if item.get("@type") == "FAQPage")
        structured_faq = {
            item["name"]: item["acceptedAnswer"]["text"]
            for item in faq_page["mainEntity"]
        }

        visible_faq: dict[str, str] = {}
        for question, answer in re.findall(
            r"<details><summary>(.*?)</summary><p>(.*?)</p></details>",
            self.index,
            flags=re.DOTALL,
        ):
            clean_answer = re.sub(r"<[^>]+>", "", answer)
            visible_faq[html.unescape(question)] = html.unescape(clean_answer)

        self.assertEqual(structured_faq, visible_faq)
        self.assertGreaterEqual(len(documents), 2)
        self.assertTrue(any(item.get("@type") == "SoftwareApplication" for item in documents))

    def test_every_local_site_asset_exists(self) -> None:
        missing = []
        for asset in sorted(self.parser.local_assets):
            path = ROOT / "site" / asset
            if path.is_file():
                continue
            if path.is_dir() and (path / "index.html").is_file():
                continue
            missing.append(asset)
        self.assertEqual(missing, [])

    def test_launch_copy_and_install_commands_stay_in_sync(self) -> None:
        self.assertIn(ONE_LINER, self.readme)
        self.assertIn(ONE_LINER, self.index)
        self.assertIn("Star on GitHub", self.index)
        self.assertNotIn("Maintained at", self.index)
        self.assertNotIn(">@myrrazor</a>", self.index)
        self.assertNotIn("file cockpit", self.index)
        self.assertNotRegex(self.index, r'href="[^"]+"@')
        for command in INSTALL_COMMANDS:
            self.assertIn(command, self.readme)
            self.assertIn(command, self.index)

    def test_public_launch_files_do_not_leak_a_workstation_path(self) -> None:
        paths = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "RELEASE_NOTES_v0.1.0.md",
            ROOT / "LAUNCH_CHECKLIST.md",
            *sorted((ROOT / "docs/announcements").glob("*.md")),
            *sorted((ROOT / "docs/design").glob("*.md")),
            *sorted((ROOT / "site").glob("*")),
        ]
        leaked = [
            path.relative_to(ROOT).as_posix()
            for path in paths
            if path.is_file()
            and "/Users/" in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(leaked, [])

    def test_launch_assets_are_portable_and_under_two_megabytes(self) -> None:
        asset_names = (
            "arthur-loop-demo.gif",
            "web-console.png",
            "og.png",
            "logo.svg",
            "favicon.svg",
            "media/arthurbar-demo.png",
        )
        oversized = [
            name
            for name in asset_names
            if (ROOT / "site" / name).stat().st_size >= 2_000_000
        ]
        self.assertTrue((ROOT / "assets/arthurbar-demo.png").is_file())
        self.assertTrue((ROOT / "site/media/arthurbar-demo.webp").is_file())
        self.assertEqual(oversized, [])
        self.assertEqual(
            (ROOT / "site/logo.svg").read_bytes(),
            (ROOT / "assets/arthur-loop-wordmark.svg").read_bytes(),
        )
        self.assertTrue((ROOT / "site/create-loop-wizard.png").is_file())
        self.assertTrue((ROOT / "docs/assets/how-it-works.mp4").is_file())
        self.assertLess((ROOT / "site/create-loop-wizard.png").stat().st_size, 2_000_000)

    def test_public_surfaces_are_honest_about_main_vs_branch(self) -> None:
        surfaces = [
            self.index,
            self.readme,
            (ROOT / "site/llms.txt").read_text(encoding="utf-8"),
        ]
        for text in surfaces:
            self.assertIn("https://arthurloop.com/", text)
            self.assertNotIn("https://arthur-loop.vercel.app/", text)
            self.assertNotIn("raw.githubusercontent.com/myrrazor/arthur-loop/main/install.ps1 |", text)
            self.assertNotRegex(text, r"(?i)latest release[:\s]+v0\.2\.0")
            self.assertNotIn("Open source · v0.2.0", text)
        self.assertIn('<link rel="canonical" href="https://arthurloop.com/" />', self.index)
        self.assertIn("arthur 0.1.0", self.index)
        self.assertIn('id="menu-bar"', self.index)
        self.assertIn("macOS menu bar", self.index)
        self.assertIn("Star on GitHub", self.index)
        self.assertTrue((ROOT / "install.ps1").is_file())
        self.assertTrue((ROOT / "site/docs/index.html").is_file())
        self.assertTrue((ROOT / "site/docs/install.html").is_file())
        self.assertTrue((ROOT / "site/docs/menu-bar.html").is_file())
        llms = (ROOT / "site/llms.txt").read_text(encoding="utf-8")
        self.assertNotIn("file cockpit", llms)
        self.assertIn("docs/menu-bar", llms)
        self.assertIn("ArthurBar", llms)
        robots = (ROOT / "site/robots.txt").read_text(encoding="utf-8")
        sitemap = (ROOT / "site/sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://arthurloop.com/sitemap.xml", robots)
        self.assertIn("https://arthurloop.com/", sitemap)
        self.assertNotIn("arthur-loop.vercel.app", robots + sitemap)

    def test_docs_pages_exist_and_sidebar_uses_root_absolute_urls(self) -> None:
        pages = (
            "index.html",
            "install.html",
            "first-loop.html",
            "roles.html",
            "concepts.html",
            "console.html",
            "menu-bar.html",
            "agents.html",
            "cli.html",
            "faq.html",
        )
        for name in pages:
            path = ROOT / "site/docs" / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text(encoding="utf-8")
            self.assertIn('href="/docs/"', text)
            self.assertIn('href="/docs/first-loop.html"', text)
            self.assertIn('href="/docs/roles.html"', text)
            self.assertIn('href="/docs/menu-bar.html"', text)
            self.assertNotRegex(text, r'href="first-loop\.html"')
            self.assertNotRegex(text, r'href="\./"')
            title = re.search(r"<h1>(.*?)</h1>", text, flags=re.DOTALL)
            self.assertIsNotNone(title, name)
            self.assertGreater(len(re.sub(r"<[^>]+>", "", title.group(1)).strip()), 8, name)

    def test_crawler_and_not_found_files_are_present(self) -> None:
        for name in ("robots.txt", "sitemap.xml", "llms.txt", "llms-full.txt", "404.html"):
            self.assertTrue((ROOT / "site" / name).is_file(), name)
        sitemap = (ROOT / "site/sitemap.xml").read_text(encoding="utf-8")
        for loc in (
            "https://arthurloop.com/docs/",
            "https://arthurloop.com/docs/first-loop",
            "https://arthurloop.com/docs/roles",
            "https://arthurloop.com/docs/menu-bar",
            "https://arthurloop.com/llms.txt",
        ):
            self.assertIn(f"<loc>{loc}</loc>", sitemap)
        not_found = (ROOT / "site/404.html").read_text(encoding="utf-8")
        self.assertIn("Nothing queued at this path.", not_found)
        self.assertIn('href="/docs/first-loop.html"', not_found)
        vercel = (ROOT / "site/vercel.json").read_text(encoding="utf-8")
        self.assertIn('"/first-loop.html"', vercel)
        self.assertIn('"/docs/first-loop"', vercel)
        self.assertIn('"/docs/menu-bar"', vercel)
        self.assertIn("ArthurBar", self.readme)
        self.assertIn("assets/arthurbar-demo.png", self.readme)
        menu_bar_doc = (ROOT / "site/docs/menu-bar.html").read_text(encoding="utf-8")
        self.assertIn("menu bar", menu_bar_doc.lower())
        self.assertIn("quota", menu_bar_doc.lower())
        self.assertIn("Notification Center", menu_bar_doc)


if __name__ == "__main__":
    unittest.main()
