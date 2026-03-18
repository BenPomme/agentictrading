import { useState } from 'react';
import SectionPanel from '../components/SectionPanel';
import { ErrorBoundary } from '../components/ErrorBoundary';
import type {
  DashboardSnapshot,
  SnapshotV2,
  Alert,
  MaintenanceItem,
  LineageV2,
  Lineage,
} from '../types/snapshot';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── Unified incident model ───────────────────────────────────────────────────

type Severity = 'critical' | 'warning' | 'info';
type IncidentSource =
  | 'alert'
  | 'maintenance'
  | 'escalation'
  | 'human_action'
  | 'scope_block'
  | 'stale_data'
  | 'anomaly';

interface Incident {
  id: string;
  severity: Severity;
  source: IncidentSource;
  title: string;
  detail: string;
  lineage_id?: string;
  family_id?: string;
  action?: string;
}

function fromAlerts(alerts: Alert[]): Incident[] {
  return alerts.map((a, i) => ({
    id: `alert-${i}`,
    severity:
      a.severity === 'critical'
        ? 'critical'
        : a.severity === 'warning'
        ? 'warning'
        : 'info',
    source: 'alert',
    title: a.title,
    detail: a.detail,
    lineage_id: undefined,
    family_id: undefined,
    action: undefined,
  }));
}

function fromMaintenance(items: MaintenanceItem[]): Incident[] {
  return items.map((m) => ({
    id: `maint-${m.lineage_id}`,
    severity: m.priority <= 2 ? 'critical' : m.priority <= 4 ? 'warning' : 'info',
    source: 'maintenance',
    title: `${m.action} — ${m.family_id}`,
    detail: m.reason,
    lineage_id: m.lineage_id,
    family_id: m.family_id,
    action: (m.recommended_actions ?? [])[0],
  }));
}

function fromEscalations(items: unknown[]): Incident[] {
  return items.map((e, i) => {
    const r = e as Record<string, unknown>;
    return {
      id: `esc-${i}`,
      severity: 'warning' as Severity,
      source: 'escalation' as IncidentSource,
      title: String(r.reason ?? r.title ?? 'Escalation'),
      detail: String(r.detail ?? r.lineage_id ?? ''),
      lineage_id: String(r.lineage_id ?? ''),
      family_id: String(r.family_id ?? ''),
      action: String(r.recommended_action ?? ''),
    };
  });
}

function fromHumanRequired(items: unknown[]): Incident[] {
  return items.map((h, i) => {
    const r = h as Record<string, unknown>;
    return {
      id: `human-${i}`,
      severity: 'critical' as Severity,
      source: 'human_action' as IncidentSource,
      title: String(r.reason ?? r.action ?? 'Human action required'),
      detail: String(r.detail ?? r.lineage_id ?? ''),
      lineage_id: String(r.lineage_id ?? ''),
      family_id: String(r.family_id ?? ''),
      action: 'Requires manual operator intervention',
    };
  });
}

function fromScopeBlocked(rows: LineageV2[]): Incident[] {
  return rows.map((r) => ({
    id: `scope-${r.lineage_id}`,
    severity: 'warning' as Severity,
    source: 'scope_block' as IncidentSource,
    title: `Scope excluded: ${r.family_id}`,
    detail: r.venue_scope_reason ?? 'Venue not in active scope',
    lineage_id: r.lineage_id,
    family_id: r.family_id,
    action: 'Add venue to FACTORY_PAPER_WINDOW_VENUE_SCOPE or retire lineage',
  }));
}

function fromStaleConnectors(
  lineages: Lineage[],
  connectors: DashboardSnapshot['factory']['connectors'],
): Incident[] {
  const incidents: Incident[] = [];
  for (const c of connectors ?? []) {
    if ((c.latest_age_seconds ?? 0) > 86400) {
      incidents.push({
        id: `stale-${c.connector_id}`,
        severity: 'warning',
        source: 'stale_data',
        title: `Stale connector: ${c.connector_id}`,
        detail: `No new data for ${Math.round((c.latest_age_seconds ?? 0) / 3600)}h — venue: ${c.venue}`,
        action: 'Investigate connector health or data pipeline',
      });
    }
  }
  // Detect lineages with 0 trades in paper stage (potential stuck)
  for (const l of lineages ?? []) {
    if (
      l.current_stage === 'paper' &&
      (l.trade_count ?? 0) === 0 &&
      (l.paper_days ?? 0) > 3
    ) {
      incidents.push({
        id: `stuck-${l.lineage_id}`,
        severity: 'warning',
        source: 'anomaly',
        title: `No trades: ${l.family_id}`,
        detail: `${l.paper_days}d in paper stage, 0 trades — may be stuck or misconfigured`,
        lineage_id: l.lineage_id,
        family_id: l.family_id,
        action: 'Check portfolio runtime state and connector health for this family',
      });
    }
  }
  return incidents;
}

function buildIncidents(
  snapshot: DashboardSnapshot | null,
  snapshotV2: SnapshotV2 | null,
): Incident[] {
  if (!snapshot) return [];
  const signals = snapshot.factory?.operator_signals;
  const lineages = snapshot.factory?.lineages ?? [];
  const connectors = snapshot.factory?.connectors ?? [];
  const lineageV2 = snapshotV2?.lineage_v2 ?? [];
  const scopeBlocked = lineageV2.filter((r) => r.venue_scope_reason != null);

  return [
    ...fromHumanRequired(signals?.human_action_required ?? []),
    ...fromAlerts(snapshot.company?.alerts ?? []),
    ...fromEscalations(signals?.escalation_candidates ?? []),
    ...fromMaintenance(signals?.maintenance_queue ?? []),
    ...fromScopeBlocked(scopeBlocked),
    ...fromStaleConnectors(lineages, connectors),
  ].sort((a, b) => {
    const sev = { critical: 0, warning: 1, info: 2 };
    return (sev[a.severity] ?? 3) - (sev[b.severity] ?? 3);
  });
}

// ── Filter bar ───────────────────────────────────────────────────────────────

type FilterKey =
  | 'all'
  | 'critical'
  | 'warning'
  | 'alert'
  | 'maintenance'
  | 'human_action'
  | 'anomaly';

const FILTERS: { key: FilterKey; label: string; extra?: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical', extra: 'crit' },
  { key: 'warning', label: 'Warning', extra: 'warn' },
  { key: 'human_action', label: 'Human req', extra: 'crit' },
  { key: 'maintenance', label: 'Maintenance' },
  { key: 'alert', label: 'Alerts' },
  { key: 'anomaly', label: 'Anomalies' },
];

function applyFilter(incidents: Incident[], filter: FilterKey): Incident[] {
  if (filter === 'all') return incidents;
  if (filter === 'critical') return incidents.filter((i) => i.severity === 'critical');
  if (filter === 'warning') return incidents.filter((i) => i.severity === 'warning');
  return incidents.filter((i) => i.source === filter);
}

// ── Incident item ─────────────────────────────────────────────────────────────

function IncidentItem({ inc }: { inc: Incident }) {
  return (
    <div className={`incident incident--${inc.severity}`}>
      <div className="incident__badge">
        <span className={`incident__sev incident__sev--${inc.severity}`}>
          {inc.severity}
        </span>
        <span className="incident__src">{inc.source.replace('_', ' ')}</span>
      </div>
      <div className="incident__body">
        <div className="incident__title">{inc.title}</div>
        <div className="incident__detail">{inc.detail}</div>
        {inc.action && inc.action !== 'undefined' && (
          <div className="incident__action">→ {inc.action}</div>
        )}
      </div>
      <div className="incident__ctx">
        {inc.lineage_id && inc.lineage_id !== 'undefined' ? (
          <span title={inc.lineage_id}>
            {inc.lineage_id.length > 20
              ? '…' + inc.lineage_id.slice(-18)
              : inc.lineage_id}
          </span>
        ) : null}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function AlertsPage({ snapshot, snapshotV2 }: Props) {
  const [filter, setFilter] = useState<FilterKey>('all');

  const allIncidents = buildIncidents(snapshot, snapshotV2);
  const visible = applyFilter(allIncidents, filter);

  const critCount = allIncidents.filter((i) => i.severity === 'critical').length;
  const warnCount = allIncidents.filter((i) => i.severity === 'warning').length;
  const humanCount = allIncidents.filter((i) => i.source === 'human_action').length;
  const maintCount = allIncidents.filter((i) => i.source === 'maintenance').length;
  const anomalyCount = allIncidents.filter((i) => i.source === 'anomaly').length;

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Alerts / Anomalies</h2>
        <p className="page__subtitle">
          Unified incident feed: alerts, maintenance, escalations, human
          actions, anomalies
        </p>
      </div>

      {/* Summary strip */}
      <div className="page__runtime-strip">
        {[
          ['Total', allIncidents.length, allIncidents.length > 0],
          ['Critical', critCount, critCount > 0],
          ['Warning', warnCount, warnCount > 0],
          ['Human req', humanCount, humanCount > 0],
          ['Maintenance', maintCount, maintCount > 0],
          ['Anomalies', anomalyCount, anomalyCount > 0],
        ].map(([label, val, highlight]) => (
          <span key={String(label)} className="runtime-pill">
            <span className="runtime-pill__label">{label}</span>
            <span
              className={`runtime-pill__value${highlight ? ' runtime-pill__value--warn' : ''}`}
            >
              {String(val)}
            </span>
          </span>
        ))}
      </div>

      {/* Unified incident feed */}
      <SectionPanel title="Incident Feed" count={allIncidents.length}>
        {/* Filter bar */}
        <div className="alert-filter-bar">
          {FILTERS.map((f) => {
            const count =
              f.key === 'all'
                ? allIncidents.length
                : applyFilter(allIncidents, f.key).length;
            return (
              <button
                key={f.key}
                className={`alert-filter-btn${f.extra ? ` alert-filter-btn--${f.extra}` : ''}${filter === f.key ? ' alert-filter-btn--active' : ''}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label} ({count})
              </button>
            );
          })}
        </div>

        <ErrorBoundary name="IncidentFeed">
          {visible.length === 0 ? (
            <div className="alert-empty">
              {filter === 'all'
                ? '✓ No incidents — factory appears healthy.'
                : `No ${filter} incidents.`}
            </div>
          ) : (
            <div className="incident-feed">
              {visible.map((inc) => (
                <IncidentItem key={inc.id} inc={inc} />
              ))}
            </div>
          )}
        </ErrorBoundary>
      </SectionPanel>

      {/* Breakdown panels */}
      {humanCount > 0 && (
        <SectionPanel
          title="Human Action Required"
          count={humanCount}
          tag="operator needed"
          tagColor="var(--crit)"
          collapsible
          defaultCollapsed
        >
          <div className="incident-feed">
            {allIncidents
              .filter((i) => i.source === 'human_action')
              .map((i) => (
                <IncidentItem key={i.id} inc={i} />
              ))}
          </div>
        </SectionPanel>
      )}

      {maintCount > 0 && (
        <SectionPanel
          title="Maintenance Queue"
          count={maintCount}
          collapsible
          defaultCollapsed
        >
          <div className="incident-feed">
            {allIncidents
              .filter((i) => i.source === 'maintenance')
              .map((i) => (
                <IncidentItem key={i.id} inc={i} />
              ))}
          </div>
        </SectionPanel>
      )}

      {anomalyCount > 0 && (
        <SectionPanel
          title="Anomalies / Stuck Lineages"
          count={anomalyCount}
          collapsible
          defaultCollapsed
        >
          <div className="incident-feed">
            {allIncidents
              .filter((i) => i.source === 'anomaly' || i.source === 'stale_data')
              .map((i) => (
                <IncidentItem key={i.id} inc={i} />
              ))}
          </div>
        </SectionPanel>
      )}
    </div>
  );
}
