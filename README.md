# OTTO EA

MetaTrader 5 Expert Advisor — MQL5 port of the Pine Script `prop_guard_tester.pine`
master build. **Current base: v5.80**

## Layout

| File | Role |
|---|---|
| `OttoDefines.mqh` | Central enums, structs and `input` parameters |
| `COttoNewsFilter.mqh` | News calendar reader — RETIRED as a gate (v5.80), diagnostics only |
| `COttoRiskManager.mqh` | Position sizing / risk |
| `COttoMarketStructure.mqh` | Market-day counter, structure helpers |
| `COttoBlockManager.mqh` | Wick1+Wick2 S/R block lifecycle: creation-time Separation + ATR Sizing vetoes, arming, structural break/flip |
| `COttoOrderManager.mqh` | Order placement, pyramiding basket, journal hooks |
| `COttoCorrelationFilter.mqh` | Currency-vector consensus — DIAGNOSTICS ONLY since v5.80 (no path to an order) |
| `COttoTradeManager.mqh` | Cut / cost-BE / lock3 / ATR trail / pyramiding |
| `COttoJournal.mqh` | Human-readable trade journal |
| `CHighTableAuditor.mqh` | "High Table" decoupled watchdog: CSV audit trail + latched email alerts |
| `otto.mq5` | EA entry point and event handlers |

## Build (strict gate)

Sources are CRLF and the EA resolves its modules through angle-bracket
includes (`#include <Otto/<file>.mqh>`), so it is compiled from a staged tree
that mirrors the terminal layout (`MQL5\Experts` + `MQL5\Include\Otto`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\build_check.ps1
```

The gate is only passed on **0 errors, 0 warnings**.

`-Source` defaults to the checkout the script lives in (`$PSScriptRoot\..`), so
the command above needs no editing after a fresh clone; pass
`-Source <dir>` to gate a different tree.

## Line endings

`_tools\normalize_eol.py` rewrites MQL5 sources to strict CRLF byte-safely
(non-ASCII content is preserved exactly):

```powershell
python _tools\normalize_eol.py .            # rewrite
python _tools\normalize_eol.py . --check    # report only
```

## Version policy

Every update bumps `#property version` in **all** `.mqh`/`.mq5` files, the
banner in `OttoDefines.mqh`, the startup `Print()` banner in `otto.mq5`, the
four versioned input-group labels and the README base line.
`_tools\_bump_version.py` performs the rewrite, skipping any line containing
`FIX (` so historical annotations survive. It auto-detects the live release
from `OttoDefines.mqh` (no hardcoded from/to pair to rot) and fails if any
old-release literal is left behind, so a partial bump cannot be committed.

> `#property link` names the GitHub *repository*, not the release, so it is
> intentionally excluded from the bump.

## v5.80 — architectural strip

v5.80 removes **every post-arming execution-path veto** and **every external
correlation / news filter**. What remains is the strategy itself:

| Stage | Behaviour |
|---|---|
| Creation | W1/W2 zone formation, **Separation** and **ATR Sizing** vetoes |
| Arming | close clears the NEAR edge (plus the stacked-primary lockout) |
| Structural | **Break / Flip** on the physical FAR edge (`VETO_BROKEN` / `VETO_FLIPPED`) |
| Routing | strict limit router — a setup either rests at the broker or is `VETO_LIMIT_MISSED` |

Removed: Stale (45D), Front-Run, Near-Miss (6D), Momentum, FVG, the
`UpdateIntraBar` / `CheckVetoesInTick` intra-bar pass, the per-block
correlation guards (`IsTradeVetoed` / `IsConsensusOpposed`), the
`CancelOpposingConsensusOrders` sweep, the `HiveMind()` correlation
tie-breaker that deleted live zones, and the news-blackout gate.

**The shipped near-edge pullback geometry is byte-identical to v5.72**:
entry gap `2.0*blockHeight + 1.5*ATR` (`ENTRY_FRONT_EDGE`, as a resting
limit), `slDist = blockHeight + 0.5*ATR`, far-edge TP anchored by
`InpMaxRR` (realized `InpMaxRR + 3.0`), and the break/flip block itself.
`_probe\test_v580_strip.py` diffs each of those against the `HEAD` blob and
fails if any character moves.

The retired veto inputs (`InpUseStaleVeto`, `InpStaleDays`,
`InpUseFrontRunVeto`, `InpUseNearMissVeto`, `InpUseMomVeto`, `InpUseFvgVeto`,
`InpConsensusVetoThreshold`, `InpCancelOpposingPendings`) are **kept as
declared no-ops**, so an existing `.set` file still loads. `InpMaxRR` is
*not* retired — it is still the take-profit multiple.


Release steps, in order:

1. Run `python _tools\_bump_version.py` and eyeball the reported diff.
2. Add a `_commitNNN.txt` changelog (title, per-area sections, `VERIFICATION`).
3. Pass the gate: `powershell -NoProfile -ExecutionPolicy Bypass -File _tools\build_check.ps1`
   on **0 errors, 0 warnings**.
4. Commit with the version first in the subject, then the upgrade list:
   `NNN: <area> — <what changed>`. One commit may cover several areas; every
   commit for a release is prefixed with that release's number.
