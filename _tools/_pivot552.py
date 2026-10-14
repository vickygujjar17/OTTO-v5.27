"""
One-shot v5.52 PURE-CONTINUATION pivot patch for the OTTO EA tree.

Collapses the two-phase "bounce then reverse" block lifecycle into a single,
phase-free AGGRESSIVE CONTINUATION (breakout / breakdown) engine.

WHY A SCRIPT RATHER THAN HAND EDITS
    The pivot touches ~25 coordinated regions across five sources and must
    preserve strict CRLF, no BOM and every Unicode box-drawing / em-dash code
    point (verify_integrity.py compares HEAD against the working tree). A
    hand edit of that shape is a coin flip on EOL and encoding. This script
    follows the _bumpNNN.py convention already in this directory: it rewrites
    ONLY the regions it is given, asserts on the exact text it expects to
    replace, and aborts wholesale if any anchor is missing or ambiguous, so it
    can never half-apply.

THE MAPPING (the one idea this whole patch implements)
    Pine's can_place gate is: flat OR (long & resistance) OR (short & support).
    A RESISTANCE zone therefore trades LONG and a SUPPORT zone trades SHORT --
    a breakout above resistance and a breakdown below support. The two-phase
    lifecycle that v5.34 layered on top made a zone's FIRST fill the opposite
    (a bounce fade) and only a reclaimed zone the flip. v5.52 retires the
    fade: the mapping is unconditional and reads no phase counter.

WHAT IS RETIRED (the reversal machinery existed only to serve the fade)
    * COttoBlockManager::TryConvertToReversal() + its 6 call sites
    * COttoBlockManager::SyncReversalCluster() + its 2 call sites
    * the five InpRev* inputs and InpShiftReversalEntry (neutralised as
      documented no-ops rather than deleted, so saved .set files still load)
    * SSniperBlock::conversionReason (field kept, nothing writes it)
    * the InpShiftReversalEntry outer-boundary entry shift (both twins)
    * every touches-driven branch in hasExited / arming / break-flip / the two
      Front-Run projections

WHAT IS KEPT
    * block.touches and the ++ increments -- they are load-bearing for the
      v5.28 duplicate-tranche shield (a block that has filled cannot fill a
      second tranche). Retiring the field would re-open that bug.
    * the Pine break detector VERBATIM (break is an S/R-breach / polarity-flip
      detector, not entry invalidation), deleting only its dead `else`.
    * CanPlaceForDirection() -- it already reads the direction chokepoint, so
      it becomes the correct Pine gate with no change.
    * the user's isBroken snippet, untouched.

KNOWN, DELIBERATELY UNFIXED
    otto.mq5 HiveMind(): the two DeleteBlockType() arguments look INVERTED
    against their own comment and their own "kept Support" log. That is a
    PRE-EXISTING inversion, independent of this pivot (Part 2 does not change
    how a fresh zone maps), so it is recorded in the code and left verbatim.

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


def read(path, newline):
    with io.open(path, encoding="utf-8", newline="") as f:
        return f.read()

def write(path, text, newline):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def mql(path):
    """Line list for an MQL source: strict CRLF, split on the CRLF itself."""
    raw = read(path, "")
    if "\r\n" not in raw:
        raise SystemExit("%s is not CRLF" % path)
    if "\n" in raw.replace("\r\n", ""):
        raise SystemExit("%s has a bare LF" % path)
    return raw.split("\r\n")


def save_mql(path, lines):
    write(path, "\r\n".join(lines), "")




def border(indent=0):
    return " " * indent + "//+" + "-" * 65 + "+"


BR = 'input group "══════════════════════════════════════════════════"'


# --- OttoDefines.mqh -------------------------------------------------------

def patch_defines():
    path = os.path.join(ROOT, "OttoDefines.mqh")
    print("OttoDefines.mqh")
    L = mql(path)

    # 1. The zone -> direction contract, restated unconditionally.
    sub(L, [
        "// Block polarity as DISCOVERED by the pattern engine. Support/Resistance here",
        "// describe the ZONE, NOT the trade direction. The zone -> direction mapping is",
        "// owned by COttoOrderManager::GetDirectionForBlock() and is PHASE-AWARE per the",
        "// two-phase lifecycle: a fresh zone (touches == 0) is a PHASE 1 BOUNCE",
        "// (Support -> BUY, Resistance -> SELL); a zone that has consumed Touch 1",
        "// (touches >= 1) is a PHASE 2 REVERSAL that trades the S/R flip",
        "// (Support -> SELL, Resistance -> BUY). Enum VALUES are persisted and compared,",
        "// so they must never be renumbered.",
        "enum ENUM_BLOCK_TYPE",
        "  {",
        "   BLOCK_RESISTANCE = 0,  // Resistance zone (Phase 1 -> SELL, Phase 2 -> BUY)",
        "   BLOCK_SUPPORT    = 1   // Support zone    (Phase 1 -> BUY,  Phase 2 -> SELL)",
    ], [
        "// Block polarity as DISCOVERED by the pattern engine. Support/Resistance here",
        "// describe the ZONE, NOT the trade direction. The zone -> direction mapping is",
        "// owned by COttoOrderManager::GetDirectionForBlock() and is UNCONDITIONAL in",
        "// v5.52's PURE CONTINUATION engine: a RESISTANCE zone trades LONG (a breakout",
        "// above it) and a SUPPORT zone trades SHORT (a breakdown below it), which is",
        "// exactly what Pine's can_place gate states (long & resistance, short &",
        "// support). The old two-phase fade -- the first fill a bounce, only a reclaimed",
        "// zone the flip -- is retired, so this mapping no longer reads block.touches.",
        "// Enum VALUES are persisted and compared, so they must never be renumbered.",
        "enum ENUM_BLOCK_TYPE",
        "  {",
        "   BLOCK_RESISTANCE = 0,  // Resistance zone (-> LONG: breakout above it)",
        "   BLOCK_SUPPORT    = 1   // Support zone    (-> SHORT: breakdown below it)",
    ], "defines.enum")

    # 2. touches survives, but as a fill counter rather than a phase cursor.
    sub(L, [
        "   // v5.34: two-phase lifecycle counter. 0 = fresh/unarmed, 1 = Touch 1 (the",
        "   // normal entry) consumed, 2 = Touch 2 (the shifted reversal) consumed. It is",
        "   // zeroed by ZeroMemory() at every block-creation site (see COttoBlockManager).",
        "   int               touches;",
    ], [
        "   // v5.34: lifecycle counter of FILLS CONSUMED on this block: 0 = never filled,",
        "   // 1 = the single continuation entry has filled, 2+ = a duplicate fill, which",
        "   // the v5.28 one-tranche-per-block shield refuses. v5.52: the two-phase engine",
        "   // this counter once encoded is gone, but the field is KEPT because that shield",
        "   // reads it -- retiring it would let one block fill a SECOND tranche. It no",
        "   // longer influences direction; see COttoOrderManager::GetDirectionForBlock().",
        "   // Zeroed by ZeroMemory() at every block-creation site (COttoBlockManager).",
        "   int               touches;",
    ], "defines.touches")

    # 3. The continuation entry anchor: front edge, or the tool is dead on arrival.
    sub(L, [
        "input ENUM_ENTRY_STYLE InpEntryStyle = ENTRY_MIDPOINT;  // Midpoint or Front Edge",
    ], [
        "input ENUM_ENTRY_STYLE InpEntryStyle = ENTRY_FRONT_EDGE; // Midpoint or Front Edge",
    ], "defines.entrystyle")

    # 4. The shifted-reversal input is neutralised, not deleted (.set safety).
    sub(L, [
        "// v5.34: Shift Reversal entry. When true, Touch 2 of a zone is entered from its",
        '// OUTER boundary instead of the midpoint (the "shifted reversal" geometry).',
        "input bool     InpShiftReversalEntry = true;  // Shift Reversal (Touch 2) Entry to Outer Boundary",
    ], [
        "// v5.34: Shift Reversal entry -- the Touch 2 outer-boundary geometry.",
        "// v5.52: RETIRED. The pure-continuation engine has no Touch 2 (a continuation",
        "// is a single breakout/breakdown entry), so no consumer remains in the tree.",
        "// Kept as a DECLARED NO-OP so an old .set file still loads without an unknown-",
        "// input error; it changes no behaviour.",
        "input bool     InpShiftReversalEntry = false; // RETIRED (v5.52) - no-op, kept for .set compatibility",
    ], "defines.shiftentry")

    # 5. The five conversion flags become documented no-ops.
    sub(L, [
        'input group "  [12] VETO REVERSAL CONVERSIONS — v5.52"',
        BR,
        '// v5.34: the old [12] "INVERTED S/R + VIRTUAL ORDERS" experiment block was',
        "// retired. The zone -> direction mapping is now the standard PHASE-AWARE",
        "// two-phase lifecycle (see ENUM_BLOCK_TYPE and GetDirectionForBlock) and the",
        "// virtual-order engine was deleted. These five flags let a vetoed setup",
        "// be re-used as a REVERSAL candidate instead of being discarded. They are now",
        "// consumed by v5.34 Part 2: a convertible veto on a Phase 1 block (touches==0)",
        "// advances it to Touch 2 via COttoBlockManager::TryConvertToReversal instead of",
        "// deleting the zone outright.",
        "input bool     InpRevFrontRun = true;   // Convert Front-Run Veto to Phase 2 Reversal",
        "input bool     InpRevNearMiss = true;   // Convert Near-Miss Veto to Phase 2 Reversal",
        "input bool     InpRevStale    = true;   // Convert Stale Veto to Phase 2 Reversal",
        "input bool     InpRevMom      = true;   // Convert Momentum Veto to Phase 2 Reversal",
        "input bool     InpRevFvg      = true;   // Convert FVG Veto to Phase 2 Reversal",
    ], [
        'input group "  [12] RETIRED VETO REVERSAL CONVERSIONS — v5.52"',
        BR,
        "// v5.34 built five switches that let a vetoed Phase 1 setup be re-used as a",
        "// Phase 2 REVERSAL instead of being discarded.",
        "//",
        "// v5.52 RETIRES that machinery. The pure CONTINUATION engine has no second",
        "// phase to promote a block INTO, so COttoBlockManager::TryConvertToReversal()",
        "// and SyncReversalCluster() are gone and a convertible veto is now simply a",
        "// veto. The five inputs are KEPT as DECLARED NO-OPS rather than deleted, so an",
        "// old .set file from a two-phase build still loads instead of aborting on an",
        "// unknown input. Nothing in the tree reads them any longer.",
        "input bool     InpRevFrontRun = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility",
        "input bool     InpRevNearMiss = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility",
        "input bool     InpRevStale    = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility",
        "input bool     InpRevMom      = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility",
        "input bool     InpRevFvg      = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility",
    ], "defines.group12")

    save_mql(path, L)


# --- COttoBlockManager.mqh -------------------------------------------------

def patch_block_manager():
    path = os.path.join(ROOT, "COttoBlockManager.mqh")
    print("COttoBlockManager.mqh")
    L = mql(path)

    # 1. The direction chokepoint, now unconditional. The parallel
    #    GetDirectionForBlock() in COttoOrderManager.mqh must stay identical.
    sub(L, [
        border(3),
        box("DIRECTION CHOKEPOINT (standard two-phase lifecycle).", 3),
        box("", 3),
        box("A block's ZONE type is not its TRADE direction: that mapping is", 3),
        box("owned by COttoOrderManager::GetDirectionForBlock() and mirrored", 3),
        box("here. This file needs the direction in two places the OrderManager", 3),
        box("cannot reach (both veto funnel projections), so the same mapping", 3),
        box("is mirrored rather than guessed.", 3),
        box("", 3),
        box("v5.34 Part 3: the mapping is PHASE-AWARE. A fresh block", 3),
        box("(touches == 0) is a PHASE 1 BOUNCE (support -> LONG). A block that", 3),
        box("has consumed Touch 1 (touches >= 1) is a PHASE 2 REVERSAL and", 3),
        box("trades the S/R flip, so support -> SHORT. The Front-Run veto", 3),
        box("projects its target from here, so projecting off the wrong phase", 3),
        box("would veto the live reversal or miss real front-runs -- silently.", 3),
        box("", 3),
        box("The two implementations deliberately sit side by side in review:", 3),
        box("they must stay IDENTICAL or the Front-Run veto would project its", 3),
        box("target off the wrong direction while the order was priced from the", 3),
        box("other -- vetoing live entries or missing real front-runs, silently.", 3),
        box("", 3),
        border(3),
        "   ENUM_TRADE_DIRECTION    BlockDirection(const SSniperBlock &b) const",
        "     {",
        "      if(b.touches >= 1)",
        "         return (b.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Phase 2 — reversal (S/R flip)",
        "      return (b.type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;      // Phase 1 — normal bounce",
        "     }",
    ], [
        border(3),
        box("DIRECTION CHOKEPOINT (standard two-phase lifecycle).", 3),
        box("", 3),
        box("A block's ZONE type is not its TRADE direction: that mapping is", 3),
        box("owned by COttoOrderManager::GetDirectionForBlock() and mirrored", 3),
        box("here. This file needs the direction in two places the OrderManager", 3),
        box("cannot reach (both veto funnel projections), so the same mapping", 3),
        box("is mirrored rather than guessed.", 3),
        box("", 3),
        box("v5.52 PURE CONTINUATION: the mapping is UNCONDITIONAL and reads no", 3),
        box("phase. Pine's can_place gate is 'flat OR (long & resistance) OR", 3),
        box("(short & support)', so a RESISTANCE zone is traded LONG (a breakout", 3),
        box("above it) and a SUPPORT zone is traded SHORT (a breakdown below", 3),
        box("it). The v5.34 two-phase fade -- the first fill a bounce, only a", 3),
        box("reclaimed zone the flip -- is retired, so block.touches no longer", 3),
        box("enters this decision at all.", 3),
        box("", 3),
        box("The two implementations deliberately sit side by side in review:", 3),
        box("they must stay IDENTICAL or the Front-Run veto would project its", 3),
        box("target off the wrong direction while the order was priced from the", 3),
        box("other -- vetoing live entries or missing real front-runs, silently.", 3),
        box("", 3),
        border(3),
        "   ENUM_TRADE_DIRECTION    BlockDirection(const SSniperBlock &b) const",
        "     {",
        "      return (b.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Support -> SHORT, Resistance -> LONG",
        "     }",
    ], "bm.direction")

    # 2. The front edge now sits on the near side (the breakout level), so the
    #    comment must describe the continuation, not a phase.
    sub(L, [
        border(3),
        box("BlockEntryPrice — Pine entry price for a block, honouring", 3),
        box("InpEntryStyle. The front edge is the edge price APPROACHES FROM,", 3),
        box("which follows the mapped direction: for a LONG it is", 3),
        box("support.top / resistance.bottom, mirrored for a SHORT.", 3),
        box("", 3),
        box("This is the exact twin of COttoOrderManager::CalcEntryPrice() and", 3),
        box("flips its front edge with the phase-aware mapped direction (in", 3),
        box("Phase 1 a Support zone is a LONG, entered from the top; in Phase 2", 3),
        box("it is a SHORT, entered from the bottom).", 3),
        border(3),
    ], [
        border(3),
        box("BlockEntryPrice — Pine entry price for a block, honouring", 3),
        box("InpEntryStyle. The front edge is the edge price APPROACHES FROM,", 3),
        box("which follows the mapped direction: for a LONG it is", 3),
        box("resistance.bottom / support.top, mirrored for a SHORT.", 3),
        box("", 3),
        box("This is the exact twin of COttoOrderManager::CalcEntryPrice().", 3),
        box("v5.52: with the unconditional continuation mapping the front edge", 3),
        box("is the NEAR edge of the zone, so a Resistance LONG is entered at", 3),
        box("resistance.bottom and a Support SHORT at support.top. The order", 3),
        box("waits at the zone and the price crosses that edge on its way", 3),
        box("THROUGH it, which makes the entry a STOP-style trigger.", 3),
        border(3),
    ], "bm.entryprice")

    # 3. TryConvertToReversal is the machinery the whole pivot deletes.
    sub(L, [
        border(3),
        box("VETO-TO-REVERSAL (v5.34 Part 2) — a Phase 1 block (touches==0)", 3),
        "   //| that trips a convertible veto is upgraded to Phase 2 instead of  |",
        "   //| being deleted: the lifecycle counter advances to Touch 2, any    |",
        "   //| resting Phase 1 order is cancelled, and the block is reset to a  |",
        "   //| clean, unarmed reversal candidate. Returns true so the caller    |",
        "   //| SKIPS its SetVeto() call (the block survives as Touch 2).        |",
        border(3),
        "   bool                    TryConvertToReversal(int index, bool enabled, ENUM_VETO_REASON reason)",
        "     {",
        "      if(!enabled)                           return false;   // conversion disabled",
        "      if(index < 0 || index >= m_blockCount) return false;",
        "      if(m_blocks[index].touches != 0)       return false;   // Touch 2 -> permanent veto",
        "",
        "      SSniperBlock b   = m_blocks[index];    // copy — no struct reference",
        "      b.touches        = 1;                  // Phase 2 (Touch 1 consumed)",
        "      b.conversionReason = reason;           // v5.36: record the convertible veto that upgraded us",
        "      b.isArmed        = false;",
        "      b.hasPlacedOrder = false;",
        "      b.hasExited      = false;",
        "      b.isTriggered    = false;",
        "      m_blocks[index]  = b;                  // persist BEFORE the cancel request",
        "",
        "      RequestCancel(index);                  // drop any resting Phase 1 order",
        "",
        "      if(EnableLogging)",
        '         Print("[Block] CONVERT #", b.serial,',
        '               " (", (b.type == BLOCK_SUPPORT ? "SUP" : "RES"), ") ",',
        '               VetoReasonString(reason), " -> Phase 2 continuation");',
        "      return true;",
        "     }",
    ], [
        border(3),
        box("VETO-TO-REVERSAL — RETIRED (v5.52).", 3),
        box("", 3),
        box("v5.34 Part 2 upgraded a Phase 1 block that tripped a convertible", 3),
        box("veto into a Phase 2 reversal instead of deleting it. The pure", 3),
        box("CONTINUATION engine has no second phase to promote a block INTO,", 3),
        box("so every one of the five convertible vetoes is now simply a veto", 3),
        box("and this helper has no caller. Removed rather than left inert so", 3),
        box("no future edit can reintroduce the fade by accident; the five", 3),
        box("InpRev* inputs survive as declared no-ops in OttoDefines.mqh so an", 3),
        box("old .set file still loads.", 3),
        border(3),
    ], "bm.convert")

    # 3. SyncReversalCluster promoted a whole same-territory cluster to Phase 2.
    sub(L, [
        border(3),
        box("CLUSTER SYNC (v5.34 Part 3) — propagate a Phase 2 promotion to", 3),
        "   //| every PHASE 1 sibling that shares territory.                     |",
        "   //|                                                                  |",
        "   //| When the OrderManager consumes Touch 1 on `originIndex` (its      |",
        "   //| touches just became 1) the reclaimed zone trades as a REVERSAL.   |",
        "   //| Any OTHER live, same-polarity block (touches==0) whose range      |",
        "   //| intersects the origin is the SAME reclaim seen twice, so it is    |",
        "   //| promoted in lockstep: touches -> 1, reset to a clean unarmed      |",
        "   //| reversal candidate, and any resting Phase 1 order is cancelled.   |",
        "   //| Without this the twin would keep fading the very level the origin |",
        "   //| is now reversing against.                                        |",
        "   //|                                                                  |",
        "   //| A block whose touches != 0 is a Phase 2 peer already handled (its |",
        "   //| own sync), so it is skipped — this cannot cascade. Returns the     |",
        "   //| count of siblings promoted.                                       |",
        border(3),
        "   int               SyncReversalCluster(int originIndex)",
        "     {",
        "      if(originIndex < 0 || originIndex >= m_blockCount) return 0;",
        "      if(m_blocks[originIndex].touches < 1)              return 0;   // origin not Phase 2",
        "",
        "      SSniperBlock origin = m_blocks[originIndex];   // copy — no struct reference",
        "      int promoted = 0;",
        "",
        "      for(int j = 0; j < m_blockCount; j++)",
        "        {",
        "         if(j == originIndex)                  continue;",
        "         if(m_blocks[j].touches != 0)          continue;   // Phase 2 peer -> skip",
        "         if(m_blocks[j].type != origin.type)   continue;   // same polarity only",
        "         if(m_blocks[j].isVetoed)              continue;",
        "         if(m_blocks[j].deleteOnBarTime > 0)   continue;   // already leaving",
        "",
        "         // Same-territory test: ranges must intersect. This is the standard",
        "         // t1 >= b2 && b1 <= t2 test and is identical to HasTerritoryOverlap().",
        "         bool overlaps = (origin.top >= m_blocks[j].bottom && origin.bottom <= m_blocks[j].top);",
        "         if(!overlaps) continue;",
        "",
        "         SSniperBlock sib = m_blocks[j];   // copy — persist before the cancel request",
        "         sib.touches        = 1;           // Phase 2 (Touch 1 consumed)",
        "         sib.conversionReason = VETO_NONE; // v5.36: Phase 2 reached normally — NOT a veto conversion",
        "         sib.isArmed        = false;",
        "         sib.isTriggered    = false;",
        "         sib.hasPlacedOrder = false;",
        "         sib.hasExited      = false;",
        "         sib.deleteOnBarTime = 0;",
        "         m_blocks[j] = sib;",
        "         RequestCancel(j);                 // drop any resting Phase 1 order",
        "",
        "         if(EnableLogging)",
        '            Print("[Block] CLUSTER SYNC #", sib.serial,',
        '                  " (", (sib.type == BLOCK_SUPPORT ? "SUP" : "RES"), ")",',
        '                  " -> Phase 2 continuation (origin #", origin.serial, ")");',
        "         promoted++;",
        "        }",
        "      return promoted;",
        "     }",
    ], [
        border(3),
        box("CLUSTER SYNC — RETIRED (v5.52).", 3),
        box("", 3),
        box("v5.34 Part 3 promoted every same-territory Phase 1 sibling to", 3),
        box("Phase 2 the moment one of them was reclaimed, so a reclaim seen", 3),
        box("twice would not leave a twin still fading the level. With the", 3),
        box("two-phase engine gone there is no promotion and no fading twin --", 3),
        box("every zone now trades the SAME unconditional continuation -- so", 3),
        box("the helper's two callers are gone with it. Removed rather than", 3),
        box("left inert: an unreferenced cluster sync is a silent invitation to", 3),
        box("re-wire the fade.", 3),
        border(3),
    ], "bm.cluster")

    # 4. The funnel banner: the phase-aware language is gone.
    sub(L, [
        border(3),
        box("BLOCK MANAGEMENT FUNNEL — the exact v4.70 state machine.", 3),
        "   //| Runs once per new bar (calc_on_every_tick=false mirror). Order  |",
        "   //| per Pine: deletion -> hasExited -> Stale(45) -> Front-Run(InpMaxRR)|",
        "   //| -> Near-Miss(6D) -> Arming -> Break/Flip -> Momentum/FVG.       |",
        box("", 3),
        "   //| v5.49 -- the state machine is PHASE-AWARE: hasExited, arming and |",
        "   //| break/flip read block.touches and INVERT their geometry for a    |",
        "   //| Phase 2 reversal, so a reclaimed S/R flip is measured against    |",
        "   //| its OWN trade side, not the Phase 1 bounce it replaced.          |",
        border(3),
    ], [
        border(3),
        box("BLOCK MANAGEMENT FUNNEL — the exact v4.70 state machine.", 3),
        "   //| Runs once per new bar (calc_on_every_tick=false mirror). Order  |",
        "   //| per Pine: deletion -> hasExited -> Stale(45) -> Front-Run(InpMaxRR)|",
        "   //| -> Near-Miss(6D) -> Arming -> Break/Flip -> Momentum/FVG.       |",
        box("", 3),
        box("v5.52 -- the state machine is PHASE-FREE. v5.49's touches-driven", 3),
        box("geometry inversion existed only to measure a reclaimed zone against", 3),
        box("its own reversal side; there is no reversal any more, so hasExited,", 3),
        box("arming and break/flip are each a single unconditional test that", 3),
        box("never reads block.touches. See the break block below: its test is", 3),
        box("the OPPOSITE edge from arming, on purpose.", 3),
        border(3),
    ], "bm.funnel")

    # 5. hasExited: one unconditional wick test.
    sub(L, [
        "         // --- hasExited: price left the zone on a wick basis (phase-aware) ---",
        "         if(!b.hasExited)",
        "           {",
        "            if(b.touches == 0)   // Phase 1 (bounce) - a wick away from the zone",
        "              {",
        "               if((b.type == BLOCK_SUPPORT && low1 > b.top) ||",
        "                  (b.type == BLOCK_RESISTANCE && high1 < b.bottom))",
        "                  b.hasExited = true;",
        "              }",
        "            else                 // Phase 2 (reversal) - a wick through the near side",
        "              {",
        "               if((b.type == BLOCK_SUPPORT && low1 < b.bottom) ||",
        "                  (b.type == BLOCK_RESISTANCE && high1 > b.top))",
        "                  b.hasExited = true;",
        "              }",
        "           }",
    ], [
        "         // --- hasExited: price left the zone on a wick basis ------------",
        "         // A wick clear of the zone means price has left it and the setup is",
        "         // free to arm. Unconditional in v5.52: the continuation mapping does",
        "         // not depend on a phase, so neither does this test.",
        "         if(!b.hasExited)",
        "           {",
        "            if((b.type == BLOCK_SUPPORT && low1 > b.top) ||",
        "               (b.type == BLOCK_RESISTANCE && high1 < b.bottom))",
        "               b.hasExited = true;",
        "           }",
    ], "bm.hasexited")

    # 6. STALE veto: a veto is just a veto now. The two "persist <flag>
    #    before conversion decision" lines existed only to feed
    #    TryConvertToReversal(), so they go with it.
    sub(L, [
        "            if(b.wick1Day > 0 && (marketDay - b.wick1Day) >= InpStaleDays)",
        "              {",
        "               m_blocks[i] = b;   // persist hasExited before conversion decision",
        "               if(TryConvertToReversal(i, InpRevStale, VETO_STALE)) continue;",
        "               SetVeto(i, VETO_STALE);",
        "               continue;",
        "              }",
    ], [
        "            if(b.wick1Day > 0 && (marketDay - b.wick1Day) >= InpStaleDays)",
        "              {",
        "               SetVeto(i, VETO_STALE);",
        "               continue;",
        "              }",
    ], "bm.stale")

    # 7. FRONT-RUN projection: drop the Phase 2 entry anchor and the convert.
    sub(L, [
        "            // v5.34 Part 2 - mirror the OrderManager's Phase 2 entry anchor so",
        "            // this projection cannot veto a live reversal from the wrong price.",
        "            // Once the order rests, b.localTP supersedes this below.",
        "            if(InpShiftReversalEntry && b.touches >= 1)",
        "               calcEntry = (b.type == BLOCK_RESISTANCE) ? (b.bottom - calcSLDist)",
        "                                                        : (b.top + calcSLDist);",
        "            double target = isLong ? calcEntry + tpRR * calcSLDist",
        "                                   : calcEntry - tpRR * calcSLDist;",
        "            if(b.hasPlacedOrder && b.localTP > 0)",
        "               target = b.localTP;   // use exact TP parameter once placed",
        "",
        "            // TP above entry is hit by the HIGH, TP below by the LOW; both are",
        "            // independent of how the trade direction was mapped.",
        "            bool hit = isLong ? (high1 >= target) : (low1 <= target);",
        "            if(hit)",
        "              {",
        "               m_blocks[i] = b;   // persist hasExited before conversion decision",
        "               if(TryConvertToReversal(i, InpRevFrontRun, VETO_FRONTRUN)) continue;",
        "               SetVeto(i, VETO_FRONTRUN);",
        "               continue;",
        "              }",
    ], [
        "            // v5.34 Part 2 used to re-anchor this projection to the Phase 2",
        "            // outer-boundary entry. v5.52 has no Phase 2: the projection is",
        "            // taken straight from BlockEntryPrice(), the single front edge.",
        "            // Once the order rests, b.localTP supersedes this below.",
        "            double target = isLong ? calcEntry + tpRR * calcSLDist",
        "                                   : calcEntry - tpRR * calcSLDist;",
        "            if(b.hasPlacedOrder && b.localTP > 0)",
        "               target = b.localTP;   // use exact TP parameter once placed",
        "",
        "            // TP above entry is hit by the HIGH, TP below by the LOW; both are",
        "            // independent of how the trade direction was mapped.",
        "            bool hit = isLong ? (high1 >= target) : (low1 <= target);",
        "            if(hit)",
        "              {",
        "               SetVeto(i, VETO_FRONTRUN);",
        "               continue;",
        "              }",
    ], "bm.frontrun")

    # 8. NEAR-MISS veto: drop the convert.
    sub(L, [
        "            // 6-market-day expiry from the anchor",
        "            if(b.anchorDay > 0 && (marketDay - b.anchorDay) >= 6)",
        "              {",
        "               m_blocks[i] = b;   // persist anchor before conversion decision",
        "               if(TryConvertToReversal(i, InpRevNearMiss, VETO_NEARMISS)) continue;",
        "               SetVeto(i, VETO_NEARMISS);",
        "               continue;",
        "              }",
    ], [
        "            // 6-market-day expiry from the anchor",
        "            if(b.anchorDay > 0 && (marketDay - b.anchorDay) >= 6)",
        "              {",
        "               SetVeto(i, VETO_NEARMISS);",
        "               continue;",
        "              }",
    ], "bm.nearmiss")

    # 9. ARMING: one unconditional close-beyond-the-zone test.
    sub(L, [
        "            if(b.touches == 0)   // Phase 1 (bounce) - arm on a close out the far side",
        "              {",
        "               if((b.type == BLOCK_SUPPORT && close1 > b.top + InpArmATR * atrNow) ||",
        "                  (b.type == BLOCK_RESISTANCE && close1 < b.bottom - InpArmATR * atrNow))",
        "                  b.isArmed = true;",
        "              }",
        "            else                 // Phase 2 (reversal) - arm on a close out the near side",
        "              {",
        "               if((b.type == BLOCK_SUPPORT && close1 < b.bottom - InpArmATR * atrNow) ||",
        "                  (b.type == BLOCK_RESISTANCE && close1 > b.top + InpArmATR * atrNow))",
        "                  b.isArmed = true;",
        "              }",
    ], [
        "            // ARMED once price has CLOSED clear of the zone on the NEAR edge --",
        "            // for a SUPPORT zone a close ABOVE it, for a RESISTANCE zone a close",
        "            // BELOW it. The entry that follows rests back at the zone, so arming",
        "            // is the PRE-CONDITION, not the trigger.",
        "            //",
        "            // This is the pre-v5.52 arming test, kept verbatim. It is deliberately",
        "            // the edge OPPOSITE the break test below: arming looks past the NEAR",
        "            // edge, the break past the FAR edge, so the two can never fire on the",
        "            // same close and a block cannot arm and be vetoed BROKEN in one pass.",
        "            if((b.type == BLOCK_SUPPORT && close1 > b.top + InpArmATR * atrNow) ||",
        "               (b.type == BLOCK_RESISTANCE && close1 < b.bottom - InpArmATR * atrNow))",
        "               b.isArmed = true;",
    ], "bm.arming")

    # 10. BREAK / FLIP. The test is kept VERBATIM for the single phase it has
    #     left; only the dead Phase 2 arm is deleted. It is deliberately the
    #     OPPOSITE edge from arming: it detects the S/R breach that flips a
    #     zone's polarity, which is why arming at the near edge can never be a
    #     strict subset of a break at the far edge.
    sub(L, [
        "         // --- BREAK / FLIP (phase-aware: a break is the edge the trade cannot use) ---",
        "         bool isBroken = false;",
        "         if(b.touches == 0)   // Phase 1: a close through the zone's own far edge",
        "           {",
        "            isBroken = (b.type == BLOCK_SUPPORT && close1 < b.bottom - ATRBufferBreak * atrNow) ||",
        "                       (b.type == BLOCK_RESISTANCE && close1 > b.top + ATRBufferBreak * atrNow);",
        "           }",
        "         else                 // Phase 2: the flipped setup breaks out the opposite edge",
        "           {",
        "            isBroken = (b.type == BLOCK_SUPPORT && close1 > b.top + ATRBufferBreak * atrNow) ||",
        "                       (b.type == BLOCK_RESISTANCE && close1 < b.bottom - ATRBufferBreak * atrNow);",
        "           }",
    ], [
        "         // --- BREAK / FLIP (a break is the edge the trade cannot use) ---",
        "         // Pine's S/R-BREACH / POLARITY-FLIP detector, NOT an entry validator:",
        "         // it asks whether the close went out the FAR edge of the zone, which",
        "         // reclassifies the zone and blacklists it. That is why it names the",
        "         // OPPOSITE edge from arming above -- arming is the near side the trade",
        "         // breaks out toward, the break is the far side. They are mutually",
        "         // exclusive by construction, so a block can never arm here and be",
        "         // vetoed BROKEN in the same pass.",
        "         //",
        "         // KEPT VERBATIM through the v5.52 pivot for the one phase it still",
        "         // has; only the dead Phase 2 arm was removed. v5.49's Phase 2 branch",
        "         // tested the near edge, which for an armed block was already true --",
        "         // it would have vetoed the live reversal on its own arming close.",
        "         bool isBroken =",
        "            (b.type == BLOCK_SUPPORT && close1 < b.bottom - ATRBufferBreak * atrNow) ||",
        "            (b.type == BLOCK_RESISTANCE && close1 > b.top + ATRBufferBreak * atrNow);",
    ], "bm.break")

    # 11. MOMENTUM & FVG veto: drop the convert.
    sub(L, [
        "            if(momentum || fvg)",
        "              {",
        "               m_blocks[i] = b;   // persist isArmed before conversion decision",
        "               if(TryConvertToReversal(i, momentum ? InpRevMom : InpRevFvg,",
        "                                       momentum ? VETO_MOMENTUM : VETO_FVG)) continue;",
        "               SetVeto(i, momentum ? VETO_MOMENTUM : VETO_FVG);",
        "               continue;",
        "              }",
    ], [
        "            if(momentum || fvg)",
        "              {",
        "               SetVeto(i, momentum ? VETO_MOMENTUM : VETO_FVG);",
        "               continue;",
        "              }",
    ], "bm.momfvg")

    # 12. UpdateIntraBar. L-734/L-743 are the ONLY two conversion calls in this
    #     function that lack the "persist X before conversion decision" marker
    #     line in front of them, so each anchor below is unique. The `!b.hasExited`
    #     guard at the top becomes load-bearing: it is what stops the Front-Run
    #     and 6-day branches from firing on every tick once their
    #     TryConvertToReversal() gate is gone.
    sub(L, [
        "            bool hit = isLong ? (high0 >= target) : (low0 <= target);",
        "            if(hit)",
        "              {",
        "               if(TryConvertToReversal(i, InpRevFrontRun, VETO_FRONTRUN)) continue;",
        "               SetVeto(i, VETO_FRONTRUN);",
        "               continue;",
        "              }",
    ], [
        "            bool hit = isLong ? (high0 >= target) : (low0 <= target);",
        "            if(hit)",
        "              {",
        "               SetVeto(i, VETO_FRONTRUN);",
        "               continue;",
        "              }",
    ], "bm.intrabar.frontrun")

    sub(L, [
        "         if(InpUseNearMissVeto && b.anchorDay > 0 && (marketDay - b.anchorDay) >= 6)",
        "           {",
        "            if(TryConvertToReversal(i, InpRevNearMiss, VETO_NEARMISS)) continue;",
        "            SetVeto(i, VETO_NEARMISS);",
        "            continue;",
        "           }",
    ], [
        "         if(InpUseNearMissVeto && b.anchorDay > 0 && (marketDay - b.anchorDay) >= 6)",
        "           {",
        "            SetVeto(i, VETO_NEARMISS);",
        "            continue;",
        "           }",
    ], "bm.intrabar.nearmiss")

    # 13. The intra-bar Front-Run twin of the phase-aware entry anchor.
    sub(L, [
        "            // Intra-bar twin of the Update() projection above — same",
        "            // phase-aware mapped direction.",
        "            bool   isLong = (BlockDirection(b) == DIR_LONG);",
        "            // v5.34 Part 2 - same Phase 2 entry anchor as the Update() projector",
        "            // above, so both intra-bar and bar-close vetoes judge one price.",
        "            if(InpShiftReversalEntry && b.touches >= 1)",
        "               calcEntry = (b.type == BLOCK_RESISTANCE) ? (b.bottom - calcSLDist)",
        "                                                        : (b.top + calcSLDist);",
    ], [
        "            // Intra-bar twin of the Update() projection above — same",
        "            // unconditional mapped direction.",
        "            bool   isLong = (BlockDirection(b) == DIR_LONG);",
        "            // v5.34 Part 2 used to re-anchor this projection to the Phase 2",
        "            // outer-boundary entry. v5.52 has no Phase 2: calcEntry() already",
        "            // holds the single front edge, so both intra-bar and bar-close",
        "            // vetoes judge one price with no shift.",
    ], "bm.intrabar.entry")

    save_mql(path, L)


# --- comment box helpers ---------------------------------------------------
# The sources use a fixed-width banner. The MODULES (.mqh) pad their text field
# to 66 columns; otto.mq5 and COttoJournal.mqh use 65. Generating the padding
# instead of hand-counting it keeps every rewritten banner aligned with the
# untouched blocks around it.

def box(text, indent=0, field=66):
    return " " * indent + "//|" + (" " + text).ljust(field) + "|"


def border(indent=0):
    return " " * indent + "//+" + "-" * 66 + "+"


def sborder(indent=3, dashes=64):
    # The journal and otto.mq5 use a shorter banner border than the modules.
    return " " * indent + "//+" + "-" * dashes + "+"


# --- COttoOrderManager.mqh -------------------------------------------------

def patch_order_manager():
    path = os.path.join(ROOT, "COttoOrderManager.mqh")
    print("COttoOrderManager.mqh")
    L = mql(path)

    # 1. The AUTHORITY. COttoBlockManager::BlockDirection() and
    #    MappedZoneDirection() in otto.mq5 mirror this byte for byte.
    sub(L, [
        "   ENUM_ORDER_TYPE         GetOrderTypeForBlock(const SSniperBlock &block)",
        "     {",
        "      // The order type must FOLLOW the mapped direction, not the zone.",
        "      // Deriving it independently from block.type would emit a BUY_LIMIT",
        "      // while the direction said SHORT. Kept coherent here for every caller.",
        "      return (GetDirectionForBlock(block) == DIR_LONG) ? ORDER_TYPE_BUY_LIMIT",
        "                                                       : ORDER_TYPE_SELL_LIMIT;",
        "     }",
        "   ENUM_TRADE_DIRECTION    GetDirectionForBlock(const SSniperBlock &block)",
        "     {",
        "      // A Support/Resistance zone is DISCOVERED polarity, NOT trade direction.",
        "      // The mapping is the standard two-phase lifecycle: Phase 1 trades the",
        "      // zone as a BOUNCE, Phase 2 trades the reclaimed zone as a REVERSAL.",
        "      // This is the single chokepoint every consumer reads (front edge, SL,",
        "      // TP, basket seed, correlation gate), so the mapping stays coherent.",
        "      //",
        "      // v5.34 Part 3 — TWO-PHASE LIFECYCLE. The counter in block.touches",
        "      // carries the phase:",
        "      //   touches == 0  -> PHASE 1, the normal BOUNCE off the zone (support ->",
        "      //                    LONG, resistance -> SHORT): SL below a Long, TP",
        "      //                    above; mirrored for a Short.",
        "      //   touches == 1  -> PHASE 2, the REVERSAL. Having reclaimed the zone the",
        "      //                    setup trades the S/R FLIP, so the direction inverts",
        "      //                    (support -> SHORT, resistance -> LONG). The block",
        "      //                    geometry and shifted entry are unchanged; only the",
        "      //                    side of the trade changes.",
        "      //   touches == 2  -> terminal (the reversal fill), Phase-2 mapping kept.",
        "      // Keeping this decision in ONE place is what makes the reversal coherent",
        "      // for the front edge, SL, TP, basket seed and correlation gate at once.",
        "      if(block.touches >= 1)",
        "         return (block.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Phase 2 — reversal (S/R flip)",
        "      return (block.type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;      // Phase 1 — normal bounce",
        "     }",
    ], [
        "   ENUM_ORDER_TYPE         GetOrderTypeForBlock(const SSniperBlock &block)",
        "     {",
        "      // The order type must FOLLOW the mapped direction, not the zone.",
        "      // Deriving it independently from block.type would emit a BUY_LIMIT",
        "      // while the direction said SHORT. Kept coherent here for every caller.",
        "      return (GetDirectionForBlock(block) == DIR_LONG) ? ORDER_TYPE_BUY_LIMIT",
        "                                                       : ORDER_TYPE_SELL_LIMIT;",
        "     }",
        "   ENUM_TRADE_DIRECTION    GetDirectionForBlock(const SSniperBlock &block)",
        "     {",
        "      // A Support/Resistance zone is DISCOVERED polarity, NOT trade direction.",
        "      // v5.52 PURE CONTINUATION: the mapping is UNCONDITIONAL and reads no",
        "      // phase. Pine's can_place gate is 'flat OR (long & resistance) OR",
        "      // (short & support)', so a RESISTANCE zone is traded LONG (a breakout",
        "      // above it) and a SUPPORT zone is traded SHORT (a breakdown below it).",
        "      // The v5.34 two-phase fade -- the first fill a bounce, only a reclaimed",
        "      // zone the flip -- is retired.",
        "      //",
        "      // This is the single chokepoint every consumer reads (front edge, SL,",
        "      // TP, basket seed, correlation gate), so the mapping stays coherent.",
        "      // COttoBlockManager::BlockDirection() and otto.mq5's MappedZoneDirection()",
        "      // are identical twins and must be kept in lockstep, or the",
        "      // Front-Run veto would project off the opposite side of the order.",
        "      return (block.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Support -> SHORT, Resistance -> LONG",
        "     }",
    ], "om.direction")

    # 2. Entry price. The arithmetic is UNCHANGED (it already keys off the
    #    mapped direction, which is now unconditional), so this is the comment
    #    plus the retired InpShiftReversalEntry shift.
    sub(L, [
        "   double                  CalcEntryPrice(const SSniperBlock &block)",
        "     {",
        "      if(InpEntryStyle == ENTRY_MIDPOINT)",
        "         return block.midpoint;",
        "      // The front edge is the edge the price APPROACHES FROM. With the standard",
        "      // bounce mapping that is the near edge (support.top / resistance.bottom);",
        "      // in Phase 2 the setup trades the reclaimed zone from the opposite side,",
        "      // so the front edge follows the phase-aware mapped direction.",
        "      return (GetDirectionForBlock(block) == DIR_LONG) ? block.top : block.bottom;",
        "     }",
    ], [
        "   double                  CalcEntryPrice(const SSniperBlock &block)",
        "     {",
        "      if(InpEntryStyle == ENTRY_MIDPOINT)",
        "         return block.midpoint;",
        "      // The front edge is the edge the price APPROACHES FROM. v5.52 keys it",
        "      // straight off the unconditional mapped direction, so a Resistance LONG",
        "      // is entered at resistance.bottom and a Support SHORT at support.top --",
        "      // the NEAR edge of the zone, which price crosses on its way through it.",
        "      // Twin of COttoBlockManager::BlockEntryPrice(); they must stay identical",
        "      // or the Front-Run veto would judge a different price from the order's.",
        "      return (GetDirectionForBlock(block) == DIR_LONG) ? block.top : block.bottom;",
        "     }",
    ], "om.entryprice")

    # 3. The retired outer-boundary shift, plus the hoist comment above it.
    sub(L, [
        "      // v5.34 Part 3 — SHIFTED REVERSAL ENTRY (Phase 2 only). A Touch-1 block",
        "      // is a reclaimed zone being traded as a REVERSAL, so its entry is moved",
        "      // to the OUTER boundary of the block by one full stop distance. That",
        "      // orders the reversal BEYOND the extreme that rejected the Phase 1 bounce,",
        "      // instead of back inside the zone the bounce already occupied. The",
        "      // anchor is keyed off the block's OWN type (Phase 2 maps Resistance to a",
        "      // LONG and Support to a SHORT), so a LONG reversal sits BELOW the block",
        "      // bottom and a SHORT reversal sits ABOVE the block top. slDist is",
        "      // hoisted here because the shift below depends on it; both are",
        "      // entry-independent, so snapping/shifting never changes the risk distance.",
        "      double slDist     = CalcSLDistance(block, atr);",
        "      if(InpShiftReversalEntry && block.touches >= 1)",
        "        {",
        "         // Phase 2 Reversal Outer Boundary Geometry:",
        "         // Resistance-turned-Support (LONG): Entry is shifted BELOW the block bottom by slDist",
        "         // Support-turned-Resistance (SHORT): Entry is shifted ABOVE the block top by slDist",
        "         double shifted = (block.type == BLOCK_RESISTANCE) ? (block.bottom - slDist)",
        "                                                           : (block.top + slDist);",
        "         entryPrice = SnapToTick(shifted);   // re-snap: the shift may leave the grid",
        "         if(EnableLogging)",
        '            Print("[OrderManager] SHIFTED CONTINUATION ENTRY: block ", block.tradeId,',
        '                  " touched=", block.touches,',
        '                  " dir=", (GetDirectionForBlock(block) == DIR_LONG ? "LONG" : "SHORT"),',
        '                  " entry=", DoubleToString(entryPrice, _Digits),',
        '                  " (slDist=", DoubleToString(slDist, _Digits), ")");',
        "        }",
    ], [
        "      // v5.34 Part 3 — SHIFTED REVERSAL ENTRY — RETIRED (v5.52).",
        "      // The shift moved a Touch-1 block's entry to the OUTER boundary by one",
        "      // full stop distance, to order the reversal beyond the extreme that",
        "      // rejected the Phase 1 bounce. With the two-phase engine gone there is",
        "      // no Phase 2 to shift, so the entry is the single front edge from",
        "      // CalcEntryPrice() and InpShiftReversalEntry is a declared no-op.",
        "      // slDist stays hoisted here: SL/TP below are entry-relative and reuse it.",
        "      double slDist     = CalcSLDistance(block, atr);",
    ], "om.shiftentry")

    # 4. The physical rest comment: the phase tag has one value now.
    sub(L, [
        "         // v5.41 -- R/C PHASE TAG (physical). Mirrors the virtual fill-time",
        "         // marker so a resting broker row names its phase: a block's FIRST",
        '         // fill (touches == 0) is the Phase 1 "R"eversal (the S/R bounce),',
        '         // its SECOND and later (touches >= 1) the Phase 2 "C"ontinuation.',
        '         // ClampOrderComment() already tail-preserves the "-BLK<n>" setup id,',
        "         // so the extra tag still fits the MT5 31-char limit.",
        '         string phaseType  = (block.touches == 0) ? "R" : "C";',
        '         request.comment   = ClampOrderComment(armSessionId + "_" + phaseType + "_P");',
    ], [
        "         // v5.41 R/C PHASE TAG, v5.52 — the tag is CONSTANT now. A block's",
        "         // entry is the single continuation (the breakout / breakdown); the",
        "         // phase branch dropped with the two-phase engine. The tag is kept",
        "         // rather than removed so the MT5 31-char comment SHAPE, and the",
        '         // "-BLK<n>" setup id ClampOrderComment() tail-preserves, are',
        "         // unchanged for any downstream tooling that parses the row.",
        '         string phaseType  = "C";',
        '         request.comment   = ClampOrderComment(armSessionId + "_" + phaseType + "_P");',
    ], "om.phase.physical")

    # 5. The hourly report's per-setup phase label: constant too.
    sub(L, [
        "      // v5.52 Part 3 -- R/C PHASE TAG (hourly report). Mirrors the R/C marker",
        "      // the arm and retag paths stamp on the live order, but reads the BLOCK",
        "      // instead, so the operator sees a setup's phase BEFORE it ever fills.",
        "      for(int v = 0; v < m_virtualCount; v++)",
        "        {",
        "         int vs   = m_virtualOrders[v].blockSerial;",
        "         int bIdx = m_blockManager.FindBlockIndexBySerial(vs);",
        "         SSniperBlock b;",
        '         string phaseLabel = "UNKNOWN";',
        "         if(bIdx >= 0 && m_blockManager.GetBlockAt(bIdx, b))",
        '            phaseLabel = (b.touches == 0) ? "R (S/R Reversal)" : "C (Continuation)";',
    ], [
        "      // v5.51 Part 3 -- R/C PHASE TAG (hourly report), v5.52 — every armed",
        "      // setup is a CONTINUATION now: there is no reversal phase left, so this",
        "      // opens with the constant label instead of discriminating on block.touches.",
        "      // The per-serial walk is kept because it is what proves each armed order",
        "      // still resolves to a live block.",
        "      for(int v = 0; v < m_virtualCount; v++)",
        "        {",
        "         int vs   = m_virtualOrders[v].blockSerial;",
        "         int bIdx = m_blockManager.FindBlockIndexBySerial(vs);",
        "         SSniperBlock b;",
        "         // Unresolved block -> a VISIBLE UNKNOWN, never a blank or a guess.",
        '         string phaseLabel = "UNKNOWN";',
        "         if(bIdx >= 0 && m_blockManager.GetBlockAt(bIdx, b))",
        '            phaseLabel = "C (Continuation)";',
    ], "om.phase.hourly")

    # 6. CheckPendingOrderFills: the sibling-promotion call and the log wording.
    sub(L, [
        "                  if(mod.touches == 1)",
        "                     m_blockManager.SyncReversalCluster(bi);",
        "                  if(EnableLogging)",
        '                     Print("[OrderManager] TOUCH ", mod.touches, " CONSUMED: block ", mod.tradeId,',
        '                           " (prev=", prevTouch, ") -> Continuation phase active");',
    ], [
        "                  if(EnableLogging)",
        '                     Print("[OrderManager] TOUCH ", mod.touches, " CONSUMED: block ", mod.tradeId,',
        '                           " (prev=", prevTouch, ") -> continuation entry done; block is one-shot");',
    ], "om.fills.touch")

    # 7. CheckVirtualTriggers: the market-leg comment tag.
    sub(L, [
        "         // v5.38 -- R/C PHASE TAG + REUSE OF THE ARM SESSION. A block serial",
        "         // executes repeatedly: its FIRST fill (touches == 0) is the Phase 1",
        '         // "R"eversal (the S/R bounce), each later fill (touches >= 1) the',
        '         // Phase 2 "C"ontinuation. The one-letter marker rides in the broker',
        "         // comment. The SESSION id is NOT re-minted here: it was minted when",
        "         // the setup was ARMED and is carried on the descriptor, so the arm",
        "         // snapshot, this fill and the later exit all resolve to ONE journal",
        "         // file, and a fallback mint keeps the identifier defined.",
        '         string phaseType = (blocks[bi].touches == 0) ? "R" : "C";',
    ], [
        "         // v5.38 -- R/C PHASE TAG + REUSE OF THE ARM SESSION. v5.52: the marker",
        "         // is CONSTANT -- every setup is the single continuation entry (the",
        "         // breakout / breakdown), so there is no phase left to discriminate.",
        "         // The SESSION id is NOT re-minted here: it was minted when the setup",
        "         // was ARMED and is carried on the descriptor, so the arm snapshot, this",
        "         // fill and the later exit all resolve to ONE journal file, and a",
        "         // fallback mint keeps the identifier defined.",
        '         string phaseType = "C";',
    ], "om.virtual.phase")

    # 8. CheckVirtualTriggers: consume the single entry, drop the cluster sync.
    sub(L, [
        "         // Consume the touch: 0 -> 1 promotes the block to a Phase 2 reversal",
        "         // candidate; 1 -> 2 retires it. Mirrors CheckPendingOrderFills().",
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
        "",
        "         // Cluster sync: any same-polarity Phase 1 sibling sharing this",
        "         // territory reverses in lockstep (same call CheckPendingOrderFills",
        "         // makes when Touch 1 is consumed).",
        "         if(block.touches == 1)",
        "            m_blockManager.SyncReversalCluster(bi);",
    ], [
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
    ], "om.virtual.touch")

    # 9. The router legality gate. The ARITHMETIC is unchanged; this records the
    #    v5.52 consequence: a front-edge continuation entry is crossed BY price,
    #    so it is a STOP-style trigger. When the price has not yet crossed, the
    #    limit-direction test fails and the setup arms VIRTUALLY and fires a
    #    MARKET deal on the cross -- the correct breakout execution either way.
    sub(L, [
        "      // REST at the broker. A BUY_LIMIT must sit BELOW the market (ask) and a",
        "      // SELL_LIMIT ABOVE it (bid), each clearing the broker's stops/freeze",
        "      // boundary. When the price is still valid we send a real pending order",
        "      // (broker-side resting fill). When price has already reached/through the",
        "      // zone the resting form is illegal, so we ARM an in-memory virtual order",
        "      // and let CheckVirtualTriggers() fire it as a MARKET deal on the cross.",
    ], [
        "      // REST at the broker. A BUY_LIMIT must sit BELOW the market (ask) and a",
        "      // SELL_LIMIT ABOVE it (bid), each clearing the broker's stops/freeze",
        "      // boundary. When the price is still valid we send a real pending order",
        "      // (broker-side resting fill). When price has already reached/through the",
        "      // zone the resting form is illegal, so we ARM an in-memory virtual order",
        "      // and let CheckVirtualTriggers() fire it as a MARKET deal on the cross.",
        "      //",
        "      // v5.52 CONTINUATION NOTE. With InpEntryStyle = ENTRY_FRONT_EDGE the",
        "      // entry is the zone edge price CROSSES -- a Resistance LONG is entered at",
        "      // resistance.bottom and a Support SHORT at support.top -- so the trigger",
        "      // is STOP-style, not limit-style. That is handled by the two routes below",
        "      // rather than by a new order type: while the price has NOT yet crossed,",
        "      // the entry sits on the non-limit side of the market, this test is false,",
        "      // and the setup arms VIRTUALLY and fires a MARKET deal on the cross. A",
        "      // physical rest is only ever used when the entry will genuinely be",
        "      // crossed toward (a pullback into the zone), which the limit test accepts.",
    ], "om.router.note")

    # 10. The copy/attach order-type -> direction maps. TWO identical sites; each
    #     is disambiguated by the guard line that follows it. v5.52 trades
    #     continuation only, so a BUY is a Resistance LONG and a SELL a Support
    #     SHORT -- the mapping the enumeration itself now states.
    sub(L, [
        "      if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP)   odir = DIR_LONG;",
        "      else if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) odir = DIR_SHORT;",
        "      if(odir == DIR_NONE) continue;",
    ], [
        "      if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP)   odir = DIR_LONG;",
        "      else if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) odir = DIR_SHORT;",
        "      // v5.52: a BUY is a Resistance continuation (a breakout above it) and a",
        "      // SELL a Support continuation (a breakdown below it) -- the mapping",
        "      // GetDirectionForBlock() states unconditionally, so this leg's direction",
        "      // cannot disagree with the block it mirrors.",
        "      if(odir == DIR_NONE) continue;",
    ], "om.copydir.consensus")

    sub(L, [
        "      if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP)   odir = DIR_LONG;",
        "      else if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) odir = DIR_SHORT;",
        "      if(odir != m_activeDirection) continue;",
    ], [
        "      if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP)   odir = DIR_LONG;",
        "      else if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) odir = DIR_SHORT;",
        "      // v5.52: same continuation mapping as the consensus sweep above.",
        "      if(odir != m_activeDirection) continue;",
    ], "om.copydir.conflict")

    # 11. PendingOrderCleanup: physical cancel path. The conversion branch was
    #     reachable ONLY because TryConvertToReversal() stamped conversionReason;
    #     with that writer gone the field is permanently VETO_NONE, so the branch
    #     is deleted and the cancellation path is left in its `else` block.
    sub(L, [
        "                  if(blocks[i].conversionReason != VETO_NONE && blocks[i].touches >= 1)",
        "                    {",
        '                     string convReason = "Fired Front-Run 1:4 Veto";',
        '                     if(blocks[i].conversionReason == VETO_NEARMISS)      convReason = "Near-Miss Veto (6D)";',
        '                     else if(blocks[i].conversionReason == VETO_STALE)    convReason = "Stale Veto (45D)";',
        '                     else if(blocks[i].conversionReason == VETO_MOMENTUM) convReason = "Momentum Veto";',
        '                     else if(blocks[i].conversionReason == VETO_FVG)      convReason = "FVG Veto";',
        "                     // v5.43 -- save/restore: a transient setup log must not",
        "                     // steal the journal from a live basket (see PlaceOrArmOrder).",
        "                     string prevSession = m_sessionID;",
        "                     m_journal.SetSessionID(physSession);",
        "                     m_journal.LogConversion(physTicket, blocks[i].type, convReason);",
        '                     if(prevSession != "") m_journal.SetSessionID(prevSession);',
        "                    }",
        "                  else",
        "                    {",
        '                     string cancelReason = "Manual";',
        "                     if(blocks[i].vetoReason == VETO_FRONTRUN)           cancelReason = \"Fired Front-Run 1:4 Veto\";",
        "                     else if(blocks[i].vetoReason == VETO_STALE)         cancelReason = \"Stale Veto (45D)\";",
        "                     else if(blocks[i].vetoReason == VETO_NEARMISS)      cancelReason = \"Near-Miss Veto (6D)\";",
        "                     else if(blocks[i].vetoReason == VETO_MOMENTUM)      cancelReason = \"Momentum Veto\";",
        "                     else if(blocks[i].vetoReason == VETO_FVG)           cancelReason = \"FVG Veto\";",
        "                     else if(blocks[i].vetoReason == VETO_SIZING)        cancelReason = \"Sizing Veto\";",
        "                     else if(blocks[i].vetoReason == VETO_NO_SEPARATION) cancelReason = \"Separation Veto\";",
        "                     else if(blocks[i].vetoReason == VETO_BROKEN)        cancelReason = \"Block Broken\";",
        "                     else if(blocks[i].vetoReason == VETO_FLIPPED)       cancelReason = \"Block Flipped\";",
        "                     else if(blocks[i].vetoReason == VETO_CORRELATION)   cancelReason = \"Vector Consensus Veto\";",
        "                     else if(blocks[i].pendingOrderCancel)               cancelReason = \"Manual / Direction Conflict\";",
        "                     // v5.43 -- save/restore (see the conversion branch above).",
        "                     string prevSession = m_sessionID;",
        "                     m_journal.SetSessionID(physSession);",
        "                     m_journal.LogCancellation(cancelReason);",
        '                     if(prevSession != "") m_journal.SetSessionID(prevSession);',
        "                    }",
    ], [
        "                  // v5.52: the conversion branch that used to guard this is gone",
        "                  // with TryConvertToReversal(). conversionReason is now permanently",
        "                  // VETO_NONE, so every drop is a plain cancellation.",
        '                  string cancelReason = "Manual";',
        "                  if(blocks[i].vetoReason == VETO_FRONTRUN)           cancelReason = \"Fired Front-Run 1:4 Veto\";",
        "                  else if(blocks[i].vetoReason == VETO_STALE)         cancelReason = \"Stale Veto (45D)\";",
        "                  else if(blocks[i].vetoReason == VETO_NEARMISS)      cancelReason = \"Near-Miss Veto (6D)\";",
        "                  else if(blocks[i].vetoReason == VETO_MOMENTUM)      cancelReason = \"Momentum Veto\";",
        "                  else if(blocks[i].vetoReason == VETO_FVG)           cancelReason = \"FVG Veto\";",
        "                  else if(blocks[i].vetoReason == VETO_SIZING)        cancelReason = \"Sizing Veto\";",
        "                  else if(blocks[i].vetoReason == VETO_NO_SEPARATION) cancelReason = \"Separation Veto\";",
        "                  else if(blocks[i].vetoReason == VETO_BROKEN)        cancelReason = \"Block Broken\";",
        "                  else if(blocks[i].vetoReason == VETO_FLIPPED)       cancelReason = \"Block Flipped\";",
        "                  else if(blocks[i].vetoReason == VETO_CORRELATION)   cancelReason = \"Vector Consensus Veto\";",
        "                  else if(blocks[i].pendingOrderCancel)               cancelReason = \"Manual / Direction Conflict\";",
        "                  // v5.43 -- save/restore: a transient setup log must not steal the",
        "                  // journal from a live basket (see PlaceOrArmOrder).",
        "                  string prevSession = m_sessionID;",
        "                  m_journal.SetSessionID(physSession);",
        "                  m_journal.LogCancellation(cancelReason);",
        '                  if(prevSession != "") m_journal.SetSessionID(prevSession);',
    ], "om.cleanup.physical")

    # 12. PendingOrderCleanup: virtual cancel path. Same deletion as above; the
    #     cancellation body is left dedented in place of the old `else`.
    #     Built as named lists because the region is too large for one edit.
    old = [
        "               // v5.36: a block whose Phase 2 promotion came from a convertible",
        "               // veto is NOT a fatal cancel: it is a re-phase. Journal (and email)",
        "               // a conversion notice instead of a CANCELLED_ rename.",
        "               if(blocks[i].conversionReason != VETO_NONE && blocks[i].touches >= 1)",
        "                 {",
        '                  string convReason = "Fired Front-Run 1:4 Veto";',
        '                  if(blocks[i].conversionReason == VETO_NEARMISS)      convReason = "Near-Miss Veto (6D)";',
        '                  else if(blocks[i].conversionReason == VETO_STALE)    convReason = "Stale Veto (45D)";',
        '                  else if(blocks[i].conversionReason == VETO_MOMENTUM) convReason = "Momentum Veto";',
        '                  else if(blocks[i].conversionReason == VETO_FVG)      convReason = "FVG Veto";',
        "",
        "                  // v5.38: keep the setup's ONE journal. Reuse the arm-time",
        "                  // session carried on the descriptor; only a legacy entry",
        "                  // with no session id falls back to a freshly minted one.",
        "                  string voSession = m_virtualOrders[v].sessionId;",
        '                  if(voSession == "")',
        "                    {",
        "                     MqlDateTime ctm2; TimeToStruct(TimeCurrent(), ctm2);",
        '                     string cts2 = StringFormat("%04d%02d%02d-%02d%02d%02d", ctm2.year, ctm2.mon, ctm2.day, ctm2.hour, ctm2.min, ctm2.sec);',
        '                     voSession = StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, cts2, blocks[i].serial);',
        "                    }",
        "                  // v5.43 -- save/restore (see the physical-cancel path above).",
        "                  string prevSession = m_sessionID;",
        "                  m_journal.SetSessionID(voSession);",
        "                  m_journal.LogConversion(0, blocks[i].type, convReason);",
        '                  if(prevSession != "") m_journal.SetSessionID(prevSession);',
        "                 }",
        "               else",
        "                 {",
        '                  string cancelReason = "Manual";',
        '                  if(blocks[i].vetoReason == VETO_FRONTRUN)       cancelReason = "Fired Front-Run 1:4 Veto";',
        '                  else if(blocks[i].vetoReason == VETO_STALE)     cancelReason = "Stale Veto (45D)";',
        '                  else if(blocks[i].vetoReason == VETO_NEARMISS)  cancelReason = "Near-Miss Veto (6D)";',
        '                  else if(blocks[i].vetoReason == VETO_MOMENTUM)  cancelReason = "Momentum Veto";',
        '                  else if(blocks[i].vetoReason == VETO_FVG)        cancelReason = "FVG Veto";',
        '                  else if(blocks[i].vetoReason == VETO_SIZING)     cancelReason = "Sizing Veto";',
        '                  else if(blocks[i].vetoReason == VETO_NO_SEPARATION) cancelReason = "Separation Veto";',
        '                  else if(blocks[i].vetoReason == VETO_BROKEN)     cancelReason = "Block Broken";',
        '                  else if(blocks[i].vetoReason == VETO_FLIPPED)    cancelReason = "Block Flipped";',
        '                  else if(blocks[i].vetoReason == VETO_CORRELATION) cancelReason = "Vector Consensus Veto";',
        '                  else if(blocks[i].pendingOrderCancel)           cancelReason = "Manual / Direction Conflict";',
        "                  // v5.38: reuse the arm-time session (see the conversion branch",
        "                  // above) so the cancel lands in the setup's ONE journal.",
        "                  string voSession = m_virtualOrders[v].sessionId;",
        '                  if(voSession == "")',
        "                    {",
        "                     MqlDateTime ctm; TimeToStruct(TimeCurrent(), ctm);",
        '                     string cts = StringFormat("%04d%02d%02d-%02d%02d%02d", ctm.year, ctm.mon, ctm.day, ctm.hour, ctm.min, ctm.sec);',
        '                     voSession = StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, cts, blocks[i].serial);',
        "                    }",
        "                  // v5.43 -- save/restore (see the physical-cancel path above).",
        "                  string prevSession = m_sessionID;",
        "                  m_journal.SetSessionID(voSession);",
        "                  m_journal.LogCancellation(cancelReason);",
        '                  if(prevSession != "") m_journal.SetSessionID(prevSession);',
        "                 }",
    ]

    new = [
        "               // v5.52: the conversion branch that used to guard this is gone with",
        "               // TryConvertToReversal(). conversionReason is now permanently",
        "               // VETO_NONE, so every drop is a plain cancellation.",
        '               string cancelReason = "Manual";',
        '               if(blocks[i].vetoReason == VETO_FRONTRUN)       cancelReason = "Fired Front-Run 1:4 Veto";',
        '               else if(blocks[i].vetoReason == VETO_STALE)     cancelReason = "Stale Veto (45D)";',
        '               else if(blocks[i].vetoReason == VETO_NEARMISS)  cancelReason = "Near-Miss Veto (6D)";',
        '               else if(blocks[i].vetoReason == VETO_MOMENTUM)  cancelReason = "Momentum Veto";',
        '               else if(blocks[i].vetoReason == VETO_FVG)        cancelReason = "FVG Veto";',
        '               else if(blocks[i].vetoReason == VETO_SIZING)     cancelReason = "Sizing Veto";',
        '               else if(blocks[i].vetoReason == VETO_NO_SEPARATION) cancelReason = "Separation Veto";',
        '               else if(blocks[i].vetoReason == VETO_BROKEN)     cancelReason = "Block Broken";',
        '               else if(blocks[i].vetoReason == VETO_FLIPPED)    cancelReason = "Block Flipped";',
        '               else if(blocks[i].vetoReason == VETO_CORRELATION) cancelReason = "Vector Consensus Veto";',
        '               else if(blocks[i].pendingOrderCancel)           cancelReason = "Manual / Direction Conflict";',
        "               // v5.38: reuse the arm-time session so the cancel lands in the",
        "               // setup's ONE journal, with a fresh mint as the fallback.",
        "               string voSession = m_virtualOrders[v].sessionId;",
        '               if(voSession == "")',
        "                 {",
        "                  MqlDateTime ctm; TimeToStruct(TimeCurrent(), ctm);",
        '                  string cts = StringFormat("%04d%02d%02d-%02d%02d%02d", ctm.year, ctm.mon, ctm.day, ctm.hour, ctm.min, ctm.sec);',
        '                  voSession = StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, cts, blocks[i].serial);',
        "                 }",
        "               // v5.43 -- save/restore (see the physical-cancel path above).",
        "               string prevSession = m_sessionID;",
        "               m_journal.SetSessionID(voSession);",
        "               m_journal.LogCancellation(cancelReason);",
        '               if(prevSession != "") m_journal.SetSessionID(prevSession);',
    ]

    sub(L, old, new, "om.cleanup.virtual")

    # 13. The seed helper's rationale. The logic is unchanged, but its premise --
    #     "deriving the side from the block's phase (touches)" -- is no longer
    #     true: with the unconditional mapping GetDirectionForBlock() ignores
    #     touches entirely, so the physical/virtual ordering subtlety this
    #     guarded has evaporated. The override stays as belt-and-braces.
    sub(L, [
        "      // v5.48 -- the direction this basket is seeded with. Deriving it from the",
        "      // block's phase (touches) is the correct basis for a PHYSICAL fill,",
        "      // because that path seeds BEFORE it consumes the touch. A VIRTUAL fill",
        "      // consumes the touch first, so it hands the arm-time side in explicitly",
        "      // (overrideDir); otherwise GetDirectionForBlock() would flip the basket to",
        "      // the OPPOSITE of the MARKET order that just filled, desyncing the trail,",
        "      // the 1R basis, the journal and the High Table mirror for that whole leg.",
    ], [
        "      // The direction this basket is seeded with. v5.52 makes the default path",
        "      // trivially correct: GetDirectionForBlock() is now UNCONDITIONAL and no",
        "      // longer reads block.touches, so a fill that consumes the touch first",
        "      // still resolves the SAME side the MARKET order executed. overrideDir is",
        "      // kept as belt-and-braces -- the virtual fire already knows the exact",
        "      // side it sent, so passing it costs nothing and cannot desync the trail,",
        "      // the 1R basis, the journal or the High Table mirror for the leg.",
    ], "om.seed.note")

    # 14. The router's own header, so the STOP-style consequence is stated where
    #     a reader meets the router rather than only at its tail.
    sub(L, [
        border(3),
        box("PlaceOrArmOrder — HYBRID ROUTER (v5.40). Translates Pine", 3),
        box("strategy.entry(limit=...) into a resting BROKER limit when the", 3),
        box("entry price is legal for a pending order, and into an in-memory", 3),
        box("SVirtualOrder when price has already reached/through the zone.", 3),
        box("NO take-profit is attached on either path (exact mirror: exits", 3),
        box("are purely trail-based; localTP is stored on the block only for", 3),
        box("the Front-Run veto). A physical fill is caught by", 3),
        box("CheckPendingOrderFills(); a virtual arm fires its MARKET leg in", 3),
        box("CheckVirtualTriggers() on the cross.", 3),
        border(3),
    ], [
        border(3),
        box("PlaceOrArmOrder — HYBRID ROUTER (v5.40). Translates Pine", 3),
        box("strategy.entry(limit=...) into a resting BROKER limit when the", 3),
        box("entry price is legal for a pending order, and into an in-memory", 3),
        box("SVirtualOrder when price has already reached/through the zone.", 3),
        box("NO take-profit is attached on either path (exact mirror: exits", 3),
        box("are purely trail-based; localTP is stored on the block only for", 3),
        box("the Front-Run veto). A physical fill is caught by", 3),
        box("CheckPendingOrderFills(); a virtual arm fires its MARKET leg in", 3),
        box("CheckVirtualTriggers() on the cross.", 3),
        box("", 3),
        box("v5.52 CONTINUATION: with InpEntryStyle = ENTRY_FRONT_EDGE the", 3),
        box("entry is STOP-style (see the CONTINUATION NOTE at the routing", 3),
        box("tail), so the VIRTUAL arm is the ordinary path and a physical", 3),
        box("rest is the pullback case. Either way the trigger is a MARKET", 3),
        box("deal when the continuation level is crossed.", 3),
        border(3),
    ], "om.router.header")

    # 15. The armed-block dispatcher's rationale.
    sub(L, [
        "         // v5.40: Phase 1 (touches==0) and Phase 2 (touches>=1) BOTH route",
        "         // through PlaceOrArmOrder(). It is phase-aware — it rests a Phase 2",
        "         // entry at the shift-reversal outer boundary via InpShiftReversalEntry",
        "         // — and picks PHYSICAL (broker limit) vs VIRTUAL (CheckVirtualTriggers",
        "         // fires the MARKET leg on the cross) per setup, by live-price validity.",
        "         // This is the single execution path for every armed block.",
    ], [
        "         // v5.52: EVERY armed block routes through PlaceOrArmOrder(), which",
        "         // picks PHYSICAL (broker limit) vs VIRTUAL (CheckVirtualTriggers fires",
        "         // the MARKET leg on the cross) per setup by live-price validity. With",
        "         // the two-phase engine gone there is no Phase 2 to shift, so this is",
        "         // the single, phase-free execution path for every armed block.",
    ], "om.dispatch")

    # 16. The physical fill scanner's banner.
    sub(L, [
        "   //| The hybrid router may rest a real broker limit order. When one    |",
        "   //| fills, the broker removes the pending and opens a position whose  |",
        "   //| ticket is UNRELATED, so resolve it via the 3-tier detector, seed  |",
        "   //| the basket, and advance the two-phase lifecycle exactly as the    |",
        "   //| virtual trigger does. A pending that VANISHED without a position  |",
        "   //| is a cancel/expiry — just release the block's ticket reference.   |",
    ], [
        "   //| The hybrid router may rest a real broker limit order. When one    |",
        "   //| fills, the broker removes the pending and opens a position whose  |",
        "   //| ticket is UNRELATED, so resolve it via the 3-tier detector, seed  |",
        "   //| the basket, and consume the block's single continuation entry     |",
        "   //| exactly as the virtual trigger does. A pending that VANISHED      |",
        "   //| without a position is a cancel/expiry — just release the ticket.  |",
    ], "om.fills.banner")

    save_mql(path, L)


# --- COttoJournal.mqh ------------------------------------------------------

def patch_journal():
    path = os.path.join(ROOT, "COttoJournal.mqh")
    print("COttoJournal.mqh")
    L = mql(path)
    F = 65   # otto.mq5 and the journal use a 65-column text field

    # 1. LogSetupArmed's banner prose.
    sub(L, [
        "   //| v5.40: the hybrid router arms a setup EITHER as a physical       |",
        "   //| broker pending limit (price valid for a resting order) OR as an  |",
        "   //| in-memory virtual order (price already through the zone). This   |",
        "   //| CREATES the per-setup journal either way and stamps the two       |",
        "   //| facts the operator needs on the header: the R/C Phase (touch     |",
        "   //| 1 = Reversal, touch 2+ = Continuation) and the Execution Mode    |",
        "   //| (PHYSICAL vs VIRTUAL). The later entry/cancel/exit append into   |",
        "   //| the same file; the email ships the full snapshot.                |",
    ], [
        box("v5.40: the hybrid router arms a setup EITHER as a physical", 3, F),
        box("broker pending limit (price valid for a resting order) OR as an", 3, F),
        box("in-memory virtual order (price already through the zone). This", 3, F),
        box("CREATES the per-setup journal either way and stamps the two", 3, F),
        box("facts the operator needs on the header: the Logic and the", 3, F),
        box("Execution Mode (PHYSICAL vs VIRTUAL). v5.52: every setup is a", 3, F),
        box("single CONTINUATION entry, so the header no longer names a", 3, F),
        box("phase. The later entry/cancel/exit append into the same file;", 3, F),
        box("the email ships the full snapshot.", 3, F),
    ], "jn.armed.banner")

    # 2. The phase local + the Logic description in the same function.
    sub(L, [
        '      string phase    = (blk.touches == 0) ? "R (Phase 1 - S/R Reversal Bounce)" : "C (Phase 2 - Trend Continuation)";',
        '      string execMode = physical ? "PHYSICAL (Broker Limit Order)" : "VIRTUAL (In-Memory Arm)";',
    ], [
        "      // v5.52: constant. A block has exactly one entry -- the continuation",
        "      // (a breakout above Resistance or a breakdown below Support) -- so the",
        "      // header names that and never a phase.",
        '      string phase    = "C (Continuation - Breakout / Breakdown)";',
        '      string execMode = physical ? "PHYSICAL (Broker Limit Order)" : "VIRTUAL (In-Memory Arm)";',
    ], "jn.armed.phase")

    sub(L, [
        '      W("  Phase             : " + phase);',
    ], [
        '      W("  Logic             : " + phase);',
    ], "jn.armed.label")

    sub(L, [
        "      string actionDesc = \"\";",
        "      if(btype == BLOCK_SUPPORT)",
        '         actionDesc = (dir == DIR_LONG) ? "SUPPORT (Buying the S/R Reversal)" : "SUPPORT (Selling the Trend Continuation / Breakdown)";',
        "      else",
        '         actionDesc = (dir == DIR_SHORT) ? "RESISTANCE (Selling the S/R Reversal)" : "RESISTANCE (Buying the Trend Continuation / Breakout)";',
        '      W("  Logic             : " + actionDesc);',
    ], [
        "      string actionDesc = \"\";",
        "      if(btype == BLOCK_SUPPORT)",
        '         actionDesc = "SUPPORT (Selling the Trend Continuation / Breakdown)";',
        "      else",
        '         actionDesc = "RESISTANCE (Buying the Trend Continuation / Breakout)";',
        '      W("  Trade             : " + actionDesc);',
    ], "jn.armed.action")

    # 3. LogConversion has no writer left; remove the method and its banner.
    sub(L, [
        sborder(3),
        box("LOG CONVERSION (v5.36): Phase 1 dropped -> Phase 2 Continuation", 3, F),
        box("Appends a conversion notice to the session journal. v5.42: no", 3, F),
        box("longer emails -- LogExit() is the only sender.", 3, F),
        box("Unlike LogCancellation() this does NOT rename the session file", 3, F),
        box("to CANCELLED_ : the setup is still live, merely re-phased.", 3, F),
        sborder(3),
        "   void              LogConversion(ulong ticket, ENUM_BLOCK_TYPE btype, string reason)",
        "     {",
        "      if(!m_ready) return;",
        "      if(!OpenAppend())",
        "        {",
        "         if(!OpenWrite()) return;   // no prior file -> create it",
        "        }",
        '      W("================================================================");',
        '      W("[CONVERTED TO CONTINUATION] Session " + m_sessionID + " | " + m_symbol + " | " + TimeToString(TimeCurrent()));',
        '      W("  Ticket Dropped    : #" + IntegerToString((int)ticket));',
        '      W("  Block Polarity    : " + (btype == BLOCK_SUPPORT ? "SUPPORT" : "RESISTANCE"));',
        '      W("  Trigger Event     : " + reason);',
        '      W("  Next Action       : Phase 1 order cancelled; Block upgraded to Phase 2 Continuation.");',
        '      W("================================================================");',
        "      CloseHandle();",
        "      // v5.42: NO email here. A re-phase is an intermediate lifecycle stage,",
        "      // not a session endpoint -- LogExit() is the only writer that ships",
        "      // the aggregated session file.",
        "     }",
    ], [
        sborder(3),
        box("LOG CONVERSION — REMOVED (v5.52).", 3, F),
        box("", 3, F),
        box("A conversion notice ('Phase 1 dropped -> Phase 2 Continuation')", 3, F),
        box("has no meaning without the two-phase engine: no block is ever", 3, F),
        box("re-phased, so this method had no writer. Deleted rather than left", 3, F),
        box("inert so the next reader is not misled into thinking a conversion", 3, F),
        box("path still exists. A vetoed setup is now simply cancelled and", 3, F),
        box("journalled by LogCancellation(), which is the surviving path.", 3, F),
        sborder(3),
    ], "jn.conversion")

    save_mql(path, L)
# --- otto.mq5 --------------------------------------------------------------

def patch_otto():
    path = os.path.join(ROOT, "otto.mq5")
    print("otto.mq5")
    L = mql(path)

    # 1. The twin direction resolver for the portfolio bias vote. This is the
    #    third and last copy of the chokepoint; all three must agree.
    sub(L, [
        "//+------------------------------------------------------------------+",
        box("MAPPED direction of a zone for the portfolio bias vote.", 0, 65),
        box("", 0, 65),
        box("A Support / Resistance zone is DISCOVERED polarity, NOT trade", 0, 65),
        box("direction. HiveMind publishes a LONG / SHORT consensus into the", 0, 65),
        box("correlation matrix, and the conflict tie-breaker does not merely", 0, 65),
        box("prefer one of two opposing zones -- it DELETES the losing", 0, 65),
        box("polarity and vetoes its live orders. Reading the raw zone here", 0, 65),
        box("would therefore publish the EXACT OPPOSITE bias to how every", 0, 65),
        box("entry is mapped, so the hive mind would systematically veto", 0, 65),
        box("exactly the setups this build now takes.", 0, 65),
        box("", 0, 65),
        box("Byte-identical twin of COttoOrderManager::GetDirectionForBlock()", 0, 65),
        box("and COttoBlockManager::BlockDirection() (both private): the", 0, 65),
        box("standard two-phase mapping is PHASE-AWARE -- a fresh zone", 0, 65),
        box("(touches == 0) is a PHASE 1 BOUNCE (Support -> DIR_LONG) and a", 0, 65),
        box("zone that has consumed Touch 1 (touches >= 1) is a PHASE 2", 0, 65),
        box("REVERSAL that trades the S/R flip (Support -> DIR_SHORT).", 0, 65),
        "//+------------------------------------------------------------------+",
        "ENUM_TRADE_DIRECTION MappedZoneDirection(const SSniperBlock &b)",
        "  {",
        "   if(b.touches >= 1)",
        "      return (b.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Phase 2 — reversal (S/R flip)",
        "   return (b.type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;      // Phase 1 — normal bounce",
        "  }",
    ], [
        "//+------------------------------------------------------------------+",
        box("MAPPED direction of a zone for the portfolio bias vote.", 0, 65),
        box("", 0, 65),
        box("A Support / Resistance zone is DISCOVERED polarity, NOT trade", 0, 65),
        box("direction. HiveMind publishes a LONG / SHORT consensus into the", 0, 65),
        box("correlation matrix, and the conflict tie-breaker does not merely", 0, 65),
        box("prefer one of two opposing zones -- it DELETES the losing", 0, 65),
        box("polarity and vetoes its live orders. Reading the raw zone here", 0, 65),
        box("would therefore publish the EXACT OPPOSITE bias to how every", 0, 65),
        box("entry is mapped, so the hive mind would systematically veto", 0, 65),
        box("exactly the setups this build now takes.", 0, 65),
        box("", 0, 65),
        box("Twin of COttoOrderManager::GetDirectionForBlock() and", 0, 65),
        box("COttoBlockManager::BlockDirection() (both private). v5.52 PURE", 0, 65),
        box("CONTINUATION: the mapping is UNCONDITIONAL and reads no phase --", 0, 65),
        box("a RESISTANCE zone trades DIR_LONG (a breakout above it) and a", 0, 65),
        box("SUPPORT zone trades DIR_SHORT (a breakdown below it), which is", 0, 65),
        box("exactly Pine's can_place gate. All three copies must stay in", 0, 65),
        box("lockstep or this vote would oppose the entries it is meant to", 0, 65),
        box("arbitrate.", 0, 65),
        "//+------------------------------------------------------------------+",
        "ENUM_TRADE_DIRECTION MappedZoneDirection(const SSniperBlock &b)",
        "  {",
        "   return (b.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Support -> SHORT, Resistance -> LONG",
        "  }",
    ], "otto.twin")

    # 2. HiveMind's votes and tie-breaker prose.
    sub(L, [
        "      // Each live zone votes with the direction it would actually TRADE,",
        "      // not with the polarity it was discovered as: the two-phase mapping",
        "      // is PHASE-AWARE, so the vote uses the helper rather than the type.",
        "      mappedVote += (MappedZoneDirection(allBlocks[b]) == DIR_LONG) ? 1 : -1;",
    ], [
        "      // Each live zone votes with the direction it would actually TRADE,",
        "      // not with the polarity it was discovered as, so the vote uses the",
        "      // helper rather than the raw type.",
        "      mappedVote += (MappedZoneDirection(allBlocks[b]) == DIR_LONG) ? 1 : -1;",
    ], "otto.hm.vote")

    sub(L, [
        "   // The tie-breaker DELETES the zone whose MAPPED direction LOST the vote.",
        "   // Both DeleteBlockType arguments below carry an identical",
        "   // \"supportVote > 0 ? Long : Short\" discriminator:",
        "   //   in a two-polarity conflict (supportVote > 0) it picks polarity-vs-polarity;",
        "   //   with only a resistance zone live (supportVote == 0) it picks the polled",
        "   //   winner as a zone, so a resistance-only field cannot be deleted.",
        "   // Log lines and myBias are unchanged:",
        "   //   resolution == +1 -> peers Long  -> the SHORT-mapped zone is deleted",
        "   //   resolution == -1 -> peers Short -> the LONG-mapped  zone is deleted",
    ], [
        "   // The tie-breaker DELETES the zone whose MAPPED direction LOST the vote.",
        "   // Under v5.52 an unconditional mapping is in force -- RESISTANCE -> LONG,",
        "   // SUPPORT -> SHORT -- and the two a-priori branches below read it straight:",
        "   //   mappedVote > 0 -> peers Long  -> the SHORT-mapped (Support) side goes.",
        "   //   mappedVote < 0 -> peers Short -> the LONG-mapped (Resistance) side goes.",
        "   // The else branch is unchanged from the two-phase build.",
        "   //",
        "   // PRE-EXISTING, deliberately NOT touched here: the two DeleteBlockType()",
        "   // arguments inside the ResolveBidirectionalConflict() branches below look",
        "   // INVERTED against this comment and against their own 'kept Support' /",
        "   // 'kept Resistance' log lines (they pass BLOCK_RESISTANCE where a Long",
        "   // resolution implies Support and vice versa). The inversion predates this",
        "   // pivot -- it is a property of how a zone maps, which is not what changed --",
        "   // so it is recorded rather than fixed in a continuation-mechanism commit.",
    ], "otto.hm.tiebreak")

    # 3. STEP 4's dispatch comment.
    sub(L, [
        "      // Every armed block - Phase 1 (touches==0) and Phase 2 (touches==1)",
        "      // alike - now routes through the single in-memory virtual engine:",
        "      // PlaceOrdersForArmedBlocks() ARMS the order and the order manager's",
        "      // CheckVirtualTriggers() FIRES the MARKET leg once the level trades",
        "      // through. The old per-phase execution split is gone.",
    ], [
        "      // Every armed block routes through the single hybrid engine:",
        "      // PlaceOrdersForArmedBlocks() ARMS the order -- a resting broker limit",
        "      // when the price permits one, else an in-memory virtual order -- and the",
        "      // order manager's CheckVirtualTriggers() FIRES the MARKET leg once the",
        "      // continuation level trades through. v5.52 has no per-phase split.",
    ], "otto.step4")

    save_mql(path, L)


# --- driver ----------------------------------------------------------------

def main():
    print("=" * 74)
    print("v5.52 PURE-CONTINUATION PIVOT")
    print("=" * 74)
    patch_defines()
    patch_block_manager()
    patch_order_manager()
    patch_journal()
    patch_otto()
    print()
    print("patch applied cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
