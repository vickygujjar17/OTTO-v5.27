"""Mutation test: confirm test_v531_smart_trim.py actually FAILS on regressions.

A probe that cannot fail is worthless. This copies the OTTO tree to a temp
dir, applies one targeted regression at a time, runs the v5.31 probe against
the mutated copy, and asserts the probe goes red. Any mutation that still
passes means the corresponding check is decorative.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REAL = r"C:\Users\vivek\Downloads\OTTO-v5.00 v5.27"
PROBE = os.path.join("_probe", "test_v531_smart_trim.py")
FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "OttoDefines.mqh"]

MUTATIONS = [
    ("ascending trim loop (index-shift bug)", "COttoTradeManager.mqh",
     "for(int idx = PositionsTotal() - 1; idx >= 0; idx--)",
     "for(int idx = 0; idx < PositionsTotal(); idx++)"),
    ("drop the symbol scope", "COttoTradeManager.mqh",
     "if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;",
     "// symbol filter removed"),
    ("drop the magic scope", "COttoTradeManager.mqh",
     "if(PositionGetInteger(POSITION_MAGIC) != magic) continue;",
     "// magic filter removed"),
    ("drop primary exclusion", "COttoTradeManager.mqh",
     "if(ticket == 0 || ticket == primary) continue;",
     "if(ticket == 0) continue;"),
    ("read the primary inside the loop (post-clear)", "COttoTradeManager.mqh",
     "ulong primary = m_orderManager.GetActiveTrade().ticket;",
     "ulong primary = 0;"),
    ("trim instead of skip a stopped-out-at-entry leg", "COttoTradeManager.mqh",
     "if(total <= 0.0) continue;",
     "if(total <= 0.0) total = 1.0;"),
    ("trim legs with no stop", "COttoTradeManager.mqh",
     "if(sl <= 0.0) continue;",
     "sl = entry;"),
    ("latch raised even when nothing closed", "COttoTradeManager.mqh",
     "return true;   // nothing relieved -> caller does the full close",
     "m_trimLogged = true; return true;"),
    ("escalation inverted (no-op trim)", "COttoTradeManager.mqh",
     "return true;   // nothing relieved -> caller does the full close",
     "return false;"),
    ("latch never raised (re-trim every tick)", "COttoTradeManager.mqh",
     "m_trimLogged = true;",
     "m_trimLogged = false;"),
    ("ClearTrimLatch becomes a no-op", "COttoTradeManager.mqh",
     "void            ClearTrimLatch(void) { m_trimLogged = false; }",
     "void            ClearTrimLatch(void) { }"),
    ("full close unconditional again (trim ignored)", "otto.mq5",
     "if(needFullClose)\r\n               g_orderManager.CloseEntireBasket(",
     "if(true)\r\n               g_orderManager.CloseEntireBasket("),
    ("cap raised back to 1.0", "OttoDefines.mqh",
     "SafetyMaxFloatingLoss = 0.90;", "SafetyMaxFloatingLoss = 1.0;"),
    ("trim threshold disabled", "OttoDefines.mqh",
     "InpTrimLoserStopPct = 70.0;", "InpTrimLoserStopPct = 0.0;"),
    ("InpMaxRR reverted to the old default", "OttoDefines.mqh",
     "InpMaxRR             = 4.0;", "InpMaxRR             = 3.0;"),
    ("order-manager TP back to a hardcoded multiple", "COttoOrderManager.mqh",
     "tpRR * slDist", "3.0 * slDist"),
    ("block-manager front-run back to a hardcoded multiple",
     "COttoBlockManager.mqh", "tpRR * calcSLDist", "3.0 * calcSLDist"),
    ("latch cleared inside the breach branch", "otto.mq5",
     "return;\r\n        }\r\n\r\n      // v5.31: float is back under the cap",
     "g_tradeManager.ClearTrimLatch();\r\n         return;\r\n        }\r\n\r\n"
     "      // v5.31: float is back under the cap"),
    ("version stamp left behind", "OttoDefines.mqh",
     '#property version   "5.31"', '#property version   "5.30"'),
    ("startup banner left behind", "otto.mq5",
     "OTTO EA v5.31 \u2014 28-Pair", "OTTO EA v5.30 \u2014 28-Pair"),
]


def run_probe(root):
    r = subprocess.run([sys.executable, PROBE], cwd=root,
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def main():
    print("=" * 74)
    print("v5.31 PROBE MUTATION TEST")
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
            if old not in text:
                print("  [SKIP] %s  <- anchor not found in %s" % (name, fname))
                skipped.append(name)
                continue
            open(p, "w", encoding="utf-8", newline="").write(
                text.replace(old, new, 1))

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
