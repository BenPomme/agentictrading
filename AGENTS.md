# NEBULA Control Room — Operator Notes

## Repo Role

This repo is the **NEBULA** standalone autonomous strategy factory.

It owns:

- agentic strategy invention and orchestration
- lineage registry and learning memory
- experiment orchestration and evaluation
- Goldfish sidecar workspaces
- promotion governance and manifest publication
- data ingestion (Yahoo, Alpaca, Binance, Betfair, Polymarket)
- NEBULA Control Room dashboard (React + Vite)

It does **not** own live trading execution.

In standalone mode, paper execution runs via embedded runners. An external execution repo can optionally be connected through `EXECUTION_REPO_ROOT`.

## Learned User Preferences

- Be fully autonomous: do not ask the user to restart dashboards, servers, or processes -- do it yourself
- Always verify work visually using browser tools (navigate to dashboard, take screenshots, inspect DOM) before claiming something works
- Use available skills and MCPs proactively instead of doing things manually
- Show progress frequently -- the user dislikes silent long-running work with no visibility
- Use cheap/local agents and models for simple tasks (classification, formatting, tweaks); reserve expensive models only for tasks requiring serious reasoning
- Expensive models (gpt-5.4, gpt-5.3, o3) should be used no more than 5-10% of total agent runs; the cost guard enforces this automatically
- When Codex CLI quota is exhausted, gracefully fall back to the OpenAI API; never let quota exhaustion block the factory loop or paper trading
- The user works with Codex CLI in parallel sessions; always check for uncommitted work on `main` before assuming the worktree is current
- Preserve system state across sessions: if ideas were processed, models were trading, feeds were healthy -- that state must not regress
- Keep this repo self-contained; no external repo dependencies required for standalone operation
- Update `docs/ROADMAP.md` as work lands -- it is the canonical planning document
- Do not add unnecessary HubSpot, marketing, or unrelated MCP servers to the Codex config
- Start with a plan for complex multi-step work; execute the plan systematically
- Delegate mechanical/routine tasks to cheaper subagents; handle only critical/hard tasks directly
- All factory and research data should be version-controlled (data/ is tracked in git)

## Learned Workspace Facts

- Remote: `https://github.com/BenPomme/agentictrading.git`
- Local main repo: `/Users/benjaminpommeraud/Documents/AgenticTrading`
- bng worktree: `/Users/benjaminpommeraud/.cursor/worktrees/AgenticTrading/bng`
- Execution mode: standalone embedded (FACTORY_EMBEDDED_EXECUTION_ENABLED=true)
- Dashboard: `http://127.0.0.1:8788` served by `scripts/factory_dashboard.py` (React build from `dashboard-ui/dist`)
- Factory loop: `scripts/factory_loop.py` (15-minute cycle interval by default)
- `.env` is gitignored and must exist in the repo root for the dashboard and factory loop to read config
- `.env` must contain `PORTFOLIO_STATE_ROOT`, `OPENAI_API_KEY`, `FACTORY_AGENT_PROVIDER_ORDER`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`
- Agent provider chain: `codex,openai_api,deterministic` (Codex CLI first, then direct OpenAI API fallback, then empty deterministic)
- Cost guard: `FACTORY_AGENT_EXPENSIVE_CAP_PCT=10` caps frontier/deep model usage at 10% of recent runs
- `data/` directory contains all local data (portfolios, Yahoo OHLCV, Alpaca, factory state) — no external symlinks
- Yahoo data: 501 Parquet files in `data/yahoo/ohlcv/` (5yr daily OHLCV for S&P 500 + ETFs + VIX + Treasuries)
- Alpaca data: stock quotes/bars in `data/alpaca/` (free tier, no SIP bars)
- Portfolio runners run via embedded execution (`factory.local_runner_main`) in standalone mode
- HMM regime-adaptive family is wired into experiment runner and uses Yahoo OHLCV data

## Integration Contract

This repo should communicate with the execution repo through:

- approved manifests
- candidate context payloads
- packaged artifacts
- execution state snapshots

The execution repo should never import internal factory modules directly.

## Commands

Start the NEBULA Control Room dashboard:

```bash
python3 scripts/factory_dashboard.py --host 0.0.0.0 --port 8788
```

Start the factory loop:

```bash
python3 scripts/factory_loop.py --json
```

Smoke the standalone factory:

```bash
python3 scripts/factory_smoke.py --cycles 1 --json
```

Rebuild the React dashboard (after UI changes):

```bash
cd dashboard-ui && npm run build
```

Run batch backtests on Yahoo data:

```bash
python3 scripts/batch_backtest.py --param-grid --tickers "SPY,QQQ,AAPL,MSFT"
```

Refresh Yahoo data (incremental daily):

```bash
python3 scripts/refresh_yahoo_data.py
```

Refresh Alpaca data:

```bash
python3 scripts/refresh_alpaca_data.py
```

Bulk download Yahoo data (one-time, 5 years):

```bash
python3 scripts/download_stock_data.py
```

Run focused tests:

```bash
python3 -m pytest -q tests/unit/test_factory_evaluation.py tests/unit/test_factory_promotion.py tests/unit/test_factory_registry.py tests/unit/test_factory_runtime_mode.py tests/unit/test_factory_strategy_inventor.py tests/unit/test_factory_execution_bridge.py
```

## Known External Constraints

Betfair:

- execution repo currently fails Betfair login with `BETTING_RESTRICTED_LOCATION`
- this is an external account/location issue, not a factory-code issue

Binance:

- demo futures auth works
- production futures auth is still not needed and still unresolved

Polymarket:

- execution repo runner now starts in paper mode
- CLOB request-shape bug was fixed in the execution repo

## Important Design Rules

Do not add live trading logic here.

Do not copy execution-repo secrets into this repo unless explicitly required.

Do not expand direct imports from the execution repo in production code.

If you need execution-repo functionality:

- first prefer adapter interfaces
- second prefer reading artifacts or state files
- only use optional integration through `EXECUTION_REPO_ROOT`

## Next Priorities

The canonical roadmap lives in `docs/ROADMAP.md`. Keep it up to date as work lands.

## Agent Cost Architecture

The factory enforces a tiered cost model for all agent runs:

- **Cost guard**: `_apply_cost_guard()` in `factory/agent_runtime.py` checks the rolling window of recent runs. If expensive tiers (`TASK_FRONTIER`/`TASK_DEEP`) exceed `FACTORY_AGENT_EXPENSIVE_CAP_PCT` (default 10%), subsequent expensive requests are auto-downgraded to `TASK_HARD`.
- **Family bootstrap exempt**: `generate_family_proposal` always uses `TASK_FRONTIER` — creating new strategy families needs the best model.
- **Provider chain**: `codex -> openai_api -> deterministic`. Each provider is tried in order; the first success wins. The `codex` provider calls the Codex CLI; `openai_api` calls `https://api.openai.com/v1/chat/completions` directly with the API key from `.env`.
- **Proposal model default**: Changed from `gpt-5.4` to `gpt-5.2-codex` to avoid burning expensive budget on routine proposals.

## Data Pipeline

- **Yahoo Finance**: 501 Parquet files in `data/yahoo/ohlcv/`. Bulk download via `scripts/download_stock_data.py`. Daily incremental via `scripts/refresh_yahoo_data.py`. Used by HMM regime-adaptive family and batch backtest.
- **Alpaca**: Stock bars and quotes in `data/alpaca/`. Refresh via `scripts/refresh_alpaca_data.py`. Requires API keys in `.env`. Free tier does not include SIP bar data.
- **Binance**: Existing connector reads from `data/binance/`. Used by funding-contrarian and cascade families.
- **Betfair**: Existing connector reads from `data/betfair/`. Used by betfair families.
- **Polymarket**: Existing connector reads from `data/polymarket/`. Used by cross-venue family.
- **Connectors**: All defined in `factory/connectors.py` via `default_connector_catalog()`. Dashboard `api_feeds` strip reads from these connectors.

## Experiment Runner Families

The experiment runner (`factory/experiment_runner.py`) dispatches experiments based on `lineage.family_id`:

- **hmm_regime_adaptive**: Uses Yahoo OHLCV, `HMMRegimeModel` from `research/goldfish/hmm_regime_adaptive/model.py`. Walk-forward backtest with 70% train split.
- **binance_funding_contrarian**, **binance_cascade_regime**, **polymarket_cross_venue**: Existing families using venue-specific data.

## Batch Backtest

`scripts/batch_backtest.py` is a standalone tool for systematic backtesting outside the factory loop:

- Parameter grid search: `n_states` (2-5) x `lookback_days` (20,40,60)
- Train/test split: configurable (default 3yr train, 1yr test)
- Results: JSON files in `data/backtest_results/{family}/{ticker}_results.json`
- Usage: `python3 scripts/batch_backtest.py --param-grid --tickers "SPY,QQQ,AAPL"`

## Fresh Session Handoff

If starting a new session in this repo:

- check for uncommitted Codex CLI work on `main` before starting
- verify the dashboard is running at http://127.0.0.1:8788 (start it if not)
- verify the factory loop is running (`ps aux | grep factory_loop`)
- verify embedded runners are alive if applicable
- read `docs/ROADMAP.md` for current priorities and recent completions
- inspect the dashboard visually with browser tools to confirm system health
- check agent run success rate: `ls -lt data/factory/agent_runs/ | head` and verify recent runs show `openai_api` provider with `success: true`
- if data feeds are showing stale/warning, run the relevant refresh scripts
- if factory loop crashed, check for Python import errors (missing `logging` etc.) in the traceback
