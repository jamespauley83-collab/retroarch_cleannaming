# RetroArch CleanNaming

Rename ROM files to libretro database titles, using CRC32 first and approximate
filename matching with `--approximate` when no checksum matches. **Preview is the default; only `--apply` renames.**

## Setup

From this repository, with Python 3 installed:

```sh
git clone https://github.com/libretro/libretro-database.git
python3 -m pip install -r requirements.txt
python3 rename_roms.py --help
```

On Windows, use `py -3` instead of `python3` if needed. If `libretro-database`
already exists, reuse it; do not clone over an existing directory.

## Safe previews of your existing collection

Use the updated script from this branch or a release containing this correction.
Run one command per **existing, single-console folder**. Replace these example
paths with your actual folders; you do not need to copy, move, or reorganize games.
The following commands work in PowerShell from this repository:

```powershell
py -3 .\rename_roms.py --system ps2 --roms-dir "D:\ROMs\PS2" --dry-run
py -3 .\rename_roms.py --system psp --roms-dir "D:\ROMs\PSP" --dry-run
py -3 .\rename_roms.py --system psx --roms-dir "D:\ROMs\PlayStation" --dry-run
py -3 .\rename_roms.py --system gamecube --roms-dir "D:\ROMs\GameCube" --dry-run
```

On Linux/macOS, for example:

```sh
python3 rename_roms.py --system ps2 --roms-dir "/path/to/ROMs/PS2" --dry-run
python3 rename_roms.py --system psp --roms-dir "/path/to/ROMs/PSP" --dry-run
python3 rename_roms.py --system psx --roms-dir "/path/to/ROMs/PlayStation" --dry-run
python3 rename_roms.py --system gamecube --roms-dir "/path/to/ROMs/GameCube" --dry-run
```

These commands read matching files directly in the selected folder. They do not
recurse, rename or rewrite ROMs, modify saves/save states or RetroArch settings,
or create/append `rename_log.txt`. An existing log is also left unchanged. Review
`System:`, `directory:`, and `database:` in the output, then each `[DRY-RUN] Would
rename` line and the summary. Output identifies CRC versus approximate matches;
planned counts are separate from actual rename counts. Existing target filenames
and targets reserved by earlier planned operations are skipped.

`--system` is your declaration of the folder's console, **not automatic detection**.
An ISO does not identify its console. Do not select one console for a folder that
contains PS2, PSP, PlayStation, and GameCube images mixed together. Instead point
to an existing console subfolder, or test disposable copies in separate folders.
Without `--system`, shared disc formats are skipped, including when an older
config still maps `iso` to PSP or `chd` to PlayStation. The script does not infer
the console from folder names and never tries another console's DAT after a miss.

By default only CRC matches are planned; possible fuzzy matches are reported but
not used. Add `--approximate` to include fuzzy matches in a preview. To suppress
fuzzy suggestions entirely, add `--distance 0`:

```powershell
py -3 .\rename_roms.py --system ps2 --roms-dir "D:\ROMs\PS2" --dry-run --distance 0
```

CHD, CSO, and RVZ are containers: their stored-file CRC32 generally differs from
the checksum of the original dump in a DAT. This tool does not decompress or
convert images. They usually require `--approximate` to plan filename matches, which
is a suggestion to review and does not verify a dump. An unrecognized checksum
or filename is not evidence that a game is damaged.

Do not apply these previews to the original collection until you have reviewed
and backed up the collection. **Only `--apply` performs renames** and writes
`rename_log.txt` beside the script; omitting `--dry-run` still previews.
For example, after reviewing a PS2 preview:

```sh
python3 rename_roms.py --system ps2 --roms-dir "/path/to/ROMs/PS2" --apply
# Only if the approximate suggestions were also reviewed:
python3 rename_roms.py --system ps2 --roms-dir "/path/to/ROMs/PS2" --apply --approximate
```

Before each applied rename, its intent is recorded in `rename_undo.json` in the
selected ROM folder. Both apply and undo publish a complete journal through a
same-directory temporary file, flush and sync it, then atomically replace the
previous journal. A journal publication failure stops apply before moving the
next ROM, preserving earlier recovery records. If the move itself fails or the
process stops before it, the uncompleted intent is retained for inspection; do
not assume every journal entry represents a completed move.
`python3 rename_roms.py --roms-dir "/path/to/ROMs/PS2" --undo` restores recorded
filenames without needing the current DAT or `--system`. Keep that journal until
you have checked the results. Missing files, conflicting names, and failed restores
remain in the journal for a later `--undo`; successfully restored entries are removed.
If one step of a rename chain cannot be restored, dependent steps wait as well.
Dependency comparisons ignore case so an unresolved spelling such as `Game.iso`
also blocks older records using `game.iso`. This deliberately errs on the side
of retaining history on case-sensitive volumes too. A case-only restore is
allowed when both names resolve to one directory entry and the original spelling
is absent; distinct files or hardlinks are never treated as a case-only rename.
An occupied original name, including a dangling symlink, remains a conflict.
Undo does not restore other files or associations. The tool does not update playlists,
CUE references, save names, save-state names, or RetroArch settings. A later ROM
rename can break those associations even though their contents are untouched.
BIN/CUE sets, SBI sidecars, and M3U playlists are therefore not renamed by this
single-file workflow. Previewing does not change any of these associations.
For an existing RetroArch Lab collection, keep using previews until the specific
playlist entries, core-specific saves/save states, and any sidecar references
have been checked on disposable copies. Do not run apply or undo concurrently
with another renamer or while another program changes the same folder. The
journal protects recovery records; it is not a collection backup or a lock.

## Console profiles and configuration

`config.json` retains extension defaults for cartridge formats, and adds explicit
console profiles under `systems`:

| `--system` | Console | Eligible extensions | DAT under `libretro-database/metadat/` |
| --- | --- | --- | --- |
| `ps2` | PlayStation 2 | `.iso`, `.chd` | `redump/Sony - PlayStation 2.dat` |
| `psp` | PlayStation Portable | `.iso`, `.cso` | `no-intro/Sony - PlayStation Portable.dat` |
| `psx` | Original PlayStation | `.iso`, `.chd` | `redump/Sony - PlayStation.dat` |
| `gamecube` | GameCube | `.iso`, `.gcm`, `.rvz` | `redump/Nintendo - GameCube.dat` |

This table describes files accepted for naming, not emulator compatibility.
Extensions and the supplied system option are case-insensitive. Unsupported
extensions and subdirectories are ignored. Symlinked files are skipped.

A selected profile replaces all extension defaults for that invocation. Both CRC
and approximate matching use only that profile's DAT. An unknown console, missing
selected DAT, invalid configuration, or invalid CLI option fails before renaming
or opening a log. The shared-checksum safeguards from PR #8 remain: multiple
candidate titles require unique exact or normalized filename evidence; ambiguous
CRCs leave the file unchanged without falling back to fuzzy matching.

Example custom configuration (partial):

```json
{
  "nes": "libretro-database/metadat/no-intro/Nintendo - Nintendo Entertainment System.dat",
  "systems": {
    "ps2": {
      "dat": "libretro-database/metadat/redump/Sony - PlayStation 2.dat",
      "extensions": ["iso", "chd"]
    }
  }
}
```

Relative DAT paths are resolved from the **configuration file's directory**, not
from the ROM directory or shell's working directory. Use `--config PATH` to load
a different file. Check that each profile points to the correct console DAT;
custom configurations are trusted declarations. Extension-only defaults cannot
reactivate protected disc formats or extensions declared in any system profile.
Old flat cartridge configurations continue to work. Migrate old disc mappings
to a system profile instead of adding them back as extension defaults.

For a cartridge-only preview:

```sh
python3 rename_roms.py --roms-dir "/path/to/cartridge-ROMs" --dry-run
```

`--roms` is an alias for `--roms-dir`, including the existing `--roms=DIR` form.
If `--roms-dir` is omitted, the script directory is scanned, preserving the
previous default. Repository `README.md` and `AGENTS.md` are excluded from Mega
Drive `.md` detection. Keep project documents separate from ROM collections.

## Other options

- `--apply`: apply renames; without this flag the script only previews.
- `--approximate`: opt in to fuzzy matching; names with different numbers are excluded.
- `--undo`: reverse recorded renames in the selected folder. Operation flags cannot be combined.
- `--distance N` / `-distance=N`: exclusive approximate-match distance threshold;
  default 10. Higher values permit less similar suggestions. `0` disables fuzzy
  matching while retaining exact CRC matching.
- `--verbose` / `-verbose` / `-v`: show CRC and matching diagnostics.
- `--dry-run` / `-dry-run`: preview only, with console output and no log file.
- `--help`: show usage; unknown or abbreviated options are rejected so a typo
  cannot silently turn a preview into an apply run.

## Disposable regression tests

```sh
python3 -m unittest discover -s tests -v
```

Tests create synthetic bytes and DATs in temporary directories; no original ROM
collection is needed or accessed. They compare planned and actual renames on
those disposable files, exercise all four console profiles and competing CRC /
fuzzy candidates, preserve the duplicate-CRC regressions, and check that previews
leave file contents, names, and modification times unchanged. The unit suite
uses a standard-library matching substitute and does not require downloaded DATs.
Undo regressions model case-insensitive lookup over real temporary files and
also exercise native case-sensitive conflicts and hardlinks where supported;
the model is not a native macOS/Windows filesystem certification.

With `requirements.txt` installed, the same command also runs eight real-library
CLI scenarios (four consoles x CRC/approximate). Without that dependency, the
two CLI test methods are skipped. These scenarios check preview immutability,
explicit approximate opt-in, application, repeated conflicts, and complete undo
after removal of the configuration and DATs. Apply tests also inject partial
journal writes, sync/replace failures, failed moves, and interruption after a move.

## Docker / Base44 preview

The status page shows readiness and detected files; it does not run renames.
Use a console-specific directory already visible inside the container:

```sh
docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py --system ps2 --roms-dir /app/roms/PS2 --dry-run
```

For example, add `- /your/ROMs:/app/roms:ro` to the Compose service volumes
for a read-only preview mount; `/app/roms` is not created automatically.
Use an existing read-only ROM mount when previewing a collection in Docker. Do
not copy your original collection into the repository just to use this command.
