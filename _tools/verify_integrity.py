#!/usr/bin/env python3
"""
verify_integrity.py -- OTTO EA source-integrity audit.

Compares every working-tree .mq5/.mqh file against its git HEAD blob and
reports, per file:

  * non-ASCII byte count in HEAD vs worktree
  * literal '?' count in HEAD vs worktree
  * whether the CONTENT matches HEAD once line endings are normalised
  * CRLF / bare-LF / bare-CR counts in the worktree

Exit code 0 = every file content-identical to HEAD and strict CRLF.
Exit code 1 = drift detected (prints a per-file FAIL reason).

Read-only: never writes to any source file.
"""

import glob
import os
import subprocess
import sys

EXTS = (".mq5", ".mqh")


def head_blob(path):
    res = subprocess.run(["git", "show", "HEAD:" + path],
                         capture_output=True)
    if res.returncode != 0:
        return None
    return res.stdout


def nonascii(data):
    return sum(1 for b in data if b > 127)


def eol_stats(data):
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    bare_cr = data.count(b"\r") - crlf
    return crlf, bare_lf, bare_cr


def norm(data):
    return data.replace(b"\r\n", b"\n")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    os.chdir(root)

    paths = []
    for ext in EXTS:
        paths.extend(glob.glob("*" + ext))
    paths.sort()

    if not paths:
        print("no .mq5/.mqh files found")
        return 2

    print("=" * 100)
    print("OTTO SOURCE INTEGRITY AUDIT")
    print("=" * 100)
    print("%-28s %8s %8s %7s %7s %6s %6s %6s %s"
          % ("FILE", "HEADna", "WORKna", "Hqmark", "Wqmark",
             "CRLF", "bareLF", "bareCR", "CONTENT"))
    print("-" * 100)

    failures = 0
    for path in paths:
        base = os.path.basename(path)
        head = head_blob(path)
        with open(path, "rb") as fh:
            work = fh.read()

        if head is None:
            print("%-28s  <not in HEAD - untracked>" % base)
            failures += 1
            continue

        crlf, bare_lf, bare_cr = eol_stats(work)
        content_ok = norm(head) == norm(work)

        print("%-28s %8d %8d %7d %7d %6d %6d %6d %s"
              % (base, nonascii(head), nonascii(work),
                 head.count(b"?"), work.count(b"?"),
                 crlf, bare_lf, bare_cr,
                 "MATCH" if content_ok else "DIFFERS"))

        if not content_ok:
            print("    !! content differs from HEAD")
            failures += 1
        if bare_lf or bare_cr:
            print("    !! non-CRLF line endings (bareLF=%d bareCR=%d)"
                  % (bare_lf, bare_cr))
            failures += 1

    print("-" * 100)
    print("files=%d  failures=%d" % (len(paths), failures))
    if failures == 0:
        print("*** INTEGRITY OK: all sources match HEAD, strict CRLF ***")
        return 0
    print("*** INTEGRITY FAILED ***")
    return 1


if __name__ == "__main__":
    sys.exit(main())