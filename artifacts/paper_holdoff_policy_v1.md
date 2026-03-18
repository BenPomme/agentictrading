# Paper Holdoff Policy v1

**Date:** 2026-03-18
**Status:** Active

## Principle

When a lineage is actively paper trading and healthy, the factory should NOT burn agentic compute on mutations, critiques, or tweaks every cycle. Paper models are monitored by deterministic checks. LLM/agentic compute is triggered only by explicit events.

## When a Lineage Is in Paper-Active Hold-Off

A lineage enters hold-off when:
- `current_stage == "paper"` AND `active == True`
- `iteration_status` is NOT one of: `failed`, `retiring`, `review_requested_rework`

## What Is BLOCKED During Hold-Off

| Action | Blocked | Reason |
|---|---|---|
| `mutate_model` | YES | Don't mutate a running paper model |
| `suggest_tweak` | YES | Don't tweak a running paper model |
| `critique_post_evaluation` | YES (unless scheduled checkpoint) | Don't burn tokens reviewing every cycle |
| `resolve_maintenance_item` | YES (unless triggered by event) | Don't diagnose a healthy model |
| `generate_proposal` for the same family | YES (if max challengers reached) | Don't spawn more when paper is running |

## What IS ALLOWED During Hold-Off

| Action | Allowed | Trigger |
|---|---|---|
| Deterministic health check | YES | Every cycle |
| Paper PnL evaluation | YES | Every cycle |
| Drawdown breach alert | YES | Threshold exceeded |
| Slippage/execution check | YES | Every cycle |
| Trade flow monitoring | YES | Expected but missing trades |
| Scheduled review checkpoint | YES | After N days (configurable) |
| Agent critique | YES | Only at scheduled checkpoint |
| Emergency maintenance | YES | Stale data, API failure, risk breach |

## Scheduled Review Checkpoints

| Checkpoint | Timing | Action |
|---|---|---|
| First assessment | After 2 days paper (or min_fast_trades reached) | Agent critique + deterministic eval |
| Mid-period review | After 14 days paper | Agent critique + incumbent comparison |
| Full evaluation | After 30 days paper | Full post-paper validation gate |

## Emergency Triggers (Break Hold-Off)

These conditions trigger immediate agentic review:
1. No trade flow for 48h when strategy should be active
2. Drawdown exceeds 80% of gate threshold (6.4% of 8%)
3. API/data feed stale for 4h+
4. Slippage exceeds 2x expected
5. Circuit breaker trips

## Implementation

The orchestrator's `run_cycle()` checks `lineage.current_stage == "paper"` before dispatching agentic workflows. If in paper and healthy, skip mutation/critique/maintenance unless an emergency trigger fires.

## Config Keys

| Key | Default | Purpose |
|---|---|---|
| FACTORY_PAPER_HOLDOFF_ENABLED | true | Enable/disable hold-off |
| FACTORY_PAPER_FIRST_ASSESSMENT_DAYS | 2 | Days before first agent review |
| FACTORY_PAPER_MID_REVIEW_DAYS | 14 | Days before mid-period review |
| FACTORY_PAPER_FULL_EVAL_DAYS | 30 | Days before full evaluation |
| FACTORY_PAPER_DRAWDOWN_ALERT_RATIO | 0.8 | Ratio of max_drawdown that triggers alert |
