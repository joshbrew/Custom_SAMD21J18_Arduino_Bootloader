#!/usr/bin/env python3
"""Build a SAMD sketch with an explicit board/bootloader pair; flashing is opt-in."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zlib
import hashlib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'bootloader'))
from build import package_root, sha, write, validate_release
from combine import combine


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def find_tool(name, explicit=None):
    if explicit:
        path=Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return path
    found=shutil.which(name)
    if found:
        return Path(found)
    if sys.platform=='win32':
        if name=='arduino-cli':
            candidate=Path(os.environ.get('LOCALAPPDATA','')) / 'Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe'
        else:
            candidate=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)')) / 'OpenOCD/bin/openocd.exe'
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f'{name} not found. Install it, add it to PATH, or pass its explicit path.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('sketch',nargs='?',default='blink',help='blink, blinky, or a sketch folder')
    p.add_argument('--board',choices=('custom','zero'),default='custom')
    p.add_argument('--bootloader',choices=('custom','official'),default='custom')
    p.add_argument('--arduino-cli')
    p.add_argument('--arduino-package',type=Path)
    p.add_argument('--openocd')
    p.add_argument('--adapter',choices=('stlink','gpio'),default='stlink')
    p.add_argument('--flash',action='store_true',help='Program the selected bootloader at zero and application at 0x2000.')
    a=p.parse_args()
    cli=find_tool('arduino-cli',a.arduino_cli)
    if a.arduino_package:
        package=a.arduino_package.resolve()
    else:
        config=json.loads(run([cli,'config','dump','--format','json'],capture_output=True,text=True).stdout).get('config',{})
        data=config.get('directories',{}).get('data')
        package=Path(data)/'packages/arduino' if data else package_root()
    folders={'blink':REPO/'sketches/Blink', 'blinky':REPO/'sketches/Blink'}
    sketch=folders.get(a.sketch,Path(a.sketch)).resolve()
    if sketch.is_file() and sketch.suffix=='.ino':
        sketch=sketch.parent
    if not (sketch/(sketch.name+'.ino')).is_file():
        p.error('Sketch folder must contain a main .ino with the same name.')
    core=package/'hardware/samd/1.8.14'
    if not core.is_dir():
        p.error('Install arduino:samd@1.8.14 before building.')
    custom=validate_release(REPO/'bootloader/firmware') if a.board=='custom' or a.bootloader=='custom' else None
    profile=REPO/'bootloader/arduino-profile/hardware/custom/samd'
    if a.board=='custom':
        for name, expected in custom['profile_sha256'].items():
            if sha(profile/name)!=expected:
                p.error('Generated profile changed; rebuild bootloader first: '+name)
    boot_path=(REPO/'bootloader/firmware/samd21_sam_ba.bin' if a.bootloader=='custom'
               else core/'bootloaders/zero/samd21_sam_ba.bin')
    boot=boot_path.read_bytes()
    boot_sha=hashlib.sha256(boot).hexdigest()
    if a.bootloader=='official' and boot_sha!='89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b':
        p.error('Installed official bootloader differs from pinned Arduino SAMD 1.8.14.')
    work=REPO/'.build/samd'/f'{sketch.name}-{a.board}-{uuid.uuid4().hex[:8]}'
    work.mkdir(parents=True)
    output=work/'output'
    cfg=work/'cli.yaml'
    write(cfg,json.dumps({'directories':{'data':str(package.parents[1]),
        'user':str(REPO/'bootloader/arduino-profile'),'downloads':str(REPO/'.build/staging')},
        'build_cache':{'path':str(REPO/'.build/arduino-cache')}}))
    header=work/'BuildSelection.h'
    # Zero needs these core-wide LED overrides even with a custom bootloader.
    # The matching custom profile already handles LEDs; repeating them is safe.
    # The byte count/CRC below identify the selected image, which may differ
    # from the profile's default. Clock/pin configuration stays in the profile.
    write(header,'#pragma once\n#include <Arduino.h>\n#undef PIN_LED_TXL\n#undef PIN_LED_RXL\n'
          '#define J18_CORE_USB_LEDS_DISABLED 1\n#undef J18_CUSTOM_BOOTLOADER_BYTES\n#undef J18_CUSTOM_BOOTLOADER_CRC32\n'
          f'#define J18_CUSTOM_BOOTLOADER_BYTES {len(boot)}u\n#define J18_CUSTOM_BOOTLOADER_CRC32 0x{zlib.crc32(boot):08x}ul\n')
    fqbn='custom:samd:custom_j18' if a.board=='custom' else 'arduino:samd:arduino_zero_native'
    print(f'Compiling {sketch.name} for {fqbn} with {a.bootloader} bootloader...',flush=True)
    inputs={x.relative_to(sketch).as_posix():sha(x) for x in sketch.rglob('*') if x.is_file()
        and x.suffix in ('.ino','.h','.cpp','.c') and not any(part in x.relative_to(sketch).parts
        for part in ('build','.j18-build','tests','firmware'))}
    command=[cli,'--config-file',cfg,'compile','--fqbn',fqbn,'--build-path',work/'compiler',
             '--output-dir',output,'--build-property',f'compiler.cpp.extra_flags=-include "{header.as_posix()}"',sketch]
    try:
        result=run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    except subprocess.CalledProcessError as e:
        write(work/'compile.log',e.stdout or '')
        raise
    write(work/'compile.log',result.stdout)
    print(result.stdout,end='')
    app_path=output/(sketch.name+'.ino.bin')
    app=app_path.read_bytes()
    combined=combine(boot,app)
    (output/'firmware.sketch.bin').write_bytes(app)
    (output/'samd21_sam_ba.bin').write_bytes(boot)
    (output/'firmware.with_bootloader.bin').write_bytes(combined)
    source_hashes={}
    for src in sketch.rglob('*'):
        relative=src.relative_to(sketch)
        if not src.is_file() or src.suffix not in ('.ino','.h','.c','.cpp') or any(x in relative.parts for x in ('build','.j18-build','tests','firmware')):
            continue
        source_hashes[relative.as_posix()]=sha(src)
        if source_hashes[relative.as_posix()]!=inputs.get(relative.as_posix()):
            p.error('Sketch input changed during compilation: '+str(relative))
        if src.suffix!='.ino':
            compiled=work/'compiler/sketch'/relative
            copy=compiled.read_bytes().replace(b'\r\n',b'\n')
            if copy.startswith(b'#line 1 "'):
                copy=copy.split(b'\n',1)[1]
            if copy!=src.read_bytes().replace(b'\r\n',b'\n'):
                p.error('Sketch source changed during compilation: '+str(relative))
    if set(source_hashes)!=set(inputs):
        p.error('Sketch input files were added/removed during compilation; rebuild.')
    manifest={'fqbn':fqbn,'bootloader':a.bootloader,'bootloader_sha256':boot_sha,
        'bootloader_crc32':f'{zlib.crc32(boot):08x}','hardware_tested':False,
        'source_sha256':source_hashes,'build_selection_header':header.read_text(),
        'images':{x.name:{'sha256':sha(x),'bytes':x.stat().st_size,'flash_offset':
            0 if x.name in ('samd21_sam_ba.bin','firmware.with_bootloader.bin') else 8192}
            for x in output.glob('*.bin')}}
    write(output/'BUILD_MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
    destination=REPO/'releases/samd'/f'{a.board}-{a.bootloader}'/sketch.name
    destination.mkdir(parents=True,exist_ok=True)
    for x in output.iterdir():
        if x.is_file():
            shutil.copy2(x,destination/x.name)
    shutil.copy2(work/'compile.log',destination/'compile.log')
    print(f'Build complete: {destination}',flush=True)
    try:
        openocd=find_tool('openocd',a.openocd)
    except FileNotFoundError:
        if a.flash or a.openocd:
            raise
        print('OpenOCD not found; binaries built. Install OpenOCD before flashing.')
        return
    if a.adapter=='gpio':
        model=Path('/proc/device-tree/model')
        if sys.platform!='linux' or not model.exists() or 'Raspberry Pi 4' not in model.read_text():
            p.error('GPIO adapter requires a Raspberry Pi 4; use ST-LINK on other computers.')
    config=REPO/'bootloader'/f'openocd_bootloader_{"rpi" if a.adapter=="gpio" else "stlink"}.cfg'
    command=[openocd]
    scripts=openocd.parent.parent/'share/openocd/scripts'
    if scripts.is_dir():
        command+=['-s',scripts.as_posix()]
    # Tcl braces preserve spaces; reject braces/newlines rather than constructing unsafe Tcl.
    for path in (output,config):
        if any(c in str(path) for c in '{}\r\n'):
            p.error('OpenOCD paths cannot contain braces or line breaks.')
    # Freeze programming to this run's validated output, independent of a later
    # build overwriting the human-facing releases/ directory.
    command+=['-c',f'set BOOT_IMAGE {{{(output/"samd21_sam_ba.bin").as_posix()}}}',
              '-c',f'set APP_IMAGE {{{(output/"firmware.sketch.bin").as_posix()}}}']
    run(command+['-c','set J18_CHECK_ONLY 1','-f',config.as_posix()])
    if a.flash:
        print('Flashing selected bootloader at 0 and sketch at 0x2000...',flush=True)
        run(command+['-f',config.as_posix()])
    else:
        print('OpenOCD files/config checked. No hardware was flashed.')


if __name__=='__main__':
    try:
        main()
    except subprocess.CalledProcessError as e:
        if e.stdout:
            print(e.stdout)
        print(f'FAILED: command exited {e.returncode}',file=sys.stderr)
        sys.exit(1)
    except (OSError,ValueError,KeyError) as e:
        print('FAILED: '+str(e),file=sys.stderr)
        sys.exit(1)
