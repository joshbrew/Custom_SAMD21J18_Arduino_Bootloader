#!/usr/bin/env python3
"""Build a nearby Arduino sketch and flash SAMD21J18 over GPIO or ST-LINK."""
import argparse
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
FQBN = 'arduino:samd:arduino_zero_native'
BOOT_URL = 'https://raw.githubusercontent.com/arduino/ArduinoCore-samd/1.8.14/bootloaders/zero/samd21_sam_ba.bin'
LOG = None

def say(text):
    print(text, flush=True)
    if LOG:
        LOG.write(str(text) + '\n')
        LOG.flush()

def run(command, quiet=False, cwd=None):
    process = subprocess.Popen([str(x) for x in command], cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='replace')
    lines = []
    for line in process.stdout:
        line = line.rstrip('\r\n')
        lines.append(line)
        if not quiet:
            say(line)
    status = process.wait()
    result = '\n'.join(lines)
    if status:
        if quiet and result:
            say(result)
        raise RuntimeError(f'Command failed ({status}): {command[0]}')
    return result

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def download(url, destination):
    say(f'Downloading: {url}')
    request = urllib.request.Request(url, headers={'User-Agent': 'OPEnS-J18-Tools'})
    with urllib.request.urlopen(request, timeout=90) as response, Path(destination).open('wb') as stream:
        shutil.copyfileobj(response, stream)

def privileged():
    if getattr(os, 'geteuid', lambda: -1)() == 0:
        return []
    if not shutil.which('sudo'):
        raise RuntimeError('sudo is required for package installation and flashing.')
    return ['sudo']

def install_openocd():
    if sys.platform != 'linux' or not shutil.which('apt-get'):
        raise RuntimeError('Install OpenOCD 0.12 or newer, or set J18_OPENOCD to its executable.')
    say('Installing OpenOCD using the system package manager...')
    run(privileged() + ['apt-get', 'update'])
    run(privileged() + ['apt-get', 'install', '-y', 'openocd', 'ca-certificates'])
    tool = shutil.which('openocd')
    if not tool:
        raise RuntimeError('OpenOCD was not found after installation.')
    return tool

def install_arduino():
    if sys.platform != 'linux':
        raise RuntimeError('Automatic Arduino CLI installation in this package requires Linux.')
    machine = platform.machine().lower()
    if machine in ('aarch64', 'arm64') and struct.calcsize('P') == 8:
        architecture, expected = 'ARM64', '1e69e077479f300614d4551334e0a33f08ee40b04315d83b8e7e0e94f0d0ee62'
    elif machine.startswith('armv6'):
        architecture, expected = 'ARMv6', '168aa0c632d7079fea0ccd30b3f1e928e89e2f59a339404f1d2f4a07ed6cc566'
    elif machine.startswith('arm') or machine == 'aarch64':
        architecture, expected = 'ARMv7', '890af36e9873606e4dfa743534846186621b9f3a339175ea3ec481adecf07143'
    elif machine in ('x86_64', 'amd64'):
        architecture, expected = '64bit', '28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b'
    else:
        raise RuntimeError(f'No bundled Arduino CLI download selection for {machine}.')
    store = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    folder = store / 'OPEnS/j18-tools/arduino-cli/1.5.1' / architecture
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'arduino-cli.tar.gz'
    download(f'https://github.com/arduino/arduino-cli/releases/download/v1.5.1/arduino-cli_1.5.1_Linux_{architecture}.tar.gz', archive)
    if sha(archive) != expected:
        raise RuntimeError('Arduino CLI download checksum failed. Installation stopped.')
    executable = folder / 'arduino-cli'
    with tarfile.open(archive) as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and Path(m.name).name == 'arduino-cli'), None)
        if member is None:
            raise RuntimeError('Arduino CLI archive did not contain the executable.')
        with tar.extractfile(member) as src, executable.open('wb') as dest:
            shutil.copyfileobj(src, dest)
    executable.chmod(0o755)
    return str(executable)

def find_tool(name, variable):
    override = os.environ.get(variable)
    if override:
        if not Path(override).is_file():
            raise RuntimeError(f'{variable} does not point to a file.')
        return override
    tool = shutil.which(name)
    if tool:
        return tool
    if name == 'arduino-cli':
        local = Path.home() / '.local/bin/arduino-cli'
        if local.is_file():
            return str(local)
        store = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
        candidates = sorted((store / 'OPEnS/j18-tools/arduino-cli').glob('*/*/arduino-cli'), reverse=True)
        if candidates:
            return str(candidates[0])
    return None

def find_bootloader(sketch, include_package=True):
    direct = sketch / 'samd21_sam_ba.bin'
    if direct.is_file():
        return direct
    for folder in (sketch / 'build', sketch / '.j18-build'):
        if folder.is_dir():
            candidates = sorted(folder.rglob('samd21_sam_ba.bin'))
            if candidates:
                hashes = {sha(p) for p in candidates}
                if len(hashes) > 1:
                    raise RuntimeError(f'Different bootloaders found under {folder}; put the intended file beside the sketch.')
                return candidates[0]
    bundled = PACKAGE / 'samd21_sam_ba.bin'
    return bundled if include_package and bundled.is_file() else None

def select_sketch(folder):
    files = sorted(folder.glob('*.ino'))
    main = folder / (folder.name + '.ino')
    if main.is_file():
        return main
    if len(files) == 1:
        return files[0]
    if not files:
        raise RuntimeError(f'No .ino found in {folder}. Put the .ino beside this tools folder or inside it.')
    raise RuntimeError('Multiple .ino files found; the main .ino must match the sketch folder name.')

def pi_model():
    path = Path('/proc/device-tree/model')
    return path.read_text(errors='replace').rstrip('\0') if path.exists() else ''

def select_adapter(requested, openocd):
    if requested == 'stlink':
        return 'stlink'
    model = pi_model()
    drivers = run([openocd, '-c', 'adapter list', '-c', 'shutdown'], quiet=True)
    gpio_available = 'Raspberry Pi 4' in model and 'bcm2835gpio' in drivers
    if requested == 'gpio' and not gpio_available:
        raise RuntimeError('GPIO mode requires a Pi 4 and OpenOCD built with bcm2835gpio. Use --adapter stlink otherwise.')
    return 'gpio' if gpio_available else 'stlink'

def validate_vectors(boot, app):
    if not 8 <= len(boot) <= 8192 or len(app) < 8 or len(app) + 8192 > 262144:
        raise RuntimeError('Bootloader or sketch size does not fit the SAMD21J18 layout.')
    for label, data, offset in [('bootloader', boot, 0), ('sketch', app, 8192)]:
        sp, reset = struct.unpack_from('<II', data)
        alignment = 4 if offset == 0 else 8
        if not 0x20000000 < sp <= 0x20008000 or sp % alignment or not reset & 1 or not offset <= (reset & ~1) < offset + len(data):
            raise RuntimeError(f'Invalid {label} vector table for flash offset {offset:#x}.')

def configuration(adapter, mode):
    if adapter == 'gpio':
        clock = Path('/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq')
        try:
            coefficient = int(clock.read_text().strip()) // 4
        except (OSError, ValueError):
            coefficient = 375000
        adapter_config = f'''adapter driver bcm2835gpio
bcm2835gpio peripheral_base 0xFE000000
bcm2835gpio speed_coeffs {coefficient} 52
transport select swd
adapter gpio swdio 8 -chip 0
adapter gpio swclk 11 -chip 0'''
    else:
        adapter_config = '''if {[catch {find interface/stlink-dap.cfg} STLINK_CFG]} {
    set STLINK_CFG [find interface/stlink.cfg]
}
source $STLINK_CFG
if {[adapter name] ne "st-link"} { error "Native ST-LINK driver required" }
if {[catch {transport select dapdirect_swd}]} { transport select swd }'''
    common = adapter_config + '''
set CHIPNAME at91samd21j18
source [find target/at91samdXX.cfg]
adapter speed 100
reset_config none
cortex_m reset_config sysresetreq
'''
    if mode == 'combined':
        return common + '''set BIN_FILE firmware.with_bootloader.bin
if {![file exists $BIN_FILE]} { error "Missing combined binary" }
if {[info exists J18_CHECK_ONLY]} { shutdown }
init
reset halt
at91samd bootloader 0
reset halt
flash write_image erase $BIN_FILE 0x00000000 bin
verify_image $BIN_FILE 0x00000000 bin
at91samd bootloader 8192
reset halt
verify_image $BIN_FILE 0x00000000 bin
reset run
shutdown
'''
    return common + '''if {![info exists PHASE]} { error "Set PHASE to boot or app" }
if {$PHASE eq "boot"} {
    set BIN_FILE samd21_sam_ba.bin
    set FLASH_ADDR 0x00000000
} elseif {$PHASE eq "app"} {
    set BIN_FILE firmware.sketch.bin
    set FLASH_ADDR 0x00002000
} else { error "Invalid phase" }
if {![file exists $BIN_FILE]} { error "Missing binary: $BIN_FILE" }
if {[info exists J18_CHECK_ONLY]} { shutdown }
init
reset halt
if {$PHASE eq "boot"} { at91samd bootloader 0; reset halt }
flash write_image erase $BIN_FILE $FLASH_ADDR bin
verify_image $BIN_FILE $FLASH_ADDR bin
if {$PHASE eq "boot"} { at91samd bootloader 8192 }
reset halt
verify_image $BIN_FILE $FLASH_ADDR bin
if {$PHASE eq "app"} { reset run }
shutdown
'''

def main(args):
    global LOG
    if args.sketch_dir:
        sketch = Path(args.sketch_dir).resolve()
    else:
        sketch = next((p for p in (PACKAGE, PACKAGE.parent) if any(p.glob('*.ino'))), None)
        if sketch is None:
            raise RuntimeError('No .ino found in the tools folder or its parent folder.')
    main_ino = select_sketch(sketch)
    say(f'Sketch: {main_ino}')
    say(f'Board: {FQBN}; workflow: {args.mode}')
    arduino = find_tool('arduino-cli', 'J18_ARDUINO_CLI')
    openocd = find_tool('openocd', 'J18_OPENOCD')
    if args.check:
        for label, tool, option in [('Arduino CLI', arduino, 'version'), ('OpenOCD', openocd, '--version')]:
            say(f'{label}: {tool or "missing; a build/flash run will install it"}')
            if tool:
                run([tool, option])
        say(f'Pi model: {pi_model() or "not detected"}')
        say(f'Adapter: {select_adapter(args.adapter, openocd) if openocd else args.adapter}')
        say(f'Local standalone bootloader: {find_bootloader(sketch) or "none; will download when needed"}')
        say('Check complete. No installation, compilation, or target access.')
        return
    if not args.build_only and sys.platform != 'linux':
        raise RuntimeError('Hardware flashing with this package requires Linux.')
    run_root = sketch / '.j18-build' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    output = run_root / 'firmware'
    output.mkdir(parents=True)
    LOG = (run_root / 'build-and-flash.log').open('w', encoding='utf-8')
    try:
        arduino = arduino or install_arduino()
        openocd = openocd or install_openocd()
        cli_version = run([arduino, 'version'])
        match = re.search(r'Version:\s*(\d+)\.', cli_version)
        if not match or int(match[1]) < 1:
            arduino = install_arduino()
        ocd_version = run([openocd, '--version'])
        match = re.search(r'Open On-Chip Debugger\s+(\d+)\.(\d+)\.(\d+)', ocd_version)
        if not match or tuple(map(int, match.groups())) < (0, 12, 0):
            raise RuntimeError('OpenOCD 0.12 or newer is required; update the system OpenOCD package.')
        adapter = select_adapter(args.adapter, openocd)
        say(f'Using adapter: {adapter}')
        if adapter == 'gpio':
            say('BCM8 / physical 24 -> PA31 SWDIO; BCM11 / physical 23 -> PA30 SWCLK; common GND.')
        else:
            say('ST-LINK SWDIO -> PA31; SWCLK -> PA30; common GND and target 3.3 V reference.')
        say('Power the target normally. Reset wire is not used.')
        say('Checking Arduino SAMD board support...')
        try:
            cores = run([arduino, 'core', 'list'])
        except RuntimeError:
            cores = ''
        if not re.search(r'^arduino:samd\s+1\.8\.14(?:\s|$)', cores, re.M):
            run([arduino, 'core', 'update-index'])
            run([arduino, 'core', 'install', 'arduino:samd@1.8.14'])
        source = sketch
        if main_ino.stem != sketch.name:
            source = run_root / 'sketch' / main_ino.stem
            source.mkdir(parents=True)
            extensions = {'.ino','.pde','.h','.hpp','.hh','.c','.cpp','.cc','.cxx','.s','.ipp','.tpp'}
            for file in sketch.iterdir():
                if file.is_file() and (file.suffix.lower() in extensions or file.name == 'sketch.json'):
                    shutil.copy2(file, source / file.name)
            for name in ('src', 'data'):
                if (sketch / name).is_dir():
                    shutil.copytree(sketch / name, source / name)
        extra = ['--libraries', str(sketch / 'libraries')] if (sketch / 'libraries').is_dir() else []
        override = sketch / 'J18BuildOverrides.h'
        if override.is_file():
            cpp_flags = '-include "' + override.as_posix() + '"'
            vendor = sketch / 'src/SdFat'
            if vendor.is_dir():
                cpp_flags += ' -I"' + vendor.as_posix() + '"'
            extra += ['--build-property', 'compiler.cpp.extra_flags=' + cpp_flags]
            say('Applying sketch J18BuildOverrides.h to core and sketch C++ compilation.')
        say('Compiling sketch...')
        properties = run([arduino,'compile','--fqbn',FQBN,'--show-properties',*extra,source], quiet=True)
        props = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
        if not props.get('build.ldscript', '').endswith('flash_with_bootloader.ld'):
            raise RuntimeError('Sketch is not linked for the 0x2000 Arduino bootloader offset.')
        say('Building firmware...')
        run([arduino,'compile','--fqbn',FQBN,'--build-path',run_root/'compiler',
             '--output-dir',output,'--export-binaries',*extra,source])
        files = list(output.glob('*.ino.bin'))
        if len(files) != 1:
            raise RuntimeError('Expected exactly one freshly compiled sketch-only .ino.bin.')
        app = files[0].read_bytes()
        if args.mode == 'combined':
            boot_path = Path(props['runtime.platform.path']) / 'bootloaders' / props['bootloader.file']
            if boot_path.suffix.lower() != '.bin':
                raise RuntimeError('Expected a binary Arduino Zero bootloader.')
        else:
            boot_path = find_bootloader(sketch)
            if not boot_path:
                boot_path = output / 'samd21_sam_ba.bin.download'
                download(BOOT_URL, boot_path)
                if sha(boot_path) != "89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b":
                    raise RuntimeError("Pinned Arduino bootloader download checksum failed.")
        say(f'Bootloader source: {boot_path}')
        boot = boot_path.read_bytes()
        validate_vectors(boot, app)
        (output / 'samd21_sam_ba.bin').write_bytes(boot)
        (output / 'firmware.sketch.bin').write_bytes(app)
        if args.mode == 'combined':
            (output / 'firmware.with_bootloader.bin').write_bytes(boot + b'\xff' * (8192-len(boot)) + app)
        say(f'Bootloader SHA256: {hashlib.sha256(boot).hexdigest()}')
        config = output / 'flash.cfg'
        config.write_text(configuration(adapter, args.mode), encoding='ascii')
        tool_base = Path(openocd).resolve().parent.parent
        scripts = next((p for p in (tool_base / 'share/openocd/scripts', tool_base / 'scripts')
                        if (p / 'target/at91samdXX.cfg').is_file()), None)
        script_args = ['-s', scripts] if scripts else []
        phases = [None] if args.mode == 'combined' else ['boot', 'app']
        for phase in phases:
            phase_args = ['-c', f'set PHASE {phase}'] if phase else []
            run([openocd,*script_args,*phase_args,'-c','set J18_CHECK_ONLY 1','-f',config], cwd=output)
        if args.build_only:
            say('BUILD COMPLETE. Configuration checked; no hardware accessed.')
        else:
            for phase in phases:
                say('Flashing combined image at 0x0000...' if phase is None else
                    ('Flashing standalone bootloader at 0x0000...' if phase == 'boot' else 'Flashing sketch at 0x2000...'))
                phase_args = ['-c', f'set PHASE {phase}'] if phase else []
                run([*privileged(),openocd,*script_args,*phase_args,'-f',config], cwd=output)
            say('FLASH AND VERIFICATION SUCCEEDED.')
        say(f'Firmware and log: {run_root}')
    finally:
        LOG.close()
        LOG = None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('combined','separate'), default='combined')
    parser.add_argument('--adapter', choices=('auto','gpio','stlink'), default='auto')
    parser.add_argument('--sketch-dir')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--pause', action='store_true')
    args = parser.parse_args()
    status = 0
    try:
        main(args)
    except (Exception, KeyboardInterrupt) as error:
        say(f'FAILED: {error}')
        status = 1
    if args.pause and sys.stdin.isatty():
        try:
            input('Press Enter to close...')
        except (EOFError, KeyboardInterrupt):
            pass
    sys.exit(status)
