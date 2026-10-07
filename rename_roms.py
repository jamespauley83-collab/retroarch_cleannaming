import os
import re
import json
import zlib
import sys
import logging
from Levenshtein import distance

# Default closest distance
closest_distance_threshold = 10
dry_run = False
verbose = False

# Parse command line arguments
for arg in sys.argv[1:]:
    if arg.startswith('-distance='):
        closest_distance_threshold = int(arg.split('=')[1])
    elif arg in ('--dry-run', '-dry-run'):
        dry_run = True
    elif arg in ('--verbose', '-verbose', '-v'):
        verbose = True

# --- Logging setup: writes to both console and a log file ---
log_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'rename_log.txt')
logging.basicConfig(
    level=logging.DEBUG if verbose else logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file_path, encoding='utf-8'),
    ],
)
log = logging.getLogger(__name__)

# Counters for end-of-run summary
stats = {'processed': 0, 'renamed': 0, 'approximate': 0, 'skipped': 0, 'no_match': 0,
         'planned_crc': 0, 'planned_approximate': 0}

# Simulate filesystem changes made by earlier dry-run operations.
projected_destinations = set()
projected_sources = set()

def destination_exists(filepath):
    return (filepath in projected_destinations or
            (os.path.exists(filepath) and filepath not in projected_sources))

def plan_rename(filepath, new_filepath):
    projected_destinations.discard(filepath)
    projected_sources.add(filepath)
    projected_destinations.add(new_filepath)
    projected_sources.discard(new_filepath)

# Load configuration from JSON file
config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')
with open(config_path, 'r', encoding='utf-8') as config_file:
    config = json.load(config_file)

# Path to your ROM files
roms_path = os.path.dirname(os.path.realpath(__file__))

if dry_run:
    log.info('Dry-run mode enabled — no files will be renamed')

def parse_dat_file(dat_file_path, filter_prefix):
    """Return all ROM CRCs and prefix-filtered names from a clrmamepro DAT."""
    crc_map = {}
    name_map = {}
    blocks = []
    key = None
    game_name = None
    game_crcs = []
    # Quoted names may contain parentheses; only bare parentheses delimit blocks.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')
    with open(dat_file_path, 'r', encoding='utf-8') as file:
        for line in file:
            for token in tokens.findall(line):
                if token == '(':
                    blocks.append(key)
                    key = None
                    if blocks == ['game']:
                        game_name = None
                        game_crcs = []
                elif token == ')':
                    if blocks == ['game'] and game_name:
                        for crc in game_crcs:
                            crc_map[crc] = game_name
                        if game_name.lower().startswith(filter_prefix.lower()):
                            name_map[game_name.lower().strip()] = game_name
                    if blocks:
                        blocks.pop()
                    key = None
                elif key is None:
                    key = token
                else:
                    value = token
                    if token.startswith('"'):
                        value = re.sub(r'\\(["\\])', r'\1', token[1:-1])
                    if blocks == ['game'] and key == 'name':
                        game_name = value
                    elif blocks == ['game', 'rom'] and key == 'crc':
                        if re.fullmatch(r'[0-9a-fA-F]{1,8}', value):
                            game_crcs.append(value.upper().zfill(8))
                    key = None
    return crc_map, name_map

def get_crc32(filepath):
    prev = 0
    with open(filepath, "rb") as file:
        for eachLine in file:
            prev = zlib.crc32(eachLine, prev)
    return "%08X" % (prev & 0xFFFFFFFF)

def arabic_to_roman(number):
    arabic_roman_map = [
        (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
        (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
        (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
    ]
    roman_number = ''
    for (arabic, roman) in arabic_roman_map:
        while number >= arabic:
            roman_number += roman
            number -= arabic
    return roman_number

def correct_region_tag(name):
    name = name.replace('(FR)', '(Europe)')
    name = name.replace('(U)', '(USA)')
    name = name.replace('(E)', '(Europe)')
    name = name.replace('(EU)', '(Europe)')
    name = name.replace('(JP)', '(Japan)')
    return name

def normalize_name(name):
    # Specific handling for Final Fantasy titles
    final_fantasy_match = re.match(r'final fantasy (\d+)', name, re.IGNORECASE)
    if final_fantasy_match:
        number = int(final_fantasy_match.group(1))
        roman_number = arabic_to_roman(number)
        name = re.sub(r'final fantasy \d+', f'Final Fantasy {roman_number}', name, flags=re.IGNORECASE)
    
    # Apply general region fix
    name = correct_region_tag(name)

    # Change CD to Disc
    name = re.sub(r'CD(\d+)', r'Disc \1 of', name, flags=re.IGNORECASE)

    # Ensure the format matches the database entries
    disc_match = re.search(r'Disc (\d+) of', name)
    if disc_match:
        disc_number = disc_match.group(1)
        name = re.sub(r'Disc \d+ of', f'(Disc {disc_number} of )', name)
    
    # Return the normalized name
    return name.lower().strip()

def extract_number_from_name(name):
    match = re.search(r'\d+', name)
    if match:
        return match.group(0)
    return None

# Gather all files by extension
files_by_extension = {}
for filename in os.listdir(roms_path):
    filepath = os.path.join(roms_path, filename)
    if os.path.isfile(filepath):
        extension = filename.split('.')[-1].lower()
        if extension in config:
            if extension not in files_by_extension:
                files_by_extension[extension] = []
            files_by_extension[extension].append(filename)

# Process files by extension
for extension, filenames in files_by_extension.items():
    dat_file_path = os.path.join(roms_path, config[extension])
    log.debug(f'Extension .{extension}: {len(filenames)} file(s) found, database: {config[extension]}')
    
    # Process each file
    for filename in filenames:
        stats['processed'] += 1
        filepath = os.path.join(roms_path, filename)
        filter_prefix = filename[:3]  # Use the first three letters for filtering
        crc_map, name_map = parse_dat_file(dat_file_path, filter_prefix)
        log.debug(f'Loaded {len(crc_map)} CRC entries and {len(name_map)} names matching prefix "{filter_prefix}"')
        crc = get_crc32(filepath)
        log.debug(f'CRC32 for {filename}: {crc}')
        if crc in crc_map:
            new_name = f"{crc_map[crc]}.{extension}"
            new_name = correct_region_tag(new_name)
            new_filepath = os.path.join(roms_path, new_name)
            if not destination_exists(new_filepath):
                if dry_run:
                    plan_rename(filepath, new_filepath)
                    stats['planned_crc'] += 1
                    log.info(f'[DRY-RUN] Would rename: {filename} -> {new_name} (CRC match)')
                else:
                    os.rename(filepath, new_filepath)
                    stats['renamed'] += 1
                    log.info(f'Renamed: {filename} -> {new_name} (CRC match)')
            else:
                log.warning(f'Skipping: target already exists -> {new_filepath}')
                stats['skipped'] += 1
        else:
            # If the exact CRC match is not found, use approximate matching
            closest_match = None
            closest_distance = float('inf')
            norm_filename = normalize_name(filename.rsplit('.', 1)[0])
            filename_number = extract_number_from_name(norm_filename)
            log.info(f'Processing: {filename} (normalized: {norm_filename})')

            for db_name in name_map.keys():
                db_name_number = extract_number_from_name(db_name)
                if filename_number and db_name_number and filename_number != db_name_number:
                    continue
                current_distance = distance(norm_filename, db_name)
                log.debug(f'  Candidate: {name_map[db_name]} (distance={current_distance})')
                if current_distance < closest_distance:
                    closest_distance = current_distance
                    closest_match = name_map[db_name]

            if closest_match and closest_distance < closest_distance_threshold:  # Use the threshold from the command line or default
                new_name = f"{closest_match}.{extension}"
                new_name = correct_region_tag(new_name)
                new_filepath = os.path.join(roms_path, new_name)
                if not destination_exists(new_filepath):
                    if dry_run:
                        plan_rename(filepath, new_filepath)
                        stats['planned_approximate'] += 1
                        log.info(f'[DRY-RUN] Would rename: {filename} -> {new_name} (approximate match, distance={closest_distance})')
                    else:
                        os.rename(filepath, new_filepath)
                        stats['approximate'] += 1
                        log.info(f'Renamed: {filename} -> {new_name} (approximate match, distance={closest_distance})')
                else:
                    log.warning(f'Skipping: target already exists -> {new_filepath}')
                    stats['skipped'] += 1
            else:
                log.warning(f'No close match found for {filename}')
                stats['no_match'] += 1

# --- End-of-run summary ---
planned_summary = (
    f'{stats["planned_crc"]} planned (CRC), '
    f'{stats["planned_approximate"]} planned (approximate), '
) if dry_run else ''
log.info(
    f'Summary: {stats["processed"]} processed, '
    f'{stats["renamed"]} renamed (CRC), '
    f'{stats["approximate"]} renamed (approximate), '
    f'{planned_summary}'
    f'{stats["skipped"]} skipped, '
    f'{stats["no_match"]} no match'
)
log.info(f'Full log written to {log_file_path}')
