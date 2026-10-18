"""
v5.67 source patch -- JOURNAL SL SIGN for the SUPPORT row (+ the pin for it).

No trading geometry changes. The entry stays at NEAR edge +/- (2.0*blockHeight
+ 1.5*ATR), the break veto stays anchored on BlockEntryPrice(), and slDist
stays blockHeight + 0.5*ATR. What moves is one printed sign.

slDist is IDENTICAL in both directions, so with the far edge pinned at 0.0:

  LONG  (RESISTANCE): near = -bh, entry = -bh + gap =  bh + 1.5*ATR
                     stop  = entry - slDist       =  +1.0*ATR   (ABOVE)
  SHORT (SUPPORT)   : near = +bh, entry = +bh - gap = -bh - 1.5*ATR
                     stop  = entry + slDist       =  -1.0*ATR   (BELOW)

The blockHeight terms cancel exactly, so the stop always lands one full ATR
past the far edge and only the SIGN follows the direction. v5.57..v5.66
printed "SL at Far Edge + 1.0*ATR" for BOTH rows, so the SUPPORT row described
a stop sitting 1.0*ATR on the wrong side of its own zone -- the mirror image
of where the broker stop actually sits.

Nothing tied that caption to CalcSLDistance(), which is why it survived four
releases. The probe now DERIVES the offset and pins both literal rows.

Every replacement is an exact literal with an asserted match count, so a
drifted anchor aborts instead of silently patching the wrong line.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    p = os.path.join(ROOT, name)
    raw = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
    if "\r\n" not in raw:
        raise SystemExit("ABORT: %s is not CRLF" % name)
    return raw.replace("\r\n", "\n")


def save(name, text):
    p = os.path.join(ROOT, name)
    io.open(p, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(text.replace("\n", "\r\n"))


def rep(text, old, new, count, label):
    found = text.count(old)
    if found != count:
        raise SystemExit("ABORT %s: matched %d, expected %d"
                         % (label, found, count))
    print("  ok   %s" % label)
    return text.replace(old, new)


# ======================================================================
# 1. COttoJournal.mqh -- the SUPPORT SL clause, in BOTH writers
# ======================================================================
# LogSetupArmed() and LogEntry() carry byte-identical actionDesc rows, so one
# literal with count=2 covers both. The RESISTANCE row is CORRECT as shipped
# ("+ 1.0*ATR") and must not be touched.
jr = load("COttoJournal.mqh")

JR_OLD = "Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge + 1.0*ATR)"
JR_NEW = "Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge - 1.0*ATR)"

jr = rep(jr, JR_OLD, JR_NEW, 2, "JOURNAL SUPPORT SL sign (+ -> -), 2 writers")
save("COttoJournal.mqh", jr)
print("COttoJournal.mqh complete")

# ======================================================================
# 2. _probe/test_v557_frontrun.py -- docstring, corrected pin, derived pin
# ======================================================================
pr = load("_probe/test_v557_frontrun.py")

# 2a. Record the release in the module docstring, beside the other four
#     properties this probe exists to guard.
DOC_OLD = """     section 7 proves numerically that the retired threshold falls short of
     the entry in every shipped geometry."""

DOC_NEW = """     section 7 proves numerically that the retired threshold falls short of
     the entry in every shipped geometry.

  5. THE JOURNAL'S PRINTED SL SIGN (v5.67). slDist is identical in both
     directions, so the stop always lands exactly 1.0*ATR past the far edge
     and only the SIGN follows the direction: ABOVE it for a RESISTANCE LONG,
     BELOW it for a SUPPORT SHORT. v5.57..v5.66 printed "+ 1.0*ATR" for both,
     so the SUPPORT row described a stop on the wrong side of its own zone.
     Nothing tied that caption to CalcSLDistance(), which is why it survived
     four releases; section 10 now derives the offset AND pins both rows."""

pr = rep(pr, DOC_OLD, DOC_NEW, 1, "PROBE docstring: property 5")

# 2b. The existing header pin flips for SUPPORT only.
PIN_OLD = '      JRN_RAW.count("SUPPORT (Front-Run Breakdown / Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge + 1.0*ATR)") == 2 and'
PIN_NEW = '      JRN_RAW.count("SUPPORT (Front-Run Breakdown / Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge - 1.0*ATR)") == 2 and'

pr = rep(pr, PIN_OLD, PIN_NEW, 1, "PROBE section 10: SUPPORT literal pin")

# 2c. The DERIVED pin: tie the caption to the geometry instead of trusting it.
CHECK_OLD = """check("the retired 'Aggressive' block wording is gone",
      "Aggressive" not in JRN_RAW)"""

CHECK_NEW = """check("the retired 'Aggressive' block wording is gone",
      "Aggressive" not in JRN_RAW)

# The printed SL offset is a DERIVED fact, not a caption. Nothing in the .mqh
# ties the journal string to CalcSLDistance(), so the two can drift apart in
# silence -- and did: v5.57..v5.66 printed "SL at Far Edge + 1.0*ATR" for a
# SUPPORT block whose stop actually sits 1.0*ATR BELOW that far edge.
def sl_offset_from_far_edge(bh, atr, is_long):
    \"\"\"Signed distance from the far edge to the stop, far edge pinned at 0.0.

    LONG  (RESISTANCE): far edge is block.top    -> 0.0, near edge is -bh.
                       entry = near + gap = -bh + 2.0*bh + 1.5*atr
                       stop  = entry - slDist        ->  +1.0*atr
    SHORT (SUPPORT)   : far edge is block.bottom -> 0.0, near edge is +bh.
                       entry = near - gap = +bh - 2.0*bh - 1.5*atr
                       stop  = entry + slDist        ->  -1.0*atr
    slDist = bh + 0.5*atr cancels the blockHeight terms exactly, so the offset
    is always one full ATR; only its SIGN follows the direction.
    \"\"\"
    gap = 2.0 * bh + 1.5 * atr
    sl = bh + 0.5 * atr
    near = -bh if is_long else bh
    entry = (near + gap) if is_long else (near - gap)
    return (entry - sl) if is_long else (entry + sl)


check("the stop lands exactly +/- 1.0*ATR from the far edge, sign by direction",
      all(abs(sl_offset_from_far_edge(bh, atr, True) - atr) <= 1e-12 and
          abs(sl_offset_from_far_edge(bh, atr, False) + atr) <= 1e-12
          for bh, atr, _ in CASES))
check("the journal prints that sign: SUPPORT stop BELOW the far edge (-), "
      "RESISTANCE stop ABOVE it (+)",
      "Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge - 1.0*ATR)" in JRN_RAW and
      "Buy at Near Edge + 2.0*H + 1.5*ATR | SL at Far Edge + 1.0*ATR)" in JRN_RAW and
      "Sell at Near Edge - 2.0*H - 1.5*ATR | SL at Far Edge + 1.0*ATR)" not in JRN_RAW)"""

pr = rep(pr, CHECK_OLD, CHECK_NEW, 1, "PROBE section 10: derived SL-sign checks")
save("_probe/test_v557_frontrun.py", pr)
print("_probe/test_v557_frontrun.py complete")

print("\nPATCH COMPLETE")
