param(
    [ValidateSet('combined','separate','boot','app','probe','install-profile')][string]$Mode,
    [string]$Sketch,
    [ValidateSet('zero','custom','target')][string]$Board='custom',
    [ValidateSet('current','core','github','custom','target','local')][string]$Bootloader='custom',
    [string]$BootloaderFile,
    [switch]$BuildOnly,
    [switch]$Rebuild,
    [switch]$InspectOnly,
    [switch]$ProbeNoCompare,
    [switch]$ProbeUnderReset,
    [switch]$ProbeIdentifyImages,
    [ValidateSet('auto','generic','custom')][string]$ProbeMap='auto',
    [string]$ProbeAppOffset='0x2000',
    [string]$Profile='custom',
    [string]$ProfileSource,
    [string]$ProfileSketchbook,
    [switch]$List
)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$menuSettingsPath=if ($env:J18_MENU_SETTINGS) { [IO.Path]::GetFullPath($env:J18_MENU_SETTINGS) } else { Join-Path $repo '.build\menu\settings-windows.json' }
$batch=Join-Path $PSScriptRoot 'Windows_J18_Tools\build_and_flash_j18_separate_bootloader.bat'
$toolFolder=Split-Path -Parent $batch
$toolStore=Join-Path $env:LOCALAPPDATA 'SAMD21Custom\Tools'
$scriptRoot=$repo
# Reuse the same tool discovery/install/validation functions as the copy-in tool.
# Register function definitions only; never execute its main flashing script here.
$payload=[IO.File]::ReadAllText($batch)
$marker='# J18_POWERSHELL_PAYLOAD'
$payload=$payload.Substring($payload.LastIndexOf($marker)+$marker.Length)
$tokens=$null; $parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($payload,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([ScriptBlock]::Create($node.Extent.Text))
    }
}

function Get-AvailableSketches {
    $items=@()
    foreach ($folder in (Get-ChildItem -LiteralPath (Join-Path $repo 'sketches') -Directory | Sort-Object Name)) {
        if (Test-Path -LiteralPath (Join-Path $folder.FullName ($folder.Name+'.ino')) -PathType Leaf) {
            $items += [pscustomobject]@{Name=$folder.Name;Path=$folder.FullName}
        }
    }
    return $items
}

function Get-CoreBootloader {
    $candidates=@((Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'),
        (Join-Path $env:ProgramFiles 'Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'))
    $cli=Find-Tool 'arduino-cli.exe' $candidates 'arduino-cli'
    if ($env:J18_ARDUINO_CLI) { $cli=$env:J18_ARDUINO_CLI }
    if (!$cli) { $cli=Install-ArduinoCli }
    $data=(Invoke-Tool $cli @('config','get','directories.data') -Quiet).Trim()
    if (!$data) { throw 'Cannot determine the Arduino core directory.' }
    $path=Join-Path $data 'packages\arduino\hardware\samd\1.8.14\bootloaders\zero\samd21_sam_ba.bin'
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        $null=Invoke-Tool $cli @('core','update-index')
        $null=Invoke-Tool $cli @('core','install','arduino:samd@1.8.14')
    }
    if ((Get-Sha256 $path) -ne '89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b') {
        throw 'Installed Arduino 1.8.14 bootloader checksum mismatch.'
    }
    return $path
}

function Get-GitHubBootloader {
    $path=Join-Path $repo '.build\menu\samd21_sam_ba.github.bin'
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        $null=New-Item -ItemType Directory -Force (Split-Path -Parent $path)
        $bundled=Join-Path $PSScriptRoot 'RPi_J18_Tools\samd21_sam_ba.bin'
        if (Test-Path -LiteralPath $bundled -PathType Leaf) {
            Write-Host 'Using the bundled, verified ArduinoCore-samd 1.8.14 GitHub binary.'
            Copy-Item -LiteralPath $bundled -Destination $path
        } else {
            [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/arduino/ArduinoCore-samd/1.8.14/bootloaders/zero/samd21_sam_ba.bin' -OutFile $path
        }
    }
    if ((Get-Sha256 $path) -ne '89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b') {
        throw 'GitHub bootloader checksum mismatch; remove the cached download and retry.'
    }
    return $path
}

function Select-LoaderFile([string]$Choice,[string]$LocalFile,[string]$Firmware) {
    switch ($Choice) {
        'current' {
            $path=Join-Path $Firmware 'samd21_sam_ba.bin'
            if (!(Test-Path -LiteralPath $path -PathType Leaf)) { return Get-GitHubBootloader }
            return $path
        }
        'core' { return Get-CoreBootloader }
        'github' { return Get-GitHubBootloader }
        'custom' {
            $path=Join-Path $repo 'bootloader\firmware\samd21_sam_ba.bin'
            $record=Get-Content -LiteralPath (Join-Path $repo 'bootloader\firmware\manifest.json') -Raw | ConvertFrom-Json
            if ((Get-Sha256 $path) -ne $record.sha256) { throw 'Custom bootloader does not match its manifest; rebuild it first.' }
            return $path
        }
        'target' {
            $folder=Get-ConfiguredTarget (Split-Path -Parent (Split-Path -Parent $Firmware))
            $path=Join-Path $folder 'firmware\samd21_sam_ba.bin'
            $record=Get-Content -LiteralPath (Join-Path $folder 'firmware\manifest.json') -Raw | ConvertFrom-Json
            if ((Get-Sha256 $path) -ne $record.sha256) { throw 'Configured bootloader differs from its manifest; rebuild with L then C.' }
            return $path
        }
        'local' {
            if (!$LocalFile) { throw 'Select a local bootloader .bin first.' }
            $path=[IO.Path]::GetFullPath($LocalFile.Trim('"'))
            if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing bootloader: $path" }
            return $path
        }
    }
}

function Get-ConfiguredTarget([string]$Folder) {
    $target=Join-Path $Folder '.j18-build\custom-bootloader'
    $manifest=Get-Content -LiteralPath (Join-Path $target 'firmware\manifest.json') -Raw | ConvertFrom-Json
    if ((Get-Sha256 (Join-Path $target 'board.json')) -ne $manifest.board_config_sha256) {
        throw 'Custom settings changed. Use L then C to compile them first.'
    }
    foreach ($entry in $manifest.profile_sha256.PSObject.Properties) {
        if ((Get-Sha256 (Join-Path $target ('arduino-profile\hardware\custom\samd\'+$entry.Name))) -ne $entry.Value) {
            throw "Configured profile differs from its manifest: $($entry.Name)"
        }
    }
    return $target
}

function Move-MenuSettingsFile([string]$Temporary,[string]$Destination) {
    if (!('J18MenuStateFile' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class J18MenuStateFile {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileEx(string source, string destination, uint flags);
}
'@
    }
    # Same-directory atomic replacement without File.Replace's metadata merge.
    if (![J18MenuStateFile]::MoveFileEx($Temporary,$Destination,9)) {
        throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }
}

function Save-MenuSettings {
    $temporary=$null
    try {
        $folder=Split-Path -Parent $menuSettingsPath
        $null=New-Item -ItemType Directory -Force $folder
        $path=$menuSettingsPath
        $values=[ordered]@{schema=1;sketch=$selected.Name;board=$Board;bootloader=$loader;
            bootloader_file=$localLoader;build_only=$checkOnly}
        $content=$values | ConvertTo-Json
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and [IO.File]::ReadAllText($path).Trim() -eq $content.Trim()) { return }
        $temporary=$path+'.'+$PID+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
        [IO.File]::WriteAllText($temporary,$content,[Text.UTF8Encoding]::new($false))
        for ($attempt=0;$attempt -lt 4;$attempt++) {
            try { Move-MenuSettingsFile $temporary $path; return }
            catch { if ($attempt -eq 3) { throw }; Start-Sleep -Milliseconds (50*($attempt+1)) }
        }
    } catch {
        Write-Warning ('Could not save menu settings; the menu will keep running with your current selections. Last saved settings are retained. '+$_.Exception.Message)
    } finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) {
            try { [IO.File]::Delete($temporary) } catch { Write-Warning ('Could not remove settings temporary file: '+$temporary) }
        }
    }
}

function Invoke-MenuAction([string]$Action,[string]$Folder,[string]$Loader,[string]$LocalFile,[bool]$CheckOnly,[bool]$Force) {
    if ($Action -eq 'probe') { Invoke-SwdProbe $Folder $CheckOnly ([bool]$InspectOnly) ([bool]$ProbeNoCompare) ([bool]$ProbeUnderReset) ([bool]$ProbeIdentifyImages); return }
    if ($Action -eq 'install-profile') { Invoke-ProfileInstall $Profile $ProfileSource $CheckOnly; return }
    $script:scriptRoot=$Folder
    $build=Join-Path $Folder '.j18-build'
    Assert-ManagedBuildDirectory $build $Folder
    $firmware=Join-Path $build 'firmware'
    $null=New-Item -ItemType Directory -Force $firmware
    if ($Action -ne 'app') {
        # Combined mode follows the board profile; separate mode follows B.
        $choice=if ($Action -eq 'combined') { if ($Board -in @('custom','target')) { $Board } else { 'core' } } else { $Loader }
        $source=Select-LoaderFile $choice $LocalFile $firmware
        $target=Join-Path $firmware 'samd21_sam_ba.bin'
        # Hold the same launcher lock while selecting the static image.
        $lock=[IO.File]::Open((Join-Path $build 'launcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        try {
            if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($target)) {
                Copy-Item -LiteralPath $source -Destination $target -Force
            }
        } finally { $lock.Dispose() }
    }
    $arguments=@()
    switch ($Action) {
        'combined' { $arguments+='--combined' }
        'boot' { $arguments+='--bootloader-only' }
        'app' { $arguments+='--sketch-only' }
    }
    if ($CheckOnly) { $arguments+='--build-only' }
    if ($Force -and $Action -ne 'boot') { $arguments+='--rebuild' }
    $oldSketch=$env:J18_SKETCH_DIR; $oldPause=$env:J18_NO_PAUSE
    $oldBoard=$env:J18_BOARD; $oldProfile=$env:J18_PROFILE_ROOT
    $oldSignature=$env:J18_PROFILE_SIGNATURE
    try {
        $env:J18_SKETCH_DIR=$Folder; $env:J18_NO_PAUSE='1'
        $env:J18_BOARD=if ($Board -eq 'target') { 'custom' } else { $Board }
        $env:J18_PROFILE_SIGNATURE=''
        if ($Board -eq 'target') {
            $target=Get-ConfiguredTarget $Folder
            $env:J18_PROFILE_ROOT=Join-Path $target 'arduino-profile'
            $env:J18_PROFILE_SIGNATURE=Get-Sha256 (Join-Path $target 'firmware\manifest.json')
        } else {
            $env:J18_PROFILE_ROOT=Join-Path $repo 'bootloader\arduino-profile'
            if ($Board -eq 'custom') { $env:J18_PROFILE_SIGNATURE=Get-Sha256 (Join-Path $repo 'bootloader\firmware\manifest.json') }
        }
        & $batch @arguments
        if ($LASTEXITCODE) { throw "Build/flash stopped with exit code $LASTEXITCODE." }
    } finally {
        $env:J18_SKETCH_DIR=$oldSketch; $env:J18_NO_PAUSE=$oldPause
        $env:J18_BOARD=$oldBoard; $env:J18_PROFILE_ROOT=$oldProfile
        $env:J18_PROFILE_SIGNATURE=$oldSignature
    }
}

function Get-MenuPython {
    $python=$null; $prefix=@()
    if ($env:J18_PYTHON -and (Test-Path -LiteralPath $env:J18_PYTHON -PathType Leaf)) { $python=$env:J18_PYTHON }
    if (!$python) {
        $py=Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) { $python=$py.Source; $prefix=@('-3') }
    }
    if (!$python) {
        $py=Get-Command python.exe -ErrorAction SilentlyContinue
        if ($py) { $python=$py.Source }
    }
    if (!$python) { throw 'Python 3.10+ is needed for diagnostics/profile installation. Install Python or set J18_PYTHON.' }
    & $python @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
    if ($LASTEXITCODE) { throw 'Python 3.10+ is required.' }
    return [pscustomobject]@{Executable=$python;Arguments=$prefix}
}

function Get-MenuArduinoPackage {
    $cli=$env:J18_ARDUINO_CLI
    if (!$cli) { $cli=Find-Tool 'arduino-cli.exe' @((Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe')) 'arduino-cli' }
    if (!$cli) { $cli=Install-ArduinoCli }
    $data=(Invoke-Tool $cli @('config','get','directories.data') -Quiet).Trim()
    if (!$data) { throw 'Cannot determine Arduino package directory.' }
    $core=Join-Path $data 'packages\arduino\hardware\samd\1.8.14'
    if (!(Test-Path -LiteralPath $core)) {
        $null=Invoke-Tool $cli @('core','update-index')
        $null=Invoke-Tool $cli @('core','install','arduino:samd@1.8.14')
    }
    return (Join-Path $data 'packages\arduino')
}

function Invoke-ProfileInstall([string]$Id,[string]$Source,[bool]$CheckOnly) {
    $runtime=Get-MenuPython; $python=$runtime.Executable; $prefix=@($runtime.Arguments)
    $cli=$env:J18_ARDUINO_CLI
    if (!$cli) {
        $candidates=@((Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'),
            (Join-Path $env:ProgramFiles 'Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'))
        $cli=Find-Tool 'arduino-cli.exe' $candidates 'arduino-cli'
    }
    if (!$cli) { $cli=Install-ArduinoCli }
    $arguments=@((Join-Path $PSScriptRoot 'install_board_profile.py'),'--profile',$Id,'--arduino-cli',$cli)
    if ($Source) { $arguments+=@('--source',$Source) }
    if ($ProfileSketchbook) { $arguments+=@('--sketchbook',$ProfileSketchbook) }
    if ($CheckOnly) { $arguments+='--check-only' }
    & $python @prefix @arguments
    if ($LASTEXITCODE) { throw 'Arduino profile installation/check failed; see the preceding message.' }
}

function Invoke-SwdProbe([string]$Folder,[bool]$CheckOnly,[bool]$Inspect,[bool]$NoCompare=$false,[bool]$UnderReset=$false,[bool]$Identify=$false) {
    $runtime=Get-MenuPython; $python=$runtime.Executable; $prefix=@($runtime.Arguments)
    $openocd=$env:J18_OPENOCD
    if (!$openocd) {
        $candidates=@((Join-Path ${env:ProgramFiles(x86)} 'OpenOCD\bin\openocd.exe'),(Join-Path $env:ProgramFiles 'OpenOCD\bin\openocd.exe'))
        $openocd=Find-Tool 'openocd.exe' $candidates 'openocd'
    }
    if (!$openocd) { $openocd=Install-OpenOcd }
    if (!$CheckOnly) { Show-StlinkUsbStatus -RequireConnected -InstallMissing -OpenOcdPath $openocd }
    $arguments=@((Join-Path $PSScriptRoot 'swd_probe.py'),'--platform','windows','--sketch',$Folder,'--openocd',$openocd,'--adapter','stlink')
    $arguments+=@('--app-offset',$ProbeAppOffset,'--board-map',$(if ($NoCompare) { 'generic' } else { $ProbeMap }))
    if ($CheckOnly) { $arguments+='--check-only' }
    if ($Inspect) { $arguments+='--inspect-only' }
    if ($NoCompare) { $arguments+='--no-compare' }
    if ($UnderReset) { $arguments+='--connect-under-reset' }
    if ($Identify) { $arguments+='--identify-images' }
    & $python @prefix @arguments
    if ($LASTEXITCODE) { throw 'SWD probe found a failure; see .j18-build/swd/swd-report.json and swd-probe.log.' }
}

$sketches=@(Get-AvailableSketches)
if (!$sketches.Count) { throw 'No sketches found under sketches/.' }
if ($List) { $sketches | ForEach-Object { Write-Output $_.Name }; exit 0 }
if (!$Mode) {
    $settingsPath=$menuSettingsPath
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        try {
            $saved=Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
            if ($saved.schema -ne 1) { throw 'Unsupported menu settings format.' }
            if (!$PSBoundParameters.ContainsKey('Sketch') -and $saved.sketch -in $sketches.Name) { $Sketch=$saved.sketch }
            if (!$PSBoundParameters.ContainsKey('Board') -and $saved.board -in @('zero','custom','target')) { $Board=$saved.board }
            if (!$PSBoundParameters.ContainsKey('Bootloader') -and $saved.bootloader -in @('current','core','github','custom','target','local')) { $Bootloader=$saved.bootloader }
            if (!$PSBoundParameters.ContainsKey('BootloaderFile') -and $saved.bootloader_file) { $BootloaderFile=$saved.bootloader_file }
            if (!$PSBoundParameters.ContainsKey('BuildOnly') -and $saved.build_only -is [bool]) { $BuildOnly=[bool]$saved.build_only }
            if ($Bootloader -eq 'local' -and (!$BootloaderFile -or !(Test-Path -LiteralPath $BootloaderFile -PathType Leaf))) {
                Write-Host 'Saved local bootloader is missing; choose B again. Using current static file.'
                $Bootloader='current'
            }
        } catch { Write-Host ('Ignoring unreadable menu settings: '+$_.Exception.Message) }
    }
}
$selected=@($sketches | Where-Object { $_.Name -eq $Sketch })
if ($Sketch -and !$selected.Count) { throw "Unknown sketch: $Sketch" }
if (!$selected.Count) { $selected=@($sketches | Where-Object { $_.Name -eq 'Blink' }) }
if (!$selected.Count) { $selected=@($sketches[0]) }
$selected=$selected[0]
if ($Mode) {
    try { Invoke-MenuAction $Mode $selected.Path $Bootloader $BootloaderFile ([bool]$BuildOnly) ([bool]$Rebuild); exit 0 }
    catch { Write-Host ('FAILED: '+$_.Exception.Message) -ForegroundColor Red; exit 1 }
}

$checkOnly=[bool]$BuildOnly
$loader=$Bootloader; $localLoader=$BootloaderFile
:MenuLoop while ($true) {
    Clear-Host
    Save-MenuSettings
    Write-Host 'SAMD21 CUSTOM BUILD AND FLASH'
    $boardLabel=if ($Board -eq 'target') { 'Configured custom J18 profile (selected sketch folder)' } elseif ($Board -eq 'custom') { 'Custom J18 Native USB (repository defaults)' } else { 'Arduino Zero Native USB / M0+ (arduino:samd:arduino_zero_native)' }
    Write-Host "Board profile: $boardLabel"
    Write-Host "Sketch: $($selected.Name)"
    $loaderLabel=@{current='Current static binary';core='Official installed Arduino core';github='Official GitHub ArduinoCore-samd';custom='Custom repository defaults';target='Configured custom loader for selected sketch';local='Local .bin'}[$loader]
    Write-Host "Bootloader: $loaderLabel"
    $customFolder=Join-Path $selected.Path '.j18-build\custom-bootloader'
    $sourceLabel=Join-Path $repo 'bootloader'
    try {
        $selectionFile=Join-Path $customFolder 'selection.json'
        if (Test-Path -LiteralPath $selectionFile) { $sourceLabel=(Get-Content -LiteralPath $selectionFile -Raw | ConvertFrom-Json).source }
    } catch { $sourceLabel='Invalid saved source selection; review L configuration' }
    Write-Host "Custom bootloader source: $sourceLabel"
    Write-Host ('Mode: '+$(if ($checkOnly) { 'BUILD / CHECK ONLY - no hardware access' } else { 'FLASH - options 1-4 program the connected board' }))
    Write-Host "Files: $($selected.Path)\.j18-build\firmware"
    Write-Host ''
    $verb=if ($checkOnly) { 'BUILD/CHECK' } else { 'FLASH' }
    Write-Host "$verb ACTIONS (reuses binaries; missing app / changed profile compiles automatically):"
    Write-Host "1  $verb combined image: board profile's bootloader + selected sketch"
    Write-Host '   Writes one .with_bootloader.bin at 0x0000; Zero = Arduino, Custom = custom.'
    Write-Host "2  $verb two files: selected bootloader at 0x0000, then sketch at 0x2000"
    Write-Host '   Use this for GitHub, Custom, or a locally replaced bootloader.'
    Write-Host '   Bootloader selector B applies to actions 2 and 3.'
    Write-Host "3  $verb bootloader only at 0x0000; leaves sketch bytes intact"
    Write-Host "4  $verb sketch only at 0x2000; leaves bootloader intact"
    Write-Host ''
    Write-Host 'SETTINGS / FILES (these do not flash):'
    Write-Host 'S  Choose sketch        P  Choose board profile        B  Choose bootloader'
    Write-Host 'T  Switch FLASH <-> BUILD/CHECK ONLY'
    Write-Host 'R  Recompile sketch for selected board profile, without flashing'
    Write-Host 'L  Configure + compile custom bootloader for this sketch (no flashing)'
    Write-Host 'G  Rebuild repository DEFAULT Custom loader + profile (B option 4; no flashing)'
    Write-Host 'Q  Install/update Arduino board profile (T = preview only; no flashing)'
    Write-Host 'E  Arduino profile workflow: build + install and/or export a release ZIP'
    Write-Host 'I  Load a sketch-only .bin into this sketch''s static firmware folder'
    Write-Host 'O  Open firmware folder     0  Exit'
    Write-Host ''
    Write-Host 'D  SWD PROBE: chip / flash / CPU / startup (no flashing; halts CPU)'
    Write-Host '   T = BUILD/CHECK prepares probe files only, without hardware access.'
    $choice=(Read-Host 'Choose').Trim().ToUpperInvariant()
    try {
        switch ($choice) {
            '0' { exit 0 }
            'E' {
                Write-Host '1 Rebuild + install repository DEFAULT profile'
                Write-Host '2 Rebuild + install selected sketch CUSTOM profile'
                Write-Host '3 Export repository DEFAULT profile folder + ZIP'
                Write-Host '4 Export selected sketch CUSTOM profile folder + ZIP'
                Write-Host '5 Rebuild + install + export repository DEFAULT profile'
                Write-Host '6 Rebuild + install + export selected sketch CUSTOM profile'
                Write-Host 'BUILD/CHECK mode previews this workflow without changes. No option flashes.'
                $pick=(Read-Host 'Workflow number').Trim()
                if ($pick -notin @('1','2','3','4','5','6')) { throw 'Invalid workflow selection.' }
                $runtime=Get-MenuPython; $prefix=@($runtime.Arguments)
                $arguments=@((Join-Path $repo 'tools/arduino_profile_workflow.py'))
                $target=if ($pick -in @('2','4','6')) { 'sketch' } else { 'default' }
                $arguments+=@('--target',$target)
                if ($target -eq 'sketch') { $arguments+=@('--sketch',$selected.Path) }
                if ($pick -in @('1','2','5','6')) { $arguments+='--build'; $arguments+='--install' }
                if ($pick -in @('3','4','5','6')) { $arguments+='--export' }
                if ($ProfileSketchbook) { $arguments+=@('--sketchbook',$ProfileSketchbook) }
                if ($checkOnly) { $arguments+='--check-only' }
                elseif ($pick -in @('1','2','5','6')) { $arguments+=@('--arduino-package',(Get-MenuArduinoPackage)) }
                & $runtime.Executable @prefix @arguments
                if ($LASTEXITCODE) { throw 'Arduino profile workflow failed; review the preceding message.' }
                if (!$checkOnly -and $pick -in @('1','2','5','6')) {
                    $Board=$loader=if ($target -eq 'sketch') { 'target' } else { 'custom' }
                }
            }
            'Q' {
                $catalog=Get-Content -LiteralPath (Join-Path $repo 'arduino-profiles.json') -Raw | ConvertFrom-Json
                $profiles=@($catalog.profiles.PSObject.Properties)
                for ($i=0;$i -lt $profiles.Count;$i++) { Write-Host ("{0}  {1}" -f ($i+1),$profiles[$i].Value.name) }
                Write-Host 'F  Another profile folder containing hardware/<vendor>/<architecture>'
                Write-Host 'C  Configured custom profile for selected sketch'
                $pick=(Read-Host 'Profile number or F').Trim().ToUpperInvariant()
                if ($pick -eq 'C') {
                    $target=Get-ConfiguredTarget $selected.Path
                    Invoke-ProfileInstall 'custom' (Join-Path $target 'arduino-profile') $checkOnly
                } elseif ($pick -eq 'F') {
                    $source=(Read-Host 'Profile root folder').Trim('"')
                    if (!$source) { throw 'A profile source folder is required.' }
                    Invoke-ProfileInstall 'custom' $source $checkOnly
                } else {
                    $number=0
                    if (![int]::TryParse($pick,[ref]$number) -or $number -lt 1 -or $number -gt $profiles.Count) { throw 'Invalid profile selection.' }
                    Invoke-ProfileInstall $profiles[$number-1].Name '' $checkOnly
                }
            }
            'D' {
                Write-Host '1 Full SWD probe: inspect flash/CPU, then software reset and startup checkpoints'
                Write-Host '2 Inspect only: halt/read/resume; no startup reset'
                Write-Host '3 General SAMD21 inspection: no local image comparisons, generic pin names, no reset'
                Write-Host '4 ST-LINK recovery: NRST -> SAMD RESET wire REQUIRED; resets, generic inspection, no flashing'
                Write-Host '5 Full probe WITH wired ST-LINK NRST: image checks + fault frame + startup checkpoints; no flashing'
                Write-Host '6 Identify installed bootloader/sketch among local compiled images; choose reset/no-reset connection'
                $pick=(Read-Host 'Probe routine number').Trim()
                if ($pick -notin @('1','2','3','4','5','6')) { throw 'Invalid probe selection.' }
                $wired=$pick -in @('4','5')
                if ($pick -eq '6') {
                    Write-Host '1 Normal connection; no reset wire used'
                    Write-Host '2 Wired ST-LINK NRST -> SAMD RESET; resets during connection'
                    $connection=(Read-Host 'Connection number').Trim()
                    if ($connection -notin @('1','2')) { throw 'Invalid connection selection.' }
                    $wired=$connection -eq '2'
                }
                Invoke-SwdProbe $selected.Path $checkOnly ($pick -notin @('1','5')) ($pick -in @('3','4','6')) $wired ($pick -eq '6')
            }
            'P' {
                Write-Host '1 Arduino Zero Native USB / SAMD21 M0+ (stock Arduino pin mapping)'
                Write-Host '2 Custom J18 Native USB (J18 pin mapping and aliases)'
                Write-Host '3 Configured custom J18 profile (selected sketch folder; build with L first)'
                $pick=(Read-Host 'Board profile number').Trim()
                if ($pick -eq '1') { $Board='zero' }
                elseif ($pick -eq '2') { $Board='custom' }
                elseif ($pick -eq '3') { $Board='target' }
                else { throw 'Invalid board profile selection.' }
                Write-Host 'The next sketch action automatically rebuilds if its saved profile differs.'
            }
            'S' {
                for ($i=0;$i -lt $sketches.Count;$i++) { Write-Host ("{0}  {1}" -f ($i+1),$sketches[$i].Name) }
                $number=0
                if ([int]::TryParse((Read-Host 'Sketch number'),[ref]$number) -and $number -ge 1 -and $number -le $sketches.Count) {
                    $selected=$sketches[$number-1]
                } else { throw 'Invalid sketch selection.' }
                continue MenuLoop
            }
            'B' {
                Write-Host '1 Current static file (GitHub fallback if missing)'
                Write-Host '2 Official installed Arduino core 1.8.14'
                Write-Host '3 Official GitHub ArduinoCore-samd 1.8.14'
                Write-Host '4 Repository DEFAULT Custom bootloader (rebuild with G)'
                Write-Host '5 Local bootloader .bin'
                Write-Host '6 Configured custom bootloader for selected sketch (build with L -> C)'
                $pick=(Read-Host 'Bootloader number').Trim()
                $loaders=@{'1'='current';'2'='core';'3'='github';'4'='custom';'5'='local';'6'='target'}
                if (!$loaders.ContainsKey($pick)) { throw 'Invalid bootloader selection.' }
                $loader=$loaders[$pick]
                if ($loader -eq 'local') { $localLoader=(Read-Host 'Full path to bootloader .bin').Trim('"') }
                continue MenuLoop
            }
            'T' { $checkOnly=!$checkOnly; continue MenuLoop }
            'O' {
                $folder=Join-Path $selected.Path '.j18-build\firmware'
                $null=New-Item -ItemType Directory -Force $folder
                Invoke-Item -LiteralPath $folder
                continue MenuLoop
            }
            'I' {
                $source=(Read-Host 'Full path to sketch-only .bin linked at 0x2000').Trim('"')
                if (!(Test-SketchImage ([IO.File]::ReadAllBytes($source)))) { throw 'Invalid sketch-only image.' }
                $build=Join-Path $selected.Path '.j18-build'; Assert-ManagedBuildDirectory $build $selected.Path
                $folder=Join-Path $build 'firmware'; $null=New-Item -ItemType Directory -Force $folder
                $lock=[IO.File]::Open((Join-Path $build 'launcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
                try {
                    $signature=if ($Board -eq 'target') { Get-Sha256 (Join-Path (Get-ConfiguredTarget $selected.Path) 'firmware\manifest.json') } elseif ($Board -eq 'custom') { Get-Sha256 (Join-Path $repo 'bootloader\firmware\manifest.json') } else { '' }
                    $boardConfig=if ($Board -eq 'target') { (Get-Content -LiteralPath (Join-Path (Get-ConfiguredTarget $selected.Path) 'board.json') -Raw | ConvertFrom-Json) } elseif ($Board -eq 'custom') { Get-Content -LiteralPath (Join-Path $repo 'bootloader\board.json') -Raw | ConvertFrom-Json } else { $null }
                    $target=Join-Path $folder 'firmware.sketch.bin'
                    if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($target)) {
                        Copy-Item -LiteralPath $source -Destination $target -Force
                    }
                    $fqbn=if ($Board -in @('custom','target')) { 'custom:samd:custom_j18' } else { 'arduino:samd:arduino_zero_native' }
                    Save-StaticSketchReceipt $build $fqbn $target 'manually imported for selected board profile' '' $signature $boardConfig
                }
                finally { $lock.Dispose() }
                Write-Host 'Stored sketch image; no flashing performed.'
            }
            'L' {
                $runtime=Get-MenuPython
                $package=Get-MenuArduinoPackage
                $result=Join-Path $selected.Path '.j18-build\custom-bootloader\menu-result.json'
                $prefix=@($runtime.Arguments)
                & $runtime.Executable @prefix (Join-Path $repo 'tools\bootloader_config.py') --sketch $selected.Path --arduino-package $package --result-file $result
                if ($LASTEXITCODE) { throw 'Custom bootloader configuration/build stopped; saved settings are retained.' }
                $record=Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
                if ($record.built) {
                    $Board='target'; $loader='target'
                    Write-Host 'Selected configured custom board profile and bootloader. Next sketch action recompiles if needed.'
                }
            }
            'G' {
                $runtime=Get-MenuPython; $prefix=@($runtime.Arguments)
                $package=Get-MenuArduinoPackage
                & $runtime.Executable @prefix (Join-Path $repo 'tools\bootloader_config.py') --rebuild-default --arduino-package $package
                if ($LASTEXITCODE) { throw 'Default bootloader rebuild failed; selections unchanged.' }
                $Board='custom'; $loader='custom'
                Write-Host 'Selected repository Custom board profile and B option 4. Next sketch action rebuilds if the profile changed.'
            }
            'R' { Invoke-MenuAction 'app' $selected.Path $loader $localLoader $true $true }
            default {
                $modes=@{'1'='combined';'2'='separate';'3'='boot';'4'='app'}
                if (!$modes.ContainsKey($choice)) { throw 'Choose one of the listed options.' }
                Invoke-MenuAction $modes[$choice] $selected.Path $loader $localLoader $checkOnly $false
            }
        }
    } catch { Write-Host ('FAILED: '+$_.Exception.Message) -ForegroundColor Red }
    $null=Read-Host 'Press Enter to return to the menu'
}
