"""Exercise DAT matching and dry-run planning in temporary ROM fixtures."""

import json
import contextlib
import io
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
from contextlib import ExitStack

from test_undo_history import CaseInsensitiveDirectory, native_case_insensitive


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
    def run_fixture(self, files, games, dry_run, insensitive=False, lookup_key=str.casefold):
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
            filesystem = CaseInsensitiveDirectory(root, lookup_key=lookup_key)
            # Native case-insensitive volumes need no lookup simulation. This
            # keeps these same assertions useful on an actual Mac or Windows.
            if insensitive and native_case_insensitive(root):
                insensitive = False
            real_rename = os.rename

            def rename_file(source, target):
                if insensitive:
                    resolved_source = filesystem.resolve(source)
                    resolved_target = filesystem.resolve(target)
                    if resolved_target != resolved_source:
                        target = resolved_target
                    source = resolved_source
                real_rename(source, target)

            # Use the same deterministic input order in simulated and real runs.
            with ExitStack() as stack, \
                    patch.dict("sys.modules", {"Levenshtein": dependency}), \
                    patch("sys.argv", [str(script), "--approximate"] + ([] if dry_run else ["--apply"])), \
                    patch("os.listdir", side_effect=lambda path: sorted(listdir(path))), \
                    patch("os.rename", side_effect=rename_file) as rename, \
                    patch("logging.basicConfig"), patch("logging.FileHandler"), \
                    patch("logging.getLogger", return_value=logger):
                if insensitive:
                    stack.enter_context(patch('os.path.exists', side_effect=filesystem.exists))
                    stack.enter_context(patch('os.path.lexists', side_effect=filesystem.lexists))
                    stack.enter_context(patch('os.path.samefile', side_effect=filesystem.samefile))
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

    def assert_preview_matches_run(self, files, games, ambiguous=False, insensitive=False,
                                   lookup_key=str.casefold):
        planned, unchanged, dry_calls, summary, preview, dry_warnings = self.run_fixture(
            files, games, True, insensitive=insensitive, lookup_key=lookup_key,
        )
        actual, remaining, real_calls, real_summary, renamed, real_warnings = self.run_fixture(
            files, games, False, insensitive=insensitive, lookup_key=lookup_key,
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

    def test_case_only_crc_rename_preserves_canonical_spelling_on_insensitive_volume(self):
        payload = b'case-only CRC fixture'
        stats, remaining = self.assert_preview_matches_run(
            {'zelda.nes': payload}, {'Zelda': [f'{zlib.crc32(payload):08X}']},
            insensitive=True,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(remaining, {'Zelda.nes': payload})

    def test_unicode_source_does_not_hide_case_insensitive_directory(self):
        first, second = b'first unicode probe', b'second unicode probe'
        stats, remaining = self.assert_preview_matches_run(
            {'Stra\u00dfe.nes': first, 'zeldx.nes': second},
            {'Zelda': [f'{zlib.crc32(first):08X}'], 'zelda': [f'{zlib.crc32(second):08X}']},
            insensitive=True, lookup_key=str.lower,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(remaining, {'Zelda.nes': first, 'zeldx.nes': second})

    def test_unicode_fold_reservations_are_conservative_in_both_modes(self):
        first, second = b'first unicode reservation', b'second unicode reservation'
        # Native filesystems need not equate sharp-s with ss as casefold does.
        # The portable reservation policy must make the same decision in apply.
        stats, remaining = self.assert_preview_matches_run(
            {'aaa.nes': first, 'bbb.nes': second},
            {'Stra\u00dfe': [f'{zlib.crc32(first):08X}'], 'Strasse': [f'{zlib.crc32(second):08X}']},
            insensitive=True, lookup_key=str.lower,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(remaining, {'Stra\u00dfe.nes': first, 'bbb.nes': second})

    def test_freeing_one_unicode_entry_does_not_free_its_fold_neighbor(self):
        first, second, third = b'unicode moved', b'unicode occupied', b'unicode blocked'
        stats, remaining = self.assert_preview_matches_run(
            {'Strasse.nes': first, 'Stra\u00dfe.nes': second, 'bbb.nes': third},
            {'New Strasse': [f'{zlib.crc32(first):08X}'],
             'Stra\u00dfe': [f'{zlib.crc32(second):08X}', f'{zlib.crc32(third):08X}']},
            insensitive=True, lookup_key=str.lower,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(stats['skipped'], 2)
        self.assertEqual(remaining, {'New Strasse.nes': first, 'Stra\u00dfe.nes': second,
                                     'bbb.nes': third})

    def test_moving_unicode_neighbor_keeps_prior_destination_reserved(self):
        first, second, third = b'reserved unicode', b'moving unicode', b'blocked unicode'
        stats, remaining = self.assert_preview_matches_run(
            {'AAA.nes': first, 'Stra\u00dfe.nes': second, 'zzz.nes': third},
            {'Strasse': [f'{zlib.crc32(first):08X}'],
             'Other': [f'{zlib.crc32(second):08X}'], 'STRASSE': [f'{zlib.crc32(third):08X}']},
            insensitive=True, lookup_key=str.lower,
        )
        self.assertEqual(stats['planned_crc'], 2)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(remaining, {'Strasse.nes': first, 'Other.nes': second,
                                     'zzz.nes': third})

    def test_initial_unicode_ambiguity_stays_reserved_after_a_neighbor_moves(self):
        first, second = b'unicode neighbor moved', b'unicode neighbor retained'
        stats, remaining = self.assert_preview_matches_run(
            {'Strasse.nes': first, 'Stra\u00dfe.nes': second},
            {'New': [f'{zlib.crc32(first):08X}'], 'STRA\u00dfE': [f'{zlib.crc32(second):08X}']},
            insensitive=True, lookup_key=str.lower,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(remaining, {'New.nes': first, 'Stra\u00dfe.nes': second})

    def test_case_only_approximate_rename_preserves_canonical_spelling_on_insensitive_volume(self):
        payload = b'case-only approximate fixture'
        stats, remaining = self.assert_preview_matches_run(
            {'mario.nes': payload}, {'Mario': []}, insensitive=True,
        )
        self.assertEqual(stats['planned_approximate'], 1)
        self.assertEqual(remaining, {'Mario.nes': payload})

    def test_case_only_destination_reservation_blocks_a_later_case_alias(self):
        first, second = b'first case fixture', b'second case fixture'
        stats, remaining = self.assert_preview_matches_run(
            {'zelda.nes': first, 'zeldx.nes': second},
            {'Zelda': [f'{zlib.crc32(first):08X}'], 'zelda': [f'{zlib.crc32(second):08X}']},
            insensitive=True,
        )
        self.assertEqual(stats['planned_crc'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(remaining, {'Zelda.nes': first, 'zeldx.nes': second})

    def test_case_sensitive_volume_keeps_distinct_case_variant_destinations_available(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / 'case-probe.nes'
            probe.write_bytes(b'fixture')
            if probe.with_name(probe.name.upper()).exists():
                self.skipTest('requires case-sensitive directory entries')
        first, second = b'first case fixture', b'second case fixture'
        stats, remaining = self.assert_preview_matches_run(
            {'zelda.nes': first, 'zeldx.nes': second},
            {'Zelda': [f'{zlib.crc32(first):08X}'], 'zelda': [f'{zlib.crc32(second):08X}']},
        )
        self.assertEqual(stats['planned_crc'], 2)
        self.assertEqual(stats['skipped'], 0)
        self.assertEqual(remaining, {'Zelda.nes': first, 'zelda.nes': second})

    def test_projected_source_is_free_through_a_case_alias_after_rename(self):
        first, second = b'first freed fixture', b'second freed fixture'
        stats, remaining = self.assert_preview_matches_run(
            {'aaa.nes': first, 'bbb.nes': second},
            {'New AAA': [f'{zlib.crc32(first):08X}'], 'AAA': [f'{zlib.crc32(second):08X}']},
            insensitive=True,
        )
        self.assertEqual(stats['planned_crc'], 2)
        self.assertEqual(stats['skipped'], 0)
        self.assertEqual(remaining, {'New AAA.nes': first, 'AAA.nes': second})

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
            {"Mario (U).nes": payload, "Mario (USA) .nes": payload},
            {"Mario (USA)": [crc], "Zelda": [crc]},
        )
        self.assertEqual(stats["planned_crc"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(remaining, {"Mario (USA).nes": payload, "Mario (USA) .nes": payload})

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


class SafetyTests(unittest.TestCase):
    """Renames happen only when asked for, and can be reversed."""

    def run_script(self, root, args, games=None, files=None, config=None):
        script = root / "rename_roms.py"
        shutil.copyfile(SCRIPT, script)
        (root / "config.json").write_text(json.dumps(config or {"nes": "games.dat"}))
        (root / "games.dat").write_text("".join(
            f'game ( name "{title}" )\n' for title in (games or [])
        ))
        for filename, contents in (files or {}).items():
            (root / filename).write_bytes(contents)
        dependency = types.ModuleType("Levenshtein")
        dependency.distance = levenshtein_distance
        logger = Mock()
        with patch.dict("sys.modules", {"Levenshtein": dependency}), \
                patch("sys.argv", [str(script)] + args), \
                patch("logging.basicConfig"), patch("logging.FileHandler"), \
                patch("logging.getLogger", return_value=logger):
            try:
                runpy.run_path(str(script))
            except SystemExit as exit:
                if exit.code not in (None, 0):
                    raise
        return logger

    def names(self, root, pattern="*.nes"):
        return sorted(path.name for path in root.glob(pattern))

    def test_default_run_only_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_script(root, ["--approximate"], ["Mario"], {"Mario a.nes": b"x"})
            self.assertEqual(self.names(root), ["Mario a.nes"])

    def test_unknown_option_stops_before_renaming(self):
        for option in ("--dryrun", "-n", "--aply"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stopped:
                    self.run_script(root, ["--apply", "--approximate", option],
                                    ["Mario"], {"Mario a.nes": b"x"})
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn(option, stderr.getvalue())
                self.assertEqual(self.names(root), ["Mario a.nes"])

    def test_fuzzy_matches_need_approximate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = self.run_script(root, ["--apply"], ["Mario"], {"Mario a.nes": b"x"})
            self.assertEqual(self.names(root), ["Mario a.nes"])
            warnings = " ".join(call.args[0] for call in logger.warning.call_args_list)
            self.assertIn("add --approximate", warnings)

    def test_approximate_destination_dangling_symlink_is_preserved_in_preview_and_apply(self):
        for mode in ('--dry-run', '--apply'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / 'Mario.nes'
                missing = root / 'missing.nes'
                try:
                    target.symlink_to(missing)
                except OSError:
                    self.skipTest('symlinks unavailable')
                original_link = os.readlink(target)
                with patch('os.rename', wraps=os.rename) as moves:
                    logger = self.run_script(root, [mode, '--approximate'],
                                             ['Mario'], {'Mario a.nes': b'source'})
                moves.assert_not_called()
                self.assertEqual((root / 'Mario a.nes').read_bytes(), b'source')
                self.assertTrue(target.is_symlink())
                self.assertEqual(os.readlink(target), original_link)
                self.assertFalse(missing.exists())
                self.assertFalse((root / 'rename_undo.json').exists())
                warnings = ' '.join(call.args[0] for call in logger.warning.call_args_list)
                self.assertIn('target already exists', warnings)

    def test_distinct_case_variant_destination_is_never_overwritten(self):
        for destination in ('file', 'hardlink', 'symlink'):
            for mode in ('--dry-run', '--apply'):
                with self.subTest(destination=destination, mode=mode), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / 'mario.nes'
                    target = root / 'Mario.nes'
                    source.write_bytes(b'source')
                    if target.exists():
                        self.skipTest('requires distinct case-variant directory entries')
                    if destination == 'file':
                        target.write_bytes(b'unrelated')
                    elif destination == 'hardlink':
                        os.link(source, target)
                    else:
                        target.symlink_to(source)
                    target_contents = target.read_bytes()
                    with patch('os.rename', wraps=os.rename) as moves:
                        self.run_script(root, [mode, '--approximate'], ['Mario'])
                    moves.assert_not_called()
                    self.assertEqual(source.read_bytes(), b'source')
                    self.assertEqual(target.read_bytes(), target_contents)
                    self.assertIn('mario.nes', os.listdir(root))
                    self.assertIn('Mario.nes', os.listdir(root))
                    self.assertEqual(target.is_symlink(), destination == 'symlink')
                    self.assertFalse((root / 'rename_undo.json').exists())

    def test_number_in_only_one_name_is_not_a_match(self):
        for filename, title in (("Final Fight 3.nes", "Final Fight (USA)"),
                                ("Sonic.nes", "Sonic 3 (Europe)")):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.run_script(root, ["--apply", "--approximate", "--distance=30"],
                                [title], {filename: b"x"})
                self.assertEqual(self.names(root), [filename])

    def test_project_files_are_never_treated_as_roms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_script(root, ["--apply", "--approximate", "--distance=30"],
                            ["Agent Hugo (Europe)", "README (USA)"],
                            {"README.md": b"docs", "AGENTS.md": b"docs"},
                            {"md": "games.dat"})
            self.assertEqual(self.names(root, "*.md"), ["AGENTS.md", "README.md"])

    def test_undo_restores_applied_renames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_script(root, ["--apply", "--approximate"], ["Mario", "Zelda"],
                            {"Mario a.nes": b"1", "Zelda b.nes": b"2"})
            self.assertEqual(self.names(root), ["Mario.nes", "Zelda.nes"])
            self.run_script(root, ["--undo"])
            self.assertEqual(self.names(root), ["Mario a.nes", "Zelda b.nes"])
            self.assertFalse((root / "rename_undo.json").exists())

    def test_roms_folder_can_be_outside_the_tool(self):
        with tempfile.TemporaryDirectory() as tool, tempfile.TemporaryDirectory() as roms:
            (Path(roms) / "Mario a.nes").write_bytes(b"x")
            self.run_script(Path(tool), ["--apply", "--approximate", f"--roms={roms}"], ["Mario"])
            self.assertEqual(self.names(Path(roms)), ["Mario.nes"])
            self.assertTrue((Path(roms) / "rename_undo.json").exists())


if __name__ == "__main__":
    unittest.main()
