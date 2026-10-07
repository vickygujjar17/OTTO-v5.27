"""
v5.40 static verification probe - HYBRID PHYSICAL/VIRTUAL ORDER ROUTER.

Pins the behaviours v5.40 adds on top of the v5.37/v5.38/v5.39 virtual-order
engine. None of them is provable by the MQL5 compiler gate: they are control
flow and side-effect properties of the shipped sources.

  1. ROUTER. COttoOrderManager::PlaceOrArmOrder() (renamed from
     ArmVirtualOrder) decides per setup whether an entry may REST at the broker.
     A valid price -> a real TRADE_ACTION_PENDING limit (PHYSICAL). A price
     already through the zone -> an in-memory SVirtualOrder (VIRTUAL). A
     refused physical send FALLS BACK to the virtual arm.

  2. FILL SCANNER. CheckPendingOrderFills() (recovered) is wired into Update()
     and resolves a physical fill through the 3-tier detector.

  3. DUAL-BOOK hygiene: the duplicate shield (IsAnyOrderLiveAtPrice), the
     pending count (CountMyPendingOrders) and the cancel sweeps all span BOTH
     the broker pending pool AND the in-memory virtual book.

  4. N/R PHASE + EXECUTION MODE journal: COttoJournal::LogSetupArmed() stamps
     the Phase and Execution Mode on the arm header. v5.42: the arm no longer
     emails -- only LogExit()/LogCancellation() ship the session file.

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
check("physical route journals with physical=true",
      ROUTER is not None and "LogSetupArmed(true," in ROUTER)
check("router arms a VIRTUAL order when price is through the zone",
      ROUTER is not None and "SVirtualOrder vo;" in ROUTER)
check("virtual route journals with physical=false",
      ROUTER is not None and "LogSetupArmed(false," in ROUTER)
# Fallback: a refused resting send must arm virtually, not drop the setup.
check("a refused physical send falls back to the virtual arm",
      ROUTER is not None and
      "falling back to VIRTUAL arm" in ROUTER)
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
check("fill scanner runs before the virtual trigger",
      UPDATE is not None and
      UPDATE.find("CheckPendingOrderFills();") < UPDATE.find("CheckVirtualTriggers();"))
check("fill scanner skips a still-resting order",
      FILLS is not None and "IsBlockOrderAlive(ticket)" in FILLS)
check("fill scanner resolves the position via the 3-tier detector",
      FILLS is not None and "ResolveFilledPositionTicket(ticket, tracked" in FILLS)
check("fill scanner seeds the basket from the filled block",
      FILLS is not None and "SeedActiveTradeFromBlock(blocks[i], newTicket)" in FILLS)
check("fill scanner advances the two-phase lifecycle",
      FILLS is not None and "mod.touches += 1;" in FILLS and
      "SyncReversalCluster(bi)" in FILLS)
check("IsBlockOrderAlive helper exists", "IsBlockOrderAlive(ulong ticket)" in OM_T)
check("ResolveFilledPositionTicket helper exists",
      re.search(r"ResolveFilledPositionTicket\s*\(ulong orderTicket", OM_T) is not None)
check("DeleteOrder helper exists", "DeleteOrder(ulong ticket)" in OM_T)



# ----------------------------------------------------------------------
# 3. Dual-book hygiene
# ----------------------------------------------------------------------
print("\n-- Dual-book hygiene --")

check("duplicate shield located", SHIELD is not None)
check("shield scans the broker pending pool",
      SHIELD is not None and "OrdersTotal()" in SHIELD and
      "OrderGetTicket(i)" in SHIELD)
check("shield also scans the virtual book",
      SHIELD is not None and "m_virtualOrders[v].triggerPrice" in SHIELD)
check("shield name reflects the dual book (IsAnyOrderLiveAtPrice)",
      "IsAnyOrderLiveAtPrice(double targetPrice" in OM_T and
      "IsOrderAlreadyLiveAtPrice" not in OM_T)
check("pending count spans broker pendings + virtual orders",
      COUNT is not None and "OrdersTotal()" in COUNT and
      "count + m_virtualCount" in COUNT)
check("invalid-block cleanup deletes a resting physical order",
      "DeleteOrder(physTicket)" in OM_T)
check("consensus sweep deletes physical pendings",
      "VECTOR CANCEL (physical)" in OM_T)
check("direction conflict deletes physical pendings",
      "DIRECTION CONFLICT (physical)" in OM_T)
check("cancel-all prunes physical pendings before emptying the book",
      "CancelAllPendingOrders" in OM_T and "DeleteOrder(t);" in OM_T)


# ----------------------------------------------------------------------
# 4. N/R phase + execution-mode journal
# ----------------------------------------------------------------------
print("\n-- N/R phase + execution mode journal --")

check("LogSetupArmed located (LogVirtualOrderArmed is gone)",
      ARM_J is not None and "LogVirtualOrderArmed" not in JRN_T)
check("arm journal stamps the N/R phase",
      ARM_J is not None and "N (Normal Bounce)" in ARM_J and
      "R (Reversal)" in ARM_J)
check("arm journal stamps the execution mode",
      ARM_J is not None and "PHYSICAL (Broker Limit Order)" in ARM_J and
      "VIRTUAL (In-Memory Arm)" in ARM_J)
check("arm journal keys the phase off touches",
      ARM_J is not None and "blk.touches == 0" in ARM_J)
check("arm journal writes the snapshot but does NOT email (v5.42)",
      ARM_J is not None and "SUBJECT:" in ARM_J and
      "CloseHandle();" in ARM_J and "SendMailFromFile" not in ARM_J)
check("the per-setup session is pinned on SSniperBlock.sessionId",
      re.search(r"string\s+sessionId\s*;", DEFS_T) is not None and
      "block.sessionId          = armSessionId;" in OM_T)


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

