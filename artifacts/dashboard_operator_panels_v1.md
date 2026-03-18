# Dashboard Operator Panels v1

**Date:** 2026-03-18
**Status:** Active

## Summary

Five critical operator panels implemented in this chunk:

1. **Factory Health** — enhanced with status bar, staleness detection, execution bridge, infrastructure health
2. **Paper-Active Models Console** — per-lineage table with gate drilldown, P&L, holdoff, balance
3. **Deterministic Gate Blocker Drilldown** — inline within Paper Console, per-lineage expandable
4. **Venue Readiness Matrix** — per-venue cards with readiness, blockers, data freshness, impacted lineages
5. **Unified Alerts / Anomalies** — unified incident feed with filter bar, source tagging, and anomaly detection

---

## A. Factory Health Page

### New sections added

| Section | Data source | Key fields |
|---|---|---|
| Status Bar | `factory_paused`, `factory.status`, `factory.cycle_count`, `snapshot.generated_at` | RUNNING/PAUSED pill with animated dot; cycle count; snapshot age; backend; mode; schema version |
| Stale Detection | `snapshot.generated_at` | Banner appears when snapshot is > 90s old (server unresponsive or factory stopped) |
| Execution Bridge | `factory.execution_bridge` | running/desired portfolio ratio; runtime_mode; auto_start; suppressed count; suppressed_targets list |
| Infrastructure Health | `runtime`, `research_summary.learning_memory_count` | Mobkit backend status; Goldfish memory count; Paper holdoff state; Live trading hard-disable status |
| Readiness Checks | `factory.readiness` | Pass/fail per check; blockers; warnings (existed, now enhanced) |
| Research Summary | `factory.research_summary` | Collapsible; active lineages, families, positives, memories, escalations, paper PnL, canary-ready |

### Staleness detection logic

```
snapshotAgeSeconds = now - Date.parse(snapshot.generated_at)
if ageS > 90 → show yellow banner with age
```

Triggered at 90s (2 missed 5s polls with buffer). Not triggered by factory pause — that's a normal state shown by the PAUSED pill.

---

## B. Paper-Active Models Console

### Console columns

| Column | Source | Notes |
|---|---|---|
| Lineage / Family | `lineage.lineage_id`, `lineage.family_id` | Family on top (more readable), lineage_id truncated |
| Venue · Lane | `lineage_v2.venue`, `lineage.runtime_lane_kind` | Derived from parent family venue |
| Stage | `lineage.current_stage` | Color-coded badge (paper=green, shadow=blue, retired=red) |
| Balance | `portfolio.current_balance` | Matched via `lineage_v2.paper_portfolio_id` |
| P&L | `portfolio.realized_pnl` | Green/red with +/- sign |
| DD% | `portfolio.drawdown_pct` | Orange if > 5% |
| Trades | `portfolio.trade_count` or `lineage.trade_count` | Portfolio count preferred |
| Next Chkpt | `lineage.assessment` | Days remaining or trades remaining to next gate |
| Holdoff / Blocker | `lineage_v2.holdoff_reason`, `lineage_v2.deterministic_blockers`, `lineage.blockers` | "holdoff", "N gate", "N blk", "scope", or "—" |

### Portfolio matching logic

```
portId = lineage_v2.paper_portfolio_id ?? lineage.runtime_target_portfolio
portfolio = portfolios.find(p => p.portfolio_id === portId)
```

Portfolio pool includes both `execution.portfolios` and `execution.placeholders`.

### Lineage filter

Console shows lineages where:
- `current_stage === 'paper'` OR `current_stage === 'shadow'`
- OR `runtime_lane_selected === true`
- OR `paper_days > 0`

---

## C. Deterministic Gate Drilldown

### Gates surfaced per lineage (expandable row)

| Gate name | Source | Threshold | Evidence |
|---|---|---|---|
| Paper Days | `assessment.paper_days_required / observed` | `≥ N days` | days_observed |
| Trade Count | `assessment.trade_count_required / observed` | `≥ N trades` | trade_count_observed |
| Return Sign | `assessment.roi_pct` | `≥ 0%` | current roi_pct |
| Structured Blockers | `lineage_v2.deterministic_blockers[].code` | pass | description |
| String Blockers | `lineage.blockers[]` | pass | raw blocker string |

### Next action column

- For assessment gates: days/trades remaining, ETA from `assessment.eta`
- For deterministic blockers: `blocker.evidence` if populated, else generic message
- Pass gates: "—"

### Promotion scorecard

If `lineage.promotion_scorecard` is populated (from `promotion.decide()`), renders as a structured pass/fail grid below the gate table.

---

## D. Venue Readiness Matrix

### VenueData per card

```typescript
interface VenueData {
  venue: string;
  connectors: ConnectorHealth[];     // from factory.connectors filtered by venue
  families: Family[];                 // families whose .venue includes this venue
  activeLineageCount: number;         // non-retired lineages in these families
  scopeBlockedCount: number;          // lineages excluded by venue scope
  status: 'ready' | 'blocked' | 'partial' | 'no-data';
  blockers: string[];                 // derived: cert missing, critical connectors, scope
  latestDataTs: string | null;        // best timestamp across connectors
  latestAgeSeconds: number;           // freshest connector age
  recordCount: number;                // total records across connectors
  inScope: boolean;                   // present in runtime.venue_scope
}
```

### Status derivation logic

1. `betfair` → always BLOCKED (cert missing, no active families)
2. No connectors → NO DATA
3. Any critical connector → BLOCKED + blocker list
4. Any warning connector → PARTIAL
5. Out of scope → PARTIAL + blocker note
6. Otherwise → READY

### Scope enforcement strip

Shows all venues as color-coded pills: green (in-scope) or red strikethrough (out-of-scope). Only shown when `runtime.venue_scope` is non-null.

---

## E. Unified Alerts / Anomalies

### Incident sources and severity mapping

| Source | Severity | Data |
|---|---|---|
| `human_action_required` | critical | Requires manual intervention |
| `company.alerts` (critical) | critical | Factory-level critical alerts |
| `company.alerts` (warning) | warning | Factory-level warnings |
| `operator_signals.escalation_candidates` | warning | Research escalations |
| `operator_signals.maintenance_queue` | warning (priority-based) | Lineage maintenance items |
| Scope-blocked lineages | warning | `lineage_v2.venue_scope_reason != null` |
| Stale connectors | warning | `latest_age_seconds > 86400` |
| Anomalous lineages | warning | paper + 0 trades + 3+ days = likely stuck |

### Anomaly detection (inline, no backend needed)

```
for lineage in factory.lineages:
  if current_stage == 'paper' and trade_count == 0 and paper_days > 3:
    emit warning "No trades: N days in paper, may be stuck"
```

### Filter bar

8 filter buttons: All, Critical, Warning, Human Req, Maintenance, Alerts, Anomalies — each shows live count.

---

## Data Gaps (Backend)

| Gap | Impact | Required backend change |
|---|---|---|
| `lineage.venue` not populated in v1 lineage table | `venue` column shows "—" in console; `VenueData.families` match may miss cross-venue families | Set venue on lineage from parent family in `_build_lineage_table()` |
| `lineage_v2.deterministic_blockers[].evidence` always null | Gate evidence column shows generic messages | Wire `promotion_scorecard` evidence into `deterministic_blockers` in `build_snapshot_v2()` |
| `execution_bridge.targets` not typed | Rendered via raw JSON in future debug panel | Add `targets: BridgeTarget[]` to `ExecutionBridge` type and snapshot v2 |
| Goldfish write health | Infra health card shows "Planned" | Expose goldfish ping/last_write_at in snapshot v2 |
| Mobkit RPC depth/latency | Infra health card shows backend=mobkit as proxy | Expose mobkit health block in snapshot v2 |
| `assessment.roi_pct` ≠ gate threshold | Return Sign gate uses sign check, not actual gate threshold | Expose gate thresholds (sparse vs rich) in lineage_v2 |
| `lineage.paper_days_required` not in Lineage type | Gate threshold from assessment only (works) | — (assessment covers it) |

---

## Build Results

```
tsc -b    → 0 errors
vite build → 379 modules, dist built in ~120ms
```

Snapshot v2 dry-run:
```
schema_version: v2
runtime: {backend: mobkit, mode: full, paused: false, ...}
lineage_v2 count: 24  (all with correct keys)
execution_bridge: running=1, desired=1 (healthy)
paper_runtime: running=5, suppressed=9, retired=43
```

---

## Files Changed

| File | Change |
|---|---|
| `dashboard-ui/src/types/snapshot.ts` | Added `iteration_status?`, `blockers?`, `promotion_scorecard?`, `paper_runtime_status?` to Lineage |
| `dashboard-ui/src/pages/pages.css` | +400 lines: status bar, exec bridge, infra health, paper console, gate drilldown, venue matrix, incident feed, filter bar, scorecard styles |
| `dashboard-ui/src/pages/FactoryHealthPage.tsx` | Rewritten: status bar + stale detection + exec bridge + infra health + enhanced readiness |
| `dashboard-ui/src/pages/PaperModelsPage.tsx` | Rewritten: mergeRows() + PaperConsole + per-row gate drilldown + scope-blocked table |
| `dashboard-ui/src/pages/VenueReadinessPage.tsx` | Rewritten: buildVenueData() + VenueCard matrix + scope enforcement strip |
| `dashboard-ui/src/pages/AlertsPage.tsx` | Rewritten: buildIncidents() + UnifiedIncidentFeed + filter bar + anomaly detection |
