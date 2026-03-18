# Dashboard Schema Drift Fixes

**Date:** 2026-03-18
**Status:** Applied

## Summary

Schema drift audit covering the backend `/api/snapshot` and `/api/portfolio/:id/chart` endpoints
against the React TypeScript types and component implementations.

---

## Fixes Applied

### 1. Trade Marker Bug (Breaking — markers never rendered)

**File:** `dashboard-ui/src/components/PnlChart.tsx`

| | Value |
|---|---|
| **Root cause** | Backend emits `kind: "trade_opened"` and `kind: "trade_closed"`, React filtered for `'open'` and `'close'` |
| **Impact** | Green/red entry and win/loss exit markers were never visible on any portfolio chart |
| **Fix** | Changed filter from `t.kind === 'open'` → `t.kind === 'trade_opened'`, `t.kind === 'close'` → `t.kind === 'trade_closed'` |
| **Backend code** | `scripts/factory_dashboard.py:104` emits `"trade_opened"`, line 113 emits `"trade_closed"` |

```diff
-  const opens = trades.filter(t => t.kind === 'open')
+  const opens = trades.filter(t => t.kind === 'trade_opened')
-  const closes = trades.filter(t => t.kind === 'close');
+  const closes = trades.filter(t => t.kind === 'trade_closed');
```

### 2. ChartTrade Kind Type Documentation

**File:** `dashboard-ui/src/types/snapshot.ts`

Added explicit union type to `ChartTrade.kind` to document the canonical values:

```typescript
// Before (permissive, no documentation):
kind: string;

// After (documented, drift-detectable):
kind: 'trade_opened' | 'trade_closed' | string;
```

---

## Schema Drift Not Fixed (Known, Tracked)

### LineageAtlas History Events

The `LineageAtlasEvent` type (`ts, kind, lineage_id, detail`) may not match
what `_build_lineage_atlas()` in `operator_dashboard.py` actually emits.
Both sides use `[key: string]: unknown` catch-all so TypeScript doesn't flag it.

**Status:** Not fixed in this chunk. Planned for atlas refactor phase.

### PortfolioSnapshot `points` vs `balance_points`

Both field names exist on `ChartPayload` as optional (`points?` and `balance_points?`).
The backend emits `points` only (see `factory_dashboard.py:152`).
The React component handles both via `data.points ?? data.balance_points ?? []`.

**Status:** Not broken, handled defensively. Will be unified in v2.1.

---

## Observability Gaps (Not Yet Fixed)

The following fields are absent from both v1 and v2 snapshots. Tracked for future chunks:

| Gap | Required for |
|---|---|
| mobkit gateway RPC health | Chunk 2: runtime health panel |
| Token count + cost per agent run | Chunk 3: cost/budget telemetry |
| Goldfish provenance write health | Chunk 4: DNA intelligence panel |
| Computed "stuck lineage" (time-in-stage) | Chunk 2: pipeline lifecycle panel |
| Venue readiness matrix (connector → venue → lineages) | Chunk 5: venue readiness panel |

---

## Files Changed in This Chunk

| File | Change |
|---|---|
| `dashboard-ui/src/components/PnlChart.tsx` | Fix trade_opened/trade_closed filter |
| `dashboard-ui/src/types/snapshot.ts` | Document ChartTrade kind, add SnapshotV2/RuntimeV2/LineageV2/DeterministicBlocker |
| `dashboard-ui/src/hooks/useSnapshotV2.ts` | New — polls /api/snapshot/v2 |
| `dashboard-ui/src/components/TopCommandBar.tsx` | Add schemaVersion + runtimeBackend props, render schema badge |
| `dashboard-ui/src/components/TopCommandBar.css` | Add .tcb__schema-badge style |
| `dashboard-ui/src/App.tsx` | Import useSnapshotV2, pass schema/runtime to TopCommandBar |
| `scripts/factory_dashboard.py` | Import build_snapshot_v2, add GET /api/snapshot/v2 handler |
| `factory/operator_dashboard.py` | Add build_snapshot_v2() function |
