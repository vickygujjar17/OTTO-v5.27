$root = 'C:\Users\vivek\Downloads\OTTO-v5.00 v5.27'
$out  = 'C:\Users\vivek\AppData\Local\Temp\encoding.txt'
$files = Get-ChildItem -Path ($root + '\*') -Include '*.mq5','*.mqh' -File
$res = @()
foreach ($f in $files) {
  $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
  $bom = 'none'
  if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $bom = 'UTF8-BOM' }
  $nonAscii = 0
  $firstNonAscii = -1
  for ($i = 0; $i -lt $bytes.Length; $i++) {
    if ($bytes[$i] -gt 0x7F) { $nonAscii++; if ($firstNonAscii -lt 0) { $firstNonAscii = $i } }
  }
  # strict UTF-8 decode check
  $strictOk = $true
  try {
    $enc = New-Object System.Text.UTF8Encoding($false, $true)
    $null = $enc.GetString($bytes)
  } catch { $strictOk = $false }
  # count lone 0x0A not preceded by 0x0D
  $bareLf = 0
  for ($i = 0; $i -lt $bytes.Length; $i++) {
    if ($bytes[$i] -eq 0x0A -and ($i -eq 0 -or $bytes[$i-1] -ne 0x0D)) { $bareLf++ }
  }
  $res += ('{0,-26} bom={1,-9} nonAsciiBytes={2,-5} validUTF8={3,-6} bareLF={4}' -f $f.Name, $bom, $nonAscii, $strictOk, $bareLf)
}
$res | Set-Content -Path $out -Encoding UTF8
Get-Content $out
