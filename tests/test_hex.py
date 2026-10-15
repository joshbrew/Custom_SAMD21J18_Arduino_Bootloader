import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from intel_hex import parse,encode,merge,checked_dual_core


class HexTests(unittest.TestCase):
    def test_roundtrip_sparse_and_core_boundaries(self):
        image={0:1,1:2,65535:3,65536:4,0x1000000:5}
        self.assertEqual(parse(encode(image)),image)
        self.assertEqual(merge({0:1},{0:1,1:2}),{0:1,1:2})
        with self.assertRaises(ValueError): merge({0:1},{0:2})
        with self.assertRaises(ValueError): parse(':0100000001FF\n:00000001FF\n')
        with self.assertRaises(ValueError): parse(encode(image).replace(':00000001FF\n',''))

    def test_dual_core_requires_both_vectors(self):
        app={i:b for i,b in enumerate((0x20008000).to_bytes(4,'little')+(0x101).to_bytes(4,'little'))}
        net={0x1000000+i:b for i,b in enumerate((0x21010000).to_bytes(4,'little')+(0x1000101).to_bytes(4,'little'))}
        self.assertEqual(checked_dual_core(app,net),merge(app,net))
        with self.assertRaises(ValueError): checked_dual_core(app,app)


if __name__=='__main__': unittest.main()
