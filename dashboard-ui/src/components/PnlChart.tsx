import { useEffect, useState, useRef } from 'react';
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
}

export function PnlChart({ portfolioId }: PnlChartProps) {
  const [data, setData] = useState<ChartPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<ChartJS<'line'> | null>(null);

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
    return <div className="pnl-chart__error">Chart unavailable: {error}</div>;
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
    .filter(t => t.kind === 'open')
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));

  const closes = trades.filter(t => t.kind === 'close');
  const winCloses = closes
    .filter(t => (t.pnl ?? 0) >= 0)
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));
  const lossCloses = closes
    .filter(t => (t.pnl ?? 0) < 0)
    .map(t => ({ x: new Date(t.ts).getTime(), y: findBalanceAt(data, t.ts) }));

  const chartData = {
    datasets: [
      {
        label: 'Balance',
        data: balancePoints,
        borderColor: '#00ffd0',
        backgroundColor: 'rgba(0, 255, 208, 0.08)',
        borderWidth: 1.5,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 6,
      },
      {
        label: 'Trade Open',
        data: opens,
        showLine: false,
        pointRadius: 4,
        pointBackgroundColor: '#00ffd0',
        pointBorderColor: '#00ffd088',
        pointBorderWidth: 1,
      },
      {
        label: 'Win Close',
        data: winCloses,
        showLine: false,
        pointRadius: 4,
        pointBackgroundColor: '#4ade80',
        pointBorderColor: '#4ade8088',
        pointBorderWidth: 1,
      },
      {
        label: 'Loss Close',
        data: lossCloses,
        showLine: false,
        pointRadius: 4,
        pointBackgroundColor: '#ff3b3b',
        pointBorderColor: '#ff3b3b88',
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
        grid: { color: 'var(--border)', lineWidth: 0.5 },
        ticks: {
          color: 'var(--text-muted)',
          font: { family: 'JetBrains Mono', size: 10 },
          maxTicksLimit: 6,
        },
        border: { color: 'var(--border)' },
      },
      y: {
        grid: { color: 'var(--border)', lineWidth: 0.5 },
        ticks: {
          color: 'var(--text-muted)',
          font: { family: 'JetBrains Mono', size: 10 },
          callback: (v: string | number) => {
            const n = typeof v === 'string' ? parseFloat(v) : v;
            return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : n.toString();
          },
        },
        border: { color: 'var(--border)' },
      },
    },
    plugins: {
      tooltip: {
        backgroundColor: '#1a2332ee',
        titleFont: { family: 'JetBrains Mono', size: 11 },
        bodyFont: { family: 'JetBrains Mono', size: 11 },
        borderColor: '#1e293b',
        borderWidth: 1,
        cornerRadius: 4,
        padding: 8,
      },
    },
  };

  return (
    <div className="pnl-chart">
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
