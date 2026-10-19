"""
v5.70 source patch -- CONFIG-PROOF AGGREGATE RISK CEILING.

v5.63 measured the aggregate leg against the SUM of the four nominal rung
risks (RiskPercent + InpRiskT2Pct + InpRiskT3Pct + InpRiskT4Pct) plus the
same 50% tolerance the per-leg check uses. That sum is the pyramid's design
ceiling and is DELIBERATELY retained: with the shipped defaults it resolves
to 2.75% nominal / 4.125% with tolerance, so a correctly-sized ladder sits
exactly at it and never alerts.

The defect is that the sum is only an upper bound while the tranche inputs
are positive. A malformed config -- a zeroed, negative, or mis-typed
InpRiskT2Pct/T3Pct/T4Pct -- drives the sum BELOW RiskPercent, and the ceiling
then resolves under the primary rung. A plain single-leg basket at its
nominal risk would read above the ceiling and trip the aggregate leg before
any scale-in had ever happened, which is precisely the false CRITICAL v5.46
removed the MathAbs form to avoid.

v5.70 floors the sum at RiskPercent:

    maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;

The floor is INERT under every sane config (nominalStackPct >= RiskPercent
whenever the tranche inputs are non-negative), so the shipped ladder's
ceiling is unchanged at 4.125%. It is a guard against degenerate input, not
a behaviour change.

NOTE ON MathMax ARITY: MQL5's MathMax takes exactly TWO arguments
(`double MathMax(double value1, double value2);`). The four-argument form
that appears in the release sketch does not compile, and all 17 existing
MathMax call sites in this tree pass two. The two-argument floor above is
the compiling equivalent.

Every replacement is an exact literal with an asserted match count, so a
drifted anchor aborts instead of silently patching the wrong line.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOX_WIDTH = 75


def load(name):
    """Read as LF-normalised text plus the file's own EOL.

    mqh/mq5 are CRLF and the probes are LF, so anchors are written once with
    plain \\n and re-expanded on save. Reading through a \\r\\n -> \\n
    normalisation is what makes a multi-line anchor matchable at all.
    """
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
    """Render the `//| ... |` documentation box at BOX_WIDTH."""
    out = []
    for ln in lines:
        body = ("   //| " + ln) if ln else "   //|"
        out.append(body + " " * (BOX_WIDTH - 1 - len(body)) + "|")
    return "\n".join(out)


# ======================================================================
# 1. CHighTableAuditor.mqh -- the floored ceiling
# ======================================================================
print("CHighTableAuditor.mqh")
aud, aud_eol = load("CHighTableAuditor.mqh")

# 1a. The ceiling itself. nominalStackPct on the preceding line is UNCHANGED,
#     so the "sum of the four nominal rung risks" assertion keeps its anchor.
AUD_CEIL_OLD = "      double maxAllowedStackPct = nominalStackPct * 1.50;"

AUD_CEIL_NEW = """      // v5.70: floor the design sum at the primary rung. The sum is only an
      // upper bound while the tranche inputs are positive -- a zeroed or
      // inverted InpRiskT2Pct/T3/T4 would resolve the ceiling BELOW
      // RiskPercent and trip this leg on a plain single-leg basket. The floor
      // is inert under every sane config, so the shipped ladder is unaffected.
      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;"""

aud = rep(aud, AUD_CEIL_OLD, AUD_CEIL_NEW, 1,
          "aggregate ceiling floored at RiskPercent")

# 1b. The v5.70 rationale goes at the END of the existing doc box, before the
#     closing rule. Appending rather than rewriting keeps the v5.46/v5.63/v5.68
#     history intact -- those paragraphs still describe the shipped geometry.
AUD_BOX_ANCHOR = """   //| sizing bug still does.                                             |
   //+------------------------------------------------------------------+"""

AUD_BOX_NEW = box([
    "sizing bug still does.",
    "",
    "v5.70 FLOORS THE STACKED BUDGET AT RiskPercent. The sum above is",
    "RETAINED -- it is the ceiling the pyramid was designed against",
    "(2.75% nominal, 4.125% with tolerance), so a correctly-sized ladder",
    "still sits exactly at it and still never alerts. The floor exists",
    "for the degenerate case: InpRiskT2Pct/T3Pct/T4Pct are operator",
    "inputs, and a zeroed or negative ladder would drive the sum BELOW",
    "the primary rung, resolving the ceiling under RiskPercent so that a",
    "plain single-leg basket trips this leg before any scale-in. The",
    "bound is MathMax(nominalStackPct, RiskPercent) * 1.50, which is",
    "IDENTICAL to the v5.63 value whenever the tranche inputs are",
    "non-negative and never under-reads either. (MathMax takes exactly",
    "two arguments in MQL5, hence the two-operand floor rather than the",
    "four-argument form.)",
]) + "\n   //+------------------------------------------------------------------+"

aud = rep(aud, AUD_BOX_ANCHOR, AUD_BOX_NEW, 1,
          "v5.70 rationale appended to the doc box")

save("CHighTableAuditor.mqh", aud, aud_eol)


# ======================================================================
# 2. _probe/test_v563_trail_health.py -- the ceiling pins
# ======================================================================
print("_probe/test_v563_trail_health.py")
prb, prb_eol = load(os.path.join("_probe", "test_v563_trail_health.py"))

PRB_OLD = '''check("the stacked allowance keeps the same 50% tolerance",
      re.search(r"maxAllowedStackPct\\s*=\\s*nominalStackPct\\s*\\*\\s*1\\.50", RISK_C)
      is not None)'''

PRB_NEW = '''# v5.70: the sum is floored at the primary rung before the tolerance is
# applied, so a zeroed or inverted tranche config cannot resolve the ceiling
# BELOW RiskPercent and trip the leg on a plain single-leg basket.
check("the stacked sum is floored at the primary rung",
      re.search(r"MathMax\\(\\s*nominalStackPct\\s*,\\s*RiskPercent\\s*\\)", RISK_C)
      is not None)
check("the floored allowance keeps the same 50% tolerance",
      re.search(r"maxAllowedStackPct\\s*=\\s*MathMax\\([^)]*\\)\\s*\\*\\s*1\\.50", RISK_C)
      is not None)'''

prb = rep(prb, PRB_OLD, PRB_NEW, 1, "ceiling pins rewritten for the floor")

# The module docstring still describes the bound as the bare design sum.
PRB_DOC_OLD = """   The aggregate leg exists because peakRiskPct is blind to how many legs are
   stacked behind the shared stop: four legs each inside their own budget can
   still sum past the stacked-pyramid design."""

PRB_DOC_NEW = """   The aggregate leg exists because peakRiskPct is blind to how many legs are
   stacked behind the shared stop: four legs each inside their own budget can
   still sum past the stacked-pyramid design. v5.70 floors that sum at
   RiskPercent, so a degenerate tranche config cannot resolve the ceiling
   below the primary rung."""

prb = rep(prb, PRB_DOC_OLD, PRB_DOC_NEW, 1, "probe docstring records the v5.70 floor")

save(os.path.join("_probe", "test_v563_trail_health.py"), prb, prb_eol)

# ======================================================================
# 3. _probe/_mutate_v563.py -- retarget + new floor mutation
# ======================================================================
print("_probe/_mutate_v563.py")
mut, mut_eol = load(os.path.join("_probe", "_mutate_v563.py"))

MUT_OLD = '''    ("the aggregate allowance loses the 50% tolerance", AUD,
     "      double maxAllowedStackPct = nominalStackPct * 1.50;",
     "      double maxAllowedStackPct = nominalStackPct;"),'''

MUT_NEW = '''    ("the aggregate allowance loses the 50% tolerance", AUD,
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;",
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent);"),

    ("the aggregate allowance loses its primary-rung floor", AUD,
     "      double maxAllowedStackPct = MathMax(nominalStackPct, RiskPercent) * 1.50;",
     "      double maxAllowedStackPct = nominalStackPct * 1.50;"),'''

mut = rep(mut, MUT_OLD, MUT_NEW, 1,
          "tolerance mutation retargeted + floor mutation added")

MUT_DOC_OLD = """   * hardcoding the stacked budget instead of summing the live inputs
   * dropping the geometry push, or gating it on the wrong branch"""

MUT_DOC_NEW = """   * hardcoding the stacked budget instead of summing the live inputs
   * dropping the v5.70 primary-rung floor so a zeroed ladder under-reads
   * dropping the geometry push, or gating it on the wrong branch"""

mut = rep(mut, MUT_DOC_OLD, MUT_DOC_NEW, 1, "mutation docstring lists the new case")

save(os.path.join("_probe", "_mutate_v563.py"), mut, mut_eol)

print("\nv5.70 patch applied: 3 files, 6 replacements.")
