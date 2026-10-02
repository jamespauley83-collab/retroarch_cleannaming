
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
Put all the ROMs in the current directory, and run the scripts

# Run
```
python3 rename_roms.py
```
# parameters
You can change the matching distance by adding this parameter and change the value, more the number is higher more the approx will be
```
python3 rename_roms.py -distance=10
```

# Results
The ROMs will be renamed according to the database of libretro

# RetroArch
Then you simply have to scan the directory and everything will be recognized well
