"""
v5.26 static verification probe — Currency Vector & Affinity Correlation Engine.

Pins the behaviour that cannot be exercised by the MQL5 compiler gate:

  1. Affinity matrix matches the reference table and is structurally symmetric.
  2. USD carries NO affinity edges (independent counterweight baseline).
  3. Base/Quote seeding signs: long => Base +1 / Quote -1, inverse for short.
  4. First-order propagation only touches currencies NOT directly set.
  5. Anchor signs: DXY up => USD +1, DXY down => USD -1, gold up => USD -1.
  6. maxAbsMovement == 0 guard exists (flat portfolio never divides by zero).
  7. Suffix sanitization resolves broker variants to canonical roots.
  8. Veto threshold semantics: long opposes at <= -t, short opposes at >= +t.
  9. VETO_CORRELATION defined and wired into the journal reason map.
 10. v5.25 ladder + TS_Bias_ hive-mind path are untouched.

Pure static analysis of the shipped sources — no MT5 required.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORR = os.path.join(ROOT, "COttoCorrelationFilter.mqh")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


CORR_T = read(CORR)
OM_T = read(OM)
DEFS_T = read(DEFS)
MAIN_T = read(MAIN)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


# ----------------------------------------------------------------------
# 1. Affinity table fidelity + symmetry
# ----------------------------------------------------------------------
EXPECTED = {
    ("EUR", "GBP"): 0.75,
    ("EUR", "CHF"): 0.80,
    ("GBP", "CHF"): 0.60,
    ("CHF", "JPY"): 0.40,
    ("AUD", "NZD"): 0.85,
    ("AUD", "CAD"): 0.60,
    ("NZD", "CAD"): 0.55,
}

found = re.findall(
    r'Affinity\(\s*"([A-Z]{3})"\s*,\s*"([A-Z]{3})"\s*,\s*([0-9.]+)\s*\)', CORR_T)

check("affinity call count == 7", len(found) == 7, "found %d" % len(found))

tbl = {}
for a, b, w in found:
    tbl[frozenset((a, b))] = float(w)

for (a, b), w in EXPECTED.items():
    got = tbl.get(frozenset((a, b)))
    check("affinity %s/%s == %s" % (a, b, w), got == w, "got %s" % got)

# Symmetry is structural: Affinity() must write BOTH cells.
check("Affinity() writes the upper cell",
      re.search(r"m_affinity\[ia\]\[ib\]\s*=\s*w\s*;", CORR_T) is not None)
check("Affinity() writes the lower cell",
      re.search(r"m_affinity\[ib\]\[ia\]\s*=\s*w\s*;", CORR_T) is not None)
check("unknown codes ignored, not fatal", "if(ia < 0 || ib < 0) return;" in CORR_T)

# ----------------------------------------------------------------------
# 2. USD must stay empty (global liquid counterweight)
# ----------------------------------------------------------------------
usd_edges = [k for k in tbl if "USD" in k]
check("USD has no affinity edges", len(usd_edges) == 0, "edges: %s" % usd_edges)
check("USD baseline documented in code", "intentionally no entries" in CORR_T)

# ----------------------------------------------------------------------
# 3. Base/Quote seeding signs
# ----------------------------------------------------------------------
add = re.search(r"void\s+AddExposure\(.*?\r?\n[ ]{3,7}\}\r?\n", CORR_T, re.S)
check("AddExposure present", add is not None)
if add:
    body = add.group(0)
    check("Base seeded +1*dir", "m_currencyValues[ib] += 1.0 * dir;" in body)
    check("Quote seeded -1*dir", "m_currencyValues[iq] += -1.0 * dir;" in body)
    check("Base/Quote both marked directly set", body.count("m_directlySet[") == 2)
    check("non-6-char roots rejected", "StringLen(clean) != 6" in body)

# ----------------------------------------------------------------------
# 4. Propagation runs only for NOT-directly-set currencies
# ----------------------------------------------------------------------
prop = re.search(r"// 3\. First-order affinity propagation.*?\r?\n[ ]{3,7}\}\r?\n", CORR_T, re.S)
check("propagation loop present", prop is not None)
if prop:
    pb = prop.group(0)
    check("skips directly-set currencies", "if(m_directlySet[c]) continue;" in pb)
    check("skips non-set contributors", "if(!m_directlySet[s]) continue;" in pb)
    check("accumulates the affinity product",
          "influence += m_affinity[c][s] * m_currencyValues[s];" in pb)
    check("assigns (not accumulates) the propagated value",
          "m_currencyValues[c] = influence;" in pb)

# ----------------------------------------------------------------------
# 5. Anchor injection signs
# ----------------------------------------------------------------------
anchor = re.search(r"void\s+ApplyExternalAnchors\(void\)\s*\r?\n\s*\{.*?\r?\n[ ]{3,7}\}\r?\n", CORR_T, re.S)
check("ApplyExternalAnchors present", anchor is not None)
if anchor:
    ab = anchor.group(0)
    check("DXY contributes +1.0 * dxyDir to USD",
          "m_currencyValues[iUsd] += (1.0 * dxyDir);" in ab)
    check("gold-up contributes -1.0 to USD",
          "m_currencyValues[iUsd] += -1.0;" in ab)
    check("gold counted only when rising", "if(xauDir == 1)" in ab)
    check("anchors respect InpUseExternalAnchors",
          "if(!InpUseExternalAnchors) return;" in ab)
    check("anchor bias reset each pass", "m_anchorUsdBias = 0.0;" in ab)
check("missing symbol is a silent no-op", 'if(symbol == "") return 0;' in CORR_T)
check("zero history is a silent no-op", "if(c1 <= 0 || c2 <= 0) return 0;" in CORR_T)
check("anchor discovery cached, not per-tick", "m_anchorsResolved = true;" in CORR_T)

# ----------------------------------------------------------------------
# 6. Divide-by-zero guard + normalization
# ----------------------------------------------------------------------
check("maxAbs == 0 guarded to 1.0", "if(maxAbs == 0.0) maxAbs = 1.0;" in CORR_T)
check("consensus normalized to x100", "* 100.0;" in CORR_T)
check("ComputeConsensus walks exactly 28 pairs", "p < OTTO_PAIR_COUNT" in CORR_T)
check("pair count constant defined", "#define OTTO_PAIR_COUNT     28" in DEFS_T)
check("currency count constant defined", "#define OTTO_CURRENCY_COUNT 8" in DEFS_T)

# ----------------------------------------------------------------------
# 7. Suffix sanitization
# ----------------------------------------------------------------------
check("gold root detected",
      'StringFind(sym, "XAUUSD")' in CORR_T and 'StringFind(sym, "GOLD")' in CORR_T)
check("DXY root detected", 'StringFind(sym, "DXY")' in CORR_T)
check("USDX alias detected", 'StringFind(sym, "USDX")' in CORR_T)
check("USIDX alias detected", 'StringFind(sym, "USIDX")' in CORR_T)
check("uppercases before matching", "StringToUpper(sym);" in CORR_T)
check("stops at exactly 6 alpha chars", "if(StringLen(base) == 6) break;" in CORR_T)
check("anchor roots resolved before generic slicing",
      CORR_T.find('return "XAUUSD";') < CORR_T.find('string base = "";'))

# ----------------------------------------------------------------------
# 8. Veto threshold semantics
# ----------------------------------------------------------------------
opp = re.search(r"bool\s+IsConsensusOpposed\(.*?\r?\n[ ]{3,7}\}\r?\n", CORR_T, re.S)
check("IsConsensusOpposed present", opp is not None)
if opp:
    ob = opp.group(0)
    check("long vetoes at <= -threshold", "c <= -t" in ob)
    check("short vetoes at >= +threshold", "c >=  t" in ob)
    check("threshold read from the input",
          "double t = InpConsensusVetoThreshold;" in ob)
    check("disabled engine never vetoes",
          "if(!InpEnableVectorEngine) return false;" in ob)

check("InpConsensusVetoThreshold defaults to 50",
      re.search(r"InpConsensusVetoThreshold\s*=\s*50", DEFS_T) is not None)
check("InpEnableVectorEngine defaults true",
      re.search(r"InpEnableVectorEngine\s*=\s*true", DEFS_T) is not None)
check("InpUseExternalAnchors defaults true",
      re.search(r"InpUseExternalAnchors\s*=\s*true", DEFS_T) is not None)
check("InpCancelOpposingPendings defaults true",
      re.search(r"InpCancelOpposingPendings\s*=\s*true", DEFS_T) is not None)


# ----------------------------------------------------------------------
# 9. Enum + journal wiring
# ----------------------------------------------------------------------
check("VETO_CORRELATION defined", "VETO_CORRELATION" in DEFS_T)
check("journal maps VETO_CORRELATION to a reason", "Vector Consensus Veto" in OM_T)
check("order manager vetoes on consensus",
      "m_correlationFilter.IsConsensusOpposed(m_symbol, dir)" in OM_T)
check("veto is recorded on the block",
      "block.vetoReason = VETO_CORRELATION;" in OM_T)
check("cancellation sweep exists",
      "CancelOpposingConsensusOrders(void)" in OM_T)
check("sweep respects its enable flag",
      "if(!InpCancelOpposingPendings) return;" in OM_T)
check("sweep filters by magic",
      "OrderGetInteger(ORDER_MAGIC) != MagicNumber" in OM_T)
check("sweep covers all four pending types",
      all(t in OM_T for t in ("ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_BUY_STOP",
                              "ORDER_TYPE_SELL_LIMIT", "ORDER_TYPE_SELL_STOP")))
check("sweep releases the block ticket", "mod.limitOrderTicket = 0;" in OM_T)

# ----------------------------------------------------------------------
# 10. Integration order in otto.mq5
# ----------------------------------------------------------------------
ri = MAIN_T.find("g_correlationFilter.RefreshVectorState();")
ci = MAIN_T.find("g_orderManager.CancelOpposingConsensusOrders();")
check("otto.mq5 calls RefreshVectorState", ri > 0)
check("otto.mq5 calls CancelOpposingConsensusOrders", ci > 0)
check("refresh runs before the cancel sweep", 0 < ri < ci)

# ----------------------------------------------------------------------
# 11. Regression guards — v5.25 layer must be untouched
# ----------------------------------------------------------------------
check("GetCorrelationScore retained", "GetCorrelationScore" in CORR_T)
check("TS_Bias_ hive path retained", '"TS_Bias_"' in CORR_T)
check("ResolveBidirectionalConflict retained",
      "ResolveBidirectionalConflict" in CORR_T)
check("IsTradeVetoed retained", "IsTradeVetoed" in CORR_T)
check("BroadcastBias retained", "BroadcastBias" in CORR_T)
check("GetWeightedBiasSum retained", "GetWeightedBiasSum" in CORR_T)
check("28-pair + gold universe intact", 'm_allSymbols[28] = "XAUUSD";' in CORR_T)

# ----------------------------------------------------------------------
# 12. Currency index integrity
# ----------------------------------------------------------------------
codes = re.findall(r'm_currencies\[(\d)\] = "([A-Z]{3})";', CORR_T)
check("8 currencies mapped", len(codes) == 8, "found %d" % len(codes))
check("indices contiguous 0..7", [int(i) for i, _ in codes] == list(range(8)))
codeset = set(c for _, c in codes)
check("currency set == USD EUR GBP CHF JPY AUD NZD CAD",
      codeset == {"USD", "EUR", "GBP", "CHF", "JPY", "AUD", "NZD", "CAD"},
      str(sorted(codeset)))
cmap = dict((c, int(i)) for i, c in codes)
check("USD is index 0", cmap.get("USD") == 0, "USD at %s" % cmap.get("USD"))
check("affinity seeding runs in the ctor", "SeedAffinities();" in CORR_T)
check("consensus array initialized", "ArrayInitialize(m_pairConsensus, 0.0);" in CORR_T)

# ----------------------------------------------------------------------
# 13. EOL integrity (CRLF preserved everywhere)
# ----------------------------------------------------------------------
for path, label in ((CORR, "COttoCorrelationFilter.mqh"),
                    (OM, "COttoOrderManager.mqh"),
                    (DEFS, "OttoDefines.mqh"),
                    (MAIN, "otto.mq5")):
    raw = io.open(path, "rb").read()
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    check("%s is fully CRLF" % label, lf == crlf and lf > 0,
          "%d LF / %d CRLF" % (lf, crlf))

# ----------------------------------------------------------------------
# 14. Version stamp
# ----------------------------------------------------------------------
check("COttoCorrelationFilter.mqh carries a version stamp",
      re.search(r'#property version\s+"\d+\.\d+"', CORR_T) is not None)


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.26 CURRENCY VECTOR & AFFINITY ENGINE - STATIC PROBE")
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

