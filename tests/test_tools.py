import importlib.util
from pathlib import Path
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortableToolTests(unittest.TestCase):
    def test_linux_and_mac_reject_invalid_images(self):
        for path in ('RPi_J18_Tools/j18_rpi.py','Mac_J18_Tools/j18_mac.py'):
            module=load(path.replace('/','_'),ROOT/'tools'/path)
            boot=(ROOT/'tools/RPi_J18_Tools/samd21_sam_ba.bin').read_bytes()
            app=struct.pack('<II',0x20008000,0x2011)+bytes(64)
            module.validate_vectors(boot,app)
            for sp,pc in ((0x20000000,0x2011),(0x20007ffc,0x2011),(0x20008000,0x1001),
                          (0x20008000,0x2010),(0xffffffff,0xffffffff)):
                with self.assertRaises(RuntimeError):
                    module.validate_vectors(boot,struct.pack('<II',sp,pc)+bytes(64))

    def test_official_bootloader_matches_upstream_reference(self):
        import hashlib
        import zlib
        for path in ('RPi_J18_Tools','Mac_J18_Tools'):
            data=(ROOT/'tools'/path/'samd21_sam_ba.bin').read_bytes()
            self.assertEqual(len(data),6504)
            self.assertEqual(zlib.crc32(data),0x032dc51e)
            self.assertEqual(hashlib.sha256(data).hexdigest(),'89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b')


if __name__=='__main__': unittest.main()
