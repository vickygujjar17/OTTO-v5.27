#!/usr/bin/env python3
"""
repair_mojibake.py -- OTTO EA build tooling

Repairs the lossy UTF-8 mojibake that a chain of editor round-trips copied
into comment and log-string separators in two source files.

WHAT WENT WRONG
---------------
A single separator character (an em dash in practice) was repeatedly
re-encoded through UTF-8 -> CP1252 -> UTF-8. Each round trip multiplies the
byte length by ~3-4, so one original character became a ~2026-character run
that was then copy-pasted into ~23 unrelated comments and Print() strings.
The transform is LOSSY: decoding the run back recovers only U+FFFD, so the
original character cannot be recovered programmatically.

HOW IT IS REPAIRED
------------------
Every damaged run is a separator sitting between spaces in a comment or a
string literal. The repair replaces each maximal non-ASCII run with the
project's own separator convention (EM DASH, matching the 17 legitimate
U+2014 uses already in OttoDefines.mqh). ASCII-only text is never touched:
the tool refuses to run if a damaged line's ASCII skeleton would change.

Usage:
    python repair_mojibake.py <directory>          # report + rewrite
    python repair_mojibake.py <directory> --check  # report only, no writes
"""

import sys
import os
import glob
import re

EXTS = (".mq5", ".mqh")

# A maximal run of characters outside printable ASCII. The 'm' prefix keeps
# the pattern operating on str (the file is decoded as UTF-8 first).
RUN_RE = re.compile(r"[^\x20-\x7e]+")

# Project separator convention, verified against surviving legitimate uses.
REPLACEMENT = "\u2014"  # EM DASH

# ---------------------------------------------------------------------------
# CORRUPTION SIGNATURE
# ---------------------------------------------------------------------------
# Damage from the UTF-8 -> CP1252 -> UTF-8 cascade always begins with the
# 4-char sequence 'Ã' 'Æ' ... in this codebase (byte prefix C3 83 C6 92), and
# every damaged run is thousands of characters long. Legitimate non-ASCII in
# this project is short and different ('═' border rows, '—', '×', '≤'). So a
# run is treated as damage ONLY when both hold:
#   1. it starts with the corrupt-prefix signature, and
#   2. it is at least MIN_RUN chars long.
# This refuses to touch the legitimate separators in OttoDefines.mqh / otto.mq5.
CORRUPT_PREFIX = "\u00c3\u0192\u00c6\u2019"
MIN_RUN = 8


def is_damaged(run: str) -> bool:
    """True only for the long UTF-8-cascade runs, never for legitimate chars."""
    return len(run) >= MIN_RUN and run.startswith(CORRUPT_PREFIX)


def skeleton(line: str) -> str:
    """ASCII skeleton: every non-ASCII run collapsed to a single marker."""
    return RUN_RE.sub("<SEP>", line)


def analyze(data: bytes):
    """Returns (text, [(line_no, run_char_len, context), ...]) for DAMAGED runs."""
    text = data.decode("utf-8")  # raises on genuinely invalid bytes
    sites = []
    for idx, line in enumerate(text.split("\r\n")):
        for m in RUN_RE.finditer(line):
            if is_damaged(m.group(0)):
                sites.append((idx + 1, len(m.group(0)), line))
    return text, sites


def repair_text(text: str) -> str:
    """Replace only damaged runs; legitimate non-ASCII is preserved as-is."""
    return RUN_RE.sub(lambda m: REPLACEMENT if is_damaged(m.group(0)) else m.group(0),
                      text)


def main():
    if len(sys.argv) < 2:
        print("usage: repair_mojibake.py <directory> [--check]")
        return 2

    root = sys.argv[1]
    check_only = "--check" in sys.argv

    files = []
    for ext in EXTS:
        files.extend(glob.glob(os.path.join(root, "*" + ext)))
    files.sort()

    if not files:
        print("no .mq5/.mqh files found in %s" % root)
        return 1

    print("=" * 78)
    print("MOJIBAKE REPAIR  root=%s  mode=%s"
          % (root, "CHECK-ONLY" if check_only else "REWRITE"))
    print("=" * 78)

    total_sites = 0
    total_chars = 0
    changed = 0

    for path in files:
        with open(path, "rb") as fh:
            raw = fh.read()

        try:
            text, sites = analyze(raw)
        except UnicodeDecodeError as exc:
            print("%-28s !! NOT VALID UTF-8 - SKIP (%s)"
                  % (os.path.basename(path), exc))
            continue

        if not sites:
            continue

        fixed = repair_text(text)

        # SAFETY: the ASCII skeleton must be identical before and after.
        # This guarantees we only swapped separator runs, never real text.
        if skeleton(text) != skeleton(fixed):
            print("%-28s !! SKELETON CHANGED - ABORT" % os.path.basename(path))
            return 3

        name = os.path.basename(path)
        print("%-28s sites=%-4d chars=%-7d"
              % (name, len(sites), sum(s[1] for s in sites)))
        for line_no, run_len, line in sites:
            print("    line %-5d run=%-6d | %s"
                  % (line_no, run_len, skeleton(line).strip()[:88]))

        total_sites += len(sites)
        total_chars += sum(s[1] for s in sites)

        if text != fixed:
            changed += 1
            if not check_only:
                with open(path, "wb") as fh:
                    fh.write(fixed.encode("utf-8"))

    print("-" * 78)
    print("runs=%d  damaged_nonASCII_chars=%d  rewritten=%d"
          % (total_sites, total_chars, changed if not check_only else 0))
    if total_sites:
        print("replacement char: %s (U+%04X)"
              % (repr(REPLACEMENT), ord(REPLACEMENT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
