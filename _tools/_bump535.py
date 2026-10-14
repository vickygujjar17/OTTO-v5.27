"""
One-shot v5.34 -> v5.35 version bump for the OTTO EA tree.

Rewrites #property version "5.34" -> "5.35" in otto.mq5 and every .mqh
header. Historical annotations naming the release a feature LANDED in --
the many '// v5.34:' notes and the 'v5.34 Part N' markers that document
the two-phase lifecycle, the shifted entry, the veto conversion and the
Phase 2 market-trigger split -- must survive untouched, so any line
containing 'FIX (' is skipped outright and every remaining pattern is
anchored on a specific human-facing prefix rather than on the version
number alone. The one non-prefixed form is the versioned INPUT GROUP label
('  [N] NAME <em-dash> vX'), which a comment can never satisfy.

v5.35 note: this release is the HYBRID EXECUTION build. Phase 1
(touches==0) still rests a LIMIT at its mapped entry, exactly as Pine's
strategy.entry does. Phase 2 (touches==1) is a RECLAIMED zone traded as a
REVERSAL, and its entry is now a dynamically-triggered MARKET order fired
by COttoOrderManager::CheckPhase2MarketTriggers() the moment the market
trades THROUGH the shifted outer boundary, instead of resting a limit at
it. No new source FILE is added -- the execution split lives in the
existing order/block managers -- so FILES is unchanged from v5.34.

The [9]/[10]/[11]/[12] engine groups keep their release stamp so an
operator reading the Inputs dialog sees which build the flags belong to.
The README's 'Current base' line is bumped too -- it is the documented
release marker the build tree is checked against, and no #property pattern
would ever find it.

Every em-dash inside an anchored banner is matched with a single '.' so the
script is agnostic to how the dash is encoded, while the patterns still
cannot match a bare version inside a comment.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.34"
NEW = "5.35"

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
# 'FIX (vX)' historical annotations are already skipped by the caller, and
# the '// v5.34:' / 'v5.34 Part N' engine notes are untouched because no
# pattern here matches a bare version in a comment. A single '.' stands in
# for the em-dash so the pattern never depends on the dash's encoding.
PATTERNS += [
    (re.compile(r'(Master Build Port \(v)' + re.escape(OLD) + r'(\))'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(Master Build \(v)' + re.escape(OLD) + r'(\))'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(OTTO EA v)' + re.escape(OLD) + r'( . 28-Pair Institutional Master Build)'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(#property description "OTTO v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[9\] CURRENCY VECTOR & AFFINITY ENGINE . v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[10\] HIGH TABLE AUDITOR . v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[11\] MANUAL TRADE ADOPTION . v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
    (re.compile(r'(\[12\] VETO REVERSAL CONVERSIONS . v)' + re.escape(OLD)),
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
