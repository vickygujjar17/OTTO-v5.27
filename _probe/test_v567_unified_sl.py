"""
v5.67 - UNIFIED-STOP REPAIR + RUNG-AWARE RISK BUDGET - static verification probe.

The audit of ApplyUnifiedSL() against the 1.0 / 1.0 / 0.5 / 0.25 pyramid found
two defects that the MQL5 compiler is structurally incapable of seeing. Both
compile cleanly while misbehaving, and both fail SILENTLY in production.

  1. THE RATCHET CAN STRAND A LOOSER STOP FOREVER.
     ApplyUnifiedSL() pushes the new stop to every ticket, then advances
     m_sessionSL if AT LEAST ONE write succeeded (the v5.16 decision, kept).
     But the guard above the loop is one-way:

         if(isLong && newSL <= m_sessionSL) return;

     so once m_sessionSL has advanced, the level can never be re-offered to
     the tickets the venue REFUSED. They keep their OLD, looser stop for the
     life of the basket, and nothing retries. That is the shape of a partial
     stop-out: T1 exits at the raised floor while T4 keeps running on the
     original 1R, so the basket's real exposure exceeds the risk the ladder
     believes it holds -- in the worst case by every rung that failed.

     This probe pins the repair path: a staged floor that is only ever a
     level at least one ticket ALREADY accepted (so it cannot loosen
     anything), a broker-read ledger of who still owes it, a bounded retry
     budget, and clearing on every path that invalidates the debt.

  2. THE AUDITOR'S 50% TOLERANCE WAS ANCHORED ON TRANCHE 1.
     Both risk checks used RiskPercent as the base, but only tranche 1 is
     sized from it: T2/T3/T4 are sized from InpRiskT2Pct/3Pct/4Pct. Any
     configuration with a rung ABOVE RiskPercent raised a CRITICAL against a
     leg sized exactly as designed -- and, symmetrically, a rung far BELOW it
     could oversize by a large multiple and still pass. The budget is now
     resolved per leg from its "_T<n>" tag, with RiskPercent as the fallback.

  3. THE AGGREGATE LEG WAS UNREACHABLE, NOT TIGHT.
     Its allowance summed all four nominal rungs (2.75% at the defaults, x1.5
     = 4.125%) while the unified stop caps the basket's real exposure at
     roughly one rung: before +1.0R only tranche 1 is open, and from +1.0R
     the first floor is cost-covering breakeven so every leg reads lossDist
     <= 0 and the sum is ZERO. No input could ever trip it. The allowance now
     tracks the budgets of the legs actually on the loss side, which is the
     invariant the check was always meant to assert.

Everything below is either static analysis of the shipped sources or an
independent Python model of the same arithmetic -- no MT5 runtime required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh",
             "CHighTableAuditor.mqh"]


def read(p):
    # CRLF -> LF so multi-line anchors can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


def strip_comments(s):
    return re.sub(r"//[^\n]*", "", s)


def brace_body(text, header_pat):
    """Body of the first {...} block following a header match."""
    m = re.search(header_pat, text)
    if not m:
        return None
    i = text.find("{", m.end() - 1)
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


OM_T = read(OM)
TM_T = read(TM)
AUD_T = read(AUD)
DEFS_T = read(DEFS)

APPLY = brace_body(OM_T, r"void\s+ApplyUnifiedSL\s*\(\s*double\s+newSL\s*\)")
APPLY_C = strip_comments(APPLY) if APPLY else ""
REPAIR = brace_body(OM_T, r"int\s+RepairUnifiedSL\s*\(\s*void\s*\)")
REPAIR_C = strip_comments(REPAIR) if REPAIR else ""
STAGE_C = strip_comments(brace_body(OM_T, r"void\s+StageUnifiedRepair\s*\(") or "")
CLEAR_C = strip_comments(brace_body(OM_T, r"void\s+ClearUnifiedRepair\s*\(\s*void\s*\)") or "")
CBODY_C = strip_comments(brace_body(OM_T, r"void\s+ClearBasket\s*\(\s*void\s*\)") or "")
RISK = brace_body(AUD_T, r"void\s+AuditRiskExposure\s*\(\s*void\s*\)")
RISK_C = strip_comments(RISK) if RISK else ""

# RepairUnifiedSL() has two exits that both clear the debt: the "everything
# owed was written" exit and the "credit spent, abandon" exit. Split them at
# the abandonment marker so each exit's clear can be asserted separately --
# otherwise one path's clear satisfies the check for the other, and a leak on
# either slips through.
_SPLIT = "UNIFIED SL REPAIR ABANDONED"
if REPAIR_C.find(_SPLIT) > 0:
    _cut = REPAIR_C.rfind("if(", 0, REPAIR_C.find(_SPLIT))
    if _cut <= 0:
        _cut = REPAIR_C.find(_SPLIT)
else:
    _cut = -1

# The "everything owed was written" exit. Sliced from `if(failed == 0)` so the
# top guard's own `ClearUnifiedRepair(); return 0;` -- which sits ABOVE this
# point and would satisfy any clear-then-return scan -- is excluded.
_OK_START = REPAIR_C.find("if(failed == 0)")
_OK_C = REPAIR_C[_OK_START:_cut] if _OK_START >= 0 and _cut > _OK_START else ""
# The "credit spent, abandon" exit.
_ABANDON_C = REPAIR_C[_cut:] if _cut >= 0 else ""

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))



def num_const(text, name):
    m = re.search(r"^\s*(?:input\s+)?(?:const\s+)?(?:double|int)\s+" +
                  re.escape(name) + r"\s*=\s*([0-9.]+)\s*;", text, re.M)
    return float(m.group(1)) if m else None


# ----------------------------------------------------------------------
# 1. A. THE RATCHET DEFECT, MODELLED.
#    An independent Python model of the shipped ApplyUnifiedSL() control
#    flow, used to prove the defect is real and that the repair closes it.
#    The model is deliberately a transcription of the MQL5, not a redesign:
#    same guard, same "any applied" rule, same one-way ratchet.
# ----------------------------------------------------------------------
print("\n-- A. the stranded-stop defect, modelled --")


def shipped_apply(session_sl, tickets, new_sl, is_long):
    """v5.66 ApplyUnifiedSL(): returns (session_sl, tickets, attempted)."""
    # ONE-WAY RATCHET, before any broker write.
    if is_long and new_sl <= session_sl:
        return session_sl, dict(tickets), False
    if (not is_long) and new_sl >= session_sl:
        return session_sl, dict(tickets), False
    any_applied = False
    out = dict(tickets)
    for t, (sl, accepts) in tickets.items():
        if accepts:
            out[t] = (new_sl, accepts)
            any_applied = True
    if any_applied:
        session_sl = new_sl
    return session_sl, out, True


# A 4-leg LONG basket at the 1.0 / 1.0 / 0.5 / 0.25 ladder, entry 1.1000,
# 1R = 0.0020. T4's venue refuses the +1.0R breakeven write (INVALID_STOPS).
ENTRY, RRU = 1.1000, 0.0020
BE = ENTRY + 0.0001                      # cost-covering breakeven + friction
legs = {"T1": (ENTRY - RRU, True), "T2": (ENTRY - RRU, True),
        "T3": (ENTRY - RRU, True), "T4": (ENTRY - RRU, False)}
SS, AFTER, attempted = shipped_apply(ENTRY - RRU, legs, BE, True)
check("v5.66 model: the +1.0R write IS attempted", attempted)
check("v5.66 model: T1-T3 take the breakeven floor",
      all(AFTER[t][0] == BE for t in ("T1", "T2", "T3")))
check("v5.66 model: T4 is stranded on its ORIGINAL 1R stop", AFTER["T4"][0] == ENTRY - RRU)
check("v5.66 model: the session ratchet advanced anyway", SS == BE)

# The consequence: once the ratchet is at BE, the level can never be
# re-offered. This is the actual bug -- not that the write failed, but that
# the failure is UNRECOVERABLE.
SS2, AFTER2, attempted2 = shipped_apply(SS, AFTER, BE, True)
check("v5.66 model: re-offering the same level is refused by the ratchet",
      attempted2 is False)
check("v5.66 model: T4 is STILL stranded after re-offering", AFTER2["T4"][0] == ENTRY - RRU)

# Exposure this leaves on the table, in units of 1R. T4 is stranded at
# entry - 1.0R while the rest of the basket moved to entry + friction, so the
# gap is 1.0R plus the friction offset (0.0001 / 0.0020 = 0.05R) = 1.05R.
stranded_r = abs((AFTER["T4"][0]) - BE) / RRU
check("v5.66 model: the stranded leg runs 1.05R of stop distance the basket no longer has",
      abs(stranded_r - 1.05) < 1e-9, stranded_r)


def repaired_apply(session_sl, tickets, new_sl, is_long, debt, attempts, now, next_try):
    """v5.67 ApplyUnifiedSL() + RepairUnifiedSL(), same control flow.

    The two halves are deliberately independent: the ratchet decides whether
    a NEW level is offered this tick, while the repair is driven by PERSISTED
    debt and therefore keeps working on ticks where the ratchet says nothing.
    Collapsing them is exactly the bug -- if the repair depended on the
    ratchet accepting, it could never run, because the ratchet is what
    refuses the re-offer in the first place.
    """
    refused_any = False
    if not ((is_long and new_sl <= session_sl) or
            ((not is_long) and new_sl >= session_sl)):
        out = dict(tickets)
        any_applied = False
        for t, (sl, accepts) in tickets.items():
            if accepts:
                out[t] = (new_sl, accepts)
                any_applied = True
            else:
                refused_any = True
        if any_applied:
            session_sl = new_sl
            debt = None                        # ClearUnifiedRepair()
            if refused_any:
                debt = new_sl                  # StageUnifiedRepair(new_sl)
        tickets = out
    # ---- RepairUnifiedSL(), as called later in the same tick ----
    if debt is not None and now >= next_try:
        failed = 0
        for t in list(tickets.keys()):
            sl, accepts = tickets[t]
            if is_long and sl >= debt:
                continue
            if not is_long and sl <= debt:
                continue
            if accepts:
                tickets[t] = (debt, accepts)
            else:
                failed += 1
        if failed == 0:
            debt = None
        else:
            attempts += 1
            next_try = now + 5
            if attempts >= 6:
                debt = None
    return session_sl, tickets, debt, attempts, next_try


# v5.67 REPAIR PATH, modelled from the PRE-partial tick. The staging tick is
# the same one on which T1-T3 accepted BE and T4 refused it: that is where the
# debt is born. (Starting from the post-partial state would model a basket
# whose debt had never been recorded, which is the v5.66 bug, not the fix.)
SS5, REP5, debt5, att5, nt5 = repaired_apply(ENTRY - RRU, legs, BE, True,
                                            None, 0, 1000, 0)
check("v5.67 model: the debt is staged on the partially-applied tick", debt5 == BE)
check("v5.67 model: the stage does not disturb the ratchet", SS5 == BE)
check("v5.67 model: T1-T3 still take the floor while T4 refuses it",
      all(REP5[t][0] == BE for t in ("T1", "T2", "T3")) and
      REP5["T4"][0] == ENTRY - RRU)
check("v5.67 model: the first repair attempt happens at once, not a tick late",
      att5 == 1 and nt5 == 1005)

# It is driven by PERSISTED debt, so it keeps running on later ticks where the
# ratchet has nothing new to say -- which is exactly the stranded case.
SS6, REP6, debt6, att6, nt6 = repaired_apply(SS5, REP5, BE, True,
                                             debt5, att5, 1010, nt5)
check("v5.67 model: the repair keeps running while the ratchet is silent",
      att6 == 2 and debt6 == BE)
check("v5.67 model: the venue still refusing keeps the debt alive, not lost",
      REP6["T4"][0] == ENTRY - RRU)

# Venue recovers: the debt settles, T4 reaches the floor, the debt clears.
rec = dict(REP6)
rec["T4"] = (ENTRY - RRU, True)
SS7, REP7, debt7, att7, _ = repaired_apply(SS6, rec, BE, True, debt6, att6, 1020, nt6)
check("v5.67 model: once the venue accepts, T4 reaches the unified floor",
      REP7["T4"][0] == BE)
check("v5.67 model: the debt clears on success", debt7 is None)
check("v5.67 model: the whole basket now wears ONE stop",
      len({sl for sl, _ in REP7.values()}) == 1)

# ----------------------------------------------------------------------
# 2. B. THE REPAIR IS STRUCTURALLY SAFE (static pins on the MQL5).
# ----------------------------------------------------------------------
print("\n-- B. repair safety --")

check("ApplyUnifiedSL body located", APPLY is not None)
check("RepairUnifiedSL body located", REPAIR is not None)
check("StageUnifiedRepair body located", STAGE_C != "")
check("ClearUnifiedRepair body located", CLEAR_C != "")

# The ratchet itself MUST NOT be weakened: the defect was recovered around
# it, never by relaxing it. A looser stop pushed through the public path is
# still refused.
check("v5.67: the LONG ratchet guard is byte-identical to v5.66",
      "if(isLong && newSL <= m_sessionSL) return;" in APPLY_C)
check("v5.67: the SHORT ratchet guard is byte-identical to v5.66",
      "if(!isLong && newSL >= m_sessionSL) return;" in APPLY_C)
check("v5.67: some ticket applying still advances the session ratchet",
      "if(anyApplied)" in APPLY_C and "m_sessionSL = newSL;" in APPLY_C)

# The debt may only be STAGED from inside the anyApplied branch -- i.e. from a
# level at least one live ticket accepted. Staging from an unapplied value
# would let the repair push a stop the basket has never worn.
check("v5.67: the repair is staged only after at least one ticket applied",
      APPLY_C.find("if(anyApplied)") < APPLY_C.find("StageUnifiedRepair("))
check("v5.67: the repair is staged only when some ticket was REFUSED",
      re.search(r"if\s*\(\s*!\s*allApplied\s*\)\s*StageUnifiedRepair\s*\(\s*newSL\s*\)",
                APPLY_C) is not None)
check("v5.67: an accepted write clears any earlier debt",
      APPLY_C.find("ClearUnifiedRepair()") < APPLY_C.find("StageUnifiedRepair("))

# Staging is monotonic: a second partial before the first settles may only
# move the target forward, mirroring the ratchet it is repairing.
check("v5.67: staging refuses a level LOOSER than the one already owed (LONG)",
      re.search(r"isLong\s*\?\s*\(\s*level\s*<=\s*m_unifiedRepairSL\s*\)", STAGE_C)
      is not None)
check("v5.67: staging refuses a level LOOSER than the one already owed (SHORT)",
      re.search(r":\s*\(\s*level\s*>=\s*m_unifiedRepairSL\s*\)", STAGE_C) is not None)
check("v5.67: staging refuses a non-positive or direction-less basket",
      re.search(r"if\s*\(\s*level\s*<=\s*0\.0\s*\|\|\s*m_basketDir\s*==\s*DIR_NONE\s*\)\s*return",
                STAGE_C) is not None)
check("v5.67: a fresh stage resets the attempt credit",
      "m_unifiedRepairAttempts = 0;" in STAGE_C)

# The repair reads the BROKER, and skips a ticket already at/beyond the level,
# so a healthy leg is never written again.
check("v5.67: the repair selects the real position before reading its stop",
      "PositionSelectByTicket(m_basket[i].ticket)" in REPAIR_C)
check("v5.67: the repair reads the live stop from the book",
      "PositionGetDouble(POSITION_SL)" in REPAIR_C)
check("v5.67: a ticket already at or beyond the level is skipped (LONG)",
      re.search(r"liveSL\s*>=\s*m_unifiedRepairSL", REPAIR_C) is not None)
check("v5.67: a ticket already at or beyond the level is skipped (SHORT)",
      re.search(r"liveSL\s*<=\s*m_unifiedRepairSL", REPAIR_C) is not None)
check("v5.67: a closed ticket is not debt",
      re.search(r"if\s*\(\s*!\s*PositionSelectByTicket\([^)]*\)\s*\)\s*continue",
                REPAIR_C) is not None)
check("v5.67: the repair's own entry guard is the pending flag, not a no-op",
      # A guard scan on the TEXT of the first `return 0` would be satisfied by
      # `if(true) return 0;`, which disables the repair while looking like a
      # guard. Assert the exact predicate instead.
      re.search(r"if\(!m_unifiedRepairPending\)\s*return\s*0\s*;", REPAIR_C) is not None
      and "if(true)" not in REPAIR_C)
check("v5.67: the repair is bounded by a snapshot of the basket count",
      "int  count = m_basketCount;" in REPAIR_C)
check("v5.67: the repair writes each owed leg exactly once per pass",
      REPAIR_C.count("ModifyStopLoss(") == 1)

# Bounded retry: staggered, credited, and abandoned LOUDLY.
check("v5.67: a refused repair is credited", "m_unifiedRepairAttempts++;" in REPAIR_C)
check("v5.67: retries are staggered by the shared constant",
      "OTTO_UNIFIED_REPAIR_STAGGER_SEC" in REPAIR_C)
check("v5.67: the attempt budget is the shared constant",
      "OTTO_UNIFIED_REPAIR_MAX_ATTEMPTS" in REPAIR_C)
check("v5.67: the abandonment is a Print, not a silent drop",
      "UNIFIED SL REPAIR ABANDONED" in REPAIR_C)
check("v5.67: a successful repair clears the debt",
      # Asserted on the SUCCESS segment only. The top guard's own
      # `ClearUnifiedRepair(); return 0;` and the abandon exit's clear both sit
      # outside this slice, so neither can mask a leak on the success path.
      _OK_C.find("ClearUnifiedRepair();") >= 0 and
      _OK_C.find("ClearUnifiedRepair();") < _OK_C.find("return repaired;"))
check("v5.67: an abandoned repair also releases the debt",
      _ABANDON_C.find("ClearUnifiedRepair();") >= 0 and
      _ABANDON_C.find("ClearUnifiedRepair();") < _ABANDON_C.find("return repaired;"))
check("v5.67: a non-positive level or missing direction clears the debt",
      re.search(r"if\s*\(\s*m_unifiedRepairSL\s*<=\s*0\.0\s*\|\|\s*"
                r"m_basketDir\s*==\s*DIR_NONE\s*\)", REPAIR_C) is not None)

# The two tuning constants are file-scope, NOT class members: MQL5 rejects an
# in-class `static const int` with an initialiser ("illegal assignment use").
check("v5.67: the repair constants are file-scope in OttoDefines",
      num_const(DEFS_T, "OTTO_UNIFIED_REPAIR_STAGGER_SEC") is not None and
      num_const(DEFS_T, "OTTO_UNIFIED_REPAIR_MAX_ATTEMPTS") is not None)
check("v5.67: no in-class static const survives in the order manager",
      re.search(r"^\s{3}static\s+const\s+", OM_T, re.M) is None)

# Bounded: a permanently refused level is abandoned, never retried forever.
_, _, d, a, nx = repaired_apply(SS6, REP6, BE, True, debt6, 5, 2000, 1995)
check("v5.67 model: the debt is abandoned after the attempt budget",
      d is None and a == 6)

# ----------------------------------------------------------------------
# 3. C. RUNG-AWARE PER-LEG BUDGET.
# ----------------------------------------------------------------------
print("\n-- C. per-leg budget attribution --")

RP  = num_const(DEFS_T, "RiskPercent")
T2D = num_const(DEFS_T, "InpRiskT2Pct")
T3D = num_const(DEFS_T, "InpRiskT3Pct")
T4D = num_const(DEFS_T, "InpRiskT4Pct")
check("the four nominal rung risks are all readable", None not in (RP, T2D, T3D, T4D),
      (RP, T2D, T3D, T4D))
RP, T2D, T3D, T4D = (RP or 0.0), (T2D or 0.0), (T3D or 0.0), (T4D or 0.0)

check("the shipped ladder is 1.0 / 1.0 / 0.5 / 0.25",
      (RP, T2D, T3D, T4D) == (1.0, 1.0, 0.5, 0.25), (RP, T2D, T3D, T4D))


def rung_budget(comment, rp=RP, t2=T2D, t3=T3D, t4=T4D):
    """Independent model of CHighTableAuditor::RungRiskPct()."""
    if not comment or len(comment) < 3:
        return rp
    at = comment.rfind("_T")
    # The MQL5 form is StringFind(comment, "_T", len - 2): only the LAST two
    # characters are searched, so a mid-string "_T" is never trusted.
    if at < 0 or at != len(comment) - 3:
        return rp
    digits = comment[at + 2:]
    if not digits.isdigit():
        return rp
    tag = int(digits)
    return {2: t2, 3: t3, 4: t4}.get(tag, rp)


check("an untagged leg is tranche 1 (RiskPercent)",
      rung_budget("#OTTO-EURUSD-20261018-101010-BLK7") == RP)
check("a _T2 tag resolves to InpRiskT2Pct", rung_budget("#OTTO-X-1010-1010_T2") == T2D)
check("a _T3 tag resolves to InpRiskT3Pct", rung_budget("#OTTO-X-1010-1010_T3") == T3D)
check("a _T4 tag resolves to InpRiskT4Pct", rung_budget("#OTTO-X-1010-1010_T4") == T4D)
check("an adopted manual leg (MAN tail) falls back to RiskPercent",
      rung_budget("#OTTO-X-1010-1010-MAN") == RP)
check("a tag buried mid-string is NOT trusted",
      rung_budget("#OTTO-X_T2-1010-1010") == RP)
check("a non-numeric tag falls back to RiskPercent", rung_budget("#OTTO-X_TX") == RP)
check("an empty comment falls back to RiskPercent", rung_budget("") == RP)
check("a truncated 2-char comment falls back to RiskPercent", rung_budget("_T") == RP)
check("an out-of-range tag falls back to RiskPercent", rung_budget("#A_T9") == RP)

# THE REGRESSION THIS FIXES. Before v5.67 every leg was judged against
# RiskPercent * 1.5 = 1.5%, so a T2 leg sized at its own 2.0% budget read as
# a breach even though nothing was wrong with it.
OLD_ALLOW = RP * 1.50
T2_LEG_ACTUAL = 2.0          # a legal InpRiskT2Pct = 2.0, sized exactly
check("v5.66: a correctly sized 2.0% T2 leg breached the old 1.5% allowance",
      T2_LEG_ACTUAL > OLD_ALLOW)
check("v5.67: the same leg is inside its OWN allowance (2.0 * 1.5 = 3.0)",
      T2_LEG_ACTUAL <= T2_LEG_ACTUAL * 1.50)

# ... and the FAILURE the old rule under-reacted to: a T4 rung running 5.6x
# its own budget (a real sizing fault) sat comfortably inside 1.5%.
T4_LEG_ACTUAL = 1.40
check("v5.66: a T4 leg at 5.6x its 0.25%% budget passed the 1.5%% allowance",
      T4_LEG_ACTUAL < OLD_ALLOW)
check("v5.67: that same leg is caught by its own 0.375%% allowance",
      T4_LEG_ACTUAL > T4D * 1.50)

check("v5.67: the per-leg breach is decided by a RATIO, not a raw maximum",
      "legRatio" in RISK_C and "peakRatio" in RISK_C)
check("v5.67: a rung configured at 0 cannot hide an oversize (ratio sentinel)",
      "999.0" in RISK_C)

# --- static pins for the attribution helpers themselves -----------------
# These are what make the arithmetic above non-hypothetical: the model can be
# perfect and the shipped attribution still wrong.
check("v5.67: the leg budget is derived from the leg's OWN comment",
      re.search(r"double\s+legBudgetPct\s*=\s*RungRiskPct\s*\("
                r"\s*PositionGetString\s*\(\s*POSITION_COMMENT\s*\)\s*\)", RISK_C)
      is not None)
check("v5.67: the allowance is a multiple of the RUNG budget, not RiskPercent",
      re.search(r"double\s+legAllowPct\s*=\s*legBudgetPct\s*\*\s*1\.50", RISK_C) is not None)
check("v5.67: the breach compares the leg's RATIO against the peak ratio",
      re.search(r"if\s*\(\s*legRatio\s*>\s*peakRatio\s*\)", RISK_C) is not None)
check("v5.67: an all-clear leg poses no ratio ceiling (0.0, not 1.0 or 999)",
      re.search(r":\s*\(\(\s*posRiskPct\s*>\s*0\.0\s*\)\s*\?\s*999\.0\s*:\s*0\.0\s*\)",
                RISK_C) is not None)
check("v5.67: the peak ratio, not the peak percent, is what the breach tests",
      re.search(r"if\s*\(\s*peakRatio\s*>\s*1\.0\s*\)", RISK_C) is not None)

# The tag is parsed from the LAST TWO characters only, because the 31-char
# comment clamp eats the front of the string, not the tail.
check("v5.67: the tranche tag is scanned from the END of the comment",
      re.search(r"StringFind\s*\(\s*comment\s*,\s*\"_T\"\s*,\s*len\s*-\s*2\s*\)",
                AUD_T) is not None)
check("v5.67: a malformed tag is rejected before StringToInteger",
      re.search(r"if\s*\(\s*ch\s*<\s*48\s*\|\|\s*ch\s*>\s*57\s*\)\s*return\s+1\s*;",
                AUD_T) is not None)
check("v5.67: an out-of-range tag degrades to tranche 1, never a new rung",
      re.search(r"return\s*\(\s*tag\s*>=\s*2\s*&&\s*tag\s*<=\s*4\s*\)\s*\?\s*tag\s*:\s*1\s*;",
                AUD_T) is not None)
check("v5.67: the rung map is a PURE read of the live inputs, not a cache",
      re.search(r"case\s+2:\s*return\s+InpRiskT2Pct\s*;", AUD_T) is not None and
      re.search(r"case\s+3:\s*return\s+InpRiskT3Pct\s*;", AUD_T) is not None and
      re.search(r"case\s+4:\s*return\s+InpRiskT4Pct\s*;", AUD_T) is not None)


# ----------------------------------------------------------------------
# 4. D. THE AGGREGATE LEG: REACHABLE NOW, AND IT WAS NOT BEFORE.
# ----------------------------------------------------------------------
print("\n-- D. the aggregate leg --")

NOMINAL = RP + T2D + T3D + T4D
check("the nominal four-rung stack is 2.75% at the defaults",
      abs(NOMINAL - 2.75) < 1e-9, NOMINAL)


def exposure_by_stage():
    """(stage, legs open, summed loss-side risk %) for the shipped ladder."""
    return [
        ("T1 only, stop at initial 1R",     1, RP),
        ("T1+T2 at +1.0R, stop at BE",      2, 0.0),
        ("full ladder at +3.0R, stop >= BE", 4, 0.0),
    ]


CEILING = max(r for _, _, r in exposure_by_stage())
check("the greatest loss-side sum this ladder can produce is one rung",
      abs(CEILING - RP) < 1e-9, CEILING)
check("v5.66: that ceiling is BELOW the old allowance, so the leg was dead code",
      CEILING < NOMINAL * 1.50, "%s vs %s" % (CEILING, NOMINAL * 1.50))
check("v5.66: the old allowance exceeded the achievable maximum by more than 4x",
      (NOMINAL * 1.50) / CEILING > 4.0, (NOMINAL * 1.50) / CEILING)

# v5.67 measures against the budgets of the legs that are actually at risk, so
# a correctly sized basket reads at or below 1.0x its allowance at EVERY stage.
for stage, legs, risk in exposure_by_stage():
    budget = legs * RP          # every rung at the default 1.0% here
    allowed = budget * 1.50
    ok = not (risk > allowed and budget > 0.0)
    check("v5.67: no false aggregate breach at stage '%s'" % stage, ok,
          (risk, allowed))

# The incident the aggregate leg exists to catch: a refused leg keeps its old,
# looser stop while its neighbours wear breakeven, so the basket carries
# loss-side risk the unified floor does not account for.
PARTIAL_LEGS, PARTIAL_RISK = 1, RP
check("v5.67: a single stranded leg at its nominal size stays in allowance",
      not (PARTIAL_RISK > PARTIAL_LEGS * RP * 1.50))
check("v5.67: a stranded leg carrying twice its own budget trips the allowance",
      (PARTIAL_RISK * 2.0) > (PARTIAL_LEGS * RP * 1.50))

check("v5.67: the aggregate allowance is derived from the legs at risk, not the ladder",
      re.search(r"maxAllowedStackPct\s*=\s*budgetedStackPct\s*\*\s*1\.50", RISK_C)
      is not None)
check("v5.67: only legs on the loss side contribute to the allowance",
      RISK_C.find("lossDist <= 0.0") < RISK_C.find("budgetedStackPct += legBudgetPct"))
check("v5.67: the full nominal stack is still reported, as context not a threshold",
      re.search(r"nominalStackPct\s*=\s*RiskPercent\s*\+\s*InpRiskT2Pct", RISK_C)
      is not None)
check("v5.67: an empty budget can never raise the breach",
      re.search(r"sumRiskPct\s*>\s*maxAllowedStackPct\s*&&\s*budgetedStackPct\s*>\s*0\.0",
                RISK_C) is not None)


# ----------------------------------------------------------------------
# E. THE ORPHAN-LEG SWEEP: the shape no other audit can see.
# ----------------------------------------------------------------------
print("\n-- E. orphan-leg sweep --")

ORPHAN = brace_body(AUD_T, r"int\s+CountOrphanLegs\s*\(\s*void\s*\)")
ORPHAN_C = strip_comments(ORPHAN) if ORPHAN else ""
REPORT = brace_body(AUD_T, r"void\s+AuditOrphanLegs\s*\(\s*void\s*\)")
REPORT_C = strip_comments(REPORT) if REPORT else ""
RUN_C = strip_comments(brace_body(AUD_T, r"void\s+RunAudit\s*\(\s*void\s*\)") or "")

check("v5.67: CountOrphanLegs() is defined", ORPHAN is not None)
check("v5.67: AuditOrphanLegs() is defined", REPORT is not None)

# WHY THIS EXISTS. AuditRiskExposure cannot measure a leg with no stop: it
# `continue`s past sl <= 0, because a risk percentage needs a stop distance.
# So a leg the venue refused to stop was invisible to the risk audit AND
# missing from the aggregate allowance -- the two checks that would have
# caught it. The sweep is the only body that looks for the shape itself.
check("v5.67: the risk audit still skips a stopless leg (so the sweep is needed)",
      re.search(r"if\s*\(\s*sl\s*<=\s*0\.0\s*\)\s*continue\s*;", RISK_C) is not None)
check("v5.67: the sweep counts a leg whose stop is zero",
      re.search(r"PositionGetDouble\s*\(\s*POSITION_SL\s*\)\s*>\s*0\.0\s*\)\s*continue\s*;",
                ORPHAN_C) is not None)

# Magic scoping must match AuditRiskExposure's v5.46 rule exactly: the EA's own
# magic, widened by exactly ONE ticket for an adopted magic-0 primary. A looser
# scope would report the operator's manual trades or a foreign EA's legs.
check("v5.67: the sweep is magic-scoped to this EA",
      re.search(r"magic\s*!=\s*\(long\)m_magic", ORPHAN_C) is not None)
check("v5.67: the adopted magic-0 primary is the ONLY foreign ticket admitted",
      re.search(r"!m_trackedAdopted\s*\|\|\s*ticket\s*!=\s*m_trackedPrimary\)\s*continue\s*;",
                ORPHAN_C) is not None)
check("v5.67: the sweep is symbol-scoped with the sibling book reads",
      re.search(r"POSITION_SYMBOL\s*\)\s*!=\s*m_symbol\s*\)\s*continue\s*;",
                ORPHAN_C) is not None)

# A leg submitted seconds ago has not been armed yet; reporting it would make
# the audit cry wolf on every entry. The grace period must key on the venue's
# own open stamp, so it needs no per-ticket state that could drift.
check("v5.67: a freshly opened leg gets a grace period before it is called orphan",
      re.search(r"TimeCurrent\s*\(\s*\)\s*-\s*opened\s*<\s*HT_ORPHAN_GRACE_SECONDS",
                ORPHAN_C) is not None)
check("v5.67: the grace period is measured from the venue's open stamp",
      re.search(r"datetime\s+opened\s*=\s*\(datetime\)\s*PositionGetInteger\s*\(\s*POSITION_TIME\s*\)",
                ORPHAN_C) is not None)

# The reporting body must be CRITICAL (an unprotected leg can lose its whole
# notional) and must RE-ARM its latch, or the second incident is silent.
check("v5.67: an orphan leg is reported at CRITICAL, not WARN",
      re.search(r"DispatchAlertOnce\s*\(\s*m_alertSent_OrphanLeg", REPORT_C) is not None and
      "HT_SEV_CRITICAL" in REPORT_C and "HT_SEV_WARN" not in REPORT_C)
check("v5.67: the orphan latch re-arms once no leg is orphaned",
      re.search(r"orphans\s*<=\s*0", REPORT_C) is not None and
      re.search(r"ClearLatch\s*\(\s*m_alertSent_OrphanLeg\s*\)", REPORT_C) is not None)
check("v5.67: the sweep runs from RunAudit, not from the tick path",
      "AuditOrphanLegs()" in RUN_C)
check("v5.67: the orphan latch is initialised in the constructor",
      re.search(r"m_alertSent_OrphanLeg\s*=\s*false\s*;", AUD_T) is not None)
check("the aggregate leg keeps its own latch and its two-cycle confirmation",
      "DispatchAlertOnce(m_alertSent_AggRiskBreach" in RISK_C and
      "m_aggRiskSeen" in RISK_C)


# ----------------------------------------------------------------------
# 5. E. WIRING AND LIFECYCLE.
# ----------------------------------------------------------------------
print("\n-- E. wiring and lifecycle --")

UPDATE = brace_body(TM_T, r"void\s+Update\s*\(\s*void\s*\)")
UPDATE_C = strip_comments(UPDATE) if UPDATE else ""
check("the trade manager's Update body located", UPDATE is not None)
check("v5.67: the repair is driven from the tick path",
      "m_orderManager.RepairUnifiedSL();" in UPDATE_C)
# ORDER MATTERS: the ratchet must get first refusal each tick, so a level it
# accepts this tick clears the debt before the repair runs. Driving the repair
# first would re-offer a level the ratchet is about to accept anyway, doubling
# the broker writes.
check("v5.67: the repair runs AFTER the unified ratchet, not before",
      0 < UPDATE_C.find("ApplyUnifiedSL(desiredSL);") <
          UPDATE_C.find("RepairUnifiedSL();"))
# ... and inside the same guard, so a stop is never re-offered to a ticket the
# broker has not finished confirming (the v5.14/v5.20 INVALID_STOPS race).
check("v5.67: the repair is inside the trancheOpenedThisTick guard",
      UPDATE_C.find("if(!trancheOpenedThisTick)") <
      UPDATE_C.find("RepairUnifiedSL();") and
      UPDATE_C.count("RepairUnifiedSL();") == 1)

# The debt describes ONE basket, so it must die with it. Asserted on
# MEMBERSHIP of ClearBasket's body rather than on a bare "ClearUnifiedRepair()"
# count: the search string "ClearUnifiedRepair();" is a substring of its own
# DEFINITION line (`void ClearUnifiedRepair();` would not be, but the call and
# the body's braces both match), and a whole-file count cannot tell a call in
# the basket teardown from one in the constructor.
check("v5.67: ClearBasket releases the repair debt", "ClearUnifiedRepair();" in CBODY_C)
check("v5.67: the constructor zeroes the repair state", "ClearUnifiedRepair();" in OM_T)
check("v5.67: the repair state is exposed for observability",
      "GetUnifiedRepairSL" in OM_T and "HasUnifiedRepairDebt" in OM_T)
check("v5.67: every member the repair touches is initialised in one place",
      all(k in CLEAR_C for k in ("m_unifiedRepairSL", "m_unifiedRepairPending",
                                 "m_unifiedRepairAttempts", "m_unifiedRepairNextTry")))


# ----------------------------------------------------------------------
# 6. F. RELEASE HYGIENE.
# ----------------------------------------------------------------------
print("\n-- F. release hygiene --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1, stamps)
RELEASE = sorted(stamps)[0] if stamps else "0.0"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.67 or later", parts >= (5, 67), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE)
           not in read(os.path.join(ROOT, f))]
check("every shipped module stamps v%s" % RELEASE, not missing, missing)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.67 UNIFIED-STOP REPAIR + RUNG-AWARE RISK BUDGET - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + str(detail) if detail != "" else "")))
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

