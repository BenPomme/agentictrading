import { ErrorBoundary } from '../components/ErrorBoundary';
import SectionPanel from '../components/SectionPanel';
import type {
  DashboardSnapshot,
  SnapshotV2,
  Family,
  Lineage,
  LineageV2,
  ModelLeagueEntry,
  ResearchPositiveModel,
} from '../types/snapshot';
import { relativeTime } from '../utils/format';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

type QueueBucket = 'ready' | 'evidence' | 'blocked';

interface PromotionRow {
  lineage: Lineage;
  lineageV2: LineageV2 | null;
  family: Family | null;
  league: ModelLeagueEntry | null;
  researchPositive: ResearchPositiveModel | null;
  score: number;
  bucket: QueueBucket;
  signals: string[];
  blockers: string[];
}

interface FamilySignalRow {
  family: Family;
  league: ModelLeagueEntry | null;
  researchPositiveModels: ResearchPositiveModel[];
  liveReadinessScore: number;
  status: 'ready' | 'promoting' | 'watch' | 'blocked';
}

const PROMOTION_READY_STAGES = new Set(['canary_ready', 'live_ready', 'approved_live']);
const EVIDENCE_STAGES = new Set([
  'idea',
  'spec',
  'data_check',
  'goldfish_run',
  'walkforward',
  'stress',
  'shadow',
  'paper',
]);

function shortId(id: string | null | undefined, keep = 10): string {
  if (!id) return '—';
  return id.length > keep ? `…${id.slice(-keep)}` : id;
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function formatPct(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '—' : `${value.toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '—' : value.toLocaleString();
}

function stageScore(stage: string | undefined | null): number {
  switch (stage) {
    case 'approved_live':
      return 100;
    case 'live_ready':
      return 88;
    case 'canary_ready':
      return 78;
    case 'paper':
      return 50;
    case 'shadow':
      return 42;
    case 'stress':
      return 36;
    case 'walkforward':
      return 30;
    case 'goldfish_run':
      return 24;
    case 'data_check':
      return 18;
    case 'spec':
      return 10;
    case 'idea':
      return 4;
    default:
      return 0;
  }
}

function buildBlockers(
  lineage: Lineage,
  lineageV2: LineageV2 | null,
): string[] {
  const blockers = new Set<string>();

  for (const blocker of lineage.blockers ?? []) {
    if (blocker) blockers.add(blocker);
  }

  if (lineage.backtest_gate_status && lineage.backtest_gate_status !== 'pass') {
    blockers.add(`backtest gate: ${lineage.backtest_gate_status}`);
  }

  if (lineage.iteration_status === 'retired') {
    blockers.add('iteration retired');
  }

  if (lineageV2?.holdoff_reason) blockers.add(lineageV2.holdoff_reason);
  if (lineageV2?.venue_scope_reason) blockers.add(lineageV2.venue_scope_reason);

  const assessment = lineage.assessment;
  if (!assessment.complete && assessment.phase !== 'paper') {
    blockers.add(`assessment incomplete (${assessment.phase})`);
  }
  if ((assessment.completion_pct ?? 0) < 50 && lineage.current_stage !== 'idea') {
    blockers.add(`assessment at ${Math.round(assessment.completion_pct)}%`);
  }

  return Array.from(blockers);
}

function buildSignals(
  lineage: Lineage,
  family: Family | null,
  lineageV2: LineageV2 | null,
  league: ModelLeagueEntry | null,
  researchPositive: ResearchPositiveModel | null,
): string[] {
  const signals = new Set<string>();

  if (PROMOTION_READY_STAGES.has(lineage.current_stage)) signals.add('promotion-ready stage');
  if (EVIDENCE_STAGES.has(lineage.current_stage)) signals.add('evidence stage');
  if (family?.research_positive) signals.add('family research-positive');
  if (league?.isolated_evidence_ready) signals.add('isolated evidence ready');
  if (league?.alias_runner_running) signals.add('alias runner active');
  if (league?.activation_status) signals.add(`activation ${league.activation_status}`);
  if (league?.incubation_status) signals.add(`incubation ${league.incubation_status}`);
  if (researchPositive?.assessment_complete) signals.add('assessment complete');
  if (researchPositive?.execution_health_status) {
    signals.add(`execution ${researchPositive.execution_health_status}`);
  }
  if (researchPositive?.manifest_id) signals.add(`manifest ${shortId(researchPositive.manifest_id, 8)}`);
  if (lineageV2?.paper_portfolio_id) signals.add(`paper portfolio ${shortId(lineageV2.paper_portfolio_id, 8)}`);
  if (lineage.trade_count >= 20) signals.add('trade count threshold');
  if (lineage.paper_days >= 7) signals.add('paper age threshold');

  return Array.from(signals);
}

function scoreLineage(
  lineage: Lineage,
  lineageV2: LineageV2 | null,
  family: Family | null,
  league: ModelLeagueEntry | null,
  researchPositive: ResearchPositiveModel | null,
  blockers: string[],
): number {
  let score = stageScore(lineage.current_stage);

  if (family?.research_positive) score += 12;
  if (league?.isolated_evidence_ready) score += 10;
  if (league?.alias_runner_running) score += 4;
  if (researchPositive) {
    score += 10;
    score += clamp(12 - (researchPositive.curated_family_rank ?? 12), 0, 12);
    if (researchPositive.assessment_complete) score += 6;
    if ((researchPositive.live_trade_count ?? 0) > 0) score += 6;
    if ((researchPositive.live_roi_pct ?? 0) > 0) score += 6;
  }
  if (lineage.assessment.complete) score += 8;
  score += clamp(Math.round(lineage.assessment.completion_pct / 10), 0, 10);
  if (lineage.roi_pct > 0) score += 8;
  if (lineage.trade_count >= 20) score += 6;
  if (lineage.paper_days >= 7) score += 4;
  if (lineageV2?.holdoff_reason) score -= 20;
  if (lineageV2?.venue_scope_reason) score -= 24;
  score -= blockers.length * 8;

  return Math.max(0, score);
}

function bucketFor(score: number, blockers: string[], stage: string): QueueBucket {
  if (blockers.length > 0 || stage === 'retired' || score < 25) return 'blocked';
  if (score >= 70 || PROMOTION_READY_STAGES.has(stage)) return 'ready';
  return 'evidence';
}

function buildPromotionRows(snapshot: DashboardSnapshot | null, snapshotV2: SnapshotV2 | null): PromotionRow[] {
  if (!snapshot) return [];

  const lineages = snapshot.factory?.lineages ?? [];
  const families = snapshot.factory?.families ?? [];
  const league = snapshot.factory?.model_league ?? [];
  const lineageV2 = snapshotV2?.lineage_v2 ?? [];
  const researchPositiveModels = snapshot.factory?.operator_signals?.research_positive_models ?? [];

  const byFamily = new Map(families.map((family) => [family.family_id, family] as const));
  const byLineageV2 = new Map(lineageV2.map((row) => [row.lineage_id, row] as const));
  const byLeague = new Map(league.map((row) => [row.family_id, row] as const));
  const byResearchPositive = new Map(
    researchPositiveModels.map((row) => [row.lineage_id, row] as const),
  );

  return [...lineages]
    .filter((lineage) => lineage.current_stage !== 'retired')
    .map((lineage) => {
      const family = byFamily.get(lineage.family_id) ?? null;
      const lineageV2Row = byLineageV2.get(lineage.lineage_id) ?? null;
      const leagueRow = byLeague.get(lineage.family_id) ?? null;
      const researchPositive = byResearchPositive.get(lineage.lineage_id) ?? null;
      const blockers = buildBlockers(lineage, lineageV2Row);
      const score = scoreLineage(
        lineage,
        lineageV2Row,
        family,
        leagueRow,
        researchPositive,
        blockers,
      );
      const signals = buildSignals(
        lineage,
        family,
        lineageV2Row,
        leagueRow,
        researchPositive,
      );

      return {
        lineage,
        lineageV2: lineageV2Row,
        family,
        league: leagueRow,
        researchPositive,
        score,
        bucket: bucketFor(score, blockers, lineage.current_stage),
        signals,
        blockers,
      };
    })
    .sort((a, b) => {
      const order: Record<QueueBucket, number> = { ready: 0, evidence: 1, blocked: 2 };
      const bucketDelta = order[a.bucket] - order[b.bucket];
      if (bucketDelta !== 0) return bucketDelta;
      if (b.score !== a.score) return b.score - a.score;
      return (b.lineage.trade_count ?? 0) - (a.lineage.trade_count ?? 0);
    });
}

function buildFamilySignals(snapshot: DashboardSnapshot | null, snapshotV2: SnapshotV2 | null): FamilySignalRow[] {
  if (!snapshot) return [];

  const families = snapshot.factory?.families ?? [];
  const league = snapshot.factory?.model_league ?? [];
  const researchPositiveModels = snapshot.factory?.operator_signals?.research_positive_models ?? [];
  const lineageV2 = snapshotV2?.lineage_v2 ?? [];
  const lineages = snapshot.factory?.lineages ?? [];

  const familyModelMap = new Map(league.map((row) => [row.family_id, row] as const));
  const researchByFamily = new Map<string, ResearchPositiveModel[]>();
  for (const row of researchPositiveModels) {
    const list = researchByFamily.get(row.family_id) ?? [];
    list.push(row);
    researchByFamily.set(row.family_id, list);
  }

  const lineagesByFamily = new Map<string, Lineage[]>();
  for (const lineage of lineages) {
    const list = lineagesByFamily.get(lineage.family_id) ?? [];
    list.push(lineage);
    lineagesByFamily.set(lineage.family_id, list);
  }

  const lineageV2ByFamily = new Map<string, LineageV2[]>();
  for (const row of lineageV2) {
    const family = lineages.find((lineage) => lineage.lineage_id === row.lineage_id)?.family_id ?? null;
    if (!family) continue;
    const list = lineageV2ByFamily.get(family) ?? [];
    list.push(row);
    lineageV2ByFamily.set(family, list);
  }

  return families
    .map((family) => {
      const leagueRow = familyModelMap.get(family.family_id) ?? null;
      const rp = researchByFamily.get(family.family_id) ?? [];
      const familyLineages = lineagesByFamily.get(family.family_id) ?? [];
      const familyV2 = lineageV2ByFamily.get(family.family_id) ?? [];

      const readyLineages = familyLineages.filter((l) => PROMOTION_READY_STAGES.has(l.current_stage));
      const blockedLineages = familyLineages.filter((l) => {
        const row = familyV2.find((v2) => v2.lineage_id === l.lineage_id) ?? null;
        return buildBlockers(l, row).length > 0;
      });
      const positiveSignals =
        (family.research_positive ? 1 : 0) +
        (leagueRow?.isolated_evidence_ready ? 1 : 0) +
        (leagueRow?.alias_runner_running ? 1 : 0) +
        rp.length;

      const liveReadinessScore = clamp(
        positiveSignals * 18 +
          readyLineages.length * 14 +
          Math.min(20, familyLineages.filter((l) => l.current_stage === 'paper').length * 4) -
          blockedLineages.length * 10,
        0,
        100,
      );

      const status: FamilySignalRow['status'] =
        blockedLineages.length > 0 && liveReadinessScore < 35
          ? 'blocked'
          : liveReadinessScore >= 75
          ? 'ready'
          : liveReadinessScore >= 45
          ? 'promoting'
          : 'watch';

      return {
        family,
        league: leagueRow,
        researchPositiveModels: rp,
        liveReadinessScore,
        status,
      };
    })
    .sort((a, b) => {
      const order: Record<FamilySignalRow['status'], number> = {
        ready: 0,
        promoting: 1,
        watch: 2,
        blocked: 3,
      };
      const delta = order[a.status] - order[b.status];
      if (delta !== 0) return delta;
      if (b.liveReadinessScore !== a.liveReadinessScore) return b.liveReadinessScore - a.liveReadinessScore;
      return a.family.family_id.localeCompare(b.family.family_id);
    });
}

function QueueCard({
  row,
}: {
  row: PromotionRow;
}) {
  const statusLabel =
    row.bucket === 'ready'
      ? 'READY'
      : row.bucket === 'evidence'
      ? 'EVIDENCE'
      : 'BLOCKED';

  const statusClass = row.bucket === 'ready' ? 'ready' : row.bucket === 'evidence' ? 'partial' : 'blocked';
  const scoreColor =
    row.bucket === 'ready'
      ? 'var(--ok)'
      : row.bucket === 'evidence'
      ? 'var(--warn)'
      : 'var(--crit)';

  return (
    <div className={`venue-card venue-card--${statusClass}`}>
      <div className="venue-card__header">
        <span className="venue-card__name" title={row.lineage.lineage_id}>
          {row.family?.family_id ?? row.lineage.family_id} · {shortId(row.lineage.lineage_id)}
        </span>
        <span className={`venue-status-badge venue-status-badge--${statusClass}`}>
          {statusLabel}
        </span>
      </div>

      <div className="venue-card__row">
        <span className="venue-card__label">Score</span>
        <span className="venue-card__val" style={{ color: scoreColor }}>
          {Math.round(row.score)}
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Stage</span>
        <span className="venue-card__val">{row.lineage.current_stage}</span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">ROI / Trades</span>
        <span className="venue-card__val">
          {formatPct(row.lineage.roi_pct)} · {formatCount(row.lineage.trade_count)}
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Paper age</span>
        <span className="venue-card__val">{formatCount(row.lineage.paper_days)}d</span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Assessment</span>
        <span className="venue-card__val">
          {Math.round(row.lineage.assessment.completion_pct)}% · {row.lineage.assessment.phase}
        </span>
      </div>

      {row.signals.length > 0 && (
        <div className="venue-card__families" style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {row.signals.slice(0, 5).map((signal) => (
            <span
              key={signal}
              style={{
                padding: '2px 6px',
                borderRadius: 999,
                background: 'rgba(255,255,255,0.04)',
                color: 'var(--text)',
              }}
            >
              {signal}
            </span>
          ))}
        </div>
      )}

      {row.blockers.length > 0 && (
        <div className="readiness-blockers">
          <div className="readiness-blockers__title">Blockers</div>
          {row.blockers.slice(0, 4).map((blocker) => (
            <div key={blocker} className="readiness-blockers__item">
              {blocker}
            </div>
          ))}
        </div>
      )}

      {row.researchPositive && (
        <div className="venue-card__families" style={{ marginTop: 2 }}>
          <span style={{ color: 'var(--ok)' }}>
            research-positive · rank {row.researchPositive.curated_family_rank}
          </span>
          {row.researchPositive.manifest_id ? (
            <span style={{ marginLeft: 6 }}>
              manifest {shortId(row.researchPositive.manifest_id, 8)}
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}

function FamilySignalCard({ row }: { row: FamilySignalRow }) {
  const statusLabel =
    row.status === 'ready' ? 'READY' : row.status === 'promoting' ? 'PROMOTING' : row.status === 'blocked' ? 'BLOCKED' : 'WATCH';
  const statusClass =
    row.status === 'ready' ? 'ready' : row.status === 'promoting' ? 'partial' : row.status === 'blocked' ? 'blocked' : 'partial';

  return (
    <div className={`venue-card venue-card--${statusClass}`}>
      <div className="venue-card__header">
        <span className="venue-card__name">{row.family.family_id}</span>
        <span className={`venue-status-badge venue-status-badge--${statusClass}`}>
          {statusLabel}
        </span>
      </div>

      <div className="venue-card__row">
        <span className="venue-card__label">Live readiness</span>
        <span
          className="venue-card__val"
          style={{
            color:
              row.status === 'ready'
                ? 'var(--ok)'
                : row.status === 'blocked'
                ? 'var(--crit)'
                : 'var(--warn)',
          }}
        >
          {Math.round(row.liveReadinessScore)}/100
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Research positive</span>
        <span className="venue-card__val">
          {row.family.research_positive ? 'yes' : 'no'}
        </span>
      </div>
      <div className="venue-card__row">
        <span className="venue-card__label">Lineages</span>
        <span className="venue-card__val">
          {formatCount(row.family.lineage_count)} total · {formatCount(row.family.active_lineage_count)} active
        </span>
      </div>

      {row.league && (
        <div className="venue-card__row">
          <span className="venue-card__label">League</span>
          <span className="venue-card__val">
            {row.league.isolated_evidence_ready ? 'isolated evidence ready' : 'still incubating'}
          </span>
        </div>
      )}

      <div className="venue-card__row">
        <span className="venue-card__label">Positive models</span>
        <span className="venue-card__val">{row.researchPositiveModels.length}</span>
      </div>

      {row.researchPositiveModels.length > 0 && (
        <div className="venue-card__families" style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {row.researchPositiveModels.slice(0, 5).map((model) => (
            <span
              key={model.lineage_id}
              style={{
                padding: '2px 6px',
                borderRadius: 999,
                background: 'rgba(0,255,208,0.08)',
                color: 'var(--ok)',
              }}
            >
              {shortId(model.lineage_id, 8)} · {model.current_stage}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function PromotionQueuePage({ snapshot, snapshotV2 }: Props) {
  const runtime = snapshotV2?.runtime;
  const budget = snapshotV2?.budget_governance;
  const mobkit = snapshotV2?.mobkit_health;
  const goldfish = snapshotV2?.goldfish_health;
  const rs = snapshot?.factory?.research_summary;
  const lineages = snapshot?.factory?.lineages ?? [];
  const rows = buildPromotionRows(snapshot, snapshotV2);
  const familySignals = buildFamilySignals(snapshot, snapshotV2);

  const readyRows = rows.filter((row) => row.bucket === 'ready');
  const evidenceRows = rows.filter((row) => row.bucket === 'evidence');
  const blockedRows = rows.filter((row) => row.bucket === 'blocked');
  const researchPositiveModels = snapshot?.factory?.operator_signals?.research_positive_models ?? [];

  const liveReadyCount = rows.filter((row) => PROMOTION_READY_STAGES.has(row.lineage.current_stage)).length;
  const researchPositiveFamilyCount = (snapshot?.factory?.families ?? []).filter((f) => f.research_positive).length;
  const canaryReadyCount = lineages.filter((l) => l.current_stage === 'canary_ready').length;

  if (!snapshot) {
    return (
      <div className="page">
        <div className="page__header">
          <h2 className="page__title">Promotion Queue</h2>
          <p className="page__subtitle">
            Promotion-ready, accumulating evidence, blocked, and live-readiness signals
          </p>
        </div>
        <div className="page__placeholder">
          <div className="page__placeholder-icon">⇡</div>
          <div className="page__placeholder-title">No snapshot data</div>
          <div className="page__placeholder-desc">
            Promotion queue metrics will appear once snapshot and snapshot v2 data are available.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Promotion Queue</h2>
        <p className="page__subtitle">
          Promotion-ready, accumulating evidence, blocked, and research-positive/live-readiness signals
        </p>
      </div>

      <div className="page__runtime-strip">
        {[
          ['Ready', readyRows.length, readyRows.length > 0 ? 'ok' : 'warn'],
          ['Evidence', evidenceRows.length, evidenceRows.length > 0 ? 'warn' : 'muted'],
          ['Blocked', blockedRows.length, blockedRows.length > 0 ? 'warn' : 'muted'],
          ['Research+', researchPositiveFamilyCount, researchPositiveFamilyCount > 0 ? 'ok' : 'muted'],
          ['Live-ready', liveReadyCount, liveReadyCount > 0 ? 'ok' : 'muted'],
          ['Canary', canaryReadyCount, canaryReadyCount > 0 ? 'ok' : 'muted'],
        ].map(([label, value, tone]) => (
          <span key={String(label)} className="runtime-pill">
            <span className="runtime-pill__label">{label}</span>
            <span className={`runtime-pill__value runtime-pill__value--${tone}`}>
              {String(value)}
            </span>
          </span>
        ))}

        {runtime && (
          <>
            <span className="runtime-pill__sep" />
            <span className="runtime-pill">
              <span className="runtime-pill__label">Runtime</span>
              <span className={`runtime-pill__value${runtime.backend === 'mobkit' ? ' runtime-pill__value--ok' : ''}`}>
                {runtime.backend}
              </span>
            </span>
            <span className="runtime-pill">
              <span className="runtime-pill__label">Mode</span>
              <span className="runtime-pill__value">{runtime.mode}</span>
            </span>
          </>
        )}

        {budget && (
          <span className="runtime-pill">
            <span className="runtime-pill__label">Budget</span>
            <span className={`runtime-pill__value${budget.strict_budgets ? ' runtime-pill__value--ok' : ' runtime-pill__value--warn'}`}>
              {budget.strict_budgets ? 'strict' : 'observe-only'}
            </span>
          </span>
        )}
      </div>

      <SectionPanel
        title="Queue Summary"
        tag={blockedRows.length > 0 ? `${blockedRows.length} blocked` : 'clear'}
        tagColor={blockedRows.length > 0 ? 'var(--warn)' : 'var(--ok)'}
      >
        <div className="page__grid page__grid--2col">
          <div className="venue-card venue-card--ready">
            <div className="venue-card__header">
              <span className="venue-card__name">Promotion-ready</span>
              <span className="venue-status-badge venue-status-badge--ready">{readyRows.length}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Live-ready candidates</span>
              <span className="venue-card__val">{readyRows.length}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Top score</span>
              <span className="venue-card__val">
                {readyRows.length > 0 ? Math.round(readyRows[0].score) : '—'}
              </span>
            </div>
          </div>
          <div className="venue-card venue-card--partial">
            <div className="venue-card__header">
              <span className="venue-card__name">Evidence in flight</span>
              <span className="venue-status-badge venue-status-badge--partial">{evidenceRows.length}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Research-positive models</span>
              <span className="venue-card__val">{researchPositiveModels.length}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Family signals</span>
              <span className="venue-card__val">{familySignals.length}</span>
            </div>
          </div>
        </div>
      </SectionPanel>

      <div className="page__grid page__grid--2col">
        <ErrorBoundary name="PromotionReadyQueue">
          <SectionPanel
            title="Promotion-Ready"
            count={readyRows.length}
            tag={readyRows.length > 0 ? 'ready to review' : 'waiting'}
            tagColor={readyRows.length > 0 ? 'var(--ok)' : 'var(--warn)'}
          >
            {readyRows.length === 0 ? (
              <div className="page__placeholder" style={{ padding: '24px 16px' }}>
                <div className="page__placeholder-title">No ready candidates</div>
                <div className="page__placeholder-desc">
                  Candidates will appear here once they reach canary, live-ready, or approved-live
                  stages without blockers.
                </div>
              </div>
            ) : (
              <div className="venue-matrix">
                {readyRows.slice(0, 6).map((row) => (
                  <QueueCard key={row.lineage.lineage_id} row={row} />
                ))}
              </div>
            )}
          </SectionPanel>
        </ErrorBoundary>

        <ErrorBoundary name="PromotionEvidenceQueue">
          <SectionPanel
            title="Accumulating Evidence"
            count={evidenceRows.length}
            tag={
              rs ? `${rs.isolated_evidence_ready_family_count ?? 0} families` : undefined
            }
            tagColor="var(--warn)"
          >
            {evidenceRows.length === 0 ? (
              <div className="page__placeholder" style={{ padding: '24px 16px' }}>
                <div className="page__placeholder-title">No evidence queue</div>
                <div className="page__placeholder-desc">
                  Lineages in paper, shadow, stress, walkforward, or Goldfish-run stages will
                  appear here while they accumulate proof.
                </div>
              </div>
            ) : (
              <div className="venue-matrix">
                {evidenceRows.slice(0, 6).map((row) => (
                  <QueueCard key={row.lineage.lineage_id} row={row} />
                ))}
              </div>
            )}
          </SectionPanel>
        </ErrorBoundary>
      </div>

      <div className="page__grid page__grid--2col">
        <ErrorBoundary name="PromotionBlockedQueue">
          <SectionPanel
            title="Blocked / Held"
            count={blockedRows.length}
            tag={blockedRows.length > 0 ? 'attention' : 'clear'}
            tagColor={blockedRows.length > 0 ? 'var(--crit)' : 'var(--ok)'}
          >
            {blockedRows.length === 0 ? (
              <div className="page__placeholder" style={{ padding: '24px 16px' }}>
                <div className="page__placeholder-title">No blocked candidates</div>
                <div className="page__placeholder-desc">
                  Blockers, holdoffs, scope exclusions, and incomplete assessments will surface here.
                </div>
              </div>
            ) : (
              <div className="venue-matrix">
                {blockedRows.slice(0, 6).map((row) => (
                  <QueueCard key={row.lineage.lineage_id} row={row} />
                ))}
              </div>
            )}
          </SectionPanel>
        </ErrorBoundary>

        <ErrorBoundary name="PromotionFamilySignals">
          <SectionPanel
            title="Research-Positive / Live-Readiness"
            count={familySignals.length}
            tag={
              mobkit
                ? mobkit.rpc_healthy === true
                  ? 'mobkit healthy'
                  : mobkit.rpc_healthy === false
                  ? 'mobkit degraded'
                  : 'mobkit proxy'
                : 'snapshot'
            }
            tagColor={
              mobkit?.rpc_healthy === false
                ? 'var(--warn)'
                : mobkit?.configured
                ? 'var(--ok)'
                : 'var(--text-muted)'
            }
          >
            <div className="venue-matrix">
              {familySignals.slice(0, 6).map((row) => (
                <FamilySignalCard key={row.family.family_id} row={row} />
              ))}
            </div>
          </SectionPanel>
        </ErrorBoundary>
      </div>

      <SectionPanel title="Readiness Signals" collapsible defaultCollapsed={false}>
        <div className="page__grid page__grid--2col">
          <div className="venue-card venue-card--partial">
            <div className="venue-card__header">
              <span className="venue-card__name">Runtime / Budget</span>
              <span className="venue-status-badge venue-status-badge--partial">
                {runtime?.backend ?? 'unknown'}
              </span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Mode</span>
              <span className="venue-card__val">{runtime?.mode ?? '—'}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Paused</span>
              <span className="venue-card__val">{runtime?.paused ? 'yes' : 'no'}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Strict budgets</span>
              <span className="venue-card__val">{budget?.strict_budgets ? 'on' : 'off'}</span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Daily spend</span>
              <span className="venue-card__val">
                {budget?.daily_spend_usd != null ? `$${budget.daily_spend_usd.toFixed(2)}` : '—'}
              </span>
            </div>
          </div>

          <div className="venue-card venue-card--partial">
            <div className="venue-card__header">
              <span className="venue-card__name">Mobkit / Goldfish</span>
              <span className="venue-status-badge venue-status-badge--partial">
                {goldfish?.enabled ? 'goldfish on' : 'goldfish off'}
              </span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Mobkit success</span>
              <span className="venue-card__val">
                {mobkit?.success_rate_pct != null ? `${mobkit.success_rate_pct}%` : '—'}
              </span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Goldfish latest write</span>
              <span className="venue-card__val">
                {goldfish?.latest_write ? relativeTime(goldfish.latest_write) : '—'}
              </span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Workspace root</span>
              <span className="venue-card__val" title={goldfish?.workspace_root ?? undefined}>
                {goldfish?.workspace_root ? shortId(goldfish.workspace_root, 18) : '—'}
              </span>
            </div>
            <div className="venue-card__row">
              <span className="venue-card__label">Research positives</span>
              <span className="venue-card__val">{researchPositiveModels.length}</span>
            </div>
          </div>
        </div>

        {goldfish?.note && (
          <div
            style={{
              marginTop: 10,
              fontFamily: 'var(--font-mono)',
              fontSize: '0.68rem',
              color: 'var(--text-muted)',
            }}
          >
            {goldfish.note}
          </div>
        )}
      </SectionPanel>
    </div>
  );
}
