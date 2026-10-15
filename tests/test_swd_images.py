"""Complete-range firmware identification, aliases, corruption and geometry."""
import json
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import swd_images as images


class ImageIdentificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.boot = struct.pack('<II', 0x20007c00, 0x21) + bytes(range(56))
        self.app = struct.pack('<II', 0x20007ff8, 0x2021) + bytes(range(88))
        self.installed = self.boot + b'\xff' * (8192 - len(self.boot)) + self.app
        self.put('bootloader/firmware/samd21_sam_ba.bin', self.boot)
        self.put('tools/RPi_J18_Tools/samd21_sam_ba.bin', self.boot)
        self.put('sketches/Test/.j18-build/firmware/firmware.sketch.bin', self.app)
        self.put('sketches/Test/.j18-build/sketch-build.json', json.dumps({'fqbn': 'custom:samd:custom_j18'}).encode())
        self.put('releases/samd/test/Test/firmware.with_bootloader.bin', self.installed)
        self.put('sketches/Test/build/zero/Test.ino.bin', self.app)
    def tearDown(self):
        self.temp.cleanup()
    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
    def test_catalog_deduplicates_copies_and_combined_application(self):
        catalog = images.discover(self.root)
        self.assertEqual(catalog['read_bytes'], len(self.installed))
        result = images.identify(catalog, self.installed)
        self.assertEqual(len(result['matches']['application']), 1)
        app = result['matches']['application'][0]
        self.assertEqual(len(app['paths']), 3)
        self.assertEqual(app['profiles'], ['custom:samd:custom_j18'])
        self.assertEqual(len(result['matches']['bootloader']), 2)  # Standalone bytes and full padded boot region.
        self.assertIn('outside matched ranges', result['meaning'])
    def test_matching_vectors_are_insufficient_when_tail_differs(self):
        damaged = self.installed[:-1] + bytes([self.installed[-1] ^ 1])
        result = images.identify(images.discover(self.root), damaged)
        self.assertFalse(result['matches']['application'])
        self.assertTrue(result['matches']['bootloader'])
    def test_short_dump_and_smaller_ram_do_not_produce_false_matches(self):
        result = images.identify(images.discover(self.root), self.installed[:4096], ram_bytes=2048)
        self.assertFalse(result['matches']['bootloader'])
        self.assertFalse(result['matches']['application'])
        self.assertGreater(result['outside_detected_geometry'], 0)
    def test_unpublished_compiler_output_and_non_firmware_files_are_ignored(self):
        self.put('sketches/Test/.j18-build/compiler-output/Other.ino.bin', self.app + b'extra')
        self.put('sketches/Test/data/sensor.bin', self.app)
        self.put('sketches/Test/build/invalid.ino.bin', b'bad')
        catalog = images.discover(self.root)
        self.assertEqual(len(catalog['candidates']), 3)
    def test_manifest_can_identify_an_application_linked_at_zero(self):
        raw = struct.pack('<II', 0x20007ff8, 0x21) + bytes(56)
        self.put('releases/raw/firmware.sketch.bin', raw)
        self.put('releases/raw/BUILD_MANIFEST.json', json.dumps({'images': {
            'firmware.sketch.bin': {'flash_offset': 0}}, 'fqbn': 'test:samd:raw'}).encode())
        result = images.identify(images.discover(self.root), raw)
        self.assertEqual(result['matches']['application'][0]['offset'], 0)
        self.assertEqual(result['matches']['application'][0]['profiles'], ['test:samd:raw'])
    def test_remembered_local_bootloader_is_included_with_an_arbitrary_filename(self):
        local = self.put('local/experiment.bin', self.boot + b'\0\1\2\3')
        self.put('.build/menu/settings-windows.json', json.dumps({'bootloader_file': str(local)}).encode())
        catalog = images.discover(self.root)
        self.assertTrue(any(str(local.resolve()) in x['paths'] for x in catalog['candidates']))
    def test_probe_scan_reports_exact_matches_without_using_selected_image(self):
        import swd_probe
        sketch = self.root / 'sketches/Test'
        catalog = images.discover(self.root)
        installed = self.installed
        output = ('J18_RESULT|connection|PASS|Simulated connection\n'
                  'J18_VALUE|chip.ram_bytes|32768\n'
                  'J18_RESULT|image.scan|PASS|Simulated read\n'
                  f'J18_VALUE|image.scan_bytes|{len(installed)}\n')
        class FakeProcess:
            returncode = 0
            def __init__(self, *args, **kwargs):
                (sketch / '.j18-build/swd/installed-flash.bin').write_bytes(installed)
                self.stdout = io.StringIO(output)
            def poll(self): return 0
            def wait(self): return 0
        import subprocess
        with patch.object(images, 'discover', return_value=catalog), \
             patch.object(swd_probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='')), \
             patch.object(swd_probe.subprocess, 'Popen', FakeProcess), \
             patch('sys.stdout', new_callable=io.StringIO):
            platform = 'windows' if os.name == 'nt' else ('mac' if sys.platform == 'darwin' else 'linux')
            status = swd_probe.run_probe(sketch, Path('mock-openocd'), platform=platform,
                inspect=True, compare_images=False, identify_images=True)
        self.assertEqual(status, 0)
        report = json.loads((sketch / '.j18-build/swd/swd-report.json').read_text())
        self.assertEqual(report['image_identification']['status'], 'COMPLETE')
        self.assertEqual(len(report['image_identification']['matches']['application']), 1)


if __name__ == '__main__':
    unittest.main()
