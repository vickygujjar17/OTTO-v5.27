"""
v5.31 static verification probe - InpMaxRR + 0.90% symbol-scoped smart trim.

Two changes ship in v5.31, and neither is provable by the compiler gate:

  1. InpMaxRR. The Front-Run veto's target projection used to be the literal
     `3 * slDist`, re-typed at four separate sites (the order manager's TP and
     the veto's projection in two block-manager passes). The veto compares a
     resting order's `localTP` against the projection, so the moment a site
     drifted the veto compared against an unreachable target and silently
     retired itself - no error, no log, just a veto that stopped vetoing.
     All four sites must now read the ONE input.

  2. The 0.90% floating-loss breach is answered by a SMART TRIM instead of an
     immediate liquidation. Only NON-PRIMARY tranches that have already
     travelled >= InpTrimLoserStopPct of the way to their own stop are closed,
     the primary is retained under its own stop, and the full basket close
     still runs whenever nothing qualified so 0.90% stays a hard ceiling.

The trim is the risky part, because every property that makes it safe is a
runtime one:

  * SYMBOL + MAGIC SCOPING. Magic 20240624 is shared by all 28 OTTO charts in
    one account. A trim triggered on EURUSD must not close a GBPUSD tranche.
  * PRIMARY EXCLUSION. Tranche 1 carries the basket's risk geometry and is the
    leg the trail manager tracks; closing it would be an exit, not a trim. The
    ticket is read ONCE, before the walk, because ForceClose() clears tracked
    basket state on the last leg.
  * ONE-SHOT LATCH. OnTick re-tests the cap every tick. Without a latch a
    basket parked under water would be re-trimmed and re-logged every tick.
  * LATCH RAISED ONLY ON AN ACTUAL CLOSE. Otherwise the "nothing qualified"
    path would latch itself and stop escalating.
  * ESCALATION FALLBACK. `return true` means "could not act" so the caller
    still closes the basket. Signalling the opposite would make the trim a
    silent no-op that let the basket run past the cap.
  * DOWNWARD ITERATION. Apply-mode closes legs as it walks, so an ascending
    loop would skip the leg that slid down into the freed index.
  * GEOMETRY GUARDS. A leg with no stop, or whose stop is already at/past its
    entry, has no meaningful "% of the way to the stop" and must be skipped,
    not trimmed: that would be a profit/breakeven exit, not a loss cut.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
BM = os.path.join(ROOT, "COttoBlockManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


MAIN_T = read(MAIN)
OM_T = read(OM)
TM_T = read(TM)
BM_T = read(BM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals.

    Required because the v5.31 code documents the OLD behaviour in place, so
    raw substring tests on the function bodies would trip on their own
    explanatory prose. A '//' is only a comment when an even number of '"'
    precede it on the line.
    """
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def func_body(code, sig):
    """Return the { ... } block following the first line matching `sig`."""
    m = re.search(sig, code)
    if not m:
        return None
    i = code.find("{", m.end())
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[m.start():j + 1]
    return code[m.start():]


TM_C = strip_comments(TM_T)
OM_C = strip_comments(OM_T)
BM_C = strip_comments(BM_T)

WALK = func_body(TM_C, r"int\s+WalkTrimLegs\s*\(double\s+trimPct,\s*bool\s+dryRun,\s*int\s+&outClosed\)")
TRIM = func_body(TM_C, r"bool\s+TrimHeavyLosers\s*\(double\s+trimPct\)")


# ----------------------------------------------------------------------
# 1. InpMaxRR is the single source of truth for the R projection
# ----------------------------------------------------------------------
print("\n-- InpMaxRR single source of truth --")

check("InpMaxRR is declared as an input in OttoDefines.mqh",
      re.search(r"^input\s+double\s+InpMaxRR\s*=\s*4\.0\s*;", DEFS_T, re.M) is not None)

# The whole point of the change: no literal multiple of slDist may survive.
hardcoded = []
for f in ALL_FILES:
    body = strip_comments(read(os.path.join(ROOT, f)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*\*\s*(slDist|calcSLDist)\b", body):
        if m.group(1) != "1":
            hardcoded.append("%s: %s * %s" % (f, m.group(1), m.group(2)))
check("no hardcoded <n> * slDist risk projection remains", not hardcoded,
      ", ".join(hardcoded))

check("order manager falls back to 1.0 when InpMaxRR <= 0",
      re.search(r"double\s+tpRR\s*=\s*\(InpMaxRR\s*>\s*0\.0\)\s*\?\s*InpMaxRR"
                r"\s*:\s*1\.0\s*;", OM_C) is not None)
check("order manager projects the take-profit from tpRR * slDist",
      re.search(r"double\s+takeProfit\s*=\s*isLong\s*"
                r"\?\s*entryPrice\s*\+\s*tpRR\s*\*\s*slDist", OM_C) is not None and
      re.search(r":\s*entryPrice\s*-\s*tpRR\s*\*\s*slDist\s*;", OM_C) is not None)
check("order manager stores the projection as block.localTP",
      re.search(r"block\.localTP\s*=\s*takeProfit\s*;", OM_C) is not None)
check("order manager still records block.rrUnit = slDist",
      re.search(r"block\.rrUnit\s*=\s*slDist\s*;", OM_C) is not None)

# Both block-manager passes (deletion pass + the OnTick re-check) must use the
# same input, or one of them decides against a target the other never sends.
bm_sites = re.findall(r"double\s+tpRR\s*=\s*\(InpMaxRR\s*>\s*0\.0\)\s*\?\s*InpMaxRR"
                      r"\s*:\s*1\.0\s*;", BM_C)
check("both block-manager front-run passes read InpMaxRR", len(bm_sites) == 2,
      "found %d site(s)" % len(bm_sites))
check("block-manager front-run projects from tpRR * calcSLDist",
      re.search(r"double\s+target\s*=\s*isLong\s*"
                r"\?\s*calcEntry\s*\+\s*tpRR\s*\*\s*calcSLDist", BM_C) is not None and
      re.search(r":\s*calcEntry\s*-\s*tpRR\s*\*\s*calcSLDist\s*;", BM_C) is not None)
check("front-run prefers the exact sent TP once the order is live",
      BM_C.count("target = b.localTP;") == 2,
      "found %d" % BM_C.count("target = b.localTP;"))
check("OttoDefines documents InpMaxRR as the Front-Run/TP projection",
      "InpMaxRR target hit before entry" in DEFS_T and
      "InpMaxRR projection (used ONLY for Front-Run Veto)" in DEFS_T)


# ----------------------------------------------------------------------
# 2. 0.90% cap + trim threshold inputs
# ----------------------------------------------------------------------
print("\n-- Cap and threshold inputs --")

check("SafetyMaxFloatingLoss is 0.90",
      re.search(r"^input\s+double\s+SafetyMaxFloatingLoss\s*=\s*0\.90\s*;",
                DEFS_T, re.M) is not None)
check("InpTrimLoserStopPct is 70.0",
      re.search(r"^input\s+double\s+InpTrimLoserStopPct\s*=\s*70\.0\s*;",
                DEFS_T, re.M) is not None)
check("the cap input is documented as a smart-trim ceiling",
      "smart-trim at this % floating loss" in DEFS_T)


# ----------------------------------------------------------------------
# 3. WalkTrimLegs - the filter
# ----------------------------------------------------------------------
print("\n-- WalkTrimLegs filter --")

check("WalkTrimLegs located", WALK is not None)
if WALK is not None:
    w = WALK
    check("walk is scoped to this chart's symbol",
          re.search(r"PositionGetString\(POSITION_SYMBOL\)\s*!=\s*m_symbol", w) is not None)
    check("walk is scoped to OTTO's magic",
          re.search(r"PositionGetInteger\(POSITION_MAGIC\)\s*!=\s*magic", w) is not None)
    check("magic is cast from MagicNumber once, before the loop",
          re.search(r"long\s+magic\s*=\s*\(long\)MagicNumber\s*;", w) is not None)
    # The primary ticket must be captured BEFORE the walk: ForceClose() clears
    # the tracked-basket state when it closes the last leg, so reading it
    # inside the loop would silently change the exclusion set mid-walk.
    pos_primary = w.find("ulong primary = m_orderManager.GetActiveTrade().ticket;")
    pos_loop = w.find("for(int idx")
    check("primary ticket is read once, before the loop",
          0 <= pos_primary < pos_loop)
    check("primary tranche is excluded from the trim",
          re.search(r"ticket\s*==\s*primary", w) is not None)
    check("walk iterates DOWNWARD for index-shift safety",
          re.search(r"for\(int\s+idx\s*=\s*PositionsTotal\(\)\s*-\s*1\s*;\s*idx\s*>=\s*0"
                    r"\s*;\s*idx--\)", w) is not None)
    check("a leg with no stop is skipped, not trimmed",
          re.search(r"if\(sl\s*<=\s*0\.0\)\s*continue\s*;", w) is not None)
    check("effective stop tightens toward the session stop (long)",
          re.search(r"if\(isLong\)\s*sl\s*=\s*MathMax\(sl,\s*sessionSL\)", w) is not None)
    check("effective stop tightens toward the session stop (short)",
          re.search(r"else\s+sl\s*=\s*MathMin\(sl,\s*sessionSL\)", w) is not None)
    check("session stop only applied when usable",
          re.search(r"if\(sessionSL\s*>\s*0\.0\)", w) is not None)
    check("a stop at/past entry (protected leg) is skipped",
          re.search(r"if\(total\s*<=\s*0\.0\)\s*continue\s*;", w) is not None)
    check("distance is measured in the leg's own direction",
          re.search(r"double\s+total\s*=\s*isLong\s*\?\s*\(entry\s*-\s*sl\)\s*:\s*\(sl\s*-\s*entry\)",
                    w) is not None)
    check("progress is measured in the leg's own direction",
          re.search(r"double\s+moved\s*=\s*isLong\s*\?\s*\(entry\s*-\s*mark\)\s*:\s*\(mark\s*-\s*entry\)",
                    w) is not None)
    check("threshold is trimPct percent of the leg's own stop distance",
          re.search(r"if\(moved\s*<\s*\(trimPct\s*/\s*100\.0\)\s*\*\s*total\)\s*continue\s*;",
                    w) is not None)
    # dryRun must bail BEFORE ForceClose, or a diagnostic would trade.
    pos_dry = w.find("if(dryRun) continue;")
    pos_close = w.find("ForceClose(ticket)")
    check("dry run bails out before any close", 0 <= pos_dry < pos_close)
    check("dry run reaches the same threshold test as the live path",
          pos_dry > w.find("(trimPct / 100.0) * total"))
    check("the trim closes the single leg, never the basket",
          "CloseEntireBasket" not in w and "ForceClose(ticket)" in w)
    check("walk counts every qualifying leg", re.search(r"qualified\+\+\s*;", w) is not None)
    check("walk returns the qualifying count", re.search(r"return\s+qualified\s*;", w) is not None)
    check("closed-counter is reset on entry",
          re.search(r"outClosed\s*=\s*0\s*;", w) is not None)
    check("closed-counter only increments on a confirmed close",
          re.search(r"if\(m_orderManager\.ForceClose\(ticket\)\)\s*\n\s*\{\s*\n\s*outClosed\+\+;",
                    w) is not None)
else:
    for name in ("symbol scoping", "magic scoping", "primary exclusion",
                 "downward iteration"):
        check("walk: %s" % name, False, "WalkTrimLegs not located")


# ----------------------------------------------------------------------
# 4. TrimHeavyLosers - the escalation contract
# ----------------------------------------------------------------------
print("\n-- TrimHeavyLosers contract --")

check("TrimHeavyLosers located", TRIM is not None)
if TRIM is not None:
    t = TRIM
    check("latch short-circuits a repeat call for the same breach",
          re.search(r"if\(m_trimLogged\)\s*return\s+false\s*;", t) is not None)
    check("walk runs in apply mode", "WalkTrimLegs(trimPct, false, closed)" in norm(t))
    # The escalation decision must rest on the CLOSED count, not on how many
    # legs qualified: a qualified leg that failed to close relieved nothing.
    pos_none = t.find("if(closed == 0)")
    pos_latch = t.find("m_trimLogged = true;")
    check("escalation keys off the closed count", pos_none > 0)
    check("returns true (caller must full-close) when nothing was closed",
          pos_none > 0 and re.search(r"if\(closed\s*==\s*0\)\s*\n\s*\{.*?return\s+true\s*;",
                                     t, re.S) is not None)
    # The "nothing relieved" path must NOT raise the latch: if it did, the
    # first tick that found no qualifying leg would disarm the trim forever and
    # the basket would sail past the cap with no escalation on later ticks.
    closedzero = re.search(r"if\(closed\s*==\s*0\)\s*\{(.*?)\n\s*\}", t, re.S)
    check("the nothing-closed path does not raise the latch",
          closedzero is not None and "m_trimLogged" not in closedzero.group(1),
          "latch write found inside the closed==0 block")
    check("latch is raised only AFTER a successful close", pos_latch > pos_none)
    check("returns false once a tranche was trimmed",
          pos_latch > 0 and t.find("return false;", pos_latch) > pos_latch)
    check("empty book resets the latch instead of escalating",
          re.search(r"if\(!m_orderManager\.HasActiveTrade\(\)\s*&&\s*"
                    r"m_orderManager\.CountOpenPositions\(\)\s*==\s*0\)\s*\n\s*\{\s*\n"
                    r"\s*m_trimLogged\s*=\s*false\s*;\s*\n\s*return\s+false\s*;", t) is not None)
    check("trim never reaches for the whole-basket close itself",
          "CloseEntireBasket" not in t)
    check("trim logs the qualifying-vs-closed pair for the operator",
          "qualifying tranche(s)" in TM_T and "nothing to trim" in TM_T)
else:
    check("TrimHeavyLosers body extracted", False)


# ----------------------------------------------------------------------
# 5. Latch lifecycle - arm once, clear on the non-breach path
# ----------------------------------------------------------------------
print("\n-- Trim latch lifecycle --")

TM_CLEAN = strip_comments(TM_T)
check("m_trimLogged member declared", re.search(r"bool\s+m_trimLogged\s*;", TM_T) is not None)
check("m_trimLogged initialised in the constructor",
      re.search(r"m_trimLogged\s*=\s*false\s*;", TM_CLEAN) is not None)
check("ClearTrimLatch exists and simply drops the latch",
      re.search(r"void\s+ClearTrimLatch\s*\(void\)\s*\{\s*m_trimLogged\s*=\s*false\s*;\s*\}",
                TM_CLEAN) is not None)
check("ClearTrimLatch is public",
      TM_T.find("public:") < TM_T.find("ClearTrimLatch"))

# In otto.mq5 the latch must be released ONLY on the path where the float is
# back under the cap - i.e. after the breach branch has returned.
breach = re.search(r"if\(floatingLoss\s*>=\s*SafetyMaxFloatingLoss\)", MAIN_T)
clear = MAIN_T.find("g_tradeManager.ClearTrimLatch();")
daily = MAIN_T.find("if(dailyDD >= SafetyDailyDDLimit)")
check("otto.mq5 calls ClearTrimLatch", clear > 0)
check("otto.mq5 clears the latch outside the breach branch",
      breach is not None and 0 <= breach.start() < clear)
check("the clear sits before the daily-drawdown block (non-breach path)",
      clear > 0 and 0 < clear < daily, "clear=%d daily=%d" % (clear, daily))
check("the breach branch returns before the clear is reached",
      breach is not None and
      re.search(r"return;\s*\n\s*\}\s*\n", MAIN_T[breach.start():clear]) is not None)


# ----------------------------------------------------------------------
# 6. CountTrimCandidates - read-only diagnostic
# ----------------------------------------------------------------------
print("\n-- CountTrimCandidates diagnostic --")

CNT = func_body(TM_CLEAN, r"int\s+CountTrimCandidates\s*\(double\s+trimPct\)")
check("CountTrimCandidates located", CNT is not None)
if CNT is not None:
    check("CountTrimCandidates runs the walk in dry-run mode",
          "WalkTrimLegs(trimPct, true, closed)" in norm(CNT))
    check("CountTrimCandidates never closes anything",
          "ForceClose" not in CNT and "CloseEntireBasket" not in CNT)
    check("CountTrimCandidates returns 0 on an empty book",
          re.search(r"CountOpenPositions\(\)\s*==\s*0\)\s*\n\s*return\s+0\s*;", CNT) is not None)
    check("CountTrimCandidates is public",
          TM_T.find("public:") < TM_T.find("CountTrimCandidates"))
else:
    check("CountTrimCandidates body extracted", False)


# ----------------------------------------------------------------------
# 7. otto.mq5 - the breach response
# ----------------------------------------------------------------------
print("\n-- otto.mq5 breach response --")

b = re.search(r"if\(floatingLoss\s*>=\s*SafetyMaxFloatingLoss\)\s*\n\s*\{.*?"
              r"(?=\n\s*// 3% Max Daily Drawdown)", MAIN_T, re.S)
check("0.90% breach branch located", b is not None)
if b:
    seg = b.group(0)
    check("pendings are still cancelled first", seg.find("CancelAllPendingOrders()") > 0)
    check("the trim is gated on a usable threshold",
          re.search(r"if\(InpTrimLoserStopPct\s*>\s*0\.0\s*&&\s*InpTrimLoserStopPct\s*<\s*100\.0\)",
                    seg) is not None)
    check("the trim runs before the full close",
          0 < seg.find("TrimHeavyLosers(InpTrimLoserStopPct)")
          < seg.find('CloseEntireBasket("1% Max Floating Loss Breach")'))
    check("full close is the DEFAULT and the trim can only clear it",
          re.search(r"bool\s+needFullClose\s*=\s*true\s*;", seg) is not None and
          re.search(r"needFullClose\s*=\s*g_tradeManager\.TrimHeavyLosers"
                    r"\(InpTrimLoserStopPct\)\s*;", seg) is not None)
    check("the full close is conditional on needFullClose",
          re.search(r"if\(needFullClose\)\s*\n\s*g_orderManager\.CloseEntireBasket\(",
                    seg) is not None)
    check("branch still returns for this tick",
          re.search(r"\breturn\s*;", seg) is not None)
    check("branch still does NOT latch a halt",
          "g_totalDD_Halted = true" not in seg and
          'OttoGvStoreFlag("Halted"' not in seg)
    check("the breach banner announces the smart trim",
          "SMART TRIM APPLIED" in seg)
    check("the banner still reports the cap it breached",
          "SafetyMaxFloatingLoss, 2)" in seg)
    check("the branch still requires an open book before acting",
          re.search(r"HasActiveTrade\(\)\s*\|\|\s*g_orderManager\.CountOpenPositions\(\)\s*>\s*0",
                    seg) is not None)
else:
    check("0.90% breach branch extracted", False)

check("the retained primary is documented in both files",
      "primary retained" in TM_T and
      "primary is deliberately left running" in MAIN_T)


# ----------------------------------------------------------------------
# 8. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.31 or later", parts >= (5, 31), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 10 files stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)
check("Pine port banner names the current release",
      ("Master Build Port (v%s)" % RELEASE) in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.31 InpMaxRR + 0.90% SCOPED SMART TRIM - STATIC PROBE")
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
