"""Undo retries retain unresolved applied renames in disposable folders."""

import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'rename_roms.py'


class UndoHistoryTests(unittest.TestCase):
    def prepare(self, root, entries, files):
        shutil.copyfile(SCRIPT, root / 'rename_roms.py')
        (root / 'rename_undo.json').write_text(json.dumps(entries))
        for filename, contents in files.items():
            (root / filename).write_bytes(contents)

    def run_undo(self, root, fail=(), expected_error=None):
        dependency = types.ModuleType('Levenshtein')
        dependency.distance = lambda left, right: 0
        logger = Mock()
        real_rename = os.rename

        def rename(source, target):
            if Path(source).name in fail:
                raise PermissionError('fixture restore denied')
            real_rename(source, target)

        with patch.dict('sys.modules', {'Levenshtein': dependency}), \
                patch('sys.argv', [str(root / 'rename_roms.py'), '--undo', '--roms', str(root),
                                   '--config', str(root / 'missing-config.json')]), \
                patch('os.rename', side_effect=rename) as calls, \
                patch('logging.basicConfig'), patch('logging.FileHandler'), \
                patch('logging.getLogger', return_value=logger):
            with self.assertRaises(expected_error or SystemExit) as stopped:
                runpy.run_path(str(root / 'rename_roms.py'))
            if expected_error is None:
                self.assertEqual(stopped.exception.code, 0)
        return calls.call_count, logger

    def history(self, root):
        return json.loads((root / 'rename_undo.json').read_text())

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
