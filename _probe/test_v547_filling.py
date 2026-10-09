"""
v5.47 static verification probe - ORDER-FILLING ROUTING ON THE SEND CHOKEPOINT.

SendOrderWithRetry() in COttoOrderManager.mqh is the EA's single outbound trade
call: all seven call sites -- pending limit placement, position close, an
already-submitted DEAL, SL/TP modify, reversal entry and the pyramid tranche --
funnel through the one OrderSend() inside it. So the filling mode chosen there
decides the fate of every request the EA sends, and one wrong assignment is
simultaneously a no-op for six paths and fatal for the seventh.

Before v5.47 the function assigned the dynamic mode unconditionally:

    request.type_filling = GetFillingMode();   // dynamic FOK/IOC/RETURN

GetFillingMode() reads SYMBOL_FILLING_MODE, which advertises what DEALS the
symbol permits (FOK / IOC). That is the correct question for a MARKET deal and
the wrong one for a resting pending limit, which carries no executable price at
send time: the dynamic FOK/IOC is reported as structurally invalid
(TRADE_RETCODE_INVALID_FILL) on a strict venue. The failure is silent rather
than loud, which is what makes it worth a probe:

  * the request is rejected, and TRADE_RETCODE_INVALID_FILL is not in the
    retry ladder's retry set, so the loop does not re-attempt it;
  * the limit is therefore never placed, and the strategy simply has no order
    where it believes it has one.

v5.47 narrows the dynamic mode to the action it is meaningful for and routes
every other action to ORDER_FILLING_RETURN, the only filling mode MT5 defines
for a pending order. The properties pinned below are structural -- the
compiler gate cannot see them. Pure static analysis of the shipped sources; no
MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "CHighTableAuditor.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "COttoNewsFilter.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so the multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


OM_T = read(OM)

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


# The one outbound trade call, and the driving function around it.
RAW = func_body(OM_T, r"bool\s+SendOrderWithRetry\s*\(")
check("SendOrderWithRetry located", RAW is not None)

# Comment-stripped, so the v5.47 explanatory note that QUOTES the old
# unconditional assignment cannot itself satisfy -- or break -- any check.
SEND = strip_comments(RAW) if RAW else ""

# The whole file with comments removed: a comment that merely MENTIONS
# OrderSend() must not be counted as a second call site.
OM_CODE = strip_comments(OM_T)

# ----------------------------------------------------------------------
# 1. SINGLE CHOKEPOINT
# ----------------------------------------------------------------------
# If a second OrderSend( ever appears, the guard below stops being a
# chokepoint and a new call site can bypass it silently. Counted on the
# comment-stripped file, so prose that names OrderSend() is not a call.
check("the file holds exactly ONE OrderSend() call",
      len(re.findall(r"\bOrderSend\s*\(", OM_CODE)) == 1,
      "found %d" % len(re.findall(r"\bOrderSend\s*\(", OM_CODE)))
check("that OrderSend() lives inside SendOrderWithRetry",
      len(re.findall(r"\bOrderSend\s*\(", SEND)) == 1,
      "found %d" % len(re.findall(r"\bOrderSend\s*\(", SEND)))
check("OrderSend still dispatches the caller's request/result pair",
      re.search(r"OrderSend\s*\(\s*request\s*,\s*result\s*\)", SEND) is not None)

# ----------------------------------------------------------------------
# 2. THE GUARD IS ACTION-GATED, NOT UNCONDITIONAL
# ----------------------------------------------------------------------
# This is the release. Asserted as one whole anchored construct first, then in
# pieces so a failure names the exact half that regressed.
GUARD = (r"if\s*\(\s*request\.action\s*==\s*TRADE_ACTION_DEAL\s*\)\s*\n"
         r"\s*request\.type_filling\s*=\s*GetFillingMode\(\)\s*;\s*\n"
         r"\s*else\s*\n"
         r"\s*request\.type_filling\s*=\s*ORDER_FILLING_RETURN\s*;")
check("the filling mode is chosen by a single DEAL-gated if/else",
      len(re.findall(GUARD, SEND)) == 1,
      "matches=%d" % len(re.findall(GUARD, SEND)))

check("the dynamic mode still applies to a MARKET deal",
      re.search(r"if\s*\(\s*request\.action\s*==\s*TRADE_ACTION_DEAL\s*\)", SEND)
      is not None)
check("the DEAL branch assigns the dynamic mode",
      re.search(r"if\s*\(\s*request\.action\s*==\s*TRADE_ACTION_DEAL\s*\)\s*\n"
                r"\s*request\.type_filling\s*=\s*GetFillingMode\(\)\s*;", SEND)
      is not None)
check("every other action takes ORDER_FILLING_RETURN",
      re.search(r"\belse\s*\n\s*request\.type_filling\s*=\s*ORDER_FILLING_RETURN\s*;",
                SEND) is not None)

# The pre-v5.47 defect: the function wrote filling exactly ONCE, so the value
# was whatever the resolver returned no matter the action. Exactly two
# assignments now, one per branch -- and only one of them dynamic.
check("the filling mode is now written exactly twice, once per branch",
      len(re.findall(r"request\.type_filling\s*=", SEND)) == 2,
      "found %d" % len(re.findall(r"request\.type_filling\s*=", SEND)))
check("only the DEAL branch resolves a dynamic mode",
      len(re.findall(r"request\.type_filling\s*=\s*GetFillingMode\s*\(", SEND)) == 1,
      "found %d" % len(re.findall(r"request\.type_filling\s*=\s*GetFillingMode\s*\(", SEND)))

# ----------------------------------------------------------------------
# 3. THE DYNAMIC READ IS NOT DUPLICATED, AND STILL PRECEDES THE SEND
# ----------------------------------------------------------------------
check("GetFillingMode() is consulted exactly once inside the function",
      len(re.findall(r"\bGetFillingMode\s*\(", SEND)) == 1,
      "found %d" % len(re.findall(r"\bGetFillingMode\s*\(", SEND)))

guard_at = SEND.find("TRADE_ACTION_DEAL")
send_at = SEND.find("OrderSend(")
check("the guard is placed BEFORE the send",
      guard_at >= 0 and send_at >= 0 and guard_at < send_at,
      "guard=%d send=%d" % (guard_at, send_at))

# ----------------------------------------------------------------------
# 4. THE RESOLVER ITSELF IS UNTOUCHED
# ----------------------------------------------------------------------
GM = strip_comments(func_body(OM_T,
                              r"ENUM_ORDER_TYPE_FILLING\s+GetFillingMode\s*\(")
                    or "")
check("GetFillingMode() located", GM != "")
check("the resolver still reads the symbol's SYMBOL_FILLING_MODE bitmask",
      re.search(r"SymbolInfoInteger\s*\(\s*m_symbol\s*,\s*SYMBOL_FILLING_MODE\s*\)",
                GM) is not None)
check("the resolver still prefers FOK, then IOC, then RETURN",
      re.search(r"SYMBOL_FILLING_FOK\s*\)\s*!=\s*0\s*\)\s*return\s+ORDER_FILLING_FOK", GM)
      is not None and
      re.search(r"SYMBOL_FILLING_IOC\s*\)\s*!=\s*0\s*\)\s*return\s+ORDER_FILLING_IOC", GM)
      is not None and
      re.search(r"return\s+ORDER_FILLING_RETURN\s*;", GM) is not None)

# ----------------------------------------------------------------------
# 5. THE CALL-SITE MATRIX THE CHOKEPOINT MUST COVER
# ----------------------------------------------------------------------
# The bug's blast radius: the pending builder must NOT set its own filling
# mode (it relies on the chokepoint), while a directly-built DEAL may.
ACTIONS = set(re.findall(r"\.action\s*=\s*(TRADE_ACTION_\w+)", OM_T))
check("all four request actions still reach the chokepoint",
      ACTIONS == {"TRADE_ACTION_PENDING", "TRADE_ACTION_REMOVE",
                  "TRADE_ACTION_DEAL", "TRADE_ACTION_SLTP"},
      "actions=%s" % sorted(ACTIONS))

calls = len(re.findall(r"\bSendOrderWithRetry\s*\(", OM_CODE))
check("all seven call sites still route through the chokepoint",
      calls == 8, "matches=%d (1 definition + 7 call sites expected)" % calls)

# The pending builder is the one the defect mis-served; it must keep NO
# private filling assignment, or a future edit can re-introduce a split path.
PENDING_BLK = func_body(OM_CODE, r"bool\s+PlaceOrArmOrder\s*\(") or ""
check("the pending-order builder located", PENDING_BLK != "")
check("the pending-order builder does not set its own filling mode",
      "type_filling" not in PENDING_BLK)
check("a directly-built DEAL may still set it, and does",
      re.search(r"req\.type_filling\s*=\s*GetFillingMode\(\)\s*;", OM_CODE)
      is not None)

# ----------------------------------------------------------------------
# 6. RELEASE STAMP
# ----------------------------------------------------------------------
stamps = []
for f in ALL_FILES:
    m = re.search(r'#property version\s+"(\d+\.\d+)"', read(os.path.join(ROOT, f)))
    if m:
        stamps.append(m.group(1))
check("all %d shipped sources carry a version stamp" % len(ALL_FILES),
      len(stamps) == len(ALL_FILES), "found %d" % len(stamps))
check("every shipped source is stamped 5.47",
      bool(stamps) and set(stamps) == {"5.47"}, "stamps=%s" % sorted(set(stamps)))


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.47 ORDER-FILLING ROUTING ON THE SEND CHOKEPOINT - STATIC PROBE")
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
