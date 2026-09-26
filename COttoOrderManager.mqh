//+------------------------------------------------------------------+
//|                                              COttoOrderManager.mqh |
//|         MODULE 5 — Pine-gated Limit Placement & Order Lifecycle  |
//|              OTTO EA — exact Pine v4.70 execution port           |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.29"

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

   // --- Pending limit order tracking ---
   ulong                   m_pendingLimitTickets[];
   int                     m_pendingLimitCount;

   // --- Statistics ---
   int                     m_ordersPlaced;
   int                     m_ordersFilled;
   int                     m_ordersRejected;
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

   double                  GetAsk(void) { return SymbolInfoDouble(m_symbol, SYMBOL_ASK); }
   double                  GetBid(void) { return SymbolInfoDouble(m_symbol, SYMBOL_BID); }

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
               if(request.type == ORDER_TYPE_BUY || request.type == ORDER_TYPE_BUY_LIMIT ||
                  request.type == ORDER_TYPE_BUY_STOP)
                  request.price = GetAsk();
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
      return (block.type == BLOCK_SUPPORT) ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;
     }
   ENUM_TRADE_DIRECTION    GetDirectionForBlock(const SSniperBlock &block)
     {
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
      if(!m_hasActiveTrade)
         return true;
      if(m_activeDirection == DIR_LONG && block.type == BLOCK_RESISTANCE)
         return true;
      if(m_activeDirection == DIR_SHORT && block.type == BLOCK_SUPPORT)
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
      if(InpSimSentiment == SENT_BULLISH && block.type == BLOCK_SUPPORT)
         return true;
      if(InpSimSentiment == SENT_BEARISH && block.type == BLOCK_RESISTANCE)
         return true;
      return false;
     }

   //+------------------------------------------------------------------+
   //| Entry price per Pine entry_style (Midpoint / Front Edge)        |
   //+------------------------------------------------------------------+
   double                  CalcEntryPrice(const SSniperBlock &block)
     {
      if(InpEntryStyle == ENTRY_MIDPOINT)
         return block.midpoint;
      return (block.type == BLOCK_SUPPORT) ? block.top : block.bottom;
     }

   //+------------------------------------------------------------------+
   //| SL distance: b_height + 0.5*ATR (Pine sl_dist)                  |
   //+------------------------------------------------------------------+
   double                  CalcSLDistance(const SSniperBlock &block, double atr)
     {
      return block.blockHeight + (0.5 * atr);
     }


   //+------------------------------------------------------------------+
   //| PlaceLimitOrder — translates Pine strategy.entry(limit=...)     |
   //| into an MT5 pending SELL_LIMIT/BUY_LIMIT. NO take-profit is     |
   //| attached (exact mirror: exits are purely trail-based; localTP   |
   //| is stored on the block only for the Front-Run veto).            |
   //+------------------------------------------------------------------+
   bool                    PlaceLimitOrder(int blockIndex, SSniperBlock &block)
     {
      if(block.isVetoed || block.isTriggered || block.hasPlacedOrder)
         return false;

      // Duplicate prevention: triple-ticket verification
      if(IsBlockOrderAlive(block.limitOrderTicket))
         return true;
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
      double entryPrice = NormalizeDouble(CalcEntryPrice(block), _Digits);

      // v5.28 — LIVE PRICE VALIDATION, before the duplicate shield so a refused
      // price never sets hasPlacedOrder (the block stays armed for a later tick).
      // A limit resting on the wrong side of the market is rejected by the
      // server with TRADE_RETCODE_INVALID_PRICE, which previously burned a
      // dispatch attempt and left the block in an ambiguous state. The broker's
      // SYMBOL_TRADE_STOPS_LEVEL is honoured so the check matches server rules.
      double stopsLevel = (double)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL)
                          * SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double liveAsk = GetAsk();
      double liveBid = GetBid();

      if(block.type == BLOCK_SUPPORT)   // support block -> BUY LIMIT
        {
         if(entryPrice >= (liveBid - stopsLevel))
           {
            if(EnableLogging)
               Print("[OrderManager] ABORT BUY_LIMIT: entry ",
                     DoubleToString(entryPrice, _Digits), " >= bid ",
                     DoubleToString(liveBid, _Digits),
                     " (Invalid Price) — block left armed for retry");
            return false;
           }
        }
      else                              // resistance block -> SELL LIMIT
        {
         if(entryPrice <= (liveAsk + stopsLevel))
           {
            if(EnableLogging)
               Print("[OrderManager] ABORT SELL_LIMIT: entry ",
                     DoubleToString(entryPrice, _Digits), " <= ask ",
                     DoubleToString(liveAsk, _Digits),
                     " (Invalid Price) — block left armed for retry");
            return false;
           }
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
      double stopLoss   = (block.type == BLOCK_SUPPORT) ? entryPrice - slDist
                                                        : entryPrice + slDist;
      double takeProfit = (block.type == BLOCK_SUPPORT) ? entryPrice + 3 * slDist
                                                        : entryPrice - 3 * slDist;

      // Store local entry/SL/TP/rrUnit on the block (Pine b.local_*)
      block.localEntry = entryPrice;
      block.localSL    = stopLoss;
      block.localTP    = takeProfit;
      block.rrUnit     = slDist;

      // Broker-level stop validation
      bool isLong = (block.type == BLOCK_SUPPORT);
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
               if(MathAbs(openPrice - targetPrice) <= (tolerancePoints * point))
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
      bool isLong  = (targetBlock.type == BLOCK_SUPPORT);
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
      m_activeTrade.rrUnit       = m_activeTrade.initialSLDistance;
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
   bool              IsTrackedTicketOpen(void)
     {
      if(!m_hasActiveTrade || m_activeTrade.ticket <= 0)
         return false;
      if(!PositionSelectByTicket(m_activeTrade.ticket))
         return false;
      return (PositionGetInteger(POSITION_MAGIC) == MagicNumber &&
              PositionGetString(POSITION_SYMBOL) == m_symbol);
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
      if(m_hasActiveTrade && CountMyPositions() > 0)
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
   //| Main update — reversal completion + pending-fill detection      |
   //+------------------------------------------------------------------+
   void              Update(void)
     {
      CompleteReversal();
      CheckPendingOrderFills();
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
   int               GetReversalsExecuted(void) const { return m_reversalsExecuted; }
   int               GetRetryCount(void) const { return m_retryCount; }

   //+------------------------------------------------------------------+
   //| PYRAMID BASKET methods (unified group stop)                      |
   //+------------------------------------------------------------------+
   void              InitBasket(double entry, double rrUnit, ENUM_TRADE_DIRECTION dir, ulong ticket, double lot, double initSL, int blockSerial)
     {
      ArrayResize(m_basket, 0, 3);
      m_basketCount    = 0;
      m_primaryEntry   = entry;
      m_basketRRUnit   = rrUnit;
      m_basketDir      = dir;
      m_nextTranche    = 2;
      m_basketOpenTime = TimeCurrent();
      m_sessionSL      = initSL;   // unified ratchet starts at the initial SL
        // Reuse place-time session ID if already set on the journal (ONE file per setup)
        if(m_journal != NULL && m_journal.GetSessionID() != "")
           m_sessionID = m_journal.GetSessionID();
        else
          {
           MqlDateTime utm2; TimeToStruct(TimeCurrent(), utm2);
           string tsF = StringFormat("%04d%02d%02d-%02d%02d%02d", utm2.year, utm2.mon, utm2.day, utm2.hour, utm2.min, utm2.sec);
           m_sessionID = StringFormat("#OTTO-%s-%s-BLK%d", m_symbol, tsF, blockSerial);
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
      if(!InpPyramidEnable || m_basketCount == 0) return false;
      if(trancheToAdd != m_nextTranche) return false;

      // v5.29: exact descending risk tiers, one per rung:
      //   T2 = InpRiskT2Pct, T3 = InpRiskT3Pct, T4 = InpRiskT4Pct
      // Written as an explicit chain rather than a ternary so that adding a
      // fifth rung is a one-line change, and so an unrecognised tranche can
      // never silently inherit another rung's risk.
      double riskPct = 0.0;
      if(trancheToAdd == 2)      riskPct = InpRiskT2Pct;
      else if(trancheToAdd == 3) riskPct = InpRiskT3Pct;
      else if(trancheToAdd == 4) riskPct = InpRiskT4Pct;
      else                       return false;
      double slDist = m_basketRRUnit;
      if(slDist <= 0.0) return false;

      double lot = m_riskManager.RiskPctLotSize(riskPct, slDist);
      if(lot <= 0.0)   // below broker minimum lot -> skip this tranche
        {
         if(EnableLogging)
            Print("[Pyramid] Tranche ", trancheToAdd, " skipped: risk lot below min.");

         // Advance the tranche counter so we don't spam this every tick
         // v5.29: ladder advance 2 -> 3 -> 4 -> 0 (0 = ladder exhausted).
         m_nextTranche = (trancheToAdd == 2) ? 3 : ((trancheToAdd == 3) ? 4 : 0);
         return false;
        }
      if(!m_riskManager.HasSufficientMargin(lot)) return false;
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