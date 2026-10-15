"""Settings isolation, invalid pin guards, cache identity and persisted selections."""
import copy
import hashlib
import json
import io
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import bootloader_config as config
import menu_settings
import samd21_menu as menu


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sketch = Path(self.temp.name) / 'Test'
        self.sketch.mkdir()
        (self.sketch / 'Test.ino').write_text('void setup() {} void loop() {}')
    def tearDown(self):
        self.temp.cleanup()
    def test_configuration_is_sketch_local_and_survives_reopen(self):
        baseline = (ROOT / 'bootloader/board.json').read_bytes()
        with patch('builtins.input', side_effect=['1', '2', '0']):
            built, source = config.configure(self.sketch)
        self.assertFalse(built)
        self.assertEqual(source, ROOT / 'bootloader')
        self.assertTrue(config.load_board(config.target_root(self.sketch) / 'board.json')['crystalless'])
        with patch('builtins.input', side_effect=['0']):
            config.configure(self.sketch)
        self.assertTrue(config.load_board(config.target_root(self.sketch) / 'board.json')['crystalless'])
        self.assertEqual((ROOT / 'bootloader/board.json').read_bytes(), baseline)
    def test_invalid_pin_and_unsafe_power_states_are_rejected(self):
        original = config.load_board()
        for change in ('reserved', 'unsafe', 'route'):
            board = copy.deepcopy(original)
            if change == 'reserved':
                board['pins']['LED'] = 'PA24'
            elif change == 'unsafe':
                board['safe_outputs']['GPIO_EN'] = 1
            else:
                board['pins']['UART0_TX'] = 'PA04'
            with self.assertRaises(ValueError):
                config.validate(board)
        for value in (None, [], {}, {'pins': []}):
            with self.assertRaises(ValueError):
                config.validate(value)
        for key, value in (('revision', None), ('pins', {'LED': ['PA05']}), ('high_impedance', [None])):
            board = copy.deepcopy(original)
            board[key] = value
            with self.assertRaises(ValueError):
                config.validate(board)
        board = copy.deepcopy(original)
        board['usb']['product'] = 'A "quoted" USB name'
        with self.assertRaises(ValueError):
            config.validate(board)
    def test_swd_aliases_use_saved_custom_pin_configuration(self):
        from swd_probe import configuration
        board = config.load_board()
        board['pins']['LED'] = 'PA06'
        text = configuration('stlink', Path(self.temp.name), None, None, {}, True, False,
                             12000, board_map='custom', board_config=board)
        self.assertIn('LED 0 6', text)
        self.assertNotIn('LED 0 5 ', text)
    def prepare_target(self):
        folder = config.target_root(self.sketch)
        board = config.load_board()
        config.save(folder / 'board.json', board)
        image = b'test loader'
        (folder / 'firmware').mkdir()
        (folder / 'firmware/samd21_sam_ba.bin').write_bytes(image)
        config.save(folder / 'firmware/manifest.json', {'board': board,
            'sha256': hashlib.sha256(image).hexdigest(), 'profile_sha256': {}})
        return folder
    def test_pending_edits_and_modified_loader_stop_selection(self):
        folder = self.prepare_target()
        before = config.profile_signature(self.sketch, 'target')
        board = config.load_board(folder / 'board.json')
        original_clock = board['crystalless']
        board['crystalless'] = not original_clock
        config.save(folder / 'board.json', board)
        with self.assertRaisesRegex(ValueError, 'settings changed'):
            config.checked_target(self.sketch)
        board['crystalless'] = original_clock
        config.save(folder / 'board.json', board)
        self.assertEqual(before, config.profile_signature(self.sketch, 'target'))
        (folder / 'firmware/samd21_sam_ba.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            config.checked_target(self.sketch)
    def test_same_fqbn_changed_clock_profile_requires_recompile(self):
        from test_menu import FakeTools, image
        self.prepare_target()
        build = self.sketch / '.j18-build'
        firmware = build / 'firmware'
        firmware.mkdir()
        (firmware / 'firmware.sketch.bin').write_bytes(image(8192))
        config.save(build / 'sketch-build.json', {'fqbn': menu.BOARDS['custom'], 'profile_signature': ''})
        engine = menu.Engine('linux')
        engine.tools = FakeTools()
        engine.tool = lambda name: Path(self.temp.name) / name
        calls = []
        engine.compile = lambda sketch, build, board: calls.append(board)
        engine.action(self.sketch, 'target', 'app', 'current', None, True, False, 'stlink', False)
        self.assertEqual(calls, ['target'])
        config.save(build / 'sketch-build.json', {'fqbn': menu.BOARDS['target'],
                    'profile_signature': config.profile_signature(self.sketch, 'target')})
        calls.clear()
        engine.action(self.sketch, 'target', 'app', 'current', None, True, False, 'stlink', False)
        self.assertEqual(calls, [])
    def test_preferences_roundtrip_and_missing_selections(self):
        path = Path(self.temp.name) / 'settings.json'
        local = Path(self.temp.name) / 'loader.bin'
        local.write_bytes(b'test')
        menu_settings.write(path, sketch='Test', board='target', bootloader='local',
                            bootloader_file=str(local), build_only=True, adapter='stlink', sudo_flash=False)
        saved = menu_settings.read(path, ['Test'])
        self.assertEqual(saved['board'], 'target')
        self.assertEqual(saved['sketch'], 'Test')
        self.assertTrue(saved['build_only'])
        local.unlink()
        self.assertEqual(menu_settings.read(path, [])['bootloader'], 'current')
        self.assertNotIn('sketch', menu_settings.read(path, []))
        path.write_text('bad json')
        self.assertEqual(menu_settings.read(path, ['Test']), {})
    def test_fresh_menus_select_repository_default_loader_and_profile(self):
        for platform in ('linux', 'mac', 'windows'):
            if platform == 'windows' and sys.platform != 'win32':
                continue
            path = Path(self.temp.name) / (platform + '-fresh.json')
            env = dict(os.environ, J18_MENU_SETTINGS=str(path))
            if platform == 'windows':
                result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'tools/samd21_menu.ps1')], input='0\n', env=env,
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                with patch.dict(os.environ, env), patch.object(sys, 'argv', ['menu', '--platform', platform]), \
                     patch('builtins.input', return_value='0'), patch('sys.stdout', new_callable=io.StringIO):
                    menu.main()
            saved = json.loads(path.read_text())
            self.assertEqual(saved['board'], 'custom')
            self.assertEqual(saved['bootloader'], 'custom')
    def test_preferences_locked_file_is_nonfatal_and_unchanged_is_not_rewritten(self):
        path = Path(self.temp.name) / 'settings.json'
        menu_settings.write(path, sketch='Test')
        with patch.object(menu_settings, 'save', side_effect=PermissionError('locked')) as save, \
             patch.object(menu_settings.time, 'sleep'), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertTrue(menu_settings.write(path, sketch='Test'))
            save.assert_not_called()
            self.assertFalse(menu_settings.write(path, sketch='Other'))
            self.assertEqual(save.call_count, 4)
            self.assertIn('WARNING', output.getvalue())
        self.assertEqual(json.loads(path.read_text())['sketch'], 'Test')
    @unittest.skipUnless(sys.platform == 'win32', 'Windows native settings replacement')
    def test_windows_settings_replace_and_locked_destination(self):
        result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(ROOT / 'tests/windows_menu_settings.ps1'), '-FixtureRoot', self.temp.name],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    def test_actual_unix_menus_restore_settings_and_honor_explicit_arguments(self):
        for platform in ('linux', 'mac'):
            path = Path(self.temp.name) / (platform + '.json')
            with patch.dict(os.environ, {'J18_MENU_SETTINGS': str(path)}):
                with patch.object(sys, 'argv', ['menu', '--platform', platform, '--board', 'target',
                     '--bootloader', 'target', '--sketch', 'Blink', '--build-only']), \
                     patch('builtins.input', return_value='0'), patch('sys.stdout', new_callable=io.StringIO):
                    menu.main()
                with patch.object(sys, 'argv', ['menu', '--platform', platform]), \
                     patch('builtins.input', return_value='0'), patch('sys.stdout', new_callable=io.StringIO) as output:
                    menu.main()
                    self.assertIn('Configured custom J18 profile', output.getvalue())
                    self.assertIn('Sketch: Blink', output.getvalue())
                    self.assertIn('BUILD / CHECK ONLY', output.getvalue())
                with patch.object(sys, 'argv', ['menu', '--platform', platform, '--board', 'zero']), \
                     patch('builtins.input', return_value='0'), patch('sys.stdout', new_callable=io.StringIO):
                    menu.main()
                self.assertEqual(json.loads(path.read_text())['board'], 'zero')
    def test_default_rebuild_action_selects_repository_loader_only_after_success(self):
        for platform in ('linux', 'mac'):
            path = Path(self.temp.name) / (platform + '-default.json')
            for success in (True, False):
                error = None if success else RuntimeError('simulated failed build')
                with patch.dict(os.environ, {'J18_MENU_SETTINGS': str(path)}), \
                     patch.object(sys, 'argv', ['menu', '--platform', platform, '--board', 'target', '--bootloader', 'target']), \
                     patch.object(menu.Engine, 'tool', return_value=Path('arduino-cli')), \
                     patch.object(menu.Engine, 'core', return_value=(Path(self.temp.name), Path('core'))), \
                     patch.object(config, 'rebuild_default', side_effect=error) as rebuild, \
                     patch('builtins.input', side_effect=['G', '', '0']), patch('sys.stdout', new_callable=io.StringIO):
                    menu.main()
                rebuild.assert_called_once_with(Path(self.temp.name) / 'packages/arduino')
                saved = json.loads(path.read_text())
                self.assertEqual(saved['bootloader'], 'custom' if success else 'target')
                self.assertEqual(saved['board'], 'custom' if success else 'target')
    def test_default_rebuild_command_uses_repository_defaults(self):
        with patch.object(config.subprocess, 'run') as run:
            config.rebuild_default(Path(self.temp.name))
        arguments = run.call_args.args[0]
        self.assertIn('--verify-repeat', arguments)
        self.assertNotIn('--board-config', arguments)
        self.assertNotIn('--out', arguments)
        self.assertEqual(arguments[1], str(ROOT / 'bootloader/build.py'))
    def test_unix_menus_route_wired_full_probe_and_identification(self):
        for platform in ('linux', 'mac'):
            for routine in ('5', '6'):
                inputs = ['D', routine] + (['2'] if routine == '6' else []) + ['', '0']
                with patch.dict(os.environ, {'J18_MENU_SETTINGS': str(Path(self.temp.name) / 'probe-menu.json')}), \
                     patch.object(sys, 'argv', ['menu', '--platform', platform]), \
                     patch.object(menu.Engine, 'probe') as probe, \
                     patch('builtins.input', side_effect=inputs), patch('sys.stdout', new_callable=io.StringIO):
                    menu.main()
                args = probe.call_args.args
                self.assertEqual(args[2], routine == '6')  # inspect only
                self.assertEqual(args[5], routine == '5')  # selected-image comparisons
                self.assertTrue(args[8])                  # wired reset
                self.assertEqual(args[9], routine == '6')  # image identification
    @unittest.skipUnless(sys.platform == 'win32', 'Windows menu')
    def test_actual_windows_menu_restores_saved_preferences(self):
        path = Path(self.temp.name) / 'windows.json'
        env = dict(os.environ, J18_MENU_SETTINGS=str(path))
        command = [shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', str(ROOT / 'tools/samd21_menu.ps1')]
        for extra in (['-Board', 'target', '-Bootloader', 'target', '-Sketch', 'Blink', '-BuildOnly'], [], ['-Board', 'zero']):
            result = subprocess.run(command + extra, input='0\n', text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if not extra:
                self.assertIn('Configured custom J18 profile', result.stdout)
                self.assertIn('Sketch: Blink', result.stdout)
                self.assertIn('BUILD / CHECK ONLY', result.stdout)
        self.assertEqual(json.loads(path.read_text())['board'], 'zero')


if __name__ == '__main__':
    unittest.main()
