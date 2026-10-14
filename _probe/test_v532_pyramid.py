"""
v5.32 static verification probe - half-risk rung liveness, T2 crossing trace,
pyramid refusal attribution, and original-1R persistence across a restart.

Four field-reported defects ship fixes in v5.32, and none of them is visible to
the MQL5 compiler gate (every one of them compiles cleanly while misbehaving):

  1. PHANTOM HALF-RISK. The journal recorded "risk now -0.5R" on every +1.0R
     winner, while the live stop was already at cost-covering breakeven. The
     half-risk rung is a LOSS-side floor at entry -/+ 0.5R, and the +1.0R
     breakeven floor outranks it, so at the shipped defaults
     (InpCutRiskRR == InpBreakEvenRR == 1.0) the rung can never assign. It was
     nevertheless counted, because m_halfRiskTriggers incremented on the
     TRIGGER rather than on the APPLICATION. Fix: resolve the rung's liveness
     ONCE in Initialize() and gate both the stop assignment and the counter on
     it, after HOISTING the rung to the end of each branch so the guard can see
     the final stop.

  2. TRANCHES NEVER TRIGGERING SILENTLY. AddPyramidTranche returned a bare
     'false' from several distinct early exits, so "T2 never fires" could not
     be distinguished from "T2 fired and was refused". Fix: every exit reports
     its cause, and the cursor asymmetry is now deliberate and documented --
     insufficient margin is transient and RETAINS the cursor, a sub-minimum lot
     never becomes fundable and ADVANCES it.

  3. T2 CROSSING NEVER OBSERVED. A read-only trace now prints the live RR, the
     R unit it was measured against, the basket size, the ladder cursor and the
     lot the rung would risk, once per 5%-wide RR bucket, and it sits BEFORE
     the IsPyramidPending(2) gate so it still fires when that gate is false.

  4. COLD-RESTART R CORRUPTION. SeedActiveTradeFromPosition derived 1R from
     |entry - POSITION_SL|. The stop ratchets by design, so after breakeven that
     distance is broker friction, not risk -- and dividing by it INFLATES every
     subsequent RR, which is exactly the input the +1.0R T2 gate tests. Fix:
     persist 1R in a GlobalVariable keyed to the basket's primary ticket, and
     restore it only when the stored ticket still matches.

Pure static analysis of the shipped sources plus an independent Python model of
the ladder/persistence logic - no MT5 runtime required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    # Normalise CRLF so multi-line anchors below can be written with plain \n.
    return io.open(p, encoding="utf-8", errors="replace",
                   newline="").read().replace("\r\n", "\n")


MAIN_T = read(MAIN)
OM_T = read(OM)
TM_T = read(TM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def num_input(text, name):
    """Default of a top-level `input double NAME = <value>;` declaration."""
    m = re.search(r"^\s*input\s+double\s+" + re.escape(name) +
                  r"\s*=\s*([0-9]*\.?[0-9]+)\s*;", text, re.M)
    return float(m.group(1)) if m else None


def brace_body(code, sig):
    """Return source from the first match of `sig` through its closing brace."""
    m = re.search(sig, code)
    if not m:
        return None, -1
    i = code.find("{", m.end())
    if i < 0:
        return None, m.start()
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[m.start():j + 1], m.start()
    return code[m.start():], m.start()


# ----------------------------------------------------------------------
# 1. The half-risk rung's liveness is resolved ONCE, from the inputs.
# ----------------------------------------------------------------------
print("\n-- Half-risk rung liveness --")

check("m_cutRiskRungLive is a declared member",
      re.search(r"bool\s+m_cutRiskRungLive\s*;", TM_T) is not None)

# The constructor must default it to INERT before Initialize() runs, so a rung
# that has not been proven reachable can never be counted.
ctor = brace_body(TM_T, r"COttoTradeManager\s*\(void\)")[0] or ""
check("constructor defaults the rung to INERT before Initialize() resolves it",
      re.search(r"m_cutRiskRungLive\s*=\s*false\s*;", ctor) is not None)

check("Initialize() resolves liveness as InpCutRiskRR strictly below InpBreakEvenRR",
      re.search(r"bool\s+cutBeforeBE\s*=\s*\(\s*InpCutRiskRR\s*>\s*0\.0\s*&&\s*"
                r"InpCutRiskRR\s*<\s*InpBreakEvenRR\s*\)", TM_T) is not None)

check("Initialize() assigns the resolved bool (not a per-tick recomputation)",
      re.search(r"m_cutRiskRungLive\s*=\s*cutBeforeBE\s*;", TM_T) is not None)

# An inert rung must announce itself: silence is how this defect hid.
check("Initialize() logs an INERT RUNG line when the rung is unreachable",
      "INERT RUNG" in TM_T)
check("Initialize() logs a LIVE RUNG line when the rung is armed",
      "LIVE RUNG" in TM_T)

# The shipped defaults matter: both 1.0 means the rung is inert out of the box,
# which is precisely the configuration the field was running.
_cut = num_input(DEFS_T, "InpCutRiskRR")
_be = num_input(DEFS_T, "InpBreakEvenRR")
check("input defaults are read for the liveness arithmetic",
      _cut is not None and _be is not None, "%s / %s" % (_cut, _be))
check("shipped defaults leave the rung INERT (InpCutRiskRR >= InpBreakEvenRR)",
      _cut is not None and _be is not None and _cut >= _be,
      "cut=%s be=%s" % (_cut, _be))


# ----------------------------------------------------------------------
# 2. The gate reaches BOTH directions, and reaches the COUNTER.
# ----------------------------------------------------------------------
print("\n-- Half-risk gating --")

_gates = re.findall(r"m_cutRiskRungLive\s*&&\s*currentRR\s*>=\s*InpCutRiskRR", TM_T)
check("both direction branches gate the rung on m_cutRiskRungLive",
      len(_gates) == 2, "found %d" % len(_gates))

# Every reference to the trigger must be gated. An ungated one is the bug.
_refs = re.findall(r"currentRR\s*>=\s*InpCutRiskRR", TM_T)
check("no ungated reference to InpCutRiskRR survives",
      len(_refs) == len(_gates) == 2, "refs=%d gates=%d" % (len(_refs), len(_gates)))

# The counter is the thing the journal reports from, so it must live INSIDE the
# gated blocks. Exactly two increments, and each on a gated line.
check("m_halfRiskTriggers increments exactly twice (one per direction)",
      len(re.findall(r"m_halfRiskTriggers\s*\+\+", TM_T)) == 2)
check("the counter increments on the SAME line as the gated assignment",
      len(re.findall(r"m_cutRiskRungLive\s*&&\s*currentRR\s*>=\s*InpCutRiskRR"
                     r"[^{}]*\{[^{}]*m_halfRiskTriggers\s*\+\+\s*;", TM_T)) == 2)

# test_v52x_pyramid.py pins that the half-risk arithmetic appears exactly once
# per direction and only ever writes halfRiskSL. Keep that invariant honest.
check("halfRiskSL is declared exactly twice (one per direction)",
      len(re.findall(r"double\s+halfRiskSL\s*=", TM_T)) == 2)
check("the 0.5R floor arithmetic appears exactly twice",
      len(re.findall(r"0\.5\s*\*\s*rrUnit", TM_T)) == 2)


# ----------------------------------------------------------------------
# 3. HOIST: the rung is evaluated LAST, so the guard sees the FINAL stop.
# ----------------------------------------------------------------------
print("\n-- Hoist ordering (counter means APPLIED, not triggered) --")

_trail_idx = [m.start() for m in re.finditer(r"double\s+dynamicTrail\s*=", TM_T)]
_half_idx = [m.start() for m in re.finditer(r"double\s+halfRiskSL\s*=", TM_T)]
check("the ATR trail is assigned twice (one per direction)",
      len(_trail_idx) == 2, _trail_idx)
check("both half-risk floors are evaluated AFTER the ATR trail",
      len(_trail_idx) == len(_half_idx) == 2 and
      _half_idx[0] > _trail_idx[0] and _half_idx[1] > _trail_idx[1],
      "trail=%s half=%s" % (_trail_idx, _half_idx))

# Hoisting is only safe because max()/min() resolution is order-independent:
# each rung still only ever moves the stop TOWARD market.
_longs = re.search(r"double\s+halfRiskSL\s*=\s*primaryEntry\s*-\s*\(0\.5\s*\*\s*rrUnit\)"
                   r"\s*;\s*if\([^{}]*desiredSL\s*<\s*halfRiskSL\s*\)", TM_T)
_shorts = re.search(r"double\s+halfRiskSL\s*=\s*primaryEntry\s*\+\s*\(0\.5\s*\*\s*rrUnit\)"
                    r"\s*;\s*if\([^{}]*desiredSL\s*>\s*halfRiskSL\s*\)", TM_T)
check("LONG floor is below entry and only raised (desiredSL < halfRiskSL)",
      _longs is not None)
check("SHORT floor is above entry and only lowered (desiredSL > halfRiskSL)",
      _shorts is not None)


# ----------------------------------------------------------------------
# 4. The T2 crossing trace fires BEFORE the gate and reports the verdict.
# ----------------------------------------------------------------------
print("\n-- Tranche 2 crossing trace --")

check("m_t2TraceBucket is a declared member",
      re.search(r"int\s+m_t2TraceBucket\s*;", TM_T) is not None)
check("constructor seeds the bucket latch to -1 (first crossing always reports)",
      re.search(r"m_t2TraceBucket\s*=\s*-1\s*;", ctor) is not None)

_trace = TM_T.find("[Pyramid] T2 crossing")
_gate = TM_T.find("currentRR >= InpPyramidT2RR && m_orderManager.IsPyramidPending(2)")
check("the trace exists and the T2 gate exists",
      _trace > 0 and _gate > 0, "trace=%d gate=%d" % (_trace, _gate))
check("the trace is emitted BEFORE the IsPyramidPending(2) gate",
      _trace > 0 and _gate > 0 and _trace < _gate,
      "trace=%d gate=%d" % (_trace, _gate))

# Fire once per approach, not once per tick: the latch re-arms when the ladder
# leaves rung 2, and a bucket comparison suppresses same-bucket repeats.
check("the latch re-arms when the ladder leaves rung 2",
      re.search(r"GetNextTranche\(\)\s*!=\s*2\s*\)\s*\n\s*m_t2TraceBucket\s*=\s*-1\s*;",
                TM_T) is not None)
check("the trace is bucket-latched (no per-tick spam)",
      re.search(r"bucket\s*!=\s*m_t2TraceBucket", TM_T) is not None)
check("the trace is guarded on EnableLogging and a usable trigger",
      re.search(r"if\(EnableLogging\s*&&\s*InpPyramidT2RR\s*>\s*0\.0\s*\)", TM_T) is not None)

# Every quantity the refusal decision depends on must be in the line.
for _field in ["currentRR", "trigger=", "basketRRUnit=", "ladderRRUnit=",
               "basketCount=", "nextTranche=", "pending2=", "riskT2%=",
               "lot=", "dir=", "enable="]:
    check("crossing trace reports %s" % _field, _field in TM_T)

# The trace is read-only: the only state it may touch is the latch. Guard
# against it being "fixed" later into something that mutates the basket.
_trace_block, _ = brace_body(TM_T, r"if\(EnableLogging\s*&&\s*InpPyramidT2RR")
if _trace_block:
    _mutators = [m for m in ["InitBasket", "ApplyUnifiedSL", "AddPyramidTranche",
                             "ModifyStopLoss", "ClosePosition", "m_nextTranche ="]
                 if m in _trace_block]
    check("the crossing trace mutates nothing but its own latch",
          not _mutators, "found %s" % _mutators)
else:
    check("the crossing trace mutates nothing but its own latch", False,
          "trace block not found")


# ----------------------------------------------------------------------
# 5. Every refusal in AddPyramidTranche is ATTRIBUTABLE.
# ----------------------------------------------------------------------
print("\n-- Pyramid refusal attribution --")

ADD, _add_at = brace_body(OM_T, r"bool\s+AddPyramidTranche\s*\([^)]*\)")
check("AddPyramidTranche body was located", ADD is not None and len(ADD) > 2000,
      len(ADD) if ADD else 0)

if ADD:
    _returns = [m.start() for m in re.finditer(r"return\s+false\s*;", ADD)]
    check("the refusal exits are still present", len(_returns) >= 6, len(_returns))

    # The defect: a bare `return false;` left the operator with no cause. Every
    # refusal exit must now be preceded by a Print() that names it.
    _silent = []
    for _i in _returns:
        _window = ADD[max(0, _i - 600):_i]
        if "Print(" not in _window:
            _silent.append(ADD[max(0, _i - 80):_i].splitlines()[-1].strip())
    check("no refusal exit is silent (each is preceded by a Print)",
          not _silent, "silent: %s" % _silent)

    # Each distinct cause must be nameable from the log text.
    for _cause in ["InpPyramidEnable=false", "basket empty",
                   "out of order", "unknown rung",
                   "no usable R unit", "insufficient margin",
                   "REJECTED by broker"]:
        check("refusal cause is named in the log: %s" % _cause, _cause in ADD)

    # ...and the broker-rejection exit must carry the RETCODE, not just the
    # phrase. A broker "no" is the one failure the operator cannot diagnose
    # from GetLastError() alone, so naming the cause without the code would
    # reproduce the original blindness in a cosmetically nicer form.
    _rej_at = ADD.find("REJECTED by broker")
    check("the broker-rejection exit reports the retcode and comment",
          _rej_at > 0 and
          re.search(r"retcode=\", res\.retcode, \" \(\", res\.comment",
                    ADD[_rej_at:_rej_at + 500]) is not None,
          ADD[_rej_at:_rej_at + 220].replace("\n", " ") if _rej_at > 0 else "")

    # The R-unit fallback chain: recover rather than refuse, and say which
    # source supplied the value, so a degraded R unit is never silent.
    check("R unit falls back from the basket to the active trade",
          re.search(r"slDist\s*=\s*m_activeTrade\.rrUnit", ADD) is not None)
    check("R unit falls back again to the seeded SL distance",
          re.search(r"slDist\s*=\s*MathAbs\(m_activeTrade\.entryPrice\s*-\s*"
                    r"m_activeTrade\.initialSL\)", ADD) is not None)
    check("a recovered R unit is logged loudly with its source",
          "R unit RECOVERED from" in ADD and
          re.search(r'" R unit RECOVERED from ", rSource', ADD) is not None)
    # ...but only when logging is on. AddPyramidTranche is reached on every
    # qualifying tick, so an ungated Print here would spam the log.
    _rec_at = ADD.find("R unit RECOVERED from")
    check("the recovered-R log is gated on EnableLogging",
          _rec_at > 0 and
          re.search(r'if\(rSource != "basket" && EnableLogging\)',
                    ADD[max(0, _rec_at - 200):_rec_at]) is not None)

    # CURSOR ASYMMETRY, deliberate: insufficient margin is transient so the
    # rung must be retried; a sub-minimum lot is permanent so the rung is
    # retired. Getting this backwards either spams the log or loses a tranche.
    _margin_at = ADD.find("insufficient margin")
    _lotmin_at = ADD.find("lot <= 0.0")
    check("both the margin and the lot-floor branches exist",
          _margin_at > 0 and _lotmin_at > 0)
    check("the lot-too-small branch ADVANCES the cursor (permanent condition)",
          _lotmin_at > 0 and
          re.search(r"m_nextTranche\s*=\s*\(trancheToAdd\s*==\s*2\)\s*\?\s*3",
                    ADD[_lotmin_at:_margin_at if _margin_at > _lotmin_at else len(ADD)])
          is not None)
    check("the margin branch RETAINS the cursor (transient condition, retried)",
          _margin_at > 0 and
          "nextTranche stays" in ADD[_margin_at:_margin_at + 400] and
          "m_nextTranche =" not in ADD[_margin_at:_margin_at + 400])

    # The ladder still advances 2 -> 3 -> 4 -> 0 on success and on the skip path.
    check("the success path advances the ladder 2 -> 3 -> 4 -> 0",
          len(re.findall(r"m_nextTranche\s*=\s*\(trancheToAdd\s*==\s*2\)\s*\?\s*3"
                         r"\s*:\s*\(\(trancheToAdd\s*==\s*3\)\s*\?\s*4\s*:\s*0\)", ADD)) == 2)


# ----------------------------------------------------------------------
# 6. The cursor is exposed read-only for the trace.
# ----------------------------------------------------------------------
print("\n-- Ladder cursor accessor --")

check("GetNextTranche() is a const accessor (cannot be used to write the ladder)",
      re.search(r"int\s+GetNextTranche\s*\(void\)\s*const\s*\{[^}]*"
                r"return\s+m_nextTranche\s*;", OM_T) is not None)
check("GetBasketRRUnit() is a const accessor",
      re.search(r"double\s+GetBasketRRUnit\s*\(void\)\s*const\s*\{", OM_T) is not None)


# ----------------------------------------------------------------------
# 7. Original 1R survives a cold restart (GlobalVariable persistence).
# ----------------------------------------------------------------------
print("\n-- Original-1R persistence across restart --")

check("the persistence helpers are declared",
      "PersistBasketR" in OM_T and "RestoreBasketR" in OM_T and
      "ClearBasketR" in OM_T)

# Scope the key on symbol AND magic: two charts of the same symbol under
# different magics must not share a basket's R.
check("the GV key is scoped on symbol and magic",
      re.search(r'"OTTO_"\s*\+\s*key\s*\+\s*"_"\s*\+\s*m_symbol\s*\+\s*"_"\s*\+\s*'
                r"IntegerToString\(MagicNumber\)", OM_T) is not None)


# Same symbol, different magic -> different key. Model it.
def gv_key(key, symbol, magic):
    return "OTTO_%s_%s_%d" % (key, symbol, magic)


check("model: keys differ across magic",
      gv_key("BASKETR", "EURUSD", 1) != gv_key("BASKETR", "EURUSD", 2))
check("model: keys differ across symbol",
      gv_key("BASKETR", "EURUSD", 1) != gv_key("BASKETR", "GBPUSD", 1))

check("a non-positive R or a zero ticket is never persisted",
      re.search(r"if\(r\s*<=\s*0\.0\s*\|\|\s*primaryTicket\s*==\s*0\)\s*return\s*;",
                OM_T) is not None)

# The restore must be TICKET-CHECKED. A leftover record from a closed basket
# must never be applied to a different position.
check("restore requires the stored ticket to MATCH the adopted ticket",
      re.search(r"if\(storedTicket\s*!=\s*adoptedTicket\)", OM_T) is not None)
check("a mismatched stored ticket is discarded and reported",
      "discarded (stale record)" in OM_T)
check("restore returns 0.0 (no usable record) rather than a guessed R",
      re.search(r"if\(!GlobalVariableCheck\(name\)\)\s*return\s+0\.0\s*;", OM_T) is not None)


# Model the restore contract exactly.
def restore(stored_r, stored_ticket, adopted_ticket):
    if stored_ticket != adopted_ticket:
        return 0.0
    return stored_r if stored_r > 0.0 else 0.0


check("model: matching ticket restores the R", restore(25.0, 111, 111) == 25.0)
check("model: mismatched ticket refuses the stale R", restore(25.0, 111, 222) == 0.0)
check("model: a non-positive stored R is refused", restore(0.0, 111, 111) == 0.0)


# InitBasket must store 1R against the primary ticket it was handed.
INIT, _ = brace_body(OM_T, r"void\s+InitBasket\s*\([^)]*\)")
check("InitBasket persists 1R against the primary ticket",
      INIT is not None and
      re.search(r"PersistBasketR\(rrUnit\s*,\s*ticket\)", INIT) is not None)

# The adopt path must TRY the store first, then fall back, then warn.
SEED, _ = brace_body(OM_T, r"void\s+SeedActiveTradeFromPosition\s*\([^)]*\)")
check("SeedActiveTradeFromPosition reads the persisted R",
      SEED is not None and "RestoreBasketR(positionTicket)" in SEED)
# The broker distance may still be USED, but only as a fallback. Assert the
# ordering rather than the absence of the assignment: the store must be read
# first, and the broker-derived value may only be assigned after it.
_seed_store_at = SEED.find("RestoreBasketR(positionTicket)") if SEED else -1
_seed_fallback_at = (SEED.find("m_activeTrade.rrUnit = m_activeTrade.initialSLDistance")
                     if SEED else -1)
check("the seeded rrUnit is no longer a bare broker-distance read",
      _seed_store_at >= 0 and _seed_fallback_at > _seed_store_at,
      "store=%d fallback=%d" % (_seed_store_at, _seed_fallback_at))
check("the broker distance is only taken when the store had no matching record",
      SEED is not None and
      re.search(r"if\(restoredR\s*>\s*0\.0\)", SEED) is not None and
      re.search(r"else\s+if\(m_activeTrade\.initialSLDistance\s*>\s*0\.0\)", SEED)
      is not None)
check("a restored R is logged with the broker distance it replaced",
      SEED is not None and "Basket 1R RESTORED from persistent store" in SEED)
check("the broker-distance fallback is logged and warns when the stop is at entry",
      SEED is not None and "Basket 1R REBUILT from broker SL distance" in SEED and
      "stop sits AT ENTRY" in SEED)
check("an unresolvable R is reported rather than silently zeroed",
      SEED is not None and "basket 1R UNRESOLVED" in SEED)

# The R unit is also recoverable on the ORDER path; a zeroed m_basketRRUnit on a
# cold start must not permanently disable scale-ins.
check("InitBasket still records the basket R unit used by the trace",
      INIT is not None and "m_basketRRUnit" in INIT)
check("AddPyramidTranche recovers R when the basket R unit is missing",
      ADD is not None and
      re.search(r"double\s+slDist\s*=\s*m_basketRRUnit", ADD) is not None)

# Clearing the basket must clear the record, so a cold start finds nothing
# rather than something stale.
CLEAR, _ = brace_body(OM_T, r"void\s+ClearBasket\s*\(void\)")
check("ClearBasket clears the persisted 1R",
      CLEAR is not None and "ClearBasketR()" in CLEAR)

# Free GlobalVariables are a finite, terminal-wide resource; the helpers must
# not leak one per basket.
check("ClearBasketR deletes both stored values",
      len(re.findall(r"GlobalVariableDel", OM_T)) >= 2)


# ----------------------------------------------------------------------
# 8. Version stamps agree on the current release.
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      stamps)
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.32 or later", parts >= (5, 32), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 10 files stamp v%s" % RELEASE, not missing, missing)

check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)
check("Pine port banner names the current release",
      ("Master Build Port (v%s)" % RELEASE) in MAIN_T)

# The rung-liveness warning is UNCONDITIONAL: an operator with logging off must
# still learn that the half-risk rung is unreachable.
check("OnInit warns unconditionally when InpCutRiskRR >= InpBreakEvenRR",
      re.search(r"if\(InpCutRiskRR\s*>=\s*InpBreakEvenRR\)", MAIN_T) is not None)
check("the unconditional warning names the unreachable rung",
      "UNREACHABLE" in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.32 HALF-RISK LIVENESS + T2 TRACE + REFUSAL ATTRIBUTION - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + str(detail)) if detail != "" else ""))
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

