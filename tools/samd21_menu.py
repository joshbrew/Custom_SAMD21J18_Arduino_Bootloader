#!/usr/bin/env python3
"""Native Linux/macOS menu with persistent images and explicit board selection."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
BOARDS = {'zero': 'arduino:samd:arduino_zero_native', 'custom': 'custom:samd:custom_j18', 'target': 'custom:samd:custom_j18'}
LABELS = {'zero': 'Arduino Zero Native USB / SAMD21 M0+ (stock Arduino pins)',
          'custom': 'Custom J18 Native USB (repository defaults)',
          'target': 'Configured custom J18 profile (selected sketch folder)'}
OFFICIAL_SHA = '89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b'
PHASES = {'combined': ['combined'], 'separate': ['boot', 'app'], 'boot': ['boot'], 'app': ['app']}
EXTENSIONS = {'.ino', '.pde', '.h', '.hpp', '.hh', '.c', '.cpp', '.cc', '.cxx', '.s', '.ipp', '.tpp'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def record(path, value):
    atomic(path, (json.dumps(value, indent=2) + '\n').encode())


def vectors(data, offset):
    limit = 8192 if offset == 0 else 262144 - 8192
    if not 8 <= len(data) <= limit:
        raise ValueError('Image size does not fit the 8 KB bootloader / SAMD21J18 layout.')
    sp, pc = struct.unpack_from('<II', data)
    if not (0x20000000 < sp <= 0x20008000 and sp % (4 if offset == 0 else 8) == 0
            and pc & 1 and offset <= (pc & ~1) < offset + len(data)):
        raise ValueError(f'Invalid vector table for image at {offset:#x}.')


def combine(boot, app):
    vectors(boot, 0)
    vectors(app, 8192)
    return boot + b'\xff' * (8192 - len(boot)) + app


def selection_header(boot=None):
    # Zero needs core-wide LED suppression even with a custom bootloader; the
    # matching custom variant already provides it. Keep the harmless fallback
    # for both routes. An explicit image identity below supersedes the profile's
    # default identity so verification follows the loader selected for flashing.
    header = ('#pragma once\n#include <Arduino.h>\n#undef PIN_LED_TXL\n#undef PIN_LED_RXL\n'
              '#define J18_CORE_USB_LEDS_DISABLED 1\n')
    if boot is not None:
        vectors(boot, 0)
        header += ('#undef J18_CUSTOM_BOOTLOADER_BYTES\n#undef J18_CUSTOM_BOOTLOADER_CRC32\n'
                   f'#define J18_CUSTOM_BOOTLOADER_BYTES {len(boot)}u\n'
                   f'#define J18_CUSTOM_BOOTLOADER_CRC32 0x{zlib.crc32(boot):08x}ul\n')
    return header


def available():
    return sorted(p for p in (ROOT / 'sketches').iterdir()
                  if p.is_dir() and (p / (p.name + '.ino')).is_file())


def safe_tree(path):
    """Reject symlinks and Windows reparse points before touching managed files."""
    for candidate in [path, *path.parents]:
        if candidate.is_symlink() or (candidate.exists() and
                getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError(f'Managed path contains a link/reparse point: {candidate}')
    if path.exists():
        for candidate in path.rglob('*'):
            if candidate.is_symlink() or getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError(f'Managed folder contains a link/reparse point: {candidate}')


@contextmanager
def locked(build):
    safe_tree(build)
    build.mkdir(parents=True, exist_ok=True)
    with (build / 'launcher.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            stream.write(b'0')
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def platform_tools(platform):
    folder, name = ('Mac_J18_Tools', 'j18_mac') if platform == 'mac' else ('RPi_J18_Tools', 'j18_rpi')
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def adapter_configuration(adapter):
    if adapter == 'gpio':
        try:
            coefficient = int(Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq').read_text()) // 4
        except (OSError, ValueError):
            coefficient = 375000
        interface = f'''adapter driver bcm2835gpio
bcm2835gpio peripheral_base 0xFE000000
bcm2835gpio speed_coeffs {coefficient} 52
transport select swd
if {{[catch {{adapter gpio swdio 8 -chip 0}}]}} {{
    bcm2835gpio_swd_nums 11 8
}} else {{ adapter gpio swclk 11 -chip 0 }}'''
    else:
        interface = '''adapter driver st-link
st-link vid_pid 0x0483 0x3744 0x0483 0x3748 0x0483 0x374b 0x0483 0x374d 0x0483 0x374e 0x0483 0x374f 0x0483 0x3752 0x0483 0x3753 0x0483 0x3754 0x0483 0x3755 0x0483 0x3757
if {[catch {transport select swd}]} { transport select dapdirect_swd }'''
    return interface


def configuration(adapter):
    return r'''set SCRIPT_DIR [file dirname [string map {\\ /} [info script]]]
cd $SCRIPT_DIR
if {$PHASE eq "boot"} {
    set BIN_FILE samd21_sam_ba.bin
    set FLASH_ADDR 0
} elseif {$PHASE eq "app"} {
    set BIN_FILE firmware.sketch.bin
    set FLASH_ADDR 0x2000
} elseif {$PHASE eq "combined"} {
    set BIN_FILE firmware.with_bootloader.bin
    set FLASH_ADDR 0
} else { error "Invalid PHASE" }
if {![file exists $BIN_FILE]} { error "Missing binary: $BIN_FILE" }
''' + adapter_configuration(adapter) + '''
set CHIPNAME at91samd21j18
source [find target/at91samdXX.cfg]
adapter speed 100
reset_config none
cortex_m reset_config sysresetreq
if {[info exists J18_CHECK_ONLY]} { shutdown }
init
reset halt
if {$PHASE ne "app"} {
    at91samd bootloader 0
    reset halt
}
flash write_image erase $BIN_FILE $FLASH_ADDR bin
verify_image $BIN_FILE $FLASH_ADDR bin
if {$PHASE ne "app"} { at91samd bootloader 8192 }
reset halt
verify_image $BIN_FILE $FLASH_ADDR bin
if {$J18_RUN_AFTER} { reset run }
shutdown
'''


class Engine:
    def __init__(self, platform):
        self.platform = platform
        self.tools = platform_tools(platform)

    def tool(self, name):
        variable = 'J18_ARDUINO_CLI' if name == 'arduino-cli' else 'J18_OPENOCD'
        tool = self.tools.find_tool(name, variable)
        if not tool:
            tool = self.tools.install_arduino() if name == 'arduino-cli' else self.tools.install_openocd()
        return Path(tool)

    def probe(self, sketch, check, inspect, adapter, sudo, compare=True, app_offset=8192, board_map='auto', connect_under_reset=False, identify=False):
        from swd_probe import run_probe
        openocd = self.tool('openocd')
        if self.platform == 'linux':
            actual = self.tools.select_adapter(adapter, openocd)
        else:
            if adapter == 'gpio':
                raise ValueError('GPIO requires a Raspberry Pi 4; use ST-LINK on macOS.')
            actual = 'stlink'
        if run_probe(sketch, openocd, actual, self.platform, check, inspect, sudo=sudo,
                     compare_images=compare, app_offset=app_offset, board_map=board_map, connect_under_reset=connect_under_reset, identify_images=identify):
            raise RuntimeError('SWD probe found a failure; see .j18-build/swd/swd-report.json and swd-probe.log.')

    def install_profile(self, profile, source, sketchbook, check):
        from install_board_profile import main
        args = ['--profile', profile, '--arduino-cli', str(self.tool('arduino-cli'))]
        if source:
            args += ['--source', source]
        if sketchbook:
            args += ['--sketchbook', sketchbook]
        if check:
            args += ['--check-only']
        main(args)

    def core(self, cli):
        data = Path(self.tools.run([cli, 'config', 'get', 'directories.data'], quiet=True).strip())
        core = data / 'packages/arduino/hardware/samd/1.8.14'
        if not core.is_dir():
            self.tools.run([cli, 'core', 'update-index'])
            self.tools.run([cli, 'core', 'install', 'arduino:samd@1.8.14'])
        return data, core

    def loader(self, choice, local, firmware):
        if choice == 'current':
            path = firmware / 'samd21_sam_ba.bin'
            if path.is_file():
                return path.read_bytes()
            choice = 'github'
        if choice == 'core':
            _, core = self.core(self.tool('arduino-cli'))
            path = core / 'bootloaders/zero/samd21_sam_ba.bin'
        elif choice == 'github':
            path = ROOT / 'tools/RPi_J18_Tools/samd21_sam_ba.bin'
        elif choice in ('custom', 'target'):
            if choice == 'target':
                from bootloader_config import checked_target
                folder, _ = checked_target(firmware.parent.parent)
                path = folder / 'firmware/samd21_sam_ba.bin'
            else:
                path = ROOT / 'bootloader/firmware/samd21_sam_ba.bin'
            manifest = json.loads((path.parent / 'manifest.json').read_text())
            if digest(path) != manifest['sha256']:
                raise ValueError('Custom loader differs from its manifest; rebuild it first.')
        elif choice == 'local':
            if not local:
                raise ValueError('Choose a local bootloader file first.')
            path = Path(local).expanduser()
        else:
            raise ValueError('Unknown bootloader choice.')
        if choice in ('core', 'github') and digest(path) != OFFICIAL_SHA:
            raise ValueError('Official Arduino 1.8.14 bootloader checksum mismatch.')
        return path.read_bytes()

    def compile(self, sketch, build, board):
        cli = self.tool('arduino-cli')
        data, _ = self.core(cli)
        for name in ('compiler', 'compiler-output', 'sketch'):
            target = build / name
            if target.resolve().parent != build.resolve():
                raise ValueError('Compiler scratch path escaped the static folder.')
            safe_tree(target)
            if target.exists():
                shutil.rmtree(target)
        staged = build / 'sketch' / sketch.name
        staged.mkdir(parents=True)
        inputs = {}
        for item in sorted(sketch.iterdir()):
            if item.is_file() and (item.suffix.lower() in EXTENSIONS or item.name in ('sketch.json', 'sketch.yaml')):
                shutil.copy2(item, staged / item.name)
                inputs[item.name] = digest(item)
        for name in ('src', 'data'):
            source = sketch / name
            if source.is_dir():
                safe_tree(source)
                shutil.copytree(source, staged / name)
                for item in source.rglob('*'):
                    if item.is_file():
                        inputs[item.relative_to(sketch).as_posix()] = digest(item)
        args = []
        user = Path(self.tools.run([cli, 'config', 'get', 'directories.user'], quiet=True).strip())
        if board in ('custom', 'target'):
            from bootloader_config import checked_target
            if board == 'target':
                folder, manifest = checked_target(sketch)
            else:
                folder = ROOT / 'bootloader'
                manifest = json.loads((folder / 'firmware/manifest.json').read_text())
            profile = folder / 'arduino-profile'
            for name, expected in manifest['profile_sha256'].items():
                if digest(profile / 'hardware/custom/samd' / name) != expected:
                    raise ValueError('Generated Custom profile changed: ' + name)
            config = build / 'cli.json'
            record(config, {'directories': {'data': str(data), 'user': str(profile),
                                            'downloads': str(build / 'downloads')}})
            args += ['--config-file', config]
        extra = []
        for libraries in (user / 'libraries', sketch / 'libraries'):
            if libraries.is_dir():
                extra += ['--libraries', libraries]
        reference = build / 'firmware/samd21_sam_ba.bin'
        boot = reference.read_bytes() if reference.is_file() else None
        header = build / 'BuildSelection.h'
        text = selection_header(boot)
        override = sketch / 'J18BuildOverrides.h'
        if override.is_file():
            if any(c in override.as_posix() for c in '\n\r"'):
                raise ValueError('Sketch path cannot contain quotes or line breaks.')
            text += f'#include "{override.as_posix()}"\n'
        atomic(header, text.encode())
        flags = f'compiler.cpp.extra_flags=-include "{header.as_posix()}"'
        if (sketch / 'src/SdFat').is_dir():
            flags += f' -I"{(sketch / "src/SdFat").as_posix()}"'
        extra += ['--build-property', flags]
        properties = self.tools.run([cli, *args, 'compile', '--fqbn', BOARDS[board],
            '--show-properties=unexpanded', *extra, staged], quiet=True)
        if not re.search(r'^build.ldscript=.*flash_with_bootloader\.ld\s*$', properties, re.M):
            raise ValueError('Could not confirm the application linker script uses the bootloader offset.')
        output = build / 'compiler-output'
        print(f'Compiling {sketch.name} for {BOARDS[board]}...', flush=True)
        self.tools.run([cli, *args, 'compile', '--fqbn', BOARDS[board], '--build-path', build / 'compiler',
                        '--output-dir', output, *extra, staged])
        current = {}
        for item in sketch.iterdir():
            if item.is_file() and (item.suffix.lower() in EXTENSIONS or item.name in ('sketch.json', 'sketch.yaml')):
                current[item.name] = digest(item)
        for name in ('src', 'data'):
            for item in (sketch / name).rglob('*'):
                if item.is_file():
                    current[item.relative_to(sketch).as_posix()] = digest(item)
        if current != inputs or any(digest(staged / name) != value for name, value in inputs.items()):
            raise ValueError('Sketch changed during compilation; rebuild before flashing.')
        app = (output / (sketch.name + '.ino.bin')).read_bytes()
        elf = (output / (sketch.name + '.ino.elf')).read_bytes()
        vectors(app, 8192)
        target = build / 'firmware/firmware.sketch.bin'
        atomic(target, app)
        atomic(build / 'firmware/firmware.sketch.elf', elf)
        record(build / 'sketch-inputs.json', inputs)
        from bootloader_config import profile_signature
        record(build / 'sketch-build.json', {'schema': 2, 'completed': True, 'fqbn': BOARDS[board],
               'profile_signature': profile_signature(sketch, board),
               'board_config': manifest['board'] if board in ('custom', 'target') else None,
               'app_sha256': digest(target), 'source': 'compiled', 'hardware_tested': False})

    def action(self, sketch, board, mode, loader, local, check, rebuild, adapter, sudo):
        if not check and sys.platform != ('darwin' if self.platform == 'mac' else 'linux'):
            raise ValueError('Flashing requires the selected native platform.')
        build = sketch / '.j18-build'
        with locked(build):
            firmware = build / 'firmware'
            firmware.mkdir(exist_ok=True)
            with (build / 'build-and-flash.log').open('w', encoding='utf-8') as log:
                self.tools.LOG = log
                try:
                    if mode != 'app':
                        choice = (board if board in ('custom', 'target') else 'core') if mode == 'combined' else loader
                        boot = self.loader(choice, local, firmware)
                        vectors(boot, 0)
                        atomic(firmware / 'samd21_sam_ba.bin', boot)
                    if mode != 'boot':
                        app = firmware / 'firmware.sketch.bin'
                        receipt = build / 'sketch-build.json'
                        if app.is_file() and not rebuild:
                            from bootloader_config import profile_signature
                            saved = json.loads(receipt.read_text(encoding='utf-8-sig')) if receipt.is_file() else {}
                            cached = saved.get('fqbn', BOARDS['zero'])
                            if cached != BOARDS[board] or saved.get('profile_signature', '') != profile_signature(sketch, board):
                                self.tools.say(f'Board profile changed: {cached} -> {BOARDS[board]}. Recompiling automatically in the same static folder.')
                                self.compile(sketch, build, board)
                            vectors(app.read_bytes(), 8192)
                            self.tools.say('Using static sketch; source changes require R / --rebuild, profile changes rebuild automatically.')
                        else:
                            self.compile(sketch, build, board)
                    if mode == 'combined':
                        atomic(firmware / 'firmware.with_bootloader.bin', combine(boot, app.read_bytes()))
                    openocd = self.tool('openocd')
                    version = self.tools.run([openocd, '--version'], quiet=True)
                    match = re.search(r'Open On-Chip Debugger (\d+)\.(\d+)\.(\d+)', version)
                    if not match or tuple(map(int, match.groups())) < (0, 12, 0):
                        raise ValueError('OpenOCD 0.12+ is required; update OpenOCD and rerun.')
                    if self.platform == 'mac':
                        if adapter == 'gpio':
                            raise ValueError('GPIO SWD requires Raspberry Pi 4; use ST-LINK on macOS.')
                        actual = 'stlink'
                    else:
                        actual = self.tools.select_adapter(adapter, openocd)
                    config = firmware / 'flash_separate.cfg'
                    atomic(config, configuration(actual).encode())
                    command = [openocd]
                    for scripts in (openocd.parent.parent / 'share/openocd/scripts', openocd.parent.parent / 'scripts'):
                        if (scripts / 'target/at91samdXX.cfg').is_file():
                            command += ['-s', scripts]
                            break
                    for phase in PHASES[mode]:
                        self.tools.run([*command, '-c', f'set PHASE {phase}', '-c', 'set J18_CHECK_ONLY 1', '-f', config])
                    if not check:
                        prefix = self.tools.privileged() if self.platform == 'linux' and (sudo or actual == 'gpio') else []
                        for index, phase in enumerate(PHASES[mode]):
                            self.tools.say(f'Flashing {phase} image; readback verification follows...')
                            self.tools.run([*prefix, *command, '-c', f'set PHASE {phase}', '-c',
                                f'set J18_RUN_AFTER {int(index == len(PHASES[mode]) - 1)}', '-f', config])
                        self.tools.say('FLASH AND READBACK VERIFICATION SUCCEEDED.')
                    else:
                        self.tools.say('BUILD / CONFIG CHECK COMPLETE. No hardware was accessed.')
                    self.tools.say(f'Static files: {firmware}')
                finally:
                    self.tools.LOG = None


def pick(label, options):
    for i, (_, description) in enumerate(options, 1):
        print(f'{i}  {description}')
    value = input(label + ' number: ').strip()
    if not value.isdigit() or not 1 <= int(value) <= len(options):
        raise ValueError('Invalid selection.')
    return options[int(value) - 1][0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--platform', choices=('linux', 'mac'), default='mac' if sys.platform == 'darwin' else 'linux')
    p.add_argument('--mode', choices=(*PHASES, 'probe', 'install-profile'))
    p.add_argument('--sketch', default='Blink')
    p.add_argument('--board', choices=BOARDS, default='custom')
    p.add_argument('--bootloader', choices=('current', 'core', 'github', 'custom', 'target', 'local'), default='custom')
    p.add_argument('--bootloader-file')
    p.add_argument('--adapter', choices=('auto', 'stlink', 'gpio'), default='auto')
    p.add_argument('--build-only', action='store_true')
    p.add_argument('--rebuild', action='store_true')
    p.add_argument('--inspect-only', action='store_true', help='SWD probe: skip startup reset; halt/read/resume only.')
    p.add_argument('--probe-no-compare', action='store_true')
    p.add_argument('--probe-under-reset', action='store_true')
    p.add_argument('--probe-identify-images', action='store_true')
    p.add_argument('--probe-map', choices=('auto','generic','custom'), default='auto')
    p.add_argument('--probe-app-offset', type=lambda value: int(value,0), default=8192)
    p.add_argument('--profile', default='custom')
    p.add_argument('--profile-source')
    p.add_argument('--profile-sketchbook')
    p.add_argument('--sudo-flash', action='store_true', help='Linux only: elevate OpenOCD, not compilation; GPIO always uses sudo.')
    p.add_argument('--list', action='store_true')
    a = p.parse_args()
    sketches = available()
    if a.list:
        print('\n'.join(x.name for x in sketches))
        return
    from menu_settings import read as read_settings, write as write_settings
    settings_file = Path(os.environ.get('J18_MENU_SETTINGS', ROOT / '.build/menu' / ('settings-' + a.platform + '.json')))
    if not a.mode:
        saved = read_settings(settings_file, [x.name for x in sketches])
        for key, value in saved.items():
            flag = '--' + key.replace('_', '-')
            if not any(arg == flag or arg.startswith(flag + '=') for arg in sys.argv[1:]):
                setattr(a, key, value)
    sketch = next((x for x in sketches if x.name == a.sketch), None)
    if sketch is None:
        raise ValueError('Unknown sketch: ' + a.sketch)
    engine = Engine(a.platform)
    if a.mode:
        if a.mode == 'install-profile':
            engine.install_profile(a.profile, a.profile_source, a.profile_sketchbook, a.build_only)
            return
        if a.mode == 'probe':
            engine.probe(sketch, a.build_only, a.inspect_only, a.adapter, a.sudo_flash,
                         not a.probe_no_compare, a.probe_app_offset, a.probe_map, a.probe_under_reset, a.probe_identify_images)
            return
        engine.action(sketch, a.board, a.mode, a.bootloader, a.bootloader_file,
                      a.build_only, a.rebuild, a.adapter, a.sudo_flash)
        return
    while True:
        write_settings(settings_file, sketch=sketch.name, board=a.board, bootloader=a.bootloader,
                       bootloader_file=a.bootloader_file, build_only=a.build_only,
                       adapter=a.adapter, sudo_flash=a.sudo_flash)
        print('\nSAMD21 CUSTOM BUILD AND FLASH')
        print(f'Board profile: {LABELS[a.board]} ({BOARDS[a.board]})')
        loader_labels = {'current': 'Current static binary', 'core': 'Official installed Arduino core',
                         'github': 'Official GitHub ArduinoCore-samd', 'custom': 'Custom repository defaults',
                         'target': 'Configured custom loader for selected sketch', 'local': 'Local .bin'}
        print(f'Sketch: {sketch.name}\nBootloader: {loader_labels[a.bootloader]}\nAdapter: {a.adapter}')
        from bootloader_config import target_root
        source_file = target_root(sketch) / 'selection.json'
        try:
            source_label = json.loads(source_file.read_text())['source'] if source_file.is_file() else str(ROOT / 'bootloader')
        except (OSError, ValueError, KeyError):
            source_label = 'Invalid saved source selection; review L configuration'
        print('Custom bootloader source: ' + source_label)
        print('Mode: ' + ('BUILD / CHECK ONLY - no hardware access' if a.build_only else 'FLASH - options 1-4 program the connected board'))
        print(f'Files: {sketch / ".j18-build/firmware"}\n')
        verb = 'BUILD/CHECK' if a.build_only else 'FLASH'
        print(f'{verb} ACTIONS (reuse binaries; missing app / changed profile compiles automatically):')
        print(f"1  {verb} combined image: board profile's bootloader + selected sketch")
        print('   One .with_bootloader.bin at 0x0000; Zero = Arduino, Custom = custom.')
        print(f'2  {verb} two files: selected bootloader at 0x0000, then sketch at 0x2000')
        print('   Use for GitHub, Custom, or a locally replaced bootloader.')
        print('   Bootloader selector B applies to actions 2 and 3.')
        print(f'3  {verb} bootloader only at 0x0000; leaves sketch bytes intact')
        print(f'4  {verb} sketch only at 0x2000; leaves bootloader intact\n')
        print('SETTINGS / FILES (these do not flash):')
        print('S  Choose sketch       P  Choose board profile       B  Choose bootloader')
        print('T  Switch FLASH <-> BUILD/CHECK ONLY')
        print('R  Recompile sketch for selected board profile, without flashing')
        print('L  Configure + compile custom bootloader for this sketch (no flashing)')
        print('G  Rebuild repository DEFAULT Custom loader + profile (B option 4; no flashing)')
        print('Q  Install/update Arduino board profile (T = preview only; no flashing)')
        print('E  Arduino profile workflow: build + install and/or export a release ZIP')
        print('I  Import sketch-only .bin for selected profile       O  Open firmware folder')
        if a.platform == 'linux':
            print(f'A  Choose auto / ST-LINK / Pi 4 GPIO       U  sudo for ST-LINK flash: {a.sudo_flash}')
        print('0  Exit')
        print('\nD  SWD PROBE: chip / flash / CPU / startup (no flashing; halts CPU)')
        print('   T = BUILD/CHECK prepares probe files only, without hardware access.')
        choice = input('Choose: ').strip().upper()
        try:
            if choice == '0':
                return
            elif choice == 'S':
                sketch = pick('Sketch', [(x, x.name) for x in sketches])
            elif choice == 'P':
                a.board = pick('Profile', list(LABELS.items()))
                print('The next sketch action automatically rebuilds if its saved profile differs.')
            elif choice == 'D':
                routine = pick('Probe routine', [('full', 'Full probe: inspect flash/CPU, then software reset and startup checkpoints'),
                                                 ('inspect', 'Inspect only: halt/read/resume; no startup reset'),
                                                 ('generic', 'General SAMD21 inspection: no local comparisons, generic pins, no reset'),
                                                 ('recovery', 'ST-LINK recovery: NRST -> SAMD RESET wire REQUIRED; resets, generic inspection, no flashing'),
                                                 ('full_reset', 'Full probe WITH wired ST-LINK NRST: image checks + fault frame + startup; no flashing'),
                                                 ('identify', 'Identify installed bootloader/sketch among local compiled images; choose reset/no-reset connection')])
                wired = routine in ('recovery', 'full_reset')
                if routine == 'identify':
                    wired = pick('Connection', [(False, 'Normal connection; no reset wire used'),
                                                 (True, 'Wired ST-LINK NRST -> SAMD RESET; resets during connection')])
                engine.probe(sketch, a.build_only, routine not in ('full', 'full_reset'), a.adapter, a.sudo_flash,
                             routine not in ('generic', 'recovery', 'identify'), a.probe_app_offset,
                             'generic' if routine in ('generic', 'recovery', 'identify') else a.probe_map, wired, routine == 'identify')
            elif choice == 'E':
                from arduino_profile_workflow import main as profile_workflow
                target, build, export = pick('Arduino profile workflow (BUILD/CHECK previews only; no flashing)', [
                    (('default', True, False), 'Rebuild + install repository DEFAULT profile'),
                    (('sketch', True, False), 'Rebuild + install selected sketch CUSTOM profile'),
                    (('default', False, True), 'Export repository DEFAULT profile folder + ZIP'),
                    (('sketch', False, True), 'Export selected sketch CUSTOM profile folder + ZIP'),
                    (('default', True, True), 'Rebuild + install + export repository DEFAULT profile'),
                    (('sketch', True, True), 'Rebuild + install + export selected sketch CUSTOM profile')])
                arguments = ['--target', target]
                if target == 'sketch':
                    arguments += ['--sketch', str(sketch)]
                if build:
                    arguments += ['--build', '--install']
                if export:
                    arguments += ['--export']
                if a.profile_sketchbook:
                    arguments += ['--sketchbook', a.profile_sketchbook]
                if a.build_only:
                    arguments += ['--check-only']
                elif build:
                    cli = engine.tool('arduino-cli')
                    data, _ = engine.core(cli)
                    arguments += ['--arduino-cli', str(cli), '--arduino-package', str(data / 'packages/arduino')]
                if profile_workflow(arguments):
                    raise RuntimeError('Arduino profile workflow failed.')
                if build and not a.build_only:
                    a.board = a.bootloader = 'target' if target == 'sketch' else 'custom'
            elif choice == 'Q':
                catalog = json.loads((ROOT / 'arduino-profiles.json').read_text())
                options = [(key, item['name']) for key, item in catalog['profiles'].items()]
                options += [('__folder__', 'Another profile folder containing hardware/<vendor>/<architecture>')]
                options += [('__target__', 'Configured custom profile for selected sketch')]
                profile = pick('Profile to install', options)
                source = input('Profile root folder: ').strip().strip('"') if profile == '__folder__' else None
                if profile == '__target__':
                    from bootloader_config import checked_target
                    folder, _ = checked_target(sketch)
                    source = str(folder / 'arduino-profile')
                engine.install_profile(profile, source, a.profile_sketchbook, a.build_only)
            elif choice == 'B':
                a.bootloader = pick('Bootloader', [('current', 'Current static file (bundled GitHub fallback)'),
                    ('core', 'Official installed Arduino core 1.8.14'), ('github', 'Official GitHub 1.8.14 (verified bundled copy)'),
                    ('custom', 'Repository DEFAULT Custom bootloader (rebuild with G)'),
                    ('target', 'Configured custom bootloader for selected sketch (build with L -> C)'), ('local', 'Local .bin')])
                if a.bootloader == 'local':
                    a.bootloader_file = input('Full path to bootloader .bin: ').strip().strip('"')
            elif choice == 'T':
                a.build_only = not a.build_only
            elif choice == 'A' and a.platform == 'linux':
                a.adapter = pick('Adapter', [('auto', 'Auto: Pi 4 GPIO when supported, otherwise ST-LINK'),
                    ('stlink', 'USB ST-LINK'), ('gpio', 'Raspberry Pi 4 BCM8 SWDIO / BCM11 SWCLK')])
            elif choice == 'U' and a.platform == 'linux':
                a.sudo_flash = not a.sudo_flash
            elif choice == 'L':
                from bootloader_config import configure
                cli = engine.tool('arduino-cli')
                data, _ = engine.core(cli)
                built, _ = configure(sketch, data / 'packages/arduino')
                if built:
                    a.board = a.bootloader = 'target'
                    print('Selected configured custom board profile and bootloader. Next sketch action recompiles if needed.')
            elif choice == 'O':
                # Opening files does not rebuild the loader or select another profile.
                folder = sketch / '.j18-build/firmware'
                safe_tree(folder)
                folder.mkdir(parents=True, exist_ok=True)
                subprocess.run(['open' if a.platform == 'mac' else 'xdg-open', str(folder)], check=True)
            elif choice == 'G':
                from bootloader_config import rebuild_default
                cli = engine.tool('arduino-cli')
                data, _ = engine.core(cli)
                rebuild_default(data / 'packages/arduino')
                a.board = a.bootloader = 'custom'
                print('Selected repository Custom board profile and B option 4. Next sketch action rebuilds if the profile changed.')
            elif choice == 'I':
                data = Path(input('Path to sketch-only .bin linked at 0x2000: ').strip().strip('"')).expanduser().read_bytes()
                vectors(data, 8192)
                build = sketch / '.j18-build'
                with locked(build):
                    from bootloader_config import profile_signature
                    signature = profile_signature(sketch, a.board)
                    if a.board == 'target':
                        from bootloader_config import checked_target
                        board_config = checked_target(sketch)[1]['board']
                    else:
                        board_config = json.loads((ROOT / 'bootloader/board.json').read_text()) if a.board == 'custom' else None
                    folder = build / 'firmware'
                    folder.mkdir(exist_ok=True)
                    atomic(folder / 'firmware.sketch.bin', data)
                    record(build / 'sketch-build.json', {'schema': 2, 'completed': True, 'fqbn': BOARDS[a.board],
                        'profile_signature': signature,
                        'board_config': board_config,
                        'source': 'manual import for selected profile', 'hardware_tested': False})
                print('Stored image. No flashing performed.')
            elif choice in ('1', '2', '3', '4', 'R'):
                mode = {'1': 'combined', '2': 'separate', '3': 'boot', '4': 'app', 'R': 'app'}[choice]
                engine.action(sketch, a.board, mode, a.bootloader, a.bootloader_file,
                              a.build_only or choice == 'R', choice == 'R', a.adapter, a.sudo_flash)
            else:
                raise ValueError('Choose a listed option.')
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            print('FAILED: ' + str(error))
        input('Press Enter to return to the menu...')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nClosed.')
