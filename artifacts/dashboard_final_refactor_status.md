# Dashboard Final Refactor Status

**Date:** 2026-03-18
**Status:** Control tower complete — 8 zones operational

---

## Summary

All 8 zones of the Factory Control Tower are now implemented and building cleanly.
This chunk added the missing new-architecture observability that was previously placeholder or absent.

---

## Zones Status

| Zone | Page | Status | Key panels |
|---|---|---|---|
| 1 Factory Health | `FactoryHealthPage` | ✅ Full | Status bar + stale detect + exec bridge + infra health |
| 2 Pipeline | `PipelinePage` | ✅ Full | Stage funnel + lifecycle state + stuck detector + lineage board |
| 3 Paper Models | `PaperModelsPage` | ✅ Full | Per-lineage console + gate drilldown + holdoff + balance |
| 4 Family Explorer | `FamilyExplorerPage` | ✅ Full | Families + league + lineage board + atlas |
| 5 Goldfish DNA | `GoldfishDNAPage` | ✅ Full | Goldfish health + DNA packets + motif display + architecture |
| 6 Compute / Cost | `ComputeCostPage` | ✅ Full | Budget governance + run breakdown + session telemetry proxy |
| 7 Venue Readiness | `VenueReadinessPage` | ✅ Full | Venue matrix + connector detail + scope enforcement |
| 8 Alerts | `AlertsPage` | ✅ Full | Unified feed + filters + anomaly detection |

---

## A. Mobkit Health — Implemented (proxy)

**Available:**
- `configured` — `FACTORY_RUNTIME_BACKEND == 'mobkit'` (true)
- `recent_runs_24h`, `recent_failures_24h`, `fallback_used_24h` — from agent_runs
- `success_rate_pct` — derived from agent run data
- `runs_by_provider`, `runs_by_task`, `runs_by_model_class` — full breakdown

**Current state (dry-run):**
```
configured=True, recent_runs=24, success_rate=100.0
by_provider={openai_api: 24}
by_task={maintenance_resolution_review: 24}
```

**Missing (backend gap):** Direct RPC gateway health (latency, queue depth, session count) — requires polling the gateway process from within snapshot build. Tracked in backend gaps doc.

---

## B. Compute / Cost — Implemented

**New sections in ComputeCostPage:**
1. **Budget Governance** — caps (daily=$15, weekly=$75), circuit breaker ratios (force_cheap=80%, single_agent=90%, drop_reviewer=70%), strict mode flag
2. **Session Telemetry (proxy)** — Mobkit health summary derived from agent run stats
3. **Agent Run Breakdown** — by provider, task type, model class with proportional bars
4. **Gap cards** — daily_spend, weekly_spend, token_count_total are visually shown as "— not tracked yet" backend gaps

---

## C. Goldfish DNA Intelligence — Implemented

**New sections in GoldfishDNAPage:**
1. **Goldfish Provenance Health** — enabled status, learning file count, latest write timestamp, workspace root, strict mode
2. **Provenance Metrics** — from research_summary (learning_memory_count, positives, real-agent lineages)
3. **Family DNA Packets** — per family: failure motifs (color-tagged), success patterns, hard vetoes, best ancestor with ROI and domains
4. **DNA Architecture** — documents the DNA pipeline with `build_family_dna_packet` → `enrich_dna_from_goldfish` → `as_prompt_text()` flow

**Current state (dry-run):**
```
enabled=True, learning_files=2, latest=2026-03-14T11:31:59 UTC
DNA packets: 3 families, all seen=0 (no learning memories yet)
```

DNA packets will populate as the factory runs and writes LearningMemoryEntry records to `data/factory/history/learning_memory.jsonl`.

---

## D. Pipeline Quality — Implemented

**New sections in PipelinePage:**
1. **Quick Stats Strip** — total/active/walkforward+/shadow/paper+/retired/mutations
2. **Stage Distribution** — horizontal bar funnel (existing but retained)
3. **Lifecycle State** — iteration_status breakdown grid: active/failed/retiring/revived/rework
4. **Stuck Lineage Detector** — lineages in walkforward (>72h), stress (>72h), shadow (>48h), data_check (>48h) based on `created_at`; shows age vs threshold

**Stuck detection thresholds:**

| Stage | Threshold |
|---|---|
| walkforward | 72h |
| stress | 72h |
| shadow | 48h |
| data_check | 48h |
| goldfish_run | 48h |
| paper | — (normal to stay) |

**Note:** Stuck detection requires `created_at` on lineage_v2 entries. Currently `None` for this factory state (field added going forward). Will populate for newly created/revived lineages.

---

## E. Final Polish

- All zone titles use "families", "lineages", "lanes", "paper accounts", "gates", "provenance" terminology
- No single venue dominates — venue is shown as a dimension alongside family/lineage, not as the organizing axis
- Legacy tab system removed; 8-zone NavSidebar with badge count on Alerts zone
- Schema v2 badge visible in TopCommandBar (shows `v2 · mobkit`)
- `APIFeedsStrip` shown globally (always visible) — detailed matrix in Venue Readiness zone

---

## Build Results

```
tsc -b → 0 type errors
vite build → 55 modules, 283KB JS, 54KB CSS
Backend dry-run → all v2 fields populated correctly
```

---

## Files Changed (This Chunk)

| File | Change |
|---|---|
| `factory/operator_dashboard.py` | Added mobkit_health, budget_governance, dna_packets, goldfish_health to build_snapshot_v2(); added created_at to lineage_v2 entries |
| `dashboard-ui/src/types/snapshot.ts` | Added MobkitHealth, BudgetGovernance, DNAPacket, DNAAncestor, GoldfishHealth; added created_at to LineageV2; updated SnapshotV2 |
| `dashboard-ui/src/pages/pages.css` | +350 lines: budget cards, run breakdown tables, mobkit stats, DNA packet cards, iter-status grid, stuck table |
| `dashboard-ui/src/pages/ComputeCostPage.tsx` | Rewritten: budget governance + session telemetry + run breakdown by provider/task/model |
| `dashboard-ui/src/pages/GoldfishDNAPage.tsx` | Rewritten: goldfish health section + DNA packet cards with motif tags + provenance metrics |
| `dashboard-ui/src/pages/PipelinePage.tsx` | Enhanced: lifecycle state grid + stuck lineage detector + quick stats strip |
