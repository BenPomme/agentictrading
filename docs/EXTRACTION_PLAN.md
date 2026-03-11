# Extraction Plan

## Goal

Split the autonomous strategy factory out of the trading repo without breaking the current execution system.

## Current Source Repo

- `/Users/benjaminpommeraud/Desktop/Coding/Arbitrage`

## Target Repo

- `/Users/benjaminpommeraud/Documents/AgenticTrading`
- `https://github.com/BenPomme/agentictrading.git`

## Phase 1

Decouple the trading repo from direct `factory.*` imports.

Completed on the source branch:

- `portfolio/factory_client.py` now wraps runtime-mode and manifest lookups.
- `portfolio/runner_base.py` now consumes the wrapper instead of importing factory internals directly.
- `monitoring/portfolio_process_manager.py` now uses the wrapper for research-factory start blocking.

## Phase 2

Mirror the current factory control-plane code here:

- `factory/`
- `research/goldfish/`
- `scripts/check_binance_auth.py`
- `scripts/factory_*`
- factory-focused tests

This mirror is an extraction baseline, not the final standalone shape.

## Phase 3

Replace remaining source-repo dependencies inside the factory with explicit adapters:

- `config`
- portfolio state publishing
- experiment data providers currently imported from trading modules

Known dependencies still mirrored from the source design:

- `factory/experiment_runner.py`
  - now uses `factory.experiment_sources`
- `factory/orchestrator.py`
  - now uses `factory.state_store`
- `factory/execution_bridge.py`
  - now uses `factory.runtime_execution`

The adapters support two modes:

- standalone fallback logic
- optional integration with the execution repo via `EXECUTION_REPO_ROOT`

That keeps cross-repo coupling explicit and configurable rather than hidden in direct imports.

## Final Contract

The execution repo should only need:

- manifest reader
- candidate-context reader
- approved artifact loader

The factory repo should publish those outputs into shared storage and never be imported directly by the execution repo.
