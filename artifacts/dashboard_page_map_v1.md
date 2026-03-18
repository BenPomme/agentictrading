# Dashboard Page Map v1

**Date:** 2026-03-18

Maps existing panels/components to their zone, and documents what is new vs
what was reorganized from the old two-tab layout.

---

## Zone → Component Map

### Zone 1: Factory Health (`factory-health`)

| Component | Source | Notes |
|---|---|---|
| `RuntimeStrip` | NEW | v2 backend/mode/holdoff/scope/schema display |
| `KPIDeck` | Moved from Overview | 8-card KPI summary |
| `ReadinessChecks` (inline) | NEW | Structured check list with pass/fail, blockers, warnings |
| `ResearchSummary` (collapsible) | NEW | learning_memory_count, positives, escalations, etc. |

---

### Zone 2: Pipeline / Lifecycle (`pipeline`)

| Component | Source | Notes |
|---|---|---|
| `StageFunnel` | NEW | Counts lineages by current_stage, horizontal bar chart |
| `ResearchStats` strip | NEW | Active/retired/mutations/challenges counts |
| `LineageBoard` | Moved from Overview tertiary grid | Full lineage table |
| `QueuePanel` | Moved from Overview tertiary grid | Promotion queue |
| `StuckDetection` (placeholder) | NEW placeholder | Future: flag lineages stalled > 48h |

---

### Zone 3: Paper-Active Models (`paper-models`)

| Component | Source | Notes |
|---|---|---|
| `PaperRuntimeBar` | NEW | Running/starting/assigned/candidate/suppressed/failed/retired |
| `PortfolioGrid` | Moved from Overview primary grid | Paper portfolio cards + P&L charts |
| `LineageV2Table — Paper/Shadow` | NEW | paper+shadow lineages from lineage_v2, explicit holdoff/scope |
| `LineageV2Table — Holdoff` | NEW | Lineages held off from agentic churn |
| `LineageV2Table — Scope Blocked` | NEW | Lineages excluded by venue scope enforcement |

---

### Zone 4: Family & Lineage Explorer (`family-explorer`)

| Component | Source | Notes |
|---|---|---|
| `FamiliesPanel` | Moved from Overview secondary grid | Family cards with champion ROI |
| `LeaguePanel` | Moved from Overview secondary grid | Model league table |
| `LineageBoard` | Duplicate (also in Pipeline) | Full table view for exploration |
| `LineageAtlas` | Moved from Atlas tab | Family tree with inspector + evolution ledger |

**Note:** Previous "Lineage Atlas" tab is now embedded in this zone.

---

### Zone 5: Goldfish DNA Intelligence (`goldfish-dna`)

| Component | Source | Notes |
|---|---|---|
| `ProvenanceMetrics` | NEW | learning_memory_count, positives, agent-gen counts |
| `DNAArchitecture` | NEW | Text description of DNA packet system |
| `Placeholder panel` | NEW | Documents planned panels: write health timeline, DNA inspector, memory influence heatmap |

---

### Zone 6: Compute / Cost (`compute-cost`)

| Component | Source | Notes |
|---|---|---|
| `OperatorCountsStrip` | NEW | escalations/action_inbox/human_required/review_due |
| `AgentActivityPanel` | Moved from Overview primary grid | Last 24 agent runs, success rates, providers |
| `DesksPanel` | Moved from Overview tertiary grid | Agent desk members and coverage |
| `JournalPanel` | Moved from Overview tertiary grid | Recent actions timeline |
| `IdeasPanel` | Moved from Overview tertiary grid | Ideas intake with status |
| `BudgetGovernance` (collapsible) | NEW | Documents budget env vars and circuit breaker logic |

---

### Zone 7: Venue Readiness / Blockers (`venue-readiness`)

| Component | Source | Notes |
|---|---|---|
| `VenueScopeStrip` | NEW | Active scope from v2 runtime, scope-blocked count |
| `ConnectorCountsStrip` | NEW | total/healthy/warning/critical connector counts |
| `APIFeedsStrip` | Moved (also shown globally above body) | Connector health strip with venue icons |
| `ConnectorTable` | NEW | Full connector detail: id, venue, status, records, issues, age |
| `ScopeBlockedLineages` | NEW | Lineages whose families target out-of-scope venues |
| `BetfairBlockedPanel` | NEW | Explains cert blocker, instructions to unblock |

---

### Zone 8: Alerts / Anomalies (`alerts`)

| Component | Source | Notes |
|---|---|---|
| `AlertCountsStrip` | NEW | critical/warning/escalations/maintenance/human-required |
| `AlertsPanel` | Moved from Overview sidebar | Severity-based alert list |
| `EscalationsPanel` | Moved from Overview sidebar | Operator escalation queue |
| `MaintenancePanel` | Moved from Overview sidebar | Maintenance items with priority and actions |

---

## Components Removed from App.tsx

The following were in the old top-level `App.tsx` and are now in zone pages:

| Component | Old location | New location |
|---|---|---|
| KPIDeck | App.tsx → overview | FactoryHealthPage |
| AgentActivityPanel | App.tsx → overview primary | ComputeCostPage |
| PortfolioGrid | App.tsx → overview primary | PaperModelsPage |
| AlertsPanel | App.tsx → overview sidebar | AlertsPage |
| EscalationsPanel | App.tsx → overview sidebar | AlertsPage |
| MaintenancePanel | App.tsx → overview sidebar | AlertsPage |
| FamiliesPanel | App.tsx → overview secondary | FamilyExplorerPage |
| LeaguePanel | App.tsx → overview secondary | FamilyExplorerPage |
| LineageBoard | App.tsx → overview tertiary | PipelinePage + FamilyExplorerPage |
| IdeasPanel | App.tsx → overview tertiary | ComputeCostPage |
| QueuePanel | App.tsx → overview tertiary | PipelinePage |
| DesksPanel | App.tsx → overview tertiary | ComputeCostPage |
| JournalPanel | App.tsx → overview tertiary | ComputeCostPage |
| LineageAtlas | App.tsx → atlas tab | FamilyExplorerPage |
| Tab nav (overview/atlas) | App.tsx → nav | Replaced by NavSidebar |

---

## File Inventory

### New files

```
src/types/nav.ts                         Zone type + NAV_ITEMS config
src/components/NavSidebar.tsx            Left sidebar navigation
src/components/NavSidebar.css
src/pages/pages.css                      Shared page layout + component styles
src/pages/FactoryHealthPage.tsx
src/pages/PipelinePage.tsx
src/pages/PaperModelsPage.tsx
src/pages/FamilyExplorerPage.tsx
src/pages/GoldfishDNAPage.tsx
src/pages/ComputeCostPage.tsx
src/pages/VenueReadinessPage.tsx
src/pages/AlertsPage.tsx
```

### Modified files

```
src/App.tsx                              Sidebar + 8-zone router (removed 2-tab system)
src/App.css                              Sidebar layout, removed tab styles
scripts/factory_dashboard.py            Legacy dashboard deprecation comment
```

---

## Panel Gap Analysis

Panels still needed (tracked for chunk 3+):

| Gap | Target Zone | Requires |
|---|---|---|
| Token count + cost per agent run | Compute/Cost | Backend instrumentation |
| Budget burn % gauge | Compute/Cost | Backend: daily/weekly spend tracking |
| Mobkit gateway health | Factory Health | Backend: RPC latency, queue depth |
| Goldfish write health timeline | Goldfish DNA | Backend: Goldfish health telemetry |
| DNA packet inspector per family | Goldfish DNA | Backend: expose dna_packet in snapshot v2 |
| Stuck lineage detector | Pipeline | Backend: time_in_stage field |
| Venue readiness matrix (link connectors → impacted lineages) | Venue Readiness | Backend: lineage → connector mapping |
