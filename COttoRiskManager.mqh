//+------------------------------------------------------------------+
//|                                              COttoRiskManager.mqh |
//|                 MODULE — Risk Sizing (RiskPercent% of equity)    |
//|              OTTO EA — Institutional risk manager                |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.63"

#ifndef __OTTO_RISK_MANAGER__
#define __OTTO_RISK_MANAGER__

#include "OttoDefines.mqh"

//+------------------------------------------------------------------+
//| COttoRiskManager class                                           |
//| Calculates lot size for an exact risk amount from a dynamic      |
//| RiskPercent% of LIVE ACCOUNT EQUITY (Forex & Gold).              |
//| Strict 1% dynamic-equity model - no fixed-dollar override.       |
//| v5.49: BELOW-MIN SIZING ABORTS (returns 0.0 -> caller suppresses) |
//| instead of clamping up to a fixed min lot; shared overshoot factor.|
//+------------------------------------------------------------------+
class COttoRiskManager
  {
private:
   string            m_symbol;
   double            m_tickValue;           // Cached SYMBOL_TRADE_TICK_VALUE
   double            m_tickSize;            // Cached SYMBOL_TRADE_TICK_SIZE
   double            m_volumeStep;          // Cached SYMBOL_VOLUME_STEP
   double            m_volumeMin;           // Cached SYMBOL_VOLUME_MIN
   double            m_volumeMax;           // Cached SYMBOL_VOLUME_MAX
   int               m_digits;              // Cached SYMBOL_DIGITS

   double            m_lastRiskAmount;
   double            m_lastLotSize;
   int               m_tradesCalculated;

   //+------------------------------------------------------------------+
   //| Refreshes symbol properties from the market                      |
   //+------------------------------------------------------------------+
   bool              RefreshSymbolProperties(void)
     {
      m_tickValue  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      m_tickSize   = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      m_volumeStep = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
      m_volumeMin  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      m_volumeMax  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
      m_digits     = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);

      if(m_tickValue <= 0 || m_tickSize <= 0 || m_volumeStep <= 0)
        {
         Print("[RiskManager] ERROR: Invalid symbol properties for ", m_symbol,
               " | TickVal=", m_tickValue,
               " | TickSize=", m_tickSize,
               " | VolStep=", m_volumeStep);
         return false;
        }
      return true;
     }

   //+------------------------------------------------------------------+
   //| Resolves the account-currency risk capital for this trade       |
   //+------------------------------------------------------------------+
   double            GetRiskMoney(void)
     {
      // Strict dynamic-equity risk: RiskPercent% of LIVE ACCOUNT EQUITY
      // (universal, Forex & Gold). No fixed-dollar override.
      double accountEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      return accountEquity * (RiskPercent / 100.0);
     }

   //+------------------------------------------------------------------+
   //| Rounds a lot size to the nearest valid volume step              |
   //+------------------------------------------------------------------+
   double            NormalizeLotSize(double rawLot)
     {
      if(m_volumeStep <= 0)
         return rawLot;
      // v5.29: the 1e-8 epsilon absorbs binary floating-point representation
      // error before flooring. Without it, a rawLot that is mathematically an
      // exact multiple of the volume step can sit a few ULPs BELOW that
      // multiple, and MathFloor then drops a whole step -- a silent one-step
      // UNDER-size. Safe by construction: 1e-8 of a single volume step is far
      // below the broker's smallest increment, so it can never round a lot UP
      // past the risk budget, and the strict round-down guarantee is preserved.
      double steps = MathFloor(rawLot / m_volumeStep + 1e-8);   // strict round-DOWN, never overshoot risk
      double normalized = steps * m_volumeStep;
      normalized = MathMax(m_volumeMin, MathMin(m_volumeMax, normalized));
      return normalized;
     }


public:
   //+------------------------------------------------------------------+
   //| Public wrapper for NormalizeLotSize (round-down volume step)      |
   //+------------------------------------------------------------------+
   double            NormalizeLot(double rawLot)
     {
      return NormalizeLotSize(rawLot);
     }

   //+------------------------------------------------------------------+
   //| Exact lot size to risk `riskPct`% of LIVE EQUITY over the given  |
   //| SL distance (price). Uses MathFloor to volume step + min/max clamp|
   //| Returns lot (>=0). Returns 0.0 when below min lot (=> skip tier).|
   //+------------------------------------------------------------------+
   double            RiskPctLotSize(double riskPct, double slDistPrice)
     {
      if(!RefreshSymbolProperties()) return 0.0;
      if(riskPct <= 0.0 || slDistPrice <= 0.0) return 0.0;

      double accountEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      double riskMoney = accountEquity * (riskPct / 100.0);
      double slPoints  = slDistPrice / m_tickSize;
      if(slPoints <= 0.0 || m_tickValue <= 0.0) return 0.0;

      double rawLot = riskMoney / (slPoints * m_tickValue);
      // v5.29: epsilon-safe floor -- mirrors NormalizeLotSize() above. This path
      // sizes the pyramid rungs, and the smaller a rung gets (T4 = 0.0625%) the
      // larger the RELATIVE error a single dropped volume step represents, so
      // the epsilon matters most exactly where the rungs are smallest.
      double steps  = MathFloor(rawLot / m_volumeStep + 1e-8);
      double lot    = steps * m_volumeStep;

      // Hard safety: never exceed target risk even after clamping up. Headroom
      // factor is shared (OttoDefines) with CalculateLotSize()/FromPoints().
      double actualRiskMoney = lot * slPoints * m_tickValue;
      if(actualRiskMoney > (riskMoney * OTTO_RISK_OVERSHOOT_MULT) && lot > m_volumeMin)
         lot -= m_volumeStep;

      if(lot > m_volumeMax)
        {
         if(EnableLogging)
            Print("[RiskManager] Lot clamped to broker max: ", DoubleToString(m_volumeMax,2));
         lot = m_volumeMax;
        }
      if(lot < m_volumeMin)  return 0.0;   // below min -> cannot size this tier
      return lot;
     }


   //+------------------------------------------------------------------+
   //| Constructor                                                      |
   //+------------------------------------------------------------------+
                     COttoRiskManager(void)
     {
      m_symbol            = "";
      m_tickValue         = 0;
      m_tickSize          = 0;
      m_volumeStep        = 0;
      m_volumeMin         = 0;
      m_volumeMax         = 0;
      m_digits            = 0;
      m_lastRiskAmount    = 0;
      m_lastLotSize       = 0;
      m_tradesCalculated  = 0;
     }

   //+------------------------------------------------------------------+
   //| Destructor                                                       |
   //+------------------------------------------------------------------+
                    ~COttoRiskManager(void)
     {
     }

   //+------------------------------------------------------------------+
   //| Initialize — cache symbol properties                             |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol)
     {
      m_symbol = symbol;
      if(!RefreshSymbolProperties())
         return false;

      if(EnableLogging)
         Print("[RiskManager] Initialized for ", m_symbol,
               " | TickVal=", DoubleToString(m_tickValue, m_digits),
               " | TickSize=", DoubleToString(m_tickSize, m_digits),
               " | VolMin=", DoubleToString(m_volumeMin, 2),
               " | VolMax=", DoubleToString(m_volumeMax, 2),
               " | VolStep=", DoubleToString(m_volumeStep, 2),
               " | Risk: ", DoubleToString(RiskPercent,2), "% of acct");
      return true;
     }

   //+------------------------------------------------------------------+
   //| Core lot-size calculation. Returns exact lot to risk the target  |
   //| amount, or 0.0 to signal a prop-firm abort.                      |
   //+------------------------------------------------------------------+
   double            CalculateLotSize(double entryPrice, double stopLossPrice)
     {
      if(!RefreshSymbolProperties())
         return 0.0;   // v5.49: property fault -> ABORT (was m_volumeMin fixed lot)

      double riskMoney = GetRiskMoney();
      double slDistancePrice  = MathAbs(entryPrice - stopLossPrice);
      double slDistancePoints = slDistancePrice / m_tickSize;

      if(slDistancePoints <= 0)
        {
         Print("[RiskManager] ERROR: SL distance is zero or negative!");
         return 0.0;   // v5.49: unmeasurable risk -> ABORT (was m_volumeMin)
        }

      double tickValuePerLot = m_tickValue;
      if(tickValuePerLot <= 0)
        {
         double contractSize = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_CONTRACT_SIZE);
         if(contractSize > 0)
            tickValuePerLot = contractSize * m_tickSize;
         else
           {
            Print("[RiskManager] FATAL: Cannot determine tick value");
            return 0.0;   // v5.49: no risk model -> ABORT (was m_volumeMin)
           }
        }

      double rawLotSize = riskMoney / (slDistancePoints * tickValuePerLot);

      // v5.49: STRICT MIN-LOT ABORT (the legacy fixed-risk override this branch
      // carried). Below the broker minimum the target risk cannot be expressed,
      // so return 0.0 and let the caller ABORT -- exactly what RiskPctLotSize()
      // already does. The old `= m_volumeMin` clamp silently substituted a FIXED
      // lot, defeating the dynamic-equity model. Testing the RAW lot (pre
      // NormalizeLotSize min-clamp) is deliberate; no third epsilon-floor here.
      if(rawLotSize < m_volumeMin) return 0.0;

      double finalLotSize = NormalizeLotSize(rawLotSize);

      // --- STRICT ROUND-DOWN ANTI-OVERSHOOT (NEVER exceed risk) ---
      // Headroom factor is shared via OttoDefines so the sizer can never drift
      // from the value it was tuned against. NormalizeLotSize() already rounds
      // DOWN, so this branch is a belt-and-braces re-check.
      double actualRiskMoney = finalLotSize * slDistancePoints * tickValuePerLot;
      if(actualRiskMoney > (riskMoney * OTTO_RISK_OVERSHOOT_MULT) && finalLotSize > m_volumeMin)
         finalLotSize -= m_volumeStep;

      if(finalLotSize > m_volumeMax)
        {
         if(EnableLogging)
            Print("[RiskManager] Lot clamped to broker max: ", DoubleToString(m_volumeMax,2));
         finalLotSize = m_volumeMax;
        }
      if(finalLotSize < m_volumeMin) return 0.0;   // v5.49: never fall back to a fixed lot

      m_lastRiskAmount = riskMoney;
      m_lastLotSize    = finalLotSize;
      m_tradesCalculated++;

      return finalLotSize;
     }

   //+------------------------------------------------------------------+
   //| Calculates lot from a pre-computed SL distance in points        |
   //+------------------------------------------------------------------+
   double            CalculateLotSizeFromPoints(double slDistancePoints)
     {
      if(!RefreshSymbolProperties())
         return 0.0;   // v5.49: property fault -> ABORT (was m_volumeMin fixed lot)

      if(slDistancePoints <= 0.0)
        {
         Print("[RiskManager] ERROR: SL distance is zero or negative!");
         return 0.0;   // v5.49: unmeasurable risk -> ABORT (was m_volumeMin)
        }

      double riskMoney = GetRiskMoney();
      double tickValuePerLot = m_tickValue;
      if(tickValuePerLot <= 0)
        {
         double contractSize = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_CONTRACT_SIZE);
         tickValuePerLot = (contractSize > 0) ? contractSize * m_tickSize : 0.01;
        }

      double rawLotSize = riskMoney / (slDistancePoints * tickValuePerLot);

      // v5.49: STRICT MIN-LOT ABORT -- no fixed-lot fallback (see CalculateLotSize).
      if(rawLotSize < m_volumeMin) return 0.0;

      double finalLotSize = NormalizeLotSize(rawLotSize);

      // Never exceed the target risk (shared headroom, OttoDefines). The old form
      // carried no anti-overshoot at all; added here for one consistent contract.
      double actualRiskMoney = finalLotSize * slDistancePoints * tickValuePerLot;
      if(actualRiskMoney > (riskMoney * OTTO_RISK_OVERSHOOT_MULT) && finalLotSize > m_volumeMin)
         finalLotSize -= m_volumeStep;

      if(finalLotSize > m_volumeMax)
        {
         if(EnableLogging)
            Print("[RiskManager] Lot clamped to broker max: ", DoubleToString(m_volumeMax,2));
         finalLotSize = m_volumeMax;
        }
      if(finalLotSize < m_volumeMin) return 0.0;   // v5.49: never fall back to a fixed lot
      return finalLotSize;
     }


   //+------------------------------------------------------------------+
   //| Returns the tick value per standard lot (cached)                 |
   //+------------------------------------------------------------------+
   double            GetTickValuePerLot(void) const
     {
      return m_tickValue;
     }

   //+------------------------------------------------------------------+
   //| Returns the tick size (cached)                                   |
   //+------------------------------------------------------------------+
   double            GetTickSize(void) const
     {
      return m_tickSize;
     }

   //+------------------------------------------------------------------+
   //| Returns the symbol digits (cached)                               |
   //+------------------------------------------------------------------+
   int               GetDigits(void) const
     {
      return m_digits;
     }

   //+------------------------------------------------------------------+
   //| Returns minimum volume                                           |
   //+------------------------------------------------------------------+
   double            GetVolumeMin(void) const
     {
      return m_volumeMin;
     }

   //+------------------------------------------------------------------+
   //| Returns maximum volume                                           |
   //+------------------------------------------------------------------+
   double            GetVolumeMax(void) const
     {
      return m_volumeMax;
     }

   //+------------------------------------------------------------------+
   //| Returns volume step                                              |
   //+------------------------------------------------------------------+
   double            GetVolumeStep(void) const
     {
      return m_volumeStep;
     }

   //+------------------------------------------------------------------+
   //| Returns the last calculated risk amount                           |
   //+------------------------------------------------------------------+
   double            GetLastRiskAmount(void) const
     {
      return m_lastRiskAmount;
     }

   //+------------------------------------------------------------------+
   //| Returns the last calculated lot size                              |
   //+------------------------------------------------------------------+
   double            GetLastLotSize(void) const
     {
      return m_lastLotSize;
     }

   //+------------------------------------------------------------------+
   //| Returns total number of lot calculations performed               |
   //+------------------------------------------------------------------+
   int               GetTradesCalculated(void) const
     {
      return m_tradesCalculated;
     }

   //+------------------------------------------------------------------+
   //| Checks if there is sufficient margin for a given lot size        |
   //+------------------------------------------------------------------+
   bool              HasSufficientMargin(double lotSize)
     {
      double marginRequired;
      if(!OrderCalcMargin(ORDER_TYPE_BUY, m_symbol, lotSize,
                          SymbolInfoDouble(m_symbol, SYMBOL_ASK), marginRequired))
        {
         Print("[RiskManager] ERROR: OrderCalcMargin failed");
         return false;
        }
      double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      bool sufficient = (freeMargin > marginRequired * 1.1); // 10% buffer
      if(!sufficient && EnableLogging)
         Print("[RiskManager] Insufficient margin: Required=",
               DoubleToString(marginRequired, 2),
               " Free=", DoubleToString(freeMargin, 2));
      return sufficient;
     }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_RISK_MANAGER__
