# AgenticTrading

AgenticTrading is an autonomous trading research and paper-trading factory.

It generates strategy ideas, designs and mutates Python models, backtests them, runs multi-stage evaluation, promotes promising candidates into paper trading, retires weak lineages, and records the full lineage and learning history for future generations.

This repo is the **domain control plane** for the factory:
- strategy family definitions
- lineage lifecycle
- deterministic promotion / retirement policy
- backtest and paper-evaluation interpretation
- operator dashboard and control-tower views
- venue readiness and operating scope
- governance, budgets, and safety policy

This repo is **not** the live trading venue. Live trading must remain explicitly blocked unless separately enabled through a later operational process.

---

## Current architecture

The factory runs on a layered architecture:

`AgenticTrading -> Runtime Manager -> mobkit runtime -> agent/tool layer -> Goldfish provenance + memory`

### Runtime
- **meerkat-mobkit** is the canonical runtime backend for orchestrated agent execution.
- **Legacy runtime** remains available only as an explicit fallback / rollback path.

### Provenance and memory
- **Goldfish** is the durable provenance and experiment-memory layer.
- Goldfish is used for:
  - experiment lineage
  - promotion / retirement records
  - learning memory
  - family DNA packets used to improve future proposals

### Factory logic
AgenticTrading remains responsible for:
- family creation, revival, and exhaustion policy
- lineage generation and mutation policy
- deterministic gates for backtest, shadow, and paper transitions
- lineage-scoped paper accounting
- venue scope enforcement
- operator-facing dashboard and alerts

---

## What the factory does

For each strategy family, the factory can:

1. generate new proposals
2. critique and refine them
3. design or mutate Python strategy code
4. run backtest / walkforward / stress evaluation
5. promote promising lineages into shadow and then paper
6. hold paper-active models in low-compute observation mode
7. retire weak or exhausted lineages
8. use Goldfish memory to influence future variants

The system is designed to learn across lineages and families rather than rediscover the same mistakes repeatedly.

---

## Lifecycle

Typical lineage flow:

`idea -> proposal -> model_design -> backtest -> walkforward -> shadow -> paper -> retired`

Important lifecycle behaviors:
- families with all lineages retired can be revived deterministically
- “retired before paper” is not treated as true family exhaustion
- family exhaustion is based on repeated failed paper attempts or deterministic hard-stop conditions
- paper-active lineages are protected by a holdoff policy so the system does not waste compute continuously mutating healthy paper models

---

## Paper trading model

Paper trading is **lineage-scoped**, not portfolio-pooled.

Each research-factory lineage gets its own isolated paper account, with its own:
- balance
- P&L
- drawdown
- trade count
- paper-evidence history

Promotion and retirement decisions are based on the model’s own paper performance, not on a shared pooled account.

---

## Venue scope

The factory is designed for multi-venue operation, with scope enforcement so out-of-scope families do not monopolize compute.

Currently relevant venue stacks include:
- **Binance**
- **Polymarket**
- **Yahoo / Alpaca**
- **Betfair** (requires certificate-based setup before activation)

Different venues use different deterministic gate strictness depending on data richness and realism of pre-paper evidence.

---

## Safety model

This repo is built for autonomous **paper-mode learning** first.

Safety principles:
- live trading must remain hard-disabled unless explicitly enabled later
- deterministic gates, not LLMs alone, decide promotion and retirement
- budget governance and circuit-breakers remain active
- paper-active models use low-compute monitoring by default
- Goldfish provenance must remain on
- operator dashboard must expose runtime health, venue scope, blockers, and anomalies

---

## Dashboard

The repo includes a React/Vite control-tower dashboard for real-time operation monitoring.

The dashboard is intended to show:
- runtime and backend health
- active families and lineages
- lineage lifecycle state
- paper-active models
- deterministic gate blockers
- Goldfish health and DNA memory
- venue readiness and blockers
- compute / budget / session telemetry
- alerts and anomalies

Run the dashboard server with:

```bash
python3 scripts/factory_dashboard.py --host 127.0.0.1 --port 8788