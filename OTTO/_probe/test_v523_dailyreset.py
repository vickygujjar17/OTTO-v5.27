"""
Logic verification for the v5.23 daily-reset timestamp fix.

The bug this pins: `CheckDailyReset()` and `OnInit()` derived the reset anchor
with

    MqlDateTime dt; TimeCurrent(dt); datetime anchor = StructToTime(dt);

Neither call truncates the time-of-day, so `anchor` carried the live HH:MM:SS
and differed on essentially every tick. The guard

    if(anchor != g_lastMidnightCheck)

therefore re-baselined `g_dailyResetBalance` continuously, turning the 3% daily
drawdown limit into a pure floating-loss measure and making `g_dailyDD_Paused`
non-persistent. The fix uses `iTime(_Symbol, PERIOD_D1, 0)`, which returns the
00:00 server timestamp of the current day.

The helpers below reproduce BOTH forms from raw epoch seconds so the tick-by-tick
divergence is demonstrated rather than asserted.
"""

SECONDS_PER_DAY = 86400


def struct_to_time_form(now_epoch):
    """
    Faithful model of the OLD code path.

    `TimeCurrent(dt)` fills a MqlDateTime with the CURRENT wall-clock instant
    (year/month/day/hour/min/sec all preserved); `StructToTime(dt)` converts it
    straight back. No field is zeroed, so the result == now_epoch exactly.
    """
    return now_epoch


def itime_d1_form(now_epoch):
    """
    Model of the NEW code path.

    `iTime(_Symbol, PERIOD_D1, 0)` returns the OPEN time of the current daily
    candle -- i.e. the server 00:00 boundary of the day containing `now_epoch`.
    """
    return now_epoch - (now_epoch % SECONDS_PER_DAY)


def reset_fires(anchor, last_check):
    """The guard as written in v5.22: fires whenever the anchor differs."""
    return anchor != last_check


def reset_fires_v523(anchor, last_check):
    """v5.23 guard: also rejects the invalid 0 returned while D1 loads."""
    return anchor != 0 and anchor != last_check


def check(name, got, want):
    ok = got == want
    print("  [%s] %-60s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def checkf(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("  [%s] %-60s got=%.4f want=%.4f" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def main():
    print("=" * 80)
    print("v5.23 DAILY-RESET TIMESTAMP VERIFICATION")
    print("=" * 80)
    ok = True

    # A day boundary and a moment 09:30:15 into that day.
    DAY0 = 1758326400            # 2025-09-20 00:00:00 server
    T1 = DAY0 + 9 * 3600 + 30 * 60 + 15      # 2025-09-20 09:30:15
    T2 = T1 + 1                              # one second later
    T3 = T1 + 3600                           # one hour later
    T4 = DAY0 + SECONDS_PER_DAY + 120        # 00:02:00 the NEXT day

    print("\n-- The OLD form (StructToTime(TimeCurrent(dt))) does NOT truncate --")
    ok &= checkf("old form at 09:30:15 returns the raw instant", struct_to_time_form(T1), T1)
    ok &= checkf("old form at 09:30:16 returns the raw instant", struct_to_time_form(T2), T2)
    ok &= check("old form changes between two ticks one second apart",
                struct_to_time_form(T1) != struct_to_time_form(T2), True)
    # THE BUG: the guard fires on every tick, not once per day.
    ok &= check("OLD guard fires at 09:30:15", reset_fires(struct_to_time_form(T1), DAY0), True)
    ok &= check("OLD guard fires AGAIN at 09:30:16", reset_fires(struct_to_time_form(T2), DAY0), True)
    ok &= check("OLD guard fires at 10:30:15 too", reset_fires(struct_to_time_form(T3), DAY0), True)

    print("\n-- The NEW form (iTime PERIOD_D1) truncates to 00:00 --")
    ok &= checkf("ivalue at 09:30:15 -> 00:00 of that day", float(itime_d1_form(T1)), float(DAY0))
    ok &= checkf("value at 09:30:16 -> same 00:00", float(itime_d1_form(T2)), float(DAY0))
    ok &= check("NEW form is stable across ticks in the same day",
                itime_d1_form(T1) == itime_d1_form(T2), True)
    ok &= check("NEW form is stable across hours in the same day",
                itime_d1_form(T1) == itime_d1_form(T3), True)
    # This is the whole point: with g_lastMidnightCheck == DAY0, the guard is
    # FALSE all day and only becomes true after the boundary.
    ok &= check("NEW guard does NOT fire at 09:30:15",
                reset_fires_v523(itime_d1_form(T1), DAY0), False)
    ok &= check("NEW guard does NOT fire at 09:30:16",
                reset_fires_v523(itime_d1_form(T2), DAY0), False)
    ok &= checkf("NEW form rolls over to the next 00:00",
                 float(itime_d1_form(T4)), float(DAY0 + SECONDS_PER_DAY))
    ok &= check("NEW guard DOES fire once after the boundary",
                reset_fires_v523(itime_d1_form(T4), DAY0), True)

    print("\n-- Tick-count comparison over a simulated day --")
    # Walk a full day in 5-second steps from 00:00 and count how many times each
    # form would re-baseline g_dailyResetBalance.
    old_hits = new_hits = 0
    old_anchor = new_anchor = DAY0
    for t in range(DAY0, DAY0 + SECONDS_PER_DAY, 5):
        if reset_fires(struct_to_time_form(t), old_anchor):
            old_anchor = struct_to_time_form(t)
            old_hits += 1
        if reset_fires_v523(itime_d1_form(t), new_anchor):
            new_anchor = itime_d1_form(t)
            new_hits += 1
    steps = SECONDS_PER_DAY // 5
    # The walk starts AT the boundary, so the very first step compares the
    # anchor against itself and legitimately does not count as a change; every
    # subsequent step does. i.e. 17280 samples -> 17279 changes.
    ok &= check("OLD form resets on every step after the first",
                old_hits, steps - 1)
    ok &= check("NEW form resets exactly 0 times inside the same day", new_hits, 0)
    print("       OLD resets=%d of %d steps  NEW resets=%d  -> the 3%% daily anchor was inert"
          % (old_hits, steps, new_hits))

    print("\n-- Invalid-timestamp guard (iTime returns 0 while D1 loads) --")
    # iTime yields 0 before the D1 series exists. The v5.23 guard must reject
    # that, otherwise the first tick would fire a spurious reset.
    ok &= check("v5.22 guard FIRES on 0 (spurious reset)", reset_fires(0, DAY0), True)
    ok &= check("v5.23 guard rejects 0", reset_fires_v523(0, DAY0), False)
    ok &= check("v5.23 guard rejects 0 on a zero anchor too", reset_fires_v523(0, 0), False)
    # Once D1 arrives, exactly one legitimate reset happens.
    ok &= check("v5.23 fires once when D1 becomes available",
                reset_fires_v523(DAY0, 0), True)

    print("\n-- End-to-end: pause persists until the boundary --")
    # Simulate the full CheckDailyReset body: a 3% breach at 09:30 must keep
    # g_dailyDD_Paused true for the REST of the day and clear only at the first
    # tick after 00:00. The walk samples every 60s starting at 09:30:15, so the
    # first post-boundary sample is 00:00:15 -- one grid step past midnight.
    anchor = DAY0
    paused = True                       # breached at 09:30
    lifted_at = None
    for t in range(T1, DAY0 + SECONDS_PER_DAY + 120, 60):
        a = itime_d1_form(t)
        if a != 0 and a != anchor:
            anchor = a
            if paused:
                paused = False
                lifted_at = t
    ok &= check("pause survived the rest of the day", lifted_at is not None, True)
    boundary = DAY0 + SECONDS_PER_DAY
    ok &= check("pause lifted strictly AFTER the boundary",
                (lifted_at or 0) >= boundary, True)
    ok &= check("pause lifted within one sampling step of the boundary",
                (lifted_at or 0) - boundary < 60, True)
    ok &= check("pause was NOT lifted early on the breach day",
                (lifted_at or 0) >= boundary, True)
    ok &= checkf("lift landed at 00:00:15 (first sample past midnight)",
                 float(lifted_at if lifted_at else 0), float(boundary + 15))
    ok &= check("pause is cleared after the boundary", paused, False)

    print()
    print("=" * 80)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 80)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
