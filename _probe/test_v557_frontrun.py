"""
Front-run / true deep front-edge entry - static verification probe.

Originally written for v5.57, which moved the entry, the stop and the
take-profit onto an EXTREME FRONT-RUNNING ANTICIPATION model and moved the
level the virtual engine waits on from a resting price to a MARKET cross.
v5.66 deepened that entry (gap = 2.0*blockHeight + 1.5*ATR out from the NEAR
edge) and re-anchored the BREAK veto onto the same price. v5.69 then found
both of those incoherent and corrected them: the gap is now measured to the
OPPOSITE side of the zone (a Resistance LONG buys BELOW b.bottom, a Support
SHORT sells ABOVE b.top), which makes the order a legal resting limit, and the
BREAK veto is decoupled from the entry and put back on the physical FAR edge.
The properties below carry that change and NOT ONE of them is visible to the
MQL5 compiler, because each is a runtime relationship between two files:

  1. ATR SHIFT SYNC. COttoBlockManager prices its Front-Run veto projection
     from GetATRAtShift(1) -- the last CLOSED bar. COttoOrderManager must
     price the matching entry / SL / TP / rrUnit from the SAME shift, or on
     a live bar the order and the veto would be built from two different
     ATRs and the veto would compare the market against a target the order
     never had. The cached GetATR() refreshes from shift 0 (the FORMING
     bar), so it may only ever be a FALLBACK: shift 1 first, GetATR()
     second, and a hard abandon when neither is populated. GetATRAtShift()
     itself had to move into the public section for the OrderManager call to
     compile at all -- and the compiler cannot tell that the CALLER meant
     the same shift the veto uses.

  2. THE CROSS TEST (re-flipped in v5.69). The entry sits on the NEAR side of
     the zone, i.e. BELOW the market for a LONG and ABOVE it for a SHORT, so
     the virtual engine fires when the ask comes back DOWN to the trigger (a
     LONG) or the bid rallies UP to it (a SHORT). v5.66 ran this the opposite
     way round, because its entry sat BEYOND the far edge as a breakout. The
     sign of that one comparison is the whole difference between "buy the
     pullback" and "buy the breakout", and it is invisible at compile time --
     the block still arms either way.

  3. THE FAR-EDGE TARGET ANCHOR. slDist = blockHeight + 0.5*ATR, and the
     entry is 2.0*blockHeight + 1.5*ATR out from the near edge on the OPPOSITE
     side of the zone. The target is deliberately measured from the FAR EDGE
     (block.top LONG / block.bottom SHORT), not from the entry, so the
     projection reproduces what every pre-anticipation build projected and the
     veto keeps its strictness. Anchoring it on the entry instead would pull
     the target INSIDE the zone and veto setups that were never front-runs.
     The consequence, pinned in section 7, is that the reward from the ENTRY is
     (3.0 + InpMaxRR) * slDist, i.e. the realized multiple is EXACTLY
     InpMaxRR + 3.0 at every zone height and ATR -- 7.0R on the shipped 4.0.
     The pre-v5.69 "InpMaxRR must stay above 3.0" floor is RETIRED: it was an
     artifact of an entry anchored PAST the far edge. Intended, not a defect,
     and asserted so a future re-anchor has to be a deliberate act.

  4. THE BREAK VETO ANCHOR (v5.69, decoupled). v5.66 hung the break test on
     BlockEntryPrice(), which only worked while the fill sat past the far
     edge. With the entry on the near side it inverts: InpArmATR is 0.0, so a
     RESISTANCE arms the instant close1 < b.bottom while the entry is a further
     `gap` below that, and the entry-anchored break threshold lands BELOW the
     arm trigger -- so close1 > threshold is already true on the arming close
     and the block vetoes itself VETO_BROKEN, after which
     CancelOrdersForInvalidBlocks() drops the pending. Every setup dies the
     instant it is armed. Section 4b pins the physical far-edge anchor and
     section 7 proves both halves numerically: the retired anchor fires on the
     arming close, the shipped one cannot.

  5. THE ROUTER, AND THE JOURNAL'S PRINTED GEOMETRY (v5.69). Because the entry
     is now on the limit side of the market, `priceValid` is TRUE in the
     ordinary case and the setup rests PHYSICALLY at the broker; the virtual
     arm is the fallback. v5.66 asserted the exact opposite in four separate
     comment blocks. The journal's per-direction captions had to follow too --
     they are printed strings, so neither the compiler nor any test could catch
     them drifting from the code. Section 10 pins both rows.

WHAT THIS PROBE PINS. The ATR shift sync and its fallback in all three
OrderManager price paths, the public visibility of GetATRAtShift(), the two
entry twins' textual identity (COttoBlockManager::BlockEntryPrice() vs
COttoOrderManager::CalcEntryPrice(), which MUST judge one price), the
two-argument call sites and the retirement of the single-argument form, the
far-edge projection in BOTH BlockManager passes, the break veto's decoupling
onto the physical far edge, the near-side cross test plus the absence of the
retired v5.66 predicate, the geometry identities behind the realization above,
the physical-rest-first router, the journal captions, and the release stamps.

WHAT IT CANNOT PROVE. That the ATR cache slot is actually POPULATED on the
tick that matters, that the cross fires on the intended bar, and that a block
survives the walk from arming to fill, are Strategy-Tester properties. This
probe is pure static analysis of the shipped sources -- no MT5 required.

Companion to test_v530_price_guard.py, which pins the SNAP the entry price
depends on but pins nothing about where that price comes from.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
BM = os.path.join(ROOT, "COttoBlockManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so every anchor below can be written with a plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


def strip_comments(code):
    """Drop // comments, respecting string literals.

    Load-bearing here: v5.57 documents the RETIRED behaviour in place
    ("Projecting from the entry pushed the target a further 0.5*ATR out"),
    so a raw substring test would trip over the probe's own subject matter.
    A '//' is only a comment when an even number of '"' precede it.
    """
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def norm(s):
    """Collapse every whitespace run so two spellings can be compared."""
    return re.sub(r"\s+", " ", s).strip()


def body(src, marker):
    """Brace-matched body of the function whose header contains `marker`.

    The shipped sources slate-indent every method, so a method's closing
    brace is the first one in column 0 -- but a nested block's brace is also
    column-indented and can be found first, so slicing to the next "\n    }"
    would not generalise. This walks the braces instead, skipping string and
    char literals.
    """
    i = src.find(marker)
    if i < 0:
        return None
    j = src.find("{", i)
    if j < 0:
        return None
    depth = 0
    k = j
    n = len(src)
    while k < n:
        c = src[k]
        if c == '"':
            k += 1
            while k < n and src[k] != '"':
                k += 2 if src[k] == "\\" else 1
        elif c == "'":
            k += 1
            while k < n and src[k] != "'":
                k += 2 if src[k] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    return src[j:]


def canon(fragment, var):
    """Blank the twin-specific identifiers so the two verify as one.

    BlockEntryPrice() reads `const SSniperBlock &b` and calls
    BlockDirection(); CalcEntryPrice() reads `&block` and calls
    GetDirectionForBlock(). Those spellings are the ONLY permitted
    difference; everything else must match byte for byte.
    """
    s = fragment
    s = s.replace(var + ".", "")
    s = s.replace("GetDirectionForBlock(block)", "Dir(x)")
    s = s.replace("BlockDirection(b)", "Dir(x)")
    return norm(s)


def canon_body(fn, var):
    raw = fn or ""
    brace = raw.find("{")
    return canon(raw[brace:] if brace >= 0 else raw, var)


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


OM_RAW = read(OM)
BM_RAW = read(BM)
JRN_RAW = read(JRN)
MAIN_T = read(MAIN)
DEFS_T = read(DEFS)
OM_C = strip_comments(OM_RAW)
BM_C = strip_comments(BM_RAW)

OM_PLACE = body(OM_C, "PlaceOrArmOrder(int blockIndex")
OM_VIRT = body(OM_C, "CheckVirtualTriggers(void)")
OM_REV = body(OM_C, "OpenReversalPosition(SSniperBlock &targetBlock)")
OM_CALC = body(OM_C, "CalcEntryPrice(const SSniperBlock &block, double atr)")
OM_SLD = body(OM_C, "CalcSLDistance(const SSniperBlock &block, double atr)")
BM_GET = body(BM_C, "GetATRAtShift(int shift)")
BM_BEP = body(BM_C, "BlockEntryPrice(const SSniperBlock &b, double atr) const")
BM_PROC = body(BM_C, "ProcessBlocks(int marketDay)")
# v5.80 ARCHITECTURAL STRIP: UpdateIntraBar() -- the intra-bar twin of the
# Front-Run veto -- is DELETED, together with CheckVetoesInTick(). The
# extraction is kept so section 2 can assert its ABSENCE by name rather than
# leaving a dangling `None` that crashes later code.
BM_INTRA = body(BM_C, "UpdateIntraBar(int marketDay)")

# The gate count is asserted as an EQUALITY so a third (ungated) pass added
# later fails the probe instead of passing it. The leading text guard stops
# the lookbehind escaping a named constant such as !InpUseFrontRunVeto, and
# \b admits the bar-close form that ANDs a hasExited/!isTriggered guard on.
# v5.80: the Front-Run veto is RETIRED, so the expected count is now ZERO --
# the equality still does its job, in the other direction.
_gates0 = re.findall(r"(?<![\w!])if\(InpUseFrontRunVeto\b", BM_RAW)
_gates = len(_gates0)


# ----------------------------------------------------------------------
# 1. ATR SHIFT SYNC - all three OrderManager price paths
# ----------------------------------------------------------------------
print("\n-- 1. ATR shift sync --")

SHIFT1 = "m_blockManager.GetATRAtShift(1)"
FALLBACK = "if(atr <= 0) atr = m_blockManager.GetATR();"

# v5.72: the virtual fire was the third consumer of this pair and is retired,
# leaving PlaceOrArmOrder and OpenReversalPosition -- the two paths that still
# build a price. The census moves 3 -> 2 with it; the PROPERTY (both survivors
# read shift 1, with GetATR() only as a fallback) does not change.
for label, fn in (("PlaceOrArmOrder", OM_PLACE),
                  ("OpenReversalPosition", OM_REV)):
    blob = norm(fn or "")
    check("%s prices from GetATRAtShift(1)" % label, SHIFT1 in blob)
    check("%s keeps GetATR() only as the fallback" % label, FALLBACK in blob)

check("exactly 2 shift-1 ATR reads order the manager's geometry",
      len(re.findall(r"GetATRAtShift\(1\)", OM_C)) == 2,
      "found %d" % len(re.findall(r"GetATRAtShift\(1\)", OM_C)))
check("exactly 2 matching GetATR() fallbacks",
      len(re.findall(r"atr <= 0\) atr = m_blockManager\.GetATR\(\)", OM_C)) == 2,
      "found %d" % len(re.findall(r"atr <= 0\) atr = m_blockManager\.GetATR\(\)", OM_C)))
check("the live entry path abandons the setup when no ATR is available",
      re.search(r"if\(atr <= 0\) return false;", OM_PLACE or "") is not None)
check("the reversal fallback is guarded by atr > 0, not a hard return",
      "if(atr > 0)" in (OM_REV or ""))

# An extraction failure must surface as ONE named failure, not an obscure
# TypeError deep inside norm() -- a probe that crashes reports nothing.
check("every OrderManager price path resolved",
      all(f is not None for f in (OM_PLACE, OM_REV, OM_CALC, OM_SLD)))
check("the BlockManager helpers still resolve",
      all(f is not None for f in (BM_GET, BM_BEP, BM_PROC)))

# ----------------------------------------------------------------------
# 2. GetATRAtShift() is reachable, and reads the shift it names
# ----------------------------------------------------------------------
print("\n-- 2. GetATRAtShift visibility and semantics --")

check("GetATRAtShift() is declared in the public section",
      re.search(r"public:(?:(?!private:)[\s\S])*GetATRAtShift\(int shift\)",
                BM_RAW) is not None,
      "declaration is not reachable from COttoOrderManager")
check("it bounds-checks the requested shift", "shift < 0" in (BM_GET or ""))
check("it honours the named shift rather than a hardcoded slot",
      "m_atrHistory[shift]" in (BM_GET or ""))
check("it returns 0 for an unpopulated slot rather than EMPTY_VALUE",
      "EMPTY_VALUE" in (BM_GET or "") and "return 0" in (BM_GET or ""))

# v5.80: the Front-Run veto that used to read shift 1 in BOTH BlockManager
# passes is RETIRED. What must hold now is the reverse -- no BlockManager pass
# reads shift 1 for veto purposes any more, and the intra-bar twin is gone.
check("the Front-Run veto pass is gone from the BlockManager",
      _gates == 0, "found %d InpUseFrontRunVeto gate(s)" % _gates)
check("the retired bar-close veto predicate is gone",
      "if(InpUseFrontRunVeto && b.hasExited && !b.isTriggered)"
      not in norm(BM_PROC or ""))
check("the intra-bar twin UpdateIntraBar() was deleted",
      BM_INTRA is None and "UpdateIntraBar" not in BM_C)
check("its public wrapper CheckVetoesInTick() was deleted",
      "CheckVetoesInTick" not in BM_C and
      "CheckVetoesInTick(" not in strip_comments(MAIN_T))
check("the anticipation entry applies at EVERY entry style, not just the default",
      len(re.findall(r"ENTRY_FRONT_EDGE", BM_PROC or "")) == 0,
      "the front-run pass must not be switchable away by InpEntryStyle")


# ----------------------------------------------------------------------
# 3. The two entry twins, and the retirement of the single-arg form
# ----------------------------------------------------------------------
print("\n-- 3. Entry twins --")

check("BlockManager exposes the TWO-argument BlockEntryPrice",
      "BlockEntryPrice(const SSniperBlock &b, double atr) const" in BM_RAW)
check("OrderManager exposes the TWO-argument CalcEntryPrice",
      "CalcEntryPrice(const SSniperBlock &block, double atr)" in OM_RAW)
check("SnapToTick wraps the TWO-argument call",
      "SnapToTick(CalcEntryPrice(block, atr))" in OM_RAW)
check("the single-argument CalcEntryPrice(block) form is retired",
      "CalcEntryPrice(block)" not in OM_C)
check("the single-argument BlockEntryPrice(b) form is retired",
      "BlockEntryPrice(b)" not in BM_C)

# The twins MUST judge one price: the veto compares the market against a
# target this helper produced. Comparing the canonicalised bodies catches a
# divergence in the arithmetic while tolerating the two legitimate
# spellings documented in canon().
BEP_BODY = canon_body(BM_BEP, "b")
CALC_BODY = canon_body(OM_CALC, "block")
check("the two entry twins are arithmetically identical",
      BEP_BODY == CALC_BODY,
      "BM=%s || OM=%s" % (BEP_BODY[:70], CALC_BODY[:70]))
check("both twins honour ENTRY_MIDPOINT before the front-edge maths",
      "if(InpEntryStyle == ENTRY_MIDPOINT) return midpoint;" in BEP_BODY and
      "if(InpEntryStyle == ENTRY_MIDPOINT) return midpoint;" in CALC_BODY)
check("the front-edge entry sits 2.0 heights + 1.5 ATR from the near edge, on "
      "the OPPOSITE side of the zone",
      "double gap = (2.0 * blockHeight) + (1.5 * atr);" in BEP_BODY and
      "return (Dir(x) == DIR_LONG) ? (bottom - gap) : (top + gap);" in BEP_BODY,
      BEP_BODY)
check("the OrderManager twin uses the identical gap expression",
      "double gap = (2.0 * blockHeight) + (1.5 * atr);" in CALC_BODY and
      "return (Dir(x) == DIR_LONG) ? (bottom - gap) : (top + gap);" in CALC_BODY,
      CALC_BODY)
# v5.66 shipped the inverted signs -- (bottom + gap) for a LONG, which put the
# entry ABOVE the market and made the BUY_LIMIT illegal. Both twins are pinned
# above, so this is belt-and-braces against a partial revert of one of them.
for _label, _blob in (("BlockEntryPrice", BEP_BODY), ("CalcEntryPrice", CALC_BODY)):
    check("%s no longer places the entry on the far side of the zone" % _label,
          "(bottom + gap)" not in _blob and "(top - gap)" not in _blob)

# ----------------------------------------------------------------------
# 4. The far-edge target anchor (OrderManager only since v5.80)
# ----------------------------------------------------------------------
# The Front-Run veto was the ONLY consumer of a BlockManager-side TP
# projection, so both its bar-close copy and the intra-bar twin went with it.
# What remains -- and must remain -- is the projection that actually SETS the
# take-profit: the OrderManager's. Its anchor is pinned in section 6.
print("\n-- 4. Far-edge target anchor --")

FAR_EDGE_LONG = "isLong ? MathMax(top, bottom) : MathMin(top, bottom);"

check("the BlockManager no longer projects a target at all (veto retired)",
      "farEdge" not in BM_C and "calcSLDist" not in BM_C,
      "a residual projection means a veto pass survived")
check("neither BlockManager pass vetoes with VETO_FRONTRUN",
      "SetVeto(i, VETO_FRONTRUN);" not in BM_C)
check("the BlockManager does not recall a single-arg entry helper",
      "BlockEntryPrice(b)" not in (BM_PROC or ""))

# The anchor is only FAR if the high edge is named first: on a valid block
# top > bottom, so MathMax(top,bottom) SHORT would be near-edge anchored.
# PlaceOrArmOrder holds the block by reference (&block), so its operand
# names the variable rather than the literal field -- hence `\w+\.top`.
anchor_kind = re.search(r"isLong \? (Math\w+)\(\w+\.top, \w+\.bottom\)",
                        norm(OM_PLACE or ""))
check("the surviving projection takes the HIGH edge for a LONG",
      anchor_kind is not None and anchor_kind.group(1) == "MathMax",
      "op=%s" % (anchor_kind.group(1) if anchor_kind else None))
check("the anchor comment states the top/bottom LONG-SHORT mapping",
      "top LONG / block.bottom SHORT" in OM_RAW)

# The OrderManager must project the veto an IDENTICAL target from the same
# anchor, or the veto would gate on a price the order never had.
PLACE_CANON = canon(OM_PLACE or "", "block")
check("OrderManager mirrors the far-edge anchor",
      FAR_EDGE_LONG in PLACE_CANON)
check("OrderManager projects it by tpRR * slDist",
      "isLong ? farEdge + tpRR * slDist : farEdge - tpRR * slDist;" in PLACE_CANON)
check("OrderManager writes the exact TP back to the block",
      "localTP = takeProfit;" in PLACE_CANON)


# ----------------------------------------------------------------------
# 4b. v5.69: the BREAK veto is DECOUPLED from the entry, back to the far edge
# ----------------------------------------------------------------------
# v5.66 hung the break test on the entry, which only worked while the fill sat
# BEYOND the far edge (fill and invalidation were one event, so one anchor
# served both). v5.69 moved the entry to the OPPOSITE side of the zone to make it
# a legal resting limit, and that inverted the test: InpArmATR is 0.0, so a
# RESISTANCE arms the instant close1 < b.bottom, while the entry is a further
# `gap` BELOW b.bottom. An entry-anchored threshold
#   close1 > (b.bottom - 2.0*bh - 1.5*a) + 0.5*a
# is therefore ALREADY TRUE on the arming close: the block arms and vetoes
# itself VETO_BROKEN in one pass, and CancelOrdersForInvalidBlocks() drops the
# pending. Every setup dies the instant it is armed.
#
# The veto is put back on the PHYSICAL FAR edge, which sits on the opposite side
# of the zone from the arm trigger, so no single close can satisfy both:
#   NEAR edge -> ARM    (RESISTANCE: close1 < b.bottom)
#   FAR  edge -> BREAK  (RESISTANCE: close1 > b.top + 0.5*ATR)
#
# Nothing here is compile-visible: every spelling is valid MQL5, every one vetoes
# the block, and the block still arms either way. Only the LEVEL differs, so only
# a static pin over the literals catches a regression.
print("\n-- 4b. Break veto decoupled onto the physical far edge --")

BREAK_LEVEL = "double breakLevel = (b.type == BLOCK_SUPPORT) ? b.bottom : b.top;"
BREAK_LONG = ("b.type == BLOCK_RESISTANCE && close1 > breakLevel"
              " + ATRBufferBreak * atrNow")
BREAK_SHORT = ("b.type == BLOCK_SUPPORT && close1 < breakLevel"
               " - ATRBufferBreak * atrNow")
BREAK_BLOB = norm(BM_PROC or "")

check("ProcessBlocks declares the level from the PHYSICAL far edge, by block type",
      BREAK_LEVEL in BREAK_BLOB)
check("the break fires ATRBufferBreak*ATR past that level, both directions",
      BREAK_LONG in BREAK_BLOB and BREAK_SHORT in BREAK_BLOB)
# The v5.66 entry anchor IS the regression now: it must never come back, either
# as a declaration or as a call inside the break test.
check("the retired entry-anchored break level is gone",
      "double breakLevel = BlockEntryPrice(b, atrNow);" not in BM_C)
check("the entry helper is not called from the break test at all",
      "BlockEntryPrice(b, atrNow)" not in BM_C)
check("the retired inline far-edge literals stay retired (no double buffer)",
      "b.bottom - ATRBufferBreak" not in BM_C and
      "b.top + ATRBufferBreak" not in BM_C)
# Exactly ONE declaration, and only in ProcessBlocks. v5.80 deleted the
# intra-bar twin (UpdateIntraBar) that used to be checked here, so the guard
# now asserts against the whole file: one declaration plus the two predicate
# arms is 3 mentions, and the surrounding comments keep that exact so a stray
# identifier in prose fails here rather than passing silently.
check("exactly one break level is declared, and only in ProcessBlocks",
      BM_C.count("breakLevel") == 3,
      "count=%d" % BM_C.count("breakLevel"))
# Ordering inside ProcessBlocks is the whole defence: the block must already be
# armed when the break is evaluated, or the veto could fire on a block that
# never placed anything (and the arming close is the one that still looks past
# the NEAR edge).
_arm_at = (BM_PROC or "").find("b.isArmed = true")
_break_at = (BM_PROC or "").find("double breakLevel")
check("the arming test is evaluated BEFORE the break veto",
      _arm_at >= 0 and _break_at >= 0 and _arm_at < _break_at,
      "armed@%d break@%d" % (_arm_at, _break_at))
check("the decoupling from InpEntryStyle is stated in the source",
      "DECOUPLED" in BM_RAW and "honours no entry style" in BM_RAW)
check("ATRBufferBreak is the documented 0.5 ATR buffer",
      re.search(r"ATRBufferBreak\s*=\s*0\.5;", DEFS_T) is not None)

print("\n-- 5. Limit-only entry: no cross predicate --")

RETIRED_CROSS = "isLong ? (ask < vo.triggerPrice) : (bid > vo.triggerPrice)"

# A cross predicate only exists if something has to WATCH price cross a level.
# v5.72 handed that job to the broker: a limit either rests and fills, or it is
# vetoed. So the property is no longer the SIGN of a comparison but its
# ABSENCE -- plus the geometrical consequence, that the entry price is snapped
# and normalized onto the grid it must sit on before the send.
check("no cross predicate survives (the virtual engine is retired)",
      "vo.triggerPrice" not in OM_C and "vo.stopLoss" not in OM_C)
check("the retired v5.66 breakout-follow predicate is gone",
      RETIRED_CROSS not in OM_C)
check("the entry price is snapped before the send",
      "double entryPrice = SnapToTick(CalcEntryPrice(block, atr));" in (OM_PLACE or ""))
check("the resting price is normalized to the symbol's digit grid",
      "request.price     = NormalizeDouble(entryPrice, "
      "(int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));" in (OM_PLACE or ""))


# ----------------------------------------------------------------------
# 6. SL / TP wiring and seed anchors
# ----------------------------------------------------------------------
print("\n-- 6. SL/TP wiring --")

check("the stop is one slDist on the far side of the entry",
      "isLong ? entryPrice - slDist : entryPrice + slDist;" in (OM_PLACE or ""))
PLACE_W = norm(OM_PLACE or "")
# The quo/  column spacing is part of the shipped literal, so normalise
# whitespace before matching rather than pinning a column.
check("rrUnit is seeded from the SAME slDist the order used",
      "rrUnit = slDist;" in PLACE_W)
check("InpMaxRR is the one projection input, defaulting to 1.0 if unset",
      "tpRR = (InpMaxRR > 0.0) ? InpMaxRR : 1.0;" in PLACE_W)
# v5.80: the BlockManager no longer projects a target, so it no longer reads
# InpMaxRR at all. The input survives ONLY as the OrderManager's TP multiple --
# which is exactly the "one input" property this pair of checks protects.
check("the BlockManager no longer projects a TP from InpMaxRR",
      "tpRR" not in BM_C and "InpMaxRR" not in BM_C,
      "a BlockManager InpMaxRR read means a veto pass survived")
check("the SL and TP both land on the block for the veto to read back",
      "block.localSL = stopLoss;" in PLACE_W and
      "block.localTP = takeProfit;" in PLACE_W)
check("the basket 1R is the block's rrUnit, not the broker-adjusted stop",
      "block.rrUnit;" in OM_C and
      "m_activeTrade.rrUnit" in OM_C)

# ----------------------------------------------------------------------
# 7. The geometry behind the far-edge anchor, asserted numerically
# ----------------------------------------------------------------------
print("\n-- 7. Geometry identity --")


def realized(bh, atr, rr):
    """Transcribe the shipped TRUE deep front-edge geometry, near edge at 0.0.

    LONG is the whole story: DIR_SHORT is the mirror image, so one side
    proves both. nearEdge = 0, farEdge = +bh. The gap is measured OUT from the
    NEAR edge and lands on the OPPOSITE side of the zone (v5.69), so
    entry = -(2.0*bh + 1.5*atr) -- i.e. 2.0*bh + 1.5*atr BELOW the near edge,
    which is BELOW the market at arm time and therefore a legal BUY_LIMIT.
    slDist is untouched at bh + 0.5*atr, and the target stays anchored on the
    FAR edge: tp = farEdge + rr*slDist.
    """
    sl = bh + 0.5 * atr
    entry = -(2.0 * bh + 1.5 * atr)
    return (entry, entry - sl, bh + rr * sl, sl)   # entry, stop, tp, slDist


def realized_rr(bh, atr, rr):
    """The closed form of the reward-to-risk the ENTRY actually realizes.

    reward = tp - entry = 3*bh + 1.5*atr + rr*(bh + 0.5*atr) = (3 + rr)*slDist,
    risk = slDist, so x = rr + 3.0 EXACTLY -- independent of bh and of atr.
    That is why the retired 3.0 floor cannot apply: x > 3 for every positive
    rr, so the far-edge target can never land behind the entry.
    """
    entry, stop, tp, sl = realized(bh, atr, rr)
    return (tp - entry) / abs(entry - stop)


def break_level(bh, atr, buffer):
    """The v5.69 break barrier, near edge at 0.0 (RESISTANCE / LONG side).

    The veto is DECOUPLED from the entry and anchored on the PHYSICAL far edge
    (b.top for a RESISTANCE), firing `buffer*atr` above it.
    """
    return bh + buffer * atr


# The first three are inside the shipped configuration space (rr = 4.0); the
# fourth is `InpMaxRR = 2.0`, deliberately INCLUDED as the negative control that
# proves the retired 3.0 floor no longer binds. Under the v5.69 geometry it must
# realize a healthy 5.0R rather than a negative number.
CASES = [(1.0, 2.0, 4.0), (2.0, 1.0, 4.0), (0.5, 4.0, 4.0), (1.0, 1.0, 2.0)]
SHIPPED = CASES[:3]
risk_ok = anchor_ok = side_ok = bound_ok = True
for bh, atr, rr in CASES:
    entry, stop, tp, sl = realized(bh, atr, rr)
    if abs(abs(entry - stop) - sl) > 1e-12:
        risk_ok = False                      # risk must never drift
    # reward = (3 + rr) * slDist -- the far-edge identity.
    if abs((tp - entry) - (3.0 + rr) * sl) > 1e-12:
        anchor_ok = False
    # The entry sits 2.0*bh + 1.5*atr on the NEAR side (negative), the stop a
    # further slDist out, and BOTH are below the near edge at 0.0.
    if (abs(entry + (2.0 * bh + 1.5 * atr)) > 1e-12
            or abs(stop + (3.0 * bh + 2.0 * atr)) > 1e-12
            or not (entry < 0.0 and stop < entry)):
        side_ok = False
    # x = rr + 3.0 exactly, so it sits above 3.0 and never depends on bh/atr.
    if abs(realized_rr(bh, atr, rr) - (rr + 3.0)) > 1e-12:
        bound_ok = False
    if not realized_rr(bh, atr, rr) > 3.0:
        bound_ok = False

check("the stop always sits exactly one slDist from the entry", risk_ok)
check("reward from the ENTRY == (3 + InpMaxRR) * slDist", anchor_ok)
check("the entry and stop both sit on the NEAR (limit) side of the zone",
      side_ok)
check("realized RR == InpMaxRR + 3.0 exactly, always above 3.0", bound_ok)

entry, stop, tp, sl = realized(1.0, 2.0, 4.0)
HEAD = (tp - entry) / abs(entry - stop)
check("the documented 1.0-high / ATR 2.0 / RR 4.0 zone realizes 7.0R",
      abs(HEAD - 7.0) < 1e-12, "%.4fR" % HEAD)
check("the documented 2.0-high / ATR 1.0 / RR 4.0 zone realizes 7.0R",
      abs(realized_rr(2.0, 1.0, 4.0) - 7.0) < 1e-12)
check("the documented 0.5-high / ATR 4.0 / RR 4.0 zone realizes 7.0R",
      abs(realized_rr(0.5, 4.0, 4.0) - 7.0) < 1e-12)
check("the realized multiple is independent of zone height and ATR",
      abs(realized_rr(1e-9, 1.0, 4.0) - realized_rr(50.0, 0.01, 4.0)) < 1e-12)

# The retired 3.0 floor was an artifact of an entry anchored PAST the far edge,
# where the reward carried a (0.5*rr - 1.5) coefficient on the ATR term and went
# negative for rr < 3. The v5.69 entry removes that term entirely, so the floor
# no longer binds -- and must NOT be reintroduced. This is the negative control:
# InpMaxRR = 2.0, which the old geometry could not survive on a thin zone.
FLOOR = 3.0
check("every shipped case keeps the far-edge target ahead of the entry",
      all(realized_rr(bh, atr, rr) > 0 for bh, atr, rr in SHIPPED))
check("the retired floor no longer binds: a thin zone at InpMaxRR = 2.0 is "
      "still 5.0R ahead, not behind the entry",
      abs(realized_rr(1e-9, 1.0, 2.0) - 5.0) < 1e-12)
check("even an absurd InpMaxRR keeps the target ahead of the entry",
      realized_rr(1e-9, 1.0, 0.01) > 3.0)
MMAXX = re.search(r"InpMaxRR\s*=\s*([0-9.]+)", DEFS_T)
check("the shipped InpMaxRR is the documented 4.0",
      MMAXX is not None and abs(float(MMAXX.group(1)) - 4.0) < 1e-12,
      "InpMaxRR=%s" % (MMAXX.group(1) if MMAXX else "?"))

# The break barrier is DECOUPLED from the entry: it sits on the physical far
# edge, a full buffer*ATR beyond it, with no relationship to the entry at all.
# What must hold is that the armed close can never satisfy it.
_bufraw = re.search(r"ATRBufferBreak\s*=\s*([0-9.]+);", DEFS_T)
_buf = float(_bufraw.group(1)) if _bufraw else 0.5
check("the break barrier sits exactly ATRBufferBreak*ATR past the far edge",
      all(abs(break_level(bh, atr, _buf) - (bh + _buf * atr)) <= 1e-12
          for bh, atr, _ in CASES))
check("the break barrier sits past the physical far edge, not before it",
      all(break_level(bh, atr, _buf) > bh for bh, atr, _ in CASES),
      "buf=%.2f" % _buf)
# THE REGRESSION THAT MOTIVATES v5.69: an ENTRY-anchored break threshold lands
# BELOW the arming close for a LONG (close1 < b.bottom arms it), so the test is
# already true on the very close that armed the block. Measured as the entry's
# distance below the far edge -- which is exactly the margin by which the old
# threshold sat underneath the arming close.
ENTRY_ANCHORED = [bh - realized(bh, atr, 4.0)[0] for bh, atr, _ in CASES]
check("an entry-anchored threshold sits BELOW the arming close, so it fires on "
      "the arming bar itself (the bug v5.69 removed)",
      all(t > 0 for t in ENTRY_ANCHORED),
      "margins=%s" % ["%.3f" % t for t in ENTRY_ANCHORED])
check("the entry sits strictly below the near edge in every case, so the "
      "BUY_LIMIT is legal and rests physically",
      all(realized(bh, atr, 4.0)[0] < 0.0 for bh, atr, _ in CASES))


# ----------------------------------------------------------------------
# 8. Configuration: the anticipation entry is the SHIPPED default
# ----------------------------------------------------------------------
print("\n-- 8. Configuration --")

check("ENTRY_FRONT_EDGE is declared as the true deep front-edge style",
      "ENTRY_FRONT_EDGE" in DEFS_T and
      "TRUE DEEP FRONT-EDGE" in DEFS_T)
check("the retired anticipation wording is gone from the enum banner",
      "EXTREME FRONT-RUNNING ANTICIPATION" not in DEFS_T)
check("InpEntryStyle defaults to the front-edge entry",
      "InpEntryStyle = ENTRY_FRONT_EDGE" in DEFS_T)
check("the OrderManager maps that style to the front-edge entry",
      "if(InpEntryStyle == ENTRY_MIDPOINT)" in (OM_CALC or "") and
      "if(InpEntryStyle == ENTRY_MIDPOINT)" in (BM_BEP or ""))
check("neither twin consults ENTRY_FRONT_EDGE directly (it is the else-branch)",
      "ENTRY_FRONT_EDGE" not in (OM_CALC or "") and
      "ENTRY_FRONT_EDGE" not in (BM_BEP or ""))
check("InpMaxRR defaults to 4.0",
      re.search(r"InpMaxRR\s*=\s*4\.0", DEFS_T) is not None)
# v5.80: the Front-Run VETO is retired, but InpMaxRR is NOT -- it still sets the
# take-profit multiple, so it must keep its shipped 4.0 default. The veto's own
# switch must now be OFF and labelled retired, or an operator would reasonably
# believe the filter still runs.
check("InpMaxRR survives as the live TP multiple (not a filter knob)",
      "Take-profit projection" in DEFS_T)
check("the retired Front-Run veto switch is off and labelled RETIRED",
      re.search(r"InpUseFrontRunVeto\s*=\s*false", DEFS_T) is not None and
      re.search(r"InpUseFrontRunVeto[^\n]*RETIRED \(v5\.80\)", DEFS_T) is not None)
check("no BlockManager veto pass is gated on InpUseFrontRunVeto",
      _gates == 0)
check("the retired single-arg helper left no call site anywhere",
      "BlockEntryPrice(b)" not in BM_C and
      "CalcEntryPrice(block)" not in OM_C)


# ----------------------------------------------------------------------
# 9. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- 9. Version stamps --")


def rel_parts(stamps):
    if not stamps:
        return (0, 0)
    major, minor = sorted(set(stamps))[0].split(".")
    return (int(major), int(minor))


STAMPS = re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T)
CURRENT = sorted(set(STAMPS))[0] if STAMPS else "?"

check("OttoDefines.mqh carries exactly one version stamp",
      len(set(STAMPS)) == 1, "found: %s" % sorted(set(STAMPS)))
missing = [f for f in ALL_FILES
           if not re.search(r'#property version\s+"%s"' % re.escape(CURRENT),
                            read(os.path.join(ROOT, f)))]
check("all %d shipped sources carry one common stamp" % len(ALL_FILES),
      not missing, "missing: %s" % ", ".join(missing))
check("the common stamp is v5.80 or later", rel_parts(STAMPS) >= (5, 80),
      "stamps=%s" % sorted(set(STAMPS)))
check("the two-twin invariant is justified in BOTH files",
      "the Front-Run veto would judge a different price" in BM_RAW and
      "the Front-Run veto would judge a different price" in OM_RAW,
      "the 'must stay identical' rationale is missing")
check("the source states WHY the shift had to reach the OrderManager",
      "GetATRAtShift(1) -- the LAST CLOSED bar" in BM_RAW)
check("the startup banner names the current release",
      ("OTTO EA v%s" % CURRENT) in MAIN_T)


# ----------------------------------------------------------------------
# 10. v5.69: journal geometry synced to the true deep front-edge model
# ----------------------------------------------------------------------
# The journal's per-direction captions are printed strings: nothing ties them to
# BlockEntryPrice() or CalcSLDistance(), so they drift in silence. v5.57..v5.68
# described the entry as sitting BEYOND the far edge and the SL as "at Far Edge
# +/- 1.0*ATR"; v5.69 puts the entry on the near side, 2.0*H + 1.5*ATR out, with
# the SL a full 1R behind it. The wick H/L fields are pinned here too because
# nothing reads them -- a regression there is invisible at runtime.
print("\n-- 10. Journal & wick geometry sync --")

check("the journal names the true deep front-edge geometry per direction",
      JRN_RAW.count("SUPPORT (Deep Front-Edge Anticipation / Sell from Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)") == 2 and
      JRN_RAW.count("RESISTANCE (Deep Front-Edge Anticipation / Buy from Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)") == 2,
      "S=%d R=%d" % (JRN_RAW.count("Sell from Near Edge + 2.0*H + 1.5*ATR"),
                     JRN_RAW.count("Buy from Near Edge - 2.0*H - 1.5*ATR")))
# v5.71: the entry captions moved onto the model's canonical vocabulary. The
# retired "Front-Run Breakdown/Breakout" phrasing described an entry that has
# not shipped since v5.66, so it must not survive -- while "Front-Run veto"
# legitimately remains, because the veto is still a front-run veto.
check("the retired Front-Run entry captions are gone",
      "Front-Run Breakdown" not in JRN_RAW and
      "Front-Run Breakout" not in JRN_RAW)
check("the front-run VETO wording is retained",
      "Front-Run veto anchor" in JRN_RAW)
# The retired v5.66/v5.67 captions described a far-edge-anchored stop. That is
# no longer where the stop lives, so they must not survive anywhere.
check("the retired far-edge SL captions are gone",
      "SL at Far Edge" not in JRN_RAW)
check("the retired inverted near-edge signs are gone",
      "Sell at Near Edge - 2.0*H" not in JRN_RAW and
      "Buy at Near Edge + 2.0*H" not in JRN_RAW)
check("the armed header prints the deep gap, not the retired 0.5*ATR offset",
      JRN_RAW.count("(Deep Front-Edge: near edge +/- 2.0*H +/- 1.5*ATR)") == 1 and
      "near edge +/- 0.5*ATR" not in JRN_RAW)
check("the retired 'Aggressive' block wording is gone",
      "Aggressive" not in JRN_RAW)

# The printed geometry is a DERIVED fact, not a caption. Derive BOTH sides from
# the shipped arithmetic so a future re-anchor has to update the strings too.
def geometry(bh, atr, rr, is_long):
    """Signed geometry, far edge pinned at 0.0, v5.69 signs.

    LONG  (RESISTANCE): far edge block.top = 0.0, near edge block.bottom = -bh.
                       entry = near - gap = -bh - 2.0*bh - 1.5*atr
                       stop  = entry - slDist
    SHORT (SUPPORT)   : far edge block.bottom = 0.0, near edge block.top = +bh.
                       entry = near + gap = +bh + 2.0*bh + 1.5*atr
                       stop  = entry + slDist
    Returns (entry, stop, target, slDist); entry-stop is always exactly slDist
    and both sit on the NEAR side, the mirror of the target on the far side.
    """
    gap = 2.0 * bh + 1.5 * atr
    sl = bh + 0.5 * atr
    near = -bh if is_long else bh
    entry = (near - gap) if is_long else (near + gap)
    stop = (entry - sl) if is_long else (entry + sl)
    far = bh if is_long else -bh
    target = (far + rr * sl) if is_long else (far - rr * sl)
    return (entry, stop, target, sl)


check("entry<->SL is exactly 1R in both directions, both on the NEAR side, "
      "the target on the FAR side",
      all(abs(abs(g[0] - g[1]) - g[3]) <= 1e-12
          # LONG works below a far edge pinned at 0.0, SHORT above it, so the
          # whole geometry mirrors: the entry/stop sit OUTSIDE the near edge and
          # the target on the opposite side of the entry.
          and (g[0] < 0.0 if side else g[0] > 0.0)
          and (g[1] < g[0] if side else g[1] > g[0])
          and (g[2] > g[0] if side else g[2] < g[0])
          for side in (True, False)
          for bh, atr, rr in CASES
          for g in [geometry(bh, atr, rr, side)]))
check("the journal's per-direction offsets match that arithmetic",
      "+ 2.0*H + 1.5*ATR" in JRN_RAW and "- 2.0*H - 1.5*ATR" in JRN_RAW and
      JRN_RAW.count("SL 1R above entry") == 2 and
      JRN_RAW.count("SL 1R below entry") == 2)

# Both journal blocks (LogSetupArmed and LogEntry) must print the FULL zone
# marks -- Mid and Height were never shown, so an operator could not see the
# rrUnit derivation (slDist = blockHeight + 0.5*ATR) from the log alone.
check("both journal blocks print the full zone marks (Mid + Height)",
      JRN_RAW.count('" | Mid=" + FmtPrice(blk.midpoint) + " | Height=" + FmtPrice(blk.blockHeight)') == 2)
check("the armed header prints 1R and the projected target",
      'FmtPrice(blk.rrUnit)' in JRN_RAW and
      "Projected Target  : " in JRN_RAW and "FmtPrice(blk.localTP)" in JRN_RAW)
check("the entry record prints the target alongside 1R",
      '" | 1R = " + FmtPrice(blk.rrUnit) + " | Target = " + FmtPrice(blk.localTP)' in JRN_RAW)

# The Wick H/L lines are now conditional, mirroring the entry block: a block
# whose wick times are unpopulated prints no bogus zero row.
check("both wick H/L blocks are guarded on populated wick times",
      JRN_RAW.count("if(blk.wick1Time > 0 && blk.wick2Time > 0)") == 2)

# All FOUR block-creation passes (live + warm-up, resistance + support) must
# populate the full W1/W2 candle, or the journal shows zeros for the half of
# the blocks built by the other pass.
check("all four block-creation passes populate the full W1/W2 candle",
      len(re.findall(r"nb\.wick1\.highPrice\s*=", BM_C)) == 4 and
      len(re.findall(r"nb\.wick1\.lowPrice\s*=", BM_C)) == 4 and
      len(re.findall(r"nb\.wick2\.highPrice\s*=", BM_C)) == 4 and
      len(re.findall(r"nb\.wick2\.lowPrice\s*=", BM_C)) == 4,
      "H=%d/%d L=%d/%d" % (len(re.findall(r"nb\.wick1\.highPrice\s*=", BM_C)),
                           len(re.findall(r"nb\.wick2\.highPrice\s*=", BM_C)),
                           len(re.findall(r"nb\.wick1\.lowPrice\s*=", BM_C)),
                           len(re.findall(r"nb\.wick2\.lowPrice\s*=", BM_C))))

# The far-edge anchor is now asserted on the ONE surviving projection site.
# v5.72 retired the virtual fire, which re-derived localTP with identical
# arithmetic; PlaceOrArmOrder is the only writer of block.localTP left, so the
# "twin" property collapses into "the single writer is on the far edge".
check("PlaceOrArmOrder anchors the target on the FAR edge",
      re.search(r"double\s+farEdge\s*=\s*isLong\s*\?\s*MathMax\(block\.top,\s*block\.bottom\)",
                PLACE_W) is not None and
      re.search(r"double\s+takeProfit\s*=\s*isLong\s*\?\s*farEdge\s*\+\s*tpRR\s*\*\s*slDist",
                PLACE_W) is not None and
      "block.localTP = takeProfit;" in PLACE_W)
check("exactly one writer of block.localTP remains in the OrderManager",
      len(re.findall(r"block\.localTP\s*=[^=]", OM_C)) == 1,
      "found %d" % len(re.findall(r"block\.localTP\s*=[^=]", OM_C)))
check("the retired trigger-anchored projection is gone from the OrderManager",
      "vo.triggerPrice + slDist" not in (OM_C or "") and
      "vo.triggerPrice - slDist" not in (OM_C or ""))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.57 FRONT-RUNNING ANTICIPATION ENTRY - STATIC PROBE")
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
