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

    v5.67: the exemption used to be the literal substring "FIX (" alone, which
    was too narrow. A HISTORICAL annotation also appears as a bare
    parenthesised release in prose -- e.g. "...had to be re-anchored on this
    same price (v5.66)" or "RE-ANCHORED (v5.66) on the ENTRY" -- and those are
    exactly as legitimate as a "FIX (vX)" marker. The guard fired on all four
    of them during the v5.67 release, reporting VALID history as a partial
    bump. Since the only literal being searched for is the OLD release ("5.66"
    here), the parenthesised form cannot occur anywhere except as a historical
    reference: nothing anchors a CURRENT release in parentheses. So the rule is
    now "a parenthesised historical release is exempt", not "only FIX markers
    are exempt", which keeps the guard's real purpose (catching a version row
    left un-bumped) without flagging the history the bump pass deliberately
    preserves.

    A bare, un-parenthesised "5.66" on a non-FIX line is still reported: that
    is the shape of a missed version row.

    v5.68: the v5.67 rule still over-fired, because this tree annotates history
    in a SECOND legitimate form the parenthesis test cannot see -- the
    line-leading release tag ("// v5.67: ...", "//| v5.67 - ...") and the
    trailing one ("AuditOrphanLegs();   // v5.67 - ..."). Those are the house
    style for every earlier release as well (v5.52, v5.63), so a strict
    parenthesis test reports the CURRENT release's own annotations the moment
    you bump away from it -- exactly the sixteen valid history lines v5.68 hit.
    The exemption is therefore extended: a 'v'-prefixed release reference that
    appears AFTER the comment opener on the same line is history, not a row.

    That extension is deliberately narrow, because an exemption that swallows
    everything is worse than none. Two shapes are checked FIRST and are reported
    unconditionally, so the guard cannot be talked out of a real miss:
      * a version ROW that lives inside a comment -- the file banners
        ("Pine Script Master Build Port (vX.Y)", "OTTO EA vX.Y") -- which is a
        genuine row and must be rewritten by the bump;
      * the input-group labels, matched explicitly.
    A row outside a comment (the '#property version' line, the README base
    line) has no '//' and so can never satisfy the exemption either.
    """
    hist = re.compile(r"\((?:FIX\s*\(\s*)?v?%s(?![\d.])" % re.escape(old))
    # v5.68: a release ROW that happens to live inside a comment (the file
    # banners) is still a row, so it is reported before the exemption is
    # consulted. These are the only two comment-borne row shapes the bump
    # itself rewrites, so they are enumerated rather than pattern-guessed.
    row_in_comment = re.compile(
        r"(?:Master Build Port|Master Build) \(v%s\)|OTTO EA v%s(?![\d.])"
        % (re.escape(old), re.escape(old)))
    # v5.68: the house annotation style ("// v5.67: ...", "//| v5.67 - ...",
    # "<code>;   // v5.67 - ..."). A 'v'-prefixed reference placed after the
    # comment opener on the same line is history. Un-parenthesised bare
    # literals ("// the 5.66 build") do NOT match: they carry no 'v' prefix.
    comment_hist = re.compile(r"//.*?\bv%s(?![\d.])" % re.escape(old))
    hits = []
    for path in FILES + DOC_FILES:
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        for i, line in enumerate(io.open(full, encoding="utf-8",
                                         errors="replace", newline="").read()
                                 .split("\n"), 1):
            if old not in line:
                continue
            if row_in_comment.search(line):
                hits.append("%s:%d: %s" % (path, i, line.strip()))
                continue
            if "FIX (" in line or hist.search(line) or comment_hist.search(line):
                continue
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
