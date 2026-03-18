# High-ROI Candidate Review: liquidation_rebound_absorption

**Date:** 2026-03-18

## Candidate Profile

| Field | Value |
|---|---|
| Family | liquidation_rebound_absorption |
| Venue | Binance (perpetuals) |
| Instruments | BTCUSDT, ETHUSDT, SOLUSDT |
| Backtest monthly ROI | 289.4% |
| Backtest trades | 417 |
| Backtest max drawdown | 1.25% |
| Backtest win rate | 70.5% |
| Net PnL | +9.65% |
| Slippage headroom | 0.0% |
| Baseline beaten windows | 0 |
| Hard vetoes | None |

## Why It Was Retired

**Retirement reason:** `backtest_ttl_50h_exceeded_48h`

The model exceeded the 48-hour backtest TTL. The `_retire_by_backtest_ttl()` logic checked `_backtest_positive_gate()`, which:
1. Found the venue is Binance → applied the "full gate" (strict)
2. `fitness_score` was None (0) → failed
3. `slippage_headroom_pct` was 0 → would have failed

Since the gate failed, and the model HAD trades (417), it was retired instead of promoted to paper trial. The paper trial escape hatch only applies to models with ZERO trades.

## Should It Have Entered Paper?

**Yes, under the corrected policy.**

Under the new pre-paper entry gate for sparse venues (Binance):
- Backtest ROI > 0%: **PASS** (289.4%)
- Max drawdown < 15%: **PASS** (1.25%)
- Baseline beaten windows >= 0: **PASS** (0)
- Stress positive: **NOT REQUIRED** for sparse venues
- Hard vetoes: **PASS** (none)

The model would pass all pre-paper entry criteria.

## ROI Caveat

The 289.4% monthly ROI is almost certainly a simulation artifact — likely from:
- Unrealistic fill assumptions in the generic backtest
- No slippage modeling (slippage_headroom = 0%)
- Event-driven strategy with many signals in a concentrated backtest window

**This is exactly why it should enter paper** — paper trading with real execution on Binance testnet will reveal whether the edge survives realistic conditions. The backtest ROI should not be trusted at face value, but neither should it be a reason to reject paper testing. Paper is the truth test.

## Recommendation

Under the corrected policy:
1. This model should be revived (or a fresh challenger spawned) for the family
2. It should enter paper via the pre-paper sparse-venue gate
3. Its paper performance should be evaluated by the post-paper validation gate
4. The 289% backtest ROI should be noted in Goldfish memory as unreliable but not disqualifying
