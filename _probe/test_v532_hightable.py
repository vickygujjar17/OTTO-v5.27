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
LATCHES = re.findall(r"bool\s+m_alertSent_(\w+)\s*;\s*//", AUD_C)
# The declarations carry trailing comments; also pick up a bare declaration.
if len(LATCHES) < 6:
    LATCHES = re.findall(r"bool\s+m_alertSent_(\w+)\s*;", AUD_C)
check("the auditor declares named latch members", len(LATCHES) >= 6,
      "found %d: %s" % (len(LATCHES), sorted(LATCHES)))

uninit = [l for l in LATCHES
          if not re.search(r"m_alertSent_%s\s*=\s*false\s*;" % re.escape(l), AUD_C)]
check("every latch is initialised in the constructor", not uninit,
      "uninitialised: %s" % uninit)
check("a second dispatch path exists for one-off events",
      DISP is not None and DISP1 is not None)


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
# 8. PART 2 - the live audit bodies
# ----------------------------------------------------------------------
print("\n-- Part 2 live audit bodies --")

BODIES = ["AuditTrimHealth", "AuditDrawdown", "AuditOrderHealth",
          "AuditStateConsistency"]

BODY_SRC = {}
for name in BODIES:
    BODY_SRC[name] = func_body(AUD_C, r"void\s+%s\s*\(\s*void\s*\)" % name)
    check("%s is defined" % name, BODY_SRC[name] is not None)

RUN = func_body(AUD_C, r"void\s+RunAudit\s*\(\s*void\s*\)") or ""
check("RunAudit drives all four Part 2 bodies",
      all((name + "()") in RUN for name in BODIES))

# Each body must be self-contained: it raises at most one LATCHED alert, and
# it re-arms that latch once the condition reads healthy. A body that
# dispatches but never clears latches itself silent forever after the first
# incident - the mailbox would go quiet exactly when a recurring fault needs
# reporting.
# Every dispatched latch must be RE-ARMED on the healthy path, and a latch is
# only meaningful paired with the dispatch that raises it. Testing merely that
# the word ClearLatch() appears somewhere in the body is not enough: the
# reference bug this guards - a latch that is raised but never lowered - is
# invisible to a text-presence test, because the SAME body usually clears
# some OTHER latch and satisfies the search.
def latch_lifecycle_ok(body, latch):
    disp = [m.start() for m in
            re.finditer(r"DispatchAlertOnce\(\s*m_alertSent_%s\b" % latch, body)]
    clr = [m.start() for m in
           re.finditer(r"ClearLatch\(\s*m_alertSent_%s\b" % latch, body)]
    if not disp or not clr:
        return False
    # else-shape: a re-arm that runs even while the violation is still live.
    if any(c > disp[-1] for c in clr):
        return True
    # guard-shape: the healthy path is an early return taken BEFORE the
    # dispatch, exactly as AuditStateConsistency does.
    return any(c < disp[0] and "return" in body[c:disp[0]] for c in clr)


# The halt and the trailing-total breach are deliberately never re-armed: the
# EA only ever sets them, so lowering the latch could only produce a second
# email for the same permanent condition.
NEVER_CLEARED = {"Halt", "TotalDDBreach"}

for name in BODIES:
    b = BODY_SRC[name]
    if b is None:
        continue
    check("%s actually evaluates something" % name, len(b) > 200,
          "body is %d chars" % len(b))
    check("%s dispatches through a latch" % name, "DispatchAlertOnce(" in b)

    dispatched = set(re.findall(r"DispatchAlertOnce\(\s*m_alertSent_(\w+)", b))
    cleared = set(re.findall(r"ClearLatch\(\s*m_alertSent_(\w+)", b))
    check("%s raises at least one latched alert" % name, bool(dispatched))
    check("%s never re-arms a latch it does not raise" % name,
          not (cleared - dispatched),
          "stray: %s" % sorted(cleared - dispatched))

    for latch in sorted(dispatched - NEVER_CLEARED):
        check("%s/%s is re-armed on its healthy path" % (name, latch),
              latch_lifecycle_ok(b, latch))
    for latch in sorted(dispatched & NEVER_CLEARED):
        check("%s/%s stays permanently latched" % (name, latch),
              latch not in cleared)

# The latch must be a CLASS member. This module previously shipped a latch
# declared at file scope, AFTER the include guard's #endif: the constructor's
# assignment then initialised a different object from the one the methods
# read, so the incident was reported once and then silenced for the rest of
# the session. Nothing of the class may follow the guard.
TAIL_AFTER_GUARD = AUD_T.split("#endif")[-1] if "#endif" in AUD_T else AUD_T
check("no latch state is declared outside the include guard",
      "m_alertSent_" not in TAIL_AFTER_GUARD)

# Every latch a body touches must be a declared member, or the ClearLatch
# re-arm would bind to nothing.
for name in BODIES:
    b = BODY_SRC[name]
    if b is None:
        continue
    used = set(re.findall(r"m_alertSent_(\w+)", b))
    check("%s only touches declared latches" % name,
          used and not (used - set(LATCHES)),
          "undeclared: %s" % sorted(used - set(LATCHES)))


# ----------------------------------------------------------------------
# 9. PART 2 - units, thresholds and the trim mirror
# ----------------------------------------------------------------------
print("\n-- Part 2 units and thresholds --")

# The 0.0090 fraction idiom belongs to a DIFFERENT rule shape - it appears in
# this module ONLY inside the unit-rationale comment block. Comment text is
# not code, so the prohibition has to be asserted against the comment-stripped
# source; against raw text it would be satisfied forever by the very comment
# that warns about it.
check("the 0.0090 fraction idiom is discussed but never used as code",
      "0.0090" in AUD_T and "0.0090" not in AUD_C)

FL = func_body(AUD_C, r"double\s+FloatingLossPct\s*\(\s*void\s*\)")
check("FloatingLossPct returns the percent form",
      FL is not None and
      re.search(r"100\.0\s*\*\s*\(\s*balance\s*-\s*equity\s*\)\s*/\s*balance",
                FL) is not None)
DAILYD = func_body(AUD_C, r"double\s+DailyDDPct\s*\(\s*void\s*\)")
check("DailyDDPct measures against the pushed daily anchor",
      DAILYD is not None and "m_dailyResetBalance" in DAILYD and "100.0" in DAILYD)
TOTD = func_body(AUD_C, r"double\s+TotalDDPct\s*\(\s*void\s*\)")
check("TotalDDPct measures against the pushed equity HWM",
      TOTD is not None and "m_equityHwm" in TOTD and "100.0" in TOTD)

# Every threshold is the LIVE input, never a literal copy of its value. The
# assertion has to name the COMPARISON, not merely the identifier: each of
# these names also appears in alert text and in this scaffold, so a
# file-wide `name in source` test stays green even if a comparison was
# rewritten to a hardcoded literal - exactly the drift that matters, since
# the EA's own limit would then no longer be the limit being watched.
THRESHOLD_SITES = [
    ("floating-loss cap", r"if\s*\(\s*floatingLoss\s*>=\s*SafetyMaxFloatingLoss\s*\)"),
    ("daily DD limit",    r"if\s*\(\s*dailyDD\s*>=\s*SafetyDailyDDLimit\s*\)"),
    ("total DD limit",    r"if\s*\(\s*totalDD\s*>=\s*SafetyTotalDDLimit\s*\)"),
]
for label, pat in THRESHOLD_SITES:
    check("the %s compares against the live input" % label,
          re.search(pat, AUD_C) is not None)

# ...and no numeric literal may stand in for a safety limit. A bare 0.90/3.0/
# 5.0 comparison anywhere in the drawdown bodies is the same defect the pair
# above is watching for, so reject the shape outright.
check("no drawdown comparison uses a numeric limit literal",
      re.search(r"(floatingLoss|dailyDD|totalDD)\s*>=\s*\d", AUD_C) is None)

# The trim mirror must reference the live retune input BY NAME, so a change
# to InpTrimLoserStopPct moves both implementations at once and only a change
# to the STRUCTURE of the formula could cause drift.
#
# The assertion has to name the CALL SITE, not merely the identifier: the
# input also appears in the alert text, so a bare `identifier in source` test
# would still pass if the call were changed to a hardcoded 70.0 - which is
# precisely the drift it is meant to catch.
check("the trim mirror passes the live trim input to the counter",
      re.search(r"CountTrimmableLegs\s*\(\s*InpTrimLoserStopPct\s*\)",
                AUD_C) is not None)
check("the trim mirror hardcodes no trim threshold",
      re.search(r"CountTrimmableLegs\s*\(\s*\d", AUD_C) is None)
check("the trim mirror converts percent to a fraction exactly once",
      re.search(r"trimPct\s*/\s*100\.0", AUD_C) is not None)
check("the trim mirror keeps the same guards as WalkTrimLegs",
      re.search(r"if\s*\(\s*sl\s*<=\s*0\.0\s*\)\s*continue\s*;", AUD_C) is not None and
      re.search(r"if\s*\(\s*total\s*<=\s*0\.0\s*\)\s*continue\s*;", AUD_C) is not None)

# WalkTrimLegs skips `ticket == m_orderManager.GetActiveTrade().ticket`. The
# mirror must skip the SAME ticket, pushed verbatim, or the two could disagree
# about which leg is primary and skew the count in either direction.
check("the trim mirror excludes the pushed primary ticket",
      re.search(r"ticket\s*==\s*m_trackedPrimary", AUD_C) is not None)

# "_T<n>" is appended ONLY for tranche > 1, so a bare comment is the primary.
check("the tranche-suffix parser exists for the pre-push fallback",
      re.search(r"bool\s+CommentHasTrancheSuffix\s*\(", AUD_C) is not None)
check("the auditor never hardcodes a _T1 comment suffix", "_T1" not in AUD_C)


# ----------------------------------------------------------------------
# 10. PART 2 - the state-consistency invariant
# ----------------------------------------------------------------------
print("\n-- Part 2 state-consistency invariant --")

# The invariant compares the order layer's BELIEF (which ticket it is
# trailing) against the terminal's book. It must NOT be a basket-length
# comparison: m_basketCount and the m_basket[] array legitimately lag the
# book during a normal exit, so that shape would false-positive on every
# close - an alert nobody would keep trusting.
check("the book is queried for a specific ticket",
      re.search(r"bool\s+BookHasTicket\s*\(", AUD_C) is not None)

SC = BODY_SRC["AuditStateConsistency"]
if SC is not None:
    check("the consistency check tests the pushed primary against the book",
          "BookHasTicket(m_trackedPrimary)" in SC)
    check("the phantom requires the pushed active flag",
          "m_trackedActive" in SC)
    check("the consistency check is NOT a basket-length comparison",
          not re.search(r"book\s*==\s*m_trackedLegs", SC))
    check("a divergence must persist across two cycles",
          SC.count("m_stateSkewSeen") >= 3)


# MODELLED INPUTS - transcribed from the body's documented behaviour, NOT
# parsed out of the shipped source. It steps the cycle-by-cycle decision
# table:
#   (active, primary>0, primary present in book) -> (alerts, latch, skew-seen)
# and is checked alongside the source-side predicates above, so a body that
# stopped implementing this shape would fail those even if the model passed.
def model_cycles(seq):
    skew, latch, alerts = False, False, 0
    for active, has_primary, in_book in seq:
        phantom = active and has_primary and not in_book
        if not phantom:
            latch, skew = False, False
            continue
        if not skew:
            skew = True
            continue
        if not latch:
            alerts += 1
            latch = True
    return alerts, latch


check("MODELLED: a steady phantom alerts exactly once",
      model_cycles([(True, True, False)] * 3)[0] == 1)
check("MODELLED: a one-cycle hand-off blip never alerts",
      model_cycles([(True, True, False), (True, True, True)])[0] == 0)
check("MODELLED: a recurring phantom alerts once per incident",
      model_cycles([(True, True, False), (True, True, False),
                    (True, True, True),
                    (True, True, False), (True, True, False)])[0] == 2)
check("MODELLED: a flat manager is never reported",
      model_cycles([(False, False, False)] * 4)[0] == 0)
check("MODELLED: a known primary that IS in the book never alerts",
      model_cycles([(True, True, True)] * 4)[0] == 0)

if SC is not None:
    check("the shipped body confirms the skew before it dispatches",
          SC.find("m_stateSkewSeen") < SC.find("DispatchAlertOnce("))


# ----------------------------------------------------------------------
# 11. PART 2 - pushed-fact ingress and its wiring
# ----------------------------------------------------------------------
print("\n-- Part 2 pushed-fact ingress --")

# The push carries the ticket the order layer BELIEVES it is trailing plus
# the active flag. Before the first push the auditor must not treat ticket 0
# as a phantom, so the ingress records the flag alongside the ticket.
check("SetTrackedLegs carries the belief (legs, primary, active)",
      re.search(r"void\s+SetTrackedLegs\s*\(\s*int\s+\w+\s*,\s*ulong\s+\w+\s*,"
                r"\s*bool\s+\w+\s*\)", AUD_C) is not None)
check("SetSafetyBaseline carries the anchors and the halt flag",
      re.search(r"SetSafetyBaseline\s*\(\s*double\s+\w+\s*,\s*double\s+\w+\s*,"
                r"\s*bool\s+\w+\s*\)", AUD_C) is not None)
check("the reject ingress is monotonic (delta-capable)",
      re.search(r"m_rejectCount\s*\+=", AUD_C) is not None)
check("the stop-modify ingress is monotonic (delta-capable)",
      re.search(r"m_stopModifyFailures\s*\+=", AUD_C) is not None)

# The auditor can never poll the order layer, so the ORDER MANAGER must own
# the stop-modify counter and expose it. Without a counter there is nothing
# to push and the SL-modify check would silently never fire.
ORD = read(os.path.join(ROOT, "COttoOrderManager.mqh"))
ORD_C = strip_comments(ORD)
MSL = func_body(ORD_C, r"bool\s+ModifyStopLoss\s*\(")
check("the order manager counts a rejected SL modify",
      MSL is not None and "m_stopModifyFailures++" in MSL)
check("the count is incremented exactly once, on the failure path",
      MSL is not None and MSL.count("m_stopModifyFailures++") == 1 and
      MSL.find("m_stopModifyFailures++") > MSL.find("SendOrderWithRetry"))
check("the order manager exposes the stop-modify total",
      re.search(r"int\s+GetStopModifyFailures\s*\(\s*void\s*\)", ORD_C) is not None)
check("the stop-modify counter is initialised in the constructor",
      re.search(r"m_stopModifyFailures\s*=\s*0\s*;", ORD_C) is not None)

# The push must happen on the AUDIT cadence, from the timer path, and before
# the audit runs - facts arrive, then the audit reads them.
PUSH = func_body(MAIN_C, r"void\s+HighTablePushFacts\s*\(\s*void\s*\)")
check("otto.mq5 defines the fact-ingress helper", PUSH is not None)
check("the ingress is called from OnTimer",
      "HighTablePushFacts()" in ONTIMER)
check("the ingress runs BEFORE the audit",
      0 < ONTIMER.find("HighTablePushFacts()") < ONTIMER.find("RunAudit()"))
if PUSH is not None:
    check("the ingress pushes the drawdown anchors",
          "SetSafetyBaseline(" in PUSH)
    check("the ingress pushes the tracked trade",
          "SetTrackedLegs(" in PUSH)
    check("the ingress takes the ticket from the order layer, not the book",
          "GetActiveTradeRef(" in PUSH)
    check("the ingress pushes rejects as a per-cycle delta",
          "NotifyOrderReject(" in PUSH and "rejects -" in PUSH)
    check("the ingress pushes stop failures as a per-cycle delta",
          "NotifyStopModifyFailure(" in PUSH and "stopFails -" in PUSH)
    # A delta is only correct if the high-water mark advances with it,
    # otherwise the same failures are re-counted on every cycle and the
    # burst threshold trips on a single old reject.
    check("the ingress advances its reject high-water mark",
          "g_htPushedRejects = rejects;" in PUSH)
    check("the ingress advances its stop-failure high-water mark",
          "g_htPushedStopFails = stopFails;" in PUSH)

check("OnTick still does not touch the auditor",
      ONTICK is None or "g_highTable" not in ONTICK)
check("RunAudit is still called exactly once",
      len(re.findall(r"g_highTable\.RunAudit\s*\(", MAIN_C)) == 1)


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

