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
