"""
One-shot v5.33 -> v5.34 version bump for the OTTO EA tree.

Rewrites #property version "5.33" -> "5.34" in otto.mq5 and every .mqh
header. Historical annotations naming an older release (the many '// v5.33:'
notes that document the ADOPTION feature, and the older 'FIX (v5.2x)' lines)
must survive untouched, so any line containing 'FIX (' is skipped outright and
every remaining pattern is anchored on a specific human-facing prefix rather
than on the version number alone. The one non-prefixed form is the versioned
INPUT GROUP label ('  [N] NAME <em-dash> vX'), which a comment can never
satisfy.

v5.34 note: this release completes the TWO-PHASE reversal-S/R execution hook.
A block is now a two-touch lifecycle: Touch 1 is the Phase 1 fade, and a
consumed Touch 1 promotes the SAME zone to a Phase 2 REVERSAL whose entry sits
at the shifted (outer) boundary. No new source FILE is added -- the lifecycle
lives in the existing order/block managers -- so FILES is unchanged from
v5.33.

The [12] group ('VETO REVERSAL CONVERSIONS') is this release's new group and
is stamped with the release like every engine group before it, so an operator
reading the Inputs dialog sees which build the flags belong to. The README's
'Current base' line is bumped too -- it is the documented release marker the
build tree is checked against, and no #property pattern would ever find it.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.33"
NEW = "5.34"

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
# 'FIX (vX)' historical annotations are already skipped by the caller, and the
# v5.33 '// v5.33:' engine notes are untouched because no pattern here matches a
# bare version in a comment. The v5.34 additions are the three versioned engine
# group labels ([9]/[10]/[11]) plus this release's own new group [12].
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
    (re.compile(r'(\[11\] MANUAL TRADE ADOPTION \u2014 v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[12\] VETO REVERSAL CONVERSIONS \u2014 v)' + re.escape(OLD)),
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
