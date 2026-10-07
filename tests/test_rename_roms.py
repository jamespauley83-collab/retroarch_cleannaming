"""Exercise DAT matching and dry-run planning in temporary ROM fixtures."""

import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "rename_roms.py"


def levenshtein_distance(left, right):
    row = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        next_row = [i]
        for j, right_char in enumerate(right, 1):
            next_row.append(min(
                next_row[-1] + 1, row[j] + 1,
                row[j - 1] + (left_char != right_char),
            ))
        row = next_row
    return row[-1]


class RenameRomsTests(unittest.TestCase):
    def run_fixture(self, files, games, dry_run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "rename_roms.py"
            shutil.copyfile(SCRIPT, script)
            (root / "config.json").write_text(json.dumps({"nes": "games.dat"}))
            (root / "games.dat").write_text("".join(
                f'game (\n name "{title}"\n'
                + "".join(
                    (f' rom (\n  name "part {index} (USA).nes"\n  crc {crc}\n )\n'
                     if index % 2 else
                     f' rom ( name "part {index} (USA).nes" crc {crc} )\n')
                    for index, crc in enumerate(crcs)
                ) + ')\n'
                for title, crcs in games.items()
            ))
            for filename, contents in files.items():
                (root / filename).write_bytes(contents)

            logger = Mock()
            listdir = os.listdir
            # Keep fixture matching deterministic and standard-library-only.
            dependency = types.ModuleType("Levenshtein")
            dependency.distance = levenshtein_distance
            # Use the same deterministic input order in simulated and real runs.
            with patch.dict("sys.modules", {"Levenshtein": dependency}), \
                    patch("sys.argv", [str(script)] + (["--dry-run"] if dry_run else [])), \
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

    def assert_preview_matches_run(self, files, games):
        planned, unchanged, dry_calls, summary = self.run_fixture(files, games, True)
        actual, remaining, real_calls, real_summary = self.run_fixture(files, games, False)
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

    def test_exact_crc_matches_unrelated_filename(self):
        payload = b"exact payload"
        title = "Super Mario Bros. (USA)"
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown dump.nes": payload}, {title: [f"{zlib.crc32(payload):08X}"]}
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["planned_approximate"], 0)
        self.assertEqual(stats["no_match"], 0)
        self.assertEqual(remaining, {f"{title}.nes": payload})

    def test_exact_crc_takes_priority_over_filename_match(self):
        payload = b"exact payload"
        stats, remaining = self.assert_preview_matches_run(
            {"Mario.nes": payload},
            {"Mario": [], "Canonical": [f"{zlib.crc32(payload):08X}"]},
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["planned_approximate"], 0)
        self.assertEqual(remaining, {"Canonical.nes": payload})

    def test_lowercase_and_short_crcs_are_normalized(self):
        payload = b"fixture 33"
        self.assertEqual(f"{zlib.crc32(payload):08X}", "0FD23F89")
        for crc in ("0fd23f89", "FD23F89", "fd23f89", '"fd23f89"'):
            with self.subTest(crc=crc):
                stats, remaining = self.assert_preview_matches_run(
                    {"Unknown.nes": payload}, {"Canonical": [crc]}
                )
                self.assertEqual(stats["planned_crc"], 1)
                self.assertEqual(remaining, {"Canonical.nes": payload})

    def test_single_digit_zero_crc_is_normalized(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown.nes": b""}, {"Canonical": ["0"]}
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(remaining, {"Canonical.nes": b""})

    def test_every_rom_crc_maps_to_its_game(self):
        payloads = (b"first ROM", b"second ROM")
        games = {
            "Earlier Game": ["FFFFFFFF"],
            "Canonical (USA)": [f"{zlib.crc32(data):08X}" for data in payloads],
            "Later Game": ["EEEEEEEE"],
        }
        for payload in payloads:
            with self.subTest(payload=payload):
                stats, remaining = self.assert_preview_matches_run(
                    {"Unknown.nes": payload}, games
                )
                self.assertEqual(stats["planned_crc"], 1)
                self.assertEqual(remaining, {"Canonical (USA).nes": payload})

    def test_checksum_like_title_without_rom_crc_uses_fallback(self):
        payload = b"23"
        title = f"{zlib.crc32(payload):08X}"
        stats, remaining = self.assert_preview_matches_run(
            {f"{title} a.nes": payload}, {title: []}
        )
        self.assertEqual(stats["planned_crc"], 0)
        self.assertEqual(stats["planned_approximate"], 1)
        self.assertEqual(remaining, {f"{title}.nes": payload})

    def test_invalid_or_nonmatching_crc_preserves_fallback(self):
        for crc in ("not-a-crc", "123456789", "FFFFFFFF"):
            with self.subTest(crc=crc):
                stats, remaining = self.assert_preview_matches_run(
                    {"Mario a.nes": b"source"}, {"Mario": [crc]}
                )
                self.assertEqual(stats["planned_crc"], 0)
                self.assertEqual(stats["planned_approximate"], 1)
                self.assertEqual(remaining, {"Mario.nes": b"source"})

    def test_approximate_collision_is_reserved(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Mario a.nes": b"first", "Mario b.nes": b"second"}, {"Mario": []}
        )
        self.assertEqual(stats["planned_approximate"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(remaining, {"Mario.nes": b"first", "Mario b.nes": b"second"})

    def test_crc_collision_is_reserved(self):
        payload = b"23"
        title = "Mario"
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown a.nes": payload, "Unknown b.nes": payload},
            {title: [f"{zlib.crc32(payload):08X}"]},
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertIn(f"{title}.nes", remaining)

    def test_reservations_are_shared_between_match_branches(self):
        title = "Mario"
        for first, second, counter in (
            (b"23", b"different", "planned_crc"),
            (b"different", b"23", "planned_approximate"),
        ):
            with self.subTest(first_match=counter):
                stats, _ = self.assert_preview_matches_run(
                    {f"{title} a.nes": first, f"{title} b.nes": second},
                    {title: [f"{zlib.crc32(b'23'):08X}"]},
                )
                self.assertEqual(stats[counter], 1)
                self.assertEqual(stats["skipped"], 1)

    def test_existing_target_and_already_named_file_are_skipped(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Mario a.nes": b"source", "Mario.nes": b"target"}, {"Mario": []}
        )
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["planned_approximate"], 0)
        self.assertEqual(remaining["Mario.nes"], b"target")

    def test_projected_source_is_available_after_rename(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Mario a.nes": b"23", "Mario b.nes": b"second"},
            {"Mario a": [], "Mario": [f"{zlib.crc32(b'23'):08X}"]},
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["planned_approximate"], 1)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(remaining, {
            "Mario.nes": b"23", "Mario a.nes": b"second"
        })

    def test_no_match_does_not_plan_rename(self):
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown.nes": b"source"}, {"Mario": []}
        )
        self.assertEqual(stats["no_match"], 1)
        self.assertEqual(stats["planned_crc"] + stats["planned_approximate"], 0)
        self.assertEqual(remaining, {"Unknown.nes": b"source"})


if __name__ == "__main__":
    unittest.main()
