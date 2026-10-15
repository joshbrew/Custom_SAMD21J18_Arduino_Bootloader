@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "J18_BATCH_PATH=%~f0"
set "J18_ACTION=flash"
set "J18_FORCE_REBUILD=0"
set "J18_FLASH_MODE=separate"
:args
if "%~1"=="" goto run
if /I "%~1"=="--check" goto argcheck
if /I "%~1"=="--build-only" goto argbuild
if /I "%~1"=="--rebuild" goto argrebuild
if /I "%~1"=="--bootloader-only" goto argboot
if /I "%~1"=="--sketch-only" goto argapp
if /I "%~1"=="--combined" goto argcombined
echo Usage: build_and_flash_j18_separate_bootloader.bat [--check^|--build-only] [--rebuild] [--bootloader-only^|--sketch-only^|--combined]
echo Default: use .j18-build\firmware; compile if missing, profile changed, or --rebuild.
exit /b 2
:argcheck
set "J18_ACTION=check"
shift
goto args
:argbuild
set "J18_ACTION=build"
shift
goto args
:argrebuild
set "J18_FORCE_REBUILD=1"
shift
goto args
:argboot
set "J18_FLASH_MODE=boot"
shift
goto args
:argapp
set "J18_FLASH_MODE=app"
shift
goto args
:argcombined
set "J18_FLASH_MODE=combined"
shift
goto args
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
$flashMode = $env:J18_FLASH_MODE
$toolStore = Join-Path $env:LOCALAPPDATA 'SAMD21Custom\Tools'
$fqbn = 'arduino:samd:arduino_zero_native'
if ($env:J18_BOARD -eq 'custom') { $fqbn='custom:samd:custom_j18' }
elseif ($env:J18_BOARD -and $env:J18_BOARD -ne 'zero') { throw 'Unsupported J18_BOARD; choose zero or custom.' }
$transcribing = $false
$buildLock = $null

function Get-Sha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

# Source template embedded into the self-contained separate-bootloader batch.
# BEGIN J18_SKETCH_CACHE
function Move-J18AtomicFile([string]$Source,[string]$Destination) {
    if (!('J18AtomicFile' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class J18AtomicFile {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileEx(string source, string destination, uint flags);
}
'@
    }
    for ($attempt=0;$attempt -lt 4;$attempt++) {
        if ([J18AtomicFile]::MoveFileEx($Source,$Destination,9)) { return }
        $failure=[ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
        if ($attempt -eq 3) { throw $failure }
        Start-Sleep -Milliseconds (50*($attempt+1))
    }
}

function Get-SketchBuildFingerprint {
    param([string]$SketchRoot, [string]$MainName, [string]$Board,
          [string]$Properties, [string]$Arduino, [string]$CliVersion,
          [string[]]$ExtraArguments, [string[]]$DependencyRoots, [string[]]$ToolchainRoots,
          [string]$StagedSketch, [string]$RunRoot)
    $parts = New-Object 'System.Collections.Generic.List[string]'
    $parts.Add('J18 sketch cache schema 1')
    $parts.Add($SketchRoot); $parts.Add($MainName); $parts.Add($Board)
    $parts.Add($CliVersion); $parts.Add((Get-Sha256 $Arduino))
    foreach ($arg in $ExtraArguments) { $parts.Add($arg) }
    # Unexpanded properties preserve recipe placeholders. Ignore per-run paths.
    foreach ($line in (($Properties -split "`n") | Sort-Object)) {
        if ($line -match '^(build\.(path|source\.path|project_name|cache\.path)|sketch_path|extra\.time\.[^=]+)=') { continue }
        $parts.Add($line.TrimEnd().Replace($StagedSketch,'{SKETCH}').Replace($RunRoot,'{RUN}'))
    }
    function Add-CacheSourceFile {
        param([string]$Path,[string]$Relative)
        $hash = Get-Sha256 $Path
        if ($StagedSketch -ne $SketchRoot) {
            $stagedFile = Join-Path $StagedSketch $Relative
            if (!(Test-Path -LiteralPath $stagedFile -PathType Leaf) -or (Get-Sha256 $stagedFile) -ne $hash) {
                throw "Sketch changed while being staged: $Relative. Rerun before flashing."
            }
        }
        $parts.Add($Relative); $parts.Add($hash)
    }
    function Add-CacheTree {
        param([string]$Root)
        $parts.Add('TREE ' + $Root)
        if (!(Test-Path -LiteralPath $Root -PathType Container)) { $parts.Add('MISSING'); return }
        foreach ($file in (Get-ChildItem -LiteralPath $Root -Recurse -File | Sort-Object FullName)) {
            $relative = $file.FullName.Substring($Root.TrimEnd('\','/').Length).TrimStart('\','/')
            if ($relative -match '(^|[\\/])(\.git|\.build|\.j18-build|__pycache__|node_modules)([\\/]|$)') { continue }
            if ($Root -eq (Join-Path $SketchRoot 'src') -or $Root -eq (Join-Path $SketchRoot 'data')) {
                Add-CacheSourceFile $file.FullName ((Split-Path -Leaf $Root) + '/' + $relative)
            } else { $parts.Add($relative); $parts.Add((Get-Sha256 $file.FullName)) }
        }
    }
    $extensions = @('.ino','.pde','.h','.hpp','.hh','.c','.cpp','.cc','.cxx','.s','.ipp','.tpp')
    foreach ($file in (Get-ChildItem -LiteralPath $SketchRoot -File | Sort-Object Name)) {
        if ($file.Extension.ToLowerInvariant() -in $extensions -or $file.Name -in @('sketch.json','sketch.yaml')) {
            Add-CacheSourceFile $file.FullName $file.Name
        }
    }
    foreach ($name in @('src','data','libraries')) { Add-CacheTree (Join-Path $SketchRoot $name) }
    foreach ($root in ($DependencyRoots | Where-Object { $_ } | Sort-Object -Unique)) { Add-CacheTree $root }
    # Installed toolchains are versioned packages. Check their complete file set,
    # sizes and timestamps, plus compiler-driver contents, without rereading all
    # of the GCC multilib archives on every flash. Use --rebuild after deliberately
    # editing an installed toolchain while preserving file metadata.
    foreach ($root in ($ToolchainRoots | Where-Object { $_ } | Sort-Object -Unique)) {
        $parts.Add('TOOLCHAIN ' + $root)
        if (!(Test-Path -LiteralPath $root -PathType Container)) { $parts.Add('MISSING'); continue }
        foreach ($file in (Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName)) {
            $parts.Add($file.FullName); $parts.Add([string]$file.Length); $parts.Add([string]$file.LastWriteTimeUtc.Ticks)
            if ($file.Name -in @('arm-none-eabi-gcc.exe','arm-none-eabi-g++.exe')) { $parts.Add((Get-Sha256 $file.FullName)) }
        }
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        # JSON makes the boundary between names, arguments and hashes unambiguous.
        $json = ConvertTo-Json -InputObject $parts.ToArray() -Compress
        if (Test-Path -LiteralPath $RunRoot -PathType Container) {
            [IO.File]::WriteAllText((Join-Path $RunRoot 'sketch-inputs.json'),$json,[Text.Encoding]::UTF8)
        }
        $bytes = [Text.Encoding]::UTF8.GetBytes($json)
        return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Test-SketchImage {
    param([byte[]]$Bytes)
    if ($Bytes.Length -lt 8 -or $Bytes.Length -gt (262144-8192)) { return $false }
    $sp = [BitConverter]::ToUInt32($Bytes,0)
    $pc = [BitConverter]::ToUInt32($Bytes,4)
    return ($sp -gt 0x20000000 -and $sp -le 0x20008000 -and ($sp -band 7) -eq 0 -and
        ($pc -band 1) -eq 1 -and $pc -ge 0x2000 -and $pc -lt (8192+$Bytes.Length))
}

function Find-CachedSketchBuild {
    param([string]$SketchRoot, [string]$Fingerprint, [string]$Board)
    $cache = Join-Path $SketchRoot '.j18-build'
    if (!(Test-Path -LiteralPath $cache -PathType Container)) { return $null }
    foreach ($run in (Get-ChildItem -LiteralPath $cache -Directory | Sort-Object Name -Descending)) {
        $receipt = Join-Path $run.FullName 'sketch-build.json'
        if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) { continue }
        try {
            $record = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
            if ($record.schema -ne 1 -or $record.completed -isnot [bool] -or !$record.completed -or $record.fqbn -ne $Board -or
                ($Fingerprint -and $record.inputs_sha256 -ne $Fingerprint)) { continue }
            if (!$record.app_file -or [IO.Path]::GetFileName($record.app_file) -cne $record.app_file -or
                $record.app_file -notlike '*.ino.bin') { continue }
            $path = Join-Path (Join-Path $run.FullName 'firmware') $record.app_file
            if (!(Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Sha256 $path) -ne $record.app_sha256) { continue }
            if (!(Test-SketchImage ([IO.File]::ReadAllBytes($path)))) { continue }
            return [pscustomobject]@{ Path=$path; Sha256=$record.app_sha256 }
        } catch { continue } # Incomplete, damaged and old runs do not select firmware.
    }
    return $null
}

function Save-SketchBuildReceipt {
    param([string]$RunRoot, [string]$Fingerprint, [string]$Board, [string]$AppPath)
    if (!(Test-SketchImage ([IO.File]::ReadAllBytes($AppPath)))) { throw 'Sketch vectors do not match the 0x2000 application layout.' }
    $record = [ordered]@{schema=1; completed=$true; fqbn=$Board; inputs_sha256=$Fingerprint;
        app_file=[IO.Path]::GetFileName($AppPath); app_sha256=(Get-Sha256 $AppPath);
        created_utc=[DateTime]::UtcNow.ToString('o')}
    $temp = Join-Path $RunRoot 'sketch-build.json.tmp'
    [IO.File]::WriteAllText($temp,($record | ConvertTo-Json),[Text.Encoding]::UTF8)
    [IO.File]::Move($temp,(Join-Path $RunRoot 'sketch-build.json'))
}

function Assert-ManagedBuildDirectory {
    param([string]$BuildRoot,[string]$SketchRoot)
    $expected = [IO.Path]::GetFullPath((Join-Path $SketchRoot '.j18-build')).TrimEnd('\','/')
    if ([IO.Path]::GetFullPath($BuildRoot).TrimEnd('\','/') -ne $expected) {
        throw 'Refusing a build folder outside this sketch.'
    }
    $ancestor = $expected
    $sketchPath = [IO.Path]::GetFullPath($SketchRoot).TrimEnd('\','/')
    while ($ancestor.Length -ge $sketchPath.Length) {
        if (Test-Path -LiteralPath $ancestor) {
            $entry = Get-Item -LiteralPath $ancestor -Force
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Build path contains a symlink/junction: $ancestor"
            }
        }
        if ($ancestor -eq $sketchPath) { break }
        $ancestor = Split-Path -Parent $ancestor
    }
    if (Test-Path -LiteralPath $BuildRoot) {
        $pending = New-Object 'System.Collections.Generic.Stack[string]'
        $pending.Push($BuildRoot)
        while ($pending.Count) {
            $path = $pending.Pop()
            $item = Get-Item -LiteralPath $path -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Build folder contains a symlink/junction: $path"
            }
            foreach ($child in (Get-ChildItem -LiteralPath $path -Force)) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw "Build folder contains a symlink/junction: $($child.FullName)"
                }
                if ($child.PSIsContainer) { $pending.Push($child.FullName) }
            }
        }
    }
}

function Reset-StaticCompilerFolders {
    param([string]$BuildRoot,[string]$SketchRoot)
    Assert-ManagedBuildDirectory $BuildRoot $SketchRoot
    # Only compiler scratch/staged source is cleared. Keep the editable firmware
    # and bootloader, as well as the last successful sketch until a rebuild passes.
    foreach ($name in @('compiler','compiler-output','sketch')) {
        $target = [IO.Path]::GetFullPath((Join-Path $BuildRoot $name))
        if (!$target.StartsWith(([IO.Path]::GetFullPath($BuildRoot).TrimEnd('\','/')+'\'),[StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to clear a compiler folder outside the build directory.'
        }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    }
}

function Save-StaticSketchReceipt {
    param([string]$BuildRoot,[string]$Board,[string]$AppPath,[string]$Origin,[string]$Fingerprint,[string]$ProfileSignature=$env:J18_PROFILE_SIGNATURE,$BoardConfig=$null)
    if (!(Test-SketchImage ([IO.File]::ReadAllBytes($AppPath)))) { throw 'Invalid static sketch image.' }
    if (!$BoardConfig -and $Board -eq 'custom:samd:custom_j18' -and $env:J18_PROFILE_ROOT) {
        $manifestPath=Join-Path (Split-Path -Parent $env:J18_PROFILE_ROOT) 'firmware\manifest.json'
        $BoardConfig=(Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json).board
    }
    $record = [ordered]@{schema=2; completed=$true; fqbn=$Board; inputs_sha256=$Fingerprint; profile_signature=[string]$ProfileSignature;
        app_file='firmware.sketch.bin'; app_sha256=(Get-Sha256 $AppPath);
        board_config=$BoardConfig;
        origin=$Origin; created_utc=[DateTime]::UtcNow.ToString('o')}
    $destination = Join-Path $BuildRoot 'sketch-build.json'
    $temp = $destination+'.tmp'
    [IO.File]::WriteAllText($temp,($record | ConvertTo-Json -Depth 12),[Text.Encoding]::UTF8)
    Move-J18AtomicFile $temp $destination
}
# END J18_SKETCH_CACHE

function Find-LocalBootloader {
    param([string]$SketchRoot,[string]$LegacyBootloader)
    $staticBootloader = Join-Path $SketchRoot '.j18-build\firmware\samd21_sam_ba.bin'
    if (Test-Path -LiteralPath $staticBootloader -PathType Leaf) { return $staticBootloader }
    $besideSketch = Join-Path $SketchRoot 'samd21_sam_ba.bin'
    if (Test-Path -LiteralPath $besideSketch -PathType Leaf) { return $besideSketch }
    foreach ($folderName in @('build')) {
        $folder = Join-Path $SketchRoot $folderName
        if (!(Test-Path -LiteralPath $folder -PathType Container)) { continue }
        $files = @(Get-ChildItem -LiteralPath $folder -Recurse -File -Filter 'samd21_sam_ba.bin' |
            Sort-Object FullName)
        if ($files.Count -eq 0) { continue }
        $firstHash = Get-Sha256 $files[0].FullName
        foreach ($file in $files) {
            if ((Get-Sha256 $file.FullName) -ne $firstHash) {
                throw "Different samd21_sam_ba.bin files found under $folder. Place the intended one beside the sketch."
            }
        }
        return $files[0].FullName
    }
    if ($LegacyBootloader -and (Test-Path -LiteralPath $LegacyBootloader -PathType Leaf)) { return $LegacyBootloader }
    return $null
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
    $sketchCandidates = @($toolFolder, (Split-Path -Parent $toolFolder))
    if ($env:J18_SKETCH_DIR) {
        if (!(Test-Path -LiteralPath $env:J18_SKETCH_DIR -PathType Container)) { throw 'J18_SKETCH_DIR is not a sketch folder.' }
        $sketchCandidates = @([IO.Path]::GetFullPath($env:J18_SKETCH_DIR))
    }
    foreach ($candidate in $sketchCandidates) {
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
        $localBootloader = Find-LocalBootloader $scriptRoot
        if ($localBootloader) { Write-Host "Local bootloader: $localBootloader" }
        else { Write-Host 'No local bootloader. A build/flash run will download it from ArduinoCore-samd.' }
        Write-Host 'Check complete. No installation, compilation, or hardware access was performed.'
        exit 0
    }

    $runRoot = Join-Path $scriptRoot '.j18-build'
    Assert-ManagedBuildDirectory $runRoot $scriptRoot
    New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
    $buildLock = [IO.File]::Open((Join-Path $runRoot 'launcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    Start-Transcript -Path (Join-Path $runRoot 'build-and-flash.log') -Force | Out-Null
    $transcribing = $true
    $outputDir = Join-Path $runRoot 'firmware'
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    $appCopy = Join-Path $outputDir 'firmware.sketch.bin'
    $bootCopy = Join-Path $outputDir 'samd21_sam_ba.bin'
    $legacyBootloader = $null
    if (!$openocd) { $openocd = Install-OpenOcd }
    Write-Host "OpenOCD: $openocd"
    $ocdVersion = Invoke-Tool $openocd @('--version')
    if ($ocdVersion -notmatch 'Open On-Chip Debugger\s+(\d+)\.(\d+)\.(\d+)' -or
        ([version]($Matches[1] + '.' + $Matches[2] + '.' + $Matches[3]) -lt [version]'0.12.0')) {
        Write-Host 'Installing the supported OpenOCD version.'
        $openocd = Install-OpenOcd
        $null = Invoke-Tool $openocd @('--version')
    }

    if ($action -eq 'flash') { Show-StlinkUsbStatus -RequireConnected -InstallMissing -OpenOcdPath $openocd }
    if ($flashMode -ne 'boot') {
        $needsProfileBuild=$false
        $receiptPath=Join-Path $runRoot 'sketch-build.json'
        if ((Test-Path -LiteralPath $appCopy -PathType Leaf) -and $env:J18_FORCE_REBUILD -ne '1') {
            if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
                $savedReceipt=Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
                $cachedBoard=$savedReceipt.fqbn
                if ($cachedBoard -ne $fqbn -or [string]$savedReceipt.profile_signature -ne [string]$env:J18_PROFILE_SIGNATURE) {
                    Write-Host "Board profile changed: $cachedBoard -> $fqbn. Recompiling automatically in the same static folder."
                    $needsProfileBuild=$true
                }
            } elseif ($fqbn -ne 'arduino:samd:arduino_zero_native') {
                Write-Host 'Static sketch has no board receipt for Custom. Recompiling automatically for the selected profile.'
                $needsProfileBuild=$true
            }
        }
        if (!(Test-Path -LiteralPath $appCopy -PathType Leaf) -and $env:J18_FORCE_REBUILD -ne '1' -and
            !(Test-Path -LiteralPath (Join-Path $runRoot 'sketch-build.json') -PathType Leaf)) {
            # One-time import: retain existing dated folders but stop creating them.
            $legacy = Find-CachedSketchBuild $scriptRoot '' $fqbn
            if ($legacy) {
                Write-Host "Importing existing compilation into the static folder: $($legacy.Path)"
                Copy-Item -LiteralPath $legacy.Path -Destination $appCopy
                if ((Get-Sha256 $appCopy) -ne $legacy.Sha256) { throw 'Legacy binary changed during import.' }
                $legacyBootloader = Join-Path (Split-Path -Parent $legacy.Path) 'samd21_sam_ba.bin'
                Save-StaticSketchReceipt $runRoot $fqbn $appCopy ('imported: '+$legacy.Path) ''
            }
        }
        if ($env:J18_FORCE_REBUILD -eq '1' -or $needsProfileBuild -or !(Test-Path -LiteralPath $appCopy -PathType Leaf)) {
            if ($env:J18_FORCE_REBUILD -eq '1') { Write-Host 'Forced rebuild requested; keeping the selected bootloader.' }
            elseif ($needsProfileBuild) { Write-Host "Compiling for the newly selected board profile: $fqbn" }
            else { Write-Host 'No static sketch binary; building once.' }
            if (!$arduino) { $arduino = Install-ArduinoCli }
            Write-Host "Arduino CLI: $arduino"
            $cliVersion = Invoke-Tool $arduino @('version')
            if ($cliVersion -notmatch 'Version:\s*(\d+)\.(\d+)\.(\d+)' -or [int]$Matches[1] -lt 1) {
                $arduino = Install-ArduinoCli
                $cliVersion = Invoke-Tool $arduino @('version')
            }
            Reset-StaticCompilerFolders $runRoot $scriptRoot
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
            $cliArgs=@()
            if ($fqbn -eq 'custom:samd:custom_j18') {
                $profile=$env:J18_PROFILE_ROOT
                if (!$profile) { throw 'J18_PROFILE_ROOT must point to the repository bootloader/arduino-profile folder.' }
                $manifestPath=Join-Path (Split-Path -Parent $profile) 'firmware\manifest.json'
                $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
                foreach ($entry in $manifest.profile_sha256.PSObject.Properties) {
                    $file=Join-Path $profile ('hardware\custom\samd\'+$entry.Name)
                    if ((Get-Sha256 $file) -ne $entry.Value) { throw "Custom profile differs from its manifest: $($entry.Name)" }
                }
                $data=(Invoke-Tool $arduino @('config','get','directories.data') -Quiet).Trim()
                $user=(Invoke-Tool $arduino @('config','get','directories.user') -Quiet).Trim()
                if (!$data -or !$user) { throw 'Cannot determine Arduino package/library directories.' }
                $cliConfig=Join-Path $runRoot 'cli.json'
                @{directories=@{data=$data;user=$profile;downloads=(Join-Path $runRoot 'downloads')}} |
                    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $cliConfig -Encoding UTF8
                $cliArgs=@('--config-file',$cliConfig)
                $profileLibraries=Join-Path $user 'libraries'
            }

            # Always stage only Arduino source, src and data. Compiling the original
            # folder would recursively copy prior .j18-build runs into the new build.
            $sketchPath = Join-Path $runRoot ('sketch\' + $mainIno.BaseName)
            New-Item -ItemType Directory -Path $sketchPath -Force | Out-Null
            $sourceExtensions = @('.ino','.pde','.h','.hpp','.hh','.c','.cpp','.cc','.cxx','.s','.S','.ipp','.tpp')
            Get-ChildItem -LiteralPath $scriptRoot -File | Where-Object {
                $_.Extension -in $sourceExtensions -or $_.Name -in @('sketch.json','sketch.yaml')
            } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sketchPath }
            foreach ($subfolder in @('src','data')) {
                $sourceFolder = Join-Path $scriptRoot $subfolder
                if (Test-Path -LiteralPath $sourceFolder -PathType Container) {
                    Copy-Item -LiteralPath $sourceFolder -Destination $sketchPath -Recurse
                }
            }
            Write-Host "Staged sketch in an Arduino-compatible folder: $sketchPath"
            $extraArgs = @()
            if ($fqbn -eq 'custom:samd:custom_j18' -and (Test-Path -LiteralPath $profileLibraries -PathType Container)) {
                $extraArgs+=@('--libraries',$profileLibraries)
            }
            $libraries = Join-Path $scriptRoot 'libraries'
            if (Test-Path -LiteralPath $libraries -PathType Container) { $extraArgs += @('--libraries', $libraries) }
            $overrideHeader = Join-Path $scriptRoot 'J18BuildOverrides.h'
            $selectionHeader=Join-Path $runRoot 'BuildSelection.h'
            # Zero needs core-wide USB LED overrides even with a custom loader.
            # The matching custom profile already disables them; repeating is safe.
            # Image identity below follows the selected static loader, overriding
            # the profile default for verification. Clock/SWD settings stay in the profile.
            $selectionText="#pragma once`n#include <Arduino.h>`n#undef PIN_LED_TXL`n#undef PIN_LED_RXL`n#define J18_CORE_USB_LEDS_DISABLED 1`n"
            if (Test-Path -LiteralPath $bootCopy -PathType Leaf) {
                [byte[]]$referenceBoot=[IO.File]::ReadAllBytes($bootCopy)
                if ($referenceBoot.Length -lt 8 -or $referenceBoot.Length -gt 8192) { throw 'Invalid static bootloader size for build identity.' }
                [uint32]$crc=0xffffffffL
                foreach ($value in $referenceBoot) {
                    $crc=$crc -bxor [uint32]$value
                    for ($bit=0;$bit -lt 8;$bit++) {
                        if ($crc -band 1) { $crc=($crc -shr 1) -bxor [uint32]0xedb88320L }
                        else { $crc=$crc -shr 1 }
                    }
                }
                $crc=$crc -bxor [uint32]0xffffffffL
                $selectionText+="#undef J18_CUSTOM_BOOTLOADER_BYTES`n#undef J18_CUSTOM_BOOTLOADER_CRC32`n#define J18_CUSTOM_BOOTLOADER_BYTES $($referenceBoot.Length)u`n#define J18_CUSTOM_BOOTLOADER_CRC32 0x$($crc.ToString('x8'))ul`n"
            }
            if (Test-Path -LiteralPath $overrideHeader -PathType Leaf) {
                $selectionText+='#include "'+$overrideHeader.Replace('\','/')+'"'+"`n"
            }
            [IO.File]::WriteAllText($selectionHeader,$selectionText,[Text.Encoding]::UTF8)
            $headerFlag = 'compiler.cpp.extra_flags=-include "' + $selectionHeader.Replace('\','/') + '"'
            $vendorPath = Join-Path $scriptRoot 'src\SdFat'
            if (Test-Path -LiteralPath $vendorPath -PathType Container) {
                $headerFlag += ' -I"' + $vendorPath.Replace('\','/') + '"'
            }
            $extraArgs += @('--build-property', $headerFlag)
            Write-Host 'Applying USB LED overrides and available static bootloader identity to C++ compilation.'
            Write-Host 'Checking sketch build settings...'
            $properties = Invoke-Tool $arduino ($cliArgs + @('compile','--fqbn',$fqbn,'--show-properties=unexpanded') + $extraArgs + @($sketchPath)) -Quiet
            $platformPath = $null
            $bootloaderFile = $null
            $linkerScript = $null
            foreach ($line in ($properties -split "`n")) {
                if ($line -match '^runtime\.platform\.path=(.*)$') { $platformPath = $Matches[1].Trim() }
                if ($line -match '^bootloader\.file=(.*)$') { $bootloaderFile = $Matches[1].Trim() }
                if ($line -match '^build\.ldscript=(.*)$') { $linkerScript = $Matches[1].Trim() }
            }
            if ($linkerScript -notlike '*flash_with_bootloader.ld') {
                throw 'Could not confirm that the sketch is linked for the 0x2000 Arduino bootloader offset.'
            }
            $compileOutput = Join-Path $runRoot 'compiler-output'
            $buildDir = Join-Path $runRoot 'compiler'
            New-Item -ItemType Directory -Path $compileOutput -Force | Out-Null
            $dependencyRoots = @($platformPath)
            $toolchainRoots = @()
            foreach ($line in ($properties -split "`n")) {
                if ($line -match '^runtime\.tools\.(arm-none-eabi-gcc|CMSIS|CMSIS-Atmel)\.path=(.+)$') {
                    $toolchainRoots += $Matches[2].Trim()
                }
            }
            $userDirectory = (Invoke-Tool $arduino @('config','get','directories.user') -Quiet).Trim()
            if (!$userDirectory) { throw 'Cannot determine the Arduino library directory for build reuse.' }
            $dependencyRoots += Join-Path $userDirectory 'libraries'
            $cacheInputs = @{SketchRoot=$scriptRoot; MainName=$mainIno.Name; Board=$fqbn;
                Properties=$properties; Arduino=$arduino; CliVersion=$cliVersion;
                ExtraArguments=$extraArgs; DependencyRoots=$dependencyRoots; ToolchainRoots=$toolchainRoots;
                StagedSketch=$sketchPath; RunRoot=$runRoot}
            Write-Host 'Recording build input hashes...'
            $fingerprint = Get-SketchBuildFingerprint @cacheInputs
            Write-Host 'Compiling sketch...'
            $null = Invoke-Tool $arduino ($cliArgs + @('compile','--fqbn',$fqbn,'--build-path',$buildDir,
                '--output-dir',$compileOutput) + $extraArgs + @($sketchPath))
            if ((Get-SketchBuildFingerprint @cacheInputs) -ne $fingerprint) {
                throw 'Build inputs changed during compilation. Rerun with --rebuild.'
            }
            $appFiles = @(Get-ChildItem -LiteralPath $compileOutput -File -Filter '*.ino.bin')
            if ($appFiles.Count -ne 1 -or !(Test-SketchImage ([IO.File]::ReadAllBytes($appFiles[0].FullName)))) {
                throw 'Expected one valid sketch-only binary linked at 0x2000.'
            }
            $elfFiles=@(Get-ChildItem -LiteralPath $compileOutput -File -Filter '*.ino.elf')
            if ($elfFiles.Count -ne 1) { throw 'Expected one matching ELF for the compiled sketch.' }
            # Publish only a successfully compiled/validated sketch. Preserve bootloader.
            $tempApp = $appCopy+'.tmp'
            Copy-Item -LiteralPath $appFiles[0].FullName -Destination $tempApp
            Move-J18AtomicFile $tempApp $appCopy
            $elfCopy=Join-Path $outputDir 'firmware.sketch.elf'
            Copy-Item -LiteralPath $elfFiles[0].FullName -Destination ($elfCopy+'.tmp')
            Move-J18AtomicFile ($elfCopy+'.tmp') $elfCopy
            Save-StaticSketchReceipt $runRoot $fqbn $appCopy 'compiled' $fingerprint
        } else {
            if (!(Test-SketchImage ([IO.File]::ReadAllBytes($appCopy)))) {
                throw 'The static sketch binary is invalid. Replace/delete it or use --rebuild.'
            }
            Write-Host "Reusing static sketch: $appCopy"
            Write-Host 'Skipping compilation. Source edits require --rebuild; board-profile changes rebuild automatically.'
        }
    }
    $bootCopy = Join-Path $outputDir 'samd21_sam_ba.bin'
    if ($flashMode -ne 'app') {
        $bootPath = Find-LocalBootloader $scriptRoot $legacyBootloader
        if ($bootPath) {
            Write-Host "Using local bootloader: $bootPath"
        } else {
            $bootUrl = 'https://raw.githubusercontent.com/arduino/ArduinoCore-samd/1.8.14/bootloaders/zero/samd21_sam_ba.bin'
            $bootPath = Join-Path $outputDir 'samd21_sam_ba.bin.download'
            Write-Host 'Downloading the official Arduino Zero bootloader...'
            Write-Host $bootUrl
            Invoke-WebRequest -UseBasicParsing -Uri $bootUrl -OutFile $bootPath
            if ((Get-Sha256 $bootPath) -ne '89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b') { throw 'Pinned bootloader download checksum failed.' }
        }
        [byte[]]$bootBytes = [IO.File]::ReadAllBytes($bootPath)
        if ($bootBytes.Length -lt 8 -or $bootBytes.Length -gt 8192) { throw 'Bootloader does not fit the 8 KB region.' }
        $bootSp = [BitConverter]::ToUInt32($bootBytes,0)
        $bootReset = [BitConverter]::ToUInt32($bootBytes,4)
        if ($bootSp -le 0x20000000 -or $bootSp -gt 0x20008000 -or ($bootSp -band 3) -ne 0 -or
            ($bootReset -band 1) -eq 0 -or $bootReset -ge $bootBytes.Length) { throw 'Invalid bootloader vectors.' }
        if ([IO.Path]::GetFullPath($bootPath) -ne [IO.Path]::GetFullPath($bootCopy)) {
            [IO.File]::WriteAllBytes($bootCopy,$bootBytes)
        }
        Write-Host "Bootloader file: $bootCopy"
        Write-Host ('Bootloader SHA256: ' + (Get-Sha256 $bootCopy))
    }
    if ($flashMode -ne 'boot') {
        [byte[]]$appBytes = [IO.File]::ReadAllBytes($appCopy)
        if (!(Test-SketchImage $appBytes)) { throw 'Invalid sketch-only image; expected origin 0x2000.' }
        Write-Host "Sketch-only file: $appCopy"
        Write-Host ('Sketch SHA256: ' + (Get-Sha256 $appCopy))
    }
    if ($flashMode -eq 'combined') {
        [byte[]]$combined = New-Object byte[] (8192+$appBytes.Length)
        for ($i=0; $i -lt 8192; $i++) { $combined[$i]=255 }
        [Array]::Copy($bootBytes,0,$combined,0,$bootBytes.Length)
        [Array]::Copy($appBytes,0,$combined,8192,$appBytes.Length)
        $combinedPath = Join-Path $outputDir 'firmware.with_bootloader.bin'
        [IO.File]::WriteAllBytes($combinedPath,$combined)
        Write-Host "Combined image: $combinedPath"
        Write-Host ('Combined SHA256: ' + (Get-Sha256 $combinedPath))
    }
    $config = @'
set SCRIPT_FILE [info script]
regsub -all {\\} $SCRIPT_FILE {/} SCRIPT_FILE
set SCRIPT_DIR [file dirname $SCRIPT_FILE]
cd $SCRIPT_DIR
if {![info exists PHASE]} { error "Set PHASE to boot or app" }
if {$PHASE eq "boot"} {
    set BIN_FILE samd21_sam_ba.bin
    set FLASH_ADDR 0x00000000
} elseif {$PHASE eq "app"} {
    set BIN_FILE firmware.sketch.bin
    set FLASH_ADDR 0x00002000
} elseif {$PHASE eq "combined"} {
    set BIN_FILE firmware.with_bootloader.bin
    set FLASH_ADDR 0x00000000
} else { error "Invalid PHASE: use boot, app or combined" }
if {![file exists $BIN_FILE]} { error "Missing binary: $BIN_FILE" }
# Native driver avoids deprecated interface/stlink-dap.cfg and old HLA configs.
adapter driver st-link
# Required USB discovery filters, retained from the official ST-LINK interface.
st-link vid_pid 0x0483 0x3744 0x0483 0x3748 0x0483 0x374b 0x0483 0x374d 0x0483 0x374e 0x0483 0x374f 0x0483 0x3752 0x0483 0x3753 0x0483 0x3754 0x0483 0x3755 0x0483 0x3757
if {[catch {transport select swd}]} {
    # Compatibility fallback for stock OpenOCD 0.12.
    transport select dapdirect_swd
}
set CHIPNAME at91samd21j18
source [find target/at91samdXX.cfg]
adapter speed 100
reset_config none
cortex_m reset_config sysresetreq
if {[info exists J18_CHECK_ONLY]} { shutdown }
init
reset halt
if {$PHASE ne "app"} {
    at91samd bootloader 0
    reset halt
}
flash write_image erase $BIN_FILE $FLASH_ADDR bin
verify_image $BIN_FILE $FLASH_ADDR bin
if {$PHASE ne "app"} {
    at91samd bootloader 8192
}
reset halt
verify_image $BIN_FILE $FLASH_ADDR bin
if {[info exists J18_RUN_AFTER] && $J18_RUN_AFTER} {
    echo "Image verified. Starting firmware."
    reset run
} else {
    echo "Bootloader verified; leaving CPU halted for the sketch phase."
}
shutdown
'@
    $configPath = Join-Path $outputDir 'flash_separate.cfg'
    [IO.File]::WriteAllText($configPath,$config,[Text.Encoding]::ASCII)
    $ocdBase = Split-Path -Parent (Split-Path -Parent $openocd)
    $scriptsDir = @((Join-Path $ocdBase 'share\openocd\scripts'), (Join-Path $ocdBase 'scripts')) |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ 'target\at91samdXX.cfg') } |
        Select-Object -First 1
    $ocdArgs = @()
    if ($scriptsDir) { $ocdArgs = @('-s',$scriptsDir) }
    $phases = @(switch ($flashMode) {
        'separate' { @('boot','app') }
        'boot' { @('boot') }
        'app' { @('app') }
        'combined' { @('combined') }
        default { throw 'Invalid flash mode.' }
    })
    foreach ($phase in $phases) {
        $null = Invoke-Tool $openocd ($ocdArgs + @('-c',"set PHASE $phase",'-c','set J18_CHECK_ONLY 1','-f',$configPath))
    }
    if ($action -eq 'build') {
        if ($flashMode -eq 'separate') { Write-Host 'BUILD COMPLETE. Both OpenOCD phases checked; hardware was not accessed.' }
        else { Write-Host "BUILD COMPLETE. OpenOCD $flashMode mode checked; hardware was not accessed." }
    } else {
        for ($index=0; $index -lt $phases.Count; $index++) {
            $phase=$phases[$index]
            $runAfter=[int]($index -eq ($phases.Count-1))
            Write-Host "Flashing $phase image..."
            $null = Invoke-Tool $openocd ($ocdArgs + @('-c',"set PHASE $phase",'-c',"set J18_RUN_AFTER $runAfter",'-f',$configPath))
        }
        Write-Host 'FLASH AND VERIFICATION SUCCEEDED.'
    }
    Write-Host "Build files and log: $runRoot"
} catch {
    Write-Host ''
    Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'No further steps will run. Check missing libraries, USB drivers, wiring, and the log.'
    exit 1
} finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    if ($buildLock) { $buildLock.Dispose() }
}
exit 0
