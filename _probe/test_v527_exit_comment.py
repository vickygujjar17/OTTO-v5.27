"""
v5.27 static verification probe - Exit/Trailing milestones + Order comment ID.

Pins the behaviour that cannot be exercised by the MQL5 compiler gate:

  1. Breakeven fires at 1:1 and moves to COST-COVERING breakeven (+/- beOffset).
  2. Step profit lock: trigger InpLockProfitRR, target InpLockProfitTargetRR,
     spaced exactly 1R apart, with BE one further R below the lock target.
  3. The step lock is a ONE-WAY ratchet (guarded comparison on both branches).
  4. The lock is disable-able by zeroing either input.
  5. The dynamic ATR trail still exists on BOTH branches past InpLock3RRR.
  6. The ATR trail is evaluated AFTER the step lock (tighter of the two wins).
  7. Tranche 2 is decoupled from InpBreakEvenRR onto InpPyramidT2RR.
  8. SyncTradeState() classification follows the new milestones.
  9. CalcBasketFriction still accounts for commission + swap + half-spread.
 10. Order comments route through the clamped builder at all three sites.
 11. The clamp never exceeds 31 chars for the longest realistic identifier.
 12. The clamp preserves the -BLK<n> tail (no blind head truncation).
 13. The clamp leaves short strings untouched and handles <=31 exactly.
 14. Version stamp 5.27 present; no 5.26 property stamp survives.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

# The 10 files that must all carry the 5.27 stamp.
ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


TM_T = read(TM)
OM_T = read(OM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


# ----------------------------------------------------------------------
# 0. Re-implementation of the shipped ClampOrderComment() in Python.
#    Mirrors the MQL5 source so the length/tail guarantees can be asserted
#    against real-world identifiers without an MT5 runtime.
# ----------------------------------------------------------------------
MAX_COMMENT = 31


def clamp_order_comment(s):
    if len(s) <= MAX_COMMENT:
        return s
    if s[:1] == "#":
        p = s.find("-")
        if p > 0:
            q = s.find("-", p + 1)
            if q > 0:
                seg = s[p + 1:q]
                if len(seg) >= 8 and seg[0].isdigit():
                    trimmed = s[:p + 1] + s[q + 1:]
                    if len(trimmed) <= MAX_COMMENT:
                        return trimmed
                    s = trimmed
    if len(s) > MAX_COMMENT:
        # Mirrors TAIL_LEN=5 / HEAD_LEN=MAX-5-1 in the shipped source.
        tail_len = 5
        head_len = MAX_COMMENT - tail_len - 1
        if head_len < 1:
            head_len = 1
        s = s[:head_len] + "~" + s[len(s) - tail_len:]
        if len(s) > MAX_COMMENT:
            s = s[:MAX_COMMENT]
    return s


def build_order_comment(session_id, symbol, block_serial, tranche=0):
    if session_id:
        base = session_id
    else:
        base = "OTTO_%s_%d" % (symbol, block_serial)
    if tranche > 1:
        base = base + "_T" + str(tranche)
    return clamp_order_comment(base)


# ----------------------------------------------------------------------
# 1. Breakeven at 1:1, cost-covering
# ----------------------------------------------------------------------
check("InpBreakEvenRR default is 1.0",
      re.search(r"InpBreakEvenRR\s*=\s*1\.0\s*;", DEFS_T) is not None)

check("InpBreakEvenRR no longer defaults to 2.0",
      re.search(r"InpBreakEvenRR\s*=\s*2\.0", DEFS_T) is None)

check("LONG breakeven uses primaryEntry + beOffset",
      re.search(r"beSL\s*=\s*primaryEntry\s*\+\s*beOffset\s*;", TM_T) is not None)


# ----------------------------------------------------------------------
# 2. Step profit lock inputs + milestone spacing
# ----------------------------------------------------------------------
check("InpLockProfitRR default is 3.0",
      re.search(r"InpLockProfitRR\s*=\s*3\.0\s*;", DEFS_T) is not None)

check("InpLockProfitTargetRR default is 2.0",
      re.search(r"InpLockProfitTargetRR\s*=\s*2\.0\s*;", DEFS_T) is not None)

m_be = re.search(r"InpBreakEvenRR\s*=\s*([0-9.]+)\s*;", DEFS_T)
m_tt = re.search(r"InpLockProfitTargetRR\s*=\s*([0-9.]+)\s*;", DEFS_T)
m_tr = re.search(r"InpLockProfitRR\s*=\s*([0-9.]+)\s*;", DEFS_T)
if m_be and m_tt and m_tr:
    be_v, tt_v, tr_v = float(m_be.group(1)), float(m_tt.group(1)), float(m_tr.group(1))
    check("milestones strictly ordered BE < lockTarget < lockTrigger",
          be_v < tt_v < tr_v, "BE=%s target=%s trigger=%s" % (be_v, tt_v, tr_v))
    check("lock target sits exactly 1R above breakeven",
          abs((tt_v - be_v) - 1.0) < 1e-9, "gap=%s" % (tt_v - be_v))
    check("lock trigger sits exactly 1R above lock target",
          abs((tr_v - tt_v) - 1.0) < 1e-9, "gap=%s" % (tr_v - tt_v))
else:
    check("milestones strictly ordered BE < lockTarget < lockTrigger", False, "inputs not found")

# ----------------------------------------------------------------------
# 3. One-way ratchet guards, both branches, R-scaled target
# ----------------------------------------------------------------------
check("LONG step lock computes primaryEntry + (target * rrUnit)",
      re.search(r"lockedSL\s*=\s*primaryEntry\s*\+\s*\(InpLockProfitTargetRR\s*\*\s*rrUnit\)",
                TM_T) is not None)

check("SHORT step lock computes primaryEntry - (target * rrUnit)",
      re.search(r"lockedSL\s*=\s*primaryEntry\s*-\s*\(InpLockProfitTargetRR\s*\*\s*rrUnit\)",
                TM_T) is not None)

check("LONG lock only ratchets forward (lockedSL > desiredSL)",
      re.search(r"if\(lockedSL\s*>\s*desiredSL\)\s*desiredSL\s*=\s*lockedSL\s*;", TM_T) is not None)

check("SHORT lock only ratchets forward (lockedSL < desiredSL)",
      re.search(r"if\(lockedSL\s*<\s*desiredSL\)\s*desiredSL\s*=\s*lockedSL\s*;", TM_T) is not None)

n_guard = len(re.findall(
    r"InpLockProfitRR\s*>\s*0\.0\s*&&\s*InpLockProfitTargetRR\s*>\s*0\.0", TM_T))
check("lock disable-guard present on both LONG and SHORT branches",
      n_guard >= 2, "found %d" % n_guard)

# ----------------------------------------------------------------------
# 4. Dynamic ATR trail preserved, evaluated LAST
# ----------------------------------------------------------------------
check("LONG dynamic ATR trail = high0 - (mult * atr)",
      re.search(r"dynamicTrail\s*=\s*high0\s*-\s*\(InpTrailATRMultiplier\s*\*\s*atr\)",
                TM_T) is not None)

check("SHORT dynamic ATR trail = low0 + (mult * atr)",
      re.search(r"dynamicTrail\s*=\s*low0\s*\+\s*\(InpTrailATRMultiplier\s*\*\s*atr\)",
                TM_T) is not None)

lock_at = TM_T.find("lockedSL")
trail_at = TM_T.find("double dynamicTrail")
check("ATR trail evaluated after the step lock (tighter wins)",
      lock_at > 0 and trail_at > lock_at, "lock@%d trail@%d" % (lock_at, trail_at))

check("LONG trail still ratchets forward only",
      re.search(r"dynamicTrail\s*>\s*desiredSL\)\s*desiredSL\s*=\s*dynamicTrail\s*;", TM_T) is not None)

check("SHORT trail still ratchets forward only",
      re.search(r"dynamicTrail\s*<\s*desiredSL\)\s*desiredSL\s*=\s*dynamicTrail\s*;", TM_T) is not None)


check("SHORT breakeven uses primaryEntry - beOffset",
      re.search(r"beSL\s*=\s*primaryEntry\s*-\s*beOffset\s*;", TM_T) is not None)



# ----------------------------------------------------------------------
# 5. Tranche 2 decoupled from breakeven
# ----------------------------------------------------------------------
check("InpPyramidT2RR input exists and defaults to 2.0",
      re.search(r"InpPyramidT2RR\s*=\s*2\.0\s*;", DEFS_T) is not None)

check("Tranche 2 gate reads InpPyramidT2RR",
      re.search(r"currentRR\s*>=\s*InpPyramidT2RR\s*&&\s*m_orderManager\.IsPyramidPending\(2\)",
                TM_T) is not None)

check("Tranche 2 gate NO LONGER reads InpBreakEvenRR",
      re.search(r"currentRR\s*>=\s*InpBreakEvenRR\s*&&\s*m_orderManager\.IsPyramidPending\(2\)",
                TM_T) is None)

# ----------------------------------------------------------------------
# 6. SyncTradeState classification
# ----------------------------------------------------------------------
check("SyncTradeState classifies STEP_TRAILING off InpLockProfitRR",
      re.search(r"rr\s*>=\s*InpLockProfitRR\)\s*step\s*=\s*STEP_TRAILING", TM_T) is not None)

check("SyncTradeState still classifies STEP_BREAKEVEN off InpBreakEvenRR",
      re.search(r"rr\s*>=\s*InpBreakEvenRR\)\s*step\s*=\s*STEP_BREAKEVEN", TM_T) is not None)

check("SyncTradeState still classifies STEP_HALF_RISK off InpCutRiskRR",
      re.search(r"rr\s*>=\s*InpCutRiskRR\)\s*step\s*=\s*STEP_HALF_RISK", TM_T) is not None)

# ----------------------------------------------------------------------
# 7. CalcBasketFriction still accounts for commission + swap + half-spread
# ----------------------------------------------------------------------
check("CalcBasketFriction accumulates commission",
      "GetPositionCommission(t.ticket)" in TM_T)

check("CalcBasketFriction accumulates swap",
      "PositionGetDouble(POSITION_SWAP)" in TM_T)

check("CalcBasketFriction folds in half the spread",
      re.search(r"spread\s*\*\s*0\.5", TM_T) is not None)

# ----------------------------------------------------------------------
# 8. Order comment routing
# ----------------------------------------------------------------------
check("ClampOrderComment helper exists in COttoOrderManager",
      "ClampOrderComment(string s)" in OM_T)

check("BuildOrderComment helper exists with blockSerial + tranche params",
      re.search(r"BuildOrderComment\(const\s+int\s+blockSerial\s*=\s*0,\s*const\s+int\s+tranche\s*=\s*0\)",
                OM_T) is not None)

check("clamp enforces the 31-char MT5 limit",
      re.search(r"MAX_COMMENT\s*=\s*31", OM_T) is not None)

check("PlaceLimitOrder path uses BuildOrderComment(block.serial, 0)",
      "request.comment  = BuildOrderComment(block.serial, 0);" in OM_T)

check("AddPyramidTranche path uses BuildOrderComment(..., tranche)",
      "req.comment  = BuildOrderComment(m_activeTrade.sourceBlockSerial, tranche);" in OM_T)

check("plain market path no longer sends bare TradeComment",
      "request.comment   = TradeComment;" not in OM_T)

check("no unclamped TradeComment+_PYR_T concatenation remains",
      'TradeComment + "_PYR_T"' not in OM_T)

check("helper falls back to OTTO_<sym>_<serial> when journal is absent",
      re.search(r'StringFormat\("OTTO_%s_%d",\s*m_symbol,\s*blockSerial\)', OM_T) is not None)

check("helper NULL-guards the journal pointer",
      re.search(r"if\(m_journal\s*!=\s*NULL\s*&&\s*m_journal\.GetSessionID\(\)\s*!=\s*\"\"\)",
                OM_T) is not None)

check("CalcBasketFriction feeds the LONG breakeven block",
      re.search(r"double\s+beOffset\s*=\s*CalcBasketFriction\(true\)\s*;", TM_T) is not None)

check("CalcBasketFriction feeds the SHORT breakeven block",
      re.search(r"double\s+beOffset\s*=\s*CalcBasketFriction\(false\)\s*;", TM_T) is not None)




# ----------------------------------------------------------------------
# 9. Clamp behaviour (mirrored implementation, realistic identifiers)
# ----------------------------------------------------------------------
LONG_ID = "#OTTO-EURUSD-20260922-143005-BLK3"      # 34 chars, no suffix
SUFX_ID = "#OTTO-EURUSD.x-20260922-143005-BLK3"    # 36 chars, suffixed broker

check("raw long identifier really does exceed 31", len(LONG_ID) > 31,
      "len=%d" % len(LONG_ID))

for label, raw in (("plain", LONG_ID), ("suffixed", SUFX_ID)):
    out = clamp_order_comment(raw)
    check("clamp(%-8s) length <= 31" % label, len(out) <= 31, "got %d" % len(out))
    check("clamp(%-8s) never empty" % label, len(out) > 0)

out_plain = clamp_order_comment(LONG_ID)
check("clamp keeps the BLK serial tail (plain id)", out_plain.endswith("BLK3"), out_plain)

out_sufx = clamp_order_comment(SUFX_ID)
check("clamp keeps the BLK serial tail (suffixed id)", "BLK3" in out_sufx, out_sufx)

check("clamp still identifies the symbol", "EURUSD" in out_plain, out_plain)

check("short comment untouched", clamp_order_comment("OTTO_XAUUSD_1") == "OTTO_XAUUSD_1")

EXACT31 = "#OTTO-EURUSD-143005-BLK31234567"[:31]
check("boundary: 31-char input is unchanged",
      len(EXACT31) == 31 and clamp_order_comment(EXACT31) == EXACT31,
      "len=%d" % len(EXACT31))

PATHO = "#OTTO-" + "X" * 60 + "-20260922-143005-BLK1"
check("clamp handles pathological over-long id",
      len(clamp_order_comment(PATHO)) <= 31)

t3 = build_order_comment(LONG_ID, "EURUSD", 3, tranche=3)
check("tranche tag survives clamping", "_T3" in t3, t3)
check("tranche result <= 31", len(t3) <= 31, "len=%d" % len(t3))

fb = build_order_comment("", "XAUUSD", 7)
check("fallback builds OTTO_<sym>_<serial>", fb == "OTTO_XAUUSD_7", fb)
check("fallback <= 31", len(fb) <= 31)

worst = 0
for sid in (LONG_ID, SUFX_ID, "", "#OTTO-XAUUSD.m-20260922-235959-BLK99"):
    for sym in ("EURUSD", "XAUUSD", "GBPJPY", "AUDCAD"):
        for ser in (1, 99, 9999):
            for tr in (0, 2, 3):
                c = build_order_comment(sid, sym, ser, tr)
                if len(c) > worst:
                    worst = len(c)
                assert len(c) <= 31, (sid, sym, ser, tr, c)
check("exhaustive combo sweep never exceeds 31", worst <= 31, "max=%d" % worst)

# ----------------------------------------------------------------------
# 10. Version stamps
# ----------------------------------------------------------------------
missing = [f for f in ALL_FILES
           if '#property version   "5.27"' not in read(os.path.join(ROOT, f))]
check("all 10 files stamp 5.27", not missing, "missing: %s" % ", ".join(missing))

stale = [f for f in ALL_FILES
         if '#property version   "5.26"' in read(os.path.join(ROOT, f))]
check("no 5.26 property stamp survives", not stale, "stale: %s" % ", ".join(stale))

check("OttoDefines banner names v5.27", "OTTO EA v5.27" in DEFS_T)

check("OttoDefines description names v5.27",
      '#property description "OTTO v5.27' in DEFS_T)



def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.27 EXIT/TRAILING MILESTONES + ORDER COMMENT - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        line = "  [%s] %s" % (mark, name)
        if not ok and detail:
            line += "  <%s>" % detail
        print(line)
    print("-" * 74)
    print("%d / %d checks passed" % (passed, total))
    if passed != total:
        print("*** PROBE FAILED ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
