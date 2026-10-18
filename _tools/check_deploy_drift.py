#!/usr/bin/env python3
"""
check_deploy_drift.py -- OTTO EA deployed-header drift audit.

Angle-bracket includes (<Otto/*.mqh>) in otto.mq5 resolve against the LIVE
terminal's MQL5\\Include\\Otto folder, NOT against the repo or the staging
tree. build_check.ps1 no longer mirrors headers there, so this script is the
only way to prove whether a successful-looking compile actually linked the
CURRENT repo headers or a stale/corrupted deployed copy.

Compares, per file:
  * repo HEAD blob            (git show HEAD:<file>)
  * repo worktree file
  * deployed terminal copy

Read-only: never writes anything.
Exit 0 = deployed copies match the repo worktree content.
Exit 1 = drift detected.
"""

import os
import subprocess
import sys

EXTS = (".mq5", ".mqh")

TERMINAL = os.path.join(
    os.environ.get("APPDATA", ""),
    "MetaQuotes", "Terminal",
    "10CE948A1DFC9A8C27E56E827008EBD4",
    "MQL5", "Include", "Otto")


def norm(data):
    return data.replace(b"\r\n", b"\n")


def nonascii(data):
    return sum(1 for b in data if b > 127)


def read_or_none(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    os.chdir(repo)

    print("=" * 104)
    print("OTTO DEPLOYED-HEADER DRIFT AUDIT")
    print("=" * 104)
    print("repo    : %s" % os.getcwd())
    print("deployed: %s" % TERMINAL)
    print("deployed exists: %s" % os.path.isdir(TERMINAL))
    print("")
    print("%-28s %9s %9s %9s  %s"
          % ("FILE", "HEADna", "WORKna", "DEPLna", "VERDICT"))
    print("-" * 104)

    paths = sorted(f for f in os.listdir(".") if f.endswith(EXTS))
    if not paths:
        print("no .mq5/.mqh files in repo root")
        return 2

    drift = 0
    for base in paths:
        head = subprocess.run(["git", "show", "HEAD:" + base],
                              capture_output=True).stdout or None
        work = read_or_none(base)
        depl = read_or_none(os.path.join(TERMINAL, base))

        if depl is None:
            verdict = "NOT DEPLOYED"
            drift += 1
        elif work is not None and norm(depl) == norm(work):
            verdict = "in sync with repo"
        elif head is not None and norm(depl) == norm(head):
            verdict = "matches HEAD (repo worktree differs)"
        else:
            verdict = "*** DRIFT ***"
            drift += 1

        print("%-28s %9s %9s %9s  %s"
              % (base,
                 nonascii(head) if head else "-",
                 nonascii(work) if work else "-",
                 nonascii(depl) if depl else "-",
                 verdict))

    print("-" * 104)
    print("files=%d  drifted=%d" % (len(paths), drift))
    if drift == 0:
        print("*** DEPLOY IN SYNC ***")
        return 0
    print("*** DRIFT PRESENT: compile gate would link stale/corrupt headers ***")
    return 1


if __name__ == "__main__":
    sys.exit(main())