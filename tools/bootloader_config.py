#!/usr/bin/env python3
"""Configure and build a sketch-local SAMD21J18 loader/profile. Never flashes."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROLES = {
    'LED': 'Optional active-HIGH boot indicator; use a series resistor',
    'UART0_TX': 'Serial1 TX: SERCOM3 PAD0', 'UART0_RX': 'Serial1 RX: SERCOM3 PAD1',
    'UART1_TX': 'Serial2 TX: SERCOM1 PAD0', 'UART1_RX': 'Serial2 RX: SERCOM1 PAD1 or reviewed PAD2',
    'SDA': 'Wire SDA: SERCOM0 PAD0', 'SCL': 'Wire SCL: SERCOM0 PAD1',
    'CS0': 'Generic SPI0 chip-select GPIO', 'CS1': 'Generic SPI1 chip-select GPIO',
    'SPI0_SCK': 'SPI0 clock', 'SPI0_MISO': 'SPI0 input', 'SPI0_MOSI': 'SPI0 output',
    'SPI1_SCK': 'SPI1 clock', 'SPI1_MISO': 'SPI1 input', 'SPI1_MOSI': 'SPI1 output',
}

sys.path.insert(0, str(ROOT / 'bootloader'))
from build import load_board, validate_board, package_root
from board_profile import validate_routes


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8', newline='\n')
    os.replace(temporary, path)


def target_root(sketch):
    return Path(sketch) / '.j18-build/custom-bootloader'


def validate(board):
    # Reuse the same MCU, reserved pins, power-state and SERCOM route guards.
    validate_board(board)
    validate_routes(board)


def source_folder(path):
    path = Path(path).expanduser().resolve()
    for name in ('build.py', 'board.json', 'board_profile.py'):
        if not (path / name).is_file():
            raise ValueError(f'Compatible bootloader folder must contain {name}: {path}')
    validate(load_board(path / 'board.json'))
    return path


def source_name(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def read_selection(folder):
    selection = folder / 'selection.json'
    name = json.loads(selection.read_text())['source'] if selection.is_file() else 'bootloader'
    return source_folder(ROOT / name)


def checked_target(sketch):
    """Require settings, binary and generated profile to describe the same build."""
    folder = target_root(sketch)
    manifest_path = folder / 'firmware/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    board = load_board(folder / 'board.json')
    if board != manifest['board']:
        raise ValueError('Custom bootloader settings changed. Use L then C to compile them first.')
    for relative, expected in {'samd21_sam_ba.bin': manifest['sha256']}.items():
        if hashlib.sha256((folder / 'firmware' / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Configured bootloader differs from its manifest; rebuild with L then C.')
    profile = folder / 'arduino-profile'
    for name, expected in manifest['profile_sha256'].items():
        if hashlib.sha256((profile / 'hardware/custom/samd' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Configured Arduino profile differs from its manifest: ' + name)
    return folder, manifest


def profile_signature(sketch, board):
    if board == 'target':
        folder, _ = checked_target(sketch)
        return hashlib.sha256((folder / 'firmware/manifest.json').read_bytes()).hexdigest()
    if board == 'custom':
        return hashlib.sha256((ROOT / 'bootloader/firmware/manifest.json').read_bytes()).hexdigest()
    return ''


def rebuild_default(package=None):
    from samd21_menu import locked
    print('Rebuilding repository DEFAULT Custom bootloader and matching profile...', flush=True)
    print(f'Settings: {ROOT / "bootloader/board.json"}', flush=True)
    print('Uses repository source/settings; sketch-local L settings are separate. No flashing.', flush=True)
    with locked(ROOT / '.build/menu/default-bootloader'):
        command = [sys.executable, str(ROOT / 'bootloader/build.py'), '--verify-repeat']
        if package:
            command += ['--arduino-package', str(package)]
        subprocess.run(command, check=True)


def build_target(sketch, source, package=None):
    from samd21_menu import locked
    folder = target_root(sketch)
    with locked(Path(sketch) / '.j18-build'):
        validate(load_board(folder / 'board.json'))
        command = [sys.executable, str(source / 'build.py'), '--board-config', str(folder / 'board.json'),
                   '--out', str(folder / 'firmware'), '--profile-out', str(folder / 'arduino-profile'),
                   '--verify-repeat']
        if package:
            command += ['--arduino-package', str(package)]
        subprocess.run(command, check=True)
        checked_target(sketch)
    print(f'Built custom loader and matching Arduino profile in {folder}', flush=True)
    print('Select configured custom profile for the sketch too; clock mode must match. No flashing performed.')


def describe(board, folder):
    print('\nCONFIGURATION REVIEW')
    print(f'Revision: {board["revision"]}; bootloader version: {board["version"]}')
    print('Clock: ' + ('CRYSTALLESS: calibrated internal reference + DFLL48M / USB recovery'
                       if board['crystalless'] else 'EXTERNAL: 32.768 kHz crystal -> DFLL48M'))
    print('Both bootloader and Arduino sketch receive the same clock-mode define.')
    print('Internal mode avoids waiting for XOSC32K; USB recovery needs USB SOF for accurate locking.')
    print('Without USB, check UART timing and other clock-sensitive functions on hardware.')
    usb = board['usb']
    print(f'USB manufacturer: {usb["manufacturer"]}; product: {usb["product"]}')
    print(f'USB VID=0x{usb["vid"]:04X}; boot PID=0x{usb["boot_pid"]:04X}; app PID=0x{usb["app_pid"]:04X}')
    print('Native USB CDC / SAM-BA; monitor product appends " Boot". UART boot uploads are disabled.')
    print('Double-tap reset and sketch 1200-baud USB reset remain supported.')
    print('Boot indicator: ' + (f'enabled, active HIGH on {board["pins"]["LED"]}; pulses in USB monitor'
                                 if board['boot_indicator'] else 'disabled; no boot indicator GPIO drive'))
    print('\nALIAS        GPIO   BOOT STATE          PURPOSE')
    for name, pin in board['pins'].items():
        state = ('indicator / HIGH' if board['boot_indicator'] else 'not driven') if name == 'LED' else (
            'output ' + str(board['safe_outputs'][name]) if name in board['safe_outputs'] else 'input / no pulls')
        print(f'{name:<12} {pin:<6} {state:<19} {ROLES.get(name, "Board GPIO")}')
    spi0, spi1 = validate_routes(board)
    print(f'\nReviewed hardware SPI routes: SPI0={"enabled" if spi0 else "INVALID / disabled"}; SPI1={"enabled" if spi1 else "INVALID / disabled"}')
    print('SPI0 example: MOSI PB12, SCK PB13, MISO PB11; CS0 PB10 is an ordinary GPIO.')
    print('SPI1 example: MOSI PB16, SCK PB17, MISO PB22; both default routes are valid.')
    print('Fixed target: SAMD21J18A; flash 256 KB; RAM 32 KB; boot region 8 KB; app at 0x2000.')
    print('Reserved: PA00/PA01 crystal, PA24/PA25 USB, PA30/PA31 SWD. GPIO aliases cannot use them.')
    print('Initial app MSP 0x20007FF8; double-tap cell 0x20007FFC; 8-byte stack alignment.')
    print('Another MCU, memory layout, boot transport or unreviewed SERCOM route needs driver/linker review.')
    print(f'\nEditable settings: {folder / "board.json"}')
    print(f'Loader: {folder / "firmware/samd21_sam_ba.bin"}')
    print(f'Matching profile: {folder / "arduino-profile"}')
    manifest_path = folder / 'firmware/manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        print('Last build: ' + ('SETTINGS MATCH' if manifest['board'] == board else 'OUTDATED - settings changed; compile C before use'))
        print(f'Image: {manifest["size"]}/8192 bytes; repeat-verified={manifest["repeat_build_verified"]}')
        print(f'SHA256: {manifest["sha256"]}')
    else:
        print('Last build: none for this target; C builds a loader and matching profile.')


def explain():
    print('''
CONFIGURATION HELP
1 Clock: External requires a working 32.768 kHz X1 crystal and correct load/routing
  on PA00/PA01. Internal sets CRYSTALLESS in BOTH bootloader and generated profile.
  Use internal mode to investigate the reported XOSC32K startup wait. It does not
  repair a faulty crystal; clock accuracy without USB must be verified on the bench.
2 Indicator: Boot LED pulses while staying in the USB boot monitor. It is not the
  sketch heartbeat. Disable when its pin drives a different load or no LED is fitted.
3 LED GPIO: Active HIGH is fixed by this driver. Choose a free J18 GPIO with an LED
  and series resistor. Reserved/duplicate pins are rejected. Sketch LED aliases are
  generated too; stock Zero sketches keep their own mapping unless profile P changes.
4 USB strings: Printable ASCII; manufacturer up to 47 chars, product up to 42.
  Quotes, backslashes and braces are rejected. Names apply to the custom profile;
  boot product adds ' Boot'.
5 USB IDs: Decimal or 0xHEX, 1..65535. Defaults are bench identifiers. Distributed
  hardware needs IDs you are entitled to use; changing IDs can change OS recognition.
6 Revision/version: Short printable ASCII; included in the loader's version reply.
7 Aliases: Enter a physical GPIO, or a second alias to swap both atomically. Pin names
  are SAMD port names, not Arduino digital pin numbers. The generator accepts the
  documented UART/I2C and reviewed SPI routes only. Rewiring must match the sketch's
  pinout and driver routes; editing an alias cannot create a SERCOM route in silicon.
8 Boot states: 0/1 drive LOW/HIGH, Z leaves input with no internal pulls. LED is managed
  separately. Defaults leave interface GPIOs as inputs without pulls. Configure
  outputs only for reviewed wiring. Boot states apply before application handoff.
9 Review: Prints all aliases, boot states, fixed memory/transport details and build ID.
V Validate: Checks settings and reviewed routes only; does not compile or flash.
J Import: Copy a compatible board.json into this target. Source defaults remain intact.
F Source folder: Defaults to this repository's bootloader/. Another folder must use
  the compatible build.py --board-config/--out/--profile-out interface and board schema.
  Selecting a source loads its defaults into THIS target's board.json, not its source.
C Compile: Two builds must produce identical bytes; validates vectors, 8 KB capacity,
  app offset and generated profile. Publishes a loader, ELF/symbols, manifest and local
  Arduino profile. Returning after success selects the configured profile and loader.
0 Save/back: Edits are saved after each accepted change. Editing after C requires a
  new C build; the menu refuses to mix stale artifacts with changed settings.

The folder stays .j18-build/custom-bootloader for the selected sketch. This menu
never flashes hardware. Use main-menu action 1 (matching profile's combined image)
or 2 (selected loader plus sketch) when ready. Action 3 leaves existing sketch bytes
unchanged, so it cannot by itself change the sketch's oscillator configuration.
''')


def configure(sketch, package=None, source=None):
    from samd21_menu import safe_tree, locked
    sketch = Path(sketch).resolve()
    if not (sketch / (sketch.name + '.ino')).is_file():
        raise ValueError('Target folder must contain its matching .ino sketch.')
    folder = target_root(sketch)
    safe_tree(folder)
    source = source_folder(source) if source else read_selection(folder)
    board = load_board(folder / 'board.json') if (folder / 'board.json').is_file() else load_board(source / 'board.json')
    built = False

    def store():
        validate(board)
        with locked(sketch / '.j18-build'):
            save(folder / 'board.json', board)
            save(folder / 'selection.json', {'schema': 1, 'source': source_name(source)})

    store()
    while True:
        print('\nCUSTOM BOOTLOADER CONFIGURATION (builds only; no flashing)')
        print(f'Target sketch: {sketch}\nSource folder: {source}\nSettings/output: {folder}')
        print('MCU: SAMD21J18A; application offset: 0x2000; native USB SAM-BA')
        print('Clock: ' + ('INTERNAL / CRYSTALLESS (USB clock recovery)' if board['crystalless'] else 'EXTERNAL 32.768 kHz crystal on PA00/PA01'))
        print(f'Boot indicator: {board["boot_indicator"]}; LED: {board["pins"]["LED"]}')
        print(f'USB: {board["usb"]["manufacturer"]} / {board["usb"]["product"]}')
        print(f'USB IDs: VID 0x{board["usb"]["vid"]:04X}, boot PID 0x{board["usb"]["boot_pid"]:04X}, app PID 0x{board["usb"]["app_pid"]:04X}')
        print(f'Revision: {board["revision"]}; version: {board["version"]}')
        print('Use 9 to review ALL pins/states/build details; H explains every setting.')
        print('1  Choose external crystal / internal crystal-less clock')
        print('2  Enable/disable boot indicator       3  Set LED GPIO')
        print('4  USB manufacturer/product           5  USB VID / boot PID / app PID')
        print('6  Board revision/version             7  GPIO aliases (edit or swap)')
        print('8  Boot GPIO states (default: inputs / no pulls)')
        print('9  Full configuration review          H  Detailed help')
        print('V  Validate settings only             J  Import compatible board.json')
        print('F  Choose bootloader source folder (default: repository bootloader/)')
        print('C  Compile bootloader AND matching Arduino board profile, repeat-verify')
        print('0  Save settings and return')
        choice = input('Choose: ').strip().upper()
        if choice == '0':
            return built, source
        previous = copy.deepcopy(board)
        previous_source = source
        try:
            if choice == '1':
                value = input('1 External 32.768 kHz crystal; 2 Internal / crystal-less: ').strip()
                if value not in ('1', '2'):
                    raise ValueError('Choose 1 or 2.')
                board['crystalless'] = value == '2'
            elif choice == '2':
                board['boot_indicator'] = not board['boot_indicator']
            elif choice == '3':
                board['pins']['LED'] = input('LED GPIO (e.g. PA05): ').strip().upper()
            elif choice == '4':
                for key in ('manufacturer', 'product'):
                    value = input(f'USB {key} [{board["usb"][key]}]: ').strip()
                    if value:
                        board['usb'][key] = value
            elif choice == '5':
                print('Use your assigned USB IDs for distributed devices; defaults are bench IDs.')
                for key in ('vid', 'boot_pid', 'app_pid'):
                    value = input(f'{key} [0x{board["usb"][key]:04x}] (decimal or 0xHEX): ').strip()
                    if value:
                        board['usb'][key] = int(value, 0)
            elif choice == '6':
                for key in ('revision', 'version'):
                    value = input(f'{key} [{board[key]}]: ').strip()
                    if value:
                        board[key] = value
            elif choice == '7':
                print('\n'.join(f'{name}: {pin}' for name, pin in board['pins'].items()))
                name = input('Alias to change: ').strip().upper()
                if name not in board['pins']:
                    raise ValueError('Unknown alias.')
                value = input('New PA/PB GPIO, or another alias to swap: ').strip().upper()
                if value in board['pins']:
                    board['pins'][name], board['pins'][value] = board['pins'][value], board['pins'][name]
                else:
                    board['pins'][name] = value
                print('Only reviewed UART/I2C/SPI routes are accepted. Sketch config.h must match physical rewiring too.')
            elif choice == '8':
                print('Outputs: ' + str(board['safe_outputs']))
                print('High impedance: ' + ', '.join(board['high_impedance']))
                name = input('Alias (except LED): ').strip().upper()
                value = input('Boot state: 0 / 1 / Z (input): ').strip().upper()
                if name not in board['pins'] or name == 'LED' or value not in ('0', '1', 'Z'):
                    raise ValueError('Invalid GPIO state selection.')
                board['safe_outputs'].pop(name, None)
                board['high_impedance'] = [x for x in board['high_impedance'] if x != name]
                if value == 'Z':
                    board['high_impedance'].append(name)
                else:
                    board['safe_outputs'][name] = int(value)
            elif choice == 'F':
                value = input('Bootloader source folder [repository bootloader/]: ').strip().strip('"')
                source = source_folder(value or ROOT / 'bootloader')
                board = load_board(source / 'board.json')
                print('Loaded this source folder\'s defaults into the target settings.')
            elif choice == '9':
                describe(board, folder)
                input('Press Enter to return to configuration...')
                continue
            elif choice == 'H':
                explain()
                input('Press Enter to return to configuration...')
                continue
            elif choice == 'V':
                validate(board)
                print('PASS: settings, GPIO states, reserved pins and reviewed peripheral routes. No compilation or hardware access.')
                input('Press Enter to return to configuration...')
                continue
            elif choice == 'J':
                path = Path(input('Path to compatible board.json: ').strip().strip('"')).expanduser()
                board = load_board(path)
            elif choice == 'C':
                store()
                describe(board, folder)
                build_target(sketch, source, package)
                built = True
                continue
            else:
                raise ValueError('Choose a listed option.')
            store()
            built = False  # Edited settings need another successful build.
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
            board, source = previous, previous_source
            print('FAILED: ' + str(error))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sketch', type=Path)
    p.add_argument('--rebuild-default', action='store_true', help='Rebuild repository default loader/profile; never flashes.')
    p.add_argument('--source', type=Path)
    p.add_argument('--arduino-package', type=Path)
    p.add_argument('--result-file', type=Path)
    a = p.parse_args()
    if a.rebuild_default:
        rebuild_default(a.arduino_package)
        return
    if a.sketch is None:
        p.error('--sketch is required for sketch-local configuration.')
    built, source = configure(a.sketch, a.arduino_package, a.source)
    if a.result_file:
        save(a.result_file, {'built': built, 'source': source_name(source)})


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nSettings saved; configuration menu closed.')
        sys.exit(1)
