# Prove the 5.60 tree is bump-clean: back up every file the bump touches,
# run the REAL next bump (5.60 -> 5.61), capture the guard verdict, then
# restore the tree byte-for-byte. Nothing is left bumped.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$files = @(
  'CHighTableAuditor.mqh','COttoBlockManager.mqh','COttoCorrelationFilter.mqh',
  'COttoJournal.mqh','COttoMarketStructure.mqh','COttoNewsFilter.mqh',
  'COttoOrderManager.mqh','COttoRiskManager.mqh','COttoTradeManager.mqh',
  'OttoDefines.mqh','README.md','otto.mq5'
)

$bak = Join-Path $env:TEMP 'otto_bump_probe_bak'
if (Test-Path $bak) { Remove-Item $bak -Recurse -Force }
New-Item -ItemType Directory -Path $bak | Out-Null
foreach ($f in $files) { Copy-Item $f (Join-Path $bak $f) -Force }

$hashBefore = (Get-FileHash -Algorithm SHA256 ($files)).Hash -join ','
Write-Host "backed up $($files.Count) files; running bump 5.61..."

Write-Host "`n===== BUMP 5.60 -> 5.61 =====`n"
python _tools/_bump_version.py 5.61
$bumpExit = $LASTEXITCODE
Write-Host "`nbump_exit=$bumpExit"

# Restore unconditionally so a failure still leaves a clean tree.
foreach ($f in $files) { Copy-Item (Join-Path $bak $f) $f -Force }
$hashAfter = (Get-FileHash -Algorithm SHA256 ($files)).Hash -join ','

Write-Host "`nrestored_identical=$($hashBefore -eq $hashAfter)"
Write-Host "guard_verdict=$(if ($bumpExit -eq 0) {'PASS (0 stale)'} else {'FAIL (stale remains)'})"
