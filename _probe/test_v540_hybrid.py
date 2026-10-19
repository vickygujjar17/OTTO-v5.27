"""
v5.40 static verification probe - HYBRID PHYSICAL/VIRTUAL ORDER ROUTER.

HISTORICAL (v5.40). Pins the behaviours v5.40 added on top of the v5.37/v5.38/
v5.39 virtual-order engine. v5.72 RETIRED the virtual half of that router, and
this probe is re-pinned to the strict limit-or-nothing model that replaced it.
What it still guards is the part that did not change:

  1. ROUTER. COttoOrderManager::PlaceOrArmOrder() decides per setup whether an
     entry may REST at the broker. A valid price -> a real TRADE_ACTION_PENDING
     limit. v5.72: a price already through the zone is VETOED
     (VETO_LIMIT_MISSED) instead of being armed in memory, and a refused send is
     vetoed instead of degrading into a MARKET deal. Limit or nothing.

  2. FILL SCANNER. CheckPendingOrderFills() is wired into Update() and resolves
     a physical fill through the 3-tier detector. Since v5.72 it is the ONLY
     fill path, so its ordering against the retired virtual trigger is no
     longer a property -- its presence and its sole-pathy are.

  3. BOOK HYGIENE. The duplicate shield, the pending count and the cancel sweeps
     all operate on the broker pending pool, which is now the only book.

  4. SETUP-ARM JOURNAL: COttoJournal::LogSetupArmed() stamps the Logic and
     Execution Mode on the arm header. v5.52 collapsed the old R/C phase to a
     single constant continuation label; v5.72 collapsed the execution mode to
     its single shipped value for the same reason. v5.42: the arm no longer
     emails -- only LogExit() ships the session file.

  5. THE NEGATIVE PIN. No SVirtualOrder may be declared anywhere in the tree, no
     virtual identifier may survive in the manager, and no virtual trigger may
     be called. That is the whole release, expressed as a build gate.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "otto.mq5")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
JRN = os.path.join(ROOT, "COttoJournal.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


MAIN_T = read(MAIN)
OM_T = read(OM)
JRN_T = read(JRN)
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
SHIELD = func_body(OM_C, r"bool\s+IsAnyOrderLiveAtPrice\s*\(double targetPrice")
FILLS = func_body(OM_C, r"void\s+CheckPendingOrderFills\s*\(void\)")
COUNT = func_body(OM_C, r"int\s+CountMyPendingOrders\s*\(void\)")
UPDATE = func_body(OM_C, r"void\s+Update\s*\(void\)")
ARM_J = func_body(JRN_T, r"void\s+LogSetupArmed\s*\(")


# ----------------------------------------------------------------------
# 1. The router: physical vs virtual selection
# ----------------------------------------------------------------------
print("\n-- Hybrid router --")

check("PlaceOrArmOrder located", ROUTER is not None)
check("router is named PlaceOrArmOrder (not ArmVirtualOrder)",
      "PlaceOrArmOrder" in OM_T and "ArmVirtualOrder" not in OM_T)
check("router consumes the live-price boundary to pick the route",
      ROUTER is not None and "GetPriceBoundaryBuffer()" in ROUTER)
check("router sends a PHYSICAL broker limit",
      ROUTER is not None and "TRADE_ACTION_PENDING" in ROUTER)
check("physical order type follows the mapped direction",
      ROUTER is not None and "GetOrderTypeForBlock(block)" in ROUTER)
check("physical route records the resting ticket on the block",
      ROUTER is not None and "SetBlockOrderTicket(blockIndex, result.order)" in ROUTER)
check("physical route journals through the ONE-route arm writer",
      ROUTER is not None and "LogSetupArmed(block.tradeId" in ROUTER)
# v5.72: both non-resting outcomes are VETOES. Two, because there are exactly
# two ways to fail -- the price was already through the entry, or the venue
# refused the send -- and neither may reach an order send.
check("router vetoes when price is through the zone (no in-memory arm)",
      ROUTER is not None and
      "SVirtualOrder" not in ROUTER and
      ROUTER.count("block.vetoReason         = VETO_LIMIT_MISSED;") == 2)
# The defect this release exists to remove: a refused limit must never become a
# MARKET chase. Assert the fallback literal is gone, and no DEAL can be built.
check("a refused physical send does NOT fall back to a MARKET arm",
      ROUTER is not None and
      "falling back to VIRTUAL arm" not in ROUTER and
      "TRADE_ACTION_DEAL" not in ROUTER)
# The v5.28 one-shot Invalid-Price gate is NOT re-added as a guard (the router
# decides legality itself, so no off-market resting send is ever dispatched).
check("router does not re-add the (Invalid Price) abort literal",
      "(Invalid Price)" not in OM_T)
check("router does not re-add the one-shot priceAbortLogged gate",
      re.search(r"EnableLogging\s*&&\s*!block\.priceAbortLogged", OM_T) is None)


# ----------------------------------------------------------------------
# 2. Physical fill scanner wired into Update()
# ----------------------------------------------------------------------
print("\n-- Physical fill scanner --")

check("CheckPendingOrderFills located", FILLS is not None)
check("fill scanner is called from Update()",
      UPDATE is not None and
      re.search(r"^\s*CheckPendingOrderFills\(\)\s*;", UPDATE, re.M) is not None)
check("Update() runs no virtual trigger stage (v5.72)",
      UPDATE is not None and "CheckVirtualTriggers" not in UPDATE)
check("fill scanner skips a still-resting order",
      FILLS is not None and "IsBlockOrderAlive(ticket)" in FILLS)
check("fill scanner resolves the position via the 3-tier detector",
      FILLS is not None and "ResolveFilledPositionTicket(ticket, tracked" in FILLS)
check("fill scanner seeds the basket from the filled block",
      FILLS is not None and "SeedActiveTradeFromBlock(blocks[i], newTicket)" in FILLS)
check("fill scanner retires the block (one continuation entry per block)",
      FILLS is not None and
      re.search(r"mod\.isTriggered\s*=\s*true\s*;", FILLS) is not None)
check("IsBlockOrderAlive helper exists", "IsBlockOrderAlive(ulong ticket)" in OM_T)
check("ResolveFilledPositionTicket helper exists",
      re.search(r"ResolveFilledPositionTicket\s*\(ulong orderTicket", OM_T) is not None)
check("DeleteOrder helper exists", "DeleteOrder(ulong ticket)" in OM_T)



# ----------------------------------------------------------------------
# 3. Book hygiene: ONE book
# ----------------------------------------------------------------------
print("\n-- Book hygiene (broker pendings only) --")

check("duplicate shield located", SHIELD is not None)
check("shield scans the broker pending pool",
      SHIELD is not None and "OrdersTotal()" in SHIELD and
      "OrderGetTicket(i)" in SHIELD)
check("shield scans NO in-memory book (v5.72)",
      SHIELD is not None and "m_virtualOrders" not in SHIELD and
      "m_virtualCount" not in SHIELD)
check("shield name reflects its job (IsAnyOrderLiveAtPrice)",
      "IsAnyOrderLiveAtPrice(double targetPrice" in OM_T and
      "IsOrderAlreadyLiveAtPrice" not in OM_T)
check("pending count is the broker pool alone (v5.72)",
      COUNT is not None and "OrdersTotal()" in COUNT and
      re.search(r"return\s+count\s*;", COUNT) is not None and
      "m_virtualCount" not in COUNT)
check("invalid-block cleanup deletes a resting physical order",
      "DeleteOrder(physTicket)" in OM_T)
# v5.80 ARCHITECTURAL STRIP: the consensus sweep is DELETED, so it can no
# longer delete anything -- physical or otherwise. The property this check
# exists to protect (every surviving cancel path acts on a REAL broker row, not
# an in-memory phantom) is asserted below by naming the paths that remain.
check("consensus sweep no longer deletes physical pendings",
      "VECTOR CANCEL (physical)" not in OM_T)
check("every surviving cancel path deletes a real broker row",
      "DeleteOrder(physTicket)" in OM_T and
      "DeleteOrder(t)" in OM_T)
check("direction conflict deletes physical pendings",
      "DIRECTION CONFLICT (physical)" in OM_T)
check("cancel-all prunes physical pendings before emptying the book",
      "CancelAllPendingOrders" in OM_T and "DeleteOrder(t);" in OM_T)


# ----------------------------------------------------------------------
# 4. Setup-arm journal: logic + execution mode
# ----------------------------------------------------------------------
print("\n-- Setup-arm journal: logic + execution mode --")

check("LogSetupArmed located (LogVirtualOrderArmed is gone)",
      ARM_J is not None and "LogVirtualOrderArmed" not in JRN_T)
# v5.71: the phase label moved onto the model's canonical vocabulary. The pin
# keeps its purpose -- the arm journal still stamps one CONSTANT logic string,
# not a per-setup value -- but names the string that now ships.
check("arm journal stamps the constant continuation logic (v5.81)",
      ARM_J is not None and "C (Near-Edge Pullback Trap)" in ARM_J and
      "Pure Trend Continuation" not in ARM_J)
check("arm journal stamps the ONE execution mode (v5.72)",
      ARM_J is not None and "PHYSICAL (Broker Limit Order)" in ARM_J and
      "VIRTUAL (In-Memory Arm)" not in ARM_J and
      "physical ?" not in ARM_J)
check("arm journal names the logic on the header",
      ARM_J is not None and re.search(r"Logic\s*:", ARM_J) is not None)
check("arm journal writes the snapshot but does NOT email (v5.42)",
      ARM_J is not None and "SUBJECT:" in ARM_J and
      "CloseHandle();" in ARM_J and "SendMailFromFile" not in ARM_J)
check("the per-setup session is pinned on SSniperBlock.sessionId",
      re.search(r"string\s+sessionId\s*;", DEFS_T) is not None and
      "block.sessionId          = armSessionId;" in OM_T)


# ----------------------------------------------------------------------
# 4b. THE NEGATIVE PIN: the in-memory engine is GONE (v5.72)
# ----------------------------------------------------------------------
# This is the release itself, expressed as a build gate. The DECLARATION is
# what makes the engine real, so it is matched as a declaration rather than as
# a substring: prose explaining what was retired legitimately names it.
print("\n-- Negative pin: no in-memory execution engine --")

check("no SVirtualOrder DECLARATION anywhere in the tree",
      all(re.search(r"struct\s+SVirtualOrder", read(os.path.join(ROOT, f))) is None
          for f in ALL_FILES))
for tok in ("m_virtualOrders", "m_virtualCount", "RemoveVirtualOrderAt",
            "ReleaseAllVirtualArmLocks", "VirtualArmGvName",
            "CheckVirtualTriggers"):
    check("no %s in COttoOrderManager.mqh" % tok, tok not in OM_T)
check("the execution mode is a single shipped constant",
      ARM_J is not None and "VIRTUAL (In-Memory Arm)" not in ARM_J)


# ----------------------------------------------------------------------
# 5. Version stamps agree on the current release
# ----------------------------------------------------------------------
print("\n-- Version stamps --")

stamps = set(re.findall(r'#property version\s+"(\d+\.\d+)"', DEFS_T))
check("OttoDefines.mqh carries exactly one version stamp", len(stamps) == 1,
      "found: %s" % sorted(stamps))
RELEASE = sorted(stamps)[0] if stamps else "?"
parts = tuple(int(x) for x in RELEASE.split("."))
check("release is v5.40 or later", parts >= (5, 40), RELEASE)

missing = [f for f in ALL_FILES
           if ('#property version   "%s"' % RELEASE) not in read(os.path.join(ROOT, f))]
check("all 10 files stamp v%s" % RELEASE, not missing,
      "missing: %s" % ", ".join(missing))
check("startup banner names the current release",
      ("OTTO EA v%s" % RELEASE) in MAIN_T)


# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.40 HYBRID PHYSICAL/VIRTUAL ROUTER - STATIC PROBE")
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

