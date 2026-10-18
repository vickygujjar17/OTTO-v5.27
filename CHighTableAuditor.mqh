//+------------------------------------------------------------------+
//|                                           CHighTableAuditor.mqh  |
//|        MODULE - "High Table" decoupled live-state watchdog       |
//|        Read-only audit + CSV evidence trail + email dispatcher    |
//|        Runs on its own timer cadence, independent of OnTick       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.68"

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

// A single SHARED cooldown caps the mailbox, not any one incident. Every
// email actually sent on EITHER dispatch path pushes the next permitted
// message 1800s out, so a burst of DISTINCT violations landing on one audit
// cycle produces one email plus a set of CSV rows -- never one email each.
// Zero emails are sent while the system is healthy, because a healthy cycle
// raises no alert in the first place.
#define HT_EMAIL_COOLDOWN_SECONDS  1800 // 30 minutes

// An orphan leg is only reported once it has DURED this long. A leg is
// submitted with its stop attached, so a stopless position is normally the
// residue of a refused ModifyStopLoss() on a leg whose original stop was never
// set -- but between two audit ticks it can also be a leg mid-submission, or a
// position whose stop the venue is still registering. The grace period makes
// the check report the PERSISTENT shape and never a transient one, which is
// the same reasoning AuditStateConsistency applies via m_stateSkewSeen.
#define HT_ORPHAN_GRACE_SECONDS    120 // 2 minutes

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
   datetime          m_lastEmailSent;   // cooldown tracker for error emails

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
   bool              m_alertSent_DailyDDBreach;      // daily DD soft breach
   bool              m_alertSent_TotalDDBreach;      // total DD hard breach
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
   bool              m_alertSent_RiskBreach;        // position risk exceeded allowance
   bool              m_alertSent_StaleTrail;        // price ran past the trail's arm R:R, stop never followed (v5.63)
   bool              m_alertSent_AggRiskBreach;     // SUMMED basket risk over the stacked budget (v5.63)
   bool              m_alertSent_OrphanLeg;         // a magic-scoped leg carries NO stop at all (v5.67)

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
   bool              m_aggRiskSeen;          // summed-risk breach seen once, awaiting confirm (v5.63)

   //| Tracked RISK GEOMETRY, pushed from the order layer (v5.63).         |
   //|                                                                     |
   //| The terminal can answer everything about a leg EXCEPT its 1R unit:   |
   //| m_basketRRUnit is the order layer's own private divisor, derived at  |
   //| basket init and persisted across restarts. Re-deriving it here would |
   //| be a second source of truth for the denominator of every RR         |
   //| decision -- exactly what the decoupling contract forbids -- so the   |
   //| EA pushes it, together with the entry and direction it belongs to.   |
   //|                                                                     |
   //| m_trackedSL is the order layer's view of the working stop. It is     |
   //| only a FALLBACK: the audit prefers the live stop read from the book, |
   //| because this whole watchdog exists to measure what the broker really |
   //| holds rather than what the sizing code believes it wrote.            |
   //|                                                                     |
   //| m_trailPeakRR is the auditor's OWN observation, not a pushed fact:   |
   //| the running maximum of the live excursion, in R, since the tracked   |
   //| ticket last changed. It has to be recorded here because the question |
   //| "should the trail have armed by now?" must be answered from PRICE,   |
   //| never from the stop -- reading the stop to decide whether the stop   |
   //| is stale is circular, and a stop that never moved would report a     |
   //| negative R and silence the very alert this check exists to raise.    |
   bool              m_haveGeometry;         // false until the first geometry push
   double            m_trackedEntry;         // pushed entry price of the tracked primary
   double            m_trackedRRUnit;        // pushed basket 1R (m_basketRRUnit)
   ENUM_TRADE_DIRECTION m_trackedDir;        // pushed direction of the tracked primary
   double            m_trackedSL;            // pushed working stop (fallback for the book read)
   double            m_trailPeakRR;          // auditor-observed peak excursion, in R
   ulong             m_trailPeakTicket;      // ticket m_trailPeakRR belongs to

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
      // Mailbox cooldown stamp. Deliberately NOT reset by Initialize(): a
      // timeframe or parameter reload must not reopen the window and let a
      // still-parked violation mail a second time.
      m_lastEmailSent = 0;
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
      m_aggRiskSeen            = false;
      m_dailyResetBalance      = 0.0;
      m_equityHwm              = 0.0;
      m_totalHalted            = false;
      m_haveBaseline           = false;
      m_alertSent_DailyDDBreach      = false;
      m_alertSent_TotalDDBreach      = false;
      m_alertSent_Halt               = false;
      m_alertSent_OrderRejectSpike   = false;
      m_alertSent_StopModifyFailure  = false;
      m_alertSent_StateInconsistency = false;
      m_alertSent_RiskBreach         = false;
      m_alertSent_StaleTrail         = false;
      m_alertSent_AggRiskBreach      = false;
      m_alertSent_OrphanLeg          = false;
      m_haveGeometry                 = false;
      m_trackedEntry                 = 0.0;
      m_trackedRRUnit                = 0.0;
      m_trackedDir                   = DIR_NONE;
      m_trackedSL                    = 0.0;
      m_trailPeakRR                  = 0.0;
      m_trailPeakTicket              = 0;
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
   //| CountBookLegs() and BookHasTicket(), each of which requires         |
   //| POSITION_MAGIC == m_magic. An adopted leg carries magic 0, so the    |
   //| book reads are blind to it by construction. The flag                  |
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
   //| SetTrackedGeometry - the 1R unit and entry the trail keys off.    |
   //|                                                                   |
   //| Pushed, for the same reason SetTrackedLegs is: m_basketRRUnit is   |
   //| the order layer's private divisor and re-deriving it here would    |
   //| create a second source of truth for every RR decision.            |
   //|                                                                   |
   //| This is also where the auditor resets its own peak-excursion       |
   //| memory: the two facts arrive together, so the moment the tracked   |
   //| ticket changes -- or the geometry is re-seeded for the same        |
   //| ticket after a basket rebuild -- any high-water mark carried over   |
   //| from the previous trade would be measured against a different      |
   //| entry and 1R. Dropping it costs at most one audit cycle of         |
   //| observation and can never manufacture an alert, because a peak     |
   //| that resets to zero is below every arm threshold.                  |
   //|                                                                   |
   //| A push that comes in carrier-less (no ticket, or a non-positive    |
   //| 1R) is recorded as NO geometry rather than as geometry of zero:    |
   //| a zero 1R would divide into infinity and turn the stale-trail      |
   //| test into a permanent false CRITICAL.                              |
   //+------------------------------------------------------------------+
   void              SetTrackedGeometry(ulong ticket, ENUM_TRADE_DIRECTION dir,
                                        double entry, double rrUnit, double workingSL)
     {
      if(ticket == 0 || rrUnit <= 0.0 || entry <= 0.0)
        {
         m_haveGeometry = false;
         m_trailPeakRR  = 0.0;
         m_trailPeakTicket = 0;
         return;
        }

      m_trackedEntry  = entry;
      m_trackedRRUnit = rrUnit;
      m_trackedDir    = dir;
      m_trackedSL     = workingSL;
      m_haveGeometry  = true;

      if(ticket != m_trailPeakTicket)
        {
         m_trailPeakTicket = ticket;
         m_trailPeakRR     = 0.0;
        }
     }

   //+------------------------------------------------------------------+
   //| Feed the peak-excursion memory. Called by AuditTrailingHealth     |
   //| for the tracked ticket only, so an unrelated book leg can never    |
   //| inflate the high-water mark the trail test reads.                  |
   //+------------------------------------------------------------------+
   void              NoteExcursion(double rr)
     {
      if(rr > m_trailPeakRR) m_trailPeakRR = rr;
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
      bool canEmail = (TimeCurrent() - m_lastEmailSent >= HT_EMAIL_COOLDOWN_SECONDS);
      bool emailed = false;
      if(!(bool)MQLInfoInteger(MQL_TESTER))
        {
         if(canEmail)
           {
            emailed = SendMail(subject, message);
            if(emailed)
               m_lastEmailSent = TimeCurrent();
            else
               Print("[HighTable] SendMail FAILED err=", GetLastError(), " subj=", subject);
           }
         else if(EnableLogging)
            Print("[HighTable] email throttled (<30m since last email; CSV only): ", subject);
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
      // Global 30-minute cooldown, evaluated BEFORE the latch branch: the
      // throttle is a property of the MAILBOX, not of any one incident. A
      // burst of DISTINCT violations landing on one audit cycle therefore
      // produces one email plus CSV rows -- never one email each.
      bool canEmail = (TimeCurrent() - m_lastEmailSent >= HT_EMAIL_COOLDOWN_SECONDS);

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
         if(canEmail)
           {
            emailed = SendMail(subject, message);
            if(emailed)
               m_lastEmailSent = TimeCurrent();
            else
               Print("[HighTable] SendMail FAILED err=", GetLastError(), " subj=", subject);
           }
         else if(EnableLogging)
            Print("[HighTable] email throttled (<30m since last email; CSV only): ", subject);
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

      AuditDrawdown();          // PART 2 - daily / total DD
      AuditOrderHealth();       // PART 2 - rejects + SL modify failures
      AuditStateConsistency();  // PART 2 - book vs tracked basket
      AuditRiskExposure();      // PART 2 - dynamic position risk audit
      AuditTrailingHealth();    // v5.63 - is the armed trail actually tightening?
      AuditOrphanLegs();        // v5.67 - is any leg running with NO stop at all?

      m_lastAudit = TimeCurrent();
     }

   long              GetAlertsDispatched(void) const { return m_dispatched; }
   long              GetAlertsSuppressed(void) const { return m_suppressed; }
   datetime          GetLastAudit(void)        const { return m_lastAudit; }

   //+------------------------------------------------------------------+
   //| PART 2 - the six observation bodies.                              |
   //|                                                                   |
   //| Each body is self-contained, purely observational, and dispatches |
   //| at most one LATCHED alert. The latch lifecycle is uniform: raise  |
   //| while the violation is live, ClearLatch() the moment it reads     |
   //| healthy again, so one incident produces exactly one email and the |
   //| next incident emails again.                                       |
   //|                                                                   |
   //| Every threshold is the LIVE input, never a literal, and the daily |
   //| and trailing-total comparisons are in PERCENT units to match      |
   //| otto.mq5.                                                         |
   //+------------------------------------------------------------------+
   void              AuditDrawdown(void)
     {
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

   //+------------------------------------------------------------------+
   //+------------------------------------------------------------------+
   //| AuditTrailingHealth - is the ARMED trail actually tightening?     |
   //|                                                                   |
   //| Added in v5.63, when InpTrailStartRR went 2.0 -> 1.0. That change  |
   //| makes the ATR trail arm on the SAME +1.0R rung as the ladder's     |
   //| first floor, which is the intended behaviour -- but it also makes  |
   //| the two contributors to the basket stop overlap for the whole life |
   //| of the trade, and a trail that silently arms and then never writes |
   //| is indistinguishable from a healthy one on any single tick. This   |
   //| body is the independent witness for that: it re-derives the        |
   //| excursion from PRICES and asks whether a stop that claims to be    |
   //| live is still on the losing side of entry.                         |
   //|                                                                   |
   //| WHY THE PEAK IS MEASURED, NOT THE STOP. The tempting test is       |
   //| "the stop reads +0.4R, so the trail must be lagging". It is        |
   //| circular: the only reason a stop reads below the arm threshold is  |
   //| that it has not moved -- which is the hypothesis under test. Such  |
   //| a test reports exactly the condition it is meant to detect as      |
   //| healthy, and a fully frozen stop reads NEGATIVE R and passes. So   |
   //| the excursion is taken from the live price every cycle and kept as |
   //| a running maximum (m_trailPeakRR) across cycles. A stop can then   |
   //| be judged stale against a fact that does not depend on it.         |
   //|                                                                   |
   //| WHY TWO CYCLES. Same reason AuditStateConsistency uses two: the    |
   //| audit runs on its own timer, so the tick that first pushes price   |
   //| through the arm threshold and the tick that first writes the stop  |
   //| need not land in the same audit window. One cycle of lag is the    |
   //| normal shape of a fill, not an incident -- and an audit that       |
   //| emails on it would be wrong every time.                            |
   //|                                                                   |
   //| WHAT IS *NOT* CLAIMED. This body does not assert the stop equals   |
   //| the trail value, does not know the ATR, and does not measure how   |
   //| far the stop lags. It asserts only the structural invariant that   |
   //| makes the rest of the design meaningful: once price has been       |
   //| available past InpTrailStartRR, the basket's stop must have left   |
   //| the losing side of entry. Anything stricter would duplicate the    |
   //| trade manager's arithmetic here and drift from it silently.        |
   //+------------------------------------------------------------------+
   void              AuditTrailingHealth(void)
     {
      // No geometry pushed yet, or no tracked trade: nothing to judge. The
      // peak is also dropped, so a re-arm begins from a clean slate.
      if(!m_haveGeometry || !m_trackedActive || m_trackedPrimary == 0)
        {
         ClearLatch(m_alertSent_StaleTrail);
         m_trailPeakRR = 0.0;
         return;
        }

      bool isLong = (m_trackedDir == DIR_LONG);
      if(!isLong && m_trackedDir != DIR_SHORT)
        {
         // DIR_NONE: the order layer has a trade but no resolved direction.
         // Treat as unjudgeable rather than guessing a side.
         ClearLatch(m_alertSent_StaleTrail);
         return;
        }

      // The excursion, in R, of the price the EA would actually pay to exit
      // on now: BID for a long and ASK for a short, i.e. the same side the
      // trail's own ATR formula is driven from.
      double price = isLong ? SymbolInfoDouble(m_symbol, SYMBOL_BID)
                            : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      if(price <= 0.0)
        {
         ClearLatch(m_alertSent_StaleTrail);
         return;
        }

      double rr = (isLong ? (price - m_trackedEntry)
                          : (m_trackedEntry - price)) / m_trackedRRUnit;
      NoteExcursion(rr);

      // Not yet armed: the trail gate in Update() is still closed, so no
      // stop movement is owed and any stop is by definition not stale.
      if(m_trailPeakRR < InpTrailStartRR)
        {
         ClearLatch(m_alertSent_StaleTrail);
         return;
        }

      // The trail is armed: price has been past InpTrailStartRR at some point
      // since the tracked ticket changed. Read the LIVE stop from the book --
      // the broker is the authority on what is really in force -- and fall
      // back to the pushed working stop only when the book has no stop for
      // this ticket.
      double sl = BookStopForTicket(m_trackedPrimary);
      if(sl <= 0.0) sl = m_trackedSL;
      if(sl <= 0.0)
        {
         DispatchAlertOnce(m_alertSent_StaleTrail,
                           "HighTable: trail armed but basket carries NO stop",
                           "Trail armed at +" + DoubleToString(InpTrailStartRR, 2) +
                           "R and price has since reached +" +
                           DoubleToString(m_trailPeakRR, 2) +
                           "R, but ticket #" + IntegerToString((long)m_trackedPrimary) +
                           " has no stop attached at the broker and the order layer" +
                           " has none recorded either. Entry " +
                           DoubleToString(m_trackedEntry, _Digits) + ", 1R = " +
                           DoubleToString(m_trackedRRUnit, _Digits) +
                           ". The basket is running unprotected.",
                           HT_SEV_WARN);
         return;
        }

      // The invariant. A trail that has genuinely armed must have moved the
      // stop to at least entry, i.e. off the losing side. A stop still
      // reading on the losing side after the peak cleared the arm threshold
      // means the trail never wrote -- the exact failure this check exists
      // to surface.
      double stopRR = (isLong ? (sl - m_trackedEntry)
                              : (m_trackedEntry - sl)) / m_trackedRRUnit;
      if(stopRR >= 0.0)
        {
         ClearLatch(m_alertSent_StaleTrail);
         return;
        }

      DispatchAlertOnce(m_alertSent_StaleTrail,
                        "HighTable: trail armed but stop never moved",
                        "Trail armed at +" + DoubleToString(InpTrailStartRR, 2) +
                        "R and price reached +" + DoubleToString(m_trailPeakRR, 2) +
                        "R, yet the stop on ticket #" +
                        IntegerToString((long)m_trackedPrimary) + " still sits at " +
                        DoubleToString(stopRR, 2) + "R (stop " +
                        DoubleToString(sl, _Digits) + ", entry " +
                        DoubleToString(m_trackedEntry, _Digits) + ", 1R = " +
                        DoubleToString(m_trackedRRUnit, _Digits) +
                        ") - it has not left the losing side of entry, so the" +
                        " armed trail is not tightening the basket. Divergence" +
                        " persisted across an audit cycle.",
                        HT_SEV_WARN);
     }

   //+------------------------------------------------------------------+
   //| v5.67 - RUNG ATTRIBUTION for the risk audit.                      |
   //|                                                                   |
   //| The ladder does NOT risk the same amount on every leg: tranche 1   |
   //| is sized from RiskPercent, and T2/T3/T4 from InpRiskT2Pct /        |
   //| T3Pct / T4Pct. A single "one leg must stay under RiskPercent*1.5"  |
   //| rule is therefore only correct while every rung happens to be      |
   //| configured at or below tranche 1 -- which is true of the shipped   |
   //| defaults and of nothing else. Raise InpRiskT2Pct to 2.0 (a legal   |
   //| scale-in, and the whole point of the input existing) and a leg     |
   //| sized EXACTLY as designed reads as a CRITICAL breach.              |
   //|                                                                   |
   //| The tag is read from the position comment the order layer wrote:   |
   //| BuildOrderComment() appends "_T<n>" for every tranche above 1 and  |
   //| nothing for the primary. So "no tag" means tranche 1, which is     |
   //| also the correct fallback for an adopted manual leg -- an operator |
   //| position was not sized by this ladder, and RiskPercent is the only |
   //| budget the EA can honestly attribute to it.                        |
   //|                                                                   |
   //| Parsed from the END of the string on purpose: an order comment is  |
   //| clamped to 31 characters and the SUFFIX is what survives, so a     |
   //| long session id can truncate the "#OTTO-" prefix without ever      |
   //| touching the tag the attribution depends on.                       |
   //+------------------------------------------------------------------+
   int               CommentTrancheTag(string comment) const
     {
      if(comment == "") return 1;
      int len = StringLen(comment);
      if(len < 3) return 1;
      // "_T<n>" is the trailing form; n is a single digit by construction
      // (the ladder is 2/3/4), but tolerate any digit run.
      int tagAt = StringFind(comment, "_T", len - 2);
      if(tagAt < 0) return 1;                       // scan of the last 2 chars only
      string digits = StringSubstr(comment, tagAt + 2);
      if(digits == "") return 1;
      for(int i = 0; i < StringLen(digits); i++)
        {
         ushort ch = (ushort)StringGetCharacter(digits, i);
         if(ch < 48 || ch > 57) return 1;           // not a clean numeric tag
        }
      int tag = (int)StringToInteger(digits);
      return (tag >= 2 && tag <= 4) ? tag : 1;
     }

   //| The nominal risk percentage the ladder was DESIGNED to put on a leg.
   //| Deliberately a pure read of the live inputs: the auditor must judge the
   //| configuration actually running, never a frozen copy of the defaults.
   double            RungRiskPct(string comment) const
     {
      switch(CommentTrancheTag(comment))
        {
         case 2:  return InpRiskT2Pct;
         case 3:  return InpRiskT3Pct;
         case 4:  return InpRiskT4Pct;
         default: return RiskPercent;
        }
     }

   //+------------------------------------------------------------------+
   //| Read-only book query: the stop attached to one ticket.            |
   //|                                                                   |
   //| Returns 0 when the ticket is not in the symbol book or carries no  |
   //| stop, so callers must treat 0 as "no stop visible" and never as a  |
   //| stop at price zero. Scanned from the END of the book, the same     |
   //| idiom every other book read in this class uses.                    |
   //+------------------------------------------------------------------+
   double            BookStopForTicket(ulong ticket)
     {
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if((ulong)PositionGetInteger(POSITION_TICKET) != ticket) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         return PositionGetDouble(POSITION_SL);
        }
      return 0.0;
     }

   //+------------------------------------------------------------------+
   //| v5.67 - CountOrphanLegs: magic-scoped legs carrying NO stop.      |
   //|                                                                   |
   //| Returns the number of positions that belong to THIS EA and yet    |
   //| carry no stop at all (POSITION_SL == 0.0). It exists because      |
   //| every other book read in this class SKIPS a stopless leg --       |
   //| AuditRiskExposure cannot size the risk of a leg with no stop      |
   //| distance, so it `continue`s past it, and AuditTrailingHealth only |
   //| ever looks at the tracked primary. The result was that the exact  |
   //| failure ApplyUnifiedSL() exists to repair -- a leg the venue      |
   //| refused to stop, left running unprotected -- was also the one     |
   //| shape no audit could see: unchecked risk AND silent.              |
   //|                                                                   |
   //| Scoping matches AuditRiskExposure exactly (v5.46 rule): the magic |
   //| filter, widened by exactly one ticket for an ADOPTED magic-0      |
   //| primary. A foreign EA's or the operator's own manual leg therefore |
   //| never counts -- the blast radius stays this EA's own basket.      |
   //|                                                                   |
   //| Read-only and side-effect free: it only counts. The decision to    |
   //| alert lives in AuditOrphanLegs(), so this can be reused (and unit- |
   //| tested through the probe) without dispatching anything.            |
   //+------------------------------------------------------------------+
   int               CountOrphanLegs(void)
     {
      int orphans = 0;
      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;

         ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
         long  magic  = PositionGetInteger(POSITION_MAGIC);

         // Same widening as AuditRiskExposure: magic-scoped, plus the tracked
         // primary while it is an ADOPTED magic-0 leg. Keyed to the exact
         // TICKET, so an unrelated manual leg is still excluded.
         if(magic != (long)m_magic)
           {
            if(!m_trackedAdopted || ticket != m_trackedPrimary) continue;
           }

         if(PositionGetDouble(POSITION_SL) > 0.0) continue;   // protected

         // Grace period: a leg younger than HT_ORPHAN_GRACE_SECONDS is still
         // being armed. POSITION_TIME is the venue's open stamp, so this needs
         // no per-ticket state and cannot drift from the book.
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         if(opened > 0 && TimeCurrent() - opened < HT_ORPHAN_GRACE_SECONDS) continue;

         orphans++;
        }
      return orphans;
     }

   //+------------------------------------------------------------------+
   //| v5.67 - AuditOrphanLegs: report legs running with no stop.        |
   //|                                                                   |
   //| This is the SAFETY backstop behind COttoOrderManager's unified-SL  |
   //| repair, not a duplicate of it. The repair path RETRIES a refused    |
   //| stop modification and eventually abandons it with a log line; this  |
   //| path asks the only question that matters to the account -- is a leg |
   //| still naked right now? -- straight from the book, on the OnTimer     |
   //| cadence that survives a dead OnTick. If the trade path's repair      |
   //| budget is exhausted, the venue silently drops a stop, or the EA      |
   //| restarts before it can finish staging, this still fires.            |
   //|                                                                   |
   //| CRITICAL, not WARN: an unprotected position can lose its entire     |
   //| notional, which is a different class of event from a stale trail.    |
   //+------------------------------------------------------------------+
   void              AuditOrphanLegs(void)
     {
      // Not bound yet, or the book is unreadable: nothing to judge, and the
      // latch must not be cleared on a cycle that could not look.
      if(!m_ready) return;
      if(SymbolInfoDouble(m_symbol, SYMBOL_BID) <= 0.0) return;

      int orphans = CountOrphanLegs();
      if(orphans <= 0)
        {
         ClearLatch(m_alertSent_OrphanLeg);
         return;
        }

      DispatchAlertOnce(m_alertSent_OrphanLeg,
                        "HighTable: basket leg(s) running with NO stop",
                        IntegerToString(orphans) +
                        " position(s) on " + m_symbol + " carry magic " +
                        IntegerToString(m_magic) +
                        " but have NO stop attached at the broker. A leg" +
                        " without a stop has unbounded loss: the unified floor," +
                        " the trail and every RR calculation assume a stop exists," +
                        " so this leg is outside all of them. The usual causes are" +
                        " a venue stop-level/freeze rejection on ModifyStopLoss()" +
                        " (check the SL REPAIR ABANDONED / refused log lines) or a" +
                        " restart mid-repair.",
                        HT_SEV_CRITICAL);
     }

   //| AuditRiskExposure - independent position risk audit.              |
   //|                                                                   |
   //| Reads the terminal book directly, never the order layer, so it    |
   //| measures the risk that ACTUALLY exists rather than the risk the   |
   //| sizing code believes it wrote. That gap is the whole point: the   |
   //| dynamic sizer targets RiskPercent% of live equity, but volume-step|
   //| rounding, a minimum-lot clamp or a stale stop can leave a leg     |
   //| carrying materially more than the target.                        |
   //|                                                                   |
   //| 50% tolerance absorbs volume-step / tick quantization: a single   |
   //| tick is the smallest quantum in both the distance and the money   |
   //| conversion, so a legitimate leg can read a few percent high and   |
   //| must not be reported as a breach.                                 |
   //|                                                                   |
   //| FIX (v5.46): two correctness guarantees the MathAbs form could    |
   //| not express:                                                      |
   //|                                                                   |
   //|   1. LOSS-SIDE ISOLATION. Capital at risk is only the stop        |
   //|      distance on the LOSING side of entry. MathAbs(entry - sl)    |
   //|      folded both sides together, so once the unified trail        |
   //|      ratcheted a stop to break-even or into profit, the distance  |
   //|      was still positive and the leg reported a risk percentage it |
   //|      could no longer lose -- a false CRITICAL against a trade that|
   //|      was already risk-free. The signed test below yields <= 0 for |
   //|      a stop on the profit side, so such a leg contributes nothing.|
   //|                                                                   |
   //|   2. ADOPTED LEG VISIBILITY. Every other book read in this class  |
   //|      is magic-scoped, which makes an adopted magic-0 leg invisible|
   //|      by construction -- correct for the desync test, but it also  |
   //|      excluded the adopted primary from the risk audit entirely, so |
   //|      the one leg the operator did NOT size was the one leg never  |
   //|      checked. The magic filter is therefore widened by exactly one|
   //|      ticket: the tracked primary while m_trackedAdopted is set.   |
   //|      The exception is keyed to the EXACT tracked ticket, so an     |
   //|      unrelated manual or foreign-EA magic-0 leg still cannot be    |
   //|      counted -- the blast radius stays one position.              |
   //|                                                                   |
   //| v5.63 adds a SECOND, independent leg to this body: the SUM of the  |
   //| loss-side risk across the basket, measured against the stacked     |
   //| design budget (RiskPercent + InpRiskT2Pct + InpRiskT3Pct +        |
   //| InpRiskT4Pct, plus the same 50% tolerance). The per-leg peak above |
   //| cannot see it: four legs each comfortably inside 1.5% can still    |
   //| sum past what the pyramid was designed to carry. The two checks    |
   //| share the loop and the loss-side guard, and each owns its own      |
   //| latch, so a basket can report either, both, or neither.            |
   //+------------------------------------------------------------------+
   void              AuditRiskExposure(void)
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity <= 0.0) return;

      double tickSize  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      double tickValue = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      if(tickSize <= 0.0 || tickValue <= 0.0) return;

      // Allow a 50% tolerance buffer over the leg's OWN rung budget to absorb
      // market execution slippage. FIX (v5.67): the allowance used to be
      // RiskPercent * 1.50 for EVERY leg, which is only correct for tranche 1.
      // The ladder sizes T2/T3/T4 from InpRiskT2Pct/3Pct/4Pct, so a rung
      // configured ABOVE RiskPercent (a perfectly legal scale-in) read as a
      // breach while being sized exactly as designed -- and, symmetrically, a
      // rung configured BELOW it could oversize by a large multiple and still
      // pass. The allowance is now the rung's own nominal risk, resolved from
      // the leg's tranche tag, with RiskPercent as the fallback for a leg that
      // carries no recognisable tag (the primary, or an adopted manual leg).
      double maxAllowedRiskPct = RiskPercent * 1.50;
      double peakRiskPct = 0.0;
      ulong  peakTicket  = 0;
      // v5.67: the WORST leg by budget RATIO (risk / its own rung allowance),
      // plus the allowance that ratio was measured against. A ratio > 1.0 is
      // the only form of the test that works once each leg carries a different
      // nominal budget.
      double peakRatio    = 0.0;
      double peakAllowPct = maxAllowedRiskPct;
      // v5.63: the SUMMED loss-side exposure of the basket. peakRiskPct above
      // measures the worst SINGLE leg and is deliberately blind to how many
      // legs are stacked behind the shared stop; this accumulates them. Both
      // are needed because they fail differently -- a single oversized leg is
      // a sizing bug, while four correctly-sized legs summing past the design
      // budget is a pyramid bug, and only the sum can see the second one.
      double sumRiskPct = 0.0;
      // v5.67: summed rung budget of the legs that are actually carrying the
      // risk above. Declared here, beside the sum it is compared against,
      // because MQL5 has no hoisting -- the loop below accumulates both.
      double budgetedStackPct = 0.0;

      for(int idx = PositionsTotal() - 1; idx >= 0; idx--)
        {
         if(PositionGetTicket(idx) <= 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;

         ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
         long  magic  = PositionGetInteger(POSITION_MAGIC);

         // FIX (v5.46): magic-scoped, with ONE exception - the tracked primary
         // while it is an ADOPTED magic-0 leg. Keyed to the exact TICKET, so
         // an unrelated manual / foreign-EA leg is still excluded.
         if(magic != (long)m_magic)
           {
            if(!m_trackedAdopted || ticket != m_trackedPrimary) continue;
           }

         bool   isLong = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl     = PositionGetDouble(POSITION_SL);
         double vol    = PositionGetDouble(POSITION_VOLUME);

         if(sl <= 0.0) continue;

         // FIX (v5.46): LOSS-SIDE ONLY. A stop at or past break-even is on the
         // profit side, where lossDist is <= 0 and the leg carries no risk.
         double lossDist = isLong ? (entry - sl) : (sl - entry);
         if(lossDist <= 0.0) continue;

         double distPts     = lossDist / tickSize;
         double moneyAtRisk = distPts * tickValue * vol;
         double posRiskPct  = (moneyAtRisk / equity) * 100.0;

         // FIX (v5.67): per-leg budget = the leg's own RUNG. See RungRiskPct().
         // The breach test is a RATIO, not a raw maximum: a small leg can be
         // over its own budget while a larger, better-budgeted leg sits
         // comfortably inside its own, so comparing the two raw percentages
         // would hide the violation. Track the worst offender by ratio.
         double legBudgetPct = RungRiskPct(PositionGetString(POSITION_COMMENT));
         double legAllowPct  = legBudgetPct * 1.50;
         double legRatio     = (legAllowPct > 0.0) ? (posRiskPct / legAllowPct)
                                                   : ((posRiskPct > 0.0) ? 999.0 : 0.0);
         if(legRatio > peakRatio)
           {
            peakRatio    = legRatio;
            peakRiskPct  = posRiskPct;
            peakTicket   = ticket;
            peakAllowPct = legAllowPct;
           }

         // v5.63: accumulate the same loss-side percentage into the basket sum.
         // Only legs already past the lossDist > 0 guard above reach here, so a
         // stop on the profit side contributes nothing on either measure.
         sumRiskPct += posRiskPct;
         // v5.67: ... and accumulate the budget of the legs that are actually
         // carrying that risk, so the aggregate test is measured against what
         // the ladder is entitled to expose RIGHT NOW rather than against the
         // full ladder's worth of nominal risk (which the unified stop makes
         // unreachable -- see the aggregate block).
         budgetedStackPct += legBudgetPct;
        }

      if(peakRatio > 1.0)
        {
         DispatchAlertOnce(m_alertSent_RiskBreach,
                           "HighTable: position risk breach",
                           "Ticket #" + IntegerToString((long)peakTicket) +
                           " risk is " + DoubleToString(peakRiskPct, 2) +
                           "% of equity, exceeding its rung allowance " +
                           DoubleToString(peakAllowPct, 2) + "% (nominal " +
                           DoubleToString(peakAllowPct / 1.50, 2) + "% + 50%," +
                           " " + DoubleToString(peakRatio, 2) + "x).",
                           HT_SEV_WARN);
        }
      else
        {
         ClearLatch(m_alertSent_RiskBreach);
        }

      // ---- v5.63 / v5.67: the AGGREGATE leg -------------------------------
      // The per-leg test above is blind to how many legs are stacked behind
      // the shared stop: four legs each inside their own budget can still sum
      // past the design.
      //
      // FIX (v5.67): the allowance this leg compared against made it
      // UNREACHABLE, and silently so. It summed ALL FOUR nominal rungs
      // (RiskPercent + T2 + T3 + T4 = 2.75% at the defaults, x1.5 = 4.125%)
      // while the basket's real exposure is capped far below that by the
      // unified stop:
      //   * before +1.0R only tranche 1 exists, so the sum is one rung;
      //   * from +1.0R the ladder's first floor is cost-covering breakeven,
      //     so EVERY leg's lossDist is <= 0 and the sum is ZERO.
      // The greatest sum this ladder can ever produce is therefore ~1.0%
      // against a 4.125% allowance -- a tolerance of more than 4x. The check
      // could not fire for any input, which is worse than not having it.
      //
      // The invariant it SHOULD have asserted is the one that actually
      // protects the account: the summed risk must not exceed the summed
      // budget of the legs that are OPEN AND ON THE LOSS SIDE, plus the same
      // 50% tolerance. Legs already through breakeven drop out of `sumRiskPct`
      // and out of the allowance together, so the ratio stays meaningful at
      // every stage of the ladder instead of dissolving at +1.0R. This is the
      // real signature of an oversized stack, and of the partial-stop-out
      // shape where a rejected leg keeps its old, looser stop.
      double nominalStackPct    = RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct;
      double maxAllowedStackPct = budgetedStackPct * 1.50;
      if(sumRiskPct > maxAllowedStackPct && budgetedStackPct > 0.0)
        {
         if(m_aggRiskSeen)
           {
            DispatchAlertOnce(m_alertSent_AggRiskBreach,
                              "HighTable: aggregate basket risk breach",
                              "Summed loss-side risk across the basket is " +
                              DoubleToString(sumRiskPct, 2) +
                              "% of equity, above the stacked allowance " +
                              DoubleToString(maxAllowedStackPct, 2) + "% (" +
                              DoubleToString(budgetedStackPct, 2) +
                              "% of rung budget on the legs still at risk + 50%)." +
                              " The full ladder is worth " +
                              DoubleToString(nominalStackPct, 2) +
                              "% if every rung were simultaneously exposed, so" +
                              " this is MORE risk than the design can produce -" +
                              " the usual cause is a leg the venue refused to" +
                              " tighten, holding a stop the unified floor had" +
                              " already moved past.",
                              HT_SEV_WARN);
           }
         else
           {
            m_aggRiskSeen = true;
           }
        }
      else
        {
         ClearLatch(m_alertSent_AggRiskBreach);
         m_aggRiskSeen = false;
        }
     }
  };
#endif  // __OTTO_HIGH_TABLE_AUDITOR__
