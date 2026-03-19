import type {
  DashboardSnapshot,
  SnapshotV2,
  Lineage,
  LineageV2,
  PortfolioSnapshot,
  Assessment,
  DeterministicBlocker,
} from '../types/snapshot';

export type PaperStateBucket =
  | 'promotion-ready'
  | 'accumulating'
  | 'blocked'
  | 'holdoff'
  | 'scope-blocked'
  | 'underperforming';

export interface MergedPaperModel {
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
  venue: string;
  holdoff_reason: string | null;
  venue_scope_reason: string | null;
  paper_portfolio_id: string | null;
  det_blockers: DeterministicBlocker[];
  balance: number | null;
  starting_balance: number | null;
  realized_pnl: number | null;
  drawdown_pct: number | null;
  port_trade_count: number | null;
  execution_health_status: string | null;
  readiness_score_pct: number | null;
  recent_trades: PortfolioSnapshot['recent_trades'];
  paper_state?: string | null;
  paper_reason?: string | null;
  feed_gate_status?: string | null;
  feed_gate_reason?: string | null;
  runner_gate_status?: string | null;
  runner_gate_reason?: string | null;
  state_bucket: PaperStateBucket;
  checkpoint_label: string;
  progress_pct: number;
  promotion_score: number;
}

export interface NavBadgeCounts {
  alerts: number;
  paper: number;
  promotion: number;
}

function checkpointLabel(assessment: Assessment | null): string {
  if (!assessment) return 'Assessment pending';
  if (assessment.complete) return 'Assessment complete';
  if (assessment.days_remaining > 0 && assessment.trades_remaining > 0) {
    return `${assessment.days_remaining}d or ${assessment.trades_remaining} trades`;
  }
  if (assessment.days_remaining > 0) return `${assessment.days_remaining}d remaining`;
  if (assessment.trades_remaining > 0) return `${assessment.trades_remaining} trades left`;
  if (assessment.eta) return `ETA ${assessment.eta}`;
  return `${assessment.completion_pct.toFixed(0)}% complete`;
}

function progressPct(assessment: Assessment | null): number {
  if (!assessment) return 0;
  return Math.max(0, Math.min(100, assessment.completion_pct ?? 0));
}

export function getPaperStateBucket(row: {
  holdoff_reason: string | null;
  venue_scope_reason: string | null;
  det_blockers: DeterministicBlocker[];
  blockers: string[];
  assessment: Assessment | null;
  realized_pnl: number | null;
  trade_count: number;
  port_trade_count: number | null;
  execution_health_status: string | null;
  paper_runtime_status: string | null;
  paper_state?: string | null;
  feed_gate_status?: string | null;
  runner_gate_status?: string | null;
}): PaperStateBucket {
  if (row.venue_scope_reason) return 'scope-blocked';
  if (row.holdoff_reason) return 'holdoff';
  if (
    row.paper_state === 'paper_blocked' ||
    row.paper_state === 'paper_degraded' ||
    row.feed_gate_status === 'missing' ||
    row.feed_gate_status === 'stale' ||
    row.feed_gate_status === 'unproven' ||
    (row.runner_gate_status != null && row.runner_gate_status !== 'bound')
  ) {
    return 'blocked';
  }
  if (row.paper_runtime_status === 'paper_running') {
    const totalTrades = row.port_trade_count ?? row.trade_count;
    const pnl = row.realized_pnl ?? 0;
    const unhealthy =
      row.execution_health_status != null &&
      row.execution_health_status !== 'healthy';
    if (unhealthy || (totalTrades > 0 && pnl < 0)) return 'underperforming';
    return 'accumulating';
  }
  if (row.det_blockers.length > 0 || row.blockers.length > 0) return 'blocked';

  const assessment = row.assessment;
  const totalTrades = row.port_trade_count ?? row.trade_count;
  const pnl = row.realized_pnl ?? 0;
  const unhealthy =
    row.execution_health_status != null &&
    row.execution_health_status !== 'healthy';

  if (assessment?.complete && !unhealthy && pnl >= 0) return 'promotion-ready';
  if (unhealthy || (totalTrades > 0 && pnl < 0)) return 'underperforming';
  return 'accumulating';
}

function promotionScore(row: {
  assessment: Assessment | null;
  det_blockers: DeterministicBlocker[];
  blockers: string[];
  holdoff_reason: string | null;
  venue_scope_reason: string | null;
  realized_pnl: number | null;
  roi_pct: number;
  paper_days: number;
  port_trade_count: number | null;
  trade_count: number;
  execution_health_status: string | null;
}): number {
  let score = 0;
  const assessment = row.assessment;
  score += assessment?.completion_pct ?? 0;
  score += Math.min((row.port_trade_count ?? row.trade_count) * 4, 24);
  score += Math.min(row.paper_days * 3, 18);
  score += Math.max(Math.min((row.realized_pnl ?? 0) / 10, 18), -18);
  score += Math.max(Math.min(row.roi_pct, 15), -15);
  if (assessment?.complete) score += 20;
  if (row.execution_health_status === 'healthy') score += 8;
  if (row.holdoff_reason) score -= 22;
  if (row.venue_scope_reason) score -= 40;
  score -= row.det_blockers.length * 18;
  score -= row.blockers.length * 10;
  return score;
}

export function mergePaperModels(
  lineages: Lineage[],
  lineageV2: LineageV2[],
  portfolios: PortfolioSnapshot[],
): MergedPaperModel[] {
  const v2Map = new Map(lineageV2.map((l) => [l.lineage_id, l]));
  const portMap = new Map(portfolios.map((p) => [p.portfolio_id, p]));

  return lineages
    .filter(
      (l) =>
        !l.is_history_only &&
        l.paper_runtime_status !== 'retired' &&
        l.runtime_stage !== 'retired' &&
        l.iteration_status !== 'retired' &&
        (
          l.current_stage === 'paper' ||
          l.current_stage === 'shadow' ||
          l.runtime_stage === 'paper_running' ||
          l.paper_runtime_status === 'paper_running' ||
          l.paper_runtime_status === 'paper_starting' ||
          l.paper_runtime_status === 'paper_assigned' ||
          l.runtime_lane_selected ||
          (l.paper_days ?? 0) > 0
        ),
    )
    .map((lin) => {
      const v2 = v2Map.get(lin.lineage_id) ?? null;
      const paper_portfolio_id =
        v2?.paper_portfolio_id ?? (lin.runtime_target_portfolio || null);
      const port = paper_portfolio_id ? portMap.get(paper_portfolio_id) ?? null : null;

      const partial = {
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
        paper_portfolio_id,
        paper_state: v2?.paper_state ?? null,
        paper_reason: v2?.paper_reason ?? null,
        feed_gate_status: v2?.feed_gate_status ?? null,
        feed_gate_reason: v2?.feed_gate_reason ?? null,
        runner_gate_status: v2?.runner_gate_status ?? null,
        runner_gate_reason: v2?.runner_gate_reason ?? null,
        det_blockers: v2?.deterministic_blockers ?? [],
        balance: port?.current_balance ?? null,
        starting_balance: port?.starting_balance ?? null,
        realized_pnl: port?.realized_pnl ?? null,
        drawdown_pct: port?.drawdown_pct ?? null,
        port_trade_count: port?.trade_count ?? null,
        execution_health_status: port?.execution_health_status ?? null,
        readiness_score_pct: port?.readiness_score_pct ?? null,
        recent_trades: port?.recent_trades ?? [],
      };

      return {
        ...partial,
        state_bucket: getPaperStateBucket(partial),
        checkpoint_label: checkpointLabel(partial.assessment),
        progress_pct: progressPct(partial.assessment),
        promotion_score: promotionScore(partial),
      };
    })
    .sort((a, b) => b.promotion_score - a.promotion_score);
}

export function deriveNavBadgeCounts(
  snapshot: DashboardSnapshot | null,
  snapshotV2: SnapshotV2 | null,
): NavBadgeCounts {
  const alertCount = snapshot?.company?.alerts?.length ?? 0;
  const maintenance = snapshot?.factory?.operator_signals?.maintenance_queue?.length ?? 0;
  const escalations = snapshot?.factory?.operator_signals?.escalation_candidates?.length ?? 0;
  const paperRows = mergePaperModels(
    snapshot?.factory?.lineages ?? [],
    snapshotV2?.lineage_v2 ?? [],
    [
      ...(snapshot?.execution?.portfolios ?? []),
      ...(snapshot?.execution?.placeholders ?? []),
    ],
  );

  return {
    alerts: alertCount + maintenance + escalations,
    paper: paperRows.filter(
      (row) => row.state_bucket === 'blocked' || row.state_bucket === 'holdoff',
    ).length,
    promotion: paperRows.filter((row) => row.state_bucket === 'promotion-ready').length,
  };
}

export function getResearchSummaryCards(snapshot: DashboardSnapshot | null) {
  const rs = snapshot?.factory?.research_summary;
  const lineages = snapshot?.factory?.lineages ?? [];
  return [
    {
      label: 'Paper+',
      value: lineages.filter((l) =>
        ['paper', 'canary_ready', 'live_ready', 'approved_live'].includes(l.current_stage ?? ''),
      ).length,
    },
    {
      label: 'Ready for Canary',
      value: rs?.ready_for_canary ?? 0,
    },
    {
      label: 'Positive Models',
      value: rs?.positive_model_count ?? 0,
    },
    {
      label: 'Review Due',
      value: rs?.review_due_count ?? 0,
    },
  ];
}
