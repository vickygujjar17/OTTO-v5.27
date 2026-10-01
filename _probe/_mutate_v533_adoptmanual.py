"""
Mutation test for the v5.33 Manual Trade Adoption probe.

A probe that always passes is worthless, so every load-bearing v5.33 assertion
is exercised here against a deliberate, targeted mutation of the SHIPPED
sources. Every mutation MUST make the probe fail: if one does not, the check it
was meant to justify is vacuous - it would not notice the regression it claims
to guard.

The mutations are grouped by the invariant they attack:

    A. THE IDENTIFIER      - magic 0 AND this symbol, oldest-wins
    B. THE ONE-BASKET GATE - adoption runs every tick, so refusals are safety
    D. THE BLIND SPOTS     - each magic-scoped site's own answer to
                             "is the adopted leg still mine?"
    E. THE FALSE CRITICAL  - the auditor must SWAP the presence test, never
                             mute the alert, and must keep its magic counts
    F. THE LIFECYCLE       - the flag has to die with the basket
    G. THE JOURNAL         - the record states a defensible 1R
    H. THE INGRESS         - the flag arrives through the record, not a rescan
    I. THE WIRING          - the sweep belongs to Update(), and only Update()

Ordering mutations have no honest single-line form (moving a call is an edit to
two sites at once), so they are expressed as `tuple` mutations: one anchor list,
one matching replacement list, applied in sequence to the same working copy.

The originals are restored in a finally block, so a crash mid-run cannot leave
the working tree mutated.
"""

import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "_probe", "test_v533_adoptmanual.py")

DEFS = os.path.join(ROOT, "OttoDefines.mqh")
AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
ORD = os.path.join(ROOT, "COttoOrderManager.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")


def read(p):
    # Normalise CRLF to LF so anchors can be written with plain \n, while the
    # on-disk CRLF is preserved by write() below.
    return (io.open(p, encoding="utf-8", errors="replace", newline="")
            .read().replace("\r\n", "\n"))


def write(p, s):
    # Restore CRLF: the .mq5/.mqh sources and the probe are all CRLF on disk,
    # and _tools\normalize_eol.py enforces that.
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("\n", "\r\n"))


def run_probe():
    r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# (label, path, old, new) - each `old` must occur exactly once, unless it is a
# tuple (see the module docstring), in which case every element must be unique
# and the tuple lengths must match.
MUTATIONS = [

    # ------------------------------------------------------------------
    # A. The identifier. Both halves of the discriminator are load-bearing:
    #    dropping the magic test adopts a second EA's leg, dropping the symbol
    #    test adopts a hand trade on an unrelated pair.
    # ------------------------------------------------------------------
    ("A1 the magic-0 test is dropped, so any EA's leg is adoptable", ORD,
     "         if(PositionGetInteger(POSITION_MAGIC) != 0)        continue;",
     "         if(false)                                          continue;"),

    ("A2 the symbol test is dropped, so a hand trade on another pair is "
     "adopted", ORD,
     "         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;\n"
     "         if(PositionGetInteger(POSITION_MAGIC) != 0)        continue;",
     "         if(PositionGetInteger(POSITION_MAGIC) != 0)        continue;"),

    ("A3 the magic test becomes 'not ours' instead of an equality against 0, "
     "so another EA's magic is adopted", ORD,
     "         if(PositionGetInteger(POSITION_MAGIC) != 0)        continue;",
     "         if(PositionGetInteger(POSITION_MAGIC) == MagicNumber) continue;"),

    ("A4 oldest-wins becomes first-listed-wins", ORD,
     "         if(adoptedTicket == 0 || opened < oldest)",
     "         if(adoptedTicket == 0)"),

    ("A5 an empty book falls through and adopts ticket 0", ORD,
     "      if(adoptedTicket == 0) return 0;",
     "      if(false)              return 0;"),

    # ------------------------------------------------------------------
    # B. The one-basket gate. Each of these runs on EVERY tick, and the unified
    #    ratchet is one-way, so a missing refusal cannot be undone later.
    # ------------------------------------------------------------------
    ("B1 the active-trade refusal is dropped", ORD,
     "      if(m_hasActiveTrade) return 0;",
     "      if(false)            return 0;"),

    ("B2 the live-basket refusal is dropped", ORD,
     "      if(m_basketCount > 0) return 0;",
     "      if(false)            return 0;"),

    ("B3 the open-EA-position refusal is dropped", ORD,
     "      if(CountMyPositions() > 0) return 0;",
     "      if(false)               return 0;"),

    ("B4 the resting-limit-order refusal is dropped", ORD,
     "      if(CountMyPendingOrders() > 0) return 0;",
     "      if(false)                    return 0;"),

    ("B5 the news shield is bypassed for manual legs", ORD,
     "      if(InpSimNewsShield) return 0;\n"
     "      if(InpSimMacroVeto)  return 0;",
     "      if(false)            return 0;\n"
     "      if(InpSimMacroVeto)  return 0;"),

    ("B6 the macro veto is bypassed for manual legs", ORD,
     "      if(InpSimMacroVeto)  return 0;",
     "      if(false)            return 0;"),

    ("B7 a reversal in flight is adopted into", ORD,
     "      if(m_reversalInProgress)  return 0;",
     "      if(false)                 return 0;"),

    ("B8 the flag is never raised on the manager, so nothing clears it", ORD,
     "      m_adoptedManual   = true;",
     "      m_adoptedManual   = false;"),

    ("B9 a zero-width stop is accepted, seeding the basket with 1R=0", ORD,
     "      if(sl <= 0.0 || entry <= 0.0 || MathAbs(entry - sl) <= 0.0)",
     "      if(sl < 0.0  || entry <= 0.0 || false)"),

    ("B10 the no-stop refusal is silently dropped instead of logged", ORD,
     "      if(sl <= 0.0 || entry <= 0.0 || MathAbs(entry - sl) <= 0.0)\n"
     "        {",
     "      if(false)\n"
     "        {"),

    ("B11 the no-stop notice becomes an unthrottled per-tick Print", ORD,
     "         if(TimeCurrent() - m_manualNoSLWarnTick >= warnMins * 60)",
     "         if(true)"),

    ("B12 the throttle stamp stops advancing, so the notice still floods", ORD,
     "            m_manualNoSLWarnTick = TimeCurrent();",
     "            ;"),

    ("B13 a non-positive cadence silences the notice instead of falling back",
     ORD,
     "      int warnMins = (InpManualNoSLWarnMinutes > 0) ? InpManualNoSLWarnMinutes : 5;",
     "      int warnMins = InpManualNoSLWarnMinutes;"),

    # ------------------------------------------------------------------
    # D. The blind spots. Every one of these sites filters on MagicNumber by
    #    construction, so an adopted leg is invisible to it. Each needed its own
    #    answer - and widening a site that must NOT be widened is exactly as
    #    wrong as failing to widen one that must be.
    # ------------------------------------------------------------------
    ("D1 IsTrackedTicketOpen reverts to a hard magic test, so the adopted "
     "primary reads as closed on the next tick", ORD,
     "      if(m_adoptedManual || m_activeTrade.adoptedManual)\n"
     "         return true;\n",
     ""),

    ("D2 the ghost-remnant branch is narrowed back to the magic-scoped count, "
     "so a stopped-out adopted basket is re-seeded instead of closed", ORD,
     "if(m_hasActiveTrade && (CountMyPositions() > 0 || TrackedBasketStillOpen()))",
     "if(m_hasActiveTrade && CountMyPositions() > 0)"),

    ("D3 TrackedBasketStillOpen reverts to a magic filter, so it can never "
     "see the adopted leg", ORD,
     "         if(!PositionSelectByTicket(m_basket[i].ticket)) continue;",
     "         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;\n"
     "         if(!PositionSelectByTicket(m_basket[i].ticket)) continue;"),

    ("D4 the orphan sweep stops being magic-scoped, so a force close sweeps "
     "the operator's unrelated hand trades", ORD,
     "         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;\n"
     "         ulong orphan = (ulong)PositionGetInteger(POSITION_TICKET);",
     "         ulong orphan = (ulong)PositionGetInteger(POSITION_TICKET);"),

    ("D5 the adoption sweep is reordered ahead of the EA fill detection", ORD,
     ("      CompleteReversal();\n"
      "      CheckPendingOrderFills();\n"),
     ("      AdoptManualPosition();\n"
      "      CompleteReversal();\n")),

    ("D6 FindActivePosition is widened to accept magic 0", ORD,
     "         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber ||\n"
     "            PositionGetString(POSITION_SYMBOL) != m_symbol)\n"
     "            continue;",
     "         if(PositionGetString(POSITION_SYMBOL) != m_symbol)\n"
     "            continue;"),

    ("D7 CountMyPositions is widened to accept magic 0", ORD,
     "            if(PositionGetInteger(POSITION_MAGIC) == MagicNumber &&\n"
     "               PositionGetString(POSITION_SYMBOL) == m_symbol)\n"
     "               count++;",
     "            if(PositionGetString(POSITION_SYMBOL) == m_symbol)\n"
     "               count++;"),

    ("D8 CountMyPendingOrders is widened to accept magic 0", ORD,
     "            if(OrderGetInteger(ORDER_MAGIC) == MagicNumber &&\n"
     "               OrderGetString(ORDER_SYMBOL) == m_symbol)\n"
     "               count++;",
     "            if(OrderGetString(ORDER_SYMBOL) == m_symbol)\n"
     "               count++;"),

    # ------------------------------------------------------------------
    # E. The false CRITICAL. The fix is a SWAP, so two independent regressions
    #    have to be caught: muting the alert (or reverting the swap to the
    #    magic-scoped test) AND widening the magic-scoped counts the alert
    #    text names.
    # ------------------------------------------------------------------
    ("E1 the flag never reaches the auditor's state, so the phantom test "
     "stands down for a live manual leg", AUD,
     "      m_trackedAdopted = adoptedManual;",
     "      ;"),

    ("E2 the presence test reverts to BookHasTicket, so the audit latches a "
     "fake desync for the life of the trade (the original v5.33 bug)", AUD,
     "      bool phantom = m_trackedActive && m_trackedPrimary > 0 &&\n"
     "                     !present;",
     "      bool phantom = m_trackedActive && m_trackedPrimary > 0 &&\n"
     "                     !BookHasTicket(m_trackedPrimary);"),

    ("E3 the two-cycle confirmation is dropped, so a one-tick artifact pages "
     "the operator", AUD,
     "      if(!m_stateSkewSeen)",
     "      if(false)"),

    ("E4 m_trackedAdopted survives the state reset, so the stale flag "
     "suppresses the next trade's audit", AUD,
     "      m_trackedAdopted         = false;",
     "      ;"),

    ("E5 the admitted deviation stops being a loud one-shot INFO", AUD,
     "      if(adoptedManual && !m_adoptionSeen)",
     "      if(false)"),

    ("E6 the INFO latch is never re-armed, so a SECOND adoption is silent", AUD,
     "      else if(!adoptedManual)\n         m_adoptionSeen = false;",
     "      else if(false)\n         m_adoptionSeen = false;"),

    ("E7 the presence test becomes symbol-blind, so an unrelated leg "
     "satisfies the phantom check", AUD,
     "         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;\n"
     "         return true;",
     "         return true;"),

    ("E8 CountBookLegs is widened to magic 0, redefining what the alert "
     "text claims to measure", AUD,
     "         if((long)PositionGetInteger(POSITION_MAGIC) != (long)m_magic) continue;\n"
     "         n++;",
     "         n++;"),

    ("E9 BookHasTicket is widened to magic 0, so the magic-scoped test is "
     "quietly replaced too", AUD,
     "         if((long)PositionGetInteger(POSITION_MAGIC) != (long)m_magic) continue;\n"
     "         return true;",
     "         return true;"),

    # ------------------------------------------------------------------
    # F. Lifecycle. The flag has to die with the basket, and a fresh EA seed
    #    must not inherit it - the record is reused across baskets.
    # ------------------------------------------------------------------
    ("F1 the manager never raises the adoption flag, so nothing downstream "
     "knows an adoption happened", ORD,
     "      m_adoptedManual   = true;",
     "      m_adoptedManual   = false;"),

    ("F2 ClearBasket leaves both flags set, so a future EA leg is exempted "
     "from the magic test", ORD,
     "      m_adoptedManual = false;\n"
     "      m_activeTrade.adoptedManual = false;",
     "      m_adoptedManual = true;\n"
     "      m_activeTrade.adoptedManual = true;"),

    ("F3 Initialize does not reset the adoption state, so a re-init inherits "
     "a stale suppression", ORD,
     "      m_adoptedManual             = false;\n"
     "      m_activeTrade.highestPriceSinceEntry",
     "      m_activeTrade.highestPriceSinceEntry"),

    ("F4 Initialize does not reset the warn throttle, so the first no-stop "
     "refusal is swallowed", ORD,
     "      m_adoptedManual      = false;\n"
     "      m_manualNoSLWarnTick = 0;",
     "      m_adoptedManual      = false;"),

    ("F5 an EA seed no longer clears the record's adoption flag", ORD,
     "      // v5.33: an EA-originated seed is never an adopted manual leg. Set\n"
     "      // explicitly rather than relying on ZeroMemory(), because this struct is\n"
     "      // reused across baskets on this instance and a leftover true would make\n"
     "      // the High Table auditor stand down its phantom test for a normal trade.\n"
     "      m_activeTrade.adoptedManual = false;\n",
     ""),

    # ------------------------------------------------------------------
    # G. The journal record. The 1R it prints must be the SAME stop distance
    #    the manager seeded, and the record must be guarded on a ready journal.
    # ------------------------------------------------------------------
    ("G1 the journal record is written even when the journal is not ready", JRN,
     "      if(!m_ready) return;\n"
     "      if(!OpenAppend())\n"
     "        {\n"
     "         if(!OpenWrite()) return;   // no file yet -> create it\n"
     "        }",
     "      if(!OpenAppend())\n"
     "        {\n"
     "         if(!OpenWrite()) return;   // no file yet -> create it\n"
     "        }"),

    ("G2 the printed 1R stops being the seeded stop distance", JRN,
     "      W(\"  Initial Risk (1R) : \" + FmtPrice(slDistance) +\n"
     "        \"  (\" + DoubleToString(slPips, 1) + \" pips)\");",
     "      W(\"  Initial Risk (1R) : \" + FmtPrice(slPips) +\n"
     "        \"  (\" + DoubleToString(slPips, 1) + \" pips)\");"),

    # ------------------------------------------------------------------
    # H. The ingress. The flag must arrive through the order layer's own record
    #    - a rescan for magic 0 in otto.mq5 would read the book in a second
    #    place and reintroduce the false positive.
    # ------------------------------------------------------------------
    ("H1 the ingress stops reading the adopted flag off the record", MAIN,
     "      adopted = active.adoptedManual;",
     "      ;"),

    ("H2 the flag is never handed to the auditor", MAIN,
     "   g_highTable.SetTrackedLegs(g_orderManager.CountOpenPositions(),\n"
     "                              ticket, g_orderManager.HasActiveTrade(),\n"
     "                              adopted);",
     "   g_highTable.SetTrackedLegs(g_orderManager.CountOpenPositions(),\n"
     "                              ticket, g_orderManager.HasActiveTrade());"),

    ("H3 otto.mq5 re-derives the flag from the book instead of the record, "
     "adding a second magic-0 scan", MAIN,
     "      adopted = active.adoptedManual;",
     "      adopted = false;   // POSITION_MAGIC) != 0 rescanned right here"),

    # ------------------------------------------------------------------
    # I. The wiring. The sweep belongs to Update() and to nothing else, it is
    #    a single step, and 'oldest-wins' needs its tie-break.
    # ------------------------------------------------------------------
    ("I1 the adoption sweep is smuggled into OnTick", MAIN,
     "   g_orderManager.Update();\n",
     "   g_orderManager.Update();\n"
     "   g_orderManager.AdoptManualPosition();\n"),

    ("I2 the adoption sweep runs twice in Update", ORD,
     "      CompleteReversal();\n"
     "      CheckPendingOrderFills();\n",
     "      AdoptManualPosition();\n"
     "      CompleteReversal();\n"),

    ("I3 the oldest-wins tie-break is dropped, so book order decides", ORD,
     "      if(adoptedTicket == 0 || opened < oldest)",
     "      if(adoptedTicket == 0 || true)"),

    # ------------------------------------------------------------------
    # J. The documented contract: the defaults, the record field, and the
    #    release stamps the operator reads before installing.
    # ------------------------------------------------------------------
    ("J1 adoption no longer ships enabled by default", DEFS,
     "input bool     InpAdoptManualTrades     = true;",
     "input bool     InpAdoptManualTrades     = false;"),

    ("J2 the warn cadence default drifts away from five minutes", DEFS,
     "input int      InpManualNoSLWarnMinutes = 5;",
     "input int      InpManualNoSLWarnMinutes = 1;"),

    ("J3 the record loses the adoption flag the whole feature hangs off", DEFS,
     "   bool              adoptedManual;",
     "   bool              adoptedManualUnused;"),

    ("J4 the MANUAL TRADE ADOPTION group label loses its release stamp", DEFS,
     "input group \"  [11] MANUAL TRADE ADOPTION",
     "input group \"  [11] MANUAL TRADE ADOPTION (group)"),

    ("J5 a versioned engine group label is not bumped with the release", DEFS,
     "input group \"  [9] CURRENCY VECTOR & AFFINITY ENGINE",
     "input group \"  [9] CURRENCY VECTOR & AFFINITY ENGINE (unversioned)"),

    ("J6 the Pine port banner stops naming the release otto.mq5 ships", MAIN,
     "Master Build Port (v5.33)",
     "Master Build Port"),
]

def main():
    rc, out = run_probe()
    if rc != 0:
        print("BASELINE FAILED - fix the probe before mutation testing")
        print(out)
        return 1

    print("baseline: PASS (%s)" % out.strip().split("\n")[-3].strip())

    # Every file a mutation touches, backed up once. Read from MUTATIONS rather
    # than hard-coded, so adding a mutant to a new file cannot silently escape
    # the finally-restore below.
    paths = sorted({path for _, path, _, _ in MUTATIONS})
    backups = {}
    failures = []
    try:
        for path in paths:
            backups[path] = read(path)

        for label, path, old, new in MUTATIONS:
            original = backups[path]
            # `old` may be a tuple of anchors, `new` the matching tuple of
            # replacements, so a regression that requires editing SEVERAL
            # sites at once (e.g. moving a call, which rewrites the callee and
            # the caller in one edit) is expressed as one honest mutation.
            olds = old if isinstance(old, tuple) else (old,)
            news = new if isinstance(new, tuple) else (new,)
            if len(olds) != len(news):
                failures.append("%s: %d anchors vs %d replacements"
                                % (label, len(olds), len(news)))
                continue

            mutated, bad = original, None
            for o, n in zip(olds, news):
                if mutated.count(o) != 1:
                    bad = "anchor found %d times: %r" % (mutated.count(o), o[:60])
                    break
                mutated = mutated.replace(o, n)
            if bad:
                failures.append("%s: %s" % (label, bad))
                continue

            write(path, mutated)
            rc, out = run_probe()
            if rc == 0:
                failures.append("%s: probe STILL PASSED (vacuous check)" % label)
                print("  [BAD ] %s" % label)
            else:
                print("  [GOOD] %s -> probe failed as required" % label)
            write(path, original)
    finally:
        for path, original in backups.items():
            write(path, original)

    rc, out = run_probe()
    print("\nrestored: %s" % ("PASS" if rc == 0 else "FAIL"))
    if rc != 0:
        failures.append("the tree did not restore cleanly")
        print(out)

    if failures:
        print("\n*** %d PROBLEM(S) ***" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("\n*** ALL %d MUTATIONS DETECTED ***" % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

