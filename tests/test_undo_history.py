"""Undo retries retain unresolved applied renames in disposable folders."""

import json
from contextlib import ExitStack
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'rename_roms.py'


def native_case_insensitive(root):
    """Read-only check using the ASCII script name present in every fixture."""
    alternate = root / 'RENAME_ROMS.PY'
    return (alternate.name not in os.listdir(root) and alternate.exists()
            and os.path.samefile(root / 'rename_roms.py', alternate))


class CaseInsensitiveDirectory:
    """Model case-insensitive lookup over real disposable files on any host.

    Preserve directory-entry spelling and perform real renames; only lookup is
    adapted. This does not claim to replace testing on native APFS or NTFS.
    """
    def __init__(self, root, lookup_key=str.casefold):
        self.root = root
        self.lookup_key = lookup_key
        self.real_exists = os.path.exists
        self.real_lexists = os.path.lexists
        self.real_samefile = os.path.samefile

    def resolve(self, path):
        path = Path(path)
        if path.parent == self.root:
            for child in self.root.iterdir():
                if self.lookup_key(child.name) == self.lookup_key(path.name):
                    return child
        return path

    def exists(self, path):
        return self.real_exists(self.resolve(path))

    def lexists(self, path):
        return self.real_lexists(self.resolve(path))

    def samefile(self, left, right):
        return self.real_samefile(self.resolve(left), self.resolve(right))


class UndoHistoryTests(unittest.TestCase):
    def prepare(self, root, entries, files):
        shutil.copyfile(SCRIPT, root / 'rename_roms.py')
        (root / 'rename_undo.json').write_text(json.dumps(entries))
        for filename, contents in files.items():
            (root / filename).write_bytes(contents)

    def run_undo(self, root, fail=(), expected_error=None, insensitive=False):
        dependency = types.ModuleType('Levenshtein')
        dependency.distance = lambda left, right: 0
        logger = Mock()
        real_rename = os.rename
        filesystem = CaseInsensitiveDirectory(root)
        if insensitive and native_case_insensitive(root):
            insensitive = False

        def rename(source, target):
            if Path(source).name in fail:
                raise PermissionError('fixture restore denied')
            if insensitive:
                resolved_source = filesystem.resolve(source)
                resolved_target = filesystem.resolve(target)
                # A case-only rename must update the stored spelling; any other
                # existing destination behaves like a real occupied path.
                if resolved_target != resolved_source:
                    target = resolved_target
                source = resolved_source
            real_rename(source, target)

        with ExitStack() as stack, \
                patch.dict('sys.modules', {'Levenshtein': dependency}), \
                patch('sys.argv', [str(root / 'rename_roms.py'), '--undo', '--roms', str(root),
                                   '--config', str(root / 'missing-config.json')]), \
                patch('os.rename', side_effect=rename) as calls, \
                patch('logging.basicConfig'), patch('logging.FileHandler'), \
                patch('logging.getLogger', return_value=logger):
            if insensitive:
                stack.enter_context(patch('os.path.exists', side_effect=filesystem.exists))
                stack.enter_context(patch('os.path.lexists', side_effect=filesystem.lexists))
                stack.enter_context(patch('os.path.samefile', side_effect=filesystem.samefile))
            with self.assertRaises(expected_error or SystemExit) as stopped:
                runpy.run_path(str(root / 'rename_roms.py'))
            if expected_error is None:
                self.assertEqual(stopped.exception.code, 0)
        return calls.call_count, logger

    def history(self, root):
        return json.loads((root / 'rename_undo.json').read_text())

    def test_case_only_restore_preserves_bytes_and_restores_stored_spelling(self):
        for insensitive in (False, True):
            with self.subTest(insensitive=insensitive), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entries = [{'from': 'game.iso', 'to': 'Game.iso'}]
                self.prepare(root, entries, {'Game.iso': b'original'})
                count, _ = self.run_undo(root, insensitive=insensitive)
                self.assertEqual(count, 1)
                self.assertIn('game.iso', os.listdir(root))
                self.assertNotIn('Game.iso', os.listdir(root))
                self.assertEqual((root / 'game.iso').read_bytes(), b'original')
                self.assertFalse((root / 'rename_undo.json').exists())

    def test_case_variant_conflict_blocks_older_chain_until_retry(self):
        entries = [{'from': 'Original.iso', 'to': 'MIDDLE.iso'},
                   {'from': 'middle.iso', 'to': 'Final.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'middle.iso': b'unrelated', 'Final.iso': b'original'})
            for _ in range(2):
                count, _ = self.run_undo(root, insensitive=True)
                self.assertEqual(count, 0)
                self.assertEqual(self.history(root), entries)
                self.assertEqual((root / 'middle.iso').read_bytes(), b'unrelated')
                self.assertEqual((root / 'Final.iso').read_bytes(), b'original')
                self.assertFalse((root / 'Original.iso').exists())
            (root / 'middle.iso').unlink()
            count, _ = self.run_undo(root, insensitive=True)
            self.assertEqual(count, 2)
            self.assertEqual((root / 'Original.iso').read_bytes(), b'original')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_case_only_chain_propagates_conflict_and_restores_after_resolution(self):
        entries = [{'from': 'Original.iso', 'to': 'middle.iso'},
                   {'from': 'middle.iso', 'to': 'Middle.iso'},
                   {'from': 'Middle.iso', 'to': 'Final.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'middle.iso': b'conflict', 'Final.iso': b'original'})
            count, _ = self.run_undo(root, insensitive=True)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries)
            (root / 'middle.iso').unlink()
            count, _ = self.run_undo(root, insensitive=True)
            self.assertEqual(count, 3)
            self.assertEqual((root / 'Original.iso').read_bytes(), b'original')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_failed_case_only_restore_retains_dependent_steps(self):
        entries = [{'from': 'Original.iso', 'to': 'game.iso'},
                   {'from': 'game.iso', 'to': 'Game.iso'},
                   {'from': 'Other.iso', 'to': 'Other new.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'Game.iso': b'original', 'Other new.iso': b'other'})
            count, logger = self.run_undo(root, fail={'Game.iso'}, insensitive=True)
            self.assertEqual(count, 2)
            self.assertIn('fixture restore denied', str(logger.warning.call_args_list))
            self.assertEqual(self.history(root), entries[:2])
            self.assertEqual((root / 'Game.iso').read_bytes(), b'original')
            self.assertEqual((root / 'Other.iso').read_bytes(), b'other')
            self.run_undo(root, insensitive=True)
            self.assertEqual((root / 'Original.iso').read_bytes(), b'original')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_distinct_case_variant_files_are_never_overwritten(self):
        entries = [{'from': 'game.iso', 'to': 'Game.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'Game.iso': b'original'})
            if (root / 'game.iso').exists():
                self.skipTest('requires a case-sensitive directory')
            (root / 'game.iso').write_bytes(b'conflict')
            count, _ = self.run_undo(root)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries)
            self.assertEqual((root / 'Game.iso').read_bytes(), b'original')
            self.assertEqual((root / 'game.iso').read_bytes(), b'conflict')

    def test_case_variant_hardlinks_are_not_mistaken_for_one_directory_entry(self):
        entries = [{'from': 'game.iso', 'to': 'Game.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'Game.iso': b'original'})
            if (root / 'game.iso').exists():
                self.skipTest('requires a case-sensitive directory')
            try:
                os.link(root / 'Game.iso', root / 'game.iso')
            except OSError:
                self.skipTest('hardlinks unavailable')
            count, _ = self.run_undo(root)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries)
            self.assertIn('game.iso', os.listdir(root))
            self.assertIn('Game.iso', os.listdir(root))

    def test_dangling_symlink_at_restore_destination_is_a_conflict(self):
        entries = [{'from': 'Original.iso', 'to': 'Renamed.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'Renamed.iso': b'original'})
            try:
                (root / 'Original.iso').symlink_to(root / 'missing.iso')
            except OSError:
                self.skipTest('symlinks unavailable')
            count, _ = self.run_undo(root)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries)
            self.assertTrue((root / 'Original.iso').is_symlink())
            self.assertEqual((root / 'Renamed.iso').read_bytes(), b'original')

    def test_conflicting_restore_keeps_only_unresolved_entries_for_retry(self):
        entries = [{'from': 'First.iso', 'to': 'First new.iso'},
                   {'from': 'Second.iso', 'to': 'Second new.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'First.iso': b'conflict', 'First new.iso': b'first',
                                         'Second new.iso': b'second'})
            count, _ = self.run_undo(root)
            self.assertEqual(count, 1)
            self.assertEqual(self.history(root), entries[:1])
            self.assertEqual((root / 'Second.iso').read_bytes(), b'second')
            self.assertEqual((root / 'First.iso').read_bytes(), b'conflict')
            count, _ = self.run_undo(root)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries[:1])
            (root / 'First.iso').unlink()
            count, _ = self.run_undo(root)
            self.assertEqual(count, 1)
            self.assertEqual((root / 'First.iso').read_bytes(), b'first')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_missing_renamed_file_remains_retryable(self):
        entries = [{'from': 'Original.iso', 'to': 'Renamed.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {})
            for _ in range(2):
                count, _ = self.run_undo(root)
                self.assertEqual(count, 0)
                self.assertEqual(self.history(root), entries)
            (root / 'Renamed.iso').write_bytes(b'returned')
            self.run_undo(root)
            self.assertEqual((root / 'Original.iso').read_bytes(), b'returned')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_failed_filesystem_restore_keeps_history_and_allows_other_restores(self):
        entries = [{'from': 'First.iso', 'to': 'First new.iso'},
                   {'from': 'Second.iso', 'to': 'Second new.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'First new.iso': b'first', 'Second new.iso': b'second'})
            count, logger = self.run_undo(root, fail={'Second new.iso'})
            self.assertEqual(count, 2)
            self.assertEqual(self.history(root), entries[1:])
            self.assertEqual((root / 'First.iso').read_bytes(), b'first')
            self.assertEqual((root / 'Second new.iso').read_bytes(), b'second')
            self.assertIn('fixture restore denied', str(logger.warning.call_args_list))
            self.run_undo(root)
            self.assertEqual((root / 'Second.iso').read_bytes(), b'second')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_failed_chain_does_not_consume_a_conflicting_intermediate_name(self):
        entries = [{'from': 'Original.iso', 'to': 'Middle.iso'},
                   {'from': 'Middle.iso', 'to': 'Final.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {'Middle.iso': b'unrelated conflict', 'Final.iso': b'original'})
            count, _ = self.run_undo(root)
            self.assertEqual(count, 0)
            self.assertEqual(self.history(root), entries)
            self.assertEqual((root / 'Middle.iso').read_bytes(), b'unrelated conflict')
            self.assertEqual((root / 'Final.iso').read_bytes(), b'original')
            self.assertFalse((root / 'Original.iso').exists())
            (root / 'Middle.iso').unlink()
            count, _ = self.run_undo(root)
            self.assertEqual(count, 2)
            self.assertEqual((root / 'Original.iso').read_bytes(), b'original')
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_partial_journal_write_failure_preserves_original_and_removes_temp(self):
        entries = [{'from': 'Original.iso', 'to': 'Missing.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {})
            original = (root / 'rename_undo.json').read_bytes()

            def fail_after_partial_write(entries, stream, **kwargs):
                stream.write('[{"from":')
                raise OSError('fixture disk full during journal write')

            with patch('json.dump', side_effect=fail_after_partial_write):
                self.run_undo(root, expected_error=OSError)
            self.assertEqual((root / 'rename_undo.json').read_bytes(), original)
            self.assertEqual(list(root.glob('.rename_undo-*.tmp')), [])

    def test_journal_fsync_failure_preserves_original_and_removes_temp(self):
        entries = [{'from': 'Original.iso', 'to': 'Missing.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {})
            original = (root / 'rename_undo.json').read_bytes()
            with patch('os.fsync', side_effect=OSError('fixture journal sync failed')):
                self.run_undo(root, expected_error=OSError)
            self.assertEqual((root / 'rename_undo.json').read_bytes(), original)
            self.assertEqual(list(root.glob('.rename_undo-*.tmp')), [])

    def test_journal_replace_failure_preserves_original_and_removes_temp(self):
        entries = [{'from': 'Original.iso', 'to': 'Missing.iso'}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root, entries, {})
            original = (root / 'rename_undo.json').read_bytes()
            with patch('os.replace', side_effect=OSError('fixture atomic replace failed')):
                self.run_undo(root, expected_error=OSError)
            self.assertEqual((root / 'rename_undo.json').read_bytes(), original)
            self.assertEqual(list(root.glob('.rename_undo-*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
