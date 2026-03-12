# Factory Roadmap

Last updated: 2026-03-11

## Current State

- Standalone factory repo is live and integrated with the execution repo through adapters and shared state.
- Repo-local operator dashboard is running.
- Real Codex-backed research agents are active for proposal generation and underperformance tweaks.
- Research lake and curated scorecards now exist through the execution repo research store.
- Positive ROI models and operator escalation candidates are surfaced in the dashboard.
- Challenger generation now follows an explicit `80% mutation / 20% new_model` policy.
- Background factory loop exists via `scripts/factory_loop.py`.
- `ideas.md` / `IDEAS.md` is now parsed into structured tracked ideas with statuses.
- A low-value online idea scout now adds generated ideas on a 48-hour cadence.
- Scheduled agent reviews for mature paper models are implemented.
- Debug-agent triage for runtime/model bugs with human-escalation support is implemented.
- Every factory-created `new_model` now needs a distinct generated name and a thesis normalized to `We believe we can create alpha by ...`.
- Scheduled agent reviews now emit explicit maintenance actions (`hold`, `retrain`, `rework`, `replace`, `retire`) and can directly increase rework/replacement pressure on weak lineages.
- Winner surfacing is stricter: immature or shared-evidence-only positives no longer qualify as true winner escalations.

## Completed

1. Extraction and adapter split from the execution repo.
2. Repo-local operator dashboard and execution monitor.
3. Real-agent proposal and tweak runtime using Codex.
4. Execution evidence ingestion into factory prompts.
5. Curated research-store scorecards and family model rankings.
6. Family-specific incumbent refresh jobs for cascade, polymarket, and funding.
7. Operator escalation path for strong paper winners.
8. Explicit challenger mix policy: `mutation=80`, `new_model=20`.
9. Bug-triggered debug agent with human-action escalation for credentials, venue restrictions, and similar operator-owned blockers.

## In Progress

1. Clear separation between execution failure, validation-blocked, and research-only states in the dashboard.
2. Continuous factory background operation.
3. Idea-to-lineage status quality: move more ideas from `adapted` into `tested`.
4. Debug-agent outcomes should feed retrain, retire, or human-escalation decisions more directly.
5. Shared-evidence dedupe and lineage-isolated paper assessment need tightening so one portfolio scorecard is not mistaken for multiple independent winners.
6. Every required execution-side model needs an explicit trainability contract so untrainable books are treated as bugs, not quietly left in paper purgatory.
7. Add a hard stalled-model workflow: if a running model does not make trading or required training progress for more than 8 hours, force review/debug/rework pressure and retire it if the stall persists through tweak budget.

## Next

1. Cross-machine portability and cloud sync
- Add regular export/sync of factory state, curated research datasets, and model artifacts so the factory can resume on another computer.
- Keep code, manifests, and light metadata in GitHub.
- Keep larger databases, DuckDB snapshots, Parquet lake partitions, and heavier model artifacts in syncable artifact storage rather than normal git blobs.
- Define a reproducible restore path: clone repo, pull synced artifacts, restore `.env`, resume factory loop.

2. Review-driven model maintenance
- Extend the new maintenance-action contract into dashboard queues and automated retrain refresh dispatch, not just lineage pressure/state.
- Make debug-agent outputs first-class maintenance inputs so runtime bugs and operator-owned blockers do not sit idle.

3. Structured idea pipeline, phase 2
- Promote idea usage from prompt context to explicit idea-backed lineage creation decisions.
- Add idea assignment history and per-idea experiment outcomes.
- Improve relevance filtering so manual and scouted ideas produce higher-quality family-specific challengers.

4. Winner surfacing and promotion discipline
- Tighten family league replacement rules.
- Make “why winner” and “why blocked” explicit in the dashboard.
- Keep operator signoff mandatory before any real-trading push.
- Separate shared portfolio evidence from independent lineage evidence, and require lineage-isolated paper books before treating repeated ROI signals as distinct model winners.

5. Execution-side health contract
- Make execution runners emit first-class runtime and health metadata directly.
- Reduce dashboard inference from partial state files.
- Keep hedge/funding evidence clean by filtering hedge watchlists to spot-supported symbols and normalizing exchange-driven order precision before paper execution.

6. Strategy quality
- Improve weak incumbent families: `cascade_alpha`, `contrarian_legacy`, `polymarket_quantum_fold`.
- Expand real-agent invention beyond the current enabled families once the maintenance loop is stable.

7. Trainability discipline
- Make every non-validation-only model expose `training_progress` and `trainability` in execution state.
- Fail fast when a required learner cannot train because of missing data, disabled model lanes, or broken feature builds.
- Surface `untrainable_model` directly to the factory so debug, retrain, and replacement agents treat it as an actionable defect.

8. Stalled-model maintenance policy
- Detect running models that have gone more than 8 hours without trading progress or required training progress.
- Surface `trade_stalled`, `training_stalled`, and `stalled_model` in execution evidence and the dashboard.
- Use that signal to trigger debug-agent review, maintenance review, challenger pressure, and eventual retirement if the model keeps stalling after rework.
