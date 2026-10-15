"""Identify locally available SAMD images by comparing their complete byte ranges."""
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def image_valid(data, offset):
    if not 8 <= len(data) <= 262144 - offset:
        return False
    sp, pc = struct.unpack_from('<II', data)
    return (0x20000000 < sp <= 0x20008000 and sp % 4 == 0 and pc & 1
            and offset <= (pc & ~1) < offset + len(data))


def discover(root=ROOT, extra=(), core_package=None):
    """No downloads/builds; ignore arbitrary data files and files larger than flash."""
    paths = set()
    explicit = {Path(x) for x in extra}
    for folder in (root / 'bootloader/firmware', root / 'releases', root / 'sketches', root / 'tools'):
        if folder.is_dir():
            paths.update(folder.rglob('*.bin'))
    cached = root / '.build/menu/samd21_sam_ba.github.bin'
    if cached.is_file():
        paths.add(cached)
    if core_package and (core_package / 'hardware/samd').is_dir():
        paths.update((core_package / 'hardware/samd').glob('*/bootloaders/**/*.bin'))
    for selection in (root / 'sketches').glob('*/.j18-build/custom-bootloader/selection.json'):
        source = read_json(selection).get('source')
        if isinstance(source, str):
            folder = root / source / 'firmware'
            if folder.is_dir():
                paths.update(folder.glob('*.bin'))
                explicit.update(folder.glob('*.bin'))
    for preferences in (root / '.build/menu').glob('settings-*.json'):
        local = read_json(preferences).get('bootloader_file')
        if isinstance(local, str) and Path(local).is_file():
            paths.add(Path(local))
            explicit.add(Path(local))
    paths.update(Path(x) for x in extra if Path(x).is_file())
    grouped, notes = {}, []

    def add(path, data, offset, kind, description, fqbn=None, board=None):
        if not image_valid(data, offset):
            return
        digest = hashlib.sha256(data).hexdigest()
        key = kind, offset, digest
        entry = grouped.setdefault(key, {'kind': kind, 'offset': offset, 'bytes': len(data),
            'sha256': digest, 'paths': [], 'labels': [], 'profiles': [], 'board_configs': []})
        for field, value in (('paths', str(path.resolve())), ('labels', description), ('profiles', fqbn), ('board_configs', board)):
            if value and value not in entry[field]:
                entry[field].append(value)

    for path in sorted(paths):
        if any(part in ('compiler', 'compiler-output', 'objects') for part in path.parts):
            continue
        name = path.name.lower()
        is_boot = 'sam_ba' in name or 'bootloader' in name
        is_app = name.endswith('.ino.bin') or name == 'firmware.sketch.bin'
        is_combined = 'with_bootloader' in name
        if not (is_boot or is_app or is_combined or path in explicit):
            continue
        try:
            if not 8 <= path.stat().st_size <= 262144:
                continue
            data = path.read_bytes()
            manifest = read_json(path.parent / 'BUILD_MANIFEST.json') or read_json(path.parent / 'manifest.json')
            receipt = read_json(path.parent.parent / 'sketch-build.json')
            metadata = manifest.get('images', {}).get(path.name, {})
            offset = metadata.get('flash_offset', manifest.get('sketch_offset', 8192))
            if not isinstance(offset, int) or not 0 <= offset <= 262136:
                continue
            fqbn = receipt.get('fqbn') or manifest.get('fqbn')
            board = receipt.get('board_config') or manifest.get('board')
            label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
            if is_combined:
                app_offset = manifest.get('sketch_offset', 8192)
                if isinstance(app_offset, int) and 8 <= app_offset < len(data):
                    add(path, data[:app_offset], 0, 'bootloader', label + ' [embedded boot region]')
                    add(path, data[app_offset:], app_offset, 'application', label + ' [embedded sketch]', fqbn, board)
            elif is_boot or (not is_app and image_valid(data, 0)):
                add(path, data, 0, 'bootloader', label, board=manifest.get('board'))
            else:
                add(path, data, offset, 'application', label, fqbn, board)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            notes.append(f'Skipped {path}: {error}')
    return {'schema': 1, 'candidates': list(grouped.values()), 'notes': notes,
            'read_bytes': max((x['offset'] + x['bytes'] for x in grouped.values()), default=0)}


def identify(catalog, installed, ram_bytes=32768):
    matches = {'bootloader': [], 'application': []}
    skipped = 0
    for candidate in catalog['candidates']:
        start, size = candidate['offset'], candidate['bytes']
        if start + size > len(installed):
            skipped += 1
            continue
        actual = installed[start:start + size]
        sp = struct.unpack_from('<I', actual)[0]
        if sp > 0x20000000 + ram_bytes:
            skipped += 1
            continue
        if hashlib.sha256(actual).hexdigest() == candidate['sha256']:
            matches[candidate['kind']].append(candidate)
    return {'status': 'COMPLETE', 'matches': matches, 'bytes_read': len(installed),
            'candidate_count': len(catalog['candidates']), 'outside_detected_geometry': skipped,
            'meaning': 'Exact match across each candidate file byte range. Bytes outside matched ranges are not identified. Identical builds share an identity; this does not prove which firmware is currently executing.'}
