"""
v5.48b static verification probe - HOURLY FILE-TO-EMAIL STATUS REPORT.

The operator gets one periodic snapshot of this symbol's state each hour, on
top of the per-trade exit email the journal already ships. The report follows a
single contract:

  * a FLAT text file is WRITTEN, closed, and then RE-OPENED and mailed, so the
    artifact on disk and the email body are the same bytes;
  * line 0 is the subject (the same "SUBJECT: ..." convention the per-session
    journal uses), so one file is simultaneously a readable log and a message;
  * SendMail() is suppressed in the Strategy Tester, which cannot dispatch
    mail, but the FILE is still written so a tester run still leaves evidence;
  * the file is NOT deleted afterwards -- the leftover .txt IS the audit trail.

The report does NOT go through COttoJournal. SendMailFromFile() there is pinned
to exactly ONE call site (LogExit) by test_v542_email.py and
test_v545_throttle.py, so an hourly sender must own its own SendMail call. That
constraint is re-asserted below, because the obvious "reuse the journal"
implementation would silently break those probes and the one-email-per-trade
rule they encode.

The properties pinned here are structural and ORDER-sensitive (write before
read, gate before send, tester-guard before SendMail). Pure static analysis of
the shipped sources; no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so the multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


def _rel_parts(stamps):
    """Numeric (major, minor) of the common release stamp; [] -> (0, 0).

    Bump-tolerant, matching the v5.47 probe convention: assert the sources
    AGREE on one stamp that is AT LEAST the release this probe shipped with,
    rather than pinning a literal that fails on every later bump (a probe
    defect, not a code defect)."""
    if not stamps:
        return (0, 0)
    major, minor = sorted(set(stamps))[0].split(".")
    return (int(major), int(minor))


def strip_comments(code):
    """Drop // comments, respecting string literals (see v5.32 probe)."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def func_body(code, sig):
    """Return the { ... } block following the first line matching `sig`."""
    m = re.search(sig, code)
    if not m:
        return None
    i = code.find("{", m.end())
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[m.start():j + 1]
    return code[m.start():]


OM_T = read(OM)
OM_C = strip_comments(OM_T)
JRN_T = read(JRN)
BM_C = read(os.path.join(ROOT, "COttoBlockManager.mqh"))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


GATE = func_body(OM_C, r"void\s+EmitHourlyStatusReportIfDue\s*\(")
SEND = func_body(OM_C, r"void\s+SendHourlyStatusReport\s*\(")
BODY = func_body(OM_C, r"string\s+ReportBody\s*\(")
UPD = func_body(OM_C, r"void\s+Update\s*\(")
INIT = func_body(OM_C, r"bool\s+Initialize\s*\(")
CTOR = func_body(OM_C, r"COttoOrderManager\s*\(\s*void\s*\)")

# ----------------------------------------------------------------------
# 1. The hook is wired in FIRST, before anything can mutate the book
# ----------------------------------------------------------------------
print("\n-- Update() hook --")

check("Update() located", UPD is not None)
check("Update() calls EmitHourlyStatusReportIfDue()",
      UPD is not None and
      re.search(r"^\s*EmitHourlyStatusReportIfDue\s*\(\s*\)\s*;", UPD, re.M) is not None)
if UPD is not None:
    hook = UPD.find("EmitHourlyStatusReportIfDue();")
    rev = UPD.find("CompleteReversal();")
    fill = UPD.find("CheckPendingOrderFills();")
    virt = UPD.find("CheckVirtualTriggers();")
    check("the hook runs BEFORE CompleteReversal()",
          hook >= 0 and rev >= 0 and hook < rev, "hook=%d rev=%d" % (hook, rev))
    check("the hook runs BEFORE the physical fill scan",
          hook >= 0 and fill >= 0 and hook < fill, "hook=%d fill=%d" % (hook, fill))
    check("the hook runs BEFORE the virtual trigger",
          hook >= 0 and virt >= 0 and hook < virt, "hook=%d virt=%d" % (hook, virt))
    check("the existing four Update() stages are all still present",
          rev >= 0 and fill >= 0 and virt >= 0 and "AdoptManualPosition();" in UPD)

# ----------------------------------------------------------------------
# 2. The interval is one hour, declared once
# ----------------------------------------------------------------------
print("\n-- Interval --")

check("OTTO_HOURLY_REPORT_SECONDS is defined as 3600",
      re.search(r"#define\s+OTTO_HOURLY_REPORT_SECONDS\s+3600\b", OM_T) is not None)
check("the gate compares against the named constant, not a magic number",
      GATE is not None and
      "OTTO_HOURLY_REPORT_SECONDS" in GATE and
      not re.search(r"<\s*3600\b", GATE))

# ----------------------------------------------------------------------
# 3. The clock is seeded, so the hour starts at init / re-init
# ----------------------------------------------------------------------
print("\n-- Clock seeding --")

check("m_lastHourlyReport is declared private",
      re.search(r"^\s+datetime\s+m_lastHourlyReport\s*;", OM_T, re.M) is not None)
check("the constructor zeroes the stamp",
      CTOR is not None and
      re.search(r"m_lastHourlyReport\s*=\s*0\s*;", CTOR) is not None)
check("Initialize() arms the stamp from the broker clock",
      INIT is not None and
      re.search(r"m_lastHourlyReport\s*=\s*TimeCurrent\s*\(\s*\)\s*;", INIT) is not None)
check("the gate uses the broker clock, never TimeLocal()",
      GATE is not None and "TimeCurrent()" in GATE and "TimeLocal()" not in GATE)

# ----------------------------------------------------------------------
# 4. The file -> email contract, and its ORDER
# ----------------------------------------------------------------------
print("\n-- File -> email contract --")

check("SendHourlyStatusReport located", SEND is not None)
check("the report writes a .txt artifact beside the terminal",
      SEND is not None and "_HourlyReport.txt" in SEND)
check("line 0 is the SUBJECT line (the journal's own convention)",
      SEND is not None and '"SUBJECT: [STATUS] "' in SEND)

if SEND is not None:
    w   = SEND.find("FileOpen(fname, FILE_TXT | FILE_WRITE)")
    sub = SEND.find('"SUBJECT: [STATUS] "')
    cl  = SEND.find("FileClose(fh)")
    ro  = SEND.find("FileOpen(fname, FILE_TXT | FILE_READ | FILE_SHARE_READ)")
    s0  = SEND.find("string subject = lines[0];")
    ml  = SEND.find("SendMail(subject, body)")
    check("the file is opened for WRITE first",
          w >= 0 and sub > w, "write=%d subject=%d" % (w, sub))
    check("the SUBJECT line is written into the FIRST line of the file",
          sub >= 0 and cl > sub, "subject=%d close=%d" % (sub, cl))
    check("the write handle is CLOSED before the file is re-opened",
          cl >= 0 and ro > cl, "close=%d reopen=%d" % (cl, ro))
    check("the mail subject is read back OUT of the file (lines[0])",
          ro >= 0 and s0 > ro, "reopen=%d lines0=%d" % (ro, s0))
    check("SendMail() is called only after the read-back",
          s0 >= 0 and ml > s0, "lines0=%d sendmail=%d" % (s0, ml))
    check("the artifact is NOT deleted (it is the audit trail)",
          "FileDelete" not in SEND)

# ----------------------------------------------------------------------
# 5. The tester cannot dispatch mail, so the send is guarded
# ----------------------------------------------------------------------
print("\n-- Strategy-Tester guard --")

if SEND is not None:
    g = SEND.find("MQLInfoInteger(MQL_TESTER)")
    ml = SEND.find("SendMail(subject, body)")
    check("SendMail() sits behind an MQL_TESTER guard",
          g >= 0 and ml >= 0 and g < ml, "guard=%d sendmail=%d" % (g, ml))
check("the tester branch still leaves the file behind (not an early return)",
      SEND is not None and
      re.search(r"else\s+if\(EnableLogging\)\s*\n\s*Print\(\"\[OrderManager\] "
                r"Hourly report file written", SEND) is not None)
check("the guard matches the house idiom used by the High Table auditor",
      read(os.path.join(ROOT, "CHighTableAuditor.mqh")).count(
          "MQLInfoInteger(MQL_TESTER)") >= 1)

# ----------------------------------------------------------------------
# 6. The report can actually report something
# ----------------------------------------------------------------------
print("\n-- Report body --")

check("ReportBody() located", BODY is not None)
for field in ["[BOOK]", "[COUNTERS]", "[RISK]", "[MARKET]"]:
    check("the body carries a %s section" % field,
          BODY is not None and field in BODY)
check("the body reads the live basket and says when there is none",
      BODY is not None and "m_hasActiveTrade" in BODY)
check("the body reports the armed-setup count via CountArmedBlocks()",
      BODY is not None and "CountArmedBlocks()" in BODY)
check("CountArmedBlocks() is a real helper",
      re.search(r"int\s+CountArmedBlocks\s*\(void\)", OM_C) is not None)
check("CountArmedBlocks() consults the block book, not the pending book",
      "GetBlockCount()" in OM_C and "isVetoed" in
      (func_body(OM_C, r"int\s+CountArmedBlocks\s*\(") or ""))
check("the body reports the hybrid pending count",
      BODY is not None and "CountMyPendingOrders()" in BODY)

# ----------------------------------------------------------------------
# 6b. v5.51 Part 3 -- the per-setup label in the HOURLY report
# ----------------------------------------------------------------------
# v5.52 collapsed the R/C phase to ONE constant: every armed setup is the same
# continuation (a breakout above resistance / breakdown below support), so the
# report no longer discriminates on a touch count -- the field is gone from the
# tree. The per-serial walk is KEPT because it still proves each armed order
# resolves to a live block, and an unresolved one must degrade to a VISIBLE
# UNKNOWN rather than a blank.
print("\n-- Per-setup label in the hourly report (v5.51 Part 3) --")

check("the report walks the live virtual book (armed setups)",
      BODY is not None and "m_virtualOrders" in BODY and "m_virtualCount" in BODY)
check("it re-resolves each armed setup's block by SERIAL, not by index",
      BODY is not None and "FindBlockIndexBySerial(" in BODY)
check("it reads the block snapshot it just resolved",
      BODY is not None and re.search(r"GetBlockAt\s*\(\s*bIdx\s*,", BODY) is not None)
check("the label is the constant continuation (v5.52)",
      BODY is not None and
      re.search(r'phaseLabel\s*=\s*"C \(Continuation\)"', BODY) is not None)
check("the old touch-count discriminator is gone",
      BODY is not None and "b.touches" not in BODY)
check("the Continuation label is spelled out",
      BODY is not None and "C (Continuation)" in BODY)
check("an unresolved block degrades to a visible UNKNOWN, never a blank",
      BODY is not None and "UNKNOWN" in BODY)
check("the serial lookup helper exists with the pinned signature",
      re.search(r"int\s+FindBlockIndexBySerial\s*\(\s*int\s+serial\s*\)",
                BM_C) is not None)
check("the serial lookup helper is keyed on block.serial",
      re.search(r"m_blocks\[i\]\.serial\s*==\s*serial", BM_C) is not None)

# ----------------------------------------------------------------------
# 7. The journal's one-email-per-trade rule is NOT disturbed
# ----------------------------------------------------------------------
# Re-asserted here on purpose: the tempting implementation ("let the journal
# mail the hourly file too") would add a SECOND SendMailFromFile() call site
# and break both test_v542_email.py and test_v545_throttle.py, which pin the
# sender to exactly one call site. The hourly report must own its own SendMail.
print("\n-- Journal email contract undisturbed --")

jrn_calls = len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;", JRN_T))
check("COttoJournal still has exactly one SendMailFromFile() call site",
      jrn_calls == 1, "found %d" % jrn_calls)

other = sum(len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;",
                           read(os.path.join(ROOT, f))))
            for f in ALL_FILES if f != "COttoJournal.mqh")
check("no SendMailFromFile() call outside COttoJournal.mqh", other == 0,
      "found %d" % other)

check("the hourly sender uses the native SendMail, not the journal's helper",
      SEND is not None and "SendMailFromFile" not in SEND and
      "SendMail(" in SEND)

# ----------------------------------------------------------------------
# 8. Release stamp
# ----------------------------------------------------------------------
print("\n-- Release stamp --")

stamps = []
for f in ALL_FILES:
    m = re.search(r'#property version\s+"(\d+\.\d+)"', read(os.path.join(ROOT, f)))
    if m:
        stamps.append(m.group(1))
check("all %d shipped sources carry a version stamp" % len(ALL_FILES),
      len(stamps) == len(ALL_FILES), "found %d" % len(stamps))
check("every shipped source is stamped 5.48 or later",
      len(set(stamps)) == 1 and _rel_parts(stamps) >= (5, 48),
      "stamps=%s" % sorted(set(stamps)))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.48b HOURLY FILE-TO-EMAIL STATUS REPORT - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + detail) if detail else ""))
    print("-" * 74)
    print("  %d / %d checks passed" % (passed, total))
    print("=" * 74)
    if passed != total:
        print("*** FAILURES PRESENT ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



