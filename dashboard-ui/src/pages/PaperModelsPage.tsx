import { useMemo, useState } from 'react';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { PnlChart } from '../components/PnlChart';
import SectionPanel from '../components/SectionPanel';
import type { DashboardSnapshot, SnapshotV2 } from '../types/snapshot';
import { formatPnl, relativeTime } from '../utils/format';
import { mergePaperModels, type MergedPaperModel } from '../utils/dashboard';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

interface SummaryCard {
  label: string;
  value: number | string;
  tone?: 'warn' | 'crit' | 'accent';
}

function buildSummary(rows: MergedPaperModel[]): SummaryCard[] {
  const readyRows = rows.filter((row) => row.state_bucket === 'promotion-ready');
  const issueRows = rows.filter((row) =>
    ['blocked', 'holdoff', 'scope-blocked'].includes(row.state_bucket),
  );
  const zeroTradeRows = rows.filter((row) => (row.port_trade_count ?? row.trade_count) === 0);
  const bestPnl = rows.reduce<number | null>((best, row) => {
    if (row.realized_pnl == null) return best;
    if (best == null || row.realized_pnl > best) return row.realized_pnl;
    return best;
  }, null);

  return [
    { label: 'Running', value: rows.filter((row) => row.paper_runtime_status === 'paper_running').length },
    { label: 'Promotion-ready', value: readyRows.length, tone: 'accent' },
    { label: 'Holdoff', value: rows.filter((row) => row.state_bucket === 'holdoff').length, tone: 'warn' },
    { label: 'Blocked', value: issueRows.length, tone: issueRows.length > 0 ? 'crit' : undefined },
    { label: 'Zero-trade', value: zeroTradeRows.length, tone: zeroTradeRows.length > 0 ? 'warn' : undefined },
    { label: 'Best P&L', value: bestPnl == null ? '—' : formatPnl(bestPnl), tone: bestPnl != null && bestPnl > 0 ? 'accent' : undefined },
  ];
}

function detailStateLabel(row: MergedPaperModel): string {
  switch (row.state_bucket) {
    case 'promotion-ready':
      return 'Promotion-ready';
    case 'accumulating':
      return 'Accumulating evidence';
    case 'blocked':
      return 'Blocked';
    case 'holdoff':
      return 'Holdoff';
    case 'scope-blocked':
      return 'Scope-blocked';
    case 'underperforming':
      return 'Underperforming';
  }
}

function DetailTable({ rows }: { rows: MergedPaperModel[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (rows.length === 0) {
    return <div className="alert-empty">No paper or shadow lineages available.</div>;
  }

  return (
    <div className="paper-detail-table">
      <div className="paper-detail-table__header">
        <span>Lineage</span>
        <span>State</span>
        <span>Portfolio</span>
        <span>Checkpoint</span>
        <span>Trades</span>
        <span>P&amp;L</span>
        <span />
      </div>
      {rows.map((row) => {
        const expanded = expandedId === row.lineage_id;
        const issues = [
          ...(row.det_blockers.map((blocker) => blocker.description)),
          ...row.blockers,
          ...(row.holdoff_reason ? [row.holdoff_reason] : []),
          ...(row.venue_scope_reason ? [row.venue_scope_reason] : []),
        ];

        return (
          <div key={row.lineage_id} className="paper-detail-table__row">
            <div className="paper-detail-table__cells">
              <div>
                <div className="paper-detail-table__family">{row.family_id}</div>
                <div className="paper-detail-table__lineage">{row.lineage_id}</div>
              </div>
              <span className={`paper-monitor-card__bucket paper-monitor-card__bucket--${row.state_bucket}`}>
                {detailStateLabel(row)}
              </span>
              <span className="paper-detail-table__muted">{row.paper_portfolio_id ?? '—'}</span>
              <span className="paper-detail-table__muted">{row.checkpoint_label}</span>
              <span>{row.port_trade_count ?? row.trade_count}</span>
              <span className={(row.realized_pnl ?? 0) < 0 ? 'pc-cell--crit' : 'pc-cell--ok'}>
                {row.realized_pnl == null ? '—' : formatPnl(row.realized_pnl)}
              </span>
              <button
                type="button"
                className={`pc-expand-btn${expanded ? ' pc-expand-btn--open' : ''}`}
                onClick={() =>
                  setExpandedId((current) =>
                    current === row.lineage_id ? null : row.lineage_id,
                  )
                }
              >
                {expanded ? 'Hide' : 'View'}
              </button>
            </div>
            {expanded && (
              <div className="gate-drilldown">
                <div className="gate-drilldown__title">Inspection</div>
                <div className="paper-monitor-card__meta">
                  <span className="paper-monitor-card__meta-pill">
                    Progress {Math.round(row.progress_pct)}%
                  </span>
                  <span className="paper-monitor-card__meta-pill">
                    Health {row.execution_health_status ?? 'unknown'}
                  </span>
                  <span className="paper-monitor-card__meta-pill">
                    Days {row.paper_days}
                  </span>
                  <span className="paper-monitor-card__meta-pill">
                    Last trade {row.recent_trades?.[0]?.closed_at ? relativeTime(String(row.recent_trades[0].closed_at)) : '—'}
                  </span>
                </div>
                {issues.length > 0 ? (
                  <div className="paper-monitor-card__issues">
                    {issues.map((issue, index) => (
                      <span key={`${row.lineage_id}-${index}`} className="paper-monitor-card__issue">
                        {issue}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="paper-detail-table__muted">No active blockers.</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FeaturedCard({ row }: { row: MergedPaperModel }) {
  const issues = [
    ...(row.det_blockers.map((blocker) => blocker.description)),
    ...row.blockers,
    ...(row.holdoff_reason ? [row.holdoff_reason] : []),
    ...(row.venue_scope_reason ? [row.venue_scope_reason] : []),
  ];

  return (
    <article className="paper-monitor-card">
      <div className="paper-monitor-card__header">
        <div>
          <div className="paper-monitor-card__eyebrow">{row.family_id}</div>
          <h3 className="paper-monitor-card__title">{row.lineage_id}</h3>
        </div>
        <span className={`paper-monitor-card__bucket paper-monitor-card__bucket--${row.state_bucket}`}>
          {detailStateLabel(row)}
        </span>
      </div>

      <div className="paper-monitor-card__stats">
        <div>
          <span className="paper-monitor-card__label">Checkpoint</span>
          <span className="paper-monitor-card__value">{row.checkpoint_label}</span>
        </div>
        <div>
          <span className="paper-monitor-card__label">Trades</span>
          <span className="paper-monitor-card__value">{row.port_trade_count ?? row.trade_count}</span>
        </div>
        <div>
          <span className="paper-monitor-card__label">P&amp;L</span>
          <span className={`paper-monitor-card__value${(row.realized_pnl ?? 0) < 0 ? ' paper-monitor-card__value--negative' : ''}`}>
            {row.realized_pnl == null ? '—' : formatPnl(row.realized_pnl)}
          </span>
        </div>
        <div>
          <span className="paper-monitor-card__label">Health</span>
          <span className="paper-monitor-card__value">{row.execution_health_status ?? 'unknown'}</span>
        </div>
        <div>
          <span className="paper-monitor-card__label">Portfolio</span>
          <span className="paper-monitor-card__value">{row.paper_portfolio_id ?? '—'}</span>
        </div>
      </div>

      <div className="paper-monitor-card__progress">
        <span
          className="paper-monitor-card__progress-bar"
          style={{ width: `${row.progress_pct}%` }}
        />
      </div>

      {row.paper_portfolio_id ? (
        <div className="paper-monitor-card__chart-shell">
          <PnlChart
            portfolioId={row.paper_portfolio_id}
            variant="hero"
            statusTone={row.state_bucket}
            checkpoint={row.checkpoint_label}
            healthStatus={row.execution_health_status ?? undefined}
          />
        </div>
      ) : (
        <div className="paper-monitor-card__empty">No chartable portfolio assigned.</div>
      )}

      {issues.length > 0 && (
        <div className="paper-monitor-card__issues">
          {issues.slice(0, 3).map((issue, index) => (
            <span key={`${row.lineage_id}-issue-${index}`} className="paper-monitor-card__issue">
              {issue}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export function PaperModelsPage({ snapshot, snapshotV2 }: Props) {
  const pr = snapshot?.factory?.paper_runtime;
  const portfolios = [
    ...(snapshot?.execution?.portfolios ?? []),
    ...(snapshot?.execution?.placeholders ?? []),
  ];
  const archivedPortfolios = snapshot?.execution?.archived_portfolios ?? [];

  const rows = useMemo(
    () =>
      mergePaperModels(
        snapshot?.factory?.lineages ?? [],
        snapshotV2?.lineage_v2 ?? [],
        portfolios,
      ),
    [snapshot, snapshotV2, portfolios],
  );

  const featuredRows = rows.slice(0, 3);
  const attentionRows = rows.filter((row) =>
    ['blocked', 'holdoff', 'scope-blocked', 'underperforming'].includes(row.state_bucket),
  );
  const scopeBlockedRows = rows.filter((row) => row.state_bucket === 'scope-blocked');
  const summaryCards = buildSummary(rows);

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Paper / Shadow</h2>
        <p className="page__subtitle">
          Live monitoring for paper models, promotion readiness, and execution risk
        </p>
      </div>

      <div className="paper-monitor-summary">
        {summaryCards.map((card) => (
          <div
            key={card.label}
            className={`paper-monitor-summary__card${card.tone ? ` paper-monitor-summary__card--${card.tone}` : ''}`}
          >
            <span className="paper-monitor-summary__label">{card.label}</span>
            <span className="paper-monitor-summary__value">{card.value}</span>
          </div>
        ))}
      </div>

      {pr && (
        <div className="page__runtime-strip">
          {[
            ['Running', pr.running_count],
            ['Expected', pr.expected_count],
            ['Blocked', rows.filter((row) => row.state_bucket === 'blocked').length],
            ['Holdoff', rows.filter((row) => row.state_bucket === 'holdoff').length],
            ['Scope blocked', rows.filter((row) => row.state_bucket === 'scope-blocked').length],
            ['Archived portfolios', archivedPortfolios.length],
          ].map(([label, value]) => (
            <span key={String(label)} className="runtime-pill">
              <span className="runtime-pill__label">{label}</span>
              <span className="runtime-pill__value">{String(value)}</span>
            </span>
          ))}
        </div>
      )}

      <SectionPanel
        title="Featured Monitors"
        count={featuredRows.length}
        tag={`${rows.filter((row) => row.state_bucket === 'promotion-ready').length} promotion-ready`}
        tagColor="var(--accent-strong)"
      >
        <ErrorBoundary name="PaperFeaturedCards">
          <div className="paper-monitor-grid">
            {featuredRows.map((row) => (
              <FeaturedCard key={row.lineage_id} row={row} />
            ))}
          </div>
        </ErrorBoundary>
      </SectionPanel>

      <div className="page__grid page__grid--2col">
        <SectionPanel
          title="Needs Attention"
          count={attentionRows.length}
          tag="review"
          tagColor="var(--warn)"
        >
          {attentionRows.length === 0 ? (
            <div className="alert-empty">No paper models currently need operator attention.</div>
          ) : (
            <div className="paper-attention-list">
              {attentionRows.slice(0, 6).map((row) => (
                <div key={row.lineage_id} className="paper-attention-list__item">
                  <div>
                    <div className="paper-attention-list__title">{row.family_id}</div>
                    <div className="paper-attention-list__detail">{detailStateLabel(row)}</div>
                  </div>
                  <div className="paper-attention-list__detail">{row.checkpoint_label}</div>
                </div>
              ))}
            </div>
          )}
        </SectionPanel>

        <SectionPanel
          title="Scope Blocked"
          count={scopeBlockedRows.length}
          tag={scopeBlockedRows.length > 0 ? 'excluded' : 'clear'}
          tagColor={scopeBlockedRows.length > 0 ? 'var(--crit)' : 'var(--ok)'}
        >
          {scopeBlockedRows.length === 0 ? (
            <div className="alert-empty">No scope-blocked lineages.</div>
          ) : (
            <div className="paper-attention-list">
              {scopeBlockedRows.map((row) => (
                <div key={row.lineage_id} className="paper-attention-list__item">
                  <div>
                    <div className="paper-attention-list__title">{row.family_id}</div>
                    <div className="paper-attention-list__detail">{row.lineage_id}</div>
                  </div>
                  <div className="paper-attention-list__detail">{row.venue_scope_reason}</div>
                </div>
              ))}
            </div>
          )}
        </SectionPanel>
      </div>

      <SectionPanel
        title="Detailed Monitor Table"
        count={rows.length}
        collapsible
        defaultCollapsed={false}
      >
        <ErrorBoundary name="PaperDetailTable">
          <DetailTable rows={rows} />
        </ErrorBoundary>
      </SectionPanel>
    </div>
  );
}
