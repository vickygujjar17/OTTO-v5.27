"""
One-shot v5.60 ANNOTATION-COMPLETION patch for the OTTO EA tree.

Two distinct cases, and conflating them would be a lie in the source:

  A. FIVE notes already COMMITTED in HEAD (release 5.58). They still read
     '// v5.58 ' because the bump went 5.58 -> 5.60 and skipped 5.59, so no
     bump pass ever restated them. The 5.60 bump's staleness census scans for
     the bare '5.58' literal and only exempts lines carrying 'FIX (', so these
     five would report STALE and exit 1. Restate to 'FIX (v5.58)' -- the
     release the fix actually LANDED in. Precedent: the 557->558 bump
     re-stated 16 such survivors (see _tools/_commit559.txt).

  B. ONE note that is an UNCOMMITTED '+' line -- the COttoOrderManager
     de-dup. It does NOT exist in HEAD; it ships in 5.60. Labelling it
     'FIX (v5.58)' would claim 5.58 contained a change it never had. It gets
     '// v5.60 ' -- the release it lands in -- exactly as the 5.58 notes were
     introduced as '// v5.58 ' in their own release.

Width: '// v5.58 ' (9) -> 'FIX (v5.58) ' (13) is +4. In-repo 'FIX (v5.x)'
marker lines already run to 86 cols (COttoOrderManager.mqh:2853), which is the
budget guard below. No trailing-arrow columns exist in these blocks, so the
wider label launders no alignment.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (relative path, 1-based line, acceptable current forms, exact new line)
EDITS = [
    ("COttoBlockManager.mqh", 333,
     ("                  // v5.58 \u2014 W1/W2 FULL candle geometry, for the journal's",
      "                  // FIX (v5.58) W1/W2 FULL candle geometry, for the journal's"),
     "                  // FIX (v5.58) W1/W2 FULL candle geometry, for the journal's"),
    ("COttoBlockManager.mqh", 430,
     ("                  // v5.58 \u2014 support mirror of the W1/W2 geometry above. W1:",
      "                  // FIX (v5.58) support mirror of the W1/W2 geometry above. W1:"),
     "                  // FIX (v5.58) support mirror of the W1/W2 geometry above. W1:"),
    ("COttoBlockManager.mqh", 935,
     ("                     // v5.58 \u2014 W1/W2 FULL candle geometry (warm-up twin of the",
      "                     // FIX (v5.58) W1/W2 FULL candle geometry (warm-up twin of the"),
     "                     // FIX (v5.58) W1/W2 FULL candle geometry (warm-up twin of the"),
    ("COttoBlockManager.mqh", 1023,
     ("                     // v5.58 \u2014 support mirror of the warm-up W1/W2 geometry.",
      "                     // FIX (v5.58) support mirror of the warm-up W1/W2 geometry."),
     "                     // FIX (v5.58) support mirror of the warm-up W1/W2 geometry."),
    ("COttoOrderManager.mqh", 2858,
     ("         // v5.58 \u2014 FAR-EDGE ANCHOR, twin of PlaceOrArmOrder() above. localTP",
      "         // FIX (v5.58) FAR-EDGE ANCHOR, twin of PlaceOrArmOrder() above. localTP"),
     "         // FIX (v5.58) FAR-EDGE ANCHOR, twin of PlaceOrArmOrder() above. localTP"),
    # Case B: the uncommitted de-dup note belongs to 5.60, not 5.58. It still
    # needs the 'FIX (' marker: the bump probe proved a bare '// v5.60 ' note
    # is reported STALE by the very next bump (exit 1). 'FIX (v5.60)' is both
    # truthful -- the de-dup lands in 5.60 -- and bump-clean.
    ("COttoOrderManager.mqh", 1487,
     ("            // v5.58 \u2014 de-duplicated onto the ONE stop-distance definition.",
      "            // FIX (v5.58) de-duplicated onto the ONE stop-distance definition.",
      "            // v5.60 \u2014 de-duplicated onto the ONE stop-distance definition."),
     "            // FIX (v5.60) de-duplicated onto the ONE stop-distance definition."),
]


def main():
    cache = {}
    for path, lineno, olds, new in EDITS:
        full = os.path.join(ROOT, path)
        if full not in cache:
            # newline="" + utf-8 round-trips bytes exactly (no EOL translation).
            cache[full] = io.open(full, encoding="utf-8", newline="").read()
        text = cache[full]
        # The 11 shipped sources are strict CRLF; split/join on CRLF so no
        # stray '\r' rides along and the file stays byte-for-byte CRLF.
        if text.count("\r\n") != text.count("\n"):
            raise SystemExit("ABORT %s carries bare LF -- run normalize_eol.py" % path)
        lines = text.split("\r\n")
        cur = lines[lineno - 1]
        if cur == new:
            print("  already   %s:%d" % (path, lineno))
            continue
        if cur not in olds:
            raise SystemExit("ABORT %s:%d unexpected content:\n  got: %r\n  exp: %r"
                             % (path, lineno, cur, olds[0]))
        if len(new) > 86:
            raise SystemExit("ABORT %s:%d restated width %d exceeds 86" % (path, lineno, len(new)))
        lines[lineno - 1] = new
        cache[full] = "\r\n".join(lines)
        tag = "restated" if "FIX (" in new else "relabelled"
        print("  %s %s:%d" % (tag, path, lineno))

    for full, text in cache.items():
        io.open(full, "w", encoding="utf-8", newline="").write(text)
    print("done: %d file(s) written" % len(cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

