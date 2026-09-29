"""
One-shot v5.31 -> v5.32 version bump for the OTTO EA tree.

Rewrites #property version "5.31" -> "5.32" in otto.mq5 and every .mqh
header. Historical FIX annotations naming an older release must survive
untouched, so any line containing 'FIX (' is skipped outright rather than
pattern-matched.

NOTE ON SCOPING: the v5.32 sources describe their OWN changes in '// v5.32:'
comment blocks, so OLD ('5.31') only ever matches genuine v5.31 text. The
reverse hazard is the one to watch -- a bare '5.31' -> '5.32' rewrite would
corrupt the historical 'v5.31:' annotations that document the previous
release's behaviour, which is why every pattern below is anchored on a
specific human-facing prefix rather than on the version number alone.

v5.32 note: this release introduces the "High Table" watchdog
(CHighTableAuditor.mqh) -- a decoupled, timer-driven auditor that keeps a
CSV evidence trail and emails one alert per incident via latched
dispatches. The new module is added to FILES so its #property version
tracks the release like every other source.

v5.32 note: this release also ships four fixes that share one theme -- making
the pyramid and milestone machinery ATTRIBUTABLE. (1) The half-risk rung's
liveness is resolved once from the inputs, so an unreachable rung stops being
counted and the phantom "risk now -0.5R" journal line disappears. (2) Every
silent refusal in AddPyramidTranche now names its cause. (3) A bucket-latched
tranche-2 crossing trace prints the decision inputs. (4) The basket's original
1R is persisted to a ticket-keyed GlobalVariable so a cold restart no longer
re-derives it from a ratcheted stop. The 'Pine Script Master Build Port (vX)'
banner and the 'OTTO EA vX' startup banner are both matched explicitly,
matching the v5.28-v5.31 bump scripts, so no human-facing release string is
left one version behind.

The README's 'Current base' line is bumped too -- it is the documented
release marker the build tree is checked against, and it lives outside the
compiled sources so no #property pattern would ever find it.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.31"
NEW = "5.32"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
         "OttoDefines.mqh"]

PATTERNS = [
    (re.compile(r'(#property\s+version\s+")' + re.escape(OLD) + r'(")'),
     r'\g<1>' + NEW + r'\g<2>'),
]

# Human-facing banner / description strings that name the live release.
# 'FIX (vX)' historical annotations are already skipped by the caller, so the
# v5.31 engine notes are never touched. The two v5.32 additions to this list
# are the INPUT GROUP label and the versioned engine group in OttoDefines,
# which name the release in the Inputs dialog where an operator reads it.
PATTERNS += [
    (re.compile(r'(Master Build Port \(v)' + re.escape(OLD) + r'(\))'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(Master Build \(v)' + re.escape(OLD) + r'(\))'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(OTTO EA v)' + re.escape(OLD) + r'( \u2014 28-Pair Institutional Master Build)'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(#property description "OTTO v)' + re.escape(OLD) + r'( \u2014)'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(\[9\] CURRENCY VECTOR & AFFINITY ENGINE \u2014 v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[10\] HIGH TABLE AUDITOR \u2014 v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
]

# Docs only: the README pins the live base release in prose.
DOC_PATTERNS = [
    (re.compile(r'(\*\*Current base: v)' + re.escape(OLD) + r'(\*\*)'),
     r'\g<1>' + NEW + r'\g<2>'),
]


def bump(path, patterns):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print("  MISSING  %s" % path)
        return 0
    text = io.open(full, encoding="utf-8", errors="replace", newline="").read()
    lines = text.split("\n")
    changed = 0
    for i, line in enumerate(lines):
        if "FIX (" in line:
            continue
        new_line = line
        for pat, rep in patterns:
            new_line = pat.sub(rep, new_line)
        if new_line != line:
            print("  %s:%d" % (path, i + 1))
            print("    - %s" % line.strip())
            print("    + %s" % new_line.strip())
            lines[i] = new_line
            changed += 1
    if changed:
        io.open(full, "w", encoding="utf-8", newline="").write("\n".join(lines))
    return changed


def main():
    print("=" * 74)
    print("v%s -> v%s VERSION BUMP" % (OLD, NEW))
    print("=" * 74)
    total = 0
    for f in FILES:
        total += bump(f, PATTERNS)
    for f in ("README.md",):
        total += bump(f, DOC_PATTERNS)
    print()
    print("changed %d line(s)" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
