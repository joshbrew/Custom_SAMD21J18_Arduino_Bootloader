$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (!$python) { throw 'Python 3 is required.' }
    & $python.Source -m unittest discover -s tests -p 'test_images.py' -v
    if ($LASTEXITCODE) { throw 'Image/configuration tests failed.' }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio C++ Build Tools are required for the GPIO host test.' }
    $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (!$vs) { throw 'Visual Studio C++ Build Tools were not found.' }
    New-Item -ItemType Directory -Force '.validation\host' | Out-Null
    $vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
    # One shell owns the complete compiler invocation; no filesystem deletion/moving.
    $line = 'call "' + $vcvars + '" >nul && cl /nologo /EHsc /W4 /WX /std:c++14 /Itests\host tests\test_gpio.cpp /Fe.validation\host\test_gpio.exe /Fo.validation\host\test_gpio.obj && .validation\host\test_gpio.exe'
    & $env:ComSpec /d /s /c $line
    if ($LASTEXITCODE) { throw 'GPIO sequencing test failed.' }
} finally { Pop-Location }
