# pack_release.ps1 -- build the release ZIP from the TRACKED tree, byte-exact.
#
# The manifest is `git ls-files`, not a filesystem walk: an ignored scratch file
# (e.g. _tools/commit_msg.txt) must never reach a release, and a deleted-but-
# staged file must never linger. Directory entries are emitted for the root and
# for _probe/ and _tools/ so the archive mirrors the v5.71 layout exactly
# (84 files + 3 directory entries), and every file is written with the same
# stored-vs-deflate policy the previous release used (deflate for files).
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File pack_release.ps1 `
#       -Repo <path> -Name <folder name> -Out <zip path>

param(
    [Parameter(Mandatory=$true)][string]$Repo,
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][string]$Out
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (Test-Path $Out) { Remove-Item $Out -Force }

$tracked = @(git -C $Repo ls-files)
if ($tracked.Count -eq 0) { Write-Host "FATAL: no tracked files in $Repo"; exit 2 }

$zip = [System.IO.Compression.ZipFile]::Open($Out, 'Create')
try {
    [void]$zip.CreateEntry("$Name/")
    foreach ($d in @('_probe/', '_tools/')) { [void]$zip.CreateEntry("$Name/$d") }

    foreach ($rel in $tracked) {
        $src = Join-Path $Repo ($rel -replace '/', '\')
        if (-not (Test-Path $src)) {
            Write-Host "FATAL: tracked but missing on disk: $rel"
            exit 3
        }
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $src, "$Name/$rel",
            [System.IO.Compression.CompressionLevel]::Optimal)
    }
}
finally { $zip.Dispose() }

Write-Host ("packed {0} tracked file(s) -> {1}" -f $tracked.Count, $Out)
