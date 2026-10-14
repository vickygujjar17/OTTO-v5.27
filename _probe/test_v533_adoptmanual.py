"""
v5.33 static verification probe - MANUAL TRADE ADOPTION.

v5.33 lets the EA take over a position the OPERATOR opened by hand on its own
chart symbol (magic 0), and manage it with the whole basket machinery: unified
trail, milestone ladder, pyramid rungs, smart trim, DD halt and journal.

That feature touches three separate invariants at once, and every one of them
is a RUNTIME property the compiler gate cannot see:

  1. THE IDENTIFIER. Adoption must key on magic == 0 AND this symbol. Magic 0
     is issued only by the terminal's New Order dialog, so it means "the
     operator's own hand" and nothing else. If the magic test is dropped the
     EA adopts ANY position on the symbol, including a second EA's; if the
     symbol test is dropped it adopts a hand trade on an unrelated pair and
     then computes a 1R for a market it is not trading. Neither would fail a
     compile, and only the second is obvious in a log.

  2. THE ONE-BASKET GATE. Adoption runs on EVERY tick. Without the gates, a
     second manual leg (or an EA leg) joins a running basket, and the unified
     ratchet is one-way -- the damage cannot be undone on a later tick. The
     gate is the whole reason this is safe to leave enabled by default, so it
     is asserted on the LIVE source, not on a scaffold.

  3. THE MAGIC-SCOPED BLIND SPOTS. This is the subtle one. Everything in the
     EA that reads the terminal book filters on POSITION_MAGIC == MagicNumber:
     CountMyPositions(), FindActivePosition(), the High Table auditor's
     CountBookLegs()/BookHasTicket(), the orphan sweep. An adopted leg carries
     magic 0, so it is invisible to all of them BY CONSTRUCTION. Each of those
     sites therefore had to answer "is the adopted leg still mine?" some other
     way, and each answer is a different shape:
        * SyncActiveTrade()   - a new positional test (TrackedBasketStillOpen)
        * IsTrackedTicketOpen - the magic test made CONDITIONAL on a flag
        * the auditor         - the presence test swapped, not the alert muted
     A refactor that "simplifies" any of these back to the magic filter turns
     a healthy adopted trade into a phantom close, an orphaned leg, or a
     CRITICAL email every five seconds.

  4. THE FALSE CRITICAL. The auditor's book-vs-tracked test counts only
     m_magic legs, and an adopted primary is not one of them. Left alone the
     watchdog latches "state desync" for the entire life of the trade. The fix
     must be a SWAP of the presence test (a vanished adopted ticket still has
     to escalate), never a mute of the alert -- so both halves are asserted:
     the swap exists, and the magic-scoped counts were NOT widened.

  5. THE STOP IS MANDATORY. The basket's 1R is |entry - SL|. A stopless manual
     leg has no 1R to inherit, and inventing one fabricates the whole risk
     geometry. Refusal must be throttled, or the notice prints every tick.

  6. THE FLAG DIES WITH THE BASKET. m_adoptedManual left set after a close
     would exempt a FUTURE EA leg from the magic check and keep the auditor's
     phantom test standing down for a ticket that no longer exists.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
ORD = os.path.join(ROOT, "COttoOrderManager.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")
BUILD = os.path.join(ROOT, "_tools", "build_check.ps1")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
             "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


MAIN_T = read(MAIN)
DEFS_T = read(DEFS)
AUD_T = read(AUD)
ORD_T = read(ORD)
JRN_T = read(JRN)
BUILD_T = read(BUILD) if os.path.exists(BUILD) else ""

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def strip_comments(code):
    """Drop // comments, respecting string literals (see v5.32 probe)."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


def func_body(code, sig):
    """Return the { ... } block following the first line matching `sig`."""
    m = re.search(sig, code)
    if not m:
        return None
    i = code.find("{", m.end())
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[m.start():j + 1]
    return code[m.start():]


AUD_C = strip_comments(AUD_T)
MAIN_C = strip_comments(MAIN_T)
ORD_C = strip_comments(ORD_T)

ADOPT = func_body(ORD_C, r"ulong\s+AdoptManualPosition\s*\(\s*void\s*\)")
UPDATE = func_body(ORD_C, r"void\s+Update\s*\(\s*void\s*\)")
SYNC = func_body(ORD_C, r"void\s+SyncActiveTrade\s*\(\s*void\s*\)")
ISOPEN = func_body(ORD_C, r"bool\s+IsTrackedTicketOpen\s*\(\s*void\s*\)")
STILLOPEN = func_body(ORD_C, r"bool\s+TrackedBasketStillOpen\s*\(\s*void\s*\)")
INITBASKET = func_body(ORD_C, r"void\s+InitBasket\s*\(")
CLEARBASKET = func_body(ORD_C, r"void\s+ClearBasket\s*\(\s*void\s*\)")
SETTRACKED = func_body(AUD_C, r"void\s+SetTrackedLegs\s*\(")
AUDSTATE = func_body(AUD_C, r"void\s+AuditStateConsistency\s*\(\s*void\s*\)")
COUNTLegS = func_body(AUD_C, r"int\s+CountBookLegs\s*\(\s*void\s*\)\s*const")
HAS_TICKET = func_body(AUD_C, r"bool\s+BookHasTicket\s*\(")
ADOPTED_IN_BOOK = func_body(AUD_C, r"bool\s+AdoptedTicketInBook\s*\(")
PUSHFACTS = func_body(MAIN_C, r"void\s+HighTablePushFacts\s*\(\s*void\s*\)")
LOGADOPT = func_body(JRN_T, r"void\s+LogManualAdoption\s*\(")


# ----------------------------------------------------------------------
# 1. The identifier: magic 0 AND this symbol
# ----------------------------------------------------------------------
print("\n-- The adopted-leg identifier --")

check("AdoptManualPosition is defined in the order manager", ADOPT is not None)
check("the adoption body is real, not a stub",
      ADOPT is not None and len(ADOPT) > 2000,
      "body is %d chars" % (len(ADOPT) if ADOPT else 0))

if ADOPT is not None:
    # Both halves of the discriminator, in the same comparison block. A
    # hand-opened leg on ANOTHER symbol must not be adopted, and neither must
    # another EA's leg on THIS symbol.
    check("candidate legs are filtered to this symbol",
          "PositionGetString(POSITION_SYMBOL) != m_symbol" in ADOPT)
    check("candidate legs are filtered to magic 0",
          "PositionGetInteger(POSITION_MAGIC) != 0" in ADOPT)
    check("the magic filter is an equality against 0, not 'not ours'",
          re.search(r"POSITION_MAGIC\)\s*!=\s*0\s*\)", ADOPT) is not None and
          "MagicNumber" not in ADOPT)
    check("symbol and magic are both tested on the SAME candidate loop",
          ADOPT.find("POSITION_SYMBOL") < ADOPT.find("POSITION_MAGIC"))
    # Oldest-wins with a tie-break, matching FindActivePosition(): the first
    # manual entry is the one whose stop defines the trade.
    check("adoption takes the OLDEST manual leg",
          "POSITION_TIME" in ADOPT and ADOPT.count("< oldest") == 1)
    check("the scan starts from a null ticket",
          re.search(r"ulong\s+adoptedTicket\s*=\s*0\s*;", ADOPT) is not None)
    check("an empty book returns 0 rather than adopting index 0",
          re.search(r"if\s*\(\s*adoptedTicket\s*==\s*0\s*\)\s*return\s+0\s*;",
                    ADOPT) is not None)
    check("the scan uses PositionsTotal()",
          "PositionsTotal()" in ADOPT)
    # Select-by-ticket before reading fields: PositionGetTicket(i) can return a
    # ticket that is stale by the time the getters run on a fast book.
    check("every candidate is selected before it is read",
          "PositionSelectByTicket(PositionGetTicket(i))" in ADOPT)

    # ------------------------------------------------------------------
    # 2. The one-basket gate. Runs every tick, so every refusal matters.
    # ------------------------------------------------------------------
    print("\n-- The one-basket gate --")

    GATES = [
        ("the feature is opt-in", r"if\s*\(\s*!\s*InpAdoptManualTrades\s*\)"),
        ("a reversal in flight is refused", r"if\s*\(\s*m_reversalInProgress\s*\)"),
        ("an active EA trade is refused", r"if\s*\(\s*m_hasActiveTrade\s*\)"),
        ("a live basket is refused", r"if\s*\(\s*m_basketCount\s*>\s*0\s*\)"),
        ("open EA positions are refused", r"if\s*\(\s*CountMyPositions\(\)\s*>\s*0\s*\)"),
        ("resting EA limit orders are refused",
         r"if\s*\(\s*CountMyPendingOrders\(\)\s*>\s*0\s*\)"),
        ("the news shield is honored", r"if\s*\(\s*InpSimNewsShield\s*\)"),
        ("the macro veto is honored", r"if\s*\(\s*InpSimMacroVeto\s*\)"),
    ]
    for label, pat in GATES:
        check("gate: %s" % label, re.search(pat, ADOPT) is not None)

    # Every gate must return 0 (a refusal), never a partial adopt. A gate that
    # merely skips a field would leave m_hasActiveTrade half-seeded.
    for label, pat in GATES:
        m = re.search(pat, ADOPT)
        if m:
            tail = ADOPT[m.end():m.end() + 60]
            check("gate '%s' returns 0" % label,
                  re.search(r"^\s*return\s+0\s*;", tail) is not None,
                  repr(tail[:40]))

    # The gate that matters most comes BEFORE the book scan: the scan is pure
    # waste when a basket is already running, and more importantly a future
    # reader must not be able to reorder "find a leg" ahead of "am I free?".
    if ADOPT is not None:
        scan_at = ADOPT.find("PositionsTotal()")
        gate_at = ADOPT.find("CountMyPositions()")
        check("the one-basket gate precedes the book scan",
              0 < gate_at < scan_at,
              "gate@%d scan@%d" % (gate_at, scan_at))

    # ------------------------------------------------------------------
    # 3. The stop is mandatory and the notice is throttled
    # ------------------------------------------------------------------
    print("\n-- Mandatory stop-loss + throttle --")

    check("a missing/zero stop refuses the adoption",
          re.search(r"sl\s*<=\s*0\.0", ADOPT) is not None)
    check("a zero-width stop (SL == entry) also refuses",
          "MathAbs(entry - sl) <= 0.0" in ADOPT)
    check("1R is the live stop distance",
          "MathAbs(entry - sl)" in ADOPT)
    check("the refusal happens BEFORE the state is seeded",
          ADOPT.find("sl <= 0.0") < ADOPT.find("m_hasActiveTrade  = true"),
          "check@%d seed@%d" % (ADOPT.find("sl <= 0.0"),
                                ADOPT.find("m_hasActiveTrade  = true")))
    # Unthrottled, a stopless manual leg prints on every tick.
    check("the no-stop notice is throttled",
          "m_manualNoSLWarnTick" in ADOPT and
          re.search(r"InpManualNoSLWarnMinutes", ADOPT) is not None)
    check("the throttle stamp advances only when it prints",
          ADOPT.count("m_manualNoSLWarnTick = TimeCurrent();") == 1 and
          re.search(r"if\s*\(\s*TimeCurrent\(\)\s*-\s*m_manualNoSLWarnTick\s*>=",
                    ADOPT) is not None)
    # A non-positive input must not disable the notice entirely: the fallback
    # keeps it at its historical default instead of turning it into a
    # per-tick flood or a permanent silence.
    check("a non-positive cadence falls back rather than flooding or silencing",
          re.search(r"InpManualNoSLWarnMinutes\s*>\s*0\s*\)\s*\?\s*"
                    r"InpManualNoSLWarnMinutes\s*:\s*\d+", ADOPT) is not None)
    check("every refusal path returns 0",
          ADOPT.count("return 0;") >= 8,
          "found %d" % ADOPT.count("return 0;"))
    check("the successful path returns the adopted ticket",
          re.search(r"return\s+adoptedTicket\s*;", ADOPT) is not None)

    # ------------------------------------------------------------------
    # 4. The seed: every field the basket geometry depends on
    # ------------------------------------------------------------------
    print("\n-- The seeded state --")

    for field in ("ticket", "direction", "entryPrice", "initialSL",
                  "initialSLDistance", "rrUnit", "currentTrailSL", "lotSize",
                  "openTime", "trailStep", "sourceBlockSerial",
                  "highestPriceSinceEntry", "adoptedManual"):
        check("the seed sets %s" % field,
              re.search(r"m_activeTrade\.%s\s*=" % field, ADOPT) is not None)

    check("the seed takes direction from POSITION_TYPE",
          "POSITION_TYPE_BUY" in ADOPT and "DIR_LONG" in ADOPT)
    check("the seed takes the entry from the live book",
          "POSITION_PRICE_OPEN" in ADOPT)
    check("the seed takes the lot from the live book",
          "POSITION_VOLUME" in ADOPT)
    check("the seed marks the record as adopted",
          re.search(r"m_activeTrade\.adoptedManual\s*=\s*true\s*;", ADOPT) is not None)
    check("the seed raises the manager-level adoption flag",
          re.search(r"m_adoptedManual\s*=\s*true\s*;", ADOPT) is not None)
    check("no Pine block is claimed for a manual leg",
          re.search(r"m_activeTrade\.sourceBlockSerial\s*=\s*0\s*;", ADOPT) is not None)
    check("risk is recomputed before the trade is published",
          ADOPT.find("ComputeRiskAmount(") <
          ADOPT.find("m_hasActiveTrade", ADOPT.find("ComputeRiskAmount(")))
    check("the direction is mirrored onto m_activeDirection",
          "m_activeDirection = dir" in ADOPT)
    check("the trader start price is seeded from the correct side",
          re.search(r"highestPriceSinceEntry\s*=\s*\(dir\s*==\s*DIR_LONG\)\s*\?"
                    r"\s*GetBid\(\)\s*:\s*GetAsk\(\)", ADOPT) is not None)

    # ------------------------------------------------------------------
    # 5. The basket is seeded, not re-seeded
    # ------------------------------------------------------------------
    print("\n-- Basket seeding --")

    check("the seed calls InitBasket", "InitBasket(" in ADOPT)
    check("the adopted ticket is the basket's primary",
          re.search(r"InitBasket\([^;]*?adoptedTicket", ADOPT, re.S) is not None)
    # "MAN" names the origin in the session id so an adopted basket's journal
    # file is identifiable, and it is passed as the LAST argument while the
    # block serial stays 0 -- there is no Pine block behind a manual leg.
    check("the session id is minted with the MAN origin token",
          re.search(r',\s*0\s*,\s*"MAN"\s*\)', ADOPT) is not None)
    check("the 1R handed to InitBasket is the live stop distance",
          re.search(r"InitBasket\([^;]*?m_activeTrade\.rrUnit", ADOPT, re.S) is not None)
    check("the lot handed to InitBasket is the live book volume",
          re.search(r"InitBasket\([^;]*?m_activeTrade\.lotSize", ADOPT, re.S) is not None)
    check("the journal records the adoption",
          "LogManualAdoption(" in ADOPT)
    check("the journal session id is set before the adoption is logged",
          0 <= ADOPT.find("SetSessionID(m_sessionID)") < ADOPT.find("LogManualAdoption("))
    check("the adoption is announced on the console",
          "ADOPTED manual position" in ADOPT)


# ----------------------------------------------------------------------
# 6. The magic-scoped blind spots. Unconditional - these are NOT inside
#    the ADOPT block, because a refactor that deletes the adoption path
#    entirely must still be caught by its damage to the shared helpers.
# ----------------------------------------------------------------------
print("\n-- Magic-scoped blind spots --")

# (a) IsTrackedTicketOpen(): the magic test becomes conditional.
check("IsTrackedTicketOpen is defined", ISOPEN is not None)
if ISOPEN is not None:
    check("the magic check is skipped for an adopted primary",
          re.search(r"if\s*\(\s*m_adoptedManual\s*\|\|\s*"
                    r"m_activeTrade\.adoptedManual\s*\)", ISOPEN) is not None)
    check("it still requires a real ticket",
          re.search(r"m_activeTrade\.ticket\s*<=\s*0", ISOPEN) is not None)
    check("it still requires the record to be active",
          re.search(r"if\s*\(\s*!\s*m_hasActiveTrade", ISOPEN) is not None)
    check("it still requires THIS symbol",
          "POSITION_SYMBOL) != m_symbol" in ISOPEN)
    check("the unconditional magic test survives on the EA path",
          re.search(r"return\s*\(PositionGetInteger\(POSITION_MAGIC\)\s*==\s*"
                    r"MagicNumber\s*\)", ISOPEN) is not None)
    # The exemption has to be an early RETURN. Falling through to the magic
    # test would still fail for a magic-0 leg, and skipping the whole body
    # with a `continue`-style guard is not available here.
    check("the adopted exemption is an early return, ordered before the magic test",
          0 <= ISOPEN.find("m_adoptedManual") < ISOPEN.find("MagicNumber"))

# (b) SyncActiveTrade(): the ghost-remnant branch must see an adopted leg.
check("SyncActiveTrade is defined", SYNC is not None)
if SYNC is not None:
    check("the remnant branch is widened to the tracked basket",
          re.search(r"if\s*\(\s*m_hasActiveTrade\s*&&\s*\(\s*CountMyPositions\(\)"
                    r"\s*>\s*0\s*\|\|\s*TrackedBasketStillOpen\(\)\s*\)\s*\)",
                    SYNC) is not None)
    check("the remnant branch still prefers the magic-scoped count first",
          SYNC.find("CountMyPositions()") < SYNC.find("TrackedBasketStillOpen()"))
    check("the remnant branch closes rather than re-seeding",
          "CloseEntireBasket(" in SYNC)
    check("the incumbent guard still runs first",
          0 < SYNC.find("IsTrackedTicketOpen()") < SYNC.find("TrackedBasketStillOpen()"))

# (c) TrackedBasketStillOpen(): the positional test itself.
check("TrackedBasketStillOpen is defined", STILLOPEN is not None)
if STILLOPEN is not None:
    check("it scans the tracked basket array",
          "m_basketCount" in STILLOPEN and "m_basket[i].ticket" in STILLOPEN)
    check("it skips non-positive ticket slots",
          re.search(r"m_basket\[i\]\.ticket\s*<=\s*0\s*\)\s*continue", STILLOPEN)
          is not None)
    check("it re-checks the symbol on every candidate",
          "POSITION_SYMBOL) != m_symbol" in STILLOPEN)
    check("it selects by ticket, not by magic",
          "PositionSelectByTicket(m_basket[i].ticket)" in STILLOPEN and
          "MagicNumber" not in STILLOPEN)
    check("it falls through to false for an empty basket",
          re.search(r"return\s+false\s*;", STILLOPEN) is not None)
    check("it returns true on the first live leg",
          re.search(r"return\s+true\s*;", STILLOPEN) is not None)

# (d) The close path must need no widening: the basket array IS the ownership
# proof. Step 1 closes tracked tickets with no magic filter, and the orphan
# sweep (step 2) must STAY magic-scoped or a force-close would start sweeping
# the operator's unrelated hand trades off the symbol.
CLOSE = func_body(ORD_C, r"void\s+CloseEntireBasket\s*\(")
check("CloseEntireBasket is defined", CLOSE is not None)
if CLOSE is not None:
    # Split on the orphan sweep's loop, which is CODE. Splitting on the
    # "Orphan sweep" banner would not work here: ORD_C is comment-stripped, so
    # the whole body would count as step 1 and step 2's magic filter would fail
    # the very assertion that guards it.
    SWEEP = "for(int p = PositionsTotal() - 1; p >= 0; p--)"
    check("the orphan sweep is still a separate second pass",
          CLOSE.count(SWEEP) == 1)
    step1 = CLOSE.split(SWEEP)[0]
    check("step 1 closes tracked tickets with NO magic filter",
          "POSITION_MAGIC" not in step1 and "MagicNumber" not in step1)
    check("step 1 addresses each leg by ticket",
          "PositionSelectByTicket(bt)" in step1 and "ClosePosition(bt)" in step1)
    check("step 1 skips empty basket slots", "if(bt <= 0) continue;" in step1)
    check("the orphan sweep STAYS magic-scoped",
          "POSITION_MAGIC) != MagicNumber" in CLOSE)
    check("the keep-ticket is spared in the tracked loop",
          "keepTicket > 0 && bt == keepTicket" in CLOSE)
    check("the keep-ticket is spared in the orphan sweep",
          "keepTicket > 0 && orphan == keepTicket" in CLOSE)
    # The early return must also consider the adopted case: m_hasActiveTrade is
    # true for an adopted basket, so this holds, but assert it is still keyed
    # on trade state rather than on a magic-scoped count alone.
    check("the early return is not a bare magic-scoped count",
          re.search(r"if\s*\(\s*!\s*m_hasActiveTrade\s*\|\|\s*m_basketCount\s*==\s*0",
                    CLOSE) is not None or "m_hasActiveTrade" in CLOSE)

# (e) The gates themselves must NOT have been widened. FindActivePosition and
# CountMyPositions answer "any of OUR legs"; teaching them magic 0 would make
# them answer "any of the operator's hand trades", which would then be counted
# as tranches, re-seeded as primaries and swept as orphans.
FIND = func_body(ORD_C, r"bool\s+FindActivePosition\s*\(")
check("FindActivePosition is defined", FIND is not None)
if FIND is not None:
    check("FindActivePosition still demands our magic",
          "POSITION_MAGIC) != MagicNumber" in FIND)
    check("FindActivePosition still demands this symbol",
          "POSITION_SYMBOL) != m_symbol" in FIND)
    check("FindActivePosition was not given an adoption escape hatch",
          "m_adoptedManual" not in FIND and "adoptedManual" not in FIND)

COUNT = func_body(ORD_C, r"int\s+CountMyPositions\s*\(\s*void\s*\)")
check("CountMyPositions is defined", COUNT is not None)
if COUNT is not None:
    check("CountMyPositions still demands our magic",
          "POSITION_MAGIC) == MagicNumber" in COUNT)
    check("CountMyPositions was not given an adoption escape hatch",
          "m_adoptedManual" not in COUNT)

PENDING = func_body(ORD_C, r"int\s+CountMyPendingOrders\s*\(\s*void\s*\)")
check("CountMyPendingOrders is defined", PENDING is not None)
if PENDING is not None:
    check("CountMyPendingOrders stays magic-scoped",
          "ORDER_MAGIC) != MagicNumber" in PENDING or
          "ORDER_MAGIC) == MagicNumber" in PENDING)

# ----------------------------------------------------------------------
# 7. The High Table auditor: a false CRITICAL must not fire on a healthy
#    adopted trade, and a real phantom must still escalate.
# ----------------------------------------------------------------------
print("\n-- High Table auditor --")

# (a) The magic-agnostic presence test.
check("AdoptedTicketInBook is defined", ADOPTED_IN_BOOK is not None)
if ADOPTED_IN_BOOK is not None:
    check("it rejects a null ticket",
          re.search(r"if\s*\(\s*ticket\s*==\s*0\s*\)\s*return\s+false\s*;",
                    ADOPTED_IN_BOOK) is not None)
    check("it matches on the exact ticket",
          "PositionGetTicket(idx) != ticket" in ADOPTED_IN_BOOK)
    check("it still requires this symbol",
          "POSITION_SYMBOL) != m_symbol" in ADOPTED_IN_BOOK)
    check("it deliberately drops the magic filter",
          "MAGIC" not in ADOPTED_IN_BOOK and "m_magic" not in ADOPTED_IN_BOOK)
    check("it is a read-only scan",
          "CTrade" not in ADOPTED_IN_BOOK and "PositionModify" not in ADOPTED_IN_BOOK)

# (b) The magic-scoped counts must NOT have been widened. Their alert text
# names m_magic, so folding magic-0 legs in would silently redefine what the
# message claims to measure.
check("CountBookLegs is defined", COUNTLegS is not None)
if COUNTLegS is not None:
    check("CountBookLegs stays magic-scoped",
          "POSITION_MAGIC) != (long)m_magic" in COUNTLegS)
    check("CountBookLegs was not widened for adoption",
          "adopted" not in COUNTLegS and "Adopted" not in COUNTLegS)

check("BookHasTicket is defined", HAS_TICKET is not None)
if HAS_TICKET is not None:
    check("BookHasTicket stays magic-scoped",
          "POSITION_MAGIC) != (long)m_magic" in HAS_TICKET)
    check("BookHasTicket was not widened for adoption",
          "adopted" not in HAS_TICKET and "Adopted" not in HAS_TICKET)

check("the two presence tests are distinct implementations",
      ADOPTED_IN_BOOK is not None and HAS_TICKET is not None and
      ADOPTED_IN_BOOK != HAS_TICKET)

# (c) SetTrackedLegs carries the flag, and it is OPTIONAL so the v5.32
# caller contract is unchanged.
check("SetTrackedLegs is defined", SETTRACKED is not None)
if SETTRACKED is not None:
    check("SetTrackedLegs takes the adoption flag",
          "bool adoptedManual" in SETTRACKED)
    check("the adoption flag defaults to false so v5.32 callers still compile",
          re.search(r"bool\s+adoptedManual\s*=\s*false", SETTRACKED) is not None)
    check("the flag is stored for the audit cycle to read",
          "m_trackedAdopted = adoptedManual;" in SETTRACKED)
    check("the primary ticket is still stored",
          "m_trackedPrimary = primaryTicket;" in SETTRACKED)
    check("the leg count is still stored",
          "m_trackedLegs    = legs;" in SETTRACKED)
    check("the active flag is still stored",
          "m_trackedActive  = active;" in SETTRACKED)
    # The INFO is latched so a long-lived adopted position does not email on
    # every cycle, and RE-ARMED when the trade stops being an adoption.
    check("the adoption INFO is latched, not repeated every cycle",
          re.search(r"if\s*\(\s*adoptedManual\s*&&\s*!\s*m_adoptionSeen\s*\)",
                    SETTRACKED) is not None and
          "m_adoptionSeen = true;" in SETTRACKED)
    check("the INFO latch is re-armed for the next adoption",
          re.search(r"else\s+if\s*\(\s*!\s*adoptedManual\s*\)\s*\n\s*"
                    r"m_adoptionSeen\s*=\s*false\s*;", SETTRACKED) is not None)
    check("the INFO names the suppression so the audit trail explains itself",
          "ADOPTED manual" in SETTRACKED and "suppressed" in SETTRACKED)

# (d) The phantom test itself. The FIX IS THE SWAP: the alert is not muted,
# the presence TEST is exchanged, so a vanished adopted ticket still escalates
# through the same two-cycle confirmation and the same latch.
check("AuditStateConsistency is defined", AUDSTATE is not None)
if AUDSTATE is not None:
    check("the adopted flag is read from the pushed state",
          re.search(r"bool\s+adopted\s*=\s*m_trackedAdopted\s*;", AUDSTATE) is not None)
    check("the presence test is SWAPPED, not short-circuited",
          re.search(r"bool\s+present\s*=\s*adopted\s*\?\s*"
                    r"AdoptedTicketInBook\(m_trackedPrimary\)\s*\n?\s*:\s*"
                    r"BookHasTicket\(m_trackedPrimary\)\s*;", AUDSTATE) is not None)
    check("the magic-scoped test is still the DEFAULT branch",
          AUDSTATE.find("BookHasTicket(m_trackedPrimary)") >
          AUDSTATE.find("AdoptedTicketInBook(m_trackedPrimary)"))
    check("the phantom still requires an active, non-null tracked primary",
          re.search(r"bool\s+phantom\s*=\s*m_trackedActive\s*&&\s*"
                    r"m_trackedPrimary\s*>\s*0\s*&&", AUDSTATE) is not None)
    check("the phantom still keys on the presence result",
          "!present;" in AUDSTATE)
    check("a vanished adopted ticket still escalates",
          "DispatchAlertOnce(m_alertSent_StateInconsistency," in AUDSTATE)
    check("the adopted case does not mute the alert",
          "adopted" not in AUDSTATE.split("if(!phantom)")[0] or
          "bool adopted" in AUDSTATE.split("if(!phantom)")[0])
    check("the alert text is only a NOTE appended to the evidence",
          "NOTE: the tracked primary was ADOPTED" in AUDSTATE)
    check("the two-cycle confirmation survives",
          re.search(r"if\s*\(\s*!\s*m_stateSkewSeen\s*\)", AUDSTATE) is not None)
    check("the healthy path still re-arms the latch and the skew flag",
          "ClearLatch(m_alertSent_StateInconsistency);" in AUDSTATE and
          "m_stateSkewSeen = false;" in AUDSTATE)
    check("the healthy path RETURNS rather than falling through to the alert",
          re.search(r"ClearLatch\(m_alertSent_StateInconsistency\);\s*\n\s*"
                    r"m_stateSkewSeen\s*=\s*false\s*;\s*\n\s*return\s*;", AUDSTATE)
          is not None)
    # The evidence line names WHY the counts may disagree, so an operator
    # reading the CRITICAL is not sent hunting a bug in the adoption path.
    check("the alert evidence explains the adopted case",
          "ADOPTED" in AUDSTATE)
    check("the alert still names the magic-scoped book total",
          "CountBookLegs()" in AUDSTATE and "IntegerToString(book)" in AUDSTATE)
    check("the auditor never mutates basket state",
          "CTrade" not in AUD_C and "PositionModify" not in AUD_C and
          "OrderSend" not in AUD_C)


# ----------------------------------------------------------------------
# 8. The plumbing: otto.mq5 pushes the flag through to the auditor
# ----------------------------------------------------------------------
print("\n-- The fact ingress --")

check("HighTablePushFacts is defined", PUSHFACTS is not None)
if PUSHFACTS is not None:
    check("the ingress reads the order layer's active record",
          "GetActiveTradeRef(" in PUSHFACTS)
    check("the adopted flag is taken from that record",
          re.search(r"adopted\s*=\s*active\.adoptedManual\s*;", PUSHFACTS) is not None)
    check("the flag is declared, not assumed",
          re.search(r"bool\s+adopted\s*=\s*false\s*;", PUSHFACTS) is not None)
    # SetTrackedLegs' 4th argument is the adoption flag. The ticket pushed
    # alongside it must still be the order layer's belief, not a book scan.
    check("the flag is the fourth argument to SetTrackedLegs",
          re.search(r"SetTrackedLegs\([^;]*?adopted\s*\)", PUSHFACTS, re.S)
          is not None)
    check("the ticket pushed is the order layer's own count",
          re.search(r"SetTrackedLegs\(\s*g_orderManager\.CountOpenPositions\(\)\s*,"
                    r"\s*ticket\s*,", PUSHFACTS) is not None)

# The flag must reach otto.mq5 through the record, not through a second scan:
# a book scan there would re-derive magic 0 and reintroduce the false positive.
check("otto.mq5 does not scan the book for magic 0",
      "POSITION_MAGIC) != 0" not in MAIN_C)


# ----------------------------------------------------------------------
# 9. Lifecycle: the flag must die with the basket
# ----------------------------------------------------------------------
print("\n-- Adoption lifecycle --")

# Four transitions clear the state: ClearBasket(), the Initialize() reset, the
# reversal teardown, and a fresh EA seed that replaces an adopted trade.
check("ClearBasket is defined", CLEARBASKET is not None)
if CLEARBASKET is not None:
    check("ClearBasket lowers the record's adoption flag",
          re.search(r"m_activeTrade\.adoptedManual\s*=\s*false\s*;", CLEARBASKET)
          is not None)
    check("ClearBasket lowers the manager's adoption flag",
          re.search(r"^\s*m_adoptedManual\s*=\s*false\s*;", CLEARBASKET, re.M)
          is not None)
    # Both must happen: a stale flag on the record would let
    # IsTrackedTicketOpen() exempt a FUTURE EA leg from the magic test, and a
    # stale manager flag would keep the auditor standing down for a ticket that
    # no longer exists.
    check("both flags are lowered",
          CLEARBASKET.count("adoptedManual = false;") >= 2,
          "found %d" % CLEARBASKET.count("adoptedManual = false;"))
    check("the basket is emptied before the flags are lowered",
          CLEARBASKET.find("m_basketCount = 0;") <
          CLEARBASKET.find("m_activeTrade.adoptedManual = false;"))
    check("ClearBasket still clears the persisted 1R",
          "ClearBasketR()" in CLEARBASKET)

INIT = func_body(ORD_C, r"bool\s+Initialize\s*\(")
check("Initialize is defined", INIT is not None)
if INIT is not None:
    check("Initialize starts adoption state cold",
          re.search(r"m_adoptedManual\s*=\s*false\s*;", INIT) is not None)
    check("Initialize clears the warn throttle too",
          re.search(r"m_manualNoSLWarnTick\s*=\s*0\s*;", INIT) is not None)
    check("the cold reset precedes the sync",
          0 <= INIT.find("m_adoptedManual") < INIT.find("SyncActiveTrade()"))

# An EA trade seeded over an adopted one must not inherit the exemption: the
# struct is reused across baskets on the same instance, so a leftover true
# would let a normal trade skip the magic test and stand the auditor down.
SEED = func_body(ORD_C, r"void\s+SeedActiveTradeFromBlock\s*\(")
check("SeedActiveTradeFromBlock is defined", SEED is not None)
if SEED is not None:
    check("an EA seed clears the record's adoption flag",
          re.search(r"m_activeTrade\.adoptedManual\s*=\s*false\s*;", SEED) is not None)
    check("an EA seed clears the manager's adoption flag",
          re.search(r"m_adoptedManual\s*=\s*false\s*;", SEED) is not None)
    check("the EA seed clears both flags explicitly, not via ZeroMemory alone",
          SEED.count("adoptedManual") >= 2 and "ZeroMemory" not in SEED)
    check("the seed still records the originating Pine block",
          "m_activeTrade.sourceBlockSerial = block.serial;" in SEED)
    check("the seed passes the block serial on to InitBasket",
          "block.serial)" in SEED)

# The reversal path tears the basket down through CloseEntireBasket(logExit=true),
# which lands in ClearBasket() -- so the flags die with the basket there rather
# than being cleared a second time in the reversal code itself.
REV = func_body(ORD_C, r"bool\s+InitiateReversal\s*\(")
check("InitiateReversal is defined", REV is not None)
if REV is not None:
    check("the reversal closes the whole basket with logging on",
          re.search(r'CloseEntireBasket\(\s*"SAR Reversal"\s*,\s*true\s*\)', REV)
          is not None)
    check("the reversal does not re-implement the teardown itself",
          "m_basketCount = 0;" not in REV)

# ClearBasket() is reached from the close path, so the flags are cleared on
# every exit including an adopted one.
check("the close path lands in ClearBasket when the exit is logged",
      CLOSE is not None and
      re.search(r"if\s*\(\s*logExit\s*\)\s*\n\s*ClearBasket\(\)\s*;", CLOSE)
      is not None)


# ----------------------------------------------------------------------
# 10. The journal record and the Update wiring
# ----------------------------------------------------------------------
print("\n-- Journal record + Update wiring --")

check("LogManualAdoption is defined", LOGADOPT is not None)
if LOGADOPT is not None:
    check("the record is guarded on the journal being ready",
          re.search(r"if\s*\(\s*!\s*m_ready\s*\)\s*return\s*;", LOGADOPT) is not None)
    check("the record opens the session file",
          "OpenAppend()" in LOGADOPT)
    check("the record creates the file if the session has not written yet",
          "OpenWrite()" in LOGADOPT)
    check("the record closes the handle",
          "CloseHandle();" in LOGADOPT)
    check("the record names the magic-0 origin",
          "magic 0" in LOGADOPT)
    check("the record prints the ticket, direction, entry and stop",
          "Ticket" in LOGADOPT and "Direction" in LOGADOPT and
          "Entry Price" in LOGADOPT and "Working Stop" in LOGADOPT)
    check("the record prints the derived 1R",
          "Initial Risk (1R)" in LOGADOPT)
    check("the 1R is derived from the SAME stop distance the manager seeded",
          re.search(r"Initial Risk \(1R\)\s*:\s*\"\s*\+\s*FmtPrice\(\s*slDistance\s*\)",
                    LOGADOPT) is not None)
    check("the 1R line is not repriced off the derived pip figure",
          not re.search(r"Initial Risk \(1R\)\s*:\s*\"\s*\+\s*FmtPrice\(\s*slPips\s*\)",
                        LOGADOPT))
    check("the pip figure is only derived behind a positive point-size guard",
          re.search(r"slPips\s*=\s*\(\s*pt\s*>\s*0\.0\s*\)\s*\?", LOGADOPT) is not None and
          LOGADOPT.find("slPips") < LOGADOPT.find("Initial Risk (1R)"))
    check("the derived 1R rides on the slDistance the caller was handed",
          re.search(r"FmtPrice\(\s*slDistance\s*\)\s*\+", LOGADOPT) is not None)
    check("the record states that the full basket machinery now applies",
          "DD halt" in LOGADOPT and "close all now apply" in LOGADOPT)
    check("the record does not fabricate a Pine block",
          "SSniperBlock" not in LOGADOPT)

# AdoptManualPosition() must run from Update(), LAST, so an EA fill detected in
# the same tick becomes the primary first and the one-basket gate then refuses.
check("Update is defined", UPDATE is not None)
if UPDATE is not None:
    check("Update calls the adoption sweep",
          re.search(r"^\s*AdoptManualPosition\(\)\s*;", UPDATE, re.M) is not None)
    check("the adoption sweep runs LAST in Update",
          UPDATE.find("AdoptManualPosition();") >
          UPDATE.find("CheckPendingOrderFills();") and
          UPDATE.find("AdoptManualPosition();") >
          UPDATE.find("CompleteReversal();"))
    check("the reversal is completed before the adoption sweep",
          UPDATE.find("CompleteReversal();") < UPDATE.find("AdoptManualPosition();"))
    check("Update calls the adoption sweep exactly once",
          len(re.findall(r"AdoptManualPosition\(\)\s*;", UPDATE)) == 1)
    check("Update does not adopt a second time from elsewhere in the file",
          len(re.findall(r"(?<!ulong\s{22})^\s*AdoptManualPosition\(\)\s*;", ORD_C,
                         re.M)) == 1)


# ----------------------------------------------------------------------
# 11. Inputs, struct field and version stamps
# ----------------------------------------------------------------------
print("\n-- Inputs + release stamps --")

check("the adoption input exists",
      re.search(r"input\s+bool\s+InpAdoptManualTrades\s*=\s*true\s*;", DEFS_T) is not None)
check("the adoption input is a plain bool input, not a macro",
      re.search(r"^[^/\n]*input\s+bool\s+InpAdoptManualTrades", DEFS_T, re.M) is not None)
check("the warn cadence input exists",
      re.search(r"input\s+int\s+InpManualNoSLWarnMinutes\s*=\s*5\s*;", DEFS_T) is not None)
# Defaults are the shipped behaviour: adoption ON (so the operator's hand trade
# is protected without configuring anything) and a 5-minute warn cadence.
check("adoption ships ENABLED by default",
      re.search(r"InpAdoptManualTrades\s*=\s*true", DEFS_T) is not None)
check("the warn cadence ships at 5 minutes",
      re.search(r"InpManualNoSLWarnMinutes\s*=\s*5\s*;", DEFS_T) is not None)

# The record field the whole feature hangs off.
TRADE_STRUCT = func_body(DEFS_T, r"struct\s+SActiveTrade")
check("SActiveTrade is defined", TRADE_STRUCT is not None)
if TRADE_STRUCT is not None:
    check("SActiveTrade carries the adoption flag",
          re.search(r"bool\s+adoptedManual\s*;", TRADE_STRUCT) is not None)
    check("the flag is documented as the auditor's input",
          "adopted" in TRADE_STRUCT)
    check("the struct still carries the fields the seed writes",
          all(f in TRADE_STRUCT for f in
              ("ticket", "direction", "entryPrice", "initialSL",
               "rrUnit", "currentTrailSL", "sourceBlockSerial")))

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.33 or later", parts >= (5, 33), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in
           read(os.path.join(ROOT, f))]
check("all %d sources stamp v%s" % (len(ALL_FILES), RELEASE), not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)
check("Pine port banner names the current release",
      ("Master Build Port (v%s)" % RELEASE) in MAIN_T)
check("the MANUAL TRADE ADOPTION input group names the current release",
      ("[11] MANUAL TRADE ADOPTION \u2014 v%s" % RELEASE) in DEFS_T)
check("the versioned engine group labels were bumped with the release",
      ("[9] CURRENCY VECTOR & AFFINITY ENGINE \u2014 v%s" % RELEASE) in DEFS_T and
      ("[10] HIGH TABLE AUDITOR \u2014 v%s" % RELEASE) in DEFS_T)

# Encoding contract: the adopted-basket telegraph is a plain-ASCII "-MAN" tail,
# so no source needs a new non-ASCII glyph -- and the auditor is ASCII-only.
check("CHighTableAuditor.mqh is still pure ASCII",
      all(ord(ch) < 128 for ch in AUD_T))
# The adoption teardown token ("-MAN" / "MAN") is plain ASCII, so every line
# v5.33 adds outside OttoDefines.mqh must be ASCII-clean. Assert on the added
# lines themselves rather than on the whole file, whose non-ASCII bank is
# inherited from earlier releases.
ADDED_SIGNS = ("AdoptManualPosition", "adoptedManual", "m_adoptedManual",
               "TrackedBasketStillOpen", "LogManualAdoption",
               "InpAdoptManualTrades", "InpManualNoSLWarnMinutes")
bad_enc = []
for fname in ("COttoOrderManager.mqh", "COttoJournal.mqh", "otto.mq5"):
    for ln, line in enumerate(read(os.path.join(ROOT, fname)).split("\n"), 1):
        if any(s in line for s in ADDED_SIGNS) and any(ord(c) > 127 for c in line):
            bad_enc.append("%s:%d" % (fname, ln))
check("no adoption line carries a non-ASCII byte outside OttoDefines.mqh",
      not bad_enc, ", ".join(bad_enc[:5]))
# "MAN" must be the literal tail token, not a translated glyph, because
# SessionFileName() splits the session id on '-' and the journal indexes into
# those fields.
check("the basket origin token is the literal ASCII MAN",
      re.search(r'0\s*,\s*"MAN"\s*\)', ORD_C) is not None)
check("the build gate still lists every source it verifies",
      BUILD_T.count('"CHighTableAuditor"') == 3 and
      all(('"%s"' % f.replace(".mqh", "").replace(".mq5", "")) in BUILD_T
          for f in ("COttoOrderManager.mqh", "COttoJournal.mqh")))

# The adoption sweep must not have been smuggled into a path that does not own
# it. It belongs to Update() and to nothing else: OnTick() places orders and
# the timer runs the audit, and neither may seed a basket.
ONTICK = func_body(MAIN_C, r"void\s+OnTick\s*\(\s*void\s*\)")
check("OnTick is defined", ONTICK is not None)
check("OnTick does not adopt manually",
      ONTICK is None or "AdoptManualPosition" not in ONTICK)
check("OnTick still does not touch the auditor",
      ONTICK is None or "g_highTable" not in ONTICK)
check("RunAudit is still called exactly once",
      len(re.findall(r"g_highTable\.RunAudit\s*\(", MAIN_C)) == 1)

# The auditor's adoption INFO must not become an alert: a healthy adoption is
# a NOTE, and the one-shot latch is what keeps it from being a mailbox flood.
check("the adoption note is a Print, not an email",
      SETTRACKED is not None and "Print(" in SETTRACKED and
      "SendMail" not in SETTRACKED and "DispatchAlert" not in SETTRACKED)
check("the adoption INFO latch is declared in the state block",
      re.search(r"bool\s+m_adoptionSeen\s*;", AUD_C) is not None or
      re.search(r"bool\s+m_adoptionSeen\s*;", AUD_T) is not None)
check("the adoption INFO latch is reset with the rest of the state",
      re.search(r"m_adoptionSeen\s*=\s*false\s*;", AUD_C) is not None)
check("m_trackedAdopted is reset with the rest of the state",
      re.search(r"m_trackedAdopted\s*=\s*false\s*;", AUD_C) is not None)
check("the auditor still cannot mutate basket state",
      "CTrade" not in AUD_C and "CPositionInfo" not in AUD_C)


# ----------------------------------------------------------------------
# 12. Scope: adopted-ticket support reaches the sites that need it, and
#     nowhere else. A support path that exists but is never reached is the
#     failure mode a per-function presence test is meant to catch.
# ----------------------------------------------------------------------
print("\n-- Scope of the adopted-ticket support --")

# The functions that MUST know about an adopted primary, and the token that
# proves they do: either the explicit magic-0 origin, the adoption flag, or
# the magic-agnostic presence test.
MUST_COVER = [
    ("AdoptManualPosition", ADOPT, ("POSITION_MAGIC) != 0", "adoptedTicket")),
    ("IsTrackedTicketOpen", ISOPEN, ("m_adoptedManual",)),
    ("SyncActiveTrade", SYNC, ("TrackedBasketStillOpen()",)),
    ("TrackedBasketStillOpen", STILLOPEN, ("m_basket[i].ticket",)),
    ("CloseEntireBasket", CLOSE, ("m_basket[b].ticket",)),
    ("SetTrackedLegs", SETTRACKED, ("adoptedManual",)),
    ("AuditStateConsistency", AUDSTATE, ("AdoptedTicketInBook",)),
    ("AdoptedTicketInBook", ADOPTED_IN_BOOK, ("PositionGetTicket(idx) != ticket",)),
]
for name, body, tokens in MUST_COVER:
    check("%s carries the adopted-ticket support" % name,
          body is not None and all(t in body for t in tokens))

# ... and the sites that must NOT know about it. Widening any of these turns
# "any of OUR legs" into "any of the operator's hand trades".
MUST_NOT_COVER = [
    ("FindActivePosition", FIND),
    ("CountMyPositions", COUNT),
    ("CountBookLegs", COUNTLegS),
    ("BookHasTicket", HAS_TICKET),
]
for name, body in MUST_NOT_COVER:
    check("%s stays blind to the adoption flag" % name,
          body is None or "adopt" not in body.lower())


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.33 MANUAL TRADE ADOPTION - STATIC PROBE")
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
