//+------------------------------------------------------------------+
//|                                              COttoTradeManager.mqh |
//|       MODULE 6 - Dynamic Trade Management (exact Pine v4.70) + Pyr |
//|            OTTO EA - Cut / Cost-BE / ATR Trail / Pyramiding       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.36"

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

   // v5.31 -- smart-trim one-shot gate, in MEMORY only.
   // The 0.90% floating-loss rule is re-tested on EVERY tick, so a trim that
   // closed nothing (all legs still above the 70%-toward-SL bar) would re-run
   // its symbol/magic position scan on every tick for as long as the basket
   // stays under water. This mirrors the v5.30 block.priceAbortLogged gate:
   // raised on the first breach, cleared as soon as the float is back under
   // the cap, so each sustained breach reports once instead of per tick.
   bool                 m_trimLogged;

   //+------------------------------------------------------------------+
   //| v5.32 — is the legacy "cut risk in half" rung LIVE?              |
   //|                                                                   |
   //| THE BUG THIS FIXES                                                |
   //| The half-risk rung is a LOSS-SIDE floor: LONG = entry - 0.5R,      |
   //| SHORT = entry + 0.5R. It was evaluated BEFORE the +1.0R cost-      |
   //| covering breakeven and could only ever move the stop toward       |
   //| market, so the two competed for the same value on the same tick.   |
   //|                                                                   |
   //| Under the shipped defaults InpCutRiskRR == InpBreakEvenRR == 1.0,  |
   //| so both rungs fire on the SAME tick and breakeven always wins:     |
   //| its stop sits at entry +/- friction, a fraction of a pip on the    |
   //| PROFIT side, while half-risk sits 0.5R on the LOSS side. The       |
   //| half-risk comparison is therefore provably false the instant it    |
   //| is reached, its guard never assigns, and the rung is DEAD.        |
   //|                                                                   |
   //| The old code nonetheless incremented m_halfRiskTriggers inside     |
   //| that guard's body only when it assigned -- but the shape of the    |
   //| guard meant the "reached the trigger" half of the condition was    |
   //| what the journal acted on, so every +1R winner was reported as     |
   //| "SL -> HALF-RISK / risk now -0.5R" while the live stop sat at      |
   //| breakeven. That is a live-money reporting lie: it tells the        |
   //| operator to risk -0.5R on a trade that cannot lose.               |
   //|                                                                   |
   //| THE FIX (two independent halves):                                 |
   //|  1. A rung whose target can never survive the higher rungs is      |
   //|     INERT, and an inert rung must not count. m_cutRiskRungLive is  |
   //|     resolved ONCE at Initialize() from inputs that cannot change   |
   //|     mid-session, so the gate is a pure read in the hot path.       |
   //|  2. The rung is HOISTED to the END of its direction's ladder, so   |
   //|     the halfRiskSL local is computed only after every profit-side   |
   //|     floor has been applied. Its comparison then tests the FINAL    |
   //|     stop rather than an intermediate one, which makes the trigger  |
   //|     counter mean exactly what the journal claims: "this floor was  |
   //|     actually applied to the live stop".                            |
   //|                                                                   |
   //| Behaviour is otherwise identical: every rung still resolves with   |
   //| max() for LONG / min() for SHORT, and max/min are order-           |
   //| independent, so hoisting cannot change which floor wins. The       |
   //| rung's TARGET (a literal fraction of 1R) is deliberately left as   |
   //| the only such literal in the ladder -- see the v5.31 milestone     |
   //| probe, which pins this rung's arithmetic to exactly one site per   |
   //| direction.                                                         |
   //+------------------------------------------------------------------+
   bool                 m_cutRiskRungLive;

   // v5.32 -- one-shot trace bookkeeping for the Tranche 2 crossing.
   // Holds the 5%-wide RR bucket most recently reported so the crossing
   // is logged once per approach instead of once per tick. -1 = unarmed.
   int                  m_t2TraceBucket;

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

   //+------------------------------------------------------------------+
   //| v5.31 — SMART-TRIM WALK: one leg filter, two modes.              |
   //|                                                                   |
   //| Walks the broker's live position book rather than m_basket[]:     |
   //| a tranche that opened between the last Update() and this call is  |
   //| still seen, and a leg already closed is never counted. The scan   |
   //| is filtered on BOTH POSITION_SYMBOL and POSITION_MAGIC, because   |
   //| the magic 20240624 is shared by every OTTO chart in the account — |
   //| a trim on EURUSD must never touch a GBPUSD tranche.               |
   //|                                                                   |
   //| Tranche 1 (the primary, == SActiveTrade.ticket) is excluded by     |
   //| construction. It is the leg whose stop-loss defines the basket's   |
   //| risk geometry and the leg the tracker keeps, so trimming it would  |
   //| be an exit, not a trim. The ticket is read ONCE, before the walk:  |
   //| ForceClose() clears the tracked-basket state on the last leg, so   |
   //| re-reading it inside the loop would change the exclusion set       |
   //| mid-walk.                                                          |
   //|                                                                   |
   //| A leg qualifies once it has travelled at least trimPct percent of  |
   //| the way from its entry to its OWN stop, measured in that leg's     |
   //| direction, so a leg still in profit scores 0 and is kept. Legs     |
   //| whose stop has already been ratcheted to (or beyond) their entry   |
   //| are PROTECTED at breakeven or better: their remaining risk is zero |
   //| and "% of the way to the stop" is no longer meaningful, so they are |
   //| skipped rather than trimmed -- trimming them would be taking a     |
   //| profit/breakeven exit, not cutting a loser. Legs with no stop at   |
   //| all are skipped for the same reason the geometry guard exists.     |
   //|                                                                   |
   //| The effective stop is the broker's, tightened toward the unified   |
   //| session stop when that is nearer. A rejected ApplyUnifiedSL leaves  |
   //| the live leg carrying the wider stop from its fill while           |
   //| m_sessionSL holds the ratcheted basket stop, so taking the tighter |
   //| of the two reports the leg's true progress instead of             |
   //| under-reporting it and keeping a leg the basket has already moved  |
   //| to breakeven. m_sessionSL is re-seeded by InitBasket() on every    |
   //| new basket, so it can never describe a previous basket here.       |
   //|                                                                   |
   //| dryRun=true counts only. BOTH callers share this one filter on     |
   //| purpose: a divergence between "what we counted" and "what we would |
   //| close" would let the escalation decision rest on evidence the      |
   //| trim never gathered.                                               |
   //|                                                                   |
   //| The walk runs DOWNWARD because the apply mode closes as it goes: a |
   //| removal shifts every higher index down by one, so an ascending loop |
   //| would skip the leg that slid into the freed slot.                 |
   //+------------------------------------------------------------------+
   int               WalkTrimLegs(double trimPct, bool dryRun, int &outClosed)
     {
      long  magic   = (long)MagicNumber;
      ulong primary = m_orderManager.GetActiveTrade().ticket;
      double sessionSL = m_orderManager.GetSessionSL();
      int   qualified  = 0;
      outClosed = 0;

      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != magic) continue;

         ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
         if(ticket == 0 || ticket == primary) continue;

         bool   isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl     = PositionGetDouble(POSITION_SL);

         // No stop at all -> cannot measure "% of the way to the stop".
         if(sl <= 0.0) continue;

         if(sessionSL > 0.0)
           {
            if(isLong) sl = MathMax(sl, sessionSL);
            else       sl = MathMin(sl, sessionSL);
           }

         double total = isLong ? (entry - sl) : (sl - entry);
         if(total <= 0.0) continue;   // stop at/past entry (protected) or wrong side

         double mark  = isLong ? SymbolInfoDouble(m_symbol, SYMBOL_BID)
                              : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
         double moved = isLong ? (entry - mark) : (mark - entry);

         if(moved < (trimPct / 100.0) * total) continue;
         qualified++;

         if(dryRun) continue;
         if(m_orderManager.ForceClose(ticket))
           {
            outClosed++;
            if(EnableLogging)
               Print("[SmartTrim] Closed non-primary tranche ", ticket, " | ",
                     DoubleToString(100.0 * moved / total, 1),
                     "% of the way to its stop");
           }
        }

      return qualified;
     }

public:
   COttoTradeManager(void)
     {
      m_symbol=""; m_riskManager=NULL; m_orderManager=NULL; m_blockManager=NULL;
      m_tradesManaged=0; m_halfRiskTriggers=0; m_breakevenTriggers=0; m_trailActivations=0; m_stopsHit=0;
      m_trimLogged=false;
      // v5.32: pessimistic default. Initialize() resolves the real value from
      // the inputs; until then every half-risk rung is treated as inert, which
      // is the safe direction -- a rung that cannot assign must not count.
      m_cutRiskRungLive=false;
      m_t2TraceBucket=-1;
     }
   ~COttoTradeManager(void) { }

   bool            Initialize(string symbol, COttoRiskManager *rm, COttoOrderManager *om, COttoBlockManager *bm)
     {
      m_symbol=symbol; m_riskManager=rm; m_orderManager=om; m_blockManager=bm;

      //+----------------------------------------------------------------+
      //| v5.32 -- RESOLVE THE HALF-RISK RUNG'S LIVENESS, ONCE.          |
      //|                                                                |
      //| The rung is a LOSS-SIDE floor at entry -/+ 0.5R and every      |
      //| profit-side rung OUTRANKS it: the +1.0R cost-covering          |
      //| breakeven already sits a fraction of a pip on the PROFIT side, |
      //| so once it has been applied the half-risk floor can never      |
      //| assign again. The rung is therefore only reachable at all      |
      //| while the stop is still worse than -0.5R, i.e. before          |
      //| breakeven -- which requires InpCutRiskRR to trigger strictly   |
      //| EARLIER than InpBreakEvenRR.                                   |
      //|                                                                |
      //| At the shipped defaults (both 1.0) the rung is inert, and an   |
      //| inert rung must neither move the stop NOR increment the        |
      //| counter the journal reports from. Previously it did the        |
      //| latter, which is how every +1R winner came to be journalled as |
      //| "risk now -0.5R" while the live stop was already at breakeven. |
      //|                                                                |
      //| Inputs are read-only at runtime, so this needs no per-tick     |
      //| re-evaluation: the hot path just reads the resolved bool.      |
      //+----------------------------------------------------------------+
      bool cutBeforeBE = (InpCutRiskRR > 0.0 && InpCutRiskRR < InpBreakEvenRR);
      m_cutRiskRungLive = cutBeforeBE;
      if(!cutBeforeBE)
        {
         // Loud by design: an operator who sees this line knows the half-risk
         // rung is inert and that the journal will NEVER report HALF-RISK --
         // by construction, not because a trigger was missed.
         if(EnableLogging)
            Print("[TradeManager] INERT RUNG: half-risk rung disabled because ",
                  "InpCutRiskRR (", DoubleToString(InpCutRiskRR,2), ") is not below ",
                  "InpBreakEvenRR (", DoubleToString(InpBreakEvenRR,2), "). ",
                  "The -0.5R floor can never survive the cost-covering breakeven, ",
                  "so it is never applied and never reported. Journal HALF-RISK ",
                  "lines are suppressed.");
        }
      else if(EnableLogging)
         Print("[TradeManager] LIVE RUNG: half-risk rung armed at ",
               DoubleToString(InpCutRiskRR,2), "R (< breakeven ",
               DoubleToString(InpBreakEvenRR,2), "R) -> -0.5R floor is reachable");
      return true;
     }

   //+------------------------------------------------------------------+
   //| v5.31 — SMART TRIM: closes only the non-primary tranches that    |
   //| have travelled at least InpTrimLoserStopPct of the way to their  |
   //| own stop, and reports whether the FULL basket close is still      |
   //| required.                                                         |
   //|                                                                   |
   //| Returns TRUE only when the trim could not act, i.e. no leg was    |
   //| past the bar. The caller then falls back to CloseEntireBasket(),   |
   //| which is exactly the v5.30 fallback: the 0.90% cap is still       |
   //| enforced when there is nothing to trim, and it is retried on each  |
   //| tick while the float stays over the cap.                           |
   //|                                                                   |
   //| Returns FALSE when the trim DID act (the basket was relieved       |
   //| leg-by-leg and the primary keeps running under its own stop) and   |
   //| also when this breach has already been trimmed. Escalating on the  |
   //| primary's survival would full-close on every trimmed breach and    |
   //| make the trim a no-op, so it deliberately does not.                |
   //|                                                                   |
   //| The latch is one-shot PER BREACH rather than per call: OnTick()    |
   //| re-tests the 0.90% cap every tick, so without it a basket that     |
   //| stayed under water would be re-trimmed on every tick (the v5.30    |
   //| priceAbortLogged pendulum). It is raised ONLY when the trim        |
   //| actually closed something, so the "nothing to trim" path still     |
   //| escalates every tick instead of silently giving up. Closing the    |
   //| losing legs converts their float into a realised loss, which drops  |
   //| the float under the cap and lets ClearTrimLatch() re-arm the trim   |
   //| for the next excursion.                                            |
   //+------------------------------------------------------------------+
   bool            TrimHeavyLosers(double trimPct)
     {
      if(!m_orderManager.HasActiveTrade() && m_orderManager.CountOpenPositions() == 0)
        {
         m_trimLogged = false;
         return false;
        }

      if(m_trimLogged) return false;   // already trimmed for this breach

      int closed = 0;
      int qualified = WalkTrimLegs(trimPct, false, closed);

      if(closed == 0)
        {
         if(EnableLogging)
            Print("[SmartTrim] nothing to trim | ", qualified,
                  " leg(s) considered, none past ", DoubleToString(trimPct, 1),
                  "% toward its stop — falling back to full basket close");
         return true;   // nothing relieved -> caller does the full close
        }

      m_trimLogged = true;
      if(EnableLogging)
         Print("[SmartTrim] trimmed ", closed, " of ", qualified,
               " qualifying tranche(s) | symbol=", m_symbol,
               " | threshold=", DoubleToString(trimPct, 1),
               "% toward stop — primary retained");
      return false;
     }

   //+------------------------------------------------------------------+
   //| v5.31 — clears the one-shot trim latch.                          |
   //|                                                                   |
   //| The latch must be released as soon as the float is back under the |
   //| cap, otherwise it would survive into the NEXT basket and the first |
   //| breach of that later basket would silently skip its trim. Called   |
   //| from the non-breach path of the OnTick safety block, i.e. exactly  |
   //| the "price is back inside the boundary" moment the v5.30 gate      |
   //| uses. Cheap and idempotent, so it is safe to call every tick.      |
   //+------------------------------------------------------------------+
   void            ClearTrimLatch(void) { m_trimLogged = false; }

   //+------------------------------------------------------------------+
   //| v5.31 — read-only dry run of the smart-trim filter.              |
   //| Counts the legs a trim WOULD close without touching the book, so  |
   //| a status log or a probe can report the trim's exposure without    |
   //| trading. Shares WalkTrimLegs(dryRun=true) with the live trim.     |
   //+------------------------------------------------------------------+
   int             CountTrimCandidates(double trimPct)
     {
      if(!m_orderManager.HasActiveTrade() && m_orderManager.CountOpenPositions() == 0)
         return 0;
      int closed = 0;   // unused in dry-run mode
      return WalkTrimLegs(trimPct, true, closed);
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
      // FIX (v5.20): set when a scaling tranche is added on THIS tick. The
      // ATR-trail block and the single-ticket broker push are both bypassed for
      // that one tick so the broker can confirm the protective breakeven stop on
      // the new ticket before the dynamic trail takes over.
      // v5.29: generalised from t3OpenedThisTick. With the trail now arming at
      // +1.0R, the Tranche 2 fill, the breakeven ratchet AND the trail
      // activation all land on the SAME tick, so the guard has to cover every
      // rung. Scoping it to Tranche 3 alone would have let a tight ATR trail be
      // written over a brand-new T2 ticket whose protective stop the broker had
      // not yet confirmed -- the v5.14/v5.20 INVALID_STOPS / instant-stopped-out
      // failure mode, moved one rung earlier.
      bool trancheOpenedThisTick = false;
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
         // v5.32: the legacy half-risk rung USED to sit here, ahead of every
         // profit-side floor. It has been HOISTED to the end of this branch --
         // see the "HALF-RISK (HOISTED)" block below. Nothing else moved.
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
         // v5.29: STEP PROFIT LOCK rung 2 (InpLockProfit2RR -> InpLockProfit2TargetRR).
         // Evaluated AFTER rung 3 above so the HIGHER milestone always wins: at or
         // past +3.0R the stop already sits at +2.0R, which is forward of the
         // +1.0R this rung would set, so its guard is simply false and the rung
         // is a no-op. Keeping the rungs in descending order makes the ladder
         // independent of which single trigger happens to be crossed first.
         if(InpLockProfit2RR > 0.0 && InpLockProfit2TargetRR > 0.0 && currentRR >= InpLockProfit2RR)
           {
            double lockedSL2 = primaryEntry + (InpLockProfit2TargetRR * rrUnit);
            if(lockedSL2 > desiredSL) desiredSL = lockedSL2;
           }
         // v5.29: trail gate repointed from InpLock3RRR (3.0) to InpTrailStartRR
         // (1.0), so the ATR trail arms from +1.0R instead of waiting for the
         // step at which the pyramid reaches full size. Evaluated AFTER both lock
         // rungs, so the tighter of the three wins on this same tick.
         if(currentRR >= InpTrailStartRR)
           {
            double dynamicTrail = high0 - (InpTrailATRMultiplier * atr);
            if(dynamicTrail > desiredSL) desiredSL = dynamicTrail;
           }
         // --- HALF-RISK (HOISTED, v5.32) ---
         // Evaluated LAST in this branch: after breakeven, both step locks and
         // the ATR trail. The guard below therefore tests the FINAL stop rather
         // than an intermediate one, which is what makes m_halfRiskTriggers
         // honest -- it increments only when the -0.5R floor was genuinely
         // APPLIED to the live stop.
         //
         // m_cutRiskRungLive is resolved once in Initialize(). With
         // InpCutRiskRR >= InpBreakEvenRR the rung can never survive the
         // breakeven floor above it, so it is inert and must not report. That
         // gate is what removes the phantom "risk now -0.5R" journal line from
         // every +1R winner.
         //
         // max() resolution is order-independent, so hoisting this rung cannot
         // change which floor wins -- only what the counter means.
         double halfRiskSL = primaryEntry - (0.5 * rrUnit);
         if(m_cutRiskRungLive && currentRR >= InpCutRiskRR && desiredSL < halfRiskSL)
           { desiredSL = halfRiskSL; m_halfRiskTriggers++; }
        }
      else // SHORT
        {
         currentRR = (rrUnit > 0) ? (primaryEntry - liveAsk) / rrUnit : 0;
         // v5.32: half-risk rung HOISTED to the end of this branch (mirror of
         // the LONG branch) so its counter means "the -0.5R floor was applied".
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
         // v5.29: STEP PROFIT LOCK rung 2 -- mirror of the LONG branch. Placed
         // after rung 3 for the same reason: the higher milestone must win.
         if(InpLockProfit2RR > 0.0 && InpLockProfit2TargetRR > 0.0 && currentRR >= InpLockProfit2RR)
           {
            double lockedSL2 = primaryEntry - (InpLockProfit2TargetRR * rrUnit);
            if(lockedSL2 < desiredSL) desiredSL = lockedSL2;
           }
         // v5.27: DYNAMIC ATR TRAIL — mirror of the LONG branch.
         // v5.29: trail gate repointed InpLock3RRR -> InpTrailStartRR (mirror).
         if(currentRR >= InpTrailStartRR)
           {
            double dynamicTrail = low0 + (InpTrailATRMultiplier * atr);
            if(dynamicTrail < desiredSL) desiredSL = dynamicTrail;
           }
         // --- HALF-RISK (HOISTED, v5.32) --- mirror of the LONG branch.
         // Evaluated after breakeven, both step locks and the ATR trail, so the
         // guard tests the FINAL stop and the counter only fires on a real
         // application. The '<' is mirrored to '>' so the ratchet still only
         // ever moves the stop toward market.
         double halfRiskSL = primaryEntry + (0.5 * rrUnit);
         if(m_cutRiskRungLive && currentRR >= InpCutRiskRR && desiredSL > halfRiskSL)
           { desiredSL = halfRiskSL; m_halfRiskTriggers++; }
        }

      // --- PYRAMID (unified group stop) ---
      // v5.27: Tranche 2 at +2.0R, driven by its OWN input (InpPyramidT2RR).
      // Previously this read InpBreakEvenRR, which worked only because both
      // happened to equal 2.0; lowering breakeven to 1.0 for the 1:1 rule would
      // have silently dragged the T2 scale-in down to 1.0R. The trigger is now
      // independent: add the InpRiskT2Pct tranche, then move the unified basket
      // stop to exact Cost-Covering Breakeven (entry +/- beOffset, where
      // beOffset already accounts for broker commission + swap friction).
      // v5.29: this rung now fires at +1.0R (InpPyramidT2RR = 1.0), which is the
      // tick on which three things coincide -- the T2 fill, the +1.0R breakeven
      //+------------------------------------------------------------------+
      //| v5.32 -- TRANCHE 2 CROSSING TRACE                                |
      //|                                                                  |
      //| This rung had never been observed firing in the field, and every |
      //| refusal inside AddPyramidTranche() returned a bare 'false', so   |
      //| "T2 never triggers" was indistinguishable from "T2 triggered    |
      //| and was silently refused". This block makes the crossing         |
      //| observable once per approach: the first tick whose RR enters the |
      //| 5%-wide band below the trigger prints EVERY quantity the         |
      //| decision depends on -- the live RR, the R unit it was measured   |
      //| against, the basket size, the ladder cursor, and the lot the     |
      //| rung would risk.                                                 |
      //|                                                                  |
      //| It deliberately runs BEFORE the gate below, so the trace also    |
      //| fires when that gate's second half, IsPyramidPending(2), is      |
      //| false -- which is the other candidate root cause: a m_nextTranche|
      //| that has already advanced past 2.                                |
      //|                                                                  |
      //| Read-only apart from the bucket latch, which stops a basket      |
      //| hovering just under the trigger from logging once per tick. The  |
      //| latch re-arms whenever the ladder leaves rung 2, so the next     |
      //| basket traces again.                                             |
      //+------------------------------------------------------------------+
      if(EnableLogging && InpPyramidT2RR > 0.0)
        {
         // Re-arm for the next basket: the ladder leaves rung 2 either by
         // advancing (T2 fired) or by the whole basket being cleared, and in
         // both cases a fresh basket should get its own crossing report.
         // Deliberately NOT also keyed on InpPyramidEnable: a disabled pyramid
         // leaves the cursor parked on 2, and re-arming every tick would turn
         // this trace into a per-tick spammer. Parked on 2 is the correct
         // behaviour for the disabled case too -- one report per approach.
         if(m_orderManager.GetNextTranche() != 2)
            m_t2TraceBucket = -1;

         double t2Band = InpPyramidT2RR * 0.05;   // 5% of the trigger
         if(currentRR >= (InpPyramidT2RR - t2Band))
           {
            int bucket = (int)MathFloor(currentRR / MathMax(t2Band, 0.0001));
            if(bucket != m_t2TraceBucket)
              {
               m_t2TraceBucket = bucket;
               double basketR = m_orderManager.GetBasketRRUnit();
               double traceR  = (basketR > 0.0) ? basketR : rrUnit;
               double traceLot = (traceR > 0.0)
                                 ? m_riskManager.RiskPctLotSize(InpRiskT2Pct, traceR) : 0.0;
               Print("[Pyramid] T2 crossing: currentRR=", DoubleToString(currentRR,3),
                     " trigger=", DoubleToString(InpPyramidT2RR,2), "R",
                     " | basketRRUnit=", DoubleToString(basketR, _Digits),
                     " ladderRRUnit=", DoubleToString(rrUnit, _Digits),
                     " | basketCount=", m_orderManager.GetBasketCount(),
                     " | nextTranche=", m_orderManager.GetNextTranche(),
                     " pending2=", (m_orderManager.IsPyramidPending(2) ? "true" : "false"),
                     " | riskT2%=", DoubleToString(InpRiskT2Pct,3),
                     " lot=", DoubleToString(traceLot,2),
                     " | dir=", (dir == DIR_LONG ? "LONG" : "SHORT"),
                     " enable=", (InpPyramidEnable ? "true" : "false"));
              }
           }
        }
      // ratchet and the +1.0R ATR-trail activation -- so the guard below is set
      // unconditionally on a successful add.
      if(currentRR >= InpPyramidT2RR && m_orderManager.IsPyramidPending(2))
        {
         double beOffset = CalcBasketFriction(dir==DIR_LONG);
         double groupBE = primaryEntry + (dir==DIR_LONG ? beOffset : -beOffset);
         // v5.29: hand groupBE to AddPyramidTranche as slOverride so the market
         // fill carries its protective stop in the SAME request. T2 previously
         // relied solely on the ApplyUnifiedSL() on the next line, so a skipped
         // or rejected call left the new ticket naked in hedge mode. T3 has
         // always been opened with the override; T2 now matches.
         if(m_orderManager.AddPyramidTranche(2, groupBE))
             {
              m_orderManager.ApplyUnifiedSL(groupBE);
              m_orderManager.LogGroupStop("Tranche 2 (+1.0R) - Cost-Covering Breakeven", groupBE);
              // v5.29: bypass the trail/push blocks below for THIS tick only.
              // The broker needs a moment to register the protective breakeven
              // stop attached to the brand-new ticket; pushing the tight ATR
              // trail in the same tick can be rejected as INVALID_STOPS or,
              // worse, land on the fill and stop the whole basket out on entry.
              // The normal ATR trail takes over on the next tick.
              trancheOpenedThisTick = true;
             }
        }
      // Tranche 3 at +2.0R (InpPyramidT3RR): add the InpRiskT3Pct tranche.
      // FIX (v5.14): do NOT hand the tight dynamic 'desiredSL' to T3 upon entry.
      // Two reasons: (1) 'desiredSL' is the ATR trail anchored to high0-1.5*ATR,
      // which at +2.0R already sits close to market and can be rejected with
      // INVALID_STOPS or instantly stop the whole basket on a spread spike;
      // (2) the new ticket would otherwise be opened with sl=0. Instead we pass
      // the friction-based group breakeven with the fill request, so T3 is
      // protected from the first tick, and the normal ATR-trail block below
      // (currentRR >= InpTrailStartRR) ratchets it forward on the next tick.
      if(currentRR >= InpPyramidT3RR && m_orderManager.IsPyramidPending(3))
        {
         double beOffset = CalcBasketFriction(dir==DIR_LONG);
         double safeBE = primaryEntry + (dir==DIR_LONG ? beOffset : -beOffset);
         if(m_orderManager.AddPyramidTranche(3, safeBE))
             {
              m_orderManager.ApplyUnifiedSL(safeBE);
              m_orderManager.LogGroupStop("Tranche 3 (+2.0R) - Executed (Trail Pending)", safeBE);
              trancheOpenedThisTick = true;
             }
        }
      // Tranche 4 at +3.0R (InpPyramidT4RR): add the final InpRiskT4Pct tranche.
      // This is the last rung, so a successful add advances m_nextTranche to 0
      // and IsPyramidPending() is false for every rung from here on. Mirrors the
      // T3 pattern exactly: the friction-based group breakeven travels WITH the
      // fill request (never the tight dynamic 'desiredSL', for the INVALID_STOPS
      // reasons spelled out above), and the trail/push blocks are skipped for
      // this tick so the broker can confirm the new ticket's protective stop
      // before anything tightens it further.
      if(currentRR >= InpPyramidT4RR && m_orderManager.IsPyramidPending(4))
        {
         double beOffset4 = CalcBasketFriction(dir==DIR_LONG);
         double safeBE4 = primaryEntry + (dir==DIR_LONG ? beOffset4 : -beOffset4);
         if(m_orderManager.AddPyramidTranche(4, safeBE4))
             {
              m_orderManager.ApplyUnifiedSL(safeBE4);
              m_orderManager.LogGroupStop("Tranche 4 (+3.0R) - Executed (Trail Pending)", safeBE4);
              trancheOpenedThisTick = true;
             }
        }
      // Dynamic ATR Trail: apply the SAME trailing SL to every ticket.
      // Skipped entirely on the tick a scaling tranche was added (see above).
      if(currentRR >= InpTrailStartRR && !trancheOpenedThisTick)
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
      // FIX (v5.20): also skipped on the tick a scaling tranche opened, for the
      // same reason as the trail block above -- no stop write races the broker's
      // confirmation of the new ticket's protective stop. v5.29: this now covers
      // T2 as well as T3/T4, which matters because the T2 fill coincides with
      // the +1.0R trail activation.
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      if(m_orderManager.GetBasketCount() <= 1 && !trancheOpenedThisTick)
        {
         // FIX (v5.16): compare against prevTrailSL (the value BEFORE the
         // session-SL re-seed) so the push reflects genuine local movement
         // rather than the seeding itself.
         if(MathAbs(desiredSL - prevTrailSL) > point)
           {
            if(m_orderManager.ModifySL(trade.ticket, desiredSL))
              {
               m_orderManager.SetActiveTradeSL(desiredSL);
               if(currentRR >= InpTrailStartRR) m_trailActivations++;
              }
           }
        }
      else if(currentRR >= InpTrailStartRR && !trancheOpenedThisTick)
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
      // v5.27: STEP_TRAILING keys off InpLockProfitRR (the step-profit-lock
      // milestone). v5.29: the second branch was repointed from InpLock3RRR to
      // InpTrailStartRR, so the reported trail step now agrees with the input
      // that actually arms the trail in Update(). As before, both remain
      // inputs -- the defaults (3.0 / 1.0) simply put the trail ahead of the
      // +3.0R lock, which is intentional.
      // Order matters: highest milestone first.
      if(InpLockProfitRR > 0.0 && rr >= InpLockProfitRR)      step = STEP_TRAILING;
      else if(rr >= InpTrailStartRR)                          step = STEP_TRAILING;
      else if(rr >= InpBreakEvenRR)                           step = STEP_BREAKEVEN;
      // v5.32: gated on the rung's resolved liveness so this state machine
      // cannot advertise a stop the ladder structurally never applies. With
      // InpCutRiskRR >= InpBreakEvenRR the branch above is always taken first
      // anyway, so this is a statement of intent rather than a behaviour
      // change -- but it keeps the reported step and the journalled events
      // (m_halfRiskTriggers) derived from the SAME gate.
      else if(m_cutRiskRungLive && rr >= InpCutRiskRR)        step = STEP_HALF_RISK;
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