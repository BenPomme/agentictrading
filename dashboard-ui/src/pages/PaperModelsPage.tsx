import { useState } from 'react';
import { ErrorBoundary } from '../components/ErrorBoundary';
import SectionPanel from '../components/SectionPanel';
import type {
  DashboardSnapshot,
  SnapshotV2,
  Lineage,
  LineageV2,
  PortfolioSnapshot,
  Assessment,
  DeterministicBlocker,
} from '../types/snapshot';
import { formatPnl } from '../utils/format';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

// ── Merged lineage row ──────────────────────────────────────────────────────

interface MergedRow {
  // From v1 lineage
  lineage_id: string;
  family_id: string;
  current_stage: string;
  iteration_status: string;
  runtime_lane_kind: string;
  roi_pct: number;
  trade_count: number;
  paper_days: number;
  blockers: string[];
  assessment: Assessment | null;
  paper_runtime_status: string;
  // From lineage_v2
  venue: string;
  holdoff_reason: string | null;
  venue_scope_reason: string | null;
  paper_portfolio_id: string | null;
  det_blockers: DeterministicBlocker[];
  // From portfolio
  balance: number | null;
  starting_balance: number | null;
  realized_pnl: number | null;
  drawdown_pct: number | null;
  port_trade_count: number | null;
}

function mergeRows(
  lineages: Lineage[],
  lineageV2: LineageV2[],
  portfolios: PortfolioSnapshot[],
): MergedRow[] {
  const v2Map = new Map(lineageV2.map((l) => [l.lineage_id, l]));
  const portMap = new Map(portfolios.map((p) => [p.portfolio_id, p]));

  return lineages
    .filter(
      (l) =>
        l.current_stage === 'paper' ||
        l.current_stage === 'shadow' ||
        l.runtime_lane_selected ||
        (l.paper_days ?? 0) > 0,
    )
    .map((lin) => {
      const v2 = v2Map.get(lin.lineage_id) ?? null;
      const portId =
        v2?.paper_portfolio_id ??
        (lin.runtime_target_portfolio || null);
      const port = portId ? portMap.get(portId) ?? null : null;

      return {
        lineage_id: lin.lineage_id,
        family_id: lin.family_id,
        current_stage: lin.current_stage ?? '',
        iteration_status: (lin.iteration_status as string) ?? '',
        runtime_lane_kind: lin.runtime_lane_kind ?? '',
        roi_pct: lin.roi_pct ?? 0,
        trade_count: lin.trade_count ?? 0,
        paper_days: lin.paper_days ?? 0,
        blockers: (lin.blockers as string[]) ?? [],
        assessment: lin.assessment ?? null,
        paper_runtime_status: (lin.paper_runtime_status as string) ?? '',
        venue: v2?.venue ?? '',
        holdoff_reason: v2?.holdoff_reason ?? null,
        venue_scope_reason: v2?.venue_scope_reason ?? null,
        paper_portfolio_id: v2?.paper_portfolio_id ?? null,
        det_blockers: v2?.deterministic_blockers ?? [],
        balance: port?.current_balance ?? null,
        starting_balance: port?.starting_balance ?? null,
        realized_pnl: port?.realized_pnl ?? null,
        drawdown_pct: port?.drawdown_pct ?? null,
        port_trade_count: port?.trade_count ?? null,
      };
    });
}

// ── Gate check builder ──────────────────────────────────────────────────────

interface GateCheck {
  name: string;
  threshold: string;
  evidence: string;
  passed: boolean | null;
  action: string;
}

function buildGateChecks(row: MergedRow): GateCheck[] {
  const checks: GateCheck[] = [];
  const a = row.assessment;

  if (a) {
    // Paper days gate
    if (a.paper_days_required > 0) {
      const passed = a.paper_days_observed >= a.paper_days_required;
      checks.push({
        name: 'Paper Days',
        threshold: `≥ ${a.paper_days_required}d`,
        evidence: `${a.paper_days_observed}d observed`,
        passed,
        action: passed
          ? '—'
          : `Wait ${a.days_remaining > 0 ? a.days_remaining + 'd more' : 'accumulating'}`,
      });
    }
    // Trade count gate
    if (a.trade_count_required > 0) {
      const passed = a.trade_count_observed >= a.trade_count_required;
      checks.push({
        name: 'Trade Count',
        threshold: `≥ ${a.trade_count_required}`,
        evidence: `${a.trade_count_observed} observed`,
        passed,
        action: passed
          ? '—'
          : a.trades_remaining > 0
          ? `Need ${a.trades_remaining} more (ETA: ${a.eta || '?'})`
          : 'Accumulating',
      });
    }
    // ROI gate (from assessment.roi_pct as the required level isn't explicit, use sign)
    if (a.roi_pct != null) {
      const passed = a.roi_pct >= 0;
      checks.push({
        name: 'Return Sign',
        threshold: '≥ 0%',
        evidence: `${a.roi_pct.toFixed(2)}% observed`,
        passed,
        action: passed ? '—' : 'Requires positive ROI to advance',
      });
    }
  }

  // Deterministic blockers from v2 (always failing)
  for (const b of row.det_blockers) {
    checks.push({
      name: b.code,
      threshold: 'pass',
      evidence: b.description,
      passed: false,
      action: b.evidence ?? 'Resolve blocker to advance stage',
    });
  }

  // String blockers from v1 registry (catch-all)
  const detCodes = new Set(row.det_blockers.map((b) => b.code));
  for (const b of row.blockers) {
    if (!detCodes.has(b)) {
      checks.push({
        name: 'Gate',
        threshold: 'pass',
        evidence: b,
        passed: false,
        action: 'Resolve to advance',
      });
    }
  }

  return checks;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function stageBadge(stage: string) {
  const cls =
    stage === 'paper'
      ? 'lv2-stage-badge lv2-stage-badge--paper'
      : stage === 'shadow'
      ? 'lv2-stage-badge lv2-stage-badge--shadow'
      : stage === 'retired'
      ? 'lv2-stage-badge lv2-stage-badge--retired'
      : 'lv2-stage-badge lv2-stage-badge--default';
  return <span className={cls}>{stage}</span>;
}

function shortId(id: string, n = 22): string {
  return id.length > n ? '…' + id.slice(-(n - 1)) : id;
}

function nextCheckpoint(row: MergedRow): string {
  const a = row.assessment;
  if (!a) return '—';
  if (a.complete) return 'Assessment complete';
  if (a.days_remaining > 0 && a.trades_remaining > 0) {
    return `${a.days_remaining}d or ${a.trades_remaining} trades`;
  }
  if (a.days_remaining > 0) return `${a.days_remaining}d remaining`;
  if (a.trades_remaining > 0) return `${a.trades_remaining} trades left`;
  if (a.eta) return `ETA: ${a.eta}`;
  return `${a.completion_pct.toFixed(0)}% complete`;
}

// ── Console component ────────────────────────────────────────────────────────

function PaperConsole({ rows }: { rows: MergedRow[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (rows.length === 0) {
    return (
      <div className="alert-empty">
        No paper or shadow lineages found. Run the autonomous paper window to
        advance lineages to shadow/paper stage.
      </div>
    );
  }

  return (
    <div className="paper-console">
      {/* Header row */}
      <div className="paper-console__header">
        <span>Lineage / Family</span>
        <span>Venue · Lane</span>
        <span>Stage</span>
        <span style={{ textAlign: 'right' }}>Balance</span>
        <span style={{ textAlign: 'right' }}>P&amp;L</span>
        <span style={{ textAlign: 'right' }}>DD%</span>
        <span style={{ textAlign: 'right' }}>Trades</span>
        <span>Next chkpt</span>
        <span>Holdoff / Blocker</span>
        <span />
      </div>

      {rows.map((row) => {
        const isOpen = expanded.has(row.lineage_id);
        const checks = isOpen ? buildGateChecks(row) : [];
        const hasIssue =
          row.holdoff_reason != null ||
          row.venue_scope_reason != null ||
          row.det_blockers.length > 0 ||
          row.blockers.length > 0;
        const pnlVal = row.realized_pnl;
        const pnlClass =
          pnlVal == null
            ? 'pc-cell pc-cell--right pc-cell--muted'
            : pnlVal >= 0
            ? 'pc-cell pc-cell--right pc-cell--ok'
            : 'pc-cell pc-cell--right pc-cell--crit';

        return (
          <div key={row.lineage_id} className="paper-console__row">
            <div
              className="paper-console__cells"
              onClick={() => toggle(row.lineage_id)}
            >
              {/* Lineage / Family */}
              <div className="pc-id">
                <span className="pc-id__family">{row.family_id}</span>
                <span className="pc-id__lineage" title={row.lineage_id}>
                  {shortId(row.lineage_id)}
                </span>
              </div>

              {/* Venue · Lane */}
              <span className="pc-cell pc-cell--muted">
                {row.venue || '—'}
                {row.runtime_lane_kind ? ` · ${row.runtime_lane_kind}` : ''}
              </span>

              {/* Stage */}
              <span className="pc-cell">{stageBadge(row.current_stage)}</span>

              {/* Balance */}
              <span className="pc-cell pc-cell--right">
                {row.balance != null ? `€${row.balance.toFixed(0)}` : '—'}
              </span>

              {/* P&L */}
              <span className={pnlClass}>
                {pnlVal != null ? formatPnl(pnlVal) : '—'}
              </span>

              {/* Drawdown */}
              <span
                className={`pc-cell pc-cell--right${
                  (row.drawdown_pct ?? 0) > 5 ? ' pc-cell--warn' : ''
                }`}
              >
                {row.drawdown_pct != null
                  ? `${row.drawdown_pct.toFixed(1)}%`
                  : '—'}
              </span>

              {/* Trades */}
              <span className="pc-cell pc-cell--right">
                {row.port_trade_count ?? row.trade_count}
              </span>

              {/* Next checkpoint */}
              <span className="pc-cell pc-cell--muted">
                {nextCheckpoint(row)}
              </span>

              {/* Holdoff / Blocker indicator */}
              <span
                className={`pc-cell${hasIssue ? ' pc-cell--warn' : ' pc-cell--muted'}`}
              >
                {row.holdoff_reason
                  ? 'holdoff'
                  : row.det_blockers.length > 0
                  ? `${row.det_blockers.length} gate`
                  : row.blockers.length > 0
                  ? `${row.blockers.length} blk`
                  : row.venue_scope_reason
                  ? 'scope'
                  : '—'}
              </span>

              {/* Expand button */}
              <button
                className={`pc-expand-btn${isOpen ? ' pc-expand-btn--open' : ''}`}
                onClick={(e) => {
                  e.stopPropagation();
                  toggle(row.lineage_id);
                }}
                title="Expand gate drilldown"
              >
                {isOpen ? '▲' : '▼'}
              </button>
            </div>

            {/* ── Gate drilldown ── */}
            {isOpen && (
              <div className="gate-drilldown">
                <div className="gate-drilldown__title">
                  Deterministic Gate Drilldown
                  {row.holdoff_reason && (
                    <span
                      style={{
                        color: 'var(--warn)',
                        marginLeft: 12,
                        fontFamily: 'var(--font-mono)',
                        fontSize: '0.66rem',
                      }}
                    >
                      ⚑ Holdoff: {row.holdoff_reason}
                    </span>
                  )}
                  {row.venue_scope_reason && (
                    <span
                      style={{
                        color: 'var(--crit)',
                        marginLeft: 12,
                        fontFamily: 'var(--font-mono)',
                        fontSize: '0.66rem',
                      }}
                    >
                      ✗ Scope: {row.venue_scope_reason}
                    </span>
                  )}
                </div>

                {checks.length === 0 ? (
                  <div
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '0.68rem',
                      color: 'var(--text-muted)',
                    }}
                  >
                    No gate checks available (assessment data absent).
                  </div>
                ) : (
                  <table className="gate-table">
                    <thead>
                      <tr>
                        <th>Gate</th>
                        <th>Threshold</th>
                        <th>Evidence</th>
                        <th>Pass?</th>
                        <th>Next Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {checks.map((c, i) => (
                        <tr key={i}>
                          <td className="gate-name">{c.name}</td>
                          <td className="gate-threshold">{c.threshold}</td>
                          <td className="gate-evidence">{c.evidence}</td>
                          <td>
                            {c.passed === null ? (
                              <span className="gate-pending">?</span>
                            ) : c.passed ? (
                              <span className="gate-pass">✓</span>
                            ) : (
                              <span className="gate-fail">✗</span>
                            )}
                          </td>
                          <td className="gate-action">{c.action}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {/* Promotion scorecard if available */}
                {(() => {
                  const sc = (row as unknown as Record<string, unknown>).promotion_scorecard as Record<string, unknown> | null | undefined;
                  if (!sc || typeof sc !== 'object') return null;
                  const entries = Object.entries(sc);
                  if (entries.length === 0) return null;
                  return (
                    <div className="scorecard">
                      <div className="gate-drilldown__title">Promotion Scorecard</div>
                      {entries.map(([k, v]) => (
                        <div
                          key={k}
                          className={`scorecard-row${String(v).includes('fail') || String(v).includes('✗') ? ' scorecard-row--fail' : String(v).includes('pass') || String(v).includes('✓') ? ' scorecard-row--pass' : ''}`}
                        >
                          <span className="scorecard-row__name">{k}</span>
                          <span className="scorecard-row__detail">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export function PaperModelsPage({ snapshot, snapshotV2 }: Props) {
  const pr = snapshot?.factory?.paper_runtime;
  const lineages = snapshot?.factory?.lineages ?? [];
  const lineageV2 = snapshotV2?.lineage_v2 ?? [];
  const portfolios = [
    ...(snapshot?.execution?.portfolios ?? []),
    ...(snapshot?.execution?.placeholders ?? []),
  ];

  const rows = mergeRows(lineages, lineageV2, portfolios);
  const scopeBlockedRows = lineageV2.filter((r) => r.venue_scope_reason != null);
  const holdoffCount = rows.filter((r) => r.holdoff_reason != null).length;

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Paper-Active Models</h2>
        <p className="page__subtitle">
          Per-lineage paper console with gate drilldown, P&amp;L, and holdoff
          state
        </p>
      </div>

      {/* Paper runtime summary */}
      {pr && (
        <div className="paper-runtime-bar">
          {[
            ['Running', pr.running_count, pr.running_count > 0 ? 'ok' : 'muted'],
            ['Starting', pr.starting_count, 'muted'],
            ['Assigned', pr.assigned_count, 'muted'],
            ['Candidate', pr.candidate_count, 'muted'],
            ['Suppressed', pr.suppressed_count, pr.suppressed_count > 0 ? 'warn' : 'muted'],
            ['Failed', pr.failed_count, pr.failed_count > 0 ? 'warn' : 'muted'],
            ['Retired', pr.retired_count, 'muted'],
            ['Holdoff', holdoffCount, holdoffCount > 0 ? 'warn' : 'muted'],
          ].map(([label, val, cls]) => (
            <div key={String(label)} className="paper-runtime-stat">
              <span className="paper-runtime-stat__label">{label}</span>
              <span className={`paper-runtime-stat__value paper-runtime-stat__value--${cls}`}>
                {String(val)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Per-lineage console */}
      <SectionPanel
        title="Paper / Shadow Lineage Console"
        count={rows.length}
        tag={
          rows.filter((r) => r.det_blockers.length > 0 || r.blockers.length > 0)
            .length > 0
            ? `${
                rows.filter(
                  (r) =>
                    r.det_blockers.length > 0 || r.blockers.length > 0,
                ).length
              } with blockers`
            : undefined
        }
        tagColor="var(--warn)"
      >
        <ErrorBoundary name="PaperConsole">
          <PaperConsole rows={rows} />
        </ErrorBoundary>
      </SectionPanel>

      {/* Scope-blocked lineages */}
      {scopeBlockedRows.length > 0 && (
        <SectionPanel
          title="Venue Scope Blocked"
          count={scopeBlockedRows.length}
          tag="excluded from dispatch"
          tagColor="var(--crit)"
          collapsible
          defaultCollapsed
        >
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.7rem',
              color: 'var(--text-muted)',
              marginBottom: 10,
            }}
          >
            These lineages target venues outside{' '}
            <strong>FACTORY_PAPER_WINDOW_VENUE_SCOPE</strong>. No agentic work
            dispatched; no paper execution.
          </p>
          <div style={{ overflowX: 'auto' }}>
            <table className="lineage-v2-table">
              <thead>
                <tr>
                  <th>Lineage</th>
                  <th>Family</th>
                  <th>Venue</th>
                  <th>Stage</th>
                  <th>Scope Reason</th>
                </tr>
              </thead>
              <tbody>
                {scopeBlockedRows.map((r) => (
                  <tr key={r.lineage_id}>
                    <td>
                      <span className="lv2-id" title={r.lineage_id}>
                        {shortId(r.lineage_id, 28)}
                      </span>
                    </td>
                    <td>{r.family_id}</td>
                    <td>{r.venue}</td>
                    <td>
                      <span className="lv2-stage-badge lv2-stage-badge--default">
                        {r.canonical_stage}
                      </span>
                    </td>
                    <td>
                      <span className="lv2-scope-warn">{r.venue_scope_reason}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionPanel>
      )}
    </div>
  );
}
