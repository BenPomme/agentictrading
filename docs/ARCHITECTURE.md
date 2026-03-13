# NEBULA Architecture

Last updated: 2026-03-13

## System Overview

NEBULA is an autonomous strategy factory that generates, tests, ranks, and promotes trading models. It runs as two long-lived processes plus a React dashboard:

```
factory_loop.py (15-min cycles)
  -> orchestrator.py (family iteration)
    -> agent_runtime.py (LLM-backed proposals/tweaks/critiques)
    -> experiment_runner.py (backtests, evaluations)
    -> promotion governance
    -> manifest publication

factory_dashboard.py (HTTP server, port 8788)
  -> operator_dashboard.py (snapshot assembly)
  -> dashboard-ui/dist/ (React SPA)
```

## Agent Runtime

### Provider Chain

```
codex CLI -> openai_api -> deterministic
```

Each agent task tries providers in order. The first success wins.

- **codex**: Calls the Codex CLI binary. Requires `codex login` and active Pro subscription quota.
- **openai_api**: Direct HTTP call to `https://api.openai.com/v1/chat/completions`. Uses `OPENAI_API_KEY` from `.env`. Supports both chat models (gpt-4.1-*) and reasoning models (o3, o4-mini).
- **deterministic**: Returns `success=false`. Used as a safe terminal fallback so the factory loop never crashes.

### Task Classification

Every agent request carries a `task_class` that determines model quality:

| Task Class | Description | Typical Tasks |
|---|---|---|
| `TASK_CHEAP` | Fast, low-cost | Simple parameter tweaks, formatting |
| `TASK_STANDARD` | Moderate reasoning | Standard proposals, routine research |
| `TASK_HARD` | Strong reasoning | Complex proposals, critiques, debug triage |
| `TASK_FRONTIER` | Best available | Family bootstrap (new family creation) |
| `TASK_DEEP` | Reserved premium | Currently unused (cost-guard capped) |

### Cost Guard

`_apply_cost_guard()` in `factory/agent_runtime.py`:

1. Reads the last N agent run artifacts (`FACTORY_AGENT_COST_WINDOW`, default 50).
2. Counts runs using `TASK_FRONTIER` or `TASK_DEEP`.
3. If the ratio exceeds `FACTORY_AGENT_EXPENSIVE_CAP_PCT` (default 10%), downgrades the current request to `TASK_HARD` and clears any model override.
4. Family bootstrap (`generate_family_proposal`) is always exempt.

### Task Class Assignment

Task class is assigned dynamically based on context:

- `_proposal_task_class()`: STANDARD by default, escalates to HARD when there are contradictory memories, critical health, or model quality issues.
- `_tweak_task_class()`: CHEAP by default, escalates to HARD when there are prior tweaks, contradictory metrics, or critical execution health.
- `_debug_task_class()`: CHEAP by default, escalates to HARD for critical bugs, repeated debug signatures.
- `_maintenance_task_class()`: STANDARD by default, escalates to HARD for replace/retire actions, critical health.
- `critique_post_evaluation()`: Always HARD (downgraded from DEEP).
- `generate_family_proposal()`: Always FRONTIER (exempt from cost guard).

## Data Pipeline

### Sources and Connectors

All connectors are defined in `factory/connectors.py` via `default_connector_catalog()`:

```
data/
  yahoo/
    ohlcv/          # 501 Parquet files (5yr daily OHLCV)
    metadata.json
    sp500_components.json
  alpaca/
    bars/           # Stock bars (when available)
    quotes/         # Stock quotes
    metadata.json
  binance/          # Crypto funding/price data
  betfair/          # Sports/event data
  polymarket/       # Prediction market data
  factory/
    state/          # Factory state (summary.json)
    agent_runs/     # Agent run artifacts (JSON per run)
    families/       # Family configs (hypothesis.json, genome.json)
  portfolios/       # Portfolio execution state
  backtest_results/ # Batch backtest output
```

### Refresh Scripts

| Script | Purpose | Frequency |
|---|---|---|
| `scripts/download_stock_data.py` | Bulk download 5yr Yahoo data | One-time |
| `scripts/refresh_yahoo_data.py` | Incremental Yahoo update | Daily |
| `scripts/refresh_alpaca_data.py` | Refresh Alpaca bars/quotes | As needed |
| `scripts/batch_backtest.py` | Systematic backtesting | On demand |

## Experiment Runner

`factory/experiment_runner.py` dispatches experiments by family:

- **hmm_regime_adaptive**: Loads Yahoo OHLCV, instantiates `HMMRegimeModel`, runs walk-forward backtest with 70% train split. Produces `EvaluationBundle` with walkforward, stress, shadow, and paper evaluations.
- **binance_funding_contrarian**: Uses Binance funding rate data.
- **binance_cascade_regime**: Uses Binance price/regime data.
- **polymarket_cross_venue**: Uses Polymarket cross-venue price data.

## Dashboard

The NEBULA Control Room dashboard is built with:

- **Frontend**: React 18, TypeScript, Vite, Chart.js, Framer Motion, CSS Modules
- **Backend**: Python HTTP server (`scripts/factory_dashboard.py`) serving the React build and API endpoints

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/snapshot` | GET | Full dashboard snapshot (feeds, factory, agents, portfolios) |
| `/api/portfolio/<id>/chart` | GET | Portfolio PnL chart data |
| `/api/factory/control` | POST | Factory pause/resume (`{"action": "pause"}` or `{"action": "resume"}`) |

### Key UI Sections

1. **API Feeds Strip**: Shows connectivity status for all 5 data connectors (Binance, Betfair, Polymarket, Yahoo, Alpaca)
2. **Factory Status**: Running/paused state with toggle control
3. **Agent Activity**: Recent agent runs with provider, model, success/failure status
4. **Portfolio Grid**: Active portfolio performance cards
5. **Lineage Atlas/Board**: Strategy lineage visualization
6. **Maintenance Queue**: Pending maintenance actions for operator review

## Configuration

### Required `.env` Variables

```
PORTFOLIO_STATE_ROOT=data/portfolios
FACTORY_EMBEDDED_EXECUTION_ENABLED=true
OPENAI_API_KEY=sk-proj-...
FACTORY_AGENT_PROVIDER_ORDER=codex,openai_api,deterministic
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
FACTORY_AGENT_EXPENSIVE_CAP_PCT=10
FACTORY_AGENT_COST_WINDOW=50
```

### Optional Model Override Variables

```
FACTORY_AGENT_OPENAI_MODEL_CHEAP=gpt-4.1-nano
FACTORY_AGENT_OPENAI_MODEL_STANDARD=gpt-4.1-mini
FACTORY_AGENT_OPENAI_MODEL_HARD=gpt-4.1
FACTORY_AGENT_OPENAI_MODEL_FRONTIER=o4-mini
FACTORY_AGENT_OPENAI_MODEL_DEEP=o3
```

## File Index

| File | Purpose |
|---|---|
| `factory/agent_runtime.py` | Agent execution, provider chain, cost guard, task classification |
| `factory/experiment_runner.py` | Experiment dispatch and evaluation |
| `factory/orchestrator.py` | Factory cycle orchestration, family iteration |
| `factory/connectors.py` | Data connector definitions and catalog |
| `factory/operator_dashboard.py` | Dashboard snapshot assembly |
| `factory/contracts.py` | Data contracts and type definitions |
| `scripts/factory_loop.py` | Long-running factory process |
| `scripts/factory_dashboard.py` | HTTP server for dashboard |
| `scripts/batch_backtest.py` | Standalone batch backtesting tool |
| `scripts/download_stock_data.py` | Bulk Yahoo data download |
| `scripts/refresh_yahoo_data.py` | Incremental Yahoo data refresh |
| `scripts/refresh_alpaca_data.py` | Alpaca data refresh |
| `research/goldfish/hmm_regime_adaptive/model.py` | HMM regime model implementation |
| `dashboard-ui/` | React frontend source |
| `config.py` | Configuration loading from `.env` |
| `AGENTS.md` | Operator notes, session handoff, learned preferences |
| `docs/ROADMAP.md` | Canonical roadmap and planning document |
| `ideas.md` | Strategy ideas intake |
