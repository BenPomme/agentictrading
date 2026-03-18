import { ErrorBoundary } from '../components/ErrorBoundary';
import { KPIDeck } from '../components/KPIDeck';
import SectionPanel from '../components/SectionPanel';
import { relativeTime } from '../utils/format';
import type { DashboardSnapshot, SnapshotV2 } from '../types/snapshot';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

function snapshotAgeSeconds(generated_at: string | null | undefined): number {
  if (!generated_at) return 0;
  return Math.round((Date.now() - new Date(generated_at).getTime()) / 1000);
}

export function FactoryHealthPage({ snapshot, snapshotV2 }: Props) {
  const readiness = snapshot?.factory?.readiness;
  const runtime = snapshotV2?.runtime;
  const rs = snapshot?.factory?.research_summary;
  const bridge = snapshot?.factory?.execution_bridge;
  const paused = snapshot?.factory_paused ?? false;
  const factoryStatus = snapshot?.factory?.status ?? 'unknown';
  const cycleCount = snapshot?.factory?.cycle_count ?? 0;
  const ageS = snapshotAgeSeconds(snapshot?.generated_at);
  const isStale = ageS > 90;

  // Derive a state: running | paused | error
  const stateKey =
    paused ? 'paused' : factoryStatus === 'error' ? 'error' : 'running';

  const bridgeHealthy =
    bridge != null &&
    bridge.running_portfolio_count >= bridge.desired_portfolio_count;

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Factory Health</h2>
        <p className="page__subtitle">
          Runtime status, readiness, execution bridge, and infrastructure health
        </p>
      </div>

      {/* ── Status bar ── */}
      <div className="fh-status-bar">
        <span className={`fh-state-pill fh-state-pill--${stateKey}`}>
          <span className={`fh-state-dot fh-state-dot--${stateKey}`} />
          {stateKey.toUpperCase()}
        </span>

        <span className="fh-sep" />

        <div className="fh-stat">
          <span className="fh-stat__label">Status</span>
          <span
            className={`fh-stat__value${factoryStatus === 'error' ? ' fh-stat__value--crit' : ''}`}
          >
            {factoryStatus}
          </span>
        </div>

        <span className="fh-sep" />

        <div className="fh-stat">
          <span className="fh-stat__label">Cycles</span>
          <span className="fh-stat__value">{cycleCount}</span>
        </div>

        <span className="fh-sep" />

        <div className="fh-stat">
          <span className="fh-stat__label">Snapshot age</span>
          <span
            className={`fh-stat__value${isStale ? ' fh-stat__value--warn' : ''}`}
          >
            {snapshot?.generated_at ? relativeTime(snapshot.generated_at) : '—'}
          </span>
        </div>

        {runtime && (
          <>
            <span className="fh-sep" />
            <div className="fh-stat">
              <span className="fh-stat__label">Backend</span>
              <span
                className={`fh-stat__value${runtime.backend === 'mobkit' ? ' fh-stat__value--ok' : ''}`}
              >
                {runtime.backend}
              </span>
            </div>
            <span className="fh-sep" />
            <div className="fh-stat">
              <span className="fh-stat__label">Mode</span>
              <span className="fh-stat__value">{runtime.mode}</span>
            </div>
            <span className="fh-sep" />
            <div className="fh-stat">
              <span className="fh-stat__label">Schema</span>
              <span className="fh-stat__value fh-stat__value--ok">
                {snapshotV2?.schema_version ?? 'v1'}
              </span>
            </div>
          </>
        )}
      </div>

      {/* ── Stale warning ── */}
      {isStale && (
        <div className="fh-stale-banner">
          <span>⚠</span>
          <span>
            Snapshot is {ageS}s old — dashboard server may be unresponsive or
            factory loop stopped. Last seen:{' '}
            {snapshot?.generated_at
              ? relativeTime(snapshot.generated_at)
              : 'unknown'}
          </span>
        </div>
      )}

      {/* ── KPI deck ── */}
      <ErrorBoundary name="KPIDeck">
        <KPIDeck
          factory={snapshot?.factory}
          execution={snapshot?.execution}
          ideas={snapshot?.ideas}
        />
      </ErrorBoundary>

      {/* ── Execution bridge health ── */}
      {bridge != null && (
        <SectionPanel
          title="Execution Bridge"
          tag={bridgeHealthy ? 'healthy' : 'gap detected'}
          tagColor={bridgeHealthy ? 'var(--ok)' : 'var(--warn)'}
        >
          <div className="exec-bridge">
            <div
              className={`exec-bridge-card${(bridge.running_portfolio_count ?? 0) < (bridge.desired_portfolio_count ?? 0) ? ' exec-bridge-card--warn' : ' exec-bridge-card--ok'}`}
            >
              <span className="exec-bridge-card__label">Running / Desired</span>
              <span className="exec-bridge-card__value">
                {bridge.running_portfolio_count ?? 0} / {bridge.desired_portfolio_count ?? 0}
              </span>
            </div>
            <div className="exec-bridge-card">
              <span className="exec-bridge-card__label">Runtime mode</span>
              <span className="exec-bridge-card__value">{bridge.runtime_mode ?? '—'}</span>
            </div>
            <div className="exec-bridge-card">
              <span className="exec-bridge-card__label">Auto-start</span>
              <span className="exec-bridge-card__value">
                {bridge.auto_start_enabled ? 'enabled' : 'disabled'}
              </span>
            </div>
            {(bridge.suppressed_portfolio_count ?? 0) > 0 && (
              <div className="exec-bridge-card exec-bridge-card--warn">
                <span className="exec-bridge-card__label">Suppressed</span>
                <span className="exec-bridge-card__value">
                  {bridge.suppressed_portfolio_count}
                </span>
              </div>
            )}
          </div>
          {bridge.suppressed_targets && bridge.suppressed_targets.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {bridge.suppressed_targets.map((t, i) => (
                <div
                  key={i}
                  className="readiness-blockers__item"
                  style={{ color: 'var(--warn)' }}
                >
                  Suppressed: {t.canonical_portfolio_id} (
                  {t.families.join(', ')})
                </div>
              ))}
            </div>
          )}
        </SectionPanel>
      )}

      {/* ── Infrastructure health ── */}
      <SectionPanel
        title="Infrastructure Health"
        collapsible
        defaultCollapsed={false}
      >
        <div className="infra-health-grid">
          <div
            className={`infra-health-card${runtime?.backend === 'mobkit' ? ' infra-health-card--ok' : ''}`}
          >
            <div className="infra-health-card__name">Mobkit / RPC Gateway</div>
            <div
              className={`infra-health-card__status${runtime?.backend === 'mobkit' ? ' infra-health-card__status--ok' : ' infra-health-card__status--unknown'}`}
            >
              {runtime?.backend === 'mobkit' ? '✓ active backend' : '— not primary'}
            </div>
            <div className="infra-health-card__note">
              Deep telemetry (RPC latency, queue depth) not yet in snapshot v2.
              Planned for chunk 4.
            </div>
          </div>

          <div className="infra-health-card">
            <div className="infra-health-card__name">Goldfish Provenance</div>
            <div
              className={`infra-health-card__status infra-health-card__status--${
                (rs?.learning_memory_count ?? 0) > 0 ? 'ok' : 'unknown'
              }`}
            >
              {(rs?.learning_memory_count ?? 0) > 0
                ? `✓ ${rs?.learning_memory_count} memories`
                : '— no memories yet'}
            </div>
            <div className="infra-health-card__note">
              Write health timeline not yet in snapshot v2. Planned for chunk 4.
            </div>
          </div>

          <div className="infra-health-card">
            <div className="infra-health-card__name">Paper Holdoff</div>
            <div
              className={`infra-health-card__status infra-health-card__status--${
                runtime?.paper_holdoff_enabled ? 'ok' : 'warn'
              }`}
            >
              {runtime?.paper_holdoff_enabled
                ? '✓ enabled (churn protection on)'
                : '⚠ disabled (paper models may churn)'}
            </div>
            <div className="infra-health-card__note">
              Paper-stage lineages with healthy status are held off from
              agentic mutation and critique loops.
            </div>
          </div>

          <div className="infra-health-card">
            <div className="infra-health-card__name">Live Trading Guard</div>
            <div className="infra-health-card__status infra-health-card__status--ok">
              ✓ hard-disabled
            </div>
            <div className="infra-health-card__note">
              FACTORY_ENABLE_LIVE_TRADING=false,
              FACTORY_LIVE_TRADING_HARD_DISABLE=true enforced by paper window
              script.
            </div>
          </div>
        </div>
      </SectionPanel>

      {/* ── Readiness checks ── */}
      {readiness && (
        <SectionPanel
          title="Readiness Checks"
          tag={
            readiness.score_pct != null
              ? `${Math.round(readiness.score_pct)}%`
              : undefined
          }
          tagColor={
            readiness.score_pct >= 80
              ? 'var(--ok)'
              : readiness.score_pct >= 50
              ? 'var(--warn)'
              : 'var(--crit)'
          }
        >
          <div className="readiness-checks">
            {(readiness.checks ?? []).map((check, i) => (
              <div
                key={i}
                className={`readiness-check${check.ok ? ' readiness-check--ok' : ' readiness-check--fail'}`}
              >
                <span className="readiness-check__status">{check.ok ? '✓' : '✗'}</span>
                <span className="readiness-check__name">{check.name}</span>
                {!check.ok && (
                  <span className="readiness-check__reason">{check.reason}</span>
                )}
              </div>
            ))}
          </div>
          {(readiness.blockers ?? []).length > 0 && (
            <div className="readiness-blockers">
              <div className="readiness-blockers__title">
                {readiness.blockers.length} Blocker
                {readiness.blockers.length !== 1 ? 's' : ''}
              </div>
              {readiness.blockers.map((b, i) => (
                <div key={i} className="readiness-blockers__item">{b}</div>
              ))}
            </div>
          )}
          {(readiness.warnings ?? []).length > 0 && (
            <div
              className="readiness-blockers"
              style={{
                borderLeftColor: 'var(--warn)',
                background: 'rgba(255,176,0,0.04)',
                marginTop: 8,
              }}
            >
              <div
                className="readiness-blockers__title"
                style={{ color: 'var(--warn)' }}
              >
                {readiness.warnings.length} Warning
                {readiness.warnings.length !== 1 ? 's' : ''}
              </div>
              {readiness.warnings.map((w, i) => (
                <div key={i} className="readiness-blockers__item">{w}</div>
              ))}
            </div>
          )}
        </SectionPanel>
      )}

      {/* ── Research summary ── */}
      {rs && (
        <SectionPanel title="Research Summary" collapsible defaultCollapsed>
          <div className="page__runtime-strip" style={{ flexWrap: 'wrap', gap: 16 }}>
            {(
              [
                ['Active Lineages', rs.active_lineage_count],
                ['Families', rs.family_count],
                ['Positive Models', rs.positive_model_count],
                ['Learning Memories', rs.learning_memory_count],
                ['Escalations', rs.operator_escalation_count],
                [
                  'Paper PnL',
                  rs.paper_pnl != null ? `€${rs.paper_pnl.toFixed(0)}` : '—',
                ],
                ['Ready for Canary', rs.ready_for_canary],
              ] as [string, number | string][]
            ).map(([label, val]) => (
              <span key={label} className="runtime-pill">
                <span className="runtime-pill__label">{label}</span>
                <span className="runtime-pill__value">{String(val ?? '—')}</span>
              </span>
            ))}
          </div>
        </SectionPanel>
      )}
    </div>
  );
}
