"""
v5.41 static verification probe - PHYSICAL ROUTE SAFETY PATCH.

Pins the two correctness fixes v5.41 lands on the v5.40 hybrid router. Neither
is provable by the MQL5 compiler gate: both are control-flow / side-effect
properties of the shipped sources.

  1. SAR DIRECTION GUARD. CheckPendingOrderFills() used to close the incumbent
     basket on ANY fill whose ticket differed from the tracked one. A
     SAME-direction scale-in fill was therefore read as a stop-and-reverse and
     fratricided the basket it was meant to add to. The guard now ALSO requires
     m_activeDirection != newDir, mirroring the virtual trigger's by-construction
     reversal check.

  2. N/R COMMENT TAG (physical). PlaceOrArmOrder() ROUTE A now stamps the
     block's Phase marker ("N" = Phase 1 bounce / "R" = Phase 2 reversal) into
     the resting broker limit's MT5 comment, so a terminal row names its phase
     exactly as the virtual MARKET leg already does.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
             "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def func_body(text, signature_re):
    """Return the full body of an MQL5 function, matched by counting braces."""
    m = re.search(signature_re, text)
    if m is None:
        return None
    start = text.find("{", m.start())
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def strip_comments(code):
    """Drop // comments, respecting string literals."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


OM_C = strip_comments(OM_T)
ROUTER = func_body(OM_C, r"bool\s+PlaceOrArmOrder\s*\(int blockIndex")
FILLS = func_body(OM_C, r"void\s+CheckPendingOrderFills\s*\(void\)")
VTRIG = func_body(OM_C, r"void\s+CheckVirtualTriggers\s*\(void\)")


# ----------------------------------------------------------------------
# 1. SAR direction guard on the PHYSICAL fill scanner
# ----------------------------------------------------------------------
print("\n-- Physical route SAR direction guard --")

check("CheckPendingOrderFills located", FILLS is not None)

# The incumbent-basket guard must now ALSO test direction, otherwise a
# same-direction scale-in fill is mistaken for a reversal.
check("physical SAR guard tests the incumbent ticket AND the direction",
      FILLS is not None and
      "m_hasActiveTrade" in FILLS and
      "m_activeTrade.ticket != newTicket" in FILLS and
      "m_activeDirection != newDir" in FILLS)

# The three clauses must sit on ONE boolean expression: the direction clause
# immediately follows the ticket clause (comments were stripped).
check("direction clause is welded onto the same if() as the ticket clause",
      FILLS is not None and
      re.search(r"m_activeTrade\.ticket\s*!=\s*newTicket\s*&&\s*"
                r"m_activeDirection\s*!=\s*newDir", FILLS) is not None)

# The physical guard must mirror the virtual trigger's, which already carried
# a direction clause -- there expressed as a bool->enum mapping
# (m_activeDirection != (isLong ? DIR_LONG : DIR_SHORT)).
check("physical guard mirrors the virtual trigger's direction check",
      FILLS is not None and VTRIG is not None and
      "m_activeDirection != newDir" in FILLS and
      re.search(r"m_activeDirection\s*!=", VTRIG) is not None)

# newDir is resolved by the 3-tier detector as DIR_LONG / DIR_SHORT, so the
# comparison is always meaningful (never DIR_NONE on a resolved fill).
check("newDir is resolved to a concrete direction before the guard",
      FILLS is not None and
      "ResolveFilledPositionTicket(" in FILLS and
      "DIR_LONG : DIR_SHORT" in OM_T)


# ----------------------------------------------------------------------
# 2. N/R phase tag on the resting PHYSICAL limit comment
# ----------------------------------------------------------------------
print("\n-- Physical route N/R comment tag --")

check("PlaceOrArmOrder located", ROUTER is not None)

# The phase local must be derived from the block's touch count: a block's
# FIRST fill (touches == 0) is the Phase 1 "N"ormal bounce, its SECOND
# (touches >= 1) the Phase 2 "R"eversal.
check("phase local is derived from block.touches",
      ROUTER is not None and
      re.search(r'string\s+phaseType\s*=\s*\(\s*block\.touches\s*==\s*0\s*\)'
                r'\s*\?\s*"N"\s*:\s*"R"\s*;', ROUTER) is not None)

# The physical comment must carry the phase marker between the session id and
# the "_P" (physical) flag.
check("physical comment embeds the N/R phase tag",
      ROUTER is not None and
      'ClampOrderComment(armSessionId + "_" + phaseType + "_P")' in ROUTER)

# The untagged shape must be gone, else a build could silently ship the old
# comment even though the local is declared.
check("untagged physical comment shape is gone",
      'ClampOrderComment(armSessionId + "_P")' not in OM_T)

# The tag is clamped through the existing helper (tail-preserves "-BLK<n>").
check("comment flows through ClampOrderComment",
      ROUTER is not None and "ClampOrderComment(" in ROUTER)


# ----------------------------------------------------------------------
# 3. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.41 or later", parts >= (5, 41), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 11 files stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.41 PHYSICAL ROUTE SAFETY PATCH - STATIC PROBE")
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

