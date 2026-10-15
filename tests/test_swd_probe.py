"""No-hardware ELF checks and simulated CPU execution of the real probe Tcl."""
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import swd_probe as probe


def fake_elf():
    data = bytearray(0x2180 + 5 * 40)
    ident = b'\x7fELF\x01\x01\x01' + b'\0' * 9
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, ident, 2, 40, 1, 0x2009, 52, 0x2180, 0, 52, 32, 2, 40, 5, 0)
    struct.pack_into('<IIIIIIII', data, 52, 1, 0, 0, 0, 0x2010, 0x2010, 5, 0x10000)
    struct.pack_into('<IIIIIIII', data, 84, 1, 0x2100, 0x20000000, 0x2010, 4, 16, 6, 0x10000)
    text = struct.pack('<II', 0x20007ff8, 0x2009) + b'12345678'
    data[0x2000:0x2010] = text
    data[0x2100:0x2104] = b'ABCD'
    struct.pack_into('<IIIBBH', data, 0x2120, 1, 0x2009, 4, 0x12, 0, 1)
    struct.pack_into('<IIIBBH', data, 0x2130, 7, 0x200d, 4, 0x12, 0, 1)
    data[0x2140:0x214c] = b'\0setup\0loop\0'
    sections = [(0,) * 10, (0, 1, 6, 0x2000, 0x2000, 16, 0, 0, 4, 0),
                (0, 1, 3, 0x20000000, 0x2100, 4, 0, 0, 4, 0),
                (0, 2, 0, 0, 0x2110, 48, 4, 1, 4, 16),
                (0, 3, 0, 0, 0x2140, 12, 0, 0, 1, 0)]
    for index, section in enumerate(sections):
        struct.pack_into('<IIIIIIIIII', data, 0x2180 + index * 40, *section)
    return bytes(data), text + b'ABCD'


class ProbeTests(unittest.TestCase):
    def test_elf_reconstructs_sections_without_elf_header_or_bss(self):
        data, expected = fake_elf()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.elf'
            path.write_bytes(data)
            image, symbols = probe.elf_image_symbols(path)
            self.assertEqual(image, expected)
            self.assertEqual(symbols, {'setup': 0x2008, 'loop': 0x200c})
            build = Path(folder)
            (build / 'compiler-output').mkdir()
            target = build / 'compiler-output/test.elf'
            target.write_bytes(data)
            self.assertEqual(probe.find_matching_elf(build, expected)[0], target)
            self.assertIsNone(probe.find_matching_elf(build, expected + b'bad')[0])
            (build / 'firmware').mkdir()
            retained = build / 'firmware/firmware.sketch.elf'
            retained.write_bytes(data)
            self.assertEqual(probe.find_matching_elf(build, expected)[0], retained)
    def test_bad_elf_and_paths_are_rejected(self):
        data, _ = fake_elf()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.elf'
            for bad in (b'bad', data[:60], data[:18] + b'\0\0' + data[20:]):
                path.write_bytes(bad)
                with self.assertRaises(ValueError):
                    probe.elf_image_symbols(path)
        with self.assertRaises(ValueError):
            probe.tcl_path(Path('bad{path'))
    def test_configuration_checks_before_runtime_and_never_flashes(self):
        folder = ROOT / '.build/test-probe'
        config = probe.configuration('stlink', folder, None, None, {}, False, False, 12000, board_map='custom')
        self.assertLess(config.index('J18_CHECK_ONLY'), config.index('swd_probe_body.cfg'))
        self.assertIn('reset_config none', config)
        self.assertIn('reset_config sysresetreq', config)
        self.assertIn('SDA 0 8 SCL 0 9', config)
        self.assertIn('set CHIPNAME samd21_probe', config)
        generic = probe.configuration('stlink', folder, None, None, {}, True, False, 12000)
        self.assertIn('set J18_PINS {}', generic)
        body = (ROOT / 'tools/swd_probe_body.cfg').read_text()
        self.assertNotIn('flash write', body)
        self.assertNotIn('at91samd bootloader', body)
        self.assertNotIn('mww ', body)
    def test_recovery_requires_wired_stlink_and_explicit_selection(self):
        folder = ROOT / '.build/test-probe'
        config = probe.configuration('stlink', folder, None, None, {}, True, False, 12000, connect_under_reset=True)
        self.assertIn('srst_only srst_nogate srst_open_drain connect_assert_srst', config)
        self.assertIn('set J18_CONNECT_UNDER_RESET 1', config)
        self.assertIn('adapter srst pulse_width 100', config)
        with self.assertRaises(ValueError):
            probe.configuration('gpio', folder, None, None, {}, True, False, 12000, connect_under_reset=True)
    def test_report_parser_preserves_skips_and_fault_values(self):
        results, values = probe.parse_output('noise\nJ18_RESULT|setup|SKIP|No ELF\nJ18_VALUE|attached.active_exception|3\n')
        self.assertEqual(results[0]['status'], 'SKIP')
        self.assertEqual(values['attached.active_exception'], '3')
    def test_failed_connection_preserves_adapter_error_and_voltage(self):
        output = 'Info : Target voltage: 3.268221\nError: init mode failed (unable to connect to the target)\nJ18_RESULT|connection|FAIL|\n'
        results, values = probe.parse_output(output)
        report = {'results': results, 'values': values}
        probe.connection_details(report, output)
        self.assertIn('unable to connect', report['results'][0]['description'])
        self.assertAlmostEqual(report['adapter_target_voltage_v'], 3.268221)
        notes = probe.explain_report(report, {})
        self.assertIn('3.268 V', notes[0])
        self.assertIn('were not inspected', notes[0])
        self.assertIn('D -> 3', notes[1])
    def test_verbose_logs_ignore_debug_echo_and_preserve_real_results(self):
        output = ('Debug: 1 2 command.c:82 script_debug(): command - echo J18_RESULT|connection|FAIL|fake\n'
                  'Debug: 2 3 stlink_usb.c:1 stlink_usb_error_check(): STLINK_SWD_DP_ERROR\n'
                  'Info : 3 4 stlink_usb.c:2 stlink_usb_open(): STLINK V2J29S7 (API v2) VID:PID 0483:3748\n'
                  'Info : 4 5 stlink_usb.c:3 stlink_usb_open(): Target voltage: 3.275\n'
                  'Error: 5 6 stlink_usb.c:4 stlink_usb_init_mode(): init mode failed (unable to connect to the target)\n'
                  'User : 6 7 command.c:548 command_run_line(): J18_RESULT|connection|FAIL|\n'
                  'User : 7 8 command.c:548 command_run_line(): J18_VALUE|attached.pc|0x2000\n')
        results, values = probe.parse_output(output)
        self.assertEqual(len(results), 1)
        self.assertEqual(values['attached.pc'], '0x2000')
        report = {'results': results, 'values': values}
        probe.connection_details(report, output)
        self.assertEqual(report['adapter_diagnostics'], ['STLINK_SWD_DP_ERROR'])
        self.assertIn('V2J29S7', report['adapter_identity'])
        self.assertEqual(report['openocd_errors'], ['init mode failed (unable to connect to the target)'])
        self.assertIn('STLINK_SWD_DP_ERROR', ' '.join(probe.explain_report(report, {})))
    def test_fault_diagnosis_uses_verified_stacked_pc(self):
        report = {'results': [{'check': 'app.flash', 'status': 'PASS'}], 'values': {
            'attached.active_exception': '3', 'attached.pc': '0xfffffffe',
            'attached.frame.pc': '0x40f8', 'attached.hardfault_handler': '0x3e65'}, 'connect_under_reset': True}
        notes = probe.explain_report(report, {'SystemInit': (0x3ff4, 0x190)})
        self.assertIn('SystemInit+', ' '.join(notes))
        self.assertIn('ordinary power-on', ' '.join(notes))
        report['results'] = []
        report['values'].pop('attached.frame.pc_function')
        notes = probe.explain_report(report, {'SystemInit': (0x3ff4, 0x190)})
        self.assertNotIn('SystemInit+', ' '.join(notes))
        self.assertIn('D -> 5', ' '.join(notes))
    def test_diagnoses_clock_wait_only_with_matching_flash(self):
        report={'results':[{'check':'app.flash','status':'PASS'},{'check':'setup','status':'FAIL'}],
                'values':{'setup.pc':'0x401a','setup.XOSC32K.enabled':'1','setup.XOSC32K.ready':'0'}}
        notes=probe.explain_report(report,{'SystemInit':(0x3ff4,0x120)})
        self.assertIn('external 32.768 kHz oscillator',notes[0])
        report['results'][0]['status']='FAIL'
        self.assertFalse(any('Startup is stopped' in n for n in probe.explain_report(report,{'SystemInit':(0x3ff4,0x120)})))
    def test_diagnoses_zero_custom_vector_mismatch(self):
        report={'results':[{'check':'app.flash','status':'FAIL'}],
                'values':{'app.sp':'0x20007ff8','expected.app.sp':'0x20008000'}}
        self.assertIn('Custom versus Zero',probe.explain_report(report,{})[0])


MOCK = r'''
# SIMULATED TARGET. No adapter/target is defined; all target commands are stubs.
foreach name {init halt targets reg get_reg read_memory flash dump_image verify_image reset bp rbp resume wait_halt} {
    catch {rename $name native_$name}
}
set J18_INSPECT_ONLY 0
set J18_LEAVE_HALTED 0
set J18_TIMEOUT_MS 1000
set J18_BOOT_IMAGE expected-boot.bin
set J18_APP_IMAGE expected-app.bin
set J18_BOOT_DUMP boot.bin
set J18_APP_DUMP app.bin
set J18_SYMBOLS_MATCH 1
set J18_SETUP 0x2200
set J18_LOOP 0x2300
set J18_PINS {UART0_TX 0 22 SDA 0 8 ADC_INPUT 1 7}
set SWD_BOARD_MAP custom
set SWD_APP_OFFSET 8192
set SWD_EXPECT_BOOT_BYTES -1
set SWD_VALIDATE_LAYOUT 1
set mock_pc 0x100
set mock_bp 0
if {$SCENARIO eq "inspect"} { set J18_INSPECT_ONLY 1 }
if {$SCENARIO eq "full_recovery"} { set J18_CONNECT_UNDER_RESET 1 }
if {[string match recovery* $SCENARIO]} {
    set J18_CONNECT_UNDER_RESET 1
    set J18_INSPECT_ONLY 1
    set SWD_VALIDATE_LAYOUT 0
}
if {$SCENARIO eq "leave_halted"} { set J18_LEAVE_HALTED 1 }
if {$SCENARIO eq "no_elf"} { set J18_SYMBOLS_MATCH 0 }
if {$SCENARIO eq "generic_inspect"} {
    set J18_INSPECT_ONLY 1
    set SWD_VALIDATE_LAYOUT 0
}
if {$SCENARIO eq "raw_app"} { set SWD_APP_OFFSET 0 }
if {$SCENARIO eq "small_e14" || $SCENARIO eq "generic_no_images" || $SCENARIO eq "generic_inspect" || $SCENARIO eq "raw_app" || [string match recovery* $SCENARIO]} {
    set J18_BOOT_IMAGE ""
    set J18_APP_IMAGE ""
    set J18_SYMBOLS_MATCH 0
    set SWD_BOARD_MAP generic
    set J18_PINS {}
}
proc init {} {
    if {$::SCENARIO eq "connection_failure"} { error "simulated disconnected probe" }
    if {$::SCENARIO eq "recovery_connection_failure"} { error "" }
}
proc adapter {args} { echo "MOCK_RESET_RELEASED" }
proc halt {args} {}
proc targets {} {}
proc reg {args} {}
proc flash {args} {}
proc dump_image {args} {}
proc get_reg {args} {
    set msp 0x20007ff8
    set lr 0xfffffff9
    if {$::SCENARIO eq "fault"} { set msp 0x20007f00 }
    if {$::SCENARIO eq "fault_psp"} { set lr 0xfffffffd }
    return [dict create pc $::mock_pc lr $lr msp $msp psp 0x20000100 xpsr 0x01000000 control 0]
}
proc read_memory {address width count} {
    if {$address == 0xE000ED00} { return {0x410cc601} }
    if {$address == 0x41002018} {
        if {$::SCENARIO eq "wrong_chip"} { return {0x10000005} }
        if {$::SCENARIO eq "unknown_id"} { return {0x100100ff} }
        if {$::SCENARIO eq "feather_g18"} { return {0x10010305} }
        if {$::SCENARIO eq "small_e14" || $::SCENARIO eq "small_bad_stack"} { return {0x1001000e} }
        return {0x10010000}
    }
    if {$address == 0xE000ED04 && ($::SCENARIO eq "fault" || $::SCENARIO eq "fault_psp")} { return {3} }
    if {($address == 0x20007f00 || $address == 0x20000100) && $count >= 8} {
        return {0x83 0x40000400 0x40000c00 3 0xfbf3ee3f 0x3e8b 0x40f8 0x21000200}
    }
    if {$address == 0} {
        if {$count == 16} { return {0x20007c00 0x101 0x105 0x109 0 0 0 0 0 0 0 0 0 0 0 0} }
        if {$::SCENARIO eq "raw_app"} { return {0x20007ff8 0x101} }
        if {$::SCENARIO eq "small_e14"} { return {0x200007fc 0x101} }
        return {0x20007ffc 0x101}
    }
    if {$address == 8192} {
        if {$::SCENARIO eq "bad_vectors"} { return {0xffffffff 0xffffffff} }
        if {$::SCENARIO eq "small_e14"} { return {0x200007f8 0x2101} }
        return {0x20007ff8 0x2101}
    }
    if {$address == 0x00804000} {
        if {$::SCENARIO eq "feather_g18"} { return {3} }
        return {2}
    }
    if {$address == 0x40000420} { return {0xfc} }
    if {$address == 0x40000418} { return {0x7f} }
    if {$address == 0x40000814} { return {2} }
    if {$address == 0x4000080c} {
        if {$::SCENARIO eq "clock_wait"} { return {0} }
        return {2}
    }
    if {$address == 0x41004008} {
        if {$::SCENARIO eq "small_e14" || $::SCENARIO eq "small_bad_stack"} { return {0x00030100} }
        if {$::SCENARIO eq "bad_geometry"} { return {0x00030001} }
        return {0x00031000}
    }
    return {0}
}
proc verify_image {file address type} {
    if {$address == 8192 && $::SCENARIO eq "flash_mismatch"} { error "simulated app mismatch" }
}
proc reset {args} {
    if {$::SCENARIO eq "inspect"} { error "inspect must never reset" }
    if {$::SCENARIO eq "reset_failure"} { error "simulated reset failure" }
    if {$::SCENARIO eq "recovery_reset_failure"} { error "simulated wired reset failure" }
}
proc bp {address args} { set ::mock_bp $address }
proc rbp {address} { set ::mock_bp 0; echo "MOCK_BREAKPOINT_REMOVED" }
proc resume {} { echo "MOCK_CPU_RESUMED" }
proc wait_halt {args} {
    if {$::SCENARIO eq "timeout"} { error "simulated checkpoint timeout" }
    if {$::SCENARIO eq "clock_wait" && $::mock_bp == $::J18_SETUP} {
        set ::mock_pc 0x401a
        error "simulated crystal-ready wait"
    }
    set ::mock_pc $::mock_bp
}
'''


class ProbeRuntimeTests(unittest.TestCase):
    def test_real_tcl_body_against_simulated_cpu(self):
        tool = shutil.which('openocd')
        fallback = Path(r'C:\Program Files (x86)\OpenOCD\bin\openocd.exe')
        if not tool and fallback.is_file():
            tool = str(fallback)
        if not tool:
            self.skipTest('OpenOCD is needed to execute the Tcl runtime against simulated commands.')
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'mock.cfg'
            for scenario in ('normal', 'inspect', 'no_elf', 'timeout', 'flash_mismatch', 'bad_vectors',
                             'fault', 'reset_failure', 'connection_failure', 'leave_halted', 'wrong_chip',
                             'feather_g18','small_e14','generic_no_images','generic_inspect','bad_geometry','unknown_id','small_bad_stack','clock_wait','raw_app',
                             'recovery','recovery_connection_failure','recovery_reset_failure','fault_psp','image_scan','full_recovery'):
                with self.subTest(scenario=scenario):
                    table=' '.join(f'{int(k,0)} {{{name} {flash} {ram}}}' for k,(name,flash,ram) in probe.device_catalog()['devices'].items())
                    boot=Path(folder)/'expected-boot.bin'; app=Path(folder)/'expected-app.bin'
                    boot.write_bytes(struct.pack('<II',0x20007ffc,0x101)+b'\0'*256)
                    app.write_bytes(struct.pack('<II',0x20007ff8,0x2101)+b'\0'*256)
                    setup=f'\nset SWD_DEVICES {{{table}}}\n'
                    if scenario == 'image_scan':
                        setup += 'set SWD_SCAN_BYTES 999999\nset SWD_SCAN_DUMP '+probe.tcl_path(Path(folder)/'installed.bin')+'\n'
                    if scenario not in ('small_e14','generic_no_images','generic_inspect','raw_app') and not scenario.startswith('recovery'):
                        setup+='set J18_BOOT_IMAGE '+probe.tcl_path(boot)+'\nset J18_APP_IMAGE '+probe.tcl_path(app)+'\n'
                    script.write_text(f'set SCENARIO {scenario}\n' + MOCK + setup + '\nsource ' + probe.tcl_path(ROOT / 'tools/swd_probe_body.cfg') + '\n')
                    result = subprocess.run([tool, '-f', str(script)], capture_output=True, text=True, timeout=15)
                    output = result.stdout + result.stderr
                    checks, values = probe.parse_output(output)
                    names = {x['check']: x['status'] for x in checks}
                    expected_failure = scenario in ('timeout', 'flash_mismatch', 'bad_vectors', 'fault', 'reset_failure', 'connection_failure', 'wrong_chip','bad_geometry','unknown_id','small_bad_stack','clock_wait', 'recovery_connection_failure', 'recovery_reset_failure','fault_psp')
                    self.assertEqual(result.returncode != 0, expected_failure, output)
                    if scenario == 'normal':
                        for name in ('connection', 'chip_identity', 'cpu_identity', 'boot.flash', 'app.flash', 'boot_protection',
                                     'software_reset', 'app_entry', 'setup', 'loop', 'loop_again', 'cleanup'):
                            self.assertEqual(names.get(name), 'PASS', output)
                        self.assertIn('attached.pin.SDA', values)
                        self.assertIn('attached.SERCOM0.CTRLA', values)
                    if scenario == 'timeout':
                        self.assertEqual(names.get('app_entry'), 'FAIL', output)
                        self.assertIn('MOCK_BREAKPOINT_REMOVED', output)
                    if scenario in ('no_elf', 'flash_mismatch'):
                        self.assertEqual(names.get('setup'), 'SKIP', output)
                    if scenario in ('inspect', 'bad_vectors'):
                        self.assertEqual(names.get('software_reset'), 'SKIP', output)
                    if scenario not in ('connection_failure', 'recovery_connection_failure'):
                        self.assertEqual(names.get('cleanup'), 'PASS', output)
                        if scenario != 'leave_halted':
                            self.assertIn('MOCK_CPU_RESUMED', output)
                    else:
                        self.assertNotIn('MOCK_CPU_RESUMED', output)
                    if scenario == 'recovery':
                        self.assertEqual(names.get('recovery_reset'), 'PASS', output)
                        self.assertEqual(names.get('software_reset'), 'SKIP', output)
                    if scenario == 'full_recovery':
                        for stage in ('recovery_reset', 'boot.flash', 'app.flash', 'software_reset', 'setup', 'loop_again'):
                            self.assertEqual(names.get(stage), 'PASS', output)
                    if scenario == 'image_scan':
                        self.assertEqual(names.get('image.scan'), 'PASS', output)
                        self.assertEqual(values.get('image.scan_bytes'), '262144', output)
                    if scenario == 'recovery_reset_failure':
                        self.assertEqual(names.get('reset_release'), 'PASS', output)
                        self.assertIn('MOCK_RESET_RELEASED', output)
                    if scenario == 'recovery_connection_failure':
                        self.assertEqual(names.get('reset_release'), 'SKIP', output)
                        self.assertNotIn('MOCK_RESET_RELEASED', output)
                        self.assertTrue(next(x['description'] for x in checks if x['check'] == 'connection'))
                    if scenario in ('fault', 'fault_psp'):
                        self.assertEqual(values.get('attached.frame.pc'), '0x000040f8', output)
                        self.assertEqual(values.get('attached.frame.source'), 'psp' if scenario == 'fault_psp' else 'msp', output)
                        self.assertEqual(values.get('attached.hardfault_handler'), '0x00000109', output)
                    if scenario == 'wrong_chip':
                        self.assertEqual(names.get('chip_identity'), 'FAIL')
                        self.assertNotIn('software_reset', names)
                    if scenario=='feather_g18':
                        self.assertEqual(values.get('chip.name'),'SAMD21G18A',output)
                        self.assertEqual(names.get('app_entry'),'PASS',output)
                        self.assertNotIn('attached.pin.UART0_TX',values)
                        self.assertIn('attached.pin.PA22',values)
                    if scenario=='small_e14':
                        self.assertEqual(values.get('chip.ram_bytes'),'2048',output)
                        self.assertEqual(names.get('app.vectors'),'PASS',output)
                    if scenario=='generic_inspect':
                        self.assertEqual(names.get('app.vectors'),'SKIP',output)
                        self.assertEqual(names.get('software_reset'),'SKIP',output)
                    if scenario=='small_bad_stack':
                        self.assertEqual(names.get('app.vectors'),'FAIL',output)
                        self.assertEqual(names.get('app.flash'),'FAIL',output)
                    if scenario=='clock_wait':
                        self.assertEqual(names.get('setup'),'FAIL',output)
                        self.assertEqual(int(values['setup.pc'],0),0x401a,output)
                        self.assertEqual(values.get('setup.XOSC32K.enabled'),'1',output)
                        self.assertEqual(values.get('setup.XOSC32K.ready'),'0',output)
                    if scenario=='raw_app':
                        self.assertEqual(names.get('app_entry'),'PASS',output)
                        self.assertIn('Already halted at checkpoint',output)


if __name__ == '__main__':
    unittest.main()
