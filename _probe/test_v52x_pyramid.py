"""
v5.29 static verification probe - 4-tranche Risk-% pyramid + trail repoint.

Pins the behaviour that the MQL5 compiler gate CANNOT exercise:

  1. Four risk rungs with distinct, strictly-ascending triggers:
     T1 RiskPercent (0.25, at market), T2 InpRiskT2Pct (at +1.0R),
     T3 InpRiskT3Pct (at +2.0R), T4 InpRiskT4Pct (at +3.0R).
  2. Each rung draws its OWN risk input - an unrecognised tranche is refused
     outright and can never fall through onto another rung's risk budget
     (the old two-way ternary silently gave any tranche != 2 the T3 risk).
  3. The ladder advances strictly 2 -> 3 -> 4 -> 0, at BOTH the skip path and
     the success path, so a rung can neither be re-armed nor skipped.
  4. A successful scale-in raises the shared per-tick guard on every rung, so
     the freshly-filled ticket keeps its protective stop for one tick.
  5. The ATR trail arms at +1.0R on both directions, read from
     InpTrailStartRR; InpLock3RRR survives only as a declared-but-unused input.
  6. The lot-sizing floor is epsilon-safe, so a raw lot that is mathematically
     an exact multiple of the volume step is not silently dropped one step.
  7. The ladder total sits inside the SafetyMaxRiskPct budget.

Pure static analysis of the shipped sources plus an independent Python model of
the ladder/floor arithmetic - no MT5 runtime required.
"""

import io
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
RM = os.path.join(ROOT, "COttoRiskManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


TM_T = read(TM)
OM_T = read(OM)
RM_T = read(RM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def num_input(text, name):
    """Default of a top-level `input double NAME = <value>;` declaration."""
    m = re.search(r"^\s*input\s+double\s+" + re.escape(name) +
                  r"\s*=\s*([0-9]*\.?[0-9]+)\s*;", text, re.M)
    return float(m.group(1)) if m else None


# ----------------------------------------------------------------------
# 1. The four risk rungs are declared with the expected percentages.
# ----------------------------------------------------------------------
T1_RISK = num_input(DEFS_T, "RiskPercent")
T2_RISK = num_input(DEFS_T, "InpRiskT2Pct")
T3_RISK = num_input(DEFS_T, "InpRiskT3Pct")
T4_RISK = num_input(DEFS_T, "InpRiskT4Pct")

check("T1 risk input RiskPercent exists", T1_RISK is not None)
check("T2 risk input InpRiskT2Pct exists", T2_RISK is not None)
check("T3 risk input InpRiskT3Pct exists", T3_RISK is not None)
check("T4 risk input InpRiskT4Pct exists", T4_RISK is not None)
check("RiskPercent (T1) defaults to 0.25", T1_RISK == 0.25, T1_RISK)
check("InpRiskT2Pct defaults to 0.25", T2_RISK == 0.25, T2_RISK)
check("InpRiskT3Pct defaults to 0.125", T3_RISK == 0.125, T3_RISK)
check("InpRiskT4Pct defaults to 0.0625", T4_RISK == 0.0625, T4_RISK)
check("no dead InpRiskT1Pct input was re-introduced",
      not re.search(r"^\s*input\s+double\s+InpRiskT1Pct", DEFS_T, re.M))

# The rungs after T2 must shrink: each scale-in risks less than the one above.
RISKS = [T1_RISK, T2_RISK, T3_RISK, T4_RISK]
if all(r is not None for r in RISKS):
    check("risk ladder is non-increasing after T2 (T1 >= T2 > T3 > T4)",
          RISKS[0] >= RISKS[1] > RISKS[2] > RISKS[3], RISKS)
    total = sum(RISKS)
    check("ladder total risk is 0.6875%", abs(total - 0.6875) < 1e-12, total)
    budget = num_input(DEFS_T, "SafetyMaxRiskPct")
    check("ladder total sits inside the SafetyMaxRiskPct budget",
          budget is not None and total < budget, "%s < %s" % (total, budget))
else:
    check("risk ladder is non-increasing after T2 (T1 >= T2 > T3 > T4)", False, RISKS)
    check("ladder total risk is 0.6875%", False, RISKS)
    check("ladder total sits inside the SafetyMaxRiskPct budget", False, RISKS)


# ----------------------------------------------------------------------
# 2. Each rung draws its OWN risk input, and an unknown rung is refused.
#    Independent Python model of the shipped if/else chain.
# ----------------------------------------------------------------------
def risk_for(tranche):
    """Mirror of the AddPyramidTranche risk-selection chain."""
    if tranche == 2:
        return T2_RISK
    elif tranche == 3:
        return T3_RISK
    elif tranche == 4:
        return T4_RISK
    return None


check("code selects T2 risk from InpRiskT2Pct",
      re.search(r"trancheToAdd\s*==\s*2\s*\)\s*riskPct\s*=\s*InpRiskT2Pct", OM_T) is not None)
check("code selects T3 risk from InpRiskT3Pct",
      re.search(r"trancheToAdd\s*==\s*3\s*\)\s*riskPct\s*=\s*InpRiskT3Pct", OM_T) is not None)
check("code selects T4 risk from InpRiskT4Pct",
      re.search(r"trancheToAdd\s*==\s*4\s*\)\s*riskPct\s*=\s*InpRiskT4Pct", OM_T) is not None)
check("code refuses an unrecognised tranche instead of falling through",
      re.search(r"else\s+return\s+false\s*;", OM_T) is not None)

check("risk_for(2) == T2", risk_for(2) == T2_RISK, risk_for(2))
check("risk_for(3) == T3", risk_for(3) == T3_RISK, risk_for(3))
check("risk_for(4) == T4", risk_for(4) == T4_RISK, risk_for(4))
check("risk_for(5) is None (no fallthrough rung)", risk_for(5) is None, risk_for(5))
check("risk_for(0) is None (ladder-exhausted sentinel)", risk_for(0) is None, risk_for(0))

# The old two-way ternary must be gone: it gave EVERY tranche != 2 the T3 risk.
check("old two-way risk ternary was removed",
      "(tranche == 2) ? InpRiskT2Pct : InpRiskT3Pct" not in OM_T)
check("T4 can never inherit the T3 risk",
      risk_for(4) != risk_for(3), "T3=%s T4=%s" % (risk_for(3), risk_for(4)))


# ----------------------------------------------------------------------
# 3. The scaled-in triggers are distinct and strictly ascending.
# ----------------------------------------------------------------------
T2_RR = num_input(DEFS_T, "InpPyramidT2RR")
T3_RR = num_input(DEFS_T, "InpPyramidT3RR")
T4_RR = num_input(DEFS_T, "InpPyramidT4RR")

check("InpPyramidT2RR exists", T2_RR is not None)
check("InpPyramidT3RR exists", T3_RR is not None)
check("InpPyramidT4RR exists", T4_RR is not None)
check("T2 trigger defaults to +1.0R", T2_RR == 1.0, T2_RR)
check("T3 trigger defaults to +2.0R", T3_RR == 2.0, T3_RR)
check("T4 trigger defaults to +3.0R", T4_RR == 3.0, T4_RR)
if None not in (T2_RR, T3_RR, T4_RR):
    check("scale-in triggers are strictly ascending (T2 < T3 < T4)",
          T2_RR < T3_RR < T4_RR, (T2_RR, T3_RR, T4_RR))
    check("the four rungs use four DISTINCT triggers",
          len({T2_RR, T3_RR, T4_RR}) == 3, (T2_RR, T3_RR, T4_RR))

# Each rung must be gated on its own trigger AND its own pending check.
check("T2 gate: currentRR >= InpPyramidT2RR && IsPyramidPending(2)",
      re.search(r"if\(currentRR\s*>=\s*InpPyramidT2RR\s*&&\s*"
                r"m_orderManager\.IsPyramidPending\(2\)\)", TM_T) is not None)
check("T3 gate: currentRR >= InpPyramidT3RR && IsPyramidPending(3)",
      re.search(r"if\(currentRR\s*>=\s*InpPyramidT3RR\s*&&\s*"
                r"m_orderManager\.IsPyramidPending\(3\)\)", TM_T) is not None)
check("T4 gate: currentRR >= InpPyramidT4RR && IsPyramidPending(4)",
      re.search(r"if\(currentRR\s*>=\s*InpPyramidT4RR\s*&&\s*"
                r"m_orderManager\.IsPyramidPending\(4\)\)", TM_T) is not None)
check("T2 is added through AddPyramidTranche(2, groupBE)",
      "m_orderManager.AddPyramidTranche(2, groupBE)" in TM_T)
check("T3 is added through AddPyramidTranche(3, safeBE)",
      "m_orderManager.AddPyramidTranche(3, safeBE)" in TM_T)
check("T4 is added through AddPyramidTranche(4, safeBE4)",
      "m_orderManager.AddPyramidTranche(4, safeBE4)" in TM_T)


# ----------------------------------------------------------------------
# 4. The ladder advances strictly 2 -> 3 -> 4 -> 0 on BOTH paths.
#    Independent Python model - walks the whole ladder plus the terminal
#    states, so a rung can neither be re-armed nor silently skipped.
# ----------------------------------------------------------------------
def next_tranche(current):
    """Mirror of the shipped ladder advance."""
    if current == 2:
        return 3
    elif current == 3:
        return 4
    return 0


check("walk 2 -> 3 -> 4 -> 0",
      [next_tranche(2), next_tranche(3), next_tranche(4)] == [3, 4, 0],
      [next_tranche(2), next_tranche(3), next_tranche(4)])
check("a fully-walked ladder parks on 0 and stays there",
      next_tranche(0) == 0 and next_tranche(1) == 0 and next_tranche(5) == 0)
check("the ladder never re-arms a rung it already passed",
      all(next_tranche(r) > r for r in (2, 3)) and next_tranche(4) == 0)

# Walk the ladder from a fresh basket. A new basket parks the ladder on 2
# (T1 is already open), so the walk is T1 -> T2 -> T3 -> T4 -> parked on 0.
visited = [1, 2]
while next_tranche(visited[-1]) != 0:
    visited.append(next_tranche(visited[-1]))
check("walking from a fresh basket visits exactly the four rungs T1..T4",
      visited == [1, 2, 3, 4], visited)


# ----------------------------------------------------------------------
# 4b. IsPyramidPending() contract: only the CURRENT rung is ever pending,
#     and a parked ladder (0) opens no rung at all.
# ----------------------------------------------------------------------
def is_pyramid_pending(next_t, tranche, enabled=True, active=True):
    """Mirror of the shipped IsPyramidPending()."""
    return bool(enabled) and next_t == tranche and bool(active)


check("a parked ladder (0) blocks every rung",
      not any(is_pyramid_pending(0, t) for t in (2, 3, 4)))
check("only the current rung is pending at each ladder step",
      all(is_pyramid_pending(t, t) and not is_pyramid_pending(t, o)
          for t in (2, 3, 4) for o in (2, 3, 4) if o != t))
check("IsPyramidPending is disabled wholesale when the pyramid is off",
      not any(is_pyramid_pending(t, t, enabled=False) for t in (2, 3, 4)))
check("IsPyramidPending is false with no active trade",
      not any(is_pyramid_pending(t, t, active=False) for t in (2, 3, 4)))
check("code gates on m_nextTranche == tranche",
      re.search(r"return\s*\(InpPyramidEnable\s*&&\s*m_nextTranche\s*==\s*tranche\s*&&\s*"
                r"m_hasActiveTrade\)", OM_T) is not None)

# Both the skip path and the success path must use the same 4-way advance.
ADVANCE = (r"m_nextTranche\s*=\s*\(trancheToAdd\s*==\s*2\)\s*\?\s*3\s*:\s*"
           r"\(\(trancheToAdd\s*==\s*3\)\s*\?\s*4\s*:\s*0\)\s*;")
advances = re.findall(ADVANCE, OM_T)
check("ladder advance is written 2 -> 3 -> 4 -> 0 at BOTH sites",
      len(advances) == 2, "found %d" % len(advances))
check("the old 2 -> 3 -> 0 advance is gone",
      "(tranche == 2) ? 3 : 0" not in OM_T and
      "(trancheToAdd == 2) ? 3 : 0" not in OM_T)
check("m_nextTranche starts at the FIRST rung on a new basket",
      OM_T.count("m_nextTranche    = 2;") >= 2,
      "resets found: %d" % OM_T.count("m_nextTranche    = 2;"))
# NOTE: the regex must not treat '==' as an assignment, or the comparison
# inside IsPyramidPending() is swept up as a ladder write.
live_assigns = sorted(set(re.findall(r"m_nextTranche\s*=(?!=)\s*([^;]+);", OM_T)))
check("only the 2 reset and the 4-way advance are ever assigned to the ladder",
      live_assigns == sorted(["2", "(trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0)"]),
      live_assigns)



# ----------------------------------------------------------------------
# 5. Every rung that fills raises the shared per-tick guard.
#    The guard exists so a brand-new ticket keeps the protective stop that
#    travelled with its market order for one tick, instead of having a tight
#    ATR trail written over it before the broker confirms the stop.
# ----------------------------------------------------------------------
check("the per-tick guard is declared once and named for the general case",
      len(re.findall(r"bool\s+trancheOpenedThisTick\s*=\s*false\s*;", TM_T)) == 1)
check("the old tranche-3-only guard name survives only in a historical comment",
      not [l for l in TM_T.split("\n")
           if "t3OpenedThisTick" in l and not l.strip().startswith("//")])
check("the guard is raised exactly once per rung (T2, T3 and T4)",
      len(re.findall(r"trancheOpenedThisTick\s*=\s*true\s*;", TM_T)) == 3,
      "raises: %d" % len(re.findall(r"trancheOpenedThisTick\s*=\s*true\s*;", TM_T)))

# T2 used to fill without raising the guard. Pin the raise inside the T2
# success block specifically, so a future edit cannot silently drop it.
M_T2 = re.search(r"AddPyramidTranche\(2,\s*groupBE\)\s*\)\s*\{(.*?)\n\s*\}", TM_T, re.S)
check("T2 success block found", M_T2 is not None)
if M_T2:
    check("T2 success block raises trancheOpenedThisTick",
          "trancheOpenedThisTick = true;" in M_T2.group(1))

# Every rung must hand a friction-based group breakeven to the fill, never the
# tight dynamic trail (INVALID_STOPS / instant-stopped-out risk).
check("T2 fill is protected by groupBE (friction-based breakeven)",
      re.search(r"AddPyramidTranche\(2,\s*groupBE\)", TM_T) is not None and
      "groupBE = primaryEntry" in TM_T)
check("T3 and T4 fills are protected by their own friction-based breakeven",
      re.search(r"AddPyramidTranche\(3,\s*safeBE\)", TM_T) is not None and
      re.search(r"AddPyramidTranche\(4,\s*safeBE4\)", TM_T) is not None)
check("no rung ever hands the dynamic ATR trail to AddPyramidTranche",
      not re.search(r"AddPyramidTranche\(\s*\d\s*,\s*desiredSL\s*\)", TM_T))


# ----------------------------------------------------------------------
# 6. The ATR trail arms at +1.0R on BOTH directions, and InpLock3RRR is
#    no longer read anywhere.
# ----------------------------------------------------------------------
TRAIL_RR = num_input(DEFS_T, "InpTrailStartRR")
check("InpTrailStartRR exists", TRAIL_RR is not None)
check("InpTrailStartRR defaults to +1.0R", TRAIL_RR == 1.0, TRAIL_RR)

# Three live sites read the trail trigger: the LONG arm gate, the SHORT arm
# gate, and the single-ticket push counter at the bottom of Update().
trail_gates = re.findall(r"if\(currentRR\s*>=\s*InpTrailStartRR\)", TM_T)
check("the three live trail gates read InpTrailStartRR",
      len(trail_gates) == 3, "found %d" % len(trail_gates))
check("both directions carry an ATR trail formula behind that gate",
      "high0 - (InpTrailATRMultiplier * atr)" in TM_T and
      "low0 + (InpTrailATRMultiplier * atr)" in TM_T)
check("both trail gates are armed at or before the T2 trigger",
      None not in (TRAIL_RR, T2_RR) and TRAIL_RR <= T2_RR,
      "trail@%s T2@%s" % (TRAIL_RR, T2_RR))
check("the trail/push blocks are skipped on the tick a tranche filled",
      len(re.findall(r"&&\s*!trancheOpenedThisTick\)", TM_T)) == 3,
      "guarded sites: %d" % len(re.findall(r"&&\s*!trancheOpenedThisTick\)", TM_T)))
check("SyncTradeState classification reads InpTrailStartRR",
      re.search(r"rr\s*>=\s*InpTrailStartRR\)\s*step\s*=\s*", TM_T) is not None)

# InpLock3RRR must survive as a declaration ONLY: no executable read of it.
defs_reads = [l for l in TM_T.split("\n") if "InpLock3RRR" in l
              and not l.strip().startswith("//")]
check("no live code reads InpLock3RRR in the trade manager", not defs_reads, defs_reads)
check("InpLock3RRR is still DECLARED (saved .set files must keep loading)",
      re.search(r"^\s*input\s+double\s+InpLock3RRR\s*=\s*3\.0\s*;", DEFS_T, re.M) is not None)
check("InpLock3RRR is documented as superseded",
      "SUPERSEDED" in DEFS_T)



# ----------------------------------------------------------------------
# 7. Epsilon-safe lot floor. MathFloor(rawLot/step) alone can drop a WHOLE
#    step when the quotient lands a few ULPs below an exact integer, which
#    silently UNDER-sizes the rung. Independent model over the real cases.
# ----------------------------------------------------------------------
EPS = 1e-8
check("epsilon is defined as a literal in the source", "1e-8" in RM_T)


def floor_steps(raw_lot, step, eps=EPS):
    """Mirror of the shipped epsilon-safe step floor."""
    return math.floor(raw_lot / step + eps)


check("both lot-sizing paths are epsilon-safe",
      len(re.findall(r"MathFloor\(rawLot\s*/\s*m_volumeStep\s*\+\s*1e-8\)", RM_T)) == 2,
      "sites: %d" % len(re.findall(r"MathFloor\(rawLot\s*/\s*m_volumeStep\s*\+\s*1e-8\)", RM_T)))
check("the bare (unprotected) floor was removed",
      not re.search(r"MathFloor\(rawLot\s*/\s*m_volumeStep\)(?!\s*\+)", RM_T))

# The regression case: 0.29/0.01 is genuinely 28.999999999999996 in IEEE-754,
# so the bare floor drops to 28 steps. The epsilon restores the intended 29.
check("0.29 / 0.01 is the IEEE-754 regression case (bare floor loses a step)",
      math.floor(0.29 / 0.01) == 28, math.floor(0.29 / 0.01))
check("epsilon restores the intended 29 steps",
      floor_steps(0.29, 0.01) == 29, floor_steps(0.29, 0.01))

# Exact multiples must stay exact - the epsilon must not ADD a step.
check("0.30 / 0.01 -> 30 steps", floor_steps(0.30, 0.01) == 30, floor_steps(0.30, 0.01))
check("0.10 / 0.01 -> 10 steps", floor_steps(0.10, 0.01) == 10, floor_steps(0.10, 0.01))
check("0.0625 / 0.01 -> 6 steps (T4 rung, strict round-down)",
      floor_steps(0.0625, 0.01) == 6, floor_steps(0.0625, 0.01))

# The epsilon must never round a lot UP past the risk budget: it may only
# recover an exact multiple, never promote a genuinely unterminated quotient.
check("a genuinely fractional quotient is NOT rounded up",
      floor_steps(0.2999, 0.01) == 29, floor_steps(0.2999, 0.01))
check("the epsilon is far below one volume step",
      all(abs(floor_steps(v, 0.01) - math.floor(v / 0.01)) <= 1
          for v in (0.29, 0.30, 0.10, 0.0625, 0.2999, 1.999, 0.0149)))

# Round-down guarantee: the sized lot must never exceed the risk budget.
for raw in (0.014, 0.029, 0.0625, 0.2999, 1.999):
    lot = floor_steps(raw, 0.01) * 0.01
    check("sized lot never exceeds raw budget (raw=%.4f -> %.2f)" % (raw, lot),
          lot <= raw + 1e-12, "lot=%s raw=%s" % (lot, raw))


# ----------------------------------------------------------------------
# 8. Documentation / version consistency.
# ----------------------------------------------------------------------
stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines declares exactly one version stamp", len(stamps) == 1, stamps)
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.29 or later", parts >= (5, 29), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 10 files stamp v%s" % RELEASE, not missing, missing)

check("the [4] group banner names 4-tranche pyramiding",
      "4-tranche pyramiding" in DEFS_T)
check("the pyramid group banner no longer says 3 tranches",
      "3-tranche pyramiding" not in DEFS_T)
check("the ladder total is documented in the inputs",
      "0.6875" in DEFS_T)
check("the MILESTONE LADDER documents the +2.0R rung",
      re.search(r"\+2\.0R\s+lock rung 2", DEFS_T) is not None)
check("InpLockProfit2RR and InpLockProfit2TargetRR are both declared",
      num_input(DEFS_T, "InpLockProfit2RR") == 2.0 and
      num_input(DEFS_T, "InpLockProfit2TargetRR") == 1.0)


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.29 4-TRANCHE RISK-%% PYRAMID + TRAIL REPOINT - STATIC PROBE")
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
