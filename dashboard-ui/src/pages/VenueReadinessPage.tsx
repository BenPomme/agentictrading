import { ErrorBoundary } from '../components/ErrorBoundary';
import SectionPanel from '../components/SectionPanel';
import type {
  DashboardSnapshot,
  SnapshotV2,
  ConnectorHealth,
  Family,
  Lineage,
} from '../types/snapshot';
import { relativeTime, venueIcon } from '../utils/format';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── Venue card data ──────────────────────────────────────────────────────────

interface VenueData {
  venue: string;
  connectors: ConnectorHealth[];
  families: Family[];
  activeLineageCount: number;
  scopeBlockedCount: number;
  status: 'ready' | 'blocked' | 'partial' | 'no-data';
  blockers: string[];
  latestDataTs: string | null;
  latestAgeSeconds: number;
  recordCount: number;
  inScope: boolean;
}

function buildVenueData(
  connectors: ConnectorHealth[],
  families: Family[],
  lineages: Lineage[],
  scopeSet: Set<string>,
): VenueData[] {
  // Group connectors by venue
  const byVenue = new Map<string, ConnectorHealth[]>();
  for (const c of connectors) {
    const v = c.venue || 'unknown';
    if (!byVenue.has(v)) byVenue.set(v, []);
    byVenue.get(v)!.push(c);
  }

  // All known venues from connectors + families + scope
  const allVenues = new Set<string>([
    ...byVenue.keys(),
    ...families.map((f) => f.venue || 'unknown'),
    ...scopeSet,
  ]);

  return Array.from(allVenues)
    .filter((v) => v !== 'unknown')
    .map((venue) => {
      const vConns = byVenue.get(venue) ?? [];
      const vFamilies = families.filter(
        (f) => f.venue === venue || (f.venue ?? '').includes(venue),
      );
      const activeLineages = lineages.filter(
        (l) =>
          vFamilies.some((f) => f.family_id === l.family_id) &&
          l.current_stage !== 'retired',
      );
      const scopeBlocked = lineages.filter(
        (l) =>
          vFamilies.some((f) => f.family_id === l.family_id) &&
          !scopeSet.has(venue) &&
          scopeSet.size > 0,
      );

      const blockers: string[] = [];
      let status: VenueData['status'] = 'ready';

      if (venue === 'betfair') {
        blockers.push('Missing X.509 client certificate (BF_CERTS_PATH empty)');
        blockers.push('No active families — all retired or in backup');
        status = 'blocked';
      } else if (vConns.length === 0) {
        blockers.push('No connector registered for this venue');
        status = 'no-data';
      } else {
        const hasCritical = vConns.some((c) => c.status === 'critical');
        const hasWarning = vConns.some((c) => c.status === 'warning');
        const allHealthy = vConns.every((c) => c.status === 'healthy');

        if (hasCritical) {
          status = 'blocked';
          blockers.push(
            ...vConns
              .filter((c) => c.status === 'critical')
              .map((c) => `${c.connector_id}: critical (${c.issue_count} issues)`),
          );
        } else if (hasWarning) {
          status = 'partial';
        } else if (!allHealthy) {
          status = 'partial';
        }
      }

      if (scopeSet.size > 0 && !scopeSet.has(venue) && status === 'ready') {
        status = 'partial';
        blockers.push(`Venue not in active scope (FACTORY_PAPER_WINDOW_VENUE_SCOPE)`);
      }

      const latestDataTs =
        vConns.reduce<string | null>((best, c) => {
          if (!c.latest_data_ts) return best;
          if (!best) return c.latest_data_ts;
          return c.latest_data_ts > best ? c.latest_data_ts : best;
        }, null);

      const latestAgeSeconds = Math.min(
        ...vConns.map((c) => c.latest_age_seconds ?? 999999),
      );

      const recordCount = vConns.reduce((s, c) => s + (c.record_count ?? 0), 0);

      return {
        venue,
        connectors: vConns,
        families: vFamilies,
        activeLineageCount: activeLineages.length,
        scopeBlockedCount: scopeBlocked.length,
        status,
        blockers,
        latestDataTs,
        latestAgeSeconds: isFinite(latestAgeSeconds) ? latestAgeSeconds : 0,
        recordCount,
        inScope: scopeSet.size === 0 || scopeSet.has(venue),
      };
    })
    .sort((a, b) => {
      const order = { ready: 0, partial: 1, 'no-data': 2, blocked: 3 };
      return (order[a.status] ?? 9) - (order[b.status] ?? 9);
    });
}

function VenueCard({ v }: { v: VenueData }) {
  const statusLabel =
    v.status === 'ready'
      ? 'READY'
      : v.status === 'partial'
      ? 'PARTIAL'
      : v.status === 'no-data'
      ? 'NO DATA'
      : 'BLOCKED';

  return (
    <div className={`venue-card venue-card--${v.status === 'no-data' ? 'partial' : v.status}`}>
      <div className="venue-card__header">
        <span className="venue-card__name">
          {venueIcon(v.venue)} {v.venue}
        </span>
        <span className={`venue-status-badge venue-status-badge--${v.status === 'no-data' ? 'partial' : v.status}`}>
          {statusLabel}
        </span>
      </div>

      <div className="venue-card__row">
        <span className="venue-card__label">Connectors</span>
        <span className="venue-card__val">{v.connectors.length}</span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Records</span>
        <span className="venue-card__val">
          {v.recordCount > 0 ? v.recordCount.toLocaleString() : '—'}
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Latest data</span>
        <span
          className="venue-card__val"
          style={
            v.latestAgeSeconds > 86400
              ? { color: 'var(--crit)' }
              : v.latestAgeSeconds > 3600
              ? { color: 'var(--warn)' }
              : {}
          }
        >
          {v.latestDataTs ? relativeTime(v.latestDataTs) : '—'}
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Active families</span>
        <span className="venue-card__val">{v.families.length}</span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Active lineages</span>
        <span className="venue-card__val">{v.activeLineageCount}</span>
      </div>
      {!v.inScope && (
        <div className="venue-card__row">
          <span className="venue-card__label">Scope</span>
          <span className="venue-card__val" style={{ color: 'var(--warn)' }}>
            out of scope
          </span>
        </div>
      )}

      {v.blockers.length > 0 && (
        <div className="venue-card__blockers">
          {v.blockers.map((b, i) => (
            <div key={i} className="venue-card__blocker">
              {b}
            </div>
          ))}
        </div>
      )}

      {v.families.length > 0 && (
        <div className="venue-card__families">
          {v.families.map((f) => f.family_id).join(' · ')}
        </div>
      )}
    </div>
  );
}

export function VenueReadinessPage({ snapshot, snapshotV2 }: Props) {
  // api_feeds.connectors has status + latest_age_seconds computed by the backend.
  // factory.connectors is the raw list without those computed fields.
  const connectors = (snapshot?.api_feeds?.connectors ?? snapshot?.factory?.connectors ?? []) as ConnectorHealth[];
  const families = snapshot?.factory?.families ?? [];
  const lineages = snapshot?.factory?.lineages ?? [];
  const venueScope = snapshotV2?.runtime?.venue_scope;
  const scopeSet = new Set<string>(venueScope ?? []);

  const venueData = buildVenueData(connectors, families, lineages, scopeSet);

  const readyCount = venueData.filter((v) => v.status === 'ready').length;
  const blockedCount = venueData.filter((v) => v.status === 'blocked').length;
  const partialCount = venueData.filter(
    (v) => v.status === 'partial' || v.status === 'no-data',
  ).length;

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Venue Readiness / Blockers</h2>
        <p className="page__subtitle">
          Connectivity matrix, scope enforcement, data freshness, and impacted
          lineages
        </p>
      </div>

      {/* Scope enforcement strip */}
      {venueScope && venueScope.length > 0 && (
        <div className="scope-enforcement">
          <span className="scope-label">Active scope:</span>
          {venueData.map((v) => (
            <span
              key={v.venue}
              className={`scope-venue-pill scope-venue-pill--${v.inScope ? 'in' : 'out'}`}
            >
              {v.venue}
            </span>
          ))}
        </div>
      )}

      {/* Summary strip */}
      <div className="page__runtime-strip">
        <span className="runtime-pill">
          <span className="runtime-pill__label">venues</span>
          <span className="runtime-pill__value">{venueData.length}</span>
        </span>
        <span className="runtime-pill__sep" />
        <span className="runtime-pill">
          <span className="runtime-pill__label">ready</span>
          <span className="runtime-pill__value runtime-pill__value--ok">
            {readyCount}
          </span>
        </span>
        <span className="runtime-pill__sep" />
        <span className="runtime-pill">
          <span className="runtime-pill__label">partial</span>
          <span
            className={`runtime-pill__value${partialCount > 0 ? ' runtime-pill__value--warn' : ''}`}
          >
            {partialCount}
          </span>
        </span>
        <span className="runtime-pill__sep" />
        <span className="runtime-pill">
          <span className="runtime-pill__label">blocked</span>
          <span
            className={`runtime-pill__value${blockedCount > 0 ? ' runtime-pill__value--warn' : ''}`}
          >
            {blockedCount}
          </span>
        </span>
        {venueScope && (
          <>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">scope</span>
              <span className="runtime-pill__value runtime-pill__value--ok">
                {venueScope.join(' · ')}
              </span>
            </span>
          </>
        )}
      </div>

      {/* Venue matrix */}
      <SectionPanel title="Venue Matrix" count={venueData.length}>
        <ErrorBoundary name="VenueMatrix">
          <div className="venue-matrix">
            {venueData.map((v) => (
              <VenueCard key={v.venue} v={v} />
            ))}
          </div>
        </ErrorBoundary>
      </SectionPanel>

      {/* Connector detail table */}
      <SectionPanel
        title="Connector Detail"
        count={connectors.length}
        collapsible
        defaultCollapsed={false}
      >
        <div style={{ overflowX: 'auto' }}>
          <table className="connector-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Connector</th>
                <th>Venue</th>
                <th>Records</th>
                <th>Issues</th>
                <th>Latest Data</th>
                <th>Age (s)</th>
              </tr>
            </thead>
            <tbody>
              {connectors.map((c) => (
                <tr key={c.connector_id}>
                  <td>
                    <span
                      className={`connector-status-dot connector-status-dot--${c.status ?? 'unknown'}`}
                    />
                    <span
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: '0.65rem',
                        color:
                          c.status === 'healthy'
                            ? 'var(--ok)'
                            : c.status === 'warning'
                            ? 'var(--warn)'
                            : c.status === 'critical'
                            ? 'var(--crit)'
                            : 'var(--text-muted)',
                      }}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td>{c.connector_id}</td>
                  <td>{c.venue}</td>
                  <td>{c.record_count?.toLocaleString() ?? '—'}</td>
                  <td>
                    {c.issue_count > 0 ? (
                      <span style={{ color: 'var(--warn)' }}>{c.issue_count}</span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>0</span>
                    )}
                  </td>
                  <td>
                    <span className="connector-age">
                      {c.latest_data_ts ? relativeTime(c.latest_data_ts) : '—'}
                    </span>
                  </td>
                  <td>
                    <span
                      className="connector-age"
                      style={
                        (c.latest_age_seconds ?? 0) > 86400
                          ? { color: 'var(--crit)' }
                          : (c.latest_age_seconds ?? 0) > 3600
                          ? { color: 'var(--warn)' }
                          : {}
                      }
                    >
                      {c.latest_age_seconds ?? '—'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionPanel>
    </div>
  );
}
