import { ErrorBoundary } from '../components/ErrorBoundary';
import SectionPanel from '../components/SectionPanel';
import LineageBoard from '../components/LineageBoard';
import QueuePanel from '../components/QueuePanel';
import type { DashboardSnapshot, SnapshotV2, LineageV2 } from '../types/snapshot';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── Stage funnel ─────────────────────────────────────────────────────────────

const STAGE_ORDER = [
  'idea',
  'spec',
  'data_check',
  'goldfish_run',
  'walkforward',
  'stress',
  'shadow',
  'paper',
  'canary_ready',
  'live_ready',
  'approved_live',
  'retired',
];

const STAGE_COLORS: Record<string, string> = {
  idea: '#4a5568',
  spec: '#4a5568',
  data_check: '#5a6070',
  goldfish_run: '#00a6ff',
  walkforward: '#00a6ff',
  stress: '#ffd700',
  shadow: '#00c8ff',
  paper: '#00ffd0',
  canary_ready: '#4ade80',
  live_ready: '#22c55e',
  approved_live: '#16a34a',
  retired: '#3f3f46',
};

function stageFunnelData(
  lineages: DashboardSnapshot['factory']['lineages'],
): { stage: string; count: number }[] {
  const counts: Record<string, number> = {};
  for (const lin of lineages ?? []) {
    const s = lin.current_stage ?? 'unknown';
    counts[s] = (counts[s] ?? 0) + 1;
  }
  const result: { stage: string; count: number }[] = [];
  for (const stage of STAGE_ORDER) {
    if (counts[stage] != null) result.push({ stage, count: counts[stage] });
  }
  for (const [stage, count] of Object.entries(counts)) {
    if (!STAGE_ORDER.includes(stage)) result.push({ stage, count });
  }
  return result;
}

// ── Iteration status breakdown ───────────────────────────────────────────────

interface IterStatusBucket {
  label: string;
  key: string;
  count: number;
  cssKey: string;
}

function iterStatusBuckets(
  lineages: DashboardSnapshot['factory']['lineages'],
): IterStatusBucket[] {
  const counts: Record<string, number> = {};
  for (const l of lineages ?? []) {
    const s = String((l as Record<string, unknown>).iteration_status ?? 'active');
    counts[s] = (counts[s] ?? 0) + 1;
  }
  // Grouped display buckets
  const buckets: IterStatusBucket[] = [];
  const active =
    (counts['active'] ?? 0) +
    (counts['revived_for_paper'] ?? 0) +
    (counts['probationary'] ?? 0);
  const failed =
    (counts['failed'] ?? 0) + (counts['degraded'] ?? 0);
  const retiring =
    (counts['retiring'] ?? 0) + (counts['retired'] ?? 0);
  const revived = counts['revived_for_paper'] ?? 0;
  const rework = counts['review_requested_rework'] ?? 0;

  if (active > 0) buckets.push({ label: 'Active', key: 'active', count: active, cssKey: 'active' });
  if (failed > 0) buckets.push({ label: 'Failed', key: 'failed', count: failed, cssKey: 'failed' });
  if (retiring > 0) buckets.push({ label: 'Retiring', key: 'retiring', count: retiring, cssKey: 'retiring' });
  if (revived > 0) buckets.push({ label: 'Revived', key: 'revived', count: revived, cssKey: 'revived' });
  if (rework > 0) buckets.push({ label: 'Rework Req', key: 'rework', count: rework, cssKey: 'retiring' });

  // Unknown statuses
  const known = new Set(['active', 'failed', 'degraded', 'retiring', 'retired', 'revived_for_paper', 'probationary', 'review_requested_rework']);
  for (const [s, n] of Object.entries(counts)) {
    if (!known.has(s) && n > 0) {
      buckets.push({ label: s, key: s, count: n, cssKey: 'retiring' });
    }
  }
  return buckets;
}

// ── Stuck lineage detection ──────────────────────────────────────────────────

interface StuckLineage {
  lineage_id: string;
  family_id: string;
  stage: string;
  iteration_status: string;
  ageHours: number | null;
  created_at: string | null;
}

const STUCK_STAGE_THRESHOLDS: Record<string, number> = {
  walkforward: 72,
  stress: 72,
  shadow: 48,
  data_check: 48,
  goldfish_run: 48,
  paper: 0, // never "stuck" in paper — it's normal to stay here
};

function detectStuckLineages(lineageV2: LineageV2[]): StuckLineage[] {
  const now = Date.now();
  const stuck: StuckLineage[] = [];

  for (const l of lineageV2) {
    const threshold = STUCK_STAGE_THRESHOLDS[l.canonical_stage];
    if (threshold === undefined || threshold === 0) continue;

    const iterStatus = String((l as unknown as Record<string, unknown>).iteration_status ?? 'active');
    if (iterStatus === 'retired' || iterStatus === 'retiring') continue;

    if (!l.created_at) continue;
    const ageMs = now - new Date(l.created_at).getTime();
    if (isNaN(ageMs) || ageMs < 0) continue;
    const ageHours = ageMs / 3600000;

    if (ageHours >= threshold) {
      stuck.push({
        lineage_id: l.lineage_id,
        family_id: l.family_id,
        stage: l.canonical_stage,
        iteration_status: iterStatus,
        ageHours: Math.round(ageHours),
        created_at: l.created_at,
      });
    }
  }
  return stuck.sort((a, b) => (b.ageHours ?? 0) - (a.ageHours ?? 0));
}

// ── Stage transitions summary ────────────────────────────────────────────────

function stageSummaryStats(lineages: DashboardSnapshot['factory']['lineages']) {
  const total = (lineages ?? []).length;
  const active = (lineages ?? []).filter(
    (l) => l.current_stage !== 'retired',
  ).length;
  const paperOrBetter = (lineages ?? []).filter(
    (l) =>
      l.current_stage === 'paper' ||
      l.current_stage === 'canary_ready' ||
      l.current_stage === 'live_ready' ||
      l.current_stage === 'approved_live',
  ).length;
  const walkforwardOrBetter = (lineages ?? []).filter(
    (l) =>
      [
        'walkforward', 'stress', 'shadow', 'paper',
        'canary_ready', 'live_ready', 'approved_live',
      ].includes(l.current_stage ?? ''),
  ).length;
  const shadow = (lineages ?? []).filter(
    (l) => l.current_stage === 'shadow',
  ).length;
  return { total, active, paperOrBetter, walkforwardOrBetter, shadow };
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function PipelinePage({ snapshot, snapshotV2 }: Props) {
  const lineages = snapshot?.factory?.lineages ?? [];
  const queue = snapshot?.factory?.queue ?? [];
  const archivedLineages = snapshot?.factory?.archived_lineages ?? [];
  const archivedQueue = snapshot?.factory?.archived_queue ?? [];
  const lineageV2 = snapshotV2?.lineage_v2 ?? [];
  const funnelData = stageFunnelData(lineages);
  const maxCount = Math.max(1, ...funnelData.map((r) => r.count));
  const iterBuckets = iterStatusBuckets(lineages);
  const stuckLineages = detectStuckLineages(lineageV2);
  const stats = stageSummaryStats(lineages);
  const rs = snapshot?.factory?.research_summary;

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Pipeline / Lifecycle</h2>
        <p className="page__subtitle">
          Stage funnel, lifecycle quality, stuck detection, revival and
          exhaustion states
        </p>
      </div>

      {/* Quick stats strip */}
      <div className="page__runtime-strip">
        {[
          ['Total', stats.total],
          ['Active', stats.active],
          ['Walkforward+', stats.walkforwardOrBetter],
          ['Shadow', stats.shadow],
          ['Paper+', stats.paperOrBetter],
          ['Queue', queue.length],
          ['Archived queue', archivedQueue.length],
        ].map(([label, val]) => (
          <span key={String(label)} className="runtime-pill">
            <span className="runtime-pill__label">{label}</span>
            <span className="runtime-pill__value">{String(val)}</span>
          </span>
        ))}
        {rs && (
          <>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">Retired</span>
              <span className="runtime-pill__value">
                {rs.retired_lineage_count}
              </span>
            </span>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">Mutations</span>
              <span className="runtime-pill__value">
                {rs.mutation_lineage_count}
              </span>
            </span>
          </>
        )}
      </div>

      {/* Stage funnel */}
      {funnelData.length > 0 && (
        <SectionPanel title="Stage Distribution" count={lineages.length}>
          <div className="stage-funnel">
            {funnelData.map(({ stage, count }) => (
              <div key={stage} className="stage-funnel__row">
                <span className="stage-funnel__name">
                  {stage.replace(/_/g, ' ')}
                </span>
                <div className="stage-funnel__bar-wrap">
                  <div
                    className="stage-funnel__bar"
                    style={{
                      width: `${(count / maxCount) * 100}%`,
                      background: STAGE_COLORS[stage] ?? '#4a5568',
                    }}
                  />
                </div>
                <span className="stage-funnel__count">{count}</span>
              </div>
            ))}
          </div>
        </SectionPanel>
      )}

      {/* Iteration status breakdown */}
      {iterBuckets.length > 0 && (
        <SectionPanel title="Lifecycle State">
          <div className="iter-status-grid">
            {iterBuckets.map((b) => (
              <div key={b.key} className={`iter-stat iter-stat--${b.cssKey}`}>
                <div className="iter-stat__value">{b.count}</div>
                <div className="iter-stat__label">{b.label}</div>
              </div>
            ))}
          </div>
        </SectionPanel>
      )}

      <SectionPanel
        title="Archive Snapshot"
        count={archivedLineages.length}
        tag={archivedQueue.length > 0 ? `${archivedQueue.length} archived queue` : 'clean live queue'}
        tagColor={archivedQueue.length > 0 ? 'var(--warn)' : 'var(--ok)'}
      >
        <div className="page__runtime-strip">
          <span className="runtime-pill">
            <span className="runtime-pill__label">archived lineages</span>
            <span className="runtime-pill__value">{archivedLineages.length}</span>
          </span>
          <span className="runtime-pill__sep" />
          <span className="runtime-pill">
            <span className="runtime-pill__label">archived queue</span>
            <span className="runtime-pill__value">{archivedQueue.length}</span>
          </span>
        </div>
      </SectionPanel>

      {/* Stuck lineage detector */}
      {stuckLineages.length > 0 ? (
        <SectionPanel
          title="Potentially Stuck Lineages"
          count={stuckLineages.length}
          tag="investigate"
          tagColor="var(--warn)"
        >
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.7rem',
              color: 'var(--text-muted)',
              marginBottom: 10,
            }}
          >
            Lineages in walkforward / stress / shadow / data_check that have
            exceeded age threshold without advancing or retiring. Check agent
            dispatch logs and connector health.
          </p>
          <div style={{ overflowX: 'auto' }}>
            <table className="stuck-table">
              <thead>
                <tr>
                  <th>Lineage</th>
                  <th>Family</th>
                  <th>Stage</th>
                  <th>Status</th>
                  <th>Age (h)</th>
                  <th>Threshold (h)</th>
                </tr>
              </thead>
              <tbody>
                {stuckLineages.map((s) => (
                  <tr key={s.lineage_id}>
                    <td>
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '0.64rem',
                          color: 'var(--text-muted)',
                        }}
                        title={s.lineage_id}
                      >
                        {s.lineage_id.length > 30
                          ? '…' + s.lineage_id.slice(-26)
                          : s.lineage_id}
                      </span>
                    </td>
                    <td>{s.family_id}</td>
                    <td>
                      <span className={`lv2-stage-badge lv2-stage-badge--${s.stage === 'paper' ? 'paper' : s.stage === 'shadow' ? 'shadow' : 'default'}`}>
                        {s.stage}
                      </span>
                    </td>
                    <td>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                        {s.iteration_status}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`stuck-age${(s.ageHours ?? 0) > 96 ? ' stuck-age--crit' : ' stuck-age--warn'}`}
                      >
                        {s.ageHours}h
                      </span>
                    </td>
                    <td style={{ color: 'var(--text-muted)' }}>
                      {STUCK_STAGE_THRESHOLDS[s.stage] ?? '—'}h
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionPanel>
      ) : (
        lineageV2.length > 0 && (
          <div
            style={{
              padding: '10px 14px',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.7rem',
              color: 'var(--ok)',
              background: 'rgba(0, 255, 208, 0.03)',
              border: '1px solid rgba(0, 255, 208, 0.15)',
              borderRadius: 6,
            }}
          >
            ✓ No stuck lineages detected across {lineageV2.length} tracked lineages.
          </div>
        )
      )}

      {/* Lineage board + queue */}
      <div className="page__grid page__grid--primary">
        <div className="page__col">
          <ErrorBoundary name="LineageBoard">
            <LineageBoard lineages={snapshot?.factory?.lineages} />
          </ErrorBoundary>
        </div>
        <div className="page__col">
          <ErrorBoundary name="QueuePanel">
            <QueuePanel queue={queue} />
          </ErrorBoundary>
        </div>
      </div>
    </div>
  );
}
