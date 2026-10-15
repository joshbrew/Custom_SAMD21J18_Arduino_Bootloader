"""Portable, validated menu preferences; command-line actions never change them."""
import json
import time
from pathlib import Path
from bootloader_config import save

BOARDS = ('zero', 'custom', 'target')
LOADERS = ('current', 'core', 'github', 'custom', 'target', 'local')


def read(path, sketches):
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or value.get('schema') != 1:
            raise ValueError('unsupported settings format')
    except (ValueError, OSError) as error:
        print(f'Ignoring unreadable saved menu settings: {error}')
        return {}
    result = {}
    for key, allowed in (('board', BOARDS), ('bootloader', LOADERS),
                         ('adapter', ('auto', 'stlink', 'gpio')), ('sketch', sketches)):
        if value.get(key) in allowed:
            result[key] = value[key]
    for key in ('build_only', 'sudo_flash'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    local = value.get('bootloader_file')
    if isinstance(local, str) and local and Path(local).expanduser().is_file():
        result['bootloader_file'] = local
    elif result.get('bootloader') == 'local':
        print('Saved local bootloader is missing; choose B again. Using current static file.')
        result['bootloader'] = 'current'
    return result


def write(path, **values):
    content = {'schema': 1, **values}
    try:
        if path.is_file() and json.loads(path.read_text(encoding='utf-8-sig')) == content:
            return True
    except (OSError, ValueError):
        pass
    for attempt in range(4):
        try:
            save(path, content)
            return True
        except OSError as error:
            if attempt == 3:
                print(f'WARNING: Could not save menu settings; current selections remain in memory. Last saved settings are retained: {error}')
                return False
            time.sleep(0.05 * (attempt + 1))
