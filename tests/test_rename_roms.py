"""Exercise DAT matching and dry-run planning in temporary ROM fixtures."""

import json
import os
from pathlib import Path
import re
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
            game_records = games.items() if isinstance(games, dict) else games
            (root / "games.dat").write_text("".join(
                f'game (\n name "{title}"\n'
                + "".join(
                    (f' rom (\n  name "part {index} (USA).nes"\n  crc {crc}\n )\n'
                     if index % 2 else
                     f' rom ( name "part {index} (USA).nes" crc {crc} )\n')
                    for index, crc in enumerate(crcs)
                ) + ')\n'
                for title, crcs in game_records
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
                actual_operations = [
                    tuple(Path(path).name for path in call.args)
                    for call in rename.call_args_list
                ]

            remaining = {path.name: path.read_bytes() for path in root.glob("*.nes")}
            messages = [call.args[0] for call in logger.info.call_args_list]
            summary = next(message for message in messages if message.startswith("Summary:"))
            operations = [
                match.groups() for message in messages
                if (match := re.match(
                    r'^\[DRY-RUN\] Would rename: (.*?) -> (.*?) \((?:CRC|approximate) match',
                    message,
                ))
            ] if dry_run else actual_operations
            warnings = [call.args[0] for call in logger.warning.call_args_list]
            return result["stats"], remaining, rename_count, summary, operations, warnings

    def assert_preview_matches_run(self, files, games, ambiguous=False):
        planned, unchanged, dry_calls, summary, preview, dry_warnings = self.run_fixture(
            files, games, True,
        )
        actual, remaining, real_calls, real_summary, renamed, real_warnings = self.run_fixture(
            files, games, False,
        )
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
        self.assertEqual(preview, renamed)
        projected_files = dict(files)
        for source, target in preview:
            projected_files[target] = projected_files.pop(source)
        self.assertEqual(projected_files, remaining)
        for warnings in (dry_warnings, real_warnings):
            self.assertEqual(any("Ambiguous CRC" in message for message in warnings), ambiguous)
        return planned, remaining

    def test_shared_crc_uses_filename_evidence_in_either_dat_order(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        games = [("Mario (USA)", [crc]), ("Zelda", [crc])]
        for records in (games, games[::-1]):
            for filename, title in (("mario (U).nes", "Mario (USA)"), ("zelda.nes", "Zelda")):
                with self.subTest(records=records, filename=filename):
                    stats, remaining = self.assert_preview_matches_run({filename: payload}, records)
                    self.assertEqual(stats["planned_crc"], 1)
                    self.assertEqual(stats["planned_approximate"], 0)
                    self.assertEqual(remaining, {f"{title}.nes": payload})

    def test_shared_crc_prefers_exact_title_over_normalized_title(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        games = [("Final Fantasy 2", [crc]), ("Final Fantasy II", [crc])]
        for records in (games, games[::-1]):
            with self.subTest(records=records):
                stats, remaining = self.assert_preview_matches_run(
                    {"final fantasy 2.nes": payload}, records,
                )
                self.assertEqual(stats["planned_crc"], 1)
                self.assertEqual(remaining, {"Final Fantasy 2.nes": payload})

    def test_shared_crc_without_reliable_filename_is_left_unchanged(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        games = [("Mario", [crc]), ("Zelda", [crc])]
        for records in (games, games[::-1]):
            for filename in ("Unknown.nes", "Mario a.nes"):
                with self.subTest(records=records, filename=filename):
                    files = {filename: payload}
                    stats, remaining = self.assert_preview_matches_run(files, records, ambiguous=True)
                    self.assertEqual(stats["no_match"], 1)
                    self.assertEqual(stats["planned_crc"] + stats["planned_approximate"], 0)
                    self.assertEqual(remaining, files)

    def test_shared_crc_with_conflicting_normalized_titles_is_left_unchanged(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        for filename, titles in (
            ("final fantasy II (U).nes", ["Final Fantasy 2 (U)", "Final Fantasy II (USA)"]),
            ("mario.nes", ["Mario", "MARIO"]),
            ("mario (U).nes", ["Mario (U)", "MARIO (u)"]),
        ):
            for order in (titles, titles[::-1]):
                with self.subTest(filename=filename, titles=order):
                    files = {filename: payload}
                    stats, remaining = self.assert_preview_matches_run(
                        files, [(title, [crc]) for title in order], ambiguous=True,
                    )
                    self.assertEqual(stats["no_match"], 1)
                    self.assertEqual(remaining, files)

    def test_shared_crc_does_not_fall_back_to_an_unrelated_title(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        files = {"Kirby a.nes": payload}
        stats, remaining = self.assert_preview_matches_run(
            files, {"Mario": [crc], "Zelda": [crc], "Kirby": []}, ambiguous=True,
        )
        self.assertEqual(stats["no_match"], 1)
        self.assertEqual(remaining, files)

    def test_repeated_crc_for_same_title_is_not_ambiguous(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        stats, remaining = self.assert_preview_matches_run(
            {"Unknown.nes": payload}, [("Mario", [crc, crc.lower()]), ("Mario", [crc])],
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(remaining, {"Mario.nes": payload})

    def test_disambiguated_crc_reserves_the_preview_destination(self):
        payload = b"shared checksum"
        crc = f"{zlib.crc32(payload):08X}"
        stats, remaining = self.assert_preview_matches_run(
            {"Mario (U).nes": payload, "mario (USA).nes": payload},
            {"Mario (USA)": [crc], "Zelda": [crc]},
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(remaining, {"Mario (USA).nes": payload, "mario (USA).nes": payload})

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
