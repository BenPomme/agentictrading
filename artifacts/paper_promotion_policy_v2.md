# Paper Promotion Policy v2

**Generated:** 2026-03-17
**Status:** Active (requires env flags to enable)

---

## Activation

Set these environment variables to enable autonomous paper:

- `FACTORY_ENABLE_PAPER_TRADING=true`
- `FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION=true`
- `FACTORY_LIVE_TRADING_HARD_DISABLE=true` (default, must remain true)

---

## Safety Properties

- `is_safe_for_paper_autonomy`: True when live blocked + paper enabled + autonomous promotion enabled + families <= 5 + challengers <= 3
- `is_safe_for_preflight`: Remains for pre-paper validation (requires paper disabled)
- Live trading: Permanently blocked by `FACTORY_LIVE_TRADING_HARD_DISABLE=true`

---

## Deterministic Paper Gate (promotion.py:paper_gate_blockers)

A model qualifies for paper when ALL of these pass:

1. `baseline_beaten_windows >= 3` — must beat baseline on at least 3 walkforward windows
2. `paper_days >= 30` (FACTORY_PAPER_GATE_MIN_DAYS) — minimum paper trading duration
3. `monthly_roi_pct >= 5.0` (FACTORY_PAPER_GATE_MONTHLY_ROI_PCT) — minimum monthly ROI
4. `max_drawdown_pct <= 8.0` (FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT) — maximum drawdown
5. `slippage_headroom_pct > 0` — must survive slippage stress test
6. Trade count thresholds:
   - Fast strategies: `trade_count >= 50` (FACTORY_PAPER_GATE_MIN_FAST_TRADES)
   - Slow strategies (polymarket/betfair): `settled_count >= 10` (FACTORY_PAPER_GATE_MIN_SLOW_SETTLED)
7. No hard vetoes (empty `hard_vetoes` list)

---

## Incumbent Comparison (promotion.py:compare_to_incumbent)

When an incumbent exists, challenger must beat it on family-specific scorecard:

- ROI delta: >= min_roi_delta_pct (0.20–0.35% depending on family)
- Calibration delta: >= min_calibration_delta_abs
- Drawdown delta: <= max_drawdown_delta_pct (no regression)
- Capacity delta: >= 0
- Regime robustness delta: >= min_regime_delta
- Failure rate delta: <= max_failure_rate_delta (no regression)

---

## Autonomous vs Manual Flow

### When `autonomous_paper_allowed=True`:

- Promotion decision stays at PAPER stage
- Paper trading runs autonomously within deterministic caps
- Does NOT advance to CANARY_READY or LIVE_READY
- Live trading remains permanently blocked

### When `autonomous_paper_allowed=False` (default):

- Paper gate passes → advances to LIVE_READY
- Requires human signoff for CANARY_READY, LIVE_READY, APPROVED_LIVE

---

## What Is NOT Autonomous

- Live trading promotion: always requires human signoff
- Backtest execution: deterministic engine, not LLM-driven
- Budget enforcement: deterministic cost governor with circuit breakers
