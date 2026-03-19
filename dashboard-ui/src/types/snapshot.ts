export interface DashboardSnapshot {
  generated_at: string;
  project_root: string;
  factory_paused: boolean;
  api_health: { status: string; snapshot_source: string };
  api_feeds: APIFeeds;
  factory: FactoryState;
  company: CompanyState;
  execution: ExecutionState;
  ideas: IdeasState;
}

export interface APIFeeds {
  status: string;
  headline: string;
  summary: string;
  total_count: number;
  healthy_count: number;
  warning_count: number;
  critical_count: number;
  latest_data_ts: string | null;
  latest_age_seconds: number;
  connectors: ConnectorHealth[];
}

export interface ConnectorHealth {
  connector_id: string;
  venue: string;
  status: string;
  freshness_status?: string;
  ready: boolean;
  latest_data_ts: string | null;
  latest_age_seconds: number;
  record_count: number;
  issue_count: number;
}

export interface FactoryState {
  mode: string;
  status: string;
  cycle_count: number;
  readiness: FactoryReadiness;
  research_summary: ResearchSummary;
  paper_runtime: PaperRuntime;
  feed_health: Record<string, unknown>;
  families: Family[];
  archived_families?: Family[];
  model_league: ModelLeagueEntry[];
  lineages: Lineage[];
  current_lineages?: Lineage[];
  archived_lineages?: Lineage[];
  lineage_atlas: LineageAtlas;
  queue: QueueItem[];
  current_queue?: QueueItem[];
  archived_queue?: QueueItem[];
  connectors: ConnectorHealth[];
  manifests: { live_loadable: unknown[]; pending: unknown[] };
  agent_runs: AgentRun[];
  operator_signals: OperatorSignals;
  execution_bridge: ExecutionBridge;
}

export interface FactoryReadiness {
  score_pct: number;
  status: string;
  blockers: string[];
  warnings: string[];
  checks: { name: string; ok: boolean; reason: string }[];
  eta_to_readiness: string;
}

export interface ResearchSummary {
  active_lineage_count: number;
  lineage_count: number;
  family_count: number;
  retired_lineage_count: number;
  challenge_count: number;
  mutation_lineage_count: number;
  new_model_lineage_count: number;
  agent_generated_lineage_count: number;
  real_agent_lineage_count: number;
  artifact_backed_lineage_count: number;
  debug_reviewed_lineage_count: number;
  reviewed_lineage_count: number;
  tweaked_lineage_count: number;
  learning_memory_count: number;
  paper_pnl: number;
  positive_model_count: number;
  research_positive_model_count: number;
  ready_for_canary: number;
  live_loadable_manifest_count: number;
  manifest_publication_paused: boolean;
  operator_escalation_count: number;
  operator_action_inbox_count: number;
  human_action_required_count: number;
  maintenance_queue_count: number;
  paper_qualification_count: number;
  review_due_count: number;
  weak_family_count: number;
  incubating_family_count: number;
  generated_family_count: number;
  isolated_evidence_ready_family_count: number;
  prepared_isolated_lane_count: number;
}

export interface PaperRuntime {
  running_count: number;
  expected_count: number;
  assigned_count: number;
  candidate_count: number;
  failed_count: number;
  starting_count: number;
  retired_count: number;
  suppressed_count: number;
  research_only_count: number;
}

export interface AgentRun {
  run_id: string;
  generated_at: string;
  started_at?: string;
  completed_at?: string;
  task_type: string;
  model_class: string;
  provider: string;
  model: string;
  reasoning_effort: string;
  family_id: string;
  lineage_id: string;
  success: boolean;
  fallback_used: boolean;
  duration_ms: number;
  error: string | null;
  artifact_path: string;
  headline: string;
  notes: string[];
}

export interface Family {
  family_id: string;
  label: string;
  venue: string;
  status: string;
  target_venues?: string[];
  lineage_count: number;
  active_lineage_count: number;
  champion_lineage_id: string | null;
  champion_roi_pct: number;
  champion_trade_count: number;
  champion_paper_state?: string | null;
  champion_paper_reason?: string | null;
  current_runner_portfolio_id?: string | null;
  last_activity_at?: string | null;
  last_agent_run_at?: string | null;
  research_positive: boolean;
  curated_rankings?: unknown[];
  autopilot?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Lineage {
  lineage_id: string;
  family_id: string;
  role: string;
  current_stage: string;
  canonical_stage?: string;
  runtime_stage?: string;
  /** Iteration lifecycle status: active, failed, retiring, revived_for_paper, etc. */
  iteration_status?: string;
  roi_pct: number;
  monthly_roi_pct?: number;
  backtest_roi_pct?: number | null;
  backtest_sharpe?: number | null;
  backtest_gate_status?: string | null;
  trade_count: number;
  paper_days: number;
  /** Deterministic gate blockers as opaque strings from the registry */
  blockers?: string[];
  /** Structured promotion scorecard from promotion.decide() */
  promotion_scorecard?: Record<string, unknown>;
  assessment: Assessment;
  first_assessment: Assessment;
  runtime_lane_selected: boolean;
  runtime_lane_kind: string;
  runtime_lane_reason: string;
  runtime_target_portfolio: string | null;
  source_idea_id: string | null;
  suppressed_runtime_sibling: boolean;
  debug_agent: Record<string, unknown>;
  proposal_agent: Record<string, unknown>;
  /** Paper runtime status: paper_running | paper_starting | paper_candidate | retired | etc. */
  paper_runtime_status?: string;
  is_current_family_champion?: boolean;
  is_history_only?: boolean;
  paper_portfolio_id?: string | null;
  last_trade_at?: string | null;
  [key: string]: unknown;
}

export interface Assessment {
  phase: string;
  status: string;
  complete: boolean;
  completion_pct: number;
  roi_pct: number;
  paper_days_observed: number;
  paper_days_required: number;
  trade_count_observed: number;
  trade_count_required: number;
  trades_remaining: number;
  days_remaining: number;
  eta: string;
  slow_strategy: boolean;
}

export interface ModelLeagueEntry {
  family_id: string;
  label: string;
  origin: string;
  activation_status: string;
  incubation_status: string;
  incubation_cycle_created: number;
  incubation_decided_at: string | null;
  incubation_decision_reason: string | null;
  primary_incumbent_lineage_id: string;
  isolated_challenger_lineage_id: string;
  isolated_evidence_ready: boolean;
  prepared_isolated_lane_lineage_id: string | null;
  alias_runner_running: boolean;
  runtime_lane_reason: string;
  source_idea_id: string | null;
  rankings: ModelRanking[];
}

export interface ModelRanking {
  lineage_id: string;
  family_rank: number;
  current_stage: string;
  ranking_score: number;
  paper_roi_pct: number;
  backtest_roi_pct?: number | null;
  backtest_sharpe?: number | null;
  paper_realized_pnl: number;
  paper_closed_trade_count: number;
  paper_win_rate: number;
  strict_gate_pass: boolean;
  runtime_lane_kind: string;
  runtime_lane_reason: string;
  runtime_target_portfolio: string | null;
  canonical_target_portfolio: string | null;
  target_portfolio_id: string;
  assessment: Assessment;
  first_assessment: Assessment;
}

export interface OperatorSignals {
  escalation_candidates: unknown[];
  action_inbox: unknown[];
  maintenance_queue: MaintenanceItem[];
  paper_qualification_queue: unknown[];
  first_assessment_candidates: unknown[];
  positive_models: unknown[];
  research_positive_models: ResearchPositiveModel[];
  human_action_required: unknown[];
}

export interface MaintenanceItem {
  lineage_id: string;
  family_id: string;
  scope: string;
  source: string;
  action: string;
  priority: number;
  reason: string;
  requires_human: boolean;
  current_stage: string;
  iteration_status: string;
  execution_health_status: string;
  roi_pct: number;
  trade_count: number;
  paper_days: number;
  live_win_rate: number;
  recommended_actions: string[];
  assessment: Assessment;
  first_assessment: Assessment;
  last_maintenance_review_at: string | null;
  last_maintenance_review_status: string | null;
  last_maintenance_review_summary: string | null;
  last_maintenance_review_action: string | null;
  last_maintenance_review_artifact_path: string | null;
}

export interface ResearchPositiveModel {
  lineage_id: string;
  family_id: string;
  current_stage: string;
  evidence_source_type: string;
  roi_pct: number;
  trade_count: number;
  paper_days: number;
  live_roi_pct: number;
  live_trade_count: number;
  execution_health_status: string;
  curated_family_rank: number;
  curated_target_portfolio_id: string;
  assessment_complete: boolean;
  manifest_id: string | null;
}

export interface ExecutionBridge {
  runtime_mode: string;
  auto_start_enabled: boolean;
  desired_portfolio_count: number;
  running_portfolio_count: number;
  suppressed_portfolio_count: number;
  family_target_counts: Record<string, number>;
  suppressed_targets: { canonical_portfolio_id: string; families: string[] }[];
}

export interface PortfolioSnapshot {
  portfolio_id: string;
  label: string;
  category: string;
  currency: string;
  status: string;
  display_status: string;
  running: boolean;
  is_placeholder: boolean;
  blocked: boolean;
  has_runtime_state: boolean;
  starting_balance: number;
  current_balance: number;
  realized_pnl: number;
  roi_pct: number;
  drawdown_pct: number;
  trade_count: number;
  paper_days: number;
  heartbeat_ts: string | null;
  heartbeat_age_seconds: number | null;
  error: string | null;
  execution_health_status: string;
  execution_issue_codes: string[];
  execution_recommendation_context: unknown[];
  readiness_score_pct: number;
  readiness_status: string;
  readiness_blockers: string[];
  live_manifest_count: number;
  candidate_families: string[];
  candidate_context_count: number;
  state_excerpt: Record<string, unknown>;
  assessment: Assessment;
  first_assessment: Assessment;
  recent_events: PortfolioEvent[];
  recent_trades: PortfolioTrade[];
}

export interface PortfolioEvent {
  kind: string;
  data: Record<string, unknown>;
}

export interface PortfolioTrade {
  trade_id: string;
  symbol: string;
  side: string;
  status: string;
  pnl: number | null;
  [key: string]: unknown;
}

export interface ExecutionState {
  portfolio_count: number;
  archived_portfolio_count?: number;
  running_count: number;
  blocked_count: number;
  placeholder_count: number;
  realized_pnl_total: number;
  historical_realized_pnl_total?: number;
  current_paper_pnl?: number;
  portfolios: PortfolioSnapshot[];
  archived_portfolios?: PortfolioSnapshot[];
  placeholders: PortfolioSnapshot[];
}

export interface CompanyState {
  journal_markdown: string;
  recent_actions: RecentAction[];
  desks: Desk[];
  alerts: Alert[];
}

export interface RecentAction {
  action: string;
  ts: string;
  detail: string;
  [key: string]: unknown;
}

export interface Desk {
  desk_id: string;
  desk_kind: string;
  label: string;
  status: string;
  member_count: number;
  active_count: number;
  coverage_count: number;
  members: DeskMember[];
}

export interface DeskMember {
  name: string;
  display_name: string;
  status: string;
  families: string[];
  lineage_count: number;
  stages: string[];
  real_invocation_count: number;
  recent_mention: boolean;
}

export interface Alert {
  severity: string;
  title: string;
  detail: string;
  portfolio_id?: string;
}

export interface IdeasState {
  present: boolean;
  path: string;
  content: string;
  line_count: number;
  idea_count: number;
  active_count: number;
  archived_count: number;
  status_counts: {
    new: number;
    adapted: number;
    incubated: number;
    tested: number;
    promoted: number;
    rejected: number;
  };
  items: IdeaItem[];
  archived_items: IdeaItem[];
}

export interface IdeaItem {
  idea_id: string;
  title: string;
  summary: string;
  status: string;
  source: string;
  source_path: string;
  rank: number;
  tags: string[];
  family_candidates: string[];
  family_count: number;
  lineage_count: number;
  related_lineage_ids: string[];
}

export interface QueueItem {
  queue_id: string;
  lineage_id: string;
  family_id: string;
  experiment_id: string;
  role: string;
  status: string;
  priority: number;
  current_stage: string;
  notes: string[];
  created_at: string;
  updated_at: string;
}

export interface LineageAtlas {
  summary: Record<string, unknown>;
  families: LineageAtlasFamily[];
}

export interface LineageAtlasFamily {
  family_id: string;
  label: string;
  nodes: LineageAtlasNode[];
  root_lineage_ids: string[];
  history: LineageAtlasEvent[];
}

export interface LineageAtlasNode {
  lineage_id: string;
  role: string;
  parent_id?: string | null;
  parent_lineage_id?: string | null;
  children?: string[];
  child_lineage_ids?: string[];
  stage?: string;
  current_stage?: string;
  roi_pct?: number;
  monthly_roi_pct?: number;
  trade_count: number;
  [key: string]: unknown;
}

export interface LineageAtlasEvent {
  ts: string;
  kind: string;
  lineage_id: string;
  detail: string;
  [key: string]: unknown;
}

export interface ChartPayload {
  portfolio_id: string;
  currency: string;
  current_balance: number;
  starting_balance: number;
  points?: { ts: string; balance: number }[];
  balance_points?: { ts: string; balance: number }[];
  trades: ChartTrade[];
}

export interface ChartTrade {
  ts: string;
  /** Backend emits "trade_opened" or "trade_closed" — not "open"/"close" */
  kind: 'trade_opened' | 'trade_closed' | string;
  trade_id: string;
  symbol: string;
  side: string;
  pnl: number | null;
  status?: string;
}

// ---------------------------------------------------------------------------
// Snapshot v2 — versioned contract
// ---------------------------------------------------------------------------

export interface DeterministicBlocker {
  code: string;
  description: string;
  evidence: string | null;
}

export interface RuntimeV2 {
  backend: 'mobkit' | 'legacy' | string;
  mode: string;
  paused: boolean;
  paper_holdoff_enabled: boolean;
  venue_scope: string[] | null;
}

export interface LineageV2 {
  lineage_id: string;
  family_id: string;
  venue: string;
  canonical_stage: string;
  deterministic_blockers: DeterministicBlocker[];
  holdoff_reason: string | null;
  venue_scope_reason: string | null;
  paper_portfolio_id: string | null;
  paper_state?: string | null;
  paper_reason?: string | null;
  feed_gate_status?: string | null;
  feed_gate_reason?: string | null;
  runner_gate_status?: string | null;
  runner_gate_reason?: string | null;
  grace_deadline_at?: string | null;
  /** ISO timestamp from registry lineage record — used for time-in-stage calculations */
  created_at: string | null;
}

// ── Mobkit health proxy ──────────────────────────────────────────────────────

export interface MobkitHealth {
  configured: boolean;
  backend: string;
  /** null until direct gateway health check is implemented */
  rpc_healthy: boolean | null;
  recent_runs_24h: number;
  recent_failures_24h: number;
  fallback_used_24h: number;
  success_rate_pct: number | null;
  runs_by_provider: Record<string, number>;
  runs_by_task: Record<string, number>;
  runs_by_model_class: Record<string, number>;
  note: string;
}

// ── Budget governance ────────────────────────────────────────────────────────

export interface BudgetGovernance {
  daily_budget_usd: number | null;
  weekly_budget_usd: number | null;
  strict_budgets: boolean;
  force_cheap_ratio: number;
  single_agent_ratio: number;
  reviewer_removal_ratio: number;
  /** null — not tracked yet, backend gap */
  daily_spend_usd: number | null;
  weekly_spend_usd: number | null;
  token_count_total: number | null;
  note?: string;
}

// ── DNA packets per family ───────────────────────────────────────────────────

export interface DNAAncestor {
  lineage_id: string;
  roi: number;
  trades: number;
  outcome: string;
  domains: string[];
}

export interface DNAPacket {
  family_id: string;
  total_lineages_seen: number;
  failure_motifs: string[];
  success_motifs: string[];
  hard_veto_causes: string[];
  retirement_reasons: string[];
  dominant_failure: string | null;
  best_known_roi: number | null;
  best_ancestors: DNAAncestor[];
  worst_relatives: { lineage_id: string; roi: number; outcome: string }[];
  prompt_text: string;
}

// ── Goldfish health ──────────────────────────────────────────────────────────

export interface GoldfishHealth {
  enabled: boolean;
  learning_files: number;
  latest_write: string | null;
  workspace_root: string;
  artefact_root?: string;
  strict_mode: boolean;
  note: string;
}

// ── SnapshotV2 ───────────────────────────────────────────────────────────────

export interface SnapshotV2 extends DashboardSnapshot {
  schema_version: 'v2';
  runtime: RuntimeV2;
  lineage_v2: LineageV2[];
  mobkit_health: MobkitHealth;
  budget_governance: BudgetGovernance;
  dna_packets: DNAPacket[];
  goldfish_health: GoldfishHealth;
}
