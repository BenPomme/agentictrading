import { useMemo } from 'react';
import type { AgentRun } from '../types/snapshot';
import { taskTypeLabel, providerColor } from '../utils/format';
import './AgentActivityPanel.css';

interface AgentActivityPanelProps {
  agentRuns: AgentRun[] | undefined;
}

const FAMILY_COLORS: Record<string, string> = {
  binance_funding: '#4a9eff',
  betfair_prediction: '#818cf8',
  cascade_regime: '#00ffd0',
  polymarket: '#ffb000',
};

function familyColor(familyId: string): string {
  for (const [key, color] of Object.entries(FAMILY_COLORS)) {
    if (familyId.includes(key)) return color;
  }
  const hash = [...familyId].reduce((a, c) => a + c.charCodeAt(0), 0);
  const hues = ['#4a9eff', '#818cf8', '#00ffd0', '#ffb000', '#ff6b9d', '#67e8f9'];
  return hues[hash % hues.length];
}

function familyShort(familyId: string): string {
  return familyId
    .replace(/_/g, ' ')
    .split(' ')
    .slice(0, 2)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function AgentActivityPanel({ agentRuns }: AgentActivityPanelProps) {
  const runs = agentRuns ?? [];

  const stats = useMemo(() => {
    const total = runs.length;
    const successes = runs.filter(r => r.success).length;
    const rate = total > 0 ? Math.round((successes / total) * 100) : 0;

    const providerCounts: Record<string, number> = {};
    for (const r of runs) {
      providerCounts[r.provider] = (providerCounts[r.provider] ?? 0) + 1;
    }

    const segments = Object.entries(providerCounts).map(([provider, count]) => ({
      provider,
      pct: (count / Math.max(total, 1)) * 100,
      color: providerColor(provider),
    }));

    return { total, successes, rate, segments };
  }, [runs]);

  if (runs.length === 0) {
    return (
      <section className="aap">
        <header className="aap__header">
          <h2 className="aap__title">Agent Activity</h2>
          <span className="aap__summary aap__summary--dim">No agent runs recorded</span>
        </header>
      </section>
    );
  }

  return (
    <section className="aap">
      <header className="aap__header">
        <h2 className="aap__title">Agent Activity</h2>
        <div className="aap__summary">
          <span className="aap__stat">{stats.total} runs</span>
          <span className="aap__divider">|</span>
          <span
            className="aap__stat"
            style={{ color: stats.rate >= 80 ? 'var(--ok)' : stats.rate >= 50 ? 'var(--warn)' : 'var(--crit)' }}
          >
            {stats.rate}% success
          </span>
        </div>
      </header>

      <div className="aap__tier-bar">
        {stats.segments.map(seg => (
          <div
            key={seg.provider}
            className="aap__tier-segment"
            style={{ width: `${seg.pct}%`, background: seg.color }}
            title={`${seg.provider}: ${seg.pct.toFixed(0)}%`}
          />
        ))}
      </div>

      <div className="aap__list">
        {runs.map((run, i) => (
          <div
            key={run.run_id}
            className={`aap__row ${i % 2 === 0 ? 'aap__row--even' : ''}`}
            style={{ animationDelay: `${i * 30}ms` }}
          >
            <div className="aap__row-main">
              <span className={`aap__icon ${run.success ? 'aap__icon--ok' : 'aap__icon--err'}`}>
                {run.success ? '✓' : '✗'}
              </span>
              <span className="aap__task">{taskTypeLabel(run.task_type)}</span>
              <span
                className="aap__family"
                style={{
                  background: `${familyColor(run.family_id)}22`,
                  color: familyColor(run.family_id),
                  borderColor: `${familyColor(run.family_id)}44`,
                }}
              >
                {familyShort(run.family_id)}
              </span>
              <span className="aap__model" style={{ color: providerColor(run.provider) }}>
                {run.provider === 'deterministic' ? 'deterministic' : `${run.provider}/${run.model}`}
              </span>
              <span className="aap__duration">{formatDuration(run.duration_ms)}</span>
              {run.fallback_used && <span className="aap__fallback">FB</span>}
            </div>
            <div className="aap__row-detail">
              {run.success ? (
                <span className="aap__headline">{run.headline || '—'}</span>
              ) : (
                <span className="aap__error">ERROR: {run.error || run.headline || 'Unknown'}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
