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
  8. The v5.31 STRICT stop-loss milestone floors, measured from primaryEntry:
     +1.0R -> breakeven + broker friction, +2.0R -> exactly +1.0R and
     +3.0R -> exactly +2.0R, evaluated in DESCENDING order behind a one-way
     ratchet and pushed to the WHOLE basket by ApplyUnifiedSL(). The legacy
     Pine "cut risk in half" rung (InpCutRiskRR) still ships unchanged, and
     this probe PINS its arithmetic: one 0.5*rrUnit site per direction and a
     LOSS-SIDE floor the profit-side milestones dominate from +1.0R onward.
     v5.32 HOISTED that rung to the END of its branch (it used to run first)
     and made it count only while live; neither changes which floor survives,
     which is what section 9's replay model below asserts.

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

# The unknown-rung refusal lives in the else-arm that CLOSES the risk chain, so
# it is found by locating the last named rung (T4) and taking the else that
# follows it. Asserting on the arm's body rather than on a global
# `else\s+return\s+false\s*;` keeps this honest about INTENT: v5.32 gave that
# arm a log line, which changes its formatting but not its behaviour, and a
# probe that fails on the reformatting of correct code is a probe that trains
# its reader to ignore it.
_unknown_rung_arm = re.search(
    r"trancheToAdd\s*==\s*4\s*\)\s*riskPct\s*=\s*InpRiskT4Pct\s*;"   # last named rung
    r".*?else\s*\{(?P<body>[^}]*)\}",                                # ... and its else-arm
    OM_T, re.S)

check("code refuses an unrecognised tranche instead of falling through",
      _unknown_rung_arm is not None and
      re.search(r"return\s+false\s*;", _unknown_rung_arm.group("body")) is not None,
      "else-arm of the risk chain must return false")

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


# ----------------------------------------------------------------------
# 9. STOP-LOSS MILESTONE LADDER (v5.31 floor arithmetic).
#    Floors, all measured from primaryEntry:
#      +1.0R -> primaryEntry +/- broker friction   (InpBreakEvenRR)
#      +2.0R -> primaryEntry +/- 1.0R              (InpLockProfit2TargetRR)
#      +3.0R -> primaryEntry +/- 2.0R              (InpLockProfitTargetRR)
#    Evaluated in DESCENDING order (rung 3, rung 2, ATR trail) with every
#    rung applied forward-only, then handed to ApplyUnifiedSL(), whose own
#    one-way ratchet is the last line of defence.
# ----------------------------------------------------------------------
def _norm(s):
    """Collapse all whitespace: makes source checks CRLF/indent proof."""
    return re.sub(r"\s+", " ", s)


TM_N = _norm(TM_T)
OM_N = _norm(OM_T)

L2_TRIG = num_input(DEFS_T, "InpLockProfit2RR")
L2_TGT = num_input(DEFS_T, "InpLockProfit2TargetRR")
L3_TRIG = num_input(DEFS_T, "InpLockProfitRR")
L3_TGT = num_input(DEFS_T, "InpLockProfitTargetRR")
BE_RR = num_input(DEFS_T, "InpBreakEvenRR")
CUT_RR = num_input(DEFS_T, "InpCutRiskRR")
TRAIL_RR = num_input(DEFS_T, "InpTrailStartRR")
T2_RR = num_input(DEFS_T, "InpPyramidT2RR")

# --- group A: an evenly-spaced 1R ladder ------------------------------
check("milestone rung 2 locks exactly +1.0R", L2_TGT == 1.0, L2_TGT)
check("milestone rung 3 locks exactly +2.0R", L3_TGT == 2.0, L3_TGT)
check("milestone rung triggers are +2.0R and +3.0R",
      L2_TRIG == 2.0 and L3_TRIG == 3.0, (L2_TRIG, L3_TRIG))
check("each rung target sits strictly below its own trigger (descending ladder)",
      None not in (L2_TGT, L3_TGT, L2_TRIG, L3_TRIG) and
      0.0 < L2_TGT < L3_TGT and L2_TGT < L2_TRIG and L3_TGT < L3_TRIG,
      (L2_TGT, L3_TGT, L2_TRIG, L3_TRIG))
check("breakeven / T2 scale-in / trail all arm on the same +1.0R rung",
      BE_RR == 1.0 and T2_RR == 1.0 and TRAIL_RR == 1.0,
      (BE_RR, T2_RR, TRAIL_RR))

# --- group B: the floor arithmetic exactly as shipped -----------------
check("LONG rung 3 floor = primaryEntry + (InpLockProfitTargetRR * rrUnit)",
      "primaryEntry + (InpLockProfitTargetRR * rrUnit)" in TM_N)
check("LONG rung 2 floor = primaryEntry + (InpLockProfit2TargetRR * rrUnit)",
      "primaryEntry + (InpLockProfit2TargetRR * rrUnit)" in TM_N)
check("SHORT rungs mirror both floors with a minus sign",
      "primaryEntry - (InpLockProfit2TargetRR * rrUnit)" in TM_N and
      "primaryEntry - (InpLockProfitTargetRR * rrUnit)" in TM_N)
check("breakeven floors are primaryEntry +/- the broker-friction offset",
      "double beOffset = CalcBasketFriction(true); "
      "double beSL = primaryEntry + beOffset;" in TM_N and
      "double beOffset = CalcBasketFriction(false); "
      "double beSL = primaryEntry - beOffset;" in TM_N)
check("no milestone floor encodes its target as a literal fraction of 1R",
      re.search(r"(beSL|lockedSL2?)\s*=[^;]*?(0\.5\s*\*|/\s*2\.0)", TM_N) is None)

# --- group C: descending evaluation + forward-only ratchets ------------
check("both step-lock rungs are guarded on their own trigger AND Both targets",
      "InpLockProfitRR > 0.0 && InpLockProfitTargetRR > 0.0 && "
      "currentRR >= InpLockProfitRR" in TM_N and
      "InpLockProfit2RR > 0.0 && InpLockProfit2TargetRR > 0.0 && "
      "currentRR >= InpLockProfit2RR" in TM_N)
check("each in-branch rung only ever moves the stop TOWARD market",
      "if(lockedSL > desiredSL) desiredSL = lockedSL;" in TM_N and
      "if(lockedSL2 > desiredSL) desiredSL = lockedSL2;" in TM_N and
      "if(lockedSL < desiredSL) desiredSL = lockedSL;" in TM_N and
      "if(lockedSL2 < desiredSL) desiredSL = lockedSL2;" in TM_N)

# --- branch-scoped ordering -------------------------------------------
# The ladder is duplicated for LONG and SHORT and the two copies contain
# IDENTICAL guard text, so a bare TM_N.index() comparison silently resolves
# against the LONG copy whichever branch it was meant to describe. Split the
# text at the SHORT marker so each ordering claim is made inside the branch it
# is actually about; otherwise the SHORT mirror could be reordered (or its
# half-risk rung un-hoisted) with every "both directions" assertion still
# green. The reorder mutants in _mutate_ms_milestones.py cover exactly this.
_short_at = TM_N.index("else // SHORT")
LONG_N = TM_N[:_short_at]
SHORT_N = TM_N[_short_at:]


def _ordered(blk, pairs):
    """True only if every (a, b) pair appears in blk with a before b.

    Returns False rather than raising when an anchor is absent, so a
    refactor that renames a guard reads as ONE failed check instead of
    aborting the suite mid-run and hiding every check after it.
    """
    for a, b in pairs:
        if a not in blk or b not in blk:
            return False
        if blk.index(a) >= blk.index(b):
            return False
    return True


check("the milestone rungs run DESCENDING in BOTH directions (BE, then 3, then 2)",
      _ordered(LONG_N, [
          ("currentRR >= InpBreakEvenRR && desiredSL < primaryEntry",
           "currentRR >= InpLockProfitRR"),
          ("currentRR >= InpLockProfitRR", "currentRR >= InpLockProfit2RR"),
      ]) and
      _ordered(SHORT_N, [
          ("currentRR >= InpBreakEvenRR && desiredSL > primaryEntry",
           "currentRR >= InpLockProfitRR"),
          ("currentRR >= InpLockProfitRR", "currentRR >= InpLockProfit2RR"),
      ]))
# The ATR trail is the last rung that may tighten the stop BEFORE the half-risk
# floor, so it must still follow both step locks in each branch; otherwise a
# later rung could overwrite its tighter stop. (halfRiskSL is excluded from the
# no-half-RR-literal check above precisely because it is not a milestone.)
check("the ATR trail is evaluated after both step locks in BOTH directions",
      _ordered(LONG_N, [("currentRR >= InpLockProfit2RR",
                         "double dynamicTrail = high0")]) and
      _ordered(SHORT_N, [("currentRR >= InpLockProfit2RR",
                          "double dynamicTrail = low0")]))
# v5.32 HOIST: the half-risk rung now sits AFTER the ATR trail in each branch,
# not before the milestone rungs. Its guard therefore tests the FINAL stop, so
# m_halfRiskTriggers counts APPLICATIONS rather than mere assignments.
#
# This is the assertion that motivated the reorder mutants, so it is worth
# stating why: moving the rung is a pure ORDERING change, and the two branch
# copies are byte-identical, so a whole-file TM_N.index() comparison -- or even
# a SHORT_N-scoped one over a block still containing the LONG copy -- keeps
# answering from the LONG branch. Reorder only the SHORT mirror and a
# branch-blind version of this check stays green. The "SHORT mirror reordered
# alone" mutant in _mutate_ms_milestones.py exists solely to keep it honest.
check("the half-risk rung is HOISTED after the ATR trail in BOTH directions",
      _ordered(LONG_N, [
          ("double dynamicTrail = high0",
           "m_cutRiskRungLive && currentRR >= InpCutRiskRR"),
      ]) and
      _ordered(SHORT_N, [
          ("double dynamicTrail = low0",
           "m_cutRiskRungLive && currentRR >= InpCutRiskRR"),
      ]))
check("the ladder lives in the trade manager only (no second drifted copy)",
      "InpLockProfit2TargetRR" not in OM_N and
      "InpLockProfit2RR" not in OM_N)
check("the trail gate that re-pushes the basket opens no later than the T2 add",
      None not in (TRAIL_RR, T2_RR) and TRAIL_RR <= T2_RR,
      (TRAIL_RR, T2_RR))


# --- group D: the legacy half-risk rung stays contained ----------------
check("the legacy half-risk rung is still declared (Pine half_risk_rr parity)",
      CUT_RR is not None, CUT_RR)
check("half-risk arithmetic appears exactly once per direction, into halfRiskSL only",
      TM_N.count("0.5 * rrUnit") == 2 and
      "halfRiskSL = primaryEntry - (0.5 * rrUnit);" in TM_N and
      "halfRiskSL = primaryEntry + (0.5 * rrUnit);" in TM_N and
      "InpLockProfit2TargetRR * rrUnit" in TM_N)


def _ladder_sl(entry, rru, friction, rr, is_long):
    """Replay the shipped rung order with the shipped forward-only guard.

    Each rung is a signed offset measured from entry, positive meaning
    "toward market". The half-risk rung is the only LOSS-SIDE floor
    (LONG: entry - 0.5R, SHORT: entry + 0.5R); breakeven and both lock
    rungs all sit on the profit side.

    The order below is the SHIPPED order as of v5.32, which HOISTED the
    half-risk rung from the front of the branch to the end of it (the ATR
    trail sits after rung 2 in the source and is not modelled here, since
    it needs live price data). The hoist is what makes the half-risk
    counter mean "the -0.5R floor was APPLIED" rather than "this rung was
    reached"; it cannot change which floor wins, because every rung
    resolves with max() / min() and those are order-independent. The
    assertions below therefore hold on either ordering -- this list is
    kept in shipped order so a reader comparing it to the source is not
    misled.
    """
    rungs = [(BE_RR, None), (L3_TRIG, L3_TGT), (L2_TRIG, L2_TGT), (CUT_RR, -0.5)]
    sl = entry - rru if is_long else entry + rru   # poor seed: the raw initial stop
    for trig, target in rungs:
        if trig is None or rr < trig:
            continue
        cand = friction if target is None else target * rru
        cand = entry + (cand if is_long else -cand)
        sl = max(sl, cand) if is_long else min(sl, cand)
    return sl


ENTRY, RRU, FRICTION = 1.1000, 0.0020, 0.0001

check("model +1.0R: the surviving floor is breakeven+friction, so the half-risk "
      "rung never survives a milestone",
      abs(_ladder_sl(ENTRY, RRU, FRICTION, 1.0, True) - (ENTRY + FRICTION)) < 1e-9 and
      _ladder_sl(ENTRY, RRU, FRICTION, 1.0, True) > ENTRY - 0.5 * RRU)
check("model LONG: +2.0R locks exactly +1.0R, +3.0R locks exactly +2.0R",
      abs(_ladder_sl(ENTRY, RRU, FRICTION, 2.0, True) - (ENTRY + 1.0 * RRU)) < 1e-9 and
      abs(_ladder_sl(ENTRY, RRU, FRICTION, 3.0, True) - (ENTRY + 2.0 * RRU)) < 1e-9)
check("model SHORT mirrors both floors below entry",
      abs(_ladder_sl(ENTRY, RRU, FRICTION, 2.0, False) - (ENTRY - 1.0 * RRU)) < 1e-9 and
      abs(_ladder_sl(ENTRY, RRU, FRICTION, 3.0, False) - (ENTRY - 2.0 * RRU)) < 1e-9)
check("model: a higher milestone can never move the floor backwards",
      all(_ladder_sl(ENTRY, RRU, FRICTION, rr + 0.1, True) >=
          _ladder_sl(ENTRY, RRU, FRICTION, rr, True) for rr in (0.5, 1.0, 2.0, 3.0)))


# --- group E: the whole basket takes the raised floor -------------------
check("ApplyUnifiedSL is called on every rung push (breakeven floors included)",
      TM_N.count("m_orderManager.ApplyUnifiedSL(desiredSL);") >= 1 and
      TM_N.count("m_orderManager.ApplyUnifiedSL(groupBE);") == 1 and
      "m_orderManager.ApplyUnifiedSL(safeBE4);" in TM_N)
check("ApplyUnifiedSL enforces the one-way ratchet for BOTH directions",
      "if(isLong && newSL <= m_sessionSL) return;" in OM_N and
      "if(!isLong && newSL >= m_sessionSL) return;" in OM_N)
check("the T2/T3/T4 scale-in hands its protective floor to the market order",
      "AddPyramidTranche(2, groupBE)" in TM_N and
      "AddPyramidTranche(4, safeBE4)" in TM_N)
check("session SL only advances once a stop is live at the broker",
      "if(anyApplied) { m_sessionSL = newSL;" in OM_N)
check("PM note: the ladder push is nested under the trail gate, so keep "
      "InpTrailStartRR <= InpPyramidT2RR or the +1.0R floor goes dark",
      None not in (TRAIL_RR, T2_RR) and TRAIL_RR <= T2_RR)


# ----------------------------------------------------------------------
# 10. v5.32 REFUSAL ATTRIBUTION + ORIGINAL-1R PERSISTENCE.
#     This probe owns the ladder's semantics, so the two v5.32 changes that
#     touch the ladder live here as well as in test_v532_pyramid.py: a rung
#     that cannot fire must say so, and the 1R the ladder is measured against
#     must survive a restart instead of being re-derived from a ratcheted stop.
# ----------------------------------------------------------------------
# Every refusal exit in the scale-in must be preceded by a Print(). A bare
# 'return false' is what made "T2 never triggers" unattributable in the field.
_pyr_start = OM_T.find("AddPyramidTranche")
_pyr_end = OM_T.find("void              ApplyUnifiedSL", _pyr_start)
PYR = OM_T[_pyr_start:_pyr_end] if _pyr_start >= 0 and _pyr_end > _pyr_start else ""

check("the scale-in body was located for the attribution scan", len(PYR) > 2000,
      len(PYR))

_silent_exits = []
for _m in re.finditer(r"return\s+false\s*;", PYR):
    if "Print(" not in PYR[max(0, _m.start() - 600):_m.start()]:
        _silent_exits.append(_m.start())
check("no silent refusal survives in the scale-in path", not _silent_exits,
      "offsets %s" % _silent_exits)

check("the refusal log prefix is stable and greppable",
      PYR.count("[Pyramid] Tranche ") >= 6 and
      " REFUSED: " in PYR and " REJECTED by broker" in PYR)
check("the basket-empty refusal names the pyramid flag and the count",
      re.search(r"\" REFUSED: \",\s*\n\s*!InpPyramidEnable\s*\?", PYR) is not None or
      "!InpPyramidEnable ? \"InpPyramidEnable=false\"" in PYR)
check("the ladder recovers a missing R unit instead of refusing outright",
      "R unit RECOVERED from" in PYR)

# Persistence: the 1R the whole ladder is measured against must be restored from
# a GlobalVariable keyed on symbol + magic + primary ticket, and must be
# REFUSED when the stored ticket is not the position being adopted.
check("the persistence helpers exist (persist / restore / clear)",
      "PersistBasketR" in OM_T and "RestoreBasketR" in OM_T and
      "ClearBasketR" in OM_T)
check("the stored R is paired to the primary ticket and checked on restore",
      re.search(r"if\(storedTicket\s*!=\s*adoptedTicket\)", OM_T) is not None)
check("a mismatched record is discarded rather than trusted",
      "discarded (stale record)" in OM_T)
check("InitBasket persists the 1R it was handed",
      re.search(r"PersistBasketR\(rrUnit\s*,\s*ticket\)", OM_T) is not None)
check("adopting a position prefers the stored 1R over the broker distance",
      OM_T.find("RestoreBasketR(positionTicket)") >= 0 and
      OM_T.find("RestoreBasketR(positionTicket)") <
      OM_T.find("m_activeTrade.rrUnit = m_activeTrade.initialSLDistance"))
check("clearing the basket clears the stored 1R",
      re.search(r"ClearBasketR\(\)", OM_T) is not None)




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
            line += "  <%s>" % (detail,)
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
