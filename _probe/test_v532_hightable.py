"""
v5.32 static verification probe - the "High Table" decoupled watchdog.

v5.32 adds CHighTableAuditor.mqh, and almost every property that makes it
worth shipping is a RUNTIME property that the compiler gate cannot see. The
build check proves it compiles and is staged; this probe proves it is wired
to the right event and cannot silently degrade into either (a) a watchdog
that stops watching or (b) a mailbox bomb.

  1. DECOUPLING. The auditor must not be reachable from the tick path in a
     way that lets a stalled OnTick silence it. Its entry point is driven by
     OnTimer, and the timer is registered in OnInit. If someone later moves
     RunAudit() into OnTick "for simplicity", the watchdog inherits every
     early-return in OnTick - including the halt and daily-pause returns,
     which are exactly the states it exists to report.

  2. TIMER LIFECYCLE. EventKillTimer() must run in OnDeinit. OnDeinit fires
     on every parameter change, timeframe switch and recompile; without the
     kill, each re-init stacks another timer on the same chart and the audit
     cadence silently multiplies (5s -> 10s -> 15s of stacked work).

  3. ONE-SHOT LATCHES. OnTimer re-tests every condition every cycle. An
     alert with no latch re-emails every 5 seconds for as long as the
     violation stands. Each latch must be RAISED on dispatch and re-armed
     only via ClearLatch(), and - critically - raised as a side effect of the
     DISPATCH, not of the caller noticing the violation, or a live condition
     latches itself silent before it is ever reported.

  4. LATCH PASSED BY REFERENCE. `bool &latch` binds each call site to a named
     bool member. A by-value parameter would compile cleanly and do nothing.

  5. CSV EVIDENCE. The append must open FILE_READ|FILE_WRITE and seek to EOF.
     Plain FILE_WRITE truncates, which would leave the audit trail holding
     only its most recent row - an evidence file that erases its own history.

  6. TESTER SAFETY. SendMail cannot dispatch inside the Strategy Tester. An
     unguarded call is a wasted journal error per alert per run.

  7. ASCII + PARSE SAFETY. The repo has shipped mojibake before, and the CSV
     is a comma-delimited machine-read artifact: the sanitizer must fold the
     delimiter, the quote character, raw CR/LF and every non-ASCII byte.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
BUILD = os.path.join(ROOT, "_tools", "build_check.ps1")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


MAIN_T = read(MAIN)
DEFS_T = read(DEFS)
AUD_T = read(AUD) if os.path.exists(AUD) else ""
BUILD_T = read(BUILD) if os.path.exists(BUILD) else ""

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals (see v5.31 probe)."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


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


AUD_C = strip_comments(AUD_T)
MAIN_C = strip_comments(MAIN_T)



# ----------------------------------------------------------------------
# 1. Decoupling: the watchdog runs on its own timer, not on ticks
# ----------------------------------------------------------------------
print("\n-- Decoupling from the tick path --")

check("CHighTableAuditor.mqh exists at repo root",
      os.path.exists(AUD))
check("the module declares the class",
      re.search(r"\bclass\s+CHighTableAuditor\b", AUD_C) is not None)

ONTIMER = func_body(MAIN_C, r"void\s+OnTimer\s*\(\s*void\s*\)")
check("otto.mq5 defines an OnTimer handler", ONTIMER is not None)
if ONTIMER is not None:
    check("OnTimer runs the audit", "RunAudit()" in ONTIMER)
    check("OnTimer does NOT early-return on halt/pause state",
          "g_totalDD_Halted" not in ONTIMER and "DailyDD_Paused" not in ONTIMER)
    check("OnTimer guards on initialization only",
          "g_initialized" in ONTIMER)

# RunAudit must be reachable ONLY from the timer path. If it is also called
# from OnTick, the watchdog is no longer independent of the trade loop.
tick_calls = re.findall(r"g_highTable\.RunAudit\s*\(", MAIN_C)
check("RunAudit is called exactly once in otto.mq5", len(tick_calls) == 1,
      "found %d call site(s)" % len(tick_calls))

ONTICK = func_body(MAIN_C, r"void\s+OnTick\s*\(\s*void\s*\)")
check("OnTick does not drive the audit",
      ONTICK is None or "g_highTable" not in ONTICK)

# The auditor must hold no reference to any trade module - that is what makes
# it structurally immune to a fault in the trade path.
foreign = re.findall(r"\bCOtto\w+|\bCTrade\b|\bCPositionInfo\b", AUD_C)
check("the auditor holds no trade-module types", not foreign,
      "found: %s" % sorted(set(foreign)))
check("the auditor does not include a trade module",
      not re.search(r'#include\s+"C(Otto|Trade)', AUD_C))
check("the auditor documents the decoupling contract",
      "DECOUPLING CONTRACT" in AUD_T)


# ----------------------------------------------------------------------
# 2. Timer lifecycle
# ----------------------------------------------------------------------
print("\n-- Timer lifecycle --")

check("OnInit registers the timer",
      re.search(r"EventSetTimer\s*\(", MAIN_C) is not None)
check("OnDeinit kills the timer",
      re.search(r"EventKillTimer\s*\(\s*\)\s*;", MAIN_C) is not None)
check("the timer is registered exactly once", MAIN_C.count("EventSetTimer(") == 1,
      "found %d" % MAIN_C.count("EventSetTimer("))
check("the timer is killed exactly once", MAIN_C.count("EventKillTimer()") == 1,
      "found %d" % MAIN_C.count("EventKillTimer()"))

ONDEINIT = func_body(MAIN_C, r"void\s+OnDeinit\s*\(\s*const\s+int\s+reason\s*\)")
check("OnDeinit located", ONDEINIT is not None)
if ONDEINIT is not None:
    check("OnDeinit kills the timer before standing the auditor down",
          0 < ONDEINIT.find("EventKillTimer()") < ONDEINIT.find("g_highTable.Deinit()"))

check("the cadence is clamped to a usable minimum",
      re.search(r"InpHighTableAuditSeconds\s*<\s*1\s*\)\s*\?\s*1\s*:"
                r"\s*InpHighTableAuditSeconds", MAIN_C) is not None)
check("OnInit honors the enable input",
      re.search(r"if\s*\(\s*InpEnableHighTable\s*\)", MAIN_C) is not None)
check("OnTimer honors the enable input", "InpEnableHighTable" in ONTIMER)



# ----------------------------------------------------------------------
# 3. Inputs
# ----------------------------------------------------------------------
print("\n-- Inputs --")

check("InpEnableHighTable defaults to true",
      re.search(r"^input\s+bool\s+InpEnableHighTable\s*=\s*true\s*;",
                DEFS_T, re.M) is not None)
check("InpHighTableAuditSeconds defaults to 5",
      re.search(r"^input\s+int\s+InpHighTableAuditSeconds\s*=\s*5\s*;",
                DEFS_T, re.M) is not None)
check("the new group is numbered [10]",
      '[10] HIGH TABLE AUDITOR' in DEFS_T)
check("the group header pins the introducing release",
      re.search(r'\[10\]\s+HIGH TABLE AUDITOR\s+\u2014\s+v\d+\.\d+', DEFS_T) is not None)
check("group [10] is declared after group [9]",
      0 < DEFS_T.find("[9] CURRENCY VECTOR") < DEFS_T.find("[10] HIGH TABLE"))


# ----------------------------------------------------------------------
# 4. Latches: one alert per incident, raised on DISPATCH
# ----------------------------------------------------------------------
print("\n-- One-shot incident latches --")

DISP1 = func_body(AUD_C, r"void\s+DispatchAlertOnce\s*\(\s*bool\s*&")
check("DispatchAlertOnce located", DISP1 is not None)
if DISP1 is not None:
    check("the latch is taken BY REFERENCE",
          re.search(r"DispatchAlertOnce\s*\(\s*bool\s*&\s*latch", AUD_T) is not None)
    check("a live latch short-circuits before the email",
          0 < DISP1.find("if(latch)") < DISP1.find("SendMail"))
    check("the short-circuit counts the suppressed repeat",
          "m_suppressed++" in DISP1)
    check("the latch is SET only after the guard",
          DISP1.find("latch = true;") > DISP1.find("if(latch)"))
    check("the latch is set on the DISPATCH, not by the caller",
          "latch = true;" in DISP1 and
          not re.search(r"latch\s*=\s*true\s*;", AUD_C.replace(DISP1, "")))
    check("a suppressed repeat does NOT re-email",
          "SendMail" not in DISP1.split("latch = true;")[0])
    check("a suppressed repeat is still written to the CSV",
          DISP1.split("latch = true;")[0].count("WriteCsv(") == 1)

DISP = func_body(AUD_C, r"void\s+DispatchAlert\s*\(\s*string")
check("DispatchAlert located", DISP is not None)
if DISP is not None:
    check("DispatchAlert emails", "SendMail(" in DISP)
    check("DispatchAlert always writes the CSV row", "WriteCsv(" in DISP)
    check("DispatchAlert counts the dispatch", "m_dispatched++" in DISP)

check("ClearLatch re-arms by reference",
      re.search(r"void\s+ClearLatch\s*\(\s*bool\s*&\s*latch\s*\)\s*\{\s*latch\s*=\s*false\s*;\s*\}",
                AUD_C) is not None)

# The latch inventory must be named bool members, not a string-keyed map:
# a typo in a map key is a silent no-op, a typo in a member name will not
# compile.
LATCHES = re.findall(r"bool\s+m_alertSent_(\w+)\s*;", AUD_C)
check("the auditor declares named latch members", len(LATCHES) >= 6,
      "found %d: %s" % (len(LATCHES), sorted(LATCHES)))


# ----------------------------------------------------------------------
# 5. CSV evidence trail
# ----------------------------------------------------------------------
print("\n-- CSV evidence trail --")

CSV = func_body(AUD_C, r"void\s+WriteCsv\s*\(")
check("WriteCsv located", CSV is not None)
if CSV is not None:
    check("the CSV opens FILE_READ|FILE_WRITE (append, not truncate)",
          re.search(r"FILE_READ\s*\|\s*FILE_WRITE", CSV) is not None)
    check("the CSV seeks to EOF before writing",
          re.search(r"FileSeek\s*\(\s*h\s*,\s*0\s*,\s*SEEK_END\s*\)", CSV) is not None)
    check("the header is written only when the file is empty",
          re.search(r"if\s*\(\s*FileTell\s*\(\s*h\s*\)\s*==\s*0\s*\)", CSV) is not None)
    check("the handle is closed on every path",
          CSV.count("FileClose(") == 1)
    check("a failed open is logged and swallowed, not thrown",
          re.search(r"INVALID_HANDLE", CSV) is not None and
          re.search(r"Print\s*\(", CSV) is not None and
          re.search(r"return\s*;", CSV) is not None)
    check("every written field is sanitized or a safe literal",
          CSV.count("Sanitize(") >= 4)
check("the artifact name is a single constant",
      re.search(r'#define\s+HT_AUDIT_CSV\s+"[\w\.]+\.csv"', AUD_T) is not None)

SAN = func_body(AUD_C, r"string\s+Sanitize\s*\(")
check("Sanitize folds the delimiter, the quote and CR/LF",
      SAN is not None and
      all(k in SAN for k in ("59", "39", "13", "10")))
check("Sanitize maps every non-ASCII byte to '_'",
      SAN is not None and re.search(r"ch\s*<\s*32\s*\|\|\s*ch\s*>\s*126", SAN) is not None and
      "95" in SAN)


# ----------------------------------------------------------------------
# 6. Tester safety
# ----------------------------------------------------------------------
print("\n-- Strategy Tester safety --")

check("both dispatch paths guard SendMail with a tester check",
      AUD_C.count("MQL_TESTER") >= 2,
      "found %d guard(s)" % AUD_C.count("MQL_TESTER"))
check("the tester guard wraps the SendMail call",
      all(re.search(r"MQL_TESTER", seg) and
          seg.find("MQL_TESTER") < seg.find("SendMail")
          for seg in (DISP1, DISP) if seg is not None))
check("the CSV records the tester-suppressed state",
      "TESTER" in AUD_C)


# ----------------------------------------------------------------------
# 7. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.32 or later", parts >= (5, 32), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 11 sources stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)
check("Pine port banner names the current release",
      ("Master Build Port (v%s)" % RELEASE) in MAIN_T)

# The new module must be in the provenance gate, or it compiles but is never
# verified - the exact failure mode the gate exists to prevent.
check("build_check.ps1 declares the new module in ottoNames",
      "CHighTableAuditor" in BUILD_T)
check("build_check.ps1 lists the new module 3 times (names/expected/modules)",
      BUILD_T.count('"CHighTableAuditor"') == 3,
      "found %d" % BUILD_T.count('"CHighTableAuditor"'))
check("the staging-tree expectation counts 11 OTTO sources",
      "all 11 OTTO sources" in BUILD_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.32 HIGH TABLE WATCHDOG - STATIC PROBE")
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

uninit = [l for l in LATCHES
          if not re.search(r"m_alertSent_%s\s*=\s*false\s*;" % re.escape(l), AUD_C)]
check("every latch is initialised in the constructor", not uninit,
      "uninitialised: %s" % uninit)
check("a second dispatch path exists for one-off events",
      DISP is not None and DISP1 is not None)
