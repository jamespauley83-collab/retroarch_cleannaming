"""Optional real-dependency CLI acceptance: four consoles x CRC/approximate.

Install requirements.txt to enable these tests. All data is synthetic and every
invocation, including --apply and --undo, is confined to a temporary directory.
"""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib


REPO = Path(__file__).resolve().parents[1]
SYSTEMS = {'ps2': 'Racer PlayStation Two', 'psp': 'Racer Portable',
           'psx': 'Racer PlayStation', 'gamecube': 'Racer GameCube'}


@unittest.skipUnless(importlib.util.find_spec('Levenshtein'), 'install requirements.txt for CLI acceptance')
class RealDependencyCliTests(unittest.TestCase):
    def exercise_console(self, system, approximate):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, roms, settings = (root / part for part in ('app', 'roms', 'settings'))
            for path in (app, roms, settings):
                path.mkdir()
            script = app / 'rename_roms.py'
            shutil.copyfile(REPO / 'rename_roms.py', script)
            payload = b'synthetic disc fixture, never a real game'
            crc = '00000000' if approximate else f'{zlib.crc32(payload):08X}'
            config = {'systems': {key: {'dat': f'{key}.dat', 'extensions': ['iso']}
                                  for key in SYSTEMS}}
            config_path = settings / 'config.json'
            config_path.write_text(json.dumps(config))
            # Every other console deliberately has competing checksum/name data.
            for key, title in SYSTEMS.items():
                (settings / f'{key}.dat').write_text(f'game ( name "{title}" rom ( crc {crc} ) )\n')
            original = f'{SYSTEMS[system]} draft.iso'
            canonical = f'{SYSTEMS[system]}.iso'
            (roms / original).write_bytes(payload)
            (roms / 'saves').mkdir()
            (roms / 'saves' / 'game.srm').write_bytes(b'untouched save fixture')
            (roms / 'playlist.m3u').write_text(original + '\n')
            (roms / 'retroarch.cfg').write_text('untouched settings fixture\n')
            (roms / 'OtherConsole').mkdir()
            (roms / 'OtherConsole' / 'Other.iso').write_bytes(b'untouched nested fixture')

            def snapshot(path):
                return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mtime_ns)
                        for p in path.rglob('*') if p.is_file()}

            def invoke(*args, success=True):
                result = subprocess.run(
                    [sys.executable, str(script), '--config', str(config_path),
                     '--roms-dir', str(roms), *args], cwd=root,
                    text=True, capture_output=True, timeout=30)
                if success:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                return result.stdout + result.stderr

            baseline = snapshot(root)
            self.assertIn('console is ambiguous', invoke())
            invoke('--system', 'not-a-console', success=False)
            matching = ['--approximate'] if approximate else []
            for mode in ([], ['--dry-run']):
                output = invoke('--system', system, *mode, *matching)
                self.assertIn(f'Would rename: {original} -> {canonical}', output)
                self.assertIn('0 renamed (CRC), 0 renamed (approximate)', output)
                self.assertIn(f'database: {settings / (system + ".dat")}', output)
                self.assertEqual(snapshot(root), baseline)
            if approximate:
                self.assertIn('add --approximate', invoke('--system', system, '--apply'))
                self.assertTrue((roms / original).exists())
                self.assertFalse((roms / 'rename_undo.json').exists())
            invoke('--system', system, '--apply', *matching)
            entries = [{'from': original, 'to': canonical}]
            self.assertEqual(json.loads((roms / 'rename_undo.json').read_text()), entries)
            self.assertEqual((roms / canonical).read_bytes(), payload)
            self.assertFalse((roms / original).exists())
            config_path.unlink()
            for dat in settings.glob('*.dat'):
                dat.unlink()
            (roms / original).write_bytes(b'unrelated conflict')
            blocked = snapshot(roms)
            for _ in range(2):
                self.assertIn('0 of 1 rename(s) restored', invoke('--undo'))
                self.assertEqual(json.loads((roms / 'rename_undo.json').read_text()), entries)
                after = snapshot(roms)
                for name, value in blocked.items():
                    if name != 'rename_undo.json':
                        self.assertEqual(after[name], value)
            (roms / original).unlink()
            self.assertIn('1 of 1 rename(s) restored', invoke('--undo'))
            self.assertFalse((roms / 'rename_undo.json').exists())
            self.assertFalse((roms / canonical).exists())
            # Include bytes, stored names and mtimes of every fixture in the folder.
            self.assertEqual(snapshot(roms), {name.removeprefix('roms/'): value
                                            for name, value in baseline.items()
                                            if name.startswith('roms/')})

    def test_crc_cli_acceptance_for_all_four_consoles(self):
        for system in SYSTEMS:
            with self.subTest(system=system):
                self.exercise_console(system, approximate=False)

    def test_approximate_cli_acceptance_for_all_four_consoles(self):
        for system in SYSTEMS:
            with self.subTest(system=system):
                self.exercise_console(system, approximate=True)


if __name__ == '__main__':
    unittest.main()
