# Autonomous Paper Window — Runbook

**Version:** v1
**Date:** 2026-03-18

## Startup

```bash
# Dry run (preflight only, no cycles)
.venv312/bin/python scripts/run_autonomous_paper_window.py --dry-run

# Short supervised window (5 cycles, 60s interval)
.venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 5 --interval 60

# Longer window (20 cycles, 2min interval)
.venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 20 --interval 120
```

The script sets all paper window env overrides internally. No manual .env changes required.

## Stop

**Option 1 — Signal:**
```bash
# Ctrl+C in the terminal (SIGINT)
# Or: kill -SIGTERM <pid>
```
Finishes the current cycle, then exits cleanly.

**Option 2 — Pause flag:**
```bash
touch data/factory/factory_paused.flag
```
The script checks this flag before each cycle and exits if present.

**Remove pause flag to allow future runs:**
```bash
rm data/factory/factory_paused.flag
```

## Rollback

To fully disable paper autonomy and return to preflight state:

```bash
# Set these in your .env.staging or shell:
export FACTORY_ENABLE_PAPER_TRADING=false
export FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION=false
export FACTORY_ALLOW_AUTONOMOUS_MUTATION=false
export FACTORY_STRICT_BUDGETS=false
```

Or simply stop the script — it is not a daemon and does not persist env changes.

## What to Monitor

### Operator Status
```bash
cat artifacts/operator_status.json | python -m json.tool
```

Key fields:
- `staging_guards.trading.paper_enabled` → should be `true`
- `staging_guards.trading.live_blocked` → should be `true`
- `staging_guards.autonomy.allow_paper_promotion` → should be `true`
- `staging_guards.is_safe_for_paper_autonomy` → should be `true`
- `factory_status` → should be `running` or `idle`

### Paper Window Log
```bash
tail -f data/factory/paper_window.log | python -m json.tool
```

### Goldfish Provenance
```bash
# Check if thoughts are being written
ls -la data/factory/goldfish_learning/
```

### Lineage Progression
```bash
# Check lineage stages
for f in data/factory/lineages/*/lineage.json; do
  echo "--- $(basename $(dirname $f)) ---"
  python -c "import json; d=json.load(open('$f')); print(f\"  stage={d['current_stage']} active={d['active']} status={d['iteration_status']}\")"
done
```

## Healthy Behavior

A healthy paper-learning window should show:

1. **Cycles completing without errors** — `status=ok` in the log
2. **Family count stable at 2** — funding_term_structure_dislocation + liquidation_rebound_absorption
3. **New challenger lineages appearing** — the factory generates fresh challengers for retired families
4. **Lineage stages progressing** — idea → spec → data_check → goldfish_run → walkforward → ...
5. **Goldfish thought records accumulating** — RUN_CREATED / RUN_FINALIZED records in workspaces
6. **Budget usage within bounds** — strict budgets enforced, no circuit breaker trips
7. **No live trading activity** — zero entries in any live execution log

## Immediate Stop Conditions

Stop the window immediately if:

1. **Live trading detected** — any evidence of non-paper order execution
2. **Budget circuit breaker trips** — `circuit_global_open=true` in operator status
3. **Goldfish write failures in strict mode** — provenance integrity compromised
4. **Repeated cycle errors** — 3+ consecutive `status=error` cycles
5. **Uncontrolled lineage fanout** — lineage count growing beyond 10 unexpectedly
6. **Memory/CPU runaway** — mobkit gateway consuming excessive resources

## Architecture Invariants

These must remain true throughout any paper window:

- Live trading is hard-disabled (`FACTORY_LIVE_TRADING_HARD_DISABLE=true`)
- Agents propose / critique / mutate — they do NOT execute backtests
- Orchestrator + deterministic backtest engine run backtests
- Promotion to paper is decided by deterministic gates, not by LLM alone
- Budget governance (strict mode) enforces cost ceilings with circuit breakers
- Goldfish records all provenance (thoughts, runs, evaluations)
