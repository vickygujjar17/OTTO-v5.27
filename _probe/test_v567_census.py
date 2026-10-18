"""Negative/positive controls for the v5.67 census exemption in _bump_version.

The exemption must ignore HISTORICAL release references while still catching a
version row that a partial bump left behind. Both directions are asserted: an
exemption that swallows everything is as useless as no exemption at all.
"""
import re

OLD = "5.66"


def census(lines):
    """Mirror of _bump_version.census() for a list of lines."""
    hist = re.compile(r"\((?:FIX\s*\(\s*)?v?%s(?![\d.])" % re.escape(OLD))
    return [l for l in lines if OLD in l and not ("FIX (" in l or hist.search(l))]


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
