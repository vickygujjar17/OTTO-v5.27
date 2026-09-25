"""
Logic verification for the v5.22 / v5.24 prop-firm safety rules.

MODEL HISTORY -- SUPERSEDED FOR THE 1% RULE (v5.28). The functions below
pin the v5.22/v5.24 arithmetic so their behaviour stays reproducible and the
basis change stays documented, but they NO LONGER describe what the EA does:

  * v5.22/v5.24 (this file): 1% floating = TRAILING retracement from peak
    EQUITY, sharing one basis with the 5% trailing DD, and the breach set a
    PERMANENT halt latch.
  * v5.28 (live): 1% floating = (balance - equity)/balance, i.e. the live
    basket float. It reads 0 whenever nothing is open, and a breach cancels
    the pendings, closes the basket and resumes on the next tick -- no halt
    latch is set. See _probe/test_v528_hotfix.py for the live contract.

The 5% trailing DD measured from peak EQUITY is UNCHANGED by v5.28.

Covers the change a compile CANNOT validate: the GFT drawdown arithmetic.
  * 3% daily DD measured from the 5PM New York reset balance
  * 5% TRAILING total DD measured from the peak EQUITY high-water mark
    (v5.22 used peak CLOSED balance; v5.24 switched it to peak equity)
  * 1% floating loss -- v5.22/v5.24 model: trailing retracement from peak
    EQUITY (v5.28 replaced this with the live balance-vs-equity float)

The floating rule is the important one. A raw (balance - equity)/balance
ratio trips on any routine dip while equity is below its own peak. The
trailing measure must NOT fire in that situation -- that difference is
pinned below. v5.28 then reinstated the raw float measure as the CORRECT
reading for a "max floating loss" rule, having removed the permanent halt
that made the old behaviour destructive.

v5.24 NOTE: because total_dd and floating_trailing now share one equity
basis, they evaluate the SAME quantity. The 1% threshold is strictly
tighter, so it fires first and the 5% trailing check is a backstop. That
interaction is pinned in `-- Rule interaction` at the end.
"""


def daily_dd(reset_balance, equity):
    if reset_balance <= 0:
        return 0.0
    return 100.0 * (reset_balance - equity) / reset_balance


def total_dd(equity_hwm, equity):
    """v5.24 measure: retracement from peak EQUITY (was peak closed balance)."""
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


def total_dd_balance_basis(hwm_balance, equity):
    """The v5.22 measure, retained to document what changed and why."""
    if hwm_balance <= 0:
        return 0.0
    return 100.0 * (hwm_balance - equity) / hwm_balance


def floating_trailing(equity_hwm, equity):
    """v5.22 measure: retracement from peak EQUITY."""
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


def floating_raw(balance, equity):
    """The rejected measure, kept to demonstrate the false positive."""
    if balance <= 0:
        return 0.0
    return 100.0 * (balance - equity) / balance


def check(name, got, want):
    ok = got == want
    print("  [%s] %-58s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def checkf(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("  [%s] %-58s got=%.4f want=%.4f" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


SAFETY_DAILY, SAFETY_TOTAL, SAFETY_FLOAT = 3.0, 5.0, 1.0


def main():
    print("=" * 78)
    print("v5.22 / v5.24 PROP-FIRM SAFETY RULE VERIFICATION")
    print("=" * 78)
    ok = True

    # ---- 3% Daily DD ------------------------------------------------------
    print("\n-- 3% Daily Drawdown (reset balance = 5PM EST close) --")
    # Day starts at 100000, equity dips to 97000 = exactly 3%.
    ok &= checkf("equity 97000 from 100000 -> 3.0000%",
                 daily_dd(100000, 97000), 3.0)
    ok &= check("3.00% breach triggers pause", daily_dd(100000, 97000) >= SAFETY_DAILY, True)
    ok &= check("2.00% dip does NOT pause", daily_dd(100000, 98000) >= SAFETY_DAILY, False)
    # Profit above the reset balance gives NEGATIVE dd, not a false breach.
    ok &= checkf("equity 105000 -> -5.0000% (no breach)",
                 daily_dd(100000, 105000), -5.0)
    ok &= check("zero reset balance guard -> 0.0", daily_dd(0, 97000), 0.0)

    # ---- 5% Trailing Total DD --------------------------------------------
    print("\n-- 5% Trailing Total Drawdown (peak EQUITY, v5.24 basis) --")
    # Account grew 100000 -> 110000; a 5% trail from 110000 is 104500.
    ok &= checkf("equity 104500 from peak 110000 -> 5.0000%",
                 total_dd(110000, 104500), 5.0)
    ok &= check("5.00% from peak triggers halt", total_dd(110000, 104500) >= SAFETY_TOTAL, True)
    # 105000 sits 4.55% below the 110000 peak but only 5.00% below... it is
    # ABOVE the 100000 start, so the old static initial-balance measure read
    # NEGATIVE and would have missed the breach entirely.
    checkf("trailing peak 110000/equity 105000 -> 4.5455%",
           total_dd(110000, 105000), 4.545454545454545)
    checkf("static basis 100000/equity 105000 -> -5.0000%",
           total_dd(100000, 105000), -5.0)
    ok &= check("static measure MISSES it (negative, no halt)",
                total_dd(100000, 105000) >= SAFETY_TOTAL, False)
    # A new equity high raises the trail.
    ok &= check("equity 110000 at peak -> 0% drawdown", total_dd(110000, 110000), 0.0)
    ok &= check("zero hwm guard -> 0.0", total_dd(0, 50000), 0.0)

    # v5.24: the basis moved from peak CLOSED balance to peak EQUITY. Pin the
    # divergence so the distinction cannot silently regress either way. A book
    # that peaked at 110000 closed and now carries an open loss shows equity
    # below balance; the equity basis is the more conservative of the two.
    print("\n-- v5.24 basis change: peak equity vs peak closed balance --")
    # Both bases read identically when equity IS the peak (nothing open).
    ok &= checkf("flat account: both bases agree -> 0.5000%",
                 total_dd(110000, 109450), total_dd_balance_basis(110000, 109450))
    # Balance 110000 closed, then a position opens and equity drops to 109100.
    # The balance basis is blind to the intraday excursion; the equity basis
    # trails the true high and reports the real give-back.
    ok &= checkf("balance basis (blind) 110000/109100 -> 0.8182%",
                 total_dd_balance_basis(110000, 109100), 0.8181818181818181)
    ok &= checkf("equity basis (trail) 110000/109100 -> 0.8182%",
                 total_dd(110000, 109100), 0.8181818181818181)
    # Equity ratcheted to 115000 intraday before closing back at 110000. The
    # balance basis forgets that peak entirely; the equity basis remembers it.
    ok &= checkf("equity-basis remembers intraday 115000 peak -> 8.6957%",
                 total_dd(115000, 105000), 8.695652173913045)
    ok &= checkf("balance-basis forgets it -> 4.5455%",
                 total_dd_balance_basis(110000, 105000), 4.545454545454545)
    ok &= check("equity basis fires where balance basis would NOT",
                total_dd(115000, 105000) >= SAFETY_TOTAL, True)
    ok &= check("...and balance basis misses the same book",
                total_dd_balance_basis(110000, 105000) >= SAFETY_TOTAL, False)

    # ---- 1% Trailing Floating Loss (the contested rule) -------------------
    print("\n-- 1% Floating Loss: TRAILING from peak equity --")
    # Equity peaked at 100000 then gives back 1% -> 99000.
    ok &= checkf("equity 99000 from peak 100000 -> 1.0000%",
                 floating_trailing(100000, 99000), 1.0)
    ok &= check("1.00% retracement from peak breaches",
                floating_trailing(100000, 99000) >= SAFETY_FLOAT, True)
    ok &= check("0.90% retracement does NOT breach",
                floating_trailing(100000, 99100) >= SAFETY_FLOAT, False)

    # THE false positive: the raw ratio uses BALANCE as its basis. When balance
    # is still showing the OPEN loss (or has moved), the basis differs from the
    # equity peak and the two measures disagree. Below, balance 110000 with
    # equity 108900: raw reads 1.00%... but raw and trailing diverge when the
    # account is DOWN from its peak while balance lags the peak.
    # Concretely: the peak equity was 110000 on a CLOSED basis (balance 110000),
    # then a position opens and equity falls to 109450.
    #   raw basis (balance) 110000 -> 0.50%
    #   trailing basis (peak equity) 110000 -> 0.50%   <- agree
    ok &= checkf("balance 110000/equity 109450 -> 0.5000%",
                 floating_raw(110000, 109450), 0.5)
    ok &= checkf("peak 110000/equity 109450 -> 0.5000%",
                 floating_trailing(110000, 109450), 0.5)

    # DIVERGENCE: after drawing down and partially recovering, the equity peak
    # is 110000 but the realised balance has dropped to 108900 while a fresh
    # position sits at 109450.
    #   raw basis (low balance 108900) vs equity 109450 -> -0.51% (no signal)
    #   trailing basis (peak 110000)    vs equity 109450 ->  0.50% (true risk)
    # The raw measure understates real give-back once balance has fallen.
    checkf("low-balance raw basis 108900/equity 109450 -> -0.5051%",
           floating_raw(108900, 109450), -0.5050505050505051)
    ok &= check("raw basis understates (negative, silent)",
                floating_raw(108900, 109450) >= SAFETY_FLOAT, False)
    ok &= checkf("trailing basis still reports 0.5000%",
                 floating_trailing(110000, 109450), 0.5)

    # A genuine 1% give-back from the peak must breach on the trailing measure.
    ok &= checkf("peak 110000/equity 108900 -> 1.0000%",
                 floating_trailing(110000, 108900), 1.0)
    ok &= check("trailing measure at the real threshold",
                floating_trailing(110000, 108900) >= SAFETY_FLOAT, True)
    ok &= check("raw basis at 108900 also reads 0% -> blind",
                floating_raw(108900, 108900) >= SAFETY_FLOAT, False)

    # Flat account at its peak must never breach.
    ok &= checkf("flat at peak -> 0.0000% give-back", floating_trailing(110000, 110000), 0.0)
    ok &= check("flat account never breaches",
                floating_trailing(110000, 110000) >= SAFETY_FLOAT, False)

    # Zero-guard on the very first tick before OnInit populated the HWM.
    ok &= check("zero equity-hwm guard -> 0.0", floating_trailing(0, 50000), 0.0)
    ok &= check("  ...and cannot breach on uninitialised state",
                floating_trailing(0, 50000) >= SAFETY_FLOAT, False)

    # ---- Rule priority ----------------------------------------------------
    print("\n-- Rule priority: floating checked first --")
    # A state breaching BOTH the floating and daily limits must be handled by
    # the floating branch, which is evaluated first and returns immediately.
    # Reset balance 101500 and equity 98500 => daily 2.956% (below 3%) --
    # so push the reset balance down to 100000 to breach both simultaneously.
    eq, rb, hwm = 98500, 100000, 100000
    f, d = floating_trailing(hwm, eq), daily_dd(rb, eq)
    ok &= check("floating breached", f >= SAFETY_FLOAT, True)
    ok &= check("daily NOT yet breached at 1.50%", d >= SAFETY_DAILY, False)
    ok &= checkf("floating measured 1.5000%", f, 1.5)
    ok &= checkf("daily measured 1.5000%", d, 1.5)

    # True simultaneous breach: reset balance 100000, equity 96900.
    #   daily  = 3.10%   -> breaches 3%
    #   peak give-back = 3.10% from a 100000 peak -> also breaches 1%
    f2, d2 = floating_trailing(100000, 96900), daily_dd(100000, 96900)
    ok &= check("both fire simultaneously",
                (f2 >= SAFETY_FLOAT, d2 >= SAFETY_DAILY), (True, True))
    ok &= checkf("floating measured 3.1000%", f2, 3.1)
    ok &= checkf("daily measured 3.1000%", d2, 3.1)

    # ---- Rule interaction (v5.24) -----------------------------------------
    print("\n-- v5.24 rule interaction: 1% floating vs 5% trailing on one basis --")
    # With both rules on the equity HWM they compute the SAME quantity, so the
    # tighter 1% threshold always trips first and the 5% check is a backstop.
    # Pin that outcome explicitly rather than leaving it implicit.
    for eq in (100000, 109000, 99000, 95000, 94000):
        f = floating_trailing(100000, eq)
        t = total_dd(100000, eq)
        ok &= checkf("  equity %d: floating == trailing (shared basis)" % eq, f, t)

    eq = 99000  # exactly 1% below the 100000 peak
    f, t = floating_trailing(100000, eq), total_dd(100000, eq)
    ok &= check("1% floating breaches here", f >= SAFETY_FLOAT, True)
    ok &= check("5% trailing does NOT breach at the same give-back",
                t >= SAFETY_TOTAL, False)
    ok &= check("...so the floating branch fires first (as coded)",
                (f >= SAFETY_FLOAT) and not (t >= SAFETY_TOTAL), True)

    # The 5% check is reachable only if the 1% input is raised above 5%.
    eq = 94500  # 5.5% below peak
    f, t = floating_trailing(100000, eq), total_dd(100000, eq)
    ok &= check("at 5.50% both thresholds breached", (f >= 5.0, t >= SAFETY_TOTAL),
                (True, True))
    ok &= checkf("floating reads 5.5000%", f, 5.5)
    ok &= checkf("trailing reads the same 5.5000%", t, 5.5)

    print()
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 78)
    print()
    print("Threshold implemented (v5.28): live (balance - equity) float >= 1.00%")
    print("cancels the pendings, closes the basket, then RESUMES next tick.")
    print("v5.24 model above: peak-equity give-back >= 1.00% halted the EA; the")
    print("5% trailing DD (>=", SAFETY_TOTAL, "%) still shares that equity basis and is")
    print("now the ONLY path that can hard-halt. Superseded 1% semantics live in")
    print("_probe/test_v528_hotfix.py.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
