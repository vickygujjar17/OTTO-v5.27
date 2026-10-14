"""
One-shot v5.36 -> v5.37 version bump for the OTTO EA tree.

Rewrites #property version "5.36" -> "5.37" in otto.mq5 and every .mqh
header. Historical annotations naming the release a feature LANDED in --
the many '// v5.3x:' notes, the 'v5.34 Part N' two-phase-lifecycle markers
and the 'v5.36' veto-conversion notes -- must survive untouched, so any
line containing 'FIX (' is skipped outright and every remaining pattern is
anchored on a specific human-facing prefix rather than on the version
number alone. The one non-prefixed form is the versioned INPUT GROUP label
('  [N] NAME <em-dash> vX'), which a comment can never satisfy.

v5.37 note: this release replaces the Phase-1 RESTING BROKER LIMIT with an
in-memory VIRTUAL PENDING ORDER. A Phase-1 block no longer rests a physical
TRADE_ACTION_PENDING; COttoOrderManager::ArmVirtualOrder() stores a
lightweight SVirtualOrder descriptor in m_virtualOrders[] and
CheckVirtualTriggers() fires a real TRADE_ACTION_DEAL the instant the live
price CROSSES the trigger. The registry is keyed on block.serial (NOT an
array index) because COttoBlockManager::RemoveBlock() compacts m_blocks[],
so COttoBlockManager gains FindBlockIndexBySerial(). Every placement gate
(spread, can_place, correlation, consensus, sentiment, stop distance,
margin, volume) and the block geometry written at arm time are unchanged --
only the EXECUTION mechanism moves from a resting pending to a crossed
deal, which removes the stale broker pending that the old path left behind
whenever a zone was re-phased or reaped. No new source FILE is added, so
FILES is unchanged from v5.36.

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

OLD = "5.36"
NEW = "5.37"

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
# the already-SHIPPED '// v5.3x:' / 'v5.36:' engine notes are untouched
# because no pattern here matches a bare version in a comment. A single '.'
# stands in for the em-dash so the pattern never depends on the dash's
# encoding.
PATTERNS += [
    # The 'vX.NNN Part N' markers name the IN-FLIGHT refactor, not a landed
    # release: v5.36 shipped WITHOUT the virtual-order engine, so those
    # annotations describe code that first reaches a build here, in v5.37.
    # Re-stamping them keeps the part-numbered narrative honest. The bare
    # 'v5.36:' notes (veto-to-reversal conversion) name a feature that DID
    # land in v5.36 and are deliberately left alone.
    (re.compile(r'(v)' + re.escape(OLD) + r'( Part)'),
     r'\g<1>' + NEW + r'\g<2>'),
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
