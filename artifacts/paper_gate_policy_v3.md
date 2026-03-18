# Paper Gate Policy v3

**Date:** 2026-03-18
**Status:** Active after patch

## Lifecycle Phases

### Phase A: PRE-PAPER ENTRY GATE
**Purpose:** Decide whether a model is promising enough to start paper testing.
**Evidence source:** Backtest/walkforward/stress results only.
**Must NOT require:** paper_days, paper trade_count, paper settled_count, slippage_headroom.

### Phase B: POST-PAPER VALIDATION GATE
**Purpose:** Decide whether a model has survived paper strongly enough to advance.
**Evidence source:** Paper trading results.
**May require:** paper_days, paper trade_count, slippage_headroom, incumbent comparison.

---

## Pre-Paper Gate: Sparse-History Venues (Binance, Polymarket, Betfair)

These venues have structural data limitations:
- Binance: funding rates and OI are short-history, event-driven
- Polymarket: no historical backtest data available
- Betfair: limited historical data, seasonal patterns

**Entry thresholds:**
- Backtest ROI > 0% (any positive evidence)
- Max drawdown < 15%
- Baseline beaten windows >= 0 (no minimum)
- Stress positive: NOT required
- Hard vetoes: must be empty

**Rationale:** Paper is free. These venues need paper to learn — backtest alone is insufficient. Any positive signal should be paper-tested.

## Pre-Paper Gate: Rich-History Venues (Yahoo, Alpaca)

These venues have rich daily price history spanning years:
- Yahoo: decades of daily price data
- Alpaca: institutional-grade equity data

**Entry thresholds:**
- Backtest ROI >= 3%
- Max drawdown < 10%
- Baseline beaten windows >= 2
- Stress positive: required
- Hard vetoes: must be empty

**Rationale:** Richer backtest data means we can be more selective before committing paper resources.

## Post-Paper Validation Gate (All Venues)

Applied after a model has been paper trading. Controls advancement beyond paper.

**Validation thresholds:**
- Paper days >= 30
- Monthly ROI >= 5%
- Max drawdown <= 8%
- Slippage headroom > 0
- Trade count >= 50 (fast strategies) or settled count >= 10 (slow strategies)
- Baseline beaten windows >= 3 (paper-period)
- Hard vetoes: must be empty
- Incumbent comparison: must pass family scorecard

## Decision Flow

```
IDEA → SPEC → DATA_CHECK → GOLDFISH_RUN → WALKFORWARD → STRESS → SHADOW
                                                                    |
                                                        [pre-paper gate]
                                                                    |
                                                              → PAPER ←──── (learning loop)
                                                                    |
                                                       [post-paper gate]
                                                                    |
                                                         CANARY_READY → LIVE_READY → APPROVED_LIVE
```

## Config Keys

| Key | Default | Purpose |
|---|---|---|
| `FACTORY_PAPER_GATE_MONTHLY_ROI_PCT` | 5.0 | Post-paper: minimum monthly ROI |
| `FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT` | 8.0 | Post-paper: maximum drawdown |
| `FACTORY_PAPER_GATE_MIN_DAYS` | 30 | Post-paper: minimum paper trading days |
| `FACTORY_PAPER_GATE_MIN_FAST_TRADES` | 50 | Post-paper: minimum trades (fast strategies) |
| `FACTORY_PAPER_GATE_MIN_SLOW_SETTLED` | 10 | Post-paper: minimum settled events (slow) |
