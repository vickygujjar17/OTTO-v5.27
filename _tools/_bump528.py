"""
One-shot v5.27 -> v5.28 version bump for the OTTO EA tree.

Rewrites #property version "5.27" -> "5.28" in otto.mq5 and every .mqh
header. Historical FIX annotations naming an older release must survive
untouched, so any line containing 'FIX (' is skipped outright rather than
pattern-matched.

v5.28 note: the 'Pine Script Master Build Port (vX)' banner in otto.mq5 was
NOT matched by the previous bump scripts -- their pattern required
'Master Build (vX)' with no intervening word, so the 'Port' variant fell
through and stayed one release behind. It is matched explicitly here.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.27"
NEW = "5.28"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "OttoDefines.mqh"]

PATTERNS = [
    (re.compile(r'(#property\s+version\s+")' + re.escape(OLD) + r'(")'),
     r'\g<1>' + NEW + r'\g<2>'),
]

# Human-facing banner / description strings that name the live release.
# 'FIX (vX)' historical annotations are already skipped by the caller, so the
# v5.26 engine notes in COttoCorrelationFilter.mqh are never touched.
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
]


def bump(path):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print("  MISSING  %s" % path)
        return 0
    text = io.open(full, encoding="utf-8", newline="").read()
    lines = text.split("\n")
    changed = 0
    for i, line in enumerate(lines):
        if "FIX (" in line:
            continue
        new_line = line
        for pat, rep in PATTERNS:
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
        total += bump(f)
    print()
    print("changed %d line(s)" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
