# AgenticTrading

Autonomous strategy factory for generating, testing, ranking, and promoting trading models.

This repo is being extracted from the execution repo at:

- `/Users/benjaminpommeraud/Desktop/Coding/Arbitrage`

Target ownership split:

- `Arbitrage`: execution, paper/live runners, venue adapters, trading dashboard
- `AgenticTrading`: research factory, agent system, lineage registry, experiment orchestration, promotion governance

The near-term contract between the two repos is:

- approved manifests
- candidate context payloads
- packaged artifacts
- shared storage root or object storage bucket

No live trading should happen from this repo. This repo is the control plane and research plane.

To connect this repo to the execution repo during extraction, set:

- `EXECUTION_REPO_ROOT=/absolute/path/to/Arbitrage`

That allows the factory to read execution state and, where adapters support it, launch execution runners without importing the execution repo directly from business logic modules.

Use the local `.env` in this repo as the operator contract.

Minimum required values for this repo shape:

- `AGENTIC_FACTORY_MODE`
- `FACTORY_REAL_AGENTS_ENABLED`
- `FACTORY_AGENT_PROVIDER_ORDER`
- `FACTORY_ROOT`
- `FACTORY_GOLDFISH_ROOT`
- `EXECUTION_REPO_ROOT`
- `EXECUTION_PORTFOLIO_STATE_ROOT`

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
- high-value loops can explicitly request Codex child-agent decomposition for proposal, family bootstrap, review, and debug tasks
- default fallback: deterministic inventor/tweaker
- optional local fallback for cheap tasks only: `ollama`

If `codex` is the first provider in `FACTORY_AGENT_PROVIDER_ORDER`, make sure the machine is already authenticated with `codex login`.

The v1 default routing is:

- `cheap_structured` -> `gpt-5.1-codex-mini`
- `proposal_generation` -> `gpt-5.4`
- `standard_research` -> `gpt-5.1-codex`
- `hard_research` -> `gpt-5.2-codex`
- `frontier_research` -> `gpt-5.3-codex`
- `deep_review` -> `gpt-5.4`

Every real agent call writes a JSON artifact under `data/factory/agent_runs/`, and the repo-local dashboard surfaces recent runs plus per-lineage agent provenance.

The default real-agent family allowlist is:

- `binance_funding_contrarian`
- `binance_cascade_regime`
- `polymarket_cross_venue`

Those are the families currently intended to invent, test, retire, and replace paper models with Codex-backed challengers.

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
python3 scripts/bootstrap_env.py --execution-repo-root /Users/benjaminpommeraud/Desktop/Coding/Arbitrage
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
- execution portfolio snapshots through `EXECUTION_PORTFOLIO_STATE_ROOT`
- idea intake from `ideas.md` or `IDEAS.md` in the repo root

The demo runner is the fastest proof path for real agents. It isolates a temporary factory root by default, runs the specified family through the orchestrator, and prints the resulting real-agent lineages plus recent Codex artifacts.
