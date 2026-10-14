$root = 'C:\Users\vivek\Downloads\OTTO-v5.00 v5.27'
$out  = 'C:\Users\vivek\AppData\Local\Temp\scan_zone.txt'
$root = 'C:\Users\vivek\Downloads\OTTO-v5.00 v5.27'
$out  = 'C:\Users\vivek\AppData\Local\Temp\scan_leak.txt'
$files = Get-ChildItem -Path ($root + '\*') -Include '*.mq5','*.mqh' -File
$res = @()
foreach ($f in $files) {
  $t = [System.IO.File]::ReadAllLines($f.FullName)
  for ($i = 0; $i -lt $t.Count; $i++) {
    $ln = $t[$i]
    $hit = $false
    if ($ln -match 'DeleteBlockType') { $hit = $true }
    if ($ln -match 'OTTO_SUP|OTTO_RES') { $hit = $true }
    if ($ln -match '\bisLong\s*=') { $hit = $true }
    if ($ln -match 'BLOCK_SUPPORT\s*\)|BLOCK_RESISTANCE\s*\)' ) { $hit = $true }
    if ($ln -match '\bhasSupport\b|\bhasResistance\b') { $hit = $true }
    if ($hit) { $res += ('{0}:{1}: {2}' -f $f.Name, ($i + 1), $ln.Trim()) }
  }
}
$res | Set-Content -Path $out -Encoding UTF8
Write-Output ("LEAK_CANDIDATES: " + $res.Count)
