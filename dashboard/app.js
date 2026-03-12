const REFRESH_MS = 5000;

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

function pillClass(kind) {
  if (["live_ready", "approved_live", "running", "active", "healthy", "positive"].includes(kind)) return "status-pill status-ok";
  if (["blocked", "error", "critical", "start_failed"].includes(kind)) return "status-pill status-critical";
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

function renderAssessment(assessment) {
  const item = assessment || {};
  const pct = formatNumber(item.completion_pct || 0, 0);
  const eta = item.eta || "n/a";
  const status = item.status || "info";
  const observedDays = formatNumber(item.paper_days_observed || 0, 0);
  const requiredDays = formatNumber(item.paper_days_required || 0, 0);
  const observedTrades = formatNumber(item.trade_count_observed || 0, 0);
  const requiredTrades = formatNumber(item.trade_count_required || 0, 0);
  return `
    <div class="cell-stack">
      <span class="${pillClass(status)}">${escapeHtml(`${pct}% assessed`)}</span>
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
  const readiness = factory.readiness || {};
  const agentRuns = factory.agent_runs || [];
  const cards = [
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
      label: "Execution Runners",
      value: formatNumber(execution.running_count, 0),
      detail: `${formatNumber(execution.blocked_count, 0)} blocked or degraded · ${formatNumber(execution.placeholder_count || 0, 0)} placeholders hidden`,
    },
    {
      label: "Realized PnL",
      value: formatCurrency(execution.realized_pnl_total || 0, "USD"),
      detail: execution.note || "Summed across tracked execution portfolios",
    },
    {
      label: "Pending Manifests",
      value: formatNumber((factory.manifests?.pending || []).length, 0),
      detail: `${formatNumber((factory.manifests?.live_loadable || []).length, 0)} approved for live load`,
    },
    {
      label: "Operator Reviews",
      value: formatNumber((factory.operator_signals?.escalation_candidates || []).length, 0),
      detail: `${formatNumber((research.positive_model_count || 0), 0)} positive ROI models · ${formatNumber((research.human_action_required_count || 0), 0)} human blockers`,
    },
    {
      label: "Real Agent Runs",
      value: formatNumber(agentRuns.length, 0),
      detail: `${formatNumber((research.real_agent_lineage_count || 0), 0)} lineages with real-agent origins`,
    },
    {
      label: "Idea Notes",
      value: formatNumber(ideas.idea_count || 0, 0),
      detail: ideas.present ? `${formatNumber((ideas.status_counts || {}).tested || 0, 0)} tested · ${formatNumber((ideas.status_counts || {}).promoted || 0, 0)} promoted` : "waiting for ideas.md",
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
      <article class="alert-card" data-severity="positive">
        <header class="panel-header">
          <h3>${escapeHtml(item.family_id)} · positive ROI</h3>
          <span class="${pillClass("positive")}">green</span>
        </header>
        <p>${escapeHtml(item.lineage_id)} is at ${formatNumber(item.roi_pct)}% ROI across ${escapeHtml(item.trade_count)} trades.</p>
        <p class="card-subtitle">Stage ${escapeHtml(item.current_stage)} · rank #${escapeHtml(item.curated_family_rank || "n/a")}${item.shared_lineage_count > 1 ? ` · shared by ${escapeHtml(item.shared_lineage_count)} lineages via ${escapeHtml(item.curated_target_portfolio_id || "shared portfolio")}` : ""}</p>
        ${renderAssessment(item.assessment)}
      </article>
    `
  );
  container.innerHTML = humanCards.concat(escalationCards, positiveCards).join("");
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
              <p class="card-subtitle">${escapeHtml(desk.active_count)} active of ${escapeHtml(desk.member_count)} operators</p>
            </div>
            <span class="${pillClass(desk.active_count ? "active" : "standby")}">${escapeHtml(desk.active_count ? "active" : "standby")}</span>
          </header>
          <div class="desk-members">
            ${(desk.members || [])
              .map(
                (member) => `
                  <article class="desk-member">
                    <strong>${escapeHtml(member.name)}</strong>
                    <p>${escapeHtml(member.lineage_count)} attached lineages</p>
                    <p>${escapeHtml(member.real_invocation_count || 0)} real model runs</p>
                    <div class="tag-row">
                      <span class="${pillClass(member.status)}">${escapeHtml(member.status)}</span>
                      <span class="${pillClass((member.real_invocation_count || 0) > 0 ? "active" : "info")}">${escapeHtml((member.real_invocation_count || 0) > 0 ? "model-active" : "role-only")}</span>
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
  document.getElementById("portfolio-grid").innerHTML = portfolios
    .map((portfolio) => {
      const status = portfolio.display_status || (portfolio.error ? "blocked" : (portfolio.status || "unknown"));
      const eventHint = (portfolio.recent_events || []).slice(-1)[0];
      return `
        <article class="portfolio-card">
          <header>
            <div>
              <h3 class="card-title">${escapeHtml(portfolio.label)}</h3>
              <p class="card-subtitle">${escapeHtml(portfolio.portfolio_id)} · ${escapeHtml(portfolio.category)}</p>
            </div>
            <span class="${pillClass(status)}">${escapeHtml(status)}</span>
          </header>
          <div class="metric-row"><span class="metric-label">Execution Health</span><strong><span class="${pillClass(portfolio.execution_health_status || "info")}">${escapeHtml(portfolio.execution_health_status || "unknown")}</span></strong></div>
          <div class="metric-row"><span class="metric-label">Balance</span><strong>${formatCurrency(portfolio.current_balance, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">Starting Bankroll</span><strong>${formatCurrency(portfolio.starting_balance, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">Realized PnL</span><strong>${formatCurrency(portfolio.realized_pnl, portfolio.currency)}</strong></div>
          <div class="metric-row"><span class="metric-label">ROI / Drawdown</span><strong>${formatNumber(portfolio.roi_pct)}% / ${formatNumber(portfolio.drawdown_pct)}%</strong></div>
          <div class="metric-row"><span class="metric-label">Heartbeat Age</span><strong>${portfolio.heartbeat_age_seconds == null ? "n/a" : `${formatNumber(portfolio.heartbeat_age_seconds, 1)}s`}</strong></div>
          <div class="metric-row"><span class="metric-label">Candidate Contexts</span><strong>${escapeHtml(portfolio.candidate_context_count)}</strong></div>
          <div class="metric-row"><span class="metric-label">Readiness</span><strong>${escapeHtml(portfolio.readiness_status || "n/a")} ${portfolio.readiness_score_pct ? `(${formatNumber(portfolio.readiness_score_pct, 0)}%)` : ""}</strong></div>
          <div class="metric-row"><span class="metric-label">Assessment</span><strong>${formatNumber((portfolio.assessment || {}).completion_pct || 0, 0)}% · ${escapeHtml((portfolio.assessment || {}).eta || "n/a")}</strong></div>
          ${portfolio.error ? `<p class="card-subtitle">${escapeHtml(portfolio.error)}</p>` : ""}
          ${(portfolio.execution_recommendation_context || []).length ? `<p class="card-subtitle">${escapeHtml(portfolio.execution_recommendation_context[0])}</p>` : ""}
          <p class="card-subtitle">${escapeHtml(`${formatNumber((portfolio.assessment || {}).paper_days_observed || 0, 0)}/${formatNumber((portfolio.assessment || {}).paper_days_required || 0, 0)} days · ${formatNumber((portfolio.assessment || {}).trade_count_observed || 0, 0)}/${formatNumber((portfolio.assessment || {}).trade_count_required || 0, 0)} trades`)}</p>
          ${eventHint ? `<div class="tag-row"><span class="tag">${escapeHtml(eventHint.kind || "event")}</span></div>` : ""}
          <div class="tag-row">
            ${(portfolio.execution_issue_codes || []).slice(0, 3).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}
            ${(portfolio.candidate_families || []).slice(0, 4).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}
          </div>
        </article>
      `;
    })
    .join("");
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
  const items = ideas.items || [];
  const summary = ideas.status_counts || {};
  const cards = items.slice(0, 10).map((item) => `
    <article class="alert-card" data-severity="${escapeHtml(item.status || "info")}">
      <header>
        <h3>${escapeHtml(item.title || item.idea_id)}</h3>
        <span class="${pillClass(item.status || "info")}">${escapeHtml(item.status || "new")}</span>
      </header>
      <p>${escapeHtml(item.summary || "No summary yet.")}</p>
      <p class="card-subtitle">${escapeHtml((item.family_candidates || []).join(", ") || "unmapped")} · lineages ${escapeHtml(item.lineage_count || 0)}</p>
    </article>
  `).join("");
  document.getElementById("ideas-shell").innerHTML = `
    <div class="tag-row">
      <span class="tag">new ${escapeHtml(summary.new || 0)}</span>
      <span class="tag">adapted ${escapeHtml(summary.adapted || 0)}</span>
      <span class="tag">tested ${escapeHtml(summary.tested || 0)}</span>
      <span class="tag">promoted ${escapeHtml(summary.promoted || 0)}</span>
      <span class="tag">rejected ${escapeHtml(summary.rejected || 0)}</span>
    </div>
    ${cards}
  `;
}

function renderFrame(snapshot) {
  document.getElementById("factory-mode").textContent = snapshot.factory.mode || "unknown";
  document.getElementById("snapshot-time").textContent = new Date(snapshot.generated_at).toLocaleTimeString();
  document.getElementById("factory-status-tag").className = pillClass(snapshot.factory.readiness?.status || "info");
  document.getElementById("factory-status-tag").textContent = snapshot.factory.readiness?.status || "unknown";
  renderKpis(snapshot);
  renderAlerts(snapshot);
  renderEscalations(snapshot);
  renderDesks(snapshot);
  renderFamilies(snapshot);
  renderModelLeague(snapshot);
  renderPortfolios(snapshot);
  renderQueue(snapshot);
  renderAgentRuns(snapshot);
  renderLineages(snapshot);
  renderJournal(snapshot);
  renderIdeas(snapshot);
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

refresh();
setInterval(refresh, REFRESH_MS);
