//+------------------------------------------------------------------+
//|                                                   OttoDefines.mqh |
//|             OTTO EA v5.81 — 28-Pair Institutional Master Build |
//|                 Central Definitions / Enums / Input Parameters    |
//|         Exact MQL5 port of Pine Script "prop_guard_tester.pine"   |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.81"
#property description "OTTO v5.81 — Goat Funded Trader (GFT) Master Build (Wick1+Wick2 | Separation + ATR Sizing at creation | structural Break/Flip | NO execution-path vetoes, NO news/correlation filters)"

#ifndef __OTTO_DEFINES__
#define __OTTO_DEFINES__

//+------------------------------------------------------------------+
//| Enumerations                                                     |
//+------------------------------------------------------------------+

// Block polarity as DISCOVERED by the pattern engine. Support/Resistance here
// describe the ZONE, NOT the trade direction. The zone -> direction mapping is
// owned by COttoOrderManager::GetDirectionForBlock() and is UNCONDITIONAL in
// v5.52's PURE CONTINUATION engine: a RESISTANCE zone trades LONG (a breakout
// above it) and a SUPPORT zone trades SHORT (a breakdown below it), which is
// exactly what Pine's can_place gate states (long & resistance, short &
// support). The old two-phase fade -- the first fill a bounce, only a reclaimed
// zone the flip -- is retired, so this mapping reads no lifecycle counter at
// all. Enum VALUES are persisted and compared, never renumbered.
enum ENUM_BLOCK_TYPE
  {
   BLOCK_RESISTANCE = 0,  // Resistance zone (-> LONG: breakout above it)
   BLOCK_SUPPORT    = 1   // Support zone    (-> SHORT: breakdown below it)
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

// v4.70 veto reasons. v5.80 ARCHITECTURAL STRIP: only the two CREATION-TIME
// separation/sizing vetoes and the structural break/flip outcomes remain.
// Every post-arming execution-path veto (stale, front-run, near-miss,
// momentum, FVG) and the external correlation veto were REMOVED outright, so
// their enum members are gone too -- a phantom enum member with no writer is
// a lie in the journal. VETO_LIMIT_MISSED survives: it is not a filter, it is
// the record of an entry the venue could not hold (v5.72 strict-limit router).
enum ENUM_VETO_REASON
  {
   VETO_NONE          = 0,  // Not vetoed
   VETO_SIZING,             // h > (1.5*ATR) or h < (0.1*ATR)
   VETO_NO_SEPARATION,      // No candle strictly outside the zone between W1 & W2
   VETO_BROKEN,             // Broken without flip capability
   VETO_FLIPPED,            // Broken -> block flipped (S<->R)
   // v5.72 STRICT LIMIT-OR-NOTHING: the entry could not be placed as a resting
   // broker limit -- either price had already run through it (no legal
   // BUY_LIMIT / SELL_LIMIT side) or the venue refused the send. Deliberately
   // NOT VETO_BROKEN: that one says the ZONE failed (price closed back through
   // the far edge), while here the zone is intact and the ENTRY was missed.
   VETO_LIMIT_MISSED        // Limit entry missed (price through it / send refused)
  };

// Pine entry_style: "Midpoint" or "Front Edge"
enum ENUM_ENTRY_STYLE
  {
   ENTRY_MIDPOINT  = 0,     // entry = (top+bottom)/2
   ENTRY_FRONT_EDGE = 1     // TRUE DEEP FRONT-EDGE (v5.69). The entry is
                            //   measured OUT from the NEAR edge by
                            //   2.0*blockHeight + 1.5*ATR, on the OPPOSITE
                            //   side of the zone from the target:
                            //     Resistance LONG : b.bottom - gap
                            //     Support    SHORT: b.top    + gap
                            //   At arm time the close has only just cleared the
                            //   NEAR edge, so the entry sits below the market
                            //   for a LONG and above it for a SHORT: a legal
                            //   BUY_LIMIT / SELL_LIMIT, so the order RESTS AT
                            //   THE BROKER and fills on the pullback. That is
                            //   the ONLY route (v5.72): an entry that cannot
                            //   rest is vetoed VETO_LIMIT_MISSED, never chased
                            //   at market.
                            //   slDist is unchanged at blockHeight + 0.5*ATR, so
                            //   entry<->SL stays exactly slDist -- the offset is
                            //   carried by the entry alone, never a wider stop.
                            //   The target stays anchored on the FAR edge, so
                            //   the realized multiple is exactly InpMaxRR + 3.0
                            //   (7.0R on the shipped 4.0). The BREAK veto is
                            //   anchored on the physical FAR edge, NOT on this
                            //   entry (v5.69 decoupled them): anchored on the
                            //   entry it would fire on the arming close itself
                            //   and veto every setup it just armed.
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
   int               wick1Day;         // market-day of W1 (Pine w1_day) — provenance only since v5.80

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
   // v5.34's touches counter -- the two-phase lifecycle cursor, later kept as a
   // 'fill counter' -- is RETIRED here. Nothing read it once v5.52 removed the
   // phase branches, and the one-shot latch that actually refuses a duplicate
   // tranche is isTriggered / hasPlacedOrder (set on the fill, cleared only by
   // block deletion), so the field carried dead weight and a false comment.
   // v5.36: why this zone was promoted to a Phase 2 reversal — VESTIGIAL, no
   // longer written. The v5.52 pure-continuation engine has no second phase to
   // promote a block INTO, so nothing sets this field any more and nothing reads
   // it. Kept as a struct member so the SSniperBlock layout and the ZeroMemory()
   // reset at every block-creation site are unchanged; a fresh or reused block
   // therefore reads VETO_NONE.
   ENUM_VETO_REASON  conversionReason;

   // --- Order data (Pine: local_sl / local_tp / local_entry / rr_unit / trade_id) ---
   ulong             limitOrderTicket; // resting broker pending-order ticket (0 = none)
   bool              pendingOrderCancel; // OrderManager must cancel this ticket first
   string            tradeId;          // "OTTO_SUP_n" / "OTTO_RES_n"
   double            localEntry;       // entry price
   double            localSL;          // stop-loss price
   double            localTP;          // InpMaxRR projection (used ONLY for Front-Run Veto)
   double            rrUnit;           // = blockHeight + 0.5*ATR (Pine b.rr_unit)
   bool              priceAbortLogged; // v5.30: one-shot gate for the Invalid-Price abort
                                       //   (0 from ZeroMemory at every creation site)
   // v5.40: the per-setup journal session, minted when the setup's resting
   // broker limit is ACCEPTED, and reused at the fill and cancel paths so ONE
   // setup maps to ONE journal file. Empty
   // until arm time; ZeroMemory() clears it at every block-creation site. The
   // string member is safe because SSniperBlock is never raw-serialised (no
   // FileWriteStruct / FileReadStruct / memcpy / sizeof use anywhere in the tree).
   string            sessionId;

   // v5.80 ARCHITECTURAL STRIP: the v4.30 Near-Miss trackers
   // (minProxDist / anchorTime / anchorDay / anchorPrice / anchorId) are GONE.
   // They existed only to age a proximity anchor for the 6-market-day
   // near-miss veto, and that veto no longer exists, so the fields had no
   // reader left.
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
   // v5.33: TRUE when this record describes a MAGIC-0 position the operator
   // opened by hand and the EA adopted. It exists so the adoption path stays
   // attributable (the journal, the console and the High Table auditor can all
   // say WHY this basket looks unlike a normal one) and so the auditor's
   // magic-scoped phantom test can be told to stand down for this ticket. The
   // field is a plain bool: every EA-originated seed leaves it false, either
   // because ZeroMemory() cleared it with the rest of the struct or because
   // SeedActiveTradeFromBlock() sets it explicitly.
   bool              adoptedManual;
  };

// Pyramid tranche — a single scaling-in market position within a basket
struct SPyramidTranche
  {
   ulong             ticket;          // position ticket
   double            entry;           // tranche entry price
   double            size;            // tranche lot size
   int               tranche;        // 1 = initial, 2 = +1R add, 3 = +2R add, 4 = +3R add
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
input group "  [2] VETO SETTINGS — v5.81 (creation-time only)"
input group "══════════════════════════════════════════════════"
// v5.80 ARCHITECTURAL STRIP. Every POST-ARMING execution-path veto is gone:
// stale, front-run, near-miss, momentum and FVG no longer exist anywhere in
// the tree, so nothing reads the six inputs below. They are KEPT as DECLARED
// NO-OPS (the v5.52 InpRev* / InpShiftReversalEntry precedent) so an old .set
// file from a v5.7x build still loads instead of aborting on an unknown input.
// What still vetoes here is CREATION-TIME only: separation below, plus the
// ATR sizing window in group [3]. Structural VETO_BROKEN / VETO_FLIPPED live in
// the break detector and are NOT configuration.
input bool     InpUseFvgVeto        = false;   // RETIRED (v5.80) - no-op, kept for .set compatibility
input bool     InpUseMomVeto        = false;   // RETIRED (v5.80) - no-op, kept for .set compatibility
input bool     InpUseSeparationVeto = true;    // Separation veto (Enabled) - CREATION TIME
input bool     InpUseNearMissVeto   = false;   // RETIRED (v5.80) - no-op, kept for .set compatibility
input bool     InpUseFrontRunVeto   = false;   // RETIRED (v5.80) - no-op, kept for .set compatibility
// v5.31: the Front-Run veto's target projection was hardcoded to exactly
// 3 * slDist across four sites. It is now a single input so the projection
// that vetoes a trade and the take-profit the trade is actually sent with
// can never drift apart again -- the veto compares against the live TP once
// an order is resting, so a mismatch silently retires the veto.
// v5.80: the VETO is retired, but InpMaxRR is NOT. It remains the live
// take-profit multiple (PlaceOrArmOrder projects TP from the far edge with
// it), so it is a strategy parameter, not a filter knob.
input double   InpMaxRR             = 4.0;     // Take-profit projection (risk-reward multiple)
input bool     InpUseStaleVeto      = false;   // RETIRED (v5.80) - no-op, kept for .set compatibility
input int      InpStaleDays         = 45;      // RETIRED (v5.80) - no-op, kept for .set compatibility

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
input ENUM_ENTRY_STYLE InpEntryStyle = ENTRY_FRONT_EDGE; // Midpoint or Front Edge
input double   InpArmATR          = 0.0;      // Arming distance (ATR) — 0 = instant arm
// v5.34: Shift Reversal entry -- the Touch 2 outer-boundary geometry.
// v5.52: RETIRED. The pure-continuation engine has no Touch 2 (a continuation
// is a single breakout/breakdown entry), so no consumer remains in the tree.
// Kept as a DECLARED NO-OP so an old .set file still loads without an unknown-
// input error; it changes no behaviour.
input bool     InpShiftReversalEntry = false; // RETIRED (v5.52) - no-op, kept for .set compatibility
input bool     InpPyramidEnable   = true;     // Enable 4-tranche pyramiding (unified group SL)
// v5.29: Tranche 1 risk continues to flow through RiskPercent in group [6]
// (1.0%), so there is NO separate InpRiskT1Pct input. The v5.27 declaration of
// one was dead -- nothing in the tree ever read it -- and a second knob that
// silently changes no behaviour is worse than no knob at all.
// FIX (v5.61): the ladder is now a FLAT-HEAD pyramid. T1 and T2 EACH risk 1.0%
// of equity, then T3 halves to 0.5% and T4 quarters to 0.25%. The four rungs
// sum to 2.75% of equity with all four legs live:
//   T1  1.0%     RiskPercent (group [6])   at market
//   T2  1.0%     InpRiskT2Pct              at +1.0R  (InpPyramidT2RR)
//   T3  0.5%     InpRiskT3Pct              at +2.0R  (InpPyramidT3RR)
//   T4  0.25%    InpRiskT4Pct              at +3.0R  (InpPyramidT4RR)
// 2.75% is the SUMMED stacked exposure, and that sum is the intended design:
// each rung is only reached once the rungs beneath it are already wearing a
// milestone floor (T2 at friction breakeven, T3 at +1.0R, T4 at +2.0R), so the
// risk actually at stake is always a fraction of the nominal sum.
// Do NOT read 2.75% against SafetyMaxRiskPct (1.5%). That input is a PER-LEG
// abort threshold, not a basket-sum cap, and it is what
// CHighTableAuditor::AuditRiskExposure() actually enforces: it tracks
// peakRiskPct -- the worst SINGLE leg, measured from its own entry to the shared
// basket SL -- and never the sum. On this ladder the worst single leg is T1/T2
// at 1.0%, and both of those legs read as risk-free (lossDist <= 0) once the
// unified SL ratchets past their entries, so the peak sits at 1.0% against the
// 1.5% budget. The v5.29 text that stood here claimed the SUM was inside 1.5%;
// nothing in the tree ever enforced that, and the 2.75% sum is deliberate.
input double   InpRiskT2Pct       = 1.0;      // Tranche 2 risk % of equity (at +1.0R)
input double   InpRiskT3Pct       = 0.5;      // Tranche 3 risk % of equity (at +2.0R)
input double   InpRiskT4Pct       = 0.25;     // Tranche 4 risk % of equity (at +3.0R)
// v5.27: Tranche 2 is DECOUPLED from InpBreakEvenRR. The two milestones used
// to share a value (both 2.0), so lowering breakeven to 1.0 would have dragged
// the T2 scale-in down with it and fired it a full R early. T2 now has its own
// trigger.
// v5.29: all three scale-in triggers are independent inputs, so the pyramid
// ladder, the breakeven milestone and the trail activation can each be retuned
// without dragging the others. They must stay STRICTLY ASCENDING
// (T2 < T3 < T4): a rung whose trigger is never reached leaves m_nextTranche
// parked on it, and every later rung behind it is then unreachable.
input double   InpPyramidT2RR     = 1.0;      // Tranche 2 scale-in trigger R:R
input double   InpPyramidT3RR     = 2.0;      // Tranche 3 scale-in trigger R:R
input double   InpPyramidT4RR     = 3.0;      // Tranche 4 scale-in trigger R:R

input group "══════════════════════════════════════════════════"
input group "  [5] EXIT & TRAILING (Pine half_risk_rr / be_rr / trail_rr)"
input group "══════════════════════════════════════════════════"
input double   InpCutRiskRR   = 1.0;         // Cut Risk in Half at R:R
input double   InpBreakEvenRR = 1.0;         // Move to Cost-Covering Breakeven at R:R
input double   InpTrailStartRR = 1.0;        // Dynamic ATR trail activation R:R
// v5.29: InpLock3RRR is SUPERSEDED. Every live read of it in COttoTradeManager
// (both ATR trail gates, both single-ticket push guards and the SyncTradeState
// classification) was repointed to InpTrailStartRR, because the trail is a
// risk-management tool and must not wait for the same +3.0R step at which the
// pyramid reaches full size. The input is retained so saved .set files keep
// loading without an "unknown input" mismatch; MQL5 emits no warning for an
// unused input, so it costs nothing.
// FIX (v5.62): this input now gates the DYNAMIC ATR TRAIL ONLY -- the ATR
// formula on each branch plus the m_trailActivations tally. The milestone
// ladder (the +1.0R cost-covering breakeven floor, the +2.0R lock rung and the
// +3.0R lock rung) is applied to the basket UNCONDITIONALLY by its own block
// in Update(). It used to be nested inside this gate, which made
// InpTrailStartRR the de facto activation R:R of the whole ladder: raising it
// 1.0 -> 2.0 under that old layout would have darkened the +1.0R floor
// (contradicting the 1R ladder documented below) instead of merely delaying
// the trail. The gate and the ladder are now separate concerns.
// FIX (v5.63): the value is 1.0, so the +1.0R -> +2.0R ATR gap described above
// is CLOSED. The trail now arms on the SAME rung as the ladder's first floor
// (the +1.0R cost-covering breakeven), which is the intent: from +1.0R onward
// the basket wears BOTH the milestone floors and the ATR component, and the
// tighter of the two wins on any given tick. There is no longer an R of open
// profit carried by the floors alone, so a wide-ATR chop above +1.0R can pull
// the stop in as soon as the trail is armed -- that is the accepted trade for
// banking the +1.0R floor earlier.
// NOTE the one consequence in SyncTradeState: with InpTrailStartRR ==
// InpBreakEvenRR == 1.0 the two thresholds coincide, and because the state
// machine tests STEP_TRAILING first, the +1.0R rung now reports STEP_TRAILING
// rather than STEP_BREAKEVEN. That is deliberate -- at +1.0R the trail really
// is armed, so STEP_TRAILING is the truthful label. STEP_BREAKEVEN remains
// reachable only if InpTrailStartRR is raised above InpBreakEvenRR.
input double   InpLock3RRR      = 3.0;        // SUPERSEDED by InpTrailStartRR (retained for .set compatibility)
// v5.27 STEP PROFIT LOCK. When price reaches InpLockProfitRR, the stop is
// ratcheted forward to InpLockProfitTargetRR (expressed in R, measured from
// primaryEntry). This is a ONE-WAY ratchet: the lock can only ever tighten an
// existing stop, never loosen it. The Dynamic ATR trail keeps running in
// parallel and wins whenever it computes a tighter stop than the step lock.
// Set either input to 0.0 to disable the step lock entirely.
//
// MILESTONE LADDER: the floors now form an evenly-spaced 1R ladder, and each
// rung is strictly forward of the one below it --
//   +1.0R  breakeven (InpBreakEvenRR)  -> stop at primaryEntry +/- beOffset
//   +2.0R  lock rung 2                 -> stop at +1.0R (InpLockProfit2TargetRR)
//   +3.0R  lock rung 3                 -> stop at +2.0R (InpLockProfitTargetRR)
// The spacing is deliberate: a rung target that fell at or below the rung
// beneath it would be a no-op, because the lower ratchet already demands a
// tighter stop. Locking anything looser than the floor already in place would
// only ever move the stop BACKWARDS, and the one-way ratchet in
// ApplyUnifiedSL() would silently refuse it. Keep every trigger strictly above
// its own target.
// FIX (v5.62): every floor above is applied to the basket UNCONDITIONALLY
// (subject only to the shared per-tick tranche guard), each behind its own
// Inp*RR trigger. They are NOT gated on InpTrailStartRR -- that input arms the
// ATR trail alone. Reading the ladder and the trail as one milestone is what
// previously tied the +1.0R floor to the trail's activation R:R.
input double   InpLockProfit2RR       = 2.0; // Step profit lock rung 2 trigger (+2.0R)
input double   InpLockProfit2TargetRR = 1.0; // Step profit lock rung 2 target: lock SL at +1.0R
input double   InpLockProfitRR       = 3.0;  // Step profit lock trigger (+3.0R)
input double   InpLockProfitTargetRR = 2.0;  // Step profit lock target: lock SL at +2.0R

input group "══════════════════════════════════════════════════"
input group "  [6] RISK MANAGEMENT"
input group "══════════════════════════════════════════════════"
const double   RiskPercent        = 1.0;      // STRICTLY LOCKED - Terminal cannot override
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

input group "══════════════════════════════════════════════════"
input group "  [9] CURRENCY VECTOR & AFFINITY ENGINE — v5.81"
input group "══════════════════════════════════════════════════"
// FIX (v5.26): portfolio-wide consensus engine ported from the theoretical
// Base/Quote + Regional Affinity model. Additive to the per-chart
// GetCorrelationScore() ladder, which still drives the TS_Bias_* hive mind.
//
// v5.80 ARCHITECTURAL STRIP: this layer NO LONGER TOUCHES TRADING. The
// per-block IsTradeVetoed / IsConsensusOpposed guards and the
// CancelOpposingConsensusOrders sweep are deleted, and HiveMind() no longer
// deletes zones or publishes a bias GV. The vectors are computed and shown as
// DIAGNOSTICS ONLY -- no path from this module to an order exists any more.
input bool     InpEnableVectorEngine = true;   // Enable currency-vector consensus layer (diagnostics)
input double   InpConsensusVetoThreshold = 50.0; // RETIRED (v5.80) - no-op, kept for .set compatibility
input bool     InpUseExternalAnchors = true;   // Factor untraded DXY / XAUUSD macro anchors
input int      InpAnchorTF           = PERIOD_H1;  // Anchor candle timeframe (H1 default)
input bool     InpCancelOpposingPendings = false; // RETIRED (v5.80) - no-op, kept for .set compatibility

input group "══════════════════════════════════════════════════"
input group "  [10] HIGH TABLE AUDITOR — v5.81"
input group "══════════════════════════════════════════════════"
// Decoupled watchdog: audits live state on its OWN timer cadence rather
// than inside OnTick, so a halted / paused / tick-starved trade loop can
// never silence it. Evidence goes to Otto_HighTable_Audit.csv and alerts
// are emailed (outside the Strategy Tester, where SendMail is inert).
input bool     InpEnableHighTable       = true;  // Enable High Table watchdog
input int      InpHighTableAuditSeconds = 5;     // Audit cadence (seconds, >= 1)

input group "══════════════════════════════════════════════════"
input group "  [11] MANUAL TRADE ADOPTION — v5.81"
input group "══════════════════════════════════════════════════"
// Let this EA manage positions opened BY HAND on its own chart symbol.
// A manual position is identified as a MAGIC-0 position on m_symbol: the
// operator's terminal is the only source that assigns magic 0, so another
// EA (which always stamps its own non-zero id) can never be captured.
// Adoption is REFUSED unless the position already carries a stop-loss --
// the basket's 1R is derived from the live stop, so inventing one would
// fabricate the entire risk geometry (and every RR milestone with it).
// The High Table auditor's book-vs-tracked test is deliberately
// magic-scoped, so the adopted ticket is handed to it explicitly; see
// SetTrackedLegs() in CHighTableAuditor.mqh.
input bool     InpAdoptManualTrades     = true;  // Adopt magic-0 manual positions into the basket
input int      InpManualNoSLWarnMinutes = 5;     // Re-warn cadence for a manual leg with no SL (minutes)

//+------------------------------------------------------------------+
input group "══════════════════════════════════════════════════"
input group "  [12] RETIRED VETO REVERSAL CONVERSIONS — v5.81"
input group "══════════════════════════════════════════════════"
// v5.34 built five switches that let a vetoed Phase 1 setup be re-used as a
// Phase 2 REVERSAL instead of being discarded.
//
// v5.52 RETIRES that machinery. The pure CONTINUATION engine has no second
// phase to promote a block INTO, so COttoBlockManager::TryConvertToReversal()
// and SyncReversalCluster() are gone and a convertible veto is now simply a
// veto. The five inputs are KEPT as DECLARED NO-OPS rather than deleted, so an
// old .set file from a two-phase build still loads instead of aborting on an
// unknown input. Nothing in the tree reads them any longer.
input bool     InpRevFrontRun = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility
input bool     InpRevNearMiss = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility
input bool     InpRevStale    = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility
input bool     InpRevMom      = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility
input bool     InpRevFvg      = false;  // RETIRED (v5.52) - no-op, kept for .set compatibility
//| Global Constants                                                 |
//+------------------------------------------------------------------+
#define MAX_BLOCKS        50        // Max concurrent S/R blocks (memory safety)
#define MAX_ATR_HISTORY   32        // ATR history buffer for the 8-bar veto scan
#define OTTO_CURRENCY_COUNT 8       // 8-currency universe (USD..JPY)
#define OTTO_PAIR_COUNT     28      // 28 FX crosses derived from those 8

// Anti-overshoot headroom for the SIZER's "did volume-step rounding push this lot
// over budget?" correction, expressed as a plain multiple of the target risk
// money (1.05 => 5%). Deliberately TIGHT: the round-down floor in
// COttoRiskManager::NormalizeLotSize() / RiskPctLotSize() already guarantees the
// lot cannot exceed the target, so this branch is a belt-and-braces re-check that
// should never fire -- a WIDER factor would silently permit an over-risk lot.
//
// CAVEAT: the High Table auditor (CHighTableAuditor::AuditRiskExposure) applies a
// DIFFERENT, wider 1.50 factor to decide whether to ALERT. That is a different
// question (report vs permit) and is intentionally NOT unified here: this
// constant is the sizer's hard cap, the auditor's is its reporting tolerance.
#define OTTO_RISK_OVERSHOOT_MULT 1.05

//+------------------------------------------------------------------+
#endif  // __OTTO_DEFINES__
