"""
v5.43 static verification probe - SESSION OWNERSHIP + PHYSICAL SAR RACE.

Two runtime defects rode along under a spotless v5.42 compile. Neither is
provable by the MQL5 compiler gate: both are control-flow / side-effect
properties of the shipped sources.

  1. JOURNAL SESSION-ID CROSSTALK. COttoJournal holds ONE m_sessionID that
     names the file every Log* writer appends to. The OrderManager repointed it
     at every arm / cancel / conversion, so whichever setup touched the journal
     LAST owned it -- not the LIVE basket. A later basket exit could therefore
     land in a different setup's file. v5.43 makes COttoOrderManager::m_sessionID
     the single source of truth for the running basket and has every transient
     setup write preserve it (save/restore), with InitBasket() reusing it when
     already resolved.

  2. PHYSICAL SAR RACE. CheckPendingOrderFills() refused to REVERSE on a
     same-direction scale-in fill (v5.41 guard) but then RE-SEEDED the live
     basket from the newcomer, repointing its journal and overwriting its 1R.
     v5.43 CLOSES the redundant fill instead and leaves the incumbent intact.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "CHighTableAuditor.mqh",
             "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def func_body(text, signature_re):
    """Return the full body of an MQL5 function, matched by counting braces."""
    m = re.search(signature_re, text)
    if m is None:
        return None
    start = text.find("{", m.start())
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def strip_comments(code):
    """Drop // comments, respecting string literals."""
    out = []
    for line in code.split("\n"):
        idx = line.find("//")
        if idx < 0:
            out.append(line)
            continue
        out.append(line if line[:idx].count('"') % 2 else line[:idx])
    return "\n".join(out)


OM_C = strip_comments(OM_T)
ROUTER = func_body(OM_C, r"bool\s+PlaceOrArmOrder\s*\(int blockIndex")
FILLS = func_body(OM_C, r"void\s+CheckPendingOrderFills\s*\(void\)")
VTRIG = func_body(OM_C, r"void\s+CheckVirtualTriggers\s*\(void\)")
CANCEL = func_body(OM_C, r"void\s+CancelOrdersForInvalidBlocks\s*\(void\)")
INIT = func_body(OM_C, r"void\s+InitBasket\s*\(")


SAVE = "string prevSession = m_sessionID;"
RESTORE = 'if(prevSession != "") m_journal.SetSessionID(prevSession);'


# ----------------------------------------------------------------------
# 1. Physical SAR: redundant same-direction fill is CLOSED, not re-seeded
# ----------------------------------------------------------------------
print("\n-- Physical SAR redundant-fill handling --")

check("CheckPendingOrderFills located", FILLS is not None)

# The v5.41 direction clause must survive (reversal still requires opposition).
check("v5.41 direction clause still guards the reversal",
      FILLS is not None and "m_activeDirection != newDir" in FILLS)

# The new branch is keyed on the SAME direction (the ELSE of the reversal if).
check("redundant branch is the else of the reversal guard",
      FILLS is not None and
      re.search(r"else\s+if\(m_hasActiveTrade\s*&&\s*m_activeTrade\.ticket\s*!=\s*newTicket\)",
                FILLS) is not None)

# The redundant fill is CLOSED (not adopted), and the loop is advanced with
# continue so the incumbent is never re-seeded from the newcomer.
check("redundant fill is closed",
      FILLS is not None and "ClosePosition(newTicket);" in FILLS)
check("redundant branch logs its action",
      FILLS is not None and "REDUNDANT SAME-DIRECTION FILL" in FILLS)

if FILLS is not None:
    red = FILLS.find("REDUNDANT SAME-DIRECTION FILL")
    adopt = FILLS.find("SeedActiveTradeFromBlock(blocks[i], newTicket)")
    cont = FILLS.find("continue;", red) if red >= 0 else -1
    check("redundant branch continues BEFORE the ADOPT block",
          0 <= red < cont < adopt, "red=%d cont=%d adopt=%d" % (red, cont, adopt))


# ----------------------------------------------------------------------
# 2. SAR close locks the journal to the INCUMBENT basket (both routes)
# ----------------------------------------------------------------------
print("\n-- SAR close locks the incumbent journal --")

if FILLS is not None:
    lock = FILLS.find("SetSessionID(m_sessionID)")
    close = FILLS.find('CloseEntireBasket("SAR Reversal"')
    check("physical SAR locks the incumbent before the close",
          0 <= lock < close, "lock=%d close=%d" % (lock, close))

check("CheckVirtualTriggers located", VTRIG is not None)
if VTRIG is not None:
    lock = VTRIG.find("SetSessionID(m_sessionID)")
    close = VTRIG.find('CloseEntireBasket("SAR Reversal"')
    check("virtual SAR locks the incumbent before the close",
          0 <= lock < close, "lock=%d close=%d" % (lock, close))


# ----------------------------------------------------------------------
# 3. Fill paths HAND the setup session to the basket before seeding
# ----------------------------------------------------------------------
print("\n-- Fill-path session hand-off --")

check("physical fill adopts the block's arm session",
      FILLS is not None and "m_sessionID = blocks[i].sessionId;" in FILLS)
check("virtual fill adopts its pre-minted session",
      VTRIG is not None and "m_sessionID = newSessionId;" in VTRIG)


# ----------------------------------------------------------------------
# 4. Virtual route no longer repoints the journal BEFORE the send
# ----------------------------------------------------------------------
print("\n-- No pre-send journal repoint (virtual) --")

if VTRIG is not None:
    send = VTRIG.find("SendOrderWithRetry(request, result)")
    repoint = VTRIG.find("SetSessionID(newSessionId)")
    check("the only newSessionId repoint is the post-send rejection path",
          0 <= send < repoint, "send=%d repoint=%d" % (send, repoint))
    check("exactly one newSessionId repoint remains in the trigger",
          VTRIG.count("SetSessionID(newSessionId)") == 1,
          "found %d" % VTRIG.count("SetSessionID(newSessionId)"))


# ----------------------------------------------------------------------
# 5. Transient setup writes SAVE/RESTORE the live basket session
# ----------------------------------------------------------------------
print("\n-- Transient-write save/restore --")

check("PlaceOrArmOrder located", ROUTER is not None)
if ROUTER is not None:
    check("arm saves the live session (physical + virtual)",
          ROUTER.count(SAVE) == 2, "found %d" % ROUTER.count(SAVE))
    check("arm restores the live session (physical + virtual)",
          ROUTER.count(RESTORE) == 2, "found %d" % ROUTER.count(RESTORE))

check("CancelOrdersForInvalidBlocks located", CANCEL is not None)
if CANCEL is not None:
    check("cancel saves the live session at all four writes",
          CANCEL.count(SAVE) == 4, "found %d" % CANCEL.count(SAVE))
    check("cancel restores the live session at all four writes",
          CANCEL.count(RESTORE) == 4, "found %d" % CANCEL.count(RESTORE))


# ----------------------------------------------------------------------
# 6. InitBasket session ownership (reuse / mint / MAN / journal push)
# ----------------------------------------------------------------------
print("\n-- InitBasket session ownership --")

check("InitBasket located", INIT is not None)
if INIT is not None:
    check("reuses m_sessionID when already resolved",
          re.search(r'if\(idSuffix\s*==\s*""\s*&&\s*m_sessionID\s*!=\s*""\)', INIT) is not None)
    check("still mints a fresh id in the else branch",
          re.search(r'm_sessionID\s*=\s*StringFormat\("#OTTO-%s-%s-%s"', INIT) is not None)
    check("an ADOPTED basket keeps its -MAN origin token",
          re.search(r'string\s+tail\s*=\s*\(idSuffix\s*!=\s*""\)\s*\?\s*idSuffix', INIT) is not None)
    check("InitBasket pushes the resolved id into the journal",
          "m_journal.SetSessionID(m_sessionID);" in INIT)
    check("no reliance on the shared journal pointer to resolve the id",
          "m_sessionID = m_journal.GetSessionID();" not in INIT)


# ----------------------------------------------------------------------
# 7. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.43 or later", parts >= (5, 43), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 11 files stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.43 SESSION OWNERSHIP + PHYSICAL SAR RACE - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        if ok:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s%s" % (name, ("  <- " + detail) if detail else ""))
    print("-" * 74)
    print("  %d / %d checks passed" % (passed, total))
    print("=" * 74)
    if passed != total:
        print("*** FAILURES PRESENT ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

