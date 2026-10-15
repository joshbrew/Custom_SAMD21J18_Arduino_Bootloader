#!/usr/bin/env python3
"""Combine a sketch-only Zero/Custom .bin with this specific bootloader."""
import argparse
import hashlib
from pathlib import Path
import struct
from build import ROOT, write


def combine(boot, app):
    if not 8 <= len(boot) <= 8192 or len(app) < 8 or len(app) > 253952:
        raise ValueError('Image size is outside the 8 KB boot / 248 KB application layout.')
    for image, low, high in ((boot, 0, len(boot)), (app, 8192, 8192 + len(app))):
        sp, reset = struct.unpack_from('<II', image)
        alignment = 4 if low == 0 else 8 # Official Zero loader starts at 0x20007FFC.
        if not 0x20000000 < sp <= 0x20008000 or sp % alignment or not reset & 1 or not low <= (reset & ~1) < high:
            raise ValueError('Invalid vectors. Pass the sketch-only .bin linked at 0x2000, not a combined image.')
    return boot + b'\xff' * (8192 - len(boot)) + app


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('app', type=Path)
    p.add_argument('--boot', type=Path, default=ROOT / 'firmware/samd21_sam_ba.bin')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.resolve() in (a.app.resolve(), a.boot.resolve()):
        p.error('Output must not replace an input image.')
    try:
        boot, app = a.boot.read_bytes(), a.app.read_bytes()
        data = combine(boot, app)
    except (OSError, ValueError) as e:
        p.error(str(e))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(data)
    write(a.out.with_suffix(a.out.suffix + '.sha256'), hashlib.sha256(data).hexdigest() + '  ' + a.out.name + '\n')
    print(f'Combined {len(boot)} boot bytes + erased padding + {len(app)} sketch bytes at 0x2000: {a.out}')
    print('Flash the combined file at 0x00000000, with boot protection temporarily disabled.')


if __name__ == '__main__':
    main()
