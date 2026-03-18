# Dashboard Backend Gaps Remaining

**Date:** 2026-03-18
**Status:** Tracked — prioritized for next factory sprint

These are gaps where the React UI has a placeholder or proxy, but the backend
does not yet emit the required data. Listed by priority.

---

## Priority 1 — Blocks operator decisions

### 1.1 Token count and cost per agent run

**Gap:** `AgentRun.token_count`, `AgentRun.cost_usd` do not exist.

**Impact:** Budget burn is invisible. Operators cannot tell if $15/day cap is being approached.

**Backend change required:**
- Instrument `MobkitOrchestratorBackend._run_profile_async()` to capture token usage from the MobKit response
- Add `token_count_in`, `token_count_out`, `cost_usd_estimate` to the agent run artifact JSON
- Expose in `_build_agent_run_view()` and in `budget_governance.daily_spend_usd`

**UI location:** ComputeCostPage — "Daily Spend", "Weekly Spend", "Token Total" cards currently show "— not tracked yet"

---

### 1.2 Deterministic blocker evidence

**Gap:** `lineage_v2.deterministic_blockers[].evidence` is always `null`.

**Impact:** Gate drilldown in Paper Models console shows generic messages; operators cannot see the specific metric that caused the gate to fail.

**Backend change required:**
- In `build_snapshot_v2()`, extract evidence from `lineage.promotion_scorecard` (which contains structured gate results from `promotion.decide()`)
- Map scorecard keys to blockers: `scorecard[gate_key].evidence → deterministic_blockers[].evidence`

**UI location:** PaperModelsPage — gate drilldown "Evidence" column

---

### 1.3 lineage.venue not set in lineage table

**Gap:** `family.venue` is populated but the lineage table in `_build_lineage_table()` does not copy it to the lineage row.

**Impact:** "Venue" column in Paper Models console shows "—" for all lineages; venue matrix can only match families, not lineages directly.

**Backend change required:**
- In `_build_lineage_table()`, add `"venue": family_venue_map.get(row.get("family_id"), "")`

**UI location:** PaperModelsPage console "Venue · Lane" column

---

## Priority 2 — Enhances observability

### 2.1 Mobkit RPC gateway telemetry

**Gap:** `mobkit_health.rpc_healthy` is always `null`. Queue depth, session count, RPC latency not available.

**Impact:** Cannot detect gateway saturation or RPC failures from the dashboard.

**Backend change required:**
- In `build_snapshot_v2()`, attempt `RuntimeManager.health_check()` or parse the gateway PID file and send a lightweight ping
- Expose: `rpc_healthy`, `active_sessions`, `queue_depth`, `avg_latency_ms`

**UI location:** ComputeCostPage "Session Telemetry" section; FactoryHealth "Infrastructure" section

---

### 2.2 Goldfish daemon health state

**Gap:** `goldfish_health` shows filesystem evidence only. Daemon connectivity, write latency, and error rate are not surfaced.

**Impact:** Cannot detect Goldfish daemon crashes or write failures from the dashboard.

**Backend change required:**
- In `build_snapshot_v2()`, instantiate `ProvenanceService` briefly to call `client.healthcheck()` and read `_degraded`, `_last_error`, `_last_write_time`
- Expose: `daemon_reachable`, `degraded`, `last_error`, `last_write_iso`

**UI location:** GoldfishDNAPage "Goldfish Provenance Health" section

---

### 2.3 Lineage created_at / time_in_stage

**Gap:** `lineage_v2[].created_at` is `None` for most lineages in this factory state (field not historically populated in registry lineage records).

**Impact:** Stuck lineage detector in PipelinePage cannot fire for existing lineages.

**Backend change required:**
- Ensure `created_at` is written to every lineage record in the registry (already done for `execute_revival()`, needs to be done for initial creation in `_generate_lineage_id()` or wherever new lineages are instantiated)
- Alternatively, add `entered_stage_at` field that tracks when the current_stage was last changed

**UI location:** PipelinePage "Potentially Stuck Lineages" table

---

### 2.4 execution_bridge.targets not typed

**Gap:** `execution_bridge` contains a `targets` array with rich runtime state (portfolio health, PID, heartbeat) but it's not in the TypeScript type.

**Impact:** Cannot surface per-portfolio runtime health in the Venue Readiness or Paper Models zones.

**Backend change required:**
- Add `BridgeTarget` interface to TypeScript types with: `portfolio_id`, `running`, `pid`, `heartbeat`, `runtime_status`, `health_status`, `issue_codes`, `label`
- Wire `targets` into `ExecutionBridge` TypeScript type

**UI location:** Potential: Paper Models zone, Venue Readiness zone

---

## Priority 3 — Future features

### 3.1 Learning memory count per family

**Gap:** DNA packets are empty because no `LearningMemoryEntry` records exist yet.

**Not a code bug.** The factory needs to run more cycles and complete evaluations. Once `record_learning_note()` is called by the orchestrator, memories will appear.

**Expected to auto-resolve** as the factory accumulates experience.

---

### 3.2 Lineage time-in-stage (vs total age)

**Gap:** Stuck detection uses `created_at` (lineage total age) not `entered_current_stage_at`.

**Impact:** A lineage created long ago but recently promoted to walkforward appears stuck.

**Backend change required:**
- Add `stage_entered_at` to lineage records (written when `current_stage` changes in `registry.py`)

---

### 3.3 Per-family agent cost allocation

**Gap:** No per-family or per-lineage cost attribution.

**Impact:** Cannot identify which families are consuming disproportionate inference budget.

**Backend change required:**
- Add `family_id`, `lineage_id` to agent run cost tracking
- Aggregate by family in `budget_governance` response

---

## Summary Table

| Gap | Priority | Effort | Impact |
|---|---|---|---|
| Token count / cost per run | P1 | Medium | Budget visibility blocked |
| Deterministic blocker evidence | P1 | Low | Gate drilldown incomplete |
| lineage.venue not set | P1 | Low | Paper console venue column |
| Mobkit RPC telemetry | P2 | High | Gateway health invisible |
| Goldfish daemon health | P2 | Medium | Provenance health blind |
| Lineage created_at | P2 | Low | Stuck detection limited |
| execution_bridge.targets typed | P2 | Low | Portfolio health not wired |
| Learning memories (data state) | P3 | None (auto) | DNA packets will self-populate |
| stage_entered_at vs created_at | P3 | Low | More precise stuck detection |
| Per-family cost allocation | P3 | Medium | Budget attribution |
