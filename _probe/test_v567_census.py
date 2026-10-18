"""Negative/positive controls for the census exemption in _bump_version.

The exemption must ignore HISTORICAL release references while still catching a
version row that a partial bump left behind. Both directions are asserted: an
exemption that swallows everything is as useless as no exemption at all.

v5.68: the exemption gained a second leg. This tree annotates history with a
line-leading or trailing release tag ("// v5.66: ...", "//| v5.66 - ...",
"code;   // v5.66 - ..."), which the parenthesis test cannot see -- so bumping
away from a release reported that release's own valid annotations as missed
rows. The new cases below pin BOTH that extension and its narrowness: the
comment-borne ROW shapes (banners) are still reported, and a bare literal with
no 'v' prefix inside a comment is still reported.
"""
import re

OLD = "5.66"

# v5.68: mirrors _bump_version.census() exactly, including the row-in-comment
# pre-check and the comment-borne history exemption. Kept as a copy (not an
# import) so the control still runs if the tool is refactored.
_ROW = re.compile(r"(?:Master Build Port|Master Build) \(v%s\)"
                  r"|OTTO EA v%s(?![\d.])" % (re.escape(OLD), re.escape(OLD)))
_HIST = re.compile(r"\((?:FIX\s*\(\s*)?v?%s(?![\d.])" % re.escape(OLD))
_COMMENT_HIST = re.compile(r"//.*?\bv%s(?![\d.])" % re.escape(OLD))


def census(lines):
    """Mirror of _bump_version.census() for a list of lines."""
    out = []
    for l in lines:
        if OLD not in l:
            continue
        if _ROW.search(l):
            out.append(l)
            continue
        if "FIX (" in l or _HIST.search(l) or _COMMENT_HIST.search(l):
            continue
        out.append(l)
    return out


CASES = [
    # (label, line, must_be_flagged)
    ("historical parenthesised prose",
     "// why the break veto had to be re-anchored on this same price (v5.66):",
     False),
    ("historical block-prefixed prose",
     "// RE-ANCHORED (v5.66) on the ENTRY, not on the physical far edge.",
     False),
    ("historical FIX marker",
     "// FIX (v5.66): the break veto anchor moved", False),
    ("historical unversioned parens", "// the deep entry (5.66 geometry)", False),
    ("MISSED #property version row",
     '#property version   "5.66"', True),
    ("MISSED input-group label",
     'input group "  [10] HIGH TABLE AUDITOR - v5.66"', True),
    ("MISSED README base line",
     "master build. **Current base: v5.66**", True),
    ("bare literal in prose",
     "// the 5.66 build was slow to arm", True),
    # --- v5.68: comment-borne history annotations are NOT rows ---
    ("history: line-leading tag",
     "// v5.66: the tracked RISK GEOMETRY is pushed on the same branch", False),
    ("history: block-prefixed tag",
     "//| v5.66 adds a SECOND, independent leg to this body.", False),
    ("history: trailing code tag",
     "AuditTrailingHealth();    // v5.66 - is the trail tightening?", False),
    ("history: revision pair with '/'",
     "// ---- v5.63 / v5.66: the AGGREGATE leg -------------------", False),
    # --- v5.68: ...but a ROW that lives in a comment is STILL reported ---
    ("MISSED banner row (Pine port)",
     "//| Pine Script Master Build Port (v5.66)", True),
    ("MISSED banner row (EA header)",
     "//| OTTO EA v5.66 - continuation pattern", True),
    # --- v5.68: a comment-borne bare literal must NOT be exempted ---
    ("bare literal inside a comment",
     "// as of 5.66 the veto reads the ENTRY", True),
]

bad = 0
for label, line, want in CASES:
    got = bool(census([line]))
    ok = (got == want)
    if not ok:
        bad += 1
    print("%s %-36s flagged=%-5s expected=%s"
          % ("PASS" if ok else "FAIL", label, got, want))

print()
if bad:
    print("*** %d CENSUS CONTROL(S) FAILED ***" % bad)
    raise SystemExit(1)
print("*** ALL CENSUS CONTROLS PASS ***")
