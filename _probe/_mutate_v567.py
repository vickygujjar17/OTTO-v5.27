"""
Mutation test for the v5.67 probe.

A probe that always passes is worthless, so every v5.67 assertion is exercised
against a deliberate, targeted mutation of the shipped sources. Every mutation
MUST make `test_v567_unified_sl.py` fail: if it does not, the check is
decorative and would not notice the regression it claims to guard.

The mutations are written as the plausible edits a future maintainer might make
while "simplifying" the release, not as arbitrary corruption:

  * deleting the repair entirely (the exact v5.66 regression)
  * staging the debt from a level NO ticket accepted
  * staging on a fully-applied write (debt that nothing owes)
  * staging an ever-looser level, so the repair walks the stop BACKWARDS
  * dropping the retry credit, turning a refusal into a per-tick storm
  * dropping the abandon warning, losing the only signal of a stuck leg
  * reading the order layer's belief instead of the broker's stop
  * repairing a ticket that is already at the level (pointless writes)
  * driving the repair before the ratchet, or outside the per-tick guard
  * leaking the debt across baskets
  * restoring the RiskPercent-anchored per-leg allowance
  * restoring the unreachable four-rung aggregate allowance
  * measuring the leg budget from the raw comment instead of its _T tag
  * dropping the orphan-leg sweep, un-scoping it from this EA's magic, or
    downgrading a naked leg from CRITICAL to WARN


Anchors are written with plain \n and applied CRLF-tolerantly, so this table
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
PROBE = os.path.join(ROOT, "_probe", "test_v567_unified_sl.py")

OM = os.path.join(ROOT, "COttoOrderManager.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")


def read(p):
    return (io.open(p, encoding="utf-8", errors="replace", newline="")
            .read().replace("\r\n", "\n"))


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("\n", "\r\n"))


def run_probe():
    r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# (label, path, old, new) - each old must occur exactly once.
MUTATIONS = [
    # ---- A. the repair itself -----------------------------------------
    ("the debt is staged but the repair never runs (the v5.66 regression)", OM,
     "      if(!m_unifiedRepairPending) return 0;",
     "      if(true) return 0;   // MUTANT: debt staged, never repaired"),

    ("the repair is driven by a level NO ticket accepted", OM,
     "         if(!allApplied) StageUnifiedRepair(newSL);",
     "         if(!allApplied) StageUnifiedRepair(desiredSL);"),

    ("a fully-applied write still stages phantom debt", OM,
     "         if(!allApplied) StageUnifiedRepair(newSL);",
     "         StageUnifiedRepair(newSL);"),

    ("staging accepts a LOOSER level, so the repair walks the stop backwards", OM,
     "         if(isLong ? (level <= m_unifiedRepairSL) : (level >= m_unifiedRepairSL))",
     "         if(isLong ? (level < 0.0) : (level < 0.0))"),

    ("the retry credit is dropped, so a refusal becomes a per-tick storm", OM,
     "      m_unifiedRepairAttempts++;",
     "      ;   // MUTANT: no credit"),

    ("the abandon warning is dropped, hiding a permanently stuck leg", OM,
     '         Print("[OrderManager] UNIFIED SL REPAIR ABANDONED at ",\n',
     '         ;\n'),

    ("the repair reads the order layer's belief instead of the broker's stop", OM,
     "         double liveSL = PositionGetDouble(POSITION_SL);",
     "         double liveSL = m_sessionSL;"),

    ("a ticket already at the level is written again (pointless broker traffic)", OM,
     "            if(isLong ? (liveSL >= m_unifiedRepairSL) : (liveSL <= m_unifiedRepairSL))\n"
     "               continue;   // already at or beyond the floor",
     "            if(false) continue;"),

    ("a closed ticket is treated as outstanding debt", OM,
     "         if(!PositionSelectByTicket(m_basket[i].ticket)) continue;   // closed: not debt",
     "         if(false) continue;"),

    ("a successful repair does not clear the debt", OM,
     "         ClearUnifiedRepair();\n         return repaired;\n        }\n\n      // Some legs still refused.",
     "         return repaired;   // MUTANT: debt never cleared\n        }\n\n      // Some legs still refused."),

    # ---- B. wiring and lifecycle ---------------------------------------
    ("the repair is never driven from the tick path", TM,
     "         m_orderManager.RepairUnifiedSL();\n", ""),

    ("the repair is driven BEFORE the ratchet", TM,
     "         m_orderManager.ApplyUnifiedSL(desiredSL);",
     "         m_orderManager.RepairUnifiedSL();\n"
     "         m_orderManager.ApplyUnifiedSL(desiredSL);"),

    ("the debt leaks across baskets", OM,
     "      ClearUnifiedRepair();\n      // v5.33: the adoption flag must die",
     "      // MUTANT: debt survives the basket\n      // v5.33: the adoption flag must die"),

    # ---- C. the per-leg budget -----------------------------------------
    ("the per-leg allowance goes back to RiskPercent for EVERY leg", AUD,
     "         double legBudgetPct = RungRiskPct(PositionGetString(POSITION_COMMENT));",
     "         double legBudgetPct = RiskPercent;"),

    ("the per-leg breach goes back to comparing raw peaks", AUD,
     "         if(legRatio > peakRatio)",
     "         if(posRiskPct > peakRiskPct)"),

    ("the rung attribution ignores the _T tag", AUD,
     '      int tagAt = StringFind(comment, "_T", len - 2);',
     "      int tagAt = -1;"),

    ("the rung attribution reads a frozen 1.0 instead of the live rung input", AUD,
     "         case 2:  return InpRiskT2Pct;",
     "         case 2:  return 1.0;"),

    ("an unparseable tag is promoted to its raw value instead of tranche 1", AUD,
     "      return (tag >= 2 && tag <= 4) ? tag : 1;",
     "      return tag;"),

    ("a rung configured at 0 loses its oversize sentinel", AUD,
     "                                                   : ((posRiskPct > 0.0) ? 999.0 : 0.0);",
     "                                                   : 0.0;"),

    # ---- D. the aggregate allowance -------------------------------------
    ("the aggregate allowance goes back to the unreachable four-rung stack", AUD,
     "      double maxAllowedStackPct = budgetedStackPct * 1.50;",
     "      double maxAllowedStackPct = nominalStackPct * 1.50;"),

    ("the aggregate allowance stops tracking the legs at risk", AUD,
     "         budgetedStackPct += legBudgetPct;",
     "         ;"),

    ("the aggregate test loses its empty-budget guard", AUD,
     "      if(sumRiskPct > maxAllowedStackPct && budgetedStackPct > 0.0)",
     "      if(sumRiskPct > maxAllowedStackPct)"),

    ("the aggregate tolerance is widened past 50%", AUD,
     "      double maxAllowedStackPct = budgetedStackPct * 1.50;",
     "      double maxAllowedStackPct = budgetedStackPct * 3.00;"),

    # ---- E. the constants ----------------------------------------------
    ("the repair constants leave file scope (MQL5 rejects the class form)", DEFS,
     "const int      OTTO_UNIFIED_REPAIR_MAX_ATTEMPTS = 6;   // attempts per staged level",
     "// MUTANT: constant removed from file scope"),

    # ---- F. the orphan-leg sweep ---------------------------------------
    ("the sweep is dropped from the audit cadence", AUD,
     "      AuditOrphanLegs();        // v5.67 - is any leg running with NO stop at all?",
     "      // MUTANT: orphan sweep never runs"),

    ("the sweep stops counting stopless legs (it counts protected ones)", AUD,
     "         if(PositionGetDouble(POSITION_SL) > 0.0) continue;   // protected",
     "         if(PositionGetDouble(POSITION_SL) <= 0.0) continue;   // MUTANT: inverted"),

    ("the sweep is no longer magic-scoped, so it reports any leg on the chart", AUD,
     "         // TICKET, so an unrelated manual leg is still excluded.\n"
     "         if(magic != (long)m_magic)",
     "         // TICKET, so an unrelated manual leg is still excluded.\n"
     "         if(false)   // MUTANT: every leg on the symbol counts"),

    ("the sweep drops its grace period and cries wolf on every entry", AUD,
     "         if(opened > 0 && TimeCurrent() - opened < HT_ORPHAN_GRACE_SECONDS) continue;\n"
     "\n"
     "         n++;",
     "         ;   // MUTANT: no grace period\n"
     "\n"
     "         n++;"),

    ("the orphan latch is never re-armed, so the second incident is silent", AUD,
     "         ClearLatch(m_alertSent_OrphanLeg);\n"
     "         return;",
     "         return;   // MUTANT: latch stays raised"),

    ("an unprotected leg is downgraded from CRITICAL to WARN", AUD,
     "                        \" refused log lines) or a restart mid-repair.\",\n"
     "                        HT_SEV_CRITICAL);",
     "                        \" refused log lines) or a restart mid-repair.\",\n"
     "                        HT_SEV_WARN);"),
]


def main():
    print("=" * 74)
    print("v5.67 MUTATION TEST - every mutation MUST make the probe fail")
    print("=" * 74)
    print("probe: %s" % PROBE)
    print("%d mutation(s)\n" % len(MUTATIONS))

    # The probe must be green BEFORE any mutation, or the run proves nothing.
    code, out = run_probe()
    if code != 0:
        print("ABORT: the probe does not pass on the unmutated tree.")
        print(out[-2000:])
        return 2

    saved = {}
    results = []
    try:
        for label, path, old, new in MUTATIONS:
            if path not in saved:
                saved[path] = read(path)
            src = read(path)
            count = src.count(old)
            if count != 1:
                results.append((label, "SKIPPED", "anchor found %d times" % count))
                print("  [SKIPPED] %s  <- anchor found %d times" % (label, count))
                continue
            write(path, src.replace(old, new))
            try:
                code, out = run_probe()
            finally:
                write(path, saved[path])
            if code == 0:
                results.append((label, "SURVIVED", ""))
                print("  [SURVIVED] %s  <- the probe did NOT notice" % label)
            else:
                failed = len([l for l in out.split("\n") if "[FAIL]" in l])
                results.append((label, "KILLED", "%d check(s) failed" % failed))
                print("  [KILLED]   %s  (%d check(s) failed)" % (label, failed))
    finally:
        for path, original in saved.items():
            write(path, original)

    killed = sum(1 for _, r, _ in results if r == "KILLED")
    total = len(results)
    skipped = sum(1 for _, r, _ in results if r == "SKIPPED")
    print()
    print("-" * 74)
    print("  %d / %d mutations killed, %d skipped" % (killed, total, skipped))
    print("=" * 74)
    if skipped or killed != total:
        print("*** FAILURES PRESENT ***")
        return 1
    print("*** ALL MUTATIONS KILLED ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
