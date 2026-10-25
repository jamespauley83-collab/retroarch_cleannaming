"""Console isolation regressions; all files are disposable temporary fixtures."""

import builtins
import contextlib
import io
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

from test_rename_roms import levenshtein_distance


REPO = Path(__file__).resolve().parents[1]
BUNDLED_CONFIG = json.loads((REPO / 'config.json').read_text())
PROFILES = BUNDLED_CONFIG['systems']
PAYLOAD = b'disposable disc payload'
CRC = f'{zlib.crc32(PAYLOAD):08X}'


class SystemSelectionTests(unittest.TestCase):
    def run_fixture(self, files, games, args=(), config=None, missing=(), undo_after=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            app.mkdir()
            roms = root / 'collection'
            roms.mkdir()
            script = app / 'rename_roms.py'
            shutil.copyfile(REPO / 'rename_roms.py', script)
            config = BUNDLED_CONFIG if config is None else config
            # The config, script, working directory, and ROM directory differ.
            settings = root / 'settings'
            settings.mkdir()
            config_path = settings / 'config.json'
            config_path.write_text(json.dumps(config))
            for system, records in games.items():
                if system in missing:
                    continue
                dat = settings / (config['systems'][system]['dat']
                                  if system in config.get('systems', {}) else system)
                dat.parent.mkdir(parents=True, exist_ok=True)
                dat.write_text(''.join(
                    f'game ( name "{title}" '
                    + ''.join(f'rom ( crc {crc} ) ' for crc in crcs) + ')\n'
                    for title, crcs in records
                ))
            for filename, contents in files.items():
                path = roms / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in root.rglob('*') if p.is_file()}
            logger = Mock()
            dependency = types.ModuleType('Levenshtein')
            dependency.distance = levenshtein_distance
            opened = []
            real_open = builtins.open

            def track_open(path, *args, **kwargs):
                opened.append(os.fspath(path))
                return real_open(path, *args, **kwargs)

            stderr = io.StringIO()
            result = None
            error = None
            with patch.dict('sys.modules', {'Levenshtein': dependency}), \
                    patch('sys.argv', [str(script), '--config', str(config_path),
                                       '--roms-dir', str(roms), *args]), \
                    patch('builtins.open', side_effect=track_open), \
                    patch('os.rename', wraps=os.rename) as rename, \
                    patch('logging.basicConfig'), \
                    patch('logging.FileHandler') as file_handler, \
                    patch('logging.getLogger', return_value=logger), \
                    contextlib.redirect_stderr(stderr):
                try:
                    result = runpy.run_path(str(script))
                except SystemExit as exc:
                    error = exc.code
                renamed = [tuple(Path(p).name for p in call.args)
                           for call in rename.call_args_list]
                log_file_calls = file_handler.call_count
                undo_path = roms / 'rename_undo.json'
                history = json.loads(undo_path.read_text()) if undo_path.exists() else None
                restored = []
                if undo_after:
                    rename.reset_mock()
                    # Undo must work independently of both console/DAT selection and
                    # the config that was used for the applied rename.
                    with patch('sys.argv', [str(script), '--undo', '--roms', str(roms),
                                           '--config', str(settings / 'missing.json')]):
                        with self.assertRaises(SystemExit) as stopped:
                            runpy.run_path(str(script))
                        self.assertEqual(stopped.exception.code, 0)
                    restored = [tuple(Path(p).name for p in call.args)
                                for call in rename.call_args_list]

            after = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in root.rglob('*') if p.is_file()}
            if '--apply' not in args or error is not None:
                self.assertEqual(before, after)
                self.assertEqual(renamed, [])
                self.assertEqual(log_file_calls, 0)
            return types.SimpleNamespace(
                stats=result['stats'] if result else None,
                output='\n'.join(str(call.args[0]) for call in logger.info.call_args_list),
                warnings='\n'.join(str(call.args[0]) for call in logger.warning.call_args_list),
                error=error, stderr=stderr.getvalue(), renamed=renamed,
                history=history, restored=restored,
                undo_exists=undo_path.exists(),
                dats={Path(path).name for path in opened if path.endswith('.dat')},
                rom_reads={Path(path).name for path in opened if str(roms) in path},
                remaining={p.relative_to(roms).as_posix(): p.read_bytes()
                           for p in roms.rglob('*') if p.is_file() and p != undo_path},
            )

    def test_bundled_disc_mappings_require_system(self):
        for ext in ('iso', 'chd', 'cso', 'gcm', 'rvz'):
            self.assertNotIn(ext, BUNDLED_CONFIG)
        files = {f'Racer.{ext}': PAYLOAD for ext in ('iso', 'chd', 'cso', 'gcm', 'rvz')}
        result = self.run_fixture(files, {}, ['--dry-run'])
        self.assertEqual(result.stats['processed'], 0)
        self.assertEqual(result.stats['skipped'], len(files))
        self.assertEqual(result.dats, set())
        self.assertEqual(result.rom_reads, set())
        self.assertIn('select --system', result.warnings)

    def test_legacy_unsafe_extension_mappings_are_also_blocked(self):
        config = {'iso': 'psp.dat', 'chd': 'psx.dat', 'sbi': 'psx.dat'}
        files = {'Racer a.iso': PAYLOAD, 'Racer a.chd': PAYLOAD, 'Racer.sbi': PAYLOAD}
        games = {'psp.dat': [('Racer', [CRC])], 'psx.dat': [('Racer', [CRC])]}
        for args in (['--dry-run'], []):
            with self.subTest(args=args):
                result = self.run_fixture(files, games, args, config)
                self.assertEqual(result.stats['processed'], 0)
                self.assertEqual(result.remaining, files)
                self.assertEqual(result.dats, set())

    def test_each_console_uses_only_its_dat_for_crc_and_fuzzy_matches(self):
        for system, profile in PROFILES.items():
            for extension in profile['extensions']:
                for kind in ('CRC', 'approximate'):
                    with self.subTest(system=system, extension=extension, kind=kind):
                        games = {key: [(f'Racer {key.replace("2", "two")}', [CRC] if kind == 'CRC' else [])]
                                 for key in PROFILES}
                        files = {f'Racer a.{extension.upper()}': PAYLOAD}
                        matching = ['--approximate'] if kind == 'approximate' else []
                        preview = self.run_fixture(files, games, ['--system', system, *matching])
                        actual = self.run_fixture(files, games, ['--system', system, '--apply', *matching])
                        target = f'Racer {system.replace("2", "two")}.{extension}'
                        self.assertIn(f'-> {target} ({kind} match', preview.output)
                        self.assertEqual(actual.renamed, [(next(iter(files)), target)])
                        self.assertEqual(actual.remaining, {target: PAYLOAD})
                        self.assertEqual(preview.dats, {Path(profile['dat']).name})
                        self.assertEqual(actual.dats, preview.dats)

    def test_other_system_crc_and_fuzzy_candidates_never_supply_a_match(self):
        for system, profile in PROFILES.items():
            for other in PROFILES.keys() - {system}:
                for kind in ('CRC', 'approximate'):
                    with self.subTest(system=system, other=other, kind=kind):
                        games = {key: [('Unrelated', [])] for key in PROFILES}
                        games[other] = [('Racer', [CRC] if kind == 'CRC' else [])]
                        result = self.run_fixture({'Racer a.iso': PAYLOAD}, games,
                                                  ['--system', system, '--approximate'])
                        self.assertEqual(result.stats['no_match'], 1)
                        self.assertEqual(result.stats['planned_crc'], 0)
                        self.assertEqual(result.stats['planned_approximate'], 0)
                        self.assertEqual(result.dats, {Path(profile['dat']).name})

    def test_profile_overrides_legacy_defaults_and_ignores_other_extensions(self):
        config = {**BUNDLED_CONFIG, 'iso': 'wrong.dat', 'chd': 'wrong.dat', 'nes': 'wrong.dat'}
        games = {key: [('Racer', [CRC])] for key in PROFILES}
        games['wrong.dat'] = [('Wrong console', [CRC])]
        files = {'Racer a.iso': PAYLOAD, 'Racer.chd': PAYLOAD, 'Racer.nes': PAYLOAD}
        result = self.run_fixture(files, games, ['--system', 'psp', '--dry-run'], config)
        self.assertEqual(result.stats['processed'], 1)
        self.assertEqual(result.rom_reads, {'Racer a.iso'})
        self.assertNotIn('wrong.dat', result.dats)

    def test_unknown_system_fails_before_reading_roms_or_logging(self):
        result = self.run_fixture({'Racer.iso': PAYLOAD}, {}, ['--system', 'ps22'])
        self.assertEqual(result.error, 2)
        self.assertIn('unknown system', result.stderr)
        self.assertEqual(result.rom_reads, set())

    def test_missing_selected_dat_never_falls_back(self):
        for system in PROFILES:
            with self.subTest(system=system):
                games = {key: [('Racer', [CRC])] for key in PROFILES}
                result = self.run_fixture({'Racer.iso': PAYLOAD}, games,
                                          ['--system', system], missing=[system])
                self.assertEqual(result.error, 2)
                self.assertIn('Cannot read selected DAT', result.stderr)
                self.assertEqual(result.rom_reads, set())

    def test_ambiguous_crc_still_blocks_fuzzy_fallback_with_selected_system(self):
        for system in PROFILES:
            for reverse in (False, True):
                with self.subTest(system=system, reverse=reverse):
                    records = [('First', [CRC]), ('Second', [CRC]), ('Racer', [])]
                    if reverse:
                        records.reverse()
                    result = self.run_fixture({'Racer a.iso': PAYLOAD}, {system: records},
                                              ['--system', system, '--approximate'])
                    self.assertEqual(result.stats['no_match'], 1)
                    self.assertIn('Ambiguous CRC', result.warnings)
                    self.assertEqual(result.stats['planned_approximate'], 0)

    def test_shared_crc_uses_unique_filename_evidence_with_selected_system(self):
        for system in PROFILES:
            with self.subTest(system=system):
                result = self.run_fixture({'racer (U).iso': PAYLOAD},
                                          {system: [('Other', [CRC]), ('Racer (USA)', [CRC])]},
                                          ['--system', system, '--dry-run'])
                self.assertEqual(result.stats['planned_crc'], 1)
                self.assertIn('-> Racer (USA).iso (CRC match)', result.output)

    def test_renames_never_recurse_into_other_console_or_save_directories(self):
        files = {'Racer a.iso': PAYLOAD, 'PSP/Racer a.iso': PAYLOAD,
                 'saves/profile.srm': b'save', 'states/game.state': b'state',
                 'retroarch.cfg': b'config', 'Racer.sbi': b'sidecar',
                 'Racer.bin': PAYLOAD, 'Racer.cue': b'cue', 'Racer.m3u': b'playlist'}
        result = self.run_fixture(files, {'ps2': [('Racer', [CRC])]}, ['--system', 'ps2', '--apply'])
        self.assertEqual(result.renamed, [('Racer a.iso', 'Racer.iso')])
        self.assertEqual(result.remaining, {**{k: v for k, v in files.items() if k != 'Racer a.iso'},
                                            'Racer.iso': PAYLOAD})

    def test_dry_run_preserves_all_bytes_and_mtimes_and_does_not_open_log(self):
        files = {'Racer a.chd': PAYLOAD, 'Racer b.chd': PAYLOAD,
                 'saves/profile.srm': b'save', 'retroarch.cfg': b'config'}
        result = self.run_fixture(files, {'ps2': [('Racer', [CRC])]},
                                  ['--system', 'ps2', '--dry-run'])
        self.assertEqual(result.stats['planned_crc'], 1)
        self.assertEqual(result.stats['skipped'], 1)
        self.assertIn('no log file will be written', result.output)

    def test_distance_zero_disables_fuzzy_matches_only(self):
        for args in (['--distance', '0'], ['-distance=0']):
            with self.subTest(args=args):
                result = self.run_fixture({'Racer a.iso': PAYLOAD, 'Unknown.iso': b'exact'},
                    {'ps2': [('Racer', []), ('Canonical', [f'{zlib.crc32(b"exact"):08X}'])]},
                    ['--system', 'PS2', '--dry-run', *args])
                self.assertEqual(result.stats['planned_crc'], 1)
                self.assertEqual(result.stats['planned_approximate'], 0)
                self.assertEqual(result.stats['no_match'], 1)

    def test_invalid_options_and_config_fail_closed(self):
        for args in (['--dry-rnu'], ['--dry'], ['--system'], ['--distance', '-1'],
                     ['--distance', 'oops'], ['--roms-dir', '/missing-rom-directory']):
            with self.subTest(args=args):
                result = self.run_fixture({'Racer.iso': PAYLOAD}, {}, args)
                self.assertEqual(result.error, 2)
                self.assertEqual(result.rom_reads, set())
        for config in ([], {'systems': []}, {'systems': {'ps2': {'dat': None}}},
                       {'nes': None}):
            with self.subTest(config=config):
                result = self.run_fixture({'Racer.iso': PAYLOAD}, {}, config=config)
                self.assertEqual(result.error, 2)

    def test_cartridge_matching_is_retained_in_mixed_default_mode(self):
        config = {'nes': 'nes.dat', 'iso': 'wrong.dat', 'chd': 'wrong.dat'}
        files = {'Unknown.nes': PAYLOAD, 'Racer.iso': PAYLOAD, 'Racer.chd': PAYLOAD}
        result = self.run_fixture(files, {'nes.dat': [('Canonical', [CRC])]},
                                  ['--dry-run'], config)
        self.assertEqual(result.stats['planned_crc'], 1)
        self.assertEqual(result.stats['skipped'], 2)
        self.assertEqual(result.dats, {'nes.dat'})

    def test_system_profile_extensions_cannot_be_reenabled_as_defaults(self):
        config = {'custom': 'wrong.dat', 'systems': {
            'console': {'dat': 'console.dat', 'extensions': ['custom']}}}
        result = self.run_fixture({'Racer.custom': PAYLOAD}, {}, ['--dry-run'], config)
        self.assertEqual(result.stats['processed'], 0)
        self.assertEqual(result.stats['skipped'], 1)

    def test_selected_system_still_previews_by_default_without_writing_a_journal(self):
        result = self.run_fixture({'Unknown.iso': PAYLOAD},
                                  {'ps2': [('Canonical', [CRC])]}, ['--system', 'ps2'])
        self.assertEqual(result.stats['planned_crc'], 1)
        self.assertEqual(result.renamed, [])
        self.assertIsNone(result.history)
        self.assertFalse(result.undo_exists)

    def test_selected_system_requires_approximate_even_with_apply(self):
        result = self.run_fixture({'Racer a.chd': PAYLOAD},
                                  {'ps2': [('Racer', [])]}, ['--system', 'ps2', '--apply'])
        self.assertEqual(result.renamed, [])
        self.assertIsNone(result.history)
        self.assertIn('add --approximate', result.warnings)

    def test_selected_system_renames_are_journaled_and_undoable_without_config(self):
        for kind in ('CRC', 'approximate'):
            with self.subTest(kind=kind):
                result = self.run_fixture({'Racer a.iso': PAYLOAD},
                    {'ps2': [('Racer', [CRC] if kind == 'CRC' else [])]},
                    ['--system', 'ps2', '--apply', '--approximate'], undo_after=True)
                self.assertEqual(result.renamed, [('Racer a.iso', 'Racer.iso')])
                self.assertEqual(result.history, [{'from': 'Racer a.iso', 'to': 'Racer.iso'}])
                self.assertEqual(result.restored, [('Racer.iso', 'Racer a.iso')])
                self.assertEqual(result.remaining, {'Racer a.iso': PAYLOAD})
                self.assertFalse(result.undo_exists)

    def test_conflicting_operation_flags_fail_before_any_file_io(self):
        for flags in (['--apply', '--dry-run'], ['--apply', '--undo'],
                      ['--dry-run', '--undo']):
            with self.subTest(flags=flags):
                result = self.run_fixture({'Racer.iso': PAYLOAD}, {}, flags)
                self.assertEqual(result.error, 2)
                self.assertEqual(result.rom_reads, set())

    def test_selected_system_retains_number_guard_from_safe_defaults(self):
        result = self.run_fixture({'Racer 2.iso': PAYLOAD}, {'ps2': [('Racer', [])]},
                                  ['--system', 'ps2', '--apply', '--approximate'])
        self.assertEqual(result.renamed, [])
        self.assertIsNone(result.history)


if __name__ == '__main__':
    unittest.main()
