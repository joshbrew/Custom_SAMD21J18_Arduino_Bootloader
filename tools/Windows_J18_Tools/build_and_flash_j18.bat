@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "J18_BATCH_PATH=%~f0"
set "J18_ACTION=flash"
if "%~1"=="" goto run
if /I "%~1"=="--check" set "J18_ACTION=check"
if /I "%~1"=="--build-only" set "J18_ACTION=build"
if /I "%~1"=="--check" goto run
if /I "%~1"=="--build-only" goto run
echo Usage: build_and_flash_j18.bat [--check^|--build-only]
echo Default: install missing tools, compile nearby sketch, flash via ST-LINK.
exit /b 2
:run
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:J18_BATCH_PATH); $m='# J18_POWERSHELL_PAYLOAD'; & ([ScriptBlock]::Create($s.Substring($s.LastIndexOf($m)+$m.Length)))"
set "J18_EXIT=%ERRORLEVEL%"
if not "%J18_NO_PAUSE%"=="1" pause
exit /b %J18_EXIT%
# J18_POWERSHELL_PAYLOAD
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$toolFolder = Split-Path -Parent $env:J18_BATCH_PATH
$scriptRoot = $toolFolder
$action = $env:J18_ACTION
$toolStore = Join-Path $env:LOCALAPPDATA 'SAMD21Custom\Tools'
$fqbn = 'arduino:samd:arduino_zero_native'
$transcribing = $false

function Get-Sha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Invoke-Tool {
    param([string]$Executable, [string[]]$Arguments, [switch]$Quiet)
    $savedPreference = $ErrorActionPreference
    $lines = New-Object 'System.Collections.Generic.List[string]'
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $lineText = $_.ToString()
            $lines.Add($lineText)
            if (!$Quiet) { Write-Host $lineText }
        }
        $nativeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    $text = $lines -join "`n"
    if ($Quiet -and $nativeExit -ne 0 -and $text) { Write-Host $text }
    if ($nativeExit -ne 0) {
        throw "Command failed with exit code ${nativeExit}: $Executable $($Arguments -join ' ')"
    }
    return $text
}

function Get-StlinkUsbDevices {
    Add-Type -AssemblyName System.Management
    $searcher = New-Object System.Management.ManagementObjectSearcher "SELECT Name,PNPDeviceID,ConfigManagerErrorCode,Service FROM Win32_PnPEntity WHERE PNPDeviceID LIKE 'USB%VID_0483%'"
    try {
        @($searcher.Get() | Where-Object {
            $_.PNPDeviceID -match 'PID_(3744|3748|374A|374B|374D|374E|374F|3752|3753|3754|3755|3757)'
        } | ForEach-Object {
            # Copy values before disposing the WMI result/searcher.
            [pscustomobject]@{ Name = $_.Name; PNPDeviceID = $_.PNPDeviceID
                ConfigManagerErrorCode = [int]$_.ConfigManagerErrorCode; Service = $_.Service }
        })
    } finally { $searcher.Dispose() }
}

function Show-StlinkDriverHelp {
    Write-Host 'Official ST-LINK driver download:'
    Write-Host 'https://www.st.com/en/development-tools/stsw-link009.html#get-software'
    Write-Host 'You can also put the extracted package in stsw-link009 beside this batch, then rerun.'
}

function Find-StlinkDriverInfs {
    param([string]$Root, [object[]]$Devices)
    if (!(Test-Path -LiteralPath $Root -PathType Container)) { return }
    foreach ($inf in (Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.inf')) {
        $content = [IO.File]::ReadAllText($inf.FullName)
        if ($content -notmatch '(?i)STMicroelectronics') { continue }
        $matchesDevice = $false
        foreach ($id in [regex]::Matches($content,'(?i)USB\\VID_0483&PID_[0-9A-F]{4}(?:&MI_[0-9A-F]{2})?')) {
            foreach ($device in $Devices) {
                if ($device.PNPDeviceID.StartsWith($id.Value + '\',[StringComparison]::OrdinalIgnoreCase)) {
                    $matchesDevice = $true
                }
            }
        }
        if (!$matchesDevice) { continue }
        $catalog = [regex]::Match($content,'(?im)^\s*CatalogFile\.NTAMD64\s*=\s*([^;\r\n]+)')
        if (!$catalog.Success) {
            $catalog = [regex]::Match($content,'(?im)^\s*CatalogFile\s*=\s*([^;\r\n]+)')
        }
        if (!$catalog.Success) { continue }
        $catPath = Join-Path $inf.DirectoryName $catalog.Groups[1].Value.Trim().Trim('"')
        if (!(Test-Path -LiteralPath $catPath -PathType Leaf)) { continue }
        $signature = Get-AuthenticodeSignature -LiteralPath $catPath
        if ($signature.Status -ne 'Valid' -or
            $signature.SignerCertificate.Subject -notmatch 'STMicroelectronics|Microsoft Windows Hardware Compatibility Publisher') {
            Write-Host "Skipping driver package with an unverified catalog: $($inf.FullName)"
            continue
        }
        # Windows also verifies that the INF and package files match the signed catalog.
        $inf.FullName
    }
}

function Install-MissingStlinkDriver {
    param([object[]]$Devices, [string]$OpenOcdPath)
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw 'Automatic ST-LINK driver installation currently supports x64 Windows only.'
    }
    Write-Host 'Windows reports a missing ST-LINK driver (code 28). Preparing installation...'
    $driverCache = Join-Path $toolStore 'stsw-link009'
    $roots = @( (Join-Path $toolFolder 'stsw-link009'), (Join-Path $scriptRoot 'stsw-link009'), $driverCache )
    if ($OpenOcdPath) {
        $openOcdRoot = Split-Path -Parent (Split-Path -Parent $OpenOcdPath)
        $roots += Join-Path $openOcdRoot 'drivers\ST-Link'
    }
    $infs = @()
    foreach ($candidate in $roots) {
        $infs = @(Find-StlinkDriverInfs $candidate $Devices)
        if ($infs.Count) { break }
    }
    if (!$infs.Count) {
        New-Item -ItemType Directory -Path $driverCache -Force | Out-Null
        $localZip = @($toolFolder, $scriptRoot) | ForEach-Object {
            $zipFolder = $_
            foreach ($zipName in @('stsw-link009.zip','en.stsw-link009.zip')) {
                $candidate = Join-Path $zipFolder $zipName
                if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate }
            }
        } | Select-Object -First 1
        if ($localZip) { $archive = $localZip }
        else {
            $archive = Join-Path $driverCache ('download-' + [guid]::NewGuid().ToString('N') + '.zip')
            Write-Host 'Downloading STSW-LINK009 from STMicroelectronics...'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://www.st.com/resource/en/driver/stsw-link009.zip' -OutFile $archive -TimeoutSec 45
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $unpack = Join-Path $driverCache ('package-' + [guid]::NewGuid().ToString('N'))
        [IO.Compression.ZipFile]::ExtractToDirectory($archive,$unpack)
        $infs = @(Find-StlinkDriverInfs $unpack $Devices)
        if (!$infs.Count) { throw 'No matching ST-LINK driver with a valid signed catalog was found in the package.' }
    }
    New-Item -ItemType Directory -Path $driverCache -Force | Out-Null
    $driverLog = Join-Path $driverCache ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '.log')
    $infLiterals = ($infs | ForEach-Object { "'" + $_.Replace("'","''") + "'" }) -join ','
    $logLiteral = "'" + $driverLog.Replace("'","''") + "'"
    # Pass data in an encoded PowerShell command; never interpolate paths into cmd.exe.
    $installScript = @'
$ErrorActionPreference = 'Continue'
$driverLog = __LOG__
$driverInfs = @(__INFS__)
$result = 0
foreach ($driverInf in $driverInfs) {
    & "$env:SystemRoot\System32\pnputil.exe" /add-driver $driverInf /install 2>&1 | Out-File -LiteralPath $driverLog -Append -Encoding utf8
    $code = $LASTEXITCODE
    if ($code -eq 3010) { if ($result -eq 0) { $result = 3010 } }
    elseif ($code -ne 0) { $result = $code; break }
}
exit $result
'@
    $installScript = $installScript.Replace('__LOG__',$logLiteral).Replace('__INFS__',$infLiterals)
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($installScript))
    Write-Host 'Installing the matching signed ST-LINK driver. Approve the Windows administrator prompt.'
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $process = Start-Process -FilePath $powershellExe -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if (Test-Path -LiteralPath $driverLog) { Get-Content -LiteralPath $driverLog | ForEach-Object { Write-Host $_ } }
    Write-Host "Driver installation log: $driverLog"
    if ($process.ExitCode -eq 3010) { throw 'Driver installation requires a Windows restart. Restart, reconnect ST-LINK, and rerun the batch.' }
    if ($process.ExitCode -ne 0) { throw "Driver installation failed (exit $($process.ExitCode)). See $driverLog" }
}

function Show-StlinkUsbStatus {
    param([switch]$RequireConnected, [switch]$InstallMissing, [string]$OpenOcdPath)
    Write-Host 'Checking ST-LINK USB connection and driver...'
    try { $devices = @(Get-StlinkUsbDevices) }
    catch {
        Write-Host 'Windows USB inventory could not be read. OpenOCD will check the connection during flashing.'
        return
    }
    if (!$devices.Count) {
        Write-Host 'ST-LINK: not detected by Windows. Connect the adapter and rerun.'
        Show-StlinkDriverHelp
        if ($RequireConnected) { throw 'Connect the ST-LINK USB adapter, then rerun. No compilation or flashing has started.' }
        return
    }
    foreach ($device in $devices) {
        $driver = if ($device.Service) { $device.Service } else { 'none reported' }
        Write-Host ("ST-LINK USB: {0}; driver service: {1}; device error code: {2}" -f $device.Name,$driver,$device.ConfigManagerErrorCode)
    }
    $missing = @($devices | Where-Object { $_.ConfigManagerErrorCode -eq 28 })
    if ($missing.Count -and $InstallMissing) {
        try { Install-MissingStlinkDriver $missing $OpenOcdPath }
        catch { Show-StlinkDriverHelp; throw "Automatic ST-LINK driver installation could not finish: $($_.Exception.Message)" }
        # Allow enumeration to settle, then recheck once without repeating installation.
        Start-Sleep -Seconds 2
        Show-StlinkUsbStatus -RequireConnected:$RequireConnected
        return
    }
    if ($missing.Count) {
        Write-Host 'A driver is missing. A normal flash run will attempt to install it.'
        Show-StlinkDriverHelp
        if ($RequireConnected) { throw 'ST-LINK still reports a missing driver. Reconnect it and rerun; see README_WINDOWS.md.' }
        return
    }
    $healthy = @($devices | Where-Object { $_.ConfigManagerErrorCode -eq 0 })
    if (!$healthy.Count) {
        Write-Host 'Windows reports a device/driver problem. Check Device Manager.'
        Show-StlinkDriverHelp
        if ($RequireConnected) { throw 'ST-LINK USB driver/device is not ready. See README_WINDOWS.md.' }
    } else {
        Write-Host 'Windows detects the adapter. OpenOCD will confirm USB access during flashing.'
    }
}

function Find-Tool {
    param([string]$Name, [string[]]$Candidates, [string]$CacheName)
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    $cache = Join-Path $toolStore $CacheName
    if (Test-Path -LiteralPath $cache) {
        $found = Get-ChildItem -LiteralPath $cache -Recurse -File -Filter $Name |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

function Install-PortableTool {
    param([string]$CacheName, [string]$Version, [string]$Url,
          [string]$Sha256, [string]$ExecutableName)
    $destination = Join-Path $toolStore ($CacheName + '\' + $Version)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $archive = Join-Path $destination 'download.zip'
    Write-Host "Downloading $CacheName $Version from its official release..."
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $archive
    $actualHash = Get-Sha256 $archive
    if ($actualHash -ine $Sha256) { throw "SHA256 mismatch for $CacheName; installation stopped." }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $unpackDir = Join-Path $destination ('unpacked-' + [guid]::NewGuid().ToString('N'))
    [IO.Compression.ZipFile]::ExtractToDirectory($archive,$unpackDir)
    $exe = Get-ChildItem -LiteralPath $unpackDir -Recurse -File -Filter $ExecutableName |
        Select-Object -First 1
    if (!$exe) { throw "Archive did not contain $ExecutableName" }
    return $exe.FullName
}

function Install-ArduinoCli {
    Install-PortableTool 'arduino-cli' '1.5.1' `
        'https://github.com/arduino/arduino-cli/releases/download/v1.5.1/arduino-cli_1.5.1_Windows_64bit.zip' `
        'fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40' 'arduino-cli.exe'
}

function Install-OpenOcd {
    Install-PortableTool 'openocd' '0.12.0-7' `
        'https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v0.12.0-7/xpack-openocd-0.12.0-7-win32-x64.zip' `
        '6bfd3c97135aafef8affc9af1acf34fd0e2b9ca26044506f6abd7f95b7630052' 'openocd.exe'
}

try {
    if (![Environment]::Is64BitOperatingSystem) { throw 'This batch requires 64-bit Windows.' }
    $inoFiles = @()
    foreach ($candidate in @($toolFolder, (Split-Path -Parent $toolFolder))) {
        if (!$candidate) { continue }
        $inoFiles = @(Get-ChildItem -LiteralPath $candidate -File -Filter '*.ino')
        if ($inoFiles.Count) { $scriptRoot = $candidate; break }
    }
    if (!$inoFiles.Count) { throw 'No .ino found beside this batch or in its parent folder.' }
    Set-Location -LiteralPath $scriptRoot
    $folderName = Split-Path -Leaf $scriptRoot
    $mainIno = $inoFiles | Where-Object { $_.BaseName -eq $folderName } | Select-Object -First 1
    if (!$mainIno) {
        if ($inoFiles.Count -ne 1) {
            throw "Multiple .ino files found without ${folderName}.ino. Use the normal Arduino main-sketch naming convention."
        }
        $mainIno = $inoFiles[0]
    }
    Write-Host "Sketch: $($mainIno.FullName)"
    Write-Host "Board:  $fqbn"
    Write-Host 'ST-LINK SWDIO -> PA31, SWCLK -> PA30, common GND; target powered normally.'
    Write-Host 'Reset wire is not used.'

    $arduinoCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'),
        (Join-Path $env:ProgramFiles 'Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe')
    )
    $openocdCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'OpenOCD\bin\openocd.exe'),
        (Join-Path $env:ProgramFiles 'OpenOCD\bin\openocd.exe')
    )
    $arduino = Find-Tool 'arduino-cli.exe' $arduinoCandidates 'arduino-cli'
    $openocd = Find-Tool 'openocd.exe' $openocdCandidates 'openocd'
    # Optional explicit paths for portable or nonstandard tool installations.
    if ($env:J18_ARDUINO_CLI) {
        if (!(Test-Path -LiteralPath $env:J18_ARDUINO_CLI -PathType Leaf)) { throw 'J18_ARDUINO_CLI does not point to an executable.' }
        $arduino = $env:J18_ARDUINO_CLI
    }
    if ($env:J18_OPENOCD) {
        if (!(Test-Path -LiteralPath $env:J18_OPENOCD -PathType Leaf)) { throw 'J18_OPENOCD does not point to an executable.' }
        $openocd = $env:J18_OPENOCD
    }
    if ($action -eq 'check') {
        foreach ($entry in @(@('Arduino CLI', $arduino, 'version'), @('OpenOCD', $openocd, '--version'))) {
            if ($entry[1]) {
                Write-Host "$($entry[0]): $($entry[1])"
                $null = Invoke-Tool $entry[1] @($entry[2])
            } else { Write-Host "$($entry[0]): missing; a normal run will install a portable copy." }
        }
        Show-StlinkUsbStatus
        Write-Host 'Check complete. No installation, compilation, or hardware access was performed.'
        exit 0
    }

    $runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $runRoot = Join-Path $scriptRoot ('.j18-build\' + $runId)
    New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
    Start-Transcript -Path (Join-Path $runRoot 'build-and-flash.log') | Out-Null
    $transcribing = $true
    if (!$arduino) { $arduino = Install-ArduinoCli }
    if (!$openocd) { $openocd = Install-OpenOcd }
    Write-Host "Arduino CLI: $arduino"
    $cliVersion = Invoke-Tool $arduino @('version')
    if ($cliVersion -notmatch 'Version:\s*(\d+)\.(\d+)\.(\d+)' -or [int]$Matches[1] -lt 1) {
        Write-Host 'Installing the supported Arduino CLI version.'
        $arduino = Install-ArduinoCli
        $null = Invoke-Tool $arduino @('version')
    }
    Write-Host "OpenOCD: $openocd"
    $ocdVersion = Invoke-Tool $openocd @('--version')
    if ($ocdVersion -notmatch 'Open On-Chip Debugger\s+(\d+)\.(\d+)\.(\d+)' -or
        ([version]($Matches[1] + '.' + $Matches[2] + '.' + $Matches[3]) -lt [version]'0.12.0')) {
        Write-Host 'Installing the supported OpenOCD version.'
        $openocd = Install-OpenOcd
        $null = Invoke-Tool $openocd @('--version')
    }

    if ($action -eq 'flash') { Show-StlinkUsbStatus -RequireConnected -InstallMissing -OpenOcdPath $openocd }
    Write-Host 'Checking Arduino SAMD board support...'
    try { $coreList = Invoke-Tool $arduino @('core', 'list') }
    catch {
        Write-Host 'Initializing Arduino package indexes before installing board support.'
        $coreList = ''
    }
    if ($coreList -notmatch '(?m)^arduino:samd\s+1\.8\.14(?:\s|$)') {
        $null = Invoke-Tool $arduino @('core', 'update-index')
        $null = Invoke-Tool $arduino @('core', 'install', 'arduino:samd@1.8.14')
    }

    $sketchPath = $scriptRoot
    if ($mainIno.BaseName -ne $folderName) {
        # Arduino requires the main .ino filename to match its enclosing folder.
        # Stage source files without renaming or editing the originals.
        $sketchPath = Join-Path $runRoot ('sketch\' + $mainIno.BaseName)
        New-Item -ItemType Directory -Path $sketchPath -Force | Out-Null
        $sourceExtensions = @('.ino','.pde','.h','.hpp','.hh','.c','.cpp','.cc','.cxx','.s','.S','.ipp','.tpp')
        Get-ChildItem -LiteralPath $scriptRoot -File | Where-Object {
            $_.Extension -in $sourceExtensions -or $_.Name -eq 'sketch.json'
        } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sketchPath }
        foreach ($subfolder in @('src','data')) {
            $sourceFolder = Join-Path $scriptRoot $subfolder
            if (Test-Path -LiteralPath $sourceFolder -PathType Container) {
                Copy-Item -LiteralPath $sourceFolder -Destination $sketchPath -Recurse
            }
        }
        Write-Host "Staged sketch in an Arduino-compatible folder: $sketchPath"
    }
    $extraArgs = @()
    $libraries = Join-Path $scriptRoot 'libraries'
    if (Test-Path -LiteralPath $libraries -PathType Container) { $extraArgs = @('--libraries', $libraries) }
    $overrideHeader = Join-Path $scriptRoot 'J18BuildOverrides.h'
    if (Test-Path -LiteralPath $overrideHeader -PathType Leaf) {
        $headerFlag = 'compiler.cpp.extra_flags=-include "' + $overrideHeader.Replace('\','/') + '"'
        $vendorPath = Join-Path $scriptRoot 'src\SdFat'
        if (Test-Path -LiteralPath $vendorPath -PathType Container) {
            $headerFlag += ' -I"' + $vendorPath.Replace('\','/') + '"'
        }
        $extraArgs += @('--build-property', $headerFlag)
        Write-Host 'Applying sketch J18BuildOverrides.h to core and sketch C++ compilation.'
    }
    Write-Host 'Compiling sketch...'
    $properties = Invoke-Tool $arduino (@('compile','--fqbn',$fqbn,'--show-properties') + $extraArgs + @($sketchPath)) -Quiet
    $platformPath = $null
    $bootloaderFile = $null
    $linkerScript = $null
    foreach ($line in ($properties -split "`n")) {
        if ($line -match '^runtime\.platform\.path=(.*)$') { $platformPath = $Matches[1].Trim() }
        if ($line -match '^bootloader\.file=(.*)$') { $bootloaderFile = $Matches[1].Trim() }
        if ($line -match '^build\.ldscript=(.*)$') { $linkerScript = $Matches[1].Trim() }
    }
    if (!$platformPath -or !$bootloaderFile -or $linkerScript -notlike '*flash_with_bootloader.ld') {
        throw 'Could not confirm the standard Arduino Zero bootloader and linker layout.'
    }
    $bootPath = Join-Path (Join-Path $platformPath 'bootloaders') $bootloaderFile
    if ([IO.Path]::GetExtension($bootPath) -ine '.bin' -or !(Test-Path -LiteralPath $bootPath)) {
        throw "Expected a binary bootloader in the selected Arduino core: $bootPath"
    }
    $outputDir = Join-Path $runRoot 'firmware'
    $buildDir = Join-Path $runRoot 'compiler'
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    Write-Host 'Building firmware...'
    $null = Invoke-Tool $arduino (@('compile','--fqbn',$fqbn,'--build-path',$buildDir,
        '--output-dir',$outputDir,'--export-binaries') + $extraArgs + @($sketchPath))
    $appFiles = @(Get-ChildItem -LiteralPath $outputDir -File -Filter '*.ino.bin')
    if ($appFiles.Count -ne 1) { throw 'Expected exactly one freshly compiled sketch-only .ino.bin.' }
    [byte[]]$appBytes = [IO.File]::ReadAllBytes($appFiles[0].FullName)
    [byte[]]$bootBytes = [IO.File]::ReadAllBytes($bootPath)
    if ($appBytes.Length -lt 8 -or $bootBytes.Length -lt 8 -or $bootBytes.Length -gt 8192 -or
        ($appBytes.Length + 8192) -gt 262144) { throw 'Firmware sizes do not fit the SAMD21J18 bootloader layout.' }
    $appSp = [BitConverter]::ToUInt32($appBytes,0)
    $appReset = [BitConverter]::ToUInt32($appBytes,4)
    $bootSp = [BitConverter]::ToUInt32($bootBytes,0)
    $bootReset = [BitConverter]::ToUInt32($bootBytes,4)
    if ($appSp -le 0x20000000 -or $appSp -gt 0x20008000 -or ($appSp -band 7) -ne 0 -or ($appReset -band 1) -eq 0 -or
        $appReset -lt 0x2000 -or $appReset -ge (8192 + $appBytes.Length)) {
        throw 'Sketch vectors are not valid for an application linked at 0x2000.'
    }
    if ($bootSp -le 0x20000000 -or $bootSp -gt 0x20008000 -or ($bootSp -band 3) -ne 0 -or ($bootReset -band 1) -eq 0 -or
        $bootReset -ge $bootBytes.Length) { throw 'The Arduino core bootloader vectors are invalid.' }
    [byte[]]$combined = New-Object byte[] (8192 + $appBytes.Length)
    for ($i=0; $i -lt 8192; $i++) { $combined[$i] = 255 }
    [Array]::Copy($bootBytes,0,$combined,0,$bootBytes.Length)
    [Array]::Copy($appBytes,0,$combined,8192,$appBytes.Length)
    $combinedPath = Join-Path $outputDir 'firmware.with_bootloader.bin'
    [IO.File]::WriteAllBytes($combinedPath,$combined)
    Write-Host "Combined image: $combinedPath"
    Write-Host "Bootloader: $bootPath"
    Write-Host ('SHA256: ' + (Get-Sha256 $combinedPath))

    $config = @'
set SCRIPT_FILE [info script]
regsub -all {\\} $SCRIPT_FILE {/} SCRIPT_FILE
set SCRIPT_DIR [file dirname $SCRIPT_FILE]
cd $SCRIPT_DIR
set BIN_FILE firmware.with_bootloader.bin
if {[catch {find interface/stlink-dap.cfg} STLINK_CFG]} {
    set STLINK_CFG [find interface/stlink.cfg]
}
source $STLINK_CFG
if {[adapter name] ne "st-link"} {
    error "Native st-link driver required; use a matching OpenOCD installation"
}
if {[catch {transport select dapdirect_swd}]} {
    transport select swd
}
set CHIPNAME at91samd21j18
source [find target/at91samdXX.cfg]
adapter speed 100
reset_config none
cortex_m reset_config sysresetreq
if {[info exists J18_CHECK_ONLY]} { shutdown }
init
reset halt
at91samd bootloader 0
reset halt
flash write_image erase $BIN_FILE 0x00000000 bin
verify_image $BIN_FILE 0x00000000 bin
at91samd bootloader 8192
reset halt
verify_image $BIN_FILE 0x00000000 bin
echo "Image verified. Starting firmware."
reset run
shutdown
'@
    $configPath = Join-Path $outputDir 'flash_combined.cfg'
    [IO.File]::WriteAllText($configPath,$config,[Text.Encoding]::ASCII)
    $ocdBase = Split-Path -Parent (Split-Path -Parent $openocd)
    $scriptsDir = @((Join-Path $ocdBase 'share\openocd\scripts'), (Join-Path $ocdBase 'scripts')) |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ 'target\at91samdXX.cfg') } |
        Select-Object -First 1
    $ocdArgs = @()
    if ($scriptsDir) { $ocdArgs = @('-s',$scriptsDir) }
    # Validate configuration without initializing the adapter or accessing hardware.
    $null = Invoke-Tool $openocd ($ocdArgs + @('-c','set J18_CHECK_ONLY 1','-f',$configPath))
    if ($action -eq 'build') {
        Write-Host 'BUILD COMPLETE. OpenOCD configuration checked; hardware was not accessed.'
    } else {
        Write-Host 'Flashing the combined image through ST-LINK...'
        $null = Invoke-Tool $openocd ($ocdArgs + @('-f',$configPath))
        Write-Host 'FLASH AND VERIFICATION SUCCEEDED.'
    }
    Write-Host "Build files and log: $runRoot"
} catch {
    Write-Host ''
    Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'No further steps will run. Check missing libraries, USB drivers, wiring, and the log.'
    if ($transcribing) { Stop-Transcript | Out-Null }
    exit 1
}
if ($transcribing) { Stop-Transcript | Out-Null }
exit 0
