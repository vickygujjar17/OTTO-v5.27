//+------------------------------------------------------------------+
//|                                           CHighTableAuditor.mqh  |
//|        MODULE - "High Table" decoupled live-state watchdog       |
//|        Read-only audit + CSV evidence trail + email dispatcher    |
//|        Runs on its own timer cadence, independent of OnTick       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.41"

#ifndef __OTTO_HIGH_TABLE_AUDITOR__
#define __OTTO_HIGH_TABLE_AUDITOR__

#include "OttoDefines.mqh"

//+------------------------------------------------------------------+
//| Audit artifact + severity tokens                                  |
//|                                                                   |
//| The CSV is written into the terminal's MQL5\Files sandbox (the    |
//| same root COttoJournal writes its .txt journals to), so it is     |
//| readable from the terminal's data folder without any absolute     |
//| path handling.                                                    |
//+------------------------------------------------------------------+
#define HT_AUDIT_CSV     "Otto_HighTable_Audit.csv"
#define HT_SEV_INFO      "INFO"
#define HT_SEV_WARN      "WARN"
#define HT_SEV_CRITICAL  "CRITICAL"
// A reject "burst" is this many refused submissions WITHIN one audit cycle.
// A single refusal is ordinary broker/fill-mode traffic and is not worth an
// email; a cluster is the signature of a broken venue and is. Named rather
// than inlined so the threshold is greppable and tunable in one place.
#define HT_REJECT_BURST  3

//+------------------------------------------------------------------+
//| CHighTableAuditor                                                 |
//|                                                                   |
//| DECOUPLING CONTRACT                                               |
//|   MQL5 has no exceptions, so "survives a trade-logic exception"   |
//|   cannot be implemented as a try/catch. The equivalent guarantee  |
//|   used here is structural:                                        |
//|                                                                   |
//|     1. The auditor holds NO pointer to any trade module. It is    |
//|        read-only and pull-based: it reads account/position/order  |
//|        state straight from the terminal, so there is no shared    |
//|        object graph to corrupt and nothing to keep in sync.       |
//|     2. Its entry point (RunAudit) is driven by OnTimer, NOT by    |
//|        OnTick. An OnTick that early-returns (halted, paused, no   |
//|        ticks arriving) can no longer silence the watchdog, and a  |
//|        fault in the audit path cannot unwind basket state.        |
//|     3. Every write is confined to its own CSV handle, opened and  |
//|        closed per dispatch, so a failed write cannot leave a      |
//|        dangling handle behind for the trade path to trip over.    |
//+------------------------------------------------------------------+
class CHighTableAuditor
  {
private:
   string            m_symbol;
   int               m_magic;
   bool              m_ready;
   long              m_dispatched;      // alerts actually raised (post-latch)
   long              m_suppressed;      // repeats absorbed by a live latch
   datetime          m_lastAudit;       // last completed audit cycle

   //+------------------------------------------------------------------+
   //| One-shot incident latches.                                        |
   //|                                                                   |
   //| Spam prevention: OnTimer re-tests every condition on every cycle. |
   //| Without a latch a parked violation would re-email on every pass.  |
   //| Each latch is RAISED when its incident is first reported and is   |
   //| re-armed ONLY by ClearLatch(), which Part 2 calls once the        |
   //| condition is observed healthy again -- so one incident produces   |
   //| exactly one email, and the NEXT incident emails again.            |
   //+------------------------------------------------------------------+
   bool              m_alertSent_TrimFailure;        // smart trim could not act
   bool              m_alertSent_DailyDDBreach;      // daily DD soft breach
   bool              m_alertSent_TotalDDBreach;      // total DD hard breach
   bool              m_alertSent_FloatingLossCap;    // floating-loss cap hit
   bool              m_alertSent_Halt;               // permanent halt latched
   bool              m_alertSent_OrderRejectSpike;   // broker reject burst
   bool              m_alertSent_StopModifyFailure;  // SL modify rejected
   // v5.32 Part 2: this latch used to be declared OUTSIDE the class, after
   // the include guard's #endif. At file scope it was an ordinary global,
   // so the constructor's `m_alertSent_StateInconsistency = false;` was
   // initialising a different object from the one this class's methods
   // read -- the latch never re-armed and the incident was reported once
   // and then silenced for the lifetime of the terminal session. Moved
   // here, next to its peers, where the constructor assignment binds to
   // the member it names.
   bool              m_alertSent_StateInconsistency; // book vs tracked state

   //+------------------------------------------------------------------+
   //| PART 2 - pushed observation state.                                |
   //|                                                                   |
   //| The auditor is forbidden from including any trade module (it is   |
   //| structurally read-only), so it cannot poll COttoOrderManager's    |
   //| reject counters. Facts the trade path already knows are therefore |
   //| PUSHED in through NotifyOrderReject() / NotifyStopModifyFailure() |
   //| -- the same direction that OnInit pushes the symbol and magic.    |
   //| Each counter is monotonic; the auditor only ever compares it      |
   //| against its own last-seen value, so a burst is a DELTA and not a  |
   //| lifetime total.                                                   |
   //+------------------------------------------------------------------+
   long              m_rejectCount;          // current pushed reject total
   long              m_rejectSeenAtLastAudit;// total at the previous cycle
   long              m_stopModifyFailures;   // current pushed SL-modify fails
   long              m_stopModifySeen;       // total at the previous cycle

   //| Drawdown bases. PUSHED from otto.mq5 rather than read out of the    |
   //| terminal's GlobalVariables: the GV key format ("OTTO_<key>_<login>")|
   //| is private to otto.mq5, and re-implementing it here would be a      |
   //| second, silently drift-prone source of truth for the trailing floor.|
   //| otto.mq5 pushes the same live values it enforces, so the auditor's  |
   //| reading can never disagree with the EA's.                           |
   double            m_dailyResetBalance;    // pushed daily anchor
   double            m_equityHwm;            // pushed all-time equity HWM
   bool              m_totalHalted;          // pushed g_totalDD_Halted
   bool              m_haveBaseline;         // false until the first push

   //| The tracked PRIMARY ticket, pushed from the order layer. This is   |
   //| the one piece of the trade path's belief that the terminal cannot   |
   //| answer for it: the book says which legs EXIST, this says which      |
   //| ticket the trail/trim believe they are managing. If that ticket is  |
   //| gone from the book while the manager still thinks it is active, the |
   //| EA is driving a phantom -- and a phantom ticket means the risk       |
   //| geometry is being maintained against nothing.                       |
   //|                                                                     |
   //| Note this is NOT tautological with the book (the manager re-derives |
   //| nothing here; it reports its own state) and it is NOT the basket    |
   //| array length, which legitimately lags a close until ClearBasket     |
   //| and would therefore produce false positives during a normal exit.   |
   int               m_trackedLegs;          // legs the order layer believes are open
   ulong             m_trackedPrimary;       // ticket the trail believes it manages
   bool              m_trackedActive;        // m_hasActiveTrade, as pushed
   bool              m_trackedAdopted;       // primary is an ADOPTED magic-0 manual leg (v5.33)
   bool              m_stateSkewSeen;        // divergence seen once, awaiting confirm
   bool              m_adoptionSeen;         // adoption INFO already dispatched (v5.33)

   //+------------------------------------------------------------------+
   //| CSV field sanitizer.                                              |
   //|                                                                   |
   //| The audit CSV must stay machine-parseable, so the delimiter, the  |
   //| quote character and any raw CR/LF are folded to safe substitutes  |
   //| before a field is written. Non-ASCII bytes are additionally       |
   //| mapped to '_': this repository has previously shipped mojibake    |
   //| (see _tools\repair_mojibake.py), and an ASCII-only evidence file  |
   //| cannot be corrupted by a future encoding regression.              |
   //+------------------------------------------------------------------+
   string            Sanitize(string s)
     {
      string out = "";
      int len = StringLen(s);
      for(int i = 0; i < len; i++)
        {
         ushort ch = (ushort)StringGetCharacter(s, i);
         if(ch == 44 || ch == 59)              out += ShortToString((ushort)59);   // ',' ';' -> ';'
         else if(ch == 34)                     out += ShortToString((ushort)39);   // '"'     -> '''
         else if(ch == 13 || ch == 10)         out += ShortToString((ushort)32);   // CR/LF   -> ' '
         else if(ch < 32 || ch > 126)          out += ShortToString((ushort)95);   // non-ASCII -> '_'
         else                                  out += ShortToString(ch);
        }
      return out;
     }

   //+------------------------------------------------------------------+
   //| Appends one audit row, creating the file + header on first use.   |
   //|                                                                   |
   //| The handle is opened FILE_READ|FILE_WRITE (NOT plain FILE_WRITE,  |
   //| which truncates) and seeked to EOF -- the same append idiom       |
   //| COttoJournal uses. If this fails it is logged and ignored: a      |
   //| watchdog that aborts on its own I/O failure is worse than one     |
   //| that keeps watching.                                              |
   //+------------------------------------------------------------------+
   void              WriteCsv(string subject, string message, string severity, bool emailed)
     {
      int h = FileOpen(HT_AUDIT_CSV, FILE_CSV | FILE_READ | FILE_WRITE | FILE_ANSI, ',');
      if(h == INVALID_HANDLE)
        {
         Print("[HighTable] CSV open FAILED err=", GetLastError(), " file=", HT_AUDIT_CSV);
         return;
        }
      FileSeek(h, 0, SEEK_END);
      if(FileTell(h) == 0)
         FileWrite(h, "timestamp", "symbol", "magic", "severity",
                   "subject", "message", "email_status");
      FileWrite(h,
                TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
                Sanitize(m_symbol),
                IntegerToString(m_magic),
                Sanitize(severity),
                Sanitize(subject),
                Sanitize(message),
                (emailed ? "SENT"
                         : ((bool)MQLInfoInteger(MQL_TESTER) ? "TESTER" : "FAILED")));
      FileClose(h);
     }

   //+------------------------------------------------------------------+
   //| PART 2 - read-only terminal queries.                              |
   //|                                                                   |
   //| Everything below reads the terminal directly. No COtto* type, no  |
   //| CTrade and no CPositionInfo appears anywhere in this file, which  |
   //| is what makes the auditor structurally incapable of mutating      |
   //| basket state -- the decoupling contract at the top of this file.  |
   //| The one thing the terminal cannot tell us (which leg is the       |
   //| PRIMARY, and what the trailing drawdown anchors are) is PUSHED in |
   //| via the Notify*/SetSafetyBaseline ingress points instead.         |
   //+------------------------------------------------------------------+
   int               CountBookLegs(void) const
     {
      int n = 0;
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != (long)m_magic) continue;
         n++;
        }
      return n;
     }

   //+------------------------------------------------------------------+
   //| Is a given ticket present in the terminal book, for us?           |
   //|                                                                   |
   //| Used to test the order layer's belief against reality. This is    |
   //| NOT tautological with the push: the order layer reports the       |
   //| ticket it thinks it is trailing (m_activeTrade.ticket), which it  |
   //| holds across a close until SyncActiveTrade() reconciles it on the |
   //| following OnTick -- so the two can and do disagree.               |
   //+------------------------------------------------------------------+
   bool              BookHasTicket(const ulong ticket) const
     {
      if(ticket == 0) return false;
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) != ticket) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != (long)m_magic) continue;
         return true;
        }
      return false;
     }

   //+------------------------------------------------------------------+
   //| Is a given ticket present in the terminal book, IGNORING magic?   |
   //|                                                                   |
   //| v5.33: used ONLY for a primary the order layer adopted from a     |
   //| magic-0 manual position. BookHasTicket() remains the correct test |
   //| for every EA-originated leg, but it cannot express the adopted    |
   //| case: it requires POSITION_MAGIC == m_magic, so an adopted leg    |
   //| would fail it forever and the watchdog would report a desync for  |
   //| the entire life of a perfectly healthy trade. This variant answers |
   //| the only question that is meaningful for an adopted ticket -- does |
   //| the position still EXIST on this symbol? -- and is deliberately    |
   //| not used anywhere else, because dropping the magic filter globally |
   //| would let an unrelated manual or foreign-EA leg satisfy the EA's   |
   //| own book test.                                                    |
   //+------------------------------------------------------------------+
   bool              AdoptedTicketInBook(const ulong ticket) const
     {
      if(ticket == 0) return false;
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) != ticket) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         return true;
        }
      return false;
     }

   //+------------------------------------------------------------------+
   //| Primary-leg identification (read-only, comment-derived).          |
   //|                                                                   |
   //| The auditor cannot ask COttoOrderManager which ticket is the      |
   //| primary, but it does not need to: BuildOrderComment() appends     |
   //| "_T<n>" ONLY for n > 1 (COttoOrderManager.mqh:419). The primary   |
   //| tranche is therefore exactly the leg whose comment carries no     |
   //| "_T" suffix -- a free, stable, read-only identifier that requires |
   //| no new state and no new field on SActiveTrade.                    |
   //|                                                                   |
   //| FAIL-SAFE DIRECTION: if the format ever changes so that NO leg    |
   //| looks like a primary, every leg is treated as primary and the     |
   //| trim check finds nothing to have missed -- it under-reports       |
   //| rather than inventing a TrimFailure.                              |
   //+------------------------------------------------------------------+
   bool              CommentHasTrancheSuffix(const string c) const
     {
      int len = StringLen(c);
      if(len < 3) return false;               // too short to end in "_T<n>"
      // Scan back over the trailing digits, then require the literal "_T"
      // immediately before them. "_T" must be a real suffix: a bare 'T' in
      // the middle of an ID (e.g. "...-BLKT3") must not match.
      int i = len - 1;
      int digits = 0;
      while(i >= 0)
        {
         ushort ch = (ushort)StringGetCharacter(c, i);
         if(ch < '0' || ch > '9') break;
         digits++;
         i--;
        }
      if(digits < 1) return false;
      if(i < 1) return false;
      if(StringGetCharacter(c, i)     != 'T') return false;
      if(StringGetCharacter(c, i - 1) != '_') return false;
      return true;
     }

   bool              IsPrimaryLeg(void) const
     {
      string c = PositionGetString(POSITION_COMMENT);
      return !CommentHasTrancheSuffix(c);
     }

   //+------------------------------------------------------------------+
   //| A pure-function mirror of COttoTradeManager's trim filter.        |
   //|                                                                   |
   //| This is a DELIBERATE, documented duplication of the qualification |
   //| maths in WalkTrimLegs() (COttoTradeManager.mqh:134-186). The      |
   //| auditor cannot call that method -- WalkTrimLegs is private and    |
   //| taking a COttoTradeManager reference would breach the no-pointer  |
   //| contract -- so the filter is mirrored here and must be kept in    |
   //| step by hand. The constant that matters is referenced by NAME     |
   //| (the live InpTrimLoserStopPct input, not a hardcoded 70.0), so a  |
   //| retune moves both copies at once and only a change to the         |
   //| STRUCTURE of the formula could ever cause drift.                  |
   //|                                                                   |
   //| Counts legs that are (a) ours, (b) not the primary, (c) have a    |
   //| usable stop, and (d) have travelled >= trimPct% of the way to     |
   //| that stop. That count is what tells the auditor whether the       |
   //| trim had something it COULD have closed.                          |
   //|                                                                   |
   //| KNOWN LIMITATION (accepted): the real filter also clamps each     |
   //| leg's stop against the basket's session SL, which is private to   |
   //| the order manager and unknown here. An unclamped comparison can   |
   //| only ever UNDER-count qualified legs -- so this may miss a        |
   //| TrimFailure after a rejected ApplyUnifiedSL, but it can never     |
   //| raise a false one. Under-reporting is the safe direction.         |
   //|                                                                   |
   //| FALLBACK: the primary leg is identified by the pushed ticket      |
   //| (the same identifier WalkTrimLegs uses). Only before the first    |
   //| push arrives does this fall back to the comment-suffix rule,      |
   //| which is itself fail-safe: an unrecognised comment format makes   |
   //| every leg look primary, so nothing qualifies and the count is 0.  |
   //+------------------------------------------------------------------+
   int               CountTrimmableLegs(double trimPct) const
     {
      int qualified = 0;
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != (long)m_magic) continue;

         // Exclude the primary exactly as WalkTrimLegs does: it skips
         // `ticket == m_orderManager.GetActiveTrade().ticket`, and that
         // ticket is pushed to us verbatim. Using the SAME identifier is
         // what keeps this mirror faithful -- a comment-derived primary
         // could disagree with the manager's belief and quietly skew the
         // count in either direction.
         ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
         if(m_trackedPrimary > 0)
           {
            if(ticket == 0 || ticket == m_trackedPrimary) continue;
           }
         else if(IsPrimaryLeg()) continue;   // no push yet: see fallback note

         bool   isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl     = PositionGetDouble(POSITION_SL);
         if(sl <= 0.0) continue;

         double total = isLong ? (entry - sl) : (sl - entry);
         if(total <= 0.0) continue;

         double mark  = isLong ? SymbolInfoDouble(m_symbol, SYMBOL_BID)
                               : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
         double moved = isLong ? (entry - mark) : (mark - entry);
         if(moved < (trimPct / 100.0) * total) continue;
         qualified++;
        }
      return qualified;
     }

   //+------------------------------------------------------------------+
   //| Drawdown measures, in PERCENT units, matching otto.mq5 exactly.   |
   //|                                                                   |
   //| otto.mq5:734-736 computes 100.0*(balance-equity)/balance against  |
   //| SafetyMaxFloatingLoss (0.90), and :726-727 compute the daily and  |
   //| trailing-total measures against SafetyDailyDDLimit (3.0) and      |
   //| SafetyTotalDDLimit (5.0). All three are PERCENT values. The       |
   //| 0.0090-fraction idiom belongs to a different rule shape; mixing   |
   //| the two units here would make the auditor disagree with the EA    |
   //| about the very breach it is reporting on, so the comparisons      |
   //| below deliberately use the percent form throughout.               |
   //+------------------------------------------------------------------+
   double            FloatingLossPct(void) const
     {
      double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      if(balance <= 0.0 || equity >= balance) return 0.0;
      return 100.0 * (balance - equity) / balance;
     }

   double            DailyDDPct(void) const
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(m_dailyResetBalance <= 0.0) return 0.0;
      return 100.0 * (m_dailyResetBalance - equity) / m_dailyResetBalance;
     }

   double            TotalDDPct(void) const
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(m_equityHwm <= 0.0) return 0.0;
      return 100.0 * (m_equityHwm - equity) / m_equityHwm;
     }

public:
                     CHighTableAuditor(void)
     {
      m_symbol     = "";
      m_magic      = 0;
      m_ready      = false;
      m_dispatched = 0;
      m_suppressed = 0;
      m_lastAudit  = 0;
      // Part 2 pushed state. Every counter starts at zero so the FIRST
      // pushed fact is always observed as a delta from a known baseline,
      // and the "seen" values start AT zero so the very first cycle does
      // not mistake a pre-existing total for a burst.
      m_rejectCount            = 0;
      m_rejectSeenAtLastAudit  = 0;
      m_stopModifyFailures     = 0;
      m_stopModifySeen         = 0;
      m_trackedLegs            = 0;
      m_trackedPrimary         = 0;
      m_trackedActive          = false;
      m_trackedAdopted         = false;
      m_stateSkewSeen          = false;
      m_adoptionSeen           = false;
      m_dailyResetBalance      = 0.0;
      m_equityHwm              = 0.0;
      m_totalHalted            = false;
      m_haveBaseline           = false;
      m_alertSent_TrimFailure        = false;
      m_alertSent_DailyDDBreach      = false;
      m_alertSent_TotalDDBreach      = false;
      m_alertSent_FloatingLossCap    = false;
      m_alertSent_Halt               = false;
      m_alertSent_OrderRejectSpike   = false;
      m_alertSent_StopModifyFailure  = false;
      m_alertSent_StateInconsistency = false;
     }
                    ~CHighTableAuditor(void) { }

   //+------------------------------------------------------------------+
   //| Binds the watchdog to a chart identity. Never fails: an auditor   |
   //| that refuses to start is an auditor that cannot report the        |
   //| failure that stopped it.                                          |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol, int magic)
     {
      m_symbol = symbol;
      m_magic  = magic;
      m_ready  = true;
      Print("[HighTable] Auditor armed | symbol=", m_symbol,
            " | magic=", m_magic, " | csv=", HT_AUDIT_CSV);
      return true;
     }

   void              Deinit(void)
     {
      Print("[HighTable] Auditor standing down | dispatched=", m_dispatched,
            " | suppressed=", m_suppressed,
            " | lastAudit=", (m_lastAudit > 0 ? TimeToString(m_lastAudit, TIME_DATE | TIME_SECONDS)
                                              : "never"));
      m_ready = false;
     }

   //+------------------------------------------------------------------+
   //| PART 2 - pushed-fact ingress.                                     |
   //|                                                                   |
   //| The auditor cannot poll the trade path (it must not include a     |
   //| trade module), so the two facts the order layer owns are PUSHED   |
   //| in by otto.mq5 and read back inside AuditOrderHealth(). These are |
   //| deliberately dumb ingress points: they record a counter and      |
   //| return. They never dispatch -- escalating a single event is the   |
   //| audit cycle's job -- and they never call RunAudit(), so the       |
   //| "RunAudit runs exactly once, from OnTimer" contract still holds.  |
   //+------------------------------------------------------------------+
   void              NotifyOrderReject(int retcode, string context, int count = 1)
     {
      m_rejectCount += count;
      if(EnableLogging)
         Print("[HighTable] observed order reject | retcode=", retcode,
               " | ", context, " | x", count, " | total=", m_rejectCount);
     }

   //| The ticket and retcode are both private to COttoOrderManager, so the
   //| push from otto.mq5 carries its COUNTER DELTA and leaves them 0. The
   //| count is what AuditOrderHealth escalates on; the identity of the
   //| individual request is already in the order layer's own log.
   void              NotifyStopModifyFailure(ulong ticket, int retcode, int count = 1)
     {
      m_stopModifyFailures += count;
      if(EnableLogging)
         Print("[HighTable] observed stop-modify failure | ticket=", ticket,
               " | retcode=", retcode, " | x", count, " | total=", m_stopModifyFailures);
     }

   long              GetRejectsObserved(void)      const { return m_rejectCount; }
   long              GetStopModifyFailures(void)   const { return m_stopModifyFailures; }

   //+------------------------------------------------------------------+
   //| Pushes the live drawdown anchors.                                 |
   //|                                                                   |
   //| otto.mq5 owns g_dailyResetBalance, g_equityHighWaterMark and      |
   //| g_totalDD_Halted. Re-deriving them here from GlobalVariables would |
   //| mean re-implementing otto.mq5's private "OTTO_<key>_<login>" key  |
   //| format -- a second source of truth for the trailing floor, and    |
   //| exactly the kind of silent divergence the decoupling contract is  |
   //| meant to prevent. So the EA pushes the values it is actually      |
   //| enforcing, and the auditor can never disagree with them.          |
   //|                                                                   |
   //| Called every OnTick, so it is intentionally trivial.              |
   //+------------------------------------------------------------------+
   void              SetSafetyBaseline(double dailyResetBalance, double equityHwm,
                                       bool totalHalted)
     {
      m_dailyResetBalance = dailyResetBalance;
      m_equityHwm         = equityHwm;
      m_totalHalted       = totalHalted;
      m_haveBaseline      = true;
     }

   //| Pushes the order layer's belief about the trade it is driving.      |
   //|                                                                     |
   //| The terminal's book is the authority on which positions EXIST. This |
   //| is what the order layer thinks it is managing, and the two may       |
   //| legitimately disagree for a tick around a fill or a close because    |
   //| the manager reconciles on the following OnTick. That is why         |
   //| AuditStateConsistency requires the divergence to repeat across two  |
   //| consecutive audit cycles before it reports anything.                |
   //|                                                                     |
   //| v5.33 adoptsManual: TRUE when the primary is a magic-0 position the  |
   //| operator opened by hand and the order layer adopted. This matters    |
   //| because EVERY book read in this class is magic-scoped -- see         |
   //| CountBookLegs(), BookHasTicket() and CountTrimmableLegs(), each of   |
   //| which requires POSITION_MAGIC == m_magic. An adopted leg carries     |
   //| magic 0, so it is invisible to all three by construction. The flag   |
   //| is therefore the ONLY way the phantom test can distinguish "the      |
   //| trail is managing a ticket that no longer exists" from "the trail is |
   //| managing a live manual leg the book is not allowed to count".        |
   //| Suppressing the alert when adopted is strictly the safe direction:   |
   //| the manager's own IsTrackedTicketOpen() still sees the leg every     |
   //| tick (it accepts an adopted ticket explicitly), so a genuinely        |
   //| phantom adopted ticket is caught by the order layer and logged       |
   //| there, while this watchdog stays silent on a healthy adoption.       |
   //| The magic-scoped counts are deliberately NOT widened: their alert   |
   //| text names m_magic, and folding foreign magic-0 legs into them would |
   //| be a silent redefinition of what the message claims to measure.      |
   //+------------------------------------------------------------------+
   void              SetTrackedLegs(int legs, ulong primaryTicket, bool active,
                                    bool adoptedManual = false)
     {
      m_trackedLegs    = legs;
      m_trackedPrimary = primaryTicket;
      m_trackedActive  = active;
      m_trackedAdopted = adoptedManual;
      // One INFO per adoption, so the audit trail states WHY the book count
      // and the tracked count are allowed to disagree -- but latched, so a
      // long-lived adopted position does not email on every cycle.
      if(adoptedManual && !m_adoptionSeen)
        {
         m_adoptionSeen = true;
         Print("[HighTable] note: tracked primary ", primaryTicket,
               " is an ADOPTED manual (magic-0) leg -- book/tracked",
               " divergence class suppressed for this ticket");
        }
      else if(!adoptedManual)
         m_adoptionSeen = false;
     }

   //+------------------------------------------------------------------+
   //| DispatchAlert - unconditional alert.                              |
   //|                                                                   |
   //|  * logs every alert to the audit CSV (evidence survives a console |
   //|    that nobody is watching), and                                  |
   //|  * emails it via the native SendMail, guarded by a tester check   |
   //|    because SendMail cannot dispatch inside the Strategy Tester.    |
   //|                                                                   |
   //| Use this for one-off events. For an ONGOING violation use         |
   //| DispatchAlertOnce so the mailbox is not flooded.                  |
   //+------------------------------------------------------------------+
   void              DispatchAlert(string subject, string message)
     {
      bool emailed = false;
      if(!(bool)MQLInfoInteger(MQL_TESTER))
        {
         emailed = SendMail(subject, message);
         if(!emailed)
            Print("[HighTable] SendMail FAILED err=", GetLastError(), " subj=", subject);
        }
      else if(EnableLogging)
         Print("[HighTable] tester mode - email suppressed (CSV only): ", subject);

      WriteCsv(subject, message, HT_SEV_CRITICAL, emailed);
      m_dispatched++;
      if(EnableLogging)
         Print("[HighTable] ALERT [", subject, "] ", message);
     }

   //+------------------------------------------------------------------+
   //| DispatchAlertOnce - latched alert, one email per incident.        |
   //|                                                                   |
   //| `latch` is passed BY REFERENCE so each call site owns a named     |
   //| bool member (m_alertSent_*) that is greppable and cannot be       |
   //| mistyped into a silent no-op the way a string-keyed map can.      |
   //|                                                                   |
   //| Lifecycle:                                                        |
   //|   latch == false -> raise the alert, SET the latch                |
   //|   latch == true  -> incident already reported: swallow the repeat |
   //|                      (CSV at INFO, no email) and count it         |
   //|   ClearLatch()   -> re-arms, called once the condition reads      |
   //|                      healthy, so the NEXT incident alerts again   |
   //|                                                                   |
   //| The latch is raised on the alert itself -- not on the caller's    |
   //| observation of the violation -- so a condition that is true but   |
   //| not yet reported can never latch itself silent.                   |
   //+------------------------------------------------------------------+
   void              DispatchAlertOnce(bool &latch, string subject, string message,
                                       string severity = HT_SEV_CRITICAL)
     {
      if(latch)
        {
         m_suppressed++;
         WriteCsv(subject, message + " [repeat suppressed]", HT_SEV_INFO, false);
         if(EnableLogging)
            Print("[HighTable] suppressed repeat: ", subject);
         return;
        }

      latch = true;

      bool emailed = false;
      if(!(bool)MQLInfoInteger(MQL_TESTER))
        {
         emailed = SendMail(subject, message);
         if(!emailed)
            Print("[HighTable] SendMail FAILED err=", GetLastError(), " subj=", subject);
        }
      else if(EnableLogging)
         Print("[HighTable] tester mode - email suppressed (CSV only): ", subject);

      WriteCsv(subject, message, severity, emailed);
      m_dispatched++;
      if(EnableLogging)
         Print("[HighTable] ALERT ONCE [", severity, "] ", subject, " - ", message);
     }

   //+------------------------------------------------------------------+
   //| Re-arms a latch once its violation has cleared.                   |
   //+------------------------------------------------------------------+
   void              ClearLatch(bool &latch) { latch = false; }
   bool              LatchActive(bool &latch) const { return latch; }


   //+------------------------------------------------------------------+
   //| RunAudit - the watchdog's own cadence.                            |
   //|                                                                   |
   //| Called from OnTimer, never from OnTick, so a halted or paused     |
   //| trade loop cannot silence the audit. Each check below is          |
   //| self-contained and purely observational: it reads terminal state  |
   //| and dispatches at most one latched alert.                         |
   //+------------------------------------------------------------------+
   void              RunAudit(void)
     {
      if(!m_ready) return;

      AuditTrimHealth();        // PART 2 - smart-trim outcome
      AuditDrawdown();          // PART 2 - daily / total / floating DD
      AuditOrderHealth();       // PART 2 - rejects + SL modify failures
      AuditStateConsistency();  // PART 2 - book vs tracked basket

      m_lastAudit = TimeCurrent();
     }

   long              GetAlertsDispatched(void) const { return m_dispatched; }
   long              GetAlertsSuppressed(void) const { return m_suppressed; }
   datetime          GetLastAudit(void)        const { return m_lastAudit; }

   //+------------------------------------------------------------------+
   //| PART 2 - the four observation bodies.                             |
   //|                                                                   |
   //| Each body is self-contained, purely observational, and dispatches |
   //| at most one LATCHED alert. The latch lifecycle is uniform: raise  |
   //| while the violation is live, ClearLatch() the moment it reads     |
   //| healthy again, so one incident produces exactly one email and the |
   //| next incident emails again.                                       |
   //|                                                                   |
   //| Every threshold is the LIVE input, never a literal, and all three |
   //| drawdown comparisons are in PERCENT units to match otto.mq5.      |
   //+------------------------------------------------------------------+
   void              AuditTrimHealth(void)
     {
      // The breach that arms the smart trim, read exactly as otto.mq5
      // reads it (percent, against the live 0.90% cap).
      double floatingLoss = FloatingLossPct();
      if(floatingLoss < SafetyMaxFloatingLoss)
        {
         ClearLatch(m_alertSent_TrimFailure);
         return;
        }

      // The cap is breached and otto.mq5 has run TrimHeavyLosers() on this
      // very tick. Any non-primary leg still standing that the trim's own
      // filter would have qualified is therefore a leg the trim TRIED and
      // FAILED to close -- the whole point of the 0.90% cap is that those
      // legs do not survive it.
      int trimmable = CountTrimmableLegs(InpTrimLoserStopPct);
      if(trimmable > 0)
         DispatchAlertOnce(m_alertSent_TrimFailure,
                           "HighTable: smart trim could not act",
                           "Floating loss " + DoubleToString(floatingLoss, 2) +
                           "% >= cap " + DoubleToString(SafetyMaxFloatingLoss, 2) +
                           "% and " + IntegerToString(trimmable) +
                           " non-primary leg(s) remain past " +
                           DoubleToString(InpTrimLoserStopPct, 1) +
                           "% of the way to their stop. The trim could not close them.");
      else
         ClearLatch(m_alertSent_TrimFailure);
     }

   void              AuditDrawdown(void)
     {
      // --- Floating-loss cap (no baseline needed: it is a balance/equity
      //     ratio, readable on any tick, exactly as otto.mq5 reads it) ---
      double floatingLoss = FloatingLossPct();
      if(floatingLoss >= SafetyMaxFloatingLoss)
         DispatchAlertOnce(m_alertSent_FloatingLossCap,
                           "HighTable: floating-loss cap breached",
                           "Floating loss " + DoubleToString(floatingLoss, 2) +
                           "% >= " + DoubleToString(SafetyMaxFloatingLoss, 2) + "%");
      else
         ClearLatch(m_alertSent_FloatingLossCap);

      // --- Permanent halt. PUSHED, because the halt flag can survive a
      //     restart in a GlobalVariable and be set before OnTick ever
      //     reaches the safety block. DELIBERATELY NEVER CLEARED: the halt
      //     is permanent by design (otto.mq5 only ever sets it), so
      //     re-arming this latch would be meaningless at best and a
      //     misreport at worst. ---
      if(m_totalHalted)
         DispatchAlertOnce(m_alertSent_Halt,
                           "HighTable: EA PERMANENTLY HALTED",
                           "Total trailing drawdown halt is latched. The EA will not "
                           "place or manage orders until it is manually re-armed.");

      // The remaining two measures are meaningless without the trailing
      // anchors, so a push that has not arrived yet is reported as nothing
      // rather than as a breach of zero.
      if(!m_haveBaseline) return;

      // --- Daily soft breach: pauses new orders, clears at the session
      //     rollover when otto.mq5 re-anchors g_dailyResetBalance, which
      //     in turn drops this measure back under the limit and re-arms
      //     the latch through the ClearLatch below. ---
      double dailyDD = DailyDDPct();
      if(dailyDD >= SafetyDailyDDLimit)
         DispatchAlertOnce(m_alertSent_DailyDDBreach,
                           "HighTable: daily drawdown limit breached",
                           "Daily DD " + DoubleToString(dailyDD, 2) +
                           "% >= " + DoubleToString(SafetyDailyDDLimit, 2) +
                           "% - new orders paused");
      else
         ClearLatch(m_alertSent_DailyDDBreach);

      // --- Total trailing hard breach. Like the halt, this one is NEVER
      //     cleared: the trailing floor only ratchets down, so once the
      //     breach is real it is terminal. Re-arming could only produce a
      //     second email for the same permanent condition. ---
      double totalDD = TotalDDPct();
      if(totalDD >= SafetyTotalDDLimit)
         DispatchAlertOnce(m_alertSent_TotalDDBreach,
                           "HighTable: total trailing drawdown breached",
                           "Total DD " + DoubleToString(totalDD, 2) +
                           "% >= " + DoubleToString(SafetyTotalDDLimit, 2) + "%");
     }

   void              AuditOrderHealth(void)
     {
      // --- Broker reject BURST. A delta, not a lifetime total: the total
      //     is monotonic, so comparing it against the value seen at the
      //     previous cycle isolates the rejections that happened since.
      //     One refusal is ordinary venue traffic; HT_REJECT_BURST or more
      //     within a single cycle is the signature of a broken feed. ---
      long rejectDelta = m_rejectCount - m_rejectSeenAtLastAudit;
      m_rejectSeenAtLastAudit = m_rejectCount;

      if(rejectDelta >= HT_REJECT_BURST)
         DispatchAlertOnce(m_alertSent_OrderRejectSpike,
                           "HighTable: broker reject burst",
                           IntegerToString((int)rejectDelta) +
                           " order(s) rejected since the last audit (threshold " +
                           IntegerToString(HT_REJECT_BURST) + ").",
                           HT_SEV_WARN);
      else if(rejectDelta == 0)
         ClearLatch(m_alertSent_OrderRejectSpike);

      // --- Stop-modify failures. A stop that cannot be written is unmanaged
      //     risk: the position is open with no live protection at the level
      //     the trail believes it has. Same delta treatment as rejects. ---
      long stopDelta = m_stopModifyFailures - m_stopModifySeen;
      m_stopModifySeen = m_stopModifyFailures;

      if(stopDelta > 0)
         DispatchAlertOnce(m_alertSent_StopModifyFailure,
                           "HighTable: stop-loss modify failed",
                           IntegerToString((int)stopDelta) +
                           " SL modify request(s) were rejected since the last audit.",
                           HT_SEV_WARN);
      else
         ClearLatch(m_alertSent_StopModifyFailure);
     }

   void              AuditStateConsistency(void)
     {
      // The one fact the terminal cannot supply is which ticket the order
      // layer believes it is trailing. otto.mq5 pushes that belief
      // (m_activeTrade.ticket, via SetTrackedLegs). The book is the
      // authority on existence, so a tracked primary that is NOT in the
      // book means the trail and the trim are maintaining risk geometry
      // against a ticket that is no longer there -- a phantom.
      //
      // The inverse is deliberately NOT alerted on: a book leg the order
      // layer has not yet adopted is the normal one-tick hand-off while
      // SyncActiveTrade() reconciles. Only the phantom direction is a real
      // desync, and alerting on the other would email on every fill.
      //
      // Even the phantom is confirmed across TWO cycles, because
      // CloseEntireBasket() leaves m_hasActiveTrade set until the caller's
      // next sync (see COttoOrderManager.mqh:2137) -- so a one-cycle sighting
      // is an artifact of the close path, not a desync.
      int book = CountBookLegs();

      // ---- v5.33: the ADOPTED (magic-0) primary ------------------------
      // BookHasTicket() demands POSITION_MAGIC == m_magic, so for a primary
      // the order layer adopted from a manual position it can only ever
      // answer false: it is not evidence of anything. An adopted leg is
      // alive exactly when a position carrying that ticket is present on
      // this symbol, whatever its magic. So the presence TEST is swapped
      // rather than the alert suppressed -- a vanished adopted ticket still
      // escalates through the same two-cycle confirmation and the same
      // latch. Note this state is only reachable through a one-tick race
      // (SyncActiveTrade clears m_hasActiveTrade on the following tick),
      // which is precisely the window the magic-scoped test also guards.
      bool adopted = m_trackedAdopted;
      bool present = adopted ? AdoptedTicketInBook(m_trackedPrimary)
                             : BookHasTicket(m_trackedPrimary);

      bool phantom = m_trackedActive && m_trackedPrimary > 0 &&
                     !present;

      if(!phantom)
        {
         ClearLatch(m_alertSent_StateInconsistency);
         m_stateSkewSeen = false;
         return;
        }

      if(!m_stateSkewSeen)
        {
         // First sighting: remember it and wait one cycle to see whether
         // it resolves on its own before calling it an incident.
         m_stateSkewSeen = true;
         return;
        }

      DispatchAlertOnce(m_alertSent_StateInconsistency,
                        "HighTable: book/tracked state desync",
                        "The order layer believes it is trailing ticket " +
                        (string)m_trackedPrimary + " (basket holds " +
                        IntegerToString(m_trackedLegs) + " leg(s)) but the terminal " +
                        "book holds " + IntegerToString(book) + " leg(s) for " +
                        m_symbol + " magic " + IntegerToString(m_magic) +
                        " and that ticket is not among them. Divergence " +
                        "persisted across an audit cycle - the trail and trim " +
                        "are acting on a phantom position." +
                        // v5.33: an adopted primary is absent from the
                        // magic-scoped count above BY CONSTRUCTION, so the
                        // evidence line names that reason explicitly. Without
                        // it the two numbers in this alert read as a bug in
                        // the adoption path when they are in fact the expected
                        // shape of one.
                        (adopted ? " NOTE: the tracked primary was ADOPTED from a"
                                   " magic-0 manual position (-MAN basket), so it"
                                   " is not counted in the magic-scoped book"
                                   " total above; the ticket itself is gone from"
                                   " the symbol book."
                                 : ""));
     }
  };
#endif  // __OTTO_HIGH_TABLE_AUDITOR__
