import { ErrorBoundary } from '../components/ErrorBoundary';
import { AgentActivityPanel } from '../components/AgentActivityPanel';
import SectionPanel from '../components/SectionPanel';
import type {
  DashboardSnapshot,
  SnapshotV2,
  AgentRun,
} from '../types/snapshot';
import { providerColor, taskTypeLabel } from '../utils/format';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── Run breakdown helpers ────────────────────────────────────────────────────

function countBy(runs: AgentRun[], key: keyof AgentRun): [string, number][] {
  const counts: Record<string, number> = {};
  for (const r of runs) {
    const v = String(r[key] ?? 'unknown');
    counts[v] = (counts[v] ?? 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

function RunBreakdownTable({
  title,
  rows,
  total,
  colorFn,
}: {
  title: string;
  rows: [string, number][];
  total: number;
  colorFn?: (k: string) => string;
}) {
  return (
    <div>
      <div
        style={{
          fontFamily: 'var(--font-display)',
          fontSize: '0.62rem',
          textTransform: 'uppercase',
          letterSpacing: '0.1em',
          color: 'var(--text-muted)',
          marginBottom: 6,
        }}
      >
        {title}
      </div>
      <table className="run-breakdown-table">
        <thead>
          <tr>
            <th>{title}</th>
            <th>Runs</th>
            <th>Share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, n]) => (
            <tr key={k}>
              <td style={{ color: colorFn ? colorFn(k) : 'var(--text)' }}>
                {k}
              </td>
              <td>{n}</td>
              <td>
                <div className="run-bar-wrap">
                  <div
                    className="run-bar"
                    style={{
                      width: `${Math.round((n / Math.max(1, total)) * 100)}%`,
                      background: colorFn ? colorFn(k) : 'var(--info)',
                    }}
                  />
                </div>
                <span
                  style={{
                    marginLeft: 4,
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.62rem',
                    color: 'var(--text-muted)',
                  }}
                >
                  {Math.round((n / Math.max(1, total)) * 100)}%
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function ComputeCostPage({ snapshot, snapshotV2 }: Props) {
  const budget = snapshotV2?.budget_governance;
  const mobkit = snapshotV2?.mobkit_health;
  const runs = snapshot?.factory?.agent_runs ?? [];
  const rs = snapshot?.factory?.research_summary;

  const successCount = runs.filter((r) => r.success).length;
  const failCount = runs.filter((r) => !r.success).length;
  const fallbackCount = runs.filter((r) => r.fallback_used).length;
  const successRate =
    runs.length > 0 ? Math.round((successCount / runs.length) * 100) : null;

  const byProvider = countBy(runs, 'provider');
  const byTask = countBy(runs, 'task_type');
  const byModelClass = countBy(runs, 'model_class');

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Compute / Cost</h2>
        <p className="page__subtitle">
          Budget governance, agent run breakdown, session telemetry
        </p>
      </div>

      {/* Budget governance */}
      {budget && (
        <SectionPanel
          title="Budget Governance"
          tag={budget.strict_budgets ? 'strict' : 'observe-only'}
          tagColor={budget.strict_budgets ? 'var(--ok)' : 'var(--warn)'}
        >
          <div className="budget-grid">
            <div className={`budget-card${budget.daily_budget_usd ? ' budget-card--ok' : ''}`}>
              <span className="budget-card__label">Daily Cap</span>
              <span className="budget-card__value">
                {budget.daily_budget_usd != null
                  ? `$${budget.daily_budget_usd}`
                  : '—'}
              </span>
              <span className="budget-card__sub">FACTORY_DAILY_INFERENCE_BUDGET_USD</span>
            </div>
            <div className="budget-card">
              <span className="budget-card__label">Weekly Cap</span>
              <span className="budget-card__value">
                {budget.weekly_budget_usd != null
                  ? `$${budget.weekly_budget_usd}`
                  : '—'}
              </span>
              <span className="budget-card__sub">FACTORY_WEEKLY_INFERENCE_BUDGET_USD</span>
            </div>
            <div
              className={`budget-card${budget.strict_budgets ? ' budget-card--ok' : ' budget-card--warn'}`}
            >
              <span className="budget-card__label">Strict Mode</span>
              <span className="budget-card__value">
                {budget.strict_budgets ? 'ON' : 'OFF'}
              </span>
              <span className="budget-card__sub">FACTORY_STRICT_BUDGETS</span>
            </div>
            <div className="budget-card">
              <span className="budget-card__label">Force Cheap at</span>
              <span className="budget-card__value">
                {budget.force_cheap_ratio != null
                  ? `${Math.round(budget.force_cheap_ratio * 100)}%`
                  : '—'}
              </span>
              <span className="budget-card__sub">of daily budget</span>
            </div>
            <div className="budget-card">
              <span className="budget-card__label">Single Agent at</span>
              <span className="budget-card__value">
                {budget.single_agent_ratio != null
                  ? `${Math.round(budget.single_agent_ratio * 100)}%`
                  : '—'}
              </span>
              <span className="budget-card__sub">no parallelism</span>
            </div>
            <div className="budget-card">
              <span className="budget-card__label">Drop Reviewer at</span>
              <span className="budget-card__value">
                {budget.reviewer_removal_ratio != null
                  ? `${Math.round(budget.reviewer_removal_ratio * 100)}%`
                  : '—'}
              </span>
              <span className="budget-card__sub">of daily budget</span>
            </div>
            {/* Backend gap cards */}
            <div className="budget-card budget-card--gap">
              <span className="budget-card__label">Daily Spend</span>
              <span className="budget-card__value">—</span>
              <span className="budget-card__sub">not tracked yet</span>
            </div>
            <div className="budget-card budget-card--gap">
              <span className="budget-card__label">Weekly Spend</span>
              <span className="budget-card__value">—</span>
              <span className="budget-card__sub">not tracked yet</span>
            </div>
            <div className="budget-card budget-card--gap">
              <span className="budget-card__label">Token Total</span>
              <span className="budget-card__value">—</span>
              <span className="budget-card__sub">not tracked yet</span>
            </div>
          </div>
          <div className="budget-gap-note">
            ⚠ daily_spend_usd / weekly_spend_usd / token_count_total are backend
            gaps — cost tracking not yet instrumented in the agent runtime.
          </div>
        </SectionPanel>
      )}

      {/* Mobkit session telemetry proxy */}
      {mobkit && (
        <SectionPanel
          title="Session Telemetry (proxy)"
          tag={
            mobkit.success_rate_pct != null
              ? `${mobkit.success_rate_pct}% success`
              : undefined
          }
          tagColor={
            (mobkit.success_rate_pct ?? 100) >= 90
              ? 'var(--ok)'
              : (mobkit.success_rate_pct ?? 100) >= 70
              ? 'var(--warn)'
              : 'var(--crit)'
          }
        >
          <div className="mobkit-health-grid">
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">Backend</span>
              <span
                className={`mobkit-stat__value${mobkit.configured ? ' mobkit-stat__value--ok' : ''}`}
              >
                {mobkit.backend}
              </span>
            </div>
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">Runs (24h)</span>
              <span className="mobkit-stat__value">{mobkit.recent_runs_24h}</span>
            </div>
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">Failures</span>
              <span
                className={`mobkit-stat__value${mobkit.recent_failures_24h > 0 ? ' mobkit-stat__value--warn' : ' mobkit-stat__value--ok'}`}
              >
                {mobkit.recent_failures_24h}
              </span>
            </div>
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">Success Rate</span>
              <span
                className={`mobkit-stat__value${
                  (mobkit.success_rate_pct ?? 100) >= 90
                    ? ' mobkit-stat__value--ok'
                    : ' mobkit-stat__value--warn'
                }`}
              >
                {mobkit.success_rate_pct != null
                  ? `${mobkit.success_rate_pct}%`
                  : '—'}
              </span>
            </div>
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">Fallbacks</span>
              <span
                className={`mobkit-stat__value${mobkit.fallback_used_24h > 0 ? ' mobkit-stat__value--warn' : ' mobkit-stat__value--muted'}`}
              >
                {mobkit.fallback_used_24h}
              </span>
            </div>
            <div className="mobkit-stat">
              <span className="mobkit-stat__label">RPC Health</span>
              <span className="mobkit-stat__value mobkit-stat__value--muted">
                {mobkit.rpc_healthy === null ? 'not polled' : mobkit.rpc_healthy ? '✓ healthy' : '✗ down'}
              </span>
            </div>
          </div>
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.65rem',
              color: 'var(--text-muted)',
              marginTop: 8,
            }}
          >
            {mobkit.note}
          </p>
        </SectionPanel>
      )}

      {/* Agent run breakdown */}
      {runs.length > 0 && (
        <SectionPanel title="Agent Run Breakdown" count={runs.length}>
          {/* Summary stats */}
          <div className="page__runtime-strip" style={{ marginBottom: 12 }}>
            <span className="runtime-pill">
              <span className="runtime-pill__label">total</span>
              <span className="runtime-pill__value">{runs.length}</span>
            </span>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">success</span>
              <span className="runtime-pill__value runtime-pill__value--ok">
                {successCount}
              </span>
            </span>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">failed</span>
              <span
                className={`runtime-pill__value${failCount > 0 ? ' runtime-pill__value--warn' : ''}`}
              >
                {failCount}
              </span>
            </span>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">fallbacks</span>
              <span
                className={`runtime-pill__value${fallbackCount > 0 ? ' runtime-pill__value--warn' : ''}`}
              >
                {fallbackCount}
              </span>
            </span>
            {successRate !== null && (
              <>
                <span className="runtime-pill__sep" />
                <span className="runtime-pill">
                  <span className="runtime-pill__label">success rate</span>
                  <span
                    className={`runtime-pill__value${successRate >= 90 ? ' runtime-pill__value--ok' : ' runtime-pill__value--warn'}`}
                  >
                    {successRate}%
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="run-breakdown">
            <RunBreakdownTable
              title="By Provider"
              rows={byProvider}
              total={runs.length}
              colorFn={providerColor}
            />
            <RunBreakdownTable
              title="By Task Type"
              rows={byTask.map(([k, n]) => [taskTypeLabel(k), n])}
              total={runs.length}
            />
            <RunBreakdownTable
              title="By Model Class"
              rows={byModelClass}
              total={runs.length}
            />
          </div>
        </SectionPanel>
      )}

      {/* Agent activity feed */}
      <ErrorBoundary name="AgentActivity">
        <AgentActivityPanel agentRuns={snapshot?.factory?.agent_runs} />
      </ErrorBoundary>

      {/* Operator signals strip */}
      {rs && (
        <div className="page__runtime-strip">
          {[
            ['Escalations', rs.operator_escalation_count],
            ['Action Inbox', rs.operator_action_inbox_count],
            ['Human Req', rs.human_action_required_count],
            ['Review Due', rs.review_due_count],
          ].map(([label, val]) => (
            <span key={String(label)} className="runtime-pill">
              <span className="runtime-pill__label">{label}</span>
              <span
                className={`runtime-pill__value${Number(val) > 0 ? ' runtime-pill__value--warn' : ''}`}
              >
                {String(val ?? '—')}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
