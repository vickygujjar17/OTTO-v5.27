"""
v5.42 static verification probe - EMAIL CONTRACT (single terminal summary).

Pins the v5.42 consolidation: an operator receives EXACTLY ONE aggregated
session-file email per trade, sent only at a terminal lifecycle moment. Every
intermediate stage writes to disk silently and accumulates into the same
journal file that the terminal writer ships.

  1. EXACTLY TWO SENDERS. COttoJournal::SendMailFromFile() is called from
     exactly two places -- LogExit() and LogCancellation() -- and nowhere else
     in the shipped sources. The count is pinned so a future edit that
     reintroduces an intermediate mail fails the build gate.

  2. INTERMEDIATE WRITERS STAY SILENT. LogSetupArmed() (the arm snapshot) and
     LogEntry() (the fill record) still write their SUBJECT + body and close
     the handle, but they do NOT email.

  3. CONVERSION STAYS SILENT. LogConversion() (Phase 1 dropped for Phase 2
     reversal) writes its notice but does NOT email -- it is a re-phase, not a
     session endpoint.

  4. TERMINAL WRITERS STILL SEND, AFTER CloseHandle(). Both LogExit() and
     LogCancellation() keep the SendMailFromFile() call positioned after
     CloseHandle(), so the read-back never races a writer holding the lock.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
JRN_T = read(JRN)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def func_body(text, signature_re):
    """Return the full body of an MQL5 function, matched by counting braces."""
    m = re.search(signature_re, text)
    if m is None:
        return None
    start = text.find("{", m.start())
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def strip_comments(code):
    """Drop // comments, respecting string literals."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


EXIT = func_body(JRN_T, r"void\s+LogExit\s*\(")
CANCEL = func_body(JRN_T, r"void\s+LogCancellation\s*\(")
ARM = func_body(JRN_T, r"void\s+LogSetupArmed\s*\(")
ENTRY = func_body(JRN_T, r"void\s+LogEntry\s*\(")
CONV = func_body(JRN_T, r"void\s+LogConversion\s*\(")



# ----------------------------------------------------------------------
# 1. Exactly two senders, both terminal
# ----------------------------------------------------------------------
print("\n-- Exactly two terminal senders --")

# Count call sites (the trailing ';') across the whole journal, so the
# definition line ('...SendMailFromFile(void)') is not counted.
calls = len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;", JRN_T))
check("journal has exactly two SendMailFromFile() call sites", calls == 2,
      "found %d" % calls)

# ...and none anywhere else in the tree.
other = sum(len(re.findall(r"SendMailFromFile\s*\(\s*\)\s*;", read(os.path.join(ROOT, f))))
            for f in ALL_FILES if f != "COttoJournal.mqh")
check("no SendMailFromFile() call outside COttoJournal.mqh", other == 0,
      "found %d" % other)

check("LogExit() is a sender",
      EXIT is not None and "SendMailFromFile();" in EXIT)
check("LogCancellation() is a sender",
      CANCEL is not None and "SendMailFromFile();" in CANCEL)


# ----------------------------------------------------------------------
# 2. Intermediate writers stay silent
# ----------------------------------------------------------------------
print("\n-- Intermediate writers stay silent --")

check("LogSetupArmed() located", ARM is not None)
check("LogSetupArmed() writes the SUBJECT", ARM is not None and "SUBJECT:" in ARM)
check("LogSetupArmed() closes the handle", ARM is not None and "CloseHandle();" in ARM)
check("LogSetupArmed() does NOT email", ARM is not None and "SendMailFromFile" not in ARM)

check("LogEntry() located", ENTRY is not None)
check("LogEntry() writes the SUBJECT", ENTRY is not None and "SUBJECT:" in ENTRY)
check("LogEntry() closes the handle", ENTRY is not None and "CloseHandle();" in ENTRY)
check("LogEntry() does NOT email", ENTRY is not None and "SendMailFromFile" not in ENTRY)


# ----------------------------------------------------------------------
# 3. Conversion stays silent
# ----------------------------------------------------------------------
print("\n-- Conversion stays silent --")

check("LogConversion() located", CONV is not None)
check("LogConversion() closes the handle", CONV is not None and "CloseHandle();" in CONV)
check("LogConversion() does NOT email", CONV is not None and "SendMailFromFile" not in CONV)


# ----------------------------------------------------------------------
# 4. Terminal sends happen after CloseHandle()
# ----------------------------------------------------------------------
print("\n-- Terminal sends after CloseHandle() --")

if EXIT is not None:
    check("LogExit() sends after CloseHandle()",
          EXIT.find("CloseHandle();") < EXIT.find("SendMailFromFile();"))
if CANCEL is not None:
    check("LogCancellation() sends after CloseHandle()",
          CANCEL.find("CloseHandle();") < CANCEL.find("SendMailFromFile();"))


# ----------------------------------------------------------------------
# 5. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.42 or later", parts >= (5, 42), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 10 files stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.42 EMAIL CONTRACT - STATIC PROBE")
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
