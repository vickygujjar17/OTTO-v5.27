$ErrorActionPreference = 'Stop'
$p    = 'C:\Users\vivek\Downloads\OTTO-v5.00 v5.27\otto.mq5'
$bak  = 'C:\Users\vivek\AppData\Local\Temp\otto_mq5_pre_hivemind.bak'
$logF = 'C:\Users\vivek\AppData\Local\Temp\flip_hivemind.log'
$L1   = [System.Text.Encoding]::GetEncoding(28591)

# Normalise a here-string body to CRLF and drop the single trailing newline
# that precedes the '@ terminator.
function Blk([string]$s) {
  $s = $s -replace "`r`n", "`n"
  if ($s.EndsWith("`n")) { $s = $s.Substring(0, $s.Length - 1) }
  return ($s -replace "`n", "`r`n")
}
function NonAscii([string]$s) {
  $n = 0
  foreach ($ch in $s.ToCharArray()) { if ([int]$ch -gt 127) { $n++ } }
  return $n
}

$bytes = [System.IO.File]::ReadAllBytes($p)
[System.IO.File]::WriteAllBytes($bak, $bytes)
$script:T = $L1.GetString($bytes)
# the PRE-EXISTING double-encoded arrow (8 bytes) that must survive untouched
$arrow = $L1.GetString([byte[]](0xC3,0xA2,0xE2,0x80,0xA0,0xE2,0x80,0x99))

$script:LOG = @()
$arrowBefore    = ([regex]::Matches($script:T, [regex]::Escape($arrow))).Count
$nonAsciiBefore = NonAscii $script:T
$script:LOG += "pre : arrow=$arrowBefore nonAsciiBytes=$nonAsciiBefore"

function Do-Rep([string]$old, [string]$new, [string]$tag) {
  $n = ([regex]::Matches($script:T, [regex]::Escape($old))).Count
  if ($n -ne 1) { throw "[$tag] expected exactly 1 match, found $n" }
  $script:T = $script:T.Replace($old, $new)
  $script:LOG += "[OK ] $tag"
}

$oldLoop = Blk @'
   SSniperBlock allBlocks[];
   int total = g_blockManager.GetAllBlocks(allBlocks);
   bool hasSupport = false, hasResistance = false;
   for(int b = 0; b < total; b++)
     {
      if(allBlocks[b].isVetoed) continue;
      if(allBlocks[b].type == BLOCK_SUPPORT) hasSupport = true;
      if(allBlocks[b].type == BLOCK_RESISTANCE) hasResistance = true;
     }
'@

$newLoop = Blk @'
   SSniperBlock allBlocks[];
   int total = g_blockManager.GetAllBlocks(allBlocks);
   int supportVote = 0, resistanceVote = 0;   // raw zone polarity (diagnostics)
   int mappedVote  = 0;                       // EXPERIMENT: MAPPED direction digest
   for(int b = 0; b < total; b++)
     {
      if(allBlocks[b].isVetoed) continue;
      if(allBlocks[b].type == BLOCK_SUPPORT)
         supportVote++;
      else if(allBlocks[b].type == BLOCK_RESISTANCE)
         resistanceVote++;
      // EXPERIMENT (experiment/reverse-sr): each live zone votes with the
      // direction it would actually TRADE, not with the polarity it was
      // discovered as. With InpReverseSR = false this is the identity
      // mapping from block.type, so mappedVote == supportVote - resistanceVote
      // and the tree below reduces to the original tree exactly.
      mappedVote += (MappedZoneDirection(allBlocks[b].type) == DIR_LONG) ? 1 : -1;
     }
'@
# @@NEXT@@
$oldTree = Blk @'
   if(hasSupport && !hasResistance)
      myBias = 1;
   else if(hasResistance && !hasSupport)
      myBias = -1;
   else if(hasSupport && hasResistance)
     {
      int resolution = g_correlationFilter.ResolveBidirectionalConflict();
      if(resolution == 1)
        {
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Long @@ARROW@@ kept Support");
        }
      else if(resolution == -1)
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         myBias = -1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Short @@ARROW@@ kept Resistance");
        }
      else
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 0;
         if(EnableLogging) Print("[HiveMind] CONFLICT: no consensus @@ARROW@@ deleted both");
        }
     }
'@
$oldTree = $oldTree.Replace('@@ARROW@@', $arrow)

$newTree = Blk @'
   // EXPERIMENT (experiment/reverse-sr): the tie-breaker DELETES the zone whose
   // MAPPED direction LOST the vote. Both DeleteBlockType arguments below carry
   // an identical "supportVote > 0 ? Long : Short" discriminator:
   //   in a two-polarity conflict (supportVote > 0) it picks polarity-vs-polarity,
   //   the SAME argument the original tree used;
   //   with only a resistance zone live (supportVote == 0) it picks the polled
   //   winner as a zone, so a resistance-only field cannot be deleted.
   // Log lines and myBias are unchanged:
   //   resolution == +1 -> peers Long  -> the SHORT-mapped zone is deleted
   //   resolution == -1 -> peers Short -> the LONG-mapped  zone is deleted
   // With InpReverseSR = false supportVote > 0 => support->DIR_LONG, so both
   // arguments reduce to the originals -- main behaviour unchanged.
   if(mappedVote > 0 && resistanceVote == 0)
      myBias = 1;
   else if(mappedVote < 0 && supportVote == 0)
      myBias = -1;
   else if(mappedVote != 0)
     {
      int resolution = g_correlationFilter.ResolveBidirectionalConflict();
      if(resolution == 1)
        {
         g_blockManager.DeleteBlockType(supportVote > 0 ? BLOCK_SUPPORT : BLOCK_RESISTANCE);
         myBias = 1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Long @@ARROW@@ kept Support");
        }
      else if(resolution == -1)
        {
         g_blockManager.DeleteBlockType(supportVote > 0 ? BLOCK_RESISTANCE : BLOCK_SUPPORT);
         myBias = -1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Short @@ARROW@@ kept Resistance");
        }
      else
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 0;
         if(EnableLogging) Print("[HiveMind] CONFLICT: no consensus @@ARROW@@ deleted both");
        }
     }
'@
$newTree = $newTree.Replace('@@ARROW@@', $arrow)

# --- EDIT 1: insert the mapped-direction helper above the HiveMind banner ---
$crlf   = "`r`n"
# Take the EXISTING banner line verbatim out of the file so its non-ASCII
# mojibake bytes are preserved exactly, whatever they happen to be.
$banner = ($script:T -split "`r`n" | Where-Object { $_ -like '//| Hive Mind*' } | Select-Object -First 1)
if (-not $banner) { throw 'HiveMind banner line not found in source' }
$helperTail = Blk @'
//+------------------------------------------------------------------+
//| MAPPED direction of a zone for the portfolio bias vote.         |
//|                                                                 |
//| EXPERIMENT (experiment/reverse-sr): a Support / Resistance zone  |
//| is DISCOVERED polarity, NOT trade direction. HiveMind publishes  |
//| a LONG / SHORT consensus into the correlation matrix, and the    |
//| conflict tie-breaker does not merely prefer one of two opposing  |
//| zones -- it DELETES the losing polarity and vetoes its live      |
//| orders. Reading the raw zone here would therefore publish the    |
//| EXACT OPPOSITE bias under InpReverseSR while every entry is      |
//| mapped the other way, so the hive mind would systematically      |
//| veto exactly the setups this build now takes.                    |
//|                                                                 |
//| Byte-identical twin of COttoOrderManager::GetDirectionForBlock() |
//| and COttoBlockManager::BlockDirection() (both private) for the   |
//| support<->resistance mapping: with InpReverseSR = false a        |
//| Support zone maps to DIR_LONG and a Resistance zone to DIR_SHORT,|
//| so every branch below reduces to the original literal zone.      |
//+------------------------------------------------------------------+
ENUM_TRADE_DIRECTION MappedZoneDirection(const ENUM_BLOCK_TYPE type)
  {
   if(InpReverseSR)
      return (type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;
   return (type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;
  }
'@
$helperNew = $helperTail + $crlf + $crlf +
             "//+------------------------------------------------------------------+" + $crlf +
             $banner

Do-Rep $banner  $helperNew "mapped-direction helper"
Do-Rep $oldLoop $newLoop   "vote loop"
Do-Rep $oldTree $newTree   "conflict tree"

# --- write + byte-fidelity verification (auto-rollback on any drift) ---
$out = $L1.GetBytes($script:T)
[System.IO.File]::WriteAllBytes($p, $out)

$rb            = [System.IO.File]::ReadAllBytes($p)
$rt            = $L1.GetString($rb)
$arrowAfter    = ([regex]::Matches($rt, [regex]::Escape($arrow))).Count
$nonAsciiAfter = NonAscii $rt
$bareLf        = 0
for ($i = 0; $i -lt $rb.Length; $i++) {
  if ($rb[$i] -eq 0x0A -and ($i -eq 0 -or $rb[$i-1] -ne 0x0D)) { $bareLf++ }
}
$hasBom = ($rb.Length -ge 3 -and $rb[0] -eq 0xEF -and $rb[1] -eq 0xBB -and $rb[2] -eq 0xBF)

$script:LOG += "post: arrow=$arrowAfter nonAsciiBytes=$nonAsciiAfter bareLF=$bareLf bom=$hasBom"
$script:LOG += "delta bytes = $($rb.Length - $bytes.Length)"

if ($arrowAfter -ne $arrowBefore -or $nonAsciiAfter -ne $nonAsciiBefore -or $bareLf -ne 0 -or $hasBom) {
  [System.IO.File]::WriteAllBytes($p, $bytes)
  $script:LOG += "[FAIL] byte-fidelity drift -> file RESTORED from pre-edit bytes"
} else {
  $script:LOG += "[PASS] non-ASCII preserved 1:1, CRLF only, no BOM, arrow intact"
}

$script:LOG | Set-Content -Path $logF -Encoding UTF8
$script:LOG | ForEach-Object { Write-Output $_ }


