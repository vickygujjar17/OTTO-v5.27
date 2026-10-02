//+------------------------------------------------------------------+
//|                                              COttoOrderManager.mqh |
//|         MODULE 5 — Pine-gated Limit Placement & Order Lifecycle  |
//|              OTTO EA — exact Pine v4.70 execution port           |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.33"

#ifndef __OTTO_ORDER_MANAGER__
#define __OTTO_ORDER_MANAGER__

#include "OttoDefines.mqh"
#include "COttoRiskManager.mqh"
#include "COttoBlockManager.mqh"
#include "COttoCorrelationFilter.mqh"
#include "COttoJournal.mqh"

//+------------------------------------------------------------------+
//| COttoOrderManager class                                         |
//| Translates the Pine strategy.entry limit orders into MT5         |
//| OrderSend TRADE_ACTION_PENDING calls. Reimplements Pine gates:   |
//|   pass_news, pass_macro, pass_sent, pass_dd, can_place.          |
//| Also handles fill detection (seeding SActiveTrade), order        |
//| cancellation for invalidated blocks, and direction conflict.     |
//+------------------------------------------------------------------+
class COttoOrderManager
  {
private:
   string                  m_symbol;
   COttoRiskManager       *m_riskManager;
   COttoBlockManager      *m_blockManager;
   COttoCorrelationFilter *m_correlationFilter;
   COttoJournal           *m_journal;

   // --- Active trade tracking ---
   SActiveTrade            m_activeTrade;
   bool                    m_hasActiveTrade;
   ENUM_TRADE_DIRECTION    m_activeDirection;
   //| v5.33 adoption state. m_adoptedManual marks the tracked primary as a
   //| MAGIC-0 position the operator opened by hand; it is mirrored onto
   //| m_activeTrade.adoptedManual for the journal and pushed to the High Table
   //| auditor through otto.mq5. m_manualNoSLWarnTick throttles the "manual leg
   //| has no stop" notice: a position without an SL is refused every tick, and
   //| an unthrottled Print would flood the log with the same refusal thousands
   //| of times an hour.
   bool                    m_adoptedManual;
   datetime                m_manualNoSLWarnTick;

   // --- EXPERIMENT (experiment/reverse-sr): virtual market orders --------
   //| InpReverseSR inverts the polarity mapping, which places a setup's
   //| entry on the WRONG side of the market for any resting limit: a
   //| reversed SUPPORT setup is a SELL whose entry lies BELOW the live
   //| price, so a SELL_LIMIT there is refused by the server with
   //| TRADE_RETCODE_INVALID_PRICE -- and symmetrically for a reversed
   //| RESISTANCE BUY. No legal pending equivalent exists, so the setup's
   //| geometry is held HERE and fired as a MARKET order once price reaches
   //| the stored entry. Every field below is the exact mirror of what the
   //| pending path would have placed; a default deployment (InpVirtualOrders
   //| = false) never touches any of it.
   int                     m_virtualBlockSerial[];   // block serial (stable across array compaction)
   int                     m_virtualBlockIndex[];    // block index at mark time (diagnostics only)
   ENUM_TRADE_DIRECTION    m_virtualDirection[];     // mapped direction (via GetDirectionForBlock)
   ENUM_ORDER_TYPE         m_virtualOrderType[];     // BUY/SELL MARKET that matches the direction
   double                  m_virtualEntry[];         // stored trigger price (block.localEntry)
   double                  m_virtualSL[];            // stored stop-loss
   double                  m_virtualTP[];            // InpMaxRR projection (mirrors localTP)
   double                  m_virtualRRUnit[];        // b_height + 0.5*ATR at mark time
   double                  m_virtualLot[];           // lot size resolved at mark time
   int                     m_virtualCount;           // live virtual setups
   ulong                   m_lastVirtualTriggerMs;   // GetTickCount() of the last market fire

   // --- Pending limit order tracking ---
   ulong                   m_pendingLimitTickets[];
   int                     m_pendingLimitCount;

   // --- Statistics ---
   int                     m_ordersPlaced;
   int                     m_ordersFilled;
   int                     m_ordersRejected;
   //| Count of SL-modify requests that the venue refused. Distinct from
   //| m_ordersRejected (which counts order PLACEMENTS) because a rejected
   //| stop is a different and more dangerous class of failure: the position
   //| is already open, so a stop the trail believes is live may not exist.
   //| Incremented in ModifyStopLoss, surfaced via GetStopModifyFailures()
   //| and pushed to the High Table auditor, which alerts on the delta.
   int                     m_stopModifyFailures;
   int                     m_reversalsExecuted;
   int                     m_retryCount;

   // --- Reversal state machine ---
   bool                    m_reversalInProgress;
   SSniperBlock            m_reversalTargetBlock;
   ENUM_TRADE_DIRECTION    m_reversalTargetDir;
   datetime                m_reversalStartTime;

   // --- Physical OrderSend throttle ---
   datetime                m_lastOrderTime;

   // --- PYRAMID BASKET (unified group stop) ---
   SPyramidTranche         m_basket[];        // active scaling tranches
   int                     m_basketCount;     // number of open tranches
   double                  m_primaryEntry;    // Tranche 1 entry (reference for RR)
   double                  m_basketRRUnit;    // rrUnit shared by basket
   int                     m_nextTranche;     // next tranche to add (2, 3, 4 or 0 = exhausted)
   ENUM_TRADE_DIRECTION    m_basketDir;
   datetime                m_basketOpenTime;
   string                  m_sessionID;       // unique session ID for this trade basket
   double                  m_sessionSL;       // one-way-ratchet unified stop (never backward)

   //+------------------------------------------------------------------+
   //| FIX 1: three-second OrderSend throttle                           |
   //+------------------------------------------------------------------+
   bool                    CheckOrderTimeLock(void)
     {
      if(TimeCurrent() - m_lastOrderTime < 3)
        {
         if(EnableLogging)
            Print("[OrderManager] TIME-LOCK: < 3s since last OrderSend");
         return false;
        }
      m_lastOrderTime = TimeCurrent();
      return true;
     }

   //+------------------------------------------------------------------+
   //| Bulletproof position scan by MagicNumber + symbol               |
   //+------------------------------------------------------------------+
   bool                    HasPositionForMagic(void)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(PositionSelectByTicket(PositionGetTicket(i)))
           {
            if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
               PositionGetString(POSITION_SYMBOL) == m_symbol)
               return true;
           }
        }
      return false;
     }

   bool                    ValidateStopDistance(double price, double sl, bool isLong)
     {
      double stopsLevel = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL) *
                          SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double freezLevel = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL) *
                          SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double maxLevel = MathMax(stopsLevel, freezLevel);
      double slDistance = MathAbs(price - sl);
      if(slDistance < maxLevel)
        {
         if(EnableLogging)
            Print("[OrderManager] WARNING: SL distance ", DoubleToString(slDistance, Digits()),
                  " < min required ", DoubleToString(maxLevel, Digits()));
         return false;
        }
      return true;
     }

   double                  AdjustSLToMinimum(double price, double sl, bool isLong)
     {
      double stopsLevel = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL) *
                          SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double freezLevel = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL) *
                          SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double minDist = MathMax(stopsLevel, freezLevel) * 1.1;
      if(isLong) { if(price - sl < minDist) return price - minDist; }
      else       { if(sl - price < minDist) return price + minDist; }
      return sl;
     }

   //+------------------------------------------------------------------+
   //| v5.30 — pre-flight price helpers                                 |
   //|                                                                  |
   //| GetPriceBoundaryBuffer: the minimum distance a pending limit     |
   //| must keep from the live market price. STOPS_LEVEL and            |
   //| FREEZE_LEVEL are both honoured (brokers publish one, the other   |
   //| or both), plus one point of cushion: a price resting exactly on  |
   //| the boundary is still refused by some servers, whereas with the  |
   //| cushion it simply passes on the next tick.                       |
   //|                                                                  |
   //| Deliberately NOT named `stopsLevel` — that identifier is already |
   //| a local inside ValidateStopDistance, AdjustSLToMinimum and the   |
   //| guard in PlaceLimitOrder, so a field of that name would be       |
   //| shadowed at every call site.                                     |
   //+------------------------------------------------------------------+
   double                  GetPriceBoundaryBuffer(void)
     {
      double point     = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double stopsPts  = (double)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL);
      double freezePts = (double)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
      if(point <= 0.0) point = _Point;
      return MathMax(stopsPts, freezePts) * point + point;
     }

   //+------------------------------------------------------------------+
   //| SnapToTick — round a price onto the symbol's trade tick grid.    |
   //| A price that is off-grid is refused by the server with           |
   //| TRADE_RETCODE_INVALID_PRICE even when it sits on the correct     |
   //| side of the market. Shared by the PlaceLimitOrder pre-flight     |
   //| snap and the SendOrderWithRetry re-quote path so the two grids   |
   //| can never drift apart.                                           |
   //+------------------------------------------------------------------+
   double                  SnapToTick(double price)
     {
      double tick = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick > 0.0)
         price = MathRound(price / tick) * tick;
      return NormalizeDouble(price, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
     }

   double                  GetAsk(void) { return SymbolInfoDouble(m_symbol, SYMBOL_ASK); }
   double                  GetBid(void) { return SymbolInfoDouble(m_symbol, SYMBOL_BID); }

   //+------------------------------------------------------------------+
   //| v5.32 -- BASKET 1R PERSISTENCE ACROSS A RESTART                  |
   //|                                                                  |
   //| m_basketRRUnit is the divisor for EVERY RR decision the Trade    |
   //| Manager makes, and it is NOT recoverable from the broker. By the |
   //| time a restart re-adopts the position the stop may already have  |
   //| been ratcheted (breakeven alone collapses it to a fraction of a  |
   //| pip), so |entry - POSITION_SL| understates the true 1R.          |
   //|                                                                  |
   //| The consequence is not cosmetic. An understated 1R INFLATES the  |
   //| live RR, which is precisely the input the +1.0R tranche-2 gate   |
   //| tests; a grossly inflated RR can jump the rung in a single tick  |
   //| and the crossing is never observed. Persisting 1R removes that   |
   //| whole failure class on the restart path.                         |
   //|                                                                  |
   //| The stored value is keyed to the PRIMARY POSITION TICKET, which  |
   //| MT5 keeps stable across restarts. That pairing is what makes the |
   //| restore safe: a leftover value from some earlier, already-closed  |
   //| basket can never be applied to a different position, because the  |
   //| ticket will not match. A mismatch falls back to the broker-derived |
   //| distance and says so loudly.                                     |
   //+------------------------------------------------------------------+
   string                  BasketGvName(const string key)
     {
      return "OTTO_" + key + "_" + m_symbol + "_" + IntegerToString(MagicNumber);
     }

   void                    PersistBasketR(double r, ulong primaryTicket)
     {
      if(r <= 0.0 || primaryTicket == 0) return;
      if(!GlobalVariableSet(BasketGvName("BASKETR"), r) ||
         !GlobalVariableSet(BasketGvName("BASKETT"), (double)primaryTicket))
         Print("[OrderManager] WARNING: could not persist basket 1R for ticket ",
               primaryTicket, " | err=", GetLastError());
     }

   //| Returns the stored 1R, but ONLY when the stored ticket names the  |
   //| position being adopted. 0.0 means "no usable record".            |
   double                  RestoreBasketR(ulong adoptedTicket)
     {
      string name = BasketGvName("BASKETR");
      if(!GlobalVariableCheck(name)) return 0.0;
      string tName = BasketGvName("BASKETT");
      if(!GlobalVariableCheck(tName)) return 0.0;
      ulong storedTicket = (ulong)GlobalVariableGet(tName);
      if(storedTicket != adoptedTicket)
        {
         if(EnableLogging)
            Print("[OrderManager] Stored basket 1R belongs to ticket ", storedTicket,
                  ", not ", adoptedTicket, " -> discarded (stale record)");
         return 0.0;
        }
      double r = GlobalVariableGet(name);
      return (r > 0.0) ? r : 0.0;
     }

   void                    ClearBasketR(void)
     {
      string name  = BasketGvName("BASKETR");
      string tName = BasketGvName("BASKETT");
      if(GlobalVariableCheck(name))  GlobalVariableDel(name);
      if(GlobalVariableCheck(tName)) GlobalVariableDel(tName);
     }


   double                  GetCurrentSpread(void)
     {
      return (GetAsk() - GetBid()) / SymbolInfoDouble(m_symbol, SYMBOL_POINT);
     }

   bool                    IsSpreadAcceptable(void)
     {
      double spread = GetCurrentSpread();
      if(spread > MaxSpreadPoints)
        {
         if(EnableLogging)
            Print("[OrderManager] Spread too wide: ", DoubleToString(spread, 1),
                  " > ", MaxSpreadPoints);
         return false;
        }
      return true;
     }

//+------------------------------------------------------------------+
   //| Dynamic order filling mode — some brokers/prop firms reject      |
   //| ORDER_FILLING_RETURN (TRADE_RETCODE_INVALID_FILL). Read the       |
   //| symbol's SYMBOL_FILLING_MODE bitmask and pick FOK/IOC/RETURN.    |
   //+------------------------------------------------------------------+
   ENUM_ORDER_TYPE_FILLING GetFillingMode(void)
     {
      long filling = SymbolInfoInteger(m_symbol, SYMBOL_FILLING_MODE);
      if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
      if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
      return ORDER_FILLING_RETURN;
     }

   //+------------------------------------------------------------------+
   //| Core OrderSend with throttle + retry                            |
   //+------------------------------------------------------------------+
   bool                    SendOrderWithRetry(MqlTradeRequest &request,
                                              MqlTradeResult  &result)
     {
      // Only throttle NEW pending order placement. Never throttle SL mods,
      // position closes, pending-order deletes or reversal entries - dropping
      // those silently desynced m_sessionSL from the real broker stop.
      if(request.action == TRADE_ACTION_PENDING)
        {
         if(!CheckOrderTimeLock()) return false;
        }

      ZeroMemory(result);
      int attempts = 0;
      bool success = false;
      while(attempts < MaxRetries && !success)
        {
         attempts++;
         request.deviation = MaxSlippage;
         request.magic     = MagicNumber;
         request.comment   = BuildOrderComment(0, 0);   // v5.27: session ID, clamped to 31
         request.type_filling = GetFillingMode();   // dynamic FOK/IOC/RETURN
         ResetLastError();
         if(OrderSend(request, result))
           {
            if(result.retcode == TRADE_RETCODE_DONE ||
               result.retcode == TRADE_RETCODE_DONE_PARTIAL ||
               result.retcode == TRADE_RETCODE_PLACED)
              { success = true; m_ordersPlaced++; break; }
            string retMsg = GetTradeRetcodeString(result.retcode);
            Print("[OrderManager] OrderSend result: ", retMsg,
                  " (code=", result.retcode, ")");
            if(result.retcode == TRADE_RETCODE_REQUOTE ||
               result.retcode == TRADE_RETCODE_PRICE_CHANGED ||
               result.retcode == TRADE_RETCODE_PRICE_OFF)
              {
               // v5.30 — never move a LIMIT onto the wrong side of the
               // market. Re-pricing a BUY_LIMIT onto the live Ask is invalid
               // by definition (a BUY LIMIT must rest BELOW the market), and
               // it was the dominant source of the repeated [Invalid price]
               // journal errors: every retry re-sent the bad price, up to
               // MaxRetries attempts per block. A limit is re-quoted against
               // the side it must legally rest on; only a true market order
               // is re-priced to the executable side.
               if(request.type == ORDER_TYPE_BUY_LIMIT)
                  request.price = SnapToTick(GetBid());
               else if(request.type == ORDER_TYPE_SELL_LIMIT)
                  request.price = SnapToTick(GetAsk());
               else if(request.type == ORDER_TYPE_BUY)
                  request.price = GetAsk();
               else if(request.type == ORDER_TYPE_SELL)
                  request.price = GetBid();
               Sleep(RetryDelayMs); m_retryCount++; continue;
              }
            else if(result.retcode == TRADE_RETCODE_CONNECTION)
              {
               Print("[OrderManager] Connection issue — retrying...");
               Sleep(RetryDelayMs * 2); m_retryCount++; continue;
              }
            else
              {
               Print("[OrderManager] FATAL: Non-retryable error: ", retMsg);
               m_ordersRejected++; return false;
              }
           }
         else
           {
            int error = GetLastError();
            Print("[OrderManager] OrderSend FAILED (attempt ", attempts,
                  "/", MaxRetries, "): error=", error);
            if(error == TRADE_RETCODE_INVALID_STOPS)
              {
               Print("[OrderManager] Invalid stops — retrying once");
               Sleep(RetryDelayMs); m_retryCount++; continue;
              }
            else
              {
               Print("[OrderManager] FATAL: OrderSend error ", error);
               m_ordersRejected++; return false;
              }
           }
        }
      if(!success)
        {
         Print("[OrderManager] OrderSend exhausted all ", MaxRetries, " retries");
         m_ordersRejected++;
        }
      return success;
     }

   string                  GetTradeRetcodeString(uint retcode)
     {
      switch(retcode)
        {
         case TRADE_RETCODE_DONE:              return "DONE";
         case TRADE_RETCODE_DONE_PARTIAL:      return "DONE_PARTIAL";
         case TRADE_RETCODE_PLACED:            return "PLACED";
         case TRADE_RETCODE_REQUOTE:           return "REQUOTE";
         case TRADE_RETCODE_REJECT:            return "REJECT";
         case TRADE_RETCODE_CANCEL:            return "CANCEL";
         case TRADE_RETCODE_PRICE_CHANGED:     return "PRICE_CHANGED";
         case TRADE_RETCODE_PRICE_OFF:         return "PRICE_OFF";
         case TRADE_RETCODE_CONNECTION:        return "CONNECTION";
         case TRADE_RETCODE_INVALID_VOLUME:    return "INVALID_VOLUME";
         case TRADE_RETCODE_INVALID_PRICE:     return "INVALID_PRICE";
         case TRADE_RETCODE_INVALID_STOPS:     return "INVALID_STOPS";
         case TRADE_RETCODE_NO_MONEY:          return "NO_MONEY";
         case TRADE_RETCODE_MARKET_CLOSED:     return "MARKET_CLOSED";
         case TRADE_RETCODE_FROZEN:            return "FROZEN";
         default:                              return "UNKNOWN(" + IntegerToString(retcode) + ")";
        }
     }

   //+------------------------------------------------------------------+
   //| Block direction helpers                                          |
   //+------------------------------------------------------------------+
   ENUM_ORDER_TYPE         GetOrderTypeForBlock(const SSniperBlock &block)
     {
      // EXPERIMENT (experiment/reverse-sr): the order type must FOLLOW the
      // mapped direction, not the zone. Deriving it independently from
      // block.type is precisely what made the reversal illegal with pending
      // orders -- it would emit a BUY_LIMIT while the direction said SHORT.
      // Kept coherent here even when InpVirtualOrders bypasses OrderSend.
      return (GetDirectionForBlock(block) == DIR_LONG) ? ORDER_TYPE_BUY_LIMIT
                                                       : ORDER_TYPE_SELL_LIMIT;
     }
   ENUM_TRADE_DIRECTION    GetDirectionForBlock(const SSniperBlock &block)
     {
      // EXPERIMENT (experiment/reverse-sr): a Support/Resistance zone is
      // DISCOVERED polarity, NOT trade direction. With InpReverseSR the
      // mapping inverts: support -> SHORT (SL above the zone, TP below) and
      // resistance -> LONG (SL below, TP above). This is the single
      // chokepoint every consumer reads (front edge, SL, TP, basket seed,
      // correlation gate), so flipping it here flips them all coherently.
      if(InpReverseSR)
         return (block.type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;
      return (block.type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;
     }


   //+------------------------------------------------------------------+
   //| PINE GATE — can_place: flat OR (long & resistance) OR (short &  |
   //| support). Mirrors strategy.position_size logic.                 |
   //+------------------------------------------------------------------+
   //| v5.27 — ORDER COMMENT BUILDER                                    |
   //|                                                                  |
   //| MT5 hard-caps MqlTradeRequest::comment at 31 characters and      |
   //| truncates silently past that, so EVERY comment this file sends   |
   //| is routed through here and clamped explicitly.                   |
   //|                                                                  |
   //| Identifier precedence:                                           |
   //|   1. the live journal session ID  (#OTTO-<SYM>-<date>-<time>-BLKn)
   //|   2. a locally synthesised form from the block serial             |
   //| A tranche tag is appended for pyramided fills.                    |
   //|                                                                  |
   //| WHY THIS DOESN'T JUST DO StringSubstr(s, 0, 31):                  |
   //| the raw session ID is ~33-36 chars, so a HEAD truncation chops    |
   //| off the "-BLK<n>" tail — the one field that identifies which      |
   //| setup the order belongs to. Instead the date component is        |
   //| dropped first, and only then is a tail-preserving clamp applied.  |
   //| The result keeps the symbol, the time and the block serial,       |
   //| which is what makes a terminal row identifiable at a glance.      |
   //+------------------------------------------------------------------+
   string                  ClampOrderComment(string s)
     {
      const int MAX_COMMENT = 31;   // MT5 hard limit on request.comment
      if(StringLen(s) <= MAX_COMMENT) return s;

      // Pass 1: drop the YYYYMMDD- date component, keeping the tail intact.
      // "#OTTO-EURUSD-20260922-143005-BLK3" -> "#OTTO-EURUSD-143005-BLK3"
      // Anchored on the '#'-form so the OTTO_<sym>_<serial> fallback (which
      // has no date segment) is left for pass 2.
      if(StringGetCharacter(s, 0) == '#')
        {
         int p = StringFind(s, "-");
         if(p > 0)
           {
            int q = StringFind(s, "-", p + 1);
            if(q > 0)
              {
               // Validate that the segment between p and q is a date stamp
               // (starts with a 4-digit year and is >= 8 chars) before daring
               // to remove it, so an unexpected ID layout is never mangled.
               int segLen = q - (p + 1);
               string seg = StringSubstr(s, p + 1, segLen);
               if(segLen >= 8 && StringLen(seg) >= 8 &&
                  (StringGetCharacter(seg, 0) >= '0' && StringGetCharacter(seg, 0) <= '9'))
                 {
                  string trimmed = StringSubstr(s, 0, p + 1) + StringSubstr(s, q + 1);
                  if(StringLen(trimmed) <= MAX_COMMENT) return trimmed;
                  s = trimmed;
                 }
              }
           }
        }

      // Pass 2: still too long (long broker suffix, long symbol, or a date
      // stamp that could not be safely removed). Preserve BOTH ends: the
      // identifier prefix and the -BLK<n> / _T<n> tail, eliding the middle.
      // Layout after this pass is exactly MAX_COMMENT chars:
      //   head(25) + '~'(1) + tail(5) = 31
      if(StringLen(s) > MAX_COMMENT)
        {
         const int TAIL_LEN = 5;                        // e.g. "-BLK3" / "45_T3"
         const int HEAD_LEN = MAX_COMMENT - TAIL_LEN - 1;   // reserve the '~'
         string tail = StringSubstr(s, StringLen(s) - TAIL_LEN);
         string head = StringSubstr(s, 0, HEAD_LEN);
         s = head + "~" + tail;
         // Absolute guarantee: never emit more than the MT5 limit, whatever
         // the arithmetic above produced (e.g. if the constants are retuned).
         if(StringLen(s) > MAX_COMMENT) s = StringSubstr(s, 0, MAX_COMMENT);
        }
      return s;
     }

   string                  BuildOrderComment(const int blockSerial = 0, const int tranche = 0)
     {
      string base = "";
      if(m_journal != NULL && m_journal.GetSessionID() != "")
         base = m_journal.GetSessionID();
      else if(m_sessionID != "")
         base = m_sessionID;          // basket already owns a resolved ID
      else
         base = StringFormat("OTTO_%s_%d", m_symbol, blockSerial);
      if(tranche > 1) base = base + "_T" + IntegerToString(tranche);
      return ClampOrderComment(base);
     }


   //+------------------------------------------------------------------+
   bool                    CanPlaceForDirection(const SSniperBlock &block)
     {
      // EXPERIMENT (experiment/reverse-sr): keyed on the MAPPED direction, not
      // the raw zone. With InpReverseSR a Support block is a SHORT, so a
      // Support block arriving while a SHORT is open is the same-direction
      // case and must be blocked -- the old zone comparison would have let it
      // through as an "opposing" setup.
      ENUM_TRADE_DIRECTION mappedDir = GetDirectionForBlock(block);
      if(!m_hasActiveTrade)
         return true;
      if(m_activeDirection == DIR_LONG && mappedDir == DIR_SHORT)
         return true;
      if(m_activeDirection == DIR_SHORT && mappedDir == DIR_LONG)
         return true;
      return false;   // same-direction order while a position is open -> blocked
     }

   //+------------------------------------------------------------------+
   //| Sentiment simulation gate (Pine pass_sent)                      |
   //+------------------------------------------------------------------+
   bool                    SentimentPasses(const SSniperBlock &block)
     {
      if(InpSimSentiment == SENT_IGNORE)
         return true;
      // EXPERIMENT (experiment/reverse-sr): sentiment is a statement about
      // DIRECTION, so it must be tested against the MAPPED direction. Keyed on
      // the raw zone (as it was) an inverted Support block -- now a SHORT --
      // would pass a BULLISH filter and be refused by a BEARISH one, which is
      // exactly backwards. With InpReverseSR = false GetDirectionForBlock()
      // is the identity mapping from block.type, so both expressions below
      // reduce to the original comparisons and main behaviour is unchanged.
      ENUM_TRADE_DIRECTION dir = GetDirectionForBlock(block);
      if(InpSimSentiment == SENT_BULLISH && dir == DIR_LONG)  return true;
      if(InpSimSentiment == SENT_BEARISH && dir == DIR_SHORT) return true;
      return false;
     }

   //+------------------------------------------------------------------+
   //| Entry price per Pine entry_style (Midpoint / Front Edge)        |
   //+------------------------------------------------------------------+
   double                  CalcEntryPrice(const SSniperBlock &block)
     {
      if(InpEntryStyle == ENTRY_MIDPOINT)
         return block.midpoint;
      // EXPERIMENT (experiment/reverse-sr): the front edge is the edge the
      // price APPROACHES FROM. With the default mapping that is the near edge
      // (support.top / resistance.bottom); inverted, the setup is entered from
      // the opposite side, so the front edge flips with the direction.
      return (GetDirectionForBlock(block) == DIR_LONG) ? block.top : block.bottom;
     }

   //+------------------------------------------------------------------+
   //| SL distance: b_height + 0.5*ATR (Pine sl_dist)                  |
   //+------------------------------------------------------------------+
   double                  CalcSLDistance(const SSniperBlock &block, double atr)
     {
      return block.blockHeight + (0.5 * atr);
     }

   //+------------------------------------------------------------------+
   //| ComputeSetupGeometry — ONE source of truth for a setup's prices. |
   //|                                                                  |
   //| EXPERIMENT (experiment/reverse-sr) EXTRACTION, not a rewrite.    |
   //| The body below is the EXACT arithmetic that used to live inline  |
   //| in PlaceLimitOrder (entry snap, MAPPED isLong, stopLoss, the     |
   //| InpMaxRR takeProfit projection, rrUnit and the b.local_* store). |
   //| It was lifted out for ONE reason: ArmVirtualEntry() must store   |
   //| byte-identical geometry to what the pending path would have      |
   //| placed, and two hand-copied formulas would drift the moment      |
   //| either side was touched -- silently re-pricing the experiment's   |
   //| triggers against the very Setup/SL the rest of the EA believes.  |
   //|                                                                  |
   //| Writes back through block.localEntry/localSL/localTP/rrUnit so    |
   //| every existing consumer (Front-Run Veto reads localTP; the        |
   //| reversal path reads localSL; SeedActiveTradeFromBlock reads       |
   //| localEntry/localSL/rrUnit) keeps working untouched. The caller    |
   //| is responsible for persisting `block` (SetBlockAt).              |
   //|                                                                  |
   //| InpReverseSR=false reduces every expression to the main-branch    |
   //| original, so this refactor is behaviour-preserving on main.       |
   //+------------------------------------------------------------------+
   void                    ComputeSetupGeometry(SSniperBlock &block, double atr,
                                               double &entryOut, bool &isLongOut,
                                               double &slOut, double &tpOut,
                                               double &rrOut)
     {
      // v5.30 — snap onto the symbol's trade tick grid. A price that is
      // off-grid is refused with TRADE_RETCODE_INVALID_PRICE even when it
      // sits on the correct side of the market. slDist/rrUnit are
      // entry-independent, so snapping does not change the risk distance.
      double entryPrice = SnapToTick(CalcEntryPrice(block));

      double slDist     = CalcSLDistance(block, atr);
      // EXPERIMENT (experiment/reverse-sr): SL/TP/isLong all key off the
      // MAPPED direction so an inverted setup stops and targets on the
      // correct side. With InpReverseSR=false and a Support block the
      // expressions reduce exactly to the original (entry - slDist /
      // entry + tpRR*slDist), so main-branch behaviour is bit-identical.
      bool   isLong     = (GetDirectionForBlock(block) == DIR_LONG);
      double stopLoss   = isLong ? entryPrice - slDist : entryPrice + slDist;
      // v5.31: the take-profit projection is InpMaxRR (default 4.0R).
      // This must stay the SAME input the Front-Run veto projects from:
      // once an order is resting the veto compares against block.localTP
      // rather than re-deriving the target, so a divergence between this
      // line and the veto's projection would silently disable the veto.
      double tpRR       = (InpMaxRR > 0.0) ? InpMaxRR : 1.0;
      double takeProfit = isLong ? entryPrice + tpRR * slDist
                                 : entryPrice - tpRR * slDist;

      // Store local entry/SL/TP/rrUnit on the block (Pine b.local_*)
      block.localEntry = entryPrice;
      block.localSL    = stopLoss;
      block.localTP    = takeProfit;
      block.rrUnit     = slDist;

      entryOut  = entryPrice;
      isLongOut = isLong;
      slOut     = stopLoss;
      tpOut     = takeProfit;
      rrOut     = slDist;
     }

   //+==================================================================+
   //| EXPERIMENT (experiment/reverse-sr) — VIRTUAL ENTRY ENGINE        |
   //|                                                                  |
   //| WHY THIS EXISTS: InpReverseSR moves a setup's entry to the       |
   //| opposite side of the market from where a resting limit may       |
   //| legally sit. A reversed SUPPORT is a SHORT whose entry lies      |
   //| BELOW the live price, so the SELL_LIMIT the Pine port would send |
   //| is refused by the server with TRADE_RETCODE_INVALID_PRICE -- and |
   //| symmetrically for a reversed RESISTANCE BUY. There is no legal   |
   //| pending equivalent, so the geometry is held HERE and fired as a  |
   //| MARKET order the moment price reaches the stored entry.          |
   //|                                                                  |
   //| Every field stored is what the pending path would have placed:   |
   //| the prices come from ComputeSetupGeometry() (the SAME routine    |
   //| PlaceLimitOrder uses), the lot from the same RiskManager call,   |
   //| and the block is left in exactly the has_placed_order state the  |
   //| pending path sets -- so the Front-Run Veto, the arm/re-arm logic |
   //| and the block reaper keep seeing a "placed" setup.               |
   //|                                                                  |
   //| Each array is index-paired by position and ALWAYS resized in     |
   //| lockstep by PushVirtual/RemoveVirtualAt, so no index can drift   |
   //| out of range. ArrayRemove is compiled because this is an .mqh     |
   //| included by otto.mq5 (only .mq5 entry points are scanned for the |
   //| MQL5 restriction).                                               |
   //+==================================================================+

   //+------------------------------------------------------------------+
   //| PushVirtual — append one armed setup. Returns its store index.   |
   //+------------------------------------------------------------------+
   int                     PushVirtual(int blockSerial, int blockIndex,
                                       ENUM_TRADE_DIRECTION dir, ENUM_ORDER_TYPE otype,
                                       double entry, double sl, double tp,
                                       double rrUnit, double lot)
     {
      int at = m_virtualCount;
      ArrayResize(m_virtualBlockSerial, at + 1, 8);
      ArrayResize(m_virtualBlockIndex,  at + 1, 8);
      ArrayResize(m_virtualDirection,  at + 1, 8);
      ArrayResize(m_virtualOrderType,  at + 1, 8);
      ArrayResize(m_virtualEntry,      at + 1, 8);
      ArrayResize(m_virtualSL,         at + 1, 8);
      ArrayResize(m_virtualTP,         at + 1, 8);
      ArrayResize(m_virtualRRUnit,     at + 1, 8);
      ArrayResize(m_virtualLot,        at + 1, 8);

      m_virtualBlockSerial[at] = blockSerial;
      m_virtualBlockIndex[at]  = blockIndex;
      m_virtualDirection[at]   = dir;
      m_virtualOrderType[at]   = otype;
      m_virtualEntry[at]       = entry;
      m_virtualSL[at]          = sl;
      m_virtualTP[at]          = tp;
      m_virtualRRUnit[at]      = rrUnit;
      m_virtualLot[at]         = lot;
      m_virtualCount           = at + 1;
      return at;
     }

   //+------------------------------------------------------------------+
   //| FindVirtualBySerial — index of a stored setup by block serial.   |
   //| Keyed on the SERIAL, never the array index: block indices shift  |
   //| when the reaper compacts m_blocks[], the serial does not.        |
   //+------------------------------------------------------------------+
   int                     FindVirtualBySerial(int blockSerial)
     {
      for(int v = 0; v < m_virtualCount; v++)
         if(m_virtualBlockSerial[v] == blockSerial)
            return v;
      return -1;
     }

   //+------------------------------------------------------------------+
   //| RemoveVirtualAt — delete one entry, keeping every array paired.  |
   //+------------------------------------------------------------------+
   void                    RemoveVirtualAt(int v)
     {
      if(v < 0 || v >= m_virtualCount) return;
      ArrayRemove(m_virtualBlockSerial, v, 1);
      ArrayRemove(m_virtualBlockIndex,  v, 1);
      ArrayRemove(m_virtualDirection,   v, 1);
      ArrayRemove(m_virtualOrderType,   v, 1);
      ArrayRemove(m_virtualEntry,       v, 1);
      ArrayRemove(m_virtualSL,          v, 1);
      ArrayRemove(m_virtualTP,          v, 1);
      ArrayRemove(m_virtualRRUnit,      v, 1);
      ArrayRemove(m_virtualLot,         v, 1);
      m_virtualCount = ArraySize(m_virtualBlockSerial);
     }

   //+------------------------------------------------------------------+
   //| ArmVirtualEntry — the InpVirtualOrders replacement for           |
   //| PlaceLimitOrder. Applies the SAME Pine gates in the SAME order,  |
   //| stores the identical geometry, and fires nothing until price     |
   //| reaches the entry. On success the block is left hasPlacedOrder   |
   //| = true / limitOrderTicket = 0, which is precisely how the        |
   //| pending path looks to every downstream consumer once its order   |
   //| has been ACCEPTED but not yet filled.                            |
   //|                                                                  |
   //| Return value mirrors PlaceLimitOrder: true = this block is now   |
   //| committed (armed here or already live); false = refused this     |
   //| tick, leaving the block free to be re-evaluated next tick.       |
   //+------------------------------------------------------------------+
   bool                    ArmVirtualEntry(int blockIndex, SSniperBlock &block)
     {
      // Same head guard as the pending path, minus the ticket branch: a
      // virtual setup never owns a broker ticket, so isTriggered is the
      // only "already done" flag that can be set here.
      if(block.isVetoed || block.isTriggered) return false;

      // Idempotence. hasPlacedOrder is set the moment a setup is armed and
      // is the ONLY duplicate gate the virtual path needs, because there is
      // no broker book to race against -- but here it CANNOT be trusted on
      // its own: the adopted-trade teardown and the block reaper both clear
      // hasPlacedOrder while the setup is still stored, and a re-arm would
      // then stack a second live trigger on one serial. The serial lookup is
      // therefore the authoritative duplicate check.
      if(FindVirtualBySerial(block.serial) >= 0)
        {
         block.hasPlacedOrder = true;
         if(m_blockManager != NULL) m_blockManager.SetBlockAt(blockIndex, block);
         return true;
        }

      if(!IsSpreadAcceptable()) return false;

      // Pine can_place + correlation veto (institutional) — identical gates.
      if(!CanPlaceForDirection(block)) return false;
      ENUM_TRADE_DIRECTION dir = GetDirectionForBlock(block);
      if(m_correlationFilter != NULL && m_correlationFilter.IsTradeVetoed(dir))
        {
         if(EnableLogging)
            Print("[Correlation] VETO on ", m_symbol,
                  (dir == DIR_LONG ? " LONG" : " SHORT"), " (virtual)");
         return false;
        }
      if(m_correlationFilter != NULL &&
         m_correlationFilter.IsConsensusOpposed(m_symbol, dir))
        {
         block.isVetoed   = true;
         block.vetoReason = VETO_CORRELATION;
         if(m_blockManager != NULL) m_blockManager.SetBlockAt(blockIndex, block);
         if(EnableLogging)
            Print("[Correlation] VECTOR VETO on ", m_symbol,
                  (dir == DIR_LONG ? " LONG" : " SHORT"), " (virtual)");
         return false;
        }
      if(!SentimentPasses(block)) return false;

      double atr = (m_blockManager != NULL) ? m_blockManager.GetATR() : 0.0;
      if(atr <= 0) return false;

      // ONE source of truth: identical entry/SL/TP/rrUnit to the pending path.
      double entryPrice, stopLoss, takeProfit, rrUnit;
      bool   isLong;
      ComputeSetupGeometry(block, atr, entryPrice, isLong, stopLoss, takeProfit, rrUnit);

      // Broker-level stop validation, exactly as PlaceLimitOrder does it.
      double adjustedSL = stopLoss;
      if(!ValidateStopDistance(entryPrice, adjustedSL, isLong))
         adjustedSL = AdjustSLToMinimum(entryPrice, adjustedSL, isLong);

      double lotSize = m_riskManager.CalculateLotSize(entryPrice, adjustedSL);
      if(lotSize <= 0)
        {
         if(EnableLogging)
            Print("[OrderManager] SAFETY ABORT (virtual): lot size zero — suppressed");
         return false;
        }
      if(!m_riskManager.HasSufficientMargin(lotSize)) return false;
      if(lotSize < m_riskManager.GetVolumeMin() || lotSize > m_riskManager.GetVolumeMax())
        {
         Print("[OrderManager] Invalid volume (virtual)");
         return false;
        }

      // Direction must match the geometry: isLong came from the same mapped
      // direction, so this can only fail if that mapping ever splits.
      ENUM_ORDER_TYPE otype = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

      PushVirtual(block.serial, blockIndex, dir, otype,
                  entryPrice, adjustedSL, takeProfit, rrUnit, lotSize);

      // ARM == PLACEMENT, for accounting purposes. In the pending path
      // SendOrderWithRetry() books m_ordersPlaced the moment the broker
      // accepts the order; a virtual setup is committed here and there is no
      // send, so the counter is incremented by hand. Without this the
      // "ORDER PLACED" journal line and the OnDeinit/status totals would
      // silently under-report every setup this experiment arms.
      m_ordersPlaced++;

      // Commit the block exactly as a "placed, not yet filled" order would:
      // hasPlacedOrder = true keeps every re-arm path from re-entering here,
      // and the local_* prices are stored so the Front-Run Veto, the
      // reversal path and SeedActiveTradeFromBlock all read real values.
      block.hasPlacedOrder     = true;
      block.localEntry         = entryPrice;
      block.localSL            = adjustedSL;
      block.localTP            = takeProfit;
      block.rrUnit             = rrUnit;
      block.limitOrderTicket   = 0;     // virtual: no broker ticket by design
      block.pendingOrderCancel = false;
      block.priceAbortLogged   = false;
      if(m_blockManager != NULL) m_blockManager.SetBlockAt(blockIndex, block);

      // Build the session ID at ARM time so the journal file exists before the
      // fill, mirroring the pending path's place-time ID (ONE file per setup).
      // Ticket 0 is passed deliberately: there is no pending ticket to name.
      MqlDateTime vtm; TimeToStruct(TimeCurrent(), vtm);
      string vts = StringFormat("%04d%02d%02d-%02d%02d%02d",
                                vtm.year, vtm.mon, vtm.day, vtm.hour, vtm.min, vtm.sec);
      if(m_journal != NULL)
        {
         m_journal.SetSessionID(StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, vts, block.serial));
         m_journal.LogOrderPlaced(0, dir, block.type, entryPrice, adjustedSL, lotSize, block);
        }

      if(EnableLogging)
         Print("[OrderManager] VIRTUAL ENTRY ARMED: serial=", block.serial,
               " ", block.tradeId,
               " dir=", (dir == DIR_LONG ? "LONG" : "SHORT"),
               " type=", (otype == ORDER_TYPE_BUY ? "BUY" : "SELL"),
               " entry=", DoubleToString(entryPrice, _Digits),
               " sl=", DoubleToString(adjustedSL, _Digits),
               " tp=", DoubleToString(takeProfit, _Digits),
               " rrUnit=", DoubleToString(rrUnit, _Digits),
               " lot=", DoubleToString(lotSize, 2));
      return true;
     }

   //+------------------------------------------------------------------+
   //| VirtualEntryReached — has price touched a stored entry yet?      |
   //|                                                                  |
   //| Direction-correct by construction: a BUY setup triggers when the |
   //| BID falls to the entry, a SELL when the ASK rises to it. This is |
   //| exactly why the experiment cannot use the pending path -- the    |
   //| trigger side is the side the inverted entry now rests on.        |
   //+------------------------------------------------------------------+
   bool                    VirtualEntryReached(int v, double bid, double ask)
     {
      if(v < 0 || v >= m_virtualCount) return false;
      if(m_virtualOrderType[v] == ORDER_TYPE_BUY)  return (bid <= m_virtualEntry[v]);
      if(m_virtualOrderType[v] == ORDER_TYPE_SELL) return (ask >= m_virtualEntry[v]);
      return false;
     }

   //+------------------------------------------------------------------+
   //| GetBlockSnapshotBySerial — block copy keyed on the SERIAL.       |
   //| The stored block INDEX is diagnostics only: the reaper compacts  |
   //| m_blocks[] behind our back, the serial never moves.              |
   //+------------------------------------------------------------------+
   bool                    GetBlockSnapshotBySerial(int serial, SSniperBlock &out)
     {
      if(m_blockManager == NULL) return false;
      SSniperBlock all[];
      int n = m_blockManager.GetAllBlocks(all);
      for(int k = 0; k < n; k++)
         if(all[k].serial == serial) { out = all[k]; return true; }
      return false;
     }

   //+------------------------------------------------------------------+
   //| MarkBlockTriggeredBySerial — the post-fill bookkeeping the       |
   //| pending handler performs on its block, keyed by serial.          |
   //+------------------------------------------------------------------+
   void                    MarkBlockTriggeredBySerial(int serial)
     {
      if(m_blockManager == NULL) return;
      SSniperBlock all[];
      int n = m_blockManager.GetAllBlocks(all);
      for(int k = 0; k < n; k++)
        {
         if(all[k].serial != serial) continue;
         SSniperBlock mod = all[k];
         mod.isTriggered      = true;
         mod.limitOrderTicket = 0;
         mod.hasPlacedOrder   = true;
         mod.deleteOnBarTime  = iTime(m_symbol, PERIOD_CURRENT, 0);
         m_blockManager.SetBlockAt(k, mod);
         return;
        }
     }

   //+------------------------------------------------------------------+
   //| FireVirtualMarketOrder — execute one stored setup at market.     |
   //|                                                                  |
   //| The SL comes from the STORED geometry (re-validated against the  |
   //| live fill side), a TP is attached at the same InpMaxRR           |
   //| projection, and the block is flagged triggered exactly as a      |
   //| pending fill would leave it -- so the reaper deletes it next bar |
   //| and the funnel cannot re-arm it.                                 |
   //|                                                                  |
   //| SendOrderWithRetry() re-prices market orders onto the executable |
   //| side on a requote (BUY -> Ask, SELL -> Bid) and does NOT apply   |
   //| the 3-second pending throttle to TRADE_ACTION_DEAL, so a fast    |
   //| reversal is not artificially delayed.                            |
   //+------------------------------------------------------------------+
   bool                    FireVirtualMarketOrder(int v)
     {
      if(v < 0 || v >= m_virtualCount) return false;

      // ONE BASKET AT A TIME. The arm path refuses NEW setups while a basket
      // is live, so a stored trigger surviving into a live book was armed
      // BEFORE that basket opened. It must not fire now: the reversal
      // machinery is built around DETECTING a broker fill
      // (CheckPendingOrderFills), and a market order opened here would have
      // no fill for it to detect, leaving an unmanaged leg. The setup stays
      // armed -- PurgeVirtualOrders() has already dropped the same-direction
      // ones -- and is re-evaluated once the book is flat again.
      if(m_hasActiveTrade) return false;

      int    serial = m_virtualBlockSerial[v];
      bool   isLong = (m_virtualOrderType[v] == ORDER_TYPE_BUY);
      double sl     = m_virtualSL[v];
      double tp     = m_virtualTP[v];
      double lot    = m_virtualLot[v];

      // Re-validate the stop against the ACTUAL fill side. A broker that has
      // widened its stops level since arming is still honoured here.
      double livePx = isLong ? GetAsk() : GetBid();
      if(!ValidateStopDistance(livePx, sl, isLong))
         sl = AdjustSLToMinimum(livePx, sl, isLong);

      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);

      MqlTradeRequest request;
      MqlTradeResult  result;
      ZeroMemory(request);
      ZeroMemory(result);
      request.action    = TRADE_ACTION_DEAL;
      request.symbol    = m_symbol;
      request.type      = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      request.volume    = lot;
      request.price     = NormalizeDouble(livePx, digits);
      request.sl        = NormalizeDouble(sl, digits);
      request.tp        = (tp > 0.0) ? NormalizeDouble(tp, digits) : 0.0;
      request.deviation = MaxSlippage;
      request.magic     = MagicNumber;
      request.comment   = BuildOrderComment(serial, 0);

      if(lot < m_riskManager.GetVolumeMin() || lot > m_riskManager.GetVolumeMax())
        {
         Print("[OrderManager] VIRTUAL FIRE ABORT: volume ", DoubleToString(lot, 2),
               " outside broker limits — setup dropped");
         RemoveVirtualAt(v);
         return false;
        }

      if(!SendOrderWithRetry(request, result))
        {
         Print("[OrderManager] VIRTUAL FIRE FAILED: serial=", serial,
               " retcode=", GetTradeRetcodeString(result.retcode));
         // Transient requote/connection errors keep the trigger for the next
         // tick; anything else must not leave a trigger that re-fires forever.
         if(result.retcode != TRADE_RETCODE_REQUOTE &&
            result.retcode != TRADE_RETCODE_PRICE_CHANGED &&
            result.retcode != TRADE_RETCODE_PRICE_OFF &&
            result.retcode != TRADE_RETCODE_CONNECTION)
            RemoveVirtualAt(v);
         return false;
        }

      // Fire confirmed. Resolve the resulting position ticket via the same
      // 3-tier resolver the pending path uses; fall back to the order ticket.
      ulong ticket = 0;
      ENUM_TRADE_DIRECTION fillDir = DIR_NONE;
      if(!ResolveFilledPositionTicket(result.order,
                                      m_hasActiveTrade ? m_activeTrade.ticket : 0,
                                      ticket, fillDir) || ticket <= 0)
         ticket = (result.order > 0) ? (ulong)result.order : 0;

      // Adopt the fill ONLY when flat. When a basket is already running the
      // arm path refused new setups, so reaching here with an active trade
      // means the fill is a stray: it is not adopted, and the block is still
      // marked triggered so the funnel does not re-arm it.
      if(!m_hasActiveTrade && ticket > 0)
        {
         SSniperBlock blk;
         if(GetBlockSnapshotBySerial(serial, blk))
           {
            SeedActiveTradeFromBlock(blk, ticket);
            m_ordersFilled++;
           }
        }

      MarkBlockTriggeredBySerial(serial);
      RemoveVirtualAt(v);

      if(EnableLogging)
         Print("[OrderManager] VIRTUAL MARKET FILLED: serial=", serial,
               " ticket=", ticket,
               " dir=", (isLong ? "LONG" : "SHORT"),
               " px=", DoubleToString(livePx, _Digits));
      return true;
     }

   //+------------------------------------------------------------------+
   //| DetectVirtualFires — per-tick scan: fire any reached setup.      |
   //|                                                                  |
   //| Ordering rationale:                                              |
   //|   * the CANCEL sweep runs FIRST (MarkVirtualOrdersToMarket) so a |
   //|     setup that price moved away from, or that the operator       |
   //|     disarmed, cannot fire after it has already been abandoned;   |
   //|   * the throttle is checked BEFORE the scan, so a throttled tick |
   //|     does no work at all and the fire rate is hard-bounded;       |
   //|   * only ONE setup fires per tick -- a second market order in    |
   //|     the same tick would be refused by the can_place gate anyway, |
   //|     and pushing it would burn a retry. The array also shifts     |
   //|     when the fired entry is removed, so the scan restarts next   |
   //|     tick.                                                        |
   //+------------------------------------------------------------------+
   void                    DetectVirtualFires(void)
     {
      if(m_virtualCount <= 0) return;

      // Global throttle: at most one fire per InpVirtualTriggerThrottleMs.
      // m_lastVirtualTriggerMs == 0 means "nothing fired yet in this run", so
      // the first trigger is never delayed.
      if(m_lastVirtualTriggerMs != 0 &&
         (GetTickCount() - m_lastVirtualTriggerMs) < (ulong)InpVirtualTriggerThrottleMs)
         return;

      double bid = GetBid();
      double ask = GetAsk();
      for(int v = 0; v < m_virtualCount; v++)
        {
         if(!VirtualEntryReached(v, bid, ask)) continue;
         if(FireVirtualMarketOrder(v))
           {
            m_lastVirtualTriggerMs = GetTickCount();
            return;   // RemoveVirtualAt() shifted the array; restart next tick
           }
         // Reached but refused. Either the book went live (kept armed for
         // later) or the fire failed transiently; scanning on would hit every
         // other reached setup in the same tick, so stop and retry next tick.
         return;
        }
     }


   //+------------------------------------------------------------------+
   //| CancelInvalidVirtualEntries — drop triggers whose block is gone. |
   //|                                                                  |
   //| A virtual setup has no broker artifact keeping it honest: if the |
   //| owning block is vetoed, flipped, reclaimed or deleted, the       |
   //| trigger must die with it. This mirrors the pending path's        |
   //| CancelOrdersForInvalidBlocks(), which for a    virtual setup      |
   //| has nothing in OrdersTotal() to cancel.                          |
   //+------------------------------------------------------------------+
   void                    CancelInvalidVirtualEntries(void)
     {
      for(int v = m_virtualCount - 1; v >= 0; v--)
        {
         int serial = m_virtualBlockSerial[v];
         SSniperBlock blk;
         bool alive = GetBlockSnapshotBySerial(serial, blk);
         if(!alive)
           {
            if(EnableLogging)
               Print("[OrderManager] VIRTUAL TRIGGER DROPPED: block serial ",
                     serial, " no longer exists");
            RemoveVirtualAt(v);
            continue;
           }
         bool mustDrop = blk.isVetoed || blk.isTriggered ||
                         blk.deleteOnBarTime > 0 || blk.pendingOrderCancel;
         if(mustDrop)
           {
            if(EnableLogging)
               Print("[OrderManager] VIRTUAL TRIGGER DROPPED: serial=", serial,
                     " vetoed=", (blk.isVetoed ? "true" : "false"),
                     " triggered=", (blk.isTriggered ? "true" : "false"),
                     " deleteOnBar=", (blk.deleteOnBarTime > 0 ? "true" : "false"),
                     " cancelReq=", (blk.pendingOrderCancel ? "true" : "false"));
            RemoveVirtualAt(v);
           }
        }
     }

   //+------------------------------------------------------------------+
   //| MarkVirtualOrdersToMarket — the InpVirtualOrders entry point.    |
   //| Called from Update() every tick: drop dead triggers, then fire    |
   //| whatever price has reached.                                      |
   //+------------------------------------------------------------------+
   void                    MarkVirtualOrdersToMarket(void)
     {
      if(!InpVirtualOrders) return;
      CancelInvalidVirtualEntries();
      DetectVirtualFires();
     }

   //+------------------------------------------------------------------+
   //| PurgeVirtualOrders — the whole-store twin of the three broker    |
   //| sweeps otto.mq5 already calls every tick / new bar:              |
   //| CancelOrdersForInvalidBlocks(), CancelOpposingConsensusOrders()   |
   //| and ManageDirectionConflict().                                   |
   //|                                                                  |
   //| A virtual setup has no ticket, so those sweeps can never see it: |
   //| under InpVirtualOrders this EA's OrdersTotal() is empty. Every    |
   //| reason those three use to kill a pending order must therefore be  |
   //| applied HERE, to the store, or a dead setup stays armed forever.  |
   //+------------------------------------------------------------------+
   void                    PurgeVirtualOrders(void)
     {
      if(!InpVirtualOrders || m_virtualCount <= 0) return;

      // (1) Block-integrity sweep: vetoed / triggered / reaped / cancel-flagged.
      CancelInvalidVirtualEntries();

      // (2) Consensus-opposed sweep — CancelOpposingConsensusOrders() twin.
      if(InpCancelOpposingPendings && m_correlationFilter != NULL)
         for(int v = m_virtualCount - 1; v >= 0; v--)
            if(m_correlationFilter.IsConsensusOpposed(m_symbol, m_virtualDirection[v]))
              {
               if(EnableLogging)
                  Print("[OrderManager] VIRTUAL VECTOR CANCEL: serial ",
                        m_virtualBlockSerial[v], " ",
                        (m_virtualDirection[v] == DIR_LONG ? "LONG" : "SHORT"));
               RemoveVirtualAt(v);
              }

      // (3) Post-fill same-direction sweep — ManageDirectionConflict() twin.
      //     Pine cancels a resting entry once a trade in that direction is on.
      if(m_hasActiveTrade)
         for(int v = m_virtualCount - 1; v >= 0; v--)
            if(m_virtualDirection[v] == m_activeDirection)
              {
               if(EnableLogging)
                  Print("[OrderManager] VIRTUAL DIRECTION CANCEL: serial ",
                        m_virtualBlockSerial[v]);
               RemoveVirtualAt(v);
              }
     }

   //+------------------------------------------------------------------+
   //| ResetVirtualStore — the InpVirtualOrders twin of                 |
   //| CancelAllPendingOrders(). Called from a DD halt, from OnDeinit()  |
   //| (via the public ClearVirtualStore() wrapper) and from             |
   //| Initialize(): the pending path deletes every resting order, so    |
   //| the store must be emptied too, or a halted EA would still fire a  |
   //| market entry from a stale trigger.                                |
   //+------------------------------------------------------------------+
   void                    ResetVirtualStore(void)
     {
      if(m_virtualCount <= 0) return;
      if(EnableLogging)
         Print("[OrderManager] VIRTUAL STORE CLEARED (", m_virtualCount, " setup(s))");
      ArrayFree(m_virtualBlockSerial);
      ArrayFree(m_virtualBlockIndex);
      ArrayFree(m_virtualDirection);
      ArrayFree(m_virtualOrderType);
      ArrayFree(m_virtualEntry);
      ArrayFree(m_virtualSL);
      ArrayFree(m_virtualTP);
      ArrayFree(m_virtualRRUnit);
      ArrayFree(m_virtualLot);
      m_virtualCount = 0;
     }





   //+------------------------------------------------------------------+
   //| PlaceLimitOrder — translates Pine strategy.entry(limit=...)     |
   //| into an MT5 pending SELL_LIMIT/BUY_LIMIT. NO take-profit is     |
   //| attached (exact mirror: exits are purely trail-based; localTP   |
   //| is stored on the block only for the Front-Run veto).            |
   //|                                                                  |
   //| EXPERIMENT (experiment/reverse-sr): under InpVirtualOrders this  |
   //| method returns immediately. ArmVirtualEntry() replaces it.       |
   //| is stored on the block only for the Front-Run veto).            |
   //+------------------------------------------------------------------+
   bool                    PlaceLimitOrder(int blockIndex, SSniperBlock &block)
     {
      if(block.isVetoed || block.isTriggered || block.hasPlacedOrder)
         return false;

      // EXPERIMENT (experiment/reverse-sr): when virtual orders are enabled the
      // broker pending path is retired for this run. ArmVirtualEntry() stores
      // the identical geometry in memory and MarkVirtualOrdersToMarket() fires
      // a market order only once price reaches the stored entry. Guarded here
      // rather than at the call site so every caller (PlaceOrdersForArmedBlocks
      // and the manual test hook) is covered by a single edit.
      if(InpVirtualOrders) return false;

      // Duplicate prevention: triple-ticket verification
      if(IsBlockOrderAlive(block.limitOrderTicket))
        {
         block.priceAbortLogged = false;   // v5.30: new episode may log again
         return true;
        }
      block.limitOrderTicket = 0;
      block.pendingOrderCancel = false;

      if(!IsSpreadAcceptable()) return false;

      // Pine can_place + correlation veto (institutional)
      if(!CanPlaceForDirection(block)) return false;
      ENUM_TRADE_DIRECTION dir = GetDirectionForBlock(block);
      if(m_correlationFilter != NULL && m_correlationFilter.IsTradeVetoed(dir))
        {
         if(EnableLogging)
            Print("[Correlation] VETO on ", m_symbol,
                  (dir == DIR_LONG ? " LONG" : " SHORT"));
         return false;
        }

      // v5.26 — ADDITIVE portfolio-consensus layer. The ladder above is a
      // per-chart geographic check; this one reads the whole book's currency
      // vectors. Consensus is refreshed by the EA once per cycle, so this is
      // a pure array read here — no per-block portfolio re-scan.
      if(m_correlationFilter != NULL &&
         m_correlationFilter.IsConsensusOpposed(m_symbol, dir))
        {
         block.isVetoed   = true;
         block.vetoReason = VETO_CORRELATION;
         m_blockManager.SetBlockAt(blockIndex, block);
         if(EnableLogging)
            Print("[Correlation] VECTOR VETO on ", m_symbol,
                  (dir == DIR_LONG ? " LONG" : " SHORT"),
                  " | consensus = ",
                  DoubleToString(m_correlationFilter.GetPairConsensus(m_symbol), 1), "%");
         return false;
        }
      if(!SentimentPasses(block)) return false;

      // --- Pine price computation ---
      double atr = m_blockManager.GetATR();
      if(atr <= 0) return false;
      // v5.30 — snap onto the symbol's trade tick grid BEFORE any validation.
      // A price that is off-grid is refused with TRADE_RETCODE_INVALID_PRICE
      // even when it sits on the correct side of the market. slDist/rrUnit are
      // entry-independent, so snapping does not change the risk distance.
      double entryPrice = SnapToTick(CalcEntryPrice(block));

      // v5.28 — LIVE PRICE VALIDATION, before the duplicate shield so a refused
      // price never sets hasPlacedOrder (the block stays armed for a later tick).
      // A limit resting on the wrong side of the market is rejected by the
      // server with TRADE_RETCODE_INVALID_PRICE, which previously burned a
      // dispatch attempt and left the block in an ambiguous state. The broker's
      // SYMBOL_TRADE_STOPS_LEVEL is honoured so the check matches server rules.
      double priceBuffer = GetPriceBoundaryBuffer();
      double liveAsk = GetAsk();
      double liveBid = GetBid();

      // Quoted once and reused in the Print below: the literals are the
      // contract the v5.28/v5.30 probes assert on, so they must stay in sync
      // with the comparisons in one place only.
      string invBuy  = "entryPrice >= (liveBid  - priceBuffer)";
      string invSell = "entryPrice <= (liveAsk + priceBuffer)";

      if(GetDirectionForBlock(block) == DIR_LONG)   // long -> BUY LIMIT below market
        {
         if(entryPrice >= (liveBid - priceBuffer))
           {
            // One-shot: OnTick re-enters every tick while the block stays
            // armed, so without this gate a single penetration floods the
            // journal with one ABORT line per tick.
            if(EnableLogging && !block.priceAbortLogged)
               Print("[OrderManager] ABORT BUY_LIMIT: entry ",
                     DoubleToString(entryPrice, _Digits), " >= bid ",
                     DoubleToString(liveBid, _Digits),
                     " (Invalid Price) — block left armed for retry [",
                     invBuy, "]");
            block.priceAbortLogged = true;
            m_blockManager.SetBlockAt(blockIndex, block);
            return false;
           }
         block.priceAbortLogged = false;   // clear of the boundary: log again later
        }
      else                              // short -> SELL LIMIT above market
        {
         if(entryPrice <= (liveAsk + priceBuffer))
           {
            if(EnableLogging && !block.priceAbortLogged)
               Print("[OrderManager] ABORT SELL_LIMIT: entry ",
                     DoubleToString(entryPrice, _Digits), " <= ask ",
                     DoubleToString(liveAsk, _Digits),
                     " (Invalid Price) — block left armed for retry [",
                     invSell, "]");
            block.priceAbortLogged = true;
            m_blockManager.SetBlockAt(blockIndex, block);
            return false;
           }
         block.priceAbortLogged = false;
        }

      // HARD ANTI-DUPLICATE CHECK against MT5's live pending-order book.
      // If an order of ours already rests at (near) this price, do NOT send
      // another — closes the OnTick race and any bookkeeping write-back lag.
      if(IsOrderAlreadyLiveAtPrice(entryPrice, 5.0))
        {
         block.hasPlacedOrder = true;
         m_blockManager.SetBlockAt(blockIndex, block);
         if(EnableLogging)
            Print("[OrderManager] DUPLICATE SHIELD: Order already live near ",
                  DoubleToString(entryPrice, _Digits), " — skipping OrderSend.");
         return false;
        }

      double slDist     = CalcSLDistance(block, atr);
      // EXPERIMENT (experiment/reverse-sr): SL/TP/isLong all key off the
      // MAPPED direction (isLong below) so an inverted setup stops and targets
      // on the correct side. With InpReverseSR=false and a Support block the
      // expressions reduce exactly to the original (entry - slDist /
      // entry + tpRR*slDist), so main-branch behaviour is bit-identical.
      bool   isLong     = (GetDirectionForBlock(block) == DIR_LONG);
      double stopLoss   = isLong ? entryPrice - slDist : entryPrice + slDist;
      // v5.31: the take-profit projection is InpMaxRR (default 4.0R).
      // This must stay the SAME input the Front-Run veto projects from:
      // once an order is resting the veto compares against block.localTP
      // (below) rather than re-deriving the target, so a divergence between
      // this line and the veto's projection would silently disable the veto.
      double tpRR       = (InpMaxRR > 0.0) ? InpMaxRR : 1.0;
      double takeProfit = isLong ? entryPrice + tpRR * slDist
                                 : entryPrice - tpRR * slDist;

      // Store local entry/SL/TP/rrUnit on the block (Pine b.local_*)
      block.localEntry = entryPrice;
      block.localSL    = stopLoss;
      block.localTP    = takeProfit;
      block.rrUnit     = slDist;

      // Broker-level stop validation (isLong computed above with slDist)
      double adjustedSL = stopLoss;
      if(!ValidateStopDistance(entryPrice, adjustedSL, isLong))
         adjustedSL = AdjustSLToMinimum(entryPrice, adjustedSL, isLong);

      // Risk sizing (RiskPercent% or fixed $)
      double lotSize = m_riskManager.CalculateLotSize(entryPrice, adjustedSL);
      if(lotSize <= 0)
        {
         if(EnableLogging)
            Print("[OrderManager] SAFETY ABORT: lot size zero — suppressed");
         return false;
        }
      if(!m_riskManager.HasSufficientMargin(lotSize)) return false;

      // --- Build the pending order request ---
      MqlTradeRequest request;
      MqlTradeResult  result;
      ZeroMemory(request);
      ZeroMemory(result);
      request.action   = TRADE_ACTION_PENDING;
      request.symbol   = m_symbol;
      request.type     = GetOrderTypeForBlock(block);
      request.volume   = lotSize;
      request.price    = NormalizeDouble(entryPrice, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      request.sl       = NormalizeDouble(adjustedSL, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      request.tp       = 0;   // NO TP — exact mirror of Pine (trail-only exits)
      request.deviation = MaxSlippage;
      request.magic    = MagicNumber;
      request.comment  = BuildOrderComment(block.serial, 0);   // v5.27: clamped to 31

      if(request.volume < m_riskManager.GetVolumeMin() ||
         request.volume > m_riskManager.GetVolumeMax())
        {
         Print("[OrderManager] Invalid volume");
         return false;
        }

      // Optimistic lock to prevent concurrent tick duplicate firing
      block.hasPlacedOrder = true;
      m_blockManager.SetBlockAt(blockIndex, block);

      if(SendOrderWithRetry(request, result))
        {
          block.limitOrderTicket = result.order;
          block.pendingOrderCancel = false;
          block.priceAbortLogged = false;   // v5.30: order live, gate re-armed
          m_blockManager.SetBlockOrderTicket(blockIndex, result.order);
          m_blockManager.SetBlockAt(blockIndex, block);
          // build a session ID at PLACE time so the journal file is created immediately
          MqlDateTime ptm; TimeToStruct(TimeCurrent(), ptm);
          string pts = StringFormat("%04d%02d%02d-%02d%02d%02d", ptm.year, ptm.mon, ptm.day, ptm.hour, ptm.min, ptm.sec);
          if(m_journal != NULL)
            {
             m_journal.SetSessionID(StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, pts, block.serial));
             m_journal.LogOrderPlaced(result.order, GetDirectionForBlock(block), block.type, entryPrice, adjustedSL, lotSize, block);
            }
         if(EnableLogging)
            Print("[OrderManager] LIMIT PLACED: ", block.tradeId,
                  " ticket=", result.order,
                  " entry=", DoubleToString(entryPrice, _Digits),
                  " sl=", DoubleToString(adjustedSL, _Digits),
                  " rrUnit=", DoubleToString(slDist, _Digits));
         return true;
        }
      else
        {
         // Rollback the lock if the order completely failed to place
         block.hasPlacedOrder = false;
         m_blockManager.SetBlockAt(blockIndex, block);
         return false;
        }
     }


   //+------------------------------------------------------------------+
   //| Triple-ticket verification: is this broker order still pending? |
   //+------------------------------------------------------------------+
   bool                    IsBlockOrderAlive(ulong ticket)
     {
      if(ticket <= 0) return false;
      if(OrderSelect(ticket))
        {
         ENUM_ORDER_STATE state = (ENUM_ORDER_STATE)OrderGetInteger(ORDER_STATE);
         return (state == ORDER_STATE_PLACED || state == ORDER_STATE_PARTIAL);
        }
      return false;
     }

   //+------------------------------------------------------------------+
   //| HARD ANTI-DUPLICATE: scans the broker's LIVE pending-order pool   |
   //| for ANY order of our Magic/symbol resting at (near) targetPrice. |
   //| Independent of local block bookkeeping — closes the race where a |
   //| new tick doesn't yet know an order was just requested.          |
   //+------------------------------------------------------------------+
   bool                    IsOrderAlreadyLiveAtPrice(double targetPrice, double tolerancePoints = 5.0)
     {
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      if(point <= 0) point = _Point;
      // v5.30: the tolerance must track the price grid the entry was snapped
      // to. A tick can be coarser than a point (3-digit metals: tick 0.01 vs
      // point 0.001), so a tick-snapped entry can slide outside a point-based
      // band and either place a duplicate or trip a false DUPLICATE SHIELD.
      double tick = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      double unit = (tick > 0.0) ? MathMin(tick, point) : point;

      int total = OrdersTotal();
      for(int i = total - 1; i >= 0; i--)
        {
         ulong ticket = OrderGetTicket(i);
         if(ticket > 0 && OrderSelect(ticket))
           {
            if(OrderGetInteger(ORDER_MAGIC) == MagicNumber &&
               OrderGetString(ORDER_SYMBOL) == m_symbol)
              {
               double openPrice = OrderGetDouble(ORDER_PRICE_OPEN);
               if(MathAbs(openPrice - targetPrice) <= (tolerancePoints * unit))
                  return true; // Duplicate detected: order already sitting on broker
              }
           }
        }
      return false;
     }


   //+------------------------------------------------------------------+
   //| 3-TIER FILL DETECTION                                            |
   //|                                                                  |
   //| MT5 hedging mode gives a filled limit order a position ticket    |
   //| unrelated to the order ticket, so resolve it by three sequential |
   //| fallbacks, cheapest and most reliable first:                     |
   //|                                                                  |
   //|   TIER 1  PositionSelectByTicket(pending order ticket)           |
   //|           Valid when the broker reuses the order id as position  |
   //|           id (common on MT5 netting-style fills).                |
   //|                                                                  |
   //|   TIER 2  Deal history -> DEAL_POSITION_ID                       |
   //|           Authoritative: find the IN deal whose DEAL_ORDER is    |
   //|           the pending order and take its DEAL_POSITION_ID.       |
   //|                                                                  |
   //|   TIER 3  Magic+symbol scan EXCLUDING the currently tracked      |
   //|           ticket. Last resort, but also the only tier that       |
   //|           cannot return a stale pyramiding tranche.              |
   //|                                                                  |
   //| excludeTicket is the position already tracked; passing it makes  |
   //| every tier refuse to re-adopt the incumbent position.            |
   //+------------------------------------------------------------------+
   bool                    ResolveFilledPositionTicket(ulong orderTicket,
                                                       ulong excludeTicket,
                                                       ulong &outTicket,
                                                       ENUM_TRADE_DIRECTION &outDir)
     {
      outTicket = 0; outDir = DIR_NONE;
      if(orderTicket <= 0) return false;

      // ---- TIER 1: position ticket == pending order ticket -------------
      // Guarded by excludeTicket for the same reason TIER 2 and TIER 3 are:
      // if the incumbent position happens to share the id of a DIFFERENT
      // pending order, echoing it back would mask a genuine reversal fill.
      if(orderTicket != excludeTicket && PositionSelectByTicket(orderTicket))
        {
         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
            PositionGetString(POSITION_SYMBOL) == m_symbol)
           {
            outTicket = (ulong)PositionGetInteger(POSITION_TICKET);
            outDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                     ? DIR_LONG : DIR_SHORT;
            if(EnableLogging)
               Print("[OrderManager] FILL TIER 1: position id matched order ticket ",
                     orderTicket);
            return true;
           }
        }

      // ---- TIER 2: history -> DEAL_POSITION_ID ------------------------
      // Scope the query tightly to avoid scanning the whole account book.
      datetime from = TimeCurrent() - 7 * 24 * 60 * 60;
      if(HistorySelect(from, TimeCurrent() + 60))
        {
         int deals = HistoryDealsTotal();
         // Walk newest-first: the fill we care about is the most recent.
         for(int d = deals - 1; d >= 0; d--)
           {
            ulong dt = HistoryDealGetTicket(d);
            if(dt <= 0) continue;
            if(HistoryDealGetInteger(dt, DEAL_ORDER) != (long)orderTicket) continue;
            if(HistoryDealGetInteger(dt, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
            if(HistoryDealGetString(dt, DEAL_SYMBOL) != m_symbol) continue;
            if(HistoryDealGetInteger(dt, DEAL_MAGIC) != MagicNumber) continue;

            ulong posId = (ulong)HistoryDealGetInteger(dt, DEAL_POSITION_ID);
            if(posId <= 0) continue;
            if(excludeTicket > 0 && posId == excludeTicket) continue;

            if(PositionSelectByTicket(posId) &&
               PositionGetString(POSITION_SYMBOL) == m_symbol &&
               PositionGetInteger(POSITION_MAGIC) == MagicNumber)
              {
               outTicket = posId;
               outDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                        ? DIR_LONG : DIR_SHORT;
               if(EnableLogging)
                  Print("[OrderManager] FILL TIER 2: history resolved order ",
                        orderTicket, " -> position ", posId);
               return true;
              }
           }
        }

      // ---- TIER 3: magic+symbol scan, excluding the tracked ticket -----
      // Newest position wins so a fresh fill cannot be confused with an
      // older pyramid tranche of the same basket.
      ulong    bestTicket = 0;
      datetime bestTime   = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong pt = PositionGetTicket(i);
         if(pt <= 0 || !PositionSelectByTicket(pt)) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         if(excludeTicket > 0 && pt == excludeTicket) continue;

         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         if(opened >= bestTime)
           {
            bestTime   = opened;
            bestTicket = pt;
           }
        }
      if(bestTicket > 0)
        {
         outTicket = bestTicket;
         outDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                  ? DIR_LONG : DIR_SHORT;
         if(EnableLogging)
            Print("[OrderManager] FILL TIER 3: magic/symbol scan found position ",
                  bestTicket, " (excluded tracked=", excludeTicket, ")");
         return true;
        }

      return false;
     }

   //+------------------------------------------------------------------+
   //| Scans live positions for our Magic+symbol. excludeTicket lets a  |
   //| caller refuse to re-adopt the position it already tracks, which  |
   //| is what stops a pyramid tranche being mistaken for a new fill.   |
   //|                                                                  |
   //| FIX (v5.20): previously this walked the book BACKWARDS and       |
   //| returned the FIRST match, i.e. the highest index. MT5 appends    |
   //| new positions at the end of the book, so that returned the       |
   //| NEWEST position -- the exact opposite of the Tranche 1 adoption  |
   //| contract. On a cold start mid-basket it would adopt a scale-in   |
   //| tranche as the primary, re-seeding entry/SL/lot from the wrong   |
   //| leg (the same hijack class as the SyncActiveTrade bug). Now the  |
   //| book is scanned forwards and the OLDEST opening position wins,   |
   //| with POSITION_TIME as the tie-break so the result does not        |
   //| depend on broker book ordering at all.                            |
   //+------------------------------------------------------------------+
   bool                    FindActivePosition(ulong &outTicket, ENUM_TRADE_DIRECTION &outDir,
                                             ulong excludeTicket = 0)
     {
      outTicket = 0; outDir = DIR_NONE;
      datetime oldest = 0;
      for(int i = 0; i < PositionsTotal(); i++)
        {
         if(!PositionSelectByTicket(PositionGetTicket(i)))
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber ||
            PositionGetString(POSITION_SYMBOL) != m_symbol)
            continue;

         ulong pt = (ulong)PositionGetInteger(POSITION_TICKET);
         if(pt <= 0) continue;
         if(excludeTicket > 0 && pt == excludeTicket) continue;

         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         // Strictly-older wins; 0 initialises on the very first candidate.
         if(outTicket == 0 || opened < oldest)
           {
            oldest    = opened;
            outTicket = pt;
            outDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                     ? DIR_LONG : DIR_SHORT;
           }
        }
      return (outTicket > 0);
     }

   int                     CountMyPositions(void)
     {
      int count = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(PositionSelectByTicket(PositionGetTicket(i)))
           {
            if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
               PositionGetString(POSITION_SYMBOL) == m_symbol)
               count++;
           }
        }
      return count;
     }

   int                     CountMyPendingOrders(void)
     {
      int count = 0;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         ulong ticket = OrderGetTicket(i);
         if(ticket > 0 && OrderSelect(ticket))
           {
            if(OrderGetInteger(ORDER_MAGIC) == MagicNumber &&
               OrderGetString(ORDER_SYMBOL) == m_symbol)
               count++;
           }
        }
      return count;
     }

   ulong                   GetMyPendingOrderByIndex(int index)
     {
      int count = 0;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         ulong ticket = OrderGetTicket(i);
         if(ticket > 0 && OrderSelect(ticket))
           {
            if(OrderGetInteger(ORDER_MAGIC) == MagicNumber &&
               OrderGetString(ORDER_SYMBOL) == m_symbol)
              {
               if(count == index) return ticket;
               count++;
              }
           }
        }
      return 0;
     }

   //+------------------------------------------------------------------+
   //| Closes a position by ticket (returns success)                   |
   //+------------------------------------------------------------------+
   bool                    ClosePosition(ulong ticket)
     {
      if(!PositionSelectByTicket(ticket)) return false;
      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      req.action   = TRADE_ACTION_DEAL;
      req.symbol   = m_symbol;
      req.position = ticket;
      req.volume   = PositionGetDouble(POSITION_VOLUME);
      long posType = PositionGetInteger(POSITION_TYPE);
      req.type     = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      req.price    = (posType == POSITION_TYPE_BUY) ? GetBid() : GetAsk();
      req.deviation = MaxSlippage;
      req.magic    = MagicNumber;
      req.comment  = TradeComment + "_CLOSE";
      return SendOrderWithRetry(req, res);
     }

   //+------------------------------------------------------------------+
   //| Modifies a position's stop-loss                                  |
   //+------------------------------------------------------------------+
   bool                    ModifyStopLoss(ulong ticket, double newSL)
     {
      if(!PositionSelectByTicket(ticket)) return false;

      // Broker stop-level guard: a trail that is too close to market would be
      // rejected with INVALID_STOPS. Widen it to the broker minimum instead so
      // the stop is always accepted (defense in depth for the ATR trail).
      bool isLongPos = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double refPrice = isLongPos ? GetBid() : GetAsk();
      if(!ValidateStopDistance(refPrice, newSL, isLongPos))
        {
         double adjusted = AdjustSLToMinimum(refPrice, newSL, isLongPos);
         if(EnableLogging)
            Print("[OrderManager] SL widened to broker minimum: ticket=", ticket,
                  " ", DoubleToString(newSL, Digits()), " -> ", DoubleToString(adjusted, Digits()));
         newSL = adjusted;
        }

      MqlTradeRequest modReq;
      MqlTradeResult  modRes;
      ZeroMemory(modReq);
      modReq.action   = TRADE_ACTION_SLTP;
      modReq.symbol   = m_symbol;
      modReq.position = ticket;
      modReq.sl       = NormalizeDouble(newSL, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      modReq.tp       = PositionGetDouble(POSITION_TP);
      modReq.magic    = MagicNumber;
      modReq.comment  = TradeComment + "_MODSL";
      if(SendOrderWithRetry(modReq, modRes))
        {
         if(EnableLogging)
            Print("[OrderManager] SL MODIFIED: ticket=", ticket,
                  " newSL=", DoubleToString(newSL, _Digits));
         return true;
        }
      // The venue refused the stop. The position is open and the trail
      // believes this level is live, so count it: the High Table auditor
      // escalates a delta of these because an unprotected open position is
      // the exact failure mode the whole safety stack exists to prevent.
      m_stopModifyFailures++;
      Print("[OrderManager] SL MODIFY FAILED: ticket=", ticket,
            " newSL=", DoubleToString(newSL, _Digits),
            " retcode=", modRes.retcode, " | total=", m_stopModifyFailures);

      return false;
     }

   //+------------------------------------------------------------------+
   //| Deletes a pending order by ticket                                |
   //+------------------------------------------------------------------+
   bool                    DeleteOrder(ulong ticket)
     {
      if(!OrderSelect(ticket)) return false;
      MqlTradeRequest delReq;
      MqlTradeResult  delRes;
      ZeroMemory(delReq);
      delReq.action = TRADE_ACTION_REMOVE;
      delReq.order  = ticket;
      delReq.magic  = MagicNumber;
      delReq.comment = TradeComment + "_DEL";
      if(SendOrderWithRetry(delReq, delRes))
        {
         if(EnableLogging)
            Print("[OrderManager] Order DELETED: ticket=", ticket);
         return true;
        }
      return false;
     }


   //+------------------------------------------------------------------+
   //| Reversal Phase 1: close the current position                    |
   //+------------------------------------------------------------------+
   bool                    InitiateReversal(ulong ticket, SSniperBlock &targetBlock)
     {
      if(m_reversalInProgress)
        {
         Print("[OrderManager] Reversal already in progress — skipping");
         return false;
        }
      m_reversalTargetBlock = targetBlock;
      m_reversalTargetDir   = GetDirectionForBlock(targetBlock);
      m_reversalStartTime   = TimeCurrent();
      m_reversalInProgress  = true;
      if(EnableLogging)
         Print("[OrderManager] REVERSAL INITIATED: closing basket (primary ticket=", ticket, ")",
               " | Target: ", (m_reversalTargetDir == DIR_LONG ? "LONG" : "SHORT"));
      // Close the ENTIRE basket, not just the primary ticket. MT5 hedging
      // mode keeps pyramid tranches as separate positions; closing only
      // the primary would orphan tranches 2/3 with no SL management.
      // FIX (v5.20): logExit=TRUE so this closure is journaled in the
      // session that is still current. The legacy deferral (logExit=false,
      // re-logged later via SyncActiveTrade) is retired because logging
      // after the fact raced the next session ID and could strand the exit
      // record in the wrong session file. CloseEntireBasket() aggregates
      // the tranche tickets before ClearBasket() wipes m_basket[], so the
      // per-tranche closing deals are still resolvable at this point.
      CloseEntireBasket("SAR Reversal", true);
      return true;
     }

   //+------------------------------------------------------------------+
   //| Reversal Phase 2: when flat, open the opposite position         |
   //+------------------------------------------------------------------+
   void                    CompleteReversal(void)
     {
      if(!m_reversalInProgress) return;
      if(CountMyPositions() > 0)
        {
         if(TimeCurrent() - m_reversalStartTime > 60)
           {
            Print("[OrderManager] Reversal TIMEOUT after 60s — clearing lock");
            m_reversalInProgress = false;
           }
         return;
        }
      if(EnableLogging)
         Print("[OrderManager] Reversal: position closed — opening opposite");
      if(!OpenReversalPosition(m_reversalTargetBlock))
         Print("[OrderManager] Reversal: failed to open opposite position");
      m_reversalInProgress = false;
     }

   //+------------------------------------------------------------------+
   //| Opens a market reversal position using the block's levels       |
   //+------------------------------------------------------------------+
   bool                    OpenReversalPosition(SSniperBlock &targetBlock)
     {
      // EXPERIMENT (experiment/reverse-sr): the reversal target's direction is
      // the MAPPED direction -- the same source InitiateReversal() already uses
      // for m_reversalTargetDir (L1833). Deriving it here from the raw zone made
      // an inverted Support block reverse into a LONG while the log and the
      // target direction said SHORT. With InpReverseSR = false the two are the
      // identity, so this reduces to the original expression.
      bool isLong  = (GetDirectionForBlock(targetBlock) == DIR_LONG);
      double entryPrice = isLong ? GetAsk() : GetBid();
      double stopLoss   = targetBlock.localSL;
      if(targetBlock.localSL <= 0)
        {
         // fall back to the Pine formula if not yet computed
         double atr = m_blockManager.GetATR();
         if(atr > 0)
         {
            double slDist = targetBlock.blockHeight + 0.5 * atr;
            stopLoss = isLong ? entryPrice - slDist : entryPrice + slDist;
         }
        }
      if(!ValidateStopDistance(entryPrice, stopLoss, isLong))
         stopLoss = AdjustSLToMinimum(entryPrice, stopLoss, isLong);

      double lotSize = m_riskManager.CalculateLotSize(entryPrice, stopLoss);
      if(lotSize <= 0) return false;
      if(!m_riskManager.HasSufficientMargin(lotSize)) return false;

      MqlTradeRequest request;
      MqlTradeResult  result;
      ZeroMemory(request);
      request.action   = TRADE_ACTION_DEAL;
      request.symbol   = m_symbol;
      request.type     = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      request.volume   = lotSize;
      request.price    = NormalizeDouble(entryPrice, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      request.sl       = NormalizeDouble(stopLoss, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      request.tp       = 0;
      request.deviation = MaxSlippage;
      request.magic    = MagicNumber;
      request.comment  = TradeComment + "_REV";
      if(SendOrderWithRetry(request, result))
        {
         SeedActiveTradeFromPosition(result.order);
         return true;
        }
      return false;
     }


   //+------------------------------------------------------------------+
   //| Fills SActiveTrade from a broker position ticket (reversal)     |
   //+------------------------------------------------------------------+
   void                    SeedActiveTradeFromPosition(ulong positionTicket)
     {
      if(!PositionSelectByTicket(positionTicket)) return;
      m_activeTrade.ticket       = positionTicket;
      long posType               = PositionGetInteger(POSITION_TYPE);
      m_activeTrade.direction    = (posType == POSITION_TYPE_BUY) ? DIR_LONG : DIR_SHORT;
      m_activeTrade.entryPrice   = PositionGetDouble(POSITION_PRICE_OPEN);
      m_activeTrade.initialSL    = PositionGetDouble(POSITION_SL);
      m_activeTrade.initialSLDistance = MathAbs(m_activeTrade.entryPrice - m_activeTrade.initialSL);
      //+----------------------------------------------------------------+
      //| v5.32 -- RECONSTRUCT THE ORIGINAL 1R, DO NOT TAKE THE LIVE ONE. |
      //|                                                                |
      //| This path runs on a COLD START (and on flat adoption), i.e.     |
      //| after the EA has lost every in-memory field. Deriving rrUnit    |
      //| from POSITION_SL is only correct while the untouched initial    |
      //| stop is still in place -- and the whole point of this EA is that |
      //| the stop ratchets. Once breakeven has fired,                  |
      //|                                                                |
      //|     |entry - POSITION_SL|  ~=  friction                       |
      //|                                                                |
      //| a few pips, where the true 1R may be hundreds. Dividing by that |
      //| inflates every subsequent RR by orders of magnitude: a position |
      //| 5 pips into profit is reported as hundreds of R, which both       |
      //| mis-reports the trade and makes the +1.0R tranche-2 gate         |
      //| unsatisfiable in a single tick -- it is jumped, not crossed.     |
      //|                                                                |
      //| Preference order:                                              |
      //|   1. the persisted 1R, but ONLY if it names THIS position       |
      //|   2. |entry - initial SL| (exact on a genuinely cold restart     |
      //|      where the stop has not moved)                              |
      //| and whichever is used is stated in the log, because a silently  |
      //| degraded R unit is exactly how this bug stayed hidden.          |
      //+----------------------------------------------------------------+
      double restoredR = RestoreBasketR(positionTicket);
      if(restoredR > 0.0)
        {
         m_activeTrade.rrUnit = restoredR;
         if(EnableLogging)
            Print("[OrderManager] Basket 1R RESTORED from persistent store: ",
                  DoubleToString(restoredR, _Digits), " (broker SL distance was ",
                  DoubleToString(m_activeTrade.initialSLDistance, _Digits), ")");
        }
      else if(m_activeTrade.initialSLDistance > 0.0)
        {
         m_activeTrade.rrUnit = m_activeTrade.initialSLDistance;
         if(EnableLogging)
            Print("[OrderManager] Basket 1R REBUILT from broker SL distance: ",
                  DoubleToString(m_activeTrade.initialSLDistance, _Digits),
                  " -- no stored record for ticket ", positionTicket,
                  (MathAbs(m_activeTrade.entryPrice - m_activeTrade.initialSL)
                   < 2.0 * SymbolInfoDouble(m_symbol, SYMBOL_POINT)
                   ? " | WARNING: stop sits AT ENTRY, so this 1R is friction-sized"
                     " and the derived RR will be badly overstated"
                   : ""));
        }
      else
        {
         // A zero stop makes every RR in this class zero, which silently
         // disables the entire milestone ladder. Say so rather than proceeding.
         m_activeTrade.rrUnit = 0.0;
         if(EnableLogging)
            Print("[OrderManager] WARNING: basket 1R UNRESOLVED for ticket ",
                  positionTicket, " (entry=", DoubleToString(m_activeTrade.entryPrice,_Digits),
                  " sl=", DoubleToString(m_activeTrade.initialSL,_Digits),
                  ") -- RR-based milestones will stay inert until the stop is set");
        }
      m_activeTrade.currentTrailSL = m_activeTrade.initialSL;
      m_activeTrade.lotSize      = PositionGetDouble(POSITION_VOLUME);
      m_activeTrade.openTime     = (datetime)PositionGetInteger(POSITION_TIME);
      m_activeTrade.trailStep    = STEP_NONE;
      m_activeTrade.highestPriceSinceEntry = (m_activeTrade.direction == DIR_LONG) ? GetBid() : GetAsk();
      ComputeRiskAmount(m_activeTrade);
      m_hasActiveTrade  = true;
      m_activeDirection = m_activeTrade.direction;

      // Rebuild basket state on EA restart so scaling logic remains active
      InitBasket(m_activeTrade.entryPrice, m_activeTrade.rrUnit,
                 m_activeTrade.direction, m_activeTrade.ticket, m_activeTrade.lotSize,
                 m_activeTrade.initialSL, 0);
     }

   //+------------------------------------------------------------------+
   //| Fills SActiveTrade from a FILLED BLOCK (Pine: active_* = b.local_*)|
   //+------------------------------------------------------------------+
   void                    SeedActiveTradeFromBlock(const SSniperBlock &block, ulong positionTicket)
     {
      if(!PositionSelectByTicket(positionTicket)) return;
      m_activeTrade.ticket       = positionTicket;
      m_activeTrade.direction    = GetDirectionForBlock(block);
      m_activeTrade.entryPrice   = block.localEntry;
      m_activeTrade.initialSL    = block.localSL;
      m_activeTrade.initialSLDistance = MathAbs(block.localEntry - block.localSL);
      m_activeTrade.rrUnit       = block.rrUnit;
      m_activeTrade.currentTrailSL = block.localSL;
      m_activeTrade.lotSize      = PositionGetDouble(POSITION_VOLUME);
      m_activeTrade.openTime     = (datetime)PositionGetInteger(POSITION_TIME);
      m_activeTrade.trailStep    = STEP_NONE;
      m_activeTrade.sourceBlockSerial = block.serial;
      // v5.33: an EA-originated seed is never an adopted manual leg. Set
      // explicitly rather than relying on ZeroMemory(), because this struct is
      // reused across baskets on this instance and a leftover true would make
      // the High Table auditor stand down its phantom test for a normal trade.
      m_activeTrade.adoptedManual = false;
      m_adoptedManual             = false;
      m_activeTrade.highestPriceSinceEntry = (m_activeTrade.direction == DIR_LONG) ? GetBid() : GetAsk();
      ComputeRiskAmount(m_activeTrade);
      m_hasActiveTrade  = true;
      m_activeDirection = m_activeTrade.direction;

      // --- PYRAMID: initialise basket with Tranche 1 (primary) ---
      InitBasket(m_activeTrade.entryPrice, m_activeTrade.rrUnit,
                 m_activeTrade.direction, m_activeTrade.ticket, m_activeTrade.lotSize,
                 m_activeTrade.initialSL, block.serial);

      // set the journal session ID for this whole trade basket
      if(m_journal != NULL)
         m_journal.SetSessionID(m_sessionID);

      // --- JOURNAL: log structured entry on fill ---
      if(m_journal != NULL)
         m_journal.LogEntry(m_activeTrade.ticket, m_activeTrade.direction,
                            m_activeTrade.entryPrice, m_activeTrade.initialSL,
                            m_activeTrade.lotSize, m_activeTrade.initialRiskAmount,
                            block);
     }

   //+------------------------------------------------------------------+
   //| Computes the money-risked field for an active trade             |
   //+------------------------------------------------------------------+
   void                    ComputeRiskAmount(SActiveTrade &trade)
     {
      double tickValue = m_riskManager.GetTickValuePerLot();
      double tickSize  = m_riskManager.GetTickSize();
      if(tickSize > 0 && tickValue > 0)
        {
         double slPoints = trade.initialSLDistance / tickSize;
         trade.initialRiskAmount = slPoints * tickValue * trade.lotSize;
        }
      else
         trade.initialRiskAmount = 0;
     }

   //+------------------------------------------------------------------+
   //| Logs a structured EXIT when an active trade closes. Uses history  |
   //| to find the closing deal for exit price / fees / gross profit.  |
   //+------------------------------------------------------------------+
   void                    LogClosedTrade(const SActiveTrade &trade, string reason = "Trade Closed")
     {
      if(m_journal == NULL) return;

      // ---- Basket-aggregated logging -------------------------------
      // A pyramid basket is closed as a UNIT (unified SL / reversal /
      // DD halt). Aggregating the tranches into ONE exit record keeps
      // the journal in 1:1 correspondence with the actual trade, and
      // guarantees no tranche is left unlogged (orphaned) when more
      // than one position was open.
      if(m_basketCount > 0)
        {
         int    logged   = 0;
         double exitPx   = 0.0;
         double aggGross = 0.0;
         double aggComm  = 0.0;
         double aggSwap  = 0.0;
         double totalLot = 0.0;

         for(int b = 0; b < m_basketCount; b++)
           {
            ulong bt = m_basket[b].ticket;
            if(bt <= 0) continue;

            // Scan deal history for THIS tranche's closing deal
            for(int i = HistorySelect(0, TimeCurrent()) - 1; i >= 0; i--)
              {
               ulong dt = HistoryDealGetTicket(i);
               if(dt <= 0) continue;
               if(HistoryDealGetInteger(dt, DEAL_POSITION_ID) != (long)bt) continue;
               if(HistoryDealGetInteger(dt, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

               // Reference exit price is Tranche 1's (the primary leg)
               if(logged == 0)
                  exitPx = HistoryDealGetDouble(dt, DEAL_PRICE);

               aggGross += HistoryDealGetDouble(dt, DEAL_PROFIT);
               aggComm  += HistoryDealGetDouble(dt, DEAL_COMMISSION);
               aggSwap  += HistoryDealGetDouble(dt, DEAL_SWAP);
               totalLot += m_basket[b].size;
               logged++;
               break;
              }
           }

         if(logged > 0)
           {
            if(totalLot <= 0.0) totalLot = trade.lotSize;
            m_journal.LogExit(m_basket[0].ticket, m_basketDir, m_primaryEntry, exitPx,
                              totalLot, aggGross, aggComm, aggSwap, m_basketOpenTime, reason);
            if(EnableLogging)
               Print("[OrderManager] Basket EXIT logged: ", logged, " tranche(s)",
                     " | lots=", DoubleToString(totalLot, 2),
                     " | net=", DoubleToString(aggGross + aggComm + aggSwap, 2),
                     " | ", reason);
            return;
           }
         // No closing deals found yet (history lag) -> fall through to
         // the single-trade path below rather than logging nothing.
         // FIX (v5.21): the settle delay in CloseEntireBasket() makes this
         // rare, but if it still happens the fallback below would emit a
         // record with exitPrice=0 and profit/swap/commission=0, which is
         // worse than no record: it reads as a genuine flat exit. Abort the
         // basket log instead. The next sync tick retries the whole
         // transition, by which point history has settled.
         return;
        }

      // ---- Single-trade logging (no basket attached) ---------------
      if(trade.ticket <= 0) return;

      double exitPrice  = 0.0;
      double commission = 0.0;
      double swap       = 0.0;
      double gross      = 0.0;

      // Scan deal history for the closing deal for this position
      for(int i = HistorySelect(0, TimeCurrent()) - 1; i >= 0; i--)
        {
         ulong dt = HistoryDealGetTicket(i);
         if(dt <= 0) continue;
         if(HistoryDealGetInteger(dt, DEAL_POSITION_ID) != (long)trade.ticket) continue;
         if(HistoryDealGetInteger(dt, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

         exitPrice  = HistoryDealGetDouble(dt, DEAL_PRICE);
         commission = HistoryDealGetDouble(dt, DEAL_COMMISSION);
         swap       = HistoryDealGetDouble(dt, DEAL_SWAP);
         gross      = HistoryDealGetDouble(dt, DEAL_PROFIT);
         break;
        }

      // FIX (v5.21): no closing deal in history means the exit price and all
      // money fields below would be zero. Publishing that would fabricate a
      // flat exit that never happened at a price that does not exist, so
      // bail out and let the next sync tick retry once history has settled.
      if(exitPrice <= 0.0)
        {
         if(EnableLogging)
            Print("[OrderManager] LogClosedTrade: no closing deal in history for ticket ",
                  trade.ticket, " -> exit record deferred, not written");
         return;
        }

      m_journal.LogExit(trade.ticket, trade.direction, trade.entryPrice, exitPrice,
                        trade.lotSize, gross, commission, swap, trade.openTime, reason);
     }


   //+------------------------------------------------------------------+
   //| Detects when a resting limit order has been FILLED. Seeds the   |
   //| active trade from the block and marks the block triggered.      |
   //+------------------------------------------------------------------+
   void                    CheckPendingOrderFills(void)
     {
      SSniperBlock blocks[];
      int count = m_blockManager.GetAllBlocks(blocks);
      for(int i = 0; i < count; i++)
        {
         if(blocks[i].limitOrderTicket <= 0)
            continue;
         ulong ticket = blocks[i].limitOrderTicket;

         // Order still resting?
         if(IsBlockOrderAlive(ticket))
            continue;

         // Order is gone — filled, canceled, or expired.
         // ---- STEP 1: 3-TIER FILL DETECTION -----------------------------
         // excludeTicket = the position we already track, so no tier can
         // re-adopt the incumbent and mask a genuine reversal fill.
         ulong tracked = m_hasActiveTrade ? m_activeTrade.ticket : 0;
         ulong newTicket; ENUM_TRADE_DIRECTION newDir;
         if(ResolveFilledPositionTicket(ticket, tracked, newTicket, newDir))
           {
            // ---- STEP 2: STOP-AND-REVERSE ------------------------------
            // MT5 hedging mode ADDS the new position instead of offsetting
            // the old leg, so an opposite-side fill leaves both baskets
            // live. Close the incumbent basket first, then adopt the new
            // position. The exit is journaled HERE, in the still-current
            // session, before the incoming fill mints its own session ID.
            if(m_hasActiveTrade && m_activeTrade.ticket != newTicket)
              {
               if(EnableLogging)
                  Print("[OrderManager] SAR REVERSAL: closing opposing basket ",
                        "(tracked=", m_activeTrade.ticket,
                        " dir=", (m_activeDirection == DIR_LONG ? "LONG" : "SHORT"),
                        ") -> new=", newTicket,
                        " dir=", (newDir == DIR_LONG ? "LONG" : "SHORT"));

               bool wasReversing    = m_reversalInProgress;
               m_reversalInProgress = true;
               // FIX (v5.20): logExit=TRUE. The outgoing basket's exit price,
               // fees and net P/L must reach the CURRENT session journal and
               // the email report BEFORE the incoming fill seeds its own
               // fresh session ID and basket. Deferring this (the old
               // logExit=false) let the new session overwrite the session ID
               // first, stranding the closure record in the wrong session.
               // The closing deals are already settled here because the fill
               // is confirmed by ResolveFilledPositionTicket() above, so
               // LogClosedTrade() can find every DEAL_ENTRY_OUT.
               // keepTicket=newTicket: the orphan sweep inside must NOT
               // close the position we are reversing INTO. Without it the
               // sweep would shut the new fill on the same tick it appears.
               CloseEntireBasket("SAR Reversal", true, newTicket);
               m_reversalInProgress = wasReversing;

               if(EnableLogging)
                  Print("[OrderManager] SAR REVERSAL complete: opposing basket closed");
              }

            // ---- STEP 3: ADOPT THE NEW TRADE ---------------------------
            // The ticket differs (or there was no active trade), so seed.
            if(!m_hasActiveTrade || m_activeTrade.ticket != newTicket)
              {
               SeedActiveTradeFromBlock(blocks[i], newTicket);
               m_ordersFilled++;
               if(EnableLogging)
                  Print("[OrderManager] LIMIT FILLED: block ", blocks[i].tradeId,
                        " ticket=", newTicket,
                        " dir=", (newDir == DIR_LONG ? "LONG" : "SHORT"));
              }
            // Mark the block triggered + schedule deletion next bar
            int bi = m_blockManager.FindBlockIndexByTicket(ticket);
            if(bi >= 0)
              {
               SSniperBlock mod;
               if(m_blockManager.GetBlockAt(bi, mod))
                 {
                  mod.isTriggered = true;
                  mod.limitOrderTicket = 0;
                  mod.hasPlacedOrder = true;
                  mod.deleteOnBarTime = iTime(m_symbol, PERIOD_CURRENT, 0);
                  m_blockManager.SetBlockAt(bi, mod);
                 }
              }
           }
         else
           {
            // Canceled/expired — just clear the ticket reference
            int bi = m_blockManager.FindBlockIndexByTicket(ticket);
            if(bi >= 0)
              {
               SSniperBlock mod;
               if(m_blockManager.GetBlockAt(bi, mod))
                 {
                  mod.limitOrderTicket = 0;
                  mod.pendingOrderCancel = false;
                  m_blockManager.SetBlockAt(bi, mod);
                 }
              }
           }
        }
     }


public:
   //+------------------------------------------------------------------+
   //| Constructor                                                      |
   //+------------------------------------------------------------------+
                     COttoOrderManager(void)
     {
      m_symbol            = "";
      m_riskManager       = NULL;
      m_blockManager      = NULL;
      m_correlationFilter = NULL;
      m_journal           = NULL;
      m_hasActiveTrade    = false;
      m_activeDirection   = DIR_NONE;
      m_pendingLimitCount = 0;
      m_ordersPlaced      = 0;
      m_ordersFilled      = 0;

      m_ordersRejected    = 0;
      m_stopModifyFailures = 0;
      m_reversalsExecuted = 0;
      m_retryCount        = 0;
      m_reversalInProgress = false;
      m_reversalStartTime = 0;
      m_lastOrderTime     = 0;
      ZeroMemory(m_activeTrade);
      ArrayResize(m_pendingLimitTickets, 0);
      ArrayResize(m_basket, 0, 3);
      m_basketCount    = 0;
      m_primaryEntry   = 0.0;
      m_basketRRUnit   = 0.0;
      m_nextTranche    = 2;
      m_basketDir      = DIR_NONE;
      m_basketOpenTime = 0;
      m_sessionID      = "";
      m_sessionSL      = 0.0;
      m_adoptedManual  = false;
      m_manualNoSLWarnTick = 0;

      // EXPERIMENT (experiment/reverse-sr): the virtual store starts cold and
      // the trigger stamp is zeroed so the FIRST fire of a run is never
      // swallowed by a timestamp left over from an earlier one.
      m_virtualCount         = 0;
      m_lastVirtualTriggerMs = 0;

     }

                    ~COttoOrderManager(void) { ArrayFree(m_pendingLimitTickets); }

   //+------------------------------------------------------------------+
   //| Initialize                                                        |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol,
                                COttoRiskManager       *riskManager,
                                COttoBlockManager      *blockManager,
                                COttoCorrelationFilter *correlationFilter)
     {
      m_symbol            = symbol;
      m_riskManager       = riskManager;
      m_blockManager      = blockManager;
      m_correlationFilter = correlationFilter;
      // v5.33: adoption state starts cold on every Initialize(). SyncActiveTrade()
      // below re-derives it from the book (a magic-0 leg already open across a
      // re-init is re-adopted there), but the throttle timestamp must not survive
      // a re-init or a fresh no-SL refusal would be swallowed by a stale stamp.
      m_adoptedManual      = false;
      m_manualNoSLWarnTick = 0;
      // EXPERIMENT (experiment/reverse-sr): a re-init means a fresh chart load,
      // symbol change or parameter edit. Every stored trigger refers to block
      // serials from a block array that no longer exists, so the store must be
      // dropped here -- the pending path's equivalent is the broker book, which
      // MT5 keeps in sync on its own. The throttle stamp is reset with it so
      // the first fire after a re-init is not artificially delayed.
      ClearVirtualStore();          // public wrapper -> ResetVirtualStore()
      m_lastVirtualTriggerMs = 0;
      SyncActiveTrade();
      if(EnableLogging)
         Print("[OrderManager] Initialized for ", m_symbol, " | Magic: ", MagicNumber);
      return true;
     }
   //+------------------------------------------------------------------+
   //| Injects the journal instance for entry/exit logging              |
   //+------------------------------------------------------------------+
   void              SetJournal(COttoJournal *journal)
     {
      m_journal = journal;
     }


   //+------------------------------------------------------------------+
   //| Re-syncs the active trade from broker positions (init/restart)  |
   //|                                                                  |
   //| ORDERING CONTRACT (FIX v5.20 — position hijacking;               |
   //|                    FIX v5.21 — ghost basket remnants):           |
   //|   1. If a trade is already tracked AND its ticket is still an    |
   //|      open position, DO NOTHING. The incumbent stays primary.     |
   //|   2. If the tracked ticket has VANISHED but other tranches are   |
   //|      still open, the primary was stopped out on a partial        |
   //|      stop-out and the survivors are ghosts: close the whole      |
   //|      basket now rather than adopting a remnant.                  |
   //|   3. Only when NOTHING of this basket is left open do we treat   |
   //|      it as a genuine flat transition and clear state.            |
   //|   4. FindActivePosition() is therefore reached ONLY on a cold    |
   //|      start (no tracked trade), where it adopts the OLDEST open   |
   //|      position = Tranche 1.                                       |
   //|                                                                  |
   //| Previously this called FindActivePosition(..., tracked)          |
   //| unconditionally, so a freshly-opened scale-in tranche could be   |
   //| adopted as the primary and re-seed entry/SL/lot from the wrong   |
   //| leg — corrupting the basket's geometry mid-trade.                |
   //+------------------------------------------------------------------+
   //+------------------------------------------------------------------+
   //| Is the tracked primary still open?                               |
   //|                                                                  |
   //| v5.33: the magic test is CONDITIONAL on the primary being an EA-  |
   //| originated leg. An adopted manual leg carries magic 0 by          |
   //| definition, so the original "magic == MagicNumber" test could     |
   //| never pass for it -- SyncActiveTrade() would read the adoption as  |
   //| closed on the very next tick, log the trade out, and wipe the      |
   //| basket. Accepting magic 0 here is safe in a way it would NOT be    |
   //| elsewhere: this predicate is only ever applied to the ticket the   |
   //| EA has ALREADY decided to manage (m_activeTrade.ticket), it still  |
   //| demands this symbol, and the flag that authorises it is set only   |
   //| by AdoptManualPosition(). The blanket magic filter is deliberately |
   //| NOT relaxed in FindActivePosition/CountMyPositions, where "any     |
   //| magic-0 position" would mean "any of the operator's hand trades".  |
   //+------------------------------------------------------------------+
   bool              IsTrackedTicketOpen(void)
     {
      if(!m_hasActiveTrade || m_activeTrade.ticket <= 0)
         return false;
      if(!PositionSelectByTicket(m_activeTrade.ticket))
         return false;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         return false;
      // Adopted leg: magic is 0 and must be, so it is not tested.
      if(m_adoptedManual || m_activeTrade.adoptedManual)
         return true;
      return (PositionGetInteger(POSITION_MAGIC) == MagicNumber);
     }

   //+------------------------------------------------------------------+
   //| Is any ticket in m_basket[] still present in the terminal book?  |
   //|                                                                  |
   //| v5.33: the positional twin of IsBasketFullyClosed(), with the two |
   //| guards that predicate lacks. IsBasketFullyClosed() returns FALSE  |
   //| for an empty basket, which in the ghost-remnant test below means  |
   //| "nothing to sweep" -- the opposite of what is needed here. It     |
   //| also reads PositionSelectByTicket() alone, without re-checking    |
   //| the symbol, so a recycled ticket number could satisfy it.         |
   //|                                                                  |
   //| This is what makes an ADOPTED primary survivable across a restart |
   //| or an Initialize(): the leg carries magic 0, so CountMyPositions() |
   //| cannot see it, and without this test SyncActiveTrade() would call |
   //| a restart with the leg still open a flat transition -- logging    |
   //| the trade out and stranding an open position with no basket       |
   //| driving it. For a normal EA basket this changes nothing, because  |
   //| CountMyPositions() already answers the same question.             |
   //+------------------------------------------------------------------+
   bool              TrackedBasketStillOpen(void)
     {
      for(int i = 0; i < m_basketCount; i++)
        {
         if(m_basket[i].ticket <= 0) continue;
         if(!PositionSelectByTicket(m_basket[i].ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         return true;
        }
      return false;
     }

   void              SyncActiveTrade(void)
     {
      // ---- 1. Incumbent still live -> nothing to do --------------------
      // This is the hijack guard: an open primary is authoritative and is
      // never replaced by a newer scale-in tranche.
      if(IsTrackedTicketOpen())
         return;

      ulong ticket; ENUM_TRADE_DIRECTION dir;

      // ---- 2. Tracked ticket gone -> are its tranches still alive? -----
      // FIX (v5.21): ghost basket remnant. A partial stop-out can take out
      // the primary while scale-ins T2/T3 survive. Those survivors are NOT
      // a new trade: their entry price, SL and lot belong to a basket that
      // no longer has a primary. Adopting one here would reset the whole
      // basket's risk geometry from a scale-in leg (the same hijack class
      // as v5.20, reached by a different route). Close them instead.
      //
      // The discriminator is the count of positions still OPEN on the
      // broker, NOT the length of m_basket[]. A legitimate flat transition
      // (primary and a tranche both already closed, m_basket[] still len>1)
      // must fall through to branch 3 so the exit is logged under its real
      // reason rather than mislabelled as a remnant cleanup.
      //
      // v5.33: TrackedBasketStillOpen() is the second half of the test so an
      // ADOPTED primary is covered. It has magic 0, so CountMyPositions()
      // cannot count it and the primary would otherwise look like it had
      // vanished into thin air -- the branch would be skipped, branch 3 would
      // find no magic-numbered position either, and the still-open adopted
      // leg would be declared a flat exit with its basket wiped.
      if(m_hasActiveTrade && (CountMyPositions() > 0 || TrackedBasketStillOpen()))
        {
         if(EnableLogging)
            Print("[OrderManager] Primary ticket ", m_activeTrade.ticket,
                  " is gone but ", CountMyPositions(),
                  " tranche(s) survive -> closing ghost basket remnants");
         // logExit=true: journal the cleanup in the CURRENT session while
         // m_basket[] still holds the tranche tickets needed to aggregate
         // the per-tranche closing deals. ClearBasket() runs inside.
         CloseEntireBasket("Orphaned Scale-In Cleanup", true);
         return;
        }

      // ---- 3. Nothing of ours open -> genuine flat transition ----------
      if(!FindActivePosition(ticket, dir))
        {
         // No position at all -> active -> flat transition = trade closed.
         // Suppressed while a legacy reversal is in flight: that path owns
         // its own logging (see InitiateReversal / CompleteReversal).
         if(m_hasActiveTrade && !m_reversalInProgress)
            LogClosedTrade(m_activeTrade);
         if(m_reversalInProgress) m_reversalInProgress = false;
         m_hasActiveTrade  = false;
         m_activeDirection = DIR_NONE;
         return;
        }

      // ---- 4. Cold start / flat adoption -> adopt OLDEST (Tranche 1) ---
      // Reached only once the account is confirmed clear of our positions
      // (branch 2 closed any remnants above), which is exactly the
      // condition that prevents a surviving scale-in from being re-seeded
      // as a brand-new primary entry.
      SeedActiveTradeFromPosition(ticket);
     }

   //+------------------------------------------------------------------+
   //| MANUAL TRADE ADOPTION (v5.33)                                    |
   //|                                                                  |
   //| Brings a position the OPERATOR opened by hand on this chart's     |
   //| symbol into the basket machinery, so the trail, the milestone     |
   //| ladder, the smart trim, the drawdown halt and the journal all     |
   //| manage it exactly as they manage an EA-originated leg.           |
   //|                                                                  |
   //| WHY magic == 0 IS THE IDENTIFIER. MT5 assigns magic 0 only to     |
   //| positions submitted from the terminal's own New Order dialog;     |
   //| every EA stamps its own id. So "magic 0 AND this symbol" is the   |
   //| operator's own hand and nothing else's -- the same discriminator  |
   //| COttoCorrelationFilter already uses to recognise external gold.   |
   //|                                                                  |
   //| WHY THE STOP IS MANDATORY. The basket's 1R is |entry - SL|: the   |
   //| milestone ladder, the pyramid rung sizing and the smart trim are  |
   //| all expressed as multiples of it. A stopless manual position has  |
   //| no 1R to inherit, and inventing one (an ATR guess, say) would     |
   //| fabricate the risk geometry and every RR derived from it. So a    |
   //| stopless leg is REFUSED, on a throttle rather than once per tick. |
   //|                                                                  |
   //| OWNERSHIP IS IMPLICIT. The adopted ticket living in m_basket[] is |
   //| what makes it ours: CloseEntireBasket() step 1 closes m_basket[]  |
   //| tickets with NO magic filter at all, so an adopted leg is already |
   //| closable. No request ever has to be sent TO the magic-0 ticket to |
   //| CLAIM it, and the only requests that carry a magic are SLTP and   |
   //| close, both of which address the position by ticket and are       |
   //| magic-agnostic on every hedging venue.                            |
   //|                                                                  |
   //| Returns the adopted ticket, or 0. Every refusal is logged: a      |
   //| silent non-adoption is indistinguishable from "no manual trade    |
   //| present", which is the one state the operator does not need help  |
   //| believing.                                                        |
   //+------------------------------------------------------------------+
   ulong                   AdoptManualPosition(void)
     {
      // ---- Gate 0: opt-in, and never while reversing -------------------
      // A stop-and-reverse is mid-flight: basket state is being torn down
      // and rebuilt around an opposing fill, so adopting into it would
      // seed a basket that the reversal is about to overwrite.
      if(!InpAdoptManualTrades) return 0;
      if(m_reversalInProgress)  return 0;

      // ---- Gate 1: one basket at a time --------------------------------
      // Refuse while the EA already owns an open position or a live basket.
      // Adopting a hand-opened leg on top of a running trade would give the
      // unified stop two primaries, and the unified ratchet is one-way, so
      // the damage could not be undone by a later tick. This is the single
      // most important refusal here.
      if(m_hasActiveTrade) return 0;
      if(m_basketCount > 0) return 0;
      if(CountMyPositions() > 0) return 0;
      // A resting EA limit order is a FUTURE primary: CheckPendingOrderFills()
      // seeds it, and SeedActiveTradeFromBlock() -> InitBasket() rebuilds the
      // basket around it, dropping an adopted leg out of m_basket[] as an
      // unmanaged orphan while the position itself stays open. Adoption is a
      // convenience, so refusing it here is the safe direction.
      if(CountMyPendingOrders() > 0) return 0;

      // ---- Gate 2: Pine's absolute lockdowns ---------------------------
      // Mirrors PlaceOrdersForArmedBlocks(). The shield/veto are the
      // operator saying "do not take risk right now", and that intent
      // cannot depend on whether the leg was opened by a limit fill or by
      // hand. Both are plain inputs, so this file takes no new dependency.
      if(InpSimNewsShield) return 0;
      if(InpSimMacroVeto)  return 0;

      // ---- Gate 3: locate the OLDEST magic-0 position on this symbol ----
      // Oldest-wins with POSITION_TIME as the tie-break, matching
      // FindActivePosition(): the operator's first manual entry is the one
      // whose stop defines the trade, not whichever the book lists first.
      // Foreign-EA magic is excluded by the ==0 test, so another EA's leg
      // can never be adopted.
      ulong    adoptedTicket = 0;
      datetime oldest        = 0;
      for(int i = 0; i < PositionsTotal(); i++)
        {
         if(!PositionSelectByTicket(PositionGetTicket(i))) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != 0)        continue;

         ulong pt = (ulong)PositionGetInteger(POSITION_TICKET);
         if(pt <= 0) continue;
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         if(adoptedTicket == 0 || opened < oldest)
           {
            oldest = opened;
            adoptedTicket = pt;
           }
        }
      if(adoptedTicket == 0) return 0;

      // ---- Gate 4: the leg must carry a usable stop --------------------
      if(!PositionSelectByTicket(adoptedTicket)) return 0;
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      if(sl <= 0.0 || entry <= 0.0 || MathAbs(entry - sl) <= 0.0)
        {
         // Throttled: the condition is stable for as long as the operator
         // leaves the leg unprotected, so an unthrottled Print would emit
         // the same line on every tick until they set a stop.
         int warnMins = (InpManualNoSLWarnMinutes > 0) ? InpManualNoSLWarnMinutes : 5;
         if(TimeCurrent() - m_manualNoSLWarnTick >= warnMins * 60)
           {
            m_manualNoSLWarnTick = TimeCurrent();
            Print("[OrderManager] Manual position ", adoptedTicket, " on ",
                  m_symbol, " NOT adopted: no stop-loss (need SL>0 to derive 1R).",
                  " Set a stop and it will be picked up automatically.");
           }
         return 0;
        }

      // ---- Adopt -------------------------------------------------------
      ENUM_TRADE_DIRECTION dir =
         (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? DIR_LONG : DIR_SHORT;

      m_activeTrade.ticket            = adoptedTicket;
      m_activeTrade.direction         = dir;
      m_activeTrade.entryPrice        = entry;
      m_activeTrade.initialSL         = sl;
      m_activeTrade.initialSLDistance = MathAbs(entry - sl);
      m_activeTrade.rrUnit            = MathAbs(entry - sl);  // 1R == initial stop distance
      m_activeTrade.currentTrailSL    = sl;
      m_activeTrade.lotSize           = PositionGetDouble(POSITION_VOLUME);
      m_activeTrade.openTime          = (datetime)PositionGetInteger(POSITION_TIME);
      m_activeTrade.trailStep         = STEP_NONE;
      m_activeTrade.sourceBlockSerial = 0;   // no Pine block produced this leg
      m_activeTrade.highestPriceSinceEntry = (dir == DIR_LONG) ? GetBid() : GetAsk();
      m_activeTrade.adoptedManual     = true;
      ComputeRiskAmount(m_activeTrade);
      m_hasActiveTrade  = true;
      m_activeDirection = dir;
      m_adoptedManual   = true;

      // "MAN" names the origin in the session ID so an adopted basket's
      // journal file is identifiable at a glance. Only the FINAL token
      // changes, and SessionFileName() splits on '-', so the timestamp
      // indices the journal relies on are untouched.
      InitBasket(entry, m_activeTrade.rrUnit, dir, adoptedTicket,
                 m_activeTrade.lotSize, sl, 0, "MAN");

      if(m_journal != NULL)
        {
         m_journal.SetSessionID(m_sessionID);
         m_journal.LogManualAdoption(adoptedTicket, dir, entry, sl,
                                     m_activeTrade.lotSize, m_activeTrade.initialSLDistance);
        }
      if(EnableLogging)
         Print("[OrderManager] ADOPTED manual position ", adoptedTicket, " on ",
               m_symbol, " | dir=", (dir == DIR_LONG ? "LONG" : "SHORT"),
               " | entry=", DoubleToString(entry, _Digits),
               " | sl=", DoubleToString(sl, _Digits),
               " | lots=", DoubleToString(m_activeTrade.lotSize, 2),
               " | 1R=", DoubleToString(m_activeTrade.rrUnit, _Digits),
               " | session=", m_sessionID);
      return adoptedTicket;
     }

   //+------------------------------------------------------------------+
   //| Main update — reversal completion + pending-fill detection      |
   //+------------------------------------------------------------------+
   void              Update(void)
     {
      CompleteReversal();
      CheckPendingOrderFills();
      // v5.33: LAST, so an EA fill detected in this same tick becomes the
      // primary first and AdoptManualPosition()'s one-basket-at-a-time gate
      // then refuses. Called every tick; the gates above are pure reads and
      // the sweep is O(PositionsTotal), so this is cheap on a flat book.
      AdoptManualPosition();

      // EXPERIMENT (experiment/reverse-sr) STEP 9: virtual market execution.
      // Runs LAST on purpose: CompleteReversal() (which can turn a reversed
      // basket into an active trade and therefore flips m_activeDirection)
      // and CheckPendingOrderFills() (which completes the changeover on a
      // basket that arrived some earlier tick) have both settled by now, so
      // PurgeVirtualOrders() sees the true basket state and can never
      // fire a market entry into a book that is mid-reversal.
      // MarkVirtualOrdersToMarket() is a no-op unless InpVirtualOrders.
      PurgeVirtualOrders();
      MarkVirtualOrdersToMarket();
     }

   //+------------------------------------------------------------------+
   //| PINE ORDER PLACEMENT — for every armed, non-vetoed, non-        |
   //| triggered, not-yet-placed block, apply the Pine gates and place |
   //| a pending limit order. Called from OnTick (has_placed_order     |
   //| prevents duplicates).                                          |
   //+------------------------------------------------------------------+
   void              PlaceOrdersForArmedBlocks(void)
     {
      if(m_reversalInProgress) return;
      if(InpSimNewsShield) return;          // Pine sim_news_shield: absolute lockdown
      if(InpSimMacroVeto) return;           // Pine sim_macro_veto

      SSniperBlock blocks[];
      int count = m_blockManager.GetAllBlocks(blocks);
      for(int i = 0; i < count; i++)
        {
         if(!blocks[i].isArmed || blocks[i].isVetoed || blocks[i].isTriggered ||
            blocks[i].hasPlacedOrder || blocks[i].limitOrderTicket > 0)
            continue;

         // STACKED / OVERLAPPING BLOCK LOCKOUT — skip if an older active
         // primary block of the same type is within 3.0*ATR (keep disarmed).
         if(m_blockManager.IsBlockedByPrimary(i))
            continue;

         // EXPERIMENT (experiment/reverse-sr): one router, two back ends.
         // InpVirtualOrders holds the geometry in RAM and fires at market when
         // price reaches it; otherwise the broker pending path runs unchanged.
         // PlaceLimitOrder() also self-guards, so the virtual branch below is
         // the readable path and the guard is the belt-and-braces one.
         if(InpVirtualOrders)
            ArmVirtualEntry(i, blocks[i]);
         else
            PlaceLimitOrder(i, blocks[i]);
        }
     }

   //+------------------------------------------------------------------+
   //| ROBUST CLEANUP — cancels resting orders on blocks that are       |
   //| vetoed, triggered, flipped, deleted, or flagged for cancellation.|
   //| Runs before/after the block funnel so no broker order is ever   |
   //| orphaned, and so the block array can release the struct safely. |
   //+------------------------------------------------------------------+
   void              CancelOrdersForInvalidBlocks(void)
     {
      // EXPERIMENT (experiment/reverse-sr): under InpVirtualOrders this EA
      // holds no resting broker orders, so the loop below can never fire for
      // one of our blocks. The store gets the identical rule set instead.
      PurgeVirtualOrders();

      SSniperBlock blocks[];
      int count = m_blockManager.GetAllBlocks(blocks);
      for(int i = 0; i < count; i++)
        {
         bool mustCancel = blocks[i].pendingOrderCancel ||
                           blocks[i].isVetoed ||
                           blocks[i].isTriggered ||
                           (blocks[i].deleteOnBarTime > 0);
         if(!mustCancel) continue;
         if(blocks[i].limitOrderTicket <= 0) continue;

         if(IsBlockOrderAlive(blocks[i].limitOrderTicket))
           {
             if(DeleteOrder(blocks[i].limitOrderTicket))
               {
                if(EnableLogging)
                   Print("[OrderManager] Cancelled order ", blocks[i].limitOrderTicket,
                         " (", blocks[i].tradeId, ")");
                if(m_journal != NULL)
                  {
                   string cancelReason = "Manual";
                   if(blocks[i].vetoReason == VETO_FRONTRUN)       cancelReason = "Fired Front-Run 1:3 Veto";
                   else if(blocks[i].vetoReason == VETO_STALE)     cancelReason = "Stale Veto (45D)";
                   else if(blocks[i].vetoReason == VETO_NEARMISS)  cancelReason = "Near-Miss Veto (6D)";
                   else if(blocks[i].vetoReason == VETO_MOMENTUM)  cancelReason = "Momentum Veto";
                   else if(blocks[i].vetoReason == VETO_FVG)        cancelReason = "FVG Veto";
                   else if(blocks[i].vetoReason == VETO_SIZING)     cancelReason = "Sizing Veto";
                   else if(blocks[i].vetoReason == VETO_NO_SEPARATION) cancelReason = "Separation Veto";
                   else if(blocks[i].vetoReason == VETO_BROKEN)     cancelReason = "Block Broken";
                   else if(blocks[i].vetoReason == VETO_FLIPPED)    cancelReason = "Block Flipped";
                   else if(blocks[i].vetoReason == VETO_CORRELATION) cancelReason = "Vector Consensus Veto";
                   else if(blocks[i].pendingOrderCancel)           cancelReason = "Manual / Direction Conflict";
                   MqlDateTime ctm; TimeToStruct(TimeCurrent(), ctm);
                   string cts = StringFormat("%04d%02d%02d-%02d%02d%02d", ctm.year, ctm.mon, ctm.day, ctm.hour, ctm.min, ctm.sec);
                   m_journal.SetSessionID(StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, cts, blocks[i].serial));
                   m_journal.LogCancellation(cancelReason);
                  }
               }
           }
         // zero the ticket regardless (order gone or cancelled)
         int bi = m_blockManager.FindBlockIndexByTicket(blocks[i].limitOrderTicket);
         if(bi >= 0)
           {
            SSniperBlock mod;
            if(m_blockManager.GetBlockAt(bi, mod))
              {
               mod.limitOrderTicket = 0;
               mod.pendingOrderCancel = false;
               m_blockManager.SetBlockAt(bi, mod);
              }
           }
        }
     }
   //+------------------------------------------------------------------+
   //| v5.26 — CancelOpposingConsensusOrders                        |
   //| Strict outlier sweep: cancels any of OUR resting pendings whose    |
   //| direction fights the portfolio currency-vector consensus beyond    |
   //| InpConsensusVetoThreshold.                                         |
   //|                                                                    |
   //| v5.28: scoped to THIS chart's symbol. The magic filter alone is NOT |
   //| safe here: every EA instance shares MagicNumber, so a magic-only    |
   //| sweep made each of the ~28 charts walk the entire shared pending    |
   //| book and delete every other chart's opposing order -- a cancel      |
   //| storm across the whole portfolio. The consensus TEST stays global   |
   //| (IsConsensusOpposed reads the portfolio vector); only the ACTOR is  |
   //| now local, so each chart prunes only its own resting orders.        |
   //+------------------------------------------------------------------+
   void              CancelOpposingConsensusOrders(void)
     {
      if(!InpCancelOpposingPendings) return;
      if(m_correlationFilter == NULL) return;

      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         ulong ticket = OrderGetTicket(i);
         if(ticket <= 0) continue;
         if(!OrderSelect(ticket)) continue;
         if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
         if(OrderGetString(ORDER_SYMBOL) != m_symbol) continue;

         ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         ENUM_TRADE_DIRECTION dir = DIR_NONE;
         if(ot == ORDER_TYPE_BUY_LIMIT || ot == ORDER_TYPE_BUY_STOP)   dir = DIR_LONG;
         if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) dir = DIR_SHORT;
         if(dir == DIR_NONE) continue;

         string sym = m_symbol;
         if(!m_correlationFilter.IsConsensusOpposed(sym, dir)) continue;

         if(DeleteOrder(ticket))
           {
            if(EnableLogging)
               Print("[OrderManager] VECTOR CANCEL: ticket ", ticket, " ", sym,
                     (dir == DIR_LONG ? " LONG" : " SHORT"),
                     " | consensus = ",
                     DoubleToString(m_correlationFilter.GetPairConsensus(sym), 1), "%");

            // Release any block still holding this ticket so the array can
            // free the struct and the duplicate shield stays consistent.
            int bi = m_blockManager.FindBlockIndexByTicket(ticket);
            if(bi >= 0)
              {
               SSniperBlock mod;
               if(m_blockManager.GetBlockAt(bi, mod))
                 {
                  mod.limitOrderTicket = 0;
                  mod.pendingOrderCancel = false;
                  m_blockManager.SetBlockAt(bi, mod);
                 }
              }
           }
        }
     }




   //+------------------------------------------------------------------+
   //| Cancels same-direction pending orders when a trade is active    |
   //| (Pine post-fill cancel of conflicting blocks).                  |
   //+------------------------------------------------------------------+
   void              ManageDirectionConflict(void)
     {
      if(!m_hasActiveTrade) return;
      int myOrders = CountMyPendingOrders();
      for(int i = myOrders - 1; i >= 0; i--)
        {
         ulong ticket = GetMyPendingOrderByIndex(i);
         if(ticket > 0 && OrderSelect(ticket))
           {
            ENUM_ORDER_TYPE oType = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
            if((m_activeDirection == DIR_LONG && oType == ORDER_TYPE_BUY_LIMIT) ||
               (m_activeDirection == DIR_SHORT && oType == ORDER_TYPE_SELL_LIMIT))
               DeleteOrder(ticket);
           }
        }
     }

   //+------------------------------------------------------------------+
   //| Cancels ALL resting pending orders (deinit / DD halt)          |
   //+------------------------------------------------------------------+
   void              CancelAllPendingOrders(void)
     {
      int myOrders = CountMyPendingOrders();
      for(int i = myOrders - 1; i >= 0; i--)
        {
         ulong ticket = GetMyPendingOrderByIndex(i);
         if(ticket > 0)
            DeleteOrder(ticket);
        }
     }

   //+------------------------------------------------------------------+
   //| Public broker-introspection helpers (used by the EA)            |
   //+------------------------------------------------------------------+
   int               CountMyPending(void) { return CountMyPendingOrders(); }
   bool              ForceClose(ulong ticket)  { return ClosePosition(ticket); }
   bool              ModifySL(ulong ticket, double newSL) { return ModifyStopLoss(ticket, newSL); }

   // EXPERIMENT (experiment/reverse-sr) EA-facing wrappers, matching the
   // CountMyPending/ForceClose/ModifySL convention above. ClearVirtualStore()
   // is the public twin of CancelAllPendingOrders() (DD halt + OnDeinit);
   // GetVirtualCount() is used by the periodic status log.
   void              ClearVirtualStore(void) { ResetVirtualStore(); }
   int               GetVirtualCount(void) const { return m_virtualCount; }


   //+------------------------------------------------------------------+
   //| Getters                                                          |
   //+------------------------------------------------------------------+
   bool              HasActiveTrade(void) const { return m_hasActiveTrade; }

   //+------------------------------------------------------------------+
   //| Public count of this EA's open positions (magic + symbol).       |
   //| Exposes the private CountMyPositions() scan so callers such as   |
   //| the drawdown halt can detect tranches even when m_hasActiveTrade |
   //| has already been cleared.                                        |
   //+------------------------------------------------------------------+
   int               CountOpenPositions(void) { return CountMyPositions(); }

   ENUM_TRADE_DIRECTION GetActiveDirection(void) const { return m_activeDirection; }
   SActiveTrade      GetActiveTrade(void) const { return m_activeTrade; }
   bool              GetActiveTradeRef(SActiveTrade &outTrade) const
     {
      if(!m_hasActiveTrade) return false;
      outTrade = m_activeTrade;
      return true;
     }
   void              SetActiveTradeSL(double newSL) { m_activeTrade.currentTrailSL = newSL; }
   void              SetActiveTradeHighWatermark(double newHigh) { m_activeTrade.highestPriceSinceEntry = newHigh; }
   void              SetActiveTradeStep(ENUM_TRAIL_STEP step) { m_activeTrade.trailStep = step; }
   bool              IsReversalInProgress(void) const { return m_reversalInProgress; }
   int               GetOrdersPlaced(void) const { return m_ordersPlaced; }
   int               GetOrdersFilled(void) const { return m_ordersFilled; }
   int               GetOrdersRejected(void) const { return m_ordersRejected; }
   //| Monotonic lifetime total, deliberately NOT cleared per trade: the
   //| auditor diffs it between audit cycles to isolate new failures.
   int               GetStopModifyFailures(void) const { return m_stopModifyFailures; }
   int               GetReversalsExecuted(void) const { return m_reversalsExecuted; }
   int               GetRetryCount(void) const { return m_retryCount; }

   //+------------------------------------------------------------------+
   //| PYRAMID BASKET methods (unified group stop)                      |
   //+------------------------------------------------------------------+
   // idSuffix: v5.33. When empty (every EA-originated basket) the session ID
   // keeps its historical "#OTTO-<sym>-<date>-<time>-BLK<n>" tail. An ADOPTED
   // basket passes "MAN" instead, minting "#OTTO-<sym>-<date>-<time>-MAN".
   // The token COUNT is unchanged and only the final token's text differs, so
   // the '-' split indices in COttoJournal::SubjectLine() (p[1..3]) and
   // SessionFileName() (parts[1..4]) are untouched, and filenames stay as
   // safe as they were: "MAN" survives the character filter as-is.
   void              InitBasket(double entry, double rrUnit, ENUM_TRADE_DIRECTION dir, ulong ticket, double lot, double initSL, int blockSerial,
                                string idSuffix = "")
     {
      ArrayResize(m_basket, 0, 3);
      m_basketCount    = 0;
      m_primaryEntry   = entry;
      m_basketRRUnit   = rrUnit;
      m_basketDir      = dir;
      m_nextTranche    = 2;
      m_basketOpenTime = TimeCurrent();
      m_sessionSL      = initSL;   // unified ratchet starts at the initial SL
      // v5.32: remember 1R against the PRIMARY ticket so a restart can rebuild
      // the basket geometry exactly instead of inferring it from a moved stop.
      PersistBasketR(rrUnit, ticket);
        // Reuse place-time session ID if already set on the journal (ONE file per setup)
        // v5.33: an ADOPTED basket must NOT inherit the journal's current ID.
        // m_sessionID has just been wiped by ClearBasket(), PATH 2 of
        // InitBasket() overwrites the journal ID on every basket it seeds, and
        // LogCancellation() sets one for a cancelled block's own setup -- so
        // the inherited value can easily name a setup that never opened this
        // position. Only the EA block path (idSuffix=="") may reuse it.
        if(idSuffix == "" && m_journal != NULL && m_journal.GetSessionID() != "")
           m_sessionID = m_journal.GetSessionID();
        else
          {
           MqlDateTime utm2; TimeToStruct(TimeCurrent(), utm2);
           string tsF = StringFormat("%04d%02d%02d-%02d%02d%02d", utm2.year, utm2.mon, utm2.day, utm2.hour, utm2.min, utm2.sec);
           // blockSerial is deliberately unused when idSuffix is supplied: an
           // adopted manual leg has no Pine block, so there is no serial to
           // name and "MAN" names its origin instead.
           string tail = (idSuffix != "") ? idSuffix : StringFormat("BLK%d", blockSerial);
           m_sessionID = StringFormat("#OTTO-%s-%s-%s", m_symbol, tsF, tail);
          }
      // FIX (v5.19): ArrayResize(m_basket, 0, 3) above leaves the array at
      // ZERO length, and m_basketCount was reset to 0 - so the write below
      // indexed [0] of an empty array and faulted ("array out of range").
      // Grow first, exactly as AddPyramidTranche already does. Harmless when
      // the array is already sized; this makes InitBasket safe on a cold or
      // freshly-cleared basket.
      ArrayResize(m_basket, m_basketCount + 1, 3);
      m_basket[m_basketCount].ticket  = ticket;
      m_basket[m_basketCount].entry   = entry;
      m_basket[m_basketCount].size    = lot;
      m_basket[m_basketCount].tranche = 1;
      m_basketCount++;
     }

   bool              IsPyramidPending(int tranche) const
     {
      return (InpPyramidEnable && m_nextTranche == tranche && m_hasActiveTrade);
     }

   // v5.29: the ladder is 2 -> 3 -> 4 -> 0, i.e. FOUR tranches total (one
   // initial + three scale-ins). trancheToAdd is named distinctly from the
   // m_basket[].tranche struct field it is stored into, so the LHS of that
   // assignment can never be captured by a rename of this parameter.
   bool              AddPyramidTranche(int trancheToAdd, double slOverride = 0.0)
     {
      if(!InpPyramidEnable || m_basketCount == 0)
        {
         // v5.32: was a bare 'false'. Silent refusals made "T2 never triggers"
         // unattributable: the caller could not tell a disabled feature from a
         // missing basket.
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " REFUSED: ",
                  !InpPyramidEnable ? "InpPyramidEnable=false" : "basket empty (m_basketCount=0)",
                  " | nextTranche=", m_nextTranche, " | err=", GetLastError());
         return false;
        }
      if(trancheToAdd != m_nextTranche)
        {
         // v5.32: the ladder cursor is the second-most-likely reason a rung
         // never fires (a parked/skipped rung leaves the cursor elsewhere), so
         // an out-of-order request is now loud instead of invisible.
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " REFUSED: out of order | ",
                  "nextTranche=", m_nextTranche, " (expected ", trancheToAdd,
                  ") | basketCount=", m_basketCount, " | err=", GetLastError());
         return false;
        }

      // v5.29: exact descending risk tiers, one per rung:
      //   T2 = InpRiskT2Pct, T3 = InpRiskT3Pct, T4 = InpRiskT4Pct
      // Written as an explicit chain rather than a ternary so that adding a
      // fifth rung is a one-line change, and so an unrecognised tranche can
      // never silently inherit another rung's risk.
      double riskPct = 0.0;
      if(trancheToAdd == 2)      riskPct = InpRiskT2Pct;
      else if(trancheToAdd == 3) riskPct = InpRiskT3Pct;
      else if(trancheToAdd == 4) riskPct = InpRiskT4Pct;
      else
        {
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd,
                  " REFUSED: unknown rung (ladder is 2/3/4) | err=", GetLastError());
         return false;
        }
      // v5.32: FALLBACK CHAIN. m_basketRRUnit is the basket's true 1R and is
      // correct on the normal path, but a cold restart can leave it zeroed (see
      // the original-R reconstruction in SeedActiveTradeFromPosition). Rather
      // than refuse the scale-in, recover 1R from the active-trade struct and
      // finally from the live stop distance, logging which source was used so a
      // degraded R unit is never silent.
      double slDist = m_basketRRUnit;
      string rSource = "basket";
      if(slDist <= 0.0 && m_hasActiveTrade && m_activeTrade.rrUnit > 0.0)
        {
         slDist = m_activeTrade.rrUnit;
         rSource = "activeTrade";
        }
      if(slDist <= 0.0 && m_hasActiveTrade
         && m_activeTrade.entryPrice > 0.0 && m_activeTrade.initialSL > 0.0)
        {
         slDist = MathAbs(m_activeTrade.entryPrice - m_activeTrade.initialSL);
         rSource = "initialSLDistance";
        }
      if(slDist <= 0.0)
        {
         // v5.32: previously an unexplained 'false'. Print every candidate so a
         // zero R unit can be traced to the field that lost it.
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " REFUSED: no usable R unit | ",
                  "m_basketRRUnit=", DoubleToString(m_basketRRUnit,_Digits),
                  " hasActiveTrade=", (m_hasActiveTrade?"true":"false"),
                  " activeRRUnit=", DoubleToString(m_hasActiveTrade?m_activeTrade.rrUnit:0.0,_Digits),
                  " | err=", GetLastError());
         return false;
        }
      if(rSource != "basket" && EnableLogging)
         Print("[Pyramid] Tranche ", trancheToAdd, " R unit RECOVERED from ", rSource,
               ": ", DoubleToString(slDist,_Digits), " (m_basketRRUnit was ",
               DoubleToString(m_basketRRUnit,_Digits), ")");

      double lot = m_riskManager.RiskPctLotSize(riskPct, slDist);
      if(lot <= 0.0)   // below broker minimum lot -> skip this tranche
        {
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " skipped: risk lot below min.",
                  " risk%=", DoubleToString(riskPct,2),
                  " slDist=", DoubleToString(slDist,_Digits));

         // Advance the tranche counter so we don't spam this every tick
         // v5.29: ladder advance 2 -> 3 -> 4 -> 0 (0 = ladder exhausted).
         m_nextTranche = (trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0);
         return false;
        }
      if(!m_riskManager.HasSufficientMargin(lot))
        {
         // v5.32: was a bare 'false'. NOTE the deliberate asymmetry with the
         // lot-too-small branch above: the cursor is NOT advanced here, because
         // insufficient margin is transient -- the next tick may be fundable,
         // whereas a sub-minimum lot never will be.
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " REFUSED: insufficient margin for lot ",
                  DoubleToString(lot,2), " | nextTranche stays ", m_nextTranche,
                  " (retried next tick) | err=", GetLastError());
         return false;
        }
      bool isLong = (m_basketDir == DIR_LONG);
      double entryPrice = isLong ? GetAsk() : GetBid();

      // PROTECT-ON-FILL: a pyramided tranche is opened with a market order, so
      // any delay before the first ApplyUnifiedSL() leaves it with NO stop at
      // all (the hedge-mode orphan risk). If the caller supplied a stop, send it
      // WITH the fill request so the position is never naked, and validate it
      // against the broker's minimum stop distance first.
      double reqSL = 0.0;
      if(slOverride > 0.0)
        {
         reqSL = AdjustSLToMinimum(entryPrice, slOverride, isLong);
         if(EnableLogging && MathAbs(reqSL - slOverride) > SymbolInfoDouble(m_symbol, SYMBOL_POINT))
            Print("[Pyramid] Tranche ", trancheToAdd, " SL widened to broker minimum: ",
                  DoubleToString(slOverride, Digits()), " -> ", DoubleToString(reqSL, Digits()));
        }

      MqlTradeRequest req; MqlTradeResult res;
      ZeroMemory(req); ZeroMemory(res);
      req.action   = TRADE_ACTION_DEAL;
      req.symbol   = m_symbol;
      req.type     = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      req.volume   = lot;
      req.price    = NormalizeDouble(entryPrice, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      req.sl       = NormalizeDouble(reqSL, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
      req.tp       = 0;
      req.deviation = MaxSlippage;
      req.magic    = MagicNumber;
      req.comment  = BuildOrderComment(m_activeTrade.sourceBlockSerial, trancheToAdd);   // v5.27: clamped to 31
      req.type_filling = GetFillingMode();
      if(SendOrderWithRetry(req, res))
        {
         ArrayResize(m_basket, m_basketCount + 1, 3);
         m_basket[m_basketCount].ticket  = res.order;
         m_basket[m_basketCount].entry   = res.price;
         m_basket[m_basketCount].size    = lot;
         m_basket[m_basketCount].tranche = trancheToAdd;
         m_basketCount++;
         // v5.29: ladder advance 2 -> 3 -> 4 -> 0 (0 = ladder exhausted).
         m_nextTranche = (trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0);
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " added: ticket=", res.order,
                  " lot=", DoubleToString(lot,2), " risk%=", DoubleToString(riskPct,2),
                  " entry=", DoubleToString(res.price,_Digits),
                  " sl=", DoubleToString(reqSL,_Digits));
         if(m_journal != NULL)
            {
             m_journal.SetSessionID(m_sessionID);
             m_journal.LogPyramid(trancheToAdd, res.order, res.price, lot, riskPct, m_sessionSL);
            }
         return true;
        }
      // v5.32: the ONLY remaining exit, and previously a bare 'false'. A
      // rejected market order is the failure mode the field could never see:
      // the rung was reached, the lot was fundable, and the broker said no.
      // Report the retcode alongside GetLastError() so the reason survives.
      if(EnableLogging)
         Print("[Pyramid] Tranche ", trancheToAdd, " REJECTED by broker | lot=",
               DoubleToString(lot,2), " risk%=", DoubleToString(riskPct,2),
               " R=", DoubleToString(slDist,_Digits),
               " retcode=", res.retcode, " (", res.comment, ")",
               " | nextTranche=", m_nextTranche, " (retried next tick)");
      return false;
     }

   void              ApplyUnifiedSL(double newSL)
     {
      // FIX (v5.19): make the "no direction yet" case EXPLICIT. Previously
      // DIR_NONE fell through to the SHORT branch below, where the test
      // "newSL >= m_sessionSL" is trivially true for any positive price when
      // m_sessionSL is still 0.0 - so the call silently did nothing. That was
      // an accident of comparison order rather than a stated rule, and it
      // would break the moment the seed value or the guard was changed.
      // Production always calls InitBasket() first (which sets a real
      // direction and seeds m_sessionSL), so this path is not reachable
      // today; refusing loudly keeps it diagnosable if that ever changes.
      if(m_basketDir == DIR_NONE)
        {
         if(EnableLogging)
            Print("[OrderManager] ApplyUnifiedSL IGNORED: basket direction not set",
                  " (DIR_NONE) | newSL=", DoubleToString(newSL, _Digits),
                  " | call InitBasket() first");
         return;
        }

      // ONE-WAY RATCHET: only advance to reduce risk / lock profit, never backward
      bool isLong = (m_basketDir == DIR_LONG);
      if(isLong && newSL <= m_sessionSL) return;
      if(!isLong && newSL >= m_sessionSL) return;

      // FIX (v5.16): push to the broker FIRST and only advance the session
      // ratchet once at least one STOP IS ACTUALLY LIVE. Previously m_sessionSL
      // was advanced before the loop, so a rejected write left internal state
      // ahead of every real broker stop - and because the ratchet is one-way,
      // the correct value could never be re-applied on later ticks.
      bool anyApplied = false;
      bool allApplied = true;
      for(int i = 0; i < m_basketCount; i++)
        {
         if(ModifyStopLoss(m_basket[i].ticket, newSL))
            anyApplied = true;
         else
            allApplied = false;
        }

      // A basket with zero tickets has nothing to push; the caller may still
      // be seeding state, so accept it rather than silently discarding it.
      if(m_basketCount == 0) anyApplied = true;

      if(anyApplied)
        {
         m_sessionSL = newSL;
         if(!allApplied && EnableLogging)
            Print("[OrderManager] PARTIAL unified SL: some tickets rejected at ",
                  DoubleToString(newSL, _Digits), " | session SL advanced (>=1 live)");
        }
      else if(EnableLogging)
         Print("[OrderManager] UNIFIED SL REJECTED on ALL tickets at ",
               DoubleToString(newSL, _Digits), " | session SL retained at ",
               DoubleToString(m_sessionSL, _Digits), " (will retry next tick)");
     }

   string            GetSessionID(void) const { return m_sessionID; }
   double            GetSessionSL(void) const { return m_sessionSL; }

   // Forwards a live group-stop milestone update to the journal (append)
   void              LogGroupStop(string milestone, double groupSL)
     {
      if(m_journal != NULL)
        {
         m_journal.SetSessionID(m_sessionID);
         m_journal.LogTrailUpdate(milestone, groupSL);
        }
     }

   int               GetBasketCount(void) const { return m_basketCount; }
   double            GetPrimaryEntry(void) const { return m_primaryEntry; }
   double            GetBasketRRUnit(void) const { return m_basketRRUnit; }
   // v5.32: exposes the ladder cursor so the Trade Manager's T2 crossing trace
   // can report WHY a rung was refused. IsPyramidPending() answers "may this
   // rung fire?" but collapses every cause into one bool; the trace needs the
   // raw cursor to distinguish "not yet at 2" from "already advanced past 2".
   int               GetNextTranche(void) const { return m_nextTranche; }
   ENUM_TRADE_DIRECTION GetBasketDir(void) const { return m_basketDir; }
   bool              GetBasketTicket(int index, SPyramidTranche &out) const
     {
      if(index < 0 || index >= m_basketCount) return false;
      out = m_basket[index];
      return true;
     }

   bool              IsBasketFullyClosed(void) const
     {
      if(m_basketCount == 0) return false;
      for(int i = 0; i < m_basketCount; i++)
         if(PositionSelectByTicket(m_basket[i].ticket)) return false;
      return true;
     }

   void              ClearBasket(void)
     {
      ArrayResize(m_basket, 0, 3);
      m_basketCount = 0;
      m_nextTranche = 2;
      m_hasActiveTrade = false;
      m_activeDirection = DIR_NONE;
      // v5.33: the adoption flag must die with the basket. Left set, the
      // High Table auditor would keep standing down its magic-scoped phantom
      // test for a ticket that no longer exists, and IsTrackedTicketOpen()
      // would exempt a future EA leg from the magic check.
      m_adoptedManual = false;
      m_activeTrade.adoptedManual = false;
      // v5.32: the basket is over, so its persisted 1R is dead weight. Dropping
      // it here means the next cold start finds no record rather than a stale
      // one -- the ticket check in RestoreBasketR() is the second line of
      // defence, this is the first.
      ClearBasketR();
     }

   //+------------------------------------------------------------------+
   //| Closes EVERY open position belonging to this basket as a UNIT.   |
   //| MT5 hedging mode allows several concurrent positions on one      |
   //| symbol; closing only the primary ticket would leave the pyramid  |
   //| tranches ORPHANED (open, unmanaged, untracked). This routine:    |
   //|   1. closes every tracked tranche in m_basket[]                  |
   //|   2. closes any remaining broker position for this magic/symbol  |
   //|      that is NOT tracked (orphan sweep - survives restarts)      |
   //|   3. logs ONE aggregated exit record via LogClosedTrade()        |
   //|   4. clears basket state so the next cycle starts flat           |
   //| Set logExit=false when the caller needs the closing deals to     |
   //| settle in history first (e.g. a reversal, which re-logs the      |
   //| aggregate via SyncActiveTrade on a later tick).                  |
   //+------------------------------------------------------------------+
   void              CloseEntireBasket(string reason = "Force Close", bool logExit = true,
                                       ulong keepTicket = 0)
     {
      if(!m_hasActiveTrade && m_basketCount == 0 && CountMyPositions() == 0)
         return;

      int closed = 0;

      // ---- 1. Tracked tranches -------------------------------------
      for(int b = 0; b < m_basketCount; b++)
        {
         ulong bt = m_basket[b].ticket;
         if(bt <= 0) continue;
         if(keepTicket > 0 && bt == keepTicket) continue;
         if(!PositionSelectByTicket(bt)) continue;
         if(ClosePosition(bt)) closed++;
        }

      // ---- 2. Orphan sweep (untracked positions for this magic) -----
      // Guards against baskets rebuilt incomplete after a restart, or a
      // tranche that opened between the last Update() and this call.
      // keepTicket is spared so a stop-and-reverse can close the outgoing
      // basket WITHOUT also closing the incoming fill it detected.
      for(int p = PositionsTotal() - 1; p >= 0; p--)
        {
         if(!PositionGetTicket(p)) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         ulong orphan = (ulong)PositionGetInteger(POSITION_TICKET);
         if(orphan <= 0) continue;
         if(keepTicket > 0 && orphan == keepTicket) continue;
         if(ClosePosition(orphan)) closed++;
        }

      if(closed > 0)
         Print("[OrderManager] CloseEntireBasket: closed ", closed, " position(s) | ", reason);

      if(logExit)
        {
         // ---- 3. History settle delay ---------------------------------
         // FIX (v5.21): the close requests above have been DISPATCHED but
         // the terminal/broker has not necessarily written the outbound
         // DEAL_ENTRY_OUT deals into local history yet. LogClosedTrade()
         // scans that history immediately, so without this pause it can
         // find nothing, fall through to the single-trade path, and publish
         // an exit record with a zero price and zero profit/swap/commission.
         // A short settle window lets the closing deals land so the scan
         // captures the true exit price, swap, commission and net profit
         // for every tranche in the basket.
         //
         // NOTE: this runs inside OnTick() (reversal and DD-halt paths), so
         // the pause blocks the tick handler for its duration. It is applied
         // ONLY when logExit is requested, which keeps the delay off the
         // deferred-logging paths that do not need it.
         Sleep(250);

         // ---- 4. One aggregated journal record ------------------------
         // m_basket[] is still populated here on purpose: LogClosedTrade()
         // needs the tranche tickets to sum the per-tranche closing deals.
         LogClosedTrade(m_activeTrade, reason);
        }

      // ---- 5. Reset state so the next cycle starts flat --------------
      // m_hasActiveTrade is deliberately left intact when the caller will
      // still need the flat-transition log (reversal path); ClearBasket()
      // is deferred there so the tranche tickets survive for aggregation.
      if(logExit)
         ClearBasket();
      else
        {
         ArrayResize(m_basket, 0, 3);
         m_basketCount = 0;
         m_nextTranche = 2;
        }
     }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_ORDER_MANAGER__