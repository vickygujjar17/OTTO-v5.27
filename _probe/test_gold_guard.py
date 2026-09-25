"""
Verification for the v5.20 Gold deployment kill-switch.

MQL5 StringToUpper() mutates in place and returns bool, so the detection
must be modelled as: copy the symbol, uppercase the copy, then StringFind.
Expresses the guard as a truth table so symbol aliases and lookalikes are
both covered.
"""

XAU_ALIASES = ["XAUUSD", "XAUUSD.a", "XAUUSDm", "xauusd", "XAU/EUR", "GOLD", "GOLD.spot",
               "gold", "UKGOLD", "XAU", "XAUUSD_SB"]
NON_GOLD = ["EURUSD", "GBPUSD", "USDJPY", "AUDCAD", "US30", "NAS100", "SPX500",
            "GERMANY40", "SILVERXAGUSD", "BTCUSD"]
# Substring matches that are NOT gold but ARE refused. The directive
# specifies a contains-check, so these are accepted collateral. Listed
# explicitly so the behaviour is documented rather than a surprise.
COLLATERAL_REFUSALS = ["GOLDMINE_INDEX_HOUSE", "GOLDFISH_FUND"]


def gold_kill_switch(symbol):
    """Mirrors the otto.mq5 OnInit guard. True => refuse initialisation."""
    s = symbol.upper()
    return ("XAU" in s) or ("GOLD" in s)


def check(name, got, want):
    ok = got == want
    print("  [%s] %-58s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def main():
    print("=" * 78)
    print("v5.20 GOLD KILL-SWITCH VERIFICATION")
    print("=" * 78)
    ok = True

    print("\n-- Gold instruments must be REFUSED --")
    for s in XAU_ALIASES:
        ok &= check("refuse %s" % s, gold_kill_switch(s), True)

    print("\n-- Permitted instruments must PASS --")
    for s in NON_GOLD:
        ok &= check("permit %s" % s, gold_kill_switch(s), False)

    print("\n-- Documented collateral refusals (contains-match trade-off) --")
    for s in COLLATERAL_REFUSALS:
        ok &= check("refuse %s (not gold)" % s, gold_kill_switch(s), True)

    print()
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 78)
    print()
    print("NOTE: the directive specifies a substring contains-check, so any")
    print("symbol containing 'XAU' or 'GOLD' is refused. Collateral cases are")
    print("listed above. This errs toward refusing rather than trading, which")
    print("is the correct direction for a hard safety kill-switch.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
