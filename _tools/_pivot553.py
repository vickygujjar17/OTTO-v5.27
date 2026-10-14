"""
One-shot v5.53 STREAMLINED-LIFECYCLE patch for the OTTO EA tree.

Completes the v5.52 PURE CONTINUATION pivot. v5.52 retired the two-phase
MACHINERY (the fade, the reversal promotion, the entry shift), but it left the
ORDER LIFECYCLE still treating a filled block as reusable: both
CheckPendingOrderFills() and CheckVirtualTriggers() CLEARED isTriggered /
hasPlacedOrder and zeroed deleteOnBarTime after a fill, so a block that had
already fired its one entry stayed alive -- and re-armable -- until the funnel
eventually aged it out.

v5.53 makes the lifecycle match the engine it serves: a block fires its ONE
continuation entry and is retired on the spot (latched dead + deleteOnBarTime
stamped). It also PURGES the now-dead SSniperBlock::touches counter, along with
the comments that still claimed it was the duplicate-tranche shield. It never
was: the real shield is the isTriggered / hasPlacedOrder latch these very edits
set, plus CanPlaceForDirection() for the same-direction case.

WHY A SCRIPT RATHER THAN HAND EDITS
    Same reason as _pivot552.py: the regions must keep strict CRLF, no BOM and
    every Unicode code point (verify_integrity.py compares HEAD against the
    working tree). Every anchor must match EXACTLY ONCE or the script aborts
    wholesale, so it can never half-apply.

WHAT IT TOUCHES (9 regions across 3 files)
    OttoDefines.mqh
        1. the zone -> direction banner: stop naming a counter that is gone
        2. DELETE the touches field and its 8-line comment
    COttoBlockManager.mqh
        3. the direction twin's banner
        4. the block-funnel banner
    COttoOrderManager.mqh
        5. SeedActiveTradeFromBlock() banner
        6. the hourly report's phase-tag comment
        7. the "ADVANCE the two-phase lifecycle" section header
        8. CheckPendingOrderFills(): clear -> RETIRE
        9. CheckVirtualTriggers(): clear -> RETIRE

NOT TOUCHED (deliberately)
    * SSniperBlock::conversionReason -- also vestigial post-v5.52, but it is a
      separate concern with its own change; do not bundle it here.
    * the five InpRev* flags and InpShiftReversalEntry -- kept as DECLARED no-op
      inputs so a saved .set from a two-phase build still loads (v5.52 rule).
    * the version stamps and README -- _bump_version.py owns all of those.

Reports every changed region so the diff can be eyeballed before commit.
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- matcher ---------------------------------------------------------------
# Matching collapses runs of whitespace (leading, trailing AND interior) so the
# anchors are immune to indentation/alignment churn while still being exact
# about the TOKEN sequence, which is what we actually mean to pin. A region
# must match at exactly ONE place or the script aborts: an ambiguous anchor is
# far more dangerous than a missing one.

def _norm(s):
    # Collapse all whitespace runs, and make the padding immediately before or
    # after a banner's '|' insignificant, so a hand-written banner string matches
    # the source's own padding instead of silently differing by one space.
    return " ".join(s.split()).replace(" |", "|").replace("| ", "|")


def sub(lines, expect, new, tag):
    n = len(expect)
    if n == 0:
        raise SystemExit("EMPTY ANCHOR [%s]" % tag)
    want = [_norm(e) for e in expect]
    hits = []
    for i in range(len(lines) - n + 1):
        if all(_norm(lines[i + k]) == want[k] for k in range(n)):
            hits.append(i)
    if len(hits) != 1:
        raise SystemExit("ANCHOR %s [%s]: %d matches" %
                         ("NOT FOUND" if not hits else "AMBIGUOUS", tag, len(hits)))
    i = hits[0]
    lines[i:i + n] = new
    print("  ok  %-34s -%d/+%d lines" % (tag, n, len(new)))
    return i


def mql(path):
    """Line list for an MQL source: strict CRLF, split on the CRLF itself."""
    with io.open(path, encoding="utf-8", newline="") as f:
        raw = f.read()
    if "\r\n" not in raw:
        raise SystemExit("%s is not CRLF" % path)
    if "\n" in raw.replace("\r\n", ""):
        raise SystemExit("%s has a bare LF" % path)
    if "\r" in raw.replace("\r\n", ""):
        raise SystemExit("%s has a bare CR" % path)
    return raw.split("\r\n")


def save_mql(path, lines):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines))


# --- OttoDefines.mqh -------------------------------------------------------

def patch_defines():
    path = os.path.join(ROOT, "OttoDefines.mqh")
    print("OttoDefines.mqh")
    L = mql(path)

    # 1. The zone -> direction banner. Stops naming a counter that no longer
    #    exists anywhere in the tree.
    sub(L, [
        "// Block polarity as DISCOVERED by the pattern engine. Support/Resistance here",
        "// describe the ZONE, NOT the trade direction. The zone -> direction mapping is",
        "// owned by COttoOrderManager::GetDirectionForBlock() and is UNCONDITIONAL in",
        "// v5.52's PURE CONTINUATION engine: a RESISTANCE zone trades LONG (a breakout",
        "// above it) and a SUPPORT zone trades SHORT (a breakdown below it), which is",
        "// exactly what Pine's can_place gate states (long & resistance, short &",
        "// support). The old two-phase fade -- the first fill a bounce, only a reclaimed",
        "// zone the flip -- is retired, so this mapping no longer reads block.touches.",
        "// Enum VALUES are persisted and compared, so they must never be renumbered.",
    ], [
        "// Block polarity as DISCOVERED by the pattern engine. Support/Resistance here",
        "// describe the ZONE, NOT the trade direction. The zone -> direction mapping is",
        "// owned by COttoOrderManager::GetDirectionForBlock() and is UNCONDITIONAL in",
        "// v5.52's PURE CONTINUATION engine: a RESISTANCE zone trades LONG (a breakout",
        "// above it) and a SUPPORT zone trades SHORT (a breakdown below it), which is",
        "// exactly what Pine's can_place gate states (long & resistance, short &",
        "// support). The old two-phase fade -- the first fill a bounce, only a reclaimed",
        "// zone the flip -- is retired, so this mapping reads no lifecycle counter at",
        "// all. Enum VALUES are persisted and compared, never renumbered.",
    ], "defines.enum")

    # 2. PURGE the field. v5.52 kept it claiming the v5.28 duplicate-tranche
    #    shield reads it; that was false in two ways -- nothing read it at all,
    #    and the lifecycle edits below now latch a filled block with isTriggered
    #    / hasPlacedOrder, which is the shield that actually refuses a second
    #    tranche. SSniperBlock is never raw-serialised (no FileWriteStruct /
    #    FileReadStruct / sizeof anywhere in the tree), so removing the member
    #    cannot churn a persisted layout.
    sub(L, [
        "   // v5.34: lifecycle counter of FILLS CONSUMED on this block: 0 = never filled,",
        "   // 1 = the single continuation entry has filled, 2+ = a duplicate fill, which",
        "   // the v5.28 one-tranche-per-block shield refuses. v5.52: the two-phase engine",
        "   // this counter once encoded is gone, but the field is KEPT because that shield",
        "   // reads it -- retiring it would let one block fill a SECOND tranche. It no",
        "   // longer influences direction; see COttoOrderManager::GetDirectionForBlock().",
        "   // Zeroed by ZeroMemory() at every block-creation site (COttoBlockManager).",
        "   int               touches;",
    ], [
        "   // v5.34's touches counter -- the two-phase lifecycle cursor, later kept as a",
        "   // 'fill counter' -- is RETIRED here. Nothing read it once v5.52 removed the",
        "   // phase branches, and the one-shot latch that actually refuses a duplicate",
        "   // tranche is isTriggered / hasPlacedOrder (set on the fill, cleared only by",
        "   // block deletion), so the field carried dead weight and a false comment.",
    ], "defines.touches")

    save_mql(path, L)


# --- COttoBlockManager.mqh -------------------------------------------------

def patch_block_manager():
    path = os.path.join(ROOT, "COttoBlockManager.mqh")
    print("COttoBlockManager.mqh")
    L = mql(path)

    # 3. The direction twin's banner.
    sub(L, [
        "//| v5.52 PURE CONTINUATION: the mapping is UNCONDITIONAL and reads no|",
        "//| phase. Pine's can_place gate is 'flat OR (long & resistance) OR  |",
        "//| (short & support)', so a RESISTANCE zone is traded LONG (a breakout|",
        "//| above it) and a SUPPORT zone is traded SHORT (a breakdown below  |",
        "//| it). The v5.34 two-phase fade -- the first fill a bounce, only a |",
        "//| reclaimed zone the flip -- is retired, so block.touches no longer|",
        "//| enters this decision at all.                                     |",
    ], [
        "//| v5.52 PURE CONTINUATION: the mapping is UNCONDITIONAL and reads  |",
        "//| no phase and no lifecycle counter. Pine's can_place gate is      |",
        "//| 'flat OR (long & resistance) OR (short & support)', so a         |",
        "//| RESISTANCE zone is traded LONG (a breakout above it) and a       |",
        "//| SUPPORT zone is traded SHORT (a breakdown below it). The v5.34   |",
        "//| two-phase fade -- the first fill a bounce, only a reclaimed zone |",
        "//| the flip -- is retired with the counter that encoded it.         |",
    ], "bm.dir")

    # 4. The block-funnel banner. The funnel still owns retirement; say so
    #    without naming the deleted counter.
    sub(L, [
        "//| v5.52 -- the state machine is PHASE-FREE. v5.49's touches-driven |",
        "//| geometry inversion existed only to measure a reclaimed zone against|",
        "//| its own reversal side; there is no reversal any more, so hasExited,|",
        "//| arming and break/flip are each a single unconditional test that  |",
        "//| never reads block.touches. See the break block below: its test is|",
        "//| the OPPOSITE edge from arming, on purpose.                       |",
    ], [
        "//| v5.52 -- the state machine is PHASE-FREE. v5.49's geometry  |",
        "//| inversion existed only to measure a reclaimed zone against  |",
        "//| its own reversal side; there is no reversal any more, so    |",
        "//| hasExited, arming and break/flip are each a single test     |",
        "//| that reads no lifecycle counter. See the break block below: |",
        "//| its test is the OPPOSITE edge from arming, on purpose.      |",
    ], "bm.funnel")

    save_mql(path, L)


# --- COttoOrderManager.mqh -------------------------------------------------

def patch_order_manager():
    path = os.path.join(ROOT, "COttoOrderManager.mqh")
    print("COttoOrderManager.mqh")
    L = mql(path)

    # 5. SeedActiveTradeFromBlock() banner. The point it makes -- the seed must
    #    describe the order that was SENT -- is still exactly right; only the
    #    reason the default is safe has changed (the mapping is unconditional,
    #    full stop).
    sub(L, [
        "      // The direction this basket is seeded with. v5.52 makes the default path",
        "      // trivially correct: GetDirectionForBlock() is now UNCONDITIONAL and no",
        "      // longer reads block.touches, so a fill that consumes the touch first",
        "      // still resolves the SAME side the MARKET order executed. overrideDir is",
        "      // kept as belt-and-braces -- the virtual fire already knows the exact",
        "      // side it sent, so passing it costs nothing and cannot desync the trail,",
        "      // the 1R basis, the journal or the High Table mirror for the leg.",
    ], [
        "      // The direction this basket is seeded with. v5.52 makes the default path",
        "      // trivially correct: GetDirectionForBlock() is UNCONDITIONAL on the zone",
        "      // type alone, so it resolves the SAME side the MARKET order executed no",
        "      // matter what the fill changed on the block. overrideDir is kept as",
        "      // belt-and-braces -- the virtual fire already knows the exact side it",
        "      // sent, so passing it costs nothing and cannot desync the trail, the 1R",
        "      // basis, the journal or the High Table mirror for the leg.",
    ], "om.seed")

    # 6. The hourly report's phase tag. Says what the label IS, without
    #    appealing to a deleted discriminator.
    sub(L, [
        "      // v5.51 Part 3 -- R/C PHASE TAG (hourly report), v5.52 — every armed",
        "      // setup is a CONTINUATION now: there is no reversal phase left, so this",
        "      // opens with the constant label instead of discriminating on block.touches.",
        "      // The per-serial walk is kept because it is what proves each armed order",
        "      // still resolves to a live block.",
    ], [
        "      // v5.51 Part 3 -- R/C PHASE TAG (hourly report). v5.52 made every armed",
        "      // setup a CONTINUATION, so there is no phase left to discriminate on and",
        "      // the report opens with one constant label. The per-serial walk is kept",
        "      // because it proves each armed order still resolves to a live block.",
    ], "om.hourly")

    # 7. CheckPendingOrderFills() -- PHYSICAL continuation entry filled.
    #    THE lifecycle edit. v5.52 still CLEARED the latch here, which left a
    #    filled block alive and re-armable; v5.53 RETIRES it instead. Latching
    #    isTriggered is what actually enforces v5.52's own promise (one entry
    #    per block) now that the phase cursor is gone -- and it is also what
    #    closes the ported bug of 2026-05-06, where a resting limit could be
    #    re-placed on the NEXT TICK after its fill and take a second position.
    #    deleteOnBarTime is stamped from the CURRENT bar so COttoBlockManager::
    #    ProcessBlocks deletes the block once iTime(0) moves on -- Pine's own
    #    delete_on_bar semantics. cancelling the now-consumed pending order is
    #    deliberately NOT done here (a filled/deleted ticket no longer exists);
    #    CancelOrdersForInvalidBlocks() owns pending cleanup.
    sub(L, [
        "            // ---- ADVANCE the two-phase lifecycle ------------------------",
        "            int bi = m_blockManager.FindBlockIndexByTicket(ticket);",
        "            if(bi >= 0)",
        "              {",
        "               SSniperBlock mod;",
        "               if(m_blockManager.GetBlockAt(bi, mod))",
        "                 {",
        "                  int prevTouch = mod.touches;",
        "                  mod.touches += 1;",
        "                  mod.limitOrderTicket   = 0;",
        "                  mod.pendingOrderCancel = false;",
        "                  mod.isTriggered        = false;",
        "                  mod.hasPlacedOrder     = false;",
        "                  mod.isArmed            = false;",
        "                  mod.hasExited          = false;",
        "                  mod.deleteOnBarTime    = 0;",
        "                  m_blockManager.SetBlockAt(bi, mod);",
        "                  if(EnableLogging)",
        "                     Print(\"[OrderManager] TOUCH \", mod.touches, \" CONSUMED: block \", mod.tradeId,",
        "                           \" (prev=\", prevTouch, \") -> continuation entry done; block is one-shot\");",
    ], [
        "            // ---- RETIRE the block: one entry per block ------------------",
        "            int bi = m_blockManager.FindBlockIndexByTicket(ticket);",
        "            if(bi >= 0)",
        "              {",
        "               SSniperBlock mod;",
        "               if(m_blockManager.GetBlockAt(bi, mod))",
        "                 {",
        "                  // RETIRE the block: its ONE continuation entry has filled,",
        "                  // so latch it dead and date it for deletion. isTriggered",
        "                  // stops PlaceOrArmOrder and the virtual engine from arming",
        "                  // a second tranche; hasPlacedOrder stops the duplicate;",
        "                  // deleteOnBarTime lets ProcessBlocks delete it next bar.",
        "                  mod.limitOrderTicket   = 0;",
        "                  mod.pendingOrderCancel = false;",
        "                  mod.priceAbortLogged   = false;",
        "                  mod.isTriggered        = true;",
        "                  mod.hasPlacedOrder     = true;",
        "                  mod.isArmed            = false;",
        "                  mod.hasExited          = false;",
        "                  mod.deleteOnBarTime    = iTime(m_symbol, PERIOD_CURRENT, 0);",
        "                  m_blockManager.SetBlockAt(bi, mod);",
        "                  if(EnableLogging)",
        "                     Print(\"[OrderManager] FILL RETIRED: block \", mod.tradeId,",
        "                           \" -> continuation entry done; block is one-shot\");",
    ], "om.fill.retire")

    # 8. CheckVirtualTriggers() -- VIRTUAL continuation entry fired. Same
    #    retirement as region 7, on the other execution route. The seedDir
    #    capture at the top of this block and the SeedActiveTradeFromBlock()
    #    call below are both LEFT ALONE: the capture must still happen before
    #    the block is mutated, and the seed is still taken from seedDir, which
    #    is what the MARKET order actually executed on. (v5.52 had a test
    #    pinning exactly that ordering.)
    sub(L, [
        "         // Consume the entry: this block's ONE continuation entry has filled.",
        "         // touches is no longer a phase cursor -- it is the counter the v5.28",
        "         // one-tranche-per-block shield reads, so a duplicate fill (already",
        "         // >= 1) is refused upstream. Mirrors CheckPendingOrderFills().",
        "         block.touches            += 1;",
        "         block.limitOrderTicket    = 0;",
        "         block.pendingOrderCancel  = false;",
        "         block.priceAbortLogged    = false;",
        "         block.isTriggered         = false;",
        "         block.hasPlacedOrder      = false;",
        "         block.isArmed             = false;",
        "         block.hasExited           = false;",
        "         block.deleteOnBarTime     = 0;",
        "         m_blockManager.SetBlockAt(bi, block);",
    ], [
        "         // RETIRE the block: its ONE continuation entry has filled (this",
        "         // fire IS that entry), so latch it dead and date it for deletion.",
        "         // Mirrors CheckPendingOrderFills(). isTriggered stops the engine",
        "         // from arming a second tranche; hasPlacedOrder stops the duplicate;",
        "         // deleteOnBarTime lets ProcessBlocks delete it next bar. seedDir",
        "         // above is still threaded into SeedActiveTradeFromBlock() below, so",
        "         // the basket is seeded from the side the MARKET order executed.",
        "         block.limitOrderTicket    = 0;",
        "         block.pendingOrderCancel  = false;",
        "         block.priceAbortLogged    = false;",
        "         block.isTriggered         = true;",
        "         block.hasPlacedOrder      = true;",
        "         block.isArmed             = false;",
        "         block.hasExited           = false;",
        "         block.deleteOnBarTime     = iTime(m_symbol, PERIOD_CURRENT, 0);",
        "         m_blockManager.SetBlockAt(bi, block);",
    ], "om.virt.retire")

    save_mql(path, L)


# --- driver ----------------------------------------------------------------

def main():
    print("=" * 74)
    print("v5.53 BLOCK-LIFECYCLE STREAMLINING + touches PURGE")
    print("=" * 74)
    patch_defines()
    patch_block_manager()
    patch_order_manager()
    print()
    print("patch applied cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
