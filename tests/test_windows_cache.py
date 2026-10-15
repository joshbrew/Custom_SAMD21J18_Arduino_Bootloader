from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'tools/Windows_J18_Tools/build_and_flash_j18_separate_bootloader.bat'


class WindowsCacheTests(unittest.TestCase):
    def test_embedded_cache_matches_source_template(self):
        def block(path):
            text=path.read_text()
            return text.split('# BEGIN J18_SKETCH_CACHE',1)[1].split('# END J18_SKETCH_CACHE',1)[0]
        self.assertEqual(block(BATCH),block(ROOT/'tools/windows_cache_functions.ps1'))

    @unittest.skipUnless(sys.platform=='win32','Windows PowerShell batch cache')
    def test_actual_batch_cache_functions(self):
        fixtures=ROOT/'.build/test-fixtures'
        fixtures.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='windows-cache-',dir=fixtures) as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(fixtures.resolve()))
            result=subprocess.run([shutil.which('powershell.exe'),'-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(ROOT/'tests/windows_cache.ps1'),'-BatchPath',str(BATCH),'-FixtureRoot',folder],
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS:',result.stdout)


if __name__=='__main__': unittest.main()
