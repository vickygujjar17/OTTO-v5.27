"""
v5.30 static verification probe - strict pre-flight limit-price validation.

Pins the behaviours that eliminate the repeated `[Invalid price]` rejections
from the broker. The MQL5 compiler gate can prove none of them, because every
one is a runtime property of how a price is derived and re-sent:

  1. TICK-SIZE SNAP. Pine computes the entry off zone geometry, so it lands
     off the symbol's trade tick grid. A price that is off-grid is refused
     with TRADE_RETCODE_INVALID_PRICE even when it rests on the correct side
     of the market. The snap must be guarded by `tickSize > 0.0` (synthetic
     and some CFD symbols report 0, which would otherwise divide by zero).
  2. BOUNDARY BUFFER. max(STOPS_LEVEL, FREEZE_LEVEL) * point + one point.
     STOPS_LEVEL alone is wrong on brokers that publish only FREEZE_LEVEL,
     and a price exactly ON the boundary is still refused by some servers.
  3. ONE-SHOT LOG GATE. PlaceOrdersForArmedBlocks runs on every OnTick, so a
     single penetration would otherwise print one ABORT line per tick. The
     gate must be a block field (survives across ticks) and must be CLEARED
     once the price is back inside the boundary, so a later penetration logs
     again. The block must stay armed - NOT vetoed.
  4. RETRY RE-QUOTE. The v5.28 path re-priced a BUY_LIMIT onto the live Ask,
     which is invalid by definition and was the dominant spam generator:
     every retry re-sent the bad price. A limit must be re-quoted against the
     side it may legally rest on.
  5. DUPLICATE-SHIELD UNITS. The tolerance band must track the snapped grid
     (min(tick, point)), or the snap can slide an entry outside the band.

v5.39 UPDATE. The v5.28 LIVE PRICE VALIDATION pre-flight this probe was built
around was DELETED from ArmVirtualOrder(): a v5.37+ setup is an in-memory
SVirtualOrder, so no limit ever rests at the broker and TRADE_RETCODE_INVALID_PRICE
cannot occur. Bullet 3 (the one-shot priceAbortLogged gate) and the GUARD-facing
half of bullets 1/2 therefore no longer describe live code. Those assertions now
pin the guard's ABSENCE (so a half-wired re-introduction is caught), while the
snap mechanics, the buffer-helper internals, the retry re-quote, the
duplicate-shield units and the version stamps remain live contracts.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
BM = os.path.join(ROOT, "COttoBlockManager.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
DEFS_T = read(DEFS)
BM_T = read(BM)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals.

    Needed because the v5.30 fix documents the OLD behaviour in place
    ("Re-pricing a BUY_LIMIT onto the live Ask is invalid by definition"),
    so a raw substring test on the function body would fail on its own
    explanatory comment. A '//' is only a comment when an even number of
    '"' precede it on the line.
    """
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


OM_C = strip_comments(OM_T)


def body(start_marker, end_marker):
    i = OM_C.find(start_marker)
    if i < 0:
        return None
    j = OM_C.find(end_marker, i + len(start_marker))
    return OM_C[i:j] if j > 0 else OM_C[i:]


PLACE = body("bool                    PlaceOrArmOrder(int blockIndex",
             "bool                    IsAnyOrderLiveAtPrice(double targetPrice")
SEND = body("bool                    SendOrderWithRetry(MqlTradeRequest &request",
            "string                  GetTradeRetcodeString(uint retcode)")
BUF = body("double                  GetPriceBoundaryBuffer(void)",
           "double                  SnapToTick(double price)")
SNAP = body("double                  SnapToTick(double price)",
            "double                  GetAsk(void)")
SHIELD = body("bool                    IsAnyOrderLiveAtPrice(double targetPrice",
              "\n\n")


# ----------------------------------------------------------------------
# 1. Tick-size snap
# ----------------------------------------------------------------------
print("\n-- Tick-size snap --")

check("PlaceOrArmOrder located", PLACE is not None)
check("SendOrderWithRetry located", SEND is not None)
check("GetPriceBoundaryBuffer located", BUF is not None)
check("SnapToTick located", SNAP is not None)

check("entry price is snapped onto the tick grid",
      re.search(r"double\s+entryPrice\s*=\s*SnapToTick\(CalcEntryPrice\(block\)\)",
                OM_T) is not None)
check("snap rounds to the nearest tick",
      "MathRound(price / tick) * tick" in SNAP)
check("snap is guarded against a zero tick size", "tick > 0.0" in SNAP)
check("snap normalises to the symbol's own digits",
      "SYMBOL_DIGITS" in SNAP and "SYMBOL_TRADE_TICK_SIZE" in SNAP)
# v5.39: the live-price guard comparisons the snap used to precede are gone.
check("entry price is snapped (v5.39: guard comparisons retired)",
      OM_T.find("SnapToTick(CalcEntryPrice(block))") >= 0
      and "entryPrice >= (liveBid - priceBuffer)" not in OM_T
      and "entryPrice <= (liveAsk + priceBuffer)" not in OM_T)


# ----------------------------------------------------------------------
# 2. Boundary buffer - max(STOPS_LEVEL, FREEZE_LEVEL) + 1 point
# ----------------------------------------------------------------------
print("\n-- Boundary buffer --")

check("buffer honours SYMBOL_TRADE_STOPS_LEVEL",
      "SYMBOL_TRADE_STOPS_LEVEL" in BUF)
check("buffer honours SYMBOL_TRADE_FREEZE_LEVEL",
      "SYMBOL_TRADE_FREEZE_LEVEL" in BUF)
check("buffer takes the WIDER of stops and freeze",
      re.search(r"MathMax\(stopsPts,\s*freezePts\)", BUF) is not None)
check("buffer adds one point of cushion on top",
      re.search(r"MathMax\(stopsPts,\s*freezePts\)\s*\*\s*point\s*\+\s*point",
                BUF) is not None)
check("buffer falls back when the symbol reports point 0",
      re.search(r"point\s*<=\s*0\.0\)\s*point\s*=\s*_Point", BUF) is not None)
# v5.40: the ROUTER consumes the boundary buffer to decide the route — a
# legal resting price stays PHYSICAL, an illegal one arms VIRTUAL.
check("PlaceOrArmOrder applies the live-price boundary to route",
      "GetPriceBoundaryBuffer()" in PLACE and "priceBuffer" not in PLACE and
      "boundary" in PLACE)

# The old local shadowed the same-named locals in ValidateStopDistance and
# AdjustSLToMinimum; the guard must no longer declare a bare `stopsLevel`.
check("guard no longer shadows `stopsLevel`", "double stopsLevel" not in PLACE)
# v5.39: the live bid/ask side checks and their invBuy/invSell reason literals
# were all removed with the guard. Assert each is gone so a half-wired
# re-introduction cannot slip back in unnoticed.
check("legacy BUY_LIMIT-vs-bid check is retired",
      re.search(r"entryPrice\s*>=\s*\(liveBid\s*-\s*priceBuffer\)",
                OM_T) is None)
check("legacy SELL_LIMIT-vs-ask check is retired",
      re.search(r"entryPrice\s*<=\s*\(liveAsk\s*\+\s*priceBuffer\)",
                OM_T) is None)
check("invBuy reason literal is retired", "invBuy" not in OM_T)
check("invSell reason literal is retired", "invSell" not in OM_T)
check("no live bid/ask locals remain in the order manager",
      "liveBid" not in OM_T and "liveAsk" not in OM_T)


# ----------------------------------------------------------------------
# 3. One-shot log gate on the block (RETIRED in v5.39)
# ----------------------------------------------------------------------
print("\n-- One-shot log gate (retired v5.39) --")

# The v5.28/v5.30 one-shot ABORT gate no longer exists: the live-price
# pre-flight it de-duplicated was removed in v5.39. The block field and the
# zero-init contract remain, and the guard must now be ABSENT.
check("SSniperBlock still carries the inert priceAbortLogged field",
      re.search(r"bool\s+priceAbortLogged\s*;", DEFS_T) is not None)
check("one-shot gate print is retired",
      re.search(r"EnableLogging\s*&&\s*!block\.priceAbortLogged",
                OM_T) is None)
check("guard abort no longer raises the gate",
      "block.priceAbortLogged = true;" not in PLACE)
check("guard abort no longer persists the gate to the block book",
      "block.priceAbortLogged = true;" not in OM_T)
check("blocks are zero-initialised at creation", "ZeroMemory(nb)" in BM_T)

# The guard region itself must be empty: the retired bare-local form is gone
# (the router names its local `boundary`, not `priceBuffer`).
g_start = OM_C.find("double priceBuffer = GetPriceBoundaryBuffer();")
check("live-price guard region is gone", g_start < 0)
check("router does NOT re-introduce the bare priceBuffer local",
      "double priceBuffer" not in OM_T)
check("abort does NOT veto the block (routing replaces the guard)",
      "IsAnyOrderLiveAtPrice(entryPrice, 5.0)" in OM_T)

# The retired guard used to precede the duplicate shield; the v5.40 shield is
# the last price gate before the route decision.
check("duplicate shield is still present",
      OM_T.find("IsAnyOrderLiveAtPrice(entryPrice, 5.0)") > 0)


# ----------------------------------------------------------------------
# 4. Retry re-quote - never move a limit onto the wrong side
# ----------------------------------------------------------------------
print("\n-- Retry re-quote --")

check("BUY_LIMIT retry re-quotes against the bid",
      re.search(r"ORDER_TYPE_BUY_LIMIT\)\s*\n\s*request\.price\s*=\s*"
                r"SnapToTick\(GetBid\(\)\)", SEND) is not None)
check("SELL_LIMIT retry re-quotes against the ask",
      re.search(r"ORDER_TYPE_SELL_LIMIT\)\s*\n\s*request\.price\s*=\s*"
                r"SnapToTick\(GetAsk\(\)\)", SEND) is not None)
check("market BUY still prices at the ask",
      re.search(r"ORDER_TYPE_BUY\)\s*\n\s*request\.price\s*=\s*GetAsk\(\)",
                SEND) is not None)
check("market SELL still prices at the bid",
      re.search(r"ORDER_TYPE_SELL\)\s*\n\s*request\.price\s*=\s*GetBid\(\)",
                SEND) is not None)
# The v5.28 defect: BUY_LIMIT grouped with the market-order branch, so a
# limit was re-priced onto the Ask and re-sent as invalid, MaxRetries times.
check("the invalid BUY_LIMIT-as-Ask retry is gone",
      "request.type == ORDER_TYPE_BUY || request.type == ORDER_TYPE_BUY_LIMIT"
      not in SEND)
check("retry path does not scale the stop-loss price",
      "request.sl" not in SEND)


# ----------------------------------------------------------------------
# 5. Duplicate-shield tolerance tracks the snapped grid
# ----------------------------------------------------------------------
print("\n-- Duplicate shield --")

check("shield located", SHIELD is not None)
check("shield tolerance uses min(tick, point)",
      re.search(r"unit\s*=\s*\(tick\s*>\s*0\.0\)\s*\?\s*"
                r"MathMin\(tick,\s*point\)\s*:\s*point", OM_T) is not None)
check("shield compares against the tick-aware unit",
      "(tolerancePoints * unit)" in OM_T)
check("shield no longer measures purely in points",
      "(tolerancePoints * point)" not in OM_T)


# ----------------------------------------------------------------------
# 6. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.30 or later", parts >= (5, 30), RELEASE)

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
    print("v5.30 STRICT PRE-FLIGHT PRICE VALIDATION - STATIC PROBE")
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



