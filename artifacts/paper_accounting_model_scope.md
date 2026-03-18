# Paper Accounting Model Scope

**Date:** 2026-03-18
**Status:** Patched

## Old Accounting Model

Paper trading state was **portfolio-scoped**, not lineage-scoped.

- Each portfolio (e.g., `contrarian_legacy`, `cascade_alpha`) had a single `account.json`
- All lineages assigned to the same portfolio shared one balance pool
- P&L, drawdown, trade count, and equity curve were accumulated together
- Multiple lineages in the same family would contaminate each other's paper metrics
- `EvaluationBundle.monthly_roi_pct`, `max_drawdown_pct`, and `trade_count` came from the shared portfolio account — not from the individual model

**Legacy families with pooled state:**
| Family | Portfolio ID | Scope |
|---|---|---|
| binance_funding_contrarian | contrarian_legacy | Pooled (unchanged) |
| binance_cascade_regime | cascade_alpha | Pooled (unchanged) |
| betfair_prediction_value_league | betfair_core | Pooled (unchanged) |
| polymarket_cross_venue | polymarket_quantum_fold | Pooled (unchanged) |

**Research-factory families (new):** Had no paper state at all — `_collect_evidence()` only returned backtest bundles for them.

## What Changed

For **research-factory lineages** (any family not in the legacy hardcoded set) that enter shadow or paper stage:

1. `_lineage_portfolio_id(lineage_id)` generates an isolated portfolio ID: `lineage__{lineage_id_safe}`
   - Example: `liquidation_rebound_absorption:champion` → `lineage__liquidation_rebound_absorption__champion`

2. `_lineage_paper_state_bundle()` creates a dedicated `PortfolioStateStore` for each lineage in its own directory under `data/portfolios/lineage__{safe_id}/`

3. On first access, the store is initialized with:
   - `initial_balance = FACTORY_PAPER_LINEAGE_INITIAL_BALANCE` (default: 1000.0, configurable)
   - `lineage_id` field embedded in account.json for traceability
   - Zero P&L, zero trades, zero drawdown

4. `_collect_evidence()` for research-factory lineages at shadow/paper stage now blends the lineage-scoped paper bundle with backtest evidence — exactly as legacy families do with their portfolio bundles

## How Lineage-Specific Balance/P&L Works

```
Research-factory lineage enters paper stage
  → _lineage_paper_state_bundle()
  → _lineage_portfolio_id()  →  "lineage__funding_term_structure_dislocation__champion"
  → PortfolioStateStore("lineage__funding_term_structure_dislocation__champion")
  → data/portfolios/lineage__funding_term_structure_dislocation__champion/account.json
  → Balance: 1000.0 (isolated, no other lineage can affect it)
  → Trades: only this lineage's trades
  → Drawdown: computed from this lineage's equity curve only
  → EvaluationBundle.monthly_roi_pct  ← lineage-specific
  → EvaluationBundle.max_drawdown_pct  ← lineage-specific
  → EvaluationBundle.trade_count      ← lineage-specific
  → promotion.post_paper_validation_blockers()  uses these lineage-specific metrics
```

## New Config Keys

| Key | Default | Purpose |
|---|---|---|
| `FACTORY_PAPER_LINEAGE_INITIAL_BALANCE` | 1000.0 (from INITIAL_BALANCE_EUR) | Starting balance for each new paper lineage |

## What Still Remains Global

| Aspect | Scope | Reason |
|---|---|---|
| Lane caps (FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES) | Global | Hard cap on total concurrent paper models |
| Budget governance (strict budgets, daily USD) | Global | Cost control across all families |
| Venue activity (connector snapshots) | Global | Shared data feeds |
| Legacy family portfolios (contrarian_legacy etc.) | Portfolio-pooled | Unchanged — those have their own pooled history |
| Goldfish provenance workspace | Family-scoped | One workspace per family |
| Learning memory | Family-scoped | Shared across lineages in family |

## Summary

Promotion and retirement decisions for new research-factory lineages now use that lineage's own paper P&L, drawdown, and trade count — not a shared pool. A model with 30% drawdown will fail the paper gate based on its own trades, not because a sibling lineage happened to have high drawdown on the same portfolio.
