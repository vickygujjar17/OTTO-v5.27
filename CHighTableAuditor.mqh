//+------------------------------------------------------------------+
//|                                           CHighTableAuditor.mqh  |
//|        MODULE - "High Table" decoupled live-state watchdog       |
//|        Read-only audit + CSV evidence trail + email dispatcher    |
//|        Runs on its own timer cadence, independent of OnTick       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.32"

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

public:
                     CHighTableAuditor(void)
     {
      m_symbol     = "";
      m_magic      = 0;
      m_ready      = false;
      m_dispatched = 0;
      m_suppressed = 0;
      m_lastAudit  = 0;
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
   //| PART 2 - observation slots.                                       |
   //|                                                                   |
   //| Deliberately side-effect free stubs so the call graph, the latch  |
   //| inventory and the dispatcher contract are final in this release.  |
   //| Part 2 fills these bodies; it does not re-plumb the auditor.      |
   //+------------------------------------------------------------------+
   void              AuditTrimHealth(void)
     {
      // PART 2: on a 0.90% floating-loss breach, observe whether the smart
      // trim actually closed a non-primary leg. If the breach is live and no
      // leg was closed, the trim could not act -> DispatchAlertOnce(
      // m_alertSent_TrimFailure, ...). Clear the latch when float recovers.
     }

   void              AuditDrawdown(void)
     {
      // PART 2: recompute daily / trailing-total / floating drawdown from
      // ACCOUNT_EQUITY+BALANCE and latch each breach independently, clearing
      // each latch once the corresponding measure is back inside limits.
     }

   void              AuditOrderHealth(void)
     {
      // PART 2: watch the broker reject burst and any position whose stop
      // could not be modified, latching per incident rather than per tick.
     }

   void              AuditStateConsistency(void)
     {
      // PART 2: cross-check the live position book against the tracked
      // basket/active-trade view and alert once on divergence.
     }
  };
#endif  // __OTTO_HIGH_TABLE_AUDITOR__

   bool              m_alertSent_StateInconsistency; // book vs tracked state
