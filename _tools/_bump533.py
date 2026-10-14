"""
One-shot v5.32 -> v5.33 version bump for the OTTO EA tree.

Rewrites #property version "5.32" -> "5.33" in otto.mq5 and every .mqh
header. Historical FIX annotations naming an older release must survive
untouched, so any line containing 'FIX (' is skipped outright rather than
pattern-matched.

NOTE ON SCOPING: the v5.33 sources describe their OWN changes in '// v5.33:'
comment blocks, so OLD ('5.32') only ever matches genuine v5.32 text. The
reverse hazard is the one to watch -- a bare '5.32' -> '5.33' rewrite would
corrupt the historical 'v5.32:' annotations that document the previous
release's behaviour, which is why every pattern below is anchored on a
specific human-facing prefix rather than on the version number alone. The one
non-prefixed pattern is the versioned INPUT GROUP label, which is a fixed
form ('  [N] NAME <em-dash> vX') that a comment can never satisfy.

v5.33 note: this release adds MANUAL TRADE ADOPTION. The EA can take over a
magic-0 position the OPERATOR opened by hand on its own chart symbol and
manage it with the whole basket machinery (unified trail, milestone ladder,
pyramid rungs, smart trim, DD halt, journal). No new source file is added --
the feature lives in the existing order manager / journal / defines and is
exposed to the watchdog through a widened SetTrackedLegs() -- so FILES is
unchanged from v5.32.

The new input group ('[11] MANUAL TRADE ADOPTION') is bumped with the release
so the Inputs dialog names the live version, exactly as the two engine groups
before it. The README's 'Current base' line is bumped too -- it is the
documented release marker the build tree is checked against, and it lives
outside the compiled sources so no #property pattern would ever find it.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.32"
NEW = "5.33"

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
# v5.32 engine notes are never touched. The v5.33 addition to this list is the
# MANUAL TRADE ADOPTION input GROUP label, which names the release in the
# Inputs dialog where an operator reads it.
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
