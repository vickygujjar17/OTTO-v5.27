"""
v5.48a static verification probe - VIRTUAL-FIRE PHASE-2 SEED DIRECTION.

A block carries two phases in one integer: touches == 0 is the PHASE 1
"N"ormal bounce (support -> LONG, resistance -> SHORT), touches == 1 is the
PHASE 2 "R"eversal, where the setup trades the reclaimed zone as an S/R FLIP
(support -> SHORT, resistance -> LONG). GetDirectionForBlock() is the single
place that decides which side a block is on, and it reads that integer.

Both fill paths seed the live basket with SeedActiveTradeFromBlock(), which
historically derived its direction from GetDirectionForBlock() -- correct only
while the block is still in the phase that was just filled. The two paths
disagree about WHEN the touch is consumed:

  * CheckPendingOrderFills() (PHYSICAL resting limit) SEEDS FIRST, then does
    mod.touches += 1. The block is still Phase 1 at seed time, so the derived
    direction equals the side of the resting limit the broker just filled.

  * CheckVirtualTriggers() (VIRTUAL market fire) consumes the touch FIRST
    (block.touches += 1), then seeds. The increment promotes a Phase 1 block to
    touches == 1, so GetDirectionForBlock() now reports the FLIPPED side, while
    the MARKET order that filled a moment earlier was sent on the arm-time side
    (vo.direction). The basket is therefore seeded with the OPPOSITE direction
    to the live position.

That desync is silent and durable: m_activeTrade.direction and m_activeDirection
drive the trail (which way "favourable" is), the 1R basis, the journal entry and
the High Table phantom-mirror for the entire life of that leg.

v5.48 gives SeedActiveTradeFromBlock() an optional overrideDir (default
DIR_NONE = derive as before) and has the virtual path capture the fill-time side
into seedDir BEFORE it consumes the touch, then pass it in. The physical path is
untouched and keeps deriving.

The properties pinned below are ORDER-sensitive, which is exactly what the
existing v5.40 / v5.41 / v5.43 probes miss: they assert the two statements are
PRESENT in each path, never which comes first. Pure static analysis of the
shipped sources; no MT5 required.
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
# 1. The root cause is intact: one phase-aware direction resolver
# ----------------------------------------------------------------------
print("\n-- Phase-aware direction resolver --")

check("GetDirectionForBlock() located", DIRFN is not None)
check("the resolver still reads the block's touch phase",
      DIRFN is not None and "block.touches" in DIRFN)
check("Phase 2 (touches >= 1) still flips support->SHORT / resistance->LONG",
      DIRFN is not None and
      re.search(r"block\.touches\s*>=\s*1\s*\)\s*"
                r"return\s*\(\s*block\.type\s*==\s*BLOCK_SUPPORT\s*\)\s*\?\s*"
                r"DIR_SHORT\s*:\s*DIR_LONG\s*;", DIRFN, re.S) is not None)

# ----------------------------------------------------------------------
# 2. The virtual path captures the fill-time side BEFORE it consumes the touch
# ----------------------------------------------------------------------
print("\n-- Virtual fire: side captured before the touch is consumed --")

check("CheckVirtualTriggers located", VTRIG is not None)
check("the fire reads the side off the armed virtual order",
      VTRIG is not None and "bool isLong = (vo.direction == DIR_LONG);" in VTRIG)
check("a seedDir local is declared from that side",
      VTRIG is not None and
      re.search(r"ENUM_TRADE_DIRECTION\s+seedDir\s*=\s*isLong\s*\?\s*"
                r"DIR_LONG\s*:\s*DIR_SHORT\s*;", VTRIG) is not None)

if VTRIG is not None:
    decl = VTRIG.find("ENUM_TRADE_DIRECTION seedDir")
    inc = re.search(r"block\.touches\s*\+=\s*1\s*;", VTRIG)
    check("seedDir is captured BEFORE block.touches += 1 (the whole point)",
          decl >= 0 and inc is not None and decl < inc.start(),
          "decl=%d increment=%s" % (decl, inc.start() if inc else None))
    seed_call = re.search(r"SeedActiveTradeFromBlock\s*\(", VTRIG)
    check("the virtual seed call follows the increment it has to outlive",
          seed_call is not None and inc is not None and
          inc.start() < seed_call.start())

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
check("DIR_NONE (a physical fill) still derives the side from the phase",
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
# 5. The physical path is deliberately untouched
# ----------------------------------------------------------------------
print("\n-- Physical fill: unchanged seed-before-increment order --")

check("CheckPendingOrderFills located", FILLS is not None)
check("the physical seed keeps its two-argument form (DIR_NONE derive)",
      FILLS is not None and
      re.search(r"SeedActiveTradeFromBlock\s*\(\s*blocks\[i\]\s*,\s*newTicket\s*\)"
                r"\s*;", FILLS) is not None)

if FILLS is not None:
    p_seed = FILLS.find("SeedActiveTradeFromBlock(blocks[i], newTicket);")
    p_inc = re.search(r"mod\.touches\s*\+=\s*1\s*;", FILLS)
    check("the physical seed still precedes its touch increment",
          p_seed >= 0 and p_inc is not None and p_seed < p_inc.start(),
          "seed=%d increment=%s" % (p_seed, p_inc.start() if p_inc else None))

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
check("every shipped source is stamped 5.48",
      bool(stamps) and set(stamps) == {"5.48"}, "stamps=%s" % sorted(set(stamps)))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.48a VIRTUAL-FIRE PHASE-2 SEED DIRECTION - STATIC PROBE")
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


