# Dashboard Information Architecture v1

**Date:** 2026-03-18
**Status:** Active (React canonical, legacy deprecated)

## Layout

```
┌─────────────────────────────────────────────────────────────┐
│  TopCommandBar  (sticky, full-width)                        │
│  brand · power toggle · mode badge · schema/runtime badge · │
│  audio · clock · snapshot age · health dot                  │
├─────────────────────────────────────────────────────────────┤
│  APIFeedsStrip  (always visible — thin connector status bar)│
├────────┬────────────────────────────────────────────────────┤
│ NavSide│                                                    │
│  bar   │   Active Zone / Page                              │
│ 168px  │                                                    │
│        │   Page = full-height, scrollable, padding 20px    │
│ (48px  │                                                    │
│  below │                                                    │
│ 1024px)│                                                    │
└────────┴────────────────────────────────────────────────────┘
```

## 8-Zone Navigation

| # | Zone | Route Key | Icon | Purpose |
|---|---|---|---|---|
| 1 | Factory Health | `factory-health` | ⬡ | Home. Runtime status, readiness score, cycle metrics, v2 runtime strip |
| 2 | Pipeline / Lifecycle | `pipeline` | ⇡ | Stage funnel, promotion queue, lineage board |
| 3 | Paper-Active Models | `paper-models` | ▣ | Paper/shadow lineages, portfolio P&L, holdoff state, lineage v2 table |
| 4 | Family & Lineage Explorer | `family-explorer` | ⊞ | Families, model league, lineage board, lineage atlas tree |
| 5 | Goldfish DNA Intelligence | `goldfish-dna` | ∿ | Provenance metrics, DNA architecture, planned: memory influence |
| 6 | Compute / Cost | `compute-cost` | ⚡ | Agent runs, desks, journal, ideas, budget governance notes |
| 7 | Venue Readiness / Blockers | `venue-readiness` | ◉ | Connector health table, scope enforcement, Betfair blocker |
| 8 | Alerts / Anomalies | `alerts` | ⚠ | Severity alerts, escalations, maintenance queue |

## Navigation Implementation

- State-based routing (`useState<Zone>`) — no external router dependency
- `NavSidebar` component: 168px on desktop, collapses to 48px icon bar below 1024px, hidden below 480px
- Alert badge on Alerts zone: shows combined count (alerts + maintenance + escalations), red if any are critical
- Each zone is an isolated page component under `src/pages/`

## Data Flow

- `useSnapshot()` polls `/api/snapshot` (v1) every 5s — base data
- `useSnapshotV2()` polls `/api/snapshot/v2` every 5s — adds `runtime`, `lineage_v2`
- Both passed as `{ snapshot, snapshotV2 }` to every page component
- Pages handle null gracefully; v2-specific content shows placeholder if v2 unavailable

## Header State Display

`TopCommandBar` shows (added in chunk 1):
- `schemaVersion` — `v2` once v2 endpoint responds
- `runtimeBackend` — `mobkit` or `legacy`

These appear as a `tcb__schema-badge` beside the mode badge.

## Legacy Dashboard

- Path: `dashboard/` (HTML/vanilla JS)
- Status: **Deprecated**
- Fallback: `_resolve_static_dir()` in `scripts/factory_dashboard.py` falls back to it only if `dashboard-ui/dist/index.html` is absent
- The React build (`npm run build` in `dashboard-ui/`) makes the legacy path unreachable
- Not maintained after 2026-03-18

## Canonical Frontend

**`dashboard-ui/` React/Vite app is the canonical frontend** as of 2026-03-18.

Build command: `cd dashboard-ui && npm run build`
Served from: `dashboard-ui/dist/` by `scripts/factory_dashboard.py`
