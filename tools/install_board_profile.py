#!/usr/bin/env python3
"""Install/update local Arduino hardware profiles, with scoped backups; no flashing."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import samd21_menu as menu

ROOT = Path(__file__).resolve().parents[1]
STAMP = '.arduino-profile-install.json'
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]+$')


def command(args):
    result = subprocess.run([str(x) for x in args], check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    return result.stdout


def find_cli(explicit=None):
    candidate = explicit or os.environ.get('J18_ARDUINO_CLI') or shutil.which('arduino-cli')
    if not candidate and os.name == 'nt':
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe'
    if candidate and Path(candidate).is_file():
        return Path(candidate).resolve()
    if os.name != 'nt':
        return menu.Engine('mac' if sys.platform == 'darwin' else 'linux').tool('arduino-cli')
    raise FileNotFoundError('Arduino CLI missing. Run a menu build/check first, or set J18_ARDUINO_CLI.')


def select_profile(catalog, profile, source=None, required=()):
    if source:
        return Path(source).expanduser().resolve(), list(required), None
    content = json.loads(catalog.read_text(encoding='utf-8-sig'))
    if content.get('schema') != 1 or profile not in content['profiles']:
        raise ValueError('Unknown profile or catalog schema: ' + profile)
    item = content['profiles'][profile]
    source = (catalog.parent / item['source']).resolve()
    integrity = item.get('integrity')
    if integrity:
        integrity = {**integrity, 'manifest': (catalog.parent / integrity['manifest']).resolve()}
    return source, list(dict.fromkeys([*item.get('required_cores', []), *required])), integrity


def platforms(source, vendor=None, architecture=None):
    menu.safe_tree(source)
    if (source / 'boards.txt').is_file():
        if not vendor and source.parent.parent.name == 'hardware':
            vendor = source.parent.name
        if not architecture and source.parent.parent.name == 'hardware':
            architecture = source.name
        if not vendor or not architecture:
            raise ValueError('A direct platform folder needs --vendor and --architecture, or hardware/<vendor>/<architecture> parents.')
        items = [(vendor, architecture, source)]
    else:
        if vendor or architecture:
            raise ValueError('--vendor/--architecture apply only to a direct platform folder containing boards.txt.')
        hardware = source / 'hardware' if (source / 'hardware').is_dir() else source
        items = [(v.name, a.name, a) for v in sorted(hardware.iterdir()) if v.is_dir()
                 for a in sorted(v.iterdir()) if a.is_dir() and (a / 'boards.txt').is_file()]
    if not items:
        raise ValueError('No hardware/<vendor>/<architecture>/boards.txt found.')
    for vendor, architecture, _ in items:
        if not IDENTIFIER.fullmatch(vendor) or not IDENTIFIER.fullmatch(architecture):
            raise ValueError('Invalid vendor/architecture identifier.')
    return items


def files(folder):
    menu.safe_tree(folder)
    ignored = {'.git', '.build', '.j18-build', '__pycache__', 'build'}
    return {x.relative_to(folder).as_posix(): menu.digest(x) for x in sorted(folder.rglob('*'))
            if x.is_file() and x.name != STAMP and not any(p in ignored for p in x.relative_to(folder).parts)}


def install_platform(source, sketchbook, vendor, architecture, validate):
    """Replace only one platform atomically, retaining unrelated files and one backup."""
    if not IDENTIFIER.fullmatch(vendor) or not IDENTIFIER.fullmatch(architecture):
        raise ValueError('Invalid vendor/architecture identifier.')
    target = sketchbook / 'hardware' / vendor / architecture
    state = sketchbook / '.arduino-profile-installer' / vendor / architecture
    stage, backup = state / 'staging', state / 'previous'
    for path in (target, state):
        menu.safe_tree(path)
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Profile source and destination must not overlap.')
    expected = files(source)
    names = re.findall(r'^([A-Za-z0-9_-]+)\.name\s*=', (source / 'boards.txt').read_text(encoding='utf-8-sig'), re.M)
    if not names:
        raise ValueError('boards.txt contains no named board definitions.')
    boards = [f'{vendor}:{architecture}:{name}' for name in names]
    receipt = target / STAMP
    if receipt.is_file():
        previous = json.loads(receipt.read_text(encoding='utf-8-sig'))
        if previous.get('files_sha256') == expected and all((target / name).is_file() and menu.digest(target / name) == value for name, value in expected.items()):
            validate(boards)
            return target, boards
    state.mkdir(parents=True, exist_ok=True)
    def clear(path):
        if path.resolve().parent != state.resolve() or path.name not in ('staging', 'previous'):
            raise ValueError('Refusing to remove a path outside the installer state folder.')
        menu.safe_tree(path)
        if path.exists():
            shutil.rmtree(path)
    clear(stage)
    if target.exists():
        shutil.copytree(target, stage)
        receipt_path = stage / STAMP
        if receipt_path.is_file():
            previous = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
            for name, old_hash in previous.get('files_sha256', {}).items():
                item = stage / name
                if not item.resolve().is_relative_to(stage.resolve()):
                    raise ValueError('Invalid path in prior installation receipt.')
                if name not in expected and item.is_file() and menu.digest(item) == old_hash:
                    item.unlink()
    else:
        stage.mkdir()
    for name in expected:
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    if files(source) != expected or any(menu.digest(stage / name) != sha for name, sha in expected.items()):
        clear(stage)
        raise ValueError('Profile source changed while copying; installation stopped.')
    menu.record(stage / STAMP, {'schema': 1, 'source': str(source), 'boards': boards, 'files_sha256': expected})
    clear(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    had_target = target.exists()
    if had_target:
        target.rename(backup)
    try:
        stage.rename(target)
        validate(boards)
    except BaseException:
        if target.exists():
            target.rename(stage)
        if had_target and backup.exists():
            backup.rename(target)
        clear(stage)
        raise
    return target, boards


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', type=Path, default=ROOT / 'arduino-profiles.json')
    p.add_argument('--profile', default='custom')
    p.add_argument('--source', type=Path, help='Another profile root/hardware tree or direct platform folder.')
    p.add_argument('--vendor')
    p.add_argument('--architecture')
    p.add_argument('--require-core', action='append', default=[], help='Core dependency, e.g. arduino:samd@1.8.14; repeatable.')
    p.add_argument('--arduino-cli')
    p.add_argument('--sketchbook', type=Path, help='Override the CLI user/sketchbook directory.')
    p.add_argument('--check-only', action='store_true', help='Preview and verify source/dependencies without installation.')
    p.add_argument('--list', action='store_true')
    a = p.parse_args(argv)
    catalog = a.catalog.resolve()
    if a.list:
        for key, item in json.loads(catalog.read_text())['profiles'].items():
            print(f'{key}: {item["name"]}')
        return 0
    source, cores, integrity = select_profile(catalog, a.profile, a.source, a.require_core)
    selected = platforms(source, a.vendor, a.architecture)
    if integrity:
        manifest = json.loads(integrity['manifest'].read_text())
        target = next((path for v, arch, path in selected if f'{v}/{arch}' == integrity['platform']), None)
        expected = dict(manifest[integrity['field']])
        for name in integrity.get('manifest_copies', []):
            expected[name] = menu.digest(integrity['manifest'])
        if target is None or files(target) != expected:
            raise ValueError('Profile differs from its integrity manifest. Rebuild the custom loader/profile before installing.')
    cli = find_cli(a.arduino_cli)
    user = a.sketchbook or Path(command([cli, 'config', 'get', 'directories.user']).strip())
    user = user.expanduser().resolve()
    menu.safe_tree(user / 'hardware')
    installed = command([cli, 'core', 'list'])
    for core in cores:
        if not re.fullmatch(r'[A-Za-z0-9_-]+:[A-Za-z0-9_-]+@[A-Za-z0-9_.+-]+', core):
            raise ValueError('Core dependencies must be pinned as vendor:architecture@version.')
        name, version = core.split('@')
        if not re.search(r'^' + re.escape(name) + r'\s+' + re.escape(version) + r'(?:\s|$)', installed, re.M):
            if a.check_only:
                print('Would install dependency: ' + core)
            else:
                print('Installing dependency: ' + core, flush=True)
                print(command([cli, 'core', 'update-index']))
                print(command([cli, 'core', 'install', core]))
    for vendor, architecture, platform in selected:
        print(f'{platform} -> {user / "hardware" / vendor / architecture}', flush=True)
    if a.check_only:
        print('Profile preview complete; no board profile installed and no hardware accessed.')
        return 0
    state = user / '.arduino-profile-installer'
    with menu.locked(state):
        data = command([cli, 'config', 'get', 'directories.data']).strip()
        cfg = state / 'cli.json'
        menu.record(cfg, {'directories': {'data': data, 'user': str(user)}})
        def validate(boards):
            found = command([cli, '--config-file', cfg, 'board', 'listall'])
            for board in boards:
                if not re.search(r'(?<![\w:])' + re.escape(board) + r'(?![\w:])', found):
                    raise ValueError('Arduino CLI did not discover installed board: ' + board)
        for vendor, architecture, platform in selected:
            destination, boards = install_platform(platform, user, vendor, architecture, validate)
            print('Installed and discovered: ' + ', '.join(boards), flush=True)
            print('Location: ' + str(destination), flush=True)
    print('Restart Arduino IDE to refresh its board list. This installs local board files; no device was flashed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stdout:
            print(error.stdout, file=sys.stderr)
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
