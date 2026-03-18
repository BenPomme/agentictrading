# Accelerated Multi-Venue Paper Window — Runbook

**Date:** 2026-03-18
**Status:** Active

## Active Venues

| Venue | Status | Families | Paper Mode |
|---|---|---|---|
| Binance | ACTIVE | funding_term_structure_dislocation, liquidation_rebound_absorption, cross_venue_probability_elasticity | Testnet (demo-fapi.binance.com) |
| Polymarket | ACTIVE | cross_venue_probability_elasticity | Simulated fills against live prices |
| Yahoo/Alpaca | ACTIVE | vol_surface_dispersion_rotation | Alpaca paper API |
| Betfair | BLOCKED | None | Missing certs |

## Startup Commands

### Multi-venue (default — all ready venues):
```bash
# Dry run first:
.venv312/bin/python scripts/run_autonomous_paper_window.py --dry-run

# Run 1 cycle:
.venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 1

# Run multiple cycles:
.venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 3 --interval 120
```

### Binance-only (targeted, faster):
```bash
.venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 1 --binance-only
```

### Revival (if Binance families are retired again):
```bash
.venv312/bin/python scripts/revive_paper_candidates.py --dry-run
.venv312/bin/python scripts/revive_paper_candidates.py --execute
```

## Stop Command

```bash
# Ctrl+C (signal stop — finishes current cycle)

# Or pause flag:
touch data/factory/factory_paused.flag

# Remove pause flag to allow future runs:
rm data/factory/factory_paused.flag
```

## Rollback Command

```bash
# Fully disable paper autonomy:
export FACTORY_ENABLE_PAPER_TRADING=false
export FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION=false

# Or: stop the script. It is not a daemon; env overrides are transient.
```

## Legacy Family Handling

Legacy families (binance_funding_contrarian, binance_cascade_regime, betfair_prediction_value_league, etc.) are **excluded** from this rollout by design:
- They are NOT in `data/factory/families/` (moved to backup)
- They are not included in `FACTORY_PAPER_WINDOW_VENUE_SCOPE`
- Their portfolio state (contrarian_legacy, cascade_alpha) is separate from research-factory portfolios
- Research-factory lineages use isolated per-lineage paper accounting

## What to Monitor

### Operator Status
```bash
cat artifacts/operator_status.json | python -m json.tool | grep -E "families|lineages|positives|live_blocked|paper_enabled"
```

Key fields:
- `staging_guards.trading.live_blocked: true` — must always be true
- `staging_guards.is_safe_for_paper_autonomy: true` — paper mode safely configured
- `positives` — models with positive evidence
- `lineages` — total active lineage count

### Paper Window Log
```bash
tail -5 data/factory/paper_window.log | python -m json.tool
```

### Lineage Stage Progression
```bash
for f in data/factory/lineages/*/lineage.json; do
  python -c "import json; d=json.load(open('$f')); print(f'{d[\"lineage_id\"][:60]:60s} stage={d[\"current_stage\"]:12s} active={d[\"active\"]}')"
done
```

### Per-lineage Paper Balances
```bash
ls data/portfolios/lineage__*/account.json 2>/dev/null
cat data/portfolios/lineage__*/account.json 2>/dev/null | python -m json.tool
```

### Goldfish Health
```bash
ls data/factory/goldfish_learning/ 2>/dev/null
```

## Immediate Stop Conditions

Stop and investigate if:
1. `staging_guards.trading.live_blocked` becomes false
2. Budget circuit breaker trips (`escalations > 0` in log)
3. Lineage count grows beyond 40 (possible runaway spawning)
4. Repeated `status: error` cycles in paper_window.log
5. Any non-paper order execution detected
6. Goldfish write failure in strict mode

## Scope Configuration (what changed)

```
Old: FACTORY_PAPER_WINDOW_VENUE_SCOPE = "binance"
New: FACTORY_PAPER_WINDOW_VENUE_SCOPE = "binance,polymarket,yahoo,alpaca"
```

Scope enforcement: `family_venues.issubset(scope_set)` — families must target only in-scope venues.
- `cross_venue_probability_elasticity` (polymarket+binance) → IN scope
- `vol_surface_dispersion_rotation` (yahoo+alpaca) → IN scope
- Betfair families → OUT of scope (no active families anyway)

## Binance ETH/SOL Expansion

BTCUSDT, ETHUSDT, SOLUSDT all have local data in:
- `data/funding_history/funding_rates/`
- `data/funding_history/klines/`

The factory's Binance families (funding_term_structure_dislocation, liquidation_rebound_absorption) explicitly reference these instruments in their incubation notes. The expanded universe is available immediately — no additional data download needed.
