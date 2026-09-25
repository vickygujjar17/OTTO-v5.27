//+------------------------------------------------------------------+
//|                                              COttoTradeManager.mqh |
//|       MODULE 6 - Dynamic Trade Management (exact Pine v4.70) + Pyr |
//|            OTTO EA - Cut / Cost-BE / ATR Trail / Pyramiding       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.28"

#ifndef __OTTO_TRADE_MANAGER__
#define __OTTO_TRADE_MANAGER__

#include "OttoDefines.mqh"
#include "COttoRiskManager.mqh"
#include "COttoOrderManager.mqh"
#include "COttoBlockManager.mqh"
#include "COttoJournal.mqh"

class COttoTradeManager
  {
private:
   string               m_symbol;
   COttoRiskManager    *m_riskManager;
   COttoOrderManager   *m_orderManager;
   COttoBlockManager   *m_blockManager;
   int                  m_tradesManaged;
   int                  m_halfRiskTriggers;
   int                  m_breakevenTriggers;
   int                  m_trailActivations;
   int                  m_stopsHit;

   double            GetCurrentATR(void)
     { return m_blockManager.GetATR(); }

   // Sum per-position commission from the opening deal. POSITION_COMMISSION
   // is deprecated in modern MT5 builds (compiler warning 89), so the value
   // is read from deal history instead — the same convention used by
   // COttoOrderManager::LogClosedTrade().
   double            GetPositionCommission(ulong ticket)
     {
      double comm = 0.0;
      if(ticket == 0) return 0.0;
      for(int i = HistorySelect(0, TimeCurrent()) - 1; i >= 0; i--)
        {
         ulong dt = HistoryDealGetTicket(i);
         if(dt == 0) continue;
         if(HistoryDealGetInteger(dt, DEAL_POSITION_ID) != (long)ticket) continue;
         comm += HistoryDealGetDouble(dt, DEAL_COMMISSION);
        }
      return comm;
     }

   // Sum negative broker friction (commission+swap) across ALL basket tickets
   double            CalcBasketFriction(bool isLong)
     {
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double tickValue = m_riskManager.GetTickValuePerLot();
      double tickSize  = m_riskManager.GetTickSize();
      if(point <= 0 || tickValue <= 0 || tickSize <= 0) return 0.0;
      double frictionMoney = 0.0;
      
      for(int i = 0; i < m_orderManager.GetBasketCount(); i++)
        {
         SPyramidTranche t;
         if(!m_orderManager.GetBasketTicket(i, t)) continue;
         if(PositionSelectByTicket(t.ticket))
           {
            double comm = GetPositionCommission(t.ticket);
            double swp  = PositionGetDouble(POSITION_SWAP);
            if(comm < 0.0) frictionMoney += MathAbs(comm);
            else if(comm == 0.0) frictionMoney += 7.0 * t.size;
            if(swp  < 0.0) frictionMoney += MathAbs(swp);
           }
        }
      double frictionPoints = (frictionMoney / (tickValue * 1.0)) * tickSize;
      double spread = SymbolInfoDouble(m_symbol, SYMBOL_ASK) - SymbolInfoDouble(m_symbol, SYMBOL_BID);
      return frictionPoints + (spread * 0.5);
     }

public:
   COttoTradeManager(void)
     {
      m_symbol=""; m_riskManager=NULL; m_orderManager=NULL; m_blockManager=NULL;
      m_tradesManaged=0; m_halfRiskTriggers=0; m_breakevenTriggers=0; m_trailActivations=0; m_stopsHit=0;
     }
   ~COttoTradeManager(void) { }

   bool            Initialize(string symbol, COttoRiskManager *rm, COttoOrderManager *om, COttoBlockManager *bm)
     {
      m_symbol=symbol; m_riskManager=rm; m_orderManager=om; m_blockManager=bm;
      return true;
     }

   void            Update(void)
     {
      if(!m_orderManager.HasActiveTrade()) return;
      SActiveTrade trade;
      if(!m_orderManager.GetActiveTradeRef(trade)) return;
      if(!PositionSelectByTicket(trade.ticket)) return;
      double rrUnit = m_orderManager.GetBasketRRUnit();
      if(rrUnit <= 0.0) rrUnit = trade.rrUnit;
      if(rrUnit <= 0.0) rrUnit = trade.initialSLDistance;
      double primaryEntry = m_orderManager.GetPrimaryEntry();
      if(primaryEntry <= 0.0) primaryEntry = trade.entryPrice;
      ENUM_TRADE_DIRECTION dir = m_orderManager.GetBasketDir();
      if(dir == DIR_NONE) dir = trade.direction;

      double atr = GetCurrentATR();
      if(atr <= 0) return;
      double high0 = iHigh(m_symbol, PERIOD_CURRENT, 0);
      double low0  = iLow(m_symbol, PERIOD_CURRENT, 0);
      // LIVE prices every tick (fixes the +1.0R pyramid trigger): LONG=BID, SHORT=ASK
      double liveBid = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      double liveAsk = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double currentRR = 0.0;
      double desiredSL = trade.currentTrailSL;
      // Snapshot the stale struct value BEFORE any re-seeding. The single-ticket
      // push below compares against this, so introducing the session SL can never
      // by itself manufacture a difference and trigger a spurious broker write.
      double prevTrailSL = desiredSL;
      // FIX (v5.20): set when Tranche 3 is added on THIS tick. The ATR-trail
      // block and the single-ticket broker push are both bypassed for that
      // one tick so the broker can confirm the protective breakeven stop on
      // the new ticket before the dynamic trail takes over.
      bool t3OpenedThisTick = false;
      // FIX (v5.15): after a T2/T3 ApplyUnifiedSL moved the BASKET stop, the
      // primary struct field (trade.currentTrailSL) lags behind the real
      // ratcheted value. Re-seed from the authoritative session SL so the
      // guards below and the T3 trail block work off the true basket stop.
      // FIX (v5.16): only do this for a MULTI-tranche basket (> 1). On a
      // single-tranche basket the per-ticket path at the bottom of Update()
      // owns the stop, and seeding here only caused extra broker writes.
      if(m_orderManager.GetBasketCount() > 1 && m_orderManager.GetSessionSL() > 0)
         desiredSL = m_orderManager.GetSessionSL();

      if(dir == DIR_LONG)
        {
         currentRR = (rrUnit > 0) ? (liveBid - primaryEntry) / rrUnit : 0;
         double halfRiskSL = primaryEntry - (0.5 * rrUnit);
         if(currentRR >= InpCutRiskRR && desiredSL < halfRiskSL)
           { desiredSL = halfRiskSL; m_halfRiskTriggers++; }
         // v5.27: BREAKEVEN AT 1:1 (InpBreakEvenRR lowered 2.0 -> 1.0).
         // Cost-covering, not nominal: beOffset carries commission + swap +
         // half-spread, so the stop sits fractionally ABOVE true entry and a
         // trip there returns the account to flat rather than to a loss.
         if(currentRR >= InpBreakEvenRR && desiredSL < primaryEntry)
           {
            double beOffset = CalcBasketFriction(true);
            double beSL = primaryEntry + beOffset;
            if(desiredSL < beSL) { desiredSL = beSL; m_breakevenTriggers++; }
           }
         // v5.27: STEP PROFIT LOCK (InpLockProfitRR -> InpLockProfitTargetRR).
         // At +3.0R the hard floor ratchets to +2.0R, banking 2R of open profit.
         // Guarded strictly forward: the '<' test means the lock can never pull
         // a stop backwards, which is also why ApplyUnifiedSL()'s ratchet will
         // accept it unconditionally. Disabled when either input is 0.
         if(InpLockProfitRR > 0.0 && InpLockProfitTargetRR > 0.0 && currentRR >= InpLockProfitRR)
           {
            double lockedSL = primaryEntry + (InpLockProfitTargetRR * rrUnit);
            if(lockedSL > desiredSL) desiredSL = lockedSL;
           }
         // v5.27: DYNAMIC ATR TRAIL runs UNCONDITIONALLY past InpLock3RRR and is
         // evaluated AFTER the step lock, so the tighter of the two wins on this
         // same tick. When the ATR trail is near market it supersedes the +2.0R
         // floor; in a wide-ATR chop the +2.0R floor holds.
         if(currentRR >= InpLock3RRR)
           {
            double dynamicTrail = high0 - (InpTrailATRMultiplier * atr);
            if(dynamicTrail > desiredSL) desiredSL = dynamicTrail;
           }
        }
      else // SHORT
        {
         currentRR = (rrUnit > 0) ? (primaryEntry - liveAsk) / rrUnit : 0;
         double halfRiskSL = primaryEntry + (0.5 * rrUnit);
         if(currentRR >= InpCutRiskRR && desiredSL > halfRiskSL)
           { desiredSL = halfRiskSL; m_halfRiskTriggers++; }
         // v5.27: BREAKEVEN AT 1:1 — mirror of the LONG branch above.
         if(currentRR >= InpBreakEvenRR && desiredSL > primaryEntry)
           {
            double beOffset = CalcBasketFriction(false);
            double beSL = primaryEntry - beOffset;
            if(desiredSL > beSL) { desiredSL = beSL; m_breakevenTriggers++; }
           }
         // v5.27: STEP PROFIT LOCK — mirror of the LONG branch, '<' mirrored to
         // '>' so the ratchet again only ever moves the stop toward market.
         if(InpLockProfitRR > 0.0 && InpLockProfitTargetRR > 0.0 && currentRR >= InpLockProfitRR)
           {
            double lockedSL = primaryEntry - (InpLockProfitTargetRR * rrUnit);
            if(lockedSL < desiredSL) desiredSL = lockedSL;
           }
         // v5.27: DYNAMIC ATR TRAIL — mirror of the LONG branch.
         if(currentRR >= InpLock3RRR)
           {
            double dynamicTrail = low0 + (InpTrailATRMultiplier * atr);
            if(dynamicTrail < desiredSL) desiredSL = dynamicTrail;
           }
        }

      // --- PYRAMID (unified group stop) ---
      // v5.27: Tranche 2 at +2.0R, driven by its OWN input (InpPyramidT2RR).
      // Previously this read InpBreakEvenRR, which worked only because both
      // happened to equal 2.0; lowering breakeven to 1.0 for the 1:1 rule would
      // have silently dragged the T2 scale-in down to 1.0R. The trigger is now
      // independent: add the InpRiskT2Pct tranche, then move the unified basket
      // stop to exact Cost-Covering Breakeven (entry +/- beOffset, where
      // beOffset already accounts for broker commission + swap friction).
      if(currentRR >= InpPyramidT2RR && m_orderManager.IsPyramidPending(2))
        {
         double beOffset = CalcBasketFriction(dir==DIR_LONG);
         double groupBE = primaryEntry + (dir==DIR_LONG ? beOffset : -beOffset);
         if(m_orderManager.AddPyramidTranche(2))
             {
              m_orderManager.ApplyUnifiedSL(groupBE);
              m_orderManager.LogGroupStop("Tranche 2 (+2.0R) - Cost-Covering Breakeven", groupBE);
             }
        }
      // Tranche 3 at +3.0R (InpTrailStartRR): add the 0.06% tranche.
      // FIX (v5.14): do NOT hand the tight dynamic 'desiredSL' to T3 upon entry.
      // Two reasons: (1) 'desiredSL' is the ATR trail anchored to high0-1.5*ATR,
      // which at +3.0R sits very close to market and can be rejected with
      // INVALID_STOPS or instantly stop the whole basket on a spread spike;
      // (2) the new ticket would otherwise be opened with sl=0. Instead we pass
      // the friction-based group breakeven with the fill request, so T3 is
      // protected from the first tick, and the normal ATR-trail block below
      // (currentRR >= InpLock3RRR) ratchets it forward on the next tick.
      if(currentRR >= InpTrailStartRR && m_orderManager.IsPyramidPending(3))
        {
         double beOffset = CalcBasketFriction(dir==DIR_LONG);
         double safeBE = primaryEntry + (dir==DIR_LONG ? beOffset : -beOffset);
         if(m_orderManager.AddPyramidTranche(3, safeBE))
             {
              m_orderManager.ApplyUnifiedSL(safeBE);
              m_orderManager.LogGroupStop("Tranche 3 (+3.0R) - Executed (Trail Pending)", safeBE);
              // FIX (v5.20): bypass the trail/push blocks below for THIS tick
              // only. The broker needs a moment to register the protective
              // breakeven stop attached to the brand-new ticket; pushing the
              // tight ATR trail in the same tick can be rejected as
              // INVALID_STOPS or, worse, land on the fill and stop the whole
              // basket out on entry. The normal ATR trail takes over on the
              // next tick, once the initial stop is confirmed.
              t3OpenedThisTick = true;
             }
        }
      // Dynamic ATR Trail at +3.0R: apply SAME trailing SL to every ticket
      // Skipped entirely on the tick Tranche 3 was added (see above).
      if(currentRR >= InpLock3RRR && !t3OpenedThisTick)
        {
         m_orderManager.ApplyUnifiedSL(desiredSL);
         // FIX (v5.15): mirror the AUTHORITATIVE ratcheted value back into the
         // active-trade struct. Read back from GetSessionSL() rather than
         // writing desiredSL: the ratchet may have rejected desiredSL and
         // retained a tighter stop, and writing desiredSL here would
         // re-introduce the very desync this fix removes.
         m_orderManager.SetActiveTradeSL(m_orderManager.GetSessionSL());
        }

      // --- Push the primary stop to the broker ---
      // When a multi-tranche basket is active, ApplyUnifiedSL() above already
      // manages every basket ticket, so the single-ticket ModifySL() below is
      // skipped to avoid a conflicting double-modification of the primary.
      // FIX (v5.20): also skipped on the tick Tranche 3 opened, for the same
      // reason as the trail block above -- no stop write races the broker's
      // confirmation of the new ticket's protective stop.
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      if(m_orderManager.GetBasketCount() <= 1 && !t3OpenedThisTick)
        {
         // FIX (v5.16): compare against prevTrailSL (the value BEFORE the
         // session-SL re-seed) so the push reflects genuine local movement
         // rather than the seeding itself.
         if(MathAbs(desiredSL - prevTrailSL) > point)
           {
            if(m_orderManager.ModifySL(trade.ticket, desiredSL))
              {
               m_orderManager.SetActiveTradeSL(desiredSL);
               if(currentRR >= InpLock3RRR) m_trailActivations++;
              }
           }
        }
      else if(currentRR >= InpLock3RRR && !t3OpenedThisTick)
         m_trailActivations++;
      m_tradesManaged++;
     }

   void            SyncTradeState(void)
     {
      if(!m_orderManager.HasActiveTrade()) return;
      SActiveTrade trade;
      if(!m_orderManager.GetActiveTradeRef(trade)) return;
      if(trade.rrUnit <= 0.0) trade.rrUnit = trade.initialSLDistance;
      double price = (trade.direction == DIR_LONG) ? SymbolInfoDouble(m_symbol, SYMBOL_BID) : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double rr = (trade.rrUnit > 0) ? ((trade.direction == DIR_LONG ? price - trade.entryPrice : trade.entryPrice - price) / trade.rrUnit) : 0.0;
      ENUM_TRAIL_STEP step = STEP_NONE;
      // v5.27: STEP_TRAILING now keys off InpLockProfitRR (the step-profit-lock
      // AND ATR-trail trigger) rather than InpLock3RRR. Both default to 3.0, so
      // behaviour is unchanged out of the box, but the classification now
      // follows the input that actually governs the locked-floor milestone.
      // Order matters: highest milestone first.
      if(InpLockProfitRR > 0.0 && rr >= InpLockProfitRR)      step = STEP_TRAILING;
      else if(rr >= InpLock3RRR)                              step = STEP_TRAILING;
      else if(rr >= InpBreakEvenRR)                           step = STEP_BREAKEVEN;
      else if(rr >= InpCutRiskRR)                             step = STEP_HALF_RISK;
      m_orderManager.SetActiveTradeStep(step);
      m_orderManager.SetActiveTradeHighWatermark(price);
     }

   int               GetTradesManaged(void) const     { return m_tradesManaged; }
   int               GetHalfRiskTriggers(void) const  { return m_halfRiskTriggers; }
   int               GetBreakevenTriggers(void) const { return m_breakevenTriggers; }
   int               GetTrailActivations(void) const  { return m_trailActivations; }
   int               GetStopsHit(void) const          { return m_stopsHit; }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_TRADE_MANAGER__