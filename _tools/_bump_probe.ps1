# Prove the current tree is bump-clean: back up every file the bump touches,
# run the REAL next bump (current -> next minor), capture the guard verdict,
# then restore the tree byte-for-byte. Nothing is left bumped.
#
# RETARGETED (v5.66). The release this exercises is now derived from
# OttoDefines.mqh instead of being hardcoded: a literal target would DOWNGRADE
# the tree the moment the shipped release passed it (this file said 5.61 while
# the tree shipped 5.66, so it would have rewritten 20 rows backwards).
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$files = @(
  'CHighTableAuditor.mqh','COttoBlockManager.mqh','COttoCorrelationFilter.mqh',
  'COttoJournal.mqh','COttoMarketStructure.mqh','COttoNewsFilter.mqh',
  'COttoOrderManager.mqh','COttoRiskManager.mqh','COttoTradeManager.mqh',
  'OttoDefines.mqh','README.md','otto.mq5'
)

# Derive current -> next from the live stamp so this probe can never go stale.
$curLine = Select-String -Path 'OttoDefines.mqh' -Pattern '#property version\s+"(\d+)\.(\d+)"' |
           Select-Object -First 1
if (-not $curLine) { throw 'ABORT: no version stamp found in OttoDefines.mqh' }
$maj = [int]$curLine.Matches[0].Groups[1].Value
$min = [int]$curLine.Matches[0].Groups[2].Value
$cur = "$maj.$min"
$next = "$maj.$($min + 1)"

$bak = Join-Path $env:TEMP 'otto_bump_probe_bak'
if (Test-Path $bak) { Remove-Item $bak -Recurse -Force }
New-Item -ItemType Directory -Path $bak | Out-Null
foreach ($f in $files) { Copy-Item $f (Join-Path $bak $f) -Force }

$hashBefore = (Get-FileHash -Algorithm SHA256 ($files)).Hash -join ','
Write-Host "backed up $($files.Count) files; running bump $cur -> $next..."

Write-Host "`n===== BUMP $cur -> $next =====`n"
python _tools/_bump_version.py $next
$bumpExit = $LASTEXITCODE
Write-Host "`nbump_exit=$bumpExit"

# Restore unconditionally so a failure still leaves a clean tree.
foreach ($f in $files) { Copy-Item (Join-Path $bak $f) $f -Force }
$hashAfter = (Get-FileHash -Algorithm SHA256 ($files)).Hash -join ','

Write-Host "`nrestored_identical=$($hashBefore -eq $hashAfter)"
Write-Host "guard_verdict=$(if ($bumpExit -eq 0) {'PASS (0 stale)'} else {'FAIL (stale remains)'})"
