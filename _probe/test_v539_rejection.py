"""
v5.39 static verification probe - arm email, terminal rejection, guard removal.

Pins the three behaviours v5.39 adds on top of the v5.37/v5.38 virtual-order
engine. None of them is provable by the MQL5 compiler gate: they are control
flow and side-effect properties of the shipped sources.

  1. ARM IS SILENT (v5.42). COttoJournal::LogSetupArmed() must WRITE the
     SUBJECT + setup block and close the handle but must NOT call
     SendMailFromFile(): arming is an intermediate lifecycle stage. Only the
     two terminal writers (LogExit()/LogCancellation()) email the session file.

  2. REJECTION IS TERMINAL AND READABLE. When SendOrderWithRetry() refuses the
     MARKET deal fired by CheckVirtualTriggers(), the raw retcode must be mapped
     through GetTradeRetcodeString(), journalled via LogCancellation(), and the
     block vetoed (VETO_BROKEN) with its virtual order dropped. The v5.37
     latch-then-retry path (which left the order armed and re-armed a second
     SVirtualOrder every tick) must be gone.

  3. LEGACY PRICE GUARD RETIRED. The v5.28 LIVE PRICE VALIDATION pre-flight in
     ArmVirtualOrder() and its one-shot priceAbortLogged gate are removed: a
     v5.37+ setup is an in-memory SVirtualOrder, so no limit rests at the broker.

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
    """Return the full body of an MQL5 function, matched by counting braces.

    A lazy regex stops at the first inner '}' aligned to the outer one and
    truncates the body, so brace counting is the only reliable extractor here.
    """
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


ARM = func_body(JRN_T, r"void\s+LogSetupArmed\s*\(")
REJ = func_body(strip_comments(OM_T), r"if\s*\(!SendOrderWithRetry\(request, result\)\)")


# ----------------------------------------------------------------------
# 1. Arm silence (v5.42)
# ----------------------------------------------------------------------
print("\n-- Arm silence --")

check("LogSetupArmed located", ARM is not None)
check("arm writes the SUBJECT line", ARM is not None and "SUBJECT:" in ARM)
check("arm closes the handle", ARM is not None and "CloseHandle();" in ARM)
# v5.42: arming is an intermediate stage, so it must NOT email. Only the two
# terminal writers (LogExit/LogCancellation) ship the aggregated session file.
check("arm does NOT email (v5.42 intermediate-silence)",
      ARM is not None and "SendMailFromFile" not in ARM)
check("LogEntry also does NOT email (v5.42)",
      "SendMailFromFile" not in func_body(JRN_T, r"void\s+LogEntry\s*\("))


# ----------------------------------------------------------------------
# 2. Terminal, readable rejection
# ----------------------------------------------------------------------
print("\n-- Terminal rejection --")

check("rejection branch located", REJ is not None)
check("rejection maps the retcode via GetTradeRetcodeString()",
      REJ is not None and
      "GetTradeRetcodeString(result.retcode)" in REJ)
check("rejection captures a readable error string",
      REJ is not None and
      re.search(r"string\s+errorStr\s*=\s*GetTradeRetcodeString\(",
                REJ) is not None)
check("rejection journals a LogCancellation()",
      REJ is not None and "LogCancellation(" in REJ)
check("rejection reason names the broker refusal + code",
      REJ is not None and
      '"Broker Rejected Market Order: "' in REJ and
      "IntegerToString(" in REJ)
check("rejection vetoes the spent block",
      REJ is not None and "isVetoed" in REJ and "VETO_BROKEN" in REJ)
check("rejection drops the virtual order",
      REJ is not None and "RemoveVirtualOrderAt(v)" in REJ)
# The v5.37 latch-and-retry comment lived on those source lines; assert on the
# RAW source, since REJ has comments stripped and would pass vacuously.
check("rejection no longer keeps the latch-and-retry path",
      "keep the one-shot latch SET" not in OM_T and
      "FIX (v5.37)" not in OM_T)


# ----------------------------------------------------------------------
# 3. Hybrid router (v5.40 supersedes the v5.39 "guard retired" pin)
# ----------------------------------------------------------------------
# v5.40 reintroduces a live-price decision, but as a ROUTER not a guard: a
# legal price becomes a resting BROKER limit (physical), an illegal one arms
# virtually. GetPriceBoundaryBuffer() therefore HAS a caller again, and the
# physical branch carries the priceBuffer local.
print("\n-- Hybrid router --")

ARM_OM = func_body(OM_T, r"bool\s+PlaceOrArmOrder\s*\(int blockIndex")
check("PlaceOrArmOrder located", ARM_OM is not None)
check("router consumes the boundary buffer to pick the route",
      ARM_OM is not None and "GetPriceBoundaryBuffer()" in ARM_OM)
check("router sends a PHYSICAL pending order",
      ARM_OM is not None and "TRADE_ACTION_PENDING" in ARM_OM)
check("router arms a VIRTUAL order when the price is through",
      ARM_OM is not None and "SVirtualOrder vo;" in ARM_OM)
check("router labels the physical route in its journal call",
      ARM_OM is not None and "LogSetupArmed(true," in ARM_OM)
check("router labels the virtual route in its journal call",
      ARM_OM is not None and "LogSetupArmed(false," in ARM_OM)
check("boundary helper is live again (has a caller)",
      "GetPriceBoundaryBuffer()" in OM_T and
      "GetPriceBoundaryBuffer(void)" in OM_T)


# ----------------------------------------------------------------------
# 4. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.39 or later", parts >= (5, 39), RELEASE)

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
    print("v5.39 ARM (SILENT) + TERMINAL REJECTION - STATIC PROBE")
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
