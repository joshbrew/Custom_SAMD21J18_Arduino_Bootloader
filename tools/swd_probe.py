#!/usr/bin/env python3
"""Inspect the SAMD21 over SWD and optionally walk startup; never program flash."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import threading

import samd21_menu as menu

ROOT = Path(__file__).resolve().parents[1]


def device_catalog():
    data = json.loads((ROOT / 'tools/swd_probe_devices.json').read_text())
    if data.get('schema') != 1 or data.get('family') != {'processor': 1, 'family': 0, 'series': 1}:
        raise ValueError('Unsupported probe device catalog schema/backend.')
    for key, (name, flash, ram) in data['devices'].items():
        if not 0 <= int(key, 0) <= 255 or not re.fullmatch(r'SAMD21[A-Z0-9]+', name):
            raise ValueError('Invalid SAMD21 device identity.')
        if flash not in (16384, 32768, 65536, 131072, 262144) or ram not in (2048, 4096, 8192, 16384, 32768):
            raise ValueError('Invalid SAMD21 memory geometry.')
    return data


def validate_image(data, offset, application=True):
    if not 8 <= len(data) <= 262144 - offset:
        raise ValueError('Image exceeds the SAMD21 probe address space.')
    sp, pc = struct.unpack_from('<II', data)
    if not (0x20000000 < sp <= 0x20008000 and sp % (8 if application else 4) == 0
            and pc & 1 and offset <= (pc & ~1) < offset + len(data)):
        raise ValueError(f'Invalid image vectors at {offset:#x}.')


def tcl_path(path):
    text = Path(path).resolve().as_posix()
    if any(c in text for c in '{}\r\n'):
        raise ValueError('Diagnostic paths cannot contain braces or line breaks.')
    return '{' + text + '}'


def elf_image_symbols(path, app_offset=8192, function_ranges=None):
    """Read a bounded ARM ELF32 executable, reconstructing its flash load image."""
    data = Path(path).read_bytes()
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32.')
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    if header[1] != 2 or header[2] != 40:
        raise ValueError('Expected an ARM executable ELF.')
    phoff, shoff = header[5:7]
    phsize, phcount, shsize, shcount = header[9:13]
    if phsize != 32 or shsize != 40 or phcount > 128 or shcount > 4096:
        raise ValueError('Invalid ELF table sizes.')
    def block(start, length):
        if start < 0 or length < 0 or start + length > len(data):
            raise ValueError('ELF section/segment exceeds file.')
        return data[start:start + length]
    loads = []
    for i in range(phcount):
        kind, offset, virtual, physical, length, _, _, _ = struct.unpack('<IIIIIIII', block(phoff + i * phsize, phsize))
        if kind == 1 and length:
            block(offset, length)
            loads.append((offset, virtual, physical, length))
    sections = [struct.unpack('<IIIIIIIIII', block(shoff + i * shsize, shsize)) for i in range(shcount)]
    segments = []
    for section in sections:
        _, kind, flags, virtual, offset, length, _, _, _, _ = section
        if not flags & 2 or kind == 8 or not length:
            continue
        candidates = [(physical + virtual - start) for file_offset, start, physical, size in loads
                      if start <= virtual and virtual + length <= start + size
                      and file_offset + virtual - start == offset]
        if len(candidates) != 1:
            raise ValueError('Allocated ELF section does not map to exactly one load segment.')
        physical = candidates[0]
        if app_offset <= physical < 262144:
            if physical + length > 262144:
                raise ValueError('ELF flash section exceeds SAMD21J18 flash.')
            segments.append((physical, block(offset, length)))
    if not segments or min(x[0] for x in segments) != app_offset:
        raise ValueError(f'ELF application is not linked at {app_offset:#x}.')
    image = bytearray(max(base + len(payload) for base, payload in segments) - app_offset)
    used = bytearray(len(image))
    for base, payload in segments:
        start = base - app_offset
        if any(used[start:start + len(payload)]):
            raise ValueError('Overlapping ELF flash load segments.')
        image[start:start + len(payload)] = payload
        used[start:start + len(payload)] = b'\x01' * len(payload)
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        offset, length, link, stride = section[4], section[5], section[6], section[9]
        if link >= len(sections) or stride != 16 or length % 16:
            raise ValueError('Invalid ELF symbol table.')
        strings = block(sections[link][4], sections[link][5])
        for cursor in range(offset, offset + length, stride):
            name, value, size, info, _, index = struct.unpack('<IIIBBH', block(cursor, stride))
            if not index or info & 15 != 2 or name >= len(strings):
                continue
            end = strings.find(b'\0', name)
            if end < 0:
                raise ValueError('Unterminated ELF symbol name.')
            label = strings[name:end].decode('utf-8', errors='replace')
            address = value & ~1
            if app_offset <= address < app_offset + len(image):
                symbols[label] = address
                if function_ranges is not None and size:
                    function_ranges[label] = (address, size)
    validate_image(image, app_offset)
    return bytes(image), symbols


def find_matching_elf(build, app, app_offset=8192, function_ranges=None):
    if app is None:
        return None, {}, 'No saved application: named startup checkpoints are unavailable.'
    published = build / 'firmware/firmware.sketch.elf'
    candidates = ([published] if published.is_file() else []) + sorted((build / 'compiler-output').glob('*.elf'))
    for path in candidates:
        try:
            ranges = {}
            image, symbols = elf_image_symbols(path, app_offset, ranges)
        except (ValueError, OSError, struct.error):
            continue
        if image == app:
            if function_ranges is not None:
                function_ranges.update(ranges)
            return path, symbols, 'ELF flash load image exactly matches the saved application.'
    return None, {}, 'No matching ELF: app-entry check remains available; setup/loop will be skipped.'


def configuration(adapter, diagnostic, boot, app, symbols, inspect, leave_halted, timeout,
                  app_offset=8192, board_map='generic', expected_boot_bytes=None, validate_layout=True, board_config=None,
                  connect_under_reset=False, scan_bytes=0):
    if connect_under_reset and adapter != 'stlink':
        raise ValueError('Connect-under-reset requires ST-LINK and its NRST wire; Pi GPIO reset is not configured.')
    board = board_config or json.loads((ROOT / 'bootloader/board.json').read_text())
    pins = []
    for alias, pin in board['pins'].items():
        match = re.fullmatch(r'P([AB])(\d{2})', pin)
        if not re.fullmatch(r'[A-Z0-9_]+', alias) or not match or int(match[2]) > 31:
            raise ValueError('Invalid board pin alias/route: ' + alias)
        pins += [alias, str(ord(match[1]) - ord('A')), str(int(match[2]))]
    devices = device_catalog()['devices']
    table = ' '.join(f'{int(key, 0)} {{{name} {flash} {ram}}}' for key, (name, flash, ram) in devices.items())
    lines = [menu.adapter_configuration(adapter), 'set CHIPNAME samd21_probe',
             'source [find target/at91samdXX.cfg]', 'adapter speed 100',
             ('reset_config srst_only srst_nogate srst_open_drain connect_assert_srst' if connect_under_reset else 'reset_config none'),
             'cortex_m reset_config sysresetreq', '$_TARGETNAME configure -work-area-size 0 -work-area-backup 1']
    if connect_under_reset:
        lines += ['adapter srst delay 100', 'adapter srst pulse_width 100']
    for service in ('gdb', 'tcl', 'telnet'):
        lines.append(f'if {{[catch {{{service} port disabled}}]}} {{ {service}_port disabled }}')
    lines += ['set SWD_DEVICES {' + table + '}',
              f'set SWD_APP_OFFSET {app_offset}', f'set J18_CONNECT_UNDER_RESET {int(connect_under_reset)}',
              f'set SWD_SCAN_BYTES {scan_bytes}', f'set SWD_SCAN_DUMP {tcl_path(diagnostic / "installed-flash.bin")}',
              f'set SWD_EXPECT_BOOT_BYTES {expected_boot_bytes if expected_boot_bytes is not None else -1}',
              f'set SWD_BOARD_MAP {board_map}',
              f'set SWD_VALIDATE_LAYOUT {int(validate_layout)}',
              'set J18_PINS {' + ' '.join(pins if board_map == 'custom' else []) + '}',
              f'set J18_BOOT_IMAGE {tcl_path(boot) if boot else "{}"}',
              f'set J18_APP_IMAGE {tcl_path(app) if app else "{}"}',
              f'set J18_BOOT_DUMP {tcl_path(diagnostic / "boot-vectors.bin")}',
              f'set J18_APP_DUMP {tcl_path(diagnostic / "app-vectors.bin")}',
              f'set J18_SETUP {symbols.get("setup", 0)}', f'set J18_LOOP {symbols.get("loop", 0)}',
              f'set J18_SYMBOLS_MATCH {int(bool(symbols))}', f'set J18_INSPECT_ONLY {int(inspect)}',
              f'set J18_LEAVE_HALTED {int(leave_halted)}', f'set J18_TIMEOUT_MS {timeout}',
              'if {[info exists J18_CHECK_ONLY]} {',
              '    echo "SWD probe configuration checked; no probe opened."', '    shutdown', '    return', '}',
              f'source {tcl_path(ROOT / "tools/swd_probe_body.cfg")}']
    return '\n'.join(lines) + '\n'


def log_message(line):
    """Return level/payload for normal and -d3 logs; keep Debug traces distinct."""
    match = re.match(r'^(Debug|Info|Warn|Error|User)\s*:\s*(.*)', line.rstrip())
    if not match:
        return '', line.rstrip()
    level, payload = match.groups()
    payload = re.sub(r'^\d+\s+\d+\s+\S+:\d+\s+\S+\(\):\s*', '', payload)
    return level, payload


def parse_output(text):
    results, values = [], {}
    for line in text.splitlines():
        level, line = log_message(line)
        if level not in ('', 'User'):
            continue
        if line.startswith('J18_RESULT|'):
            _, name, status, description = line.split('|', 3)
            results.append({'check': name, 'status': status, 'description': description})
        elif line.startswith('J18_VALUE|'):
            _, name, value = line.split('|', 2)
            values[name] = value
    return results, values


def connection_details(report, output):
    messages = [log_message(line) for line in output.splitlines()]
    errors = [payload for level, payload in messages if level == 'Error' and payload]
    report['openocd_errors'] = errors
    details = []
    for level, payload in messages:
        if (re.search(r'\bSTLINK_(?:SWD|JTAG|DEBUG|USB|AP|DP)_[A-Z_]+\b', payload)
                or 'STLINK status code' in payload
                or re.search(r'(?:libusb|usb).*?(?:error|failed|timed out|timeout)', payload, re.I)):
            if payload not in details:
                details.append(payload)
    report['adapter_diagnostics'] = details
    identity = re.search(r'STLINK V[^\r\n]+', output)
    if identity:
        report['adapter_identity'] = identity[0]
    voltage = re.search(r'Target voltage:\s*([0-9]+(?:\.[0-9]+)?)', output)
    if voltage:
        report['adapter_target_voltage_v'] = float(voltage[1])
    for result in report['results']:
        if result['check'] == 'connection' and result['status'] == 'FAIL' and errors:
            result['description'] = errors[0]


def explain_report(report, functions):
    values = report['values']
    app_matches = any(x['check'] == 'app.flash' and x['status'] == 'PASS' for x in report['results'])
    notes = []
    if any(x['check'] == 'connection' and x['status'] == 'FAIL' for x in report['results']):
        errors = ' '.join(report.get('openocd_errors', [])).lower()
        if 'unable to connect to the target' in errors or 'init mode failed' in errors:
            voltage = report.get('adapter_target_voltage_v')
            seen = f'ST-LINK reported target voltage {voltage:.3f} V, but ' if voltage is not None else ''
            notes.append(seen + 'OpenOCD could not connect to the target debug port. CPU, installed flash and oscillator state were not inspected.')
            if report.get('connect_under_reset'):
                notes.append('Wired reset recovery also failed. Confirm ST-LINK NRST/SRST reaches SAMD RESET, the adapter supports that output, SWDIO/clock/ground are connected, and target power stays stable. Check whether RESET is physically released after the attempt.')
            else:
                notes.append('Close other probe sessions; power-cycle the target and reconnect ST-LINK, then try D -> 3. Check SWDIO PA31, SWCLK PA30, common ground and stable target power. For D -> 4 recovery, wire ST-LINK NRST/SRST to SAMD RESET first. Normal probes use no reset wire.')
        else:
            notes.append('Connection failed before CPU/flash inspection. Review the OpenOCD adapter error; check adapter USB access, target power and SWD wiring.')
        if report.get('adapter_diagnostics'):
            notes.append('Low-level adapter status: ' + '; '.join(report['adapter_diagnostics']) + '. Full debug log saved; these statuses do not uniquely identify the hardware cause.')
    if app_matches:
        for key, value in list(values.items()):
            if key.endswith('.pc'):
                pc = int(value, 0) & ~1
                for name, (start, length) in functions.items():
                    if start <= pc < start + length:
                        values[key + '_function'] = f'{name}+0x{pc-start:x}'
                        break
        for result in report['results']:
            if result['status'] == 'FAIL':
                label = result['check']
                if values.get(label + '.pc_function', '').startswith('SystemInit+') and values.get(label + '.XOSC32K.enabled') == '1' and values.get(label + '.XOSC32K.ready') == '0':
                    notes.append('Startup is stopped in SystemInit with the external 32.768 kHz oscillator enabled but not ready. Check crystal presence/routing/load capacitors. A crystal-less board requires a matching CRYSTALLESS profile; the probe does not change clock configuration.')
                elif values.get(label + '.pc_function'):
                    notes.append(f'{label}: stopped at {values[label + ".pc"]} in {values[label + ".pc_function"]}. Inspect the captured clock/CPU state for the reason.')
    for key, active in values.items():
        if key.endswith('.active_exception') and int(active, 0) == 3:
            label = key[:-len('.active_exception')]
            pc = values.get(label + '.pc', 'unknown')
            message = f'{label}: HardFault observed, PC={pc}.'
            stacked = values.get(label + '.frame.pc')
            if stacked:
                message += f' Stacked fault PC={stacked}'
                if values.get(label + '.frame.pc_function'):
                    message += ' in ' + values[label + '.frame.pc_function']
                message += '.'
            if pc != 'unknown' and (int(pc, 0) & ~1) == 0xfffffffe:
                message += ' PC is an invalid execution address; inspect the installed exception vectors and exception frame before attributing this to sketch logic.'
            handler = values.get(label + '.hardfault_handler')
            if handler:
                message += f' Captured HardFault vector={handler}.'
            if report.get('connect_under_reset'):
                message += ' This was captured during wired reset recovery; it does not by itself establish a fault during ordinary power-on.'
            if not app_matches:
                message += ' Use D -> 5 with NRST connected to verify installed images and walk startup against the saved ELF.'
            notes.append(message)
    mismatch = any(x['check'] == 'app.flash' and x['status'] == 'FAIL' for x in report['results'])
    if mismatch:
        installed, expected = values.get('app.sp'), values.get('expected.app.sp')
        if installed and expected and {int(installed, 0), int(expected, 0)} == {0x20007ff8, 0x20008000}:
            notes.append(f'Installed app stack={installed}, saved image stack={expected}: consistent with Custom versus Zero linker layouts. Select the intended sketch/profile and flash it, or use generic inspection without local comparisons. This mismatch alone does not identify flash corruption.')
        else:
            notes.append('Installed app does not match the selected local image, or could not be read. Named ELF checkpoints were skipped; select the matching sketch/image, or use generic inspection.')
    report['diagnosis'] = notes
    return notes


def find_openocd(explicit, platform):
    if explicit:
        tool = Path(explicit).expanduser()
    elif platform == 'windows':
        found = os.environ.get('J18_OPENOCD') or shutil.which('openocd')
        tool = Path(found) if found else Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'OpenOCD/bin/openocd.exe'
    else:
        tool = menu.Engine(platform).tool('openocd')
    if not tool.is_file():
        raise FileNotFoundError('OpenOCD missing; run a menu build/check first or set J18_OPENOCD.')
    return tool.resolve()


def run_probe(sketch, openocd, adapter='stlink', platform='windows', check_only=False,
              inspect=False, leave_halted=False, timeout=12000, sudo=False,
              compare_images=True, app_offset=8192, board_map='auto', expected_boot_bytes=None, connect_under_reset=False,
              identify_images=False):
    if connect_under_reset and adapter != 'stlink':
        raise ValueError('Recovery requires ST-LINK NRST -> SAMD RESET. Choose ST-LINK, not Pi GPIO.')
    if not 1000 <= timeout <= 60000:
        raise ValueError('Startup checkpoint timeout must be 1000–60000 milliseconds.')
    if not 0 <= app_offset <= 262144-256 or app_offset % 256:
        raise ValueError('Application offset must be 256-byte aligned and inside SAMD21 flash.')
    if board_map not in ('auto', 'generic', 'custom'):
        raise ValueError('Unknown probe pin map.')
    if expected_boot_bytes is not None and expected_boot_bytes not in (0, 512, 1024, 2048, 4096, 8192, 16384, 32768):
        raise ValueError('Invalid expected boot protection size.')
    build = sketch / '.j18-build'
    with menu.locked(build):
        diagnostic = build / 'swd'
        diagnostic.mkdir(exist_ok=True)
        report = {'mode': 'inspect' if inspect else 'startup', 'hardware_access_attempted': False,
                  'flash_programmed': False, 'sketch': str(sketch), 'results': [], 'values': {},
                  'compare_local_images': compare_images, 'app_offset': app_offset, 'connect_under_reset': connect_under_reset,
                  'identify_local_images': identify_images}
        log_path, report_path = diagnostic / 'swd-probe.log', diagnostic / 'swd-report.json'
        try:
            catalog = None
            if identify_images:
                from bootloader_config import package_root
                from swd_images import discover
                catalog = discover(core_package=package_root())
                menu.record(diagnostic / 'image-catalog.json', catalog)
                (diagnostic / 'installed-flash.bin').unlink(missing_ok=True)
                report['image_identification'] = {'status': 'PENDING', 'candidate_count': len(catalog['candidates'])}
                print(f'Image identification: {len(catalog["candidates"])} distinct local binary ranges; up to {catalog["read_bytes"]} flash bytes to read.', flush=True)
            def saved(name, offset, application=True):
                source = build / 'firmware' / name
                if not compare_images or not source.is_file():
                    return None, None
                data = source.read_bytes()
                validate_image(data, offset, application)
                if not application and len(data) > app_offset:
                    raise ValueError('Saved bootloader overlaps the configured application offset.')
                frozen = diagnostic / ('expected-' + name)
                menu.atomic(frozen, data)
                report[name + '_sha256'] = menu.digest(frozen)
                return frozen, data
            boot, _ = saved('samd21_sam_ba.bin', 0, False) if app_offset else (None, None)
            app, app_data = saved('firmware.sketch.bin', app_offset)
            functions = {}
            elf, symbols, reason = find_matching_elf(build, app_data, app_offset, functions)
            if not compare_images:
                reason = 'Selected-sketch comparison disabled for this routine; named startup checkpoints are not used.'
            print(reason, flush=True)
            report['elf'] = str(elf) if elf else None
            report['symbols_note'] = reason
            receipt = build / 'sketch-build.json'
            report['saved_board_profile'] = json.loads(receipt.read_text(encoding='utf-8-sig')).get('fqbn') if compare_images and receipt.is_file() else None
            selected_map = ('custom' if report['saved_board_profile'] == 'custom:samd:custom_j18' and compare_images else 'generic') if board_map == 'auto' else board_map
            report['requested_board_map'] = selected_map
            saved_config = json.loads(receipt.read_text(encoding='utf-8-sig')).get('board_config') if compare_images and receipt.is_file() else None
            if saved_config is not None:
                from bootloader_config import validate
                validate(saved_config)
            report['pin_map_source'] = 'saved sketch configuration' if saved_config else 'repository defaults'
            config = diagnostic / 'swd_probe.cfg'
            menu.atomic(config, configuration(adapter, diagnostic, boot, app, symbols, inspect, leave_halted, timeout,
                                              app_offset, selected_map, expected_boot_bytes, compare_images or not inspect, saved_config, connect_under_reset,
                                              catalog['read_bytes'] if catalog else 0).encode())
            command = [str(openocd), '-d3']
            for scripts in (openocd.parent.parent / 'share/openocd/scripts', openocd.parent.parent / 'scripts'):
                if (scripts / 'target/at91samdXX.cfg').is_file():
                    command += ['-s', str(scripts)]
                    break
            dry = subprocess.run([*command, '-c', 'set J18_CHECK_ONLY 1', '-f', str(config)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            log_path.write_text(dry.stdout, encoding='utf-8')
            if dry.returncode:
                print(dry.stdout)
                raise RuntimeError('SWD probe configuration check failed; hardware was not accessed.')
            if check_only:
                report['results'].append({'check': 'configuration', 'status': 'PASS', 'description': 'No hardware access.'})
                report['status'] = 'CONFIGURATION_ONLY'
                print(f'Probe files prepared; no hardware access.\nReport: {report_path}', flush=True)
                return 0
            native = {'windows': 'win32', 'linux': 'linux', 'mac': 'darwin'}[platform]
            if sys.platform != native:
                raise ValueError('Hardware probing requires the selected native platform.')
            prefix = []
            if platform == 'linux' and (sudo or adapter == 'gpio'):
                prefix = menu.platform_tools('linux').privileged()
            report['connect_under_reset'] = connect_under_reset
            if connect_under_reset:
                print('SWD RECOVERY: requires ST-LINK NRST/SRST -> SAMD RESET. Asserts hardware reset during connection, then resets/halts for inspection.', flush=True)
            else:
                print('SWD probe: halts the SAMD21' + (' briefly; no startup reset.' if inspect else ' and performs one software reset/startup walk.'), flush=True)
            print('Flash will not be erased/programmed. Peripheral communications may time out while paused.', flush=True)
            report['hardware_access_attempted'] = True
            # Bound a wedged OpenOCD; normal per-check waits are configured separately.
            maximum = 120 + 4 * timeout / 1000 + (240 if identify_images else 0)
            if prefix:
                limiter = shutil.which('timeout')
                if not limiter:
                    raise RuntimeError('Elevated Linux probing needs the coreutils timeout command to bound the root process.')
                prefix += [limiter, '--signal=TERM', '--kill-after=5s', f'{maximum}s']
            process = subprocess.Popen([*prefix, *command, '-f', str(config)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            expired = threading.Event()
            def stop_stalled():
                if process.poll() is None:
                    expired.set()
                    process.kill()
            watchdog = threading.Timer(maximum + (10 if prefix else 0), stop_stalled)
            watchdog.daemon = True
            watchdog.start()
            lines = []
            try:
                for line in process.stdout:
                    lines.append(line)
                    level, payload = log_message(line)
                    if level in ('', 'User') and (re.match(r'^[123]\. ', payload) or payload.startswith('Image scan:')):
                        print(payload, flush=True)
                    elif level == 'Error':
                        print('OpenOCD: ' + payload, flush=True)
                    elif level in ('', 'User') and payload.startswith('J18_RESULT|'):
                        _, name, status, description = payload.split('|', 3)
                        print(f'{status:4} {name}: {description}', flush=True)
                process.wait()
            finally:
                watchdog.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
            output = ''.join(lines)
            log_path.write_text(output, encoding='utf-8')
            report['results'], report['values'] = parse_output(output)
            connection_details(report, output)
            if catalog is not None:
                passed = any(x['check'] == 'image.scan' and x['status'] == 'PASS' for x in report['results'])
                dump = diagnostic / 'installed-flash.bin'
                if passed and dump.is_file():
                    from swd_images import identify
                    data = dump.read_bytes()
                    if len(data) != int(report['values'].get('image.scan_bytes', -1)):
                        raise ValueError('Installed-flash dump size does not match this scan; identification refused.')
                    report['image_identification'] = identify(catalog, data, int(report['values']['chip.ram_bytes']))
                    for kind, matches in report['image_identification']['matches'].items():
                        if matches:
                            for match in matches:
                                print(f'CONFIRMED {kind}: {match["labels"][0]} ({match["bytes"]} bytes at {match["offset"]:#x}; SHA256 {match["sha256"]})', flush=True)
                                if len(match['labels']) > 1:
                                    print(f'  {len(match["labels"])} local copies/aliases share these bytes; all paths are in the report.', flush=True)
                        else:
                            print(f'UNKNOWN {kind}: no complete local binary range matches the installed flash.', flush=True)
                else:
                    report['image_identification']['status'] = 'NOT_READ'
            for note in explain_report(report, functions):
                print('Diagnosis: ' + note, flush=True)
            if expired.is_set() or (prefix and process.returncode in (124, 137)):
                raise RuntimeError('OpenOCD exceeded the probe time limit. CPU state is uncertain; reconnect or power-cycle the board.')
            connected = any(x['check'] == 'connection' and x['status'] == 'PASS' for x in report['results'])
            report['status'] = 'FAIL' if process.returncode or not connected or any(x['status'] == 'FAIL' for x in report['results']) else 'PASS'
            if report['status'] == 'FAIL':
                print('Review the failed check and diagnosis above. CPU snapshots and details are in the log.', flush=True)
            print(f'Report: {report_path}\nLog: {log_path}', flush=True)
            return 0 if report['status'] == 'PASS' else 1
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            report['status'] = 'ERROR'
            report['error'] = str(error) if not isinstance(error, KeyboardInterrupt) else 'Probe interrupted; CPU state may be halted. Reconnect or power-cycle.'
            print(f'FAILED: {report["error"]}\nReport: {report_path}', file=sys.stderr)
            return 1
        finally:
            menu.record(report_path, report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sketch', default='Blink', help='Menu sketch name or explicit sketch folder.')
    p.add_argument('--platform', choices=('windows', 'linux', 'mac'), default='windows' if os.name == 'nt' else ('mac' if sys.platform == 'darwin' else 'linux'))
    p.add_argument('--openocd')
    p.add_argument('--adapter', choices=('stlink', 'gpio', 'auto'), default='auto')
    p.add_argument('--check-only', action='store_true', help='Prepare/check files without opening a probe.')
    p.add_argument('--inspect-only', action='store_true', help='Halt/read/resume only; skip startup reset.')
    p.add_argument('--leave-halted', action='store_true')
    p.add_argument('--connect-under-reset', action='store_true', help='ST-LINK NRST -> SAMD RESET wire REQUIRED; resets during connection.')
    p.add_argument('--identify-images', action='store_true', help='Read installed flash and identify exact matches among local compiled images.')
    p.add_argument('--timeout-ms', type=int, default=12000)
    p.add_argument('--sudo', action='store_true', help='Linux: elevate only OpenOCD.')
    p.add_argument('--no-compare', action='store_true', help='Ignore local binaries/ELF; inspect the installed firmware only.')
    p.add_argument('--app-offset', type=lambda value: int(value, 0), default=8192)
    p.add_argument('--board-map', choices=('auto', 'generic', 'custom'), default='auto')
    p.add_argument('--expected-boot-bytes', type=lambda value: int(value, 0))
    a = p.parse_args()
    sketch = next((x for x in menu.available() if x.name == a.sketch), Path(a.sketch).expanduser()).resolve()
    if not sketch.is_dir() or not (sketch / (sketch.name + '.ino')).is_file():
        p.error('Select a folder containing its matching main .ino file.')
    openocd = find_openocd(a.openocd, a.platform)
    if a.platform == 'linux':
        adapter = menu.platform_tools('linux').select_adapter(a.adapter, openocd)
    else:
        if a.adapter == 'gpio':
            p.error('GPIO requires Raspberry Pi 4; use ST-LINK on Windows/macOS.')
        adapter = 'stlink'
    return run_probe(sketch, openocd, adapter, a.platform, a.check_only,
                     a.inspect_only, a.leave_halted, a.timeout_ms, a.sudo,
                     not a.no_compare, a.app_offset, a.board_map, a.expected_boot_bytes, a.connect_under_reset, a.identify_images)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
