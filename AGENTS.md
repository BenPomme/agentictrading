# AgenticTrading Operator Notes

## Repo Role

This repo is the standalone autonomous strategy factory.

It owns:

- agentic strategy invention
- lineage registry and memory
- experiment orchestration
- Goldfish sidecar workspaces
- evaluation and promotion governance
- manifest and artifact publication

It does **not** own live trading execution.

The execution repo remains:

- `/Users/benjaminpommeraud/Desktop/Coding/Arbitrage`

This repo may read execution state and optionally start execution runners through explicit adapters only.

## Working Style

Always start in plan work.

Prepare work with multiple agents ranked by complexity:

- use cheap/local agents first for search, grep, validation, scaffolding, and deterministic tasks
- use stronger agents only for architecture, cross-file refactors, or model/pipeline design

Use skills when available, and create skills for repeatable workflows.

Do not silently re-couple this repo to the execution repo through direct imports. Prefer adapters and explicit contracts.

## Current Status

Migration status: extracted and pushed to GitHub.

Remote:

- `https://github.com/BenPomme/agentictrading.git`

Local path:

- `/Users/benjaminpommeraud/Documents/AgenticTrading`

Current branch:

- `main`

Current state:

- standalone factory repo exists and is pushed
- production code no longer directly imports execution-repo modules for runtime behavior
- integration with the execution repo is explicit through:
  - `EXECUTION_REPO_ROOT`
  - `EXECUTION_PORTFOLIO_STATE_ROOT`
  - `factory/state_store.py`
  - `factory/runtime_execution.py`
  - `factory/experiment_sources.py`
- standalone smoke run succeeds
- extracted test subset passes

## Integration Contract

This repo should communicate with the execution repo through:

- approved manifests
- candidate context payloads
- packaged artifacts
- execution state snapshots

The execution repo should never import internal factory modules directly.

## Local Environment

Use the local `.env` in this repo.

Important fields:

- `EXECUTION_REPO_ROOT=/Users/benjaminpommeraud/Desktop/Coding/Arbitrage`
- `EXECUTION_PORTFOLIO_STATE_ROOT=/Users/benjaminpommeraud/Desktop/Coding/Arbitrage/data/portfolios`
- `AGENTIC_FACTORY_MODE=full`

Bootstrap helper:

```bash
python3 scripts/bootstrap_env.py --execution-repo-root /Users/benjaminpommeraud/Desktop/Coding/Arbitrage
```

## Commands

Smoke the standalone factory:

```bash
python3 scripts/factory_smoke.py --cycles 1 --json
```

Check Binance auth from this repo:

```bash
python3 scripts/check_binance_auth.py
```

List manifests:

```bash
python3 scripts/factory_manifest.py list
```

Run focused extracted tests:

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

The canonical roadmap now lives in:

- `/Users/benjaminpommeraud/Documents/AgenticTrading/docs/ROADMAP.md`

Keep that file up to date as work lands.

Current priorities:

1. Cross-machine portability and regular cloud sync of state, datasets, and model artifacts.
2. Structured idea pipeline from `ideas.md` into tracked agent work.
3. Scheduled agent reviews and review-driven retrain/retire/replace loops.
4. Tighten the execution health contract and reduce dashboard inference from partial files.
5. Continue replacing execution-repo-assumption tests with adapter-focused tests.
6. Continue improving strategy quality, not just factory plumbing.

## Fresh Session Handoff

If starting a new Codex session in this repo, assume:

- the migration is complete enough to continue work here
- the execution repo still exists separately at `/Users/benjaminpommeraud/Desktop/Coding/Arbitrage`
- the correct first validation step is:

```bash
python3 scripts/factory_smoke.py --cycles 1 --json
```

Then inspect:

- `factory/`
- `scripts/`
- `docs/EXTRACTION_PLAN.md`
- `README.md`

And continue from the `Next Priorities` section above.
