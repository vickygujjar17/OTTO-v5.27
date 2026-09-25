"""
Logic verification for the v5.21 fixes.

Covers the two changes a compile CANNOT validate:
  * SyncActiveTrade ghost-basket-remnant guard on PARTIAL stop-outs
  * LogClosedTrade history-lag behaviour (no all-zero exit record)

Re-implements the v5.21 decision order faithfully from the MQL5 source so
the branching can be tested deterministically without a broker book.
"""


def count_my_positions(book, magic, sym):
    """Mirrors CountMyPositions(): OPEN positions for this magic/symbol."""
    return sum(1 for p in book if p["magic"] == magic and p["symbol"] == sym)


def find_active_position(book, magic, sym):
    """v5.20 oldest-wins scan (unchanged in v5.21, kept for branch 4)."""
    out_ticket, out_dir, oldest = 0, None, 0
    for p in book:
        if p["magic"] != magic or p["symbol"] != sym:
            continue
        if p["ticket"] <= 0:
            continue
        if out_ticket == 0 or p["time"] < oldest:
            oldest, out_ticket, out_dir = p["time"], p["ticket"], p["type"]
    return (out_ticket, out_dir) if out_ticket > 0 else (0, None)


def is_tracked_ticket_open(state, book, magic, sym):
    if not state["has_active"] or state["ticket"] <= 0:
        return False
    for p in book:
        if p["ticket"] == state["ticket"]:
            return p["magic"] == magic and p["symbol"] == sym
    return False


def sync_active_trade_v521(state, book, magic, sym, log, basket_len):
    """Mirrors the v5.21 SyncActiveTrade ordering contract.

    basket_len is the LENGTH of m_basket[]. It is accepted but deliberately
    NOT used as the remnant discriminator -- that is the whole point of the
    fix, and a test below pins the behaviour by passing an inconsistent
    basket_len and asserting the outcome is driven by open positions.
    """
    # 1. incumbent still live -> do nothing
    if is_tracked_ticket_open(state, book, magic, sym):
        return "HOLD"

    # 2. tracked gone, but tranches still OPEN -> ghost remnant cleanup
    if state["has_active"] and count_my_positions(book, magic, sym) > 0:
        log.append("CLEANUP")
        state["has_active"], state["ticket"] = False, 0
        return "CLEANUP"

    # 3. nothing of ours open -> genuine flat transition
    t, d = find_active_position(book, magic, sym)
    if t == 0:
        if state["has_active"]:
            log.append("SEED_EXIT_LOG")
        state["has_active"], state["ticket"] = False, 0
        return "FLAT"

    # 4. cold start / flat adoption -> OLDEST
    state["has_active"], state["ticket"], state["dir"] = True, t, d
    return "ADOPT"


def log_closed_trade_v521(tranche_tickets, history, basket_len):
    """Mirrors the v5.21 LogClosedTrade money fields.

    history maps position_id -> closing deal dict. Returns None when NO
    record should be published (the v5.21 guard), else the aggregate.
    """
    if basket_len > 0:
        logged, gross, exit_px = 0, 0.0, 0.0
        for bt in tranche_tickets:
            deal = history.get(bt)
            if deal is None:
                continue
            if deal["entry"] != "OUT":
                continue
            if logged == 0:
                exit_px = deal["price"]
            gross += deal["profit"]
            logged += 1
        if logged > 0:
            return {"tranches": logged, "exit_price": exit_px, "gross": gross}
        # v5.21: no closing deals -> do NOT fall through and publish zeros.
        return None

    # Single-trade path
    if not tranche_tickets:
        return None
    deal = history.get(tranche_tickets[0])
    if deal is None or deal["entry"] != "OUT":
        return None
    if deal["price"] <= 0.0:            # v5.21 guard
        return None
    return {"tranches": 1, "exit_price": deal["price"], "gross": deal["profit"]}


def check(name, got, want):
    ok = got == want
    print("  [%s] %-60s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


MAGIC, SYM = 20240624, "EURUSD"


def P(ticket, time, typ="BUY", magic=None, sym=None):
    return {"ticket": ticket, "magic": magic or MAGIC, "symbol": sym or SYM,
            "time": time, "type": typ}


def main():
    print("=" * 80)
    print("v5.21 LOGIC VERIFICATION")
    print("=" * 80)
    ok = True

    # ---- Ghost basket remnant guard --------------------------------------
    print("\n-- SyncActiveTrade: partial stop-out ghost remnants --")
    # Primary T1 stopped out; scale-ins T2/T3 still open. Old behaviour
    # adopted T2 as a new primary (resetting entry/SL/lot from a scale-in
    # leg). New behaviour must close the survivors as remnants.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    surviv = [P(102, 200), P(103, 300)]
    ok &= check("primary gone + tranches open -> CLEANUP",
                sync_active_trade_v521(st, surviv, MAGIC, SYM, lg, 3), "CLEANUP")
    ok &= check("remnant cleanup writes exactly one log", lg, ["CLEANUP"])
    ok &= check("remnant never re-seeded as primary", st["has_active"], False)
    ok &= check("remnant state cleared, no ticket adopted", st["ticket"], 0)

    # THE FALSE-POSITIVE CASE that motivated counting OPEN positions rather
    # than basket-array length: primary AND a tranche both already closed,
    # but m_basket[] still holds 2 entries. Literal basket-count logic would
    # wrongly call this a remnant cleanup and mislabel the exit reason.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    ok &= check("both legs closed, basket[] len 2 -> FLAT not CLEANUP",
                sync_active_trade_v521(st, [], MAGIC, SYM, lg, 2), "FLAT")
    ok &= check("real exit reason preserved (not mislabelled)", lg, ["SEED_EXIT_LOG"])

    # Remnant guard must not disturb the v5.20 hijack guard.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    ok &= check("incumbent still open -> HOLD (v5.20 guard intact)",
                sync_active_trade_v521(st, [P(101, 100), P(102, 200)], MAGIC, SYM, lg, 2),
                "HOLD")
    ok &= check("HOLD writes no log", lg, [])

    # Surviving positions of ANOTHER magic/symbol are not our remnants.
    st = {"has_active": True, "ticket": 101, "dir": "BUY"}
    lg = []
    foreign = [P(102, 200, magic=999), P(103, 300, sym="GBPUSD")]
    ok &= check("foreign positions are not our remnants -> FLAT",
                sync_active_trade_v521(st, foreign, MAGIC, SYM, lg, 0), "FLAT")

    # Cold start mid-basket still adopts Tranche 1 (no tracked trade).
    st = {"has_active": False, "ticket": 0, "dir": None}
    lg = []
    ok &= check("cold start (untracked) still ADOPTS, no cleanup",
                sync_active_trade_v521(st, [P(101, 100), P(102, 200)], MAGIC, SYM, lg, 0),
                "ADOPT")
    ok &= check("cold start adopts oldest T1", st["ticket"], 101)

    # ---- History-lag exit logging ----------------------------------------
    print("\n-- LogClosedTrade: history latency must not publish zeros --")
    deals = {101: {"entry": "OUT", "price": 1.1050, "profit": 120.0},
             102: {"entry": "OUT", "price": 1.1055, "profit": 80.0}}

    ok &= check("deals present -> aggregated record",
                log_closed_trade_v521([101, 102], deals, 2),
                {"tranches": 2, "exit_price": 1.1050, "gross": 200.0})

    # THE REGRESSION: history not yet settled. v5.20 fell through and wrote
    # exitPrice=0, gross=0. v5.21 must return None (publish nothing).
    ok &= check("history empty -> publish NOTHING (no zero record)",
                log_closed_trade_v521([101, 102], {}, 2), None)
    ok &= check("partial history (1 of 2) -> still logs the real leg",
                log_closed_trade_v521([101, 102], {101: deals[101]}, 2),
                {"tranches": 1, "exit_price": 1.1050, "gross": 120.0})

    # Single-trade path: an entry-only deal in history is not an exit.
    ok &= check("entry-only deal -> nothing published",
                log_closed_trade_v521([101], {101: {"entry": "IN", "price": 1.10, "profit": 0.0}}, 0),
                None)
    ok &= check("single trade with zero exit price -> nothing published",
                log_closed_trade_v521([101], {101: {"entry": "OUT", "price": 0.0, "profit": 0.0}}, 0),
                None)
    ok &= check("single trade with valid exit -> published",
                log_closed_trade_v521([101], {101: {"entry": "OUT", "price": 1.1050, "profit": 120.0}}, 0),
                {"tranches": 1, "exit_price": 1.1050, "gross": 120.0})

    print()
    print("=" * 80)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 80)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
