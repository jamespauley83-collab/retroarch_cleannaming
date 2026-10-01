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
- The container stays alive (`tail -f /dev/null`) so you can exec in and run the script:
  ```
  docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py
  ```
- The `libretro-database` clone persists in a named volume so it isn't re-cloned on restart.

## Notes
- No browser preview: this is a CLI tool, so port 3000 serves nothing.
- `config.json` maps file extensions to libretro `.dat` file paths.
