//+------------------------------------------------------------------+
//|                                                       OttoEA.mq5 |
//|                    OTTO — Goat Funded Trader (GFT) Master Build    |
//|                    Pine Script Master Build Port (v5.33)            |
//|                                    Institutional / Real-Money    |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.33"
#property description "OTTO EA â€” Goat Funded Trader (GFT) Master Build"
#property description "Separation | Sizing | Front-Run | Near-Miss | Stale vetoes"
#property description "Modules: News Shield | Risk | Block Manager | Order Mgmt | Trail"
#property link      "https://github.com/vickygujjar17/OTTO-v5.27"

//+------------------------------------------------------------------+
//| Includes                                                          |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>
#include <Otto/OttoDefines.mqh>
#include <Otto/COttoNewsFilter.mqh>
#include <Otto/COttoRiskManager.mqh>
#include <Otto/COttoMarketStructure.mqh>
#include <Otto/COttoBlockManager.mqh>
#include <Otto/COttoOrderManager.mqh>
#include <Otto/COttoCorrelationFilter.mqh>
#include <Otto/COttoTradeManager.mqh>
#include <Otto/COttoJournal.mqh>
#include <Otto/CHighTableAuditor.mqh>

//+------------------------------------------------------------------+
//| Global Module Instances                                           |
//+------------------------------------------------------------------+
COttoNewsFilter        g_newsFilter;
COttoRiskManager       g_riskManager;
COttoMarketStructure   g_marketStructure;
COttoBlockManager      g_blockManager;
COttoOrderManager      g_orderManager;
COttoCorrelationFilter g_correlationFilter;
COttoTradeManager      g_tradeManager;
COttoJournal           g_journal;
CHighTableAuditor      g_highTable;

//+------------------------------------------------------------------+
//| Global State                                                      |
//+------------------------------------------------------------------+
string   g_symbol;
bool     g_isHedging        = false;
bool     g_initialized      = false;
int      g_tickCount        = 0;
datetime g_lastStatusLog    = 0;
int      g_fileHandle       = INVALID_HANDLE;
string   g_logFileName      = "";

// --- Human-readable trade journal ---
int      g_journalHandle    = INVALID_HANDLE;
string   g_journalFileName  = "";
long     g_lastPlacedCount  = 0;   // last-seen OrderManager GetOrdersPlaced()
long     g_lastFilledCount  = 0;   // last-seen GetOrdersFilled()
long     g_lastRejectedCount= 0;   // last-seen GetOrdersRejected()
long     g_lastHalfRisk     = 0;   // last-seen GetHalfRiskTriggers()
long     g_lastBE           = 0;   // last-seen GetBreakevenTriggers()
long     g_lastTrail        = 0;   // last-seen GetTrailActivations()
bool     g_lastHadTrade     = false;

// --- High Table fact ingress (v5.32) ---
// The auditor is deliberately decoupled: it holds no COtto* reference and
// reads terminal state only. Two facts it cannot derive from the terminal
// are therefore PUSHED to it every audit cycle - the live drawdown anchors
// (which are this file's private GlobalVariable-backed state) and the trade
// the order layer believes it is driving. The counters below are the
// push-side high-water marks used to turn the order manager's monotonic
// totals into the per-cycle DELTAS the auditor escalates on.
long     g_htPushedRejects    = 0;   // GetOrdersRejected() at last push
long     g_htPushedStopFails  = 0;   // GetStopModifyFailures() at last push

// --- Market-day counter (Pine ta.change(time("D")) mirror) ---
int      g_marketDay        = 0;
datetime g_lastDailyBarTime = 0;
datetime g_lastBarTime      = 0;

// --- Prop Firm Safety State ---
double   g_initialBalance      = 0;
double   g_dailyResetBalance   = 0;  // v5.28: re-anchored at 17:00 New York (DST-aware)
// FIX (v5.24): single equity high-water mark. Trailing total DD previously
// trailed the peak CLOSED balance in g_highWaterMarkBalance; it now trails
// peak EQUITY, so the 5% trailing DD shares this one basis.
// v5.28: the 1% floating rule is DECOUPLED from this HWM and now measures
// (balance - equity), i.e. live basket float. See the OnTick safety block.
double   g_equityHighWaterMark = 0;  // Tracks highest all-time EQUITY for Trailing Drawdown
datetime g_lastMidnightCheck   = 0;
bool     g_dailyDD_Paused      = false;
bool     g_totalDD_Halted      = false;
datetime g_dailyDD_ResumeTime  = 0;

//+------------------------------------------------------------------+
//| Prop-firm persistent state key helpers (v5.25)                    |
//|                                                                   |
//| The EA is deployed on a VPS and WILL be restarted mid-session.     |
//| Re-seeding the drawdown baselines from live account values on every |
//| init silently resets the daily 3% budget and re-bases the trailing |
//| limits downward, so the baselines are persisted in MT5 Global      |
//| Variables instead.                                                 |
//|                                                                   |
//| Keys are namespaced OTTO_* and suffixed with the account login, so  |
//| the ~28 EA instances and any other account on the same terminal    |
//| cannot collide. COttoCorrelationFilter owns the TS_Bias_* namespace |
//| and is deliberately not touched.                                   |
//|                                                                   |
//| NOTE: Globals live in the TERMINAL's shared namespace, not per     |
//| chart. Two terminals on one machine have separate stores; two charts|
//| of the same account in ONE terminal intentionally share these keys. |
//+------------------------------------------------------------------+
string OttoGvName(const string key)
  {
   return "OTTO_" + key + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
  }

//| Read a persisted double, falling back when the key is absent.     |
double OttoGvLoadDouble(const string key, const double fallback)
  {
   string name = OttoGvName(key);
   if(!GlobalVariableCheck(name))
      return fallback;
   double v = GlobalVariableGet(name);
   // A GV deleted by the terminal or never set reads back as 0.0; treat
   // that as absent rather than as a legitimate baseline.
   if(v == 0.0)
      return fallback;
   return v;
  }

//| Read a persisted boolean flag (stored as 0.0 / 1.0).              |
bool OttoGvLoadFlag(const string key, const bool fallback)
  {
   string name = OttoGvName(key);
   if(!GlobalVariableCheck(name))
      return fallback;
   return (GlobalVariableGet(name) >= 0.5);
  }

//| Write/persist a double. Returns false if the terminal refused it. |
//| NOTE: GlobalVariableSet returns datetime (last-access time), not bool. |
bool OttoGvStore(const string key, const double value)
  {
   if(!GlobalVariableSet(OttoGvName(key), value))
     {
      Print("[Safety] WARNING: GlobalVariableSet failed for ", OttoGvName(key));
      return false;
     }
   return true;
  }

//| Persist one boolean flag as 0.0 / 1.0.                            |
bool OttoGvStoreFlag(const string key, const bool value)
  {
   if(!GlobalVariableSet(OttoGvName(key), value ? 1.0 : 0.0))
     {
      Print("[Safety] WARNING: GlobalVariableSet failed for ", OttoGvName(key));
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Persist every prop-firm safety baseline (v5.25).                   |
//| Called whenever a baseline moves, so a restart always resumes from |
//| the true rather than the current account state.                    |
//+------------------------------------------------------------------+
void PersistSafetyState(void)
  {
   OttoGvStore("DailyReset", g_dailyResetBalance);
   OttoGvStore("HighWater",  g_equityHighWaterMark);
   OttoGvStore("LastMid",    (double)g_lastMidnightCheck);
   OttoGvStoreFlag("Paused", g_dailyDD_Paused);
   OttoGvStoreFlag("Halted", g_totalDD_Halted);
  }

//+------------------------------------------------------------------+
//| int OnInit(void)
//+------------------------------------------------------------------+
int OnInit(void)
  {
   g_symbol = _Symbol;

   Print("==============================================================");
   Print("  OTTO EA v5.33 — 28-Pair Institutional Master Build — INITIALIZING");
   Print("  Symbol: ", g_symbol, " | Magic: ", MagicNumber);
   Print("==============================================================");

   // --- Validate Hedging Account ---
   ENUM_ACCOUNT_MARGIN_MODE marginMode = (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(marginMode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("FATAL: OTTO requires HEDGING account mode!");
      Print("Current mode: ", EnumToString(marginMode));
      return INIT_FAILED;
     }
   g_isHedging = true;
   Print("[INIT] Account type: HEDGING");

   // --- GOLD DEPLOYMENT KILL-SWITCH ---------------------------------
   // OTTO is strictly forbidden from executing on Gold. The instrument's
   // volatility profile and tick-value characteristics invalidate the
   // Pine-derived separation/sizing model this build is calibrated on, and
   // its stop distances routinely breach broker minimums -- so a Gold chart
   // is refused outright rather than run in a degraded mode.
   // Correlation tracking for Gold positions held OUTSIDE this EA (manual
   // or other-magic) remains fully active: COttoCorrelationFilter reads the
   // broker position book independently of this guard.
   string symUpper = g_symbol;
   StringToUpper(symUpper);
   if(StringFind(symUpper, "XAU") >= 0 || StringFind(symUpper, "GOLD") >= 0)
     {
      Print("==============================================================");
      Print("  FATAL: OTTO is FORBIDDEN from executing on Gold charts.");
      Print("  Symbol detected: ", g_symbol);
      Print("  This EA must not trade XAU* / GOLD*." );
      Print("  Correlation tracking for externally-held Gold positions");
      Print("  remains active and is unaffected by this refusal.");
      Print("  Move the EA to a permitted instrument and re-initialise.");
      Print("==============================================================");
      return INIT_FAILED;
     }
   Print("[INIT] Gold kill-switch: ", g_symbol, " permitted");

   Print("[INIT] Balance: ", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2),
         " | Equity: ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
         " | Free: ", DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2));

   // --- News Filter ---
   if(!g_newsFilter.Initialize(g_symbol))
      Print("[INIT] WARNING: News Filter init failed â€” continuing");
   else
      Print("[INIT] News Filter OK");

   // --- Risk Manager ---
   if(!g_riskManager.Initialize(g_symbol))
     {
      Print("[INIT] FATAL: Risk Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Risk Manager OK");

   // --- Market Structure (pivots) ---
   if(!g_marketStructure.Initialize(g_symbol))
     {
      Print("[INIT] FATAL: Market Structure init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Market Structure OK");

   // --- Block Manager ---
   if(!g_blockManager.Initialize(g_symbol, &g_marketStructure))
     {
      Print("[INIT] FATAL: Block Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Block Manager OK");

   // --- Startup historical warm-up: populate blocks from past pivots ---
   g_blockManager.WarmUpHistory();
   Print("[INIT] Warm-up complete — blocks now on chart: ",
         g_blockManager.GetBlockCount());


   // --- Correlation Filter ---
   if(!g_correlationFilter.Initialize(g_symbol))
      Print("[INIT] WARNING: Correlation Filter init failed â€” continuing");
   else
      Print("[INIT] Correlation Filter OK");

   // --- Order Manager ---
   if(!g_orderManager.Initialize(g_symbol, &g_riskManager, &g_blockManager, &g_correlationFilter))
     {
      Print("[INIT] FATAL: Order Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Order Manager OK");

// --- Trade Journal (human-readable .txt lifecycle log) ---
   g_journal.Initialize(g_symbol, &g_blockManager);
   g_orderManager.SetJournal(&g_journal);


   // --- Trade Manager ---
   if(!g_tradeManager.Initialize(g_symbol, &g_riskManager, &g_orderManager, &g_blockManager))
     {
      Print("[INIT] FATAL: Trade Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Trade Manager OK");

   // --- High Table watchdog -----------------------------------------
   // A separate timer is registered for the audit so the watchdog is not
   // hostage to OnTick: if the trade loop halts (total-DD halt), pauses
   // (daily-DD pause) or simply receives no ticks, High Table still runs
   // and can still report the condition that stopped everything else.
   if(InpEnableHighTable)
     {
      g_highTable.Initialize(g_symbol, MagicNumber);
      int secs = (InpHighTableAuditSeconds < 1) ? 1 : InpHighTableAuditSeconds;
      EventSetTimer(secs);
      Print("[INIT] High Table OK | audit cadence: ", secs, "s");
     }
   else
      Print("[INIT] High Table DISABLED by input");

   // v5.32 — belt-and-braces rung-liveness warning.
   // The Trade Manager's Initialize() already logs whether the half-risk rung
   // is LIVE or INERT, but that line is gated on EnableLogging. An operator
   // running with logging OFF would otherwise never learn that InpCutRiskRR is
   // configured in a way that makes the -0.5R floor unreachable, so this one is
   // deliberately unconditional: a misconfigured rung is worth one line of
   // terminal output even in silent mode.
   if(InpCutRiskRR >= InpBreakEvenRR)
      Print("[INIT] NOTE: InpCutRiskRR (", DoubleToString(InpCutRiskRR,2),
            ") is not below InpBreakEvenRR (", DoubleToString(InpBreakEvenRR,2),
            ") -> the -0.5R half-risk rung is UNREACHABLE and will never be",
            " applied or journalled. Set InpCutRiskRR below InpBreakEvenRR to arm it.");

   // --- Prop firm safety state: PERSISTENT MEMORY (v5.25) ---
   // FIX (v5.25): these baselines are loaded from MT5 GlobalVariables and only
   // seeded when absent. Previously every init re-seeded them from the LIVE
   // account, so a VPS restart mid-session reset the 3% daily budget and
   // re-based the trailing limits downward -- "drawdown amnesia".
   //
   // The live trailing basis is g_equityHighWaterMark (single equity HWM, v5.24,
   // shared by the 5% trailing DD and the 1% floating rule). The directive that
   // requested this patch referred to a global named `g_highWaterMark`, which
   // does not exist in this build; persisting a separate new global under that
   // name would compile while leaving the REAL basis unpersisted, so the correct
   // target is used deliberately here.
   g_initialBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   double liveEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   // --- Daily reset anchor (3% daily DD basis) ---
   // v5.28: anchored on the true New York 17:00 rollover rather than the
   // broker's D1 candle open (see CheckDailyReset). Re-seeded when the stored
   // anchor names a DIFFERENT session than the current one: a prop firm can
   // RESET a challenge account on the same login, and carrying the old anchor
   // across that reset would apply a stale (possibly already-breached) budget
   // to a freshly-funded account.
   datetime utcNow   = TimeGMT();
   datetime nyAnchor = MostRecentNyRollover(utcNow);
   g_lastMidnightCheck = (datetime)OttoGvLoadDouble("LastMid", (double)nyAnchor);
   bool staleAnchor    = (nyAnchor != 0 && g_lastMidnightCheck != nyAnchor);
   double storedDaily  = OttoGvLoadDouble("DailyReset", 0);
   // v5.28: the budget basis is the ACCOUNT BALANCE, matching the live
   // (balance - equity) floating-loss measure. Seeding from equity here would
   // import open-basket float into the daily floor on every cold start.
   g_dailyResetBalance = (staleAnchor || storedDaily <= 0)
                         ? AccountInfoDouble(ACCOUNT_BALANCE)
                         : storedDaily;
   if(g_dailyResetBalance <= 0)
      g_dailyResetBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   // --- Trailing high-water mark (5% trailing DD + 1% floating basis) ---
   // A stored HWM BELOW current equity is simply stale history (the account has
   // since made new highs) and is superseded by live equity. A stored HWM far
   // ABOVE current equity is honoured as a genuine prior peak, because that is
   // precisely the state this fix exists to remember -- a restart while down.
   double storedHwm = OttoGvLoadDouble("HighWater", liveEquity);
   g_equityHighWaterMark = (storedHwm > liveEquity) ? storedHwm : liveEquity;

   // --- Halt / pause latches ---
   // g_totalDD_Halted is PERMANENT: a breached account must stay halted across a
   // restart, otherwise a VPS bounce would silently resume trading past a
   // hard limit. g_dailyDD_Paused clears at the session rollover (see
   // CheckDailyReset) and is recomputed here from the persisted anchor.
   g_totalDD_Halted = OttoGvLoadFlag("Halted", false);
   g_dailyDD_Paused = OttoGvLoadFlag("Paused", false) && !staleAnchor;
   g_dailyDD_ResumeTime = (g_dailyDD_Paused && g_lastMidnightCheck > 0)
                          ? g_lastMidnightCheck + 86400 : 0;

   // Persist the reconciled baselines so the store matches in-memory truth.
   PersistSafetyState();

   Print("[Safety] Persistence: ", staleAnchor ? "STALE ANCHOR RE-SEEDED" : "restored",
         " | DailyReset: ", DoubleToString(g_dailyResetBalance, 2),
         " (stored ", DoubleToString(storedDaily, 2), ")",
         " | Equity HWM: ", DoubleToString(g_equityHighWaterMark, 2),
         " (stored ", DoubleToString(storedHwm, 2), ")");
   Print("[Safety] Restored latches: Paused=", g_dailyDD_Paused ? "true" : "false",
         " Halted=", g_totalDD_Halted ? "true" : "false",
         " | LastMidnight: ", TimeToString(g_lastMidnightCheck, TIME_DATE|TIME_MINUTES));
   Print("[Safety] Init Balance: ", DoubleToString(g_initialBalance, 2),
         " | DailyDD: ", SafetyDailyDDLimit, "% | TotalDD(trailing): ", SafetyTotalDDLimit,
         "% | Floating: ", SafetyMaxFloatingLoss, "%");
   Print("[Safety] Daily reset anchor: ", TimeToString(g_lastMidnightCheck, TIME_DATE|TIME_MINUTES));
   // v5.28 — surface the computed New York clock so the host's UTC basis can be
   // verified at a glance on the VPS. If the third value is not -4/-5 hours the
   // host clock (not this code) is wrong.
   Print("[Safety] Time basis: UTC=", TimeToString(utcNow, TIME_DATE|TIME_MINUTES),
         " | New York=", TimeToString(UtcToNewYork(utcNow), TIME_DATE|TIME_MINUTES),
         " | offset=", IntegerToString(NewYorkUtcOffsetSeconds(utcNow) / 3600), "h",
         " | next rollover in ",
         IntegerToString((int)((MostRecentNyRollover(utcNow) + 86400 - utcNow) / 60)), " min");

   // --- Market-day counter init (mirrors ta.change(time("D"))) ---
   g_lastDailyBarTime = iTime(_Symbol, PERIOD_D1, 0);
   g_marketDay        = 0;
   g_lastBarTime      = iTime(_Symbol, PERIOD_CURRENT, 0);

   // --- Sync existing trade state ---
   g_tradeManager.SyncTradeState();

   // --- Setup log file ---
   if(EnableLogging)
     {
      g_logFileName = "Otto_" + g_symbol + "_" +
                      IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".csv";
      g_fileHandle  = FileOpen(g_logFileName, FILE_CSV | FILE_WRITE | FILE_SHARE_READ, ',');
      if(g_fileHandle != INVALID_HANDLE)
        {
         FileWrite(g_fileHandle, "Time", "Symbol", "Event", "Details");
         Print("[INIT] Log file: ", g_logFileName);
        }
      else
         Print("[INIT] WARNING: Could not create log file");
     }

// --- Setup human-readable trade journal ---
   if(EnableJournal)
     {
      g_journalFileName = "Otto_Journal_" + g_symbol + "_" +
                          IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".txt";
      g_journalHandle   = FileOpen(g_journalFileName, FILE_TXT | FILE_WRITE | FILE_SHARE_READ | FILE_ANSI);
      if(g_journalHandle != INVALID_HANDLE)
        {
         FileWriteString(g_journalHandle, "OTTO EA - Trade Journal\n");
         FileWriteString(g_journalHandle, "Symbol: " + g_symbol + " | Started: " + TimeToString(TimeCurrent()) + "\n");
         FileWriteString(g_journalHandle, "------------------------------------------------------------------\n");
         Print("[INIT] Journal file: ", g_journalFileName);
        }
      else
         Print("[INIT] WARNING: Could not create journal file");
     }
   g_initialized = true;

   Print("==============================================================");
   Print("  OTTO EA INITIALIZED SUCCESSFULLY");
   Print("  Risk: ", (InpFixedRiskUSD > 0 ? ("$" + DoubleToString(InpFixedRiskUSD,2))
                                          : (DoubleToString(RiskPercent,2) + "%")),
         " | DailyDD: ", SafetyDailyDDLimit, "% | TotalDD: ", SafetyTotalDDLimit, "%");
   Print("==============================================================");

   return INIT_SUCCEEDED;
  }


//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   Print("==============================================================");
   Print("  OTTO EA â€” DEINITIALIZING (reason ", reason, ")");

   Print("  --- News Filter ---");
   Print("  Blackouts: ", g_newsFilter.GetBlackoutCount());
   Print("  --- Block Manager ---");
   Print("  Created: ", g_blockManager.GetBlocksCreated(),
         " | Broken: ", g_blockManager.GetBlocksBroken(),
         " | Vetoed: ", g_blockManager.GetBlocksVetoed());
   Print("  --- Order Manager ---");
   Print("  Placed: ", g_orderManager.GetOrdersPlaced(),
         " | Filled: ", g_orderManager.GetOrdersFilled(),
         " | Rejected: ", g_orderManager.GetOrdersRejected());
   Print("  --- Trade Manager ---");
   Print("  HalfRisk: ", g_tradeManager.GetHalfRiskTriggers(),
         " | BE: ", g_tradeManager.GetBreakevenTriggers(),
         " | Trail: ", g_tradeManager.GetTrailActivations());

   // Cancel all pending orders
   int pending = g_orderManager.CountMyPending();
   if(pending > 0)
     {
      Print("  Cancelling ", pending, " pending orders...");
      g_orderManager.CancelAllPendingOrders();
     }
   // EXPERIMENT (experiment/reverse-sr): the virtual store has no broker
   // artifact, so it must be emptied explicitly on teardown. Without this a
   // stored trigger would survive a recompile and fire on a book the new
   // binary no longer tracks.
   g_orderManager.ClearVirtualStore();

   // --- High Table watchdog -----------------------------------------
   // The timer MUST be killed here: OnDeinit runs on every parameter
   // change, timeframe switch and recompile, and leaving the timer alive
   // would stack a new cadence on top of the old one each re-init.
   EventKillTimer();
   g_highTable.Deinit();

   // Close log file
   if(g_fileHandle != INVALID_HANDLE)
     {
      FileClose(g_fileHandle);
      g_fileHandle = INVALID_HANDLE;
     }
// Close journal file
   if(g_journalHandle != INVALID_HANDLE)
     {
      FileClose(g_journalHandle);
      g_journalHandle = INVALID_HANDLE;
     }
   g_journal.Close();   // COttoJournal detailed .txt journal

   g_initialized = false;
   Print("==============================================================");
  }

//+------------------------------------------------------------------+
//| US Eastern (New York) civil time, computed manually (v5.28).       |
//|                                                                   |
//| MQL5 has NO TimeDaylightSavings() helper, and TimeGMTOffset()      |
//| reports the offset of the PC's timezone -- not New York's. So the   |
//| DST rule is hard-coded: DST begins the 2nd Sunday of March at      |
//| 02:00 EST (07:00 UTC) and ends the 1st Sunday of November at       |
//| 02:00 EDT (06:00 UTC). These UTC instants are what make the calc    |
//| independent of the host's own DST state.                            |
//|                                                                   |
//| NOTE: in the Strategy Tester TimeGMT() is forced to server time,    |
//| so this is exact on a correctly-zoned live host only. OnInit logs   |
//| the computed value so it can be eyeballed on the VPS.               |
//+------------------------------------------------------------------+

//| True when the given UTC instant falls in US Eastern DST.          |
bool IsUsEasternDST(const datetime utcTime)
  {
   MqlDateTime dt;
   TimeToStruct(utcTime, dt);

   if(dt.mon < 3 || dt.mon > 11)
      return false;              // Jan/Feb/Dec: always EST
   if(dt.mon > 3 && dt.mon < 11)
      return true;               // Apr..Oct: always EDT

   // Day-of-week of the 1st of this month, to find the first Sunday.
   MqlDateTime first;
   ZeroMemory(first);
   first.year = dt.year;
   first.mon  = dt.mon;
   first.day  = 1;
   MqlDateTime probe;
   TimeToStruct(StructToTime(first), probe);
   int firstSunday = 1 + ((7 - probe.day_of_week) % 7);

   if(dt.mon == 3)
     {
      int secondSunday = firstSunday + 7;
      // 02:00 EST == 07:00 UTC on the transition date.
      return (dt.day > secondSunday)
             || (dt.day == secondSunday && dt.hour >= 7);
     }

   // November: DST is over from 02:00 EDT == 06:00 UTC on the 1st Sunday.
   return (dt.day < firstSunday)
          || (dt.day == firstSunday && dt.hour < 6);
  }

//| UTC offset of New York, in seconds, at the given UTC instant.     |
int NewYorkUtcOffsetSeconds(const datetime utcTime)
  {
   return IsUsEasternDST(utcTime) ? -14400 : -18000;   // EDT : EST
  }

//| Convert a UTC instant to New York civil time.                     |
datetime UtcToNewYork(const datetime utcTime)
  {
   return (datetime)((long)utcTime + NewYorkUtcOffsetSeconds(utcTime));
  }

//| Most recent 17:00 New York boundary, returned as a UTC instant.    |
//|                                                                   |
//| The offset is resolved AT the boundary rather than from `utcNow`:  |
//| within the 24h after a DST switch the two differ, and using the     |
//| current offset would land the anchor one hour off -- i.e. exactly   |
//| the class of bug this helper exists to remove. Two refinement       |
//| passes are enough (the offset can only flip once).                  |
datetime MostRecentNyRollover(const datetime utcNow)
  {
   datetime nyNow = UtcToNewYork(utcNow);

   MqlDateTime d;
   TimeToStruct(nyNow, d);
   d.hour = 0;
   d.min  = 0;
   d.sec  = 0;
   datetime nyBoundary = StructToTime(d) + 17 * 3600;   // 17:00 New York
   if(nyNow < nyBoundary)
      nyBoundary -= 86400;                              // roll back one day

   datetime utcBoundary = (datetime)((long)nyBoundary - NewYorkUtcOffsetSeconds(utcNow));
   for(int i = 0; i < 2; i++)
      utcBoundary = (datetime)((long)nyBoundary - NewYorkUtcOffsetSeconds(utcBoundary));
   return utcBoundary;
  }


//+------------------------------------------------------------------+
//| Checks the daily balance reset at the 17:00 New York close.       |
//+------------------------------------------------------------------+
void CheckDailyReset(void)
  {
   // v5.28 — TRUE New York 5:00 PM rollover, DST-aware.
   // FIX (v5.23) made this a stable timestamp rather than a per-tick one,
   // but it anchored on iTime(PERIOD_D1,0), which is the BROKER's daily
   // candle open. That equals 17:00 New York only if the server stamps D1
   // in US Eastern time; on a GMT+2/+3 server it lands at 16:00-17:00 New
   // York on a DIFFERENT DST schedule, so the daily budget was re-anchored
   // at the wrong instant (and moved by an hour twice a year).
   // The helper computes the boundary from UTC directly, so it no longer
   // depends on the broker's server timezone at all.
   datetime utcNow   = TimeGMT();
   datetime boundary = MostRecentNyRollover(utcNow);

   // A 0 boundary means the conversion produced nothing usable; skip rather
   // than trip a spurious reset (matches the v5.23 iTime==0 guard).
   if(boundary == 0 || boundary == g_lastMidnightCheck)
      return;

   g_dailyResetBalance  = AccountInfoDouble(ACCOUNT_BALANCE);
   g_lastMidnightCheck  = boundary;
   g_dailyDD_ResumeTime = 0;   // pause window ended with the session

   if(g_dailyDD_Paused)
     {
      g_dailyDD_Paused = false;
      if(EnableLogging)
         Print("[Safety] New day (5:00 PM New York) — daily DD pause LIFTED");
     }

   // FIX (v5.25): persist the new session baselines. Without this the
   // GlobalVariables would still hold yesterday's anchor, and a restart
   // after the rollover would restore a stale daily basis.
   // NOTE: g_totalDD_Halted is deliberately NOT cleared here. The 5% trailing
   // breach is permanent for the life of the account; only the daily pause is
   // a per-session state. Clearing the halt on rollover would let a breached
   // account resume trading at the next midnight.
   PersistSafetyState();

   if(EnableLogging)
      Print("[Safety] NY 17:00 rollover — daily DD budget re-anchored to ",
            DoubleToString(g_dailyResetBalance, 2));
  }

//+------------------------------------------------------------------+
//| New-bar detection â€” gates the block formation + veto funnel      |
//+------------------------------------------------------------------+
bool IsNewBar(void)
  {
   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(barTime == g_lastBarTime)
      return false;
   g_lastBarTime = barTime;
   return true;
  }

//+------------------------------------------------------------------+
//| Market-day counter â€” increments when the daily bar changes.      |
//| Weekends are naturally skipped (no daily bars on Sat/Sun). This  |
//| mirrors Pine ta.change(time("D")).                               |
//+------------------------------------------------------------------+
void UpdateMarketDay(void)
  {
   datetime dailyBar = iTime(_Symbol, PERIOD_D1, 0);
   if(dailyBar != g_lastDailyBarTime)
     {
      g_marketDay++;
      g_lastDailyBarTime = dailyBar;
     }
  }

//+------------------------------------------------------------------+
//| MAPPED direction of a zone for the portfolio bias vote.         |
//|                                                                 |
//| EXPERIMENT (experiment/reverse-sr): a Support / Resistance zone  |
//| is DISCOVERED polarity, NOT trade direction. HiveMind publishes  |
//| a LONG / SHORT consensus into the correlation matrix, and the    |
//| conflict tie-breaker does not merely prefer one of two opposing  |
//| zones -- it DELETES the losing polarity and vetoes its live      |
//| orders. Reading the raw zone here would therefore publish the    |
//| EXACT OPPOSITE bias under InpReverseSR while every entry is      |
//| mapped the other way, so the hive mind would systematically      |
//| veto exactly the setups this build now takes.                    |
//|                                                                 |
//| Byte-identical twin of COttoOrderManager::GetDirectionForBlock() |
//| and COttoBlockManager::BlockDirection() (both private) for the   |
//| support<->resistance mapping: with InpReverseSR = false a        |
//| Support zone maps to DIR_LONG and a Resistance zone to DIR_SHORT,|
//| so every branch below reduces to the original literal zone.      |
//+------------------------------------------------------------------+
ENUM_TRADE_DIRECTION MappedZoneDirection(const ENUM_BLOCK_TYPE type)
  {
   if(InpReverseSR)
      return (type == BLOCK_SUPPORT) ? DIR_SHORT : DIR_LONG;
   return (type == BLOCK_SUPPORT) ? DIR_LONG : DIR_SHORT;
  }

//+------------------------------------------------------------------+
//| Hive Mind â€” broadcast bias and resolve bidirectional conflicts   |
//+------------------------------------------------------------------+
void HiveMind(void)
  {
   if(g_dailyDD_Paused) return;

   SSniperBlock allBlocks[];
   int total = g_blockManager.GetAllBlocks(allBlocks);
   int supportVote = 0, resistanceVote = 0;   // raw zone polarity (diagnostics)
   int mappedVote  = 0;                       // EXPERIMENT: MAPPED direction digest
   for(int b = 0; b < total; b++)
     {
      if(allBlocks[b].isVetoed) continue;
      if(allBlocks[b].type == BLOCK_SUPPORT)
         supportVote++;
      else if(allBlocks[b].type == BLOCK_RESISTANCE)
         resistanceVote++;
      // EXPERIMENT (experiment/reverse-sr): each live zone votes with the
      // direction it would actually TRADE, not with the polarity it was
      // discovered as. With InpReverseSR = false this is the identity
      // mapping from block.type, so mappedVote == supportVote - resistanceVote
      // and the tree below reduces to the original tree exactly.
      mappedVote += (MappedZoneDirection(allBlocks[b].type) == DIR_LONG) ? 1 : -1;
     }

   int myBias = 0;
   // EXPERIMENT (experiment/reverse-sr): the tie-breaker DELETES the zone whose
   // MAPPED direction LOST the vote. Both DeleteBlockType arguments below carry
   // an identical "supportVote > 0 ? Long : Short" discriminator:
   //   in a two-polarity conflict (supportVote > 0) it picks polarity-vs-polarity,
   //   the SAME argument the original tree used;
   //   with only a resistance zone live (supportVote == 0) it picks the polled
   //   winner as a zone, so a resistance-only field cannot be deleted.
   // Log lines and myBias are unchanged:
   //   resolution == +1 -> peers Long  -> the SHORT-mapped zone is deleted
   //   resolution == -1 -> peers Short -> the LONG-mapped  zone is deleted
   // With InpReverseSR = false supportVote > 0 => support->DIR_LONG, so both
   // arguments reduce to the originals -- main behaviour unchanged.
   if(mappedVote > 0 && resistanceVote == 0)
      myBias = 1;
   else if(mappedVote < 0 && supportVote == 0)
      myBias = -1;
   else if(mappedVote != 0)
     {
      int resolution = g_correlationFilter.ResolveBidirectionalConflict();
      if(resolution == 1)
        {
         g_blockManager.DeleteBlockType(supportVote > 0 ? BLOCK_SUPPORT : BLOCK_RESISTANCE);
         myBias = 1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Long â†’ kept Support");
        }
      else if(resolution == -1)
        {
         g_blockManager.DeleteBlockType(supportVote > 0 ? BLOCK_RESISTANCE : BLOCK_SUPPORT);
         myBias = -1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Short â†’ kept Resistance");
        }
      else
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 0;
         if(EnableLogging) Print("[HiveMind] CONFLICT: no consensus â†’ deleted both");
        }
     }
   g_correlationFilter.BroadcastBias(myBias);
  }


//+------------------------------------------------------------------+
//| Expert tick function â€” Main Orchestration Loop                   |
//+------------------------------------------------------------------+
void OnTick(void)
  {
   if(!g_initialized) return;
   if(g_totalDD_Halted) return;

   g_tickCount++;
   CheckDailyReset();

   // ================================================================
   // STEP 0: PROP FIRM SAFETY CHECKS (highest priority)
   // ================================================================
   if(!g_dailyDD_Paused && !g_totalDD_Halted)
     {
      double equity         = AccountInfoDouble(ACCOUNT_EQUITY);
      double balance        = AccountInfoDouble(ACCOUNT_BALANCE);

      // FIX (v5.24): single equity high-water mark. The trailing total-DD limit
      // previously trailed the peak CLOSED balance; it now trails peak EQUITY,
      // matching GFT's all-time-equity trailing drawdown and sharing one basis
      // with the 1% floating rule. Only ratchets UP, never down.
      // FIX (v5.25): each new peak is persisted immediately, so a restart while
      // the account is down from its high resumes from the TRUE peak instead of
      // re-basing the trailing floor to the depressed live equity.
      if(equity > g_equityHighWaterMark)
        {
         g_equityHighWaterMark = equity;
         OttoGvStore("HighWater", g_equityHighWaterMark);
        }

      double dailyDD = (g_dailyResetBalance > 0) ? 100.0 * (g_dailyResetBalance - equity) / g_dailyResetBalance : 0;
      double totalDD = (g_equityHighWaterMark > 0) ? 100.0 * (g_equityHighWaterMark - equity) / g_equityHighWaterMark : 0;
      // v5.28: (balance - equity) IS the live basket float. The previous
      // peak-equity give-back measure fired whenever equity sat below its own
      // high-water mark -- including with NO losing position open -- and then
      // latched g_totalDD_Halted, killing the EA permanently on a normal tick.
      // A float measure is the correct reading for a "max floating loss" rule:
      // it is 0 whenever nothing is open, regardless of where the HWM sits.
      double floatingLoss = (balance > 0 && equity < balance)
                            ? 100.0 * (balance - equity) / balance
                            : 0.0;

      // v5.31: SMART TRIM before the full teardown. Closing only the
      // non-primary tranches that are already >= 70% of the way to their own
      // stop removes the legs that are nearest to hitting it anyway, without
      // dumping the whole basket at the worst possible price. The primary is
      // deliberately spared: it carries the basket's risk geometry and is the
      // leg the trail manager is tracking.
      // TrimHeavyLosers() returns true only when it could NOT act (no
      // non-primary leg was past the threshold), in which case the full sweep
      // below still runs and the 0.90% cap keeps being enforced every tick.
      // When a tranche is trimmed the primary is deliberately left running
      // under its own stop, so no full close follows -- closing everything
      // anyway would make the trim pointless.
      if(floatingLoss >= SafetyMaxFloatingLoss)
        {
         g_orderManager.CancelAllPendingOrders();
         // Close the WHOLE basket: hedging-mode pyramid tranches are separate
         // positions and must not survive the breach.
         if(g_orderManager.HasActiveTrade() || g_orderManager.CountOpenPositions() > 0)
           {
            bool needFullClose = true;
            if(InpTrimLoserStopPct > 0.0 && InpTrimLoserStopPct < 100.0)
               needFullClose = g_tradeManager.TrimHeavyLosers(InpTrimLoserStopPct);

            if(needFullClose)
               g_orderManager.CloseEntireBasket("1% Max Floating Loss Breach");
           }

         Print("==============================================================");
         Print("  [Safety] MAX FLOATING LOSS REACHED — SMART TRIM APPLIED");
         Print("  Balance: ", DoubleToString(balance, 2),
               " | Equity: ", DoubleToString(equity, 2),
               " | Floating Loss: ", DoubleToString(floatingLoss, 2), "% >= ",
               DoubleToString(SafetyMaxFloatingLoss, 2), "%");
         Print("==============================================================");
         // v5.28: return for THIS tick only. No halt latch is set, so scanning
         // resumes on the next tick. Returning here (rather than falling
         // through) prevents the same tick from immediately re-placing the
         // orders just cancelled; by the next tick equity ~= balance once the
         // basket is flat, so floatingLoss reads 0 and normal work continues.
         // v5.31 NOTE: a partial trim leaves a live position and therefore a
         // non-zero float, so this branch CAN legitimately re-enter on a later
         // tick. TrimHeavyLosers() is latched one-shot per breach, so the
         // re-entry neither re-trims nor re-logs; it returns false and the
         // trimmed basket keeps running under its own stop. The latch clears
         // as soon as the float is back under the cap (ClearTrimLatch below),
         // which re-arms the trim for the NEXT excursion.
         return;
        }

      // v5.31: float is back under the cap (or nothing was open at all), so
      // release the smart-trim one-shot latch. Without this the latch would
      // outlive its breach and the first breach of a LATER basket would skip
      // its trim. Mirrors the v5.30 priceAbortLogged reset on the clear path.
      g_tradeManager.ClearTrimLatch();

      // 3% Max Daily Drawdown (soft breach: pause new orders only)
      if(dailyDD >= SafetyDailyDDLimit)
        {
         g_dailyDD_Paused = true;
         g_dailyDD_ResumeTime = g_lastMidnightCheck + 86400;
         OttoGvStoreFlag("Paused", true);   // FIX (v5.25): survives a restart
         g_orderManager.CancelAllPendingOrders();
         if(EnableLogging)
            Print("[Safety] DAILY DRAWDOWN: ", DoubleToString(dailyDD, 2),
                  "% >= ", SafetyDailyDDLimit, "% — paused new orders");
        }

      // 5% Trailing Total Drawdown (hard breach: close all + halt)
      if(totalDD >= SafetyTotalDDLimit)
        {
         g_totalDD_Halted = true;
         OttoGvStoreFlag("Halted", true);   // FIX (v5.25): survives a restart
         g_orderManager.CancelAllPendingOrders();
         // Close the WHOLE basket, not just the primary ticket: hedging-mode
         // pyramid tranches are separate positions and must not survive the halt.
         if(g_orderManager.HasActiveTrade() || g_orderManager.CountOpenPositions() > 0)
            g_orderManager.CloseEntireBasket("Total Trailing DD Halt");
         Print("==============================================================");
         Print("  FATAL: TOTAL TRAILING DRAWDOWN LIMIT REACHED — EA PERMANENTLY HALTED");
         Print("  Equity HWM: ", DoubleToString(g_equityHighWaterMark, 2),
               " | Equity: ", DoubleToString(equity, 2));
         Print("  DD: ", DoubleToString(totalDD, 2), "% >= ", SafetyTotalDDLimit, "%");
         Print("==============================================================");
         // EXPERIMENT (experiment/reverse-sr): the halt must also disarm every
         // stored virtual trigger. The pending path is neutralised by the
         // delete above (and by the iTime<=haltTime guard); the virtual store
         // is protected only by this call, because a halt leaves the block
         // array untouched and MarkVirtualOrdersToMarket() is state-free.
         g_orderManager.ClearVirtualStore();
         return;
        }
     }

   // ================================================================
   // STEP 1: News Filter — DISABLED in v5.00
   // EnableNewsFilter defaults to false and the calendar update call is
   // bypassed entirely so no MQL5 economic-calendar queries are made.
   // ================================================================
   // g_newsFilter.Update();

   // ================================================================
   // STEP 2: NEW BAR â€” block formation + veto funnel (bar-close logic)
   // Mirrors calc_on_every_tick=false.
   // ================================================================
   if(IsNewBar())
     {
      UpdateMarketDay();               // advance the trading-day counter

      // Cancel orders on freshly-invalidated blocks BEFORE the funnel reaps them
      g_orderManager.CancelOrdersForInvalidBlocks();

      // Run the funnel (W1/W2 formation + all v4.70 vetoes)
      g_blockManager.Update(g_marketDay);

      // Cancel any orders declared invalid during this bar's funnel
      g_orderManager.CancelOrdersForInvalidBlocks();

      // Hive Mind (correlation tie-breaker)
      HiveMind();
     }


   // ================================================================
   // STEP 3: INTRA-BAR TARGET CHECKS (inside OnTick)
   // Front-Run 1:3 target + 6-day near-miss expiry, live.
   // ================================================================
   g_blockManager.CheckVetoesInTick(g_marketDay);
   g_orderManager.CancelOrdersForInvalidBlocks();

   // ================================================================
   // STEP 3b: v5.26 CURRENCY-VECTOR CONSENSUS REFRESH
   // Rebuilds the 8-currency vectors from the portfolio book and
   // re-normalizes the 28-pair consensus. Must run BEFORE order
   // placement so the consensus veto reads fresh state, and before the
   // opposing-pending sweep so cancellations use the same snapshot.
   // Cheap: one pass over positions + pendings, no history reads except
   // the two cached anchor symbols.
   // ================================================================
   g_correlationFilter.RefreshVectorState();

   // Strict outlier cancellation: drop our own pendings that now fight
   // the refreshed consensus.
   g_orderManager.CancelOpposingConsensusOrders();

   // ================================================================
   // STEP 4: ORDER PLACEMENT â€” Pine-gated limit orders for armed blocks
   // Gated further by news blackout + daily-DD pause.
   // ================================================================
   if(!g_newsFilter.IsInNewsBlackout() && !g_dailyDD_Paused)
      g_orderManager.PlaceOrdersForArmedBlocks();

   // ================================================================
   // STEP 5: MANAGE ACTIVE TRADES â€” dynamic trail (every tick)
   // ================================================================
   g_tradeManager.Update();

   // ================================================================
   // STEP 6: ORDER LIFECYCLE â€” fill detection + reversal completion
   // ================================================================
   g_orderManager.Update();

   // ================================================================
   // STEP 7: DIRECTION CONFLICT â€” cancel same-direction pending orders
   // ================================================================
   g_orderManager.ManageDirectionConflict();

// ================================================================
   // STEP 7B: TRADE JOURNAL — one-shot human-readable event log
   // ================================================================
   if(EnableJournal)
      JournalCheckEvents();
   // ================================================================
   // STEP 8: PERIODIC STATUS LOGGING
   // ================================================================
   if(EnableLogging && TimeCurrent() - g_lastStatusLog >= 3600)
     {
      LogStatus();
      g_lastStatusLog = TimeCurrent();
     }
  }

//+------------------------------------------------------------------+
//| Periodic status logging to console and file                      |
//+------------------------------------------------------------------+
void LogStatus(void)
  {
   string statusLine;
   StringConcatenate(statusLine,
                     "[STATUS] Day=", g_marketDay,
                     " | News: ", g_newsFilter.IsInNewsBlackout() ? "BLACKOUT" : "CLEAR",
                     " | Blocks: ", g_blockManager.GetBlockCount(),
                     " | ActiveTrade: ", g_orderManager.HasActiveTrade() ? "YES" : "NO",
                     " | Pending: ", g_orderManager.CountMyPending(),
                     " | ATR: ", DoubleToString(g_blockManager.GetATR(), _Digits),
                     " | DailyDD: ", g_dailyDD_Paused ? "PAUSED" : "OK",
                     " | TotalDD: ", g_totalDD_Halted ? "HALTED" : "OK");
   // EXPERIMENT (experiment/reverse-sr): appended only when the virtual path is
   // active, so a default deployment (InpVirtualOrders = false) logs exactly
   // the same line as before this branch existed.
   if(InpVirtualOrders)
     {
      string virtualSuffix = StringFormat(" | Virtual: %d", g_orderManager.GetVirtualCount());
      statusLine = statusLine + virtualSuffix;
     }
   Print(statusLine);
   if(g_fileHandle != INVALID_HANDLE)
     {
      FileWrite(g_fileHandle, TimeToString(TimeCurrent()), g_symbol, "STATUS", statusLine);
      FileFlush(g_fileHandle);
     }
  }

//+------------------------------------------------------------------+
//| Writes a line to the human-readable .txt trade journal            |
//+------------------------------------------------------------------+
void JournalWrite(string event, string details)
  {
   if(g_journalHandle == INVALID_HANDLE) return;
   string line = TimeToString(TimeCurrent()) + "  | " + event + "  | " + details;
   FileWriteString(g_journalHandle, line + "\n");
   FileFlush(g_journalHandle);
  }

//+------------------------------------------------------------------+
//| One-shot journal hooks for the EA's OnTick (counter-diff based)  |
//+------------------------------------------------------------------+
void JournalCheckEvents(void)
  {
   // --- NEW ORDER(S) PLACED ---
   long placed = g_orderManager.GetOrdersPlaced();
   if(placed > g_lastPlacedCount)
     {
      g_lastPlacedCount = placed;
      JournalWrite("ORDER PLACED", "pending count now " + IntegerToString((int)placed));
     }

   // --- FILLED ---
   long filled = g_orderManager.GetOrdersFilled();
   if(filled > g_lastFilledCount)
     {
      g_lastFilledCount = filled;
      JournalWrite("FILLED", "");
     }

   // --- REJECTED ---
   long rejected = g_orderManager.GetOrdersRejected();
   if(rejected > g_lastRejectedCount)
     {
      g_lastRejectedCount = rejected;
      JournalWrite("REJECTED", "");
     }

   // --- TRADE MANAGER STEP CHANGES ---
   int halfRisk = g_tradeManager.GetHalfRiskTriggers();
   if(halfRisk > g_lastHalfRisk)
     { g_lastHalfRisk = halfRisk; JournalWrite("SL -> HALF-RISK", "risk now -0.5R"); }

   int be = g_tradeManager.GetBreakevenTriggers();
   if(be > g_lastBE)
     { g_lastBE = be; JournalWrite("SL -> BREAKEVEN (COST-COVERING)", ""); }

   int trail = g_tradeManager.GetTrailActivations();
   if(trail > g_lastTrail)
     { g_lastTrail = trail; JournalWrite("SL -> DYNAMIC TRAIL ACTIVE", ""); }

   // --- TRADE CLOSED (active->flat transition) ---
   bool nowActive = g_orderManager.HasActiveTrade();
   if(g_lastHadTrade && !nowActive)
     JournalWrite("TRADE CLOSED", "");
   g_lastHadTrade = nowActive;
  }

//+------------------------------------------------------------------+
//| OnTrade â€” re-sync active trade state on trade events             |
//+------------------------------------------------------------------+
void OnTrade(void)
  {
   if(g_initialized)
      g_orderManager.SyncActiveTrade();
  }

//+------------------------------------------------------------------+
//| OnTimer - High Table watchdog cadence                             |
//|                                                                   |
//| Deliberately OUTSIDE the OnTick guard chain: OnTick returns early |
//| when the EA is halted or daily-paused, and those are precisely the |
//| states that most need to be reported. RunAudit() reads terminal    |
//| state only and holds no reference to any trade module, so it can   |
//| run while the trade loop is stopped without touching it.           |
//+------------------------------------------------------------------+
void OnTimer(void)
  {
   if(!g_initialized) return;
   if(!InpEnableHighTable) return;
   HighTablePushFacts();       // v5.32: hand over the facts only we can see
   g_highTable.RunAudit();
  }

//+------------------------------------------------------------------+
//| High Table fact ingress (v5.32)                                   |
//|                                                                   |
//| Called from OnTimer immediately before RunAudit(). It hands the    |
//| auditor the two facts it cannot read for itself:                  |
//|                                                                   |
//|   1. The live drawdown anchors. These are this file's private      |
//|      GlobalVariable-backed state; re-deriving them inside the      |
//|      auditor would mean duplicating the OTTO_<key>_<login> key     |
//|      format, i.e. a second source of truth for the trailing floor. |
//|                                                                   |
//|   2. The trade the order layer believes it is driving, so the      |
//|      auditor can test that belief against the real book.           |
//|                                                                   |
//| Both push points are read-only calls on the trade modules: the     |
//| auditor still holds no reference to them, which is what keeps the  |
//| decoupling contract intact (see CHighTableAuditor.mqh header).     |
//|                                                                   |
//| The counters are pushed as DELTAS because GetOrdersRejected() and  |
//| GetStopModifyFailures() are monotonic lifetime totals; the push    |
//| advances its own high-water mark so no failure is counted twice.   |
//+------------------------------------------------------------------+
void HighTablePushFacts(void)
  {
   // --- Drawdown anchors + permanent halt ---
   g_highTable.SetSafetyBaseline(g_dailyResetBalance, g_equityHighWaterMark,
                                 g_totalDD_Halted);

   // --- Order-reject delta since the previous push ---
   long rejects = g_orderManager.GetOrdersRejected();
   if(rejects > g_htPushedRejects)
     {
      g_highTable.NotifyOrderReject(0, "order placement", (int)(rejects - g_htPushedRejects));
      g_htPushedRejects = rejects;
     }

   // --- SL-modify failure delta since the previous push ---
   long stopFails = g_orderManager.GetStopModifyFailures();
   if(stopFails > g_htPushedStopFails)
     {
      g_highTable.NotifyStopModifyFailure(0, 0, (int)(stopFails - g_htPushedStopFails));
      g_htPushedStopFails = stopFails;
     }

   // --- The trade the order layer believes it is driving ---
   // The ticket is taken from the order layer rather than from the book on
   // purpose: the point of the check is to compare the manager's BELIEF
   // against reality, so both halves must come from independent sources.
   SActiveTrade active;
   ulong ticket  = 0;
   bool  adopted = false;
   if(g_orderManager.GetActiveTradeRef(active))
     {
      ticket = active.ticket;
      // v5.33: tell the auditor when the tracked primary is an ADOPTED
      // manual (magic-0) leg. The auditor's book-vs-tracked test is
      // intentionally magic-scoped -- CountBookLegs() and BookHasTicket()
      // both look only at positions carrying m_magic -- so an adopted
      // primary is invisible to it BY CONSTRUCTION. Without this flag the
      // auditor would latch its two-cycle "book/tracked state desync"
      // alert for the entire life of the adopted trade: a false CRITICAL
      // in the loudest channel the EA owns, which would in turn train the
      // operator to ignore it. The flag is read from the manager rather
      // than re-derived from POSITION_MAGIC here so the book is still read
      // in exactly one place.
      adopted = active.adoptedManual;
     }
   g_highTable.SetTrackedLegs(g_orderManager.CountOpenPositions(),
                              ticket, g_orderManager.HasActiveTrade(),
                              adopted);
  }

