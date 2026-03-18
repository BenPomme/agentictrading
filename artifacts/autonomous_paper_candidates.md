# Autonomous Paper Candidates — First Window

**Generated:** 2026-03-18
**Venue scope:** Binance only
**Selection basis:** Existing families with Binance-compatible evidence

## Selected Candidates

### 1. funding_term_structure_dislocation

| Field | Value |
|---|---|
| Family ID | funding_term_structure_dislocation |
| Venue | Binance (perp + dated futures) |
| Instruments | BTCUSDT, ETHUSDT, SOLUSDT |
| Current stage | walkforward |
| Champion lineage | RETIRED (backtest_ttl_50h_exceeded) |
| Backtest monthly ROI | 5.83% |
| Backtest trades | 14 |
| Backtest max drawdown | 0.74% |
| Backtest win rate | 64.3% |

**Why selected:**
- Binance-only, matches venue scope constraint
- Strong thesis: funding rate term structure dislocations are a real, observable market microstructure phenomenon
- Backtest ROI (5.83%) exceeds the 5% monthly target threshold
- Low drawdown (0.74%) well within 8% cap
- Trade count is low (14) but the strategy is event-driven (funding windows), so this is expected
- Champion retired due to TTL, not due to poor evidence — needs fresh challenger iteration

**Entry mode:** Fresh challenger generation via autonomous learning loop. Current champion is retired. The factory will generate a new challenger, run backtests, and promote through walkforward → stress → shadow → paper stages.

### 2. liquidation_rebound_absorption

| Field | Value |
|---|---|
| Family ID | liquidation_rebound_absorption |
| Venue | Binance (perpetuals) |
| Instruments | BTCUSDT, ETHUSDT, SOLUSDT |
| Current stage | walkforward |
| Champion lineage | RETIRED (backtest_ttl_50h_exceeded) |
| Backtest monthly ROI | 289.4% (flagged: likely simulation artifact) |
| Backtest trades | 417 |
| Backtest max drawdown | 1.25% |
| Backtest win rate | 70.5% |

**Why selected:**
- Binance-only, matches venue scope constraint
- Strong thesis: post-liquidation cascade absorption is a well-documented crypto microstructure pattern
- High trade count (417) suggests the strategy generates frequent signals
- The 289% monthly ROI is likely a simulation artifact (unrealistic), but the underlying signal generation is promising
- A fresh challenger with realistic execution modeling should produce more grounded results
- Low drawdown (1.25%) indicates the strategy has built-in risk controls

**Entry mode:** Fresh challenger generation with emphasis on execution realism. The prior maintenance review flagged "zero tracked examples, zero trades/backtest evidence" — the existing backtest eval was from the initial generic_backtest bootstrap, not from the real backtest engine. Fresh challengers need realistic fee/slippage modeling.

## Rejected Candidates

### cross_venue_probability_elasticity

| Field | Value |
|---|---|
| Venue | Polymarket + Binance |
| Rejection reason | Requires Polymarket venue (outside Binance-only scope) |

The thesis is strong (cross-venue probability elasticity), but Polymarket is not in scope for the first paper window. Candidate for second window when venue scope expands.

### vol_surface_dispersion_rotation

| Field | Value |
|---|---|
| Venue | Yahoo + Alpaca |
| Backtest ROI | -0.37% (negative) |
| Rejection reason | Not Binance-compatible; negative backtest evidence |

Equity options-based strategy targeting SPY/QQQ/TLT/GLD. Incompatible with Binance venue scope. Also has negative ROI, so even within its own venue it would need significant rework.

## Summary

| Family | Venue | Backtest ROI | Status | Decision |
|---|---|---|---|---|
| funding_term_structure_dislocation | Binance | 5.83% | Retired champion | SELECTED |
| liquidation_rebound_absorption | Binance | 289.4%* | Retired champion | SELECTED |
| cross_venue_probability_elasticity | Polymarket+Binance | N/A | Active | REJECTED (venue scope) |
| vol_surface_dispersion_rotation | Yahoo+Alpaca | -0.37% | Retired champion | REJECTED (venue + negative ROI) |

*Likely simulation artifact — will be validated with realistic execution modeling in fresh challenger
