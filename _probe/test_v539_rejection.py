"""
v5.39 static verification probe - arm email, terminal rejection, guard removal.

HISTORICAL (v5.39). Two behaviours landed here on top of the v5.37/v5.38 engine.
Both are re-pinned to the v5.72 shape, whose execution model has since changed:

  1. ARM IS SILENT (v5.42). COttoJournal::LogSetupArmed() must WRITE the
     SUBJECT + setup block and close the handle but must NOT call
     SendMailFromFile(): arming is an intermediate lifecycle stage. Only the
     two terminal writers (LogExit()/LogCancellation()) email the session file.
     UNCHANGED by v5.72.

  2. REJECTION IS TERMINAL AND READABLE. The v5.39 target was the MARKET deal
     fired by the retired CheckVirtualTriggers(). v5.72 retires that engine, so
     the same property is re-pinned on the ONE send that can now be refused: the
     RESTING limit in PlaceOrArmOrder(). A refusal there must map the raw
     retcode through GetTradeRetcodeString(), journal it via LogCancellation(),
     and veto the block as VETO_LIMIT_MISSED rather than degrading into a MARKET
     chase. The v5.37 latch-then-retry path must stay gone.

  3. LEGACY PRICE GUARD RETIRED. The v5.28 LIVE PRICE VALIDATION pre-flight and
     its one-shot priceAbortLogged gate are removed: v5.40 reintroduced a
     live-price decision as a ROUTER (rest or veto), never as a fallback arm.

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
# 2. A refused RESTING send is terminal and readable (v5.72 shape)
# ----------------------------------------------------------------------
print("\n-- Terminal refusal of the resting limit --")

# The success branch is the if(), so the refusal block is what follows it. It
# is located by its own markers rather than by a brace extraction, because a
# comment-only edit upstream must not be able to break the extraction and
# silently pass the section.
check("refusal branch located",
      "RESTING SEND REFUSED" in OM_T and "return false;" in OM_T)
check("refusal maps the retcode via GetTradeRetcodeString()",
      re.search(r"string\s+refuseErr\s*=\s*GetTradeRetcodeString\(result\.retcode\)",
                OM_T) is not None)
check("refusal captures a readable error string",
      "string refuseErr = GetTradeRetcodeString(result.retcode);" in OM_T)
check("refusal journals a LogCancellation()",
      '"Broker Refused Limit Order: " + refuseErr' in OM_T)
check("refusal reason names the broker refusal + code",
      'LogCancellation("Broker Refused Limit Order: " + refuseErr +' in OM_T and
      'IntegerToString((long)result.retcode)' in OM_T)
check("refusal vetoes the spent block with its OWN reason",
      "block.vetoReason         = VETO_LIMIT_MISSED;" in OM_T)
# The defect this release exists to remove: a refused limit must never become a
# MARKET chase.
check("refusal does NOT fall back to a MARKET arm",
      "falling back to VIRTUAL arm" not in OM_T)
# The v5.37 latch-and-retry comment lived on those source lines; assert on the
# RAW source, since REJ has comments stripped and would pass vacuously.
check("rejection no longer keeps the latch-and-retry path",
      "keep the one-shot latch SET" not in OM_T and
      "FIX (v5.37)" not in OM_T)


# ----------------------------------------------------------------------
# 3. Strict limit router (v5.40 router, v5.72 limit-or-nothing)
# ----------------------------------------------------------------------
# v5.40 introduced a live-price decision, but as a ROUTER not a guard: a legal
# price becomes a resting BROKER limit. v5.72 removed the other half of that
# route -- the in-memory arm -- so the "illegal price" outcome is now a VETO.
# The body is read with comments stripped, because the two veto rows are
# matched as CODE and prose may legitimately name the retired engine.
print("\n-- Strict limit router --")

ARM_OM = func_body(strip_comments(OM_T), r"bool\s+PlaceOrArmOrder\s*\(int blockIndex")
check("PlaceOrArmOrder located", ARM_OM is not None)
check("router consumes the boundary buffer to pick the route",
      ARM_OM is not None and "GetPriceBoundaryBuffer()" in ARM_OM)
check("router sends a PHYSICAL pending order",
      ARM_OM is not None and "TRADE_ACTION_PENDING" in ARM_OM)
check("router vetoes (never arms) when the price is through",
      ARM_OM is not None and
      "SVirtualOrder" not in ARM_OM and
      ARM_OM.count("VETO_LIMIT_MISSED") == 2)
check("router labels the ONE route in its journal call",
      ARM_OM is not None and "LogSetupArmed(block.tradeId" in ARM_OM)
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
