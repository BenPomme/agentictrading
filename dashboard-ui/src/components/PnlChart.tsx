import { useEffect, useMemo, useState, useRef } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Filler,
} from 'chart.js';
import 'chartjs-adapter-date-fns';
import type { ChartPayload } from '../types/snapshot';

ChartJS.register(LineElement, PointElement, LinearScale, TimeScale, Tooltip, Filler);

interface PnlChartProps {
  portfolioId: string;
  variant?: 'compact' | 'hero';
  checkpoint?: string;
  healthStatus?: string;
  statusTone?: string;
}

function readCssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function PnlChart({
  portfolioId,
  variant = 'compact',
  checkpoint,
  healthStatus,
  statusTone,
}: PnlChartProps) {
  const [data, setData] = useState<ChartPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<ChartJS<'line'> | null>(null);
  const colors = useMemo(
    () => ({
      border: readCssVar('--border', '#dbe2ea'),
      textMuted: readCssVar('--text-muted', '#64748b'),
      surfaceAlt: readCssVar('--surface-alt', '#f1f5f9'),
      text: readCssVar('--text', '#0f172a'),
      ok: readCssVar('--ok', '#0f9f7a'),
      crit: readCssVar('--crit', '#dc2626'),
      info: readCssVar('--info', '#2563eb'),
      accent: readCssVar('--accent', '#6366f1'),
    }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setData(null);

    fetch(`/api/portfolio/${encodeURIComponent(portfolioId)}/chart`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((payload: ChartPayload) => {
        if (!cancelled) setData(payload);
      })
      .catch(err => {
        if (!cancelled) setError(err.message);
      });

    return () => { cancelled = true; };
  }, [portfolioId]);

  if (error) {
    return (
      <div className="pnl-chart__error">
        {error.includes('404')
          ? 'No portfolio history has been materialized for this model yet.'
          : `Chart unavailable: ${error}`}
      </div>
    );
  }
  if (!data) {
    return <div className="pnl-chart__loading">Loading chart…</div>;
  }

  const balancePoints = (data.points ?? data.balance_points ?? []).map((p: { ts: string; balance: number }) => ({
    x: new Date(p.ts).getTime(),
    y: p.balance,
  }));

  const trades = data.trades ?? [];

  const opens = trades
    .filter(t => t.kind === 'trade_opened')
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));

  const closes = trades.filter(t => t.kind === 'trade_closed');
  const winCloses = closes
    .filter(t => (t.pnl ?? 0) >= 0)
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));
  const lossCloses = closes
    .filter(t => (t.pnl ?? 0) < 0)
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));

  const hasTrades = trades.length > 0;
  const hasHistory = balancePoints.length > 1;

  const chartData = {
    datasets: [
      {
        label: 'Balance',
        data: balancePoints,
        borderColor: colors.accent,
        backgroundColor: 'rgba(99, 102, 241, 0.08)',
        borderWidth: variant === 'hero' ? 2.5 : 1.75,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 6,
      },
      {
        label: 'Trade Open',
        data: opens,
        showLine: false,
        pointRadius: variant === 'hero' ? 4.5 : 3.5,
        pointBackgroundColor: colors.info,
        pointBorderColor: `${colors.info}88`,
        pointBorderWidth: 1,
      },
      {
        label: 'Win Close',
        data: winCloses,
        showLine: false,
        pointRadius: variant === 'hero' ? 4.5 : 3.5,
        pointBackgroundColor: colors.ok,
        pointBorderColor: `${colors.ok}88`,
        pointBorderWidth: 1,
      },
      {
        label: 'Loss Close',
        data: lossCloses,
        showLine: false,
        pointRadius: variant === 'hero' ? 4.5 : 3.5,
        pointBackgroundColor: colors.crit,
        pointBorderColor: `${colors.crit}88`,
        pointBorderWidth: 1,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'nearest' as const, intersect: false },
    scales: {
      x: {
        type: 'time' as const,
        grid: { color: colors.border, lineWidth: 0.5 },
        ticks: {
          color: colors.textMuted,
          font: { family: 'JetBrains Mono', size: 10 },
          maxTicksLimit: 6,
        },
        border: { color: colors.border },
      },
      y: {
        grid: { color: colors.border, lineWidth: 0.5 },
        ticks: {
          color: colors.textMuted,
          font: { family: 'JetBrains Mono', size: 10 },
          callback: (v: string | number) => {
            const n = typeof v === 'string' ? parseFloat(v) : v;
            return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : n.toString();
          },
        },
        border: { color: colors.border },
      },
    },
    plugins: {
      legend: {
        display: variant === 'hero',
        labels: {
          color: colors.textMuted,
          boxWidth: 10,
          font: { family: 'JetBrains Mono', size: 10 },
        },
      },
      tooltip: {
        backgroundColor: colors.surfaceAlt,
        titleFont: { family: 'JetBrains Mono', size: 11 },
        bodyFont: { family: 'JetBrains Mono', size: 11 },
        titleColor: colors.text,
        bodyColor: colors.text,
        borderColor: colors.border,
        borderWidth: 1,
        cornerRadius: 8,
        padding: 8,
      },
    },
  };

  return (
    <div className={`pnl-chart pnl-chart--${variant}`}>
      {variant === 'hero' && (
        <div className="pnl-chart__header">
          <div className="pnl-chart__headline">Performance trace</div>
          <div className="pnl-chart__meta">
            {checkpoint ? <span className="pnl-chart__meta-pill">{checkpoint}</span> : null}
            {healthStatus ? <span className="pnl-chart__meta-pill">Health {healthStatus}</span> : null}
            {statusTone ? <span className="pnl-chart__meta-pill">{statusTone.replace('-', ' ')}</span> : null}
          </div>
        </div>
      )}
      {!hasHistory && (
        <div className="pnl-chart__overlay">
          Balance history is still sparse. The chart will gain shape once the runner records more checkpoints.
        </div>
      )}
      {hasHistory && !hasTrades && (
        <div className="pnl-chart__overlay pnl-chart__overlay--soft">
          Balance history is present but no trades have closed yet.
        </div>
      )}
      <Line ref={chartRef} data={chartData} options={options} />
    </div>
  );
}

function findBalanceAt(data: ChartPayload, ts: string): number {
  const t = new Date(ts).getTime();
  let closest = data.starting_balance;
  for (const p of (data.points ?? data.balance_points ?? [])) {
    if (new Date(p.ts).getTime() <= t) closest = p.balance;
    else break;
  }
  return closest;
}
