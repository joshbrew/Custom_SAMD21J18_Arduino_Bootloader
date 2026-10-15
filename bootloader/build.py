#!/usr/bin/env python3
"""Build the pinned Arduino SAM-BA source with Custom defaults. Never flashes."""
import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib
import uuid

ROOT = Path(__file__).resolve().parent
SOURCES = ('board_driver_i2c board_driver_led board_driver_pmic board_driver_jtag '
           'board_driver_serial board_driver_usb board_init board_startup main '
           'sam_ba_usb sam_ba_cdc sam_ba_monitor sam_ba_serial custom_board').split()
VALID = [f'PA{i:02}' for i in range(32) if i not in (26, 29)] + [
    f'PB{i:02}' for i in list(range(18)) + [22, 23, 30, 31]]
RESERVED = {'PA00', 'PA01', 'PA24', 'PA25', 'PA30', 'PA31'}
PIN_NAMES = set('LED UART0_TX UART0_RX UART1_TX UART1_RX SDA SCL SPI0_SCK SPI0_MISO SPI0_MOSI CS0 SPI1_SCK SPI1_MISO SPI1_MOSI CS1'.split())


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_release(firmware, check_sources=True):
    m = json.loads((firmware / 'manifest.json').read_text())
    image = firmware / 'samd21_sam_ba.bin'
    data = image.read_bytes()
    if sha(image) != m['sha256'] or len(data) != m['size'] or f'{zlib.crc32(data):08x}' != m['crc32']:
        raise ValueError('Bootloader release binary/manifest mismatch; rebuild first.')
    if check_sources:
        for name, expected in {**m['source_sha256'], **m['build_inputs_sha256']}.items():
            if sha(ROOT / name) != expected:
                raise ValueError('Bootloader input changed since compilation: ' + name)
    return m


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def package_root():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Arduino15/packages/arduino'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Arduino15/packages/arduino'
    return Path.home() / '.arduino15/packages/arduino'


def load_board(path=ROOT / 'board.json'):
    return validate_board(json.loads(path.read_text(encoding='utf-8-sig')))


def validate_board(b):
    required = {'mcu', 'pins', 'safe_outputs', 'high_impedance', 'boot_indicator', 'crystalless', 'usb', 'revision', 'version'}
    if not isinstance(b, dict) or not required <= set(b):
        raise ValueError('Board settings must be an object with all required configuration fields.')
    if not isinstance(b['pins'], dict) or not all(isinstance(p, str) for p in b['pins'].values()):
        raise ValueError('Pin aliases must map to physical GPIO strings, such as PA05.')
    if not isinstance(b['safe_outputs'], dict) or not isinstance(b['high_impedance'], list) or not all(isinstance(x, str) for x in b['high_impedance']):
        raise ValueError('Boot states need an output object and a list of input alias names.')
    if not isinstance(b['usb'], dict) or not {'vid', 'boot_pid', 'app_pid', 'manufacturer', 'product'} <= set(b['usb']):
        raise ValueError('USB settings need VID, boot/app PIDs, manufacturer and product.')
    if b['mcu'] != '__SAMD21J18A__':
        raise ValueError('A different MCU needs a reviewed linker/startup change, not just a new define.')
    pins = b['pins']
    if not PIN_NAMES <= set(pins) or not all(re.fullmatch(r'[A-Z][A-Z0-9_]*', name) for name in pins):
        raise ValueError('Required interface aliases are missing, or a GPIO alias is not a valid identifier.')
    if any(p not in VALID or p in RESERVED for p in pins.values()):
        raise ValueError('Invalid package pin or reserved crystal/USB/SWD pin.')
    if len(set(pins.values())) != len(pins):
        raise ValueError('Two board functions share a pin. Resolve this before generating firmware.')
    outputs, inputs = b['safe_outputs'], b['high_impedance']
    if set(outputs) & set(inputs) or len(set(inputs)) != len(inputs):
        raise ValueError('Duplicate/conflicting boot state.')
    if set(outputs) | set(inputs) != set(pins) - {'LED'}:
        raise ValueError('Every board function except LED must have a boot state.')
    if any(type(level) is not int or level not in (0, 1) for level in outputs.values()):
        raise ValueError('Boot outputs must be integer 0 or 1.')
    if not isinstance(b['boot_indicator'], bool) or not isinstance(b['crystalless'], bool):
        raise ValueError('Clock/indicator options must be true or false.')
    for k in ('vid', 'boot_pid', 'app_pid'):
        if type(b['usb'][k]) is not int or not 0 < b['usb'][k] <= 65535:
            raise ValueError('USB IDs must fit 16 bits and be nonzero.')
    for text in (b['usb']['manufacturer'], b['usb']['product'], b['revision'], b['version']):
        if not isinstance(text, str) or not text.isascii() or not 0 < len(text) < 48 or any(ord(c) < 32 for c in text):
            raise ValueError('Names must be short printable ASCII strings.')
    if len(b['usb']['product']) > 42 or any(c in b['usb'][key] for key in ('manufacturer', 'product') for c in '\\"{}'):
        raise ValueError('USB names cannot contain quotes, backslashes or braces; product must be at most 42 characters.')
    return b


def generate(b, destination=ROOT / 'src'):
    pins, usb = b['pins'], b['usb']
    h = '// Generated from board.json by build.py; edit board.json instead.\n#pragma once\n'
    h += f'#define J18_BOARD_REVISION {json.dumps(b["revision"])}\n'
    h += f'#define J18_BOOT_VERSION {json.dumps(b["version"])}\n'
    h += f'#define J18_BOOT_INDICATOR {int(b["boot_indicator"])}\n'
    for name, p in pins.items():
        h += f'#define J18_{name} {int(p[2:]) + (32 if p[1] == "B" else 0)}u // {p}\n'
    h += '#define J18_SAFE_OUTPUTS(X) ' + ' '.join(f'X({k}, {v})' for k, v in b['safe_outputs'].items()) + '\n'
    h += '#define J18_HIGH_IMPEDANCE(X) ' + ' '.join(f'X({k})' for k in b['high_impedance']) + '\n'
    h += '#ifdef __cplusplus\nextern "C" {\n#endif\nvoid custom_board_early_init(void);\n#ifdef __cplusplus\n}\n#endif\n'
    write(destination / 'custom_board.h', h)
    d = '// Custom adaptation of Arduino Zero definitions; LGPL-2.1-or-later.\n'
    d += '#include "board_definitions_arduino_zero.h"\n#include "custom_board.h"\n'
    for name in ('STRING_PRODUCT', 'STRING_MANUFACTURER', 'USB_VID_HIGH', 'USB_VID_LOW',
                 'USB_PID_HIGH', 'USB_PID_LOW', 'BOARD_LED_PORT', 'BOARD_LED_PIN',
                 'BOARD_LEDRX_PORT', 'BOARD_LEDRX_PIN', 'BOARD_LEDTX_PORT', 'BOARD_LEDTX_PIN'):
        d += f'#undef {name}\n'
    d += f'#define STRING_PRODUCT {json.dumps(usb["product"] + " Boot") }\n'
    d += f'#define STRING_MANUFACTURER {json.dumps(usb["manufacturer"])}\n'
    for name, value in [('USB_VID', usb['vid']), ('USB_PID', usb['boot_pid'])]:
        d += f'#define {name}_HIGH 0x{value >> 8:02x}\n#define {name}_LOW 0x{value & 255:02x}\n'
    d += '#if J18_BOOT_INDICATOR\n#define BOARD_LED_PORT (J18_LED / 32)\n#define BOARD_LED_PIN (J18_LED % 32)\n#endif\n'
    write(destination / 'board_definitions_custom_j18.h', d)


def check_upstream():
    for entry in json.loads((ROOT / 'upstream/SHA256.json').read_text()):
        if sha(ROOT / 'upstream' / entry['name']) != entry['sha256']:
            raise ValueError('Pinned upstream changed: ' + entry['name'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--arduino-package', type=Path, default=package_root())
    p.add_argument('--out', type=Path, default=ROOT / 'firmware')
    p.add_argument('--board-config', type=Path, default=ROOT / 'board.json', help='Validated board settings JSON.')
    p.add_argument('--profile-out', type=Path, help='Arduino profile root; contains hardware/custom/samd.')
    p.add_argument('--verify-repeat', action='store_true', help='Build twice and require identical bytes.')
    p.add_argument('--generate-only', action='store_true')
    a = p.parse_args()
    check_upstream()
    b = load_board(a.board_config)
    from board_profile import validate_routes, generate_profile
    validate_routes(b)
    if a.generate_only:
        if a.board_config.resolve() != (ROOT / 'board.json').resolve():
            p.error('Alternate settings require a full isolated build, not --generate-only.')
        generate(b)
        return
    package = a.arduino_package.resolve()
    tools = package / 'tools'
    suffix = '.exe' if sys.platform == 'win32' else ''
    prefix = tools / 'arm-none-eabi-gcc/7-2017q4/bin/arm-none-eabi-'
    gcc = Path(str(prefix) + 'gcc' + suffix)
    includes = [tools / 'CMSIS/4.5.0/CMSIS/Include', tools / 'CMSIS-Atmel/1.2.0/CMSIS/Device/ATMEL']
    if not gcc.is_file() or any(not x.is_dir() for x in includes):
        raise FileNotFoundError('Install arduino:samd@1.8.14 with Arduino CLI, or pass --arduino-package PATH.')
    out = a.out.resolve()
    stage = ROOT / '.build' / ('boot-' + uuid.uuid4().hex)
    work = stage / 'objects'
    work.mkdir(parents=True, exist_ok=True)
    isolated = a.board_config.resolve() != (ROOT / 'board.json').resolve()
    if isolated and (out == (ROOT / 'firmware').resolve() or a.profile_out is None):
        p.error('Alternate settings require --out and --profile-out outside the default release.')
    profile_out = (a.profile_out or ROOT / 'arduino-profile').resolve()
    if isolated and profile_out == (ROOT / 'arduino-profile').resolve():
        p.error('Alternate settings cannot overwrite the default Arduino profile.')
    source = stage / 'src'
    shutil.copytree(ROOT / 'src', source)
    generate(b, source)
    common = ['-mthumb', '-mcpu=cortex-m0plus', '-Wall', '-Os', '-flto', '-DDEBUG=0',
              '-D' + b['mcu'], '-DBOARD_ID_custom_j18', '-DSAM_BA_USBCDC_ONLY']
    if b['crystalless']:
        common += ['-DCRYSTALLESS']
    compile_flags = common + ['-c', '-std=gnu99', '-ffunction-sections', '-fdata-sections', '-nostdlib',
                              '-nostartfiles', '--param', 'max-inline-insns-single=500']
    compile_flags += ['-I' + str(x) for x in includes]
    link_flags = common + ['-Wl,--cref', '-Wl,--check-sections', '-Wl,--gc-sections',
                           '-Wl,--unresolved-symbols=report-all', '-Wl,--warn-common',
                           '-Wl,--warn-section-align', '--specs=nano.specs', '--specs=nosys.specs']
    stem = stage / 'samd21_sam_ba'

    def build():
        print('Compiling Custom bootloader...', flush=True)
        objects = []
        for name in SOURCES:
            obj = work / (name + '.o')
            run([gcc, *compile_flags, source / (name + '.c'), '-o', obj], cwd=work)
            objects.append(obj)
        run([gcc, *link_flags, '-T' + str(ROOT / 'src/bootloader_samd21x18.ld'),
             '-Wl,-Map,' + str(stem.with_suffix('.map')), '-o', stem.with_suffix('.elf'),
             '-Wl,--start-group', *objects, '-lm', '-Wl,--end-group'], cwd=work)
        for fmt, ext in [('binary', '.bin'), ('ihex', '.hex')]:
            run([str(prefix) + 'objcopy' + suffix, '-O', fmt, stem.with_suffix('.elf'), stem.with_suffix(ext)])
        return stem.with_suffix('.bin').read_bytes()

    data = build()
    if a.verify_repeat and build() != data:
        raise ValueError('Repeat build differs; release not reproducible.')
    sp, reset = struct.unpack_from('<II', data)
    if not 0 < len(data) <= 8192 or not 0x20000000 < sp <= 0x20008000 or sp % 8 or not reset & 1 or not 0 <= (reset & ~1) < len(data):
        raise ValueError('Invalid boot image size/vectors.')
    symbols = run([str(prefix) + 'nm' + suffix, '-n', stem.with_suffix('.elf')], capture_output=True, text=True).stdout
    write(stem.with_suffix('.symbols.txt'), symbols)
    if not any(line.split()[:1] == ['00002000'] and line.endswith('__sketch_vectors_ptr') for line in symbols.splitlines()):
        raise ValueError('Application offset changed; refusing release.')
    crc = zlib.crc32(data)
    manifest = dict(board=b, source_base='ArduinoCore-samd 1.8.14 bootloaders/zero',
                    compiler=run([gcc, '--version'], capture_output=True, text=True).stdout.splitlines()[0],
                    compiler_sha256=sha(gcc), boot_offset=0, sketch_offset=8192, boot_region_bytes=8192,
                    size=len(data), crc32=f'{crc:08x}', sha256=hashlib.sha256(data).hexdigest(),
                    initial_sp=f'0x{sp:08x}', reset_vector=f'0x{reset:08x}',
                    repeat_build_verified=a.verify_repeat, hardware_tested=False,
                    build_inputs_sha256={name: sha(ROOT / name) for name in
                        ('build.py', 'board_profile.py', 'app_flash_with_bootloader.ld', 'upstream/SHA256.json')},
                    board_config_sha256=sha(a.board_config),
                    source_sha256={'src/' + x.name: sha(x) for x in sorted(source.iterdir()) if x.is_file()})
    if not isolated:
        manifest['build_inputs_sha256']['board.json'] = sha(ROOT / 'board.json')
    write(stage / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    write(stage / 'CustomBootloaderIdentity.h', '#pragma once\n'
          f'#define J18_CUSTOM_BOOTLOADER_BYTES {len(data)}u\n#define J18_CUSTOM_BOOTLOADER_CRC32 0x{crc:08x}ul\n')
    changes = []
    for x in sorted(source.iterdir()):
        if not x.is_file():
            continue
        old = ROOT / 'upstream' / x.name
        changes.extend(difflib.unified_diff(old.read_text().splitlines(True) if old.exists() else [],
                                          x.read_text().splitlines(True), fromfile='upstream/' + x.name, tofile='src/' + x.name))
    write(stage / 'CHANGES.patch', ''.join(changes))
    staged_profile = stage / 'profile'
    generate_profile(b, package, stage, destination=staged_profile,
                     aliases_out=ROOT / 'PIN_ALIASES.md' if not isolated else stage / 'PIN_ALIASES.md')
    manifest['profile_sha256'] = {x.relative_to(staged_profile).as_posix(): sha(x)
        for x in sorted(staged_profile.rglob('*')) if x.is_file() and x.name != 'manifest.json'}
    write(stage / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    write(staged_profile / 'bootloaders/custom/manifest.json', json.dumps(manifest, indent=2) + '\n')
    run([str(prefix) + 'size' + suffix, stem.with_suffix('.elf')])
    # Publish only a fully checked build. A compiler/profile failure leaves the
    # preceding release binary/manifest intact; scratch files stay under .build.
    out.mkdir(parents=True, exist_ok=True)
    for item in stage.iterdir():
        if item.is_file():
            shutil.copy2(item, out / item.name)
    shutil.copytree(staged_profile, profile_out / 'hardware/custom/samd', dirs_exist_ok=True)
    if not isolated:
        generate(b)
        shutil.copy2(stage / 'CHANGES.patch', ROOT / 'CHANGES.patch')
    print(f'Built {len(data)}/8192 bytes; CRC32 {crc:08x}; SHA256 {manifest["sha256"]}')
    print('Sketch offset: 0x2000. No hardware was flashed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print('FAILED: ' + str(e), file=sys.stderr)
        sys.exit(1)
