"""
Logic verification for the v5.19 3-tier fill resolver.

Re-implements ResolveFilledPositionTicket's decision order faithfully so the
TIER SELECTION and the excludeTicket guard can be tested deterministically
without a broker. This is a model check of the branching, not of MT5 itself.
"""

TIER_NONE, TIER1, TIER2, TIER3 = 0, 1, 2, 3


def resolve(order_ticket, exclude_ticket, positions, history):
    """positions: {ticket: (magic, symbol)}
       history:   [(deal_order, entry, symbol, magic, position_id)]"""
    MAGIC, SYM = 20240624, "EURUSD"

    if order_ticket <= 0:
        return TIER_NONE, 0

    # TIER 1 -- also honors excludeTicket, mirroring the implementation
    if order_ticket != exclude_ticket and order_ticket in positions:
        magic, sym = positions[order_ticket]
        if magic == MAGIC and sym == SYM:
            return TIER1, order_ticket

    # TIER 2 -- newest first
    for deal_order, entry, sym, magic, pos_id in reversed(history):
        if deal_order != order_ticket:
            continue
        if entry != "IN":
            continue
        if sym != SYM or magic != MAGIC:
            continue
        if pos_id <= 0:
            continue
        if exclude_ticket > 0 and pos_id == exclude_ticket:
            continue
        if pos_id in positions and positions[pos_id][1] == SYM and positions[pos_id][0] == MAGIC:
            return TIER2, pos_id

    # TIER 3 -- newest position wins
    best, best_time = 0, -1
    for t, (magic, sym) in positions.items():
        if sym != SYM or magic != MAGIC:
            continue
        if exclude_ticket > 0 and t == exclude_ticket:
            continue
        if t >= best_time:
            best_time, best = t, t
    if best > 0:
        return TIER3, best

    return TIER_NONE, 0


def check(name, got, want):
    ok = got == want
    print("  [%s] %-58s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def main():
    print("=" * 78)
    print("3-TIER RESOLVER LOGIC VERIFICATION")
    print("=" * 78)
    ok = True
    P = {101: (20240624, "EURUSD"), 102: (20240624, "EURUSD")}
    H = [(9001, "IN", "EURUSD", 20240624, 101)]

    ok &= check("guard: orderTicket=0 rejected by all tiers",
                resolve(0, 0, P, H)[0], TIER_NONE)

    ok &= check("TIER 1: position id == order ticket",
                resolve(101, 0, P, H), (TIER1, 101))

    ok &= check("TIER 2: unknown order ticket resolved via history",
                resolve(9001, 0, P, H), (TIER2, 101))

    ok &= check("TIER 3: nothing else matches -> magic/symbol scan",
                resolve(99999, 0, P, H), (TIER3, 102))

    # TIER 1 must skip the tracked ticket. Position 102 is still live in
    # this fixture, so falling through to TIER 3 and returning 102 is the
    # CORRECT outcome -- the point is that 101 is never returned.
    t, got = resolve(101, 101, P, H)
    ok &= check("exclude guard: tracked pos barred at TIER 1 (101 not returned)",
                (t, got), (TIER3, 102))

    ok &= check("exclude guard: tracked pos barred at TIER 2",
                resolve(9001, 101, P, H), (TIER3, 102))

    # The headline scenario: a pyramid basket is live, and an OPPOSITE-side
    # limit fills creating position 102. We must resolve to 102, never 101.
    print()
    print("  -- SAR scenario: pyramid live at 101, opposite fill at 102 --")
    tier, t = resolve(9001, 101, P, [(9001, "IN", "EURUSD", 20240624, 102)])
    ok &= check("opposite fill resolves to NEW position, not incumbent",
                t, 102)

    # If the ONLY position is the tracked one, the resolver must report
    # nothing rather than echo the incumbent back as a fresh fill.
    only = {101: (20240624, "EURUSD")}
    ok &= check("no false positive when only incumbent exists",
                resolve(55555, 101, only, [])[0], TIER_NONE)

    print()
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
