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

Paper execution runs via embedded runners. This repo is fully self-contained.

## Learned User Preferences

- Be fully autonomous: do not ask the user to restart dashboards, servers, or processes -- do it yourself; execute plans fully without prompting for manual steps
- Always verify work visually using browser tools (navigate to dashboard, take screenshots, inspect DOM) before claiming something works -- also check logs and live behavior
- Use available skills and MCPs proactively instead of doing things manually
- Show progress frequently -- the user dislikes silent long-running work with no visibility; appearing idle triggers frustration
- Delegate routine tasks to cheap subagents; reserve expensive models (gpt-5.4, o3, Opus 4.6) for critical reasoning and final review only
- When Codex CLI quota is exhausted, gracefully fall back to the OpenAI API; learning and paper trading must continue even without Codex credits and resume automatically when credits return
- The user works with Codex CLI in parallel sessions; always check for uncommitted work on `main` before assuming the worktree is current; scan Codex work and build on top of it
- Preserve system state across sessions: if ideas were processed, models were trading, feeds were healthy -- that state must not regress
- Keep this repo self-contained; paper trading and execution must run inside this repo, not the old Arbitrage system; never create runtime dependencies on external repos
- Update `docs/ROADMAP.md` and `AGENTS.md` as work lands so future sessions can pick up context expertly
- Start with a plan for complex multi-step work; execute the plan systematically
- Commit directly to `main` and push to GitHub when the user says "commit to main"
- All fixes and improvements must be generic/systemic, not family-specific patches -- changes to fitness, retirement, promotion apply to ALL families
- Never retire a model before it has traded; if no backtest data is available, paper trade first to generate data -- retiring untested models is always wrong
- Factory agents must understand venue schedules: stock-market models cannot trade on weekends/after-hours; never penalize, review, tweak, or retire a model for being idle when its market is closed
- Do not repeat instructions the user already gave earlier in the session; track and remember all stated rules within a conversation

## Learned Workspace Facts

- Remote: `https://github.com/BenPomme/agentictrading.git`; main repo at `/Users/benjaminpommeraud/Documents/AgenticTrading`, bng worktree at `.cursor/worktrees/AgenticTrading/bng`
- Standalone embedded execution mode; dashboard at `http://127.0.0.1:8787` (React build from `dashboard-ui/dist`); factory loop at 15-minute intervals
- `.env` must set `EXECUTION_PORTFOLIO_STATE_ROOT=data/portfolios` (local, not the Arbitrage repo) so the dashboard reads fresh heartbeats from embedded runners; if it points to the external repo, portfolios show as degraded/heartbeat_stale
- `.env` is gitignored; must contain `OPENAI_API_KEY`, `FACTORY_AGENT_PROVIDER_ORDER`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`; when searching for files like `.env`, search across branches and repos, not only the current worktree
- Agent provider chain: `codex,openai_api,deterministic`; Codex model names (e.g. `gpt-5.2-codex`) are automatically stripped before OpenAI API fallback
- Cost guard: `FACTORY_AGENT_EXPENSIVE_CAP_PCT=10`; OpenAI API model tiers: CHEAP=`gpt-4.1-nano`, STANDARD=`gpt-4.1-mini`, HARD/FRONTIER=`gpt-5-mini`, DEEP=`gpt-5.4`
- `data/` contains all local data; large blobs (yahoo, alpaca, polymarket, agent_runs, backtest_results) are gitignored
- Data sources: Yahoo (503 Parquet files, 5yr OHLCV), Alpaca (free tier, no SIP bars), Binance (50 klines CSVs, 1yr hourly), Betfair, Polymarket (CLOB history pipeline)
- Market-hours scheduling: stock families (yahoo*, alpaca*) paper-trade 9:30-16:00 ET only; crypto/betting families trade 24/7; factory agent reviews, debug, tweaks, and retirement are all suppressed for stock-market lineages when the market is closed -- heartbeat_stale/no_trade_syndrome are expected idle, not bugs
- Automated paper trading: 5 embedded runners managed by `EmbeddedExecutionManager` -- `contrarian_legacy` (8hr funding), `cascade_alpha` (8hr funding), `alpaca_paper` (1hr HMM, market-hours only), `betfair_core` (5min generic), `polymarket_quantum_fold` (5min generic); P&L charts update per runner cycle interval, not in real-time
- Data refresh scheduler: `scripts/data_refresh_scheduler.py` daemon runs Yahoo (6hr), Alpaca (6hr), and Binance funding (4hr) refreshes in background
- Backtest engine (`backtest/` module): Optuna TPE + walk-forward; backtest-positive gate before paper promotion (Polymarket exempt)
- Fitness formula (evaluation.py): profit floor ensures models with positive ROI and 10+ trades never score below `roi * 2.0`; hard vetoes reduced to -10 each; failure_rate weight reduced to 40; capacity/regime vetoes skipped for models with <50 trades
- Retirement rules: untested models (no trades) get a paper trial (7 days, configurable) instead of being retired; only retire after paper trial if model has traded and results are negative; 3 consecutive negative evals or expired paper trial triggers retirement; learning recorded in goldfish memory
- TASK_LOCAL bypasses agent runtime for pure computation (backtest, optimization) -- no LLM tokens spent
- Auto-optimization: `_trigger_auto_optimization()` in orchestrator spawns `scripts/optimize_all_champions.py` once/day per family (TASK_LOCAL subprocess)
- Agent cost downgrades: post_eval_critique→TASK_STANDARD; debug/maintenance/tweak escalation tightened; 40-60% cost reduction per cycle
- Dashboard P&L charts: API returns `points` key (not `balance_points`); charts only render when `balance_history.jsonl` has data; chart refresh cadence matches runner cycle interval (5min for generic, 1hr for HMM, 8hr for funding)
- Idea-to-model pipeline: `experiment_runner.run()` and `orchestrator._collect_evidence()` must have a generic fallback for new families; hardcoded family dispatch lists cause new ideas to get stuck at `goldfish_run` with `missing_*_evidence` blockers

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
python3 scripts/factory_dashboard.py --host 0.0.0.0 --port 8787
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

Optuna TPE optimization (HMM on Yahoo):

```bash
python3 scripts/batch_backtest.py --optimize --n-trials 50
```

Optuna optimization for Binance families:

```bash
python3 scripts/batch_backtest.py --optimize --family binance_funding_contrarian --n-trials 30
```

Optimize all champion families at once:

```bash
python3 scripts/optimize_all_champions.py --n-trials 50
```

Polymarket historical data collection:

```bash
python3 scripts/fetch_polymarket_history.py --max-markets 200
```

Refresh Yahoo data (incremental daily):

```bash
python3 scripts/refresh_yahoo_data.py
```

Refresh Alpaca data:

```bash
python3 scripts/refresh_alpaca_data.py
```

Refresh Binance funding rates:

```bash
python3 scripts/refresh_binance_funding.py
```

Start the data refresh scheduler (daemon):

```bash
python3 scripts/data_refresh_scheduler.py
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

## Next Priorities

The canonical roadmap lives in `docs/ROADMAP.md`. Keep it up to date as work lands.

## Agent Cost Architecture

The factory enforces a tiered cost model for all agent runs:

- **Cost guard**: `_apply_cost_guard()` in `factory/agent_runtime.py` checks the rolling window of recent runs. If expensive tiers (`TASK_FRONTIER`/`TASK_DEEP`) exceed `FACTORY_AGENT_EXPENSIVE_CAP_PCT` (default 10%), subsequent expensive requests are auto-downgraded to `TASK_HARD`.
- **Family bootstrap exempt**: `generate_family_proposal` always uses `TASK_FRONTIER` — creating new strategy families needs the best model.
- **Provider chain**: `codex -> openai_api -> deterministic`. Each provider is tried in order; the first success wins. The `codex` provider calls the Codex CLI; `openai_api` calls `https://api.openai.com/v1/chat/completions` directly with the API key from `.env`.
- **Model tiers**: CHEAP=`gpt-4.1-nano`, STANDARD=`gpt-4.1-mini`, HARD=`gpt-5-mini`, FRONTIER=`gpt-5-mini`, DEEP=`gpt-5.4`. Model design always uses DEEP (gpt-5.4). The `temperature` param is skipped for gpt-5 family models (API compatibility).
- **Task class downgrades (2026-03-14)**: `post_eval_critique` → `TASK_STANDARD` (gpt-4.1-mini). Debug escalation requires BOTH critical health AND repeated debug for TASK_HARD. Maintenance only escalates to TASK_HARD for replace/retire actions. Tweak requires `tweak_count >= 2` for TASK_HARD, `>= 1` for TASK_STANDARD, else TASK_CHEAP. Estimated 40-60% cost reduction per cycle.
- **Auto-optimization**: `_trigger_auto_optimization()` spawns `optimize_all_champions.py` as a background subprocess (TASK_LOCAL, no tokens). Runs once per family per day, tracked via `data/factory/state/last_auto_optimize.json`.
- **Auto-promotion**: `_promote_optimized_lineages()` re-evaluates champions after optimization results land.

## Data Pipeline

- **Yahoo Finance**: 501 Parquet files in `data/yahoo/ohlcv/`. Bulk download via `scripts/download_stock_data.py`. Daily incremental via `scripts/refresh_yahoo_data.py`. Used by HMM regime-adaptive family and batch backtest.
- **Alpaca**: Stock bars and quotes in `data/alpaca/`. Refresh via `scripts/refresh_alpaca_data.py`. Requires API keys in `.env`. Free tier does not include SIP bar data.
- **Binance**: Existing connector reads from `data/binance/`. Used by funding-contrarian and cascade families.
- **Betfair**: Existing connector reads from `data/betfair/`. Used by betfair families.
- **Polymarket**: Existing connector reads from `data/polymarket/`. Used by cross-venue family.
- **Connectors**: All defined in `factory/connectors.py` via `default_connector_catalog()`. Dashboard `api_feeds` strip reads from these connectors.

## Experiment Runner Families

The experiment runner (`factory/experiment_runner.py`) uses **generic backtest dispatch** -- no hardcoded family routing. Models with `model_code_path` are backtested via `factory/generic_backtest.py`. LLM-designed model code follows the `StrategyModel` protocol defined in `factory/model_protocol.py`.

Active LLM-generated families (as of 2026-03-15):
- **cross_venue_probability_elasticity**: Polymarket cross-venue arbitrage
- **funding_term_structure_dislocation**: Binance funding rate term structure
- **liquidation_rebound_absorption**: Binance liquidation cascade rebounds
- **vol_surface_dispersion_rotation**: Yahoo/Alpaca volatility surface dispersion

Dynamic runners in `factory/runners/`: `dynamic_runner.py`, `generic_runner.py`, `hmm_runner.py`, `funding_runner.py`.

## Batch Backtest

`scripts/batch_backtest.py` is a standalone tool for systematic backtesting outside the factory loop:

- Parameter grid search: `n_states` (2-5) x `lookback_days` (20,40,60)
- Train/test split: configurable (default 3yr train, 1yr test)
- Results: JSON files in `data/backtest_results/{family}/{ticker}_results.json`
- Usage: `python3 scripts/batch_backtest.py --param-grid --tickers "SPY,QQQ,AAPL"`

## Fresh Session Handoff

If starting a new session in this repo:

- check for uncommitted Codex CLI work on `main` AND on the `nebula-agent-cost-guard` branch (bng worktree) before starting; if the branch is ahead of `main`, merge it with `git merge nebula-agent-cost-guard --ff-only`
- verify `main` has the latest commits (should include "Eliminate hardcoded models" and "Auto data refresh"); if not, merge from `nebula-agent-cost-guard`
- verify the dashboard is running at http://127.0.0.1:8787 (start it if not); rebuild with `cd dashboard-ui && npm install && npm run build` if assets are stale
- verify the factory loop is running (`ps aux | grep factory_loop`)
- verify embedded runners are alive (`ps aux | grep local_runner_main`); check heartbeat files for `skipped: market_closed` on weekends
- verify data refresh scheduler is running (`ps aux | grep data_refresh_scheduler`)
- read `docs/ROADMAP.md` for current priorities and recent completions
- inspect the dashboard visually with browser tools to confirm system health
- check agent run success rate: `ls -lt data/factory/agent_runs/ | head` and verify recent runs show `openai_api` provider with `success: true`
- if data feeds are showing stale/warning, run the relevant refresh scripts
- if factory loop crashed, check for Python import errors (missing `logging` etc.) in the traceback
- never start the factory on the old code (`main` at `a62cf59` or earlier) -- always verify families are LLM-generated (not hardcoded templates) by checking `factory/orchestrator.py` for `_design_model_for_family`
