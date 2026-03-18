# Revival Lineage Proof v2

**Date:** 2026-03-18
**Status:** VALIDATED

## Revival Execution

Both Binance-only families were successfully revived and produced active learning cycles.

## Pre-Revival State (Dead Families)

| Family | Lineage | Active | Stage | Retirement Reason |
|---|---|---|---|---|
| funding_term_structure_dislocation | :champion | False | walkforward | backtest_ttl_50h_exceeded_48h |
| liquidation_rebound_absorption | :champion | False | walkforward | backtest_ttl_50h_exceeded_48h |

## Post-Revival State (After Learning Window)

### funding_term_structure_dislocation (Binance-only)

| Lineage | Stage | Active | Status |
|---|---|---|---|
| :champion | walkforward | True | tweaked |
| :challenger:1 | walkforward | True | champion (promoted) |
| :challenger:2 | walkforward | True | tweaked |
| :challenger:3 | walkforward | True | new_candidate |
| :challenger:4 | walkforward | True | new_candidate |
| :challenger:5 | walkforward | True | new_model_candidate |

**Result:** 6 active lineages, champion promoted to challenger:1, 5 challengers spawned. Zero retirements.

### liquidation_rebound_absorption (Binance-only, probationary)

| Lineage | Stage | Active | Status |
|---|---|---|---|
| :champion | walkforward | True | tweaked |
| :challenger:1 | walkforward | True | tweaked |
| :challenger:2 | walkforward | True | tweaked |
| :challenger:3 | walkforward | True | review_requested_rework |
| :challenger:4 | walkforward | True | champion (promoted) |
| :challenger:5 | walkforward | True | tweaked |
| :challenger:6 | walkforward | True | tweaked |

**Result:** 7 active lineages, champion promoted to challenger:4, 6 challengers spawned. Zero retirements.

## Scope Enforcement Validation

| Family | Venues | In Scope | Agentic Compute | Challenger Spawn |
|---|---|---|---|---|
| funding_term_structure_dislocation | binance | YES | YES | YES (5 challengers) |
| liquidation_rebound_absorption | binance | YES | YES | YES (6 challengers) |
| cross_venue_probability_elasticity | polymarket,binance | NO | BLOCKED | BLOCKED |
| vol_surface_dispersion_rotation | yahoo,alpaca | NO | BLOCKED | BLOCKED |

## Goldfish Provenance

Goldfish recorded walkforward + stress evaluation runs for both revived families and all new challengers.

## Key Metrics

- **positives: 1** (first positive model evidence in any paper window)
- **escalations: 0**
- **live_blocked: true** (confirmed throughout)
- **Binance families alive and spawning challengers** (first time in project history)
