//+------------------------------------------------------------------+
//|                                        COttoCorrelationFilter.mqh |
//|              MODULE — Weighted Correlation Matrix (-3 to +3)      |
//|              28-Pair + Gold Institutional portfolio filter        |
//|              Suffix-safe (handles broker suffixes like .x)        |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.29"

#ifndef __OTTO_CORRELATION_FILTER__
#define __OTTO_CORRELATION_FILTER__

#include "OttoDefines.mqh"

//+------------------------------------------------------------------+
//| COttoCorrelationFilter class                                     |
//| Component-decomposition correlation engine for 28 FX pairs + gold |
//| Suffix-safe: broker suffixes (.x, m, _i, etc.) are sanitized.     |
//| Veto threshold: |score| >= 2.                                    |
//| Hive Mind tie-breaker: Total >= 2 or <= -2, else delete both.    |
//+------------------------------------------------------------------+
class COttoCorrelationFilter
  {
private:
   string            m_symbol;
   string            m_allSymbols[];

   // --- v5.26 currency-vector engine state ---
   string            m_currencies[OTTO_CURRENCY_COUNT]; // USD..JPY index map
   double            m_affinity[OTTO_CURRENCY_COUNT][OTTO_CURRENCY_COUNT];
   double            m_currencyValues[OTTO_CURRENCY_COUNT];
   bool              m_directlySet[OTTO_CURRENCY_COUNT];
   double            m_pairConsensus[OTTO_PAIR_COUNT]; // normalized -100..+100

   // --- v5.26 untraded macro anchors (cached: never re-scanned per tick) ---
   string            m_anchorDxy;       // broker's real DXY symbol ("" = absent)
   string            m_anchorXau;       // broker's real XAUUSD symbol ("" = absent)
   bool              m_anchorsResolved; // discovery runs once, then cached
   double            m_anchorUsdBias;   // last computed anchor contribution to USD

   //+------------------------------------------------------------------+
   //| v5.26 — ResolveAnchorSymbol                                  |
   //| Discovers the BROKER's real symbol string for an anchor root by |
   //| enumerating Market Watch and comparing CleanSymbol() roots.      |
   //| Ex: root "DXY" may actually be "DXY.x", "USDX" or "USIDX".       |
   //| Returns "" when the broker offers no such instrument — callers   |
   //| must treat that as "anchor inactive", not as an error.           |
   //+------------------------------------------------------------------+
   string            ResolveAnchorSymbol(const string root, const string altRoot)
     {
      int total = SymbolsTotal(false);   // false = Market Watch only (has history)
      for(int i = 0; i < total; i++)
        {
         string name = SymbolName(i, false);
         string clean = CleanSymbol(name);
         if(clean == root || (altRoot != "" && clean == altRoot))
            return name;
        }
      return "";
     }

   //+------------------------------------------------------------------+
   //| v5.26 — CandleDirection                                      |
   //| Short-term direction of an anchor: Bar[1] close vs Bar[2] close. |
   //| Returns +1 up, -1 down, 0 unknown/insufficient history.          |
   //+------------------------------------------------------------------+
   int               CandleDirection(const string symbol)
     {
      if(symbol == "") return 0;
      double c1 = iClose(symbol, (ENUM_TIMEFRAMES)InpAnchorTF, 1);
      double c2 = iClose(symbol, (ENUM_TIMEFRAMES)InpAnchorTF, 2);
      if(c1 <= 0 || c2 <= 0) return 0;   // symbol not selected / no history yet
      if(c1 > c2) return 1;
      if(c1 < c2) return -1;
      return 0;
     }

   //+------------------------------------------------------------------+
   //| v5.26 — CurrencyIndex                                        |
   //| Safe index lookup; returns -1 when the code is unknown.          |
   //+------------------------------------------------------------------+
   int               CurrencyIndex(const string code)
     {
      for(int i = 0; i < OTTO_CURRENCY_COUNT; i++)
         if(m_currencies[i] == code) return i;
      return -1;
     }

   //+------------------------------------------------------------------+
   //| v5.26 — SeedAffinities                                       |
   //| Ports the reference AFFINITIES table EXACTLY. Written mirrored so |
   //| the matrix is symmetric: one directed entry seeds BOTH cells, so  |
   //| the two directions can never disagree.                            |
   //| USD is deliberately left EMPTY — it is the independent global     |
   //| liquid counterweight baseline, per the reference model. Do not    |
   //| "fix" this by inventing USD affinities.                           |
   //+------------------------------------------------------------------+
   void              SeedAffinities(void)
     {
      for(int r = 0; r < OTTO_CURRENCY_COUNT; r++)
         for(int c = 0; c < OTTO_CURRENCY_COUNT; c++)
            m_affinity[r][c] = 0.0;

      Affinity("EUR", "GBP", 0.75);
      Affinity("EUR", "CHF", 0.80);
      Affinity("GBP", "CHF", 0.60);
      Affinity("CHF", "JPY", 0.40);
      Affinity("AUD", "NZD", 0.85);
      Affinity("AUD", "CAD", 0.60);
      Affinity("NZD", "CAD", 0.55);
      // USD: intentionally no entries.
     }

   //| Write one affinity pair into BOTH symmetric cells.                |
   void              Affinity(const string a, const string b, const double w)
     {
      int ia = CurrencyIndex(a);
      int ib = CurrencyIndex(b);
      if(ia < 0 || ib < 0) return;      // unknown code — ignore rather than crash
      m_affinity[ia][ib] = w;
      m_affinity[ib][ia] = w;
     }

   //+------------------------------------------------------------------+
   //| v5.26 — AddExposure                                          |
   //| Seeds a symbol's Base/Quote vectors from a direction.            |
   //| Long  => Base +1, Quote -1.   Short => Base -1, Quote +1.        |
   //+------------------------------------------------------------------+
   void              AddExposure(const string symbol, const int dir)
     {
      if(dir == 0) return;
      string clean = CleanSymbol(symbol);
      if(StringLen(clean) != 6) return;         // anchors / non-FX are not seeded
      int ib = CurrencyIndex(StringSubstr(clean, 0, 3));
      int iq = CurrencyIndex(StringSubstr(clean, 3, 3));
      if(ib < 0 || iq < 0) return;
      m_currencyValues[ib] += 1.0 * dir;
      m_currencyValues[iq] += -1.0 * dir;
      m_directlySet[ib] = true;
      m_directlySet[iq] = true;
     }

   //| movement = Base value - Quote value, for a 6-char FX root.        |
   double            PairMovement(const string cleanSymbol)
     {
      if(StringLen(cleanSymbol) != 6) return 0.0;
      int ib = CurrencyIndex(StringSubstr(cleanSymbol, 0, 3));
      int iq = CurrencyIndex(StringSubstr(cleanSymbol, 3, 3));
      if(ib < 0 || iq < 0) return 0.0;
      return m_currencyValues[ib] - m_currencyValues[iq];
     }

   //+------------------------------------------------------------------+
   //| v5.26 — BuildCurrencyVectors                                 |
   //| Reference steps 2-4: seed from the live portfolio, propagate      |
   //| first-order affinities for undirectly-set currencies, then apply  |
   //| the untraded macro anchors.                                       |
   //+------------------------------------------------------------------+
   void              BuildCurrencyVectors(void)
     {
      for(int i = 0; i < OTTO_CURRENCY_COUNT; i++)
        {
         m_currencyValues[i] = 0.0;
         m_directlySet[i]    = false;
        }

      // 1. Open positions carrying our magic.
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket <= 0) continue;
         if(!PositionSelectByTicket(ticket)) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         int dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
         AddExposure(PositionGetString(POSITION_SYMBOL), dir);
        }

      // 2. Resting pending orders carrying our magic.
      for(int i = OrdersTotal() - 1; i >= 0; i--)
        {
         ulong ticket = OrderGetTicket(i);
         if(ticket <= 0) continue;
         if(!OrderSelect(ticket)) continue;
         if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
         ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         int dir = 0;
         if(ot == ORDER_TYPE_BUY_LIMIT  || ot == ORDER_TYPE_BUY_STOP)  dir =  1;
         if(ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP) dir = -1;
         if(dir == 0) continue;
         AddExposure(OrderGetString(ORDER_SYMBOL), dir);
        }

      // 3. First-order affinity propagation for currencies NOT directly set.
      //    Mirrors the reference loop: sum affinity[c][set] * value[set].
      for(int c = 0; c < OTTO_CURRENCY_COUNT; c++)
        {
         if(m_directlySet[c]) continue;
         double influence = 0.0;
         for(int s = 0; s < OTTO_CURRENCY_COUNT; s++)
           {
            if(!m_directlySet[s]) continue;
            influence += m_affinity[c][s] * m_currencyValues[s];
           }
         m_currencyValues[c] = influence;
        }

      // 4. Untraded external macro anchors (DXY / XAUUSD).

      ApplyExternalAnchors();
     }

   //+------------------------------------------------------------------+
   //| v5.26 — ApplyExternalAnchors                                 |
   //| DXY UP   => USD +1.0        DXY DOWN  => USD -1.0                |
   //| GOLD UP  => USD -1.0 (inverse safe-haven pressure)                |
   //| These are ADDITIVE nudges applied AFTER propagation: neither      |
   //| instrument belongs to the 8-currency universe, so it cannot be    |
   //| seeded as a base/quote pair. The EA never trades them — they are  |
   //| read-only macro context. A missing symbol ("" or no history) is   |
   //| a silent no-op, never a logged error per tick.                    |
   //+------------------------------------------------------------------+
   void              ApplyExternalAnchors(void)
     {
      m_anchorUsdBias = 0.0;
      if(!InpUseExternalAnchors) return;

      int iUsd = CurrencyIndex("USD");
      if(iUsd < 0) return;

      int dxyDir = CandleDirection(m_anchorDxy);
      if(dxyDir != 0)   // dollar index bid => USD strength
        {
         m_anchorUsdBias += 1.0 * dxyDir;
         m_currencyValues[iUsd] += (1.0 * dxyDir);
         m_directlySet[iUsd] = true;
        }

      int xauDir = CandleDirection(m_anchorXau);
      if(xauDir == 1)   // gold bid => USD offered
        {
         m_anchorUsdBias += -1.0;
         m_currencyValues[iUsd] += -1.0;
         m_directlySet[iUsd] = true;
        }
     }


   //+------------------------------------------------------------------+
   //| v5.26 — ComputeConsensus                                     |
   //| Reference steps 4-5: movement = v[Base] - v[Quote], normalized    |
   //| against the largest absolute movement across the 28 pairs.        |
   //| The reference's maxAbsMovement==0 guard is preserved: a flat      |
   //| portfolio must yield all-zero consensus, never a divide-by-zero.  |
   //+------------------------------------------------------------------+
   void              ComputeConsensus(void)
     {
      double maxAbs = 0.0;
      for(int p = 0; p < OTTO_PAIR_COUNT; p++)
        {

         double mv = PairMovement(m_allSymbols[p]);
         if(MathAbs(mv) > maxAbs) maxAbs = MathAbs(mv);
         m_pairConsensus[p] = mv;      // store raw, normalize below
        }
      if(maxAbs == 0.0) maxAbs = 1.0;    // reference guard, verbatim
      for(int p = 0; p < OTTO_PAIR_COUNT; p++)
         m_pairConsensus[p] = (m_pairConsensus[p] / maxAbs) * 100.0;
     }

   //+------------------------------------------------------------------+
   //| CleanSymbol — strip broker suffix/prefix, return 6-char FX root  |
   //| Gold is normalized to "XAUUSD" so XAUUSD.x / GOLD.x all agree.    |
   //| NOTE: extracts A-Z only; intended for the FX + gold universe.     |
   //+------------------------------------------------------------------+
   string            CleanSymbol(string sym)
     {
      StringToUpper(sym);
      // Anchor/index roots first: these are NOT 6-char FX crosses and would
      // otherwise be mis-sliced by the generic extractor below.
      if(StringFind(sym, "XAUUSD") >= 0 || StringFind(sym, "GOLD") >= 0) return "XAUUSD";
      if(StringFind(sym, "DXY") >= 0 || StringFind(sym, "USDX") >= 0 ||
         StringFind(sym, "USIDX") >= 0 || StringFind(sym, "DOLLARINDEX") >= 0) return "DXY";

      string base = "";
      int len = StringLen(sym);
      for(int i = 0; i < len; i++)
        {
         ushort ch = StringGetCharacter(sym, i);
         if(ch >= 'A' && ch <= 'Z') base += ShortToString(ch);
         if(StringLen(base) == 6) break;
        }
      return base;
     }

   //+------------------------------------------------------------------+
   //| MODULE 1: GetCorrelationScore — dynamic Base/Quote decomposition |
   //| Score is built from currency exposure, not a hardcoded ladder.    |
   //|   +2 shared base / shared quote   (same-term, moves together)     |
   //|   -2 inverted base/quote          (opposite-term, moves against)  |
   //|   +1 macro regional bloc, applied ONLY when no direct exposure    |
   //| Returns 0 when the two symbols sanitize to the same root.         |
   //+------------------------------------------------------------------+
   int               GetCorrelationScore(string sym1, string sym2)
     {
      string s1 = CleanSymbol(sym1);
      string s2 = CleanSymbol(sym2);
      if(s1 == s2) return 0;

      // Handle Gold exception
      if(s1 == "XAUUSD" || s2 == "XAUUSD")
        {
         string other = (s1 == "XAUUSD") ? s2 : s1;
         if(StringFind(other, "USD") == 3) return 2;  // e.g. EURUSD
         if(StringFind(other, "USD") == 0) return -2; // e.g. USDJPY
         return 0;
        }

      string base1  = StringSubstr(s1, 0, 3);
      string quote1 = StringSubstr(s1, 3, 3);
      string base2  = StringSubstr(s2, 0, 3);
      string quote2 = StringSubstr(s2, 3, 3);

      int score = 0;

      // 1. Direct Exposure Engine
      if(base1 == base2)   score += 2;
      if(quote1 == quote2) score += 2;
      if(base1 == quote2)  score -= 2;
      if(quote1 == base2)  score -= 2;

      // 2. Macro Regional Bloc Engine (Only applies if no direct exposure exists)
      if(score == 0)
        {
         // Commodity Bloc: AUD, NZD, CAD
         bool isComm1 = (base1=="AUD" || base1=="NZD" || base1=="CAD" || quote1=="AUD" || quote1=="NZD" || quote1=="CAD");
         bool isComm2 = (base2=="AUD" || base2=="NZD" || base2=="CAD" || quote2=="AUD" || quote2=="NZD" || quote2=="CAD");
         if(isComm1 && isComm2) score += 1;

         // European Bloc: EUR, GBP, CHF
         bool isEuro1 = (base1=="EUR" || base1=="GBP" || base1=="CHF" || quote1=="EUR" || quote1=="GBP" || quote1=="CHF");
         bool isEuro2 = (base2=="EUR" || base2=="GBP" || base2=="CHF" || quote2=="EUR" || quote2=="GBP" || quote2=="CHF");
         if(isEuro1 && isEuro2) score += 1;
        }

      return score;
     }

   //+------------------------------------------------------------------+
   //| Scans all open positions for correlation conflicts               |
   //+------------------------------------------------------------------+
   bool              ScanPositionsForVeto(int proposedDir)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket <= 0) continue;
         if(!PositionSelectByTicket(ticket)) continue;
         string posSymbol = PositionGetString(POSITION_SYMBOL);
         long   posType   = PositionGetInteger(POSITION_TYPE);

         // FIX (v5.15): manual GOLD positions are factored into portfolio
         // correlation. Scoped narrowly: (1) only gold, and (2) only TRULY
         // manual tickets (magic == 0) - other EAs positions are left alone.
         // Note gold scores +/-2 against every USD pair, i.e. exactly the
         // veto threshold, so this deliberately widens the veto surface to
         // external gold. It is NOT applied to FX pairs.
         bool isExternalGold = (CleanSymbol(posSymbol) == "XAUUSD" &&
                                PositionGetInteger(POSITION_MAGIC) == 0);
         if(!isExternalGold && PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         int    posDir    = (posType == POSITION_TYPE_BUY) ? 1 : -1;
         int    score     = GetCorrelationScore(m_symbol, posSymbol);

         // |score| >= 2 is strong correlation — apply veto
         if(proposedDir == 1)  // Proposed LONG
           {
            if(posDir == 1 && score <= -2) return true;
            if(posDir == -1 && score >= 2) return true;
           }
         if(proposedDir == -1) // Proposed SHORT
           {
            if(posDir == -1 && score <= -2) return true;
            if(posDir == 1 && score >= 2) return true;
           }
        }
      return false;
     }


public:
   //+------------------------------------------------------------------+
   //| Constructor — populate the 28-pair + gold symbol universe        |
   //+------------------------------------------------------------------+
                     COttoCorrelationFilter(void)
     {
      m_symbol = "";
      ArrayResize(m_allSymbols, 29);
      m_allSymbols[0]  = "EURUSD";
      m_allSymbols[1]  = "GBPUSD";
      m_allSymbols[2]  = "AUDUSD";
      m_allSymbols[3]  = "NZDUSD";
      m_allSymbols[4]  = "USDCAD";
      m_allSymbols[5]  = "USDCHF";
      m_allSymbols[6]  = "USDJPY";
      m_allSymbols[7]  = "EURGBP";
      m_allSymbols[8]  = "EURAUD";
      m_allSymbols[9]  = "EURNZD";
      m_allSymbols[10] = "EURCAD";
      m_allSymbols[11] = "EURCHF";
      m_allSymbols[12] = "EURJPY";
      m_allSymbols[13] = "GBPAUD";
      m_allSymbols[14] = "GBPNZD";
      m_allSymbols[15] = "GBPCAD";
      m_allSymbols[16] = "GBPCHF";
      m_allSymbols[17] = "GBPJPY";
      m_allSymbols[18] = "AUDNZD";
      m_allSymbols[19] = "AUDCAD";
      m_allSymbols[20] = "AUDCHF";
      m_allSymbols[21] = "AUDJPY";
      m_allSymbols[22] = "NZDCAD";
      m_allSymbols[23] = "NZDCHF";
      m_allSymbols[24] = "NZDJPY";
      m_allSymbols[25] = "CADCHF";
      m_allSymbols[26] = "CADJPY";
      m_allSymbols[27] = "CHFJPY";
      m_allSymbols[28] = "XAUUSD";

      // v5.26 — 8-currency index map for the vector engine. Position in this
      // array is the index used by m_affinity[][], m_currencyValues[] and
      // m_directlySet[]. Do not reorder without updating SeedAffinities().
      m_currencies[0] = "USD";
      m_currencies[1] = "EUR";
      m_currencies[2] = "GBP";
      m_currencies[3] = "CHF";
      m_currencies[4] = "JPY";
      m_currencies[5] = "AUD";
      m_currencies[6] = "NZD";
      m_currencies[7] = "CAD";

      m_anchorDxy       = "";
      m_anchorXau       = "";
      m_anchorsResolved = false;
      m_anchorUsdBias   = 0.0;
      ArrayInitialize(m_pairConsensus, 0.0);
      SeedAffinities();
     }

                    ~COttoCorrelationFilter(void) { ArrayFree(m_allSymbols); }

   //+------------------------------------------------------------------+
   //| Initialize                                                       |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol)
     {
      m_symbol = symbol;

      // v5.26 — resolve the broker's real anchor strings ONCE, then cache.
      // Re-probing SymbolsTotal() every tick would add measurable lag across
      // 28 charts. A broker with no DXY/USDX simply leaves the anchor empty.
      m_anchorDxy       = ResolveAnchorSymbol("DXY", "");
      m_anchorXau       = ResolveAnchorSymbol("XAUUSD", "");
      m_anchorsResolved = true;

      if(EnableLogging)
        {
         Print("[Correlation] Initialized for ", m_symbol,
               " | Matrix: 28-pair + gold decomposition engine");
         Print("[Correlation] v5.26 vector engine: ",
               InpEnableVectorEngine ? "ENABLED" : "disabled",
               " | veto threshold = ", DoubleToString(InpConsensusVetoThreshold, 1), "%");
         Print("[Correlation] Anchors -> DXY: ",
               (m_anchorDxy == "" ? "NOT AVAILABLE" : m_anchorDxy),
               " | XAU: ", (m_anchorXau == "" ? "NOT AVAILABLE" : m_anchorXau));
        }
      return true;
     }

   //+------------------------------------------------------------------+
   //| v5.26 — RefreshVectorState                                   |
   //| Rebuilds the currency vectors and the 28-pair consensus array.    |
   //| Called once per evaluation cycle by the EA, NOT per tick.         |
   //+------------------------------------------------------------------+
   void              RefreshVectorState(void)
     {
      if(!InpEnableVectorEngine) return;
      BuildCurrencyVectors();
      ComputeConsensus();
     }

   //+------------------------------------------------------------------+
   //| v5.26 — GetPairConsensus                                     |
   //| Normalized -100..+100 consensus for any symbol string, broker      |
   //| suffix included. Returns 0 for unknown / non-FX roots.            |
   //+------------------------------------------------------------------+
   double            GetPairConsensus(const string symbol)
     {
      string clean = CleanSymbol(symbol);
      for(int p = 0; p < OTTO_PAIR_COUNT; p++)
         if(m_allSymbols[p] == clean) return m_pairConsensus[p];
      return 0.0;
     }

   //+------------------------------------------------------------------+
   //| v5.26 — IsConsensusOpposed                                   |
   //| True when trading `direction` on `symbol` fights the portfolio    |
   //| consensus beyond the configured threshold.                        |
   //| Long  opposes when consensus <= -threshold                        |
   //| Short opposes when consensus >= +threshold                        |
   //+------------------------------------------------------------------+
   bool              IsConsensusOpposed(const string symbol, ENUM_TRADE_DIRECTION direction)
     {
      if(!InpEnableVectorEngine) return false;
      double c = GetPairConsensus(symbol);
      double t = InpConsensusVetoThreshold;
      if(direction == DIR_LONG  && c <= -t) return true;
      if(direction == DIR_SHORT && c >=  t) return true;
      return false;
     }

   //| Raw currency vector read-back, for diagnostics.                  |
   double            DebugCurrencyValue(const string code)
     {
      int i = CurrencyIndex(code);
      return (i < 0) ? 0.0 : m_currencyValues[i];
     }

   //| Net macro-anchor contribution to USD (0.0 when anchors inactive). |
   double            DebugAnchorUsdBias(void) { return m_anchorUsdBias; }

   //| Resolved anchor symbols, for diagnostics.                         |
   string            DebugAnchorDxy(void) { return m_anchorDxy; }
   string            DebugAnchorXau(void) { return m_anchorXau; }

   //+------------------------------------------------------------------+
   //| MODULE 2: IsTradeVetoed — Weighted Portfolio Veto                |
   //+------------------------------------------------------------------+
   bool              IsTradeVetoed(ENUM_TRADE_DIRECTION proposedDirection)
     {
      if(proposedDirection != DIR_LONG && proposedDirection != DIR_SHORT)
         return false;
      int dir = (proposedDirection == DIR_LONG) ? 1 : -1;
      return ScanPositionsForVeto(dir);
     }

   //+------------------------------------------------------------------+
   //| BroadcastBias — write bias to MT5 Global Variable                |
   //| Key is built from the SANITIZED root so that EURUSD.x, EURUSDm   |
   //| and EURUSD all publish to the same "TS_Bias_EURUSD" slot.        |
   //+------------------------------------------------------------------+
   void              BroadcastBias(int bias)
     {
      string cleanSym = CleanSymbol(m_symbol);
      string varName = "TS_Bias_" + cleanSym;   // Key shared across EA instances
      if(bias == 0)
         GlobalVariableDel(varName);
      else
         GlobalVariableSet(varName, bias);
     }

   //+------------------------------------------------------------------+
   //| MODULE 3: GetWeightedBiasSum — matrix-weighted peer bias sum     |
   //| Reads "TS_Bias_<root>" slots published by peer EA instances.     |
   //| INVARIANT: m_allSymbols[] is stored pre-sanitized by the ctor,   |
   //| so array entries can be compared/used directly without cleaning. |
   //+------------------------------------------------------------------+
   int               GetWeightedBiasSum(void)
     {
      int totalBias = 0;
      string myClean = CleanSymbol(m_symbol);
      for(int i = 0; i < ArraySize(m_allSymbols); i++)
        {
         if(m_allSymbols[i] == myClean) continue;
         string varName = "TS_Bias_" + m_allSymbols[i];
         if(GlobalVariableCheck(varName))
           {
            int peerBias = (int)GlobalVariableGet(varName);
            int score = GetCorrelationScore(myClean, m_allSymbols[i]);
            totalBias += peerBias * score;
           }
        }
      return totalBias;
     }

   //+------------------------------------------------------------------+
   //| MODULE 3: ResolveBidirectionalConflict — weighted tie-breaker   |
   //+------------------------------------------------------------------+
   int               ResolveBidirectionalConflict(void)
     {
      int totalBias = GetWeightedBiasSum();
      if(EnableLogging)
         Print("[Correlation] Weighted Bias Sum for ", m_symbol, " = ", totalBias);

      if(totalBias >= 2) return 1;    // Strongly Long -> keep Support
      if(totalBias <= -2) return -1;  // Strongly Short -> keep Resistance
      return 0;                       // Mixed -> delete both
     }

   //+------------------------------------------------------------------+
   //| Public access to matrix score (for diagnostics)                 |
   //+------------------------------------------------------------------+
   int               DebugScore(string sym1, string sym2)
     {
      return GetCorrelationScore(sym1, sym2);
     }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_CORRELATION_FILTER__
