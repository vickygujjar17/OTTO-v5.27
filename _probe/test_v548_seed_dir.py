"""
v5.48a static verification probe - FILL-TIME SEED DIRECTION.

HISTORICAL (v5.48). A block used to carry two phases in one integer:
touches == 0 was the PHASE 1 "R"eversal bounce (support -> LONG, resistance ->
SHORT) and touches >= 1 the PHASE 2 "C"ontinuation, where the setup traded the
reclaimed zone as an S/R FLIP (support -> SHORT, resistance -> LONG). The two
fill paths disagreed about WHEN that integer was consumed, so the virtual path
could seed the live basket from the FLIPPED side while the MARKET order had been
sent on the arm-time side. v5.48 fixed that by capturing the fill-time side into
seedDir and threading it through SeedActiveTradeFromBlock()'s optional
overrideDir.

CURRENT (v5.72). Both of those mechanisms are gone, and this probe pins the
stronger property that replaced them:

  * the two-phase engine is retired, so GetDirectionForBlock() is
    UNCONDITIONAL on the zone type and the derived side cannot lag a fill;
  * the virtual MARKET fire was overrideDir's only caller, so the parameter is
    RETIRED with it -- SeedActiveTradeFromBlock() no longer accepts an override;
  * there is now exactly ONE caller of the seed (the resting-limit fill scan),
    which is what the census pins: a second seeding path cannot reappear
    without failing this probe.

The remaining properties are STRUCTURE-sensitive, which is exactly what the
other execution probes miss. Pure static analysis of the shipped sources; no MT5
required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so the multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


def _rel_parts(stamps):
    """Numeric (major, minor) of the common release stamp; [] -> (0, 0).

    Bump-tolerant, matching the v5.47 probe convention: assert the sources
    AGREE on one stamp that is AT LEAST the release this probe shipped with,
    rather than pinning a literal that fails on every later bump (a probe
    defect, not a code defect)."""
    if not stamps:
        return (0, 0)
    major, minor = sorted(set(stamps))[0].split(".")
    return (int(major), int(minor))


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


OM_T = read(OM)
OM_C = strip_comments(OM_T)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


FILLS = func_body(OM_C, r"void\s+CheckPendingOrderFills\s*\(\s*(?:void\s*)?\)")
SEED = func_body(OM_C, r"void\s+SeedActiveTradeFromBlock\s*\(")
DIRFN = func_body(OM_C, r"ENUM_TRADE_DIRECTION\s+GetDirectionForBlock\s*\(")

# ----------------------------------------------------------------------
# 1. One direction resolver, now UNCONDITIONAL on the zone type
# ----------------------------------------------------------------------
print("\n-- Direction resolver: unconditional continuation mapping --")

check("GetDirectionForBlock() located", DIRFN is not None)
check("the resolver reads ONLY the zone type (no lifecycle counter)",
      DIRFN is not None and "touches" not in DIRFN)
check("a RESISTANCE zone trades LONG / a SUPPORT zone trades SHORT",
      DIRFN is not None and
      re.search(r"return\s*\(\s*block\.type\s*==\s*BLOCK_SUPPORT\s*\)\s*\?\s*"
                r"DIR_SHORT\s*:\s*DIR_LONG\s*;", DIRFN, re.S) is not None)

# ----------------------------------------------------------------------
# 2. The virtual fire is RETIRED, so no fill-time side can disagree
# ----------------------------------------------------------------------
# v5.48's whole premise was that two independent fill paths could disagree about
# the side. v5.72 removed one of them, which dissolves the premise rather than
# re-pinning it: there is no second path left to capture a side FROM.
print("\n-- Virtual fire retired (no second fill path) --")

check("CheckVirtualTriggers is gone from the manager",
      "CheckVirtualTriggers" not in OM_C)
check("no seedDir local exists anywhere", "seedDir" not in OM_C)

# ----------------------------------------------------------------------
# 3. The seed derives the side; it takes no override at all
# ----------------------------------------------------------------------
print("\n-- Seed derives the side (no override parameter) --")

check("SeedActiveTradeFromBlock located", SEED is not None)
check("the seed signature is two arguments (block + ticket)",
      re.search(r"void\s+SeedActiveTradeFromBlock\s*\(\s*const\s+SSniperBlock\s*&block"
                r"\s*,\s*ulong\s+positionTicket\s*\)", OM_C) is not None)
check("the overrideDir parameter is retired",
      "overrideDir" not in OM_C)
check("the seed derives the side from the block",
      SEED is not None and
      "m_activeTrade.direction    = GetDirectionForBlock(block);" in SEED)
check("there is exactly one writer of m_activeTrade.direction in the seed",
      SEED is not None and
      len(re.findall(r"m_activeTrade\.direction\s*=[^=]", SEED)) == 1,
      "found %d" % (len(re.findall(r"m_activeTrade\.direction\s*=[^=]", SEED))
                    if SEED else -1))
check("the resolved side still reaches m_activeDirection",
      SEED is not None and "m_activeDirection = m_activeTrade.direction;" in SEED)

# ----------------------------------------------------------------------
# 4. The one seed call site passes no override
# ----------------------------------------------------------------------
print("\n-- Physical fill: two-argument seed --")

check("CheckPendingOrderFills located", FILLS is not None)
check("the fill seed keeps its two-argument form",
      FILLS is not None and
      re.search(r"SeedActiveTradeFromBlock\s*\(\s*blocks\[i\]\s*,\s*newTicket\s*\)"
                r"\s*;", FILLS) is not None)
# v5.52: there is no touch increment left to order the seed against, so pin its
# absence. The seed must also remain a one-shot retirement (isTriggered) rather
# than a counter advance.
check("the retired touch increment is gone from the physical fill",
      FILLS is not None and "touches" not in FILLS)

# ----------------------------------------------------------------------
# 5. Call-site census: the chokepoint is the ONLY way a basket is seeded
# ----------------------------------------------------------------------
print("\n-- Seed call-site census --")

sites = len(re.findall(r"\bSeedActiveTradeFromBlock\s*\(", OM_C))
check("exactly one definition + ONE call site seed a basket",
      sites == 2, "matches=%d" % sites)

check("no call site passes an override",
      re.search(r"SeedActiveTradeFromBlock\s*\([^);]*\bseedDir\b", OM_C) is None)

# ----------------------------------------------------------------------
# 7. Release stamp
# ----------------------------------------------------------------------
print("\n-- Release stamp --")

stamps = []
for f in ALL_FILES:
    m = re.search(r'#property version\s+"(\d+\.\d+)"', read(os.path.join(ROOT, f)))
    if m:
        stamps.append(m.group(1))
check("all %d shipped sources carry a version stamp" % len(ALL_FILES),
      len(stamps) == len(ALL_FILES), "found %d" % len(stamps))
check("every shipped source is stamped 5.48 or later",
      len(set(stamps)) == 1 and _rel_parts(stamps) >= (5, 48),
      "stamps=%s" % sorted(set(stamps)))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.48a FILL-TIME SEED DIRECTION - STATIC PROBE")
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


