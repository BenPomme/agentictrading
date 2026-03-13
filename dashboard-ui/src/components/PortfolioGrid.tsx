import { useMemo, useState, useEffect } from 'react';
import type { PortfolioSnapshot } from '../types/snapshot';
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

interface PortfolioGridProps {
  portfolios: PortfolioSnapshot[] | undefined;
  placeholders: PortfolioSnapshot[] | undefined;
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

export function PortfolioGrid({ portfolios, placeholders }: PortfolioGridProps) {
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
