const REFRESH_MS = 5000;
const uiState = {
  activeTab: "overview",
  selectedFamilyId: null,
  selectedLineageId: null,
};
let latestSnapshot = null;
const pnlCharts = new Map();

function formatNumber(value, digits = 2) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "0";
  return number.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatCurrency(value, currency = "USD") {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return `0 ${currency}`;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(number);
}

function formatDateTime(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDate(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatAgeCompact(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "n/a";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  if (value < 86400) return `${Math.round(value / 3600)}h`;
  return `${Math.round(value / 86400)}d`;
}

function pillClass(kind) {
  if (["live_ready", "approved_live", "running", "active", "healthy", "positive"].includes(kind)) return "status-pill status-ok";
  if (["blocked", "error", "critical", "start_failed"].includes(kind)) return "status-pill status-critical";
  if (["model_active"].includes(kind)) return "status-pill status-ok";
  if (["coverage_only"].includes(kind)) return "status-pill status-warning";
  if (["warning", "paper_validating", "research_only", "autostart_disabled", "validation_blocked", "degraded", "adapted", "tested"].includes(kind)) return "status-pill status-warning";
  if (["promoted"].includes(kind)) return "status-pill status-ok";
  if (["rejected"].includes(kind)) return "status-pill status-critical";
  return "status-pill status-info";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function basenamePath(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const parts = text.split("/");
  return parts[parts.length - 1] || text;
}

function renderAssessment(assessment) {
  const item = assessment || {};
  const pct = formatNumber(item.completion_pct || 0, 0);
  const eta = item.eta || "n/a";
  const status = item.status || "info";
  const observedDays = formatNumber(item.paper_days_observed || 0, 0);
  const requiredDays = formatNumber(item.paper_days_required || 0, 0);
  const observedTrades = formatNumber(item.trade_count_observed || 0, 0);
  const requiredTrades = formatNumber(item.trade_count_required || 0, 0);
  const daysRemaining = formatNumber(item.days_remaining || 0, 0);
  const tradesRemaining = formatNumber(item.trades_remaining || 0, 0);
  const verdict = item.complete
    ? "fully assessed"
    : `not enough evidence${daysRemaining > 0 || tradesRemaining > 0 ? ` · needs ${daysRemaining}d / ${tradesRemaining} trades` : ""}`;
  return `
    <div class="cell-stack">
      <span class="${pillClass(status)}">${escapeHtml(`${pct}% assessed`)}</span>
      <span class="${pillClass(item.complete ? "active" : "warning")}">${escapeHtml(verdict)}</span>
      <span class="card-subtitle">${escapeHtml(eta)}</span>
      <span class="card-subtitle">${escapeHtml(`${observedDays}/${requiredDays} days · ${observedTrades}/${requiredTrades} trades`)}</span>
    </div>
  `;
}

function renderKpis(snapshot) {
  const factory = snapshot.factory;
  const execution = snapshot.execution;
  const ideas = snapshot.ideas;
  const research = factory.research_summary || {};
  const paperRuntime = factory.paper_runtime || {};
  const readiness = factory.readiness || {};
  const agentRuns = factory.agent_runs || [];
  const apiHealth = snapshot.api_health || {};
  const cards = [
    {
      label: "API Health",
      value: (apiHealth.status || "unknown").toUpperCase(),
      detail: apiHealth.snapshot_source || "factory_state",
    },
    {
      label: "Factory Readiness",
      value: `${formatNumber(readiness.score_pct, 0)}%`,
      detail: readiness.status || "unknown",
    },
    {
      label: "Active Lineages",
      value: formatNumber(research.active_lineage_count, 0),
      detail: `${formatNumber(research.challenge_count, 0)} challengers in rotation`,
    },
    {
      label: "Paper Runtime",
      value: formatNumber(execution.running_count, 0),
      detail: `${formatNumber(paperRuntime.running_count || 0, 0)} / ${formatNumber(paperRuntime.expected_count || 0, 0)} expected lineages running · ${formatNumber(paperRuntime.research_only_count || 0, 0)} research-only`,
    },
    {
      label: "Best Agent P&L",
      value: execution.best_performer
        ? formatCurrency(execution.best_performer.realized_pnl || 0, execution.best_performer.currency || "USD")
        : formatCurrency(0, "USD"),
      detail: execution.best_performer
        ? `${execution.best_performer.label} · ${formatNumber(execution.best_performer.roi_pct || 0)}% ROI`
        : "No live paper performer yet",
    },
    {
      label: "Pending Manifests",
      value: formatNumber((factory.manifests?.pending || []).length, 0),
      detail: `${formatNumber((factory.manifests?.live_loadable || []).length, 0)} approved for live load`,
    },
    {
      label: "Operator Reviews",
      value: formatNumber((factory.operator_signals?.escalation_candidates || []).length, 0),
      detail: `${formatNumber((factory.operator_signals?.action_inbox || []).length, 0)} inbox · ${formatNumber((factory.operator_signals?.maintenance_queue || []).length, 0)} maintenance · ${formatNumber((factory.operator_signals?.paper_qualification_queue || []).length, 0)} paper-qualify · ${formatNumber((research.human_action_required_count || 0), 0)} human blockers`,
    },
    {
      label: "Real Agent Runs",
      value: formatNumber(agentRuns.length, 0),
      detail: `${formatNumber((research.real_agent_lineage_count || 0), 0)} lineages with real-agent origins`,
    },
    {
      label: "Idea Notes",
      value: formatNumber(ideas.idea_count || 0, 0),
      detail: ideas.present ? `${formatNumber((ideas.status_counts || {}).incubated || 0, 0)} incubated · ${formatNumber((ideas.status_counts || {}).tested || 0, 0)} tested · ${formatNumber((ideas.status_counts || {}).promoted || 0, 0)} promoted · ${formatNumber((ideas.status_counts || {}).rejected || 0, 0)} rejected` : "waiting for ideas.md",
    },
  ];

  document.getElementById("kpi-grid").innerHTML = cards
    .map(
      (card) => `
        <article class="kpi-card">
          <span class="tiny-label">${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <p>${escapeHtml(card.detail)}</p>
        </article>
      `
    )
    .join("");
}

function renderFeedHealth(snapshot) {
  const health = snapshot.factory.feed_health || {};
  const container = document.getElementById("feed-health-strip");
  const connectorTags = (health.connectors || [])
    .slice(0, 4)
    .map((item) => {
      const label = item.venue || item.connector_id || "feed";
      return `
        <span class="feed-health-chip">
          <span class="${pillClass(item.status || "info")}">${escapeHtml(item.status || "info")}</span>
          <span>${escapeHtml(label)}</span>
        </span>
      `;
    })
    .join("");

  const latestText = health.latest_age_seconds != null ? `Latest ${formatAgeCompact(health.latest_age_seconds)} ago` : "No recent payload";
  container.innerHTML = `
    <div class="feed-health-card">
      <div class="feed-health-copy">
        <span class="tiny-label">Data Feeds</span>
        <div class="feed-health-row">
          <strong>${escapeHtml(health.headline || "No data feeds configured")}</strong>
          <span class="${pillClass(health.status || "info")}">${escapeHtml(health.status || "info")}</span>
        </div>
        <p>${escapeHtml(health.summary || "Connector health will appear here once feeds are wired into factory state.")}</p>
      </div>
      <div class="feed-health-meta">
        <span class="card-subtitle">${escapeHtml(latestText)}</span>
        <div class="feed-health-tags">${connectorTags || `<span class="tag">no connectors</span>`}</div>
      </div>
    </div>
  `;
}

function renderAlerts(snapshot) {
  const alerts = snapshot.company.alerts || [];
  const container = document.getElementById("alert-list");
  if (!alerts.length) {
    container.innerHTML = `<div class="alert-card" data-severity="info"><h3>No critical alerts</h3><p>The control room has no active escalations.</p></div>`;
    return;
  }
  container.innerHTML = alerts
    .map(
      (alert) => `
        <article class="alert-card" data-severity="${escapeHtml(alert.severity)}">
          <header class="panel-header">
            <h3>${escapeHtml(alert.title)}</h3>
            <span class="${pillClass(alert.severity)}">${escapeHtml(alert.severity)}</span>
          </header>
          <p>${escapeHtml(alert.detail)}</p>
        </article>
      `
    )
    .join("");
}

function renderEscalations(snapshot) {
  const signals = snapshot.factory.operator_signals || {};
  const escalations = signals.escalation_candidates || [];
  const positives = signals.positive_models || [];
  const humanActions = signals.human_action_required || [];
  const container = document.getElementById("escalation-list");
  if (!escalations.length && !positives.length && !humanActions.length) {
    container.innerHTML = `<div class="alert-card" data-severity="info"><h3>No operator escalations</h3><p>The factory is not asking for human review yet.</p></div>`;
    return;
  }
  const humanCards = humanActions.map(
    (item) => `
      <article class="alert-card" data-severity="${escapeHtml(item.execution_health_status === "critical" ? "critical" : "warning")}">
        <header class="panel-header">
          <h3>${escapeHtml(item.family_id)} · human action required</h3>
          <span class="${pillClass(item.execution_health_status === "critical" ? "critical" : "warning")}">fix</span>
        </header>
        <p>${escapeHtml(item.human_action || item.summary || "Operator intervention required.")}</p>
        <p class="card-subtitle">${escapeHtml(item.lineage_id)} · ${escapeHtml(item.bug_category || "runtime_bug")}</p>
      </article>
    `
  );
  const escalationCards = escalations.map(
    (item) => `
      <article class="alert-card" data-severity="positive">
        <header class="panel-header">
          <h3>${escapeHtml(item.family_id)} · review for real trading</h3>
          <span class="${pillClass("positive")}">review</span>
        </header>
        <p>${escapeHtml(item.lineage_id)} is ${escapeHtml(item.current_stage)} with ${formatNumber(item.roi_pct)}% paper ROI across ${escapeHtml(item.trade_count)} trades and ${escapeHtml(item.paper_days)} days.</p>
        <p class="card-subtitle">Rank #${escapeHtml(item.curated_family_rank || "n/a")} · manifest ${escapeHtml(item.manifest_id || "pending")}</p>
        ${renderAssessment(item.assessment)}
      </article>
    `
  );
  const positiveCards = positives.slice(0, 4).map(
    (item) => `
      <article class="alert-card" data-severity="${escapeHtml(item.replacement_pressure ? "warning" : item.independent_live_evidence ? "positive" : "warning")}">
        <header class="panel-header">
          <h3>${escapeHtml(item.family_id)} · positive ROI</h3>
          <span class="${pillClass(item.replacement_pressure ? "warning" : item.independent_live_evidence ? "positive" : "warning")}">${escapeHtml(item.replacement_pressure ? "watch" : item.independent_live_evidence ? "green" : "fragile")}</span>
        </header>
        <p>${escapeHtml(item.lineage_id)} is at ${formatNumber(item.roi_pct)}% ROI across ${escapeHtml(item.trade_count)} trades.</p>
        <p class="card-subtitle">Stage ${escapeHtml(item.current_stage)} · rank #${escapeHtml(item.curated_family_rank || "n/a")}${item.shared_lineage_count > 1 ? ` · shared by ${escapeHtml(item.shared_lineage_count)} lineages via ${escapeHtml(item.curated_target_portfolio_id || "shared portfolio")}` : ""}</p>
        <p class="card-subtitle">${escapeHtml(item.independent_live_evidence ? "independent live paper evidence" : "shared or incomplete live evidence")} ${item.replacement_pressure ? `· replacement pressure (${item.replacement_pressure_reason || "active"})` : ""}</p>
        ${renderAssessment(item.assessment)}
      </article>
    `
  );
  container.innerHTML = humanCards.concat(escalationCards, positiveCards).join("");
}

function renderOperatorInbox(snapshot) {
  const inbox = (snapshot.factory.operator_signals || {}).action_inbox || [];
  const container = document.getElementById("operator-inbox-list");
  if (!inbox.length) {
    container.innerHTML = `<article class="queue-item"><header class="panel-header"><h3>No pending operator actions</h3><span class="${pillClass("active")}">clear</span></header><p>The factory does not currently need a human decision for real-trading review or human-only blockers.</p></article>`;
    return;
  }
  container.innerHTML = inbox
    .slice(0, 12)
    .map(
      (item) => `
        <article class="queue-item">
          <header class="panel-header">
            <h3>${escapeHtml(item.family_id || "unknown_family")} · ${escapeHtml(item.signal_type || "operator_action")}</h3>
            <span class="${pillClass(item.signal_type === "human_action_required" ? "warning" : "positive")}">${escapeHtml(item.requested_action || "review")}</span>
          </header>
          <p>${escapeHtml(item.summary || "Operator review required.")}</p>
          <p class="card-subtitle">${escapeHtml(item.lineage_id || "family-level")} · status ${escapeHtml(item.status || "open")}</p>
          <div class="tag-row">
            ${(item.available_decisions || []).map((decision) => `<span class="tag">${escapeHtml(decision)}</span>`).join("")}
          </div>
        </article>
      `
    )
    .join("");
}

function renderMaintenanceQueue(snapshot) {
  const queue = (snapshot.factory.operator_signals || {}).maintenance_queue || [];
  const recentRuns = (snapshot.factory.agent_runs || []).filter((row) => row.task_type === "maintenance_resolution_review");
  const container = document.getElementById("maintenance-queue-list");
  if (!queue.length) {
    container.innerHTML = `<article class="queue-item"><header class="panel-header"><h3>No pending maintenance pressure</h3><span class="${pillClass("active")}">clear</span></header><p>The factory has no queued replace, retrain, retire, or isolated-lane actions right now.</p></article>`;
    return;
  }
  container.innerHTML = queue
    .slice(0, 12)
    .map((item) => {
      const recentRun = recentRuns.find(
        (row) =>
          row.lineage_id === item.lineage_id &&
          row.family_id === item.family_id
      );
      return `
        <article class="queue-item">
          <header class="panel-header">
            <h3>${escapeHtml(item.family_id)} · ${escapeHtml(item.action || "maintain")}</h3>
            <span class="${pillClass(item.execution_health_status || "warning")}">${escapeHtml(item.execution_health_status || "queued")}</span>
          </header>
          <p>${escapeHtml(item.reason || "Maintenance action queued.")}</p>
          <p class="card-subtitle">${escapeHtml(item.lineage_id || "family-level")} · ${escapeHtml(item.current_stage || "n/a")} · ${escapeHtml(item.iteration_status || "n/a")}</p>
          <div class="metric-row"><span class="metric-label">ROI / Trades</span><strong>${formatNumber(item.roi_pct || 0)}% · ${escapeHtml(item.trade_count || 0)}</strong></div>
          <div class="metric-row"><span class="metric-label">Assessment</span><strong>${formatNumber(((item.assessment || {}).completion_pct) || 0, 0)}% · ${escapeHtml(((item.assessment || {}).eta) || "n/a")}</strong></div>
          ${item.last_maintenance_review_at ? `<p class="card-subtitle">maintenance review ${escapeHtml(item.last_maintenance_review_status || "done")} · ${escapeHtml(item.last_maintenance_review_action || "hold")} · ${escapeHtml(item.last_maintenance_review_summary || "")}</p>` : recentRun ? `<p class="card-subtitle">live maintenance review · ${escapeHtml(recentRun.provider || "codex")} ${escapeHtml(recentRun.model || "")} · ${escapeHtml(recentRun.headline || "")}</p>` : ""}
          <div class="tag-row">
            <span class="tag">priority ${escapeHtml(item.priority || 0)}</span>
            <span class="tag">${escapeHtml(item.source || "factory")}</span>
            ${item.requires_human ? `<span class="tag">human</span>` : ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function renderPaperQualificationQueue(snapshot) {
  const queue = (snapshot.factory.operator_signals || {}).paper_qualification_queue || [];
  const container = document.getElementById("paper-qualification-list");
  if (!queue.length) {
    container.innerHTML = `<article class="queue-item"><header class="panel-header"><h3>No pending paper qualification</h3><span class="${pillClass("active")}">clear</span></header><p>No promising challenger is currently waiting for a first live paper read.</p></article>`;
    return;
  }
  container.innerHTML = queue
    .slice(0, 12)
    .map(
      (item) => `
        <article class="queue-item">
          <header class="panel-header">
            <h3>${escapeHtml(item.family_id)} · first paper read</h3>
            <span class="${pillClass(item.execution_health_status || "warning")}">${escapeHtml(item.execution_health_status || "queued")}</span>
          </header>
          <p>${escapeHtml(item.reason || "Promising challenger queued for live paper validation.")}</p>
          <p class="card-subtitle">${escapeHtml(item.lineage_id || "unknown lineage")} · ${escapeHtml(item.current_stage || "n/a")} · ${escapeHtml(item.iteration_status || "n/a")}</p>
          <div class="metric-row"><span class="metric-label">Research ROI / Trades</span><strong>${formatNumber(item.research_roi_pct || 0)}% · ${escapeHtml(item.research_trade_count || 0)}</strong></div>
          <div class="metric-row"><span class="metric-label">Live Paper</span><strong>${escapeHtml(item.live_trade_count || 0)} trades · ${escapeHtml(((item.first_assessment || {}).eta) || "first read pending")}</strong></div>
          <div class="tag-row">
            <span class="tag">${escapeHtml(item.source || "factory")}</span>
            <span class="tag">${escapeHtml(item.lane_reason || "qualification")}</span>
          </div>
        </article>
      `
    )
    .join("");
}

function renderDesks(snapshot) {
  const desks = snapshot.company.desks || [];
  document.getElementById("desks").innerHTML = desks
    .map(
      (desk) => `
        <section class="desk-card">
          <header>
            <div>
              <h3>${escapeHtml(desk.label)}</h3>
              <p class="card-subtitle">${escapeHtml(desk.active_count)} ${escapeHtml(desk.desk_kind === "algorithmic_control" ? "model-active algorithms" : "model-active agents")} · ${escapeHtml(desk.coverage_count || 0)} coverage-only of ${escapeHtml(desk.member_count)} ${escapeHtml(desk.desk_kind === "algorithmic_control" ? "systems" : "operators")}</p>
            </div>
            <span class="${pillClass(desk.status || "standby")}">${escapeHtml((desk.status || "standby").replaceAll("_", "-"))}</span>
          </header>
          <p class="card-subtitle">${escapeHtml(desk.desk_kind === "algorithmic_control" ? "Deterministic control logic. These are algorithms, not autonomous model-backed agents." : "Model-backed research and review workers.")}</p>
          <div class="desk-members">
            ${(desk.members || [])
              .map(
                (member) => `
                  <article class="desk-member">
                    <strong>${escapeHtml(member.display_name || member.name)}</strong>
                    <p>${escapeHtml(member.lineage_count)} attached lineages</p>
                    <p>${escapeHtml(member.real_invocation_count || 0)} ${escapeHtml(desk.desk_kind === "algorithmic_control" ? "real model handoffs" : "real model runs")}</p>
                    <div class="tag-row">
                      <span class="${pillClass(member.status)}">${escapeHtml(String(member.status || "standby").replaceAll("_", "-"))}</span>
                      ${(member.stages || []).slice(0, 2).map((stage) => `<span class="tag">${escapeHtml(stage)}</span>`).join("")}
                    </div>
                  </article>
                `
              )
              .join("")}
          </div>
        </section>
      `
    )
    .join("");
}

function renderFamilies(snapshot) {
  const families = snapshot.factory.families || [];
  document.getElementById("family-grid").innerHTML = families
    .map(
      (family) => `
        <article class="family-card">
          <header>
            <div>
              <h3 class="card-title">${escapeHtml(family.label)}</h3>
              <p class="card-subtitle">${escapeHtml(family.family_id)}</p>
            </div>
            <span class="${pillClass(family.queue_stage)}">${escapeHtml(family.queue_stage)}</span>
          </header>
          <div class="metric-row"><span class="metric-label">Champion</span><strong>${escapeHtml(family.champion_lineage_id || "n/a")}</strong></div>
          <div class="metric-row"><span class="metric-label">Champion ROI</span><strong>${formatNumber(family.champion_roi_pct)}%</strong></div>
          <div class="metric-row"><span class="metric-label">Champion Fitness</span><strong>${formatNumber(family.champion_fitness)}</strong></div>
          <div class="metric-row"><span class="metric-label">Active / Retired</span><strong>${escapeHtml(family.active_lineage_count)} / ${escapeHtml(family.retired_lineage_count)}</strong></div>
          <div class="metric-row"><span class="metric-label">Paper Runtime</span><strong>${escapeHtml(family.paper_runtime_running_count || 0)} / ${escapeHtml(family.paper_runtime_expected_count || 0)} running</strong></div>
          ${(family.paper_runtime_statuses || []).length ? `<p class="card-subtitle">${escapeHtml((family.paper_runtime_statuses || []).join(" · "))}</p>` : ""}
          ${renderFamilyAutopilot(family)}
          ${renderFamilyLeagueHint(family)}
          <div class="tag-row">
            ${(family.target_portfolios || []).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}
          </div>
        </article>
      `
    )
    .join("");
}

function renderFamilyLeagueHint(family) {
  const leader = (family.curated_rankings || [])[0];
  if (!leader) return "";
  return `
    <div class="metric-row">
      <span class="metric-label">Curated Leader</span>
      <strong>#${escapeHtml(leader.family_rank)} · ${formatNumber(leader.ranking_score)}</strong>
    </div>
  `;
}

function renderFamilyAutopilot(family) {
  if (!family.weak_family) return "";
  const actions = (family.autopilot_actions || []).slice(0, 3).join(", ") || "monitor";
  const winRatePct = formatNumber((family.autopilot_live_win_rate || 0) * 100);
  const detail = family.autopilot_reason || `${actions} in progress`;
  return `
    <div class="metric-row">
      <span class="metric-label">Factory Autopilot</span>
      <strong>${escapeHtml(family.autopilot_status || "autopilot_active")}</strong>
    </div>
    <div class="metric-row">
      <span class="metric-label">Autopilot Actions</span>
      <strong>${escapeHtml(actions)}</strong>
    </div>
    <div class="metric-row">
      <span class="metric-label">Live Read</span>
      <strong>${formatNumber(family.autopilot_live_roi_pct)}% ROI · ${winRatePct}% win · ${escapeHtml(family.autopilot_trade_count)} trades</strong>
    </div>
    <p class="card-subtitle">${escapeHtml(detail)}</p>
  `;
}

function renderModelLeague(snapshot) {
  const families = snapshot.factory.model_league || [];
  const container = document.getElementById("ranking-grid");
  if (!families.length) {
    container.innerHTML = `<article class="family-card"><p>No curated model rankings available yet.</p></article>`;
    return;
  }
  container.innerHTML = families
    .map(
      (family) => `
        <article class="family-card">
          <header>
            <div>
              <h3 class="card-title">${escapeHtml(family.label)}</h3>
              <p class="card-subtitle">${escapeHtml(family.family_id)}</p>
            </div>
            <span class="${pillClass((family.rankings || []).length ? "active" : "info")}">${escapeHtml((family.rankings || []).length ? "ranked" : "pending")}</span>
          </header>
          <div class="league-stack">
            ${(family.rankings || [])
              .map(
                (row) => `
                  <article class="league-row">
                    <div>
                      <strong>#${escapeHtml(row.family_rank)} · ${escapeHtml(row.lineage_id)}</strong>
                      <p class="card-subtitle">${escapeHtml(row.target_portfolio_id || "no portfolio")} · ${escapeHtml(row.current_stage || "n/a")}</p>
                    </div>
                    <div class="league-metrics">
                      <span>${formatNumber(row.ranking_score)}</span>
                      <span class="card-subtitle">ROI ${formatNumber(row.paper_roi_pct)}% · win ${formatNumber((row.paper_win_rate || 0) * 100)}%</span>
                      <span class="card-subtitle">${escapeHtml(row.paper_closed_trade_count)} closed trades</span>
                      <span class="card-subtitle">${escapeHtml(`${formatNumber((row.assessment || {}).completion_pct || 0, 0)}% assessed · ${((row.assessment || {}).eta || "n/a")}`)}</span>
                    </div>
                  </article>
                `
              )
              .join("")}
          </div>
        </article>
      `
    )
    .join("");
}

async function renderPnlChart(portfolioId, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  try {
    const resp = await fetch(`/api/portfolio/${encodeURIComponent(portfolioId)}/chart`, { cache: "no-store" });
    if (!resp.ok) {
      container.innerHTML = `<p class="card-subtitle">No chart data available.</p>`;
      return;
    }
    const data = await resp.json();
    if (!data.points || !data.points.length) {
      container.innerHTML = `<p class="card-subtitle">No balance history yet.</p>`;
      return;
    }

    const pnlData = data.points.map((pt) => ({ x: new Date(pt.ts).getTime(), y: pt.pnl }));

    let tradeCounter = 0;
    const tradePoints = data.trades.map((t) => {
      tradeCounter++;
      const tsMs = new Date(t.ts).getTime();
      const closest = data.points.reduce((best, pt) => {
        const d = Math.abs(new Date(pt.ts).getTime() - tsMs);
        return d < best.d ? { d, pnl: pt.pnl } : best;
      }, { d: Infinity, pnl: 0 });
      return {
        x: tsMs,
        y: closest.pnl,
        kind: t.kind,
        symbol: t.symbol,
        side: t.side,
        pnl: t.pnl,
        label: `Trade ${tradeCounter}`,
      };
    });

    const openPoints = tradePoints.filter((p) => p.kind === "trade_opened");
    const closePoints = tradePoints.filter((p) => p.kind === "trade_closed");

    const existing = pnlCharts.get(containerId);
    if (existing) {
      existing.data.datasets[0].data = pnlData;
      existing.data.datasets[1].data = openPoints;
      existing.data.datasets[2].data = closePoints;
      existing.update("none");
      return;
    }

    const canvasEl = document.createElement("canvas");
    container.innerHTML = "";
    container.appendChild(canvasEl);

    const monoFont = "'Menlo', 'SFMono-Regular', 'Consolas', monospace";
    const gridColor = "rgba(31, 27, 22, 0.10)";
    const accentColor = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#8c2f1b";
    const okColor = getComputedStyle(document.documentElement).getPropertyValue("--ok").trim() || "#2b6d36";
    const dangerColor = getComputedStyle(document.documentElement).getPropertyValue("--danger").trim() || "#a32222";

    const chart = new Chart(canvasEl, {
      type: "line",
      data: {
        datasets: [
          {
            label: "P&L",
            data: pnlData,
            borderColor: accentColor,
            backgroundColor: `${accentColor}18`,
            borderWidth: 2,
            fill: true,
            tension: 0.25,
            pointRadius: 0,
            pointHitRadius: 6,
            order: 2,
          },
          {
            label: "Trade Opened",
            data: openPoints,
            type: "scatter",
            pointRadius: 5,
            pointHoverRadius: 7,
            pointBackgroundColor: okColor,
            pointBorderColor: "#fff",
            pointBorderWidth: 1.5,
            showLine: false,
            order: 1,
          },
          {
            label: "Trade Closed",
            data: closePoints,
            type: "scatter",
            pointRadius: 5,
            pointHoverRadius: 7,
            pointBackgroundColor: dangerColor,
            pointBorderColor: "#fff",
            pointBorderWidth: 1.5,
            showLine: false,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(31,27,22,0.92)",
            titleFont: { family: monoFont, size: 11 },
            bodyFont: { family: monoFont, size: 11 },
            callbacks: {
              title(items) {
                if (!items.length) return "";
                const raw = items[0].raw;
                return raw.label || new Date(raw.x).toLocaleString();
              },
              label(item) {
                const raw = item.raw;
                if (raw.symbol) {
                  const pnlText = raw.pnl != null ? ` · P&L $${raw.pnl}` : "";
                  return `${raw.kind === "trade_opened" ? "OPEN" : "CLOSE"} ${raw.symbol} ${raw.side}${pnlText}`;
                }
                return `P&L: $${raw.y.toFixed(2)}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "time",
            grid: { color: gridColor, drawTicks: false },
            ticks: { font: { family: monoFont, size: 10 }, color: "rgba(31,27,22,0.5)", maxTicksLimit: 8 },
            border: { display: false },
          },
          y: {
            grid: { color: gridColor, drawTicks: false },
            ticks: {
              font: { family: monoFont, size: 10 },
              color: "rgba(31,27,22,0.5)",
              callback: (v) => `$${v}`,
            },
            border: { display: false },
          },
        },
      },
    });
    pnlCharts.set(containerId, chart);
  } catch {
    container.innerHTML = `<p class="card-subtitle">Chart load failed.</p>`;
  }
}

function renderPortfolios(snapshot) {
  const portfolios = snapshot.execution.portfolios || [];
  if (!portfolios.length) {
    document.getElementById("portfolio-grid").innerHTML = `
      <article class="portfolio-card">
        <header>
          <div>
            <h3 class="card-title">Execution Monitor</h3>
            <p class="card-subtitle">No lightweight portfolio rows available</p>
          </div>
          <span class="${pillClass("info")}">info</span>
        </header>
        <p class="card-subtitle">${escapeHtml(snapshot.execution.note || "Execution details are temporarily unavailable.")}</p>
      </article>
    `;
    return;
  }
  const prevExpanded = new Set();
  document.querySelectorAll(".pnl-chart-wrap.is-expanded").forEach((el) => {
    const id = el.dataset.portfolioId;
    if (id) prevExpanded.add(id);
  });

  for (const [chartId, chart] of pnlCharts) {
    chart.destroy();
    pnlCharts.delete(chartId);
  }

  document.getElementById("portfolio-grid").innerHTML = portfolios
    .map((portfolio) => {
      const status = portfolio.display_status || (portfolio.error ? "blocked" : (portfolio.status || "unknown"));
      const eventHint = (portfolio.recent_events || []).slice(-1)[0];
      const trainability = portfolio.trainability || {};
      const training = portfolio.training_progress || {};
      const trainabilityLabel = trainability.status || "n/a";
      const trainingDetail = `${formatNumber(training.labeled_examples || 0, 0)} labeled · ${formatNumber(training.pending_labels || 0, 0)} pending`;
      const modelDetail = `${formatNumber(trainability.trained_model_count || 0, 0)}/${formatNumber(trainability.required_model_count || 0, 0)} trained`;
      const chartId = `pnl-chart-${escapeHtml(portfolio.portfolio_id)}`;
      const hasActivity = Number(portfolio.realized_pnl || 0) !== 0 || Number(portfolio.trade_count || 0) > 0;
      const expanded = prevExpanded.has(portfolio.portfolio_id) || (prevExpanded.size === 0 && hasActivity);
      return `
        <article class="portfolio-card">
          <header>
            <div>
              <h3 class="card-title">${escapeHtml(portfolio.label)}</h3>
              <p class="card-subtitle">${escapeHtml(portfolio.portfolio_id)} · ${escapeHtml(portfolio.category)}</p>
            </div>
            <div class="portfolio-header-actions">
              <button class="pnl-toggle-btn" type="button" data-pnl-toggle="${escapeHtml(portfolio.portfolio_id)}">${expanded ? "Hide P&L" : "View P&L"}</button>
              <span class="${pillClass(status)}">${escapeHtml(status)}</span>
            </div>
          </header>
          <div class="metric-row"><span class="metric-label">Execution Health</span><strong><span class="${pillClass(portfolio.execution_health_status || "info")}">${escapeHtml(portfolio.execution_health_status || "unknown")}</span></strong></div>
          <div class="metric-row"><span class="metric-label">Balance</span><strong>${formatCurrency(portfolio.current_balance, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">Starting Bankroll</span><strong>${formatCurrency(portfolio.starting_balance, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">Realized PnL</span><strong>${formatCurrency(portfolio.realized_pnl, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">ROI / Drawdown</span><strong>${formatNumber(portfolio.roi_pct)}% / ${formatNumber(portfolio.drawdown_pct)}%</strong></div>
          <div class="metric-row"><span class="metric-label">Win Rate</span><strong>${formatNumber((portfolio.win_rate || 0) * 100)}% · ${escapeHtml(portfolio.wins || 0)}W / ${escapeHtml(portfolio.losses || 0)}L</strong></div>
          <div class="metric-row"><span class="metric-label">Heartbeat Age</span><strong>${portfolio.heartbeat_age_seconds == null ? "n/a" : `${formatNumber(portfolio.heartbeat_age_seconds, 1)}s`}</strong></div>
          <div class="metric-row"><span class="metric-label">Candidate Contexts</span><strong>${escapeHtml(portfolio.candidate_context_count)}</strong></div>
          <div class="metric-row"><span class="metric-label">Readiness</span><strong>${escapeHtml(portfolio.readiness_status || "n/a")} ${portfolio.readiness_score_pct ? `(${formatNumber(portfolio.readiness_score_pct, 0)}%)` : ""}</strong></div>
          <div class="metric-row"><span class="metric-label">Trainability</span><strong>${escapeHtml(trainabilityLabel)} · ${escapeHtml(modelDetail)}</strong></div>
          <div class="metric-row"><span class="metric-label">Training Flow</span><strong>${escapeHtml(trainingDetail)}</strong></div>
          <div class="metric-row"><span class="metric-label">Assessment</span><strong>${formatNumber((portfolio.assessment || {}).completion_pct || 0, 0)}% · ${escapeHtml((portfolio.assessment || {}).eta || "n/a")}</strong></div>
          <div class="metric-row"><span class="metric-label">Evidence</span><strong>${escapeHtml((portfolio.assessment || {}).complete ? "fully assessed" : "not enough evidence yet")}</strong></div>
          ${portfolio.error ? `<p class="card-subtitle">${escapeHtml(portfolio.error)}</p>` : ""}
          ${(portfolio.execution_recommendation_context || []).length ? `<p class="card-subtitle">${escapeHtml(portfolio.execution_recommendation_context[0])}</p>` : ""}
          ${(trainability.blocked_models || []).length ? `<p class="card-subtitle">blocked models: ${escapeHtml((trainability.blocked_models || []).join(", "))}</p>` : ""}
          <p class="card-subtitle">${escapeHtml(`${formatNumber((portfolio.assessment || {}).paper_days_observed || 0, 0)}/${formatNumber((portfolio.assessment || {}).paper_days_required || 0, 0)} days · ${formatNumber((portfolio.assessment || {}).trade_count_observed || 0, 0)}/${formatNumber((portfolio.assessment || {}).trade_count_required || 0, 0)} trades · remaining ${formatNumber((portfolio.assessment || {}).days_remaining || 0, 0)}d / ${formatNumber((portfolio.assessment || {}).trades_remaining || 0, 0)} trades`)}</p>
          ${eventHint ? `<div class="tag-row"><span class="tag">${escapeHtml(eventHint.kind || "event")}</span></div>` : ""}
          <div class="tag-row">
            ${(portfolio.execution_issue_codes || []).slice(0, 3).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}
            ${(portfolio.candidate_families || []).slice(0, 4).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}
          </div>
          <div class="pnl-chart-wrap ${expanded ? "is-expanded" : ""}" data-portfolio-id="${escapeHtml(portfolio.portfolio_id)}">
            <div class="pnl-chart-container" id="${chartId}"></div>
          </div>
        </article>
      `;
    })
    .join("");

  for (const pid of prevExpanded) {
    const chartId = `pnl-chart-${pid}`;
    renderPnlChart(pid, chartId);
  }
}

function renderQueue(snapshot) {
  const queue = snapshot.factory.queue || [];
  document.getElementById("queue-list").innerHTML = queue
    .slice(0, 16)
    .map(
      (item) => `
        <article class="queue-item">
          <header class="panel-header">
            <h3>${escapeHtml(item.family_id)} · ${escapeHtml(item.lineage_id)}</h3>
            <span class="${pillClass(item.current_stage)}">${escapeHtml(item.current_stage)}</span>
          </header>
          <p>${escapeHtml(item.status)} · priority ${escapeHtml(item.priority)}</p>
        </article>
      `
    )
    .join("");
}

function renderLineages(snapshot) {
  const rows = snapshot.factory.lineages || [];
  document.getElementById("lineage-table").innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.family_id)}<br><span class="card-subtitle">${escapeHtml(row.lineage_id)}</span></td>
          <td>${escapeHtml(row.current_stage)}</td>
          <td>${formatNumber(row.fitness_score)}</td>
          <td>${formatNumber(row.monthly_roi_pct)}%</td>
          <td>${escapeHtml(row.trade_count)}</td>
          <td>${renderLineageAgent(row)}</td>
          <td>${renderLineageExecution(row)}</td>
        </tr>
      `
    )
    .join("");
}

function renderLineageAgent(row) {
  const decision = row.latest_agent_decision || {};
  const proposal = row.proposal_agent || {};
  const reviewLine = row.last_agent_review_at
    ? `<span class="card-subtitle">review ${escapeHtml(row.last_agent_review_status || "done")} · ${escapeHtml(row.last_agent_review_at)}</span>`
    : (row.agent_review_due ? `<span class="card-subtitle">review due · ${escapeHtml(row.agent_review_due_reason || "scheduled")}</span>` : "");
  const debugLine = row.last_debug_review_at
    ? `<span class="card-subtitle">debug ${escapeHtml(row.last_debug_review_status || "done")} · ${escapeHtml(row.last_debug_bug_category || "runtime_bug")}${row.last_debug_requires_human ? " · human action" : ""}</span>`
    : "";
  if (decision.provider) {
    return `
      <span class="${pillClass(decision.used_real_agent ? "active" : "warning")}">${escapeHtml(decision.provider)}</span>
      <div class="cell-stack">
        <span>${escapeHtml(decision.model || "n/a")}</span>
        <span class="card-subtitle">${escapeHtml(decision.task_type || decision.kind || "")}</span>
        ${reviewLine}
        ${debugLine}
      </div>
    `;
  }
  if (proposal.provider) {
    return `
      <span class="${pillClass("active")}">${escapeHtml(proposal.provider)}</span>
      <div class="cell-stack">
        <span>${escapeHtml(proposal.model || "n/a")}</span>
        <span class="card-subtitle">${escapeHtml(proposal.task_type || "proposal_generation")}</span>
        ${reviewLine}
        ${debugLine}
      </div>
    `;
  }
  return `<span class="${pillClass("info")}">${escapeHtml(row.hypothesis_origin || "deterministic")}</span><div class="cell-stack">${reviewLine}${debugLine}</div>`;
}

function renderLineageExecution(row) {
  const signalTag = row.execution_has_signal ? "signal" : "quiet";
  const health = row.execution_health_status || "unknown";
  const runtimeIntent = row.paper_runtime_status || "research_only";
  const issue = row.latest_execution_refresh_selected || row.latest_retrain_action || (row.execution_issue_codes || [])[0];
  const refreshTag = row.latest_execution_refresh_status || "n/a";
  const scorecard = row.promotion_scorecard || {};
  const backtest = scorecard.backtest || {};
  const scorecardTag = backtest.comparison_required
    ? (backtest.comparison_passed ? "beats_incumbent" : "blocked_vs_incumbent")
    : "incumbent_n/a";
  const scorecardDetail =
    backtest.comparison_required && backtest.deltas
      ? `roi Δ ${formatNumber(backtest.deltas.roi_delta_pct)}`
      : "scorecard not required";
  const rankText = row.curated_family_rank ? `rank #${row.curated_family_rank}` : "rank n/a";
  const rankDetail = row.curated_target_portfolio_id
    ? `${row.curated_target_portfolio_id} · score ${formatNumber(row.curated_ranking_score)} · roi ${formatNumber(row.curated_paper_roi_pct)}%`
    : "no curated portfolio score";
  const debugDetail = row.last_debug_requires_human
    ? `human fix: ${row.last_debug_human_action || row.last_debug_bug_category || "required"}`
    : (row.last_debug_summary || "");
  const assessment = row.assessment || {};
  return `
    <span class="${pillClass(health)}">${escapeHtml(health)}</span>
    <div class="cell-stack">
      <span class="${pillClass(row.execution_has_signal ? "active" : "standby")}">${escapeHtml(signalTag)}</span>
      <span class="card-subtitle">paper intent: ${escapeHtml(runtimeIntent)}</span>
      <span class="card-subtitle">${escapeHtml(issue || "no acute issue")}</span>
      <span class="card-subtitle">${escapeHtml(`${formatNumber(assessment.completion_pct || 0, 0)}% assessed · ${assessment.eta || "n/a"}`)}</span>
      <span class="card-subtitle">refresh: ${escapeHtml(refreshTag)}</span>
      <span class="card-subtitle">${escapeHtml(scorecardTag)} · ${escapeHtml(scorecardDetail)}</span>
      <span class="card-subtitle">${escapeHtml(rankText)} · ${escapeHtml(rankDetail)}</span>
      ${debugDetail ? `<span class="card-subtitle">${escapeHtml(debugDetail)}</span>` : ""}
    </div>
  `;
}

function renderAgentRuns(snapshot) {
  const rows = snapshot.factory.agent_runs || [];
  const container = document.getElementById("agent-run-list");
  if (!rows.length) {
    container.innerHTML = `<div class="feed-item"><p>No real agent invocations yet. The demo family will appear here after a Codex-backed proposal or tweak runs.</p></div>`;
    return;
  }
  container.innerHTML = rows
    .slice(0, 12)
    .map(
      (row) => `
        <article class="feed-item agent-run-card">
          <header class="panel-header">
            <h3>${escapeHtml(row.task_type)} · ${escapeHtml(row.family_id || "unknown_family")}</h3>
            <span class="${pillClass(row.success ? (row.fallback_used ? "warning" : "active") : "warning")}">${escapeHtml(row.success ? (row.fallback_used ? "fallback" : "live") : "failed")}</span>
          </header>
          ${row.headline ? `<p><strong>${escapeHtml(row.headline)}</strong></p>` : ""}
          <p>${escapeHtml(row.provider)} / ${escapeHtml(row.model)} · ${escapeHtml(row.model_class)} · ${escapeHtml(row.reasoning_effort)}</p>
          <p>${escapeHtml(row.lineage_id || "family-level")} · ${escapeHtml(row.duration_ms)} ms</p>
          ${(row.notes || []).length ? `<div class="tag-row">${row.notes.map((note) => `<span class="tag">${escapeHtml(note)}</span>`).join("")}</div>` : ""}
          ${row.error ? `<p class="card-subtitle">${escapeHtml(row.error)}</p>` : ""}
        </article>
      `
    )
    .join("");
}

function renderJournal(snapshot) {
  const rows = snapshot.company.recent_actions || [];
  document.getElementById("journal-feed").innerHTML = rows
    .slice()
    .reverse()
    .map(
      (row) => `
        <article class="feed-item">
          <p>${escapeHtml(row)}</p>
        </article>
      `
    )
    .join("");
}

function renderIdeas(snapshot) {
  const ideas = snapshot.ideas || {};
  document.getElementById("ideas-tag").textContent = ideas.present ? "Loaded" : "Waiting";
  if (!ideas.present) {
    document.getElementById("ideas-shell").innerHTML = `<div class="idea-empty">Create <code>ideas.md</code> in the repo root and it will appear here automatically.</div>`;
    return;
  }
  const activeItems = ideas.items || [];
  const archivedItems = ideas.archived_items || [];
  const allItems = archivedItems.concat(activeItems);
  const summary = ideas.status_counts || {};
  const totalProcessed = (summary.incubated || 0) + (summary.tested || 0) + (summary.promoted || 0);
  const renderIdeaCard = (item) => `
    <article class="alert-card" data-severity="${escapeHtml(item.status || "info")}">
      <header>
        <h3>${escapeHtml(item.title || item.idea_id)}</h3>
        <span class="${pillClass(item.status || "info")}">${escapeHtml(item.status || "new")}</span>
      </header>
      <p>${escapeHtml(item.summary || "No summary yet.")}</p>
      <p class="card-subtitle">${escapeHtml((item.family_candidates || []).join(", ") || "unmapped")} · ${escapeHtml(item.lineage_count || 0)} lineages · ${escapeHtml(item.family_count || 0)} families</p>
      ${(item.related_lineage_ids || []).length ? `<div class="tag-row">${(item.related_lineage_ids || []).slice(0, 4).map((lid) => "<span class=\"tag\">" + escapeHtml(lid) + "</span>").join("")}${(item.related_lineage_ids || []).length > 4 ? "<span class=\"tag\">+" + ((item.related_lineage_ids || []).length - 4) + " more</span>" : ""}</div>` : ""}
    </article>
  `;
  const processedCards = archivedItems
    .filter((item) => ["incubated", "tested", "promoted"].includes(item.status))
    .slice(0, 12)
    .map(renderIdeaCard)
    .join("");
  const rejectedCards = archivedItems
    .filter((item) => item.status === "rejected")
    .slice(0, 6)
    .map(renderIdeaCard)
    .join("");
  const pipelineCards = activeItems.slice(0, 12).map(renderIdeaCard).join("");
  document.getElementById("ideas-shell").innerHTML = `
    <div class="tag-row">
      <span class="tag">new ${escapeHtml(summary.new || 0)}</span>
      <span class="tag">adapted ${escapeHtml(summary.adapted || 0)}</span>
      <span class="tag">incubated ${escapeHtml(summary.incubated || 0)}</span>
      <span class="tag">tested ${escapeHtml(summary.tested || 0)}</span>
      <span class="tag">promoted ${escapeHtml(summary.promoted || 0)}</span>
      <span class="tag">rejected ${escapeHtml(summary.rejected || 0)}</span>
    </div>
    ${totalProcessed ? `<h3 style="margin: 1rem 0 0.5rem">Processed by Agents (${totalProcessed})</h3>${processedCards}` : ""}
    ${pipelineCards ? `<h3 style="margin: 1rem 0 0.5rem">In Pipeline (${activeItems.length})</h3>${pipelineCards}` : ""}
    ${rejectedCards ? `<h3 style="margin: 1rem 0 0.5rem">Rejected (${summary.rejected || 0})</h3>${rejectedCards}` : ""}
  `;
}

function renderViewTabs() {
  document.querySelectorAll("[data-tab-target]").forEach((button) => {
    const isActive = button.dataset.tabTarget === uiState.activeTab;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.tabPanel === uiState.activeTab);
  });
}

function ensureLineageSelection(atlas) {
  const families = atlas.families || [];
  if (!families.length) {
    uiState.selectedFamilyId = null;
    uiState.selectedLineageId = null;
    return null;
  }
  let family = families.find((item) => item.family_id === uiState.selectedFamilyId) || families[0];
  const nodes = family.nodes || [];
  if (!nodes.length) {
    uiState.selectedFamilyId = family.family_id;
    uiState.selectedLineageId = null;
    return family;
  }
  const selectedNode = nodes.find((item) => item.lineage_id === uiState.selectedLineageId);
  if (!selectedNode) {
    uiState.selectedLineageId = family.champion_lineage_id || family.root_lineage_ids?.[0] || nodes[0].lineage_id;
  }
  uiState.selectedFamilyId = family.family_id;
  family = families.find((item) => item.family_id === uiState.selectedFamilyId) || family;
  return family;
}

function lineagePerformanceTone(node) {
  const roi = Number(node?.monthly_roi_pct ?? 0);
  if (roi > 0.25) return "positive";
  if (roi < -0.25) return "negative";
  return "flat";
}

function lineageParamTags(node) {
  return [
    node.selected_model_class ? `model ${node.selected_model_class}` : "",
    node.selected_horizon_seconds ? `${formatNumber(node.selected_horizon_seconds, 0)}s` : "",
    node.selected_feature_subset ? node.selected_feature_subset : "",
    node.selected_min_edge != null ? `edge ${formatNumber(node.selected_min_edge, 3)}` : "",
    node.selected_stake_fraction != null ? `stake ${formatNumber(node.selected_stake_fraction, 3)}` : "",
  ].filter(Boolean);
}

function renderAtlasHero(snapshot) {
  const summary = snapshot.factory.lineage_atlas?.summary || {};
  const cards = [
    {
      label: "Tracked Families",
      value: formatNumber(summary.family_count || 0, 0),
      detail: `${formatNumber(summary.node_count || 0, 0)} total lineage nodes`,
    },
    {
      label: "Root Branches",
      value: formatNumber(summary.root_count || 0, 0),
      detail: `${formatNumber(summary.max_depth || 0, 0)} maximum generation depth`,
    },
    {
      label: "Mutation Paths",
      value: formatNumber(summary.mutation_count || 0, 0),
      detail: `${formatNumber(summary.new_model_count || 0, 0)} new-model forks`,
    },
    {
      label: "Positive ROI Nodes",
      value: formatNumber(summary.positive_roi_count || 0, 0),
      detail: "Nodes currently above zero monthly ROI",
    },
  ];
  document.getElementById("lineage-atlas-hero").innerHTML = cards
    .map(
      (card) => `
        <article class="atlas-stat">
          <span class="tiny-label">${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <p>${escapeHtml(card.detail)}</p>
        </article>
      `
    )
    .join("");
}

function renderFamilyTabs(atlas, selectedFamily) {
  const families = atlas.families || [];
  const container = document.getElementById("lineage-family-tabs");
  container.innerHTML = families
    .map(
      (family) => `
        <button
          class="family-chip ${family.family_id === selectedFamily?.family_id ? "is-active" : ""}"
          type="button"
          data-family-select="${escapeHtml(family.family_id)}"
        >
          <span class="tiny-label">${escapeHtml(family.family_id)}</span>
          <strong>${escapeHtml(family.label)}</strong>
          <span class="card-subtitle">${escapeHtml(`${formatNumber(family.active_lineage_count || 0, 0)} active · depth ${formatNumber(family.max_depth || 0, 0)}`)}</span>
        </button>
      `
    )
    .join("");
}

function renderTreeBranch(lineageId, nodesById, selectedLineageId) {
  const node = nodesById[lineageId];
  if (!node) return "";
  const children = (node.child_lineage_ids || []).filter((childId) => nodesById[childId]);
  const tags = lineageParamTags(node).slice(0, 3);
  return `
    <li>
      <button
        class="tree-node-card ${lineageId === selectedLineageId ? "is-selected" : ""}"
        type="button"
        data-lineage-select="${escapeHtml(lineageId)}"
        data-performance="${escapeHtml(lineagePerformanceTone(node))}"
      >
        <div class="tree-node-header">
          <span class="tiny-label">${escapeHtml(node.short_name || lineageId)}</span>
          <span class="${pillClass(node.current_stage || "info")}">${escapeHtml(node.current_stage || "n/a")}</span>
        </div>
        <strong>${escapeHtml(node.display_name || node.short_name || lineageId)}</strong>
        <p class="card-subtitle">${escapeHtml(formatDate(node.created_at))} · ${escapeHtml(node.creation_kind || node.role || "lineage")}</p>
        <div class="tree-node-metrics">
          <span>ROI ${escapeHtml(`${formatNumber(node.monthly_roi_pct)}%`)}</span>
          <span>Fit ${escapeHtml(formatNumber(node.fitness_score))}</span>
          <span>${escapeHtml(`${formatNumber(node.trade_count || 0, 0)} trades`)}</span>
          <span>${escapeHtml(`${formatNumber(node.paper_days || 0, 0)} days`)}</span>
        </div>
        <div class="tree-node-footer">
          <span class="${pillClass(node.execution_health_status || "info")}">${escapeHtml(node.execution_health_status || "unknown")}</span>
          <div class="tag-row">
            ${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
          </div>
        </div>
      </button>
      ${children.length ? `<ul>${children.map((childId) => renderTreeBranch(childId, nodesById, selectedLineageId)).join("")}</ul>` : ""}
    </li>
  `;
}

function renderLineageTree(selectedFamily) {
  const container = document.getElementById("lineage-tree-shell");
  if (!selectedFamily || !(selectedFamily.nodes || []).length) {
    container.innerHTML = `<div class="lineage-tree-empty">No lineage history available for this family yet.</div>`;
    return;
  }
  const nodesById = Object.fromEntries((selectedFamily.nodes || []).map((node) => [node.lineage_id, node]));
  const roots = (selectedFamily.root_lineage_ids || []).filter((lineageId) => nodesById[lineageId]);
  container.innerHTML = `
    <ul class="lineage-tree">
      ${roots.map((lineageId) => renderTreeBranch(lineageId, nodesById, uiState.selectedLineageId)).join("")}
    </ul>
  `;
}

function renderLineageInspector(selectedFamily) {
  const container = document.getElementById("lineage-inspector");
  if (!selectedFamily || !(selectedFamily.nodes || []).length) {
    container.innerHTML = `<div class="lineage-tree-empty">Pick a family to inspect its active and historical models.</div>`;
    return;
  }
  const nodesById = Object.fromEntries((selectedFamily.nodes || []).map((node) => [node.lineage_id, node]));
  const node = nodesById[uiState.selectedLineageId] || selectedFamily.nodes[0];
  if (!node) {
    container.innerHTML = `<div class="lineage-tree-empty">No lineage selected.</div>`;
    return;
  }
  const parent = node.parent_lineage_id ? nodesById[node.parent_lineage_id] : null;
  const children = (node.child_lineage_ids || []).map((lineageId) => nodesById[lineageId]).filter(Boolean);
  const paramTags = lineageParamTags(node);
  container.innerHTML = `
    <div class="inspector-header">
      <span class="tiny-label">${escapeHtml(selectedFamily.label)}</span>
      <strong>${escapeHtml(node.display_name || node.short_name || node.lineage_id)}</strong>
      <p class="card-subtitle">${escapeHtml(node.lineage_id)}</p>
      <div class="tag-row">
        <span class="${pillClass(node.current_stage || "info")}">${escapeHtml(node.current_stage || "n/a")}</span>
        <span class="${pillClass(node.execution_health_status || "info")}">${escapeHtml(node.execution_health_status || "unknown")}</span>
        ${node.creation_kind ? `<span class="tag">${escapeHtml(node.creation_kind)}</span>` : ""}
        ${node.iteration_status ? `<span class="tag">${escapeHtml(node.iteration_status)}</span>` : ""}
      </div>
    </div>
    <div class="inspector-grid">
      <section class="inspector-card">
        <h3>Performance</h3>
        <div class="inspector-row"><span class="metric-label">Monthly ROI</span><strong>${escapeHtml(`${formatNumber(node.monthly_roi_pct)}%`)}</strong></div>
        <div class="inspector-row"><span class="metric-label">Fitness</span><strong>${escapeHtml(formatNumber(node.fitness_score))}</strong></div>
        <div class="inspector-row"><span class="metric-label">Paper Window</span><strong>${escapeHtml(`${formatNumber(node.paper_days || 0, 0)}d · ${formatNumber(node.trade_count || 0, 0)} trades`)}</strong></div>
        <div class="inspector-row"><span class="metric-label">Assessment</span><strong>${escapeHtml(`${formatNumber((node.assessment || {}).completion_pct || 0, 0)}% · ${(node.assessment || {}).eta || "n/a"}`)}</strong></div>
      </section>
      <section class="inspector-card">
        <h3>Parameters</h3>
        <div class="tag-row">
          ${paramTags.length ? paramTags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") : `<span class="tag">No compact parameter bundle</span>`}
        </div>
        ${node.target_portfolios?.length ? `<p class="card-subtitle">${escapeHtml(node.target_portfolios.join(", "))}</p>` : ""}
      </section>
      <section class="inspector-card">
        <h3>Lineage Links</h3>
        <div class="inspector-row"><span class="metric-label">Created</span><strong>${escapeHtml(formatDateTime(node.created_at))}</strong></div>
        <div class="inspector-row"><span class="metric-label">Updated</span><strong>${escapeHtml(formatDateTime(node.updated_at))}</strong></div>
        <div class="inspector-row"><span class="metric-label">Parent</span><strong>${parent ? `<button class="lineage-link" type="button" data-lineage-select="${escapeHtml(parent.lineage_id)}">${escapeHtml(parent.short_name || parent.lineage_id)}</button>` : "root"}</strong></div>
        <div class="inspector-row"><span class="metric-label">Children</span><strong>${children.length ? children.map((child) => `<button class="lineage-link" type="button" data-lineage-select="${escapeHtml(child.lineage_id)}">${escapeHtml(child.short_name || child.lineage_id)}</button>`).join(" ") : "none"}</strong></div>
      </section>
      <section class="inspector-card">
        <h3>Agent Trail</h3>
        <div class="inspector-row"><span class="metric-label">Lead role</span><strong>${escapeHtml(node.lead_agent_role || "n/a")}</strong></div>
        <div class="inspector-row"><span class="metric-label">Latest agent</span><strong>${escapeHtml(node.latest_agent_provider ? `${node.latest_agent_provider} / ${node.latest_agent_model}` : "n/a")}</strong></div>
        <div class="inspector-row"><span class="metric-label">Proposal seed</span><strong>${escapeHtml(node.proposal_provider ? `${node.proposal_provider} / ${node.proposal_model}` : "n/a")}</strong></div>
        ${node.source_idea_id ? `<div class="inspector-row"><span class="metric-label">Idea source</span><strong>${escapeHtml(node.source_idea_id)}</strong></div>` : ""}
        ${node.latest_artifact_package ? `<div class="inspector-row"><span class="metric-label">Artifact</span><strong>${escapeHtml(basenamePath(node.latest_artifact_package))}</strong></div>` : ""}
      </section>
    </div>
  `;
}

function renderLineageLedger(selectedFamily) {
  const container = document.getElementById("lineage-ledger");
  if (!selectedFamily || !(selectedFamily.history || []).length) {
    container.innerHTML = `<div class="lineage-tree-empty">No chronology available yet.</div>`;
    return;
  }
  container.innerHTML = (selectedFamily.history || [])
    .map((node) => {
      const tags = lineageParamTags(node).slice(0, 4);
      const parentText = node.parent_lineage_id ? `from ${node.parent_lineage_id}` : "root branch";
      return `
        <article class="ledger-item">
          <header>
            <div>
              <span class="tiny-label">${escapeHtml(formatDateTime(node.created_at))}</span>
              <strong>${escapeHtml(node.display_name || node.short_name || node.lineage_id)}</strong>
            </div>
            <button class="lineage-link" type="button" data-lineage-select="${escapeHtml(node.lineage_id)}">${escapeHtml(node.short_name || node.lineage_id)}</button>
          </header>
          <p>${escapeHtml(parentText)} · ${escapeHtml(node.current_stage || "n/a")} · ROI ${escapeHtml(`${formatNumber(node.monthly_roi_pct)}%`)} · fitness ${escapeHtml(formatNumber(node.fitness_score))}</p>
          <div class="tag-row">
            ${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
            ${node.execution_issue_codes?.slice(0, 2).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") || ""}
          </div>
        </article>
      `;
    })
    .join("");
}

function renderLineageAtlas(snapshot) {
  const atlas = snapshot.factory.lineage_atlas || { summary: {}, families: [] };
  renderAtlasHero(snapshot);
  const selectedFamily = ensureLineageSelection(atlas);
  document.getElementById("lineage-family-tag").textContent = selectedFamily?.label || "Family View";
  renderFamilyTabs(atlas, selectedFamily);
  renderLineageTree(selectedFamily);
  renderLineageInspector(selectedFamily);
  renderLineageLedger(selectedFamily);
}

function renderFrame(snapshot) {
  latestSnapshot = snapshot;
  document.getElementById("factory-mode").textContent = snapshot.factory.mode || "unknown";
  document.getElementById("snapshot-time").textContent = new Date(snapshot.generated_at).toLocaleTimeString();
  const apiHealthEl = document.getElementById("api-health-status");
  if (apiHealthEl) {
    const apiStatus = (snapshot.api_health || {}).status || "unknown";
    apiHealthEl.textContent = apiStatus.toUpperCase();
    apiHealthEl.style.color = apiStatus === "ok" ? "var(--accent)" : "var(--critical)";
  }
  document.getElementById("factory-status-tag").className = pillClass(snapshot.factory.readiness?.status || "info");
  document.getElementById("factory-status-tag").textContent = snapshot.factory.readiness?.status || "unknown";
  renderFeedHealth(snapshot);
  renderKpis(snapshot);
  renderAlerts(snapshot);
  renderEscalations(snapshot);
  renderOperatorInbox(snapshot);
  renderMaintenanceQueue(snapshot);
  renderPaperQualificationQueue(snapshot);
  renderDesks(snapshot);
  renderFamilies(snapshot);
  renderModelLeague(snapshot);
  renderPortfolios(snapshot);
  renderQueue(snapshot);
  renderAgentRuns(snapshot);
  renderLineages(snapshot);
  renderJournal(snapshot);
  renderIdeas(snapshot);
  renderLineageAtlas(snapshot);
  renderViewTabs();
}

async function loadSnapshot() {
  const response = await fetch("/api/snapshot", { cache: "no-store" });
  if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
  return response.json();
}

async function refresh() {
  try {
    const snapshot = await loadSnapshot();
    renderFrame(snapshot);
  } catch (error) {
    document.getElementById("alert-list").innerHTML = `
      <article class="alert-card" data-severity="critical">
        <h3>Dashboard refresh failed</h3>
        <p>${escapeHtml(error.message || String(error))}</p>
      </article>
    `;
  }
}

document.addEventListener("click", (event) => {
  const tabButton = event.target.closest("[data-tab-target]");
  if (tabButton) {
    uiState.activeTab = tabButton.dataset.tabTarget || "overview";
    renderViewTabs();
    return;
  }
  const pnlButton = event.target.closest("[data-pnl-toggle]");
  if (pnlButton) {
    const pid = pnlButton.dataset.pnlToggle;
    const wrap = pnlButton.closest(".portfolio-card")?.querySelector(".pnl-chart-wrap");
    if (wrap) {
      const expanding = !wrap.classList.contains("is-expanded");
      wrap.classList.toggle("is-expanded");
      pnlButton.textContent = expanding ? "Hide P&L" : "View P&L";
      if (expanding) {
        const chartId = `pnl-chart-${pid}`;
        renderPnlChart(pid, chartId);
      }
    }
    return;
  }
  const familyButton = event.target.closest("[data-family-select]");
  if (familyButton && latestSnapshot) {
    uiState.selectedFamilyId = familyButton.dataset.familySelect || null;
    uiState.selectedLineageId = null;
    renderFrame(latestSnapshot);
    return;
  }
  const lineageButton = event.target.closest("[data-lineage-select]");
  if (lineageButton && latestSnapshot) {
    uiState.selectedLineageId = lineageButton.dataset.lineageSelect || null;
    renderFrame(latestSnapshot);
  }
});

refresh();
setInterval(refresh, REFRESH_MS);
