"""Mutation test: confirm test_v52x_pyramid.py actually FAILS on regressions.

A probe that cannot fail is worthless. This copies the OTTO tree to a temp
dir, applies one targeted regression at a time, runs the pyramid/milestone
probe against the mutated copy, and asserts the probe goes red. Any mutation
that still passes means the corresponding check is decorative.

Every mutation below targets a check added by section 9 (the v5.31 strict
stop-loss milestone ladder), so a clean run proves those assertions have teeth.

TWO CLASSES OF MUTATION, because they need different machinery:

  MUTATIONS         - literal string replacement. Catches a guard that is
                      rewritten or deleted; the guard TEXT changes.

  REORDER_MUTATIONS - block relocation with no byte-level edit at all. The
                      v5.32 hoist is a pure ORDERING change, so reverting it
                      leaves every guard identical and the literal table is
                      structurally blind to it. Without these two mutants the
                      headline change of this release - and its SHORT mirror -
                      could silently regress with the probe still green.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

REAL = r"C:\Users\vivek\Downloads\OTTO-v5.00 v5.27"
PROBE = os.path.join("_probe", "test_v52x_pyramid.py")
FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "OttoDefines.mqh"]

HALF_RISK_MARK = "// --- HALF-RISK (HOISTED, v5.32) ---"
SHORT_MARK = "else // SHORT"
LONG_BE = "if(currentRR >= InpBreakEvenRR && desiredSL < primaryEntry)"
SHORT_BE = "if(currentRR >= InpBreakEvenRR && desiredSL > primaryEntry)"


def unhoist(text, marker, be_anchor):
    """Move the half-risk block from the END of a branch back to the FRONT.

    Byte-for-byte identical guards, different order: the pre-v5.32 layout.
    `text` must be ONE branch only (the caller slices at the SHORT marker),
    otherwise the block would be spliced to EOF and drag the rest of the file
    along -- mangling the copy instead of cleanly reverting a single rung.
    """
    i = text.index(marker)
    block, rest = text[i:], text[:i]
    a = rest.index(be_anchor)
    return rest[:a] + block + rest[a:]

MUTATIONS = [
    ("rung-2 floor reverted to half-RR (+2.0R -> +0.5R)", "OttoDefines.mqh",
     "InpLockProfit2TargetRR = 1.0;", "InpLockProfit2TargetRR = 0.5;"),
    ("rung-3 floor reverted to +1.0R", "OttoDefines.mqh",
     "InpLockProfitTargetRR = 2.0;", "InpLockProfitTargetRR = 1.0;"),
    ("trail gate raised above the first ladder rung (gap: +1.0R floor goes dark)",
     "OttoDefines.mqh",
     "InpTrailStartRR = 1.0;", "InpTrailStartRR = 2.0;"),
    ("breakeven floor loses the friction offset", "COttoTradeManager.mqh",
     "double beSL = primaryEntry + beOffset;", "double beSL = primaryEntry;"),
    ("LONG floor ratchet made two-way (stop can move backwards)",
     "COttoTradeManager.mqh",
     "if(lockedSL > desiredSL) desiredSL = lockedSL;", "desiredSL = lockedSL;"),
    ("half-risk floor flipped onto the profit side", "COttoTradeManager.mqh",
     "halfRiskSL = primaryEntry - (0.5 * rrUnit);",
     "halfRiskSL = primaryEntry + (0.5 * rrUnit);"),
    ("unified-basket one-way ratchet removed (LONG)", "COttoOrderManager.mqh",
     "if(isLong && newSL <= m_sessionSL) return;", "if(false) return;"),
    ("T2 scale-in no longer carries its protective stop", "COttoTradeManager.mqh",
     "AddPyramidTranche(2, groupBE)", "AddPyramidTranche(2, 0.0)"),
    ("a second copy of the ladder drifts into the order manager",
     "COttoOrderManager.mqh",
     "double            GetBasketRRUnit(void) const { return m_basketRRUnit; }",
     "double            GetBasketRRUnit(void) const { return m_basketRRUnit; }\r\n"
     "   double            DuplicateLadder(void) const { return InpLockProfit2TargetRR; }"),
]

# (name, fname, branch): branch is "both" to reorder every mirror, or "short"
# to reorder ONLY the SHORT mirror. The "short" entry is the important one - it
# is the regression that slips past a branch-blind ordering assertion, because
# TM_N.index() would keep resolving every claim against the still-correct LONG
# copy. REORDER_MUTATIONS exists because the literal table above is structurally
# unable to express a pure ordering change: reverting the hoist rewrites no
# guard text at all.
REORDER_MUTATIONS = [
    ("v5.32 hoist REVERTED - half-risk rung moved back before breakeven "
     "(both branches)", "COttoTradeManager.mqh", "both"),
    ("SHORT mirror reordered alone - LONG half-risk still correct",
     "COttoTradeManager.mqh", "short"),
]


def run_probe(root):
    r = subprocess.run([sys.executable, PROBE], cwd=root,
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def stage(branch="none"):
    """Copy the tracked tree to a temp dir; optionally un-hoist some branches.

    branch: "none" (plain copy for literal mutants), "both" (pre-v5.32-order
    everywhere) or "short" (only the SHORT mirror). Returns (tmp, note) with
    note None on success, or a SKIP reason when an anchor is missing.
    """
    tmp = tempfile.mkdtemp(prefix="otto_ms_")
    for f in FILES:
        shutil.copy2(os.path.join(REAL, f), os.path.join(tmp, f))
    shutil.copytree(os.path.join(REAL, "_probe"), os.path.join(tmp, "_probe"))

    if branch == "none":
        return tmp, None

    p = os.path.join(tmp, "COttoTradeManager.mqh")
    text = io.open(p, encoding="utf-8", errors="replace", newline="").read()
    try:
        if branch == "short":
            cut = text.index(SHORT_MARK)
            text = text[:cut] + unhoist(text[cut:], HALF_RISK_MARK, SHORT_BE)
        else:
            cut = text.index(SHORT_MARK)
            long_txt = unhoist(text[:cut], HALF_RISK_MARK, LONG_BE)
            short_txt = unhoist(text[cut:], HALF_RISK_MARK, SHORT_BE)
            text = long_txt + short_txt
    except ValueError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, "anchor not found (%s)" % e
    io.open(p, "w", encoding="utf-8", newline="").write(text)
    return tmp, None


def main():
    print("=" * 74)
    print("v5.31/v5.32 MILESTONE-LADDER PROBE MUTATION TEST")
    print("=" * 74)

    if not os.path.isdir(REAL):
        print("ABORT: tree not found: %s" % REAL)
        return 1

    rc, out = run_probe(REAL)
    if rc != 0:
        print("ABORT: baseline probe is already failing")
        print(out)
        return 1
    print("  baseline (unmutated tree): PASS\n")

    missed = []
    skipped = []

    print("-- literal mutations (guard text rewritten) " + "-" * 31)
    for name, fname, old, new in MUTATIONS:
        tmp, note = stage()
        try:
            if note is not None:
                print("  [SKIP] %s  <- %s" % (name, note))
                skipped.append(name)
                continue
            p = os.path.join(tmp, fname)
            text = open(p, encoding="utf-8", newline="").read()
            if old not in text:
                print("  [SKIP] %s  <- anchor not found in %s" % (name, fname))
                skipped.append(name)
                continue
            open(p, "w", encoding="utf-8", newline="").write(
                text.replace(old, new, 1))

            rc, out = run_probe(tmp)
            caught = rc != 0
            print("  [%s] %s" % ("CAUGHT" if caught else "MISSED", name))
            if caught:
                for x in [l.strip() for l in out.splitlines()
                          if "[FAIL]" in l][:2]:
                    print("           %s" % x)
            else:
                missed.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("-- reorder mutations (guard text unchanged) " + "-" * 29)
    for name, fname, branch in REORDER_MUTATIONS:
        tmp, note = stage(branch)
        try:
            if note is not None:
                print("  [SKIP] %s  <- %s" % (name, note))
                skipped.append(name)
                continue
            rc, out = run_probe(tmp)
            caught = rc != 0
            print("  [%s] %s" % ("CAUGHT" if caught else "MISSED", name))
            if caught:
                for x in [l.strip() for l in out.splitlines()
                          if "[FAIL]" in l][:2]:
                    print("           %s" % x)
            else:
                missed.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 74)
    total = len(MUTATIONS) + len(REORDER_MUTATIONS) - len(skipped)
    print("  %d/%d mutations caught  (%d anchor(s) skipped)"
          % (total - len(missed), total, len(skipped)))
    print("=" * 74)
    if missed:
        print("*** NOT CAUGHT (probe is decorative for these) ***")
        for m in missed:
            print("  - %s" % m)
        return 1
    if skipped:
        print("*** SKIPPED ANCHORS - fix the mutation table ***")
        for s in skipped:
            print("  - %s" % s)
        return 1
    print("*** EVERY MUTATION CAUGHT ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
