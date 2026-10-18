"""One-shot forensic census of the release stamps (throws away nothing)."""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
         "OttoDefines.mqh", "README.md"]

PATS = [r'#property\s+version\s+"(\d+\.\d+)"',
        r'Master Build Port \(v(\d+\.\d+)\)',
        r'OTTO EA v(\d+\.\d+)',
        r'#property description "OTTO v(\d+\.\d+)',
        r'\[\d+\] [A-Z0-9 &/-]+ . v(\d+\.\d+)',
        r'Current base: v(\d+\.\d+)']

for f in FILES:
    p = os.path.join(ROOT, f)
    if not os.path.exists(p):
        print("MISSING  %s" % f)
        continue
    txt = io.open(p, encoding="utf-8", errors="replace", newline="").read()
    hits = []
    for pat in PATS:
        for m in re.finditer(pat, txt):
            line = txt.count("\n", 0, m.start()) + 1
            hits.append("%s:%d=%s" % (pat[:12], line, m.group(1)))
    print("%-28s %s" % (f, " ".join(hits) if hits else "-- no stamp --"))
