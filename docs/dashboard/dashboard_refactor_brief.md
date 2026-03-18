# AgenticTrading Dashboard Audit and Refactor Plan

## Summary

The current “dashboard” is actually a two-layer system: a Python HTTP server that serves **(a)** a “snapshot” API and **(b)** static frontend assets, with **two** frontend implementations (a legacy static HTML dashboard and a newer React/Vite dashboard). fileciteturn11file0L1-L1 fileciteturn38file0L1-L1 fileciteturn12file0L1-L1

The factory side is already quite close to your “new architecture” goals (mobkit runtime backend flags, Goldfish provenance, lineage-scoped paper state, lane policy, deterministic promotion blockers, incubating family lifecycle). The biggest issue is that the dashboard **does not surface these as a coherent Factory Control Tower**, and the React UI has several **schema mismatches** versus the backend payloads—so it can’t be trusted as an operator panel yet. fileciteturn52file0L1-L1 fileciteturn53file0L1-L1 fileciteturn16file0L1-L1

The refactor should therefore prioritise:
- Establishing a **single canonical dashboard contract** (schema + TS types) and eliminating backend/frontend drift.
- Rebuilding the UI IA into the **8 required zones**, with drill-downs that align to the factory’s real objects: **Family → Lineage → Lane → Paper account → Gates → Provenance**.
- Adding observability coverage for **mobkit**, **token/cost budgets**, and **paper holdoff/venue-scope** decisions, which currently exist in config and/or runtime behaviour but not in the dashboard model. fileciteturn53file0L1-L1 fileciteturn52file0L1-L1

```markdown
# ./artifacts/dashboard_current_audit.md

## Scope
Branch: integration/staging
Goal: inventory current dashboard implementation (server + UI + data sources), and identify strengths/gaps vs new factory architecture.

## What exists today

### Backend server (dashboard host)
- **File**: scripts/factory_dashboard.py
- **Role**: lightweight HTTP server that:
  1) serves static dashboard assets (prefers `dashboard-ui/dist`, falls back to `dashboard/`)
  2) serves JSON APIs:
     - GET /api/healthz
     - GET /api/snapshot
     - GET /api/portfolio/<portfolio_id>/chart
     - POST /api/factory/control (pause/resume via flag file)
- **Snapshot builder**: factory/operator_dashboard.build_dashboard_snapshot(...)
- **Control plane**: pause/resume implemented by writing/removing `data/factory/factory_paused.flag`

Key observations:
- Snapshot is cached briefly (to avoid rebuilding on every poll).
- Chart endpoint reads local portfolio state (`account.json`, `trades.jsonl`, `events.jsonl`) under `data/portfolios/<portfolio_id>/`.

### Snapshot builder (data aggregation)
- **File**: factory/operator_dashboard.py
- **Role**: reads factory state + supporting artefacts and returns a “dashboard snapshot” object used by the UI.

Primary inputs:
- Factory state store (written by FactoryOrchestrator via FactoryRegistry): `data/factory/state/summary.json`
- Factory journal/notes: state markdown / ideas intake / etc.
- Connector catalog snapshots (venue readiness)
- Recent agent runs (from `data/factory/agent_runs/*.json`)
- Portfolio scorecards and per-portfolio execution evidence (reads under `data/portfolios/`)

### Frontend implementations (two)

#### New UI (preferred when built)
- **Dir**: dashboard-ui/
- **Framework**: React (v19) + Vite, TS
- **Charts**: chart.js + react-chartjs-2
- **Entry**: dashboard-ui/src/main.tsx -> App.tsx
- **Navigation**: simple tab switch (no router)
  - Tab: "Factory Overview"
  - Tab: "Lineage Atlas"
- **Refresh**: polling GET /api/snapshot every 5s.

Panels/components visible in App.tsx:
- TopCommandBar (pause/resume; audio toggle; mode badge)
- APIFeedsStrip (connector status)
- KPIDeck (readiness + counts)
- AgentActivityPanel (agent run feed)
- PortfolioGrid (portfolio cards + PnL chart)
- AlertsPanel
- EscalationsPanel
- MaintenancePanel
- FamiliesPanel
- LeaguePanel
- LineageBoard
- IdeasPanel
- QueuePanel
- DesksPanel
- JournalPanel
- LineageAtlas

Quality note:
- Several TS types/components are currently out-of-sync with backend payload fields (see “Current gaps”).

#### Legacy UI (fallback when React build missing)
- **Dir**: dashboard/
- **Framework**: static HTML + vanilla JS
- **Entry**: dashboard/index.html + dashboard/app.js
- **Refresh**: polling /api/snapshot
- **Status**: appears more “complete”/aligned to backend payload than the React port.

## Current strengths
- Simple deployment: one Python script serves UI + APIs; no DB required.
- Snapshot aligns to the “factory as filesystem” architecture: reads the same artefacts operators use.
- Operator controls exist (pause/resume).
- Core factory objects already present in snapshot: families, lineages, promotion state, maintenance/escalation signals, connectors readiness.

## Current gaps (dashboard-specific)
- Two dashboards (legacy + React) = split maintenance and schema drift risk.
- React UI has multiple contract mismatches vs Python endpoints/types (markers not displayed, atlas history schema mismatch, etc.).
- The “portfolio” concept is not yet correctly lineage-scoped in the UI (heuristics used to infer lineage/venue).
- No explicit mobkit health panel; no token/cost/compute panel; no paper holdoff visibility.
- Gate blockers exist in factory decisions, but are not surfaced as a dedicated deterministic “why blocked” operator view.
```

## Dashboard framework and entrypoints found

The dashboard is implemented as:

A Python “micro server”:
- `scripts/factory_dashboard.py` hosts static assets and exposes the API endpoints the UI calls. fileciteturn11file0L1-L1  
- It delegates the snapshot payload to `factory/operator_dashboard.py` (`build_dashboard_snapshot`). fileciteturn11file0L1-L1 fileciteturn18file0L1-L1  
- It also provides a portfolio chart endpoint which reads from the local portfolio store under `data/portfolios`. fileciteturn11file0L1-L1

Two UIs:
- **React/Vite UI** in `dashboard-ui/` (React 19, Vite, Chart.js). fileciteturn12file0L1-L1 fileciteturn13file0L1-L1  
  - Entrypoint: `dashboard-ui/src/main.tsx` → `App.tsx`. fileciteturn15file0L1-L1 fileciteturn16file0L1-L1  
  - Polling model: `useSnapshot` polls `/api/snapshot` every **5s**. fileciteturn17file0L1-L1  
- **Legacy static UI** in `dashboard/` (HTML + vanilla JS). fileciteturn38file0L1-L1 fileciteturn39file0L1-L1  
  - The Python server explicitly supports falling back to this when the React build output isn’t present. fileciteturn11file0L1-L1

Factory behaviour and state model you need to map to:
- The orchestrator is the system of record for “factory state” (families, lineages, lane policy, promotion decisions, operator signals, Goldfish workspaces, etc.). fileciteturn52file0L1-L1  
- The config explicitly declares the “new factory architecture” levers (mobkit backend, Goldfish provenance flags, strict budget governance). fileciteturn53file0L1-L1

```markdown
# ./artifacts/dashboard_architecture_gap_analysis.md

## Goal
Compare current dashboard representation (snapshot + UI) to the new factory architecture requirements.

## Legend
- ✅ Present (usable)
- 🟡 Partial (present but not operator-grade / missing drilldown / missing correctness)
- ❌ Missing (not represented)

## Required coverage vs current dashboard

### Runtime health
- Factory running/status/last cycle: 🟡
  - Present in orchestrator state and surfaced in snapshot, but dashboard lacks process-level heartbeat clarity (stale detection, cycle latency trend, scheduler health).
- Runtime mode (full/cost_saver/hard_stop): 🟡
  - Exists in factory runtime_mode + readiness metadata; not surfaced as a first-class operator control/diagnostic panel.

### mobkit health
- mobkit backend enabled, gateway configured, RPC status, queue, failures: ❌
  - Config exposes mobkit backend flags and gateway binary path, but snapshot/UI does not surface mobkit lifecycle or health.

### Goldfish health and DNA usage
- Goldfish workspace readiness: 🟡
  - Workspaces readiness exists, but no provenance write error rates, ingestion lag, last-thought timestamps, or DNA packet usage.
- DNA memory usage (what memories influenced last proposal/gate): ❌

### Pipeline stages (idea → proposal → design → backtest → walkforward → shadow → paper → retired)
- Current system stages are PromotionStage-based and do not match the new canonical stage names one-to-one: 🟡
  - Dashboard shows current_stage but not a canonical “pipeline” view with stage throughput + blockers.
- Deterministic stage transitions (why moved / why stuck): 🟡
  - Exists in last_decision + blockers but needs a dedicated “Gate” UI.

### Family revival / dead / exhausted lifecycle
- Family incubation lifecycle (incubating/graduated/retired): 🟡
  - Present in factory state; dashboard shows some, but not a lifecycle operator view.
- Exhaustion signals (max_tweaks exhausted, repeated stalls): 🟡
  - Exists as iteration_status/retirement_reason codes; dashboard does not translate to lifecycle states (“exhausted”, “revival candidate”).
- Revival actions (explicit “revive family / reseed champion / reset budget”): ❌

### Per-lineage paper balance / P&L / drawdown / trade count
- Core metrics exist per lineage (live_paper_*): 🟡
  - UI still organises around “portfolios”, and heuristically infers lineage/venue from families.
- True lineage-scoped paper accounting: 🟡
  - Implemented in orchestrator via per-lineage portfolio IDs, but not surfaced as a first-class dashboard concept.

### Holdoff state for paper-active lineages
- Holdoff policy exists (config + orchestrator logic) but is not represented in snapshot/UI: ❌

### Deterministic gate blockers
- Blockers exist in state (blockers + hard_vetoes + promotion scorecard): 🟡
  - Dashboard lacks a dedicated “Gate blocker” drilldown explaining: source, threshold, evidence, recommended action, and “who owns it”.

### Venue readiness / blocked reasons
- Connector readiness exists: 🟡
  - Shown as “feeds”, but lacks a venue readiness matrix: venue → required connectors → missing credentials → data freshness → last error → blocked reason.
- Execution readiness blockers exist in execution evidence: 🟡
  - Needs surface as “venue blockers” and “portfolio blockers”.

### Compute, tokens, cost, sessions, queue pressure
- Some budget thresholds exist in config; agent runs capture provider/model/duration: 🟡
  - No token counts, cost, concurrency, session pressure, or per-family/per-lineage budget burn is visible.
- mobkit session/queue models not shown: ❌

### Alerts / anomalies
- Alerts/escalations/maintenance exist in UI: 🟡
  - Not unified; lacks severity, dedupe, anomaly detection, and operator runbooks.

## Net result
Current dashboard is a “snapshot viewer” with partial operator controls, not a real-time Factory Control Tower.
The refactor must consolidate the UIs, define a stable telemetry contract, and add missing observability panels for mobkit + budgets + paper holdoff + venue blockers + DNA provenance usage.
```

## Biggest current gaps

The most material gaps relative to the “Factory Control Tower” mission are:

The dashboard contract is not stable:
- The React port contains multiple backend/TS schema mismatches (for example, the portfolio chart endpoint emits trade kinds like `trade_opened`/`trade_closed`, while the React chart component filters for `open`/`close`, so markers will never render). fileciteturn11file0L1-L1 fileciteturn21file0L1-L1  
- The Lineage Atlas also appears to expect an event-history schema that doesn’t match what the backend constructs, making the “ledger” view unreliable for operators. fileciteturn16file0L1-L1 fileciteturn18file0L1-L1

The UI information architecture does not match the new factory architecture:
- It is organised as “overview panels” rather than the required operator zones (Factory health, Pipeline, Paper-active models, Lineage explorer, Goldfish DNA, Compute/cost, Venue readiness, Alerts). fileciteturn16file0L1-L1  
- Pipeline stages are currently represented via `PromotionStage` (IDEA/SPEC/DATA_CHECK/GOLDFISH_RUN/…/PAPER/…) which does not align to the required canonical stage list (idea → proposal → design → backtest → walkforward → shadow → paper → retired), so operators cannot reason about pipeline flow without translation. fileciteturn19file0L1-L1

Missing observability for the “new architecture” pillars:
- **mobkit**: Config defines mobkit runtime backend flags and gateway config, but the dashboard has no mobkit health, RPC status, queue pressure, or failure rates. fileciteturn53file0L1-L1  
- **Compute/tokens/cost**: Config defines strict budgets and governance thresholds, but agent-run artifacts do not contain token usage/cost, and no dashboard panels exist for budget burn and pressure. fileciteturn53file0L1-L1 fileciteturn49file0L1-L1  
- **Paper holdoff**: The orchestrator supports a paper holdoff mode (skipping agentic compute for healthy paper-stage lineages), but this decision is not surfaced in the dashboard model, so operators can misinterpret “quiet” behaviour as “stuck”. fileciteturn52file0L1-L1

Paper operations are not operator-grade multi-venue + lineage-scoped in the UI:
- The factory is moving toward **lineage-scoped paper accounting** (per-lineage portfolio IDs and isolated paper state), but the dashboard still leans on “portfolios” and heuristics to infer lineage/venue, which breaks in multi-venue / multi-lineage scenarios. fileciteturn52file0L1-L1 fileciteturn22file0L1-L1  
- Venue readiness is shown as a simple strip, but not as a readiness matrix linking: **venue → connectors → credentials/data freshness → active lineages blocked**. fileciteturn20file0L1-L1

Goldfish DNA is not made operational:
- The orchestrator is already building DNA packets from learning memory and integrating with Goldfish provenance services, but the dashboard does not provide “DNA intelligence” panels to show memory usage, provenance health, or traceability from proposal → gate → paper outcomes. fileciteturn52file0L1-L1 fileciteturn53file0L1-L1

## Target dashboard structure

This target IA is explicitly designed to make the dashboard a **real-time Factory Control Tower**, aligned with how the factory actually runs and writes state today. fileciteturn52file0L1-L1 fileciteturn11file0L1-L1

```markdown
# ./artifacts/dashboard_target_design.md

## Design principles
- One canonical operator dashboard (no legacy/React split).
- Stable schema: backend snapshot contract is versioned; TS types are generated.
- “Control tower” > “pretty charts”: every panel answers “what is happening, what is blocked, what do I do next?”.
- Real-time where it matters (paper + blockers + incidents), slower where it doesn’t (ideas, archives).

## Required top-level zones (pages)

### Factory health
Purpose:
- Single-pane live operational status for the factory process and its critical subsystems.

Key metrics:
- Factory status (running/paused), runtime_mode (full/cost_saver/hard_stop), cycle_count, last_cycle_at age
- Readiness status + blockers (human_signoff_required, connector readiness, goldfish readiness)
- Data refresh scheduler running/last tick (if present)
- Execution bridge health (sync ok, last sync age)
- mobkit: gateway configured, RPC ok, active sessions, queue length, error rate

Data sources:
- Orchestrator state summary (data/factory/state/summary.json)
- /api/healthz (dashboard server)
- mobkit health endpoint / status file (new plumbing)

Visualization:
- Status tiles + trend sparkline for cycle latency and error rate
- “Last 20 actions” event stream (journal recent_actions)

Refresh:
- SSE or 2s polling for the header tiles; 5–15s for deep panels.

### Pipeline / lifecycle
Purpose:
- Show lineages flowing through the canonical pipeline: idea → proposal → design → backtest → walkforward → shadow → paper → retired.

Key metrics:
- Count by stage, time-in-stage, throughput per day
- Blockers by stage (top 5), “stuck > TTL” candidates
- Promotion gates pass/fail counts

Data sources:
- Orchestrator lineages (current_stage, blockers, last_decision.scorecard)
- PromotionStage mapping layer to canonical pipeline stage names

Visualization:
- Stage funnel / Sankey-like counts
- Table of “stuck” lineages with gate blockers and recommended next action

Refresh:
- 5s for counts and stuck list.

### Paper-active models
Purpose:
- Real-time operational console for paper-running lineages across venues.

Key metrics (per lineage):
- lineage_id, family_id, lane_kind (primary/incumbent vs isolated challenger), live_paper_target_portfolio_id
- balance, realised P&L, ROI%, drawdown%, trade count, win/loss
- execution health + issue codes + blocked reasons
- holdoff state (paper_holdoff, venue_scope, market_closed skip)
- deterministic promotion gate status (paper gate progress + strict gate blockers)

Data sources:
- Orchestrator lineages: live_paper_* and execution_validation targets
- Per-lineage PortfolioStateStore (lineage-scoped paper accounting)
- Config-derived policy flags surfaced via snapshot (holdoff, venue_scopes)

Visualization:
- Dense table with mini-equity sparklines + colour-coded health
- Drilldown drawer: timeline of last 50 events + gate blocker detail

Refresh:
- 2s–5s for the table; charts on-demand.

### Family & lineage explorer
Purpose:
- Navigate from family → lineage → artefacts, with lifecycle state and actions.

Key metrics:
- family: incubation_status, weak_family/autopilot actions, lane policy, active vs retired counts
- lineage: role, creation_kind, parent_lineage_id, tweak_count/max_tweaks, retirement_reason
- operator actions inbox status (open/resolved)

Data sources:
- Orchestrator families + lineages
- Registry artefacts (manifest, operator actions, learning memory)

Visualization:
- Master/detail list with filters (venue, stage, health, incubation_status)
- Lineage timeline view

Refresh:
- 10s by default; 5s when viewing a specific lineage.

### Goldfish DNA intelligence
Purpose:
- Make Goldfish provenance and DNA memory operational and inspectable.

Key metrics:
- Goldfish workspace readiness per family, last write time, last error
- DNA packet summary per family: dominant lessons, repeated failures, recommended mutation directions
- “Memory utilisation”: which learning memories influenced latest proposals and gate decisions

Data sources:
- Goldfish provenance store (ProvenanceService/GOLDFISH paths)
- Registry learning_memory
- New “dna_usage” telemetry emitted when proposals are generated

Visualization:
- Family DNA cards + memory table
- “Lessons heatmap”: issue codes / veto patterns / outcomes over time

Refresh:
- 15–60s (unless an incident).

### Compute / token / cost monitoring
Purpose:
- Budget governance console for tokens, cost, concurrency, and queue pressure.

Key metrics:
- Global daily budget vs burn (USD, tokens)
- Per-family burn; top models/providers by spend and tokens
- Active workflows/sessions; queue length; max concurrency headroom
- “Cost_saver triggers” (why downgrades happened)

Data sources:
- Config budgets (FACTORY_GLOBAL_DAILY_* etc)
- Agent run logs (needs extension to include tokens/cost)
- mobkit runtime telemetry (sessions/queue)
- Optional: aggregated metrics file written each cycle

Visualization:
- Budget bars (burn vs cap), time series for burn rate
- Table: top consumers + anomalies

Refresh:
- 10–30s, with on-demand drilldown.

### Venue readiness / blockers
Purpose:
- Multi-venue operational status: can we trade/paper test across venues and why not?

Key metrics:
- Per connector: ready, last refresh, data age, last error
- Per venue: derived readiness = all required connectors ready + credentials ok
- Lineages blocked by venue problems (mapped from execution blockers + connector readiness)

Data sources:
- Connector snapshots (default_connector_catalog snapshot)
- Execution evidence blockers (execution_validation targets)
- New mapping layer: venue -> required connectors and expected freshness thresholds

Visualization:
- Venue readiness matrix
- “Blocked reasons” rollup with counts + impacted lineages

Refresh:
- 5–15s.

### Alerts / anomalies
Purpose:
- Everything an operator must react to now.

Key metrics:
- human_action_required items + ownership + SLA clock
- maintenance_queue + suppression/dedupe status
- anomalies: sudden ROI drop, stalled trades, heartbeat stale, training stalled, queue explosion, budget near cap

Data sources:
- orchestrator operator_signals (maintenance/escalations/human_action_required)
- execution evidence issue codes / health status
- budgets + mobkit telemetry

Visualization:
- Unified incident list with severity + suggested action
- Optional: pager-like sound/desktop notifications

Refresh:
- SSE/2s polling; always “hot”.
```

## Top 10 refactor tasks

These are written as practical PR-sized chunks, sequencing correctness → contract stability → operator IA → new telemetry.

```markdown
# ./artifacts/dashboard_refactor_plan.md

## What to keep
- Keep the *Python dashboard server* (scripts/factory_dashboard.py) as the deployable entrypoint.
- Keep the *factory state summary* as the canonical source of truth (written by orchestrator/registry).
- Keep the snapshot pattern (one aggregation point), but version it and make the schema explicit.
- Keep the React/Vite app shell (dashboard-ui) as the single UI target once corrected.

## What to remove (after parity)
- Remove / deprecate the legacy static dashboard (dashboard/) once React reaches feature parity.
- Remove heuristic lineage/venue inference in the UI (PortfolioGrid guessing) and replace with explicit lineage-scoped identifiers.

## What to redesign (core)
- Snapshot contract: define “dashboard_snapshot_v2” as a versioned schema with:
  - stable IDs (family_id, lineage_id, venue, connector_id)
  - explicit policy decisions (holdoff, venue_scope, market_closed_skip)
  - deterministic gate blocker objects (structured, not freeform strings)
  - compute/token/cost aggregation
  - mobkit telemetry
  - Goldfish provenance / DNA usage summaries
- UI IA: replace tabbed overview with 8-zone navigation and deep drilldowns.

## Backend / data plumbing needed
- Add a structured telemetry layer in factory, e.g. factory/telemetry/dashboard_contract.py:
  - Pydantic models for snapshot v2
  - A mapper from orchestrator state into v2
  - A schema exporter to generate TS types (or JSON schema)
- Extend agent run artefacts to include:
  - token counts, cost_usd, provider latency, request ids (where available)
- Add mobkit telemetry adaptor:
  - gateway status, rpc latency, queue depth, sessions; surface via snapshot
- Add Goldfish telemetry adaptor:
  - last write, last error, provenance lag; plus “dna_usage” records

## Implementation sequence (small PR chunks)

PR1 — Fix correctness regressions in React UI
- Align trade marker kinds in PnlChart with backend chart payload.
- Align LineageAtlas history schema with actual backend payload (or change backend to match TS types).
- Add a “schema version” badge to debug drift.

PR2 — Unify to a single frontend (feature-flag legacy)
- Make dashboard-ui the default, and mark legacy dashboard as deprecated.
- Add CI/static checks: fail build if TS types fail validation against sample snapshot.

PR3 — Introduce snapshot v2 contract (backend)
- Add `/api/snapshot/v2` alongside existing `/api/snapshot`.
- Implement explicit, typed fields for:
  - runtime backend (mobkit/legacy), runtime mode
  - lineage holdoff reasons (paper_holdoff, venue_scope, market_closed)
  - canonical pipeline stage mapping
- Keep `/api/snapshot` for compatibility until cutover.

PR4 — Introduce TS type generation (contract stability)
- Generate `dashboard-ui/src/types/snapshot_v2.ts` from backend schema in CI.
- Stop hand-editing snapshot types.

PR5 — Rebuild navigation into 8 zones (UI skeleton)
- Add React Router (or equivalent) and implement empty pages for each zone.
- Move existing panels into the closest zone page (temporary).

PR6 — Paper-active models console (operator-grade)
- Create a dense, filterable table for `paper_active_lineages[]`.
- Add lineage-scoped paper accounting view (balance/PnL/drawdown/trades) using lineage portfolio IDs.
- Add holdoff and lane_kind columns.

PR7 — Deterministic gate blocker drilldown
- Create a Gate panel:
  - per-lineage gate status
  - structured blockers with evidence and recommended action
  - stage throughput and “stuck” detectors

PR8 — Venue readiness matrix + blockers
- Create venue readiness page:
  - connectors → venues mapping
  - impacted lineages list
  - “what to fix” recommended actions

PR9 — Goldfish DNA intelligence page
- Surface Goldfish workspaces, provenance health, last events.
- Show learning_memory and “DNA packet summary”.
- Add telemetry for DNA usage in proposal generation (what memories were pulled).

PR10 — Compute/token/cost monitoring page
- Add token/cost to agent logs and aggregate per day/family/lineage.
- Add mobkit queue/session telemetry.
- Create budget burn views + near-cap alerts.

## Highest priority (must-have to be operator-safe)
- Contract correctness (React matches backend).
- Paper-active console with lineage-scoped accounting and clear execution blockers.
- Deterministic gate blockers with drilldown (why stuck).
- Venue readiness matrix with blocked reasons.
- Basic compute/cost guard visibility (even if coarse until tokens are wired).
```

In short form (the actionable “top 10” list the team can execute immediately):

1) Fix schema mismatches in React components (chart markers + atlas history) so the UI is trustworthy. fileciteturn21file0L1-L1 fileciteturn11file0L1-L1 fileciteturn23file0L1-L1  
2) Declare a versioned snapshot contract (`/api/snapshot/v2`) and stop relying on implicit TS types. fileciteturn11file0L1-L1 fileciteturn18file0L1-L1  
3) Consolidate to **one** dashboard frontend (React/Vite) and deprecate the legacy dashboard once parity is achieved. fileciteturn11file0L1-L1 fileciteturn38file0L1-L1  
4) Add explicit “paper holdoff / venue scope / market closed” decision fields to the snapshot (operators must know why work is paused). fileciteturn52file0L1-L1  
5) Build the “Paper-active models” console around **lineage IDs** and **lineage-scoped paper accounting** (balance/P&L/drawdown/trades). fileciteturn52file0L1-L1  
6) Build the “Deterministic promotion gates” drilldown (structured blockers, evidence, recommended actions). fileciteturn52file0L1-L1 fileciteturn19file0L1-L1  
7) Add “Venue readiness & blockers” matrix: connector readiness + execution blockers mapped to impacted lineages. fileciteturn20file0L1-L1 fileciteturn52file0L1-L1  
8) Add “mobkit health” telemetry (gateway configured, RPC OK, sessions/queue/errors) into the snapshot. fileciteturn53file0L1-L1  
9) Add “Compute/token/cost” telemetry (start with budgets + run counts; then emit tokens/cost per run). fileciteturn53file0L1-L1 fileciteturn49file0L1-L1  
10) Add “Goldfish DNA intelligence” panels (workspace/provenance health + memory usage + lineage outcomes traceability). fileciteturn52file0L1-L1 fileciteturn53file0L1-L1  

## Must-have items before live

This is the operator-facing gates list for “safe broader live prep”. It is intentionally centred on **correctness + explainability + blockers**, not aesthetics.

```markdown
# ./artifacts/dashboard_must_have_before_live.md

## Must have now
- One canonical dashboard UI (React) that is contract-correct vs backend snapshot.
- Paper-active models console:
  - lineage-scoped paper accounting (balance, realised P&L, ROI, drawdown, trade count)
  - multi-venue execution readiness and explicit blockers
  - lane kind (primary incumbent vs isolated challenger)
- Deterministic gate blocker drilldown:
  - why each lineage is blocked (structured)
  - what evidence is missing
  - recommended operator action + ownership
- Venue readiness & blockers matrix:
  - connector readiness + data freshness + credential issues
  - “impacted lineages” list
- Factory health header:
  - runtime_mode + paused state
  - last_cycle age + stale detection
  - execution bridge health
- Minimal compute governance visibility:
  - current runtime backend (mobkit/legacy)
  - budget caps + burn proxy (runs/day by tier, expensive-tier rate)
- Alerts consolidated:
  - human_action_required + maintenance_queue + escalation_candidates in one view with severity and dedupe.

## Should have soon
- mobkit deep telemetry:
  - gateway up, rpc latency, queue depth, sessions, failure rate
- Token/cost accounting:
  - tokens + cost per run, per family/lineage
  - budget burn down charts and near-cap alerts
- Goldfish DNA intelligence:
  - provenance lag/health, last event timestamps/errors
  - DNA summary and memory utilisation
- Pipeline funnel analytics:
  - time-in-stage, stuck detectors, throughput trends
- Operator runbooks embedded:
  - per blocker type: “what to do” steps.

## Nice to have later
- SSE/WebSocket streaming for near-real-time updates (instead of polling)
- “Explain this lineage” auto-summaries (LLM-generated) with strict citations to artefacts
- Custom alert routing (Slack/Telegram/email) + escalation policies
- Historical playback / time travel mode (replay snapshot deltas)
- Multi-tenant operator views and permissioned controls
```

Must-have “now” (condensed for execution):
- **Paper-active lineages + lineage-scoped accounting + blockers** (this is the control tower’s core). fileciteturn52file0L1-L1  
- **Deterministic gate blockers** drilldown (operators need “why stuck” and “what next”). fileciteturn52file0L1-L1  
- **Venue readiness** matrix mapped to impacted lineages (multi-venue reality). fileciteturn20file0L1-L1  
- **Runtime backend/mode visibility** (mobkit vs legacy, cost_saver vs full, paused flags). fileciteturn53file0L1-L1 fileciteturn51file0L1-L1  
- **Unified Alerts/Incidents** view (human_action_required + maintenance/escalations). fileciteturn16file0L1-L1 fileciteturn52file0L1-L1