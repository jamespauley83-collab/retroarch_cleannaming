"""Dashboard regressions, runnable without the database or third-party packages."""

import json
import tempfile
import types
import unittest
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import preview_server


class ParsedPage(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.items = []
        self.in_item = False
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "li":
            self.in_item = True
            self.items.append("")

    def handle_endtag(self, tag):
        if tag == "li":
            self.in_item = False

    def handle_data(self, data):
        if self.in_item:
            self.items[-1] += data


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"md": "sega.dat", "nes": "nintendo.dat"}))
        database = self.root / "libretro-database"
        (database / ".git").mkdir(parents=True)

        paths = patch.multiple(
            preview_server,
            __file__=str(self.root / "preview_server.py"),
            CONFIG_PATH=str(self.config),
            DB_PATH=str(database),
        )
        paths.start()
        self.addCleanup(paths.stop)
        # Discovery only requires an importable dependency, not real ROM matching.
        dependency = types.ModuleType("Levenshtein")
        dependency.distance = lambda left, right: 0
        modules = patch.dict("sys.modules", {"Levenshtein": dependency})
        modules.start()
        self.addCleanup(modules.stop)

    def make_files(self, *names):
        for name in names:
            (self.root / name).touch()

    def assert_rom_count(self, page, count):
        self.assertIn(
            f'ROM files found</span><span class="value">{count}</span>', page
        )

    def test_filenames_are_rendered_as_text_not_html(self):
        names = (
            'game<img src=x onerror="alert(1)"> & \'bonus\'.nes',
            "literal&lt;img&gt;.nes",
        )
        self.make_files(*names)

        page = preview_server.build_status_page()
        parsed = ParsedPage(page)

        self.assert_rom_count(page, 2)
        self.assertCountEqual(parsed.items, names)
        self.assertNotIn("img", parsed.tags)
        for name in names:
            self.assertIn(f"<li>{escape(name)}</li>", page)

    def test_detects_md_roms_but_excludes_project_markdown(self):
        roms = ("Sonic.md", "Sonic 2.MD", "README (USA).md", "Mario.nes")
        self.make_files(*roms, "README.md", "AGENTS.md", "notes.txt")
        (self.root / "directory.md").mkdir()

        page = preview_server.build_status_page()

        self.assert_rom_count(page, len(roms))
        self.assertCountEqual(ParsedPage(page).items, roms)

    def test_only_detects_configured_extensions(self):
        self.config.write_text(json.dumps({"nes": "nintendo.dat"}))
        self.make_files("Sonic.md", "Mario.nes")

        page = preview_server.build_status_page()

        self.assert_rom_count(page, 1)
        self.assertEqual(ParsedPage(page).items, ["Mario.nes"])

    def test_retains_ten_file_preview_and_total_count(self):
        self.make_files(*(f"game-{number}.md" for number in range(12)))
        self.make_files("README.md", "AGENTS.md")

        page = preview_server.build_status_page()
        items = ParsedPage(page).items

        self.assert_rom_count(page, 12)
        self.assertEqual(len(items), 11)
        self.assertEqual(items[-1], "… and 2 more")

    def test_configured_extensions_are_rendered_as_text(self):
        extension = "<img src=x onerror=alert(1)>"
        self.config.write_text(json.dumps({extension: "custom.dat"}))

        page = preview_server.build_status_page()

        self.assertIn(escape(extension), page)
        self.assertNotIn("img", ParsedPage(page).tags)


if __name__ == "__main__":
    unittest.main()
