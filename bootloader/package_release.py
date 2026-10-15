#!/usr/bin/env python3
"""Package the checked bootloader/profile, optionally with a SAMD release folder."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from build import ROOT, sha, validate_release, write
from combine import combine


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sketch-build',type=Path,
        help='Release folder produced by tools/build_samd.py blink with the custom loader')
    a=p.parse_args()
    manifest=validate_release(ROOT/'firmware')
    boot=(ROOT/'firmware/samd21_sam_ba.bin').read_bytes()
    if a.sketch_build:
        build=a.sketch_build.resolve()
        record=json.loads((build/'BUILD_MANIFEST.json').read_text())
        sketch=ROOT.parent/'sketches'/build.name
        current={x.relative_to(sketch).as_posix():sha(x) for x in sketch.rglob('*')
            if x.is_file() and x.suffix in ('.ino','.h','.cpp','.c') and not any(v in x.relative_to(sketch).parts
            for v in ('build','.j18-build','tests','firmware'))}
        if current!=record['source_sha256']:
            p.error('Sketch source changed since compilation; rebuild with tools/build_samd.py.')
        if record['bootloader_sha256']!=manifest['sha256'] or record['bootloader']!='custom':
            p.error('Sketch release must use this custom bootloader.')
        captured={name:(build/name).read_bytes() for name in record['images']}
        for name,data in captured.items():
            if hashlib.sha256(data).hexdigest()!=record['images'][name]['sha256']:
                p.error('Sketch image mismatch: '+name)
        if captured['samd21_sam_ba.bin']!=boot or captured['firmware.with_bootloader.bin']!=combine(boot,captured['firmware.sketch.bin']):
            p.error('Sketch combined image does not contain this loader and sketch.')
        dest=ROOT/'firmware/sketch'
        dest.mkdir(parents=True,exist_ok=True)
        for name,data in captured.items(): (dest/name).write_bytes(data)
        write(dest/'BUILD_MANIFEST.json',json.dumps(record,indent=2)+'\n')
        write(dest/'README.md','# Sketch firmware\n\n'
            'Generated from the current repository release manifest. '
            'firmware.sketch.bin goes at 0x2000; firmware.with_bootloader.bin goes at zero. '
            'Board and selected loader are recorded in BUILD_MANIFEST.json. No target was flashed.\n')
    excluded={'.git','.build','.validation','__pycache__','objects'}
    archive=ROOT.parent/'dist/Custom_Bootloader.zip'
    archive.parent.mkdir(parents=True,exist_ok=True)
    files=[x for x in sorted(ROOT.rglob('*')) if x.is_file() and not any(v in excluded for v in x.relative_to(ROOT).parts)
        and x.name!='build.log' and x.suffix not in ('.exe','.o','.obj','.pyc')]
    frozen={}
    for x in files:
        if not x.resolve().is_relative_to(ROOT): p.error('Archive input resolves outside bootloader folder.')
        frozen[x.relative_to(ROOT).as_posix()]=x.read_bytes()
    staged=archive.with_suffix('.zip.tmp')
    with ZipFile(staged,'w',ZIP_DEFLATED) as z:
        for relative,data in frozen.items():
            info=ZipInfo(ROOT.name+'/'+relative); info.create_system=3
            info.external_attr=(0o100755 if Path(relative).suffix in ('.py','.sh') else 0o100644)<<16
            info.compress_type=ZIP_DEFLATED; z.writestr(info,data)
    with ZipFile(staged) as z:
        if z.testzip(): raise ValueError('ZIP CRC check failed.')
        for relative,data in frozen.items():
            if z.read(ROOT.name+'/'+relative)!=data or (ROOT/relative).read_bytes()!=data:
                raise ValueError('Archive input changed or ZIP mismatch: '+relative)
    staged.replace(archive)
    print(f'Packaged {len(files)} files: {archive} ({archive.stat().st_size} bytes)')


if __name__=='__main__': main()
