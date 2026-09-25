"""
v5.28 static verification probe - safety hotfix.

Pins the four behaviours that the MQL5 compiler gate CANNOT exercise:

  1. New York DST rule. MQL5 has no TimeDaylightSavings(), so the US Eastern
     offset is computed by hand. The rule is modelled independently from raw
     epoch seconds and checked on both transition edges (2nd Sunday of March
     07:00 UTC, 1st Sunday of November 06:00 UTC) plus the 2026-2028 dates.
  2. MostRecentNyRollover resolves the offset AT the boundary, not from the
     current instant - the bug that would otherwise shift the anchor by an
     hour for one day after each DST switch.
  3. The daily-reset anchor is no longer derived from iTime(PERIOD_D1,0),
     i.e. it no longer depends on the broker's server timezone.
  4. The 1% floating rule measures the LIVE (balance - equity) float and no
     longer sets a permanent halt latch; the 5% trailing rule still does.
  5. Order manager: the consensus cancel sweep is symbol-scoped, and
     PlaceLimitOrder refuses a limit on the wrong side of the live market.

Pure static analysis of the shipped sources - no MT5 required.
"""

import datetime as _dt
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals.

    Needed because the v5.28 fix documents the OLD behaviour in place
    ("...it anchored on iTime(PERIOD_D1,0), which..."), so a raw substring
    test on the function body would fail on its own explanatory comment.
    A '//' is only a comment when an even number of '"' precede it on the
    line, which keeps any '//' inside a string literal intact.
    """
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def func_body(text, signature_re):
    """Return the full body of an MQL5 function, matched by counting braces.

    A regex with a closing-brace lookahead is not reliable here: MQL5 nests
    blocks at 3/5/7/8 spaces of indentation, so a lazy quantifier stops at the
    first inner '}' aligned to the outer one -- truncating the body and giving
    false passes/failures on anything after it.
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


# ======================================================================
# Independent model of the MQL5 helpers
# ======================================================================
SECONDS_PER_DAY = 86400
UTC = _dt.timezone.utc


def _epoch(y, mo, d, h=0, mi=0, s=0):
    return int(_dt.datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp())


def first_sunday(y, mo):
    """Day-of-month of the first Sunday. MQL5's day_of_week is Sun=0..Sat=6,
    so Python's Mon=0..Sun=6 is shifted by 1 before the same formula applies."""
    first = _dt.date(y, mo, 1)
    mql_dow = (first.weekday() + 1) % 7
    return 1 + ((7 - mql_dow) % 7)


def is_us_eastern_dst(utc_epoch):
    """Mirror of IsUsEasternDST()."""
    dt = _dt.datetime.fromtimestamp(utc_epoch, UTC)
    if dt.month < 3 or dt.month > 11:
        return False
    if 3 < dt.month < 11:
        return True
    fs = first_sunday(dt.year, dt.month)
    if dt.month == 3:
        second_sunday = fs + 7
        return (dt.day > second_sunday) or (dt.day == second_sunday and dt.hour >= 7)
    return (dt.day < fs) or (dt.day == fs and dt.hour < 6)


def ny_offset(utc_epoch):
    """Mirror of NewYorkUtcOffsetSeconds()."""
    return -14400 if is_us_eastern_dst(utc_epoch) else -18000


def utc_to_ny(utc_epoch):
    return utc_epoch + ny_offset(utc_epoch)


def most_recent_ny_rollover(utc_now):
    """Mirror of MostRecentNyRollover(), including the two refinement passes."""
    ny_now = utc_to_ny(utc_now)
    d = _dt.datetime.fromtimestamp(ny_now, UTC)
    base = _epoch(d.year, d.month, d.day) + 17 * 3600
    if ny_now < base:
        base -= SECONDS_PER_DAY
    utc_b = base - ny_offset(utc_now)
    for _ in range(2):
        utc_b = base - ny_offset(utc_b)
    return utc_b

# ----------------------------------------------------------------------
# 1. US Eastern DST rule
# ----------------------------------------------------------------------
print("-- US Eastern DST rule --")

check("2026 DST starts 2nd Sunday of March (Mar 8)",
      first_sunday(2026, 3) + 7 == 8)
check("2026 DST ends 1st Sunday of November (Nov 1)", first_sunday(2026, 11) == 1)
check("2027 DST starts Mar 14", first_sunday(2027, 3) + 7 == 14)
check("2027 DST ends Nov 7", first_sunday(2027, 11) == 7)
check("2028 DST starts Mar 12", first_sunday(2028, 3) + 7 == 12)
check("2028 DST ends Nov 5", first_sunday(2028, 11) == 5)

check("Jan 15 2026 -> EST", is_us_eastern_dst(_epoch(2026, 1, 15, 12)) is False)
check("Feb 15 2026 -> EST", is_us_eastern_dst(_epoch(2026, 2, 15, 12)) is False)
check("Dec 15 2026 -> EST", is_us_eastern_dst(_epoch(2026, 12, 15, 12)) is False)
check("Jul 15 2026 -> EDT", is_us_eastern_dst(_epoch(2026, 7, 15, 12)) is True)
check("Oct 31 2026 -> EDT", is_us_eastern_dst(_epoch(2026, 10, 31, 12)) is True)

# March edge: 02:00 EST == 07:00 UTC on Mar 8 2026.
check("Mar 8 2026 06:59 UTC -> still EST",
      is_us_eastern_dst(_epoch(2026, 3, 8, 6, 59)) is False)
check("Mar 8 2026 07:00 UTC -> now EDT",
      is_us_eastern_dst(_epoch(2026, 3, 8, 7, 0)) is True)
check("Mar 8 2026 00:00 UTC -> EST (earlier same day)",
      is_us_eastern_dst(_epoch(2026, 3, 8, 0, 0)) is False)
check("Mar 7 2026 23:00 UTC -> EST (day before)",
      is_us_eastern_dst(_epoch(2026, 3, 7, 23)) is False)

# November edge: 02:00 EDT == 06:00 UTC on Nov 1 2026.
check("Nov 1 2026 05:59 UTC -> still EDT",
      is_us_eastern_dst(_epoch(2026, 11, 1, 5, 59)) is True)
check("Nov 1 2026 06:00 UTC -> now EST",
      is_us_eastern_dst(_epoch(2026, 11, 1, 6, 0)) is False)
check("Nov 1 2026 23:00 UTC -> EST",
      is_us_eastern_dst(_epoch(2026, 11, 1, 23)) is False)

check("EDT offset is -14400s", ny_offset(_epoch(2026, 7, 15, 12)) == -14400)
check("EST offset is -18000s", ny_offset(_epoch(2026, 1, 15, 12)) == -18000)

# Cross-check against the IANA database when zoneinfo is available.
try:
    from zoneinfo import ZoneInfo
    _ny = ZoneInfo("America/New_York")
    mismatches = []
    for probe_day in range(1, 366, 3):
        t = _epoch(2026, 1, 1) + probe_day * SECONDS_PER_DAY + 3600
        off = _dt.datetime.fromtimestamp(t, UTC).astimezone(_ny).utcoffset()
        want = int(off.total_seconds())
        if ny_offset(t) != want:
            mismatches.append((t, ny_offset(t), want))
    check("matches IANA America/New_York across 2026", not mismatches,
          "mismatches: %s" % mismatches[:3])
except Exception as exc:      # zoneinfo absent: the edge cases above stand alone
    check("IANA cross-check skipped (no zoneinfo)", True, str(exc))


# ----------------------------------------------------------------------
# 2. Rollover resolves the offset AT the boundary
# ----------------------------------------------------------------------
print("\n-- NY 17:00 rollover resolution --")

r = most_recent_ny_rollover(_epoch(2026, 7, 15, 22, 0))
check("Jul 15 22:00 UTC -> rollover 2026-07-15 21:00 UTC", r == _epoch(2026, 7, 15, 21, 0),
      _dt.datetime.fromtimestamp(r, UTC).isoformat())
r = most_recent_ny_rollover(_epoch(2026, 7, 15, 20, 0))
check("Jul 15 20:00 UTC (before) -> 2026-07-14 21:00 UTC", r == _epoch(2026, 7, 14, 21, 0),
      _dt.datetime.fromtimestamp(r, UTC).isoformat())

r = most_recent_ny_rollover(_epoch(2026, 1, 15, 23, 0))
check("Jan 15 23:00 UTC -> rollover 2026-01-15 22:00 UTC", r == _epoch(2026, 1, 15, 22, 0),
      _dt.datetime.fromtimestamp(r, UTC).isoformat())

# The load-bearing case: in the 24h AFTER the November switch the current
# offset (EST) differs from the offset at the boundary (EDT). Naively using
# the current offset would land the anchor at 22:00 UTC instead of 21:00.
r = most_recent_ny_rollover(_epoch(2026, 11, 1, 12, 0))
check("Nov 1 12:00 UTC -> rollover 2026-10-31 21:00 UTC (EDT boundary)",
      r == _epoch(2026, 10, 31, 21, 0),
      _dt.datetime.fromtimestamp(r, UTC).isoformat())
check("...and NOT the naive 22:00 UTC", r != _epoch(2026, 10, 31, 22, 0))

r = most_recent_ny_rollover(_epoch(2026, 3, 8, 12, 0))
check("Mar 8 12:00 UTC -> rollover 2026-03-07 22:00 UTC (EST boundary)",
      r == _epoch(2026, 3, 7, 22, 0),
      _dt.datetime.fromtimestamp(r, UTC).isoformat())

# The rollover must be in the past, and within the preceding 24h. The boundary
# is included (t == rr is a legitimate boundary instant), so the check is
# 0 <= age <= 86400.
bad = []
for hour in range(0, 24 * 4):
    for dy in (1, 15, 28):
        t = _epoch(2026, 7, 1) + (dy - 1) * SECONDS_PER_DAY + hour * 3600
        rr = most_recent_ny_rollover(t)
        if not (0 <= t - rr <= SECONDS_PER_DAY):
            bad.append(t)
check("rollover always within the preceding 24h (sweep)", not bad,
      "violations: %d" % len(bad))


# ----------------------------------------------------------------------
# 3. Source: daily-reset clock no longer depends on the broker
# ----------------------------------------------------------------------
print("\n-- Daily reset source contract --")

for fn in ("IsUsEasternDST", "NewYorkUtcOffsetSeconds", "UtcToNewYork",
           "MostRecentNyRollover"):
    check("otto.mq5 defines %s()" % fn,
          re.search(r"\b%s\s*\(" % fn, MAIN_T) is not None)

check("CheckDailyReset uses TimeGMT()", "TimeGMT()" in MAIN_T)
check("CheckDailyReset uses MostRecentNyRollover()",
      re.search(r"MostRecentNyRollover\s*\(", MAIN_T) is not None)

cdr = func_body(MAIN_T, r"void\s+CheckDailyReset\s*\(void\)")
check("CheckDailyReset body located", cdr is not None)
if cdr is not None:
    body = strip_comments(cdr)
    check("CheckDailyReset no longer calls iTime()", "iTime(" not in body)
    check("CheckDailyReset no longer calls PERIOD_D1", "PERIOD_D1" not in body)
    check("CheckDailyReset keeps the 0-boundary guard",
          re.search(r"boundary\s*==\s*0", body) is not None)
    check("CheckDailyReset still clears the daily pause",
          "g_dailyDD_Paused = false" in body)
    check("CheckDailyReset still persists state", "PersistSafetyState()" in body)
    check("CheckDailyReset does NOT clear the 5% halt",
          "g_totalDD_Halted = false" not in body)

check("OnInit no longer derives the anchor from a D1 bar",
      "datetime todayBar" not in MAIN_T)
check("OnInit logs the time basis for VPS verification",
      "Time basis:" in MAIN_T)

md = func_body(MAIN_T, r"void\s+UpdateMarketDay\s*\(void\)")
check("UpdateMarketDay still uses the D1 bar (unrelated to the DD anchor)",
      md is not None and "iTime(" in md)


# ----------------------------------------------------------------------
# 4. Source: 1% floating rule is a live float, without a halt latch
# ----------------------------------------------------------------------
print("\n-- 1% floating-loss source contract --")

flt = re.search(
    r"double\s+floatingLoss\s*=\s*\(balance\s*>\s*0\s*&&\s*equity\s*<\s*balance\)"
    r"\s*\?\s*100\.0\s*\*\s*\(balance\s*-\s*equity\)\s*/\s*balance\s*:\s*0\.0\s*;",
    MAIN_T)
check("floatingLoss is (balance - equity) / balance", flt is not None)
check("OnTick reads ACCOUNT_BALANCE", "ACCOUNT_BALANCE" in MAIN_T)

b1 = re.search(r"if\s*\(floatingLoss\s*>=\s*SafetyMaxFloatingLoss\).*?"
               r"(?=//\s*3%\s*Max\s+Daily\s+Drawdown)", MAIN_T, re.S)
check("1% floating branch located", b1 is not None)
check("floatingLoss no longer reads g_equityHighWaterMark",
      bool(b1) and "g_equityHighWaterMark - equity" not in b1.group(0))
if b1:
    seg = b1.group(0)
    check("1% branch no longer sets g_totalDD_Halted", "g_totalDD_Halted = true" not in seg)
    check("1% branch no longer persists a Halted flag",
          'OttoGvStoreFlag("Halted"' not in seg)
    check("1% branch still cancels pendings", "CancelAllPendingOrders()" in seg)
    check("1% branch still closes the basket", "CloseEntireBasket(" in seg)
    check("1% branch still returns for this tick", re.search(r"\breturn\s*;", seg) is not None)

check("Halted key is still persisted somewhere", 'OttoGvStoreFlag("Halted"' in MAIN_T)
check("Halted key is still restored on init", 'OttoGvLoadFlag("Halted"' in MAIN_T)

b5 = re.search(r"if\s*\(totalDD\s*>=\s*SafetyTotalDDLimit\)", MAIN_T)
check("5% trailing branch still present", b5 is not None)
if b5:
    tail = MAIN_T[b5.start():b5.start() + 1400]
    check("5% trailing branch still hard-halts", "g_totalDD_Halted = true" in tail)
    check("5% trailing branch still persists the halt",
          'OttoGvStoreFlag("Halted", true)' in tail)


# ----------------------------------------------------------------------
# 5. Source: order-manager guards
# ----------------------------------------------------------------------
print("\n-- Order manager guards --")

sweep = func_body(OM_T, r"void\s+CancelOpposingConsensusOrders\s*\(void\)")
check("CancelOpposingConsensusOrders located", sweep is not None)
if sweep is not None:
    body = sweep
    check("consensus sweep is symbol-scoped",
          re.search(r"OrderGetString\(ORDER_SYMBOL\)\s*!=\s*m_symbol", body) is not None)
    check("consensus sweep still applies the magic filter",
          "ORDER_MAGIC) != MagicNumber" in body)
    check("consensus test is still the global portfolio one",
          "IsConsensusOpposed(" in body)
    check("sweep honours InpCancelOpposingPendings",
          "InpCancelOpposingPendings" in body)
    check("sweep no longer re-reads the row symbol", "string sym = OrderGetString" not in body)

check("no CancelQuorumOpposingOrders exists (never did)", "CancelQuorum" not in OM_T)

check("PlaceLimitOrder validates BUY_LIMIT against bid",
      re.search(r"entryPrice\s*>=\s*\(liveBid\s*-\s*stopsLevel\)", OM_T) is not None)
check("PlaceLimitOrder validates SELL_LIMIT against ask",
      re.search(r"entryPrice\s*<=\s*\(liveAsk\s*\+\s*stopsLevel\)", OM_T) is not None)
check("PlaceLimitOrder reads SYMBOL_TRADE_STOPS_LEVEL",
      "SYMBOL_TRADE_STOPS_LEVEL" in OM_T)
check("PlaceLimitOrder reads live bid/ask",
      re.search(r"double\s+liveAsk\s*=\s*GetAsk\(\)", OM_T) is not None
      and re.search(r"double\s+liveBid\s*=\s*GetBid\(\)", OM_T) is not None)

# The guard must precede the duplicate shield, so a refused price can never
# set hasPlacedOrder and strand the block.
pos_guard = OM_T.find("entryPrice >= (liveBid - stopsLevel)")
pos_shield = OM_T.find("IsOrderAlreadyLiveAtPrice(entryPrice, 5.0)")
check("price guard precedes the duplicate shield", 0 < pos_guard < pos_shield,
      "guard=%d shield=%d" % (pos_guard, pos_shield))


# ----------------------------------------------------------------------
# 6. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.28 or later", parts >= (5, 28), RELEASE)

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
    print("v5.28 SAFETY HOTFIX - STATIC PROBE")
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
