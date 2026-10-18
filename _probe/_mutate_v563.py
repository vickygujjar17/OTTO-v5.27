"""
Mutation test for the v5.63 probe.

A probe that always passes is worthless, so every v5.63 assertion is exercised
against a deliberate, targeted mutation of the shipped sources. Every mutation
MUST make `test_v563_trail_health.py` fail: if it does not, the check is
decorative and would not notice the regression it claims to guard.

The mutations are written as the plausible edits a future maintainer might make
while "simplifying" the release, not as arbitrary corruption:

  * putting the trail back on +2.0R (the v5.62 value)
  * swapping the SyncTradeState branch order so +1.0R reports breakeven again
  * deriving the excursion from the STOP instead of the price (the circular
    form - the single most important assertion in the probe)
  * dropping the peak memory so the arm test sees only the current tick
  * forgetting to reset the peak when the tracked ticket changes
  * accepting a zero 1R instead of clearing the geometry
  * losing a healthy-path re-arm on either new latch
  * dropping the two-cycle confirmation on the aggregate leg
  * widening the per-leg sum to include profit-side legs
  * hardcoding the stacked budget instead of summing the live inputs
  * dropping the geometry push, or gating it on the wrong branch

Anchors are written with plain \\n and applied CRLF-tolerantly, so this table
does not silently rot if the working tree is normalised to a different line
ending. A missing anchor is reported as SKIPPED and FAILS the run, because a
stale anchor table is itself a silent-gap risk.

The originals are restored in a finally block, so a crash mid-run cannot leave
the working tree mutated.
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "_probe", "test_v563_trail_health.py")

AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")


def read(p):
    # Normalise CRLF to LF so anchors can be written with plain \n, while the
    # on-disk CRLF is preserved by write() below.
    return (io.open(p, encoding="utf-8", errors="replace", newline="")
            .read().replace("\r\n", "\n"))


def write(p, s):
    # Restore CRLF: the .mq5/.mqh sources are CRLF on disk and
    # _tools\normalize_eol.py enforces that.
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("\n", "\r\n"))


def run_probe():
    r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# (label, path, old, new) - each old must occur exactly once.
MUTATIONS = [
    # ---- A. the input value -------------------------------------------
    ("the trail is put back on +2.0R (the v5.62 value)", DEFS,
     "input double   InpTrailStartRR = 1.0;",
     "input double   InpTrailStartRR = 2.0;"),

    # ---- A. the state-machine order -----------------------------------
    # Swapping the two branches restores the STEP_BREAKEVEN label at +1.0R,
    # which is exactly the flip v5.63 accepted. The probe pins the ORDER.
    ("STEP_BREAKEVEN is tested before STEP_TRAILING, un-flipping +1.0R",
     TM,
     "      else if(rr >= InpTrailStartRR)                          step = STEP_TRAILING;\n"
     "      else if(rr >= InpBreakEvenRR)                           step = STEP_BREAKEVEN;",
     "      else if(rr >= InpBreakEvenRR)                           step = STEP_BREAKEVEN;\n"
     "      else if(rr >= InpTrailStartRR)                          step = STEP_TRAILING;"),

    # ---- B. the anti-circularity guarantee ----------------------------
    # THE most important mutation: derive the excursion from the stop. This is
    # the natural "simplification" and it silently disables the whole check.
    ("the excursion is derived from the STOP instead of the price (circular)",
     AUD,
     "      double rr = (isLong ? (price - m_trackedEntry)\n"
     "                          : (m_trackedEntry - price)) / m_trackedRRUnit;\n"
     "      NoteExcursion(rr);",
     "      double rr = (isLong ? (m_trackedSL - m_trackedEntry)\n"
     "                          : (m_trackedEntry - m_trackedSL)) / m_trackedRRUnit;\n"
     "      NoteExcursion(rr);"),

    ("the price side is flipped (ASK for a long)", AUD,
     "      double price = isLong ? SymbolInfoDouble(m_symbol, SYMBOL_BID)\n"
     "                            : SymbolInfoDouble(m_symbol, SYMBOL_ASK);",
     "      double price = SymbolInfoDouble(m_symbol, SYMBOL_ASK);"),

    ("the peak memory is never fed, so the arm test sees one tick only", AUD,
     "      NoteExcursion(rr);",
     "      ;"),

    ("the arm test is inverted, so a healthy trade alerts", AUD,
     "      if(m_trailPeakRR < InpTrailStartRR)",
     "      if(m_trailPeakRR > InpTrailStartRR)"),

    # ---- B. the invariant ---------------------------------------------
    ("the live book stop is replaced by the pushed (possibly stale) value", AUD,
     "      double sl = BookStopForTicket(m_trackedPrimary);\n"
     "      if(sl <= 0.0) sl = m_trackedSL;",
     "      double sl = m_trackedSL;"),

    ("the trail alert message line is dropped from the no-stop branch", AUD,
     '                           " has none recorded either. Entry " +\n',
     ''),

    ("the stale-trail latch never re-arms on the not-yet-armed path", AUD,
     "      if(m_trailPeakRR < InpTrailStartRR)\n"
     "        {\n"
     "         ClearLatch(m_alertSent_StaleTrail);\n"
     "         return;\n"
     "        }",
     "      if(m_trailPeakRR < InpTrailStartRR)\n"
     "        {\n"
     "         return;\n"
     "        }"),

    ("DIR_NONE is guessed as a long instead of standing down", AUD,
     "      if(!isLong && m_trackedDir != DIR_SHORT)\n"
     "        {\n"
     "         // DIR_NONE: the order layer has a trade but no resolved direction.\n"
     "         // Treat as unjudgeable rather than guessing a side.\n"
     "         ClearLatch(m_alertSent_StaleTrail);\n"
     "         return;\n"
     "        }",
     "      ;"),

    # ---- B2. the ingress ----------------------------------------------
    ("a zero 1R is accepted instead of clearing the geometry", AUD,
     "      if(ticket == 0 || rrUnit <= 0.0 || entry <= 0.0)",
     "      if(ticket == 0)"),

    ("the peak is not reset when the tracked ticket changes", AUD,
     "      if(ticket != m_trailPeakTicket)\n"
     "        {\n"
     "         m_trailPeakTicket = ticket;\n"
     "         m_trailPeakRR     = 0.0;\n"
     "        }",
     "      m_trailPeakTicket = ticket;"),

    ("the geometry push stores the 1R but forgets to mark it available", AUD,
     "      m_haveGeometry  = true;",
     "      ;"),

    ("NoteExcursion can lower the peak", AUD,
     "      if(rr > m_trailPeakRR) m_trailPeakRR = rr;",
     "      m_trailPeakRR = rr;"),

    ("the stale-trail latch is declared outside the include guard", AUD,
     "#endif  // __OTTO_HIGH_TABLE_AUDITOR__",
     "bool m_alertSent_SmuggledV563;\n#endif  // __OTTO_HIGH_TABLE_AUDITOR__"),

    ("AuditTrailingHealth is dropped from RunAudit", AUD,
     "      AuditTrailingHealth();    // v5.63 - is the armed trail actually tightening?\n",
     ""),

    ("the stale-trail latch is never initialised in the constructor", AUD,
     "      m_alertSent_StaleTrail         = false;\n",
     ""),
]



MUTATIONS += [
    # ---- C. the aggregate leg -----------------------------------------
    ("the profit-side guard is dropped, so the sum counts profit-side legs", AUD,
     "         double lossDist = isLong ? (entry - sl) : (sl - entry);\n"
     "         if(lossDist <= 0.0) continue;\n"
     "\n"
     "         double distPts     = lossDist / tickSize;\n"
     "         double moneyAtRisk = distPts * tickValue * vol;\n"
     "         double posRiskPct  = (moneyAtRisk / equity) * 100.0;\n"
     "\n"
     "         if(posRiskPct > peakRiskPct)",
     "         double lossDist = isLong ? (entry - sl) : (sl - entry);\n"
     "\n"
     "         double distPts     = lossDist / tickSize;\n"
     "         double moneyAtRisk = distPts * tickValue * vol;\n"
     "         double posRiskPct  = (moneyAtRisk / equity) * 100.0;\n"
     "\n"
     "         if(posRiskPct > peakRiskPct)"),

    ("the sum is never accumulated, so the aggregate leg is dead", AUD,
     "         sumRiskPct += posRiskPct;",
     "         ;"),

    ("the stacked budget is hardcoded instead of summing the live inputs", AUD,
     "      double nominalStackPct    = RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct;",
     "      double nominalStackPct    = 2.75;"),

    ("the aggregate allowance loses the 50% tolerance", AUD,
     "      double maxAllowedStackPct = nominalStackPct * 1.50;",
     "      double maxAllowedStackPct = nominalStackPct;"),

    ("the aggregate two-cycle confirmation is dropped", AUD,
     "         if(m_aggRiskSeen)\n"
     "           {\n"
     "            DispatchAlertOnce(m_alertSent_AggRiskBreach,",
     "         if(true)\n"
     "           {\n"
     "            DispatchAlertOnce(m_alertSent_AggRiskBreach,"),

    ("the aggregate latch never re-arms on the healthy path", AUD,
     "      else\n"
     "        {\n"
     "         ClearLatch(m_alertSent_AggRiskBreach);\n"
     "         m_aggRiskSeen = false;\n"
     "        }",
     "      else\n"
     "        {\n"
     "         m_aggRiskSeen = false;\n"
     "        }"),

    ("the aggregate alert is routed through the per-leg latch", AUD,
     "            DispatchAlertOnce(m_alertSent_AggRiskBreach,",
     "            DispatchAlertOnce(m_alertSent_RiskBreach,"),

    # ---- C2. the push -------------------------------------------------
    ("the geometry is never pushed from otto.mq5", MAIN,
     "      g_highTable.SetTrackedGeometry(active.ticket, active.direction,\n"
     "                                     active.entryPrice,\n"
     "                                     g_orderManager.GetBasketRRUnit(),\n"
     "                                     active.currentTrailSL);\n",
     ""),

    ("the pushed 1R is re-derived from the broker stop instead of the store",
     MAIN,
     "                                     g_orderManager.GetBasketRRUnit(),",
     "                                     MathAbs(active.entryPrice - active.currentTrailSL),"),

    ("the geometry push is moved out of the active-trade branch", MAIN,
     "      g_highTable.SetTrackedGeometry(active.ticket, active.direction,",
     "      }\n   g_highTable.SetTrackedGeometry(active.ticket, active.direction,"),
]


def main():
    rc, out = run_probe()
    if rc != 0:
        print("BASELINE FAILED - fix the probe before mutation testing")
        print(out)
        return 1

    last = [l for l in out.strip().split("\n") if "checks passed" in l]
    print("baseline: PASS (%s)" % (last[-1].strip() if last else "?"))

    backups = {}
    skipped = []
    failures = []
    try:
        for path in (AUD, MAIN, DEFS, TM):
            backups[path] = read(path)

        for label, path, old, new in MUTATIONS:
            original = backups[path]
            if original.count(old) != 1:
                skipped.append((label, original.count(old)))
                continue

            write(path, original.replace(old, new, 1))
            try:
                prc, pout = run_probe()
            finally:
                write(path, original)

            if prc == 0:
                failures.append(label)

            status = "caught" if prc != 0 else "MISSED"
            print("  [%s] %s" % (status, label))

        print("=" * 74)
        print("%d/%d mutations caught  (%d anchor(s) skipped)"
              % (len(MUTATIONS) - len(failures) - len(skipped),
                 len(MUTATIONS), len(skipped)))
        for label, n in skipped:
            print("  SKIPPED (anchor count %d): %s" % (n, label))
        for label in failures:
            print("  NOT CAUGHT: %s" % label)
    finally:
        for path, original in backups.items():
            write(path, original)

    if skipped or failures:
        print("*** MUTATION GAP ***")
        return 1
    print("*** EVERY MUTATION CAUGHT ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())

