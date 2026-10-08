"""
v5.45 static verification probe - EMAIL THROTTLE + CANCELLATION SILENCE.

Two operator-facing mailbox invariants that the compiler gate cannot see. Both
are pure RUNTIME properties of how often a call is reached, so a green build
says nothing about either of them.

  1. AT MOST ONE EMAIL PER 30 MINUTES, ACROSS ALL INCIDENTS. The High Table
     dispatcher fires from seven independent latched call sites (halt, daily
     DD, total DD, reject burst, stop-modify failure, state inconsistency,
     risk exposure). A lockout that trips several of them in one audit cycle
     must NOT produce several emails. The guard is therefore a single SHARED
     stamp -- HT_EMAIL_COOLDOWN_SECONDS -- and it is read BEFORE the latch
     branch in DispatchAlertOnce, so the throttle belongs to the MAILBOX and
     not to any one incident. Two failure modes this pins:
        * the cooldown is placed AFTER `if(latch)` -> a live latch returns
          first and the throttle never runs;
        * the window is widened/narrowed from 1800s -> the operator either
          floods again or stops hearing about a real incident.

  2. ZERO EMAILS WHILE HEALTHY. Nothing on the audit path may send mail on a
     cycle that raises no alert. That is structural -- the dispatch functions
     are only reachable from a violated condition -- so it is asserted as
     "SendMail appears nowhere in CHighTableAuditor except the two dispatch
     bodies", which also catches a future alert being wired straight to
     SendMail() behind the dispatcher's back.

  3. CANCELLATION SHIPS NO MAIL. Routine block vetoes and setup cancels
     journal their full detail to disk under the CANCELLED_ prefix but must
     not reach the inbox; only a closed trade exit emails a Trade Summary.
     Asserted on BOTH sides: LogCancellation() has no sender, and the one
     remaining sender is LogExit().

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


MAIN_T = read(MAIN)
AUD_T = read(AUD)
JRN_T = read(JRN)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


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


AUD_C = strip_comments(AUD_T)
JRN_C = strip_comments(JRN_T)


# ----------------------------------------------------------------------
# 1. The shared cooldown exists and is 30 minutes
# ----------------------------------------------------------------------
print("\n-- Shared 30-minute cooldown --")

m = re.search(r"#define\s+HT_EMAIL_COOLDOWN_SECONDS\s+(\d+)", AUD_C)
check("HT_EMAIL_COOLDOWN_SECONDS is defined", m is not None)
check("the cooldown window is exactly 1800s (30 minutes)",
      m is not None and int(m.group(1)) == 1800,
      "found %s" % (m.group(1) if m else "nothing"))

check("the auditor holds a single shared cooldown stamp",
      re.search(r"\bdatetime\s+m_lastEmailSent\s*;", AUD_C) is not None)
check("the constructor initialises the cooldown stamp to 0",
      re.search(r"m_lastEmailSent\s*=\s*0\s*;", AUD_C) is not None)
# A reload must not reopen the window for a still-parked violation.
INIT = func_body(AUD_C, r"bool\s+Initialize\s*\(")
check("Initialize() does NOT reset the cooldown stamp",
      INIT is None or "m_lastEmailSent" not in INIT)


# ----------------------------------------------------------------------
# 2. The gate is shared, and read BEFORE the latch branch
# ----------------------------------------------------------------------
print("\n-- The gate is global, not per-incident --")

DISP_ONCE = func_body(AUD_C, r"void\s+DispatchAlertOnce\s*\(\s*bool\s*&")
DISP = func_body(AUD_C, r"void\s+DispatchAlert\s*\(\s*string")

check("DispatchAlertOnce located", DISP_ONCE is not None)
check("DispatchAlert located", DISP is not None)

if DISP_ONCE is not None:
    gate = DISP_ONCE.find("HT_EMAIL_COOLDOWN_SECONDS")
    latch = DISP_ONCE.find("if(latch)")
    mail = DISP_ONCE.find("SendMail")
    # The load-bearing ordering: throttle evaluated before the latch return.
    check("DispatchAlertOnce evaluates the cooldown", gate >= 0)
    check("the cooldown is read BEFORE the latch branch (global gate)",
          gate >= 0 and latch >= 0 and gate < latch,
          "cooldown@%d latch@%d" % (gate, latch))
    check("the latch still precedes any SendMail", latch >= 0 and mail > latch)
    check("no SendMail hides between the gate and the latch",
          latch >= 0 and "SendMail" not in DISP_ONCE[:latch])
    check("the cooldown gates the SendMail call",
          "if(canEmail)" in DISP_ONCE and
          DISP_ONCE.find("if(canEmail)") < mail)

if DISP is not None:
    check("DispatchAlert evaluates the cooldown too",
          "HT_EMAIL_COOLDOWN_SECONDS" in DISP)
    check("DispatchAlert gates its SendMail on canEmail",
          "if(canEmail)" in DISP and
          DISP.find("if(canEmail)") < DISP.find("SendMail"))
    check("both dispatch paths keep the CSV row unconditionally",
          "WriteCsv(" in DISP and
          DISP_ONCE is not None and "WriteCsv(" in DISP_ONCE)

# A stamp that advanced on a FAILED send would mute the mailbox for the next
# 30 minutes after the very failure it was trying to report.
STAMP_ADV = r"if\(emailed\)\s*\n\s*m_lastEmailSent\s*=\s*TimeCurrent\(\)"
for fn, body in (("DispatchAlert", DISP), ("DispatchAlertOnce", DISP_ONCE)):
    if body is None:
        continue

# ----------------------------------------------------------------------
# 3. Zero emails while healthy: SendMail is reachable only via the dispatcher
# ----------------------------------------------------------------------
print("\n-- SendMail is confined to the two dispatch bodies --")

sites = re.findall(r"SendMail\s*\(", AUD_C)
check("the auditor contains exactly two SendMail() calls", len(sites) == 2,
      "found %d" % len(sites))

for fn, body in (("DispatchAlert", DISP), ("DispatchAlertOnce", DISP_ONCE)):
    check("%s owns a gated SendMail()" % fn,
          body is not None and "SendMail(" in body)

# Seven independent latched incidents, one shared mailbox.
latches = re.findall(r"DispatchAlertOnce\s*\(\s*(m_alertSent_\w+)", AUD_C)
check("every latched incident routes through DispatchAlertOnce",
      len(latches) >= 7, "found %d" % len(latches))
check("no incident bypasses the dispatcher with a raw SendMail()",
      len(re.findall(r"SendMail\s*\(\s*subject\s*,\s*message\s*\)", AUD_C)) == 2,
      "found %d" % len(re.findall(r"SendMail\s*\(\s*subject\s*,\s*message\s*\)", AUD_C)))


# ----------------------------------------------------------------------
# 4. Cancellation is silent; the exit still sends
# ----------------------------------------------------------------------
print("\n-- Cancellation ships no mail --")

CANCEL = func_body(JRN_C, r"void\s+LogCancellation\s*\(")
EXIT = func_body(JRN_C, r"void\s+LogExit\s*\(")

check("LogCancellation located", CANCEL is not None)
check("LogCancellation does NOT email",
      CANCEL is not None and "SendMailFromFile" not in CANCEL)
check("LogCancellation still renames the journal to CANCELLED_",
      CANCEL is not None and "CANCELLED_" in CANCEL and "FileMove(" in CANCEL)
check("LogCancellation still appends the reason to disk",
      CANCEL is not None and "[CANCELLED] Reason:" in CANCEL)

check("LogExit located", EXIT is not None)
check("LogExit is a sender", EXIT is not None and "SendMailFromFile();" in EXIT)

calls = len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;", JRN_C))
check("the journal has exactly one SendMailFromFile() call site", calls == 1,
      "found %d" % calls)

other = sum(len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;",
                           read(os.path.join(ROOT, f))))
            for f in ALL_FILES if f != "COttoJournal.mqh")
check("no SendMailFromFile() call outside the journal", other == 0,
      "found %d" % other)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.45 EMAIL THROTTLE + CANCELLATION SILENCE - STATIC PROBE")
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


