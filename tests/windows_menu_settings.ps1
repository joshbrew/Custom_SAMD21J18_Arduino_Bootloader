param([Parameter(Mandatory=$true)][string]$FixtureRoot)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../tools/samd21_menu.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Move-MenuSettingsFile','Save-MenuSettings')) {
        . ([ScriptBlock]::Create($node.Extent.Text))
    }
}
function Assert-True($Value,[string]$Message) { if (!$Value) { throw $Message } }
$menuSettingsPath=Join-Path $FixtureRoot 'settings.json'
$selected=@{Name='Test'}; $Board='zero'; $loader='current'; $localLoader=''; $checkOnly=$true
Save-MenuSettings
Assert-True (Test-Path -LiteralPath $menuSettingsPath) 'Initial settings save failed.'
$Board='target'
Save-MenuSettings
Assert-True ((Get-Content -LiteralPath $menuSettingsPath -Raw | ConvertFrom-Json).board -eq 'target') 'Atomic replacement failed.'
$stamp=[IO.File]::GetLastWriteTimeUtc($menuSettingsPath)
Save-MenuSettings
Assert-True ([IO.File]::GetLastWriteTimeUtc($menuSettingsPath) -eq $stamp) 'Unchanged settings were rewritten.'
$locked=[IO.File]::Open($menuSettingsPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $Board='custom'
    $warnings=@(Save-MenuSettings 3>&1)
    Assert-True ($warnings.Count -gt 0) 'Locked destination should warn without throwing.'
    Assert-True ((Get-Content -LiteralPath $menuSettingsPath -Raw | ConvertFrom-Json).board -eq 'target') 'Locked settings must preserve previous contents.'
} finally { $locked.Dispose() }
Save-MenuSettings
Assert-True ((Get-Content -LiteralPath $menuSettingsPath -Raw | ConvertFrom-Json).board -eq 'custom') 'Saving after lock release failed.'
Assert-True (@(Get-ChildItem -LiteralPath $FixtureRoot -Filter '*.tmp').Count -eq 0) 'Settings temporary files leaked.'
Write-Output 'PASS native settings replacement, unchanged file and locked-file recovery.'
