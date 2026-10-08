//+------------------------------------------------------------------+
//|                                                   COttoJournal.mqh |
//|                    Live per-session .txt journal + SendMail          |
//|            OTTO EA — dynamic file editing, cancellation, email       |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.44"

#ifndef __OTTO_JOURNAL__
#define __OTTO_JOURNAL__

#include "OttoDefines.mqh"

class COttoJournal
  {
private:
   string            m_symbol;
   string            m_sessionID;
   int               m_handle;
   bool              m_ready;
   COttoBlockManager *m_blockManager;   // optional, supplied for 2-arg Initialize()

   string            FmtPrice(double p)
     { return DoubleToString(p, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)); }
   string            FmtPips(double d)
     {
      double pt = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      if(pt <= 0) return "0.0";
      return DoubleToString(d / (pt * 10.0), 1);
     }

   string            FmtDuration(datetime o, datetime c)
     {
      long s = (long)(c - o);
      if(s < 0) s = 0;
      long h = s / 3600;
      long mm = (s % 3600) / 60;
      return IntegerToString((int)h) + " hours " + IntegerToString((int)mm) + " minutes";
     }

   string            SubjectLine(void)
     {
      string sid = m_sessionID;
      if(StringFind(sid, "#") == 0) sid = StringSubstr(sid, 1);
      string p[];
      int n = StringSplit(sid, '-', p);
      if(n >= 4) return "#OTTO-" + p[1] + "-" + p[2] + "-" + p[3] + "-T1";
      return "#OTTO-" + m_symbol + "-T1";
     }

   // Per-session file name: [Symbol]_[Timestamp]_BLK[Serial].txt
   // m_sessionID is built upstream as  #OTTO-<SYM>-<YYYYMMDD>-<HHMMSS>-BLK<n>
   string            SessionFileName(void)
     {
      string sid = m_sessionID;
      if(StringFind(sid, "#") == 0) sid = StringSubstr(sid, 1);
      string parts[];
      int n = StringSplit(sid, '-', parts);
      // parts == { OTTO, <SYM>, <YYYYMMDD>, <HHMMSS>, BLK<n> }
      string sym  = (n >= 2) ? parts[1] : m_symbol;
      string ts   = (n >= 4) ? (parts[2] + "_" + parts[3]) : "";
      string blk  = (n >= 5) ? parts[4] : "BLK0";
      string nm   = sym + "_" + ts + "_" + blk + ".txt";
      // Strip anything the filesystem would reject
      int len = StringLen(nm);
      string safe = "";
      for(int k = 0; k < len; k++)
        {
         int ch = StringGetCharacter(nm, k);
         if((ch>=48 && ch<=57) || (ch>=65 && ch<=90) || (ch>=97 && ch<=122) || ch==45 || ch==95 || ch==46)
            safe += ShortToString((ushort)ch);
         else
            safe += "_";
        }
      if(StringLen(safe) > 0) return safe;
      return m_symbol + "_" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "_BLK0.txt";
     }

   bool              OpenWrite(void)
     { m_handle = FileOpen(SessionFileName(), FILE_TXT | FILE_WRITE | FILE_SHARE_READ); return (m_handle != INVALID_HANDLE); }

   bool              OpenAppend(void)
     {
      m_handle = FileOpen(SessionFileName(), FILE_TXT | FILE_READ | FILE_WRITE | FILE_SHARE_READ);
      if(m_handle == INVALID_HANDLE) return false;
      FileSeek(m_handle, 0, SEEK_END);
      return true;
     }

   void              W(string s)
     {
      if(m_handle != INVALID_HANDLE)
        { FileWriteString(m_handle, s + "\n"); FileFlush(m_handle); }
     }

   void              CloseHandle(void)
     {
      if(m_handle != INVALID_HANDLE)
        { FileClose(m_handle); m_handle = INVALID_HANDLE; }
     }

   int               ReadLines(string &lines[], string fname = "")
     {
      if(fname == "") fname = SessionFileName();
      int fh = FileOpen(fname, FILE_TXT | FILE_READ | FILE_SHARE_READ);
      if(fh == INVALID_HANDLE) return 0;
      string tmp[]; int c = 0;
      while(!FileIsEnding(fh))
        {
         string l = FileReadString(fh);
         if(l == "") break;
         ArrayResize(tmp, c + 1); tmp[c] = l; c++;
        }
      FileClose(fh);
      ArrayResize(lines, c);
      for(int i = 0; i < c; i++) lines[i] = tmp[i];
      return c;
     }

   // Emails the whole session file (line[0] = subject).
   // Called ONLY by LogExit(): cancellation no longer emails, so the
   // CANCELLED_ fallback read below is retained but no longer reached.
   void              SendMailFromFile(void)
     {
      if(!m_ready) return;
      string lines[];
      int c = ReadLines(lines, "CANCELLED_" + SessionFileName());
      if(c == 0) c = ReadLines(lines);
      if(c == 0) return;
      string subject = lines[0];
      string body = "";
      for(int i = 0; i < c; i++)
        { body += lines[i]; if(i < c-1) body += "\n"; }
      SendMail(subject, body);
      if(EnableLogging) Print("[Journal] EMAIL sent: ", subject);
     }

public:
                     COttoJournal(void)
     { m_symbol=""; m_sessionID=""; m_handle=INVALID_HANDLE; m_ready=false; m_blockManager=NULL; }
                     ~COttoJournal(void) { CloseHandle(); }

   bool              Initialize(string symbol, COttoBlockManager *bm = NULL)
     {
      m_symbol = symbol;
      m_blockManager = bm;
      return true;
     }
   void              Close(void) { CloseHandle(); }
   void              SetSessionID(string id) { m_sessionID = id; m_ready = (m_sessionID != ""); }
   string            GetSessionID(void) const { return m_sessionID; }

   //+--------------------------------------------------------------+
   //| LOG SETUP ARMED - create file + SUBJECT line + setup details.   |
   //| v5.40: the hybrid router arms a setup EITHER as a physical       |
   //| broker pending limit (price valid for a resting order) OR as an  |
   //| in-memory virtual order (price already through the zone). This   |
   //| CREATES the per-setup journal either way and stamps the two       |
   //| facts the operator needs on the header: the N/R Phase (Normal    |
   //| Touch-1 bounce vs Reversal Touch-2) and the Execution Mode       |
   //| (PHYSICAL vs VIRTUAL). The later entry/cancel/exit append into   |
   //| the same file; the email ships the full snapshot.                |
   //+--------------------------------------------------------------+
   void              LogSetupArmed(bool physical, string tradeId, ENUM_TRADE_DIRECTION dir, ENUM_BLOCK_TYPE btype, double entryPrice, double slPrice, double lotSize, const SSniperBlock &blk)
     {
      if(!m_ready) return;
      if(!OpenWrite()) return;
      string phase    = (blk.touches == 0) ? "N (Normal Bounce)" : "R (Reversal)";
      string execMode = physical ? "PHYSICAL (Broker Limit Order)" : "VIRTUAL (In-Memory Arm)";
      W("SUBJECT: [ACTIVE] Session " + SubjectLine());
      W("================================================================");
      W("[SETUP ARMED] Session " + m_sessionID + " | " + m_symbol + " (" + (dir==DIR_LONG?"BUY / LONG":"SELL / SHORT") + ") | " + TimeToString(TimeCurrent()));
      W("  Setup ID          : " + tradeId);
      W("  Phase             : " + phase);
      W("  Execution Mode    : " + execMode);
      W("  Trigger Price     : " + FmtPrice(entryPrice));
      W("  Initial SL        : " + FmtPrice(slPrice));
      W("  Volume            : " + DoubleToString(lotSize,2));
      W("  Block Polarity    : " + (btype==BLOCK_SUPPORT?"SUPPORT":"RESISTANCE"));
      W("  Wick 1 (Anchor)   : " + TimeToString(blk.wick1Time) + " H=" + FmtPrice(blk.wick1.highPrice) + " L=" + FmtPrice(blk.wick1.lowPrice));
      W("  Wick 2 (Retest)   : " + TimeToString(blk.wick2Time) + " H=" + FmtPrice(blk.wick2.highPrice) + " L=" + FmtPrice(blk.wick2.lowPrice));
      W("  Separation        : " + (blk.vetoReason==VETO_NO_SEPARATION?"FAILED":"PASSED"));
      W("================================================================");
      CloseHandle();
      // v5.42: NO email here. Arming a setup is an intermediate lifecycle
      // stage, not a session endpoint, so this write stays silent on disk.
      // Only LogExit() ships the aggregated session file; every earlier
      // append simply accumulates into it.
     }
   void              LogEntry(ulong ticket, ENUM_TRADE_DIRECTION dir, double entryPrice, double slPrice, double lotSize, double riskMoney, const SSniperBlock &blk)
     {
      if(!m_ready) return;
      // v5.38: APPEND into the per-setup file the arm() created, so the armed
      // snapshot and the fill share ONE journal. OpenAppend() seeks to EOF and
      // fails when the file is absent (a manual adoption, or a legacy session),
      // in which case OpenWrite() creates it -- same fallback the pyramid /
      // trail / cancellation writers already use.
      if(!OpenAppend())
        {
         if(!OpenWrite()) return;   // no prior file -> create it
        }
      double riskDist = MathAbs(entryPrice - slPrice);
      W("SUBJECT: [ACTIVE] Session " + SubjectLine());
      W("================================================================");
      W("[TRADE ENTRY] Session " + m_sessionID + " | " + m_symbol + " (" + (dir==DIR_LONG?"BUY / LONG":"SELL / SHORT") + ") | " + TimeToString(TimeCurrent()));
      W("  Execution Price   : " + FmtPrice(entryPrice));
      W("  Initial Stop Loss : " + FmtPrice(slPrice) + " | 1R = " + FmtPrice(blk.rrUnit));
      W("  Volume & Sizing   : " + DoubleToString(lotSize,2) + " Lots | Risk: $" + DoubleToString(riskMoney,2));
      W("  S/R Block:");
      W("    - Polarity  : " + (blk.type==BLOCK_SUPPORT?"SUPPORT":"RESISTANCE"));
      W("    - Zone      : Top=" + FmtPrice(blk.top) + " | Bottom=" + FmtPrice(blk.bottom));
      if(blk.wick1Time > 0 && blk.wick2Time > 0)
        {
         W("    - Wick1     : " + TimeToString(blk.wick1Time) + " H=" + FmtPrice(blk.wick1.highPrice) + " L=" + FmtPrice(blk.wick1.lowPrice));
         W("    - Wick2     : " + TimeToString(blk.wick2Time) + " H=" + FmtPrice(blk.wick2.highPrice) + " L=" + FmtPrice(blk.wick2.lowPrice));
        }
      W("    - Separation  : " + (blk.vetoReason==VETO_NO_SEPARATION?"FAILED":"PASSED"));
      W("================================================================");
      CloseHandle();
      // v5.42: NO email here. A fill is an intermediate lifecycle stage, not
      // a session endpoint, so this write stays silent on disk. Only
      // LogExit() ships the aggregated session file.
     }

   void              LogPyramid(int tranche, ulong ticket, double entry, double size, double riskPct, double groupSL)
     {
      if(!m_ready) return;
      if(!OpenAppend()) return;
      W("  [PYRAMID] Tranche " + IntegerToString(tranche) + " | T#" + IntegerToString((int)ticket) + " | Price=" + FmtPrice(entry) + " | Lots=" + DoubleToString(size,2) + " | Risk=" + DoubleToString(riskPct,2) + "%");
      W("  Unified Group SL  : " + FmtPrice(groupSL));
      CloseHandle();
     }

   // v5.33 -- MANUAL TRADE ADOPTION record.
   // Self-contained by design: the generic logging entry point takes an
   // SSniperBlock&, and an adopted hand-opened position has no Pine block
   // behind it -- there is no block to pass and no block fields to print.
   // This mirrors LogPyramid()'s shape instead: append to the session file.
   void              LogManualAdoption(ulong ticket, ENUM_TRADE_DIRECTION dir,
                                       double entry, double sl, double lot, double slDistance)
     {
      if(!m_ready) return;
      if(!OpenAppend())
        {
         if(!OpenWrite()) return;   // no file yet -> create it
        }
      double pt     = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double slPips = (pt > 0.0) ? (slDistance / (pt * 10.0)) : 0.0;
      W("================================================================");
      W("[MANUAL ADOPTION] Session " + m_sessionID + " | " + m_symbol +
        " | " + TimeToString(TimeCurrent()));
      W("  Origin            : Hand-opened position (magic 0) taken over by the EA");
      W("  Ticket            : #" + IntegerToString((int)ticket));
      W("  Direction         : " + (dir == DIR_LONG ? "BUY / LONG" : "SELL / SHORT"));
      W("  Entry Price       : " + FmtPrice(entry));
      W("  Working Stop      : " + FmtPrice(sl));
      W("  Initial Risk (1R) : " + FmtPrice(slDistance) +
        "  (" + DoubleToString(slPips, 1) + " pips)");
      W("  Volume            : " + DoubleToString(lot, 2) + " lots");
      W("  NOTE              : trail, ladder, trim, DD halt and the basket");
      W("                      close all now apply to this ticket.");
      W("================================================================");
      CloseHandle();
     }

   void              LogTrailUpdate(string milestone, double groupSL)
     {
      if(!m_ready) return;
      if(!OpenAppend()) return;
      W("  [GROUP STOP] " + milestone + " | Unified SL = " + FmtPrice(groupSL) + " | " + TimeToString(TimeCurrent()));
      CloseHandle();
     }

   void              LogExit(ulong ticket, ENUM_TRADE_DIRECTION dir, double entryPrice, double exitPrice, double lotSize, double grossProfit, double commission, double swap, datetime openTime, string exitReason)
     {
      if(!m_ready) return;
      if(!OpenAppend()) return;
      double net = grossProfit + commission + swap;
      W("================================================================");
      W("[TRADE EXIT] Session " + m_sessionID + " | " + m_symbol + " (" + (dir==DIR_LONG?"BUY / LONG":"SELL / SHORT") + ") | " + TimeToString(TimeCurrent()));
      W("  Entry Price       : " + FmtPrice(entryPrice));
      W("  Exit Price        : " + FmtPrice(exitPrice));
      W("  Exit Reason       : " + exitReason);
      W("  Gross Result      : " + (grossProfit>=0?"+":"") + DoubleToString(grossProfit,2));
      W("  Duration          : " + FmtDuration(openTime, TimeCurrent()));
      W("  Net Profit/Loss   : " + (net>=0?"+":"") + DoubleToString(net,2));
      W("================================================================");
      CloseHandle();
      SendMailFromFile();
     }

   // Vetoed / cancelled before fill: append the reason to the active log,
   // close the handle, then rename on disk so the historical setup data is
   // preserved under a clear CANCELLED_ prefix.
   void              LogCancellation(string reason)
     {
      if(!m_ready) return;

      string fname = SessionFileName();

      // 1) Append the cancellation reason to the active text log
      if(!OpenAppend())
        {
         if(!OpenWrite()) return;   // no prior file -> create it
        }
      W("================================================================");
      W("[CANCELLED] Reason: " + reason + " | " + TimeToString(TimeCurrent()));
      W("================================================================");

      // 2) Close the file handle so the OS releases the file lock
      CloseHandle();

      // 3) Rename on disk, preserving the setup history
      string cancelledName = "CANCELLED_" + fname;
      FileDelete(cancelledName);                       // clear any stale target
      if(FileMove(fname, 0, cancelledName, FILE_REWRITE))
        {
         if(EnableLogging) Print("[Journal] CANCELLED -> ", cancelledName);
        }
      else
        {
         int err = GetLastError();
         Print("[Journal] FileMove FAILED (err=", err, ") for ", fname, " -> ", cancelledName);
        }

      // NO email here: routine block vetoes and setup cancels would otherwise
      // spam the operator's mailbox; the full detail is already preserved on
      // disk under the CANCELLED_ prefix above. Only a closed trade exit
      // (LogExit) ships a Trade Summary email.
     }

   //+----------------------------------------------------------------+
   //| LOG CONVERSION (v5.36) - Phase 1 dropped for Phase 2 Reversal  |
   //| Appends a conversion notice to the session journal. v5.42: no  |
   //| longer emails -- LogExit() is the only sender.                 |
   //| Unlike LogCancellation() this does NOT rename the session file |
   //| to CANCELLED_ : the setup is still live, merely re-phased.     |
   //+----------------------------------------------------------------+
   void              LogConversion(ulong ticket, ENUM_BLOCK_TYPE btype, string reason)
     {
      if(!m_ready) return;
      if(!OpenAppend())
        {
         if(!OpenWrite()) return;   // no prior file -> create it
        }
      W("================================================================");
      W("[CONVERTED TO REVERSAL] Session " + m_sessionID + " | " + m_symbol + " | " + TimeToString(TimeCurrent()));
      W("  Ticket Dropped    : #" + IntegerToString((int)ticket));
      W("  Block Polarity    : " + (btype == BLOCK_SUPPORT ? "SUPPORT" : "RESISTANCE"));
      W("  Trigger Event     : " + reason);
      W("  Next Action       : Phase 1 order cancelled; Block upgraded to Phase 2 Reversal.");
      W("================================================================");
      CloseHandle();
      // v5.42: NO email here. A re-phase is an intermediate lifecycle stage,
      // not a session endpoint -- LogExit() is the only writer that ships
      // the aggregated session file.
     }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_JOURNAL__