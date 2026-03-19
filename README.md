# AgenticTrading

Autonomous trading research factory and flagship reference app for Meerkat, MobKit, and Goldfish.

## What This Repo Is

AgenticTrading is an autonomous trading research and paper-trading factory.

This repository is the **public flagship reference app** for the Meerkat + Goldfish stack:

- **Meerkat** provides the agent harness and session/tool runtime
- **meerkat-mobkit** provides orchestration for multi-agent workflows
- **Goldfish** provides durable provenance, experiment memory, and reproducibility
- **AgenticTrading** provides the domain control plane, deterministic gates, dashboard, and paper-mode factory workflow that stress-tests the stack on a hard, realistic workload

The repo exists publicly to make that architecture inspectable, runnable, and improvable without exposing the full private trading edge.

This repo is **not** the live trading venue. Live trading remains explicitly disabled in the public repository unless enabled through a separate private operational process.

## Why This Exists Publicly

This project is open source so developers can:

- inspect a realistic multi-agent reference workload instead of toy demos
- see how orchestration, provenance, and deterministic safety gates fit together
- run the paper-mode factory locally
- contribute improvements to the public control plane, docs, dashboard, and safe example workflows

The goal is not to publish every private production advantage. The goal is to expose the architecture, interfaces, safety model, and developer experience of the stack.

## Status

AgenticTrading is a **paper-mode research factory**, not a public live-trading product.

Important safety constraints:

- live trading is hard-disabled in the public repo
- deterministic gates, not LLMs alone, decide promotion and retirement
- budget governance and circuit breakers remain active
- provenance and runtime boundaries are treated as first-class architecture constraints

## Public vs Private Boundary

Public in this repository:

- paper-mode research workflows
- dashboard and operator-facing control-plane surfaces
- orchestration and provenance integration
- deterministic safety, governance, and observability logic
- safe example families and demo-ready workflows

Private and intentionally excluded:

- live-trading enablement
- venue credentials, certificates, and secrets
- production deployment manifests and private infrastructure
- proprietary datasets or commercial-only connectors
- highest-alpha strategy logic and production heuristics

See [docs/OPEN_SOURCE_BOUNDARY.md](docs/OPEN_SOURCE_BOUNDARY.md) for the explicit boundary.

## Quickstart

This is a **Python-first repository**. There is no root npm package for AgenticTrading itself.

- Use `pip` to install the control plane and research factory code
- Use `npm` only if you want to work on the React dashboard in `dashboard-ui/`

### Install The Python Repo

Safe local evaluation should stay in paper-mode.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Run The Dashboard Server

```bash
python3 scripts/factory_dashboard.py --host 127.0.0.1 --port 8788
```

Then open the local dashboard and inspect the factory state without attempting to wire live venue credentials into the public repo.

### Optional: Work On The React Dashboard

Only needed for frontend development inside `dashboard-ui/`:

```bash
cd dashboard-ui
npm install
npm run dev
```

## Why It's Interesting

- long-running agent workflows with explicit orchestration boundaries
- deterministic safety gates for promotion, retirement, and budget control
- lineage-scoped paper evaluation instead of pooled performance hand-waving
- durable provenance and experiment memory through Goldfish
- replayable operator-facing state through the dashboard and telemetry surfaces

## Stack Architecture

The factory runs on a layered architecture:

`AgenticTrading -> Runtime Manager -> mobkit runtime -> agent/tool layer -> Goldfish provenance + memory`

Related projects:

- [Meerkat](https://github.com/lukacf/meerkat)
- [meerkat-mobkit](https://github.com/lukacf/meerkat-mobkit)
- [Goldfish](https://github.com/lukacf/goldfish)

License alignment:

- Meerkat: `MIT OR Apache-2.0`
- MobKit: `MIT OR Apache-2.0`
- Goldfish: `AGPL-3.0`
- AgenticTrading: `AGPL-3.0`

This keeps the lower-level infrastructure easy to adopt while keeping the provenance layer and flagship reference app reciprocal.

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
```

## Contributing

See:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)
- [SUPPORT.md](SUPPORT.md)

If you are contributing to the migration architecture, start with the docs under [docs/refactor](docs/refactor/).
