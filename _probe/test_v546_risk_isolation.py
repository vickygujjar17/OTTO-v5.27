"""
v5.46 static verification probe - RISK-AUDIT LOSS-SIDE ISOLATION + ADOPTED LEGS.

AuditRiskExposure() in CHighTableAuditor.mqh is the EA's independent position
risk audit: it reads the TERMINAL BOOK, not the order layer, so it measures the
risk that ACTUALLY exists rather than the risk the sizer believes it wrote.
v5.46 closes two blind spots in that measurement. Neither is visible to the
compiler gate -- each is a property of a formula, not of a type.

  1. LOSS-SIDE ONLY. `MathAbs(entry - sl)` is direction-agnostic: it returns a
     POSITIVE distance whether the stop sits below entry (real loss) or above
     it (a stop the unified trail has ratcheted to break-even / into profit).
     So the moment ApplyUnifiedSL moved a stop to the profit side, the auditor
     went on reporting `distPts * tickValue * vol` as capital at risk and
     raised "position risk breach" against a leg that could no longer lose
     money -- a false CRITICAL from the loudest channel the EA owns. The fix
     isolates the LOSING side with a signed test
     (`isLong ? entry - sl : sl - entry`) and skips the leg when it is <= 0.

  2. ADOPTED LEGS ARE VISIBLE. Every book read in this class is magic-scoped
     (`POSITION_MAGIC == m_magic`), which is correct for the desync test but
     also hid the adopted magic-0 primary from the risk audit entirely -- the
     one leg the operator did NOT size was the one leg never checked. The
     filter now admits EXACTLY that ticket: a magic mismatch still skips unless
     m_trackedAdopted is set AND ticket == m_trackedPrimary. Keying on the
     ticket, rather than on magic 0 alone, keeps the blast radius at one
     position so an unrelated manual / foreign-EA leg still cannot pass.

  3. THE PUSH THAT MAKES (2) LIVE. The bypass reads m_trackedAdopted, which is
     only ever set by SetTrackedLegs(). otto.mq5 must therefore still hand it
     the manager's adoptedManual flag, or the exception is dead code that
     silently never fires -- the exact failure mode this probe exists to catch.

The 35% tolerance and the WARN dispatch are asserted UNCHANGED: this release
narrows what counts as risk, it does not retune the threshold.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")

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


AUDIT = func_body(AUD_T, r"void\s+AuditRiskExposure\s*\(\s*void\s*\)")
check("AuditRiskExposure located", AUDIT is not None)

# Assert on the function with comments stripped, so the v5.46 header note that
# QUOTES `MathAbs(entry - sl)` cannot itself satisfy (or break) any check.
CODE = strip_comments(AUDIT) if AUDIT else ""

# ----------------------------------------------------------------------
# 1. LOSS-SIDE ISOLATION
# ----------------------------------------------------------------------
check("the direction-agnostic MathAbs distance is GONE from the audit",
      "MathAbs" not in CODE,
      "MathAbs still present" if "MathAbs" in CODE else "")

check("the leg's direction is read from POSITION_TYPE",
      re.search(r"POSITION_TYPE\s*\)\s*==\s*POSITION_TYPE_BUY", CODE) is not None)

check("lossDist is the SIGNED loss-side distance (long: entry-sl, short: sl-entry)",
      re.search(r"lossDist\s*=\s*isLong\s*\?\s*\(\s*entry\s*-\s*sl\s*\)\s*:"
                r"\s*\(\s*sl\s*-\s*entry\s*\)", CODE) is not None)

check("a stop on the profit side is skipped before any money is computed",
      re.search(r"if\s*\(\s*lossDist\s*<=\s*0\.0\s*\)\s*continue\s*;", CODE) is not None)


# ----------------------------------------------------------------------
# 2. ADOPTED MAGIC-0 LEGS
# ----------------------------------------------------------------------
check("the ticket and magic are read once into named locals",
      re.search(r"ticket\s*=\s*\(ulong\)PositionGetInteger\(POSITION_TICKET\)", CODE) is not None
      and re.search(r"magic\s*=\s*PositionGetInteger\(POSITION_MAGIC\)", CODE) is not None)

check("the blanket magic-scoped `continue` is GONE",
      re.search(r"if\s*\(\s*\(long\)PositionGetInteger\(POSITION_MAGIC\)[^;]*continue", CODE) is None)

check("a magic mismatch still skips by default",
      re.search(r"magic\s*!=\s*\(long\)m_magic", CODE) is not None)

check("the ONLY exception is gated on m_trackedAdopted",
      re.search(r"!\s*m_trackedAdopted", CODE) is not None)

check("the exception is keyed to the EXACT tracked ticket",
      re.search(r"ticket\s*!=\s*m_trackedPrimary", CODE) is not None)

# Ordering guard: the adopted test must be nested INSIDE the mismatch branch. If
# it were hoisted to the top level, a same-magic leg would be made to depend on
# the adopted flag too, and the semantics would silently change.
check("the exception is nested inside the magic-mismatch branch",
      re.search(r"if\s*\(\s*magic\s*!=\s*\(long\)m_magic\s*\)\s*\{"
                r"[^}]*m_trackedAdopted[^}]*m_trackedPrimary[^}]*\}", CODE, re.S) is not None)

# Neither member may be a fresh local -- the bypass reads the PUSHED state.
check("m_trackedAdopted is a pushed member, not a local",
      re.search(r"\bbool\s+m_trackedAdopted\s*;", AUD_T) is not None)
check("m_trackedPrimary is a pushed member, not a local",
      re.search(r"\bulong\s+m_trackedPrimary\s*;", AUD_T) is not None)
check("SetTrackedLegs stores the adopted flag",
      re.search(r"m_trackedAdopted\s*=\s*adoptedManual\s*;", AUD_T) is not None)
check("SetTrackedLegs still accepts the flag as a parameter",
      re.search(r"SetTrackedLegs\(int\s+legs,\s*ulong\s+primaryTicket,\s*bool\s+active,"
                r"\s*bool\s+adoptedManual\s*=\s*false\s*\)", AUD_T) is not None)

# ----------------------------------------------------------------------
# 3. THE PUSH (otto.mq5) - without this the bypass never fires on a live leg
# ----------------------------------------------------------------------
check("otto.mq5 reads adoptedManual from the order manager",
      re.search(r"adopted\s*=\s*active\.adoptedManual", MAIN_T) is not None)
check("otto.mq5 passes the adopted flag into SetTrackedLegs",
      re.search(r"SetTrackedLegs\([^;]*adopted\s*\)", MAIN_T, re.S) is not None)

# ----------------------------------------------------------------------
# 4. THRESHOLD AND DISPATCH UNCHANGED
# ----------------------------------------------------------------------
check("the 35% tolerance is unchanged",
      re.search(r"maxAllowedRiskPct\s*=\s*RiskPercent\s*\*\s*1\.35", CODE) is not None)

check("the breach still dispatches as WARN",
      "HT_SEV_WARN" in CODE
      and "DispatchAlertOnce(m_alertSent_RiskBreach" in CODE)

check("the alert still names the offending ticket",
      "IntegerToString((long)peakTicket)" in CODE)

check("the peak is still the WORST leg, not the last one seen",
      re.search(r"if\s*\(\s*posRiskPct\s*>\s*peakRiskPct\s*\)", CODE) is not None)

check("the winner's risk is recorded from the local ticket",
      re.search(r"peakTicket\s*=\s*ticket\s*;", CODE) is not None)

check("the ticket read still ends in `continue` only if invalid",
      re.search(r"if\s*\(\s*PositionGetTicket\(idx\)\s*<=\s*0\s*\)\s*continue\s*;", CODE) is not None)

check("a clean cycle still re-arms the latch",
      re.search(r"ClearLatch\(m_alertSent_RiskBreach\)", CODE) is not None)

# ----------------------------------------------------------------------
# 5. RELEASE STAMP
# ----------------------------------------------------------------------
stamps = []
for f in ALL_FILES:
    m = re.search(r'#property version\s+"(\d+\.\d+)"', read(os.path.join(ROOT, f)))
    if m:
        stamps.append(m.group(1))
check("all %d shipped sources carry a version stamp" % len(ALL_FILES),
      len(stamps) == len(ALL_FILES), "found %d" % len(stamps))
check("every shipped source is stamped 5.46",
      bool(stamps) and set(stamps) == {"5.46"}, "stamps=%s" % sorted(set(stamps)))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.46 RISK-AUDIT LOSS-SIDE ISOLATION + ADOPTED LEGS - STATIC PROBE")
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

check("the distance in points is built from lossDist, not from entry-sl directly",
      re.search(r"distPts\s*=\s*lossDist\s*/\s*tickSize", CODE) is not None)

# The money conversion itself must be untouched by this release.
check("the money-at-risk conversion is unchanged",
      re.search(r"moneyAtRisk\s*=\s*distPts\s*\*\s*tickValue\s*\*\s*vol\s*;", CODE) is not None)
