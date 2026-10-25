"""Apply publishes recoverable intent before touching disposable ROM files."""

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


SCRIPT = Path(__file__).resolve().parents[1] / 'rename_roms.py'
PAYLOAD = b'disposable apply payload'
ENTRY = {'from': 'Original.nes', 'to': 'Canonical.nes'}


class ApplyJournalTests(unittest.TestCase):
    def prepare(self, root, entries=()):
        shutil.copyfile(SCRIPT, root / 'rename_roms.py')
        (root / 'config.json').write_text(json.dumps({'nes': 'games.dat'}))
        (root / 'games.dat').write_text(
            f'game ( name "Canonical" rom ( crc {zlib.crc32(PAYLOAD):08X} ) )\n')
        (root / ENTRY['from']).write_bytes(PAYLOAD)
        if entries:
            (root / 'rename_undo.json').write_text(json.dumps(entries))

    def run_script(self, root, mode='--apply', error=None):
        dependency = types.ModuleType('Levenshtein')
        dependency.distance = lambda left, right: 100
        with patch.dict('sys.modules', {'Levenshtein': dependency}), \
                patch('sys.argv', [str(root / 'rename_roms.py'), mode, '--roms-dir', str(root)]), \
                patch('logging.basicConfig'), patch('logging.FileHandler'), \
                patch('logging.getLogger', return_value=Mock()):
            if error:
                with self.assertRaises(error):
                    runpy.run_path(str(root / 'rename_roms.py'))
            elif mode == '--undo':
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(root / 'rename_roms.py'))
                self.assertEqual(stopped.exception.code, 0)
            else:
                runpy.run_path(str(root / 'rename_roms.py'))

    def test_failed_journal_publication_preserves_prior_history_and_never_moves_rom(self):
        old = [{'from': 'Old.nes', 'to': 'Old new.nes'}]

        def partial_dump(entries, stream, **kwargs):
            stream.write('[{"from":')
            raise OSError('fixture partial write')

        for stage in ('json.dump', 'os.fsync', 'os.replace'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.prepare(root, old)
                original = (root / 'rename_undo.json').read_bytes()
                failure = partial_dump if stage == 'json.dump' else OSError('fixture publication failed')
                with patch(stage, side_effect=failure), patch('os.rename', wraps=os.rename) as moves:
                    self.run_script(root, error=OSError)
                moves.assert_not_called()
                self.assertEqual((root / ENTRY['from']).read_bytes(), PAYLOAD)
                self.assertFalse((root / ENTRY['to']).exists())
                self.assertEqual((root / 'rename_undo.json').read_bytes(), original)
                self.assertEqual(list(root.glob('.rename_undo-*.tmp')), [])

    def test_intent_is_published_before_rename_and_successful_undo_removes_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            real_rename = os.rename

            def checked_rename(source, target):
                self.assertEqual(json.loads((root / 'rename_undo.json').read_text()), [ENTRY])
                real_rename(source, target)

            with patch('os.rename', side_effect=checked_rename):
                self.run_script(root)
            self.assertEqual((root / ENTRY['to']).read_bytes(), PAYLOAD)
            self.run_script(root, '--undo')
            self.assertEqual((root / ENTRY['from']).read_bytes(), PAYLOAD)
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_failed_move_keeps_intent_without_overwriting_original_on_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            with patch('os.rename', side_effect=PermissionError('fixture move denied')):
                self.run_script(root, error=PermissionError)
            self.assertEqual(json.loads((root / 'rename_undo.json').read_text()), [ENTRY])
            self.run_script(root, '--undo')
            self.assertEqual((root / ENTRY['from']).read_bytes(), PAYLOAD)
            self.assertEqual(json.loads((root / 'rename_undo.json').read_text()), [ENTRY])
            self.assertFalse((root / ENTRY['to']).exists())

    def test_interruption_after_move_leaves_a_recoverable_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            real_rename = os.rename

            def interrupted_rename(source, target):
                real_rename(source, target)
                raise OSError('fixture interruption after move')

            with patch('os.rename', side_effect=interrupted_rename):
                self.run_script(root, error=OSError)
            self.assertEqual((root / ENTRY['to']).read_bytes(), PAYLOAD)
            self.run_script(root, '--undo')
            self.assertEqual((root / ENTRY['from']).read_bytes(), PAYLOAD)
            self.assertFalse((root / 'rename_undo.json').exists())

    def test_later_journal_failure_preserves_earlier_applied_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            second_payload = b'second disposable payload'
            (root / 'Second.nes').write_bytes(second_payload)
            with (root / 'games.dat').open('a') as dat:
                dat.write(f'game ( name "Second canonical" rom ( crc {zlib.crc32(second_payload):08X} ) )\n')
            real_replace = os.replace
            publications = 0

            def fail_second(source, target):
                nonlocal publications
                publications += 1
                if publications == 2:
                    raise OSError('fixture second publication failed')
                real_replace(source, target)

            with patch('os.replace', side_effect=fail_second):
                self.run_script(root, error=OSError)
            self.assertEqual(json.loads((root / 'rename_undo.json').read_text()), [ENTRY])
            self.assertEqual((root / ENTRY['to']).read_bytes(), PAYLOAD)
            self.assertEqual((root / 'Second.nes').read_bytes(), second_payload)
            self.assertFalse((root / 'Second canonical.nes').exists())
            self.assertEqual(list(root.glob('.rename_undo-*.tmp')), [])
            self.run_script(root, '--undo')
            self.assertEqual((root / ENTRY['from']).read_bytes(), PAYLOAD)
            self.assertFalse((root / 'rename_undo.json').exists())


if __name__ == '__main__':
    unittest.main()
