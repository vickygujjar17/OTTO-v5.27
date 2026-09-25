# build_check.ps1 -- OTTO EA strict compilation gate
#
# Stages a temporary MQL5 build tree that mirrors the MetaTrader terminal
# layout so that `#include "../Include/Otto/*.mqh"` resolves, compiles via the
# MetaEditor CLI, and reports the exact error/warning counts.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File build_check.ps1

param(
    [string]$Source = "C:\Users\vivek\Downloads\OTTO-v5.00 v5.27\OTTO",
    [string]$MetaEditor = "C:\Program Files\Five Percent Online MetaTrader 5\MetaEditor64.exe",
    [string]$Entry = "otto.mq5",
    [switch]$NoDeploy
)

$ErrorActionPreference = "Stop"

$buildRoot = Join-Path $env:TEMP "otto_build"
$experts   = Join-Path $buildRoot "MQL5\Experts"
$includes  = Join-Path $buildRoot "MQL5\Include\Otto"
$logPath   = Join-Path $buildRoot "compile.log"

Write-Host "=================================================================="
Write-Host "OTTO EA COMPILATION GATE"
Write-Host "=================================================================="
Write-Host "source    : $Source"
Write-Host "metaeditor: $MetaEditor"
Write-Host "entry     : $Entry"
Write-Host "buildRoot : $buildRoot"
Write-Host ""

if (-not (Test-Path $MetaEditor)) {
    Write-Host "FATAL: MetaEditor not found at $MetaEditor"
    exit 2
}

# --- Stage a clean build tree -------------------------------------------
# Layout MUST mirror a real terminal so angle-bracket includes resolve:
#   $buildRoot\MQL5\Experts\otto.mq5            <- entry point
#   $buildRoot\MQL5\Include\Otto\*.mqh          <- OTTO modules
#   $buildRoot\MQL5\Include\Trade\...           <- stock framework
# /inc is then pointed at $buildRoot\MQL5, so the compiler reads ONLY this
# tree and never the live terminal's Include folder.
if (Test-Path $buildRoot) { Remove-Item $buildRoot -Recurse -Force }
$mql5Root = Join-Path $buildRoot "MQL5"
$experts  = Join-Path $mql5Root "Experts"
$stageInc = Join-Path $mql5Root "Include"
$includes = Join-Path $stageInc "Otto"
$logPath  = Join-Path $buildRoot "compile.log"

New-Item -ItemType Directory -Force -Path $experts, $includes | Out-Null

# 1) OTTO sources first, into Include\Otto (the <Otto/...> target).
Copy-Item (Join-Path $Source "*.mqh") $includes -Force
Copy-Item (Join-Path $Source "*.mq5") $experts  -Force

# --- Header resolution for angle-bracket includes --------------------------
# VERIFIED PROBLEM (v5.10 investigation): otto.mq5 uses angle-bracket
# includes (<Otto/*.mqh>) plus <Trade/Trade.mqh>. MetaEditor resolves those
# against the terminal's REAL MQL5\Include folder -- NOT against a staging
# tree that merely sits in %TEMP%. With no mirror step the gate compiles
# whatever stale/corrupted headers happen to be deployed and still prints
# "0 errors, 0 warnings", i.e. it silently validates the WRONG code.
#
# Reproduced 18-09-2026: repo held clean headers, the deployed
# Include\Otto folder held '?'-mangled copies, and the gate still reported
# 0 errors / 0 warnings. That green result was meaningless.
#
# FIX: pass the compiler an explicit include root via /inc:<MQL5 parent>.
# Verified by log inspection: with /inc:$mql5Root every OTTO module loads
# from $mql5Root\Include\Otto and the terminal is never read. The stock
# framework folders are copied in so <Trade/...> keeps resolving.
#
#   default    : stage + /inc redirect (terminal NEVER written to)
#   -NoDeploy  : retained for compatibility; identical to the default, the
#                terminal is not touched either way.

# 2) Stock MQL5 framework includes, so <Trade/...> still resolves after the
#    include root is redirected away from the terminal.
#
#    NOTE: the live Include folder also holds LOOSE legacy OTTO headers at its
#    top level (Include\COttoBlockManager.mqh, Include\OttoDefines.mqh, ...).
#    Those are old v4.x copies. They MUST NOT be copied in: the staging
#    Include folder is a search root, so a loose OttoDefines.mqh would shadow
#    or collide with Include\Otto\OttoDefines.mqh and silently link stale
#    code. Everything matching the OTTO module names or the Otto folder is
#    therefore excluded from the framework mirror.
$ottoNames = @("OttoDefines", "COttoNewsFilter", "COttoRiskManager",
               "COttoMarketStructure", "COttoBlockManager", "COttoOrderManager",
               "COttoCorrelationFilter", "COttoTradeManager", "COttoJournal")
$skipNames = New-Object System.Collections.Generic.HashSet[string]
[void]$skipNames.Add("Otto")
foreach ($n in $ottoNames) { [void]$skipNames.Add("$n.mqh") }
# Legacy/foreign variants that must never shadow an OTTO module.
foreach ($n in @("COrderManager.mqh")) { [void]$skipNames.Add($n) }

$mt5Include = Join-Path $env:APPDATA "MetaQuotes\Terminal\10CE948A1DFC9A8C27E56E827008EBD4\MQL5\Include"
if (Test-Path $mt5Include) {
    $skipped = 0
    Get-ChildItem $mt5Include -Force | ForEach-Object {
        $isSkip = $skipNames.Contains($_.Name) -or ($_.Name -like "_Otto_backup_*")
        if ($isSkip) { $skipped++ }
        else { Copy-Item $_.FullName $stageInc -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "framework includes staged from: $mt5Include"
    Write-Host ("legacy/OTTO entries excluded : {0}" -f $skipped)
} else {
    Write-Host "WARNING: terminal Include folder not found; framework headers unavailable"
}
if ($NoDeploy) {
    Write-Host "deploy disabled : terminal Include\Otto will NOT be touched"
}
Write-Host "include root    : $mql5Root  (via /inc)"
Write-Host ""

# Remove stale build artifacts so we never compile a cached binary
Get-ChildItem $buildRoot -Recurse -Include *.ex5, *.log -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$entryPath = Join-Path $experts $Entry
if (-not (Test-Path $entryPath)) {
    Write-Host "FATAL: entry file not found: $entryPath"
    exit 2
}

# Hard guarantee: all 10 OTTO sources must be present in the staging tree.
$expectedOtto = @("OttoDefines", "COttoNewsFilter", "COttoRiskManager",
                  "COttoMarketStructure", "COttoBlockManager", "COttoOrderManager",
                  "COttoCorrelationFilter", "COttoTradeManager", "COttoJournal")
$missingStage = @()
foreach ($m in $expectedOtto) {
    if (-not (Test-Path (Join-Path $includes "$m.mqh"))) { $missingStage += "$m.mqh" }
}
if ($missingStage.Count -gt 0) {
    Write-Host "FATAL: staging incomplete, missing:"
    $missingStage | ForEach-Object { Write-Host "  $_" }
    exit 2
}

Write-Host "staged OTTO modules ($($expectedOtto.Count + 1) sources):"
Get-ChildItem $includes -File | ForEach-Object { Write-Host ("  {0} ({1} bytes)" -f $_.Name, $_.Length) }
Write-Host ("  entry: {0} ({1} bytes)" -f (Split-Path $entryPath -Leaf), (Get-Item $entryPath).Length)
Write-Host ""

# --- Compile -------------------------------------------------------------
$sw = [System.Diagnostics.Stopwatch]::StartNew()
& $MetaEditor /compile:"$entryPath" /inc:"$mql5Root" /log:"$logPath" | Out-Null
$sw.Stop()
Write-Host ("compile wall time: {0:N0} ms" -f $sw.Elapsed.TotalMilliseconds)
Write-Host ""

if (-not (Test-Path $logPath)) {
    Write-Host "FATAL: compiler produced no log at $logPath"
    exit 2
}

# --- Parse the log -------------------------------------------------------
# MetaEditor writes the log as UTF-16LE on some builds; detect and decode.
$bytes = [System.IO.File]::ReadAllBytes($logPath)
if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
    $text = [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
} else {
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
}

# --- Link provenance guard ------------------------------------------------
# Prove the compiler actually loaded the STAGED headers and never silently
# fell back to the terminal's Include\Otto (the v5.10 failure mode). Any
# module resolved outside the staging tree fails the gate outright.
$lines = $text -split "`r?`n" | ForEach-Object { $_.TrimEnd("`r") }

$ottoModules = @("OttoDefines", "COttoNewsFilter", "COttoRiskManager",
                 "COttoMarketStructure", "COttoBlockManager", "COttoOrderManager",
                 "COttoCorrelationFilter", "COttoTradeManager", "COttoJournal")
$linkedFromStage = @{}
foreach ($line in $lines) {
    if ($line -match "including\s+(.+\.mqh)\s*$") {
        $incPath = $Matches[1].Trim()
        foreach ($m in $ottoModules) {
            if ((Split-Path $incPath -Leaf) -eq "$m.mqh") {
                $linkedFromStage[$m] = $incPath
            }
        }
    }
}

$provenanceBad = @()
foreach ($m in $ottoModules) {
    if (-not $linkedFromStage.ContainsKey($m)) {
        $provenanceBad += "$m.mqh : NOT INCLUDED AT ALL"
    } elseif (-not $linkedFromStage[$m].StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        $provenanceBad += ("{0}.mqh : linked from OUTSIDE staging tree -> {1}" -f $m, $linkedFromStage[$m])
    }
}

Write-Host "=================================================================="
Write-Host "LINK PROVENANCE (all Otto modules must come from the staging tree)"
Write-Host "=================================================================="
foreach ($m in $ottoModules) {
    if ($linkedFromStage.ContainsKey($m)) {
        $p = $linkedFromStage[$m]
        $ok = $p.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)
        Write-Host ("  {0,-24} {1} {2}" -f "$m.mqh", $(if ($ok) { "[STAGED]" } else { "[TERMINAL]" }), $p)
    } else {
        Write-Host ("  {0,-24} [MISSING]" -f "$m.mqh")
    }
}
Write-Host ""

$errors   = @()
$warnings = @()
$ignored  = @()
$summary  = ""

foreach ($line in $lines) {
    $t = $line.Trim()
    # MetaEditor prefixes diagnostics with "<path>(line,col) : " on some builds
    # and with a bare ": " on others -- match both forms.
    if ($t -match "(^|:\s)error\s+\d+:")   { $errors   += $t; continue }
    if ($t -match "(^|:\s)warning\s+\d+:") { $warnings += $t; continue }
    if ($t -match "(^|:\s)information:\s*ignoring") { $ignored += $t; continue }
    if ($t -match "^\d+\s+error\(s\)")   { $summary = ($summary + $t + " ") }
    if ($t -match "^Result:")            { $summary = ($summary + $t) }
    if ($t -match "^\d+\s+warning\(s\)") { $summary = ($summary + $t + " ") }
}

Write-Host "=================================================================="
Write-Host "COMPILER LOG (full)"
Write-Host "=================================================================="
foreach ($line in $lines) {
    if ($line.Trim() -ne "") { Write-Host $line }
}
Write-Host ""

# --- Verdict -------------------------------------------------------------
Write-Host "=================================================================="
Write-Host "VERDICT"
Write-Host "=================================================================="

$nErr = $errors.Count
$nWarn = $warnings.Count
$ex5 = Get-ChildItem $experts -Filter "*.ex5" -File -ErrorAction SilentlyContinue

if ($ignored.Count -gt 0) {
    Write-Host ("ignored pragma/informational lines: {0}" -f $ignored.Count)
}
Write-Host ("errors  : {0}" -f $nErr)
Write-Host ("warnings: {0}" -f $nWarn)
if ($ex5) {
    Write-Host ("binary  : {0} ({1} bytes)" -f $ex5.FullName, $ex5.Length)
} else {
    Write-Host "binary  : NOT PRODUCED"
}
Write-Host ""
if ($summary) { Write-Host ("summary : {0}" -f $summary.Trim()) }

if ($nErr -gt 0) {
    Write-Host ""
    Write-Host "ERRORS:"
    $errors | ForEach-Object { Write-Host "  $_" }
}
if ($nWarn -gt 0) {
    Write-Host ""
    Write-Host "WARNINGS:"
    $warnings | ForEach-Object { Write-Host "  $_" }
}

Write-Host ""
if ($provenanceBad.Count -gt 0) {
    Write-Host "PROVENANCE FAILURES:"
    $provenanceBad | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
}
if ($nErr -eq 0 -and $nWarn -eq 0 -and $ex5 -and $provenanceBad.Count -eq 0) {
    Write-Host "*** GATE PASSED: 0 errors, 0 warnings, headers verified from staging tree ***"
    exit 0
} else {
    if ($provenanceBad.Count -gt 0 -and $nErr -eq 0 -and $nWarn -eq 0) {
        Write-Host "*** GATE FAILED: compile was green but linked the WRONG headers ***"
    } else {
        Write-Host "*** GATE FAILED ***"
    }
    exit 1
}