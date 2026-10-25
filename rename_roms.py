import os
import re
import json
import zlib
import sys
import logging
import argparse
import tempfile
from Levenshtein import distance

script_path = os.path.dirname(os.path.realpath(__file__))
parser = argparse.ArgumentParser(
    description='Preview console-specific ROM renames. Use --apply to rename; fuzzy matches require --approximate.',
    allow_abbrev=False,
)
parser.add_argument('--system', type=str.lower,
                    help='Console profile in config.json: ps2, psp, psx, or gamecube')
parser.add_argument('--roms-dir', '--roms', default=script_path,
                    help='One console directory to scan (non-recursive; default: script directory)')
parser.add_argument('--config', default=os.path.join(script_path, 'config.json'),
                    help='Configuration file; relative DAT paths are resolved beside this file')
parser.add_argument('--distance', '-distance', type=int, default=10,
                    help='Exclusive fuzzy-distance threshold (default: 10; 0 disables fuzzy matching)')
mode = parser.add_mutually_exclusive_group()
mode.add_argument('--apply', action='store_true', help='Apply renames and record them for --undo')
mode.add_argument('--dry-run', '-dry-run', action='store_true',
                  help='Preview only (the default); do not rename files or write a log')
mode.add_argument('--undo', action='store_true', help='Reverse recorded renames in the selected ROM directory')
parser.add_argument('--approximate', action='store_true',
                    help='Allow fuzzy name matching when no CRC matches')
parser.add_argument('--verbose', '-verbose', '-v', action='store_true')
args = parser.parse_args()
closest_distance_threshold = args.distance
dry_run = not args.apply
approximate = args.approximate
verbose = args.verbose
if closest_distance_threshold < 0:
    parser.error('--distance must be non-negative')

# Disc/container extensions do not identify a console. This guard also applies
# to legacy flat configs, so an old iso -> PSP mapping cannot silently return.
SYSTEM_REQUIRED_EXTENSIONS = {
    'iso', 'chd', 'cso', 'gcm', 'rvz', 'wbfs', 'bin', 'cue', 'img', 'mdf',
    'nrg', 'cdi', 'pbp', 'sbi',
}
# Companion files must keep their names in sync with their images. Multi-file
# disc sets and sidecars are outside the single-file rename workflow.
UNSUPPORTED_COMPANION_EXTENSIONS = {'bin', 'cue', 'sbi', 'm3u'}
PROJECT_FILES = {'README.md', 'AGENTS.md', 'config.json', 'requirements.txt', 'rename_undo.json'}
config_path = os.path.abspath(args.config)
config_directory = os.path.dirname(config_path)
roms_path = os.path.abspath(args.roms_dir)
if not os.path.isdir(roms_path):
    parser.error(f'ROM directory does not exist: {roms_path}')
undo_path = os.path.join(roms_path, 'rename_undo.json')
log_file_path = os.path.join(script_path, 'rename_log.txt')

def configure_logging(write_log):
    handlers = [logging.StreamHandler(sys.stdout)]
    if write_log:
        handlers.append(logging.FileHandler(log_file_path, encoding='utf-8'))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=handlers,
    )
    return logging.getLogger(__name__)

def replace_undo_history(entries):
    """Publish a complete journal without truncating the existing retry history."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=roms_path,
                                         prefix='.rename_undo-', suffix='.tmp',
                                         delete=False) as undo_file:
            temporary_path = undo_file.name
            json.dump(entries, undo_file, indent=1)
            undo_file.flush()
            os.fsync(undo_file.fileno())
        os.replace(temporary_path, undo_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass

def undo_dependency_key(path):
    # Be conservative even when an unresolved path is missing, so samefile()
    # cannot identify it. normcase() alone does not fold case on macOS. On a
    # case-sensitive volume this may defer an independent case-variant entry;
    # retaining it for retry is safer than consuming a conflicting file.
    return os.path.normpath(path).casefold()

def is_case_only_alias(source, target):
    """True only for two spellings of one existing directory entry."""
    if source == target or undo_dependency_key(source) != undo_dependency_key(target):
        return False
    try:
        # samefile alone also accepts distinct hardlinks. The requested original
        # spelling must be absent from the directory before restoring its case.
        return (os.path.basename(source) not in os.listdir(os.path.dirname(source))
                and os.path.samefile(source, target))
    except OSError:
        return False

# Undo uses its own journal, not the current DAT/configuration or console profile.
if args.undo:
    try:
        with open(undo_path, 'r', encoding='utf-8') as undo_file:
            renames = json.load(undo_file)
    except (OSError, ValueError) as error:
        parser.error(f'Cannot read undo history: {error}')
    log = configure_logging(True)
    restored = 0
    unresolved = []
    blocked_paths = set()
    for entry in reversed(renames):
        source = os.path.join(roms_path, entry['from'])
        target = os.path.join(roms_path, entry['to'])
        source_key = undo_dependency_key(source)
        target_key = undo_dependency_key(target)
        if source_key in blocked_paths or target_key in blocked_paths:
            log.warning(f'Cannot restore {entry["to"]} -> {entry["from"]}; a later rename is unresolved')
        elif os.path.exists(target) and (not os.path.lexists(source)
                                         or is_case_only_alias(source, target)):
            try:
                os.rename(target, source)
            except OSError as error:
                log.warning(f'Cannot restore {entry["to"]} -> {entry["from"]}: {error}; left unchanged')
            else:
                restored += 1
                log.info(f'Restored: {entry["to"]} -> {entry["from"]}')
                continue
        else:
            log.warning(f'Cannot restore {entry["to"]} -> {entry["from"]}; left unchanged')
        unresolved.append(entry)
        # Older records may depend on this rename. Do not mistake a conflicting
        # intermediate filename for the file those older records originally moved.
        blocked_paths.update((source_key, target_key))
    if unresolved:
        # Keep chronological order so a later --undo retries newest records first.
        replace_undo_history(list(reversed(unresolved)))
        log.warning(f'Kept {len(unresolved)} unresolved rename(s) in {undo_path}; resolve conflicts and retry --undo')
    else:
        os.remove(undo_path)
    log.info(f'Undo: {restored} of {len(renames)} rename(s) restored')
    sys.exit(0)


try:
    with open(config_path, 'r', encoding='utf-8') as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError('configuration must be an object')
    systems = config.get('systems', {})
    if not isinstance(systems, dict):
        raise ValueError('systems must be an object')
    default_mapping = {ext: dat for ext, dat in config.items() if ext != 'systems'}
    if any(not isinstance(dat, str) or not dat for dat in default_mapping.values()):
        raise ValueError('extension mappings must contain non-empty DAT paths')
    system_extensions = set(SYSTEM_REQUIRED_EXTENSIONS)
    for system, profile in systems.items():
        if (not isinstance(profile, dict) or
                not isinstance(profile.get('dat'), str) or not profile['dat'] or
                not isinstance(profile.get('extensions'), list) or not profile['extensions'] or
                any(not isinstance(ext, str) or not re.fullmatch(r'[a-z0-9]+', ext)
                    for ext in profile['extensions'])):
            raise ValueError(f'invalid system profile: {system}')
        system_extensions.update(profile['extensions'])
    if args.system:
        if args.system not in systems:
            raise ValueError(f'unknown system {args.system!r}; available: {", ".join(sorted(systems))}')
        profile = systems[args.system]
        # A selected profile replaces defaults; it never falls back to another DAT.
        extension_mapping = {ext: profile['dat'] for ext in profile['extensions']
                             if ext not in UNSUPPORTED_COMPANION_EXTENSIONS}
    else:
        extension_mapping = {ext: dat for ext, dat in default_mapping.items()
                             if ext not in system_extensions and
                             ext not in UNSUPPORTED_COMPANION_EXTENSIONS}
except (OSError, ValueError) as error:
    parser.error(f'Unable to load configuration: {error}')

# Preflight before opening a log or renaming anything. A missing selected DAT
# is an error, never a reason to try a different console's database.
files_by_extension = {}
blocked_files = []
for filename in sorted(os.listdir(roms_path)):
    filepath = os.path.join(roms_path, filename)
    if (not os.path.isfile(filepath) or os.path.islink(filepath) or
            filename in PROJECT_FILES):
        continue
    extension = filename.rsplit('.', 1)[-1].lower()
    if extension in extension_mapping:
        files_by_extension.setdefault(extension, []).append(filename)
    elif not args.system and extension in system_extensions:
        blocked_files.append(filename)

dat_paths = {ext: os.path.join(config_directory, dat)
             for ext, dat in extension_mapping.items()}
required_dats = set(dat_paths.values()) if args.system else {
    dat_paths[ext] for ext in files_by_extension
}
for dat_file_path in sorted(required_dats):
    try:
        with open(dat_file_path, 'r', encoding='utf-8'):
            pass
    except OSError as error:
        parser.error(f'Cannot read selected DAT {dat_file_path}: {error}')

log = configure_logging(not dry_run)

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

if dry_run:
    log.info('Preview only — no files will be renamed; no log file will be written. Run with --apply to rename.')
log.info(f'System: {args.system or "extension defaults (disc images require --system)"}; directory: {roms_path}')
for filename in blocked_files:
    log.warning(f'Skipping: {filename}; console is ambiguous — select --system and a single-console --roms-dir')
    stats['skipped'] += 1

if not approximate:
    log.info('Fuzzy name matches are reported but not used. Add --approximate to use them.')

applied_renames = []
if not dry_run and os.path.exists(undo_path):
    # Keep earlier runs undoable; --undo reverses them newest first.
    with open(undo_path, 'r', encoding='utf-8') as undo_file:
        applied_renames = json.load(undo_file)

def rename_file(filepath, new_filepath):
    applied_renames.append({'from': os.path.basename(filepath), 'to': os.path.basename(new_filepath)})
    # Publish the intent before moving the file: a write/sync/replace failure
    # leaves the ROM untouched, and interruption after rename retains recovery.
    # A failed move leaves a conservative, unresolved record for inspection.
    replace_undo_history(applied_renames)
    os.rename(filepath, new_filepath)

def parse_dat_file(dat_file_path, filter_prefix):
    """Return CRC-to-title sets and prefix-filtered names from a clrmamepro DAT."""
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
                            crc_map.setdefault(crc, set()).add(game_name)
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

def resolve_crc_title(titles, filename_stem):
    """Choose a unique CRC title, requiring filename evidence for shared CRCs.

    Prefer a literal title match before applying the existing name normalization.
    Never break ties by DAT order or fuzzy distance: shared ROM data alone cannot
    identify a game, and normalization can collapse distinct titles.
    """
    if len(titles) == 1:
        return next(iter(titles))
    exact = [title for title in titles
             if title.lower().strip() == filename_stem.lower().strip()]
    if exact:
        return exact[0] if len(exact) == 1 else None
    normalized_stem = normalize_name(filename_stem)
    normalized = [title for title in titles
                  if normalize_name(title) == normalized_stem]
    if len(normalized) == 1:
        return normalized[0]
    return None

def extract_number_from_name(name):
    match = re.search(r'\d+', name)
    if match:
        return match.group(0)
    return None

# Process files by extension
for extension, filenames in files_by_extension.items():
    dat_file_path = dat_paths[extension]
    log.info(f'Extension .{extension}: {len(filenames)} file(s) found, database: {dat_file_path}')
    
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
            title = resolve_crc_title(crc_map[crc], filename.rsplit('.', 1)[0])
            if title is None:
                log.warning(
                    f'Ambiguous CRC {crc} for {filename}: '
                    f'{", ".join(sorted(crc_map[crc]))}; leaving file unchanged'
                )
                stats['no_match'] += 1
                continue
            new_name = f"{title}.{extension}"
            new_name = correct_region_tag(new_name)
            new_filepath = os.path.join(roms_path, new_name)
            if not destination_exists(new_filepath):
                if dry_run:
                    plan_rename(filepath, new_filepath)
                    stats['planned_crc'] += 1
                    log.info(f'[DRY-RUN] Would rename: {filename} -> {new_name} (CRC match)')
                else:
                    rename_file(filepath, new_filepath)
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
                # A number in only one name (e.g. "Final Fight 3" vs "Final Fight") is a different game.
                if filename_number != db_name_number:
                    continue
                current_distance = distance(norm_filename, db_name)
                log.debug(f'  Candidate: {name_map[db_name]} (distance={current_distance})')
                if current_distance < closest_distance:
                    closest_distance = current_distance
                    closest_match = name_map[db_name]

            if closest_match and closest_distance < closest_distance_threshold and not approximate:
                log.warning(
                    f'Possible match for {filename}: {closest_match} '
                    f'(distance={closest_distance}); add --approximate to use it'
                )
                stats['no_match'] += 1
            elif closest_match and closest_distance < closest_distance_threshold:
                new_name = f"{closest_match}.{extension}"
                new_name = correct_region_tag(new_name)
                new_filepath = os.path.join(roms_path, new_name)
                if not destination_exists(new_filepath):
                    if dry_run:
                        plan_rename(filepath, new_filepath)
                        stats['planned_approximate'] += 1
                        log.info(f'[DRY-RUN] Would rename: {filename} -> {new_name} (approximate match, distance={closest_distance})')
                    else:
                        rename_file(filepath, new_filepath)
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
if not dry_run:
    log.info(f'Full log written to {log_file_path}')
