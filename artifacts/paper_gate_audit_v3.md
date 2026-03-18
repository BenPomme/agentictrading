# Paper Gate Audit v3

**Date:** 2026-03-18
**Auditor:** Architecture agent (Opus)

## Critical Bug: Circular Dependency in Paper Entry

The `paper_gate_blockers()` method in `promotion.py` was used as the ONLY gate for entering paper trading. It required:

1. `paper_days >= 30` — requires 30 days of paper trading before entering paper
2. `trade_count >= 50` (fast) or `settled_count >= 10` (slow) — requires paper trades before entering paper
3. `slippage_headroom_pct > 0` — requires paper execution evidence
4. `baseline_beaten_windows >= 3` — applied to paper_bundle which only exists after paper

**The circular dependency:** To enter paper, you need paper_bundle. To get paper_bundle, you need to be in paper. A model can never satisfy these conditions before paper starts.

## Additional Bug: No Venue Differentiation

All venues used the same thresholds regardless of data quality:
- Binance: sparse funding/OI data, short history → same 5% ROI / 3-window requirement as equities
- Polymarket: no historical backtest data at all → same requirements as equities
- Betfair: limited historical data → same requirements as equities
- Yahoo/Alpaca: rich daily price history → appropriate for strict gates

## Consequence

Every lineage got stuck at `walkforward` stage. The only path to paper was the TTL-based escape hatch in `_retire_by_backtest_ttl()` which promotes never-traded models to `paper_trial_no_backtest`. But models WITH trades (like liquidation_rebound_absorption with 417 trades) were retired instead of promoted.

## Rules Incorrectly Used for Pre-Paper Entry

| Rule | Current Gate | Correct Phase |
|---|---|---|
| `paper_days >= 30` | Pre-paper | **Post-paper validation** |
| `trade_count >= 50` | Pre-paper | **Post-paper validation** |
| `settled_count >= 10` | Pre-paper | **Post-paper validation** |
| `slippage_headroom_pct > 0` | Pre-paper | **Post-paper validation** |
| `baseline_beaten_windows >= 3` (on paper_bundle) | Pre-paper | **Post-paper validation** |
| `monthly_roi_pct >= 5%` (on paper_bundle) | Pre-paper | **Post-paper validation** |

## Rules That Should Apply Pre-Paper

| Rule | Sparse Venues | Rich Venues |
|---|---|---|
| Positive backtest ROI | > 0% | >= 3% |
| Max drawdown | < 15% | < 10% |
| Baseline beaten windows (backtest) | >= 0 | >= 2 |
| Stress positive | Not required | Required |
| Hard vetoes | Block entry | Block entry |

## Fix Applied

Split into `pre_paper_entry_blockers()` and `post_paper_validation_blockers()` with venue-differentiated thresholds.
