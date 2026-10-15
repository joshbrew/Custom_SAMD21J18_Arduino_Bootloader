#!/usr/bin/env python3
"""Run generic bootloader/profile/menu/SWD and startup GPIO host checks."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def run(args,**kwargs):
    return subprocess.run([str(x) for x in args],check=True,**kwargs)


def main():
    run([sys.executable,'-m','unittest','discover','-s',ROOT/'bootloader/tests','-p','test_images.py','-v'])
    run([sys.executable,'-m','unittest','discover','-s',ROOT/'tests','-p','test_*.py','-v'])
    env=os.environ.copy()
    windows=sys.platform=='win32'
    if windows:
        vswhere=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
        vs=run([vswhere,'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],capture_output=True,text=True).stdout.strip()
        if not vs:
            raise RuntimeError('Visual Studio C++ Build Tools were not found.')
        vcvars=Path(vs)/'VC/Auxiliary/Build/vcvars64.bat'
        # cmd.exe quoting differs from the C argv quoting used by list2cmdline.
        # Pass this fixed, trusted Visual Studio setup command as one command line.
        setup=f'"{os.environ.get("COMSPEC","cmd.exe")}" /d /s /c ""{vcvars}" >nul && set"'
        text=subprocess.run(setup,check=True,capture_output=True,text=True).stdout
        env.update({k.upper():v for k,v in (line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('='))})
        compiler=shutil.which('cl.exe',path=env['PATH'])
    else:
        compiler=shutil.which('c++')
    if not compiler:
        raise RuntimeError('A host C/C++ compiler is required.')

    def compile_case(name,sources,includes=(),defines=(),c=False,extra=(),execute=True,expect_failure=None):
        folder=ROOT/'.build/host'/name
        folder.mkdir(parents=True,exist_ok=True)
        exe=folder/('test.exe' if windows else 'test')
        if windows:
            flags=['/nologo','/W4','/WX','/D_CRT_SECURE_NO_WARNINGS','/std:c11' if c else '/std:c++14']
            if not c: flags+=['/EHsc','/wd4127']
            flags += ['/I'+str(x) for x in includes]+['/D'+x for x in defines]
            flags += ['/Fe'+str(exe),'/Fo'+str(folder)+os.sep]
        else:
            flags=['-std=c11' if c else '-std=c++14','-Wall','-Wextra','-Werror']
            flags += ['-I'+str(x) for x in includes]+['-D'+x for x in defines]+['-o',str(exe)]
        tool=compiler if windows or not c else shutil.which('cc')
        if not tool: raise RuntimeError('A C compiler is required for host checks.')
        command=[tool,*flags,*extra,*sources]
        if expect_failure:
            command+=['/c' if windows else '-c']
            result=subprocess.run([str(x) for x in command],env=env,cwd=folder,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            if result.returncode==0 or expect_failure not in result.stdout:
                print(result.stdout)
                raise RuntimeError('Expected configuration rejection missing: '+name)
            print('PASS: '+name+' rejected at compile time.')
            return
        print('Testing '+name+'...',flush=True)
        run(command,env=env,cwd=folder)
        if execute: run([exe],env=env)

    compile_case('boot-gpio',[ROOT/'bootloader/tests/test_gpio.cpp'],[ROOT/'bootloader/tests/host'])
    # Exercise the installed, unmodified core's GPIO implementation against the
    # actual generated variant, rather than a mock copy of pinMode's behavior.
    sys.path.insert(0,str(ROOT/'bootloader'))
    from build import package_root
    core=package_root()/'hardware/samd/1.8.14/cores/arduino'
    fixture=ROOT/'.build/host/startup-pins'
    fixture.mkdir(parents=True,exist_ok=True)
    shutil.copy2(core/'wiring_digital.c',fixture/'wiring_digital.cpp')
    variant=(ROOT/'bootloader/arduino-profile/hardware/custom/samd/variants/custom_j18/variant.cpp').read_text()
    pins=re.findall(r'\{(PORT[AB]), (\d+), (PIO_\w+),',variant)
    count=int(re.search(r'#define NUM_DIGITAL_PINS (\d+)u',
        (ROOT/'bootloader/arduino-profile/hardware/custom/samd/variants/custom_j18/variant.h').read_text())[1])
    if len(pins)!=count: raise RuntimeError('Review startup pin-map fixture extraction.')
    (fixture/'GeneratedPinMap.h').write_text('#define NUM_DIGITAL_PINS '+str(count)+'u\n'
        'const PinDescription g_APinDescription[] = {\n'+''.join('{'+','.join(p)+'},\n' for p in pins)+'};\n')
    compile_case('startup-pins',[ROOT/'tests/startup_pins/test_startup.cpp',fixture/'wiring_digital.cpp'],
        [ROOT/'tests/startup_pins',fixture])
    print('PASS: all host checks. No hardware was accessed.')


if __name__=='__main__':
    try: main()
    except (OSError,RuntimeError,subprocess.CalledProcessError) as e:
        print('FAILED: '+str(e),file=sys.stderr)
        sys.exit(1)
