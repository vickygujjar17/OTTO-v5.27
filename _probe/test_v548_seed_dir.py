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

CURRENT (v5.52). The two-phase engine is gone. The block no longer carries a
touch counter at all, and GetDirectionForBlock() is UNCONDITIONAL on the zone
type -- "a RESISTANCE zone trades LONG, a SUPPORT zone trades SHORT" -- so the
derived side can no longer lag the fill. What survives, and what this probe now
pins, is the CONTRACT, which is still worth a build gate:

  * the overrideDir parameter and its DIR_NONE default,
  * the precedence "(overrideDir != DIR_NONE) ? overrideDir : derived",
  * the virtual fire capturing the side from the live virtual order and
    declaring it BEFORE the seed call that consumes it,
  * the physical fill seeding in its two-argument (derive) form, and
  * the one-definition plus two-call-site seed census.

The remaining properties are STRUCTURE-sensitive, which is exactly what the
existing v5.40 / v5.41 / v5.43 probes miss. Pure static analysis of the shipped
sources; no MT5 required.
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


VTRIG = func_body(OM_C, r"void\s+CheckVirtualTriggers\s*\(\s*(?:void\s*)?\)")
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
# 2. The virtual path captures the fill-time side from the live order
# ----------------------------------------------------------------------
print("\n-- Virtual fire: side captured from the armed virtual order --")

check("CheckVirtualTriggers located", VTRIG is not None)
check("the fire reads the side off the armed virtual order",
      VTRIG is not None and "bool isLong = (vo.direction == DIR_LONG);" in VTRIG)
check("a seedDir local is declared from that side",
      VTRIG is not None and
      re.search(r"ENUM_TRADE_DIRECTION\s+seedDir\s*=\s*isLong\s*\?\s*"
                r"DIR_LONG\s*:\s*DIR_SHORT\s*;", VTRIG) is not None)

if VTRIG is not None:
    decl = VTRIG.find("ENUM_TRADE_DIRECTION seedDir")
    seed_call = re.search(r"SeedActiveTradeFromBlock\s*\(", VTRIG)
    check("seedDir is declared BEFORE the seed call that consumes it",
          decl >= 0 and seed_call is not None and decl < seed_call.start(),
          "decl=%d seed_call=%s" % (decl, seed_call.start() if seed_call else None))
    # v5.52: the touch counter is gone from the tree, so the old "captured
    # before the increment" ordering cannot silently regress any more. Pin the
    # counter's ABSENCE instead -- if it ever returns, the capture ordering
    # above stops being sufficient and this probe must be revisited.
    check("the retired touch counter is gone from the virtual fire",
          "touches" not in VTRIG)

# ----------------------------------------------------------------------
# 3. The virtual path threads that side into the seed
# ----------------------------------------------------------------------
print("\n-- Virtual fire: seed receives the fill-time side --")

check("the virtual fill passes seedDir into SeedActiveTradeFromBlock",
      VTRIG is not None and
      re.search(r"SeedActiveTradeFromBlock\s*\(\s*block\s*,\s*result\.order\s*,\s*"
                r"seedDir\s*\)\s*;", VTRIG) is not None)

# ----------------------------------------------------------------------
# 4. The seed honours the override in exactly one place
# ----------------------------------------------------------------------
print("\n-- Seed honours the fill-time override --")

check("SeedActiveTradeFromBlock located", SEED is not None)
check("the seed takes the optional overrideDir parameter",
      SEED is not None and
      re.search(r"ENUM_TRADE_DIRECTION\s+overrideDir\s*=\s*DIR_NONE\s*\)", SEED)
      is not None)
check("DIR_NONE (a physical fill) still derives the side from the block",
      SEED is not None and
      re.search(r"\(overrideDir\s*!=\s*DIR_NONE\s*\)\s*\?\s*overrideDir\s*:\s*"
                r"GetDirectionForBlock\s*\(\s*block\s*\)", SEED, re.S) is not None)
check("there is exactly one writer of m_activeTrade.direction in the seed",
      SEED is not None and
      len(re.findall(r"m_activeTrade\.direction\s*=[^=]", SEED)) == 1,
      "found %d" % (len(re.findall(r"m_activeTrade\.direction\s*=[^=]", SEED))
                    if SEED else -1))
check("the resolved side still reaches m_activeDirection",
      SEED is not None and "m_activeDirection = m_activeTrade.direction;" in SEED)

# ----------------------------------------------------------------------
# 5. The physical path seeds in its two-argument (derive) form
# ----------------------------------------------------------------------
print("\n-- Physical fill: two-argument seed --")

check("CheckPendingOrderFills located", FILLS is not None)
check("the physical seed keeps its two-argument form (DIR_NONE derive)",
      FILLS is not None and
      re.search(r"SeedActiveTradeFromBlock\s*\(\s*blocks\[i\]\s*,\s*newTicket\s*\)"
                r"\s*;", FILLS) is not None)
# v5.52: there is no touch increment left to order the seed against, so pin its
# absence. The seed must also remain a one-shot retirement (isTriggered) rather
# than a counter advance.
check("the retired touch increment is gone from the physical fill",
      FILLS is not None and "touches" not in FILLS)

# ----------------------------------------------------------------------
# 6. Call-site census: the chokepoint is the only way a basket is seeded
# ----------------------------------------------------------------------
print("\n-- Seed call-site census --")

sites = len(re.findall(r"\bSeedActiveTradeFromBlock\s*\(", OM_C))
check("exactly one definition + two call sites seed a basket",
      sites == 3, "matches=%d" % sites)

overrides = len(re.findall(r"SeedActiveTradeFromBlock\s*\([^);]*\bseedDir\b", OM_C))
check("exactly one call site passes an override (the virtual fire)",
      overrides == 1, "matches=%d" % overrides)

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


