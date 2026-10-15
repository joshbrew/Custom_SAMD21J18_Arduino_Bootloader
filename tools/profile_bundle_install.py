#!/usr/bin/env python3
"""Installer entry point copied into exported Arduino profile bundles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--arduino-cli')
    args, rest = parser.parse_known_args()
    cli = args.arduino_cli or os.environ.get('J18_ARDUINO_CLI') or shutil.which('arduino-cli')
    if not cli and os.name == 'nt':
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe'
        if candidate.is_file():
            cli = str(candidate)
    if not cli:
        raise ValueError('Install Arduino CLI first, or pass --arduino-cli /path/to/arduino-cli.')
    manifest = json.loads((root / 'PROFILE_MANIFEST.json').read_text())
    for name, expected in manifest['files_sha256'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Release file missing/modified: ' + name)
    sys.path.insert(0, str(root / 'tools'))
    from install_board_profile import main as install
    return install(['--catalog', str(root / 'arduino-profiles.json'), '--arduino-cli', str(cli), *rest])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
