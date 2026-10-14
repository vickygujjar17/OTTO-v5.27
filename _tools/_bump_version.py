"""
Version bump for the OTTO EA tree.

Standing rule: EVERY source change bumps the release stamp. Run this before
committing; it rewrites all 20 human-facing version rows in one pass so no
partial bump can leave the tree stamping two different releases.

Rewrites the CURRENT release (auto-detected from OttoDefines.mqh, so the
anchors can never rot into dead strings) to the next minor release.

What is bumped:
  * '#property version "X.Y"'            -- all 11 files (otto.mq5 + 10 .mqh)
  * 'Pine Script Master Build Port (vX.Y)'   -- otto.mq5 file banner
  * 'OTTO EA vX.Y'                        -- otto.mq5 startup Print + OttoDefines banner
  * '#property description "OTTO vX.Y'    -- the EA description
  * '[N] NAME <em-dash> vX.Y'             -- the four versioned input-group labels
  * README '**Current base: vX.Y**'       -- the documented base release

What is deliberately NOT bumped: historical annotations naming the release a
feature LANDED in -- the many '// v5.3x:' notes and 'FIX (vX)' markers. The
bump pass skips any line containing 'FIX (' outright, and every remaining
pattern is anchored on a specific human-facing prefix rather than on the
version number alone, so no comment can be rewritten by accident. The one
non-prefixed form is the versioned INPUT GROUP label, which a comment can never
satisfy. The post-bump staleness census applies the same 'FIX (' exemption, so
a historical marker is never reported as stale.

Every em-dash inside an anchored banner is matched with a single '.' so the
script is agnostic to how the dash is encoded.

Usage:
    python _tools/_bump_version.py            # auto: current -> next minor
    python _tools/_bump_version.py 5.46       # current -> explicit release

Passing the current release explicitly is a harmless no-op; the bare form always
advances one minor, so use an explicit target for an idempotent bump-then-verify.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
         "OttoDefines.mqh"]

DOC_FILES = ["README.md"]

_VERSION_RE = re.compile(r'#property version\s+"(\d+)\.(\d+)"')


def detect_current():
    """Read the live release from OttoDefines.mqh -- never a hardcoded anchor."""
    text = io.open(os.path.join(ROOT, "OttoDefines.mqh"), encoding="utf-8",
                   errors="replace", newline="").read()
    stamps = set(_VERSION_RE.findall(text))
    if len(stamps) != 1:
        raise SystemExit("ABORT: OttoDefines.mqh carries %d version stamps: %s"
                         % (len(stamps), sorted(stamps)))
    major, minor = stamps.pop()
    return "%s.%s" % (major, minor), (int(major), int(minor))


def build_patterns(old, new):
    o = re.escape(old)
    return [
        (re.compile(r'(#property\s+version\s+")' + o + r'(")'),
         r'\g<1>' + new + r'\g<2>'),
        (re.compile(r'(Master Build Port \(v)' + o + r'(\))'),
         r'\g<1>' + new + r'\g<2>'),
        (re.compile(r'(Master Build \(v)' + o + r'(\))'),
         r'\g<1>' + new + r'\g<2>'),
        (re.compile(r'(OTTO EA v)' + o),
         r'\g<1>' + new),
        (re.compile(r'(#property description "OTTO v)' + o),
         r'\g<1>' + new),
        (re.compile(r'(\[\d+\] [A-Z0-9 &/-]+ . v)' + o),
         r'\g<1>' + new),
    ]


def build_doc_patterns(old, new):
    o = re.escape(old)
    return [
        (re.compile(r'(\*\*Current base: v)' + o + r'(\*\*)'),
         r'\g<1>' + new + r'\g<2>'),
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
        # Historical 'FIX (vX)' annotations name the release a fix LANDED in.
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


def census(old):
    """Every surviving literal `old` in the shipped sources + docs.

    Mirrors the bump() exemption above: a line carrying a historical
    'FIX (vX)' annotation LEGITIMATELY names the release the fix landed in, so
    it is not stale. Without this the guard fires on exactly the markers the
    bump pass was written to preserve.
    """
    hits = []
    for path in FILES + DOC_FILES:
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        for i, line in enumerate(io.open(full, encoding="utf-8",
                                         errors="replace", newline="").read()
                                 .split("\n"), 1):
            if old in line and "FIX (" not in line:
                hits.append("%s:%d: %s" % (path, i, line.strip()))
    return hits


def main():
    old, parts = detect_current()
    if len(sys.argv) > 1:
        new = sys.argv[1]
        if not re.match(r"^\d+\.\d+$", new):
            raise SystemExit("ABORT: '%s' is not a X.Y release" % new)
    else:
        major, minor = parts
        new = "%d.%d" % (major, minor + 1)

    if new == old:
        # Idempotent, not fatal: re-running the bump on an already-bumped tree
        # must be a harmless no-op, so a scripted bump-then-verify sequence
        # cannot be broken by its own second invocation.
        print("no-op: %s is already the current release" % old)
        return 0

    print("=" * 74)
    print("v%s -> v%s VERSION BUMP" % (old, new))
    print("=" * 74)

    total = 0
    for f in FILES:
        total += bump(f, build_patterns(old, new))
    for f in DOC_FILES:
        total += bump(f, build_doc_patterns(old, new))

    print()
    print("changed %d line(s)" % total)

    # Guard: a partial bump is worse than no bump, so refuse to exit clean if
    # the OLD release is still stamped anywhere operator-visible.
    stale = census(old)
    if stale:
        print("\n*** STALE v%s REMAINS ***" % old)
        for h in stale:
            print("  " + h)
        return 1

    print("no v%s literal remains in the shipped sources or docs" % old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
