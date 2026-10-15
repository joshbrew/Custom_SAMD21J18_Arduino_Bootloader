"""Install only into temporary sketchbooks; never touch the user's Arduino setup."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import install_board_profile as installer


class ProfileInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.source=self.base/'source/hardware/example/samd'
        self.source.mkdir(parents=True)
        (self.source/'boards.txt').write_text('test_board.name=Test native USB board\n')
        (self.source/'variant.h').write_text('original')
        self.book=self.base/'sketchbook'
    def tearDown(self):
        self.temp.cleanup()
    def install(self, validate=lambda boards: None):
        return installer.install_platform(self.source,self.book,'example','samd',validate)
    def test_general_source_layouts(self):
        for source in (self.base/'source',self.base/'source/hardware',self.source):
            self.assertEqual(installer.platforms(source),[('example','samd',self.source)])
        with self.assertRaises(ValueError):
            installer.platforms(self.source,vendor='../escape',architecture='samd')
    def test_update_preserves_unrelated_files_and_backup(self):
        target,boards=self.install()
        self.assertEqual(boards,['example:samd:test_board'])
        (target/'user-note.txt').write_text('keep me')
        (self.source/'variant.h').write_text('updated')
        self.install()
        self.assertEqual((target/'variant.h').read_text(),'updated')
        self.assertEqual((target/'user-note.txt').read_text(),'keep me')
        backup=self.book/'.arduino-profile-installer/example/samd/previous'
        self.assertEqual((backup/'variant.h').read_text(),'original')
        self.install()
        self.assertEqual((backup/'variant.h').read_text(),'original','Idempotent install must retain prior backup.')
    def test_failed_discovery_rolls_back(self):
        target,_=self.install()
        (self.source/'variant.h').write_text('new incompatible version')
        def reject(boards): raise ValueError('board discovery failed')
        with self.assertRaisesRegex(ValueError,'discovery failed'):
            self.install(reject)
        self.assertEqual((target/'variant.h').read_text(),'original')
    def test_obsolete_managed_files_removed_but_user_edits_kept(self):
        (self.source/'old.txt').write_text('original')
        (self.source/'edited.txt').write_text('original')
        target,_=self.install()
        (target/'edited.txt').write_text('user edited')
        (self.source/'old.txt').unlink()
        (self.source/'edited.txt').unlink()
        self.install()
        self.assertFalse((target/'old.txt').exists())
        self.assertEqual((target/'edited.txt').read_text(),'user edited')
    def test_bad_receipt_path_cannot_escape_platform(self):
        target,_=self.install()
        stamp=target/installer.STAMP
        stamp.write_text(json.dumps({'files_sha256':{'../../escape.txt':'fake'}}))
        with self.assertRaisesRegex(ValueError,'Invalid path'):
            self.install()
        self.assertTrue((target/'boards.txt').is_file())


if __name__=='__main__': unittest.main()
