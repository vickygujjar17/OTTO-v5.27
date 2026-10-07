"""
One-shot v5.37 -> v5.38 version bump for the OTTO EA tree.

Rewrites #property version "5.37" -> "5.38" in otto.mq5 and every .mqh
header. Historical annotations naming the release a feature LANDED in -- the
many '// v5.3x:' notes and the 'v5.37 Part N' virtual-engine markers -- must
survive untouched, so any line containing 'FIX (' is skipped outright and
every remaining pattern is anchored on a specific human-facing prefix rather
than on the version number alone. The one non-prefixed form is the versioned
INPUT GROUP label ('  [N] NAME <em-dash> vX'), which a comment can never
satisfy.

v5.38 note: this release is a CLEANUP of the v5.37 virtual-order engine, not a
new mechanism. Three things change:
  1. The ARM half is renamed COttoOrderManager::PlaceLimitOrder() ->
     ArmVirtualOrder(). It never sent a broker LIMIT after v5.37, so the old
     name lied about what it does; no behaviour changes with the rename.
  2. The hard anti-duplicate shield IsOrderAlreadyLiveAtPrice() now scans the
     in-memory VIRTUAL ORDER BOOK (m_virtualOrders[]) instead of the broker's
     pending pool. Since v5.37 nothing rests at the broker, so scanning
     OrdersTotal() could never see our own armed setups -- the shield was a
     no-op against the one duplicate source that still exists.
  3. ONE JOURNAL PER SETUP. The per-setup session id is minted at ARM time,
     stored on SVirtualOrder.sessionId, and reused by the fire path and the
     cancel/conversion sweep. COttoJournal::LogOrderPlaced() becomes
     LogVirtualOrderArmed(string tradeId, ...) and creates the file; LogEntry()
     APPENDS into it (OpenAppend, with an OpenWrite fallback). The arm
     snapshot, the fill and the exit therefore resolve to a single .txt file.
No new source FILE is added, so FILES is unchanged from v5.37.

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

OLD = "5.37"
NEW = "5.38"

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
# the already-SHIPPED '// v5.3x:' / 'v5.37 Part N' engine notes are untouched
# because no pattern here matches a bare version in a comment. A single '.'
# stands in for the em-dash so the pattern never depends on the dash's
# encoding.
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
