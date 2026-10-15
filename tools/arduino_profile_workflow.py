#!/usr/bin/env python3
"""Build, install and/or export a checked Arduino board profile. Never flashes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

import bootloader_config as config
import install_board_profile as installer
import samd21_menu as menu

ROOT = Path(__file__).resolve().parents[1]


def checked_profile(profile, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    platforms = installer.platforms(profile)
    if len(platforms) != 1:
        raise ValueError('A built loader manifest must describe exactly one platform.')
    platform = platforms[0][2]
    expected = dict(manifest['profile_sha256'])
    expected['bootloaders/custom/manifest.json'] = menu.digest(manifest_path)
    if installer.files(platform) != expected:
        raise ValueError('Profile differs from its build manifest; rebuild before installing/exporting.')
    return manifest


def export_profile(profile, destination, manifest):
    """Freeze a portable hardware tree plus the existing, rollback-capable installer."""
    destination = destination.resolve()
    menu.safe_tree(destination)
    if destination == profile.resolve() or destination.is_relative_to(profile.resolve()) or profile.resolve().is_relative_to(destination):
        raise ValueError('Export destination and source must not overlap.')
    if destination.exists() and not (destination / 'PROFILE_MANIFEST.json').is_file():
        raise ValueError('Refusing to replace an unrelated export directory.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.profile-export-', dir=destination.parent) as temporary:
        scratch = Path(temporary)
        bundle = scratch / destination.name
        bundle.mkdir()
        for vendor, architecture, platform in installer.platforms(profile):
            snapshot = installer.files(platform)
            for name in snapshot:
                target = bundle / 'hardware' / vendor / architecture / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(platform / name, target)
                if menu.digest(target) != snapshot[name]:
                    raise ValueError('Profile source changed during export: ' + name)
            if installer.files(platform) != snapshot:
                raise ValueError('Profile file set changed during export.')
            for name, expected in manifest['profile_sha256'].items():
                if menu.digest(bundle / 'hardware' / vendor / architecture / name) != expected:
                    raise ValueError('Export differs from the checked build manifest: ' + name)
            embedded = bundle / 'hardware' / vendor / architecture / 'bootloaders/custom/manifest.json'
            if json.loads(embedded.read_text()) != manifest:
                raise ValueError('Exported bootloader manifest changed during export.')
        for name in ('install_board_profile.py', 'samd21_menu.py'):
            target = bundle / 'tools' / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / 'tools' / name, target)
        shutil.copy2(ROOT / 'tools/profile_bundle_install.py', bundle / 'install_profile.py')
        catalog = {'schema': 1, 'profiles': {'custom': {'name': 'Custom Arduino profile',
            'source': '.', 'required_cores': ['arduino:samd@1.8.14']}}}
        (bundle / 'arduino-profiles.json').write_text(json.dumps(catalog, indent=2) + '\n')
        (bundle / 'README.md').write_text(
            '# Custom Arduino profile release\n\n'
            'Requires Python 3.10+ and Arduino CLI (or Arduino IDE on Windows).\n'
            'Run `python install_profile.py` to install into your configured Arduino\n'
            'sketchbook; `--sketchbook /path/to/Arduino` overrides the destination.\n'
            'Use `--check-only` to preview, or `--arduino-cli /path/to/arduino-cli`.\n'
            'The installer checks file hashes, installs arduino:samd@1.8.14 if needed,\n'
            'backs up the previous profile, and checks Arduino board discovery.\n'
            'Restart Arduino IDE and select Custom SAMD21J18 (Native USB).\n\n'
            'For manual deployment, copy hardware/custom/samd into the sketchbook\n'
            'hardware/custom/samd after installing arduino:samd@1.8.14.\n'
            'This release can ship with another project; a library install alone does not\n'
            'install board definitions. Boards Manager distribution additionally\n'
            'requires a published package index and platform packaging.\n\n'
            'Includes the selected generated variant, linker script, bootloader,\n'
            'identity/manifests and LGPL license. It relies on the installed Arduino\n'
            'SAMD core; it does not bundle that core/toolchain or flash hardware.\n')
        files = {p.relative_to(bundle).as_posix(): menu.digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
        release = {'schema': 1, 'board': manifest['board'], 'bootloader_sha256': manifest['sha256'],
                   'build_manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                   'files_sha256': files}
        (bundle / 'PROFILE_MANIFEST.json').write_text(json.dumps(release, indent=2) + '\n')
        staged_zip = scratch / 'bundle.zip'
        with ZipFile(staged_zip, 'w', ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    info = ZipInfo(destination.name + '/' + path.relative_to(bundle).as_posix())
                    info.create_system = 3
                    info.external_attr = (0o100755 if path.suffix == '.py' else 0o100644) << 16
                    info.compress_type = ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
        with ZipFile(staged_zip) as archive:
            if archive.testzip():
                raise ValueError('Export ZIP verification failed.')
        previous = scratch / 'previous'
        if destination.exists():
            destination.rename(previous)
        try:
            bundle.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
        staged_zip.replace(destination.with_suffix('.zip'))
    print(f'Profile folder: {destination}\nProfile ZIP: {destination.with_suffix(".zip")}', flush=True)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('default', 'sketch'), default='default')
    parser.add_argument('--sketch', help='Sketch folder or name in sketches/; needed for --target sketch.')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--export', action='store_true')
    parser.add_argument('--output', type=Path, help='Managed export folder; defaults to dist/Custom_Arduino_Profile[-sketch].')
    parser.add_argument('--arduino-cli')
    parser.add_argument('--arduino-package', type=Path)
    parser.add_argument('--sketchbook', type=Path)
    parser.add_argument('--check-only', action='store_true', help='Preview all requested actions; do not build, install or export.')
    args = parser.parse_args(argv)
    if not any((args.build, args.install, args.export)):
        parser.error('Choose --build, --install and/or --export.')
    sketch = None
    if args.target == 'sketch':
        if not args.sketch:
            parser.error('--target sketch requires --sketch.')
        candidate = Path(args.sketch).expanduser()
        sketch = (candidate if candidate.is_dir() else ROOT / 'sketches' / args.sketch).resolve()
        folder = config.target_root(sketch)
        source = config.read_selection(folder)
        config.load_board(folder / 'board.json')
    else:
        folder, source = ROOT / 'bootloader', ROOT / 'bootloader'
    profile = folder / 'arduino-profile'
    manifest_path = folder / 'firmware/manifest.json'
    output = args.output or ROOT / 'dist' / ('Custom_Arduino_Profile' + ('-' + sketch.name if sketch else ''))
    print(f'Arduino profile workflow: {args.target}\nSource: {source}\nProfile: {profile}', flush=True)
    if args.check_only:
        print(f'PREVIEW build={args.build}, install={args.install}, export={args.export}; output={output}. No changes/hardware access.')
        return 0
    if args.build:
        if sketch:
            config.build_target(sketch, source, args.arduino_package)
        else:
            config.rebuild_default(args.arduino_package)
    manifest = checked_profile(profile, manifest_path)
    if manifest['board'] != config.load_board(folder / 'board.json'):
        raise ValueError('Board settings changed; add --build before installing/exporting.')
    if menu.digest(folder / 'firmware/samd21_sam_ba.bin') != manifest['sha256']:
        raise ValueError('Bootloader binary differs from the build manifest; rebuild first.')
    if args.install:
        command = ['--source', str(profile), '--require-core', 'arduino:samd@1.8.14']
        if args.arduino_cli:
            command += ['--arduino-cli', args.arduino_cli]
        if args.sketchbook:
            command += ['--sketchbook', str(args.sketchbook)]
        if installer.main(command):
            raise RuntimeError('Arduino profile installation failed; export stopped.')
    if args.export:
        with menu.locked(ROOT / '.build/menu/profile-export'):
            export_profile(profile, output, manifest)
    print('Profile workflow complete. No device was flashed.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
