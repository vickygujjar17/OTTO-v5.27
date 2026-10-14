"""Mutation test: confirm test_v532_pyramid.py actually FAILS on regressions.

A probe that cannot fail is worthless. This copies the OTTO tree to a temp
dir, applies one targeted regression at a time, runs the v5.32 probe against
the mutated copy, and asserts the probe goes red. Any mutation that still
passes means the corresponding check is decorative.

The four v5.32 defects are all of the same kind -- code that compiles cleanly
while misbehaving -- so every mutation below is written as the plausible edit
a future maintainer might make while "simplifying" the fix:

  * replacing the resolved liveness bool with a bare input comparison
  * dropping the gate from one direction (LONG gated, SHORT not)
  * moving the counter back onto the trigger instead of the application
  * un-hoisting the rung so the guard sees an intermediate stop
  * collapsing the refusal exits back to bare `return false;`
  * merging the two cursor policies so the margin branch advances too
  * removing the ticket check from the restore
  * re-deriving 1R from the broker stop instead of reading the store

Anchors are written with plain \\n and applied CRLF-tolerantly, so this table
does not silently rot if the working tree is normalised to a different line
ending. A missed anchor is reported as SKIP and fails the run, because a stale
anchor table is itself a silent-gap risk.
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = ROOT
PROBE = os.path.join("_probe", "test_v532_pyramid.py")
FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "OttoDefines.mqh"]

MUTATIONS = [
    # --- 1. half-risk rung liveness ------------------------------------
    ("liveness default flipped to optimistic in the constructor",
     "COttoTradeManager.mqh",
     "m_cutRiskRungLive=false;",
     "m_cutRiskRungLive=true;  // MUTANT: rung assumed live before Initialize()"),

    ("Initialize stops resolving liveness from the inputs",
     "COttoTradeManager.mqh",
     "bool cutBeforeBE = (InpCutRiskRR > 0.0 && InpCutRiskRR < InpBreakEvenRR);",
     "bool cutBeforeBE = true;  // MUTANT: rung always treated as reachable"),

    ("resolved bool never assigned (rung liveness recomputed wrongly)",
     "COttoTradeManager.mqh",
     "m_cutRiskRungLive = cutBeforeBE;",
     "m_cutRiskRungLive = (InpCutRiskRR >= InpBreakEvenRR);  // MUTANT"),

    ("inert-rung announcement removed (silence again)",
     "COttoTradeManager.mqh",
     '"[TradeManager] INERT RUNG: half-risk rung disabled because ",',
     '"[TradeManager] rung note: ",'),

    # --- 2. the gate must reach BOTH directions AND the counter --------
    ("gate dropped from the SHORT branch only",
     "COttoTradeManager.mqh",
     "if(m_cutRiskRungLive && currentRR >= InpCutRiskRR && desiredSL > halfRiskSL)",
     "if(currentRR >= InpCutRiskRR && desiredSL > halfRiskSL)"),

    ("counter moved off the gated line (trigger counted, not application)",
     "COttoTradeManager.mqh",
     "{ desiredSL = halfRiskSL; m_halfRiskTriggers++; }",
     "{ desiredSL = halfRiskSL; } m_halfRiskTriggers++;"),

    # --- 3. hoist ordering: the guard must see the FINAL stop ----------
    ("half-risk rung un-hoisted ahead of the ATR trail",
     "COttoTradeManager.mqh",
     "{ desiredSL = halfRiskSL; m_halfRiskTriggers++; }",
     "{ desiredSL = halfRiskSL; m_halfRiskTriggers++;"
     " if(currentRR >= InpTrailStartRR)"
     " { double dynamicTrail = high0 - (InpTrailATRMultiplier * atr);"
     " if(dynamicTrail > desiredSL) desiredSL = dynamicTrail; } }"),

    # --- 4. the T2 crossing trace -------------------------------------
    ("bucket latch removed (trace spams every tick)",
     "COttoTradeManager.mqh",
     "if(bucket != m_t2TraceBucket)",
     "if(true)"),

    ("latch never re-arms when the ladder leaves rung 2",
     "COttoTradeManager.mqh",
     "if(m_orderManager.GetNextTranche() != 2)",
     "if(false)"),

    ("constructor no longer seeds the bucket latch",
     "COttoTradeManager.mqh",
     "m_t2TraceBucket=-1;",
     "m_t2TraceBucket=0;  // MUTANT: first crossing suppressed"),

    ("pending2 (the gate's own verdict) dropped from the trace",
     "COttoTradeManager.mqh",
     '" pending2=", (m_orderManager.IsPyramidPending(2) ? "true" : "false"),',
     '"",'),

    ("T2 crossing trace renamed away (operator search string lost)",
     "COttoTradeManager.mqh",
     '"[Pyramid] T2 crossing: currentRR=",',
     '"[Pyramid] T2 approach: currentRR=",'),

    ("trace made stateful (mutates the ladder)",
     "COttoTradeManager.mqh",
     "               m_t2TraceBucket = bucket;",
     "               m_t2TraceBucket = bucket;\n"
     "               m_orderManager.InitBasket();  // MUTANT: side effect"),

    # --- 5. refusal attribution ---------------------------------------
    ("disabled-feature refusal goes silent again",
     "COttoOrderManager.mqh",
     '!InpPyramidEnable ? "InpPyramidEnable=false" : "basket empty (m_basketCount=0)",',
     '!InpPyramidEnable ? "" : "",'),

    ("out-of-order refusal goes silent again",
     "COttoOrderManager.mqh",
     '" REFUSED: out of order | ",',
     '" note | ",'),

    ("unknown-rung refusal goes silent again",
     "COttoOrderManager.mqh",
     '" REFUSED: unknown rung (ladder is 2/3/4) | err=", GetLastError());',
     '" note | err=", GetLastError());'),

    ("no-usable-R refusal loses its candidates",
     "COttoOrderManager.mqh",
     '" REFUSED: no usable R unit | ",',
     '" note | ",'),

    ("broker rejection no longer names the retcode",
     "COttoOrderManager.mqh",
     '" retcode=", res.retcode, " (", res.comment, ")",',
     '"",'),

    ("R-unit fallback to the active trade removed",
     "COttoOrderManager.mqh",
     "      if(slDist <= 0.0 && m_hasActiveTrade && m_activeTrade.rrUnit > 0.0)\n"
     "        {\n"
     '         slDist = m_activeTrade.rrUnit;\n'
     '         rSource = "activeTrade";\n'
     "        }",
     "      // MUTANT: activeTrade fallback removed"),

    ("R-unit fallback to the seeded SL distance removed",
     "COttoOrderManager.mqh",
     "         slDist = MathAbs(m_activeTrade.entryPrice - m_activeTrade.initialSL);\n"
     '         rSource = "initialSLDistance";',
     "         // MUTANT: initialSLDistance fallback removed"),

    ("recovered R unit logged silently",
     "COttoOrderManager.mqh",
     '" R unit RECOVERED from "',
     '" R unit taken from "'),

    ("recovered R unit log loses its gating (spams every tick)",
     "COttoOrderManager.mqh",
     '      if(rSource != "basket" && EnableLogging)\n'
     '         Print("[Pyramid] Tranche ", trancheToAdd, " R unit RECOVERED from ", rSource,',
     '      if(rSource != "basket")\n'
     '         Print("[Pyramid] Tranche ", trancheToAdd, " R unit RECOVERED from ", rSource,'),

    # --- 6. cursor asymmetry, both directions -------------------------
    ("sub-minimum lot no longer advances the cursor (spams forever)",
     "COttoOrderManager.mqh",
     "         m_nextTranche = (trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0);\n"
     "         return false;\n"
     "        }\n"
     "      if(!m_riskManager.HasSufficientMargin(lot))",
     "         return false;\n"
     "        }\n"
     "      if(!m_riskManager.HasSufficientMargin(lot))"),

    ("insufficient margin no longer states the cursor is retained",
     "COttoOrderManager.mqh",
     '                  DoubleToString(lot,2), " | nextTranche stays ", m_nextTranche,',
     '                  DoubleToString(lot,2), " | nextTranche=", m_nextTranche,'),

    ("ladder advance reverted to the pre-v5.29 two-rung form",
     "COttoOrderManager.mqh",
     "m_nextTranche = (trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0);",
     "m_nextTranche = (trancheToAdd == 2) ? 3 : 0;"),

    # --- 7. cursor accessor ------------------------------------------
    ("GetNextTranche() loses const (writable cursor)",
     "COttoOrderManager.mqh",
     "int               GetNextTranche(void) const { return m_nextTranche; }",
     "int               GetNextTranche(void) { return m_nextTranche; }"),

    # --- 8. original-1R persistence -----------------------------------
    ("GV key loses its magic scope (charts collide)",
     "COttoOrderManager.mqh",
     'return "OTTO_" + key + "_" + m_symbol + "_" + IntegerToString(MagicNumber);',
     'return "OTTO_" + key + "_" + m_symbol;'),

    ("restore loses the ticket check (stale R applied)",
     "COttoOrderManager.mqh",
     "      if(storedTicket != adoptedTicket)",
     "      if(false)"),

    ("stale record no longer reported",
     "COttoOrderManager.mqh",
     '" -> discarded (stale record)"',
     '" -> ignored"'),

    ("restore returns a guessed R when no record exists",
     "COttoOrderManager.mqh",
     "      string name = BasketGvName(\"BASKETR\");\n"
     "      if(!GlobalVariableCheck(name)) return 0.0;",
     "      string name = BasketGvName(\"BASKETR\");\n"
     "      if(!GlobalVariableCheck(name)) return 1.0;"),

    ("a zero ticket is persisted (orphaned record)",
     "COttoOrderManager.mqh",
     "      if(r <= 0.0 || primaryTicket == 0) return;",
     "      if(r <= 0.0) return;"),

    ("InitBasket stops persisting 1R",
     "COttoOrderManager.mqh",
     "      PersistBasketR(rrUnit, ticket);",
     "      // MUTANT: persistence removed"),

    ("ClearBasket stops clearing the record",
     "COttoOrderManager.mqh",
     "      ClearBasketR();",
     "      // MUTANT: persisted 1R not dropped with the basket"),

    ("stored ticket value never deleted (GV leak)",
     "COttoOrderManager.mqh",
     "GlobalVariableDel(tName);",
     "// MUTANT: ticket value never deleted"),

    ("seed path re-derives 1R from the ratcheted stop again",
     "COttoOrderManager.mqh",
     "      double restoredR = RestoreBasketR(positionTicket);",
     "      double restoredR = 0.0;  // MUTANT: store never consulted"),

    ("restored R no longer logged",
     "COttoOrderManager.mqh",
     '            Print("[OrderManager] Basket 1R RESTORED from persistent store: ",',
     '            Print("[OrderManager] Basket 1R restored: ",'),

    ("broker-distance fallback no longer warns about a stop at entry",
     "COttoOrderManager.mqh",
     '" | WARNING: stop sits AT ENTRY, so this 1R is friction-sized"',
     '" | NOTE: stop sits at entry"'),

    ("unresolvable R silently zeroed again",
     "COttoOrderManager.mqh",
     'Print("[OrderManager] WARNING: basket 1R UNRESOLVED for ticket ",',
     'Print("[OrderManager] basket 1R unresolved for ticket ",'),

    # --- 9. the unconditional OnInit warning --------------------------
    ("OnInit rung warning silenced when logging is off",
     "otto.mq5",
     "   if(InpCutRiskRR >= InpBreakEvenRR)\r\n"
     '      Print("[INIT] NOTE: InpCutRiskRR (",',
     "   if(InpCutRiskRR >= InpBreakEvenRR && EnableLogging)\r\n"
     '      Print("[INIT] NOTE: InpCutRiskRR (",'),

    ("OnInit rung warning softened (UNREACHABLE keyword lost)",
     "otto.mq5",
     "is UNREACHABLE and will never be",
     "is unreachable and will never be"),

    # --- 10. version stamps ------------------------------------------
    ("version stamp left behind",
     "OttoDefines.mqh",
     '#property version   "5.32"', '#property version   "5.31"'),

    ("startup banner left behind",
     "otto.mq5",
     "OTTO EA v5.32 \u2014 28-Pair", "OTTO EA v5.31 \u2014 28-Pair"),

    ("Pine port banner left behind",
     "otto.mq5",
     "Master Build Port (v5.32)", "Master Build Port (v5.31)"),
]


def run_probe(root):
    r = subprocess.run([sys.executable, PROBE], cwd=root,
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def apply_mutation(text, old, new):
    """Replace `old` with `new`, tolerating CRLF in the target file.

    Anchors are written with plain \\n. The shipped sources are CRLF, so a
    naive compare would miss every multi-line anchor and the whole table would
    report SKIP (looking like an anchor problem, not a coverage problem). The
    file is substituted back in whatever line ending it already used.
    """
    for nl in ("\r\n", "\n"):
        o = old.replace("\n", nl)
        if o in text:
            return text.replace(o, new.replace("\n", nl), 1), nl
    return None, None


def main():
    print("=" * 74)
    print("v5.32 PROBE MUTATION TEST")
    print("=" * 74)

    if not os.path.isdir(REAL):
        print("ABORT: tree not found: %s" % REAL)
        return 1

    rc, out = run_probe(REAL)
    if rc != 0:
        print("ABORT: baseline probe is already failing")
        print(out)
        return 1
    print("  baseline (unmutated tree): PASS\n")

    missed = []
    skipped = []
    for name, fname, old, new in MUTATIONS:
        tmp = tempfile.mkdtemp(prefix="otto_mut_")
        try:
            for f in FILES:
                shutil.copy2(os.path.join(REAL, f), os.path.join(tmp, f))
            shutil.copytree(os.path.join(REAL, "_probe"),
                            os.path.join(tmp, "_probe"))

            p = os.path.join(tmp, fname)
            text = open(p, encoding="utf-8", newline="").read()
            mutated, _nl = apply_mutation(text, old, new)
            if mutated is None:
                print("  [SKIP] %s  <- anchor not found in %s" % (name, fname))
                skipped.append(name)
                continue
            open(p, "w", encoding="utf-8", newline="").write(mutated)

            rc, out = run_probe(tmp)
            caught = rc != 0
            print("  [%s] %s" % ("CAUGHT" if caught else "MISSED", name))
            if caught:
                for x in [l.strip() for l in out.splitlines()
                          if "[FAIL]" in l][:2]:
                    print("           %s" % x)
            else:
                missed.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 74)
    total = len(MUTATIONS) - len(skipped)
    print("  %d/%d mutations caught  (%d anchor(s) skipped)"
          % (total - len(missed), total, len(skipped)))
    print("=" * 74)
    if missed:
        print("*** NOT CAUGHT (probe is decorative for these) ***")
        for m in missed:
            print("  - %s" % m)
        return 1
    if skipped:
        print("*** SKIPPED ANCHORS - fix the mutation table ***")
        for s in skipped:
            print("  - %s" % s)
        return 1
    print("*** EVERY MUTATION CAUGHT ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
