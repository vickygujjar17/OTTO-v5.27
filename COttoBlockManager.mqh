//+------------------------------------------------------------------+
//|                                                COttoBlockManager.mqh |
//|         MODULE 4 — Wick1+Wick2 Formation & v4.70 Veto Funnel     |
//|              OTTO EA — exact Pine v4.70 block logic port          |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.80"

#ifndef __OTTO_BLOCK_MANAGER__
#define __OTTO_BLOCK_MANAGER__

#include "OttoDefines.mqh"
#include "COttoMarketStructure.mqh"

//+------------------------------------------------------------------+
//| COttoBlockManager class                                         |
//| Owns the S/R block array and runs the v5.80 state machine:       |
//| W1/W2 formation, the two CREATION-time vetoes (Separation &       |
//| Sizing), arming, break/flip — with robust memory management      |
//| (blocks are released when vetoed, flipped, broken or expired).   |
//|                                                                  |
//| v5.80 ARCHITECTURAL STRIP: every POST-ARMING execution-path veto |
//| is gone -- stale, front-run, near-miss, momentum and FVG are no  |
//| longer evaluated anywhere, and neither is the external currency  |
//| correlation filter. The funnel is now exactly:                   |
//|   deletion -> hasExited -> arming -> break/flip -> persist       |
//| Break/flip is a STRUCTURAL test on the physical far edge and is  |
//| therefore kept byte-identical (see ProcessBlocks).               |
//+------------------------------------------------------------------+
class COttoBlockManager
  {
private:
   string                  m_symbol;
   COttoMarketStructure   *m_marketStruct;

   // --- Blocks array + memory management ---
   SSniperBlock            m_blocks[];       // active blocks (serial deletion)
   int                     m_blockCount;

   // --- ATR handle + history for the 8-bar veto scan ---
   int                     m_atrHandle;
   double                  m_atrHistory[MAX_ATR_HISTORY];
   double                  m_currentATR;

   // --- W1 persistence (Pine w1_res_* / w1_sup_*) ---
   double                  m_w1ResTop, m_w1ResBot;
   datetime                m_w1ResTime;
   double                  m_w1ResAtr;
   int                     m_w1ResDay;

   double                  m_w1SupTop, m_w1SupBot;
   datetime                m_w1SupTime;
   double                  m_w1SupAtr;
   int                     m_w1SupDay;

   // --- Statistics ---
   int                     m_blocksCreated;
   int                     m_blocksBroken;
   int                     m_blocksVetoed;
   int                     m_blockSerialCounter;
   datetime                m_lastProcessedBarTime;
   int                     m_currentMarketDay;


   //+------------------------------------------------------------------+
   //| Refreshes the ATR history buffer once per bar                    |
   //+------------------------------------------------------------------+
   void                    RefreshATRHistory(void)
     {
      if(m_atrHandle == INVALID_HANDLE) return;
      int copied = CopyBuffer(m_atrHandle, 0, 0, MAX_ATR_HISTORY, m_atrHistory);
      if(copied < MAX_ATR_HISTORY)
         for(int i = copied; i < MAX_ATR_HISTORY; i++)
            m_atrHistory[i] = 0;
     }

   //+------------------------------------------------------------------+
   //| Returns ATR at the given shift (0 = forming bar).                |
   //|                                                                  |
   //| PUBLIC as of FIX (v5.57): COttoOrderManager pins its own entry /  |
   //| TP geometry to GetATRAtShift(1) -- the LAST CLOSED bar -- so the  |
   //| order and the Front-Run veto project off the SAME ATR. The cached |
   //| GetATR() can still lag shift 1 while the forming bar moves, and a |
   //| geometry priced from a different ATR than the veto's would skew   |
   //| the very target the veto compares against.                        |
   //+------------------------------------------------------------------+
public:
   double                  GetATRAtShift(int shift)
     {
      if(shift < 0 || shift >= MAX_ATR_HISTORY) return 0;
      double v = m_atrHistory[shift];
      return (v == EMPTY_VALUE || v <= 0) ? 0 : v;
     }
private:

   //+------------------------------------------------------------------+
   //| Returns ATR at ANY historical shift via a direct CopyBuffer fetch |
   //| (the 32-deep m_atrHistory buffer only covers recent bars).        |
   //| Used by WarmUpHistory() so historical blocks get a valid ATR      |
   //| snapshot for the sizing veto.                                    |
   //+------------------------------------------------------------------+
   double                  GetATRDirect(int shift)
     {
      if(m_atrHandle == INVALID_HANDLE || shift < 0) return 0;
      double arr[1];
      if(CopyBuffer(m_atrHandle, 0, shift, 1, arr) > 0)
         return (arr[0] == EMPTY_VALUE || arr[0] <= 0) ? 0 : arr[0];
      return 0;
     }


   //+------------------------------------------------------------------+
   //| DIRECTION CHOKEPOINT (standard two-phase lifecycle).             |
   //|                                                                  |
   //| A block's ZONE type is not its TRADE direction: that mapping is  |
   //| owned by COttoOrderManager::GetDirectionForBlock() and mirrored  |
   //| here. This file needs the direction in two places the OrderManager|
   //| cannot reach (both veto funnel projections), so the same mapping |
   //| is mirrored rather than guessed.                                 |
   //|                                                                  |
   //| v5.52 PURE CONTINUATION: the mapping is UNCONDITIONAL and reads  |
   //| no phase and no lifecycle counter. Pine's can_place gate is      |
   //| 'flat OR (long & resistance) OR (short & support)', so a         |
   //| RESISTANCE zone is traded LONG (a breakout above it) and a       |
   //| SUPPORT zone is traded SHORT (a breakdown below it). The v5.34   |
   //| two-phase fade -- the first fill a bounce, only a reclaimed zone |
   //| the flip -- is retired with the counter that encoded it.         |
   //|                                                                  |
   //| The two implementations deliberately sit side by side in review: |
   //| they must stay IDENTICAL or the Front-Run veto would project its |
   //| target off the wrong direction while the order was priced from the|
   //| other -- vetoing live entries or missing real front-runs, silently.|
   //|                                                                  |
   //+------------------------------------------------------------------+
   ENUM_TRADE_DIRECTION    BlockDirection(const SSniperBlock &b) const
     {
      return (b.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;   // Support -> SHORT, Resistance -> LONG
     }

   //+------------------------------------------------------------------+
   //| BlockEntryPrice — Pine entry price for a block, honouring        |
   //| InpEntryStyle.                                                   |
   // TRUE DEEP FRONT-EDGE (v5.69). The entry is measured OUT from the NEAR
   // edge -- the edge the arming close clears: b.bottom for a RESISTANCE
   // arm, b.top for a SUPPORT arm -- by two block heights plus one and a half
   // ATRs (gap, below), and it lands on the OPPOSITE side of the zone from
   // the target:
   //   LONG  (RESISTANCE): near = b.bottom, entry = b.bottom - gap
   //   SHORT (SUPPORT)   : near = b.top,    entry = b.top    + gap
   // With the near edge pinned at 0.0 and the far edge at +blockHeight:
   //   LONG : entry = -(2.0*bh + 1.5*a)    far edge = +bh
   //   SHORT: entry = +(2.0*bh + 1.5*a)    far edge = -bh
   // At arm time the close has only just cleared the NEAR edge, so the entry
   // sits BELOW the market for a LONG and ABOVE it for a SHORT. That is a
   // legal BUY_LIMIT / SELL_LIMIT (see GetOrderTypeForBlock), so the order
   // RESTS AT THE BROKER: the fakeout-pullback fill, not a breakout chase.
   // Resting is the normal route by construction, not a lucky coincidence;
   // see the v5.69 note in PlaceOrArmOrder's router.
   //
   // slDist is UNCHANGED at blockHeight + 0.5*ATR, so the stop lands
   //   LONG : entry - slDist = -(3.0*bh + 2.0*a)   (below the near edge)
   //   SHORT: entry + slDist = +(3.0*bh + 2.0*a)   (above the near edge)
   // -- a full slDist behind the entry in both directions. The offset is
   // carried by the ENTRY alone, never by a wider stop, so entry<->SL stays
   // exactly slDist and every rung that reads 1R (pyramid ladder, breakeven,
   // trail) is untouched by this geometry.
   //
   // THE BREAK VETO IS DECOUPLED FROM THE ENTRY (v5.69). v5.66 anchored the
   // break on the entry, which was workable only because the entry sat on the
   // far side of the zone. That anchor is now actively wrong: InpArmATR is
   // 0.0, so a RESISTANCE arms the instant close1 < b.bottom, while entry is
   // a further `gap` BELOW b.bottom. A break test of close1 > entry +
   // ATRBufferBreak*atr therefore evaluates close1 > b.bottom - 2.0*bh -
   // 1.0*a, which is TRUE on the arming bar itself -- the block would arm and
   // veto itself VETO_BROKEN in one pass, and CancelOrdersForInvalidBlocks()
   // would drop the pending. ProcessBlocks() hence anchors the break on the
   // PHYSICAL far edge again: SUPPORT breaks DOWN through b.bottom,
   // RESISTANCE breaks UP through b.top a further ATRBufferBreak*ATR. Near
   // edge = arm, far edge = invalidate, opposite sides, never the same close.
   //
   // TARGET ANCHOR (FIX v5.57, deliberately unchanged). The target is still
   // projected from the FAR edge, never from this entry: a LONG target =
   // b.top + tpRR*slDist, a SHORT target = b.bottom - tpRR*slDist. With the
   // entry now on the NEAR side, the reward from the ENTRY is
   //   3.0*bh + 1.5*a + tpRR*slDist = (3.0 + tpRR) * slDist
   // so the realized multiple is EXACTLY InpMaxRR + 3.0 at every zone height
   // and every ATR -- 7.0R on the shipped 4.0, and it can never fall behind
   // the entry for any positive InpMaxRR. The pre-v5.69 "InpMaxRR must stay
   // above 3.0" floor is therefore GONE: it was an artifact of an entry that
   // sat past the far edge, and it no longer constrains the configuration.
   // Derivation in full in the front-run block of ProcessBlocks().
   //
   // This is the exact twin of COttoOrderManager::CalcEntryPrice(); the two
   // must stay identical or the Front-Run veto would judge a different price
   // from the order's.
   double                  BlockEntryPrice(const SSniperBlock &b, double atr) const
     {
      if(InpEntryStyle == ENTRY_MIDPOINT) return b.midpoint;
      // True deep front-edge: 2.0 block heights + 1.5 ATR out from the NEAR
      // edge, landing on the OPPOSITE side of the zone from the target so the
      // order can rest as a limit. slDist is untouched, so the stop keeps
      // exactly one slDist of separation from the entry.
      double gap = (2.0 * b.blockHeight) + (1.5 * atr);
      return (BlockDirection(b) == DIR_LONG) ? (b.bottom - gap) : (b.top + gap);
     }

   //+------------------------------------------------------------------+
   //| Human-readable veto reason (diagnostics)                         |
   //| v5.80: only the surviving outcomes are mapped. Stale / Front-Run |
   //| / Near-Miss / Momentum / FVG / Correlation are gone from the     |
   //| enum, so mapping them here would not compile -- by design.        |
   //+------------------------------------------------------------------+
   string                  VetoReasonString(ENUM_VETO_REASON r)
     {
      switch(r)
        {
         case VETO_SIZING:        return "SIZING";
         case VETO_NO_SEPARATION: return "NO_SEPARATION";
         case VETO_BROKEN:        return "BROKEN";
         case VETO_FLIPPED:       return "FLIPPED";
         case VETO_LIMIT_MISSED:  return "LIMIT-MISSED";
         default:                 return "NONE";
        }
      }


   //+------------------------------------------------------------------+
   //| Marks a block vetoed: reasons, schedules deletion next bar and   |
   //| requests cancellation of any resting broker order.              |
   //+------------------------------------------------------------------+
   void                    SetVeto(int index, ENUM_VETO_REASON reason)
     {
      if(index < 0 || index >= m_blockCount) return;
      m_blocks[index].isVetoed   = true;
      m_blocks[index].vetoReason = reason;
      m_blocks[index].deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
      if(m_blocks[index].limitOrderTicket > 0)
         m_blocks[index].pendingOrderCancel = true;
      m_blocksVetoed++;
      if(EnableLogging)
         Print("[Block] VETO #", m_blocks[index].serial,
               " (", (m_blocks[index].type == BLOCK_SUPPORT ? "SUP" : "RES"), ") ",
               VetoReasonString(reason));
     }

   //+------------------------------------------------------------------+
   //| VETO-TO-REVERSAL — RETIRED (v5.52).                              |
   //|                                                                  |
   //| v5.34 Part 2 upgraded a Phase 1 block that tripped a convertible |
   //| veto into a Phase 2 reversal instead of deleting it. The pure    |
   //| CONTINUATION engine has no second phase to promote a block INTO, |
   //| so every one of the five convertible vetoes is now simply a veto |
   //| and this helper has no caller. Removed rather than left inert so |
   //| no future edit can reintroduce the fade by accident; the five    |
   //| InpRev* inputs survive as declared no-ops in OttoDefines.mqh so an|
   //| old .set file still loads.                                       |
   //+------------------------------------------------------------------+

   //+------------------------------------------------------------------+
   //| Requests order cancellation for the given block (no veto state).|
   //+------------------------------------------------------------------+
   void                    RequestCancel(int index)
     {
      if(index < 0 || index >= m_blockCount) return;
      if(m_blocks[index].limitOrderTicket > 0)
         m_blocks[index].pendingOrderCancel = true;
     }

   //+------------------------------------------------------------------+
   //| Adds a block to the array (memory-safe, respects MAX_BLOCKS)    |
   //+------------------------------------------------------------------+
   void                    AddBlock(const SSniperBlock &src)
     {
      if(m_blockCount >= MAX_BLOCKS)
        {
         if(EnableLogging)
            Print("[Block] MAX_BLOCKS reached — block #", src.serial, " dropped");
         return;
        }
      ArrayResize(m_blocks, m_blockCount + 1, MAX_BLOCKS);
      m_blocks[m_blockCount] = src;
      m_blockCount++;
     }

   //+------------------------------------------------------------------+
   //| Removes a block. If it still holds a broker order, defers the   |
   //| removal until COttoOrderManager cancels the ticket (no orphans).|
   //+------------------------------------------------------------------+
   bool                    RemoveBlock(int index)
     {
      if(index < 0 || index >= m_blockCount) return false;
      if(m_blocks[index].limitOrderTicket > 0)
        {
         m_blocks[index].pendingOrderCancel = true;
         return false; // defer — OrderManager must cancel the ticket first
        }
      for(int i = index; i < m_blockCount - 1; i++)
         m_blocks[i] = m_blocks[i + 1];
      m_blockCount--;
      ArrayResize(m_blocks, m_blockCount, MAX_BLOCKS);
      return true;
     }

   //+------------------------------------------------------------------+
   //| EXCLUSIVE TERRITORY (v5.34 Part 2) — anti-stacking guard. Returns |
   //| true when a LIVE, non-vetoed, same-polarity block already occupies|
   //| any part of [top,bottom]. Overlapping blocks are never pushed and |
   //| never counted, so two zones can never share territory.           |
   //+------------------------------------------------------------------+
   bool                    HasTerritoryOverlap(ENUM_BLOCK_TYPE type, double top, double bottom) const
     {
      for(int j = 0; j < m_blockCount; j++)
        {
         if(m_blocks[j].isVetoed)              continue;
         if(m_blocks[j].deleteOnBarTime > 0)   continue;   // marked for deletion
         if(m_blocks[j].type != type)          continue;   // same polarity only
         if(top >= m_blocks[j].bottom && bottom <= m_blocks[j].top)
            return true;
        }
      return false;
     }

   //+------------------------------------------------------------------+
   //| WICK 1 + WICK 2 BLOCK FORMATION (Pine v4.70 separation-aware)  |
   //| On each new bar, checks for a freshly-confirmed pivot (shift   |
   //| InpRightBars + 1). If a previous W1 exists within [Min,Max]     |
   //| bars and the two candle wicks overlap, forms an S/R block.     |
   //| Applies the Separation Veto and the Sizing Veto at creation.   |
   //+------------------------------------------------------------------+
   void                    TryCreateNewBlocks(int marketDay)
     {
      int pivotShift = InpRightBars + 1;   // the confirmed pivot candle
      if(pivotShift + InpLeftBars >= LookbackBars)
         return;

      // ============ RESISTANCE (pivot high) ============
      if(m_marketStruct != NULL && m_marketStruct.IsPivotHigh(pivotShift))
        {
         double curTop = iHigh(m_symbol, PERIOD_CURRENT, pivotShift);
         double curBot = MathMax(iOpen(m_symbol, PERIOD_CURRENT, pivotShift),
                                 iClose(m_symbol, PERIOD_CURRENT, pivotShift));

         if(m_w1ResTime != 0)   // a previous W1 exists
           {
            int w1Shift = iBarShift(m_symbol, PERIOD_CURRENT, m_w1ResTime);
            int dist    = w1Shift - pivotShift;   // = Pine "dist"

            if(dist >= MinBlockDistance && dist <= MaxBlockDistance)
              {
               // Wick overlap check: w1_top >= cur_bot and w1_bot <= cur_top
               if(m_w1ResTop >= curBot && m_w1ResBot <= curTop)
                 {
                  double zoneTop = MathMax(m_w1ResTop, curTop);
                  double zoneBot = MathMin(m_w1ResBot, curBot);
                  double h       = zoneTop - zoneBot;

                  SSniperBlock nb;
                  ZeroMemory(nb);
                  nb.type         = BLOCK_RESISTANCE;
                  nb.top          = zoneTop;
                  nb.bottom       = zoneBot;
                  nb.blockHeight  = h;
                  nb.midpoint     = (zoneTop + zoneBot) / 2.0;
                  nb.atrSnapshot  = GetATRAtShift(pivotShift);
                  nb.wick1Time    = m_w1ResTime;
                  nb.wick2Time    = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
                  // FIX (v5.58) W1/W2 FULL candle geometry, for the journal's
                  // Wick H/L lines only (no trading path reads these fields).
                  // W1: the stored m_w1ResTop IS the W1 candle high (it is the
                  // iHigh() captured when this W1 was created), but m_w1ResBot
                  // is the W1 BODY top -- not a low -- so the low is read from
                  // the series. W2 is the pivot bar: curTop is its iHigh().
                  nb.wick1.time       = m_w1ResTime;
                  nb.wick1.highPrice  = m_w1ResTop;
                  nb.wick1.lowPrice   = iLow(m_symbol, PERIOD_CURRENT, w1Shift);
                  nb.wick2.time       = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
                  nb.wick2.highPrice  = curTop;
                  nb.wick2.lowPrice   = iLow(m_symbol, PERIOD_CURRENT, pivotShift);
                  nb.wick1Day     = m_w1ResDay;
                  nb.creationTime = TimeCurrent();
                  nb.creationBarSerial = (int)Bars(m_symbol, PERIOD_CURRENT);
                  nb.serial       = ++m_blockSerialCounter;
                  nb.tradeId      = StringFormat("OTTO_RES_%d", nb.serial);

                  // --- v4.70 SEPARATION VETO ---
                  // A separation candle: at least one candle between W1 and
                  // W2 whose HIGH is strictly below the block bottom.
                  bool hasSeparation = false;
                  if(InpUseSeparationVeto)
                    {
                     if(dist > 1)
                        for(int s = pivotShift + 1; s < w1Shift; s++)
                           if(iHigh(m_symbol, PERIOD_CURRENT, s) < zoneBot)
                             { hasSeparation = true; break; }
                    }
                  else
                     hasSeparation = true;   // veto bypassed

                  if(!hasSeparation)
                    {
                     nb.isVetoed = true;
                     nb.vetoReason = VETO_NO_SEPARATION;
                     nb.deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
                    }
                  else if(h > (1.5 * m_w1ResAtr) || h < (0.1 * m_w1ResAtr))
                    {
                     // --- SIZING VETO (0.1x – 1.5x ATR window) ---
                     nb.isVetoed = true;
                     nb.vetoReason = VETO_SIZING;
                     nb.deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
                    }

                  // --- EXCLUSIVE TERRITORY (v5.34 Part 2): skip entirely when
                  // a live, same-polarity zone already overlaps this range.
                  if(!HasTerritoryOverlap(nb.type, nb.top, nb.bottom))
                    {
                     AddBlock(nb);   // always pushed (Pine array.push)
                     if(!nb.isVetoed)
                        m_blocksCreated++;
                    }
                 }
              }
           }

         // Update W1 state regardless of block creation
         m_w1ResTop = curTop;
         m_w1ResBot = curBot;
         m_w1ResTime = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
         m_w1ResAtr  = GetATRAtShift(pivotShift);
         m_w1ResDay  = marketDay;
        }


      // ============ SUPPORT (pivot low) ============
      if(m_marketStruct != NULL && m_marketStruct.IsPivotLow(pivotShift))
        {
         double curTop = MathMin(iOpen(m_symbol, PERIOD_CURRENT, pivotShift),
                                 iClose(m_symbol, PERIOD_CURRENT, pivotShift));
         double curBot = iLow(m_symbol, PERIOD_CURRENT, pivotShift);

         if(m_w1SupTime != 0)
           {
            int w1Shift = iBarShift(m_symbol, PERIOD_CURRENT, m_w1SupTime);
            int dist    = w1Shift - pivotShift;

            if(dist >= MinBlockDistance && dist <= MaxBlockDistance)
              {
               if(m_w1SupTop >= curBot && m_w1SupBot <= curTop)
                 {
                  double zoneTop = MathMax(m_w1SupTop, curTop);
                  double zoneBot = MathMin(m_w1SupBot, curBot);
                  double h       = zoneTop - zoneBot;

                  SSniperBlock nb;
                  ZeroMemory(nb);
                  nb.type         = BLOCK_SUPPORT;
                  nb.top          = zoneTop;
                  nb.bottom       = zoneBot;
                  nb.blockHeight  = h;
                  nb.midpoint     = (zoneTop + zoneBot) / 2.0;
                  nb.atrSnapshot  = GetATRAtShift(pivotShift);
                  nb.wick1Time    = m_w1SupTime;
                  nb.wick2Time    = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
                  // FIX (v5.58) support mirror of the W1/W2 geometry above. W1:
                  // m_w1SupBot IS the W1 candle low, while m_w1SupTop is the W1
                  // BODY bottom -- so the high comes from the series. W2 is the
                  // pivot bar: curBot is its iLow().
                  nb.wick1.time       = m_w1SupTime;
                  nb.wick1.highPrice  = iHigh(m_symbol, PERIOD_CURRENT, w1Shift);
                  nb.wick1.lowPrice   = m_w1SupBot;
                  nb.wick2.time       = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
                  nb.wick2.highPrice  = iHigh(m_symbol, PERIOD_CURRENT, pivotShift);
                  nb.wick2.lowPrice   = curBot;
                  nb.wick1Day     = m_w1SupDay;
                  nb.creationTime = TimeCurrent();
                  nb.creationBarSerial = (int)Bars(m_symbol, PERIOD_CURRENT);
                  nb.serial       = ++m_blockSerialCounter;
                  nb.tradeId      = StringFormat("OTTO_SUP_%d", nb.serial);

                  // --- SEPARATION VETO (support mirror) ---
                  // A candle between W1 and W2 whose LOW is strictly above
                  // the block top.
                  bool hasSeparation = false;
                  if(InpUseSeparationVeto)
                    {
                     if(dist > 1)
                        for(int s = pivotShift + 1; s < w1Shift; s++)
                           if(iLow(m_symbol, PERIOD_CURRENT, s) > zoneTop)
                             { hasSeparation = true; break; }
                    }
                  else
                     hasSeparation = true;

                  if(!hasSeparation)
                    {
                     nb.isVetoed = true;
                     nb.vetoReason = VETO_NO_SEPARATION;
                     nb.deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
                    }
                  else if(h > (1.5 * m_w1SupAtr) || h < (0.1 * m_w1SupAtr))
                    {
                     nb.isVetoed = true;
                     nb.vetoReason = VETO_SIZING;
                     nb.deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
                    }

                  // --- EXCLUSIVE TERRITORY (v5.34 Part 2): skip entirely when
                  // a live, same-polarity zone already overlaps this range.
                  if(!HasTerritoryOverlap(nb.type, nb.top, nb.bottom))
                    {
                     AddBlock(nb);
                     if(!nb.isVetoed)
                        m_blocksCreated++;
                    }
                 }
              }
           }

         m_w1SupTop = curTop;
         m_w1SupBot = curBot;
         m_w1SupTime = iTime(m_symbol, PERIOD_CURRENT, pivotShift);
         m_w1SupAtr  = GetATRAtShift(pivotShift);
         m_w1SupDay  = marketDay;
        }
     }


   //+------------------------------------------------------------------+
   //| BLOCK MANAGEMENT FUNNEL — v5.80 (creation-time vetoes + structure)|
   //| Runs once per new bar (calc_on_every_tick=false mirror). Order:  |
   //| deletion -> hasExited -> Arming -> Break/Flip.                   |
   //|                                                                  |
//| v5.52 -- the state machine is PHASE-FREE. v5.49's geometry  |
//| inversion existed only to measure a reclaimed zone against  |
//| its own reversal side; there is no reversal any more, so    |
//| hasExited, arming and break/flip are each a single test     |
//| that reads no lifecycle counter. See the break block below: |
//| its test is the OPPOSITE edge from arming, on purpose.      |
//|                                                             |
//| v5.80 -- THE STRIP. The per-bar passes that used to sit     |
//| between hasExited and arming (Stale 45D, Front-Run,         |
//| Near-Miss 6D) and the one that used to sit after break/flip |
//| (Momentum & FVG) are DELETED, together with the intra-bar   |
//| twin UpdateIntraBar(). Nothing between arming and the broker |
//| may veto a setup any more: once a block arms, the ONLY exit  |
//| is the structural break/flip below (or the venue refusing    |
//| the resting price in the router).                           |
   //+------------------------------------------------------------------+
   void                    ProcessBlocks(int marketDay)
     {
      double atrNow = GetATRAtShift(1);
      if(atrNow <= 0) atrNow = m_currentATR;

      for(int i = m_blockCount - 1; i >= 0; i--)
        {
         SSniperBlock b = m_blocks[i];

         // --- DELETION (Pine: bar_index >= delete_on_bar) ---
         if(b.deleteOnBarTime > 0 && iTime(m_symbol, PERIOD_CURRENT, 0) > b.deleteOnBarTime)
           {
            RemoveBlock(i);
            continue;
           }

         // --- Vetoed blocks are inert until deleted (Pine waits) ---
         if(b.isVetoed)
            continue;

         double close1 = iClose(m_symbol, PERIOD_CURRENT, 1);
         double high1  = iHigh(m_symbol, PERIOD_CURRENT, 1);
         double low1   = iLow(m_symbol, PERIOD_CURRENT, 1);

         // --- hasExited: price left the zone on a wick basis ------------
         // A wick clear of the zone means price has left it and the setup is
         // free to arm. Unconditional in v5.52: the continuation mapping does
         // not depend on a phase, so neither does this test.
         if(!b.hasExited)
           {
            if((b.type == BLOCK_SUPPORT && low1 > b.top) ||
               (b.type == BLOCK_RESISTANCE && high1 < b.bottom))
               b.hasExited = true;
           }

         // --- ARMING (price exits the zone on a close basis) ---
         if(!b.isArmed)
           {
            // STACKED / OVERLAPPING BLOCK LOCKOUT — an older active primary
            // block nearby blocks this block from arming.
            if(IsBlockedByPrimary(i))
              {
               m_blocks[i] = b;   // persist any state already set (e.g. hasExited)
               continue;          // cannot arm while an older active primary is in range
              }

            // ARMED once price has CLOSED clear of the zone on the NEAR edge --
            // for a SUPPORT zone a close ABOVE it, for a RESISTANCE zone a close
            // BELOW it. The continuation entry that follows triggers at the
            // MAPPED edge (a breakout above resistance / breakdown below
            // support), so arming is the PRE-CONDITION, not the trigger.
            //
            // This is the pre-v5.52 arming test, kept verbatim. It is deliberately
            // on the edge OPPOSITE the break test below: arming looks past the
            // NEAR edge, while the break is measured from the PHYSICAL FAR edge
            // (v5.69 decoupled it from the entry), so the two can never fire on
            // the same close and a block cannot arm and be vetoed BROKEN in one
            // pass.
            if((b.type == BLOCK_SUPPORT && close1 > b.top + InpArmATR * atrNow) ||
               (b.type == BLOCK_RESISTANCE && close1 < b.bottom - InpArmATR * atrNow))
               b.isArmed = true;
           }

         // --- BREAK / FLIP (a break is the edge the trade cannot use) ---
         // Pine's S/R-BREACH / POLARITY-FLIP detector, NOT an entry validator:
         // it asks whether the close went out the FAR edge of the zone, which
         // reclassifies the zone and blacklists it. The zone-to-direction
         // mapping is untouched (a SUPPORT breaks DOWN, a RESISTANCE breaks
         // UP), so arming above still looks past the NEAR edge while this
         // looks past the FAR side.
         //
         // ANCHORED ON THE PHYSICAL FAR EDGE (v5.69, restored after v5.66).
         // v5.66 hung this test on BlockEntryPrice(), which worked only while
         // the entry sat BEYOND the far edge -- the fill and the invalidation
         // were the same event, so one anchor served both. The v5.69 entry is
         // on the OPPOSITE side of the zone (a RESISTANCE LONG fills BELOW
         // b.bottom, a SUPPORT SHORT ABOVE b.top), and hanging the break on it
         // inverts the test's meaning: with InpArmATR = 0.0 a RESISTANCE arms
         // the instant close1 < b.bottom, while entry = b.bottom - gap sits a
         // further gap BELOW that, so close1 > entry + ATRBufferBreak*ATR is
         // already TRUE on the arming bar. The block would arm and veto itself
         // VETO_BROKEN in one pass and CancelOrdersForInvalidBlocks() would
         // drop the pending. Every armed block would die instantly.
         //
         // So the two tests are DECOUPLED and live on opposite sides of the
         // zone, which is the only arrangement in which both can be true of
         // one close:
         //   NEAR edge -> ARM      (a RESISTANCE: close1 < b.bottom)
         //   FAR  edge -> BREAK    (a RESISTANCE: close1 > b.top + buffer)
         // The level is therefore the physical far edge -- b.top for a
         // RESISTANCE, b.bottom for a SUPPORT -- and honours no entry style.
         // That decoupling is the invariant to keep: the veto must never move
         // with the entry again, or the pending dies before it can fill.
         double breakLevel = (b.type == BLOCK_SUPPORT) ? b.bottom : b.top;
         bool isBroken =
            (b.type == BLOCK_SUPPORT && close1 < breakLevel - ATRBufferBreak * atrNow) ||
            (b.type == BLOCK_RESISTANCE && close1 > breakLevel + ATRBufferBreak * atrNow);
         if(isBroken)
           {
            if((high1 - low1) > (2.0 * atrNow))
              {
               // FLIP: revert polarity, delete next bar (Pine)
               b.isFlipped = true;
               b.type = (b.type == BLOCK_SUPPORT) ? BLOCK_RESISTANCE : BLOCK_SUPPORT;
               m_blocks[i] = b;   // persist the flip before veto
               SetVeto(i, VETO_FLIPPED);
               continue;
              }
            else
              {
               if(!b.isArmed)
                 {
                  // Delete immediately (Pine box.delete + array.remove)
                  RemoveBlock(i);
                  continue;
                 }
               else
                 {
                  SetVeto(i, VETO_BROKEN);
                  continue;
                 }
              }
           }

         // --- Persist block mutations made during this funnel pass ---
         m_blocks[i] = b;
        }
     }


public:
   //+------------------------------------------------------------------+
   //| Constructor                                                      |
   //+------------------------------------------------------------------+
                     COttoBlockManager(void)
     {
      m_symbol           = "";
      m_marketStruct     = NULL;
      m_blockCount       = 0;
      m_atrHandle        = INVALID_HANDLE;
      m_currentATR       = 0;
      m_w1ResTime        = 0;
      m_w1SupTime        = 0;
      m_blocksCreated    = 0;
      m_blocksBroken     = 0;
      m_blocksVetoed     = 0;
      m_blockSerialCounter = 0;
      m_lastProcessedBarTime = 0;
      m_currentMarketDay = 0;
      ArrayResize(m_blocks, 0, MAX_BLOCKS);
      ArrayInitialize(m_atrHistory, 0);
     }

   //+------------------------------------------------------------------+
   //| Destructor — release handle + visual objects                    |
   //+------------------------------------------------------------------+
                    ~COttoBlockManager(void)
     {
      ArrayFree(m_blocks);
      if(m_atrHandle != INVALID_HANDLE)
         IndicatorRelease(m_atrHandle);
     }

   //+------------------------------------------------------------------+
   //| Initialize                                                        |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol, COttoMarketStructure *marketStruct)
     {
      m_symbol       = symbol;
      m_marketStruct = marketStruct;
      m_atrHandle    = iATR(m_symbol, PERIOD_CURRENT, ATRPeriod);
      if(m_atrHandle == INVALID_HANDLE)
        {
         Print("[BlockManager] ERROR: Failed to create ATR handle");
         return false;
        }
      if(EnableLogging)
         Print("[BlockManager] Initialized for ", m_symbol,
               " | Pivot ", InpLeftBars, "/", InpRightBars,
               " | ATR ", ATRPeriod,
               " | Sizing ", ATRVetoMin, "x-", ATRVetoMax, "x ATR");
      return true;
     }

   //+------------------------------------------------------------------+
   //| MAIN UPDATE — new-bar funnel (W1/W2 formation + vetoes).        |
   //| Called by the EA only on a new bar.                             |
   //+------------------------------------------------------------------+
   void              Update(int marketDay)
     {
      datetime barTime = iTime(m_symbol, PERIOD_CURRENT, 0);
      if(barTime == m_lastProcessedBarTime)
         return;                       // once per new bar
      m_lastProcessedBarTime = barTime;
      m_currentMarketDay = marketDay;

      RefreshATRHistory();
      m_currentATR = GetATRAtShift(0);

      TryCreateNewBlocks(marketDay);
      ProcessBlocks(marketDay);
     }

   //+------------------------------------------------------------------+
   //| STARTUP HISTORICAL WARM-UP                                       |
   //| Called once from OnInit(). Scans history (LookbackBars) oldest-  |
   //| to-newest, re-deriving Wick 1 + Wick 2 S/R blocks exactly like   |
   //| the live TryCreateNewBlocks() funnel so existing zones populate   |
   //| the chart immediately on load instead of waiting for new bars.  |
   //+------------------------------------------------------------------+
   void              WarmUpHistory(void)
     {
      if(m_atrHandle == INVALID_HANDLE || m_marketStruct == NULL) return;

      // Ensure the recent ATR buffer is valid for ProcessBlocks() below
      RefreshATRHistory();
      m_currentATR = GetATRAtShift(0);   // so IsBlockedByPrimary uses a valid ATR

      int totalBars = (int)Bars(m_symbol, PERIOD_CURRENT);
      if(totalBars <= InpLeftBars + InpRightBars + 2) return;
      int scanLimit = (int)MathMin(LookbackBars, totalBars - InpLeftBars - InpRightBars - 2);
      if(scanLimit <= InpRightBars + 1) return;

      // Iterate oldest -> newest confirmed pivot bars
      for(int s = scanLimit; s >= InpRightBars + 1; s--)
        {
         // ======================== RESISTANCE ========================
         if(m_marketStruct.IsPivotHigh(s))
           {
            double curTop = iHigh(m_symbol, PERIOD_CURRENT, s);
            double curBot = MathMax(iOpen(m_symbol, PERIOD_CURRENT, s), iClose(m_symbol, PERIOD_CURRENT, s));
            datetime curTime = iTime(m_symbol, PERIOD_CURRENT, s);

            if(m_w1ResTime != 0)
              {
               int w1Shift = iBarShift(m_symbol, PERIOD_CURRENT, m_w1ResTime);
               int dist    = w1Shift - s;
               if(dist >= MinBlockDistance && dist <= MaxBlockDistance)
                 {
                  if(m_w1ResTop >= curBot && m_w1ResBot <= curTop)
                    {
                     double zoneTop = MathMax(m_w1ResTop, curTop);
                     double zoneBot = MathMin(m_w1ResBot, curBot);
                     double h       = zoneTop - zoneBot;

                     double w1Atr = GetATRDirect(w1Shift);
                     if(w1Atr <= 0) w1Atr = GetATRDirect(s);

                     SSniperBlock nb;
                     ZeroMemory(nb);
                     nb.type         = BLOCK_RESISTANCE;
                     nb.top          = zoneTop;
                     nb.bottom       = zoneBot;
                     nb.blockHeight  = h;
                     nb.midpoint     = (zoneTop + zoneBot) / 2.0;
                     nb.atrSnapshot  = GetATRDirect(s);
                     nb.wick1Time    = m_w1ResTime;
                     nb.wick2Time    = curTime;
                     // FIX (v5.58) W1/W2 FULL candle geometry (warm-up twin of the
                     // live pass in TryCreateNewBlocks). The historical scan has
                     // a shift `s` for W2 rather than pivotShift; getATRDirect()
                     // is the warm-up reader, iHigh()/iLow() are shift-agnostic.
                     nb.wick1.time       = m_w1ResTime;
                     nb.wick1.highPrice  = m_w1ResTop;
                     nb.wick1.lowPrice   = iLow(m_symbol, PERIOD_CURRENT, w1Shift);
                     nb.wick2.time       = curTime;
                     nb.wick2.highPrice  = curTop;
                     nb.wick2.lowPrice   = iLow(m_symbol, PERIOD_CURRENT, s);
                     nb.wick1Day     = m_w1ResDay;
                     nb.creationTime = curTime;
                     nb.creationBarSerial = (int)Bars(m_symbol, PERIOD_CURRENT);
                     nb.serial       = ++m_blockSerialCounter;
                     nb.tradeId      = StringFormat("OTTO_RES_%d", nb.serial);

                     // --- Separation Veto ---
                     bool hasSeparation = false;
                     if(InpUseSeparationVeto)
                       {
                        if(dist > 1)
                           for(int k = s + 1; k < w1Shift; k++)
                              if(iHigh(m_symbol, PERIOD_CURRENT, k) < zoneBot) { hasSeparation = true; break; }
                       }
                     else
                        hasSeparation = true;

                     if(!hasSeparation)
                       {
                        nb.isVetoed = true;
                        nb.vetoReason = VETO_NO_SEPARATION;
                       }
                     else if(h > (1.5 * w1Atr) || h < (0.1 * w1Atr))
                       {
                        nb.isVetoed = true;
                        nb.vetoReason = VETO_SIZING;
                       }

                     // --- EXCLUSIVE TERRITORY (v5.34 Part 2): skip entirely when
                     // a live, same-polarity zone already overlaps this range.
                     if(!nb.isVetoed && !HasTerritoryOverlap(nb.type, nb.top, nb.bottom))
                       {
                        AddBlock(nb);
                        m_blocksCreated++;
                       }
                    }
                 }
              }

            m_w1ResTop  = curTop;
            m_w1ResBot  = curBot;
            m_w1ResTime = curTime;
            m_w1ResAtr  = GetATRDirect(s);
            m_w1ResDay  = 0;
           }

         // ======================== SUPPORT ========================
         if(m_marketStruct.IsPivotLow(s))
           {
            double curTop = MathMin(iOpen(m_symbol, PERIOD_CURRENT, s), iClose(m_symbol, PERIOD_CURRENT, s));
            double curBot = iLow(m_symbol, PERIOD_CURRENT, s);
            datetime curTime = iTime(m_symbol, PERIOD_CURRENT, s);

            if(m_w1SupTime != 0)
              {
               int w1Shift = iBarShift(m_symbol, PERIOD_CURRENT, m_w1SupTime);
               int dist    = w1Shift - s;
               if(dist >= MinBlockDistance && dist <= MaxBlockDistance)
                 {
                  if(m_w1SupTop >= curBot && m_w1SupBot <= curTop)
                    {
                     double zoneTop = MathMax(m_w1SupTop, curTop);
                     double zoneBot = MathMin(m_w1SupBot, curBot);
                     double h       = zoneTop - zoneBot;

                     double w1Atr = GetATRDirect(w1Shift);
                     if(w1Atr <= 0) w1Atr = GetATRDirect(s);

                     SSniperBlock nb;
                     ZeroMemory(nb);
                     nb.type         = BLOCK_SUPPORT;
                     nb.top          = zoneTop;
                     nb.bottom       = zoneBot;
                     nb.blockHeight  = h;
                     nb.midpoint     = (zoneTop + zoneBot) / 2.0;
                     nb.atrSnapshot  = GetATRDirect(s);
                     nb.wick1Time    = m_w1SupTime;
                     nb.wick2Time    = curTime;
                     // FIX (v5.58) support mirror of the warm-up W1/W2 geometry.
                     nb.wick1.time       = m_w1SupTime;
                     nb.wick1.highPrice  = iHigh(m_symbol, PERIOD_CURRENT, w1Shift);
                     nb.wick1.lowPrice   = m_w1SupBot;
                     nb.wick2.time       = curTime;
                     nb.wick2.highPrice  = iHigh(m_symbol, PERIOD_CURRENT, s);
                     nb.wick2.lowPrice   = curBot;
                     nb.wick1Day     = m_w1SupDay;
                     nb.creationTime = curTime;
                     nb.creationBarSerial = (int)Bars(m_symbol, PERIOD_CURRENT);
                     nb.serial       = ++m_blockSerialCounter;
                     nb.tradeId      = StringFormat("OTTO_SUP_%d", nb.serial);

                     // --- Separation Veto (support mirror) ---
                     bool hasSeparation = false;
                     if(InpUseSeparationVeto)
                       {
                        if(dist > 1)
                           for(int k = s + 1; k < w1Shift; k++)
                              if(iLow(m_symbol, PERIOD_CURRENT, k) > zoneTop) { hasSeparation = true; break; }
                       }
                     else
                        hasSeparation = true;

                     if(!hasSeparation)
                       {
                        nb.isVetoed = true;
                        nb.vetoReason = VETO_NO_SEPARATION;
                       }
                     else if(h > (1.5 * w1Atr) || h < (0.1 * w1Atr))
                       {
                        nb.isVetoed = true;
                        nb.vetoReason = VETO_SIZING;
                       }

                     // --- EXCLUSIVE TERRITORY (v5.34 Part 2): skip entirely when
                     // a live, same-polarity zone already overlaps this range.
                     if(!nb.isVetoed && !HasTerritoryOverlap(nb.type, nb.top, nb.bottom))
                       {
                        AddBlock(nb);
                        m_blocksCreated++;
                       }
                    }
                 }
              }

            m_w1SupTop  = curTop;
            m_w1SupBot  = curBot;
            m_w1SupTime = curTime;
            m_w1SupAtr  = GetATRDirect(s);
            m_w1SupDay  = 0;
           }


         }   // end for(s) warm-up scan loop

      // --- Evaluate warm-loaded blocks' current status / cleanup once ---
      ProcessBlocks(0);
     }   // end WarmUpHistory()

   //+------------------------------------------------------------------+
   //| STACKED / OVERLAPPING BLOCK LOCKOUT                              |
   //| Returns true if an OLDER block of the SAME type (Support/         |
   //| Resistance) is still actively waiting for a fill or running a    |
   //| trade, and sits within 3.0*ATR of the given block. When true,    |
   //| the newer/stacked block must not be armed or get an order.       |
   //+------------------------------------------------------------------+
   bool              IsBlockedByPrimary(int currentIndex)
     {
      if(currentIndex < 0 || currentIndex >= m_blockCount) return false;

      SSniperBlock target = m_blocks[currentIndex];   // copy — no struct reference

      for(int j = 0; j < m_blockCount; j++)
        {
         if(j == currentIndex) continue;
         if(m_blocks[j].type != target.type) continue;         // same direction only
         if(m_blocks[j].serial >= target.serial) continue;     // only OLDER blocks

         // "Alive" = armed, order placed, or in an active trade
         bool alive = m_blocks[j].isArmed ||
                      m_blocks[j].hasPlacedOrder ||
                      m_blocks[j].isTriggered;
         if(!alive || m_blocks[j].isVetoed) continue;          // must be actively waiting

         if(MathAbs(m_blocks[j].midpoint - target.midpoint) > (3.0 * m_currentATR)) continue;

         return true;   // blocked by an older, active primary block
        }
      return false;
     }





   //+------------------------------------------------------------------+
   //| Current ATR (refreshes the cached value)                        |
   //+------------------------------------------------------------------+
   double            GetATR(void)
     {
      if(m_atrHandle != INVALID_HANDLE)
        {
         double arr[1];
         if(CopyBuffer(m_atrHandle, 0, 0, 1, arr) > 0)
            m_currentATR = arr[0];
        }
      return m_currentATR;
     }

   //+------------------------------------------------------------------+
   //| Returns the current block count                                  |
   //+------------------------------------------------------------------+
   int               GetBlockCount(void) const
     {
      return m_blockCount;
     }

   //+------------------------------------------------------------------+
   //| Copies all blocks into the caller's array (snapshot)            |
   //+------------------------------------------------------------------+
   int               GetAllBlocks(SSniperBlock &outBlocks[]) const
     {
      ArrayResize(outBlocks, m_blockCount);
      for(int i = 0; i < m_blockCount; i++)
         outBlocks[i] = m_blocks[i];
      return m_blockCount;
     }

   //+------------------------------------------------------------------+
   //| Reads one block snapshot                                         |
   //+------------------------------------------------------------------+
   bool              GetBlockAt(int index, SSniperBlock &out) const
     {
      if(index < 0 || index >= m_blockCount) return false;
      out = m_blocks[index];
      return true;
     }

   //+------------------------------------------------------------------+
   //| Writes back a full block snapshot (used by OrderManager)        |
   //+------------------------------------------------------------------+
   void              SetBlockAt(int index, const SSniperBlock &src)
     {
      if(index < 0 || index >= m_blockCount) return;
      m_blocks[index] = src;
     }


   //+------------------------------------------------------------------+
   //| Sets the resting broker-order ticket for a block                |
   //+------------------------------------------------------------------+
   void              SetBlockOrderTicket(int index, ulong ticket)
     {
      if(index < 0 || index >= m_blockCount) return;
      m_blocks[index].limitOrderTicket = ticket;
      m_blocks[index].pendingOrderCancel = false;
     }

   //+------------------------------------------------------------------+
   //| Finds a block by its resting order ticket (or -1)               |
   //+------------------------------------------------------------------+
   int               FindBlockIndexByTicket(ulong ticket)
     {
      if(ticket <= 0) return -1;
      for(int i = 0; i < m_blockCount; i++)
         if(m_blocks[i].limitOrderTicket == ticket)
            return i;
      return -1;
     }

   //+------------------------------------------------------------------+
   //| Finds a block by its unique serial (or -1)                       |
   //|                                                                  |
   //| Serial lookup, not array index: RemoveBlock() compacts m_blocks[] |
   //| and a stored index would dangle. This is the serial twin of       |
   //| FindBlockIndexByTicket(), which every ticket-keyed path still      |
   //| uses (v5.72: the resting broker limit is the only order artefact, |
   //| so the ticket lookup is the primary one).                         |
   //+------------------------------------------------------------------+
   int               FindBlockIndexBySerial(int serial)
     {
      if(serial <= 0) return -1;
      for(int i = 0; i < m_blockCount; i++)
         if(m_blocks[i].serial == serial)
            return i;
      return -1;
     }

   //+------------------------------------------------------------------+
   //| CLUSTER SYNC — RETIRED (v5.52).                                  |
   //|                                                                  |
   //| v5.34 Part 3 promoted every same-territory Phase 1 sibling to    |
   //| Phase 2 the moment one of them was reclaimed, so a reclaim seen  |
   //| twice would not leave a twin still fading the level. With the    |
   //| two-phase engine gone there is no promotion and no fading twin --|
   //| every zone now trades the SAME unconditional continuation -- so  |
   //| the helper's two callers are gone with it. Removed rather than   |
   //| left inert: an unreferenced cluster sync is a silent invitation to|
   //| re-wire the fade.                                                |
   //+------------------------------------------------------------------+

   //+------------------------------------------------------------------+
   //| HIVE-MIND tie-breaker — RETIRED (v5.80 architectural strip).      |
   //|                                                                  |
   //| DeleteBlockType() deleted every live zone of one polarity on a    |
   //| portfolio-wide correlation reading (HiveMind's vote). That is an  |
   //| EXTERNAL filter deleting zones whose own chart geometry had       |
   //| already approved them, so its only caller is gone and the helper  |
   //| is removed with it -- the same reasoning as the v5.52 cluster      |
   //| sync above. An unreferenced type-deleter is a silent invitation   |
   //| to re-wire the correlation filter.                                |
   //+------------------------------------------------------------------+

   //+------------------------------------------------------------------+
   //| Statistics getters                                               |
   //+------------------------------------------------------------------+
   int               GetBlocksCreated(void) const { return m_blocksCreated; }
   int               GetBlocksBroken(void) const { return m_blocksBroken; }
   int               GetBlocksVetoed(void) const { return m_blocksVetoed; }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_BLOCK_MANAGER__

