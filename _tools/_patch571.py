"""
v5.71 source patch -- WORST-RUNG AGGREGATE CEILING + DEEP FRONT-EDGE JOURNAL.

THE CEILING. v5.63 measured the aggregate leg against the SUM of the four
nominal rung risks (RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct)
plus the same 50% tolerance the per-leg check uses. On the shipped inputs that
resolves to 2.75% nominal / 4.125% with tolerance, and it was documented as
"the pyramid's design ceiling" that a correctly-sized ladder "sits exactly
at ... and never alerts".

That reasoning does not survive the shipped geometry. Every rung is opened
about 1R from ONE shared stop, and ApplyUnifiedSL() ratchets that stop forward
one way only:

    T2 at +1.0R  ->  stop = primaryEntry +/- friction  (breakeven)
    T3 at +2.0R  ->  stop = primaryEntry +/- 1.0R
    T4 at +3.0R  ->  stop = primaryEntry +/- 2.0R

An older leg whose entry is already BEHIND that stop has lossDist <= 0 and is
skipped by the loss-side guard, so it contributes nothing to the sum. A
healthy ladder therefore sums to roughly the NEWEST rung alone:

    ~1.0% at T2   ~0.5% at T3   ~0.25% at T4

never 2.75%. The 4.125% bound was unreachable: it could not fire on the very
stacking it was documented to catch, and the only way to reach it was a
sizing bug so large the per-leg check had already fired.

The bound is therefore re-keyed to the largest rung:

    maxRungRiskPct = MathMax(RiskPercent, MathMax(InpRiskT2Pct,
                              MathMax(InpRiskT3Pct, InpRiskT4Pct)));
    maxAllowedAggPct = maxRungRiskPct * 1.50;      // 1.5% on the shipped 1.0%

RiskPercent is folded into the max so a zeroed or inverted tranche config can
never resolve the ceiling below the primary rung -- the v5.70 property is
preserved, not dropped. The leg keeps its own latch, its two-cycle
confirmation and its WARN severity; only the bound moves.

_commit570.txt:37-43 rejected exactly this change on the grounds that "dropping
it to a single-rung ceiling would alert WARN on every correctly-sized T1+T2
basket". Measured against the ratchet above, a correctly-sized T1+T2 basket
reads ~1.0% against a 1.5% ceiling: 33% headroom, no alert. That objection is
recorded there as a design claim, not an observation, and it is wrong.

THE JOURNAL. COttoJournal still described the entry as a "Front-Run" in five
operator-facing literals, while the shipped model has been TRUE DEEP FRONT-EDGE
since v5.66/v5.69 (OttoDefines: "TRUE DEEP FRONT-EDGE (v5.69)"; the retired
"EXTREME FRONT-RUNNING ANTICIPATION" banner is already pinned as gone). The
literals move onto the tree's own canonical vocabulary. The "Front-Run veto"
wording is deliberately LEFT ALONE: the veto is a named subsystem
(InpEnableFrontRunVeto) and is still a front-run veto.

Every replacement is an exact literal with an asserted match count, so a
drifted anchor aborts instead of silently patching the wrong line.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOX_WIDTH = 75


def load(name):
    """Read as LF-normalised text plus the file's own EOL."""
    p = os.path.join(ROOT, name)
    raw = open(p, "rb").read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    return text, eol


def save(name, text, eol):
    p = os.path.join(ROOT, name)
    data = text.replace("\r\n", "\n").replace("\n", eol)
    open(p, "wb").write(data.encode("utf-8", "surrogateescape"))


def rep(text, old, new, count, label):
    found = text.count(old)
    if found != count:
        raise SystemExit("ABORT %s: matched %d, expected %d"
                         % (label, found, count))
    print("  ok   %s" % label)
    return text.replace(old, new)


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
# 1. CHighTableAuditor.mqh -- the worst-rung ceiling
# ======================================================================
print("CHighTableAuditor.mqh")
aud, aud_eol = load("CHighTableAuditor.mqh")

# 1a. The measurement itself. nominalStackPct is REMOVED rather than kept
#     beside the new bound: it has no remaining reader, and an MQL5 unused
#     local is a warning on every build.
AUD_DECL_OLD = """      double nominalStackPct    = RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct;
      // v5.70: floor the design sum at the primary rung. The sum is only an
      // upper bound while the tranche inputs are positive -- a zeroed or
      // inverted InpRiskT2Pct/T3/T4 would resolve the ceiling BELOW
      // RiskPercent and trip this leg on a plain single-leg basket. The floor
      // is inert under every sane config, so the shipped ladder is unaffected.
      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;"""

AUD_DECL_NEW = """      // v5.71: measure the LARGEST rung, not the sum. The sum is unreachable
      // through the live ratchet (see the doc box above), so it could not fire
      // on the stacking it claimed to catch. RiskPercent is folded into the
      // max so the v5.70 property survives: a zeroed or inverted tranche
      // config can never resolve the ceiling below the primary rung.
      double maxRungRiskPct   = MathMax(InpRiskT2Pct, InpRiskT3Pct);
      maxRungRiskPct          = MathMax(maxRungRiskPct, InpRiskT4Pct);
      maxRungRiskPct          = MathMax(maxRungRiskPct, RiskPercent);
      double maxAllowedAggPct = maxRungRiskPct * 1.50;"""

aud = rep(aud, AUD_DECL_OLD, AUD_DECL_NEW, 1,
          "aggregate ceiling re-keyed to the largest rung")

aud = rep(aud,
          "      if(sumRiskPct > maxAllowedStackPct)",
          "      if(sumRiskPct > maxAllowedAggPct)",
          1, "aggregate comparison reads the new bound")

# 1b. The alert text. It named the bound as a "stacked-pyramid allowance" built
#     from a "nominal design" sum, and closed by blaming "how many of them are
#     stacked". Under the ratchet a breach means the opposite: the older legs
#     are NOT being absorbed, so the sum is running above the newest rung.
AUD_MSG_OLD = """                              "% of equity, above the stacked-pyramid allowance " +
                              DoubleToString(maxAllowedStackPct, 2) + "% (" +
                              DoubleToString(nominalStackPct, 2) +
                              "% nominal design + 50% tolerance). The worst single" +
                              " leg is " + DoubleToString(peakRiskPct, 2) +
                              "%, so every leg may be individually in budget - the" +
                              " problem is how many of them are stacked behind the" +
                              " shared stop.","""

AUD_MSG_NEW = """                              "% of equity, above the worst-rung allowance " +
                              DoubleToString(maxAllowedAggPct, 2) + "% (" +
                              DoubleToString(maxRungRiskPct, 2) +
                              "% largest rung + 50% tolerance). The worst single" +
                              " leg is " + DoubleToString(peakRiskPct, 2) +
                              "%, so every leg may be individually in budget - the" +
                              " problem is that the shared ratchet is NOT absorbing" +
                              " the legs behind the newest one.","""

aud = rep(aud, AUD_MSG_OLD, AUD_MSG_NEW, 1,
          "aggregate alert restated in ratchet terms")

# 1b2. The explanatory comment immediately above the leg. v5.70 left it
#      describing the SUM as the reachable budget, which is now the opposite of
#      what the code does -- a comment that contradicts the line beneath it is
#      worse than no comment.
AUD_COMMENT_OLD = """      // ---- v5.63: the AGGREGATE leg --------------------------------------
      // The per-leg test above is blind to how many legs are stacked behind
      // the shared stop: four legs each inside their own budget can still sum
      // past the design. The stacked budget is the same 50% tolerance applied
      // to the SUM of the nominal rung risks, so a correctly-sized pyramid at
      // full size sits exactly at it and never alerts. Confirmed across two
      // cycles for the same reason as the trail check: a tranche landing just
      // before its stop is pushed reads high for one cycle, and that is the
      // normal shape of a fill rather than an incident."""

AUD_COMMENT_NEW = """      // ---- v5.63: the AGGREGATE leg --------------------------------------
      // The per-leg test above is blind to what the whole basket carries: one
      // leg can sit inside its own budget while the total does not.
      //
      // v5.71 keys the bound to the LARGEST rung, not to the sum of the
      // ladder. Under the shared one-way ratchet every older leg sits at or
      // beyond the live stop, so its loss-side distance is <= 0, the guard
      // above skips it, and a healthy ladder sums to roughly its newest rung
      // (~1.0% at T2, ~0.5% at T3, ~0.25% at T4). The 2.75% sum was therefore
      // unreachable and could not fire on the stacking it was written to
      // catch. Still confirmed across two cycles for the same reason as the
      // trail check: a tranche landing just before its stop is pushed reads
      // high for one cycle, which is the normal shape of a fill rather than
      // an incident."""

aud = rep(aud, AUD_COMMENT_OLD, AUD_COMMENT_NEW, 1,
          "aggregate comment matches the new bound")

# 1c. The doc box. The v5.63/v5.68/v5.70 paragraphs each assert that the sum is
#     the reachable design ceiling; that is the claim being corrected, so they
#     are replaced rather than appended to. The v5.63 paragraph's closing
#     sentence about the deep entry gap is still true and is kept verbatim.
AUD_BOX_OLD = """   //|                                                                   |
   //| v5.63 adds a SECOND, independent leg to this body: the SUM of the  |
   //| loss-side risk across the basket, measured against the stacked     |
   //| design budget (RiskPercent + InpRiskT2Pct + InpRiskT3Pct +        |
   //| InpRiskT4Pct, plus the same 50% tolerance). The per-leg peak above |
   //| cannot see it: four legs each comfortably inside 1.5% can still    |
   //| sum past what the pyramid was designed to carry. The two checks    |
   //| share the loop and the loss-side guard, and each owns its own      |
   //| latch, so a basket can report either, both, or neither.            |
   //|                                                                   |
   //| v5.68 DELIBERATELY CHANGED NOTHING HERE. With the shipped inputs    |
   //| the stacked budget resolves to 1.0 + 1.0 + 0.5 + 0.25 = 2.75%      |
   //| nominal, 4.125% with tolerance, which is the pyramid's design      |
   //| ceiling -- so the deep entry geometry (2.0*bh + 1.5*ATR) and the   |
   //| pyramiding tranches are already fully covered by this body and     |
   //| needed no new check. Note in particular that the DEEP ENTRY GAP     |
   //| cannot inflate either measure: both read the distance from entry   |
   //| to the LIVE broker stop, and that leg starts as m_basketRRUnit     |
   //| (bh + 0.5*ATR), which is 1R by construction and independent of how |
   //| wide the entry gap is. A correctly-sized scale-in therefore reads  |
   //| at its nominal rung and can never false-alarm here, while a real   |
   //| sizing bug still does.                                             |
   //|                                                                    |
   //| v5.70 FLOORS THE STACKED BUDGET AT RiskPercent. The sum above is   |
   //| RETAINED -- it is the ceiling the pyramid was designed against     |
   //| (2.75% nominal, 4.125% with tolerance), so a correctly-sized ladder|
   //| still sits exactly at it and still never alerts. The floor exists  |
   //| for the degenerate case: InpRiskT2Pct/T3Pct/T4Pct are operator     |
   //| inputs, and a zeroed or negative ladder would drive the sum BELOW  |
   //| the primary rung, resolving the ceiling under RiskPercent so that a|
   //| plain single-leg basket trips this leg before any scale-in. The    |
   //| bound is MathMax(nominalStackPct, RiskPercent) * 1.50, which is    |
   //| IDENTICAL to the v5.63 value whenever the tranche inputs are       |
   //| non-negative and never under-reads either. (MathMax takes exactly  |
   //| two arguments in MQL5, hence the two-operand floor rather than the |
   //| four-argument form.)                                               |"""

AUD_BOX_NEW = box([
    "",
    "v5.63 adds a SECOND, independent leg to this body: the loss-side",
    "risk AGGREGATED across the basket, measured against the largest",
    "single rung plus the same 50% tolerance. The per-leg peak above",
    "cannot see it: one leg can be comfortably inside its own budget",
    "while the total the basket puts at risk is not. The two checks",
    "share the loop and the loss-side guard, and each owns its own",
    "latch, so a basket can report either, both, or neither.",
    "",
    "v5.71 RE-KEYS THE BOUND TO THE LARGEST RUNG. v5.63/v5.70 measured",
    "the SUM of the four nominal rung risks (1.0 + 1.0 + 0.5 + 0.25 =",
    "2.75%, 4.125% with tolerance) and called that the design ceiling.",
    "The shipped geometry cannot reach it. Every rung is opened about 1R",
    "from ONE shared stop, and ApplyUnifiedSL() ratchets that stop",
    "forward only:",
    "",
    "    T2 at +1.0R  ->  stop at breakeven     (primary entry +/- fric)",
    "    T3 at +2.0R  ->  stop at primary +1.0R",
    "    T4 at +3.0R  ->  stop at primary +2.0R",
    "",
    "so an OLDER leg is already behind the stop and its loss-side",
    "distance is <= 0. The guard skips it, and it adds nothing to the",
    "sum. A healthy ladder therefore reads roughly its NEWEST rung:",
    "~1.0% at T2, ~0.5% at T3, ~0.25% at T4 -- never 2.75%. The 4.125%",
    "bound could not fire on the stacking it was documented to catch,",
    "and the only way to reach it was a sizing error the per-leg check",
    "had already reported.",
    "",
    "The bound is now max(rungs, RiskPercent) * 1.50, i.e. 1.50% on the",
    "shipped 1.0% ladder. A correctly-sized T1+T2 basket reads ~1.0%",
    "against 1.50%, so it keeps 33% headroom and still never alerts. The",
    "RiskPercent term preserves the v5.70 property: a zeroed or inverted",
    "InpRiskT2Pct/T3/T4 cannot resolve the ceiling below the primary",
    "rung, so a plain single-leg basket cannot trip this leg. A breach",
    "now means the ratchet is NOT absorbing the older legs, which is the",
    "failure this leg exists to report.",
    "",
    "Note that the DEEP ENTRY GAP cannot inflate either measure: both",
    "read the distance from entry to the LIVE broker stop, and that leg",
    "starts as m_basketRRUnit (bh + 0.5*ATR), which is 1R by",
    "construction and independent of how wide the entry gap is. A",
    "correctly-sized scale-in therefore reads at its nominal rung and",
    "can never false-alarm here, while a real sizing bug still does.",
])

# The full box is quoted above because every row of it asserts the claim being
# corrected. Nothing is appended: the old paragraphs would contradict the new
# bound sitting three lines beneath them.
aud = rep(aud, AUD_BOX_OLD, AUD_BOX_NEW, 1,
          "doc box restated around the shared ratchet")

save("CHighTableAuditor.mqh", aud, aud_eol)


# ======================================================================
# 2. COttoJournal.mqh -- the deep front-edge wording
# ======================================================================
print("COttoJournal.mqh")
jrn, jrn_eol = load("COttoJournal.mqh")

# 2a. The phase label names the model. "Pure Trend Continuation" is v5.52's
#     wording and predates the entry-style work; the shipped model is the deep
#     front-edge anticipation entry, so the header says so.
jrn = rep(jrn,
          '      string phase    = "C (Pure Trend Continuation)";',
          '      string phase    = "C (Deep Front-Edge Anticipation)";',
          1, "phase label names the deep front-edge model")

# 2b. The entry literals. The two `actionDesc` pairs are BYTE-IDENTICAL, so each
#     anchor has to carry its enclosing `if` line to stay unique --
#     LogSetupArmed reads `blk.type`, LogEntry reads `btype`.
#
#     The VETO wording (the "Projected Target" line and every "Front-Run veto"
#     comment) is deliberately untouched: the veto is still a front-run veto and
#     InpEnableFrontRunVeto still names it.
JRN_DESC_OLD = """      if(btype == BLOCK_SUPPORT)
         actionDesc = "SUPPORT (Front-Run Breakdown / Sell at Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)";
      else
         actionDesc = "RESISTANCE (Front-Run Breakout / Buy at Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)";"""

JRN_DESC_NEW = """      if(btype == BLOCK_SUPPORT)
         actionDesc = "SUPPORT (Deep Front-Edge Anticipation / Sell from Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)";
      else
         actionDesc = "RESISTANCE (Deep Front-Edge Anticipation / Buy from Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)";"""

jrn = rep(jrn, JRN_DESC_OLD, JRN_DESC_NEW, 1,
          "LogSetupArmed action descriptions")

JRN_ENTRY_OLD = """      if(blk.type == BLOCK_SUPPORT)
         actionDesc = "SUPPORT (Front-Run Breakdown / Sell at Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)";
      else
         actionDesc = "RESISTANCE (Front-Run Breakout / Buy at Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)";"""

JRN_ENTRY_NEW = """      if(blk.type == BLOCK_SUPPORT)
         actionDesc = "SUPPORT (Deep Front-Edge Anticipation / Sell from Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)";
      else
         actionDesc = "RESISTANCE (Deep Front-Edge Anticipation / Buy from Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)";"""

jrn = rep(jrn, JRN_ENTRY_OLD, JRN_ENTRY_NEW, 1,
          "LogEntry action descriptions")

jrn = rep(jrn,
          '" (Front-Run: near edge +/- 2.0*H +/- 1.5*ATR)"',
          '" (Deep Front-Edge: near edge +/- 2.0*H +/- 1.5*ATR)"',
          1, "arm journal trigger-price annotation")

save("COttoJournal.mqh", jrn, jrn_eol)


# ======================================================================
# 3. _probe/test_v563_trail_health.py -- worst-rung pins
# ======================================================================
print("_probe/test_v563_trail_health.py")
prb, prb_eol = load(os.path.join("_probe", "test_v563_trail_health.py"))

PRB_DOC_OLD = """   The aggregate leg exists because peakRiskPct is blind to how many legs are
   stacked behind the shared stop: four legs each inside their own budget can
   still sum past the stacked-pyramid design. v5.70 floors that sum at
   RiskPercent, so a degenerate tranche config cannot resolve the ceiling
   below the primary rung."""

PRB_DOC_NEW = """   The aggregate leg exists because peakRiskPct is blind to what the WHOLE
   basket puts at risk: one leg can sit inside its own budget while the total
   does not. v5.71 bounds that total by the LARGEST rung plus tolerance, not
   by the sum of the ladder: the shared ratchet absorbs every older leg, so
   the nominal sum is unreachable through the live geometry. RiskPercent still
   participates in the max, so a zeroed ladder cannot under-read."""

prb = rep(prb, PRB_DOC_OLD, PRB_DOC_NEW, 1,
          "probe docstring describes the worst-rung bound")

PRB_PIN_OLD = '''check("the stacked budget is the SUM of the four nominal rung risks",
      re.search(r"nominalStackPct\\s*=\\s*RiskPercent\\s*\\+\\s*InpRiskT2Pct\\s*\\+"
                r"\\s*InpRiskT3Pct\\s*\\+\\s*InpRiskT4Pct\\s*;", RISK_C) is not None)
# v5.70: the sum is floored at the primary rung before the tolerance is
# applied, so a zeroed or inverted tranche config cannot resolve the ceiling
# BELOW RiskPercent and trip the leg on a plain single-leg basket.
check("the stacked sum is floored at the primary rung",
      re.search(r"MathMax\\(\\s*nominalStackPct\\s*,\\s*RiskPercent\\s*\\)", RISK_C)
      is not None)
check("the floored allowance keeps the same 50% tolerance",
      re.search(r"maxAllowedStackPct\\s*=\\s*MathMax\\([^)]*\\)\\s*\\*\\s*1\\.50", RISK_C)
      is not None)'''

PRB_PIN_NEW = '''# v5.71: the bound is keyed to the LARGEST rung, not the sum of the ladder.
# The shared ratchet puts every older leg at or beyond the live stop, so the
# nominal sum (2.75%) is unreachable and could not fire on the stacking it was
# documented to catch. maxRungRiskPct folds in RiskPercent, which preserves the
# v5.70 property: a zeroed or inverted ladder cannot resolve the ceiling below
# the primary rung.
check("the aggregate bound is the largest rung, not the summed ladder",
      re.search(r"maxRungRiskPct\\s*=\\s*MathMax\\(\\s*InpRiskT2Pct\\s*,"
                r"\\s*InpRiskT3Pct\\s*\\)", RISK_C) is not None and
      re.search(r"maxRungRiskPct\\s*=\\s*MathMax\\(\\s*maxRungRiskPct\\s*,"
                r"\\s*InpRiskT4Pct\\s*\\)", RISK_C) is not None)
check("the unreachable nominal sum is retired from the leg",
      "nominalStackPct" not in RISK_C)
check("RiskPercent still participates, so a zeroed ladder cannot under-read",
      re.search(r"maxRungRiskPct\\s*=\\s*MathMax\\(\\s*maxRungRiskPct\\s*,"
                r"\\s*RiskPercent\\s*\\)", RISK_C) is not None)
check("the worst-rung allowance keeps the same 50% tolerance",
      re.search(r"maxAllowedAggPct\\s*=\\s*maxRungRiskPct\\s*\\*\\s*1\\.50", RISK_C)
      is not None)'''

prb = rep(prb, PRB_PIN_OLD, PRB_PIN_NEW, 1,
          "aggregate pins rewritten for the worst-rung bound")

prb = rep(prb,
          're.search(r"if\\s*\\(\\s*sumRiskPct\\s*>\\s*maxAllowedStackPct\\s*\\)", RISK_C)',
          're.search(r"if\\s*\\(\\s*sumRiskPct\\s*>\\s*maxAllowedAggPct\\s*\\)", RISK_C)',
          1, "aggregate comparison pin reads the new bound")

save(os.path.join("_probe", "test_v563_trail_health.py"), prb, prb_eol)


# ======================================================================
# 4. _probe/_mutate_v563.py -- retarget the aggregate mutations
# ======================================================================
print("_probe/_mutate_v563.py")
mut, mut_eol = load(os.path.join("_probe", "_mutate_v563.py"))

# 4a. The three old aggregate mutations are anchored on nominalStackPct and
#     maxAllowedStackPct, which no longer exist. Left alone they would report
#     as SKIPPED, and a skipped anchor fails the run by design -- so they are
#     RETARGETED, not dropped. The mutation count stays at 39 (0 skipped).
MUT_OLD = '''    ("the stacked budget is hardcoded instead of summing the live inputs", AUD,
     "      double nominalStackPct    = RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct;",
     "      double nominalStackPct    = 2.75;"),

    ("the aggregate allowance loses the 50% tolerance", AUD,
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;",
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent);"),

    ("the aggregate allowance loses its primary-rung floor", AUD,
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;",
     "      double maxAllowedStackPct = nominalStackPct * 1.50;"),'''

MUT_NEW = '''    # v5.71 retarget: the bound is the largest rung. "Simplifying" it back to the
    # summed ladder is the most plausible regression, and it is exactly the
    # unreachable form v5.71 removed -- so it must be caught.
    ("the aggregate bound reverts to summing the ladder", AUD,
     "      double maxRungRiskPct   = MathMax(InpRiskT2Pct, InpRiskT3Pct);",
     "      double maxRungRiskPct   = RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct;"),

    ("the aggregate allowance loses the 50% tolerance", AUD,
     "      double maxAllowedAggPct = maxRungRiskPct * 1.50;",
     "      double maxAllowedAggPct = maxRungRiskPct;"),

    ("the aggregate allowance drops the RiskPercent term", AUD,
     "      maxRungRiskPct          = MathMax(maxRungRiskPct, RiskPercent);\\n",
     ""),'''

mut = rep(mut, MUT_OLD, MUT_NEW, 1,
          "aggregate mutations retargeted to the worst-rung bound")

MUT_DOC_OLD = """  * hardcoding the stacked budget instead of summing the live inputs
  * dropping the v5.70 primary-rung floor so a zeroed ladder under-reads"""

MUT_DOC_NEW = """  * reverting the bound to the summed ladder (unreachable, so inert)
  * dropping the RiskPercent term so a zeroed ladder under-reads"""

mut = rep(mut, MUT_DOC_OLD, MUT_DOC_NEW, 1,
          "mutation docstring lists the new cases")

save(os.path.join("_probe", "_mutate_v563.py"), mut, mut_eol)


# ======================================================================
# 5. _probe/test_v557_frontrun.py -- retarget the journal captions
# ======================================================================
print("_probe/test_v557_frontrun.py")
frn, frn_eol = load(os.path.join("_probe", "test_v557_frontrun.py"))

FRN_PIN_OLD = '''check("the journal names the true deep front-edge geometry per direction",
      JRN_RAW.count("SUPPORT (Front-Run Breakdown / Sell at Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)") == 2 and
      JRN_RAW.count("RESISTANCE (Front-Run Breakout / Buy at Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)") == 2,
      "S=%d R=%d" % (JRN_RAW.count("Sell at Near Edge + 2.0*H + 1.5*ATR"),
                     JRN_RAW.count("Buy at Near Edge - 2.0*H - 1.5*ATR")))'''

FRN_PIN_NEW = '''check("the journal names the true deep front-edge geometry per direction",
      JRN_RAW.count("SUPPORT (Deep Front-Edge Anticipation / Sell from Near Edge + 2.0*H + 1.5*ATR | SL 1R above entry)") == 2 and
      JRN_RAW.count("RESISTANCE (Deep Front-Edge Anticipation / Buy from Near Edge - 2.0*H - 1.5*ATR | SL 1R below entry)") == 2,
      "S=%d R=%d" % (JRN_RAW.count("Sell from Near Edge + 2.0*H + 1.5*ATR"),
                     JRN_RAW.count("Buy from Near Edge - 2.0*H - 1.5*ATR")))
# v5.71: the entry captions moved onto the model's canonical vocabulary. The
# retired "Front-Run Breakdown/Breakout" phrasing described an entry that has
# not shipped since v5.66, so it must not survive -- while "Front-Run veto"
# legitimately remains, because the veto is still a front-run veto.
check("the retired Front-Run entry captions are gone",
      "Front-Run Breakdown" not in JRN_RAW and
      "Front-Run Breakout" not in JRN_RAW)
check("the front-run VETO wording is retained",
      "Front-Run veto anchor" in JRN_RAW)'''

frn = rep(frn, FRN_PIN_OLD, FRN_PIN_NEW, 1,
          "journal captions retargeted to deep front-edge")

frn = rep(frn,
          'JRN_RAW.count("(Front-Run: near edge +/- 2.0*H +/- 1.5*ATR)") == 1 and',
          'JRN_RAW.count("(Deep Front-Edge: near edge +/- 2.0*H +/- 1.5*ATR)") == 1 and',
          1, "armed-header annotation retargeted")

save(os.path.join("_probe", "test_v557_frontrun.py"), frn, frn_eol)


# ======================================================================
# 6. _probe/test_v540_hybrid.py -- the arm-journal phase pin
# ======================================================================
print("_probe/test_v540_hybrid.py")
hyb, hyb_eol = load(os.path.join("_probe", "test_v540_hybrid.py"))

HYB_OLD = '''check("arm journal stamps the constant continuation logic (v5.52)",
      ARM_J is not None and "C (Pure Trend Continuation)" in ARM_J)'''

HYB_NEW = '''# v5.71: the phase label moved onto the model's canonical vocabulary. The pin
# keeps its purpose -- the arm journal still stamps one CONSTANT logic string,
# not a per-setup value -- but names the string that now ships.
check("arm journal stamps the constant continuation logic (v5.71)",
      ARM_J is not None and "C (Deep Front-Edge Anticipation)" in ARM_J and
      "Pure Trend Continuation" not in ARM_J)'''

hyb = rep(hyb, HYB_OLD, HYB_NEW, 1, "arm-journal phase pin retargeted")

save(os.path.join("_probe", "test_v540_hybrid.py"), hyb, hyb_eol)

print("\nv5.71 patch applied: 6 files, 16 replacements.")
