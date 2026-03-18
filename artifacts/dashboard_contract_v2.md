# Dashboard Snapshot v2 Contract

**Date:** 2026-03-18
**Status:** Active

## Overview

Snapshot v2 extends the existing `/api/snapshot` payload with three additions:

1. `schema_version: "v2"` — explicit version tag, absent from v1
2. `runtime` block — structured backend/mode/pause/scope metadata
3. `lineage_v2` array — per-lineage explicit typed fields for decisions that were previously implicit or missing

The original `/api/snapshot` endpoint is preserved unchanged. The v2 contract is served at `/api/snapshot/v2`.

---

## Endpoint

```
GET /api/snapshot/v2
```

Returns the full v1 payload plus v2 additions. React consumes this via `useSnapshotV2()`.

---

## Top-level additions

| Field | Type | Description |
|---|---|---|
| `schema_version` | `"v2"` | Always `"v2"` on this endpoint |
| `runtime` | `RuntimeV2` | Runtime backend/mode/pause/scope state |
| `lineage_v2` | `LineageV2[]` | Per-lineage explicit decision fields |

---

## RuntimeV2

```typescript
interface RuntimeV2 {
  backend: "mobkit" | "legacy" | string;   // FACTORY_RUNTIME_BACKEND
  mode: string;                             // AGENTIC_FACTORY_MODE
  paused: boolean;                          // factory_paused flag file
  paper_holdoff_enabled: boolean;           // FACTORY_PAPER_HOLDOFF_ENABLED
  venue_scope: string[] | null;             // FACTORY_PAPER_WINDOW_VENUE_SCOPE parsed, null = all
}
```

---

## LineageV2

One entry per lineage in `factory.lineages`. Fields are explicit and deterministic:

```typescript
interface LineageV2 {
  lineage_id: string;
  family_id: string;

  // Venue string inherited from the parent family (e.g. "binance", "yahoo,alpaca")
  venue: string;

  // PromotionStage value as string: idea | spec | shadow | paper | ...
  canonical_stage: string;

  // Structured promotion blockers — previously only available as opaque strings
  deterministic_blockers: DeterministicBlocker[];

  // Non-null when this lineage is held off from agentic churn (paper_holdoff policy)
  holdoff_reason: string | null;

  // Non-null when this lineage's family targets venues outside the active scope
  venue_scope_reason: string | null;

  // Lineage-scoped paper portfolio directory: lineage__{lineage_id}
  paper_portfolio_id: string | null;
}

interface DeterministicBlocker {
  code: string;        // Machine-readable blocker code
  description: string; // Human-readable description
  evidence: string | null; // Supporting evidence (null in v2.0, reserved for v2.1)
}
```

---

## Schema Version Badge

The React header (`TopCommandBar`) displays a debug badge showing:
```
v2 · mobkit
```

Props: `schemaVersion` and `runtimeBackend` (optional, no badge if both absent).

---

## Backwards Compatibility

- `/api/snapshot` — unchanged, returns v1 payload
- `/api/snapshot/v2` — superset of v1 with `schema_version`, `runtime`, `lineage_v2` additions
- React falls back gracefully: `useSnapshot` still polls v1; `useSnapshotV2` polls v2
- App.tsx uses v1 for all existing panels, v2 only for the schema badge

---

## Future v2.1 additions (not yet implemented)

- `deterministic_blockers[].evidence` populated from promotion_scorecard
- `runtime.mobkit_healthy` (gateway RPC health)
- `runtime.queue_depth` (mobkit session queue)
- Cost/token telemetry per agent run
- Goldfish provenance health
