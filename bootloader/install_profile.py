#!/usr/bin/env python3
"""Install the generated board profile in a specified Arduino sketchbook."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from build import ROOT, write, sha, validate_release


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sketchbook', type=Path, required=True, help='Arduino IDE Sketchbook location / CLI directories.user')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    source = ROOT / 'arduino-profile/hardware/custom/samd'
    if not (source / 'boards.txt').is_file():
        p.error('Run build.py first.')
    try:
        manifest = validate_release(ROOT / 'firmware')
        if not manifest.get('profile_sha256'):
            raise ValueError('Missing generated-profile hashes; rebuild first.')
        for name, expected in manifest['profile_sha256'].items():
            if sha(source / name) != expected:
                raise ValueError('Generated profile changed since build: ' + name)
    except (OSError, ValueError) as e:
        p.error(str(e))
    sketchbook = a.sketchbook.resolve()
    dest = sketchbook / 'hardware/custom/samd'
    if not dest.resolve().is_relative_to(sketchbook):
        p.error('Destination resolves outside the sketchbook; inspect existing junctions/symlinks.')
    if dest.exists() and any(dest.iterdir()) and not (dest / '.custom-profile.json').is_file():
        p.error(f'Refusing to replace an unrelated platform at {dest}.')
    files = [x for x in source.rglob('*') if x.is_file()]
    print(f'{"Would install" if a.dry_run else "Installing"} {len(files)} files into {dest}')
    if a.dry_run:
        return
    backup = dest / '.custom-backup' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    for x in files:
        relative = x.relative_to(source)
        target = dest / relative
        if not target.resolve().is_relative_to(dest.resolve()):
            p.error('Destination file resolves outside this board profile.')
        if target.is_file() and target.read_bytes() != x.read_bytes():
            old = backup / relative
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, old)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(x, target)
    write(dest / '.custom-profile.json', json.dumps({
        'source': str(ROOT), 'fqbn': 'custom:samd:custom_j18',
        'files_sha256': {x.relative_to(source).as_posix(): hashlib.sha256(x.read_bytes()).hexdigest() for x in files}
    }, indent=2) + '\n')
    print('Restart Arduino IDE, then select Custom SAMD21J18 (Native USB).')
    print('FQBN: custom:samd:custom_j18. No hardware was flashed.')
    if backup.exists():
        print('Previous changed files saved in ' + str(backup))


if __name__ == '__main__':
    main()
