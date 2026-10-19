#!/usr/bin/env python3
"""
v5.63 static verification probe - TRAIL START AT 1.0R + HIGH TABLE
TRAILING-HEALTH AUDIT + AGGREGATE BASKET-RISK LEG.

Two changes landed in v5.63 and this probe pins both.

A. InpTrailStartRR 2.0 -> 1.0.

   v5.62 split the milestone ladder away from the ATR trail gate so the trail
   could be retuned without darkening the ladder's first floor, and then set
   the trail to +2.0R. v5.63 closes that gap: the trail arms on the SAME
   +1.0R rung as the first ladder floor, so from +1.0R onward the basket
   wears both the milestone floors and the ATR component and the tighter of
   the two wins on any tick.

   The one behavioural consequence is deliberate and pinned below: with
   InpTrailStartRR == InpBreakEvenRR == 1.0, the SyncTradeState state machine
   reports STEP_TRAILING at +1.0R rather than STEP_BREAKEVEN, because
   STEP_TRAILING is tested first. At +1.0R the trail genuinely IS armed, so
   STEP_TRAILING is the truthful label. This probe asserts the ORDERING that
   produces it, not just the values, because the ordering is what makes the
   label correct.

B. CHighTableAuditor gains AuditTrailingHealth() plus an aggregate
   basket-risk leg inside AuditRiskExposure().

   The trail check exists because a trail that silently arms and then never
   writes is indistinguishable from a healthy one on any single tick. The
   critical design point, and the main thing this probe guards, is that the
   excursion is measured from PRICE and kept as a peak -- never inferred from
   the stop. Reading the stop to decide whether the stop is stale is
   circular: a fully frozen stop reads NEGATIVE R and would sail through the
   very test meant to catch it.

   The aggregate leg exists because peakRiskPct is blind to what the WHOLE
   basket puts at risk: one leg can sit inside its own budget while the total
   does not. v5.71 bounds that total by the LARGEST rung plus tolerance, not
   by the sum of the ladder: the shared ratchet absorbs every older leg, so
   the nominal sum is unreachable through the live geometry. RiskPercent still
   participates in the max, so a zeroed ladder cannot under-read.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so the multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


AUD_T = read(AUD)
MAIN_T = read(MAIN)
DEFS_T = read(DEFS)
TM_T = read(TM)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals (see v5.32 probe)."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


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


def num_input(text, name):
    m = re.search(r"input\s+double\s+%s\s*=\s*([0-9.]+)\s*;" % re.escape(name), text)
    return float(m.group(1)) if m else None


def latch_lifecycle_ok(body, latch):
    """Mirrors the v5.32 probe's lifecycle test: else-shape or guard-shape."""
    disp = [m.start() for m in
            re.finditer(r"DispatchAlertOnce\(\s*m_alertSent_%s\b" % latch, body)]
    clr = [m.start() for m in
           re.finditer(r"ClearLatch\(\s*m_alertSent_%s\b" % latch, body)]
    if not disp or not clr:
        return False
    if any(c > disp[-1] for c in clr):
        return True
    return any(c < disp[0] and "return" in body[c:disp[0]] for c in clr)


# ----------------------------------------------------------------------
# A. InpTrailStartRR is 1.0, and the state-machine ORDER is pinned
# ----------------------------------------------------------------------
print("\n-- A. InpTrailStartRR is 1.0 and the state machine order is pinned --")

TRAIL_RR = num_input(DEFS_T, "InpTrailStartRR")
BE_RR = num_input(DEFS_T, "InpBreakEvenRR")
T2_RR = num_input(DEFS_T, "InpPyramidT2RR")

check("InpTrailStartRR declares a value", TRAIL_RR is not None)
check("InpTrailStartRR defaults to +1.0R (v5.63)", TRAIL_RR == 1.0, TRAIL_RR)
check("breakeven sits on the same +1.0R rung as the trail",
      BE_RR == 1.0 and TRAIL_RR == 1.0, (BE_RR, TRAIL_RR))
check("the T2 scale-in still lands on the +1.0R rung", T2_RR == 1.0, T2_RR)
# The coincidence is only safe because the ladder is applied unconditionally.
check("the first ladder rung opens no later than the T2 scale-in trigger",
      None not in (BE_RR, T2_RR) and BE_RR <= T2_RR,
      "breakeven@%s T2@%s" % (BE_RR, T2_RR))

# The three live ATR gates must NOT have moved -- v5.63 changed the VALUE, not
# the number of read sites.
trail_gates = re.findall(r"if\(currentRR\s*>=\s*InpTrailStartRR\)", TM_T)
check("the three live ATR-trail gates still read InpTrailStartRR",
      len(trail_gates) == 3, "found %d" % len(trail_gates))

SYNC = func_body(TM_T, r"void\s+SyncTradeState\s*\(\s*void\s*\)") or ""
SYNC_C = strip_comments(SYNC)
check("SyncTradeState still reads InpTrailStartRR",
      re.search(r"rr\s*>=\s*InpTrailStartRR\)\s*step\s*=\s*STEP_TRAILING", SYNC_C)
      is not None)
# THE ORDERING PIN. With the two inputs equal, whichever branch is tested first
# is the label the +1.0R rung reports. STEP_TRAILING must come first, or the rung
# would claim to be a breakeven floor while the ATR trail is armed.
trail_pos = SYNC_C.find("rr >= InpTrailStartRR")
be_pos = SYNC_C.find("rr >= InpBreakEvenRR")
check("STEP_TRAILING is tested before STEP_BREAKEVEN, so +1.0R reports the "
      "armed trail (the accepted v5.63 flip)",
      0 <= trail_pos < be_pos, "trail@%s breakeven@%s" % (trail_pos, be_pos))
check("STEP_BREAKEVEN remains reachable further down the chain",
      be_pos > trail_pos >= 0 and "STEP_BREAKEVEN" in SYNC_C)


# ----------------------------------------------------------------------
# A. Release stamp
# ----------------------------------------------------------------------
stamps = []
for f in ALL_FILES:
    m = re.search(r'#property version\s+"(\d+\.\d+)"', read(os.path.join(ROOT, f)))
    if m:
        stamps.append(m.group(1))
check("all %d shipped sources carry a version stamp" % len(ALL_FILES),
      len(stamps) == len(ALL_FILES), "found %d" % len(stamps))
check("every shipped source carries the SAME release stamp",
      bool(stamps) and len(set(stamps)) == 1, "stamps=%s" % sorted(set(stamps)))
# The tree-wide release, derived rather than pinned, so a bump cannot leave this
# probe asserting a stale literal. Section D compares the auditor against it.
RELEASE = stamps[0] if stamps else "?"


# ----------------------------------------------------------------------
# B. The trail-health body: rule, ingress, latch lifecycle
# ----------------------------------------------------------------------
print("\n-- B. AuditTrailingHealth --")

TRAIL = func_body(AUD_T, r"void\s+AuditTrailingHealth\s*\(\s*void\s*\)")
check("AuditTrailingHealth is defined", TRAIL is not None)

TRAIL_C = strip_comments(TRAIL) if TRAIL else ""

# --- The anti-circularity guarantee -----------------------------------
# The whole check is worthless if it derives the excursion from the stop.
# Assert that the R value fed to the peak memory comes from a PRICE variable,
# and that the stop is only ever read AFTER the peak has been fed.
check("the excursion is computed from a live PRICE, not from the stop",
      re.search(r"double\s+rr\s*=\s*\(isLong\s*\?\s*\(\s*price\s*-\s*m_trackedEntry\s*\)"
                r"\s*:\s*\(\s*m_trackedEntry\s*-\s*price\s*\)\s*\)\s*/\s*m_trackedRRUnit\s*;",
                TRAIL_C) is not None,
      "the excursion must divide a PRICE difference, not a stop difference")
check("the price used is BID for a long and ASK for a short",
      "SYMBOL_BID" in TRAIL_C and "SYMBOL_ASK" in TRAIL_C)
check("the arm test compares the PEAK, never the raw stop",
      re.search(r"if\s*\(\s*m_trailPeakRR\s*<\s*InpTrailStartRR\s*\)", TRAIL_C)
      is not None)
check("the peak memory is fed before the stop is ever read",
      TRAIL_C.find("NoteExcursion(rr)") >= 0
      and TRAIL_C.find("NoteExcursion(rr)") < TRAIL_C.find("BookStopForTicket("))
# THE ANTI-CIRCULARITY GUARANTEE. If the stop's own R leaks into the excursion
# -- before the peak memory is written, or into the R the peak is fed -- the
# check disables itself: a frozen stop reads negative R and sails through the
# very test meant to catch it. Assert the excursion's R is free of the stop.
_rr_stmt = re.search(r"double\s+rr\s*=\s*[^;]*;", TRAIL_C)
check("the excursion's R expression is free of any stop reference",
      _rr_stmt is not None and "sl" not in _rr_stmt.group(0),
      _rr_stmt.group(0).strip() if _rr_stmt else "no rr statement")
_peak_call = re.search(r"NoteExcursion\s*\(([^)]*)\)", TRAIL_C)
check("the peak is fed the price-derived R, not a stop-derived one",
      _peak_call is not None and _peak_call.group(1).strip() == "rr",
      _peak_call.group(1).strip() if _peak_call else "no NoteExcursion call")

# --- The invariant ----------------------------------------------------
check("the live stop is preferred from the book, with the pushed value as fallback",
      re.search(r"double\s+sl\s*=\s*BookStopForTicket\(m_trackedPrimary\)", TRAIL_C)
      is not None
      and re.search(r"if\s*\(\s*sl\s*<=\s*0\.0\s*\)\s*sl\s*=\s*m_trackedSL\s*;",
                    TRAIL_C) is not None)
check("the stop's own R is derived with the debug-vs-long sign convention",
      re.search(r"stopRR\s*=\s*\(isLong\s*\?\s*\(sl\s*-\s*m_trackedEntry\)", TRAIL_C)
      is not None)
check("a stop still on the losing side is what raises the v5.63 alert",
      re.search(r"if\s*\(\s*stopRR\s*<\s*0\.0\s*\)\s*\{[^}]*DispatchAlertOnce"
                r"\(\s*m_alertSent_StaleTrail", TRAIL_C, re.S) is not None)
# v5.68: `stopRR >= 0.0` must no longer be a terminal pass. Clearing the latch
# and returning there is exactly what let a stop frozen at +0.01R read healthy
# forever, so the absence of that shape is itself a requirement.
check("v5.68: leaving the losing side is NOT the whole test any more",
      re.search(r"if\s*\(\s*stopRR\s*>=\s*0\.0\s*\)\s*\{\s*ClearLatch"
                r"\(\s*m_alertSent_StaleTrail\s*\)\s*;\s*return\s*;\s*\}",
                TRAIL_C) is None,
      "the pre-v5.68 terminal pass is still present")
check("a missing stop entirely is reported rather than skipped",
      re.search(r"if\s*\(\s*sl\s*<=\s*0\.0\s*\)\s*\{[^}]*DispatchAlertOnce", TRAIL_C, re.S)
      is not None)
# Slice the no-stop branch EXACTLY: from its own guard to the stopRR line that
# follows it. A looser regex would run past the branch and let a degraded
# message still satisfy the check.
_ns_start = TRAIL_C.find("if(sl <= 0.0)")
_ns_end = TRAIL_C.find("double stopRR")
_nostop = TRAIL_C[_ns_start:_ns_end] if _ns_start >= 0 and _ns_end > _ns_start else ""
# Two distinct faults must produce two distinct, evidence-bearing messages: the
# no-stop branch says the basket is unprotected by ANY source, the trailing-side
# branch says the stop never moved. If either degrades, the operator cannot tell
# them apart.
check("the no-stop alert names the absent stop",
      "no stop attached at the broker" in _nostop,
      "no-stop message lost its evidence")
check("the no-stop alert states BOTH sources were empty",
      "has none recorded either" in _nostop,
      "the no-stop message no longer says the order layer had no stop either")
check("the no-stop alert states the basket is running unprotected",
      "running unprotected" in _nostop)
check("the no-stop alert carries the entry and the 1R for triage",
      "m_trackedEntry" in _nostop and "m_trackedRRUnit" in _nostop)
_stale_start = TRAIL_C.find("double stopRR")
_stale = TRAIL_C[_stale_start:] if _stale_start >= 0 else ""
check("the stale-stop alert names the ticket and the stop's own R",
      "still sits at" in _stale and "losing side of entry" in _stale)

# --- Unjudgeable states must stand down, not guess --------------------
check("the body stands down when no geometry has been pushed",
      re.search(r"if\s*\(\s*!\s*m_haveGeometry", TRAIL_C) is not None)
check("the body stands down when there is no tracked trade",
      "m_trackedActive" in TRAIL_C and "m_trackedPrimary == 0" in TRAIL_C)
check("DIR_NONE is treated as unjudgeable rather than guessed",
      re.search(r"!\s*isLong\s*&&\s*m_trackedDir\s*!=\s*DIR_SHORT", TRAIL_C)
      is not None)
check("a non-positive price stands the check down instead of dividing",
      re.search(r"if\s*\(\s*price\s*<=\s*0\.0\s*\)", TRAIL_C) is not None)

check("the alert is WARN, not CRITICAL",
      "HT_SEV_WARN" in TRAIL_C)
check("the alert names the offending ticket",
      "IntegerToString((long)m_trackedPrimary)" in TRAIL_C)
check("the alert reports the observed peak excursion in R",
      "DoubleToString(m_trailPeakRR, 2)" in TRAIL_C)

check("AuditTrailingHealth re-arms its latch on every healthy path",
      TRAIL_C.count("ClearLatch(m_alertSent_StaleTrail)") == 5,
      "found %d" % TRAIL_C.count("ClearLatch(m_alertSent_StaleTrail)"))
check("the latch lifecycle pairing holds (raise + re-arm in the same body)",
      latch_lifecycle_ok(TRAIL_C, "StaleTrail"))
check("v5.68: the lagging-floor latch has the same pairing",
      latch_lifecycle_ok(TRAIL_C, "LaggingFloor"))
# Every healthy exit of the body must re-arm, or the incident is reported once
# and then silenced for the rest of the session. The body now owns TWO latches,
# so the bound is on the sum: at most one exit may stand down without a re-arm,
# and that one is the deliberate first-sighting wait (which has raised nothing).
_returns = len(re.findall(r"\breturn\s*;", TRAIL_C))
_clears = sum(len(re.findall(r"ClearLatch\(\s*m_alertSent_%s\b" % _l, TRAIL_C))
              for _l in ("StaleTrail", "LaggingFloor"))
check("every early exit in the body is paired with a re-arm",
      _clears >= _returns - 1,
      "clears=%d returns=%d" % (_clears, _returns))



# ----------------------------------------------------------------------
# B2. The geometry ingress and the peak memory
# ----------------------------------------------------------------------
print("\n-- B2. SetTrackedGeometry and the peak memory --")

GEO = func_body(AUD_T, r"void\s+SetTrackedGeometry\s*\(")
check("SetTrackedGeometry is defined", GEO is not None)
GEO_C = strip_comments(GEO) if GEO else ""

check("SetTrackedGeometry accepts ticket, direction, entry, 1R and the stop",
      re.search(r"SetTrackedGeometry\s*\(\s*ulong\s+\w+\s*,\s*ENUM_TRADE_DIRECTION\s+\w+\s*,"
                r"\s*double\s+\w+\s*,\s*double\s+\w+\s*,\s*double\s+\w+\s*\)", AUD_T)
      is not None)
check("a carrier-less push clears the geometry instead of storing zeros",
      re.search(r"if\s*\(\s*ticket\s*==\s*0\s*\|\|\s*rrUnit\s*<=\s*0\.0", GEO_C)
      is not None)
check("the carrier-less branch clears m_haveGeometry",
      re.search(r"m_haveGeometry\s*=\s*false", GEO_C) is not None)
check("a valid push stores the 1R unit",
      re.search(r"m_trackedRRUnit\s*=\s*rrUnit\s*;", GEO_C) is not None)
check("a valid push stores the entry and the direction",
      re.search(r"m_trackedEntry\s*=\s*entry\s*;", GEO_C) is not None
      and re.search(r"m_trackedDir\s*=\s*dir\s*;", GEO_C) is not None)
check("a valid push records that geometry is available",
      re.search(r"m_haveGeometry\s*=\s*true", GEO_C) is not None)
check("the peak resets when the tracked ticket CHANGES",
      re.search(r"if\s*\(\s*ticket\s*!=\s*m_trailPeakTicket\s*\)\s*\{[^}]*m_trailPeakRR\s*=\s*0\.0",
                GEO_C, re.S) is not None)

NOTE = func_body(AUD_T, r"void\s+NoteExcursion\s*\(")
check("NoteExcursion is defined", NOTE is not None)
check("NoteExcursion only ever raises the peak",
      NOTE is not None
      and re.search(r"if\s*\(\s*rr\s*>\s*m_trailPeakRR\s*\)\s*m_trailPeakRR\s*=\s*rr\s*;",
                    strip_comments(NOTE)) is not None)

# Member declarations + constructor init, following the existing latch pattern.
for member, decl in (("m_alertSent_StaleTrail", r"bool\s+m_alertSent_StaleTrail\s*;"),
                     ("m_alertSent_AggRiskBreach", r"bool\s+m_alertSent_AggRiskBreach\s*;"),
                     ("m_haveGeometry", r"bool\s+m_haveGeometry\s*;"),
                     ("m_trackedEntry", r"double\s+m_trackedEntry\s*;"),
                     ("m_trackedRRUnit", r"double\s+m_trackedRRUnit\s*;"),
                     ("m_trackedSL", r"double\s+m_trackedSL\s*;"),
                     ("m_trailPeakRR", r"double\s+m_trailPeakRR\s*;"),
                     ("m_trailPeakTicket", r"ulong\s+m_trailPeakTicket\s*;")):
    check("%s is a declared member" % member,
          re.search(decl, AUD_T) is not None)
    check("%s is initialised in the constructor" % member,
          re.search(r"%s\s*=\s*(false|0|0\.0|DIR_NONE)\s*;" % re.escape(member), AUD_T)
          is not None)

check("m_trackedDir is declared as the shared direction enum",
      re.search(r"ENUM_TRADE_DIRECTION\s+m_trackedDir\s*;", AUD_T) is not None)
check("the auditor still includes the shared defines for the enum + inputs",
      '#include "OttoDefines.mqh"' in AUD_T)


# ----------------------------------------------------------------------
# B3. RunAudit wires the new body, and the class is still closed
# ----------------------------------------------------------------------
print("\n-- B3. RunAudit wiring --")

RUN = func_body(AUD_T, r"void\s+RunAudit\s*\(\s*void\s*\)") or ""
for name in ("AuditDrawdown", "AuditOrderHealth", "AuditStateConsistency",
             "AuditRiskExposure", "AuditTrailingHealth"):
    check("RunAudit drives %s()" % name, (name + "()") in RUN)

check("the observation-body count in the doc block names six",
      "the six observation bodies" in AUD_T)
# The class must be entirely inside the guard: the v5.32 module shipped a latch
# AFTER the #endif, where the constructor's assignment bound to a different
# object. Split on the LAST #endif specifically -- splitting on the first would
# look at a comment that merely mentions the guard and always pass.
TAIL_AFTER_GUARD = AUD_T.rsplit("#endif", 1)[-1]
check("no latch or geometry state is declared outside the include guard",
      "m_alertSent_" not in TAIL_AFTER_GUARD
      and "m_trackedRRUnit" not in TAIL_AFTER_GUARD)
check("the include guard is still present and closes the file",
      "#endif  // __OTTO_HIGH_TABLE_AUDITOR__" in AUD_T)
# Nothing may sit between the CLASS body's closing brace and the guard -- that
# is where the v5.32 module put a latch, outside the class but inside the guard,
# so the constructor's assignment bound to a different object.
_upto_guard = AUD_T.rsplit("#endif", 1)[0]
_after_class = _upto_guard[_upto_guard.rfind("};") + 2:]
check("no state is declared between the class body and the include guard",
      _after_class.strip() == "", repr(_after_class.strip()[:80]))
# The smuggled-latch class is caught by the inventory rule the v5.32 probe uses:
# every declared latch must be initialised in the constructor, so a latch added
# anywhere unpaired with a ctor assignment cannot compile-and-go-quiet.
ALL_LATCHES = re.findall(r"bool\s+m_alertSent_(\w+)\s*;", AUD_T)
check("the auditor declares only named latch members", len(ALL_LATCHES) >= 8,
      "found %d: %s" % (len(ALL_LATCHES), sorted(ALL_LATCHES)))
_uninit = [l for l in ALL_LATCHES
           if not re.search(r"m_alertSent_%s\s*=\s*false\s*;" % re.escape(l), AUD_T)]
check("every declared latch is initialised in the constructor", not _uninit,
      "uninitialised: %s" % _uninit)



# ----------------------------------------------------------------------
# C. The aggregate basket-risk leg (additive to AuditRiskExposure)
# ----------------------------------------------------------------------
print("\n-- C. Aggregate basket risk --")

RISK = func_body(AUD_T, r"void\s+AuditRiskExposure\s*\(\s*void\s*\)")
check("AuditRiskExposure is defined", RISK is not None)
RISK_C = strip_comments(RISK) if RISK else ""

# --- THE v5.46 SHAPE MUST SURVIVE BYTE-FOR-BYTE ------------------------
# test_v546_risk_isolation.py pins these exact forms; the v5.63 leg is
# additive only. If any of these fail, v5.63 broke a shipped guarantee.
check("v5.46: per-leg MathAbs distance is still gone",
      "MathAbs" not in RISK_C)
check("v5.46: the signed loss-side distance is intact",
      re.search(r"lossDist\s*=\s*isLong\s*\?\s*\(\s*entry\s*-\s*sl\s*\)\s*:"
                r"\s*\(\s*sl\s*-\s*entry\s*\)", RISK_C) is not None)
check("v5.46: a profit-side stop is still skipped before any money is computed",
      re.search(r"if\s*\(\s*lossDist\s*<=\s*0\.0\s*\)\s*continue\s*;", RISK_C)
      is not None)
check("v5.46: the 50% per-leg tolerance is unchanged",
      re.search(r"maxAllowedRiskPct\s*=\s*RiskPercent\s*\*\s*1\.50", RISK_C)
      is not None)
check("v5.46: the peak is still the WORST leg",
      re.search(r"if\s*\(\s*posRiskPct\s*>\s*peakRiskPct\s*\)", RISK_C) is not None)
check("v5.46: the peak winner's ticket is still recorded from the local ticket",
      re.search(r"peakTicket\s*=\s*ticket\s*;", RISK_C) is not None)
check("v5.46: the money conversion is unchanged",
      re.search(r"moneyAtRisk\s*=\s*distPts\s*\*\s*tickValue\s*\*\s*vol\s*;", RISK_C)
      is not None)
check("v5.46: the per-leg breach still dispatches as WARN through its own latch",
      "DispatchAlertOnce(m_alertSent_RiskBreach" in RISK_C
      and "HT_SEV_WARN" in RISK_C)
check("v5.46: a clean cycle still re-arms the per-leg latch",
      re.search(r"ClearLatch\(m_alertSent_RiskBreach\)", RISK_C) is not None)

# --- The new leg ------------------------------------------------------
check("the sum accumulates each leg's loss-side percentage",
      re.search(r"sumRiskPct\s*\+=\s*posRiskPct\s*;", RISK_C) is not None)
check("the sum starts at zero before the loop",
      re.search(r"double\s+sumRiskPct\s*=\s*0\.0\s*;", RISK_C) is not None)
# v5.71: the bound is keyed to the LARGEST rung, not the sum of the ladder.
# The shared ratchet puts every older leg at or beyond the live stop, so the
# nominal sum (2.75%) is unreachable and could not fire on the stacking it was
# documented to catch. maxRungRiskPct folds in RiskPercent, which preserves the
# v5.70 property: a zeroed or inverted ladder cannot resolve the ceiling below
# the primary rung.
check("the aggregate bound is the largest rung, not the summed ladder",
      re.search(r"maxRungRiskPct\s*=\s*MathMax\(\s*InpRiskT2Pct\s*,"
                r"\s*InpRiskT3Pct\s*\)", RISK_C) is not None and
      re.search(r"maxRungRiskPct\s*=\s*MathMax\(\s*maxRungRiskPct\s*,"
                r"\s*InpRiskT4Pct\s*\)", RISK_C) is not None)
check("the unreachable nominal sum is retired from the leg",
      "nominalStackPct" not in RISK_C)
check("RiskPercent still participates, so a zeroed ladder cannot under-read",
      re.search(r"maxRungRiskPct\s*=\s*MathMax\(\s*maxRungRiskPct\s*,"
                r"\s*RiskPercent\s*\)", RISK_C) is not None)
check("the worst-rung allowance keeps the same 50% tolerance",
      re.search(r"maxAllowedAggPct\s*=\s*maxRungRiskPct\s*\*\s*1\.50", RISK_C)
      is not None)
check("the aggregate test is strictly greater-than, like the per-leg one",
      re.search(r"if\s*\(\s*sumRiskPct\s*>\s*maxAllowedAggPct\s*\)", RISK_C)
      is not None)
check("the aggregate breach dispatches through its OWN latch",
      "DispatchAlertOnce(m_alertSent_AggRiskBreach" in RISK_C)
check("the aggregate alert is WARN",
      RISK_C.count("HT_SEV_WARN") >= 2)
check("the aggregate alert distinguishes itself from the per-leg one",
      "aggregate basket risk breach" in RISK_C)
check("the aggregate breach is confirmed across two cycles",
      re.search(r"if\s*\(\s*m_aggRiskSeen\s*\)", RISK_C) is not None)
check("the second-cycle flag is set on the first sighting",
      re.search(r"m_aggRiskSeen\s*=\s*true\s*;", RISK_C) is not None)
check("a healthy basket re-arms the aggregate latch AND clears the flag",
      re.search(r"ClearLatch\(m_alertSent_AggRiskBreach\)\s*;\s*\n\s*"
                r"m_aggRiskSeen\s*=\s*false\s*;", RISK_C) is not None)
check("the aggregate latch lifecycle pairing holds",
      latch_lifecycle_ok(RISK_C, "AggRiskBreach"))
check("the two legs share ONE loop and ONE loss-side guard",
      RISK_C.count("for(int idx = PositionsTotal() - 1; idx >= 0; idx--)") == 1
      and RISK_C.count("if(lossDist <= 0.0) continue;") == 1)



# ----------------------------------------------------------------------
# C2. The push in otto.mq5 carries the geometry
# ----------------------------------------------------------------------
print("\n-- C2. otto.mq5 geometry push --")

PUSH = func_body(MAIN_T, r"void\s+HighTablePushFacts\s*\(\s*void\s*\)")
check("HighTablePushFacts is defined", PUSH is not None)
PUSH_C = strip_comments(PUSH) if PUSH else ""

check("the ingress pushes the risk geometry",
      "SetTrackedGeometry(" in PUSH_C)
check("the geometry is pushed on the SAME record as the tracked trade",
      re.search(r"adopted\s*=\s*active\.adoptedManual\s*;\s*\n[^}]*SetTrackedGeometry\(",
                PUSH_C, re.S) is not None)
check("the geometry push takes the ticket from the record",
      re.search(r"SetTrackedGeometry\(active\.ticket,\s*active\.direction,", PUSH_C)
      is not None)
check("the geometry push takes the entry from the record",
      "active.entryPrice" in PUSH_C)
check("the 1R unit is taken from the order layer's own accessor",
      "g_orderManager.GetBasketRRUnit()" in PUSH_C)
check("the working stop is taken from the record",
      "active.currentTrailSL" in PUSH_C)
# The push must stay INSIDE the same GetActiveTradeRef branch: a geometry push
# with no active trade would offer a stale 1R from the previous basket.
check("the geometry push is gated by the active-trade test",
      re.search(r"if\s*\(\s*g_orderManager\.GetActiveTradeRef\(active\)\s*\)\s*\{[^}]*"
                r"SetTrackedGeometry\(", PUSH_C, re.S) is not None)
check("the ingress still pushes the tracked trade",
      "SetTrackedLegs(" in PUSH_C)
check("the ingress still takes the ticket from the order layer, not the book",
      "GetActiveTradeRef(" in PUSH_C)
check("the ingress still pushes the drawdown anchors",
      "SetSafetyBaseline(" in PUSH_C)
check("the ingress advances its reject high-water mark",
      "g_htPushedRejects = rejects;" in PUSH_C)
check("the ingress advances its stop-failure high-water mark",
      "g_htPushedStopFails = stopFails;" in PUSH_C)

# The accessor the push relies on must exist and be non-mutating.
ORD_T = read(os.path.join(ROOT, "COttoOrderManager.mqh"))
check("the order manager exposes the basket 1R as a const accessor",
      re.search(r"double\s+GetBasketRRUnit\s*\(void\)\s*const\s*\{\s*return\s+m_basketRRUnit",
                ORD_T) is not None)
check("GetActiveTradeRef is still a const read of the active record",
      re.search(r"bool\s+GetActiveTradeRef\s*\(\s*SActiveTrade\s*&\s*outTrade\s*\)\s*const",
                ORD_T) is not None)

# The auditor must remain structurally read-only: no trade module may appear.
check("the auditor still includes NO trade module",
      not re.search(r"#include\s+\"COtto\w+\.mqh\"", AUD_T))


# ----------------------------------------------------------------------
# D. v5.68 -- the milestone floor, the orphan sweep
# ----------------------------------------------------------------------
print("\n-- D. v5.68 milestone floor + orphan sweep --")

check("the auditor carries the tree-wide release stamp",
      RELEASE != "?" and
      re.search(r'#property\s+version\s+"%s"' % re.escape(RELEASE), AUD_T) is not None,
      "auditor=%s tree=%s" % (
          (re.search(r'#property\s+version\s+"([\d.]+)"', AUD_T) or [None, "?"])[1],
          RELEASE))

# --- The floor helper --------------------------------------------------
FLOOR = func_body(AUD_T, r"double\s+ExpectedFloorRR\s*\(\s*const\s+double\s+peakRR\s*,"
                          r"\s*bool\s*&\s*owesFloor\s*\)\s*const")
check("ExpectedFloorRR is a const member taking an out-parameter", FLOOR is not None)
FLOOR_C = strip_comments(FLOOR) if FLOOR else ""

# The rungs are read from the LIVE inputs, never from literals. This is the same
# rule the rest of the class follows and it is what keeps the auditor honest if
# the ladder is retuned.
for _inp in ("InpLockProfitRR", "InpLockProfitTargetRR",
             "InpLockProfit2RR", "InpLockProfit2TargetRR", "InpBreakEvenRR"):
    check("the floor reads the live input %s" % _inp, _inp in FLOOR_C)

# Both inputs of a rung must gate it, so a rung disabled by setting its trigger
# OR its target to 0.0 cannot manufacture an expectation the EA will never meet.
check("rung 3 is guarded by BOTH its trigger and its target",
      re.search(r"InpLockProfitRR\s*>\s*0\.0\s*&&\s*InpLockProfitTargetRR\s*>\s*0\.0",
                FLOOR_C) is not None)
check("rung 2 is guarded by BOTH its trigger and its target",
      re.search(r"InpLockProfit2RR\s*>\s*0\.0\s*&&\s*InpLockProfit2TargetRR\s*>\s*0\.0",
                FLOOR_C) is not None)
check("the breakeven rung is guarded by its own trigger",
      re.search(r"InpBreakEvenRR\s*>\s*0\.0\s*&&\s*peakRR\s*>=\s*InpBreakEvenRR",
                FLOOR_C) is not None)

# Highest rung first: at +3.0R the stop is already at +2.0R, so testing the lower
# rung first would under-state the floor and let a lagging stop through.
_i3 = FLOOR_C.find("InpLockProfitRR > 0.0")
_i2 = FLOOR_C.find("InpLockProfit2RR > 0.0")
_ib = FLOOR_C.find("InpBreakEvenRR > 0.0")
check("the ladder is resolved highest rung first",
      -1 < _i3 < _i2 < _ib, "positions: 3.0R=%d 2.0R=%d BE=%d" % (_i3, _i2, _ib))

check("below the first rung the helper reports that NO floor is owed",
      re.search(r"owesFloor\s*=\s*false\s*;", FLOOR_C) is not None
      and re.search(r"owesFloor\s*=\s*true\s*;", FLOOR_C) is not None)
check("the breakeven rung owes exactly 0.0R, not a re-derived offset",
      re.search(r"peakRR\s*>=\s*InpBreakEvenRR\s*\)\s*return\s+0\.0\s*;", FLOOR_C)
      is not None)

# ANTI-DUPLICATION. The helper must not re-derive the trade manager's
# arithmetic: beOffset, the ATR trail and the half-risk rung are its private
# business, and a second copy here would drift silently.
check("the floor does not re-derive the trade manager's beOffset/ATR/half-risk",
      not re.search(r"beOffset|ATR|InpCutRiskRR|HalfRisk", FLOOR_C),
      "the helper duplicates trade-manager arithmetic")

# --- The derived table: recomputed from the live defines, never pinned ----
_lp3r = num_input(DEFS_T, "InpLockProfitRR")
_lp3t = num_input(DEFS_T, "InpLockProfitTargetRR")
_lp2r = num_input(DEFS_T, "InpLockProfit2RR")
_lp2t = num_input(DEFS_T, "InpLockProfit2TargetRR")
_ber = num_input(DEFS_T, "InpBreakEvenRR")
check("every ladder input resolves from OttoDefines",
      None not in (_lp3r, _lp3t, _lp2r, _lp2t, _ber),
      str((_lp3r, _lp3t, _lp2r, _lp2t, _ber)))


def expected_floor(peak, r3, t3, r2, t2, be):
    """Independent re-implementation of the SPEC, not a copy of the MQL."""
    if r3 > 0.0 and t3 > 0.0 and peak >= r3:
        return t3
    if r2 > 0.0 and t2 > 0.0 and peak >= r2:
        return t2
    if be > 0.0 and peak >= be:
        return 0.0
    return None  # nothing owed


# The peaks span below-BE, each rung boundary, and well past the top.
_PEAKS = [0.0, 0.5, 0.99, 1.0, 1.5, 1.99, 2.0, 2.5, 2.99, 3.0, 4.0, 9.0]
_ROWS = [(p, expected_floor(p, _lp3r, _lp3t, _lp2r, _lp2t, _ber)) for p in _PEAKS]

# Sanity on the spec itself, before it is used as an oracle: the ladder must be
# monotone -- a wider excursion can never EARN a looser floor.
_owed = [f for _, f in _ROWS if f is not None]
check("the floor spec is monotone non-decreasing in the peak",
      all(b >= a for a, b in zip(_owed, _owed[1:])), str(_ROWS))
check("below InpBreakEvenRR no floor is owed",
      all(f is None for p, f in _ROWS if p < _ber))
check("at InpBreakEvenRR the floor is 0.0R (cost-covering breakeven)",
      expected_floor(_ber, _lp3r, _lp3t, _lp2r, _lp2t, _ber) == 0.0)
check("at InpLockProfit2RR the floor is the +1.0R rung",
      expected_floor(_lp2r, _lp3r, _lp3t, _lp2r, _lp2t, _ber) == _lp2t)
check("at InpLockProfitRR the floor is the +2.0R rung",
      expected_floor(_lp3r, _lp3r, _lp3t, _lp2r, _lp2t, _ber) == _lp3t)

# The shipped values, asserted as the ladder the EA actually ships.
check("the shipped ladder is +1.0R->BE, +2.0R->+1.0R, +3.0R->+2.0R",
      (_ber, _lp2r, _lp2t, _lp3r, _lp3t) == (1.0, 2.0, 1.0, 3.0, 2.0),
      str((_ber, _lp2r, _lp2t, _lp3r, _lp3t)))

# --- The tolerance -----------------------------------------------------
_tol = re.search(r"#define\s+HT_FLOOR_TOLERANCE_R\s+([0-9.]+)", AUD_T)
check("HT_FLOOR_TOLERANCE_R is defined", _tol is not None)
TOL = float(_tol.group(1)) if _tol else -1.0
check("the tolerance is positive, so one cycle of ratchet lag is absorbed",
      TOL > 0.0, "tolerance=%s" % TOL)
# The tolerance exists to absorb lag, not to excuse a missing rung. The tightest
# rung spacing in the ladder is the gap between consecutive floors.
_gaps = [abs(b - a) for a, b in zip(_owed, _owed[1:]) if b != a]
check("the tolerance is far smaller than the tightest rung gap",
      bool(_gaps) and TOL < min(_gaps) / 2.0,
      "tolerance=%s tightest gap=%s" % (TOL, min(_gaps) if _gaps else None))

# --- The body actually uses it ----------------------------------------
check("AuditTrailingHealth calls ExpectedFloorRR on the PEAK",
      re.search(r"ExpectedFloorRR\s*\(\s*m_trailPeakRR\s*,", TRAIL_C) is not None)
check("the floor test honours the tolerance",
      re.search(r"stopRR\s*>=\s*floorRR\s*-\s*HT_FLOOR_TOLERANCE_R", TRAIL_C)
      is not None)
check("the floor test is a LOWER bound (a tighter trail still passes)",
      "stopRR >= floorRR" in TRAIL_C and "MathAbs" not in TRAIL_C,
      "the floor test is not one-sided")
check("the lagging-floor alert is WARN, not CRITICAL",
      re.search(r"DispatchAlertOnce\(\s*m_alertSent_LaggingFloor[\s\S]{0,1600}?HT_SEV_WARN",
                TRAIL_C) is not None)
check("the lagging-floor alert names the floor it owes and the stop it found",
      "floorRR" in TRAIL_C and "stopRR" in TRAIL_C)
check("the lagging-floor alert is confirmed across two cycles",
      re.search(r"if\s*\(\s*!\s*m_lagFloorSeen\s*\)", TRAIL_C) is not None)

# --- Latch initialisation ---------------------------------------------
# Scoped to the CONSTRUCTOR body: a tree-wide search would also match the
# healthy-path assignment inside the audit bodies, so deleting the constructor
# line would still "pass" against a file-scope artefact elsewhere.
CTOR = func_body(AUD_T, r"CHighTableAuditor\s*\(\s*void\s*\)")
check("the constructor is defined", CTOR is not None)
CTOR_C = strip_comments(CTOR) if CTOR else ""

check("the lagging-floor latch and its confirm flag start false",
      re.search(r"m_alertSent_LaggingFloor\s*=\s*false\s*;", CTOR_C) is not None
      and re.search(r"m_lagFloorSeen\s*=\s*false\s*;", CTOR_C) is not None,
      "an uninitialised latch would silence the first incident")
check("the orphan latch and its confirm flag start false",
      re.search(r"m_alertSent_OrphanLeg\s*=\s*false\s*;", CTOR_C) is not None
      and re.search(r"m_orphanSeen\s*=\s*false\s*;", CTOR_C) is not None,
      "an uninitialised latch would silence the first incident")
check("both v5.68 latches are declared INSIDE the class, before the guard",
      AUD_T.find("m_alertSent_LaggingFloor;") > 0
      and AUD_T.find("m_alertSent_OrphanLeg;") > 0
      and AUD_T.find("#endif  // __OTTO_HIGH_TABLE_AUDITOR__") > 0
      and AUD_T.find("m_alertSent_LaggingFloor;")
      < AUD_T.find("#endif  // __OTTO_HIGH_TABLE_AUDITOR__")
      and AUD_T.find("m_alertSent_OrphanLeg;")
      < AUD_T.find("#endif  // __OTTO_HIGH_TABLE_AUDITOR__"))

# --- The orphan sweep --------------------------------------------------
CONS = func_body(AUD_T, r"void\s+AuditStateConsistency\s*\(\s*void\s*\)")
check("AuditStateConsistency is defined", CONS is not None)
CONS_C = strip_comments(CONS) if CONS else ""

COUNT_C = strip_comments(func_body(AUD_T,
                                   r"int\s+CountOrphanLegs\s*\(\s*void\s*\)\s*const") or "")
FIRST_C = strip_comments(func_body(AUD_T,
                                   r"ulong\s+FirstOrphanLegTicket\s*\(\s*void\s*\)\s*const") or "")
check("the orphan sweep reads the live broker stop", "POSITION_SL" in COUNT_C)
check("the orphan sweep is magic-scoped, so adopted -MAN legs cannot trip it",
      re.search(r"POSITION_MAGIC\)\s*!=\s*\(long\)m_magic", COUNT_C) is not None,
      "the sweep is not magic-scoped")
check("the orphan sweep treats a NON-POSITIVE stop as the fault",
      re.search(r"POSITION_SL\)\s*>\s*0\.0\s*\)\s*continue\s*;", COUNT_C) is not None
      and re.search(r"POSITION_SL\)\s*>\s*0\.0\s*\)\s*continue\s*;", FIRST_C) is not None)
check("the orphan sweep is symbol-scoped too",
      "POSITION_SYMBOL" in COUNT_C and "POSITION_SYMBOL" in FIRST_C)
check("the sweep runs in AuditStateConsistency, not behind the phantom branch",
      "CountOrphanLegs()" in CONS_C
      and CONS_C.find("CountOrphanLegs()") < CONS_C.find("bool phantom"))
# ...and it must run UNCONDITIONALLY. An ordering test alone cannot see this:
# a conditional call still sits textually above `bool phantom`. The statement
# has to be a plain assignment with no gate of its own.
check("the sweep call is unconditional, not gated on the phantom test",
      re.search(r"int\s+orphans\s*=\s*CountOrphanLegs\(\)\s*;", CONS_C) is not None,
      "the sweep is called conditionally")
check("the orphan alert is WARN, not CRITICAL",
      re.search(r"DispatchAlertOnce\(\s*m_alertSent_OrphanLeg[\s\S]{0,900}?HT_SEV_WARN",
                CONS_C) is not None)
check("the orphan alert names the offending ticket",
      "FirstOrphanLegTicket()" in CONS_C)
check("the orphan alert is confirmed across two cycles",
      re.search(r"if\s*\(\s*orphans\s*>\s*0\s*&&\s*m_orphanSeen\s*\)", CONS_C) is not None
      and re.search(r"m_orphanSeen\s*=\s*\(\s*orphans\s*>\s*0\s*\)", CONS_C) is not None,
      "the first sighting must only advance the confirm flag")
check("the orphan latch re-arms when the book is clean",
      latch_lifecycle_ok(CONS_C, "OrphanLeg"))

# The auditor must STILL be structurally read-only after all of this.
check("v5.68 added no trade-module include",
      not re.search(r"#include\s+\"COtto\w+\.mqh\"", AUD_T))
check("the orphan sweep declares itself read-only (no CTrade, no COtto type)",
      not re.search(r"\bCTrade\b|\bCOtto\w+", COUNT_C + FIRST_C))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.63 TRAIL START 1.0R + TRAIL-HEALTH + AGGREGATE-RISK - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + detail) if detail else ""))
    print("-" * 74)
    print("  %d / %d checks passed" % (passed, total))
    if passed != total:
        print("*** FAILURES PRESENT ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

