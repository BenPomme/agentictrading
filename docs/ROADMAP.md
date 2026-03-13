# Factory Roadmap

Last updated: 2026-03-13

## Current State

- **NEBULA is fully standalone** — zero dependencies on the Arbitrage repo. All portfolio, funding, and prediction data has been ported locally.
- NEBULA Control Room dashboard (React + Vite) is live with mission-control aesthetic.
- Real Codex-backed research agents are active for proposal generation and underperformance tweaks, with OpenAI API fallback when Codex quota is exhausted.
- Research lake and curated scorecards now exist in the local data store.
- Positive ROI models and operator escalation candidates are surfaced in the dashboard.
- Challenger generation now follows an explicit `80% mutation / 20% new_model` policy.
- Background factory loop exists via `scripts/factory_loop.py`.
- `ideas.md` / `IDEAS.md` is now parsed into structured tracked ideas with statuses.
- Manual idea intake is now watched on every factory cycle, and the parser accepts both numbered `1.` entries and `10: new idea:` style additions from the live ideas file.
- A low-value online idea scout now adds generated ideas on a 48-hour cadence.
- Active idea intake is now freshness-first: newly added manual ideas rise to the top immediately, while ideas consumed into `new_model` lineages move out of active intake and into archive.
- Unused high-novelty ideas can now seed brand-new incubating families on a controlled cadence, instead of only feeding challengers inside the hardcoded default families.
- New-family incubation is now agentic as well as deterministic: Codex-backed family bootstrap proposals can create fresh families, with deterministic seeding preserved as fallback.
- Scheduled agent reviews for mature paper models are implemented.
- Debug-agent triage for runtime/model bugs with human-escalation support is implemented.
- Debug-agent outputs now create direct maintenance pressure too: non-human debug reviews can escalate into `retrain`, `rework`, `replace`, or `retire` requests instead of remaining passive metadata.
- Funding live inference is closer to training parity now: the contrarian live feature row covers the trained selector schema, the sentiment collector is hardened against malformed ratio payloads, and research-store query paths now use read-only DuckDB access so runners can keep learning while the local store is in use elsewhere.
- Every factory-created `new_model` now needs a distinct generated name and a thesis normalized to `We believe we can create alpha by ...`.
- Scheduled agent reviews now emit explicit maintenance actions (`hold`, `retrain`, `rework`, `replace`, `retire`) and can directly increase rework/replacement pressure on weak lineages.
- Winner surfacing is stricter: immature or shared-evidence-only positives no longer qualify as true winner escalations.
- Positive-model surfacing is stricter too: the operator view now distinguishes independent live-paper positives from fragile/shared-evidence positives, and highlights active replacement pressure instead of showing every positive ROI as a clean green signal.
- Maintenance requests from reviews, stalled-model policy, and debug signals now flow into experiment refresh inputs instead of remaining dashboard-only metadata.
- Review, retrain, rework, replace, retire, and human-action requests now surface as an explicit maintenance queue in factory state and operator signals.
- Operator action inbox items now exist so a human can explicitly approve, reject, or send an instruction back to the factory/agents.
- The dashboard now renders both the operator inbox and the maintenance queue directly, so pending factory/autopilot work is visible on the main control-room surface instead of hidden in raw snapshot JSON.
- Live paper evidence and research/backtest evidence are now separated in factory state so current performance is not confused with shared scorecards or research bundles.
- Trainability contracts are stricter now: persistent learner failures no longer default to endless retrain pressure, and can escalate to replacement pressure once the grace window is breached.
- Codex-native multi-agent execution is now enabled for the highest-value agent tasks: proposal generation, post-evaluation critique, and runtime debug review. Cheap tweak work remains single-agent by design.
- High-value Codex agent artifacts can now persist an explicit `multi_agent_trace` object, so proposal/debug/critique/family-bootstrap runs can record child-role findings instead of only a boolean multi-agent flag when the model returns that structure.
- Maintenance resolution is now becoming a first-class multi-agent lane: the factory can run a dedicated `maintenance_resolution_review` on top actionable maintenance requests, persist that artifact on the lineage, and feed its recommended action back into maintenance pressure.
- Maintenance queue quality is now materially tighter: recent completed maintenance reviews can suppress duplicate queue pressure, inactive/retired lineage items are filtered out, and per-family queue compaction keeps the operator surface focused on the highest-value unresolved actions.
- Resolved operator actions now feed back into experiment inputs and agent review/debug context, so human decisions can change what the factory investigates next.
- Candidate runtime selection is now lane-aware: the manifest/execution adapter can select one family candidate lane per portfolio, and replacement pressure can prefer an isolated challenger instead of flooding a runner with sibling lineages.
- The backend dashboard snapshot now carries the same lane assignment metadata as factory state and the execution bridge, so incumbent vs isolated challenger status is no longer hidden in backend-only state.
- The factory now explicitly flags shared-evidence risk when a selected isolated challenger still has no separate paper target or is sharing the incumbent evidence surface.
- Isolated challenger runtime aliases now exist end to end: the factory can assign a separate runtime target id, the execution adapter can launch the canonical runner under that alias, and the dashboard can surface the alias as a distinct paper-evidence lane.
- Runtime lane policy is now shared and merit-aware: the factory can prefer an isolated challenger not only on explicit replacement pressure, but also when the incumbent is weak and a challenger is materially stronger on ranking/evidence. If the policy wants a challenger lane but no challenger is runnable yet, the maintenance queue now emits `prepare_isolated_lane`.
- Runtime lane policy now also keeps preferring challengers once isolated-lane work is already in progress, so prepared/active alias lanes do not get dropped back to incumbents before they accumulate independent paper evidence.
- `prepare_isolated_lane` is now an active factory workflow, not just a warning: the best challenger is explicitly marked, boosted in queue priority, and reclassified as the family paper-side candidate while it advances toward runnable shadow/paper stages.
- The execution command-center/process-manager path is now runtime-alias aware for discovery and lifecycle control, so isolated challenger books can be listed, viewed, and started under their alias ids instead of only through the factory-owned adapter path.
- Prepared isolated challengers can now activate into shadow automatically once they have valid walkforward and stress evidence with no hard vetoes, and bridge state now distinguishes `pending_stage`, `ready_to_launch`, `started`, `running`, and `start_failed` instead of collapsing alias startup into a single generic state.
- Alias execution evidence is now read from the alias store directly, so isolated challenger lanes can accumulate distinct live-paper proof without being collapsed back onto the canonical incumbent surface.
- Fast first assessment is now a first-class policy target: strong backtest-qualified candidates should reach a first paper review in about 2 days, while the full 30-day paper gate remains the promotion standard.
- Active paper runtime is now moving toward a hard narrow surface: many research lineages are allowed, but execution-facing paper lanes should stay capped globally and per family so evidence remains trustworthy.
- Incubating families now use that fast first assessment as a real lifecycle gate: they graduate only after a positive first live-paper read and get retired early when the first read is decisively weak.
- Isolated challenger alias lanes now use the same fast first-assessment discipline: once alias evidence is live, weak challengers are retired quickly and strong challengers are marked as having passed the first paper read instead of lingering in a generic active state.
- Graduated incubating families now enter challenger rotation in the same cycle instead of waiting for the next loop, so successful new families start compounding immediately.
- Bridge admission now prefers fresh isolated challengers that still need their first paper read over already-qualified isolated lanes when global paper capacity is tight, keeping the narrow paper surface focused on qualification throughput.
- Bridge admission now also demotes stale isolated alias lanes that sit in `ready_to_launch`, `started`, or `running` without publishing fresh distinct evidence, so paper capacity rotates back toward fresh challengers instead of getting trapped in dead lanes.
- Execution-side runtime health contract, backend phase 1, is now live: runners can publish a normalized `runtime_health.json`, the factory reads that contract before legacy files, and bridge activation state now uses runtime/publication/health contract fields instead of mostly inferring alias lifecycle from partial state.
- The dashboard critical feed is now much stricter: raw Codex/MCP stderr is compacted into short operator-safe alerts, positive/review noise is kept out of the critical strip, and repeated fallback failures are grouped instead of dumped line by line.
- Tiered desk activity is now partially real instead of purely coverage-based: tier 1/2/3 desks derive `model_active` state from successful Codex task lanes, and the scientific swarm can now become `model_active` when successful runs carry explicit scientific-domain attribution from hypothesis/family context.
- Deterministic control logic is now separated conceptually from the agent workforce: tier 0 is being treated as algorithmic control, not as a fake model-backed agent desk, and the dashboard should keep that distinction explicit.
- The executive-summary KPI now prefers `Best agent P&L` over blended realized PnL, showing the strongest live paper performer in dollars and percentage ROI from actual runner account state.
- Win rate is now surfaced in the execution monitor so outsized ROI or P&L can be judged against hit-rate quality instead of being treated as a clean green winner signal.
- Weak-family improvement is now becoming first-class factory behavior instead of only lineage-local pressure: family summaries and operator signals can carry explicit `weak_family`, `autopilot_status`, and `autopilot_actions` metadata so families like `cascade`, `contrarian`, and `polymarket` are visibly under autonomous maintenance.
- The stalled-model policy is now stricter in the factory core: once a stalled or untrainable lineage persists beyond the configured stall window and has already exhausted its tweak budget, the factory can retire it automatically instead of leaving it in endless maintenance limbo.
- The execution monitor is now more truthful for paper runners: win rate is visible per model, current runner readiness can downgrade stale critical lineage labels, and portfolio cards now expose training-flow data so “not trading” can be read as evidence scarcity, trainability trouble, or real model weakness instead of opaque failure.
- The paper-validation surface is widening: promising challengers are now allowed to compete for first live paper reads more directly, and the runtime cap should be tuned to actual machine capacity instead of an artificially tiny lane budget.
- Promising backtest-positive challengers can now surface explicitly in a paper-qualification queue, so “good in research, not yet validated on live paper” is treated as an actionable state instead of being lost inside maintenance noise.
- Stalled incumbents no longer keep paper lanes by default: if a live paper incumbent is flagged as `trade_stalled`, `training_stalled`, `stalled_model`, or `no_trade_syndrome`, a qualified challenger can now take the family paper-qualification lane instead of waiting behind an alive-but-cold runner.

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
10. Isolated challenger activation and live-evidence discipline, backend phase 1: prepared challengers can activate to shadow, bridge targets carry explicit activation state, alias runtime books are first-class in execution controls, and backend dashboard snapshots expose lane activation metadata without frontend inference.
11. New family incubation, backend phase 1: high-novelty ideas can create first-class incubating families with their own seeded champion, explicit incubation metadata, and challenger suppression until the family exits early incubation.
12. New family incubation, backend phase 2: Codex-backed family bootstrap proposals can now invent fresh families directly from structured ideas, with multi-agent family-thesis / connector-planning / incubation-risk decomposition and deterministic fallback.
13. Dual-speed assessment policy, backend phase 1: the factory now distinguishes a fast `first assessment` from the full promotion-grade paper gate, so active paper candidates can be reviewed quickly without weakening winner discipline.
14. Active paper lane caps, backend phase 1: execution-facing candidate routing now has explicit global and per-family caps so weaker siblings are suppressed from runtime instead of quietly accumulating noisy partial evidence.
15. Execution-side health contract, backend phase 1: execution runners can now emit normalized runtime health metadata, the factory reads that contract first in execution evidence and light dashboard snapshots, and alias activation mapping is contract-driven instead of mostly inferred from legacy files.
16. Family-level weak-family autopilot, backend phase 1: the factory now computes explicit family-wide maintenance plans (`replace`, `retrain`, `rework`, `isolate_evidence`) and persists them in family state/operator signals instead of leaving weak-family improvement implicit in scattered lineage actions.
17. Hard stalled-model retirement, backend phase 1: persistent stalled/untrainable lineages that burn through their tweak budget now retire automatically, and maintenance requests can escalate them straight to `retire` instead of only `rework` or `retrain`.
18. Operator queue visibility, frontend phase 1: the dashboard now exposes the operator inbox and maintenance queue as first-class panels, with empty states and live maintenance-card details.
19. Best-performer and hit-rate surfacing, frontend phase 1: the main KPI now shows `Best agent P&L`, and execution cards surface win rate alongside ROI, drawdown, and assessment maturity.
20. Maintenance-resolution review, backend phase 1: the factory can now run a dedicated multi-agent Codex maintenance review for top actionable lineage requests, persist the result, and feed it back into `maintenance_request` as a first-class source.
21. Maintenance queue compaction, backend + dashboard phase 1: duplicate lineage pressure, stale retired queue rows, and recently reviewed items are now filtered/compacted so the dashboard backlog reflects live unresolved work instead of raw historical pressure.
22. Paper qualification queue, backend + dashboard phase 1: promising challengers that still need a first live paper read can now be surfaced explicitly, and runtime policy can prefer them for qualification lanes.

## In Progress

1. Embedded execution layer: in-repo paper runners via `factory.local_runner_main`, `EmbeddedExecutionManager`, and `get_process_manager()` factory function that switches between embedded and external execution based on `FACTORY_EMBEDDED_EXECUTION_ENABLED`.
2. Validation profiles: `FACTORY_VALIDATION_PROFILE` (dev/paper/prod) with profile-based thresholds in execution evidence for heartbeat staleness, rejection rates, and no-trade scan minimums.
3. Standalone mode: `EXECUTION_REPO_ROOT` and `EXECUTION_PORTFOLIO_STATE_ROOT` are now optional. When empty, embedded mode uses `PORTFOLIO_STATE_ROOT` for state, `execution_refresh` skips gracefully, and `bootstrap_env.py` supports embedded-only `.env` generation.
4. Clear separation between execution failure, validation-blocked, and research-only states in the dashboard.
5. Continuous factory background operation.
6. Idea-to-lineage status quality: move more ideas from `adapted` into `tested`.
7. ~~**Agent cost optimization and OpenAI API fallback**~~ — **COMPLETED 2026-03-13** (see Completed section below).
4. Surface assessment maturity more clearly in the dashboard so high ROI on tiny trade counts is visibly blocked as insufficient evidence.
5. Shared-evidence dedupe and lineage-isolated paper assessment need tightening so one portfolio scorecard is not mistaken for multiple independent winners.
6. Every required execution-side model needs an explicit trainability contract so untrainable books are treated as bugs, not quietly left in paper purgatory.
7. Add a hard stalled-model workflow: if a running model does not make trading or required training progress for more than 8 hours, force review/debug/rework pressure and retire it if the stall persists through tweak budget.
8. Expand the new Codex child-agent runtime beyond the first task trio so multi-agent execution also informs idea assignment, maintenance queue resolution, and stronger family-level replacement decisions.
8. Expand the new Codex child-agent runtime beyond the first task trio so multi-agent execution also informs idea assignment, maintenance queue resolution, and stronger family-level replacement decisions. Backend phase 1 is now in place for top lineage maintenance requests; family-level queue resolution and broader queue coverage still need follow-through.
9. Make the scientific swarm real: successful agent runs need explicit scientific-domain attribution so swarm domains can move from `coverage_only` to genuine `model_active`.
10. Move from alias-capable isolation to consistently running stronger challengers on their own paper books and accumulating trustworthy live paper evidence, especially for families currently stuck in `prepare_isolated_lane`, `ready_to_launch`, `started`, or nominally `running` without ever publishing fresh live alias evidence.
11. Keep widening the paper-validation surface up to safe machine capacity so good research candidates actually reach the real feed instead of stagnating as backtest-only positives.
12. Use the new fast first-assessment loop to graduate only the strongest incubating families into normal family challenger rotation, and retire weak incubators early.
13. Keep active paper runtime narrow by policy: prefer one incumbent plus one isolated challenger per family, and keep the total active paper lane count capped on this machine.
14. Eliminate the remaining clustered `multi_agent_trace` structured-output fallbacks so high-value Codex multi-agent paths stop polluting the operator feed and can be trusted as first-class runtime lanes.
15. Add first-class operator controls in the dashboard so inbox items can eventually be acted on directly instead of only being surfaced as read-only queue rows.
16. Improve operator queue quality further by prioritizing the most urgent maintenance and human-action items, and suppressing low-value queue noise once the backlog grows. Backend/dashboard phase 1 is done; the remaining work is stronger family-level prioritization and faster queue burn-down.
17. Push the new family-level weak-family autopilot deeper into actual execution policy so the strongest family-wide actions directly increase challenger pressure, retrain cadence, and isolated-evidence priority without manual nudging.
18. Deepen stalled-model and trainability visibility so the dashboard distinguishes warming-up models, blocked trainability, and hard-retirement candidates without needing log inspection.

## Next

1. Cross-machine portability and cloud sync
- Add regular export/sync of factory state, curated research datasets, and model artifacts so the factory can resume on another computer.
- Keep code, manifests, and light metadata in GitHub.
- Keep larger databases, DuckDB snapshots, Parquet lake partitions, and heavier model artifacts in syncable artifact storage rather than normal git blobs.
- Define a reproducible restore path: clone repo, pull synced artifacts, restore `.env`, resume factory loop.

2. Review-driven model maintenance
- Extend the new maintenance-action contract further into dashboard queues and explicit operator-visible maintenance worklists.
- Keep pushing debug-agent outputs deeper into retrain, retire, and human-escalation routing so runtime bugs and operator-owned blockers do not sit idle.
- Add first-class operator controls in the dashboard so a human can act on inbox items with `yes`, `no`, or a written instruction for an agent, not just read alerts.
- Add a dedicated winner-robustness agent before any real-trading escalation:
  - verify model math and payoff accounting are internally consistent
  - verify venue fees, commissions, borrow/funding, and slippage assumptions are modeled realistically
  - verify backtest/paper P&L is not inflated by stale fills, impossible fills, or missing transaction costs
  - verify extreme ROI / low-hit-rate strategies are stress-tested for fragility before they are treated as true winners
  - emit a clear robustness verdict that can block promotion even when raw paper ROI looks attractive

3. Codex-native multi-agent execution
- Keep the distinction explicit: the factory is already multi-agent conceptually through separate inventor, reviewer, debug, and maintenance roles, but it is not yet fully using Codex child-agent execution patterns internally.
- Upgrade the highest-value loops to Codex-native multi-agent workflows:
  - strategy invention: proposer + critic + execution/microstructure reviewer
  - scheduled model review: reviewer + retrain planner + risk assessor
  - bug/debug triage: runtime debugger + data-pipeline debugger + operator-escalation classifier
- Prefer cheap/local agents for search, validation, and deterministic subtasks, and reserve stronger Codex agents for synthesis, critique, and cross-file decisions.
- Persist sub-agent outputs as first-class artifacts so the dashboard can show not just that a model was reviewed, but which child agents participated and what each concluded.

4. Structured idea pipeline, phase 2
- Promote idea usage from prompt context to explicit idea-backed lineage creation decisions.
- Add idea assignment history and per-idea experiment outcomes.
- Improve relevance filtering so manual and scouted ideas produce higher-quality family-specific challengers.
- Keep new family creation as a first-class parallel lane: some high-novelty ideas should create entirely new incubating families instead of being forced into the nearest existing family.

5. Winner surfacing and promotion discipline
- Tighten family league replacement rules.
- Make “why winner” and “why blocked” explicit in the dashboard.
- Keep operator signoff mandatory before any real-trading push.
- Separate shared portfolio evidence from independent lineage evidence, and require lineage-isolated paper books before treating repeated ROI signals as distinct model winners.
- Prefer one primary incumbent plus one isolated challenger lane per family instead of letting multiple siblings masquerade as independent paper winners.
- Keep `positive_models` and real-trading escalation tied to current live paper evidence; show research positives separately as research evidence only.
- Block `potential_winner` and real-trading escalation status for isolated challengers that are still sharing the incumbent paper target or publishing stale alias evidence.
- Extend the new lane-preparation workflow so selected challengers graduate from `prepare_isolated_lane` into actively running isolated paper books with minimal manual intervention.
- Tighten the alias lifecycle further so execution can natively distinguish `spawned but not yet publishing`, `publishing live alias evidence`, and `failed before first publish`, instead of leaving that distinction to factory-side inference alone.
- Keep isolated challenger paper lanes on a fast qualification cadence: get to first assessment quickly, retire weak lanes quickly, and reserve the narrow paper surface for strong challengers with distinct evidence.
- Make bridge/runtime admission increasingly lifecycle-aware so already-qualified or stale non-publishing aliases stop crowding out fresher challenger qualification opportunities.

6. Execution-side health contract
- Extend the new `runtime_health.json` contract beyond the current backend phase 1 so all important runners publish it consistently.
- Reduce dashboard inference from partial state files even further by making the full execution monitor consume the same normalized contract.
- Keep tightening alias lifecycle from the execution side so `spawned`, `publishing`, `first_publish_failed`, and similar states originate in runner metadata rather than factory inference.
- Keep hedge/funding evidence clean by filtering hedge watchlists to spot-supported symbols and normalizing exchange-driven order precision before paper execution.

7. Strategy quality
- Improve weak incumbent families: `cascade_alpha`, `contrarian_legacy`, `polymarket_quantum_fold`.
- Expand real-agent invention beyond the current enabled families once the maintenance loop is stable.
- Let only the strongest incubating families graduate into the normal challenger loop, instead of letting family creation become a noisy side channel.
- Keep pruning incubators aggressively after failed first assessment so family creation compounds quality instead of noise.

8. Trainability discipline
- Make every non-validation-only model expose `training_progress` and `trainability` in execution state.
- Fail fast when a required learner cannot train because of missing data, disabled model lanes, or broken feature builds.
- Surface `untrainable_model` directly to the factory so debug, retrain, and replacement agents treat it as an actionable defect.

9. Stalled-model maintenance policy
- Detect running models that have gone more than 8 hours without trading progress or required training progress.
- Surface `trade_stalled`, `training_stalled`, and `stalled_model` in execution evidence and the dashboard.
- Use that signal to trigger debug-agent review, maintenance review, challenger pressure, and eventual retirement if the model keeps stalling after rework.

## Completed: NEBULA Independence and Data Expansion (2026-03-13)

1. **Arbitrage repo fully severed**: All `EXECUTION_REPO_ROOT` and `EXECUTION_PORTFOLIO_STATE_ROOT` references cleared. Portfolio data (492MB), funding history (76MB), and prediction/state data ported locally. `factory/connectors.py` now uses only local `data/` paths. `factory/execution_refresh.py` and `factory/runtime_execution.py` guard gracefully when no external execution repo is configured.
2. **Yahoo Finance bulk data**: 5-year daily OHLCV for all S&P 500 constituents + major ETFs + VIX + Treasury yields downloaded to `data/yahoo/ohlcv/` as Parquet files (~530 tickers). Script: `scripts/download_stock_data.py`. Incremental refresh: `scripts/refresh_yahoo_data.py`.
3. **Yahoo Finance connector**: `yahoo_stocks` connector in `factory/connectors.py` tracks `data/yahoo/ohlcv/`, `sp500_components.json`, and `metadata.json`. Appears as a first-class API feed in the NEBULA Control Room and is treated as healthy whenever local data exists.
4. **Alpaca connector**: `alpaca_stocks` connector in `factory/connectors.py` tracks `data/alpaca/bars/`, `data/alpaca/quotes/`, and `metadata.json`. Refresh script: `scripts/refresh_alpaca_data.py`. Requires `ALPACA_API_KEY` and `ALPACA_API_SECRET` in `.env`, and appears alongside Binance/Betfair/Polymarket/Yahoo in the API feeds strip.
5. **Alpaca MCP server**: Official [alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) can be installed via `uvx alpaca-mcp-server init` for direct natural-language trading from Cursor/agents.
6. **HMM regime-adaptive strategy family**: New instrument-agnostic strategy using Hidden Markov Models for market regime detection. Idea in `ideas.md`, family bootstrap in `data/factory/families/hmm_regime_adaptive/`, model scaffold in `research/goldfish/hmm_regime_adaptive/model.py`.
7. **Dashboard bug fixes**: Fixed 5 component crashes (PnlChart, LineageAtlas, LineageBoard, JournalPanel, FamiliesPanel) with null safety and field name alignment.
8. **Codex fallback logging**: Added startup diagnostics showing OPENAI_API_KEY status, provider order, and standalone mode. Agent runtime now logs each provider attempt and failure reason.

## Completed: Agent Cost Control, Backtest Automation, HMM Wiring (2026-03-13)

1. **Codex fallback fully operational**: Fixed missing `import logging` / `logger` in `factory/agent_runtime.py` that was crashing the factory loop. After restart, all agent runs successfully fall back from Codex CLI to OpenAI API. Post-fix stats: 21/21 runs successful, 100% using `openai_api` provider.
2. **Cost guard implemented**: New `_apply_cost_guard()` method in `agent_runtime.py` auto-downgrades `TASK_FRONTIER`/`TASK_DEEP` to `TASK_HARD` when expensive-model usage exceeds a rolling cap. Config: `FACTORY_AGENT_EXPENSIVE_CAP_PCT=10` (default 10%), `FACTORY_AGENT_COST_WINDOW=50` (last 50 runs). Family bootstrap (`generate_family_proposal`) is exempt since it needs frontier quality. Post-fix stats: 90% cheap models (`gpt-4.1`), 10% expensive.
3. **Post-eval critique downgraded**: `critique_post_evaluation` changed from `TASK_DEEP` (gpt-5.4/o3) to `TASK_HARD` (gpt-5.2-codex/gpt-4.1). This was the single largest cost reduction since critiques run frequently.
4. **Proposal model default lowered**: `_proposal_model()` default changed from `gpt-5.4` to `gpt-5.2-codex`. Proposals still get quality reasoning via `TASK_HARD` tier but no longer burn frontier/deep budget.
5. **Proposal escalation tightened**: `_proposal_task_class()` no longer escalates to `TASK_FRONTIER` for critical-health or high-retirement scenarios — it caps at `TASK_HARD`. Only `generate_family_proposal` (brand-new family creation) uses `TASK_FRONTIER`.
6. **Batch backtest script**: New `scripts/batch_backtest.py` for automated 3yr+1yr parameter-grid backtesting on Yahoo historical data. Supports `--param-grid` (n_states x lookback_days), `--tickers`, `--train-years`, `--test-years`. Results stored as JSON in `data/backtest_results/`.
7. **HMM wired into experiment runner**: `factory/experiment_runner.py` now dispatches `hmm_regime_adaptive` family experiments using Yahoo OHLCV data. Full pipeline: load Parquet, instantiate `HMMRegimeModel` with genome parameters, run walk-forward backtest, emit `EvaluationBundle` artifacts. Also bootstrapped in `factory/orchestrator.py`.
8. **Model tiering enforced end-to-end**: The full tiering table is now actively enforced:

   | Task Class | Codex Model | OpenAI API Fallback | When Used |
   |---|---|---|---|
   | `cheap_structured` | gpt-5.1-codex-mini | gpt-4.1-nano | Simple tweaks, classification |
   | `standard_research` | gpt-5.1-codex | gpt-4.1-mini | Standard proposals, research |
   | `hard_research` | gpt-5.2-codex | gpt-4.1 | Complex proposals, critiques, debug |
   | `frontier_research` | gpt-5.3-codex | o4-mini | Family bootstrap only |
   | `deep_review` | gpt-5.4 | o3 | Reserved, cost-guard capped at 10% |

### Remaining work (agent costs)

- Add token usage tracking per agent run so cost per cycle is visible in the dashboard.
- Consider OpenAI Batch API (50% off) for non-time-sensitive agent tasks like scheduled reviews.
- Add rate-limit / quota detection for the OpenAI API fallback so it can surface "both Codex and API quota exhausted" cleanly.
- Periodically re-evaluate model pricing as OpenAI releases new models.

## Later

1. External market-data expansion
- First-wave candidate APIs:
  - `FRED` for macro regime and release-aware economic features.
  - `SEC EDGAR Data` for filings, company facts, and earnings-driven event research.
  - `OpenFIGI` for cross-vendor symbol and contract normalization.
  - `Tradier` for US equity/options chain, expiries, and options-surface research.
  - `MarketAux` for ticker-linked market news and sentiment inputs.
  - `Financial Modeling Prep` for fundamentals, statements, and transcript-style equity research features.
- Consider deeper coverage:
  - `Polygon` if deeper equities/options coverage becomes worth the higher cost.
- Broader crypto and macro expansion can follow through `CoinGecko`, `Messari`, `Econdb`, `Fed Treasury`, or `Nasdaq Data Link` when the factory is ready for more research breadth.
- Any addition should land through explicit connectors, persisted artifacts, and family-level research hypotheses, not ad hoc API calls.
