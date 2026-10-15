"""Portable profile export, workflow stop ordering and menu preview; no flashing."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import arduino_profile_workflow as workflow
import samd21_menu as menu


class ProfileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = ROOT / 'bootloader/arduino-profile'
        self.manifest_path = ROOT / 'bootloader/firmware/manifest.json'
        self.manifest = workflow.checked_profile(self.source, self.manifest_path)
    def tearDown(self):
        self.temp.cleanup()
    def test_export_is_reproducible_and_contains_installable_hardware(self):
        folder = self.base / 'Profile'
        workflow.export_profile(self.source, folder, self.manifest)
        first = folder.with_suffix('.zip').read_bytes()
        workflow.export_profile(self.source, folder, self.manifest)
        self.assertEqual(first, folder.with_suffix('.zip').read_bytes())
        with ZipFile(folder.with_suffix('.zip')) as archive:
            self.assertIn('Profile/hardware/custom/samd/boards.txt', archive.namelist())
            self.assertIn('Profile/install_profile.py', archive.namelist())
        release = json.loads((folder / 'PROFILE_MANIFEST.json').read_text())
        self.assertEqual(release['bootloader_sha256'], self.manifest['sha256'])
        for name, expected in release['files_sha256'].items():
            self.assertEqual(menu.digest(folder / name), expected)
    def test_modified_profile_or_unrelated_destination_is_rejected(self):
        copied = self.base / 'profile'
        shutil.copytree(self.source, copied)
        variant = copied / 'hardware/custom/samd/variants/custom_j18/variant.cpp'
        variant.write_text('changed source')
        with self.assertRaisesRegex(ValueError, 'differs'):
            workflow.checked_profile(copied, self.manifest_path)
        destination = self.base / 'unrelated'
        destination.mkdir()
        (destination / 'keep.txt').write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            workflow.export_profile(self.source, destination, self.manifest)
        self.assertEqual((destination / 'keep.txt').read_text(), 'preserve')
    def test_preview_makes_no_changes_and_failed_build_stops_install_export(self):
        with patch.object(workflow.config, 'rebuild_default') as build, \
             patch.object(workflow.installer, 'main') as install, \
             patch.object(workflow, 'export_profile') as export:
            workflow.main(['--build', '--install', '--export', '--check-only'])
            build.assert_not_called(); install.assert_not_called(); export.assert_not_called()
            build.side_effect = ValueError('build failed')
            with self.assertRaisesRegex(ValueError, 'build failed'):
                workflow.main(['--build', '--install', '--export'])
            install.assert_not_called(); export.assert_not_called()
            build.side_effect = None
            install.return_value = 1
            with self.assertRaisesRegex(RuntimeError, 'installation failed'):
                workflow.main(['--install', '--export'])
            export.assert_not_called()
    def test_linux_mac_menu_routes_export_workflow_preview(self):
        for platform in ('linux', 'mac'):
            with patch.dict(os.environ, {'J18_MENU_SETTINGS': str(self.base / (platform + '.json'))}), \
                 patch.object(sys, 'argv', ['menu', '--platform', platform, '--build-only']), \
                 patch('builtins.input', side_effect=['E', '5', '', '0']), \
                 patch.object(workflow, 'main', return_value=0) as execute, \
                 patch('sys.stdout', new_callable=io.StringIO):
                menu.main()
            self.assertEqual(execute.call_args.args[0], ['--target', 'default', '--build', '--install', '--export', '--check-only'])
    @unittest.skipUnless(sys.platform == 'win32', 'Windows menu/Arduino CLI')
    def test_actual_windows_menu_preview_and_extracted_release_install(self):
        env = dict(os.environ, J18_MENU_SETTINGS=str(self.base / 'menu.json'))
        result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', str(ROOT / 'tools/samd21_menu.ps1'), '-BuildOnly'],
            input='E\n5\n\n0\n', env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PREVIEW build=True, install=True, export=True', result.stdout)
        folder = self.base / 'Profile'
        workflow.export_profile(self.source, folder, self.manifest)
        extracted = self.base / 'extracted'
        with ZipFile(folder.with_suffix('.zip')) as archive:
            archive.extractall(extracted)
        cli = workflow.installer.find_cli()
        result = subprocess.run([sys.executable, str(extracted / 'Profile/install_profile.py'),
            '--arduino-cli', str(cli), '--sketchbook', str(self.base / 'Arduino')],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Installed and discovered: custom:samd:custom_j18', result.stdout)
        installed = self.base / 'Arduino/hardware/custom/samd'
        self.assertEqual((installed / 'boards.txt').read_bytes(),
            (self.source / 'hardware/custom/samd/boards.txt').read_bytes())


if __name__ == '__main__':
    unittest.main()
