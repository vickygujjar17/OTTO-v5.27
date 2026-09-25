//+------------------------------------------------------------------+
//|                                                   OttoDefines.mqh |
//|             OTTO EA v5.27 — 28-Pair Institutional Master Build |
//|                 Central Definitions / Enums / Input Parameters    |
//|         Exact MQL5 port of Pine Script "prop_guard_tester.pine"   |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.27"
#property description "OTTO v5.27 — Goat Funded Trader (GFT) Master Build (Wick1+Wick2 | Separation | Front-Run | Near-Miss | Stale Vetoes | Currency-Vector Consensus)"

#ifndef __OTTO_DEFINES__
#define __OTTO_DEFINES__

//+------------------------------------------------------------------+
//| Enumerations                                                     |
//+------------------------------------------------------------------+

// Block polarity. Support => Long, Resistance => Short (mirrors Pine is_support)
enum ENUM_BLOCK_TYPE
  {
   BLOCK_RESISTANCE = 0,  // Resistance block -> SELL LIMIT
   BLOCK_SUPPORT    = 1   // Support block    -> BUY  LIMIT
  };

enum ENUM_TRADE_DIRECTION
  {
   DIR_NONE  = 0,
   DIR_LONG  = 1,
   DIR_SHORT = -1
  };

// Trailing state machine — kept for statistics/logging. The actual SL
// decisions use the exact Pine v4.70 price formulas (see COttoTradeManager).
// The R:R values named in these comments are the INPUT DEFAULTS as of v5.27
// (InpCutRiskRR / InpBreakEvenRR / InpLockProfitRR); the enum members
// themselves are milestones, not fixed prices, and shift with those inputs.
// STEP_HALF_RISK is unreachable under v5.27 defaults because breakeven sits at
// 1.0R, on the same tick, and is the strictly tighter of the two.
enum ENUM_TRAIL_STEP
  {
   STEP_NONE       = 0,   // Initial SL — no trail yet
   STEP_HALF_RISK  = 1,   // InpCutRiskRR reached (1.0R) — SL tightened to 0.5R
   STEP_BREAKEVEN  = 2,   // InpBreakEvenRR reached (1.0R) — SL to cost-covering BE
   STEP_TRAILING   = 3    // InpLockProfitRR reached (3.0R) — step lock + ATR trail
  };

// v4.70 veto reasons — each maps to a block deletion path
enum ENUM_VETO_REASON
  {
   VETO_NONE          = 0,  // Not vetoed
   VETO_SIZING,             // h > (1.5*ATR) or h < (0.1*ATR)
   VETO_NO_SEPARATION,      // No candle strictly outside the zone between W1 & W2
   VETO_MOMENTUM,           // (high-low) > 3.5*ATR in the last 8 bars
   VETO_FVG,                // Fair-value-gap present in the last 8 bars
   VETO_STALE,              // 45 market days from W1 without an entry
   VETO_FRONTRUN,           // 1:3 target hit before entry
   VETO_NEARMISS,           // 6 market days in the proximity zone without entry
   VETO_BROKEN,             // Broken without flip capability
   VETO_FLIPPED,            // Broken -> block flipped (S<->R)
   // FIX (v5.26): portfolio-consensus veto. Set when a resting pending order (or
   // a newly forming block) opposes the global currency-vector consensus beyond
   // InpConsensusVetoThreshold. Distinct from the geographic block vetoes above,
   // which are per-chart; this one is portfolio-wide.
   VETO_CORRELATION         // Opposes the 8-currency vector consensus
  };

// Pine entry_style: "Midpoint" or "Front Edge"
enum ENUM_ENTRY_STYLE
  {
   ENTRY_MIDPOINT  = 0,     // entry = (top+bottom)/2
   ENTRY_FRONT_EDGE = 1     // support: top | resistance: bottom
  };

// Pine sim_sentiment simulation input
enum ENUM_SIM_SENTIMENT
  {
   SENT_IGNORE   = 0,
   SENT_BULLISH  = 1,
   SENT_BEARISH  = 2,
   SENT_NEUTRAL  = 3
  };

//+------------------------------------------------------------------+
//| Structures                                                       |
//+------------------------------------------------------------------+

// A pivot candle's full data (used for Wick 1 / Wick 2)
struct SFractalPeak
  {
   datetime time;          // Server time of the pivot candle
   double   price;         // High (resistance) or Low (support) of the candle
   double   openPrice;     // Open
   double   closePrice;    // Close
   double   highPrice;     // Full high
   double   lowPrice;      // Full low
   double   atrSnapshot;   // ATR at formation (Pine atr[rb])
   int      marketDay;     // Market-day counter when the candle formed
  };

// MODULE 4 — Wick1 + Wick2 block. Direct MQL5 translation of the Pine
// "SRBlock" UDT. Every v4.70 state member is ported verbatim.
struct SSniperBlock
  {
   // --- Zone geometry (Pine: top / bottom / start_bar / atr_snap) ---
   ENUM_BLOCK_TYPE   type;             // support <-> BLOCK_SUPPORT
   double            top;              // zone top (Pine b.top)
   double            bottom;           // zone bottom (Pine b.bottom)
   double            midpoint;         // (top+bottom)/2 — midpoint entry
   double            blockHeight;      // top-bottom
   double            atrSnapshot;      // ATR at W2 formation (Pine atr_snap)

   // --- Wicks (Wick 1 anchor, Wick 2 retest) ---
   SFractalPeak      wick1;            // W1 anchor candle
   SFractalPeak      wick2;            // W2 retest candle
   datetime          wick1Time;        // server time of W1 candle
   datetime          wick2Time;        // server time of W2 candle
   int               wick1Day;         // market-day of W1 (Pine w1_day) — stale veto

   // --- v4.70 state machine ---
   bool              isArmed;          // Pine b.is_armed   (close beyond zone + arm_atr)
   bool              isTriggered;      // Pine b.is_triggered (limit order filled)
   bool              isVetoed;         // Pine b.is_vetoed
   bool              isFlipped;        // Pine b.is_flipped
   bool              hasExited;        // Pine b.has_exited (price left the zone)
   bool              hasPlacedOrder;   // Pine b.has_placed_order
   ENUM_VETO_REASON  vetoReason;       // why this block was vetoed
   datetime          deleteOnBarTime;  // Pine b.delete_on_bar — remove on next bar
   datetime          creationTime;     // when the block was formed
   int               creationBarSerial;// Bars() serial at creation (diagnostics)
   int               serial;           // unique block id (for trade_id + visuals)

   // --- Order data (Pine: local_sl / local_tp / local_entry / rr_unit / trade_id) ---
   ulong             limitOrderTicket; // resting broker pending-order ticket (0 = none)
   bool              pendingOrderCancel; // OrderManager must cancel this ticket first
   string            tradeId;          // "OTTO_SUP_n" / "OTTO_RES_n"
   double            localEntry;       // entry price
   double            localSL;          // stop-loss price
   double            localTP;          // 1:3 projection (used ONLY for Front-Run Veto)
   double            rrUnit;           // = blockHeight + 0.5*ATR (Pine b.rr_unit)

   // --- v4.30 Near-Miss trackers (Pine: min_prox_dist / anchor_*) ---
   double            minProxDist;      // closest proximity wick distance (0 = unset)
   datetime          anchorTime;       // bar of the closest approach
   int               anchorDay;        // market-day of the anchor
   double            anchorPrice;      // closest wick price
   int               anchorId;         // unique anchor counter
  };

// Active-tracked position (mirrors Pine active_* variables)
struct SActiveTrade
  {
   ulong             ticket;
   ENUM_TRADE_DIRECTION direction;
   double            entryPrice;
   double            initialSL;
   double            initialSLDistance;
   double            rrUnit;            // Pine active_r_r_unit == initialSLDistance
   double            initialRiskAmount; // money risked at open
   double            currentTrailSL;    // Pine active_sl
   double            highestPriceSinceEntry;
   ENUM_TRAIL_STEP   trailStep;
   datetime          openTime;
   double            lotSize;
   int               sourceBlockSerial;
  };

// Pyramid tranche — a single scaling-in market position within a basket
struct SPyramidTranche
  {
   ulong             ticket;          // position ticket
   double            entry;           // tranche entry price
   double            size;            // tranche lot size
   int               tranche;        // 1 = initial, 2 = +1R add, 3 = +2R add
  };

//+------------------------------------------------------------------+
//| Extern Input Parameters                                          |
//| Defaults STRICTLY per the v4.70 requirement spec.               |
//+------------------------------------------------------------------+

input group "══════════════════════════════════════════════════"
input group "  [GENERAL]"
input group "══════════════════════════════════════════════════"
input int      MagicNumber       = 20240624;   // Unique EA Magic Number
input string   TradeComment      = "OTTO";     // Trade comment prefix
input bool     EnableLogging     = true;       // Detailed console/file logging
input bool     EnableJournal     = true;       // Write human-readable .txt trade journal

input group "══════════════════════════════════════════════════"
input group "  [1] PIVOT STRUCTURE (Pine: Left=8, Right=3)"
input group "══════════════════════════════════════════════════"
input int      InpLeftBars       = 8;          // Left bars (Wick 1 anchor) — REQUIRED 8
input int      InpRightBars      = 3;          // Right bars (Wick 1 anchor) — REQUIRED 3
input int      MinBlockDistance  = 3;          // Min bars between W1 and W2 for a block
input int      MaxBlockDistance  = 150;        // Max bars between W1 and W2 for a block
input int      LookbackBars      = 300;        // Bars the pivot engine may reference

input group "══════════════════════════════════════════════════"
input group "  [2] VETO SETTINGS (NOISE REDUCTION) — v4.70"
input group "══════════════════════════════════════════════════"
input bool     InpUseFvgVeto        = false;   // FVG veto (Disabled)
input bool     InpUseMomVeto        = false;   // Momentum veto (Disabled)
input bool     InpUseSeparationVeto = true;    // Separation veto (Enabled)
input bool     InpUseNearMissVeto   = true;    // Near Miss (6D) veto (Enabled)
input bool     InpUseFrontRunVeto   = true;    // Front-Run (1:3) veto (Enabled)
input bool     InpUseStaleVeto      = true;    // Stale (45D) veto (Enabled)
input int      InpStaleDays         = 45;     // Stale veto market-days (Pine stale_days)

input group "══════════════════════════════════════════════════"
input group "  [3] ATR / SIZING (Pine: 0.1x – 1.5x window)"
input group "══════════════════════════════════════════════════"
input int      ATRPeriod          = 14;       // ATR period (Pine ta.atr(14))
input double   ATRVetoMin         = 0.1;      // Zone height must be >= this × ATR
input double   ATRVetoMax         = 1.5;      // Zone height must be <= this × ATR
input double   ATRBufferBreak     = 0.5;      // Break buffer in ATR (Pine 0.5)
input double   InpTrailATRMultiplier = 1.5;   // Dynamic trail ATR multiplier (Pine trail_atr)

input group "══════════════════════════════════════════════════"
input group "  [4] EXECUTION SETTINGS (Pine entry/arm)"
input group "══════════════════════════════════════════════════"
input ENUM_ENTRY_STYLE InpEntryStyle = ENTRY_MIDPOINT;  // Midpoint or Front Edge
input double   InpArmATR          = 0.0;      // Arming distance (ATR) — 0 = instant arm
input double   InpFixedRiskUSD    = 0.0;      // Fixed $ risk/trade (0 = use RiskPercent%)
input bool     InpPyramidEnable   = true;     // Enable 3-tranche pyramiding (unified group SL)
input double   InpRiskT1Pct       = 0.25;     // Tranche 1 risk % of equity
input double   InpRiskT2Pct       = 0.12;     // Tranche 2 risk % of equity (at +2.0R)
input double   InpRiskT3Pct       = 0.06;     // Tranche 3 risk % of equity (at +3.0R)
// v5.27: Tranche 2 is DECOUPLED from InpBreakEvenRR. The two milestones used
// to share a value (both 2.0), so lowering breakeven to 1.0 would have dragged
// the T2 scale-in down with it and fired it a full R early. T2 now has its own
// trigger, defaulted to preserve the historic +2.0R behaviour exactly.
input double   InpPyramidT2RR     = 2.0;      // Tranche 2 scale-in trigger R:R

input group "══════════════════════════════════════════════════"
input group "  [5] EXIT & TRAILING (Pine half_risk_rr / be_rr / trail_rr)"
input group "══════════════════════════════════════════════════"
input double   InpCutRiskRR   = 1.0;         // Cut Risk in Half at R:R
input double   InpBreakEvenRR = 1.0;         // Move to Cost-Covering Breakeven at R:R
input double   InpTrailStartRR = 3.0;        // Tranche 3 / dynamic ATR trail activation R:R
input double   InpLock3RRR      = 3.0;        // Dynamic ATR trail activation (+3.0R, no fixed lock)
// v5.27 STEP PROFIT LOCK. When price reaches InpLockProfitRR, the stop is
// ratcheted forward to InpLockProfitTargetRR (expressed in R, measured from
// primaryEntry). This is a ONE-WAY ratchet: the lock can only ever tighten an
// existing stop, never loosen it. The Dynamic ATR trail keeps running in
// parallel and wins whenever it computes a tighter stop than the step lock.
// Set either input to 0.0 to disable the step lock entirely.
//
// MILESTONE GAP: breakeven (InpBreakEvenRR = 1.0) sits exactly 1R below the
// lock target (InpLockProfitTargetRR = 2.0), which sits exactly 1R below the
// lock trigger (InpLockProfitRR = 3.0). That spacing is deliberate: a lock
// target that fell at or below the cost-covering breakeven would be a no-op,
// because the existing breakeven ratchet already demands a stop at
// primaryEntry +/- beOffset (a few points of pure friction cost). Locking
// anything tighter than that would only ever LOOSEN the stop, and the
// ratchet in ApplyUnifiedSL() would silently refuse it.
input double   InpLockProfitRR       = 3.0;  // Step profit lock trigger (+3.0R)
input double   InpLockProfitTargetRR = 2.0;  // Step profit lock target: lock SL at +2.0R

input group "══════════════════════════════════════════════════"
input group "  [6] RISK MANAGEMENT"
input group "══════════════════════════════════════════════════"
input double   RiskPercent        = 0.25;     // Risk per trade (% of account)
input int      MaxSlippage        = 30;       // Max slippage in points
input int      MaxRetries         = 3;        // OrderSend retry attempts
input int      RetryDelayMs       = 500;      // Retry delay (milliseconds)
input int      MaxSpreadPoints    = 50;       // Max spread (points) for new entries

input group "══════════════════════════════════════════════════"
input group "  [7] NEWS SHIELD (real + simulated per Pine)"
input group "══════════════════════════════════════════════════"
input bool     EnableNewsFilter    = false;   // Real calendar blackout (disabled in v5.00)
input int      NewsMinutesBefore   = 5;       // Minutes before high-impact event
input int      NewsMinutesAfter    = 5;       // Minutes after high-impact event
input bool     InpSimNewsShield    = false;   // TRUE = absolute lockdown (Pine sim)
input bool     InpSimMacroVeto     = false;   // TRUE = macro correlation veto (Pine sim)
input ENUM_SIM_SENTIMENT InpSimSentiment = SENT_IGNORE;  // Simulated AI sentiment gate

input group "══════════════════════════════════════════════════"
input group "  [8] PROP FIRM SAFETY BUFFERS"
input group "══════════════════════════════════════════════════"
input double   SafetyMaxRiskPct    = 1.5;     // Hard abort if risk > this % of account
input double   SafetyDailyDDLimit  = 3.0;     // Soft breach: pause new orders at this %
input double   SafetyTotalDDLimit  = 5.0;     // Hard breach: close all + halt at this % (Trailing)
// FIX (v5.22): GFT 1% max FLOATING loss. Measured as a TRAILING retracement
// from the peak-equity high-water mark (not raw balance-vs-equity), so a
// routine intraday dip while equity is still below its own peak cannot trip
// a permanent halt. Breach => close all + halt.
input double   SafetyMaxFloatingLoss = 1.0;   // Hard breach: close all + halt if floating loss hits %

input group "══════════════════════════════════════════════════"
input group "  [9] CURRENCY VECTOR & AFFINITY ENGINE — v5.27"
input group "══════════════════════════════════════════════════"
// FIX (v5.26): portfolio-wide consensus engine ported from the theoretical
// Base/Quote + Regional Affinity model. Additive to the per-chart
// GetCorrelationScore() ladder, which still drives the TS_Bias_* hive mind.
input bool     InpEnableVectorEngine = true;   // Enable currency-vector consensus layer
input double   InpConsensusVetoThreshold = 50.0; // |consensus| % to veto/cancel opposing orders
input bool     InpUseExternalAnchors = true;   // Factor untraded DXY / XAUUSD macro anchors
input int      InpAnchorTF           = PERIOD_M15; // Anchor candle timeframe (M15 default)
input bool     InpCancelOpposingPendings = true; // Cancel resting pendings against consensus

//+------------------------------------------------------------------+
//| Global Constants                                                 |
//+------------------------------------------------------------------+
#define MAX_BLOCKS        50        // Max concurrent S/R blocks (memory safety)
#define MAX_ATR_HISTORY   32        // ATR history buffer for the 8-bar veto scan
#define OTTO_CURRENCY_COUNT 8       // 8-currency universe (USD..JPY)
#define OTTO_PAIR_COUNT     28      // 28 FX crosses derived from those 8

//+------------------------------------------------------------------+
#endif  // __OTTO_DEFINES__
