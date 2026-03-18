# Paper-Unvalidated Family Audit

**Date:** 2026-03-18

## Audit Summary

| Family | Venues | Best ROI | Trades | Paper Days | Retirement Reason | Paper Tested | Revival Eligible |
|---|---|---|---|---|---|---|---|
| funding_term_structure_dislocation | binance | 5.83% | 14 | 0 | backtest_ttl_50h_exceeded_48h | NO | YES |
| liquidation_rebound_absorption | binance | 289.4% | 417 | 0 | backtest_ttl_50h_exceeded_48h | NO | YES (probationary) |
| cross_venue_probability_elasticity | polymarket,binance | 1.08% | 0 | 0 | N/A (active lineages exist) | NO | N/A (already active) |
| vol_surface_dispersion_rotation | yahoo,alpaca | -0.19% | 14 | 0 | backtest_ttl_50h_exceeded_48h | NO | NO (negative ROI, out of scope) |

## Root Cause: Dead Family State

Both Binance families have only retired champions. The orchestrator's `_seed_challengers()` method requires an active champion (line 1169: `if champion is None: return`). With no active champion:
- No challengers are spawned
- No proposals or mutations are generated
- No evaluations run
- The promotion gate is never reached
- The corrected pre-paper entry logic never fires

## Why They Were Retired

Both families were retired by `_retire_by_backtest_ttl()` after 48+ hours in walkforward stage without passing `_backtest_positive_gate()`. The gate failed because:
- `fitness_score` was None/0 (not populated by the generic backtest)
- `slippage_headroom_pct` was 0 (not modeled in the generic backtest)

These are **infrastructure gaps**, not evidence of strategy failure. The models had positive backtest ROI but were blocked by missing metadata fields.

## Revival Decision

| Family | Decision | Rationale |
|---|---|---|
| funding_term_structure_dislocation | REVIVE | 5.83% ROI, 14 trades, strong thesis, Binance-only. Retired by TTL, not by evidence failure. |
| liquidation_rebound_absorption | REVIVE (probationary) | 289.4% ROI likely inflated, but 417 trades shows signal. Paper is the truth test. |
| vol_surface_dispersion_rotation | DO NOT REVIVE | Negative ROI (-0.19%), out of Binance scope. |
| cross_venue_probability_elasticity | NO ACTION NEEDED | Already has active lineages. Out of Binance-only scope. |
