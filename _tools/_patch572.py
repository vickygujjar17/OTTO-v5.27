"""
v5.72 source patch -- STRICT LIMIT-OR-NOTHING EXECUTION.

THE DEFECT. Since v5.37 the EA has carried two execution engines. A setup whose
front-edge entry price was legal for a pending order RESTED at the broker
(ROUTE A, physical); a setup whose price had already run through the entry was
held in memory as an SVirtualOrder and fired as a TRADE_ACTION_DEAL MARKET
order the moment price crossed the trigger (ROUTE B, virtual). v5.69 made ROUTE
A the ordinary case, which left ROUTE B as a fallback that only ever ran in the
one situation it must not: price had ALREADY gapped through the entry, i.e. the
setup's own premise -- "fill on the pullback back through the near edge" -- had
just been invalidated. The fallback therefore bought the worst available price
of the whole idea and reported it as a normal continuation fill.

THE FIX. The virtual engine is excised, not disabled. An entry now exists in
exactly one form: a resting broker limit. When the entry cannot legally rest,
or the venue refuses the resting send, the setup is VETOED with the new
VETO_LIMIT_MISSED and nothing is sent. Limit or nothing.

WHY A NEW VETO REASON. Reusing VETO_BROKEN would have been the cheap route and
is wrong: VETO_BROKEN means the ZONE failed (price closed back through the far
edge), while a missed limit means the zone was fine and the ENTRY was missed.
Cancelling a setup because price crashed through it is not the block breaking,
and the journal's "Block Broken" cancellation reason would have said so.

WHAT IS REMOVED (nothing unique is lost):

  * SVirtualOrder / m_virtualOrders[] / m_virtualCount
  * CheckVirtualTriggers() and its Update() hook
  * VirtualArmGvName() / ReleaseAllVirtualArmLocks() (the v5.49 cross-chart
    lock existed ONLY because a virtual arm was invisible to other charts; a
    resting broker limit is itself the shared artefact, so ROUTE A never
    needed the lock)
  * RemoveVirtualOrderAt() and all eight element-removal sites
  * the Book-2 half of IsAnyOrderLiveAtPrice(), the + m_virtualCount term in
    CountMyPendingOrders(), the virtual walks in CancelOrdersForInvalidBlocks
    / CancelOpposingConsensusOrders / ManageDirectionConflict, and the
    virtual-book empties in the constructor / clear paths.

... and SeedActiveTradeFromBlock() loses its overrideDir parameter: the MARKET
fire was its only caller, so the derived direction is now the only path.

STALE GLOBAL VARIABLES. Builds up to v5.71 published "OTTO_V_ARM_<sym>_<serial>"
from release builds. Nothing reads those names any more, so they cannot
suppress a setup; they simply linger in the terminal's GlobalVariable store
until the operator deletes them once. That is recorded in _commit572.txt rather
than papered over with a delete-on-init sweep, which would have needed the
whole naming convention back.

Every replacement is an asserted literal or an asserted marker pair, so a
drifted anchor aborts instead of silently patching the wrong region.
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOX_WIDTH = 75

# PATCH_DRY=1 reports every drifted anchor in one pass instead of aborting on
# the first, so a whole-file sweep of spacing/encoding drift takes one run
# rather than one run per anchor.
DRY = os.environ.get("PATCH_DRY") == "1"

MISSES = []


def load(name):
    """Read as LF-normalised text plus the file's own EOL."""
    p = os.path.join(ROOT, name)
    raw = open(p, "rb").read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    return text, eol


def save(name, text, eol):
    # In dry mode nothing is written: the run is a pure anchor audit.
    if DRY:
        return
    p = os.path.join(ROOT, name)
    data = text.replace("\r\n", "\n").replace("\n", eol)
    open(p, "wb").write(data.encode("utf-8", "surrogateescape"))


def _drift(label, got, want):
    if DRY:
        MISSES.append("%s: matched %d, expected %d" % (label, got, want))
        print("  MISS %s (matched %d, expected %d)" % (label, got, want))
        return True
    return False


def rep(text, old, new, count, label):
    found = text.count(old)
    if found != count:
        if _drift(label, found, count):
            return text
        raise SystemExit("ABORT %s: matched %d, expected %d"
                         % (label, found, count))
    print("  ok   %s" % label)
    return text.replace(old, new)


def swap(text, start, end, new, label):
    """Replace the INCLUSIVE span [start .. end] with `new`.

    Both markers must occur exactly once, and `end` must follow `start`. This is
    how whole doc boxes and whole functions move without pinning every row of
    them (rows carry em-dashes whose encoding must not become an anchor).
    """
    cs, ce = text.count(start), text.count(end)
    if cs != 1 or ce != 1:
        if DRY:
            MISSES.append("%s: start=%d end=%d, expected 1/1" % (label, cs, ce))
            print("  MISS %s (start=%d end=%d)" % (label, cs, ce))
            return text
        if cs != 1:
            raise SystemExit("ABORT %s: start marker matched %d, expected 1"
                             % (label, cs))
        raise SystemExit("ABORT %s: end marker matched %d, expected 1"
                         % (label, ce))
    i = text.find(start)
    j = text.find(end, i)
    if j < 0:
        if DRY:
            MISSES.append("%s: end marker precedes start" % label)
            return text
        raise SystemExit("ABORT %s: end marker precedes start" % label)
    print("  ok   %s" % label)
    return text[:i] + new + text[j + len(end):]


def box(lines):
    """Render the `//| ... |` documentation box at BOX_WIDTH.

    Asserts rather than trusting the caller: an over-wide row silently breaks
    the rule in every source file of this tree, and the box is the only place
    that rule is visible.
    """
    out = []
    for ln in lines:
        body = ("   //| " + ln) if ln else "   //|"
        width = len(body) + 1
        if width > BOX_WIDTH:
            raise SystemExit("ABORT box(): row is %d cols, limit %d: %r"
                             % (width, BOX_WIDTH, ln))
        out.append(body + " " * (BOX_WIDTH - 1 - len(body)) + "|")
    return "\n".join(out)


# ======================================================================
# 1. COttoOrderManager.mqh -- retire the virtual engine
# ======================================================================
print("COttoOrderManager.mqh")
om, om_eol = load("COttoOrderManager.mqh")

# --- 1a. The book itself: struct + arrays -----------------------------
OM_BOOK_OLD = """   // --- Virtual Order Engine (v5.37 Part 1) ---------------------------
   // Armed setups are held in memory and executed as MARKET orders when price
   // crosses the continuation level, bypassing broker pending-price rules.
   // v5.69 makes this the FALLBACK path: the TRUE FRONT-EDGE entry sits on the
   // NEAR side of the zone (below the market for a LONG, above it for a SHORT),
   // so a broker BUY_LIMIT / SELL_LIMIT can legitimately rest there and the
   // physical route carries the ordinary case. The virtual arm still exists for
   // the tick where price has already run through the entry.
   struct SVirtualOrder
     {
      int                  blockIndex;
      int                  blockSerial;
      ENUM_TRADE_DIRECTION direction;
      double               triggerPrice;
      double               stopLoss;
      double               lotSize;
      // v5.38: the per-setup journal session, minted when the setup is ARMED
      // and reused at the fire path. Carrying it on the descriptor (rather
      // than re-minting on each event) is what lets the arm snapshot, the
      // fill, and the exit all land in ONE journal file per setup.
      string               sessionId;
     };
   SVirtualOrder           m_virtualOrders[];
   int                     m_virtualCount;
"""

OM_BOOK_NEW = """   // --- Order execution: STRICT LIMIT-OR-NOTHING (v5.72) --------------
   // v5.37..v5.71 held armed setups in memory (SVirtualOrder) and fired them
   // as MARKET deals when price crossed the level. That engine is RETIRED, on
   // purpose and without a switch: every entry must be a resting broker limit
   // or nothing at all. An entry the broker cannot hold is a missed trade, and
   // chasing it at market is the one outcome that turns a missed pullback into
   // a fill at the far side of the move. There is therefore no in-memory order
   // book, no cross test and no per-setup execution state declared here.
"""

om = rep(om, OM_BOOK_OLD, OM_BOOK_NEW, 1, "virtual book (struct + arrays) retired")

# --- 1b. v5.49 cross-chart arm lock: the whole reason is gone ---------
OM_LOCK_TAIL = """      if(GlobalVariableCheck(tName)) GlobalVariableDel(tName);
     }
"""

OM_LOCK_END = "            GlobalVariableDel(VirtualArmGvName(m_virtualOrders[v].blockSerial));\n     }"

om = swap(om, OM_LOCK_TAIL, OM_LOCK_END, OM_LOCK_TAIL,
          "cross-chart virtual arm lock retired (2 funcs, 1 doc box)")

# --- 1c. SendOrderWithRetry: the comment named the virtual engine -----
OM_SEND_C_OLD = """         // v5.37 -- preserve a caller-supplied comment instead of
         // unconditionally overwriting it. The virtual engine now stamps an
         // R/C phase tag plus session ID on its request before calling here,
         // and OrderSend() reads this field on every retry of the loop, so the
         // guard must live inside the loop. Only a blank comment is defaulted."""

OM_SEND_C_NEW = """         // Preserve a caller-supplied comment instead of unconditionally
         // overwriting it. Every order path stamps its own session/phase tag
         // before calling here (v5.72: the resting limit is the ONLY entry
         // path), and OrderSend() reads this field on every retry of the
         // loop, so the guard lives INSIDE the loop. Blank is defaulted."""

om = rep(om, OM_SEND_C_OLD, OM_SEND_C_NEW, 1, "SendOrderWithRetry comment retargeted")

# --- 1d. PlaceOrArmOrder header: router -> strict limit ---------------
OM_HDR_NEW = box([
    "PlaceOrArmOrder - STRICT LIMIT ROUTER (v5.72). Translates Pine",
    "strategy.entry(limit=...) into a resting BROKER limit and",
    "nothing else. An entry that cannot legally rest is VETOED, never",
    "executed at market: limit or nothing. NO take-profit is attached",
    "(exact mirror: exits are purely trail-based; localTP is stored",
    "on the block only for the Front-Run veto). A fill is caught by",
    "CheckPendingOrderFills().",
]) + """
   // FIX (v5.57) FRONT-RUNNING ANTICIPATION: with InpEntryStyle = ENTRY_FRONT_EDGE
   // the entry sits on the NEAR side of the zone, 2.0*blockHeight + 1.5*ATR out
   // from the edge the arming close clears (v5.69: below the market for a LONG,
   // above it for a SHORT). That is a legal BUY_LIMIT / SELL_LIMIT, so the
   // PHYSICAL rest is the ordinary path - and, since v5.72, the only one."""

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| PlaceOrArmOrder",
          "   // is a MARKET deal when the level is crossed.",
          OM_HDR_NEW,
          "PlaceOrArmOrder doc box + FIX(v5.57) note retargeted")

# --- 1e. Clean ticket state comment -----------------------------------
OM_CLEAN_OLD = """      // Start from a clean ticket/pending state. The hybrid router decides
      // BELOW whether to set a resting broker ticket (physical) or leave it
      // 0 and register an in-memory SVirtualOrder (virtual)."""

OM_CLEAN_NEW = """      // Start from a clean ticket/pending state. The router below decides
      // whether the entry can legally REST; it never arms anything in memory,
      // so a block that leaves this function with ticket 0 has no order."""

om = rep(om, OM_CLEAN_OLD, OM_CLEAN_NEW, 1, "clean-state comment retargeted")

# --- 1f. The v5.40 "duplicate shield ... physical-vs-virtual" note ----
OM_DUP_OLD = """      // v5.40 — the duplicate shield and the physical-vs-virtual routing both
      // live at the TAIL of this function, AFTER the risk sizing, so the router
      // owns one coherent decision point. See "HYBRID ROUTER" below. The v5.28
      // live-price pre-flight is NOT re-added here: the router decides legality
      // itself (a legal price becomes a real broker limit, an illegal one arms
      // virtually), so it can never dispatch a resting order the server refuses."""

OM_DUP_NEW = """      // v5.72 -- the duplicate shield and the resting-price test both live at
      // the TAIL of this function, AFTER the risk sizing, so the router owns one
      // coherent decision point. See "STRICT LIMIT ROUTER" below. A price that
      // cannot rest is vetoed there and nothing is dispatched, so this function
      // can never send a resting order the server is bound to refuse."""

om = rep(om, OM_DUP_OLD, OM_DUP_NEW, 1, "duplicate-shield note retargeted")

# --- 1g. Journal mint note -------------------------------------------
OM_MINT_NEW = """      // --- Mint the per-setup journal session (v5.38, reused v5.40) -------
      // The setup gets ONE journal file the instant its limit is ACCEPTED by
      // the broker, so the arm snapshot and the later fill/exit append into
      // the same file instead of a re-minted session fragmenting the record.
      // The id is carried on the BLOCK and reused by the fill and cancel
      // paths, so ONE setup always maps to ONE journal file."""

om = swap(om,
          "      // --- Mint the per-setup journal session (v5.38, reused v5.40) -------",
          "      // cancel paths reuse the SAME id whichever route was taken.",
          OM_MINT_NEW,
          "journal-mint note retargeted")

# --- 1h. The router body note ----------------------------------------
OM_ROUTER_NEW = """      // STRICT LIMIT ROUTER (v5.72)
      // ------------------------------------------------------------------
      // There is exactly ONE way an entry can exist: a pending order resting at
      // the broker. A BUY_LIMIT must sit BELOW the market (ask) and a SELL_LIMIT
      // ABOVE it (bid), each clearing the broker's stops/freeze boundary. When
      // that test fails the setup is VETOED as VETO_LIMIT_MISSED and nothing is
      // sent -- v5.72 removed the in-memory fallback that used to fire a MARKET
      // deal on the cross.
      //
      // DEEP FRONT-EDGE NOTE (v5.69). With InpEntryStyle = ENTRY_FRONT_EDGE the
      // entry is measured out from the NEAR edge by 2.0*blockHeight + 1.5*ATR
      // and lands on the OPPOSITE side of the zone from the target: a Resistance
      // LONG at b.bottom-gap (= b.top - bh - 1.5*ATR), a Support SHORT at
      // b.top+gap (= b.bottom + bh + 1.5*ATR). At arm time the close has only
      // just cleared the NEAR edge, so a LONG entry sits BELOW the market and a
      // SHORT entry ABOVE it -- exactly the side a BUY_LIMIT / SELL_LIMIT must
      // sit on. The test below is therefore TRUE in the ordinary case and the
      // setup RESTS PHYSICALLY at the broker, filling on the pullback back
      // through the near edge.
      //
      // The SL sits a full slDist behind the entry, and because the target is
      // still anchored on the FAR edge the realized multiple is exactly
      // InpMaxRR + 3.0 (see CalcEntryPrice). The break veto is deliberately NOT
      // anchored on this price: it stays on the physical far edge so the pending
      // cannot be cancelled on the very close that armed it (v5.69 decoupling,
      // derived in ProcessBlocks)."""

om = swap(om,
          "      // HYBRID ROUTER (v5.40)",
          "      // decoupling, derived in ProcessBlocks).",
          OM_ROUTER_NEW,
          "HYBRID ROUTER note -> STRICT LIMIT ROUTER")

# --- 1i. Duplicate-shield comment ------------------------------------
OM_ADUP_NEW = """      // HARD ANTI-DUPLICATE: reject (latched) if an order already sits at
      // (near) this price. Closes the OnTick race and any bookkeeping
      // write-back lag between the send and the block write-back."""

om = swap(om,
          "      // HARD ANTI-DUPLICATE:",
          "      // bookkeeping write-back lag, independent of which route placed it.",
          OM_ADUP_NEW,
          "anti-duplicate comment retargeted")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 1 complete")


# --- 1j. ROUTE B: the refusal branch feeds a VETO, not a fallback -----
# The venue refused a RESTING send. v5.71 fell through to a virtual arm here,
# which meant a refused limit silently became a MARKET deal. v5.72 treats the
# refusal for what it is: the entry could not be held, so the setup is spent.
OM_REFUSE_OLD = """         // The venue refused a RESTING send. This is exactly the class the
         // router exists for (an off-grid / boundary price the server will not
         // rest), so FALL BACK to the virtual arm rather than dropping the
         // setup. Any other refusal falls back too — a virtual arm can still
         // fire as a MARKET deal, so it is strictly more likely to trade.
         if(EnableLogging)
            Print("[OrderManager] PHYSICAL SEND REFUSED (retcode=", result.retcode, " ",
                  GetTradeRetcodeString(result.retcode), ") — falling back to VIRTUAL arm");
         block.limitOrderTicket   = 0;
         block.pendingOrderCancel = false;
         m_blockManager.SetBlockAt(blockIndex, block);
         // fall through to the virtual arm below
        }
"""

OM_REFUSE_NEW = """         // The venue refused the RESTING send. v5.71 fell through to a virtual
         // arm here, i.e. a refused limit became a MARKET deal at whatever the
         // book offered. v5.72 has no such fallback: the limit could not be
         // held, so the entry is MISSED and the setup is retired with its own
         // reason. Journal the refusal, veto, and send nothing else.
         string refuseErr = GetTradeRetcodeString(result.retcode);
         if(EnableLogging)
            Print("[OrderManager] RESTING SEND REFUSED (retcode=", result.retcode, " ",
                  refuseErr, ") — entry missed, setup vetoed (limit-or-nothing)");
         if(m_journal != NULL)
           {
            // Same save/restore contract as the success path above: a transient
            // setup write must not steal the journal from a live basket.
            string prevRefuse = m_sessionID;
            m_journal.SetSessionID(armSessionId);
            m_journal.LogCancellation("Broker Refused Limit Order: " + refuseErr +
                                      " (Code: " + IntegerToString((long)result.retcode) + ")");
            if(prevRefuse != "") m_journal.SetSessionID(prevRefuse);
           }
         block.limitOrderTicket   = 0;
         block.pendingOrderCancel = false;
         block.isVetoed           = true;
         block.vetoReason         = VETO_LIMIT_MISSED;
         block.deleteOnBarTime    = iTime(m_symbol, PERIOD_CURRENT, 0);
         m_blockManager.SetBlockAt(blockIndex, block);
         return false;
        }

      // ---- ROUTE B RETIRED (v5.72): the entry cannot legally rest --------
      // priceValid was FALSE, so no BUY_LIMIT / SELL_LIMIT could sit where this
      // setup needs its fill. v5.37..v5.71 armed an in-memory SVirtualOrder and
      // fired a MARKET deal when price crossed the trigger -- i.e. it bought the
      // one print that invalidates the idea. It is gone: veto the setup, journal
      // the miss, and leave the book untouched. Limit or nothing.
      if(EnableLogging)
         Print("[OrderManager] LIMIT MISSED for block ", block.tradeId,
               " at price ", DoubleToString(entryPrice, _Digits),
               " (", (isLong ? "LONG needs BUY_LIMIT below " : "SHORT needs SELL_LIMIT above "),
               DoubleToString(isLong ? GetBid() : GetAsk(), _Digits),
               ") — price already through the entry, setup vetoed");
      if(m_journal != NULL)
        {
         string prevMiss = m_sessionID;
         m_journal.SetSessionID(armSessionId);
         m_journal.LogCancellation("Limit Entry Missed: price already through " +
                                   DoubleToString(entryPrice, _Digits));
         if(prevMiss != "") m_journal.SetSessionID(prevMiss);
        }
      block.limitOrderTicket   = 0;
      block.pendingOrderCancel = false;
      block.isVetoed           = true;
      block.vetoReason         = VETO_LIMIT_MISSED;
      block.deleteOnBarTime    = iTime(m_symbol, PERIOD_CURRENT, 0);
      m_blockManager.SetBlockAt(blockIndex, block);
      return false;
     }
"""

om = rep(om, OM_REFUSE_OLD, OM_REFUSE_NEW, 1,
         "refusal branch -> veto, ROUTE B -> VETO_LIMIT_MISSED")

# The whole virtual arm body (v5.49 shield + descriptor + arm journal) is now
# unreachable and must be DELETED, not left as dead code the compiler still
# type-checks against a struct that no longer exists.
om = swap(om,
          "      // ---- ROUTE B: VIRTUAL",
          "               \" session=\", armSessionId);\n      return true;\n     }",
          "",
          "ROUTE B body (shield, SVirtualOrder arm, journal, log) deleted")

# The deleted span carried its own leading/trailing blank lines, so collapse the
# run they leave behind into the two-blank separator this tree uses between
# members.
om = rep(om,
         "      return false;\n     }\n\n\n\n\n   //+------------------------------------------------------------------+\n   //| HARD ANTI-DUPLICATE",
         "      return false;\n     }\n\n   //+------------------------------------------------------------------+\n   //| HARD ANTI-DUPLICATE",
         1, "router tail: blank-line run collapsed")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 2 complete")


# --- 1k. The duplicate shield loses its second book -------------------
OM_SHIELD_NEW = box([
    "HARD ANTI-DUPLICATE (v5.72): scans the broker's LIVE pending-",
    "order pool for an order sitting at (near) targetPrice. With the",
    "in-memory book retired, a broker pending is the ONLY thing that",
    "can hold a price, so it is also the only duplicate source. This",
    "closes the OnTick race where a second tick re-places a setup",
    "before the first is latched.",
])

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| HARD ANTI-DUPLICATE (v5.40 hybrid)",
          "   bool                    IsAnyOrderLiveAtPrice(double targetPrice, double tolerancePoints = 5.0)",
          "   //+------------------------------------------------------------------+\n" + OM_SHIELD_NEW +
          "\n   bool                    IsAnyOrderLiveAtPrice(double targetPrice, double tolerancePoints = 5.0)",
          "IsAnyOrderLiveAtPrice doc box retargeted")

om = swap(om,
          "      // ---- Book 2: armed VIRTUAL orders (virtual route) ------------------",
          "         if(MathAbs(trigger - targetPrice) <= (tolerancePoints * unit))\n            return true; // Duplicate detected: armed virtual order already sits here\n        }\n",
          "",
          "duplicate shield Book 2 (virtual walk) deleted")

# --- 1l. CountMyPendingOrders: no in-memory term ---------------------
OM_COUNT_NEW = box([
    "Count of THIS chart's resting broker pending orders. v5.72: the",
    "in-memory virtual book is gone, so the broker pool is the whole",
    "answer. Kept under the old name so every caller (AdoptManual-",
    "Position()'s gate, the EA's CountMyPending() wrapper) reads the",
    "same total it always did.",
])

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| v5.40: the pending book is the HYBRID engine",
          "   int                     CountMyPendingOrders(void)",
          OM_COUNT_NEW + "\n   int                     CountMyPendingOrders(void)",
          "CountMyPendingOrders doc box retargeted")

om = rep(om, "      return count + m_virtualCount;",
         "      return count;", 1,
         "CountMyPendingOrders drops the virtual term")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 3 complete")


# --- 1m. RemoveVirtualOrderAt: nothing left to remove ----------------
om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| Removes a virtual order (v5.37 Part 2).",
          "      m_virtualCount--;\n      ArrayResize(m_virtualOrders, m_virtualCount, 3);\n     }\n",
          "",
          "RemoveVirtualOrderAt() deleted (8 call sites go with it)")

# --- 1n. SeedActiveTradeFromBlock loses overrideDir -----------------
OM_SEED_SIG_OLD = """   void                    SeedActiveTradeFromBlock(const SSniperBlock &block, ulong positionTicket,
                                                     ENUM_TRADE_DIRECTION overrideDir = DIR_NONE)"""

OM_SEED_SIG_NEW = """   void                    SeedActiveTradeFromBlock(const SSniperBlock &block, ulong positionTicket)"""

om = rep(om, OM_SEED_SIG_OLD, OM_SEED_SIG_NEW, 1,
         "seed signature: overrideDir parameter removed")

OM_SEED_NOTE_OLD = """      // The direction this basket is seeded with. v5.52 makes the default path
      // trivially correct: GetDirectionForBlock() is UNCONDITIONAL on the zone
      // type alone, so it resolves the SAME side the MARKET order executed no
      // matter what the fill changed on the block. overrideDir is kept as
      // belt-and-braces -- the virtual fire already knows the exact side it
      // sent, so passing it costs nothing and cannot desync the trail, the 1R
      // basis, the journal or the High Table mirror for the leg.
      m_activeTrade.direction    = (overrideDir != DIR_NONE) ? overrideDir
                                                             : GetDirectionForBlock(block);"""

OM_SEED_NOTE_NEW = """      // The direction this basket is seeded with. GetDirectionForBlock() is
      // UNCONDITIONAL on the zone type alone, so it resolves the SAME side the
      // resting limit was placed on. v5.72 removed the overrideDir parameter
      // with its only caller (the virtual MARKET fire): a limit fill is a
      // broker event on a block whose side never moves, so a derived side
      // cannot lag the fill.
      m_activeTrade.direction    = GetDirectionForBlock(block);"""

om = rep(om, OM_SEED_NOTE_OLD, OM_SEED_NOTE_NEW, 1,
         "seed direction: override plumbing removed")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 4 complete")


# --- 1o. Hourly report: walk the resting limits, not the memory book --
OM_HOURLY_OLD = """      // v5.51 Part 3 -- R/C PHASE TAG (hourly report). v5.52 made every armed
      // setup a CONTINUATION, so there is no phase left to discriminate on and
      // the report opens with one constant label. The per-serial walk is kept
      // because it proves each armed order still resolves to a live block.
      for(int v = 0; v < m_virtualCount; v++)
        {
         int vs   = m_virtualOrders[v].blockSerial;
         int bIdx = m_blockManager.FindBlockIndexBySerial(vs);
         SSniperBlock b;
         // Unresolved block -> a VISIBLE UNKNOWN, never a blank or a guess.
         string phaseLabel = "UNKNOWN";
         if(bIdx >= 0 && m_blockManager.GetBlockAt(bIdx, b))
            phaseLabel = "C (Continuation)";
         s += "    Setup #" + IntegerToString(vs) + " " +
              (m_virtualOrders[v].direction == DIR_LONG ? "LONG" : "SHORT") +
              " | Phase : " + phaseLabel + "\\n";
        }"""

OM_HOURLY_NEW = """      // v5.72 -- walk the RESTING LIMITS. With the in-memory book retired, a
      // live setup is exactly a block holding a broker ticket, so the report
      // reads that ticket instead of a descriptor. The v5.51/v5.52 property is
      // unchanged: every armed order must still resolve to a live block, and an
      // unresolved one degrades to a VISIBLE UNKNOWN rather than a blank.
      int hTotal = m_blockManager.GetBlockCount();
      for(int h = 0; h < hTotal; h++)
        {
         SSniperBlock b;
         if(!m_blockManager.GetBlockAt(h, b)) continue;
         if(b.limitOrderTicket <= 0) continue;
         // Unresolved block -> a VISIBLE UNKNOWN, never a blank or a guess.
         string phaseLabel = "UNKNOWN";
         if(!b.isVetoed && !b.isTriggered) phaseLabel = "C (Continuation)";
         s += "    Setup #" + IntegerToString(b.serial) + " " +
               (GetDirectionForBlock(b) == DIR_LONG ? "LONG" : "SHORT") +
               " | Phase : " + phaseLabel +
               " | Ticket: #" + IntegerToString((long)b.limitOrderTicket) + "\\n";
        }"""

om = rep(om, OM_HOURLY_OLD, OM_HOURLY_NEW, 1,
         "hourly report walks resting limits, not the virtual book")

om = rep(om, '      s += "  Open pendings   : " + IntegerToString(CountMyPendingOrders()) +\n           " (broker + virtual)\\n";',
         '      s += "  Open pendings   : " + IntegerToString(CountMyPendingOrders()) +\n           " (resting broker limits)\\n";',
         1, "hourly report pending label: broker limits only")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 5 complete")


# --- 1p. CountArmedBlocks doc box -----------------------------------
OM_ARMED_NEW = """   //| A block is ARMED when it has passed its gates and is waiting on   |
   //| price -- but NOT once it is vetoed, triggered or already retired. |
   //| This is the "how many live setups do I have" number. v5.72: a     |
   //| live setup is a block whose limit the broker ACCEPTED, so this    |
   //| counts armed blocks while CountMyPendingOrders() counts the       |
   //| resting broker rows they produced -- the two agree by             |
   //| construction, because every arm attempt now ends either in a      |
   //| resting ticket or in VETO_LIMIT_MISSED.                          |
   //+------------------------------------------------------------------+
"""

om = swap(om,
          "   //| A block is ARMED when it has passed its gates and is waiting on",
          "   int                     CountArmedBlocks(void)",
          OM_ARMED_NEW + "   int                     CountArmedBlocks(void)",
          "CountArmedBlocks doc box retargeted")

# --- 1q. Constructor / destructor: no book to seed or release --------
OM_CTOR_OLD = """      // v5.49: the book is about to be dropped wholesale, so release the
      // cross-chart arm locks it holds before the descriptors vanish.
      ReleaseAllVirtualArmLocks();
      m_virtualCount   = 0;
      ArrayResize(m_virtualOrders, 0);

     }

   // v5.49: true teardown. The book may still hold armed zones when the
   // instance is torn down (parameter change / recompile), so their
   // terminal-wide arm locks are released here. Left behind, a stale
   // \"OTTO_V_ARM_<sym>_<serial>\" would suppress the next session's block of
   // the same serial, because GVs outlive the EA and the serial counter
   // reseeds at 0 on re-init.
                    ~COttoOrderManager(void) { ReleaseAllVirtualArmLocks(); }"""

OM_CTOR_NEW = """      // v5.72: there is no in-memory book and no terminal-wide arm lock to
      // seed or release -- a resting broker limit IS the cross-chart artefact.
     }"""

om = rep(om, OM_CTOR_OLD, OM_CTOR_NEW, 1,
         "constructor tail + destructor retired")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 6 complete")


# --- 1r. Update(): no virtual trigger stage --------------------------
OM_UPD_NEW = """   //+------------------------------------------------------------------+
   //| Main update — hourly report + reversal completion + fill scan.    |
   //| v5.72: the virtual trigger stage is GONE with its engine, so a    |
   //| fill can only arrive as a broker event on a resting limit.        |
   //+------------------------------------------------------------------+
   void              Update(void)
     {
      // v5.48: FIRST, so the hourly report describes the state at the top of
      // the tick -- before CompleteReversal() and the fill scan mutate the
      // book underneath it. The gate is a timestamp comparison, so this costs
      // nothing on the ticks that are not due.
      EmitHourlyStatusReportIfDue();
      CompleteReversal();
      // v5.40: the entry rests a real broker limit, so a fill is a broker
      // event, not a memory trigger -- scan for it. Since v5.72 this is the
      // ONLY fill path: an entry that never rested was vetoed at arm time.
      CheckPendingOrderFills();
      // v5.33: LAST, so an EA fill detected in this same tick becomes the
      // primary first and AdoptManualPosition()'s one-basket-at-a-time gate
      // then refuses. Called every tick; the gates above are pure reads and
      // the sweep is O(PositionsTotal), so this is cheap on a flat book.
      AdoptManualPosition();
     }
"""

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| Main update ",
          "      AdoptManualPosition();\n     }\n",
          OM_UPD_NEW,
          "Update(): CheckVirtualTriggers stage removed")

# --- 1s. PlaceOrdersForArmedBlocks doc + body note -----------------
OM_PLACE_NEW = """   //+------------------------------------------------------------------+
   //| PINE ORDER PLACEMENT — for every armed, non-vetoed, non-         |
   //| triggered, not-yet-placed block, apply the Pine gates and route   |
   //| it through the STRICT LIMIT router: a resting broker limit, or a  |
   //| VETO_LIMIT_MISSED when the price cannot legally rest. Called from |
   //| OnTick (hasPlacedOrder prevents duplicates).                      |
   //+------------------------------------------------------------------+"""

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| PINE ORDER PLACEMENT",
          "   void              PlaceOrdersForArmedBlocks(void)",
          OM_PLACE_NEW + "\n   void              PlaceOrdersForArmedBlocks(void)",
          "PlaceOrdersForArmedBlocks doc box retargeted")

OM_PLACE_NOTE_OLD = """         // v5.52: EVERY armed block routes through PlaceOrArmOrder(), which
         // picks PHYSICAL (broker limit) vs VIRTUAL (CheckVirtualTriggers fires
         // the MARKET leg on the cross) per setup by live-price validity. With
         // the two-phase engine gone there is no Phase 2 to shift, so this is
         // the single, phase-free execution path for every armed block."""

OM_PLACE_NOTE_NEW = """         // v5.72: EVERY armed block routes through PlaceOrArmOrder(), which
         // either RESTS a broker limit or vetoes the setup as VETO_LIMIT_MISSED.
         // With the two-phase engine gone there is no Phase 2 to shift either,
         // so this is the single, phase-free execution path for every block."""

om = rep(om, OM_PLACE_NOTE_OLD, OM_PLACE_NOTE_NEW, 1,
         "PlaceOrdersForArmedBlocks body note retargeted")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 7 complete")


# --- 1t. CheckVirtualTriggers(): the whole engine, deleted -----------
om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| v5.37 Part 2 - VIRTUAL MARKET TRIGGER.",
          "                  \" rrUnit=\", DoubleToString(slDist, _Digits));\n        }\n     }\n",
          "",
          "CheckVirtualTriggers() body deleted (its fill logic survives in "
          "CheckPendingOrderFills)")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 8 complete")


# --- 1u. CheckPendingOrderFills comments that named the virtual fire --
OM_FILLS_NEW = """   //+------------------------------------------------------------------+
   //| v5.40 — PHYSICAL FILL SCANNER.                                   |
   //| The entry rests a real broker limit order. When one fills, the    |
   //| broker removes the pending and opens a position whose ticket is   |
   //| UNRELATED, so resolve it via the 3-tier detector, seed the        |
   //| basket, and consume the block's single continuation entry. A      |
   //| pending that VANISHED without a position is a cancel/expiry —    |
   //| just release the ticket. Since v5.72 this is the ONLY fill path.  |
   //+------------------------------------------------------------------+"""

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| v5.40 ",
          "   void              CheckPendingOrderFills(void)",
          OM_FILLS_NEW + "\n   void              CheckPendingOrderFills(void)",
          "CheckPendingOrderFills doc box retargeted")

om = rep(om,
         "            // the incumbent it is meant to add to. Mirrors the virtual trigger's\n"
         "            // by-construction guard (m_activeDirection != newDir).",
         "            // the incumbent it is meant to add to. The direction clause is the\n"
         "            // whole guard: same side = scale-in, opposite side = reversal.",
         1, "physical SAR guard comment retargeted")

om = rep(om,
         "                  // stops PlaceOrArmOrder and the virtual engine from arming\n"
         "                  // a second tranche; hasPlacedOrder stops the duplicate;",
         "                  // stops PlaceOrArmOrder from arming a second tranche once\n"
         "                  // this one has filled; hasPlacedOrder stops the duplicate;",
         1, "retire note: virtual engine reference removed")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 9 complete")


# --- 1v. CancelOrdersForInvalidBlocks: doc box + virtual walk --------
OM_CANCEL_NEW = """   //+------------------------------------------------------------------+
   //| ROBUST CLEANUP — deletes the resting broker limit of any block    |
   //| that is vetoed, triggered, flipped, deleted, or flagged for       |
   //| cancellation. Runs before/after the block funnel so no abandoned  |
   //| order is ever left live, and so the block array can release the   |
   //| struct safely. v5.72: a broker pending is the ONLY order artefact |
   //| in the system, so this walk IS the whole cleanup.                 |
   //+------------------------------------------------------------------+"""

om = swap(om,
          "   //+------------------------------------------------------------------+\n   //| ROBUST CLEANUP",
          "   void              CancelOrdersForInvalidBlocks(void)",
          OM_CANCEL_NEW + "\n   void              CancelOrdersForInvalidBlocks(void)",
          "CancelOrdersForInvalidBlocks doc box retargeted")

# The physical cancelReason chain gains the new reason.
om = rep(om,
         '                  else if(blocks[i].vetoReason == VETO_CORRELATION)   cancelReason = "Vector Consensus Veto";\n'
         '                  else if(blocks[i].pendingOrderCancel)               cancelReason = "Manual / Direction Conflict";',
         '                  else if(blocks[i].vetoReason == VETO_CORRELATION)   cancelReason = "Vector Consensus Veto";\n'
         '                  else if(blocks[i].vetoReason == VETO_LIMIT_MISSED)  cancelReason = "Limit Entry Missed (price through entry)";\n'
         '                  else if(blocks[i].pendingOrderCancel)               cancelReason = "Manual / Direction Conflict";',
         1, "physical cancelReason chain: VETO_LIMIT_MISSED row added")

# The virtual half of the sweep: from the "Drop the armed virtual order" note
# through its RemoveVirtualOrderAt + break. The block's arm latch release below
# stays -- it is the physical ticket clear.
om = swap(om,
          "         // Drop the armed virtual order (if any) that belongs to this block.",
          "            RemoveVirtualOrderAt(v);\n            break;   // at most one virtual order per block\n           }\n",
          "",
          "CancelOrdersForInvalidBlocks: virtual walk + journal block deleted")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 10 complete")


# --- 1w. CancelOpposingConsensusOrders: virtual walk removed ---------
OM_CONSENSUS_OLD = """      // v5.37 Part 3: walk our own in-memory virtual book (reverse, so
      // RemoveVirtualOrderAt()'s compaction stays index-safe). Scoping this
      // to m_virtualOrders[] is what the v5.28 note below always wanted:
      // each chart now prunes only ITS OWN armed setups -- no shared broker
      // book, no cancel storm across the portfolio.
      for(int v = m_virtualCount - 1; v >= 0; v--)
        {
         SVirtualOrder vo = m_virtualOrders[v];
         if(vo.direction == DIR_NONE) continue;

         string sym = m_symbol;
         if(!m_correlationFilter.IsConsensusOpposed(sym, vo.direction)) continue;

         if(EnableLogging)
            Print("[OrderManager] VECTOR CANCEL: serial ", vo.blockSerial, " ", sym,
                  (vo.direction == DIR_LONG ? " LONG" : " SHORT"),
                  " | consensus = ",
                  DoubleToString(m_correlationFilter.GetPairConsensus(sym), 1), "%");

         // Release any block still latched to this serial so the array can
         // free the struct and the duplicate shield stays consistent.
         int bi = m_blockManager.FindBlockIndexBySerial(vo.blockSerial);
         if(bi >= 0)
           {
            SSniperBlock mod;
            if(m_blockManager.GetBlockAt(bi, mod))
              {
               mod.limitOrderTicket = 0;
               mod.pendingOrderCancel = false;
               m_blockManager.SetBlockAt(bi, mod);
              }
           }

         RemoveVirtualOrderAt(v);
        }
"""

om = rep(om, OM_CONSENSUS_OLD, "", 1,
         "CancelOpposingConsensusOrders: virtual walk deleted")

# --- 1x. ManageDirectionConflict: virtual walk removed ---------------
OM_DIRCONF_OLD = """      // v5.37 Part 3: drop armed virtual orders that fight the live trade's
      // direction. Reverse walk keeps RemoveVirtualOrderAt() index-safe.
      for(int v = m_virtualCount - 1; v >= 0; v--)
        {
         if(m_virtualOrders[v].direction != m_activeDirection)
            continue;
         if(EnableLogging)
            Print("[OrderManager] DIRECTION CONFLICT: dropping virtual order ",
                  m_virtualOrders[v].blockSerial, " (dir ", m_activeDirection, ")");
         RemoveVirtualOrderAt(v);
        }
"""

om = rep(om, OM_DIRCONF_OLD, "", 1,
         "ManageDirectionConflict: virtual walk deleted")

# --- 1y. CancelAllPendingOrders: no in-memory book to empty ----------
OM_CANCELALL_OLD = """      // v5.40: the book is the HYBRID engine — delete every resting BROKER
      // pending this chart owns, then empty the in-memory virtual book."""

OM_CANCELALL_NEW = """      // v5.72: a resting BROKER pending is the only order artefact this chart
      // can own, so deleting those IS emptying the book."""

om = rep(om, OM_CANCELALL_OLD, OM_CANCELALL_NEW, 1,
         "CancelAllPendingOrders comment retargeted")

om = rep(om,
         "      // v5.49: release the cross-chart arm locks this chart published before\n"
         "      // the in-memory book is emptied (deinit / DD halt path).\n"
         "      ReleaseAllVirtualArmLocks();\n"
         "      ArrayResize(m_virtualOrders, 0, 3);\n"
         "      m_virtualCount = 0;\n",
         "", 1,
         "CancelAllPendingOrders: virtual-book empty deleted")

# --- 1z. ClearBasket: no arm locks to release ------------------------
om = rep(om,
         "      // v5.49: a closed basket takes its unconsumed arm locks with it.\n"
         "      ReleaseAllVirtualArmLocks();\n"
         "      ArrayResize(m_virtualOrders, 0, 3);\n"
         "      m_virtualCount = 0;\n",
         "", 1,
         "ClearBasket: virtual-book empty deleted")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 11 complete")


# --- 1aa. Remaining doc boxes that described the dual route ----------
om = rep(om,
         "   //| v5.40: LIVE again. The hybrid router consumes this buffer to      |\n"
         "   //| decide whether an entry may REST at the broker (physical route)   |\n"
         "   //| or must arm virtually instead.                                    |",
         "   //| v5.72: LIVE, and it is the whole routing test. The strict limit   |\n"
         "   //| router consumes this buffer to decide whether the entry may REST  |\n"
         "   //| at the broker; when it may not, the setup is vetoed and no order  |\n"
         "   //| is sent.                                                          |",
         1, "GetPriceBoundaryBuffer doc box retargeted")

om = rep(om,
         "   //| SnapToTick — round a price onto the symbol's trade tick grid.    |\n"
         "   //| A price that is off-grid is refused by the server with           |\n"
         "   //| TRADE_RETCODE_INVALID_PRICE even when it sits on the correct     |\n"
         "   //| side of the market. Shared by the PlaceOrArmOrder pre-flight     |\n"
         "   //| snap and the SendOrderWithRetry re-quote path so the two grids   |\n"
         "   //| can never drift apart.                                           |",
         "   //| SnapToTick — round a price onto the symbol's trade tick grid.    |\n"
         "   //| A price that is off-grid is refused by the server with           |\n"
         "   //| TRADE_RETCODE_INVALID_PRICE even when it sits on the correct     |\n"
         "   //| side of the market. Shared by the PlaceOrArmOrder entry snap     |\n"
         "   //| and the SendOrderWithRetry re-quote path so the two grids can    |\n"
         "   //| never drift apart.                                               |",
         1, "SnapToTick doc box retargeted")

save("COttoOrderManager.mqh", om, om_eol)
print("COttoOrderManager.mqh part 12 complete")


# --- 1ab. Two stragglers that named the retired engine ----------------
om = rep(om,
         "            // FIX (v5.60) de-duplicated onto the ONE stop-distance definition.\n"
         "            // Every other entry path (PlaceOrArmOrder, CheckVirtualTriggers)\n"
         "            // already calls CalcSLDistance(); only this SAR fallback re-spelled",
         "            // FIX (v5.60) de-duplicated onto the ONE stop-distance definition.\n"
         "            // Every other entry path (PlaceOrArmOrder) already calls\n"
         "            // CalcSLDistance(); only this SAR fallback re-spelled",
         1, "SAR fallback note: virtual path reference removed")

om = rep(om,
         "        // whole lifecycle resolves against (see the SAR locks in\n"
         "        // CheckPendingOrderFills / CheckVirtualTriggers). Reuse it verbatim\n"
         "        // when it is already resolved -- an EA block path hands over the\n"
         "        // arm-time session, a virtual fill hands over its pre-minted id, a",
         "        // whole lifecycle resolves against (see the SAR lock in\n"
         "        // CheckPendingOrderFills). Reuse it verbatim when it is already\n"
         "        // resolved -- an EA block path hands over the arm-time session, a",
         1, "InitBasket session note: virtual reference removed")

save("COttoOrderManager.mqh", om, om_eol)

# --- 1ac. Residual census: no retired IDENTIFIER may survive --------
# Scoped to CODE, not prose: a comment explaining what was removed and why
# legitimately names the engine, while a declaration, a call or a field
# carrying the old engine's name would mean the excision was incomplete -- and
# the compiler would carry a phantom second execution path.
def strip_comments(code):
    """Drop // comments, respecting string literals."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


om_code = strip_comments(om)

for tok in ("SVirtualOrder", "m_virtualOrders", "m_virtualCount",
            "RemoveVirtualOrderAt", "ReleaseAllVirtualArmLocks",
            "VirtualArmGvName", "CheckVirtualTriggers", "overrideDir",
            "vo.triggerPrice", "vo.stopLoss", "vo.lotSize",
            "vo.sessionId", "vo.direction", "vo.blockSerial"):
    if tok in om_code:
        raise SystemExit("ABORT: retired token %r still present in "
                         "COttoOrderManager.mqh code" % tok)
print("  ok   retired identifiers all absent from the code")

for need in ("VETO_LIMIT_MISSED",
             'LogCancellation("Broker Refused Limit Order: "',
             'LogCancellation("Limit Entry Missed: price already through "',
             "      return count;",
             "m_activeTrade.direction    = GetDirectionForBlock(block);"):
    if need not in om:
        raise SystemExit("ABORT: expected %r missing from COttoOrderManager.mqh" % need)
print("  ok   new limit-only contract present")

print("COttoOrderManager.mqh complete")


# ======================================================================
# 2. OttoDefines.mqh -- the new veto reason + two stale notes
# ======================================================================
print("OttoDefines.mqh")
df, df_eol = load("OttoDefines.mqh")

DF_ENUM_OLD = """   VETO_CORRELATION         // Opposes the 8-currency vector consensus
  };"""

DF_ENUM_NEW = """   VETO_CORRELATION,        // Opposes the 8-currency vector consensus
   // v5.72 STRICT LIMIT-OR-NOTHING: the entry could not be placed as a resting
   // broker limit -- either price had already run through it (no legal
   // BUY_LIMIT / SELL_LIMIT side) or the venue refused the send. Deliberately
   // NOT VETO_BROKEN: that one says the ZONE failed (price closed back through
   // the far edge), while here the zone is intact and the ENTRY was missed.
   VETO_LIMIT_MISSED        // Limit entry missed (price through it / send refused)
  };"""

df = rep(df, DF_ENUM_OLD, DF_ENUM_NEW, 1,
         "ENUM_VETO_REASON: VETO_LIMIT_MISSED appended")

DF_ENTRY_OLD = """                            //   BUY_LIMIT / SELL_LIMIT, so the order RESTS AT
                            //   THE BROKER and fills on the pullback. That is
                            //   the ordinary route; the virtual MARKET arm is
                            //   the fallback (see CheckVirtualTriggers)."""

DF_ENTRY_NEW = """                            //   BUY_LIMIT / SELL_LIMIT, so the order RESTS AT
                            //   THE BROKER and fills on the pullback. That is
                            //   the ONLY route (v5.72): an entry that cannot
                            //   rest is vetoed VETO_LIMIT_MISSED, never chased
                            //   at market."""

df = rep(df, DF_ENTRY_OLD, DF_ENTRY_NEW, 1,
         "ENTRY_FRONT_EDGE doc: fallback claim retired")

DF_SESS_OLD = """   // v5.40: the per-setup journal session, minted when the setup is ARMED (a
   // physical broker limit OR an in-memory virtual arm) and reused at the fill,
   // cancel and conversion paths so ONE setup maps to ONE journal file. Empty"""

DF_SESS_NEW = """   // v5.40: the per-setup journal session, minted when the setup's resting
   // broker limit is ACCEPTED, and reused at the fill and cancel paths so ONE
   // setup maps to ONE journal file. Empty"""

df = rep(df, DF_SESS_OLD, DF_SESS_NEW, 1,
         "SSniperBlock.sessionId note retargeted")

save("OttoDefines.mqh", df, df_eol)
print("OttoDefines.mqh complete")


# ======================================================================
# 3. COttoJournal.mqh -- LogSetupArmed has one execution mode now
# ======================================================================
print("COttoJournal.mqh")
jrn, jrn_eol = load("COttoJournal.mqh")

JRN_DOC_OLD = """   //| LOG SETUP ARMED - create file + SUBJECT line + setup details.   |
   //| v5.40: the hybrid router arms a setup EITHER as a physical      |
   //| broker pending limit (price valid for a resting order) OR as an |
   //| in-memory virtual order (price already through the zone). This  |
   //| CREATES the per-setup journal either way and stamps the two     |
   //| facts the operator needs on the header: the Logic and the       |
   //| Execution Mode (PHYSICAL vs VIRTUAL). v5.52: every setup is a   |
   //| single CONTINUATION entry, so the header no longer names a      |
   //| phase. The later entry/cancel/exit append into the same file;   |
   //| the email ships the full snapshot.                              |"""

JRN_DOC_NEW = """   //| LOG SETUP ARMED - create file + SUBJECT line + setup details.   |
   //| A setup exists only once its RESTING BROKER LIMIT is accepted,  |
   //| so there is exactly one execution mode to stamp (v5.72 removed  |
   //| the in-memory arm that used to be the second). This CREATES the |
   //| per-setup journal and records the Logic and the Execution Mode  |
   //| on the header. v5.52: every setup is a single CONTINUATION      |
   //| entry, so the header no longer names a phase. The later entry/  |
   //| cancel/exit append into the same file; the email ships the full |
   //| snapshot.                                                       |"""

jrn = rep(jrn, JRN_DOC_OLD, JRN_DOC_NEW, 1, "LogSetupArmed doc box retargeted")

JRN_SIG_OLD = "   void              LogSetupArmed(bool physical, string tradeId, ENUM_TRADE_DIRECTION dir, ENUM_BLOCK_TYPE btype, double entryPrice, double slPrice, double lotSize, const SSniperBlock &blk)"
JRN_SIG_NEW = "   void              LogSetupArmed(string tradeId, ENUM_TRADE_DIRECTION dir, ENUM_BLOCK_TYPE btype, double entryPrice, double slPrice, double lotSize, const SSniperBlock &blk)"

jrn = rep(jrn, JRN_SIG_OLD, JRN_SIG_NEW, 1,
         "LogSetupArmed signature: physical flag removed")

JRN_MODE_OLD = """      string execMode = physical ? "PHYSICAL (Broker Limit Order)" : "VIRTUAL (In-Memory Arm)";"""
JRN_MODE_NEW = """      // v5.72: constant. A setup reaches this writer only after the broker
      // accepted its resting limit, so there is no second copy to print.
      string execMode = "PHYSICAL (Broker Limit Order)";"""

jrn = rep(jrn, JRN_MODE_OLD, JRN_MODE_NEW, 1,
         "LogSetupArmed execMode: single mode")

save("COttoJournal.mqh", jrn, jrn_eol)
print("COttoJournal.mqh complete")


# ======================================================================
# 4. COttoOrderManager.mqh -- the arm-journal CALL loses its flag
# ======================================================================
# Kept in its own block so the reader can see the signature change and its one
# call site move together.
print("COttoOrderManager.mqh (arm-journal call)")
om, om_eol = load("COttoOrderManager.mqh")

om = rep(om,
         "               m_journal.LogSetupArmed(true, block.tradeId, dir, block.type,\n"
         "                                       entryPrice, adjustedSL, lotSize, block);",
         "               m_journal.LogSetupArmed(block.tradeId, dir, block.type,\n"
         "                                       entryPrice, adjustedSL, lotSize, block);",
         1, "arm-journal call: physical flag dropped")

if om.count("LogSetupArmed(") != 1:
    raise SystemExit("ABORT: expected exactly one LogSetupArmed call site, found %d"
                     % om.count("LogSetupArmed("))
print("  ok   exactly one LogSetupArmed call site")

save("COttoOrderManager.mqh", om, om_eol)


# ======================================================================
# 5. COttoBlockManager.mqh -- VetoReasonString row + one stale link
# ======================================================================
print("COttoBlockManager.mqh")
bm, bm_eol = load("COttoBlockManager.mqh")

bm = rep(bm,
         "         case VETO_BROKEN:        return \"BROKEN\";\n"
         "         case VETO_FLIPPED:       return \"FLIPPED\";\n"
         "         default:                 return \"NONE\";",
         "         case VETO_BROKEN:        return \"BROKEN\";\n"
         "         case VETO_FLIPPED:       return \"FLIPPED\";\n"
         "         case VETO_CORRELATION:   return \"CORRELATION\";\n"
         "         case VETO_LIMIT_MISSED:  return \"LIMIT-MISSED\";\n"
         "         default:                 return \"NONE\";",
         1, "VetoReasonString: CORRELATION + LIMIT-MISSED rows")

bm = rep(bm,
         "   //| v5.37 Part 3: the virtual-order engine keys its book by serial,  |\n"
         "   //| not by array index, because RemoveBlock() compacts m_blocks[] and |\n"
         "   //| a stored index would dangle. This is the serial twin of          |\n"
         "   //| FindBlockIndexByTicket() (which can no longer be used once no    |\n"
         "   //| broker ticket exists to key on).                                  |",
         "   //| Serial lookup, not array index: RemoveBlock() compacts m_blocks[] |\n"
         "   //| and a stored index would dangle. This is the serial twin of       |\n"
         "   //| FindBlockIndexByTicket(), which every ticket-keyed path still      |\n"
         "   //| uses (v5.72: the resting broker limit is the only order artefact, |\n"
         "   //| so the ticket lookup is the primary one).                         |",
         1, "FindBlockIndexBySerial doc box retargeted")

save("COttoBlockManager.mqh", bm, bm_eol)
print("COttoBlockManager.mqh complete")


# ======================================================================
# 6. otto.mq5 -- the STEP 4 comment claimed a hybrid engine
# ======================================================================
print("otto.mq5")
main, main_eol = load("otto.mq5")

main = rep(main,
           "      // Every armed block routes through the single hybrid engine:\n"
           "      // PlaceOrdersForArmedBlocks() ARMS the order -- a resting broker limit\n"
           "      // when the price permits one, else an in-memory virtual order -- and the\n"
           "      // order manager's CheckVirtualTriggers() FIRES the MARKET leg once the\n"
           "      // continuation level trades through. v5.52 has no per-phase split.",
           "      // Every armed block routes through the single LIMIT engine:\n"
           "      // PlaceOrdersForArmedBlocks() either RESTS a broker limit at the deep\n"
           "      // front-edge entry or vetoes the setup as VETO_LIMIT_MISSED. v5.72\n"
           "      // retired the in-memory arm: an entry the broker cannot hold is a\n"
           "      // missed trade, never a MARKET chase. v5.52 has no per-phase split.",
           1, "STEP 4 comment retargeted")

save("otto.mq5", main, main_eol)
print("otto.mq5 complete")


# ======================================================================
# 7. _probe/test_v540_hybrid.py -- INVERTED to the limit-only model
# ======================================================================
print("_probe/test_v540_hybrid.py")
hyb, hyb_eol = load(os.path.join("_probe", "test_v540_hybrid.py"))

HYB_DOC_OLD = """Pins the behaviours v5.40 adds on top of the v5.37/v5.38/v5.39 virtual-order
engine. None of them is provable by the MQL5 compiler gate: they are control
flow and side-effect properties of the shipped sources.

  1. ROUTER. COttoOrderManager::PlaceOrArmOrder() (renamed from
     ArmVirtualOrder) decides per setup whether an entry may REST at the broker.
     A valid price -> a real TRADE_ACTION_PENDING limit (PHYSICAL). A price
     already through the zone -> an in-memory SVirtualOrder (VIRTUAL). A
     refused physical send FALLS BACK to the virtual arm.

  2. FILL SCANNER. CheckPendingOrderFills() (recovered) is wired into Update()
     and resolves a physical fill through the 3-tier detector.

  3. DUAL-BOOK hygiene: the duplicate shield (IsAnyOrderLiveAtPrice), the
     pending count (CountMyPendingOrders) and the cancel sweeps all span BOTH
     the broker pending pool AND the in-memory virtual book.

  4. SETUP-ARM JOURNAL: COttoJournal::LogSetupArmed() stamps the Logic and
     Execution Mode on the arm header. v5.52 collapses the old R/C phase to a
     single constant continuation label, so the journal now names one logic.
     v5.42: the arm no longer emails -- only LogExit() ships the session file.

Pure static analysis of the shipped sources - no MT5 required.
"""

HYB_DOC_NEW = """HISTORICAL (v5.40). Pins the behaviours v5.40 added on top of the v5.37/v5.38/
v5.39 virtual-order engine. v5.72 RETIRED the virtual half of that router, and
this probe is re-pinned to the strict limit-or-nothing model that replaced it.
What it still guards is the part that did not change:

  1. ROUTER. COttoOrderManager::PlaceOrArmOrder() decides per setup whether an
     entry may REST at the broker. A valid price -> a real TRADE_ACTION_PENDING
     limit. v5.72: a price already through the zone is VETOED
     (VETO_LIMIT_MISSED) instead of being armed in memory, and a refused send is
     vetoed instead of degrading into a MARKET deal. Limit or nothing.

  2. FILL SCANNER. CheckPendingOrderFills() is wired into Update() and resolves
     a physical fill through the 3-tier detector. Since v5.72 it is the ONLY
     fill path, so its ordering against the retired virtual trigger is no
     longer a property -- its presence and its sole-pathy are.

  3. BOOK HYGIENE. The duplicate shield, the pending count and the cancel sweeps
     all operate on the broker pending pool, which is now the only book.

  4. SETUP-ARM JOURNAL: COttoJournal::LogSetupArmed() stamps the Logic and
     Execution Mode on the arm header. v5.52 collapsed the old R/C phase to a
     single constant continuation label; v5.72 collapsed the execution mode to
     its single shipped value for the same reason. v5.42: the arm no longer
     emails -- only LogExit() ships the session file.

  5. THE NEGATIVE PIN. No SVirtualOrder may be declared anywhere in the tree, no
     virtual identifier may survive in the manager, and no virtual trigger may
     be called. That is the whole release, expressed as a build gate.

Pure static analysis of the shipped sources - no MT5 required.
"""

hyb = rep(hyb, HYB_DOC_OLD, HYB_DOC_NEW, 1, "docstring: v5.72 model")

HYB_ROUTER_OLD = '''check("physical route journals with physical=true",
      ROUTER is not None and "LogSetupArmed(true," in ROUTER)
check("router arms a VIRTUAL order when price is through the zone",
      ROUTER is not None and "SVirtualOrder vo;" in ROUTER)
check("virtual route journals with physical=false",
      ROUTER is not None and "LogSetupArmed(false," in ROUTER)
# Fallback: a refused resting send must arm virtually, not drop the setup.
check("a refused physical send falls back to the virtual arm",
      ROUTER is not None and
      "falling back to VIRTUAL arm" in ROUTER)'''

HYB_ROUTER_NEW = '''check("physical route journals through the ONE-route arm writer",
      ROUTER is not None and "LogSetupArmed(block.tradeId" in ROUTER)
# v5.72: both non-resting outcomes are VETOES. Two, because there are exactly
# two ways to fail -- the price was already through the entry, or the venue
# refused the send -- and neither may reach an order send.
check("router vetoes when price is through the zone (no in-memory arm)",
      ROUTER is not None and
      "SVirtualOrder" not in ROUTER and
      ROUTER.count("block.vetoReason         = VETO_LIMIT_MISSED;") == 2)
# The defect this release exists to remove: a refused limit must never become a
# MARKET chase. Assert the fallback literal is gone, and no DEAL can be built.
check("a refused physical send does NOT fall back to a MARKET arm",
      ROUTER is not None and
      "falling back to VIRTUAL arm" not in ROUTER and
      "TRADE_ACTION_DEAL" not in ROUTER)'''

hyb = rep(hyb, HYB_ROUTER_OLD, HYB_ROUTER_NEW, 1, "router: two vetoes, no fallback")

HYB_FILL_OLD = '''check("fill scanner runs before the virtual trigger",
      UPDATE is not None and
      UPDATE.find("CheckPendingOrderFills();") < UPDATE.find("CheckVirtualTriggers();"))'''

HYB_FILL_NEW = '''check("Update() runs no virtual trigger stage (v5.72)",
      UPDATE is not None and "CheckVirtualTriggers" not in UPDATE)'''

hyb = rep(hyb, HYB_FILL_OLD, HYB_FILL_NEW, 1, "fill scanner: sole fill path")

HYB_BOOK_OLD = '''# 3. Dual-book hygiene
# ----------------------------------------------------------------------
print("\\n-- Dual-book hygiene --")

check("duplicate shield located", SHIELD is not None)
check("shield scans the broker pending pool",
      SHIELD is not None and "OrdersTotal()" in SHIELD and
      "OrderGetTicket(i)" in SHIELD)
check("shield also scans the virtual book",
      SHIELD is not None and "m_virtualOrders[v].triggerPrice" in SHIELD)
check("shield name reflects the dual book (IsAnyOrderLiveAtPrice)",
      "IsAnyOrderLiveAtPrice(double targetPrice" in OM_T and
      "IsOrderAlreadyLiveAtPrice" not in OM_T)
check("pending count spans broker pendings + virtual orders",
      COUNT is not None and "OrdersTotal()" in COUNT and
      "count + m_virtualCount" in COUNT)'''

HYB_BOOK_NEW = '''# 3. Book hygiene: ONE book
# ----------------------------------------------------------------------
print("\\n-- Book hygiene (broker pendings only) --")

check("duplicate shield located", SHIELD is not None)
check("shield scans the broker pending pool",
      SHIELD is not None and "OrdersTotal()" in SHIELD and
      "OrderGetTicket(i)" in SHIELD)
check("shield scans NO in-memory book (v5.72)",
      SHIELD is not None and "m_virtualOrders" not in SHIELD and
      "m_virtualCount" not in SHIELD)
check("shield name reflects its job (IsAnyOrderLiveAtPrice)",
      "IsAnyOrderLiveAtPrice(double targetPrice" in OM_T and
      "IsOrderAlreadyLiveAtPrice" not in OM_T)
check("pending count is the broker pool alone (v5.72)",
      COUNT is not None and "OrdersTotal()" in COUNT and
      re.search(r"return\\s+count\\s*;", COUNT) is not None and
      "m_virtualCount" not in COUNT)'''

hyb = rep(hyb, HYB_BOOK_OLD, HYB_BOOK_NEW, 1, "book hygiene: single book")

HYB_ARM_OLD = '''check("arm journal stamps the execution mode",
      ARM_J is not None and "PHYSICAL (Broker Limit Order)" in ARM_J and
      "VIRTUAL (In-Memory Arm)" in ARM_J)'''

HYB_ARM_NEW = '''check("arm journal stamps the ONE execution mode (v5.72)",
      ARM_J is not None and "PHYSICAL (Broker Limit Order)" in ARM_J and
      "VIRTUAL (In-Memory Arm)" not in ARM_J and
      "physical ?" not in ARM_J)'''

hyb = rep(hyb, HYB_ARM_OLD, HYB_ARM_NEW, 1, "arm journal: single mode")

HYB_NEG_ANCHOR = '''# ----------------------------------------------------------------------
# 5. Version stamps agree on the current release'''

HYB_NEG_NEW = '''# ----------------------------------------------------------------------
# 4b. THE NEGATIVE PIN: the in-memory engine is GONE (v5.72)
# ----------------------------------------------------------------------
# This is the release itself, expressed as a build gate. The DECLARATION is
# what makes the engine real, so it is matched as a declaration rather than as
# a substring: prose explaining what was retired legitimately names it.
print("\\n-- Negative pin: no in-memory execution engine --")

check("no SVirtualOrder DECLARATION anywhere in the tree",
      all(re.search(r"struct\\s+SVirtualOrder", read(os.path.join(ROOT, f))) is None
          for f in ALL_FILES))
for tok in ("m_virtualOrders", "m_virtualCount", "RemoveVirtualOrderAt",
            "ReleaseAllVirtualArmLocks", "VirtualArmGvName",
            "CheckVirtualTriggers"):
    check("no %s in COttoOrderManager.mqh" % tok, tok not in OM_T)
check("the execution mode is a single shipped constant",
      ARM_J is not None and "VIRTUAL (In-Memory Arm)" not in ARM_J)


# ----------------------------------------------------------------------
# 5. Version stamps agree on the current release'''

hyb = rep(hyb, HYB_NEG_ANCHOR, HYB_NEG_NEW, 1, "negative pin added")

save(os.path.join("_probe", "test_v540_hybrid.py"), hyb, hyb_eol)
print("_probe/test_v540_hybrid.py complete")

# ======================================================================
# 8. _probe/test_v539_rejection.py
# ======================================================================
print("_probe/test_v539_rejection.py")
rej, rej_eol = load(os.path.join("_probe", "test_v539_rejection.py"))

REJ_DOC_OLD = """v5.39 static verification probe - arm email, terminal rejection, guard removal.

Pins the three behaviours v5.39 adds on top of the v5.37/v5.38 virtual-order
engine. None of them is provable by the MQL5 compiler gate: they are control
flow and side-effect properties of the shipped sources.

  1. ARM IS SILENT (v5.42). COttoJournal::LogSetupArmed() must WRITE the
     SUBJECT + setup block and close the handle but must NOT call
     SendMailFromFile(): arming is an intermediate lifecycle stage. Only the
     two terminal writers (LogExit()/LogCancellation()) email the session file.

  2. REJECTION IS TERMINAL AND READABLE. When SendOrderWithRetry() refuses the
     MARKET deal fired by CheckVirtualTriggers(), the raw retcode must be mapped
     through GetTradeRetcodeString(), journalled via LogCancellation(), and the
     block vetoed (VETO_BROKEN) with its virtual order dropped. The v5.37
     latch-then-retry path (which left the order armed and re-armed a second
     SVirtualOrder every tick) must be gone.

  3. LEGACY PRICE GUARD RETIRED. The v5.28 LIVE PRICE VALIDATION pre-flight in
     ArmVirtualOrder() and its one-shot priceAbortLogged gate are removed: a
     v5.37+ setup is an in-memory SVirtualOrder, so no limit rests at the broker.

Pure static analysis of the shipped sources - no MT5 required.
"""

REJ_DOC_NEW = """v5.39 static verification probe - arm email, terminal rejection, guard removal.

HISTORICAL (v5.39). Two behaviours landed here on top of the v5.37/v5.38 engine.
Both are re-pinned to the v5.72 shape, whose execution model has since changed:

  1. ARM IS SILENT (v5.42). COttoJournal::LogSetupArmed() must WRITE the
     SUBJECT + setup block and close the handle but must NOT call
     SendMailFromFile(): arming is an intermediate lifecycle stage. Only the
     two terminal writers (LogExit()/LogCancellation()) email the session file.
     UNCHANGED by v5.72.

  2. REJECTION IS TERMINAL AND READABLE. The v5.39 target was the MARKET deal
     fired by the retired CheckVirtualTriggers(). v5.72 retires that engine, so
     the same property is re-pinned on the ONE send that can now be refused: the
     RESTING limit in PlaceOrArmOrder(). A refusal there must map the raw
     retcode through GetTradeRetcodeString(), journal it via LogCancellation(),
     and veto the block as VETO_LIMIT_MISSED rather than degrading into a MARKET
     chase. The v5.37 latch-then-retry path must stay gone.

  3. LEGACY PRICE GUARD RETIRED. The v5.28 LIVE PRICE VALIDATION pre-flight and
     its one-shot priceAbortLogged gate are removed: v5.40 reintroduced a
     live-price decision as a ROUTER (rest or veto), never as a fallback arm.

Pure static analysis of the shipped sources - no MT5 required.
"""

rej = rep(rej, REJ_DOC_OLD, REJ_DOC_NEW, 1, "docstring: v5.72 model")

REJ_SEC2_OLD = '''# ----------------------------------------------------------------------
# 2. Terminal, readable rejection
# ----------------------------------------------------------------------
print("\\n-- Terminal rejection --")

check("rejection branch located", REJ is not None)
check("rejection maps the retcode via GetTradeRetcodeString()",
      REJ is not None and
      "GetTradeRetcodeString(result.retcode)" in REJ)
check("rejection captures a readable error string",
      REJ is not None and
      re.search(r"string\\s+errorStr\\s*=\\s*GetTradeRetcodeString\\(",
                REJ) is not None)
check("rejection journals a LogCancellation()",
      REJ is not None and "LogCancellation(" in REJ)
check("rejection reason names the broker refusal + code",
      REJ is not None and
      \'"Broker Rejected Market Order: "\' in REJ and
      "IntegerToString(" in REJ)
check("rejection vetoes the spent block",
      REJ is not None and "isVetoed" in REJ and "VETO_BROKEN" in REJ)
check("rejection drops the virtual order",
      REJ is not None and "RemoveVirtualOrderAt(v)" in REJ)'''

REJ_SEC2_NEW = '''# ----------------------------------------------------------------------
# 2. A refused RESTING send is terminal and readable (v5.72 shape)
# ----------------------------------------------------------------------
print("\\n-- Terminal refusal of the resting limit --")

# The success branch is the if(), so the refusal block is what follows it. It
# is located by its own markers rather than by a brace extraction, because a
# comment-only edit upstream must not be able to break the extraction and
# silently pass the section.
check("refusal branch located",
      "RESTING SEND REFUSED" in OM_T and "return false;" in OM_T)
check("refusal maps the retcode via GetTradeRetcodeString()",
      re.search(r"string\\s+refuseErr\\s*=\\s*GetTradeRetcodeString\\(result\\.retcode\\)",
                OM_T) is not None)
check("refusal captures a readable error string",
      "string refuseErr = GetTradeRetcodeString(result.retcode);" in OM_T)
check("refusal journals a LogCancellation()",
      \'"Broker Refused Limit Order: " + refuseErr\' in OM_T)
check("refusal reason names the broker refusal + code",
      \'LogCancellation("Broker Refused Limit Order: " + refuseErr +\' in OM_T and
      \'IntegerToString((long)result.retcode)\' in OM_T)
check("refusal vetoes the spent block with its OWN reason",
      "block.vetoReason         = VETO_LIMIT_MISSED;" in OM_T)
# The defect this release exists to remove: a refused limit must never become a
# MARKET chase.
check("refusal does NOT fall back to a MARKET arm",
      "falling back to VIRTUAL arm" not in OM_T)'''

rej = rep(rej, REJ_SEC2_OLD, REJ_SEC2_NEW, 1, "section 2: refusal of the limit")

save(os.path.join("_probe", "test_v539_rejection.py"), rej, rej_eol)
print("_probe/test_v539_rejection.py complete")

REJ_SEC3_OLD = '''# ----------------------------------------------------------------------
# 3. Hybrid router (v5.40 supersedes the v5.39 "guard retired" pin)
# ----------------------------------------------------------------------
# v5.40 reintroduces a live-price decision, but as a ROUTER not a guard: a
# legal price becomes a resting BROKER limit (physical), an illegal one arms
# virtually. GetPriceBoundaryBuffer() therefore HAS a caller again, and the
# physical branch carries the priceBuffer local.
print("\\n-- Hybrid router --")

ARM_OM = func_body(OM_T, r"bool\\s+PlaceOrArmOrder\\s*\\(int blockIndex")
check("PlaceOrArmOrder located", ARM_OM is not None)
check("router consumes the boundary buffer to pick the route",
      ARM_OM is not None and "GetPriceBoundaryBuffer()" in ARM_OM)
check("router sends a PHYSICAL pending order",
      ARM_OM is not None and "TRADE_ACTION_PENDING" in ARM_OM)
check("router arms a VIRTUAL order when the price is through",
      ARM_OM is not None and "SVirtualOrder vo;" in ARM_OM)
check("router labels the physical route in its journal call",
      ARM_OM is not None and "LogSetupArmed(true," in ARM_OM)
check("router labels the virtual route in its journal call",
      ARM_OM is not None and "LogSetupArmed(false," in ARM_OM)
check("boundary helper is live again (has a caller)",
      "GetPriceBoundaryBuffer()" in OM_T and
      "GetPriceBoundaryBuffer(void)" in OM_T)'''

REJ_SEC3_NEW = '''# ----------------------------------------------------------------------
# 3. Strict limit router (v5.40 router, v5.72 limit-or-nothing)
# ----------------------------------------------------------------------
# v5.40 introduced a live-price decision, but as a ROUTER not a guard: a legal
# price becomes a resting BROKER limit. v5.72 removed the other half of that
# route -- the in-memory arm -- so the "illegal price" outcome is now a VETO.
# The body is read with comments stripped, because the two veto rows are
# matched as CODE and prose may legitimately name the retired engine.
print("\\n-- Strict limit router --")

ARM_OM = func_body(strip_comments(OM_T), r"bool\\s+PlaceOrArmOrder\\s*\\(int blockIndex")
check("PlaceOrArmOrder located", ARM_OM is not None)
check("router consumes the boundary buffer to pick the route",
      ARM_OM is not None and "GetPriceBoundaryBuffer()" in ARM_OM)
check("router sends a PHYSICAL pending order",
      ARM_OM is not None and "TRADE_ACTION_PENDING" in ARM_OM)
check("router vetoes (never arms) when the price is through",
      ARM_OM is not None and
      "SVirtualOrder" not in ARM_OM and
      ARM_OM.count("VETO_LIMIT_MISSED") == 2)
check("router labels the ONE route in its journal call",
      ARM_OM is not None and "LogSetupArmed(block.tradeId" in ARM_OM)
check("boundary helper is live again (has a caller)",
      "GetPriceBoundaryBuffer()" in OM_T and
      "GetPriceBoundaryBuffer(void)" in OM_T)'''

rej = rep(rej, REJ_SEC3_OLD, REJ_SEC3_NEW, 1, "section 3: strict limit router")

save(os.path.join("_probe", "test_v539_rejection.py"), rej, rej_eol)
print("_probe/test_v539_rejection.py complete")


# ======================================================================
# 9. _probe/test_v526_vector.py -- pin a behaviour, not a column
# ======================================================================
print("_probe/test_v526_vector.py")
vec, vec_eol = load(os.path.join("_probe", "test_v526_vector.py"))

vec = rep(vec,
          'check("sweep releases the block ticket", "mod.limitOrderTicket = 0;" in OM_T)',
          '# v5.72: pinned as a BEHAVIOUR, not a column. The retired virtual\n'
          '# consensus walk happened to be the only site writing\n'
          '# `mod.limitOrderTicket = 0;` with single spaces, so the old literal was\n'
          '# silently tracking column alignment rather than the release. The\n'
          '# property is that the sweep clears the ticket on the block.\n'
          'check("sweep releases the block ticket",\n'
          '      re.search(r"mod\\.limitOrderTicket\\s*=\\s*0\\s*;", OM_T) is not None)',
          1, "ticket release pinned as a behaviour")

save(os.path.join("_probe", "test_v526_vector.py"), vec, vec_eol)


# ======================================================================
# 10. _probe/test_v541_sarfix.py -- the ONLY reversal guard
# ======================================================================
print("_probe/test_v541_sarfix.py")
sar, sar_eol = load(os.path.join("_probe", "test_v541_sarfix.py"))

sar = rep(sar,
          "     m_activeDirection != newDir, mirroring the virtual trigger's by-construction\n"
          "     reversal check.",
          "     m_activeDirection != newDir. v5.72 retired the virtual MARKET fire that\n"
          "     carried the twin of this guard, so the physical guard is now the ONLY\n"
          "     one and is pinned as such.",
          1, "docstring: sole reversal guard")

sar = rep(sar,
          "# The physical guard must mirror the virtual trigger's, which already carried\n"
          "# a direction clause -- there expressed as a bool->enum mapping\n"
          "# (m_activeDirection != (isLong ? DIR_LONG : DIR_SHORT)).\n"
          'check("physical guard mirrors the virtual trigger\'s direction check",\n'
          "      FILLS is not None and VTRIG is not None and\n"
          '      "m_activeDirection != newDir" in FILLS and\n'
          '      re.search(r"m_activeDirection\\s*!=", VTRIG) is not None)',
          "# v5.72: the virtual trigger that carried the twin guard is retired, so the\n"
          '# property is no longer "mirrors the other route" but "is the ONLY route".\n'
          'check("the physical guard is the ONLY reversal guard left (v5.72)",\n'
          "      FILLS is not None and\n"
          '      "m_activeDirection != newDir" in FILLS and\n'
          '      "CheckVirtualTriggers" not in OM_C)',
          1, "mirror check -> sole-guard check")

save(os.path.join("_probe", "test_v541_sarfix.py"), sar, sar_eol)


# ======================================================================
# 11. _probe/test_v547_filling.py -- chokepoint census 8 -> 7
# ======================================================================
print("_probe/test_v547_filling.py")
fil, fil_eol = load(os.path.join("_probe", "test_v547_filling.py"))

fil = rep(fil,
          'check("all seven call sites still route through the chokepoint",\n'
          '      calls == 8, "matches=%d (1 definition + 7 call sites expected)" % calls)',
          "# v5.72: the virtual MARKET fire was one of the seven call sites and went\n"
          "# with its engine, so the census is 1 definition + 6 call sites. Asserted as\n"
          "# an EQUALITY so a new un-chokepointed send cannot hide behind a >= test.\n"
          'check("all six call sites still route through the chokepoint",\n'
          '      calls == 7, "matches=%d (1 definition + 6 call sites expected)" % calls)',
          1, "chokepoint census 8 -> 7")

save(os.path.join("_probe", "test_v547_filling.py"), fil, fil_eol)


# ======================================================================
# 12. _probe/test_v543_session.py
# ======================================================================
print("_probe/test_v543_session.py")
ses, ses_eol = load(os.path.join("_probe", "test_v543_session.py"))

SES_SEC2_OLD = '''# 2. SAR close locks the journal to the INCUMBENT basket (both routes)
# ----------------------------------------------------------------------
print("\\n-- SAR close locks the incumbent journal --")

if FILLS is not None:
    lock = FILLS.find("SetSessionID(m_sessionID)")
    close = FILLS.find('CloseEntireBasket("SAR Reversal"')
    check("physical SAR locks the incumbent before the close",
          0 <= lock < close, "lock=%d close=%d" % (lock, close))

check("CheckVirtualTriggers located", VTRIG is not None)
if VTRIG is not None:
    lock = VTRIG.find("SetSessionID(m_sessionID)")
    close = VTRIG.find('CloseEntireBasket("SAR Reversal"')
    check("virtual SAR locks the incumbent before the close",
          0 <= lock < close, "lock=%d close=%d" % (lock, close))


# ----------------------------------------------------------------------
# 3. Fill paths HAND the setup session to the basket before seeding
# ----------------------------------------------------------------------
print("\\n-- Fill-path session hand-off --")

check("physical fill adopts the block's arm session",
      FILLS is not None and "m_sessionID = blocks[i].sessionId;" in FILLS)
check("virtual fill adopts its pre-minted session",
      VTRIG is not None and "m_sessionID = newSessionId;" in VTRIG)


# ----------------------------------------------------------------------
# 4. Virtual route no longer repoints the journal BEFORE the send
# ----------------------------------------------------------------------
print("\\n-- No pre-send journal repoint (virtual) --")

if VTRIG is not None:
    send = VTRIG.find("SendOrderWithRetry(request, result)")
    repoint = VTRIG.find("SetSessionID(newSessionId)")
    check("the only newSessionId repoint is the post-send rejection path",
          0 <= send < repoint, "send=%d repoint=%d" % (send, repoint))
    check("exactly one newSessionId repoint remains in the trigger",
          VTRIG.count("SetSessionID(newSessionId)") == 1,
          "found %d" % VTRIG.count("SetSessionID(newSessionId)"))'''

SES_SEC2_NEW = '''# 2. SAR close locks the journal to the INCUMBENT basket
# ----------------------------------------------------------------------
# v5.72: only the physical fill path can trigger a reversal now, so the one
# surviving lock is the whole property. The retired virtual twin is asserted
# absent rather than merely unchecked.
print("\\n-- SAR close locks the incumbent journal --")

if FILLS is not None:
    lock = FILLS.find("SetSessionID(m_sessionID)")
    close = FILLS.find('CloseEntireBasket("SAR Reversal"')
    check("physical SAR locks the incumbent before the close",
          0 <= lock < close, "lock=%d close=%d" % (lock, close))

check("the retired virtual SAR path is gone (v5.72)",
      "CheckVirtualTriggers" not in OM_C)


# ----------------------------------------------------------------------
# 3. Fill paths HAND the setup session to the basket before seeding
# ----------------------------------------------------------------------
print("\\n-- Fill-path session hand-off --")

check("physical fill adopts the block's arm session",
      FILLS is not None and "m_sessionID = blocks[i].sessionId;" in FILLS)
check("the virtual fill's pre-minted session hand-off is gone (v5.72)",
      "m_sessionID = newSessionId;" not in OM_C)


# ----------------------------------------------------------------------
# 4. No journal repoint can precede a send (v5.72 assertion of absence)
# ----------------------------------------------------------------------
# v5.43's rule was that a fill must never repoint the journal BEFORE the order
# is sent. The route that made this subtle was the virtual MARKET fire, and it
# is retired -- so what must hold now is the stronger form: every
# LogSetupArmed call is bracketed by a save/restore pair.
print("\\n-- No pre-send journal repoint --")

check("every LogSetupArmed call is save/restore bracketed",
      ROUTER is not None and "LogSetupArmed(" in ROUTER and
      ROUTER.find("string prevSession = m_sessionID;") < ROUTER.find("LogSetupArmed(") and
      ROUTER.find("LogSetupArmed(") <
      ROUTER.find('if(prevSession != "") m_journal.SetSessionID(prevSession);'))'''

ses = rep(ses, SES_SEC2_OLD, SES_SEC2_NEW, 1, "sections 2-4: single route")

save(os.path.join("_probe", "test_v543_session.py"), ses, ses_eol)


SES_SEC5_OLD = '''check("PlaceOrArmOrder located", ROUTER is not None)
if ROUTER is not None:
    check("arm saves the live session (physical + virtual)",
          ROUTER.count(SAVE) == 2, "found %d" % ROUTER.count(SAVE))
    check("arm restores the live session (physical + virtual)",
          ROUTER.count(RESTORE) == 2, "found %d" % ROUTER.count(RESTORE))

check("CancelOrdersForInvalidBlocks located", CANCEL is not None)
if CANCEL is not None:
    check("cancel saves the live session at all four writes",
          CANCEL.count(SAVE) == 4, "found %d" % CANCEL.count(SAVE))
    check("cancel restores the live session at all four writes",
          CANCEL.count(RESTORE) == 4, "found %d" % CANCEL.count(RESTORE))'''

SES_SEC5_NEW = '''# v5.72: the router has ONE journal writer (the resting limit) and the cancel
# sweep has ONE (the physical delete), where v5.43 had two of each. The counts
# therefore drop 2 -> 1 and 4 -> 1, and the assertion moves with them: the pair
# must still be balanced around the single write.
check("PlaceOrArmOrder located", ROUTER is not None)
if ROUTER is not None:
    check("arm saves the live session before its ONE write",
          ROUTER.count(SAVE) == 1, "found %d" % ROUTER.count(SAVE))
    check("arm restores the live session after its ONE write",
          ROUTER.count(RESTORE) == 1, "found %d" % ROUTER.count(RESTORE))

check("CancelOrdersForInvalidBlocks located", CANCEL is not None)
if CANCEL is not None:
    check("cancel saves the live session before its ONE write",
          CANCEL.count(SAVE) == 1, "found %d" % CANCEL.count(SAVE))
    check("cancel restores the live session after its ONE write",
          CANCEL.count(RESTORE) == 1, "found %d" % CANCEL.count(RESTORE))'''

ses = rep(ses, SES_SEC5_OLD, SES_SEC5_NEW, 1, "section 5: save/restore counts")

save(os.path.join("_probe", "test_v543_session.py"), ses, ses_eol)
print("_probe/test_v543_session.py complete")


# ======================================================================
# 13. _probe/test_v548_hourly.py
# ======================================================================
print("_probe/test_v548_hourly.py")
hou, hou_eol = load(os.path.join("_probe", "test_v548_hourly.py"))

HOU_UPD_OLD = '''    virt = UPD.find("CheckVirtualTriggers();")
    check("the hook runs BEFORE CompleteReversal()",
          hook >= 0 and rev >= 0 and hook < rev, "hook=%d rev=%d" % (hook, rev))
    check("the hook runs BEFORE the physical fill scan",
          hook >= 0 and fill >= 0 and hook < fill, "hook=%d fill=%d" % (hook, fill))
    check("the hook runs BEFORE the virtual trigger",
          hook >= 0 and virt >= 0 and hook < virt, "hook=%d virt=%d" % (hook, virt))
    check("the existing four Update() stages are all still present",
          rev >= 0 and fill >= 0 and virt >= 0 and "AdoptManualPosition();" in UPD)'''

HOU_UPD_NEW = '''    check("the hook runs BEFORE CompleteReversal()",
          hook >= 0 and rev >= 0 and hook < rev, "hook=%d rev=%d" % (hook, rev))
    check("the hook runs BEFORE the physical fill scan",
          hook >= 0 and fill >= 0 and hook < fill, "hook=%d fill=%d" % (hook, fill))
    # v5.72: the virtual trigger stage is gone with its engine. The remaining
    # ordering is what still matters -- the report must predate BOTH mutations.
    check("the virtual trigger stage is retired (v5.72)",
          "CheckVirtualTriggers" not in UPD)
    check("the three surviving Update() stages are all still present",
          rev >= 0 and fill >= 0 and "AdoptManualPosition();" in UPD)'''

hou = rep(hou, HOU_UPD_OLD, HOU_UPD_NEW, 1, "Update() stage census")

save(os.path.join("_probe", "test_v548_hourly.py"), hou, hou_eol)

HOU_BODY_OLD = '''check("the report walks the live virtual book (armed setups)",
      BODY is not None and "m_virtualOrders" in BODY and "m_virtualCount" in BODY)
check("it re-resolves each armed setup's block by SERIAL, not by index",
      BODY is not None and "FindBlockIndexBySerial(" in BODY)
check("it reads the block snapshot it just resolved",
      BODY is not None and re.search(r"GetBlockAt\\s*\\(\\s*bIdx\\s*,", BODY) is not None)'''

HOU_BODY_NEW = '''# v5.72: the walk reads the RESTING LIMITS off the block book, because the
# in-memory descriptors are gone. The v5.51 property is unchanged -- every armed
# order must still resolve to a live block, and an unresolved one must degrade
# to a VISIBLE UNKNOWN rather than a blank.
check("the report walks the resting limits on the block book (v5.72)",
      BODY is not None and "GetBlockCount()" in BODY and
      "limitOrderTicket" in BODY and "m_virtualOrders" not in BODY)
check("it reads each block through GetBlockAt()",
      BODY is not None and re.search(r"GetBlockAt\\s*\\(\\s*h\\s*,", BODY) is not None)'''

hou = rep(hou, HOU_BODY_OLD, HOU_BODY_NEW, 1, "report walks resting limits")

hou = rep(hou,
          'check("an unresolved block degrades to a visible UNKNOWN, never a blank",\n'
          '      BODY is not None and "UNKNOWN" in BODY)',
          'check("an unresolved block degrades to a visible UNKNOWN, never a blank",\n'
          '      BODY is not None and "UNKNOWN" in BODY)\n'
          'check("the resting ticket is reported so the row is actionable",\n'
          '      BODY is not None and "Ticket: #" in BODY)',
          1, "ticket reported in the row")

save(os.path.join("_probe", "test_v548_hourly.py"), hou, hou_eol)
print("_probe/test_v548_hourly.py complete")


# ======================================================================
# 14. _probe/test_v548_seed_dir.py
# ======================================================================
print("_probe/test_v548_seed_dir.py")
sd, sd_eol = load(os.path.join("_probe", "test_v548_seed_dir.py"))

SD_DOC_OLD = '''CURRENT (v5.52). The two-phase engine is gone. The block no longer carries a
touch counter at all, and GetDirectionForBlock() is UNCONDITIONAL on the zone
type -- "a RESISTANCE zone trades LONG, a SUPPORT zone trades SHORT" -- so the
derived side can no longer lag the fill. What survives, and what this probe now
pins, is the CONTRACT, which is still worth a build gate:

  * the overrideDir parameter and its DIR_NONE default,
  * the precedence "(overrideDir != DIR_NONE) ? overrideDir : derived",
  * the virtual fire capturing the side from the live virtual order and
    declaring it BEFORE the seed call that consumes it,
  * the physical fill seeding in its two-argument (derive) form, and
  * the one-definition plus two-call-site seed census.

The remaining properties are STRUCTURE-sensitive, which is exactly what the
existing v5.40 / v5.41 / v5.43 probes miss. Pure static analysis of the shipped
sources; no MT5 required.
'''

SD_DOC_NEW = '''CURRENT (v5.72). Both of those mechanisms are gone, and this probe pins the
stronger property that replaced them:

  * the two-phase engine is retired, so GetDirectionForBlock() is
    UNCONDITIONAL on the zone type and the derived side cannot lag a fill;
  * the virtual MARKET fire was overrideDir's only caller, so the parameter is
    RETIRED with it -- SeedActiveTradeFromBlock() no longer accepts an override;
  * there is now exactly ONE caller of the seed (the resting-limit fill scan),
    which is what the census pins: a second seeding path cannot reappear
    without failing this probe.

The remaining properties are STRUCTURE-sensitive, which is exactly what the
other execution probes miss. Pure static analysis of the shipped sources; no MT5
required.
'''

sd = rep(sd, SD_DOC_OLD, SD_DOC_NEW, 1, "docstring: v5.72 contract")

SD_SECTIONS_OLD = '''VTRIG = func_body(OM_C, r"void\\s+CheckVirtualTriggers\\s*\\(\\s*(?:void\\s*)?\\)")
FILLS = func_body(OM_C, r"void\\s+CheckPendingOrderFills\\s*\\(\\s*(?:void\\s*)?\\)")'''

SD_SECTIONS_NEW = '''FILLS = func_body(OM_C, r"void\\s+CheckPendingOrderFills\\s*\\(\\s*(?:void\\s*)?\\)")'''

sd = rep(sd, SD_SECTIONS_OLD, SD_SECTIONS_NEW, 1, "VTRIG extraction removed")

SD_SEC2_OLD = '''# ----------------------------------------------------------------------
# 2. The virtual path captures the fill-time side from the live order
# ----------------------------------------------------------------------
print("\\n-- Virtual fire: side captured from the armed virtual order --")

check("CheckVirtualTriggers located", VTRIG is not None)
check("the fire reads the side off the armed virtual order",
      VTRIG is not None and "bool isLong = (vo.direction == DIR_LONG);" in VTRIG)
check("a seedDir local is declared from that side",
      VTRIG is not None and
      re.search(r"ENUM_TRADE_DIRECTION\\s+seedDir\\s*=\\s*isLong\\s*\\?\\s*"
                r"DIR_LONG\\s*:\\s*DIR_SHORT\\s*;", VTRIG) is not None)

if VTRIG is not None:
    decl = VTRIG.find("ENUM_TRADE_DIRECTION seedDir")
    seed_call = re.search(r"SeedActiveTradeFromBlock\\s*\\(", VTRIG)
    check("seedDir is declared BEFORE the seed call that consumes it",
          decl >= 0 and seed_call is not None and decl < seed_call.start(),
          "decl=%d seed_call=%s" % (decl, seed_call.start() if seed_call else None))
    # v5.52: the touch counter is gone from the tree, so the old "captured
    # before the increment" ordering cannot silently regress any more. Pin the
    # counter's ABSENCE instead -- if it ever returns, the capture ordering
    # above stops being sufficient and this probe must be revisited.
    check("the retired touch counter is gone from the virtual fire",
          "touches" not in VTRIG)

# ----------------------------------------------------------------------
# 3. The virtual path threads that side into the seed
# ----------------------------------------------------------------------
print("\\n-- Virtual fire: seed receives the fill-time side --")

check("the virtual fill passes seedDir into SeedActiveTradeFromBlock",
      VTRIG is not None and
      re.search(r"SeedActiveTradeFromBlock\\s*\\(\\s*block\\s*,\\s*result\\.order\\s*,\\s*"
                r"seedDir\\s*\\)\\s*;", VTRIG) is not None)

# ----------------------------------------------------------------------
# 4. The seed honours the override in exactly one place
# ----------------------------------------------------------------------
print("\\n-- Seed honours the fill-time override --")

check("SeedActiveTradeFromBlock located", SEED is not None)
check("the seed takes the optional overrideDir parameter",
      SEED is not None and
      re.search(r"ENUM_TRADE_DIRECTION\\s+overrideDir\\s*=\\s*DIR_NONE\\s*\\)", SEED)
      is not None)
check("DIR_NONE (a physical fill) still derives the side from the block",
      SEED is not None and
      re.search(r"\\(overrideDir\\s*!=\\s*DIR_NONE\\s*\\)\\s*\\?\\s*overrideDir\\s*:\\s*"
                r"GetDirectionForBlock\\s*\\(\\s*block\\s*\\)", SEED, re.S) is not None)
check("there is exactly one writer of m_activeTrade.direction in the seed",'''

SD_SEC2_NEW = '''# ----------------------------------------------------------------------
# 2. The virtual fire is RETIRED, so no fill-time side can disagree
# ----------------------------------------------------------------------
# v5.48's whole premise was that two independent fill paths could disagree about
# the side. v5.72 removed one of them, which dissolves the premise rather than
# re-pinning it: there is no second path left to capture a side FROM.
print("\\n-- Virtual fire retired (no second fill path) --")

check("CheckVirtualTriggers is gone from the manager",
      "CheckVirtualTriggers" not in OM_C)
check("no seedDir local exists anywhere", "seedDir" not in OM_C)

# ----------------------------------------------------------------------
# 3. The seed derives the side; it takes no override at all
# ----------------------------------------------------------------------
print("\\n-- Seed derives the side (no override parameter) --")

check("SeedActiveTradeFromBlock located", SEED is not None)
check("the seed signature is two arguments (block + ticket)",
      re.search(r"void\\s+SeedActiveTradeFromBlock\\s*\\(\\s*const\\s+SSniperBlock\\s*&block"
                r"\\s*,\\s*ulong\\s+positionTicket\\s*\\)", OM_C) is not None)
check("the overrideDir parameter is retired",
      "overrideDir" not in OM_C)
check("the seed derives the side from the block",
      SEED is not None and
      "m_activeTrade.direction    = GetDirectionForBlock(block);" in SEED)
check("there is exactly one writer of m_activeTrade.direction in the seed",'''

sd = rep(sd, SD_SEC2_OLD, SD_SEC2_NEW, 1, "sections 2-3: override retired")

SD_SEC456_OLD = '''# ----------------------------------------------------------------------
# 5. The physical path seeds in its two-argument (derive) form
# ----------------------------------------------------------------------
print("\\n-- Physical fill: two-argument seed --")

check("CheckPendingOrderFills located", FILLS is not None)
check("the physical seed keeps its two-argument form (DIR_NONE derive)",
      FILLS is not None and
      re.search(r"SeedActiveTradeFromBlock\\s*\\(\\s*blocks\\[i\\]\\s*,\\s*newTicket\\s*\\)"
                r"\\s*;", FILLS) is not None)
# v5.52: there is no touch increment left to order the seed against, so pin its
# absence. The seed must also remain a one-shot retirement (isTriggered) rather
# than a counter advance.
check("the retired touch increment is gone from the physical fill",
      FILLS is not None and "touches" not in FILLS)

# ----------------------------------------------------------------------
# 6. Call-site census: the chokepoint is the only way a basket is seeded
# ----------------------------------------------------------------------
print("\\n-- Seed call-site census --")

sites = len(re.findall(r"\\bSeedActiveTradeFromBlock\\s*\\(", OM_C))
check("exactly one definition + two call sites seed a basket",
      sites == 3, "matches=%d" % sites)

overrides = len(re.findall(r"SeedActiveTradeFromBlock\\s*\\([^);]*\\bseedDir\\b", OM_C))
check("exactly one call site passes an override (the virtual fire)",
      overrides == 1, "matches=%d" % overrides)'''

SD_SEC456_NEW = '''# ----------------------------------------------------------------------
# 4. The one seed call site passes no override
# ----------------------------------------------------------------------
print("\\n-- Physical fill: two-argument seed --")

check("CheckPendingOrderFills located", FILLS is not None)
check("the fill seed keeps its two-argument form",
      FILLS is not None and
      re.search(r"SeedActiveTradeFromBlock\\s*\\(\\s*blocks\\[i\\]\\s*,\\s*newTicket\\s*\\)"
                r"\\s*;", FILLS) is not None)
# v5.52: there is no touch increment left to order the seed against, so pin its
# absence. The seed must also remain a one-shot retirement (isTriggered) rather
# than a counter advance.
check("the retired touch increment is gone from the physical fill",
      FILLS is not None and "touches" not in FILLS)

# ----------------------------------------------------------------------
# 5. Call-site census: the chokepoint is the ONLY way a basket is seeded
# ----------------------------------------------------------------------
print("\\n-- Seed call-site census --")

sites = len(re.findall(r"\\bSeedActiveTradeFromBlock\\s*\\(", OM_C))
check("exactly one definition + ONE call site seed a basket",
      sites == 2, "matches=%d" % sites)

check("no call site passes an override",
      re.search(r"SeedActiveTradeFromBlock\\s*\\([^);]*\\bseedDir\\b", OM_C) is None)'''

sd = rep(sd, SD_SEC456_OLD, SD_SEC456_NEW, 1, "sections 4-5: census 3 -> 2")

save(os.path.join("_probe", "test_v548_seed_dir.py"), sd, sd_eol)
print("_probe/test_v548_seed_dir.py complete")


# ======================================================================
# 15. _probe/test_v557_frontrun.py
# ======================================================================
print("_probe/test_v557_frontrun.py")
frn, frn_eol = load(os.path.join("_probe", "test_v557_frontrun.py"))

FRN_ATR_OLD = '''for label, fn in (("PlaceOrArmOrder", OM_PLACE),
                  ("CheckVirtualTriggers", OM_VIRT),
                  ("OpenReversalPosition", OM_REV)):
    blob = norm(fn or "")
    check("%s prices from GetATRAtShift(1)" % label, SHIFT1 in blob)
    check("%s keeps GetATR() only as the fallback" % label, FALLBACK in blob)

check("exactly 3 shift-1 ATR reads order the manager's geometry",
      len(re.findall(r"GetATRAtShift\\(1\\)", OM_C)) == 3,
      "found %d" % len(re.findall(r"GetATRAtShift\\(1\\)", OM_C)))
check("exactly 3 matching GetATR() fallbacks",
      len(re.findall(r"atr <= 0\\) atr = m_blockManager\\.GetATR\\(\\)", OM_C)) == 3,
      "found %d" % len(re.findall(r"atr <= 0\\) atr = m_blockManager\\.GetATR\\(\\)", OM_C)))
check("the live entry path abandons the setup when no ATR is available",
      re.search(r"if\\(atr <= 0\\) return false;", OM_PLACE or "") is not None)
check("the reversal fallback is guarded by atr > 0, not a hard return",
      "if(atr > 0)" in (OM_REV or ""))
check("the virtual-fill recovery degrades to the trigger/stop distance",
      "MathAbs(vo.triggerPrice - vo.stopLoss)" in (OM_VIRT or ""))

# An extraction failure must surface as ONE named failure, not an obscure
# TypeError deep inside norm() -- a probe that crashes reports nothing.
check("every OrderManager price path resolved",
      all(f is not None for f in (OM_PLACE, OM_VIRT, OM_REV, OM_CALC, OM_SLD)))'''

FRN_ATR_NEW = '''# v5.72: the virtual fire was the third consumer of this pair and is retired,
# leaving PlaceOrArmOrder and OpenReversalPosition -- the two paths that still
# build a price. The census moves 3 -> 2 with it; the PROPERTY (both survivors
# read shift 1, with GetATR() only as a fallback) does not change.
for label, fn in (("PlaceOrArmOrder", OM_PLACE),
                  ("OpenReversalPosition", OM_REV)):
    blob = norm(fn or "")
    check("%s prices from GetATRAtShift(1)" % label, SHIFT1 in blob)
    check("%s keeps GetATR() only as the fallback" % label, FALLBACK in blob)

check("exactly 2 shift-1 ATR reads order the manager's geometry",
      len(re.findall(r"GetATRAtShift\\(1\\)", OM_C)) == 2,
      "found %d" % len(re.findall(r"GetATRAtShift\\(1\\)", OM_C)))
check("exactly 2 matching GetATR() fallbacks",
      len(re.findall(r"atr <= 0\\) atr = m_blockManager\\.GetATR\\(\\)", OM_C)) == 2,
      "found %d" % len(re.findall(r"atr <= 0\\) atr = m_blockManager\\.GetATR\\(\\)", OM_C)))
check("the live entry path abandons the setup when no ATR is available",
      re.search(r"if\\(atr <= 0\\) return false;", OM_PLACE or "") is not None)
check("the reversal fallback is guarded by atr > 0, not a hard return",
      "if(atr > 0)" in (OM_REV or ""))

# An extraction failure must surface as ONE named failure, not an obscure
# TypeError deep inside norm() -- a probe that crashes reports nothing.
check("every OrderManager price path resolved",
      all(f is not None for f in (OM_PLACE, OM_REV, OM_CALC, OM_SLD)))'''

frn = rep(frn, FRN_ATR_OLD, FRN_ATR_NEW, 1, "ATR census 3 -> 2")

save(os.path.join("_probe", "test_v557_frontrun.py"), frn, frn_eol)

FRN_CROSS_OLD = '''print("\\n-- 5. Near-side (limit) cross test --")

LIVE_CROSS = "isLong ? (ask > vo.triggerPrice) : (bid < vo.triggerPrice)"
RETIRED_CROSS = "isLong ? (ask < vo.triggerPrice) : (bid > vo.triggerPrice)"

check("the virtual engine fires on a pullback THROUGH the trigger",
      LIVE_CROSS in (OM_VIRT or ""))
check("the retired v5.66 breakout-follow predicate is gone",
      RETIRED_CROSS not in (OM_VIRT or "") and RETIRED_CROSS not in OM_C)
check("the cross is documented as a market condition",
      "THE CROSS TEST" in OM_RAW and
      "a gap through the level still fires" in OM_RAW)
# Only the entry-side comparison may be inverted; the exit-side must still
# read the opposite way or a fired order would seed its basket on the
# wrong trigger point.
check("exactly one cross predicate survives",
      len(re.findall(r"vo\\.triggerPrice\\)", OM_C)) >= 1,
      "found %d" % len(re.findall(r"vo\\.triggerPrice\\)", OM_C)))'''

FRN_CROSS_NEW = '''print("\\n-- 5. Limit-only entry: no cross predicate --")

RETIRED_CROSS = "isLong ? (ask < vo.triggerPrice) : (bid > vo.triggerPrice)"

# A cross predicate only exists if something has to WATCH price cross a level.
# v5.72 handed that job to the broker: a limit either rests and fills, or it is
# vetoed. So the property is no longer the SIGN of a comparison but its
# ABSENCE -- plus the geometrical consequence, that the entry price is snapped
# and normalized onto the grid it must sit on before the send.
check("no cross predicate survives (the virtual engine is retired)",
      "vo.triggerPrice" not in OM_C and "vo.stopLoss" not in OM_C)
check("the retired v5.66 breakout-follow predicate is gone",
      RETIRED_CROSS not in OM_C)
check("the entry price is snapped before the send",
      "double entryPrice = SnapToTick(CalcEntryPrice(block, atr));" in (OM_PLACE or ""))
check("the resting price is normalized to the symbol's digit grid",
      "request.price     = NormalizeDouble(entryPrice, "
      "(int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));" in (OM_PLACE or ""))'''

frn = rep(frn, FRN_CROSS_OLD, FRN_CROSS_NEW, 1, "cross test -> absence pin")

FRN_SLTP_OLD = '''PLACE_W = norm(OM_PLACE or "")
VIRT_W = norm(OM_VIRT or "")'''

FRN_SLTP_NEW = '''PLACE_W = norm(OM_PLACE or "")'''

frn = rep(frn, FRN_SLTP_OLD, FRN_SLTP_NEW, 1, "VIRT_W local removed")

FRN_ANCHOR_OLD = '''check("the entry is reclaimed from the virtual trigger, not recomputed",
      "block.localEntry = vo.triggerPrice;" in VIRT_W)
check("the stop is reclaimed from the armed virtual order",
      "block.localSL = vo.stopLoss;" in VIRT_W)
check("the virtual-fill path rebuilds rrUnit from the block's own geometry",
      "block.rrUnit = slDist;" in VIRT_W)
check("the reclaimed entry is normalized to the symbol's digit grid",
      "vo.triggerPrice = NormalizeDouble(entryPrice," in PLACE_W)
check("the basket 1R is the block's rrUnit, not the broker-adjusted stop",
      "block.rrUnit;" in OM_C and
      "m_activeTrade.rrUnit" in OM_C)'''

FRN_ANCHOR_NEW = '''check("the basket 1R is the block's rrUnit, not the broker-adjusted stop",
      "block.rrUnit;" in OM_C and
      "m_activeTrade.rrUnit" in OM_C)'''

frn = rep(frn, FRN_ANCHOR_OLD, FRN_ANCHOR_NEW, 1, "virtual reclaim pins removed")

FRN_TP_OLD = '''# The virtual-fire commit re-derives localTP on the SAME far-edge anchor as
# PlaceOrArmOrder; the retired form anchored it on the anticipation trigger, a
# further 0.5*ATR out, which would let the veto judge a different price after
# a virtual fire than it did while the order was armed.
check("CheckVirtualTriggers re-derives localTP on the far edge (twin of PlaceOrArmOrder)",
      "block.localTP = isLong ? farEdge + tpRR * slDist : farEdge - tpRR * slDist;" in VIRT_W and
      "double farEdge = isLong ? MathMax(block.top, block.bottom) : MathMin(block.top, block.bottom);" in VIRT_W)'''

FRN_TP_NEW = '''# The far-edge anchor is now asserted on the ONE surviving projection site.
# v5.72 retired the virtual fire, which re-derived localTP with identical
# arithmetic; PlaceOrArmOrder is the only writer of block.localTP left, so the
# "twin" property collapses into "the single writer is on the far edge".
check("PlaceOrArmOrder anchors the target on the FAR edge",
      re.search(r"double\\s+farEdge\\s*=\\s*isLong\\s*\\?\\s*MathMax\\(block\\.top,\\s*block\\.bottom\\)",
                PLACE_W) is not None and
      re.search(r"double\\s+takeProfit\\s*=\\s*isLong\\s*\\?\\s*farEdge\\s*\\+\\s*tpRR\\s*\\*\\s*slDist",
                PLACE_W) is not None and
      "block.localTP = takeProfit;" in PLACE_W)
check("exactly one writer of block.localTP remains in the OrderManager",
      len(re.findall(r"block\\.localTP\\s*=[^=]", OM_C)) == 1,
      "found %d" % len(re.findall(r"block\\.localTP\\s*=[^=]", OM_C)))'''

frn = rep(frn, FRN_TP_OLD, FRN_TP_NEW, 1, "localTP twin -> single writer")

save(os.path.join("_probe", "test_v557_frontrun.py"), frn, frn_eol)
print("_probe/test_v557_frontrun.py complete")

print("\nv5.72 patch applied: 5 sources, 15 probes.")
