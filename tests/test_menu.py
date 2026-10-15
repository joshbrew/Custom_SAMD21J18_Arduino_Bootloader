"""Exercise static images, profile selection, and flash modes without hardware."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import samd21_menu as menu


def image(offset, stack=0x20007ff8):
    return struct.pack('<II', stack, offset + 9) + b'12345678'


class FakeTools:
    LOG = None
    def __init__(self):
        self.calls = []
    def say(self, text):
        pass
    def run(self, args, **kwargs):
        self.calls.append([str(x) for x in args])
        return 'Open On-Chip Debugger 0.12.0' if '--version' in args else ''
    def select_adapter(self, adapter, openocd):
        return 'stlink'


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sketch = Path(self.temp.name) / 'Test'
        self.sketch.mkdir()
        (self.sketch / 'Test.ino').write_text('invalid source deliberately never compiled')
        self.build = self.sketch / '.j18-build'
        self.firmware = self.build / 'firmware'
        self.firmware.mkdir(parents=True)
        self.engine = menu.Engine('linux')
        self.fake = FakeTools()
        self.engine.tools = self.fake
        self.engine.tool = lambda name: Path(self.temp.name) / name
        self.engine.compile = lambda *args: self.fail('Unexpected compilation of static application')
    def tearDown(self):
        self.temp.cleanup()
    def action(self, mode, board='zero', loader='current'):
        self.engine.action(self.sketch, board, mode, loader, None, True, False, 'stlink', False)
    def test_boot_only_never_compiles_app(self):
        (self.firmware / 'samd21_sam_ba.bin').write_bytes(image(0, 0x20007ffc))
        self.action('boot')
        self.assertFalse((self.firmware / 'firmware.sketch.bin').exists())
        self.assertTrue(all('init' not in ' '.join(x) for x in self.fake.calls))
        self.assertIn('set J18_CHECK_ONLY 1', ' '.join(self.fake.calls[-1]))
    def test_app_only_never_selects_loader(self):
        (self.firmware / 'firmware.sketch.bin').write_bytes(image(8192))
        self.engine.loader = lambda *args: self.fail('App-only requested a bootloader')
        self.action('app')
        self.assertFalse((self.firmware / 'samd21_sam_ba.bin').exists())
    def test_profile_mismatch_rebuilds_automatically(self):
        (self.firmware / 'firmware.sketch.bin').write_bytes(image(8192))
        menu.record(self.build / 'sketch-build.json', {'fqbn': menu.BOARDS['zero']})
        calls = []
        self.engine.compile = lambda sketch, build, board: calls.append(board)
        self.action('app', 'custom')
        self.assertEqual(calls, ['custom'])
    def test_failed_profile_rebuild_keeps_previous_app_and_stops(self):
        app = self.firmware / 'firmware.sketch.bin'
        app.write_bytes(image(8192))
        menu.record(self.build / 'sketch-build.json', {'fqbn': menu.BOARDS['zero']})
        def fail(*args):
            raise RuntimeError('compile failed')
        self.engine.compile = fail
        with self.assertRaisesRegex(RuntimeError, 'compile failed'):
            self.action('app', 'custom')
        self.assertEqual(app.read_bytes(), image(8192))
        self.assertEqual(self.fake.calls, [])
    def test_combined_forces_official_loader_and_layout(self):
        app = image(8192)
        (self.firmware / 'firmware.sketch.bin').write_bytes(app)
        choices = []
        def loader(choice, *args):
            choices.append(choice)
            return image(0)
        self.engine.loader = loader
        self.action('combined', loader='custom')
        data = (self.firmware / 'firmware.with_bootloader.bin').read_bytes()
        self.assertEqual(choices, ['core'])
        self.assertEqual(data[:16], image(0))
        self.assertEqual(data[16:8192], b'\xff' * (8192 - 16))
        self.assertEqual(data[8192:], app)
    def test_combined_uses_custom_loader_for_custom_profile(self):
        (self.firmware / 'firmware.sketch.bin').write_bytes(image(8192))
        from bootloader_config import profile_signature
        menu.record(self.build / 'sketch-build.json', {'fqbn': menu.BOARDS['custom'],
                    'profile_signature': profile_signature(self.sketch, 'custom')})
        choices=[]
        def loader(choice,*args):
            choices.append(choice)
            return image(0)
        self.engine.loader=loader
        self.action('combined',board='custom',loader='github')
        self.assertEqual(choices,['custom'])
    def test_current_preserves_manual_loader_and_static_folder(self):
        boot = image(0)
        (self.firmware / 'samd21_sam_ba.bin').write_bytes(boot)
        (self.firmware / 'firmware.sketch.bin').write_bytes(image(8192))
        self.action('separate')
        self.action('separate')
        self.assertEqual((self.firmware / 'samd21_sam_ba.bin').read_bytes(), boot)
        self.assertEqual(sorted(x.name for x in self.build.iterdir()),
                         ['build-and-flash.log', 'firmware', 'launcher.lock'])
    def test_bad_vectors_and_combined_as_app_rejected(self):
        for data in (image(0), image(8192, 0x20007ffc), b'bad'):
            with self.assertRaises(ValueError):
                menu.vectors(data, 8192)
        with self.assertRaises(ValueError):
            menu.combine(b'x' * 8193, image(8192))
    def test_build_identity_tracks_official_or_custom_loader(self):
        for file, crc in [('tools/RPi_J18_Tools/samd21_sam_ba.bin', '032dc51e'),
                          ('bootloader/firmware/samd21_sam_ba.bin', json.loads((ROOT/'bootloader/firmware/manifest.json').read_text())['crc32'])]:
            data = (ROOT / file).read_bytes()
            header = menu.selection_header(data)
            self.assertIn(f'J18_CUSTOM_BOOTLOADER_BYTES {len(data)}u', header)
            self.assertIn(f'J18_CUSTOM_BOOTLOADER_CRC32 0x{crc}ul', header)
        self.assertNotIn('J18_CUSTOM_BOOTLOADER_CRC32', menu.selection_header())
    def test_generated_config_protects_and_verifies_in_order(self):
        config = menu.configuration('stlink')
        self.assertLess(config.index('J18_CHECK_ONLY'), config.index('\ninit\n'))
        self.assertLess(config.index('at91samd bootloader 0'), config.index('flash write_image'))
        self.assertEqual(config.count('verify_image'), 2)
        self.assertIn('st-link vid_pid 0x0483 0x3744', config)
        self.assertEqual(menu.PHASES['separate'], ['boot', 'app'])
    def test_failed_recompile_keeps_previous_app(self):
        app = self.firmware / 'firmware.sketch.bin'
        app.write_bytes(image(8192))
        def fail(*args):
            raise RuntimeError('compile failed')
        self.engine.compile = fail
        with self.assertRaisesRegex(RuntimeError, 'compile failed'):
            self.engine.action(self.sketch, 'custom', 'app', 'current', None, True, True, 'stlink', False)
        self.assertEqual(app.read_bytes(), image(8192))


if __name__ == '__main__':
    unittest.main()
