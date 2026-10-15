#!/usr/bin/env python3
"""Copy a self-contained platform tools folder beside a sketch, preserving changed files."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('platform',choices=('windows','linux','mac'))
    p.add_argument('sketch',type=Path)
    a=p.parse_args()
    sketch=a.sketch.resolve()
    if not sketch.is_dir() or not any(sketch.glob('*.ino')):
        p.error('Destination must be an existing sketch folder containing an .ino.')
    name={'windows':'Windows_J18_Tools','linux':'RPi_J18_Tools','mac':'Mac_J18_Tools'}[a.platform]
    source=ROOT/name
    dest=sketch/name
    if dest.resolve()==source.resolve() or not dest.resolve().is_relative_to(sketch):
        p.error('Invalid/symlinked destination.')
    backup=sketch/'.j18-tool-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/name
    for src in source.rglob('*'):
        if not src.is_file() or '__pycache__' in src.parts:
            continue
        relative=src.relative_to(source)
        target=dest/relative
        if not target.resolve().is_relative_to(sketch):
            p.error('Destination file resolves outside the sketch folder.')
        if target.exists() and target.read_bytes()!=src.read_bytes():
            saved=backup/relative
            saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,saved)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,target)
    print('Copied tools to '+str(dest))
    if backup.exists():
        print('Previous changed files saved in '+str(backup))

if __name__=='__main__':
    main()
