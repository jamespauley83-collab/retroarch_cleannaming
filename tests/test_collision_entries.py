"""Protect separate directory entries even when Unicode folding and inode IDs agree."""

from contextlib import ExitStack
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
from test_undo_history import CaseInsensitiveDirectory, native_case_insensitive


SCRIPT = Path(__file__).resolve().parents[1] / 'rename_roms.py'


class CollisionEntryTests(unittest.TestCase):
    def test_alias_of_distinct_unicode_entry_is_protected_in_preview_apply_and_undo(self):
        for kind in ('file', 'hardlink', 'symlink'):
            for operation in ('crc', 'approximate', 'undo'):
                modes = ('--undo',) if operation == 'undo' else ('--dry-run', '--apply')
                for mode in modes:
                    with self.subTest(kind=kind, operation=operation, mode=mode), \
                            tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        script = root / 'rename_roms.py'
                        shutil.copyfile(SCRIPT, script)
                        source, occupied = root / 'Straße.nes', root / 'Strasse.nes'
                        payload = b'disposable original'
                        source.write_bytes(payload)
                        if occupied.exists():
                            self.skipTest('requires distinct sharp-s and ss entries')
                        try:
                            if kind == 'file':
                                occupied.write_bytes(b'distinct occupied file')
                            elif kind == 'hardlink':
                                os.link(source, occupied)
                            else:
                                occupied.symlink_to(source)
                        except OSError:
                            self.skipTest(f'{kind} unavailable')
                        entries = [{'from': 'STRASSE.nes', 'to': source.name}]
                        if operation == 'undo':
                            (root / 'rename_undo.json').write_text(json.dumps(entries))
                        else:
                            (root / 'config.json').write_text(json.dumps({'nes': 'games.dat'}))
                            crc = f'{zlib.crc32(payload):08X}' if operation == 'crc' else '00000000'
                            (root / 'games.dat').write_text(
                                f'game ( name "STRASSE" rom ( crc {crc} ) )\n')

                        def snapshot():
                            return {path.name: (path.lstat().st_ino, path.lstat().st_nlink,
                                                path.lstat().st_mtime_ns,
                                                os.readlink(path) if path.is_symlink()
                                                else path.read_bytes())
                                    for path in (source, occupied)}

                        baseline = snapshot()
                        filesystem = CaseInsensitiveDirectory(root, lookup_key=str.lower)
                        simulate = not native_case_insensitive(root)
                        real_rename = os.rename

                        def rename(old, new):
                            if simulate:
                                resolved_old, resolved_new = filesystem.resolve(old), filesystem.resolve(new)
                                new = new if resolved_new == resolved_old else resolved_new
                                old = resolved_old
                            real_rename(old, new)

                        dependency = types.ModuleType('Levenshtein')
                        dependency.distance = levenshtein_distance
                        arguments = [str(script), mode]
                        if operation == 'approximate':
                            arguments.append('--approximate')
                        with ExitStack() as stack, \
                                patch.dict('sys.modules', {'Levenshtein': dependency}), \
                                patch('sys.argv', arguments), \
                                patch('logging.basicConfig'), patch('logging.FileHandler'), \
                                patch('logging.getLogger', return_value=Mock()), \
                                patch('os.rename', side_effect=rename) as moves:
                            if simulate:
                                stack.enter_context(patch('os.path.exists', side_effect=filesystem.exists))
                                stack.enter_context(patch('os.path.lexists', side_effect=filesystem.lexists))
                                stack.enter_context(patch('os.path.samefile', side_effect=filesystem.samefile))
                            if operation == 'undo':
                                with self.assertRaises(SystemExit) as stopped:
                                    runpy.run_path(str(script))
                                self.assertEqual(stopped.exception.code, 0)
                            else:
                                result = runpy.run_path(str(script))
                                self.assertEqual(result['stats']['planned_crc'], 0)
                                self.assertEqual(result['stats']['planned_approximate'], 0)
                            moves.assert_not_called()
                        self.assertEqual(snapshot(), baseline)
                        if operation == 'undo':
                            self.assertEqual(json.loads((root / 'rename_undo.json').read_text()), entries)
                        else:
                            self.assertFalse((root / 'rename_undo.json').exists())


if __name__ == '__main__':
    unittest.main()
