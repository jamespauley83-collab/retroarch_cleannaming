# AGENTS.md

## Project Overview
This is a CLI Python script (`rename_roms.py`) that renames retro game ROM files to match libretro database naming conventions. It is NOT a web application — there is no web server, frontend, or API.

## Dependencies
- Python 3
- `python-Levenshtein` (see `requirements.txt`)
- The `libretro-database` repo cloned at `./libretro-database/` (cloned automatically by the compose startup)

## Running
```
python3 rename_roms.py --dry-run           # preview cartridge defaults
python3 rename_roms.py --system ps2 --roms-dir /path/to/PS2 --dry-run
python3 rename_roms.py --dry-run -distance=15   # custom fuzzy match distance
```
Use `--roms-dir` to read an existing single-console directory non-recursively; the default is the script directory. Disc images require an explicit `--system` profile (`ps2`, `psp`, `psx`, or `gamecube`). The script matches by CRC32 first, then falls back to Levenshtein fuzzy matching within the selected DAT only. Dry runs do not write a log; omitting `--dry-run` applies renames.

## Docker Environment
- `docker-compose.base44.yml` runs a `python:3.12-slim` container with the source bind-mounted at `/app`.
- On startup it clones `libretro-database` (if not present) and installs Python dependencies.
- A lightweight status page (`preview_server.py`) is served on port 3000 so the Base44 preview has an endpoint. The CLI tool itself is still run via exec:
  ```
  docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py --dry-run
  ```
- The `libretro-database` clone persists in a named volume so it isn't re-cloned on restart.

## Preview
- `preview_server.py` serves a lightweight status dashboard on port 3000 so the Base44 preview has something to display.
- It shows environment readiness (libretro-database clone, Python deps) and detected ROM files, plus usage instructions.
- The actual renaming tool is still run via `docker compose exec app python3 rename_roms.py`.

## Notes
- `config.json` keeps cartridge extension defaults and a `systems` object of console profiles, each with a `dat` path and `extensions` list. Relative DAT paths are resolved beside the config file. Legacy extension-only disc mappings are blocked.
- `docker-compose.base44.yml` must define `ports:` exactly once. A prior merge introduced a duplicate `ports:` key, which made `docker compose` fail to parse the file with a YAML "mapping key already defined" error — keep a single `ports:` block.

## Testing
- Run dashboard regression tests with `python3 -m unittest discover -s tests -v`.
- These use temporary fixtures and require only the Python standard library.
