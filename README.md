
# RetroArch CleanNaming
This is a simple scripts, that help you to rename the ROMs with the right naming 

# Requirement
```
git clone https://github.com/libretro/libretro-database.git
pip install python-Levenshtein
```

# Config
You can update the config.json

Just add the new extensions you want, and the libretro dataset you want to use
```
{
  "smc": "libretro-database/metadat/no-intro/Nintendo - Super Nintendo Entertainment System.dat",
  "sfc": "libretro-database/metadat/no-intro/Nintendo - Super Nintendo Entertainment System.dat",
  "gb": "libretro-database/metadat/no-intro/Nintendo - Game Boy.dat",
  "gbc": "libretro-database/metadat/no-intro/Nintendo - Game Boy Color.dat",
  "md": "libretro-database/metadat/no-intro/Sega - Mega Drive - Genesis.dat",
  "nes": "libretro-database/metadat/no-intro/Nintendo - Nintendo Entertainment System.dat",
  "32x": "libretro-database/metadat/no-intro/Sega - 32X.dat",
  "n64": "libretro-database/metadat/no-intro/Nintendo - Nintendo 64.dat",
  "v64": "libretro-database/metadat/no-intro/Nintendo - Nintendo 64.dat",
  "nds": "libretro-database/metadat/no-intro/Nintendo - Nintendo DS.dat",
  "iso": "libretro-database/metadat/no-intro/Sony - PlayStation Portable.dat"
}
```

# Usage
Put all the ROMs in the current directory (or pass `--roms=DIR`). Running the script on its own only previews what it would rename:
```
python3 rename_roms.py
```
When the preview looks right, rename for real:
```
python3 rename_roms.py --apply
```
Every applied rename is recorded in `rename_undo.json` in the ROM folder. To put the old names back:
```
python3 rename_roms.py --undo
```

# parameters
By default only exact CRC matches are renamed. To also use fuzzy name matching, add `--approximate`; `--distance=N` sets how loose it may be (default 10, higher is looser). Fuzzy matches never pair names where only one contains a number, such as "Final Fight 3" and "Final Fight".
```
python3 rename_roms.py --approximate --distance=10
```
Unknown options stop the script before anything is renamed.

# Results
The ROMs will be renamed according to the database of libretro

# RetroArch
Then you simply have to scan the directory and everything will be recognized well
