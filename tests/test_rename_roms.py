"""Exercise dry-run planning against real renames in temporary ROM fixtures."""

import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "rename_roms.py"


class RenameRomsTests(unittest.TestCase):
    def run_fixture(self, files, titles, dry_run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "rename_roms.py"
            shutil.copyfile(SCRIPT, script)
            (root / "config.json").write_text(json.dumps({"nes": "games.dat"}))
            (root / "games.dat").write_text("".join(
                f'game (\n name "{title}"\n)\n' for title in titles
            ))
            for filename, contents in files.items():
                (root / filename).write_bytes(contents)

            logger = Mock()
            listdir = os.listdir
            # Use the same deterministic input order in simulated and real runs.
            with patch("sys.argv", [str(script)] + (["--dry-run"] if dry_run else [])), \
                    patch("os.listdir", side_effect=lambda path: sorted(listdir(path))), \
                    patch("os.rename", wraps=os.rename) as rename, \
                    patch("logging.basicConfig"), patch("logging.FileHandler"), \
                    patch("logging.getLogger", return_value=logger):
                result = runpy.run_path(str(script))
                rename_count = rename.call_count

            remaining = {path.name: path.read_bytes() for path in root.glob("*.nes")}
            messages = [call.args[0] for call in logger.info.call_args_list]
            summary = next(message for message in messages if message.startswith("Summary:"))
            return result["stats"], remaining, rename_count, summary

    def assert_preview_matches_run(self, files, titles):
        planned, unchanged, dry_calls, summary = self.run_fixture(files, titles, True)
        actual, remaining, real_calls, real_summary = self.run_fixture(files, titles, False)
        self.assertEqual(unchanged, files)
        self.assertEqual(dry_calls, 0)
        self.assertEqual(planned["renamed"], 0)
        self.assertEqual(planned["approximate"], 0)
        self.assertIn("0 renamed (CRC), 0 renamed (approximate)", summary)
        self.assertIn(f'{planned["planned_crc"]} planned (CRC)', summary)
        self.assertIn(f'{planned["planned_approximate"]} planned (approximate)', summary)
        self.assertNotIn("planned", real_summary)
        self.assertEqual(actual["planned_crc"], 0)
        self.assertEqual(actual["planned_approximate"], 0)
        self.assertEqual(planned["planned_crc"], actual["renamed"])
        self.assertEqual(planned["planned_approximate"], actual["approximate"])
        for counter in ("processed", "skipped", "no_match"):
            self.assertEqual(planned[counter], actual[counter])
        self.assertEqual(real_calls, actual["renamed"] + actual["approximate"])
        return planned, remaining

    def test_approximate_collision_is_reserved(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Mario a.nes": b"first", "Mario b.nes": b"second"}, ["Mario"]
        )
        self.assertEqual(stats["planned_approximate"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(remaining, {"Mario.nes": b"first", "Mario b.nes": b"second"})

    def test_crc_collision_is_reserved(self):
        # A digit-only CRC also matches the current parser's lowercase name keys.
        payload = b"23"
        title = f"{zlib.crc32(payload):08X}"
        self.assertEqual(title, "13792798")
        stats, remaining = self.assert_preview_matches_run(
            {f"{title} a.nes": payload, f"{title} b.nes": payload}, [title]
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertIn(f"{title}.nes", remaining)

    def test_reservations_are_shared_between_match_branches(self):
        title = "13792798"
        for first, second, counter in (
            (b"23", b"different", "planned_crc"),
            (b"different", b"23", "planned_approximate"),
        ):
            with self.subTest(first_match=counter):
                stats, _ = self.assert_preview_matches_run(
                    {f"{title} a.nes": first, f"{title} b.nes": second}, [title]
                )
                self.assertEqual(stats[counter], 1)
                self.assertEqual(stats["skipped"], 1)

    def test_existing_target_and_already_named_file_are_skipped(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Mario a.nes": b"source", "Mario.nes": b"target"}, ["Mario"]
        )
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["planned_approximate"], 0)
        self.assertEqual(remaining["Mario.nes"], b"target")

    def test_projected_source_is_available_after_rename(self):
        stats, remaining = self.assert_preview_matches_run(
            {"13792798a.nes": b"23", "13792798b.nes": b"second"},
            ["13792798a", "13792798"],
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["planned_approximate"], 1)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(remaining, {
            "13792798.nes": b"23", "13792798a.nes": b"second"
        })

    def test_no_match_does_not_plan_rename(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown.nes": b"source"}, ["Mario"]
        )
        self.assertEqual(stats["no_match"], 1)
        self.assertEqual(stats["planned_crc"] + stats["planned_approximate"], 0)
        self.assertEqual(remaining, {"Unknown.nes": b"source"})


if __name__ == "__main__":
    unittest.main()
