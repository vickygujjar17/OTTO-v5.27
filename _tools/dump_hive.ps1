$p = 'C:\Users\vivek\Downloads\OTTO-v5.00 v5.27\otto.mq5'
$out = 'C:\Users\vivek\AppData\Local\Temp\hive_dump.txt'
$bytes = [System.IO.File]::ReadAllBytes($p)
$latin = [System.Text.Encoding]::GetEncoding(28591)
$text  = $latin.GetString($bytes)
$lines = $text -split "`r`n"
$res = @()
for ($i = 676; $i -lt 722 -and $i -lt $lines.Count; $i++) {
  $vis = $lines[$i] -replace ' ', '.'
  $res += ('{0,5}| {1}' -f ($i + 1), $vis)
}
# hex of the arrow bytes on the "peers Long" log line
$idx = $text.IndexOf('CONFLICT: peers Long')
if ($idx -ge 0) {
  $seg = $bytes[($idx + 21)..($idx + 33)]
  $res += ('HEX after "CONFLICT: peers Long": ' + (($seg | ForEach-Object { $_.ToString('X2') }) -join ' '))
}
$res | Set-Content -Path $out -Encoding UTF8
Get-Content $out
