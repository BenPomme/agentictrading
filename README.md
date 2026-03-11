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

Bootstrap helper:

```bash
python3 scripts/bootstrap_env.py --execution-repo-root /Users/benjaminpommeraud/Desktop/Coding/Arbitrage
```

Key commands in extraction mode:

```bash
python3 scripts/check_binance_auth.py
python3 scripts/factory_smoke.py --cycles 1 --json
python3 scripts/factory_manifest.py list
```
