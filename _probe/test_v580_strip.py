#!/usr/bin/env python3
"""
v5.80 ARCHITECTURAL STRIP -- static verification probe.

v5.80 removes every POST-ARMING execution-path veto and every external
correlation / news filter, while leaving the shipped near-edge pullback
geometry byte-identical. Nothing about that is visible to the MQL5 compiler:
a deleted filter and a filter that merely stopped firing look the same from a
green build, and a silently re-anchored entry compiles and trades perfectly
well. Both classes of regression are only catchable by static analysis of the
shipped sources, which is what this probe does.

WHAT MUST BE GONE (and now fails the build if it comes back)
  * the five post-arming block vetoes         stale / front-run / near-miss /
                                              momentum / FVG
  * their bar-close passes and the intra-bar twin  UpdateIntraBar()
  * the public intra-bar wrapper              CheckVetoesInTick()
  * the per-block correlation guards          IsTradeVetoed / IsConsensusOpposed
  * the consensus cancellation sweep          CancelOpposingConsensusOrders()
  * the portfolio tie-breaker that DELETED live zones and published a bias GV
                                              HiveMind() / DeleteBlockType() /
                                              MappedZoneDirection()
  * the news blackout gate on order placement IsInNewsBlackout() pinned false

WHAT MUST SURVIVE
  * the two CREATION-time vetoes              Separation + ATR Sizing
  * the STRUCTURAL break/flip detector        VETO_BROKEN / VETO_FLIPPED
  * the strict limit router                   VETO_LIMIT_MISSED
  * the High Table auditor and prop-firm safety
  * the vector engine's MATHS, as diagnostics only

WHAT MUST BE BYTE-IDENTICAL TO v5.72
  The geometry. The entry gap (2.0*blockHeight + 1.5*ATR), slDist
  (blockHeight + 0.5*ATR), the far-edge TP anchor and the whole break/flip
  block are compared against the v5.72 commit (pinned as V572) character for
  character. A strip that quietly re-priced the strategy would be a different
  EA, not a smaller one, so this is asserted rather than reviewed.

Pure static analysis of the shipped sources -- no MT5 required.
"""

import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BM = "COttoBlockManager.mqh"
OM = "COttoOrderManager.mqh"
CORR = "COttoCorrelationFilter.mqh"
NEWS = "COttoNewsFilter.mqh"
DEFS = "OttoDefines.mqh"
MAIN = "otto.mq5"

# The v5.72 commit, pinned. This probe compares the shipped geometry against
# the v5.72 blob, so the reference MUST be that commit -- never HEAD. Reading
# HEAD was correct only while v5.72 was still the tip; once v5.80 landed, HEAD
# held the stripped sources, the HEAD-side slice terminators no longer existed,
# and the comparison silently degraded into a false failure. A pinned SHA keeps
# the reference honest no matter how far the branch advances.
V572 = "3337fab"

ALL_FILES = [MAIN, OM, "COttoTradeManager.mqh", "COttoRiskManager.mqh", BM,
             "COttoJournal.mqh", "CHighTableAuditor.mqh", CORR,
             "COttoMarketStructure.mqh", NEWS, DEFS]


def read(name):
    return io.open(os.path.join(ROOT, name), encoding="utf-8",
                   errors="replace", newline="").read()


def head_blob(name):
    """The v5.72 blob straight from git -- the geometry reference."""
    out = subprocess.run(["git", "show", V572 + ":" + name], cwd=ROOT,
                         capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def strip_comments(code):
    """Drop // comments and /* */ blocks, respecting string literals."""
    out, i, n = [], 0, len(code)
    in_block = in_str = False
    while i < n:
        if in_block:
            if code.startswith("*/", i):
                in_block = False
                i += 2
            else:
                i += 1
            continue
        ch = code[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if code.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if code.startswith("//", i):
            while i < n and code[i] != "\n":
                i += 1
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
        i += 1
    return "".join(out)


def func_body(code, decl_regex):
    """Brace-balanced body of the function whose declaration matches."""
    m = re.search(decl_regex, code)
    if not m:
        return None
    depth, seen, j = 0, False, m.start()
    while j < len(code):
        if code[j] == "{":
            depth += 1
            seen = True
        elif code[j] == "}":
            depth -= 1
            if seen and depth == 0:
                return code[m.start():j + 1]
        j += 1
    return None


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def flat(t):
    """Normalise line endings + trailing space so anchors can be plain ASCII."""
    return re.sub(r"[ \t]+", " ", (t or "").replace("\r\n", "\n"))


BM_T, OM_T, CORR_T = read(BM), read(OM), read(CORR)
NEWS_T, DEFS_T, MAIN_T = read(NEWS), read(DEFS), read(MAIN)

BM_C, OM_C = strip_comments(BM_T), strip_comments(OM_T)
MAIN_C, NEWS_C = strip_comments(MAIN_T), strip_comments(NEWS_T)

BM_H, OM_H = head_blob(BM), head_blob(OM)

BM_PROC = func_body(BM_C, r"void\s+ProcessBlocks\s*\(\s*int\s+marketDay\s*\)") or ""

# ----------------------------------------------------------------------
# 1. The funnel is exactly: deletion -> hasExited -> arming -> break/flip
# ----------------------------------------------------------------------
print("\n-- 1. Funnel shape --")

check("ProcessBlocks() still exists and was extracted",
      bool(BM_PROC), "extraction failed")

_arm = BM_PROC.find("b.isArmed = true")
_break = BM_PROC.find("double breakLevel")
# `m_blocks[i] = b;` also appears when the stacked-primary lockout and the flip
# persist early, so the funnel-tail write is located as the LAST one in the
# pass (the banner above it is a comment and is stripped by strip_comments).
_persist = BM_PROC.rfind("m_blocks[i] = b;")
check("arming is evaluated BEFORE the break/flip test",
      _arm >= 0 and _break > _arm, "arm@%d break@%d" % (_arm, _break))
check("break/flip is evaluated BEFORE the pass persists the block",
      _break >= 0 and _persist > _break, "break@%d persist@%d" % (_break, _persist))
check("no veto pass sits between hasExited and arming",
      "SetVeto(" not in BM_PROC[:max(_break, 0)],
      "a SetVeto() before the break test means a post-arming filter survived")
check("the ONLY veto writes left in ProcessBlocks are break/flip",
      set(re.findall(r"SetVeto\s*\(\s*i\s*,\s*(VETO_\w+)\s*\)", BM_PROC))
      == {"VETO_FLIPPED", "VETO_BROKEN"},
      "found: %s" % sorted(set(re.findall(r"SetVeto\s*\(\s*i\s*,\s*(VETO_\w+)\s*\)",
                                          BM_PROC))))
check("the funnel banner no longer advertises the retired passes",
      "Stale(45)" not in BM_T and "Momentum/FVG" not in BM_T)

# ----------------------------------------------------------------------
# 2. Every post-arming veto is absent from EXECUTABLE code
# ----------------------------------------------------------------------
print("\n-- 2. Removed vetoes --")

SCRATCH = "\n".join((BM_C, OM_C, MAIN_C, NEWS_C))
for symbol in ("VETO_STALE", "VETO_FRONTRUN", "VETO_NEARMISS", "VETO_MOMENTUM",
               "VETO_FVG", "VETO_CORRELATION"):
    check("%s is gone from code" % symbol, symbol not in SCRATCH)

for fn in ("UpdateIntraBar", "CheckVetoesInTick", "IsConsensusOpposed",
           "CancelOpposingConsensusOrders", "DeleteBlockType",
           "MappedZoneDirection"):
    check("%s() is gone from code" % fn, fn + "(" not in SCRATCH)

check("nothing gates a veto pass on InpUseFrontRunVeto",
      not re.search(r"(?<![\w!])if\(InpUseFrontRunVeto\b", BM_C))
check("no block is ever marked VETO_CORRELATION",
      "VETO_CORRELATION" not in OM_C)
check("the correlation filter cannot veto an entry",
      "IsTradeVetoed" not in OM_C)
check("HiveMind is not called from OnTick", "HiveMind()" not in MAIN_C)
check("no code deletes a zone type on a correlation vote",
      "DeleteBlockType" not in MAIN_C)


# ----------------------------------------------------------------------
# 3. The two CREATION-time vetoes and the structural detector SURVIVE
# ----------------------------------------------------------------------
print("\n-- 3. Survivors --")

check("the separation veto is still enforced",
      "VETO_NO_SEPARATION" in BM_C and "InpUseSeparationVeto" in BM_C)
check("the ATR sizing veto is still enforced",
      "VETO_SIZING" in BM_C and "ATRVetoMin" in BM_C and "ATRVetoMax" in BM_C)
check("structural VETO_BROKEN is still set by the far-edge break test",
      "SetVeto(i, VETO_BROKEN);" in BM_C)
check("structural VETO_FLIPPED is still set by the wick-based flip",
      "SetVeto(i, VETO_FLIPPED);" in BM_C)
check("the strict limit router still records VETO_LIMIT_MISSED",
      "VETO_LIMIT_MISSED" in OM_C)
check("the High Table auditor is still compiled in and initialised",
      "#include <Otto/CHighTableAuditor.mqh>" in MAIN_T and
      "g_highTable" in MAIN_C)
check("prop-firm safety still halts on total DD",
      "g_totalDD_Halted = true" in MAIN_C)
check("invalid blocks still have their pendings cancelled",
      MAIN_C.count("CancelOrdersForInvalidBlocks()") >= 2,
      "found %d" % MAIN_C.count("CancelOrdersForInvalidBlocks()"))

# ----------------------------------------------------------------------
# 4. News and correlation filters cannot gate a trade
# ----------------------------------------------------------------------
print("\n-- 4. External filters cannot gate --")

check("IsInNewsBlackout() is pinned false at the source",
      re.search(r"IsInNewsBlackout\(void\)\s*const\s*\{\s*return false;",
                flat(NEWS_C)) is not None)
check("order placement is gated ONLY by the daily-DD pause",
      re.search(r"if\(!g_dailyDD_Paused\)", flat(MAIN_C)) is not None and
      "IsInNewsBlackout" not in MAIN_C)
check("the status line no longer reports a news state",
      '" | News: "' not in MAIN_T)
check("the vector engine still refreshes (diagnostics)",
      "g_correlationFilter.RefreshVectorState();" in MAIN_C)
check("the vector engine is described as diagnostics-only",
      "DIAGNOSTICS" in DEFS_T or "(diagnostics)" in DEFS_T)

# ----------------------------------------------------------------------
# 5. GEOMETRY: byte-identical to v5.72
# ----------------------------------------------------------------------
print("\n-- 5. Geometry unchanged vs v5.72 --")

check("both v5.72 reference blobs are readable",
      BM_H is not None and OM_H is not None)


def same(label, a, b):
    ok = flat(a).rstrip() == flat(b).rstrip() and bool(flat(a).strip())
    check(label, ok)


if BM_H and OM_H:
    _h = BM_H[BM_H.find("double breakLevel"):
             BM_H.find("// --- MOMENTUM & FVG VETO")]
    _w = BM_T[BM_T.find("double breakLevel"):
              BM_T.find("// --- Persist block mutations")]
    same("break/flip block is byte-identical to v5.72", _h, _w)

    for label, src_h, src_w, decl in (
            ("COttoBlockManager::BlockEntryPrice", BM_H, BM_T,
             r"double\s+BlockEntryPrice\s*\("),
            ("COttoOrderManager::CalcEntryPrice", OM_H, OM_T,
             r"double\s+CalcEntryPrice\s*\("),
            ("COttoOrderManager::CalcSLDistance", OM_H, OM_T,
             r"double\s+CalcSLDistance\s*\(")):
        same("%s is byte-identical to v5.72" % label,
             func_body(src_h, decl), func_body(src_w, decl))

# Numeric identities on the shipped formulas. Both twins spell the block field
# qualified (b.blockHeight / block.blockHeight), so the anchor names it.
check("the entry gap is still 2.0*blockHeight + 1.5*ATR",
      "double gap = (2.0 * block.blockHeight) + (1.5 * atr);" in OM_C and
      "double gap = (2.0 * b.blockHeight) + (1.5 * atr);" in BM_C)
check("slDist is still blockHeight + 0.5*ATR",
      "blockHeight + (0.5 * atr)" in OM_C)
check("the TP is still projected from the far edge by InpMaxRR",
      re.search(r"takeProfit\s*=\s*isLong\s*\?\s*farEdge\s*\+\s*tpRR\s*\*\s*slDist"
                r"\s*\n\s*:\s*farEdge\s*-\s*tpRR\s*\*\s*slDist;", flat(OM_C))
      is not None)
check("the realized multiple is still documented as InpMaxRR + 3.0",
      "rr + 3.0" in OM_T or "(3.0 + rr)" in OM_T or "(3 + rr)" in OM_T)
check("the break barrier still sits ATRBufferBreak*ATR past the far edge",
      "close1 > breakLevel + ATRBufferBreak * atrNow" in BM_C and
      "close1 < breakLevel - ATRBufferBreak * atrNow" in BM_C)

# ----------------------------------------------------------------------
# 6. Retired inputs survive as DECLARED NO-OPS (.set compatibility)
# ----------------------------------------------------------------------
print("\n-- 6. Retired inputs --")

for name, val in (("InpUseStaleVeto", "false"), ("InpUseFrontRunVeto", "false"),
                  ("InpUseNearMissVeto", "false"), ("InpUseMomVeto", "false"),
                  ("InpUseFvgVeto", "false"),
                  ("InpCancelOpposingPendings", "false")):
    check("%s still exists, defaulting to %s" % (name, val),
          re.search(name + r"\s*=\s*" + val, DEFS_T) is not None)
    check("%s is labelled RETIRED (v5.80)" % name,
          re.search(name + r"[^\n]*RETIRED \(v5\.80\)", DEFS_T) is not None)

check("InpStaleDays survives for .set compatibility",
      re.search(r"InpStaleDays\s*=\s*45", DEFS_T) is not None)
check("InpConsensusVetoThreshold survives for .set compatibility",
      re.search(r"InpConsensusVetoThreshold\s*=\s*50", DEFS_T) is not None)
check("InpMaxRR is NOT retired -- it is the live TP multiple",
      re.search(r"InpMaxRR\s*=\s*4\.0", DEFS_T) is not None and
      "Take-profit projection" in DEFS_T)
check("InpUseSeparationVeto stays ENABLED (a surviving creation veto)",
      re.search(r"InpUseSeparationVeto\s*=\s*true", DEFS_T) is not None)

# ----------------------------------------------------------------------
# 7. Version stamps
# ----------------------------------------------------------------------
print("\n-- 7. Version stamps --")

stamps = re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T)
check("OttoDefines.mqh carries exactly one stamp", len(set(stamps)) == 1,
      "found %s" % sorted(set(stamps)))
# Derived from the live stamp rather than pinned: this probe asserts the strip's
# geometry and the retirement inventory, not the release number, so hardcoding
# a version here would go red on every bump for no reason. Coherence is what
# matters -- one stamp, echoed everywhere.
REL = stamps[0] if stamps else "?"
check("the release is a 5.x stamp", REL.startswith("5."), "found %r" % REL)
missing = [f for f in ALL_FILES
           if not re.search(r'#property version\s+"%s"' % re.escape(REL), read(f))]
check("all %d shipped sources carry %s" % (len(ALL_FILES), REL),
      not missing, "missing: %s" % ", ".join(missing))
check("no 5.72 stamp survives anywhere",
      all('"5.72"' not in read(f) for f in ALL_FILES))
check("the startup banner names v%s" % REL, "OTTO EA v" + REL in MAIN_T)


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.80 ARCHITECTURAL STRIP - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + detail) if detail else ""))
    print("-" * 74)
    print("  %d / %d checks passed" % (passed, total))
    print("=" * 74)
    if passed != total:
        print("*** FAILURES PRESENT ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

check("the operator-driven sim shields are KEPT",
      "InpSimNewsShield" in OM_C and "InpSimMacroVeto" in OM_C)
