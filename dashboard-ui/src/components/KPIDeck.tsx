import React from 'react';
import type { FactoryState, ExecutionState, IdeasState } from '../types/snapshot';
import { formatPnl } from '../utils/format';
import './KPIDeck.css';

interface KPIDeckProps {
  factory: FactoryState | undefined;
  execution: ExecutionState | undefined;
  ideas: IdeasState | undefined;
}

interface CardDef {
  label: string;
  value: string;
  accent?: 'ok' | 'warn' | 'crit';
  valueClass?: string;
  subtitle?: string;
}

function resolveCards(
  factory: FactoryState | undefined,
  execution: ExecutionState | undefined,
  ideas: IdeasState | undefined,
): CardDef[] {
  const dash = '—';

  const readiness = factory?.readiness;
  const readinessAccent = readiness
    ? readiness.status === 'healthy' || readiness.status === 'ok'
      ? 'ok'
      : readiness.status === 'warning'
        ? 'warn'
        : readiness.status === 'critical'
          ? 'crit'
          : undefined
    : undefined;

  const currentPaperPnl =
    execution?.current_paper_pnl ??
    execution?.realized_pnl_total ??
    factory?.research_summary?.paper_pnl ??
    null;
  const paperPnl = currentPaperPnl;
  const paperPnlAccent = paperPnl != null ? (paperPnl >= 0 ? 'ok' : 'crit') : undefined;
  const paperPnlClass =
    paperPnl != null ? (paperPnl >= 0 ? 'kpi__value--positive' : 'kpi__value--negative') : undefined;
  const historicalPnl = execution?.historical_realized_pnl_total;

  return [
    {
      label: 'READINESS',
      value: readiness ? `${readiness.score_pct}%` : dash,
      accent: readinessAccent,
    },
    {
      label: 'LINEAGES',
      value: factory
        ? `${factory.research_summary.active_lineage_count}/${factory.research_summary.lineage_count}`
        : dash,
    },
    {
      label: 'PAPER RUNTIME',
      value: factory
        ? `${factory.paper_runtime.running_count}/${factory.paper_runtime.expected_count}`
        : dash,
    },
    {
      label: 'AGENT RUNS',
      value: factory ? `${(factory.agent_runs ?? []).length}` : dash,
      subtitle: '24h',
    },
    {
      label: 'PAPER P&L',
      value: paperPnl != null ? `$${formatPnl(paperPnl)}` : dash,
      accent: paperPnlAccent,
      valueClass: paperPnlClass,
      subtitle:
        historicalPnl != null
          ? `history $${formatPnl(historicalPnl)}`
          : undefined,
    },
    {
      label: 'PORTFOLIOS',
      value: execution
        ? `${execution.running_count}/${execution.portfolio_count}`
        : dash,
      subtitle:
        execution?.archived_portfolio_count != null
          ? `${execution.archived_portfolio_count} archived`
          : undefined,
    },
    {
      label: 'IDEAS',
      value: ideas
        ? `${ideas.active_count}+${ideas.archived_count}`
        : dash,
      subtitle: ideas ? 'active + processed' : undefined,
    },
    {
      label: 'QUEUE',
      value: factory ? `${(factory.queue ?? []).length}` : dash,
      subtitle:
        factory?.archived_queue != null
          ? `${factory.archived_queue.length} archived`
          : undefined,
    },
  ];
}

function cardAccentClass(accent?: string): string {
  if (accent === 'ok') return 'kpi__card kpi__card--ok';
  if (accent === 'warn') return 'kpi__card kpi__card--warn';
  if (accent === 'crit') return 'kpi__card kpi__card--crit';
  return 'kpi__card';
}

function renderValue(raw: string): React.ReactNode {
  const slashIdx = raw.indexOf('/');
  if (slashIdx > 0 && slashIdx < raw.length - 1) {
    return (
      <>
        {raw.slice(0, slashIdx)}
        <span className="kpi__value-denom">/{raw.slice(slashIdx + 1)}</span>
      </>
    );
  }

  const plusIdx = raw.indexOf('+');
  if (plusIdx > 0 && plusIdx < raw.length - 1 && !raw.startsWith('$+') && !raw.startsWith('+')) {
    return (
      <>
        {raw.slice(0, plusIdx)}
        <span className="kpi__value-denom">+{raw.slice(plusIdx + 1)}</span>
      </>
    );
  }

  return raw;
}

export const KPIDeck: React.FC<KPIDeckProps> = ({ factory, execution, ideas }) => {
  const cards = resolveCards(factory, execution, ideas);

  return (
    <section className="kpi">
      {cards.map((c) => (
        <div key={c.label} className={cardAccentClass(c.accent)}>
          <span className={`kpi__value ${c.valueClass ?? ''}`}>
            {c.value === '—' ? <span className="kpi__empty">—</span> : renderValue(c.value)}
          </span>
          <span className="kpi__label">{c.label}</span>
          {c.subtitle && <span className="kpi__subtitle">{c.subtitle}</span>}
        </div>
      ))}
    </section>
  );
};
