import React, { useMemo, useState, useEffect } from 'react';
import type { Family, Lineage, PortfolioSnapshot } from '../types/snapshot';
import {
  venueIcon,
  formatNumber,
  formatPnl,
  formatPct,
  relativeTime,
  statusColor,
} from '../utils/format';
import { PnlChart } from './PnlChart';
import './PortfolioGrid.css';

function backtestBadge(lineage: Lineage | null | undefined): React.ReactNode {
  if (!lineage) return null;
  const bt = lineage.backtest_roi_pct;
  if (bt == null) return <span className="pg-bt-badge pg-bt-badge--none">No Backtest</span>;
  const isPositive = bt > 0;
  const cls = isPositive ? 'pg-bt-badge--pos' : 'pg-bt-badge--neg';
  return (
    <span className={`pg-bt-badge ${cls}`}>
      BT: {bt > 0 ? '+' : ''}{bt.toFixed(1)}%
      {lineage.backtest_sharpe != null && ` | SR ${lineage.backtest_sharpe.toFixed(2)}`}
    </span>
  );
}

interface PortfolioGridProps {
  portfolios: PortfolioSnapshot[] | undefined;
  placeholders: PortfolioSnapshot[] | undefined;
  lineages?: Lineage[];
  families?: Family[];
}

function pulseClass(status: string): string {
  switch (status) {
    case 'healthy':
    case 'ok':
      return 'pg-card__dot--ok';
    case 'warning':
    case 'degraded':
    case 'stale':
      return 'pg-card__dot--warn';
    case 'critical':
    case 'error':
      return 'pg-card__dot--crit';
    default:
      return 'pg-card__dot--dim';
  }
}

function venueLabel(portfolio: PortfolioSnapshot): string {
  const fam = portfolio.candidate_families?.[0] ?? '';
  if (fam.includes('binance')) return 'binance';
  if (fam.includes('betfair')) return 'betfair';
  if (fam.includes('polymarket')) return 'polymarket';
  return 'generic';
}

export function PortfolioGrid({ portfolios, placeholders, lineages, families }: PortfolioGridProps) {
  const lineageById = useMemo(() => {
    const m = new Map<string, Lineage>();
    for (const l of lineages ?? []) m.set(l.lineage_id, l);
    return m;
  }, [lineages]);
  const familyById = useMemo(() => {
    const m = new Map<string, Family>();
    for (const f of families ?? []) m.set(f.family_id, f);
    return m;
  }, [families]);
  function resolveLineageForPortfolio(p: PortfolioSnapshot): Lineage | null {
    const famId = p.candidate_families?.[0];
    if (!famId) return null;
    const fam = familyById.get(famId);
    const lid = fam?.champion_lineage_id;
    if (!lid) return null;
    return lineageById.get(lid) ?? null;
  }
  const sorted = useMemo(() => {
    const all = [...(portfolios ?? []), ...(placeholders ?? [])];
    return all.sort((a, b) => {
      if (a.running !== b.running) return a.running ? -1 : 1;
      const aHb = a.heartbeat_ts ? new Date(a.heartbeat_ts).getTime() : 0;
      const bHb = b.heartbeat_ts ? new Date(b.heartbeat_ts).getTime() : 0;
      return bHb - aHb;
    });
  }, [portfolios, placeholders]);

  const firstActiveId = useMemo(() => {
    const active = sorted.find(p => p.running);
    return active?.portfolio_id ?? null;
  }, [sorted]);

  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (firstActiveId && expandedIds.size === 0) {
      setExpandedIds(new Set([firstActiveId]));
    }
    // only on first mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstActiveId]);

  function toggleChart(id: string) {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (sorted.length === 0) {
    return (
      <section className="pg">
        <h2 className="pg__title">Portfolios</h2>
        <div className="pg__empty">No portfolios configured</div>
      </section>
    );
  }

  return (
    <section className="pg">
      <h2 className="pg__title">Portfolios</h2>
      <div className="pg__grid">
        {sorted.map(p => {
          const expanded = expandedIds.has(p.portfolio_id);
          return (
            <div key={p.portfolio_id} className={`pg-card ${p.is_placeholder ? 'pg-card--placeholder' : ''}`}>
              <div className="pg-card__header">
                <span className="pg-card__venue" title={venueLabel(p)}>
                  {venueIcon(venueLabel(p))}
                </span>
                <span className="pg-card__label">{p.label}</span>
                <span className={`pg-card__dot ${pulseClass(p.execution_health_status)}`} />
              </div>

              <div className="pg-card__status-line">
                <span
                  className="pg-card__badge"
                  style={{
                    color: p.running ? 'var(--ok)' : 'var(--text-muted)',
                    background: p.running ? 'var(--ok-dim)' : 'var(--surface-alt)',
                  }}
                >
                  {p.running ? 'RUNNING' : 'STOPPED'}
                </span>
                {p.heartbeat_ts && (
                  <span className="pg-card__heartbeat">♡ {relativeTime(p.heartbeat_ts)}</span>
                )}
                <span className="pg-card__display-status">{p.display_status}</span>
              </div>

              <div className="pg-card__metrics">
                <div className="pg-card__metric">
                  <span className="pg-card__metric-label">Balance</span>
                  <span className="pg-card__metric-value">{formatNumber(p.current_balance, 2)}</span>
                </div>
                <div className="pg-card__metric">
                  <span className="pg-card__metric-label">P&L</span>
                  <span
                    className="pg-card__metric-value"
                    style={{ color: p.realized_pnl >= 0 ? 'var(--ok)' : 'var(--crit)' }}
                  >
                    {formatPnl(p.realized_pnl)}
                  </span>
                </div>
                <div className="pg-card__metric">
                  <span className="pg-card__metric-label">ROI</span>
                  <span
                    className="pg-card__metric-value"
                    style={{ color: p.roi_pct >= 0 ? 'var(--ok)' : 'var(--crit)' }}
                  >
                    {formatPct(p.roi_pct)}
                  </span>
                </div>
                <div className="pg-card__metric">
                  <span className="pg-card__metric-label">Trades</span>
                  <span className="pg-card__metric-value">{p.trade_count}</span>
                </div>
              </div>
              {(() => {
                const badge = backtestBadge(resolveLineageForPortfolio(p));
                return badge ? <div className="pg-card__bt-row">{badge}</div> : null;
              })()}

              {p.error && (
                <div className="pg-card__error">{p.error}</div>
              )}

              <button
                className="pg-card__chart-toggle"
                onClick={() => toggleChart(p.portfolio_id)}
                style={{ color: statusColor(p.execution_health_status) }}
              >
                {expanded ? '▾ Hide P&L' : '▸ View P&L'}
              </button>

              {expanded && (
                <div className="pg-card__chart-container">
                  <PnlChart portfolioId={p.portfolio_id} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
