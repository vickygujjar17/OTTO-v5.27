"""
v5.66 source patch -- DEEP FRONT-EDGE ENTRY + RE-ANCHORED BREAK VETO.

Geometry: entry = NEAR edge +/- (2.0*blockHeight + 1.5*ATR), i.e.
blockHeight + 1.5*ATR BEYOND the far edge the breakout crosses.  slDist is
untouched at blockHeight + 0.5*ATR, so the stop lands 1.0*ATR past that far
edge with exactly one slDist of separation.

The break veto moves with the entry: through v5.65 it sat ATRBufferBreak*ATR
(= 0.5*ATR) past the PHYSICAL far edge, which is blockHeight + 1.0*ATR BEFORE
the new fill -- every armed block would have vetoed itself VETO_BROKEN (and
had its pending cancelled) on the way in.

Every replacement is an exact literal with an asserted match count, so a
drifted anchor aborts instead of silently patching the wrong line.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    p = os.path.join(ROOT, name)
    raw = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
    if "\r\n" not in raw:
        raise SystemExit("ABORT: %s is not CRLF" % name)
    return raw.replace("\r\n", "\n")


def save(name, text):
    p = os.path.join(ROOT, name)
    io.open(p, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(text.replace("\n", "\r\n"))


def rep(text, old, new, count, label):
    found = text.count(old)
    if found != count:
        raise SystemExit("ABORT %s: matched %d, expected %d"
                         % (label, found, count))
    print("  ok   %s" % label)
    return text.replace(old, new)


# ======================================================================
# 1. COttoBlockManager.mqh -- BlockEntryPrice() body
# ======================================================================
bm = load("COttoBlockManager.mqh")

BM_BODY_OLD = """      double slDist = b.blockHeight + (0.5 * atr);
      return (BlockDirection(b) == DIR_LONG) ? (b.bottom + slDist) : (b.top - slDist);"""

BM_BODY_NEW = """      // Deep front-edge: 2.0 block heights + 1.5 ATR out from the NEAR edge,
      // i.e. blockHeight + 1.5*ATR beyond the FAR edge. slDist is untouched,
      // so the stop keeps exactly one slDist of separation from the entry.
      double gap = (2.0 * b.blockHeight) + (1.5 * atr);
      return (BlockDirection(b) == DIR_LONG) ? (b.bottom + gap) : (b.top - gap);"""

bm = rep(bm, BM_BODY_OLD, BM_BODY_NEW, 1, "BM body (BlockEntryPrice)")

# ----------------------------------------------------------------------
# 2. BlockEntryPrice() header, paragraph by paragraph (the boxed //|
#    opening and the closing twin sentence stay verbatim).
# ----------------------------------------------------------------------
BM_P1_OLD = """   // EXTREME FRONT-RUNNING ANTICIPATION. The entry sits 0.5*ATR BEYOND the
   // FAR edge the breakout crosses (b.top for a LONG, b.bottom for a SHORT),
   // so at arm time price is still short of it and the trigger is a genuine
   // STOP cross rather than a resting limit."""

BM_P1_NEW = """   // DEEP FRONT-EDGE ANTICIPATION. The entry is measured OUT from the NEAR
   // edge -- the edge the arming close clears: b.bottom for a RESISTANCE
   // breakout, b.top for a SUPPORT breakdown -- by two block heights plus one
   // and a half ATRs (gap, below). The far edge is one blockHeight past the
   // near edge, so the fill lands blockHeight + 1.5*ATR BEYOND the FAR edge
   // the breakout crosses: a deep continuation entry, not a hair past it. At
   // arm time price has only just cleared the NEAR edge, so it is still short
   // of this price and the trigger stays a genuine STOP cross, never a
   // resting limit."""

bm = rep(bm, BM_P1_OLD, BM_P1_NEW, 1, "BM header P1")

BM_P2_OLD = """   // slDist = blockHeight + 0.5*ATR and the far edge is one blockHeight past
   // the near edge, so the mapped entry collapses to exactly 0.5*ATR past the
   // FAR edge:
   //   LONG : b.bottom + slDist = b.top    + 0.5*ATR
   //   SHORT: b.top    - slDist = b.bottom - 0.5*ATR
   // The SL then lands on the OPPOSITE edge of the zone, a full slDist back
   // (b.bottom for the LONG, b.top for the SHORT), so entry<->SL stays exactly
   // slDist and the risk distance is unchanged by the anticipation entry --
   // the whole entry/SL cluster is simply translated by 0.5*ATR in the
   // breakout direction, never a wider stop."""

BM_P2_NEW = """   // slDist is UNCHANGED at blockHeight + 0.5*ATR, so the stop lands
   //   LONG : b.bottom + gap - slDist = b.top    + 1.0*ATR
   //   SHORT: b.top    - gap + slDist = b.bottom - 1.0*ATR
   // -- exactly 1.0*ATR past the FAR edge and a full slDist back from the
   // entry. The anticipation offset is carried by the ENTRY alone, never by a
   // wider stop, so entry<->SL stays exactly slDist and every rung that reads
   // 1R (pyramid ladder, breakeven, trail) is untouched by this geometry."""

bm = rep(bm, BM_P2_OLD, BM_P2_NEW, 1, "BM header P2")

BM_P3_OLD = """   // FIX (v5.57) -- the RR PROJECTION did NOT follow the entry out to the
   // anticipation offset. The Front-Run veto still projects its target from
   // the FAR edge, not from the entry: a LONG target = b.top + tpRR*slDist, a
   // SHORT target = b.bottom - tpRR*slDist. Anchoring the projection to the
   // translated entry would have stretched it a further 0.5*ATR past the far
   // edge -- measurably harsher than every build before the anticipation
   // entry, i.e. it would veto setups that were never front-runs. See the
   // front-run block in ProcessBlocks() for the arithmetic."""

BM_P3_NEW = """   // THE BREAK VETO MOVES WITH THE ENTRY (v5.66). Through v5.65 the break test
   // fired ATRBufferBreak*ATR past the PHYSICAL far edge. That threshold now
   // sits blockHeight + 1.0*ATR BEFORE this entry, so a breakout walking in
   // through the zone would arm, then veto itself VETO_BROKEN -- and
   // CancelOrdersForInvalidBlocks() would drop the pending order -- long
   // before price could ever reach the fill. ProcessBlocks() therefore
   // anchors the break on THIS price: the zone is invalidated once the close
   // clears ATRBufferBreak*ATR beyond BlockEntryPrice(). The zone-to-direction
   // mapping is unchanged (SUPPORT breaks DOWN, RESISTANCE breaks UP); only
   // the level moved out, exactly as the stop did.
   //
   // TARGET ANCHOR (FIX v5.57, deliberately unchanged). The target is still
   // projected from the FAR edge the breakout crosses, never from this entry:
   // a LONG target = b.top + tpRR*slDist, a SHORT target = b.bottom -
   // tpRR*slDist. Anchoring it on the entry would stretch the target a further
   // blockHeight + 1.5*ATR and veto setups that were never front-runs. The
   // consequence, derived in full in the front-run block of ProcessBlocks(),
   // is that the reward-to-risk taken off the ENTRY is now well below
   // InpMaxRR -- and turns NEGATIVE for InpMaxRR below 3.0, because the entry
   // is out by blockHeight + 1.5*ATR while the target is only InpMaxRR*slDist
   // from the far edge. Keep InpMaxRR above 3.0; the shipped 4.0 is safe."""

bm = rep(bm, BM_P3_OLD, BM_P3_NEW, 1, "BM header P3")

# ----------------------------------------------------------------------
# 3. ProcessBlocks(): the front-run projection's REALIZED RR derivation
# ----------------------------------------------------------------------
BMP_OLD = """            // v5.34 Part 2 once re-anchored this projection to the Phase 2
            // outer-boundary entry; v5.52 dropped Phase 2 and FIX (v5.57) pins the
            // FAR-EDGE anchor. The target is measured from the edge the
            // breakout crosses (b.top LONG / b.bottom SHORT), NOT from the
            // anticipation entry BlockEntryPrice() returns: that entry sits a
            // further 0.5*ATR past this edge, so projecting from it would move
            // the target 0.5*ATR beyond what every pre-anticipation build
            // projected -- vetoing setups that were never front-runs. MATH:
            // slDist = blockHeight + 0.5*ATR and BlockEntryPrice() maps the
            // entry a further 0.5*ATR past the far edge; every pre-
            // anticipation build anchored its target ON that far edge, so this
            // far-edge form reproduces the retired
            // 'entryPrice +/- tpRR*slDist' target of the last far-edge build
            // bit for bit. Once the order rests, b.localTP supersedes this.
            // REALIZED RR (front-edge entry): the entry sits 0.5*ATR beyond
            // the far edge while the target is anchored TO that edge, so the
            // reward-to-risk taken off the ENTRY is InpMaxRR - (0.5*ATR /
            // slDist) -- slightly less than InpMaxRR, and less still the wider
            // the ATR runs against the zone height (a 1.0-high zone at ATR 2.0
            // with InpMaxRR 4.0 -> 3.5R). Intentional: the target reproduces
            // the pre-anticipation projection and keeps the veto strict. Do
            // NOT "fix" it by anchoring the target on the entry."""

BMP_NEW = """            // v5.34 Part 2 once re-anchored this projection to the Phase 2
            // outer-boundary entry; v5.52 dropped Phase 2 and FIX (v5.57) pins
            // the FAR-EDGE anchor. The target is measured from the edge the
            // breakout crosses (b.top LONG / b.bottom SHORT), NOT from the
            // deep entry BlockEntryPrice() returns: that entry now sits a
            // full blockHeight + 1.5*ATR past this edge, so projecting the
            // target from it would move the target just as far beyond what
            // every pre-anticipation build projected -- vetoing setups that
            // were never front-runs. This is the one price the Front-Run veto
            // is allowed to judge; COttoOrderManager::PlaceOrArmOrder()
            // projects it with identical arithmetic, and once the order rests
            // b.localTP supersedes this anyway.
            //
            // REALIZED RR (deep front-edge entry). Long side, near edge at 0,
            // far edge at bh, slDist = bh + 0.5*a, entry = 2*bh + 1.5*a, and
            // the target anchored on the far edge:
            //   TP     = bh + rr*(bh + 0.5*a)
            //   reward = TP - entry = (rr - 1)*bh + (0.5*rr - 1.5)*a
            //   risk   = slDist     =  bh        + 0.5*a
            // The reward term is the reason InpMaxRR MUST stay above 3.0: as
            // the zone height shrinks toward zero, reward -> (0.5*rr - 1.5)*a,
            // which is NEGATIVE for rr < 3.0 -- the far-edge target would land
            // BEHIND a deep entry and every such setup would be vetoed as a
            // front-run that never existed. Above 3.0 the target is always
            // beyond the entry, but the realized multiple is still well under
            // InpMaxRR and falls further as ATR grows against the zone height:
            //   bh 1.0 / a 2.0 / rr 4.0 -> 2.0R   (the shipped defaults)
            //   bh 2.0 / a 1.0 / rr 4.0 -> 2.8R
            //   bh 0.5 / a 4.0 / rr 4.0 -> 1.4R
            // and as a -> 0 it approaches rr - 1, still strictly below rr.
            // Intentional: the target reproduces the pre-anticipation
            // projection and keeps the veto strict. Do NOT "fix" it by
            // anchoring the target on the entry."""

bm = rep(bm, BMP_OLD, BMP_NEW, 1, "BM ProcessBlocks REALIZED RR")

# ----------------------------------------------------------------------
# 4. UpdateIntraBar(): the twin derivation, kept in lockstep
# ----------------------------------------------------------------------
BMI_OLD = """            // FIX (v5.57) measures the target from the edge the breakout crosses, not
            // from the 0.5*ATR anticipation entry, so both passes judge one
            // price; see the detailed derivation in ProcessBlocks().
            // REALIZED RR (front-edge entry): the entry sits 0.5*ATR beyond
            // the far edge while the target is anchored TO that edge, so the
            // reward-to-risk taken off the ENTRY is InpMaxRR - (0.5*ATR /
            // slDist) -- slightly less than InpMaxRR, and less still the wider
            // the ATR runs against the zone height (a 1.0-high zone at ATR 2.0
            // with InpMaxRR 4.0 -> 3.5R). Intentional: the target reproduces
            // the pre-anticipation projection and keeps the veto strict. Do
            // NOT "fix" it by anchoring the target on the entry."""

BMI_NEW = """            // FIX (v5.57) measures the target from the edge the breakout crosses
            // -- never from the deep anticipation entry, which sits a full
            // blockHeight + 1.5*ATR past it -- so both passes judge one price;
            // see the detailed derivation in ProcessBlocks().
            // REALIZED RR (deep front-edge entry): reward from the ENTRY is
            // (rr - 1)*bh + (0.5*rr - 1.5)*a over (bh + 0.5*a), which turns
            // NEGATIVE for InpMaxRR below 3.0 as the zone height shrinks. That
            // is the floor this build depends on; the full arithmetic, and the
            // shipped-defaults 2.0R, live in ProcessBlocks(). Intentional: the
            // target reproduces the pre-anticipation projection and keeps the
            // veto strict. Do NOT "fix" it by anchoring the target on the
            // entry."""

bm = rep(bm, BMI_OLD, BMI_NEW, 1, "BM UpdateIntraBar REALIZED RR")

# ----------------------------------------------------------------------
# 5. The BREAK test: re-anchored on BlockEntryPrice()
# ----------------------------------------------------------------------
BRK_OLD = """         // --- BREAK / FLIP (a break is the edge the trade cannot use) ---
         // Pine's S/R-BREACH / POLARITY-FLIP detector, NOT an entry validator:
         // it asks whether the close went out the FAR edge of the zone, which
         // reclassifies the zone and blacklists it. That is why it names the
         // OPPOSITE edge from arming above -- arming is the near side the trade
         // breaks out toward, the break is the far side. They are mutually
         // exclusive by construction, so a block can never arm here and be
         // vetoed BROKEN in the same pass.
         //
         // KEPT VERBATIM through the v5.52 pivot; only the dead Phase 2 branch
         // was removed. That v5.49 branch tested the near edge, which for an
         // armed block was already true -- it would have vetoed the live setup
         // on its own arming close.
         bool isBroken =
            (b.type == BLOCK_SUPPORT && close1 < b.bottom - ATRBufferBreak * atrNow) ||
            (b.type == BLOCK_RESISTANCE && close1 > b.top + ATRBufferBreak * atrNow);"""

BRK_NEW = """         // --- BREAK / FLIP (a break is the edge the trade cannot use) ---
         // Pine's S/R-BREACH / POLARITY-FLIP detector, NOT an entry validator:
         // it asks whether the close went out the FAR edge of the zone, which
         // reclassifies the zone and blacklists it. The zone-to-direction
         // mapping is untouched (a SUPPORT breaks DOWN, a RESISTANCE breaks
         // UP), so arming above still looks past the NEAR edge while this
         // looks past the FAR side.
         //
         // RE-ANCHORED (v5.66) on the ENTRY, not on the physical far edge.
         // Through v5.65 this test fired ATRBufferBreak*ATR (0.5*ATR) past
         // b.bottom / b.top. With the deep front-edge entry the fill sits
         // blockHeight + 1.5*ATR past that edge, so the retired threshold
         // lands blockHeight + 1.0*ATR BEFORE the entry: a breakout walking
         // in through the zone would arm, then veto itself VETO_BROKEN -- and
         // CancelOrdersForInvalidBlocks() would drop its pending -- before
         // price could reach the fill. Every armed block died on approach.
         // Anchoring on BlockEntryPrice() keeps the two mutually exclusive in
         // the intended sense: arming clears the near edge, a break clears the
         // OUTER boundary of this block's own trade, so the block is only
         // invalidated once price has gone ATRBufferBreak*ATR past the price
         // it was waiting to be filled at.
         //
         // breakLevel honours InpEntryStyle, so under ENTRY_MIDPOINT the
         // invalidation anchors on the midpoint -- the same price that style
         // would have entered at. Deliberate: a block's invalidation always
         // tracks the entry it was actually trying to take.
         double breakLevel = BlockEntryPrice(b, atrNow);
         bool isBroken =
            (b.type == BLOCK_SUPPORT && close1 < breakLevel - ATRBufferBreak * atrNow) ||
            (b.type == BLOCK_RESISTANCE && close1 > breakLevel + ATRBufferBreak * atrNow);"""

bm = rep(bm, BRK_OLD, BRK_NEW, 1, "BM break test re-anchored")
save("COttoBlockManager.mqh", bm)
print("COttoBlockManager.mqh complete")

# ======================================================================
# 6. COttoOrderManager.mqh -- CalcEntryPrice() header + body
# ======================================================================
om = load("COttoOrderManager.mqh")

OM_HEAD_OLD = """   //| ENTRY_FRONT_EDGE: EXTREME FRONT-RUNNING ANTICIPATION. The entry |
   //| sits 0.5*ATR BEYOND the FAR edge the breakout crosses (b.top for|
   //| a LONG, b.bottom for a SHORT), so at arm time price is still    |
   //| short of it and the trigger is a genuine STOP cross, not a      |
   //| resting limit.                                                  |
   //|                                                                 |
   //| slDist = blockHeight + 0.5*ATR and the far edge is one          |
   //| blockHeight past the near edge, so the mapped entry collapses   |
   //| to exactly 0.5*ATR past the FAR edge:                           |
   //|   LONG : block.bottom + slDist = block.top    + 0.5*ATR         |
   //|   SHORT: block.top    - slDist = block.bottom - 0.5*ATR         |
   //| The SL then lands on the OPPOSITE edge, a full slDist back      |
   //| (block.bottom for the LONG, block.top for the SHORT), so        |
   //| entry<->SL stays exactly slDist and the risk distance is        |
   //| unchanged by the anticipation entry -- the entry/SL cluster     |
   //| simply translates by 0.5*ATR in the breakout direction.         |
   //| FIX (v5.57): the TP PROJECTION does NOT follow the entry; it is |
   //| anchored on the FAR edge (see PlaceOrArmOrder) so the Front-Run |
   //| veto keeps the strictness of every pre-anticipation build. Twin |
   //| of COttoBlockManager::BlockEntryPrice(); they must stay         |
   //| identical or the Front-Run veto would judge a different price   |
   //| from the order's."""


def om_box(lines, width=64):
    """Render the boxed //| header at the file's exact 72-column frame.

    The shipped box is uniformly 72 chars wide ("   //| " + 64 + "|"), so a
    hand-padded replacement would rot the alignment the moment a word moved.
    """
    out = []
    for ln in lines:
        if len(ln) > width:
            raise SystemExit("ABORT: box line is %d wide (>%d): %r"
                             % (len(ln), width, ln))
        out.append("   //| " + ln.ljust(width) + "|")
    return "\n".join(out)


OM_HEAD_NEW = om_box([
    "ENTRY_FRONT_EDGE: DEEP FRONT-EDGE ANTICIPATION. The entry is",
    "measured OUT from the NEAR edge -- the edge the arming close",
    "clears: block.bottom for a RESISTANCE breakout, block.top",
    "for a SUPPORT breakdown -- by 2.0 block heights + 1.5 ATRs",
    "(gap, below). The far edge is one blockHeight past the near",
    "edge, so the fill lands blockHeight + 1.5*ATR BEYOND the FAR",
    "edge the breakout crosses. At arm time price has only just",
    "cleared the NEAR edge, so it is still short of this price and",
    "the trigger is a genuine STOP cross, not a resting limit.",
    "",
    "slDist is UNCHANGED at blockHeight + 0.5*ATR, so the stop",
    "lands:",
    "  LONG : block.bottom + gap - slDist = block.top    + 1.0*ATR",
    "  SHORT: block.top    - gap + slDist = block.bottom - 1.0*ATR",
    "-- exactly 1.0*ATR past the FAR edge and a full slDist back",
    "from the entry. entry<->SL therefore stays exactly slDist, so",
    "every 1R rung (pyramid ladder, breakeven, trail) is untouched:",
    "the anticipation offset is carried by the ENTRY alone, never",
    "by a wider stop.",
    "FIX (v5.57): the TP PROJECTION does NOT follow the entry; it",
    "is anchored on the FAR edge (see PlaceOrArmOrder) so the",
    "Front-Run veto keeps the strictness of every pre-anticipation",
    "build. The entry being a full blockHeight + 1.5*ATR out is",
    "why InpMaxRR must stay above 3.0: at or below it the far-edge",
    "target can land on the wrong side of the entry (see the",
    "ProcessBlocks derivation). Twin of",
    "COttoBlockManager::BlockEntryPrice(); they must stay identical",
    "or the Front-Run veto would judge a different price from the",
    "order's.",
])

om = rep(om, OM_HEAD_OLD, OM_HEAD_NEW, 1, "OM CalcEntryPrice header")

OM_BODY_OLD = """      double slDist = block.blockHeight + (0.5 * atr);
      return (GetDirectionForBlock(block) == DIR_LONG) ? (block.bottom + slDist)
                                                       : (block.top - slDist);"""

OM_BODY_NEW = """      // Deep front-edge, twin of COttoBlockManager::BlockEntryPrice():
      // 2.0 block heights + 1.5 ATR out from the NEAR edge. slDist is
      // untouched, so the stop keeps exactly one slDist of separation.
      double gap = (2.0 * block.blockHeight) + (1.5 * atr);
      return (GetDirectionForBlock(block) == DIR_LONG) ? (block.bottom + gap)
                                                       : (block.top - gap);"""

om = rep(om, OM_BODY_OLD, OM_BODY_NEW, 1, "OM CalcEntryPrice body")

# --- PlaceOrArmOrder: the REALIZED RR derivation ----------------------
OMP_OLD = """      // FIX (v5.57) — FAR-EDGE ANCHOR. The target is measured from the edge the
      // breakout crosses (block.top LONG / block.bottom SHORT), NOT from the
      // anticipation `entryPrice`, which sits 0.5*ATR beyond that edge.
      // Projecting from the entry pushed the target a further 0.5*ATR out --
      // harsher than every pre-anticipation build -- so the Front-Run veto
      // would miss setups it used to catch. slDist = blockHeight + 0.5*ATR and
      // the anticipation entry maps a further 0.5*ATR past the far edge; every
      // pre-anticipation build anchored its target ON that far edge, so this
      // form reproduces the retired 'entryPrice +/- tpRR*slDist' target bit
      // for bit. COttoBlockManager::ProcessBlocks projects identically.
      // REALIZED RR (front-edge entry): the entry sits 0.5*ATR beyond the
      // far edge while the target is anchored TO that edge, so the reward-
      // to-risk taken off the ENTRY is InpMaxRR - (0.5*ATR / slDist) --
      // slightly less than InpMaxRR, and less still the wider the ATR runs
      // against the zone height (a 1.0-high zone at ATR 2.0 with InpMaxRR
      // 4.0 -> 3.5R). Intentional: the target reproduces the pre-
      // anticipation projection and keeps the veto strict. Do NOT "fix" it
      // by anchoring the target on the entry."""

OMP_NEW = """      // FIX (v5.57) — FAR-EDGE ANCHOR. The target is measured from the edge the
      // breakout crosses (block.top LONG / block.bottom SHORT), NOT from the
      // deep anticipation `entryPrice`, which sits a full blockHeight +
      // 1.5*ATR beyond that edge. Projecting from the entry pushed the target
      // just as far out -- harsher than every pre-anticipation build -- so the
      // Front-Run veto would miss setups it used to catch. COttoBlockManager::
      // ProcessBlocks projects identically.
      // REALIZED RR (deep front-edge entry): with near edge at 0, far edge at
      // bh, slDist = bh + 0.5*a and entry = 2*bh + 1.5*a, the reward from the
      // ENTRY to the far-edge-anchored target is
      //     (rr - 1)*bh + (0.5*rr - 1.5)*a
      // over a risk of (bh + 0.5*a). As bh shrinks that reward turns NEGATIVE
      // for InpMaxRR < 3.0 -- the target would sit behind the entry -- so 3.0
      // is a hard floor for this geometry. With the shipped defaults (bh 1.0,
      // ATR 2.0, rr 4.0) the realized multiple is 2.0R, and it falls further
      // as ATR grows against the zone height (bh 0.5 / a 4.0 -> 1.4R).
      // Intentional: the target reproduces the pre-anticipation projection and
      // keeps the veto strict. Do NOT "fix" it by anchoring the target on the
      // entry."""

om = rep(om, OMP_OLD, OMP_NEW, 1, "OM PlaceOrArmOrder REALIZED RR")

# --- Router note: the fill is now blockHeight + 1.5*ATR out -----------
OMR_OLD = """      // FRONT-RUNNING ANTICIPATION NOTE. FIX (v5.57). With InpEntryStyle =
      // ENTRY_FRONT_EDGE the entry sits 0.5*ATR beyond the FAR edge -- a
      // Resistance LONG at b.bottom+slDist (= b.top+0.5*ATR), a Support SHORT
      // at b.top-slDist (= b.bottom-0.5*ATR) -- so the trigger is STOP-style
      // (breakout): the market must still RISE to a LONG entry and FALL to a
      // SHORT entry, while the SL sits a full slDist back on the opposite
      // edge. At arm time price has closed clear of the NEAR edge, i.e. it is
      // still short of the entry, so the limit test below is FALSE: the setup
      // arms VIRTUALLY and CheckVirtualTriggers() fires a MARKET deal on the
      // cross. That inverted cross is the ordinary path, not a fallback; a
      // physical rest is only ever used when a stale armed price happens to
      // sit on the limit side of a moved market."""

OMR_NEW = """      // DEEP FRONT-EDGE ANTICIPATION NOTE. With InpEntryStyle =
      // ENTRY_FRONT_EDGE the entry is measured out from the NEAR edge by
      // 2.0*blockHeight + 1.5*ATR -- a Resistance LONG at b.bottom+gap
      // (= b.top + blockHeight + 1.5*ATR), a Support SHORT at b.top-gap
      // (= b.bottom - blockHeight - 1.5*ATR) -- so the trigger is STOP-style
      // (breakout): the market must still RISE to a LONG entry and FALL to a
      // SHORT entry, while the SL sits a full slDist back, 1.0*ATR beyond the
      // FAR edge. At arm time price has only just closed clear of the NEAR
      // edge, i.e. it is still far short of the entry, so the limit test below
      // is FALSE: the setup arms VIRTUALLY and CheckVirtualTriggers() fires a
      // MARKET deal on the cross. That inverted cross is the ordinary path,
      // not a fallback; a physical rest is only ever used when a stale armed
      // price happens to sit on the limit side of a moved market. This deeper
      // offset makes the virtual route the norm by a wide margin -- which is
      // why the break veto had to be re-anchored on this same price (v5.66):
      // the retired far-edge threshold sat blockHeight + 1.0*ATR short of the
      // fill and vetoed every block on approach."""

om = rep(om, OMR_OLD, OMR_NEW, 1, "OM router anticipation note")

# --- CheckVirtualTriggers: the far-edge twin's wording ----------------
OMV_OLD = """         // FIX (v5.58) FAR-EDGE ANCHOR, twin of PlaceOrArmOrder() above. localTP
         // MUST stay the one price the Front-Run veto reads (the veto compares
         // the market against block.localTP once the order rests), so the
         // recovered projection is re-derived with the identical arithmetic
         // rather than from the trigger: target = farEdge +/- tpRR * slDist.
         // The retired form (vo.triggerPrice +/- slDist) anchored on the
         // anticipation ENTRY -- a further 0.5*ATR out -- which is exactly the
         // stretch PlaceOrArmOrder was re-anchored to undo; leaving it here
         // would let the veto judge a different price after a virtual fire.
         // At the reachable arm paths localTP already holds this exact value,
         // so this is a consistency guarantee, not a behaviour change."""

OMV_NEW = """         // FIX (v5.58) FAR-EDGE ANCHOR, twin of PlaceOrArmOrder() above. localTP
         // MUST stay the one price the Front-Run veto reads (the veto compares
         // the market against block.localTP once the order rests), so the
         // recovered projection is re-derived with the identical arithmetic
         // rather than from the trigger: target = farEdge +/- tpRR * slDist.
         // The retired form anchored on the anticipation ENTRY -- now a full
         // blockHeight + 1.5*ATR out, the stretch PlaceOrArmOrder was
         // re-anchored to undo -- and leaving it here would let the veto judge
         // a different price after a virtual fire. At the reachable arm paths
         // localTP already holds this exact value, so this is a consistency
         // guarantee, not a behaviour change."""
om = rep(om, OMV_OLD, OMV_NEW, 1, "OM virtual-fill twin wording")
save("COttoOrderManager.mqh", om)
print("COttoOrderManager.mqh complete")

# ======================================================================
# 7. COttoJournal.mqh -- deep front-edge wording in BOTH records
# ======================================================================
jr = load("COttoJournal.mqh")

JR_TRIG_OLD = ('      W("  Trigger Price     : " + FmtPrice(entryPrice) + " (Front-Run: '
               'far edge +/- 0.5*ATR)");')
JR_TRIG_NEW = ('      W("  Trigger Price     : " + FmtPrice(entryPrice) + " (Front-Run: '
               'near edge +/- 2.0*H +/- 1.5*ATR)");')
jr = rep(jr, JR_TRIG_OLD, JR_TRIG_NEW, 1, "JR armed trigger price")

# The SUPPORT/RESISTANCE literals are byte-identical in BOTH records
# (LogSetupArmed and LogEntry), so each replacement must match exactly twice
# -- a single hit would mean one of the two records drifted, which is exactly
# how the v5.58 journal desync happened. Asserting 2 keeps them in lockstep.
# The armed record names the exact quantities that build the fill: two block
# heights, one and a half ATRs, and the stop one ATR past the far edge. A bare
# "0.5*ATR" here would describe a build that no longer ships.
JR_S_OLD = ('actionDesc = "SUPPORT (Front-Run Breakdown / Sell at Bottom Edge - '
            '0.5*ATR | SL at Top Edge)";')
JR_S_NEW = ('actionDesc = "SUPPORT (Front-Run Breakdown / Sell at Near Edge - '
            '2.0*H - 1.5*ATR | SL at Far Edge + 1.0*ATR)";')
jr = rep(jr, JR_S_OLD, JR_S_NEW, 2, "JR SUPPORT wording (armed + entry)")

JR_R_OLD = ('actionDesc = "RESISTANCE (Front-Run Breakout / Buy at Top Edge + '
            '0.5*ATR | SL at Bottom Edge)";')
JR_R_NEW = ('actionDesc = "RESISTANCE (Front-Run Breakout / Buy at Near Edge + '
            '2.0*H + 1.5*ATR | SL at Far Edge + 1.0*ATR)";')
jr = rep(jr, JR_R_OLD, JR_R_NEW, 2, "JR RESISTANCE wording (armed + entry)")

save("COttoJournal.mqh", jr)
print("COttoJournal.mqh complete")

# ======================================================================
# 8. OttoDefines.mqh -- the ENTRY_FRONT_EDGE contract comment
# ======================================================================
df = load("OttoDefines.mqh")

DF_OLD = """   ENTRY_FRONT_EDGE = 1     // EXTREME FRONT-RUNNING ANTICIPATION. FIX (v5.57):
                            //   entry 0.5*ATR BEYOND the far edge the breakout
                            //   crosses, = b.top + 0.5*ATR for a Resistance LONG
                            //   and b.bottom - 0.5*ATR for a Support SHORT --
                            //   a full slDist back the SL lands on the opposite
                            //   edge (b.bottom / b.top), so entry<->SL stays
                            //   exactly slDist. The trigger is STOP-style and
                            //   fires on the cross (see CheckVirtualTriggers)."""

DF_NEW = """   ENTRY_FRONT_EDGE = 1     // EXTREME FRONT-RUNNING ANTICIPATION -- DEEP front
                            //   edge. The entry is measured OUT from the NEAR
                            //   edge by 2.0*blockHeight + 1.5*ATR:
                            //     Resistance LONG : b.bottom + gap
                            //     Support    SHORT: b.top    - gap
                            //   Since the far edge is one blockHeight past the
                            //   near edge, the fill lands blockHeight + 1.5*ATR
                            //   BEYOND the far edge the breakout crosses.
                            //   slDist is unchanged at blockHeight + 0.5*ATR, so
                            //   the SL sits 1.0*ATR past that far edge and
                            //   entry<->SL stays exactly slDist -- the offset is
                            //   carried by the entry alone, never a wider stop.
                            //   The trigger is STOP-style and fires on the cross
                            //   (see CheckVirtualTriggers); the BREAK veto is
                            //   anchored on this same entry price, so a block is
                            //   only invalidated ATRBufferBreak*ATR beyond the
                            //   price it was waiting to fill at."""

df = rep(df, DF_OLD, DF_NEW, 1, "DEFS ENTRY_FRONT_EDGE comment")
save("OttoDefines.mqh", df)
print("OttoDefines.mqh complete")
print("\nPATCH COMPLETE")
