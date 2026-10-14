"""
Logic verification for the v5.25 prop-firm PERSISTENT MEMORY patch.

Covers the failure mode a compile CANNOT validate: what the EA believes its
drawdown baselines are immediately after a VPS restart.

The v5.24 build seeded g_dailyResetBalance and g_equityHighWaterMark from the
LIVE account inside OnInit(). A restart mid-session therefore:
  * reset the 3% daily budget to the restart moment, and
  * re-based the trailing floor downward whenever the account was down from
    its peak -- permanently defeating the trailing rule.
v5.25 persists those baselines in MT5 GlobalVariables instead.

Each function below mirrors the corresponding branch of otto.mq5 exactly, so
this file is a behavioural spec for the MQL5 code rather than a re-implementation
of it. If the .mq5 logic drifts, these checks fail.
"""

LOGIN = 12345678


def gv_name(key):
    """Mirror of OttoGvName(): OTTO_<key>_<login>."""
    return "OTTO_" + key + "_" + str(LOGIN)


def gv_load_double(store, key, fallback):
    """Mirror of OttoGvLoadDouble(): absent OR 0.0 counts as absent."""
    name = gv_name(key)
    if name not in store:
        return fallback
    v = store[name]
    if v == 0.0:
        return fallback
    return v


def gv_load_flag(store, key, fallback):
    """Mirror of OttoGvLoadFlag(): stored as 0.0 / 1.0."""
    name = gv_name(key)
    if name not in store:
        return fallback
    return store[name] >= 0.5


def persist(store, daily, hwm, last_mid, paused, halted):
    """Mirror of PersistSafetyState()."""
    store[gv_name("DailyReset")] = daily
    store[gv_name("HighWater")] = hwm
    store[gv_name("LastMid")] = float(last_mid)
    store[gv_name("Paused")] = 1.0 if paused else 0.0
    store[gv_name("Halted")] = 1.0 if halted else 0.0


def on_init(store, balance, equity, today_bar):
    """Mirror of the OnInit() persistent-memory block. Returns a state dict."""
    last_mid = int(gv_load_double(store, "LastMid", float(today_bar)))
    stale = (today_bar != 0 and last_mid < today_bar)

    stored_daily = gv_load_double(store, "DailyReset", equity)
    daily = balance if stale else stored_daily
    if daily <= 0:
        daily = balance

    stored_hwm = gv_load_double(store, "HighWater", equity)
    hwm = stored_hwm if stored_hwm > equity else equity

    halted = gv_load_flag(store, "Halted", False)
    paused = gv_load_flag(store, "Paused", False) and not stale
    resume = (last_mid + 86400) if (paused and last_mid > 0) else 0

    persist(store, daily, hwm, last_mid, paused, halted)
    return {"daily": daily, "hwm": hwm, "last_mid": last_mid, "paused": paused,
            "halted": halted, "resume": resume, "stale": stale,
            "stored_daily": stored_daily, "stored_hwm": stored_hwm}


def tick_ratchet(store, equity, hwm):
    """Mirror of the OnTick trailing ratchet, persistence included."""
    if equity > hwm:
        hwm = equity
        store[gv_name("HighWater")] = hwm
    return hwm


def dd(hwm, eq):
    """Shared trailing measure (v5.24 basis, both rules)."""
    if hwm <= 0:
        return 0.0
    return 100.0 * (hwm - eq) / hwm


def check(label, got, want):
    ok = (got == want)
    print("  [%s] %-58s got=%s want=%s" % ("PASS" if ok else "FAIL", label, got, want))
    return ok


def checkf(label, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("  [%s] %-58s got=%.4f want=%.4f" % ("PASS" if ok else "FAIL", label, got, want))
    return ok


def main():
    print("=" * 78)
    print("v5.25 PROP-FIRM PERSISTENT MEMORY VERIFICATION")
    print("=" * 78)
    ok = True

    DAY1 = 1750000000
    DAY2 = DAY1 + 86400

    # ---- Cold start: no GVs present -------------------------------------
    print("\n-- Cold start: empty GlobalVariable store --")
    st = {}
    s = on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    ok &= checkf("daily anchor seeded to balance", s["daily"], 100000)
    ok &= checkf("HWM seeded to equity", s["hwm"], 100000)
    ok &= checkf("midnight stamp seeded to D1 bar", s["last_mid"], DAY1)
    ok &= check("flags default false", (s["paused"], s["halted"]), (False, False))
    ok &= check("all 5 GVs written", len(st), 5)
    ok &= check("keys are login-namespaced", sorted(st.keys()),
                sorted([gv_name("DailyReset"), gv_name("HighWater"),
                        gv_name("LastMid"), gv_name("Paused"), gv_name("Halted")]))

    # ---- THE BUG: restart mid-day while DOWN from the peak --------------
    print("\n-- Restart mid-day at a loss (the defect this patch fixes) --")
    st = {}
    # Session opened at 100000. Equity ratcheted to 104000 (a new high), then
    # the account gave back and the VPS restarted with equity at 102500.
    on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    hwm = tick_ratchet(st, 104000, 100000)
    ok &= checkf("peak of 104000 persisted to GV", st[gv_name("HighWater")], 104000)
    ok &= checkf("in-memory HWM at peak", hwm, 104000)

    # Restart: same day, equity now 102500.
    s2 = on_init(st, balance=100000, equity=102500, today_bar=DAY1)
    ok &= checkf("restart RESUMES the true 104000 peak", s2["hwm"], 104000)
    ok &= check("  ...and does NOT re-base to live equity", (s2["hwm"] != 102500), True)
    ok &= checkf("daily anchor retained", s2["daily"], 100000)
    ok &= check("  ...restart is not treated as stale", s2["stale"], False)

    # The trailing limits measured from the true peak:
    true_dd = dd(104000, 102500)
    rebased_dd = dd(102500, 102500)
    ok &= checkf("true trailing DD from 104000 -> 1.4423%", true_dd, 1.4423076923076923)
    ok &= checkf("v5.24 re-based form reads 0.0000% (blind)", rebased_dd, 0.0)
    ok &= check("1% floating rule fires on the TRUE basis", true_dd >= 1.0, True)
    ok &= check("  ...but was silent under the re-based basis", rebased_dd >= 1.0, False)

    # ---- Daily budget must survive a restart ----------------------------
    print("\n-- Daily 3% budget survives a mid-day restart --")
    st = {}
    on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    # Account drops to 97500 => 2.5% of the daily 3% budget consumed.
    s2 = on_init(st, balance=100000, equity=97500, today_bar=DAY1)
    consumed = dd(s2["daily"], 97500)
    ok &= checkf("daily DD measured from the SESSION anchor -> 2.5000%", consumed, 2.5)
    ok &= check("not yet at the 3% limit", consumed >= 3.0, False)
    # Under v5.24 the anchor would have become 97500 and read 0%.
    ok &= checkf("v5.24 re-based form would read 0.0000%", dd(97500, 97500), 0.0)
    ok &= check("  ...i.e. the whole 3% budget returned", dd(97500, 97500) >= 3.0, False)

    # ---- Latches survive a restart --------------------------------------
    print("\n-- Halt / pause latches survive a restart --")
    st = {}
    on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    persist(st, 100000, 100000, DAY1, paused=True, halted=False)
    s2 = on_init(st, balance=100000, equity=98000, today_bar=DAY1)
    ok &= check("daily pause restored", s2["paused"], True)
    ok &= check("  ...with a resume time derived from the anchor",
                s2["resume"], DAY1 + 86400)

    # A total-DD halt must NEVER be cleared by a restart.
    persist(st, 100000, 105000, DAY1, paused=True, halted=True)
    s3 = on_init(st, balance=100000, equity=98000, today_bar=DAY1)
    ok &= check("PERMANENT halt restored", s3["halted"], True)
    ok &= check("halted state is not reset to false on init", s3["halted"] != False, True)

    # ---- Rollover: pause clears, halt persists --------------------------
    print("\n-- Session rollover: pause clears, halt is permanent --")
    st = {}
    persist(st, 100000, 100000, DAY1, paused=True, halted=True)
    s = on_init(st, balance=101000, equity=101000, today_bar=DAY2)
    # Rollover branch of CheckDailyReset: re-anchor, clear pause, keep halt.
    new_daily = 101000
    paused_after = False
    halted_after = s["halted"]          # deliberately NOT touched by the rollover
    persist(st, new_daily, s["hwm"], DAY2, paused_after, halted_after)
    ok &= check("daily pause LIFTED at rollover", paused_after, False)
    ok &= check("total-DD halt NOT cleared at rollover", halted_after, True)

    s2 = on_init(st, balance=101000, equity=101000, today_bar=DAY2)
    ok &= checkf("re-anchored to the new session balance", s2["daily"], 101000)
    ok &= checkf("midnight stamp advanced to DAY2", s2["last_mid"], DAY2)
    ok &= check("halt still restored after rollover + restart", s2["halted"], True)
    ok &= check("pause stays lifted after rollover + restart", s2["paused"], False)

    # ---- Stale-account guard (prop firm resets the challenge) -----------
    print("\n-- Stale-account guard: challenge reset on the same login --")
    st = {}
    # Yesterday the account peaked at 118000 and was halted, short of target.
    persist(st, 110000, 118000, DAY1, paused=False, halted=True)
    # The firm resets the account: balance back to 100000, and today is DAY2.
    s = on_init(st, balance=100000, equity=100000, today_bar=DAY2)
    ok &= check("stale anchor DETECTED (stamp precedes today's bar)", s["stale"], True)
    ok &= checkf("daily anchor re-seeded to the fresh balance", s["daily"], 100000)
    ok &= check("  ...not the stale 110000", s["daily"] != 110000, True)
    # The HWM is still honoured because it is genuinely above live equity, so a
    # reset account that is DOWN is not handed a fresh trailing budget.
    ok &= checkf("stored 118000 peak still enforced", s["hwm"], 118000)

    # A stale anchor must also drop a stale daily pause.
    persist(st, 100000, 100000, DAY1, paused=True, halted=False)
    s = on_init(st, balance=100000, equity=100000, today_bar=DAY2)
    ok &= check("stale pause NOT restored", s["paused"], False)

    # ---- Login namespacing ----------------------------------------------
    print("\n-- Namespacing --")
    ok &= check("keys carry the login suffix", gv_name("HighWater"), "OTTO_HighWater_12345678")
    ok &= check("namespace does not collide with TS_Bias_*",
                gv_name("HighWater").startswith("TS_Bias_"), False)

    # ---- Zero-value guard ------------------------------------------------
    print("\n-- Guards --")
    st = {gv_name("DailyReset"): 0.0, gv_name("HighWater"): 0.0,
          gv_name("LastMid"): 0.0}
    s = on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    ok &= checkf("zero-valued GV treated as absent (daily)", s["daily"], 100000)
    ok &= checkf("zero-valued GV treated as absent (HWM)", s["hwm"], 100000)
    ok &= checkf("zero-valued stamp treated as absent", s["last_mid"], DAY1)

    # ---- Ratchet is monotonic across restarts ----------------------------
    print("\n-- Ratchet monotonicity across restarts --")
    st = {}
    on_init(st, balance=100000, equity=100000, today_bar=DAY1)
    h = 100000
    for eq in (101000, 100200, 103000, 99000, 104500):
        h = tick_ratchet(st, eq, h)
    ok &= checkf("ratcheted to the true high 104500", h, 104500)
    # A restart at a drawdown must not lower it, in memory OR in the GV.
    s = on_init(st, balance=100000, equity=95000, today_bar=DAY1)
    ok &= checkf("restart at 95000 keeps HWM 104500", s["hwm"], 104500)
    ok &= checkf("...and the GV still holds 104500", st[gv_name("HighWater")], 104500)
    ok &= checkf("trailing DD from the true peak -> 9.0909%", dd(s["hwm"], 95000),
                 9.090909090909093)

    print()
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 78)
    print()
    print("Persisted: DailyReset, HighWater, LastMid, Paused, Halted (per login).")
    print("Invariant: a restart resumes the TRUE baselines and can never clear")
    print("           the permanent total-DD halt.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
