"""
Logic verification for the v5.20 fixes.

Covers the two logic-level changes that a compile CANNOT validate:
  * SyncActiveTrade ordering contract (position hijacking)
  * FindActivePosition oldest-wins selection (cold-start Tranche 1 adoption)

Re-implements the decision order faithfully from the MQL5 source so the
branching can be tested deterministically without a broker book.
"""


def find_active_position(book, magic, sym, exclude_ticket=0):
    """Mirrors the v5.20 FindActivePosition: OLDEST opening position wins.
    book: list of dicts {ticket, magic, symbol, time, type} in broker order
    (MT5 appends newest last)."""
    out_ticket, out_dir, oldest = 0, None, 0
    for p in book:                      # forward scan
        if p["magic"] != magic or p["symbol"] != sym:
            continue
        pt = p["ticket"]
        if pt <= 0:
            continue
        if exclude_ticket > 0 and pt == exclude_ticket:
            continue
        opened = p["time"]
        if out_ticket == 0 or opened < oldest:
            oldest = opened
            out_ticket = pt
            out_dir = p["type"]
    return (out_ticket, out_dir) if out_ticket > 0 else (0, None)


def is_tracked_ticket_open(state, book, magic, sym):
    """Mirrors v5.20 IsTrackedTicketOpen()."""
    if not state["has_active"] or state["ticket"] <= 0:
        return False
    for p in book:
        if p["ticket"] == state["ticket"]:
            return p["magic"] == magic and p["symbol"] == sym
    return False


def sync_active_trade(state, book, magic, sym, log):
    """Mirrors the v5.20 SyncActiveTrade ordering contract exactly."""
    # 1. incumbent still live -> do nothing
    if is_tracked_ticket_open(state, book, magic, sym):
        return "HOLD"

    # 2. tracked gone -> flat?
    t, d = find_active_position(book, magic, sym)
    if t == 0:
        if state["has_active"]:
            log.append("SEED_EXIT_LOG")
        state["has_active"], state["ticket"] = False, 0
        return "FLAT"

    # 3. cold start / flat adoption -> OLDEST
    state["has_active"], state["ticket"], state["dir"] = True, t, d
    return "ADOPT"


def check(name, got, want):
    ok = got == want
    print("  [%s] %-62s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


MAGIC, SYM = 20240624, "EURUSD"


def P(ticket, time, typ="BUY"):
    return {"ticket": ticket, "magic": MAGIC, "symbol": SYM, "time": time, "type": typ}



def main():
    print("=" * 80)
    print("v5.20 LOGIC VERIFICATION")
    print("=" * 80)
    ok = True

    # ---- FindActivePosition: oldest wins ---------------------------------
    print("\n-- FindActivePosition: cold-start must adopt Tranche 1, not newest --")
    # T1 opened first (time 100), then scale-ins T2 (200) and T3 (300).
    book = [P(101, 100), P(102, 200), P(103, 300)]
    ok &= check("adopts OLDEST (T1=101) on cold start",
                find_active_position(book, MAGIC, SYM)[0], 101)

    # Same book reversed (broker order differs) must still pick 101 --
    # this is what makes the result independent of book ordering.
    ok &= check("order-independent: reversed book still yields 101",
                find_active_position(list(reversed(book)), MAGIC, SYM)[0], 101)

    # Nasty case: newest has the LOWEST ticket, oldest the HIGHEST. A
    # ticket-ordered scan would pick wrong; POSITION_TIME must decide.
    mixed = [P(900, 500), P(100, 50)]
    ok &= check("time (not ticket id) decides: picks 100 @ t=50",
                find_active_position(mixed, MAGIC, SYM)[0], 100)

    # excludeTicket must bar the oldest, then fall to the next oldest.
    ok &= check("excludeTicket bars T1 -> next oldest is 102",
                find_active_position(book, MAGIC, SYM, exclude_ticket=101)[0], 102)

    # Foreign magic/symbol ignored entirely.
    foreign = [{"ticket": 1, "magic": 999, "symbol": SYM, "time": 1, "type": "BUY"},
               {"ticket": 2, "magic": MAGIC, "symbol": "GBPUSD", "time": 1, "type": "BUY"}]
    ok &= check("foreign magic/symbol ignored -> none found",
                find_active_position(foreign, MAGIC, SYM), (0, None))

    # ---- SyncActiveTrade: the hijack scenario ----------------------------
    print("\n-- SyncActiveTrade: position hijacking guard --")
    # The headline bug: T1 tracked and still open; a scale-in T2 just
    # appeared. Old code re-seeded from the book; new code must HOLD.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    b2 = [P(101, 100), P(102, 200)]           # T2 freshly added
    ok &= check("incumbent open + new tranche appears -> HOLD (no hijack)",
                sync_active_trade(st, b2, MAGIC, SYM, lg), "HOLD")
    ok &= check("hijack guard leaves tracked ticket untouched", st["ticket"], 101)
    ok &= check("hijack guard writes NO exit log", lg, [])

    # Incumbent genuinely closed -> flat, and the closure is logged.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    ok &= check("incumbent gone + book empty -> FLAT",
                sync_active_trade(st, [], MAGIC, SYM, lg), "FLAT")
    ok &= check("flat transition logs the exit", lg, ["SEED_EXIT_LOG"])
    ok &= check("flat transition clears has_active", st["has_active"], False)

    # Cold start mid-basket: no tracked trade, basket live -> adopt T1.
    st = {"has_active": False, "ticket": 0, "dir": None}
    lg = []
    ok &= check("cold start mid-basket -> ADOPT oldest (101)",
                sync_active_trade(st, b2, MAGIC, SYM, lg), "ADOPT")
    ok &= check("cold start adopts T1 not T2", st["ticket"], 101)

    # Incumbent closed but a LATER tranche survives -> re-seed from the
    # remaining leg (book is not flat, so no exit log here either).
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    ok &= check("incumbent gone but tranche remains -> ADOPT",
                sync_active_trade(st, [P(102, 200)], MAGIC, SYM, lg), "ADOPT")
    ok &= check("surviving tranche becomes new primary", st["ticket"], 102)

    print()
    print("=" * 80)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 80)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
