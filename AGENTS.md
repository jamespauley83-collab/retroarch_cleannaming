# AGENTS.md

## Project Overview
This is a CLI Python script (`rename_roms.py`) that renames retro game ROM files to match libretro database naming conventions. It is NOT a web application — there is no web server, frontend, or API.

## Dependencies
- Python 3
- `python-Levenshtein` (see `requirements.txt`)
- The `libretro-database` repo cloned at `./libretro-database/` (cloned automatically by the compose startup)

## Running
```
python3 rename_roms.py           # default distance threshold of 10
python3 rename_roms.py -distance=15   # custom fuzzy match distance
```
ROM files must be placed in the working directory (`/app` inside the container). The script matches by CRC32 first, then falls back to Levenshtein fuzzy matching.

## Docker Environment
- `docker-compose.base44.yml` runs a `python:3.12-slim` container with the source bind-mounted at `/app`.
- On startup it clones `libretro-database` (if not present) and installs Python dependencies.
- A lightweight status page (`preview_server.py`) is served on port 3000 so the Base44 preview has an endpoint. The CLI tool itself is still run via exec:
  ```
  docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py
  ```
- The `libretro-database` clone persists in a named volume so it isn't re-cloned on restart.

## Preview
- `preview_server.py` serves a lightweight status dashboard on port 3000 so the Base44 preview has something to display.
- It shows environment readiness (libretro-database clone, Python deps) and detected ROM files, plus usage instructions.
- The actual renaming tool is still run via `docker compose exec app python3 rename_roms.py`.

## Notes
- `config.json` maps file extensions to libretro `.dat` file paths.
- `docker-compose.base44.yml` must define `ports:` exactly once. A prior merge introduced a duplicate `ports:` key, which made `docker compose` fail to parse the file with a YAML "mapping key already defined" error — keep a single `ports:` block.

## Testing
- Run dashboard regression tests with `python3 -m unittest discover -s tests -v`.
- These use temporary fixtures and require only the Python standard library.
