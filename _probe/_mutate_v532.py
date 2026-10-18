"""
Mutation test for the v5.32 High Table probe.

A probe that always passes is worthless, so each Part 2 assertion added by
v5.32 is exercised against a deliberate, targeted mutation of the shipped
sources. Every mutation MUST make the probe fail: if it does not, the check
is vacuous - it would not notice the regression it claims to guard.

The originals are restored in a finally block, so a crash mid-run cannot
leave the working tree mutated.
"""
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "_probe", "test_v532_hightable.py")

AUD = os.path.join(ROOT, "CHighTableAuditor.mqh")
MAIN = os.path.join(ROOT, "otto.mq5")
ORD = os.path.join(ROOT, "COttoOrderManager.mqh")


def read(p):
    # Normalise CRLF to LF so anchors can be written with plain \n, while the
    # on-disk CRLF is preserved by write() below.
    return (io.open(p, encoding="utf-8", errors="replace", newline="")
            .read().replace("\r\n", "\n"))


def write(p, s):
    # Restore CRLF: the .mq5/.mqh sources and the probe are all CRLF on disk,
    # and _tools\normalize_eol.py enforces that.
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace("\n", "\r\n"))


def run_probe():
    r = subprocess.run([sys.executable, PROBE], capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# (label, path, old, new) - each old must occur exactly once.
MUTATIONS = [
    ("state consistency reverts to a basket-length comparison", AUD,
     "bool phantom = m_trackedActive && m_trackedPrimary > 0 &&\n"
     "                     !BookHasTicket(m_trackedPrimary);",
     "bool phantom = (book == m_trackedLegs);"),

    ("the phantom stops requiring the pushed active flag", AUD,
     "bool phantom = m_trackedActive && m_trackedPrimary > 0 &&",
     "bool phantom = true && m_trackedPrimary >= 0 &&"),

    ("the trim mirror stops excluding the pushed primary", AUD,
     "if(m_trackedPrimary > 0)\n"
     "           {\n"
     "            if(ticket == 0 || ticket == m_trackedPrimary) continue;\n"
     "           }\n",
     "if(false)\n"
     "           {\n"
     "            if(ticket == 0) continue;\n"
     "           }\n"),

    ("the trim mirror drops a guard WalkTrimLegs keeps", AUD,
     "if(sl <= 0.0) continue;",
     "if(false) continue;"),

    ("drawdown reverts to the 0.0090 fraction idiom", AUD,
     "return 100.0 * (balance - equity) / balance;",
     "return 0.0090 * (balance - equity) / balance;"),

    ("the floating-loss cap stops using the live input", AUD,
     "if(floatingLoss >= SafetyMaxFloatingLoss)",
     "if(floatingLoss >= 0.90)"),

    ("the daily limit stops using the live input", AUD,
     "if(dailyDD >= SafetyDailyDDLimit)",
     "if(dailyDD >= 3.0)"),

    ("the total limit stops using the live input", AUD,
     "if(totalDD >= SafetyTotalDDLimit)",
     "if(totalDD >= 5.0)"),

    ("the trim mirror hardcodes the trim threshold", AUD,
     "CountTrimmableLegs(InpTrimLoserStopPct)",
     "CountTrimmableLegs(70.0)"),

    ("a latch is declared outside the include guard", AUD,
     "#endif  // __OTTO_HIGH_TABLE_AUDITOR__",
     "bool m_alertSent_Smuggled;\n#endif  // __OTTO_HIGH_TABLE_AUDITOR__"),

    ("the reject ingress stops being monotonic", AUD,
     "m_rejectCount += count;",
     "m_rejectCount = count;"),

    ("SetTrackedLegs loses the pushed active flag", AUD,
     "void              SetTrackedLegs(int legs, ulong primaryTicket, bool active)",
     "void              SetTrackedLegs(int legs, ulong primaryTicket, bool active = true)"),

    ("a Part 2 body stops re-arming its latch", AUD,
     "      else\n         ClearLatch(m_alertSent_FloatingLossCap);",
     "      else\n         ;"),

    # AuditTrimHealth re-arms the trim latch at TWO sites - the early-return
    # guard and the else - so a faithful "stops re-arming" mutation has to
    # strip BOTH. Removing only one leaves a live re-arm and is not a
    # regression at all.
    ("the trim body stops re-arming its latch", AUD,
     ("         ClearLatch(m_alertSent_TrimFailure);\n"
      "         return;\n",
      "      else\n         ClearLatch(m_alertSent_TrimFailure);"),
     ("         return;\n",
      "      else\n         ;")),

    ("the state-consistency body stops re-arming its latch", AUD,
     "         ClearLatch(m_alertSent_StateInconsistency);\n"
     "         m_stateSkewSeen = false;\n",
     "         m_stateSkewSeen = false;\n"),

    ("the daily-breach body stops re-arming its latch", AUD,
     "      else\n         ClearLatch(m_alertSent_DailyDDBreach);",
     "      else\n         ;"),

    ("the reject-spike body stops re-arming its latch", AUD,
     "ClearLatch(m_alertSent_OrderRejectSpike);",
     ";"),

    ("the phantom stops being confirmed across two cycles", AUD,
     "      if(!m_stateSkewSeen)\n",
     "      if(false)\n"),

    ("the stop-modify body stops re-arming its latch", AUD,
     "      else\n         ClearLatch(m_alertSent_StopModifyFailure);",
     "      else\n         ;"),

    ("the order manager stops counting rejected stops", ORD,
     "m_stopModifyFailures++;",
     ";"),

    ("the push stops advancing its reject high-water mark", MAIN,
     "g_htPushedRejects = rejects;",
     ";"),

    ("the push moves after the audit", MAIN,
     "HighTablePushFacts();       // v5.32: hand over the facts only we can see\n"
     "   g_highTable.RunAudit();",
     "g_highTable.RunAudit();\n   HighTablePushFacts();"),

    ("the push takes the ticket from the book instead of the order layer", MAIN,
     "if(g_orderManager.GetActiveTradeRef(active))",
     "if(false)"),
]


def main():
    rc, out = run_probe()
    if rc != 0:
        print("BASELINE FAILED - fix the probe before mutation testing")
        print(out)
        return 1

    print("baseline: PASS (%s)" % out.strip().split("\n")[-3].strip())

    backups = {}
    failures = []
    try:
        for path in (AUD, MAIN, ORD):
            backups[path] = read(path)

        for label, path, old, new in MUTATIONS:
            original = backups[path]
            # `old` may be a tuple of anchors, `new` the matching tuple of
            # replacements, so a regression that requires editing SEVERAL
            # sites at once (e.g. a latch re-armed on two redundant paths)
            # is expressed as one honest mutation.
            olds = old if isinstance(old, tuple) else (old,)
            news = new if isinstance(new, tuple) else (new,)
            if len(olds) != len(news):
                failures.append("%s: %d anchors vs %d replacements"
                                % (label, len(olds), len(news)))
                continue

            mutated, bad = original, None
            for o, n in zip(olds, news):
                if mutated.count(o) != 1:
                    bad = "anchor found %d times: %r" % (mutated.count(o), o[:60])
                    break
                mutated = mutated.replace(o, n)
            if bad:
                failures.append("%s: %s" % (label, bad))
                continue

            write(path, mutated)
            rc, out = run_probe()
            if rc == 0:
                failures.append("%s: probe STILL PASSED (vacuous check)" % label)
                print("  [BAD ] %s" % label)
            else:
                print("  [GOOD] %s -> probe failed as required" % label)
            write(path, original)
    finally:
        for path, original in backups.items():
            write(path, original)

    rc, out = run_probe()
    print("\nrestored: %s" % ("PASS" if rc == 0 else "FAIL"))
    if rc != 0:
        failures.append("the tree did not restore cleanly")
        print(out)

    if failures:
        print("\n*** %d PROBLEM(S) ***" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("\n*** ALL %d MUTATIONS DETECTED ***" % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
