param([string]$BatchPath,[string]$FixtureRoot)
$ErrorActionPreference='Stop'
$text=[IO.File]::ReadAllText($BatchPath)
$marker='# J18_POWERSHELL_PAYLOAD'
$text=$text.Substring($text.LastIndexOf($marker)+$marker.Length)
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([ScriptBlock]::Create($node.Extent.Text))
    }
}
function Assert-True($Value,[string]$Message) { if (!$Value) { throw $Message } }
$source=Join-Path $FixtureRoot 'Sketch'
$dependency=Join-Path $FixtureRoot 'Library'
$null=New-Item -ItemType Directory -Force $source,$dependency
$ino=Join-Path $source 'Sketch.ino'
[IO.File]::WriteAllText($ino,'void setup(){} void loop(){}')
$library=Join-Path $dependency 'Library.h'
[IO.File]::WriteAllText($library,'#define VALUE 1')
$cli=Join-Path $FixtureRoot 'cli.exe'
[IO.File]::WriteAllText($cli,'fake compiler identity; not executable')
$params=@{SketchRoot=$source; MainName='Sketch.ino'; Board='arduino:samd:arduino_zero_native';
    Properties="build.path=C:/tmp/run-a`nbuild.mcu=cortex-m0plus`nrecipe.c.o.pattern={build.path}`nextra.time.utc=12345`nextra.time.local=12345";
    Arduino=$cli; CliVersion='1.5.1'; ExtraArguments=@('--libraries',$dependency);
    DependencyRoots=@($dependency); StagedSketch=$source; RunRoot=(Join-Path $source '.j18-build/run-a')}
$original=Get-SketchBuildFingerprint @params
Assert-True ($original -eq (Get-SketchBuildFingerprint @params)) 'Same inputs must reuse the fingerprint.'
$params.Properties=$params.Properties.Replace('run-a','run-b')
Assert-True ($original -eq (Get-SketchBuildFingerprint @params)) 'Per-run build path must not invalidate reuse.'
$params.Properties=$params.Properties.Replace('12345','67890')
Assert-True ($original -eq (Get-SketchBuildFingerprint @params)) 'Current wall-clock properties must not force recompilation.'
$params.Properties=$params.Properties.Replace('cortex-m0plus','different-mcu')
Assert-True ($original -ne (Get-SketchBuildFingerprint @params)) 'Changed build settings must invalidate reuse.'
$params.Properties=$params.Properties.Replace('different-mcu','cortex-m0plus')
[IO.File]::WriteAllText($ino,'void setup(){int x=1;} void loop(){}')
Assert-True ($original -ne (Get-SketchBuildFingerprint @params)) 'Changed sketch must invalidate reuse.'
[IO.File]::WriteAllText($ino,'void setup(){} void loop(){}')
[IO.File]::WriteAllText($library,'#define VALUE 2')
Assert-True ($original -ne (Get-SketchBuildFingerprint @params)) 'Changed dependency must invalidate reuse.'
[IO.File]::WriteAllText($library,'#define VALUE 1')
$header=Join-Path $source 'config.h'; [IO.File]::WriteAllText($header,'#define NEW_PIN 5')
Assert-True ($original -ne (Get-SketchBuildFingerprint @params)) 'Added config header must invalidate reuse.'
# Restore by changing the fixture fingerprint rather than deleting files.
$fingerprint=Get-SketchBuildFingerprint @params
Assert-True (!(Find-CachedSketchBuild $source $fingerprint $params.Board)) 'Old/unrecorded cache must not be trusted.'
$run=Join-Path $source '.j18-build/001-success'
$firmware=Join-Path $run 'firmware'; $null=New-Item -ItemType Directory -Force $firmware
$app=Join-Path $firmware 'Sketch.ino.bin'
[byte[]]$bytes=New-Object byte[] 64
[Array]::Copy([BitConverter]::GetBytes([uint32]0x20008000),0,$bytes,0,4)
[Array]::Copy([BitConverter]::GetBytes([uint32]0x2021),0,$bytes,4,4)
[IO.File]::WriteAllBytes($app,$bytes)
Save-SketchBuildReceipt $run $fingerprint $params.Board $app
Assert-True ((Find-CachedSketchBuild $source $fingerprint $params.Board).Path -eq $app) 'Matching successful compilation must be found.'
Assert-True (!(Find-CachedSketchBuild $source 'changed fingerprint' $params.Board)) 'Wrong inputs must not select a binary.'
Assert-True ((Find-CachedSketchBuild $source '' $params.Board).Path -eq $app) 'Explicit static-folder migration may import a valid historical build without matching current sources.'
Assert-True (!(Find-CachedSketchBuild $source $fingerprint 'wrong:board:id')) 'Wrong board must not select a binary.'
$badRun=Join-Path $source '.j18-build/999-failed'; $null=New-Item -ItemType Directory -Force $badRun
[IO.File]::WriteAllText((Join-Path $badRun 'sketch-build.json'),'{broken json')
Assert-True ((Find-CachedSketchBuild $source $fingerprint $params.Board).Path -eq $app) 'A newer failed run must not hide a good compilation.'
$bytes[12]=1; [IO.File]::WriteAllBytes($app,$bytes)
Assert-True (!(Find-CachedSketchBuild $source $fingerprint $params.Board)) 'Changed cached binary must be rejected.'
$bytes[12]=0; [IO.File]::WriteAllBytes($app,$bytes)
$receipt=Get-Content -LiteralPath (Join-Path $run 'sketch-build.json') -Raw | ConvertFrom-Json
$receipt.app_file='../Sketch.ino.bin'
$receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run 'sketch-build.json') -Encoding UTF8
Assert-True (!(Find-CachedSketchBuild $source $fingerprint $params.Board)) 'Receipt path traversal must be rejected.'
$bytes[0]=0xfc; $bytes[1]=0x7f
Assert-True (!(Test-SketchImage $bytes)) 'Misaligned application stack must be rejected.'
$stage=Join-Path $FixtureRoot 'Staged'; $null=New-Item -ItemType Directory -Force $stage
Copy-Item -LiteralPath $ino,$header -Destination $stage
$params.StagedSketch=$stage
$null=Get-SketchBuildFingerprint @params
[IO.File]::WriteAllText($ino,'changed after staging')
$rejected=$false
try { $null=Get-SketchBuildFingerprint @params } catch { $rejected=$_.Exception.Message -like '*changed while being staged*' }
Assert-True $rejected 'Staged sources must agree with the original snapshot.'
$params.StagedSketch=$source
$toolchain=Join-Path $FixtureRoot 'Toolchain'; $null=New-Item -ItemType Directory -Force $toolchain
$archive=Join-Path $toolchain 'libgcc.a'; [IO.File]::WriteAllText($archive,'toolchain fixture')
$params.ToolchainRoots=@($toolchain)
$toolBefore=Get-SketchBuildFingerprint @params
[IO.File]::SetLastWriteTimeUtc($archive,[IO.File]::GetLastWriteTimeUtc($archive).AddSeconds(2))
Assert-True ($toolBefore -ne (Get-SketchBuildFingerprint @params)) 'Changed toolchain metadata must invalidate reuse.'
$static=Join-Path $source '.j18-build'
$staticFirmware=Join-Path $static 'firmware'; $null=New-Item -ItemType Directory -Force $staticFirmware
$staticApp=Join-Path $staticFirmware 'firmware.sketch.bin'
[byte[]]$staticBytes=New-Object byte[] 64
[Array]::Copy([BitConverter]::GetBytes([uint32]0x20008000),0,$staticBytes,0,4)
[Array]::Copy([BitConverter]::GetBytes([uint32]0x2021),0,$staticBytes,4,4)
[IO.File]::WriteAllBytes($staticApp,$staticBytes)
$staticBoot=Join-Path $staticFirmware 'samd21_sam_ba.bin'
[IO.File]::WriteAllText($staticBoot,'manually selected bootloader fixture')
Save-StaticSketchReceipt $static $params.Board $staticApp 'compiled' 'inputs'
Save-StaticSketchReceipt $static $params.Board $staticApp 'recompiled' 'changed-inputs'
$staticRecord=Get-Content -LiteralPath (Join-Path $static 'sketch-build.json') -Raw | ConvertFrom-Json
Assert-True ($staticRecord.schema -eq 2 -and $staticRecord.origin -eq 'recompiled') 'Static receipt must replace the previous one without a new folder.'
$pending=Join-Path $staticFirmware 'pending.bin'
[IO.File]::WriteAllText($pending,'replacement fixture')
$previous=[IO.File]::ReadAllBytes($staticApp)
$held=[IO.File]::Open($staticApp,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $blocked=$false
    try { Move-J18AtomicFile $pending $staticApp } catch { $blocked=$true }
    Assert-True $blocked 'A locked published image must stop publication.'
    Assert-True ([Convert]::ToBase64String([IO.File]::ReadAllBytes($staticApp)) -eq [Convert]::ToBase64String($previous)) 'Failed replacement must preserve the old image.'
} finally { $held.Dispose() }
Move-J18AtomicFile $pending $staticApp
Assert-True ([IO.File]::ReadAllText($staticApp) -eq 'replacement fixture') 'Image replacement after releasing lock failed.'
foreach ($name in @('compiler','compiler-output','sketch')) {
    $dir=Join-Path $static $name; $null=New-Item -ItemType Directory -Force $dir
    [IO.File]::WriteAllText((Join-Path $dir 'old-file.txt'),'stale compiler input')
}
Reset-StaticCompilerFolders $static $source
Assert-True (Test-Path -LiteralPath $staticApp) 'Rebuild cleanup must preserve the published sketch until successful compilation.'
Assert-True (([IO.File]::ReadAllText($staticBoot)) -eq 'manually selected bootloader fixture') 'Rebuild cleanup must preserve a manually selected bootloader.'
Assert-True (!(Test-Path -LiteralPath (Join-Path $static 'compiler'))) 'Old compiler output must be cleared on rebuild.'
$wrongPathRejected=$false
try { Reset-StaticCompilerFolders (Join-Path $FixtureRoot 'outside') $source } catch { $wrongPathRejected=$true }
Assert-True $wrongPathRejected 'Cleanup must reject a folder outside the sketch build root.'
'PASS: input/board/library changes, binary corruption, bad receipts, failed runs, and stack alignment.'
