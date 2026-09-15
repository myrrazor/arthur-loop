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
            if not value or value.startswith(("#", "http://", "https://", "mailto:")):
                continue
            self.local_assets.add(value.split("?", 1)[0])

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
        self.assertEqual(len(documents), 2)

    def test_every_local_site_asset_exists(self) -> None:
        missing = [
            asset
            for asset in sorted(self.parser.local_assets)
            if not (ROOT / "site" / asset).is_file()
        ]
        self.assertEqual(missing, [])

    def test_launch_copy_and_install_commands_stay_in_sync(self) -> None:
        self.assertIn(ONE_LINER, self.readme)
        self.assertIn(ONE_LINER, self.index)
        self.assertIn('Maintained at <a href="https://github.com/myrrazor">@myrrazor</a>', self.index)
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
        )
        oversized = [
            name
            for name in asset_names
            if (ROOT / "site" / name).stat().st_size >= 2_000_000
        ]
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
        self.assertIn("they do not install", self.index.lower())
        self.assertTrue((ROOT / "install.ps1").is_file())
        self.assertTrue((ROOT / "site/docs/index.html").is_file())
        self.assertTrue((ROOT / "site/docs/install.html").is_file())
        self.assertIn("file cockpit", (ROOT / "site/llms.txt").read_text(encoding="utf-8"))
        robots = (ROOT / "site/robots.txt").read_text(encoding="utf-8")
        sitemap = (ROOT / "site/sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://arthurloop.com/sitemap.xml", robots)
        self.assertIn("https://arthurloop.com/", sitemap)
        self.assertNotIn("arthur-loop.vercel.app", robots + sitemap)


if __name__ == "__main__":
    unittest.main()
