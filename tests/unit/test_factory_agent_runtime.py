from __future__ import annotations

import json
from pathlib import Path

import config
from factory.agent_runtime import (
    TASK_CHEAP,
    TASK_FRONTIER,
    TASK_HARD,
    TASK_STANDARD,
    AgentRunResult,
    RealResearchAgentRuntime,
    _resolve_log_dir,
    _task_model,
    apply_real_agent_proposal,
)
from factory.contracts import FactoryFamily, LearningMemoryEntry, LineageRecord, MutationBounds, StrategyGenome
from factory.operator_dashboard import _assessment_progress, build_dashboard_snapshot
from factory.orchestrator import FactoryOrchestrator


def _family() -> FactoryFamily:
    return FactoryFamily(
        family_id="binance_funding_contrarian",
        label="Funding Contrarian",
        thesis="Research funding dislocations with bounded risk.",
        target_portfolios=["hedge_validation"],
        target_venues=["binance"],
        primary_connector_ids=["binance_core"],
        champion_lineage_id="binance_funding_contrarian:champion",
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"incumbent": 70.0, "adjacent": 20.0, "moonshot": 10.0},
        queue_stage="idea",
        explainer="Funding family",
    )


def _genome() -> StrategyGenome:
    return StrategyGenome(
        genome_id="binance_funding_contrarian:genome",
        lineage_id="binance_funding_contrarian:champion",
        family_id="binance_funding_contrarian",
        parent_genome_id=None,
        role="champion",
        parameters={
            "selected_feature_subset": "baseline",
            "selected_model_class": "logit",
            "selected_horizon_seconds": 600,
            "selected_min_edge": 0.03,
            "selected_stake_fraction": 0.03,
            "selected_learning_rate": 0.02,
            "selected_lookback_hours": 48.0,
        },
        mutation_bounds=MutationBounds(
            horizons_seconds=[120, 600, 1800],
            feature_subsets=["baseline", "microstructure", "cross_science", "regime"],
            model_classes=["logit", "gbdt", "transformer", "tft", "rules"],
            execution_thresholds={"min_edge": [0.01, 0.1], "stake_fraction": [0.01, 0.1]},
            hyperparameter_ranges={"learning_rate": [0.001, 0.1], "lookback_hours": [6.0, 168.0]},
        ),
        scientific_domains=["econometrics", "microstructure"],
        budget_bucket="incumbent",
        resource_profile="local-first-hybrid",
        budget_weight_pct=14.0,
    )


def _lineage() -> LineageRecord:
    return LineageRecord(
        lineage_id="binance_funding_contrarian:challenger:1",
        family_id="binance_funding_contrarian",
        label="Funding Challenger",
        role="shadow_challenger",
        current_stage="idea",
        target_portfolios=["hedge_validation"],
        target_venues=["binance"],
        hypothesis_id="h",
        genome_id="g",
        experiment_id="e",
        budget_bucket="incumbent",
        budget_weight_pct=4.0,
        connector_ids=["binance_core"],
        goldfish_workspace="research/goldfish/binance_funding_contrarian",
    )


def test_task_router_selects_expected_models_and_escalation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
    runtime = RealResearchAgentRuntime(tmp_path)

    assert _task_model(TASK_CHEAP) == "gpt-5.1-codex-mini"
    assert _task_model(TASK_STANDARD) == "gpt-5.1-codex"
    assert _task_model(TASK_HARD) == "gpt-5.2-codex"
    assert _task_model(TASK_FRONTIER) == "gpt-5.3-codex"

    assert runtime._proposal_task_class(_family(), [], {}) == TASK_STANDARD
    assert runtime._proposal_task_class(
        _family(),
        [
            LearningMemoryEntry(
                memory_id="m1",
                family_id="binance_funding_contrarian",
                lineage_id="l1",
                hypothesis_id="h1",
                outcome="retired_underperformance",
                summary="failed",
                scientific_domains=["econometrics"],
                lead_agent_role="Director",
                tweak_count=1,
                decision_stage="paper",
                recommendations=["prefer microstructure"],
                metrics={"monthly_roi_pct": -2.0},
            ),
            LearningMemoryEntry(
                memory_id="m2",
                family_id="binance_funding_contrarian",
                lineage_id="l2",
                hypothesis_id="h2",
                outcome="retired_underperformance",
                summary="failed again",
                scientific_domains=["econometrics"],
                lead_agent_role="Director",
                tweak_count=2,
                decision_stage="paper",
                recommendations=["prefer regime"],
                metrics={"monthly_roi_pct": 1.0},
            ),
        ],
        {},
    ) == TASK_HARD
    assert runtime._proposal_task_class(
        _family(),
        [
            LearningMemoryEntry(
                memory_id=f"m{idx}",
                family_id="binance_funding_contrarian",
                lineage_id=f"l{idx}",
                hypothesis_id=f"h{idx}",
                outcome="retired_underperformance",
                summary="failed",
                scientific_domains=["econometrics"],
                lead_agent_role="Director",
                tweak_count=idx,
                decision_stage="paper",
            )
            for idx in range(4)
        ],
        {},
    ) == TASK_FRONTIER

    lineage = _lineage()
    lineage.current_stage = "paper"
    assert runtime._debug_task_class(lineage, {"health_status": "warning", "issue_codes": ["readiness_blocked"]}) == TASK_CHEAP
    assert runtime._debug_task_class(lineage, {"health_status": "critical", "issue_codes": ["runtime_error"]}) == TASK_HARD
    assert runtime._debug_task_class(lineage, {"health_status": "warning", "issue_codes": ["stalled_model"]}) == TASK_HARD
    lineage.last_debug_issue_signature = "runtime_error"
    assert runtime._debug_task_class(lineage, {"health_status": "warning", "issue_codes": ["readiness_blocked"]}) == TASK_HARD
    assert runtime._proposal_task_class(
        _family(),
        [],
        {"health_status": "critical", "issue_codes": ["negative_paper_roi", "poor_win_rate", "no_trade_syndrome", "zero_simulated_fills"]},
    ) == TASK_FRONTIER
    assert runtime._proposal_task_class(
        _family(),
        [],
        {"health_status": "warning", "issue_codes": ["stalled_model"]},
    ) == TASK_HARD


def test_enabled_families_gate_real_agent_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian,binance_cascade_regime")
    runtime = RealResearchAgentRuntime(tmp_path)

    assert runtime._family_enabled("binance_funding_contrarian") is True
    assert runtime._family_enabled("binance_cascade_regime") is True
    assert runtime._family_enabled("polymarket_cross_venue") is False


def test_runtime_falls_back_to_deterministic_and_writes_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_PROVIDER_ORDER", "codex,deterministic")
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    runtime = RealResearchAgentRuntime(tmp_path)

    def _explode(**_kwargs):
        raise RuntimeError("codex unavailable")

    monkeypatch.setattr(runtime, "_run_codex", _explode)
    result = runtime.generate_proposal(
        family=_family(),
        champion_hypothesis=None,
        champion_genome=_genome(),
        learning_memory=[],
        execution_evidence={"health_status": "warning", "issue_codes": ["negative_paper_roi"]},
        cycle_count=1,
        proposal_index=1,
    )

    assert result is not None
    assert result.provider == "deterministic"
    assert result.success is False
    assert result.fallback_used is True
    assert result.model == "gpt-5.4"
    assert result.reasoning_effort == "high"
    assert result.artifact_path is not None
    payload = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert payload["task_type"] == "proposal_generation"
    assert payload["prompt_payload"]["execution_evidence"]["issue_codes"] == ["negative_paper_roi"]
    assert payload["error"]


def test_default_agent_log_dir_follows_factory_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_ROOT", str(tmp_path / "factory"))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", "data/factory/agent_runs")

    log_dir = _resolve_log_dir(tmp_path)

    assert log_dir == (tmp_path / "factory" / "agent_runs")


def test_apply_real_agent_proposal_preserves_metadata():
    result = AgentRunResult(
        run_id="proposal_generation_123",
        task_type="proposal_generation",
        model_class="standard_research",
        provider="codex",
        model="gpt-5.1-codex",
        reasoning_effort="medium",
        success=True,
        fallback_used=False,
        family_id="binance_funding_contrarian",
        lineage_id="binance_funding_contrarian:champion",
        duration_ms=1234,
        result_payload={
            "title": "Adaptive Funding Regime",
            "thesis": "Switch features under unstable funding states.",
            "scientific_domains": ["econometrics", "microstructure"],
            "lead_agent_role": "Director",
            "collaborating_agent_roles": ["Genome Mutator"],
            "budget_bucket": "adjacent",
            "proposal_kind": "new_model",
            "parameter_overrides": {"selected_min_edge": 0.04},
            "agent_notes": ["tighten entry quality"],
        },
        artifact_path="/tmp/run.json",
    )
    proposal = apply_real_agent_proposal(result=result, family=_family(), proposal_index=2)

    assert proposal.origin == "real_agent_codex"
    assert proposal.agent_metadata["model"] == "gpt-5.1-codex"
    assert proposal.agent_metadata["task_class"] == "standard_research"
    assert proposal.parameter_overrides["selected_min_edge"] == 0.04
    assert proposal.title.endswith("Model 2")
    assert proposal.thesis.startswith("We believe we can create alpha by ")


def test_orchestrator_clips_real_agent_overrides_and_records_lineage_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_ROOT", str(tmp_path / "factory"))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(tmp_path / "research" / "goldfish"))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "factory" / "agent_runs"))
    orchestrator = FactoryOrchestrator(tmp_path)

    def _fake_generate(**_kwargs):
        return AgentRunResult(
            run_id="proposal_generation_live",
            task_type="proposal_generation",
            model_class="standard_research",
            provider="codex",
            model="gpt-5.1-codex",
            reasoning_effort="medium",
            success=True,
            fallback_used=False,
            family_id="binance_funding_contrarian",
            lineage_id="binance_funding_contrarian:champion",
            duration_ms=100,
            result_payload={
                "title": "Clipped Challenger",
                "thesis": "Try a bounded shift.",
                "scientific_domains": ["econometrics", "microstructure"],
                "lead_agent_role": "Director",
                "collaborating_agent_roles": ["Genome Mutator"],
                "budget_bucket": "adjacent",
                "parameter_overrides": {
                    "selected_min_edge": 0.8,
                    "selected_stake_fraction": -1.0,
                    "selected_horizon_seconds": 9999,
                    "selected_feature_subset": "not_allowed",
                    "selected_model_class": "made_up",
                },
                "agent_notes": ["force clipping"],
            },
            artifact_path=str(tmp_path / "factory" / "agent_runs" / "proposal_generation_live.json"),
        )

    monkeypatch.setattr(orchestrator.agent_runtime, "generate_proposal", _fake_generate)
    lineages_by_family = orchestrator._lineages_by_family()
    family = next(item for item in orchestrator.registry.families() if item.family_id == "binance_funding_contrarian")

    orchestrator._cycle_count = 1
    orchestrator._seed_challengers(family, lineages_by_family, runtime_mode_value="full", recent_actions=[])

    challenger = max(
        (lineage for lineage in orchestrator.registry.lineages() if lineage.family_id == "binance_funding_contrarian"),
        key=lambda row: row.created_at,
    )
    genome = orchestrator.registry.load_genome(challenger.lineage_id)

    assert genome is not None
    assert genome.parameters["last_agent_decision"]["provider"] == "codex"
    assert genome.parameters["last_agent_decision"]["used_real_agent"] is True
    assert genome.parameters["selected_min_edge"] == 0.1
    assert genome.parameters["selected_stake_fraction"] == 0.01
    assert genome.parameters["selected_horizon_seconds"] in genome.mutation_bounds.horizons_seconds
    assert genome.parameters["selected_feature_subset"] in genome.mutation_bounds.feature_subsets
    assert genome.parameters["selected_model_class"] in genome.mutation_bounds.model_classes


def test_orchestrator_adds_extra_challenger_pressure_for_degraded_family(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_ROOT", str(tmp_path / "factory"))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(tmp_path / "research" / "goldfish"))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "factory" / "agent_runs"))
    orchestrator = FactoryOrchestrator(tmp_path)

    monkeypatch.setattr(
        "factory.orchestrator.summarize_execution_targets",
        lambda *_args, **_kwargs: {
            "health_status": "critical",
            "issue_codes": ["negative_paper_roi", "poor_win_rate", "no_trade_syndrome"],
            "recommendation_context": ["build more challengers"],
            "targets": [],
            "running_target_count": 1,
            "recent_trade_count": 10,
            "recent_event_count": 0,
            "has_execution_signal": True,
        },
    )

    def _fake_generate(**_kwargs):
        return AgentRunResult(
            run_id=f"proposal_generation_{_kwargs['proposal_index']}",
            task_type="proposal_generation",
            model_class="frontier_research",
            provider="codex",
            model="gpt-5.3-codex",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id="binance_funding_contrarian",
            lineage_id="binance_funding_contrarian:champion",
            duration_ms=100,
            result_payload={
                "title": f"Pressure Challenger {_kwargs['proposal_index']}",
                "thesis": "Spawn extra bounded challengers under degraded execution evidence.",
                "scientific_domains": ["econometrics", "microstructure"],
                "lead_agent_role": "Director",
                "collaborating_agent_roles": ["Genome Mutator"],
                "budget_bucket": "adjacent",
                "parameter_overrides": {"selected_model_class": "gbdt"},
                "agent_notes": ["extra pressure"],
            },
            artifact_path=str(tmp_path / "factory" / "agent_runs" / f"proposal_generation_{_kwargs['proposal_index']}.json"),
        )

    monkeypatch.setattr(orchestrator.agent_runtime, "generate_proposal", _fake_generate)
    lineages_by_family = orchestrator._lineages_by_family()
    family = next(item for item in orchestrator.registry.families() if item.family_id == "binance_funding_contrarian")

    orchestrator._cycle_count = 1
    orchestrator._seed_challengers(family, lineages_by_family, runtime_mode_value="full", recent_actions=[])

    challengers = [
        lineage
        for lineage in orchestrator.registry.lineages()
        if lineage.family_id == "binance_funding_contrarian" and lineage.role != "champion"
    ]

    assert len(challengers) == 2


def test_dashboard_snapshot_includes_recent_agent_runs(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 75, "checks": []},
                "research_summary": {"real_agent_lineage_count": 1},
                "agent_roles": {"tier0_deterministic": ["Director"]},
                "scientific_researchers": [],
                "families": [],
                "lineages": [
                    {
                        "lineage_id": "binance_funding_contrarian:challenger:1",
                        "family_id": "binance_funding_contrarian",
                        "role": "shadow_challenger",
                        "current_stage": "idea",
                        "fitness_score": 1.2,
                        "monthly_roi_pct": 3.4,
                        "trade_count": 10,
                        "active": True,
                        "pareto_rank": 1,
                        "execution_has_signal": False,
                        "execution_health_status": "warning",
                        "execution_issue_codes": ["negative_paper_roi"],
                        "execution_recommendation_context": ["tighten entry logic and retrain for better trade selection quality"],
                        "lead_agent_role": "Director",
                        "collaborating_agent_roles": ["Genome Mutator"],
                        "scientific_domains": ["econometrics"],
                        "hypothesis_origin": "real_agent_codex",
                        "latest_agent_decision": {
                            "provider": "codex",
                            "model": "gpt-5.1-codex",
                            "task_type": "proposal_generation",
                            "used_real_agent": True,
                        },
                        "proposal_agent": {"provider": "codex", "model": "gpt-5.1-codex"},
                    }
                ],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n- real agent seeded a challenger\n", encoding="utf-8")
    log_dir = tmp_path / "agent_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "proposal_generation_1.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-11T00:00:00+00:00",
                "run_id": "proposal_generation_1",
                "task_type": "proposal_generation",
                "model_class": "standard_research",
                "provider": "codex",
                "model": "gpt-5.1-codex",
                "reasoning_effort": "medium",
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "success": True,
                "fallback_used": False,
                "duration_ms": 4321,
                "prompt_payload": {},
                "result_payload": {"lead_agent_role": "Director"},
                "error": "",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(log_dir))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot()

    assert snapshot["factory"]["agent_runs"]
    assert snapshot["factory"]["agent_runs"][0]["provider"] == "codex"
    assert snapshot["factory"]["lineages"][0]["latest_agent_decision"]["provider"] == "codex"
    assert snapshot["factory"]["lineages"][0]["execution_health_status"] == "warning"
    assert snapshot["factory"]["lineages"][0]["assessment"]["completion_pct"] == 10.0


def test_assessment_progress_flags_high_roi_small_sample_as_incomplete():
    early = _assessment_progress(
        paper_days=6,
        trade_count=12,
        labels=["binance_funding_contrarian", "paper"],
        realized_roi_pct=24.0,
        current_stage="paper",
    )
    mature = _assessment_progress(
        paper_days=30,
        trade_count=50,
        labels=["binance_funding_contrarian", "paper"],
        realized_roi_pct=8.0,
        current_stage="live_ready",
    )

    assert early["completion_pct"] < 50.0
    assert early["status"] == "early"
    assert early["trades_remaining"] == 38
    assert mature["completion_pct"] == 100.0
    assert mature["status"] == "complete"


def test_dashboard_groups_shared_positive_roi_evidence(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 75, "checks": []},
                "research_summary": {},
                "agent_roles": {},
                "scientific_researchers": [],
                "families": [],
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "operator_signals": {
                    "positive_models": [
                        {
                            "family_id": "binance_funding_contrarian",
                            "lineage_id": "binance_funding_contrarian:challenger:1",
                            "current_stage": "paper",
                            "roi_pct": 23.1784,
                            "trade_count": 12,
                            "paper_days": 13,
                            "curated_family_rank": 3,
                            "curated_target_portfolio_id": "contrarian_legacy",
                            "evidence_source_type": "shared_portfolio_scorecard",
                        },
                        {
                            "family_id": "binance_funding_contrarian",
                            "lineage_id": "binance_funding_contrarian:challenger:2",
                            "current_stage": "paper",
                            "roi_pct": 23.1784,
                            "trade_count": 12,
                            "paper_days": 11,
                            "curated_family_rank": 5,
                            "curated_target_portfolio_id": "contrarian_legacy",
                            "evidence_source_type": "shared_portfolio_scorecard",
                        },
                    ],
                    "escalation_candidates": [],
                    "human_action_required": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n- grouped positives\n", encoding="utf-8")
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot()
    positives = snapshot["factory"]["operator_signals"]["positive_models"]

    assert len(positives) == 1
    assert positives[0]["shared_lineage_count"] == 2
    assert positives[0]["curated_target_portfolio_id"] == "contrarian_legacy"


def test_dashboard_filters_placeholder_portfolios(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 80, "checks": []},
                "research_summary": {"real_agent_lineage_count": 0},
                "agent_roles": {},
                "scientific_researchers": [],
                "families": [],
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    portfolios_root = tmp_path / "portfolios"
    real_portfolio = portfolios_root / "cascade_alpha"
    real_portfolio.mkdir(parents=True, exist_ok=True)
    (real_portfolio / "account.json").write_text(
        json.dumps(
            {
                "currency": "USD",
                "starting_balance": 10000,
                "current_balance": 9950,
                "realized_pnl": -50,
            }
        ),
        encoding="utf-8",
    )
    (real_portfolio / "state.json").write_text(
        json.dumps({"running": True, "status": "running"}),
        encoding="utf-8",
    )
    (real_portfolio / "config_snapshot.json").write_text(
        json.dumps({"label": "Cascade Alpha", "category": "crypto_alpha", "currency": "USD"}),
        encoding="utf-8",
    )
    (real_portfolio / "readiness.json").write_text(
        json.dumps({"status": "ready", "score_pct": 100}),
        encoding="utf-8",
    )

    placeholder = portfolios_root / "betfair_crossbook_consensus"
    placeholder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root))

    snapshot = build_dashboard_snapshot()

    assert snapshot["execution"]["portfolio_count"] == 1
    assert snapshot["execution"]["placeholder_count"] == 1
    assert [row["portfolio_id"] for row in snapshot["execution"]["portfolios"]] == ["cascade_alpha"]
    assert snapshot["execution"]["placeholders"][0]["portfolio_id"] == "betfair_crossbook_consensus"
    assert snapshot["execution"]["portfolios"][0]["display_status"] == "active"


def test_dashboard_marks_warning_portfolios_degraded_without_blocking(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 80, "checks": []},
                "research_summary": {"real_agent_lineage_count": 0},
                "agent_roles": {},
                "scientific_researchers": [],
                "families": [],
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    portfolios_root = tmp_path / "portfolios"
    portfolio = portfolios_root / "contrarian_legacy"
    portfolio.mkdir(parents=True, exist_ok=True)
    (portfolio / "account.json").write_text(
        json.dumps(
            {
                "currency": "USD",
                "starting_balance": 10000,
                "current_balance": 9300,
                "realized_pnl": -700,
                "roi_pct": -7.0,
                "trade_count": 12,
                "last_updated": "2026-03-11T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (portfolio / "heartbeat.json").write_text(
        json.dumps({"ts": "2026-03-11T12:00:00+00:00", "status": "running"}),
        encoding="utf-8",
    )
    (portfolio / "readiness.json").write_text(
        json.dumps({"status": "blocked", "blockers": ["strict_gate_pass"]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root))

    snapshot = build_dashboard_snapshot()

    row = snapshot["execution"]["portfolios"][0]
    assert row["portfolio_id"] == "contrarian_legacy"
    assert row["status"] == "blocked"
    assert row["display_status"] == "validation_blocked"
    assert row["blocked"] is False
