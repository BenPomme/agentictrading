# NEBULA Control Room

Autonomous strategy factory for generating, testing, ranking, and promoting trading models.

This repo is fully standalone. It owns research, paper execution, and the control plane.

No live trading should happen from this repo. This repo is the control plane and research plane.

## Dashboard

The NEBULA Control Room is a React + Vite dashboard with a mission-control aesthetic:

```bash
python3 scripts/factory_dashboard.py --host 0.0.0.0 --port 8788
```

Open `http://127.0.0.1:8788` to see API feed health, factory status, agent activity, portfolio performance, lineage atlas, and the maintenance queue.

## Execution Modes

The factory supports two paper execution modes:

### Embedded mode (default for standalone operation)

Runners live inside this repo via `factory.local_runner_main`. Set:

- `FACTORY_EMBEDDED_EXECUTION_ENABLED=true`
- `PORTFOLIO_STATE_ROOT=data/portfolios` (state written under this path)
- `EXECUTION_PORTFOLIO_STATE_ROOT=data/portfolios`

Start a runner: `python -m factory.local_runner_main --portfolio betfair_core --interval 60`

Minimum required values:

- `AGENTIC_FACTORY_MODE`
- `FACTORY_REAL_AGENTS_ENABLED`
- `FACTORY_AGENT_PROVIDER_ORDER`
- `FACTORY_ROOT`
- `FACTORY_GOLDFISH_ROOT`
- `PORTFOLIO_STATE_ROOT`

Commonly useful explicit values:

- `PREDICTION_POLICY_GATE_PATH`
- `PORTFOLIO_STATE_ROOT`
- `PREDICTION_MODEL_KINDS`
- `FACTORY_AGENT_ENABLED_FAMILIES`
- `FACTORY_AGENT_DEMO_FAMILY`
- `FACTORY_AGENT_CODEX_MODEL_CHEAP`, `FACTORY_AGENT_CODEX_MODEL_PROPOSAL`, `FACTORY_AGENT_CODEX_MODEL_STANDARD`
- `FACTORY_AGENT_CODEX_MODEL_HARD`, `FACTORY_AGENT_CODEX_MODEL_FRONTIER`, `FACTORY_AGENT_CODEX_MODEL_DEEP`
- `FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED`, `FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS`
- `FACTORY_AGENT_REASONING_CHEAP`, `FACTORY_AGENT_REASONING_PROPOSAL`, `FACTORY_AGENT_REASONING_STANDARD`
- `FACTORY_AGENT_REASONING_HARD`, `FACTORY_AGENT_REASONING_FRONTIER`, `FACTORY_AGENT_REASONING_DEEP`
- `FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED`, `FACTORY_AGENT_OLLAMA_MODEL`, `FACTORY_AGENT_LOG_DIR`
- `FACTORY_CHALLENGER_MUTATION_PCT`, `FACTORY_CHALLENGER_NEW_MODEL_PCT`
- `BF_USERNAME`, `BF_PASSWORD`, `BF_APP_KEY`, `BF_CERTS_PATH`
- `BINANCE_*` credentials
- public `POLYMARKET_*` endpoint settings for data-only research/paper simulation

Venue credentials are optional unless you are running the venue auth or data scripts from this repo.

For real agent-backed research:

- primary backend: local `codex` CLI auth with task-routed GPT models
- first fallback: OpenAI API (`OPENAI_API_KEY` in `.env`) via direct HTTP calls
- second fallback: deterministic inventor/tweaker
- optional local fallback for cheap tasks only: `ollama`
- high-value loops can explicitly request Codex child-agent decomposition for proposal, family bootstrap, review, and debug tasks

Provider order is configured via `FACTORY_AGENT_PROVIDER_ORDER=codex,openai_api,deterministic`.

If `codex` is the first provider, make sure the machine is already authenticated with `codex login`. When Codex quota is exhausted, the factory automatically falls back to the OpenAI API.

### Model Tiering

| Task Class | Codex Model | OpenAI API Fallback | Use Case |
|---|---|---|---|
| `cheap_structured` | gpt-5.1-codex-mini | gpt-4.1-nano | Classification, formatting, simple tweaks |
| `standard_research` | gpt-5.1-codex | gpt-4.1-mini | Standard proposals, moderate research |
| `hard_research` | gpt-5.2-codex | gpt-4.1 | Complex proposals, critiques, debug |
| `frontier_research` | gpt-5.3-codex | o4-mini | Family bootstrap only |
| `deep_review` | gpt-5.4 | o3 | Reserved; cost-guard capped at 10% |

### Cost Guard

A rolling-window cost guard (`_apply_cost_guard()` in `factory/agent_runtime.py`) enforces that expensive models (`TASK_FRONTIER`/`TASK_DEEP`) are used at most `FACTORY_AGENT_EXPENSIVE_CAP_PCT` percent of recent runs (default 10%, window of 50 runs). When over budget, requests are auto-downgraded to `TASK_HARD`. Family bootstrap tasks are exempt.

Every real agent call writes a JSON artifact under `data/factory/agent_runs/`, and the NEBULA dashboard surfaces recent runs plus per-lineage agent provenance.

### Data Sources

| Source | Data | Location | Refresh Script |
|---|---|---|---|
| Yahoo Finance | 5yr daily OHLCV for S&P 500 + ETFs + VIX | `data/yahoo/ohlcv/` (501 Parquet files) | `scripts/refresh_yahoo_data.py` |
| Alpaca | Stock bars, quotes | `data/alpaca/` | `scripts/refresh_alpaca_data.py` |
| Binance | Crypto funding/price data | `data/binance/` | Built-in connector |
| Betfair | Sports/event data | `data/betfair/` | Built-in connector |
| Polymarket | Prediction market data | `data/polymarket/` | Built-in connector |

### Batch Backtesting & Optuna Optimization

Run systematic backtests outside the factory loop:

```bash
# Grid search (exhaustive)
python3 scripts/batch_backtest.py --param-grid --tickers "SPY,QQQ,AAPL,MSFT,NVDA"

# Optuna TPE optimization (much faster, Bayesian)
python3 scripts/batch_backtest.py --optimize --n-trials 50 --tickers "SPY,QQQ"
```

Optimize all champion families at once (auto-discovers data):

```bash
python3 scripts/optimize_all_champions.py --n-trials 50
python3 scripts/optimize_all_champions.py --families binance_funding_contrarian --force
```

The `backtest/` module (ported from stockpred) provides walk-forward backtesting, Optuna TPE optimization, and stability scoring — all running locally without LLM tokens. The factory auto-triggers optimization daily for families lacking results via `_trigger_auto_optimization()`.

### Polymarket Data Collection

```bash
python3 scripts/fetch_polymarket_history.py --max-markets 200 --interval 1h
```

Fetches price history from the Polymarket CLOB API and stores as Parquet in `data/polymarket/prices_history/`. Run daily to accumulate history for future backtesting.

### Market-Hours Scheduling

The factory is market-hours aware:
- Stock families (`hmm_regime_adaptive`) paper-test only during US market hours (Mon-Fri 9:30-16:00 ET)
- Always-on families (crypto, betting, prediction) get priority during off-hours and weekends
- Config: `FACTORY_STOCK_MARKET_TZ`, `FACTORY_STOCK_MARKET_OPEN`, `FACTORY_STOCK_MARKET_CLOSE`

### Backtest-Positive Gate

No model reaches paper trading without positive backtest ROI:
- Yahoo/Binance: full gate (ROI > 0 + optimization required)
- Betfair: relaxed (walkforward evidence only)
- Polymarket: exempt until historical data accumulates
- Dashboard shows backtest ROI badge on each model card

### Lineage Retirement

Lineages that never achieve positive results are retired aggressively:
- `FACTORY_MAX_LOSS_STREAK=3` consecutive negative evaluations
- `FACTORY_BACKTEST_TTL_HOURS=48` stuck in backtest without positive results
- Learning is recorded in goldfish memory before retirement

The default real-agent family allowlist is:

- `binance_funding_contrarian`
- `binance_cascade_regime`
- `polymarket_cross_venue`
- `hmm_regime_adaptive` (instrument-agnostic HMM regime detection, uses Yahoo OHLCV data)

Those are the families currently intended to invent, test, retire, and replace paper models with agent-backed challengers.

The factory also supports explicit incumbent refresh jobs through the execution repo:

- `FACTORY_EXECUTION_REFRESH_ENABLED=true`
- `FACTORY_EXECUTION_REFRESH_FAMILIES=binance_funding_contrarian,binance_cascade_regime,polymarket_cross_venue`
- `FACTORY_LOOP_INTERVAL_SECONDS=900`
- `FACTORY_LOOP_LOG_PATH=data/factory/factory_loop.log`
- champion lineages in those families write `execution_refresh.json` into each run package
- the execution-side adapter command is `scripts/factory_refresh_models.py` in the execution repo

Refresh behavior is family-specific:

- `binance_funding_contrarian`: runs the contrarian/regime training suites and refreshes funding model comparison artifacts
- `binance_cascade_regime`: rebuilds a cascade policy artifact from paper learner state
- `polymarket_cross_venue`: rebuilds a ranked model-league artifact from paper model states

Bootstrap helper:

```bash
# Standalone embedded mode (default):
python3 scripts/bootstrap_env.py

# External mode (connected to a separate execution repo):
python3 scripts/bootstrap_env.py --execution-repo-root /path/to/execution-repo
```

Key commands in extraction mode:

```bash
python3 scripts/check_binance_auth.py
python3 scripts/factory_smoke.py --cycles 1 --json
python3 scripts/factory_agent_demo.py --family binance_funding_contrarian --json
python3 scripts/factory_manifest.py list
python3 scripts/factory_dashboard.py --host 127.0.0.1 --port 8787
python3 scripts/factory_loop.py --interval-seconds 900
```

The dashboard reads:

- factory state from `data/factory/state/summary.json`
- execution portfolio snapshots through `EXECUTION_PORTFOLIO_STATE_ROOT` (external mode) or `PORTFOLIO_STATE_ROOT` (embedded mode)
- idea intake from `ideas.md` or `IDEAS.md` in the repo root

The demo runner is the fastest proof path for real agents. It isolates a temporary factory root by default, runs the specified family through the orchestrator, and prints the resulting real-agent lineages plus recent Codex artifacts.
