from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pandas as pd

import config
from factory.agent_runtime import (
    TASK_CHEAP,
    TASK_FRONTIER,
    TASK_HARD,
    TASK_LOCAL,
    TASK_STANDARD,
    AgentRunResult,
    RealResearchAgentRuntime,
    _critique_schema,
    _debug_schema,
    _family_proposal_schema,
    _maintenance_resolution_schema,
    _proposal_schema,
    _resolve_log_dir,
    _task_model,
    apply_real_agent_proposal,
    apply_real_family_proposal,
)
from factory.contracts import FactoryFamily, LearningMemoryEntry, LineageRecord, MutationBounds, StrategyGenome
from factory.operator_dashboard import (
    _assessment_progress,
    _build_operator_signal_view,
    _build_feed_health,
    _build_agent_run_view,
    _first_assessment_progress,
    build_dashboard_snapshot,
    build_dashboard_snapshot_light,
    build_snapshot_v2,
)
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


def test_proposal_task_class_escalates_to_frontier_at_high_failure_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    runtime = RealResearchAgentRuntime(tmp_path)

    retired_memories = [
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
    ]
    assert runtime._proposal_task_class(_family(), retired_memories, {}) == TASK_FRONTIER

    execution_evidence = {
        "health_status": "critical",
        "issue_codes": [
            "negative_paper_roi",
            "poor_win_rate",
            "no_trade_syndrome",
            "zero_simulated_fills",
        ],
    }
    assert runtime._proposal_task_class(_family(), [], execution_evidence) == TASK_FRONTIER


def test_task_local_bypasses_agent_runtime(monkeypatch, tmp_path):
    """TASK_LOCAL bypasses all LLM providers; pure computation, no tokens."""
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    runtime = RealResearchAgentRuntime(tmp_path)

    result = runtime._run_structured(
        task_type="backtest",
        task_class=TASK_LOCAL,
        family_id="binance_funding_contrarian",
        lineage_id="l1",
        prompt="",
        prompt_payload={},
        schema={},
    )

    assert result.success is True
    assert result.provider == "local"
    assert result.model == "none"
    assert result.model_class == TASK_LOCAL
    assert result.reasoning_effort == "none"
    assert result.fallback_used is False
    assert result.result_payload.get("output") == "Task executed locally without LLM"


def test_enabled_families_gate_real_agent_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian,binance_cascade_regime")
    runtime = RealResearchAgentRuntime(tmp_path)

    assert runtime._family_enabled("binance_funding_contrarian") is True
    assert runtime._family_enabled("binance_cascade_regime") is True
    assert runtime._family_enabled("polymarket_cross_venue") is False


def test_maintenance_resolution_review_is_not_limited_to_enabled_family_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    runtime = RealResearchAgentRuntime(tmp_path)

    seen = {}

    def _fake_run_structured(**kwargs):
        seen.update(kwargs)
        return AgentRunResult(
            run_id="maintenance_resolution_review_test",
            task_type="maintenance_resolution_review",
            model_class=kwargs["task_class"],
            provider="codex",
            model="gpt-5.2-codex",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id=kwargs["family_id"],
            lineage_id=kwargs["lineage_id"],
            duration_ms=1,
            result_payload={
                "summary": "replace it",
                "maintenance_action": "replace",
                "maintenance_reason": "weak evidence",
                "next_steps": ["seed challenger"],
                "requires_new_challenger": True,
                "multi_agent_trace": {"strategy": "parallel_panel", "roles": [], "synthesis": "replace"},
            },
        )

    monkeypatch.setattr(runtime, "_run_structured", _fake_run_structured)
    family = FactoryFamily(
        family_id="binance_stop_guessing_start",
        label="Stop Guessing",
        thesis="We believe we can create alpha by ...",
        target_portfolios=["research_factory"],
        target_venues=["binance"],
        primary_connector_ids=["binance_core"],
        champion_lineage_id="binance_stop_guessing_start:champion",
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"incumbent": 1.0},
        queue_stage="paper",
        explainer="test",
    )
    lineage = _lineage()
    lineage.family_id = family.family_id
    lineage.lineage_id = "binance_stop_guessing_start:champion"

    result = runtime.resolve_maintenance_item(
        family=family,
        lineage=lineage,
        genome=_genome(),
        latest_bundle=None,
        learning_memory=[],
        execution_evidence={"health_status": "critical", "issue_codes": ["negative_paper_roi"]},
        maintenance_request={"action": "replace", "source": "execution_policy", "reason": "weak"},
        review_context={},
    )

    assert result is not None
    assert seen["family_id"] == "binance_stop_guessing_start"


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


def test_codex_multi_agent_plan_targets_high_value_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED", True)
    monkeypatch.setattr(
        config,
        "FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS",
        "proposal_generation,post_eval_critique,runtime_debug_review,family_bootstrap_generation,maintenance_resolution_review",
    )
    runtime = RealResearchAgentRuntime(tmp_path)

    proposal_plan = runtime._codex_multi_agent_plan(
        task_type="proposal_generation",
        task_class=TASK_STANDARD,
    )
    tweak_plan = runtime._codex_multi_agent_plan(
        task_type="underperformance_tweak",
        task_class=TASK_CHEAP,
    )
    debug_plan = runtime._codex_multi_agent_plan(
        task_type="runtime_debug_review",
        task_class=TASK_HARD,
    )
    family_plan = runtime._codex_multi_agent_plan(
        task_type="family_bootstrap_generation",
        task_class=TASK_FRONTIER,
    )
    maintenance_plan = runtime._codex_multi_agent_plan(
        task_type="maintenance_resolution_review",
        task_class=TASK_HARD,
    )

    assert proposal_plan["enabled"] is True
    assert proposal_plan["child_roles"] == [
        "alpha_hypothesis_proposer",
        "falsification_critic",
        "execution_microstructure_reviewer",
    ]
    assert tweak_plan["enabled"] is False
    assert tweak_plan["child_roles"] == []
    assert debug_plan["enabled"] is True
    assert "operator_escalation_classifier" in debug_plan["child_roles"]
    assert family_plan["enabled"] is True
    assert family_plan["child_roles"] == [
        "family_thesis_proposer",
        "venue_connector_planner",
        "incubation_risk_critic",
    ]
    assert maintenance_plan["enabled"] is True
    assert maintenance_plan["child_roles"] == [
        "maintenance_triager",
        "replacement_planner",
        "execution_realism_reviewer",
    ]


def test_multi_agent_trace_schema_is_available_for_high_value_tasks():
    proposal_schema = _proposal_schema()
    critique_schema = _critique_schema()
    debug_schema = _debug_schema()
    family_schema = _family_proposal_schema()
    maintenance_schema = _maintenance_resolution_schema()

    assert "multi_agent_trace" in proposal_schema["properties"]
    assert "multi_agent_trace" in critique_schema["properties"]
    assert "multi_agent_trace" in debug_schema["properties"]
    assert "multi_agent_trace" in family_schema["properties"]
    assert "multi_agent_trace" in maintenance_schema["properties"]


def test_codex_exec_enables_multi_agent_for_proposals_only(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_PROVIDER_ORDER", "codex")
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED", True)
    monkeypatch.setattr(
        config,
        "FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS",
        "proposal_generation,post_eval_critique,runtime_debug_review",
    )
    runtime = RealResearchAgentRuntime(tmp_path)
    seen_cmds = []

    def _fake_run(cmd, **_kwargs):
        seen_cmds.append(list(cmd))
        output_path = Path(cmd[cmd.index("-o") + 1])
        is_multi_agent = "--enable" in cmd and "multi_agent" in cmd
        payload = (
            {
                "title": "Multi Agent Proposal",
                "thesis": "Use a bounded child-agent panel.",
                "scientific_domains": ["econometrics"],
                "lead_agent_role": "Director",
                "collaborating_agent_roles": ["Genome Mutator"],
                "budget_bucket": "adjacent",
                "proposal_kind": "mutation",
                "source_idea_id": None,
                "parameter_overrides": {
                    "selected_horizon_seconds": 600,
                    "selected_feature_subset": "baseline",
                    "selected_model_class": "logit",
                    "selected_min_edge": 0.03,
                    "selected_stake_fraction": 0.03,
                    "selected_learning_rate": 0.02,
                    "selected_lookback_hours": 48.0,
                },
                "agent_notes": ["panel"],
                "multi_agent_trace": {
                    "strategy": "parallel_panel",
                    "roles": [
                        {"role": "alpha_hypothesis_proposer", "finding": "Momentum reversal idea is viable."},
                        {"role": "falsification_critic", "finding": "Watch sample-size fragility."},
                    ],
                    "synthesis": "Proceed with bounded mutation and paper validation.",
                },
            }
            if is_multi_agent
            else {
                "parameter_overrides": {
                    "selected_horizon_seconds": 600,
                    "selected_feature_subset": "baseline",
                    "selected_model_class": "logit",
                    "selected_min_edge": 0.03,
                    "selected_stake_fraction": 0.03,
                    "selected_learning_rate": 0.02,
                    "selected_lookback_hours": 48.0,
                },
                "agent_notes": ["single agent tweak"],
            }
        )
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    proposal = runtime.generate_proposal(
        family=_family(),
        champion_hypothesis=None,
        champion_genome=_genome(),
        learning_memory=[],
        execution_evidence={"health_status": "warning", "issue_codes": ["negative_paper_roi"]},
        cycle_count=1,
        proposal_index=1,
    )
    tweak = runtime.suggest_tweak(
        lineage=_lineage(),
        hypothesis=None,
        genome=_genome(),
        row={"fitness_score": -1.0, "monthly_roi_pct": -2.0},
        learning_memory=[],
        execution_evidence={"health_status": "warning", "issue_codes": ["negative_paper_roi"]},
    )

    proposal_cmd = seen_cmds[0]
    tweak_cmd = seen_cmds[1]
    assert "--enable" in proposal_cmd
    assert "multi_agent" in proposal_cmd
    assert "--enable" not in tweak_cmd
    assert proposal is not None
    assert proposal.multi_agent_requested is True
    assert proposal.multi_agent_roles == [
        "alpha_hypothesis_proposer",
        "falsification_critic",
        "execution_microstructure_reviewer",
    ]
    assert tweak is not None
    assert tweak.multi_agent_requested is False
    artifact_payload = json.loads(Path(proposal.artifact_path).read_text(encoding="utf-8"))
    assert artifact_payload["multi_agent_requested"] is True
    assert artifact_payload["multi_agent_roles"] == proposal.multi_agent_roles
    assert artifact_payload["result_payload"]["multi_agent_trace"]["strategy"] == "parallel_panel"


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


def test_apply_real_family_proposal_preserves_metadata():
    result = AgentRunResult(
        run_id="family_bootstrap_generation_123",
        task_type="family_bootstrap_generation",
        model_class="frontier_research",
        provider="codex",
        model="gpt-5.4",
        reasoning_effort="high",
        success=True,
        fallback_used=False,
        family_id="incubating_family",
        lineage_id=None,
        duration_ms=2345,
        result_payload={
            "family_id": "cross_venue_entropy_lab",
            "label": "Cross Venue Entropy Lab",
            "thesis": "Exploit entropy gaps across venues.",
            "explainer": "Incubate a new family around cross-venue event compression.",
            "target_venues": ["polymarket", "binance"],
            "primary_connector_ids": ["polymarket_core", "binance_core"],
            "target_portfolios": [],
            "scientific_domains": ["information_theory", "network_epidemiology"],
            "lead_agent_role": "Family Incubator",
            "collaborating_agent_roles": ["Execution Critic"],
            "source_idea_id": "idea_777",
            "incubation_notes": ["novel family"],
        },
        artifact_path="/tmp/family_run.json",
        multi_agent_requested=True,
        multi_agent_roles=["family_thesis_proposer", "venue_connector_planner", "incubation_risk_critic"],
    )

    proposal = apply_real_family_proposal(
        result=result,
        idea={"idea_id": "idea_777", "title": "Entropy Family", "summary": "Cross venue entropy"},
        existing_family_ids=["binance_funding_contrarian"],
        cycle_count=3,
        proposal_index=1,
        research_portfolio_id="research_factory",
    )

    assert proposal.origin == "real_agent_codex"
    assert proposal.family_id == "cross_venue_entropy_lab"
    assert proposal.target_portfolios == ["research_factory"]
    assert proposal.thesis.startswith("We believe we can create alpha by ")
    assert "provider=codex" in proposal.incubation_notes


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


def test_agent_run_view_compacts_raw_codex_stderr():
    rows = _build_agent_run_view(
        [
            {
                "run_id": "family_bootstrap_generation_bad",
                "generated_at": "2026-03-12T00:00:00+00:00",
                "task_type": "family_bootstrap_generation",
                "model_class": "frontier_research",
                "provider": "codex",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "family_id": "",
                "lineage_id": "",
                "success": False,
                "fallback_used": False,
                "duration_ms": 1,
                "prompt_payload": {},
                "result_payload": {},
                "error": "codex:OpenAI Codex v0.115.0-alpha.4\\nERROR: {\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\",\"code\":\"invalid_json_schema\",\"message\":\"Invalid schema for response_format 'codex_output_schema': Missing 'multi_agent_trace'.\"}}",
            }
        ]
    )

    assert rows[0]["error"] == "structured_output_error: Invalid schema for response_format 'codex_output_schema': Missing 'multi_agent_trace'."


def test_dashboard_snapshot_builds_lineage_atlas_from_registry(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    champion_id = "binance_funding_contrarian:champion"
    challenger_id = "binance_funding_contrarian:challenger:1"
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 75, "checks": []},
                "research_summary": {"real_agent_lineage_count": 1},
                "agent_roles": {},
                "scientific_researchers": [],
                "families": [
                    {
                        "family_id": "binance_funding_contrarian",
                        "label": "Funding Contrarian",
                        "queue_stage": "paper",
                        "target_portfolios": ["hedge_validation"],
                        "lineage_count": 2,
                        "active_lineage_count": 2,
                        "retired_lineage_count": 0,
                        "champion": {
                            "lineage_id": champion_id,
                            "current_stage": "paper",
                            "monthly_roi_pct": 6.2,
                            "fitness_score": 2.8,
                        },
                    }
                ],
                "lineages": [
                    {
                        "lineage_id": champion_id,
                        "family_id": "binance_funding_contrarian",
                        "role": "champion",
                        "current_stage": "paper",
                        "iteration_status": "champion",
                        "fitness_score": 2.8,
                        "monthly_roi_pct": 6.2,
                        "paper_days": 30,
                        "trade_count": 64,
                        "active": True,
                        "pareto_rank": 1,
                        "execution_has_signal": True,
                        "execution_health_status": "healthy",
                        "execution_issue_codes": [],
                        "lead_agent_role": "Director",
                        "collaborating_agent_roles": ["Genome Mutator"],
                        "scientific_domains": ["econometrics"],
                        "hypothesis_origin": "deterministic",
                        "latest_agent_decision": {},
                        "proposal_agent": {},
                        "latest_artifact_package": str(factory_root / "packages" / "champion.json"),
                    },
                    {
                        "lineage_id": challenger_id,
                        "family_id": "binance_funding_contrarian",
                        "role": "paper_challenger",
                        "current_stage": "paper",
                        "iteration_status": "tweaked",
                        "fitness_score": 3.4,
                        "monthly_roi_pct": 8.9,
                        "paper_days": 18,
                        "trade_count": 28,
                        "active": True,
                        "pareto_rank": 2,
                        "execution_has_signal": True,
                        "execution_health_status": "warning",
                        "execution_issue_codes": ["readiness_blocked"],
                        "lead_agent_role": "Director",
                        "collaborating_agent_roles": ["Genome Mutator"],
                        "scientific_domains": ["econometrics", "microstructure"],
                        "hypothesis_origin": "real_agent_codex",
                        "latest_agent_decision": {"provider": "codex", "model": "gpt-5.2-codex"},
                        "proposal_agent": {"provider": "codex", "model": "gpt-5.4"},
                        "latest_artifact_package": str(factory_root / "packages" / "challenger.json"),
                    },
                ],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n- lineage atlas\n", encoding="utf-8")

    champion_dir = factory_root / "lineages" / champion_id
    champion_dir.mkdir(parents=True, exist_ok=True)
    champion_lineage = LineageRecord(
        lineage_id=champion_id,
        family_id="binance_funding_contrarian",
        label="Funding Champion",
        role="champion",
        current_stage="paper",
        target_portfolios=["hedge_validation"],
        target_venues=["binance"],
        hypothesis_id="hypothesis:champion",
        genome_id="genome:champion",
        experiment_id="experiment:champion",
        budget_bucket="incumbent",
        budget_weight_pct=10.0,
        connector_ids=["binance_core"],
        goldfish_workspace="research/goldfish/binance_funding_contrarian",
        creation_kind="mutation",
    )
    (champion_dir / "lineage.json").write_text(json.dumps(champion_lineage.to_dict()), encoding="utf-8")
    champion_genome = StrategyGenome(
        genome_id="genome:champion",
        lineage_id=champion_id,
        family_id="binance_funding_contrarian",
        parent_genome_id=None,
        role="champion",
        parameters={
            "selected_feature_subset": "baseline",
            "selected_model_class": "logit",
            "selected_horizon_seconds": 600,
            "selected_min_edge": 0.03,
            "selected_stake_fraction": 0.02,
        },
        mutation_bounds=MutationBounds(
            horizons_seconds=[120, 600],
            feature_subsets=["baseline", "microstructure"],
            model_classes=["logit", "gbdt"],
            execution_thresholds={"min_edge": [0.01, 0.1], "stake_fraction": [0.01, 0.1]},
            hyperparameter_ranges={},
        ),
        scientific_domains=["econometrics"],
        budget_bucket="incumbent",
        resource_profile="local",
        budget_weight_pct=10.0,
    )
    (champion_dir / "genome.json").write_text(json.dumps(champion_genome.to_dict()), encoding="utf-8")

    challenger_dir = factory_root / "lineages" / challenger_id
    challenger_dir.mkdir(parents=True, exist_ok=True)
    challenger_lineage = LineageRecord(
        lineage_id=challenger_id,
        family_id="binance_funding_contrarian",
        label="Funding Challenger 1",
        role="paper_challenger",
        current_stage="paper",
        target_portfolios=["hedge_validation"],
        target_venues=["binance"],
        hypothesis_id="hypothesis:challenger",
        genome_id="genome:challenger",
        experiment_id="experiment:challenger",
        budget_bucket="adjacent",
        budget_weight_pct=4.0,
        connector_ids=["binance_core"],
        goldfish_workspace="research/goldfish/binance_funding_contrarian",
        creation_kind="new_model",
        parent_lineage_id=champion_id,
    )
    (challenger_dir / "lineage.json").write_text(json.dumps(challenger_lineage.to_dict()), encoding="utf-8")
    challenger_genome = StrategyGenome(
        genome_id="genome:challenger",
        lineage_id=challenger_id,
        family_id="binance_funding_contrarian",
        parent_genome_id="genome:champion",
        role="paper_challenger",
        parameters={
            "selected_feature_subset": "cross_science",
            "selected_model_class": "gbdt",
            "selected_horizon_seconds": 1800,
            "selected_min_edge": 0.055,
            "selected_stake_fraction": 0.012,
        },
        mutation_bounds=MutationBounds(
            horizons_seconds=[120, 600, 1800],
            feature_subsets=["baseline", "cross_science"],
            model_classes=["logit", "gbdt"],
            execution_thresholds={"min_edge": [0.01, 0.1], "stake_fraction": [0.01, 0.1]},
            hyperparameter_ranges={},
        ),
        scientific_domains=["econometrics", "microstructure"],
        budget_bucket="adjacent",
        resource_profile="local",
        budget_weight_pct=4.0,
    )
    (challenger_dir / "genome.json").write_text(json.dumps(challenger_genome.to_dict()), encoding="utf-8")

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot()
    atlas = snapshot["factory"]["lineage_atlas"]

    assert atlas["summary"]["family_count"] == 1
    assert atlas["summary"]["node_count"] == 2
    family = atlas["families"][0]
    assert family["root_lineage_ids"] == [champion_id]
    challenger = next(node for node in family["nodes"] if node["lineage_id"] == challenger_id)
    assert challenger["parent_lineage_id"] == champion_id
    assert challenger["depth"] == 1
    assert challenger["creation_kind"] == "new_model"
    assert challenger["selected_model_class"] == "gbdt"
    assert challenger["selected_horizon_seconds"] == 1800
    assert challenger["selected_feature_subset"] == "cross_science"
    assert challenger["selected_min_edge"] == 0.055
    assert challenger["selected_stake_fraction"] == 0.012


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


def test_first_assessment_progress_completes_on_two_day_fast_read():
    early = _first_assessment_progress(
        paper_days=1,
        trade_count=6,
        labels=["binance_funding_contrarian", "paper"],
        realized_roi_pct=3.0,
        current_stage="paper",
    )
    ready = _first_assessment_progress(
        paper_days=2,
        trade_count=10,
        labels=["binance_funding_contrarian", "paper"],
        realized_roi_pct=5.5,
        current_stage="paper",
    )

    assert early["complete"] is False
    assert early["status"] == "first_read_ready"
    assert ready["complete"] is True
    assert ready["status"] == "complete"


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
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "cascade_alpha,betfair_crossbook_consensus")

    snapshot = build_dashboard_snapshot()

    assert snapshot["execution"]["portfolio_count"] == 1
    assert snapshot["execution"]["placeholder_count"] == 1
    assert [row["portfolio_id"] for row in snapshot["execution"]["portfolios"]] == ["cascade_alpha"]
    assert snapshot["execution"]["placeholders"][0]["portfolio_id"] == "betfair_crossbook_consensus"
    assert snapshot["execution"]["portfolios"][0]["display_status"] == "active"
    assert snapshot["execution"]["portfolios"][0]["first_assessment"]["phase"] == "first"


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
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)

    snapshot = build_dashboard_snapshot()

    row = snapshot["execution"]["portfolios"][0]
    assert row["portfolio_id"] == "contrarian_legacy"
    assert row["status"] == "blocked"
    assert row["display_status"] == "validation_blocked"
    assert row["blocked"] is False


def test_dashboard_snapshot_separates_current_and_archived_operational_state(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    portfolios_root = tmp_path / "portfolios"
    portfolios_root.mkdir(parents=True, exist_ok=True)
    agent_runs_root = tmp_path / "agent_runs"
    agent_runs_root.mkdir(parents=True, exist_ok=True)

    current_ts = datetime.now(timezone.utc).isoformat()
    champion_id = "binance_funding_contrarian:champion"
    archived_id = "binance_funding_contrarian:challenger:1"
    family_id = "binance_funding_contrarian"
    current_portfolio_id = "binance_funding_contrarian"

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "cycle_count": 7,
                "readiness": {"status": "healthy", "score_pct": 91, "checks": []},
                "research_summary": {"paper_pnl": 999.0, "real_agent_lineage_count": 1},
                "families": [
                    {
                        "family_id": family_id,
                        "label": "Funding Contrarian",
                        "queue_stage": "paper",
                        "lineage_count": 2,
                        "active_lineage_count": 2,
                        "champion_lineage_id": champion_id,
                        "champion": {
                            "lineage_id": champion_id,
                            "current_stage": "paper",
                            "monthly_roi_pct": 1.5,
                            "fitness_score": 2.0,
                        },
                    }
                ],
                "lineages": [
                    {
                        "lineage_id": champion_id,
                        "family_id": family_id,
                        "role": "champion",
                        "current_stage": "paper",
                        "iteration_status": "active",
                        "fitness_score": 2.0,
                        "monthly_roi_pct": 1.5,
                        "paper_days": 8,
                        "trade_count": 4,
                        "active": True,
                        "pareto_rank": 1,
                        "execution_health_status": "healthy",
                        "execution_issue_codes": [],
                        "execution_has_signal": True,
                        "runtime_lane_kind": "primary_incumbent",
                        "runtime_lane_selected": True,
                        "runtime_target_portfolio": current_portfolio_id,
                        "proposal_agent": {},
                        "latest_agent_decision": {},
                        "created_at": current_ts,
                    },
                    {
                        "lineage_id": archived_id,
                        "family_id": family_id,
                        "role": "paper_challenger",
                        "current_stage": "shadow",
                        "iteration_status": "active",
                        "fitness_score": 1.0,
                        "monthly_roi_pct": 0.3,
                        "paper_days": 2,
                        "trade_count": 0,
                        "active": True,
                        "pareto_rank": 2,
                        "execution_health_status": "warning",
                        "execution_issue_codes": ["readiness_blocked"],
                        "runtime_target_portfolio": "lineage__binance_funding_contrarian__challenger__1",
                        "proposal_agent": {},
                        "latest_agent_decision": {},
                        "created_at": current_ts,
                    },
                ],
                "queue": [
                    {
                        "queue_id": "q-current",
                        "lineage_id": champion_id,
                        "family_id": family_id,
                        "experiment_id": "exp-current",
                        "role": "champion",
                        "status": "queued",
                        "priority": 1,
                        "current_stage": "paper",
                        "notes": [],
                        "created_at": current_ts,
                        "updated_at": current_ts,
                    },
                    {
                        "queue_id": "q-archived",
                        "lineage_id": archived_id,
                        "family_id": family_id,
                        "experiment_id": "exp-archived",
                        "role": "paper_challenger",
                        "status": "queued",
                        "priority": 2,
                        "current_stage": "shadow",
                        "notes": [],
                        "created_at": current_ts,
                        "updated_at": current_ts,
                    },
                ],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    family_dir = factory_root / "families"
    family_dir.mkdir(parents=True, exist_ok=True)
    family = FactoryFamily(
        family_id=family_id,
        label="Funding Contrarian",
        thesis="Paper-test one champion only.",
        target_portfolios=[current_portfolio_id],
        target_venues=["binance"],
        primary_connector_ids=["binance_core"],
        champion_lineage_id=champion_id,
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"incumbent": 100.0},
        queue_stage="paper",
        explainer="One current champion per family.",
        origin="agent_generated",
        source_idea_id="idea_001",
    )
    (family_dir / f"{family_id}.json").write_text(json.dumps(family.to_dict()), encoding="utf-8")

    current_portfolio = portfolios_root / current_portfolio_id
    current_portfolio.mkdir(parents=True, exist_ok=True)
    (current_portfolio / "account.json").write_text(
        json.dumps(
            {
                "currency": "USD",
                "starting_balance": 10000,
                "current_balance": 9975.2646,
                "realized_pnl": -24.7354,
                "trade_count": 4,
            }
        ),
        encoding="utf-8",
    )
    (current_portfolio / "state.json").write_text(
        json.dumps(
            {
                "running": True,
                "ready": True,
                "status": "running",
                "paper_data_contract": {
                    "requirements": [
                        {
                            "source": "binance",
                            "venue": "binance",
                            "instruments": ["BTCUSDT"],
                            "fields": ["funding_rate"],
                            "feed_type": "funding",
                            "raw_cadence_seconds": 28800,
                            "freshness_sla_seconds": 43200,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (current_portfolio / "heartbeat.json").write_text(json.dumps({"ts": current_ts, "status": "running"}), encoding="utf-8")
    (current_portfolio / "readiness.json").write_text(json.dumps({"status": "ready", "score_pct": 96}), encoding="utf-8")
    (current_portfolio / "trades.json").write_text(
        json.dumps(
            [
                {
                    "trade_id": "t1",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "status": "closed",
                    "pnl": -24.7354,
                    "closed_at": current_ts,
                }
            ]
        ),
        encoding="utf-8",
    )

    archived_portfolio = portfolios_root / "lineage__binance_funding_contrarian__challenger__1"
    archived_portfolio.mkdir(parents=True, exist_ok=True)
    (archived_portfolio / "account.json").write_text(
        json.dumps(
            {
                "currency": "USD",
                "starting_balance": 10000,
                "current_balance": 10100,
                "realized_pnl": 100.0,
                "trade_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (archived_portfolio / "state.json").write_text(json.dumps({"running": False, "status": "stopped"}), encoding="utf-8")

    (agent_runs_root / "run.json").write_text(
        json.dumps(
            {
                "run_id": "proposal_1",
                "generated_at": current_ts,
                "task_type": "proposal",
                "model_class": "standard",
                "provider": "openai",
                "model": "gpt-5.4",
                "reasoning_effort": "medium",
                "family_id": family_id,
                "lineage_id": champion_id,
                "success": True,
                "fallback_used": False,
                "duration_ms": 2500,
                "prompt_payload": {},
                "result_payload": {},
                "error": "",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(agent_runs_root))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "other_portfolio", raising=False)

    snapshot = build_dashboard_snapshot()
    snapshot_v2 = build_snapshot_v2()

    assert [row["lineage_id"] for row in snapshot["factory"]["lineages"]] == [champion_id]
    assert [row["lineage_id"] for row in snapshot["factory"]["archived_lineages"]] == [archived_id]
    assert [row["queue_id"] for row in snapshot["factory"]["queue"]] == ["q-current"]
    assert [row["queue_id"] for row in snapshot["factory"]["archived_queue"]] == ["q-archived"]
    family_row = snapshot["factory"]["families"][0]
    assert family_row["target_venues"] == ["binance"]
    assert family_row["current_runner_portfolio_id"] == current_portfolio_id
    assert family_row["champion_paper_state"] == "paper_active"
    assert family_row["runner_gate_status"] == "bound"
    assert family_row["feed_gate_status"] == "fresh"
    assert family_row["last_agent_run_at"] == current_ts
    assert snapshot["execution"]["portfolio_count"] == 1
    assert snapshot["execution"]["archived_portfolio_count"] == 1
    assert snapshot["execution"]["current_paper_pnl"] == pytest.approx(-24.7354)
    assert snapshot["factory"]["research_summary"]["paper_pnl"] == pytest.approx(-24.7354)
    assert snapshot_v2["lineage_v2"][0]["paper_portfolio_id"] == current_portfolio_id


def test_dashboard_snapshot_blocks_active_label_when_runner_is_not_ready(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    current_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    family_id = "polymarket_cross_venue"
    champion_id = f"{family_id}:champion"
    portfolio_id = "polymarket_cross_venue"
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "families": [
                    {
                        "family_id": family_id,
                        "label": "Cross Venue",
                        "champion_lineage_id": champion_id,
                        "target_portfolios": [portfolio_id],
                        "target_venues": ["polymarket", "betfair"],
                    }
                ],
                "lineages": [
                    {
                        "lineage_id": champion_id,
                        "family_id": family_id,
                        "role": "champion",
                        "current_stage": "paper",
                        "target_portfolios": [portfolio_id],
                        "target_venues": ["polymarket", "betfair"],
                        "activation_status": "running",
                    }
                ],
                "queue": [],
                "research_summary": {},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    portfolios_root = tmp_path / "portfolios"
    portfolio_dir = portfolios_root / portfolio_id
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    (portfolio_dir / "account.json").write_text(
        json.dumps({"currency": "USD", "starting_balance": 10000, "current_balance": 10000, "realized_pnl": 0.0, "trade_count": 0}),
        encoding="utf-8",
    )
    (portfolio_dir / "state.json").write_text(
        json.dumps(
            {
                "running": True,
                "ready": False,
                "reason": "model_not_loaded",
                "status": "running",
                "paper_data_contract": {
                    "requirements": [
                        {
                            "source": "polymarket",
                            "venue": "polymarket",
                            "instruments": ["market1"],
                            "fields": ["price"],
                            "feed_type": "prediction_history",
                            "raw_cadence_seconds": 60,
                            "freshness_sla_seconds": 300,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (portfolio_dir / "heartbeat.json").write_text(json.dumps({"ts": current_ts, "status": "running"}), encoding="utf-8")

    prices_dir = tmp_path / "data" / "polymarket" / "prices_history"
    prices_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"timestamp": [datetime.now(timezone.utc) - timedelta(minutes=1)], "price": [0.51]}
    ).to_parquet(prices_dir / "market1.parquet", index=False)
    (tmp_path / "data" / "polymarket" / "markets_metadata.json").write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "interval": "1m"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "liquidation_rebound_absorption", raising=False)

    snapshot = build_dashboard_snapshot()

    family_row = snapshot["factory"]["families"][0]
    assert family_row["current_runner_portfolio_id"] == portfolio_id
    assert family_row["champion_paper_state"] == "paper_blocked"
    assert family_row["runner_gate_status"] == "not_ready"
    assert "model_not_loaded" in str(family_row["runner_gate_reason"])


def test_dashboard_snapshot_surfaces_research_metadata_for_equity_family(tmp_path, monkeypatch):
    from factory.registry import FactoryRegistry

    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    family_id = "vol_surface_dispersion_rotation"
    champion_id = f"{family_id}:champion"
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "families": [
                    {
                        "family_id": family_id,
                        "label": "Vol Surface",
                        "champion_lineage_id": champion_id,
                        "target_portfolios": ["alpaca_paper"],
                        "target_venues": ["alpaca"],
                    }
                ],
                "lineages": [
                    {
                        "lineage_id": champion_id,
                        "family_id": family_id,
                        "role": "champion",
                        "current_stage": "paper",
                        "target_portfolios": ["alpaca_paper"],
                        "target_venues": ["alpaca"],
                    }
                ],
                "queue": [],
                "research_summary": {},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    registry = FactoryRegistry(factory_root)
    registry.save_family(
        FactoryFamily(
            family_id=family_id,
            label="Vol Surface",
            thesis="Surface thesis",
            target_portfolios=["alpaca_paper"],
            target_venues=["alpaca"],
            primary_connector_ids=["alpaca_stocks"],
            champion_lineage_id=champion_id,
            shadow_challenger_ids=[],
            paper_challenger_ids=[],
            budget_split={"research": 1.0},
            queue_stage="paper",
            explainer="vol surface",
            metadata={"research_venues": ["yahoo"], "research_connector_ids": ["yahoo_stocks"]},
        )
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    snapshot = build_dashboard_snapshot()

    family_row = snapshot["factory"]["families"][0]
    assert family_row["target_venues"] == ["alpaca"]
    assert family_row["research_venues"] == ["yahoo"]
    assert family_row["research_connector_ids"] == ["yahoo_stocks"]


def test_dashboard_snapshot_exposes_runtime_lane_metadata(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "research_only", "score_pct": 80, "checks": []},
                "research_summary": {"real_agent_lineage_count": 1, "paper_pnl": 12.5},
                "agent_roles": {},
                "scientific_researchers": [],
                "families": [
                    {
                        "family_id": "binance_funding_contrarian",
                        "label": "Funding Contrarian",
                        "primary_incumbent_lineage_id": "binance_funding_contrarian:champion",
                        "isolated_challenger_lineage_id": "binance_funding_contrarian:challenger:1",
                        "prepared_isolated_lane_lineage_id": "binance_funding_contrarian:challenger:1",
                        "runtime_lane_reason": "family_replacement_pressure",
                        "activation_status": "running",
                        "alias_runner_running": True,
                        "isolated_evidence_ready": True,
                        "curated_rankings": [
                            {
                                "lineage_id": "binance_funding_contrarian:challenger:1",
                                "family_rank": 1,
                                "ranking_score": 1.2,
                                "target_portfolio_id": "contrarian_legacy",
                                "paper_roi_pct": 8.0,
                                "paper_realized_pnl": 15.0,
                                "paper_win_rate": 0.62,
                                "paper_closed_trade_count": 30,
                                "strict_gate_pass": True,
                                "current_stage": "paper",
                            }
                        ],
                    }
                ],
                "lineages": [
                    {
                        "lineage_id": "binance_funding_contrarian:champion",
                        "family_id": "binance_funding_contrarian",
                        "role": "champion",
                        "current_stage": "paper",
                        "iteration_status": "review_requested_replace",
                        "fitness_score": 2.0,
                        "monthly_roi_pct": 4.0,
                        "paper_days": 20,
                        "trade_count": 25,
                        "active": True,
                        "pareto_rank": 2,
                        "execution_has_signal": True,
                        "execution_health_status": "warning",
                        "execution_issue_codes": ["negative_paper_roi"],
                        "runtime_lane_selected": True,
                        "runtime_lane_kind": "primary_incumbent",
                        "runtime_lane_reason": "family_replacement_pressure",
                        "runtime_target_portfolio": "contrarian_legacy",
                        "canonical_target_portfolio": "contrarian_legacy",
                        "target_portfolios": ["contrarian_legacy"],
                    },
                    {
                        "lineage_id": "binance_funding_contrarian:challenger:1",
                        "family_id": "binance_funding_contrarian",
                        "role": "paper_challenger",
                        "current_stage": "paper",
                        "iteration_status": "tweaked",
                        "fitness_score": 3.4,
                        "monthly_roi_pct": 8.0,
                        "paper_days": 18,
                        "trade_count": 30,
                        "active": True,
                        "pareto_rank": 1,
                        "execution_has_signal": True,
                        "execution_health_status": "healthy",
                        "execution_issue_codes": [],
                        "runtime_lane_selected": True,
                        "runtime_lane_kind": "isolated_challenger",
                        "runtime_lane_reason": "family_replacement_pressure",
                        "prepared_isolated_lane": True,
                        "activation_status": "running",
                        "alias_runner_running": True,
                        "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1",
                        "canonical_target_portfolio": "contrarian_legacy",
                        "target_portfolios": ["contrarian_legacy"],
                        "curated_target_portfolio_id": "contrarian_legacy",
                    },
                ],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {
                    "targets": [
                        {
                            "portfolio_id": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1",
                            "canonical_portfolio_id": "contrarian_legacy",
                            "activation_status": "running",
                            "prepared_isolated_lane": True,
                            "running": True,
                        }
                    ]
                },
                "operator_signals": {"positive_models": [], "research_positive_models": [], "escalation_candidates": [], "human_action_required": [], "action_inbox": [], "maintenance_queue": []},
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "contrarian_legacy")

    snapshot = build_dashboard_snapshot_light()

    family = snapshot["factory"]["model_league"][0]
    lineage = next(row for row in snapshot["factory"]["lineages"] if row["lineage_id"].endswith(":challenger:1"))
    atlas_family = snapshot["factory"]["lineage_atlas"]["families"][0]
    portfolio = next(
        row
        for row in snapshot["execution"]["portfolios"]
        if row.get("portfolio_id") == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    )

    assert family["primary_incumbent_lineage_id"] == "binance_funding_contrarian:champion"
    assert family["isolated_challenger_lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert family["prepared_isolated_lane_lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert family["activation_status"] == "running"
    assert family["alias_runner_running"] is True
    assert family["isolated_evidence_ready"] is True
    assert family["rankings"][0]["runtime_lane_kind"] == "isolated_challenger"
    assert family["rankings"][0]["runtime_target_portfolio"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    assert lineage["runtime_lane_selected"] is True
    assert lineage["runtime_lane_kind"] == "isolated_challenger"
    assert lineage["prepared_isolated_lane"] is True
    assert lineage["activation_status"] == "running"
    assert lineage["alias_runner_running"] is True
    assert lineage["runtime_target_portfolio"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    assert atlas_family["isolated_challenger_lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert atlas_family["runtime_target_portfolio"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    assert portfolio["isolated_challenger_lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert portfolio["runtime_lanes"][0]["lane_kind"] == "isolated_challenger"
    assert portfolio["runtime_lanes"][0]["runtime_target_portfolio"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-1"
    assert snapshot["factory"]["execution_bridge"]["targets"][0]["activation_status"] == "running"


def test_dashboard_snapshot_light_derives_feed_health(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    portfolios_root = tmp_path / "portfolios"
    recent_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81},
                "research_summary": {},
                "lineages": [],
                "queue": [],
                "connectors": [
                    {
                        "connector_id": "binance_core",
                        "venue": "binance",
                        "ready": True,
                        "latest_data_ts": recent_ts,
                        "record_count": 12,
                        "issues": [],
                    },
                    {
                        "connector_id": "betfair_core",
                        "venue": "betfair",
                        "ready": True,
                        "latest_data_ts": stale_ts,
                        "record_count": 128,
                        "issues": [],
                    },
                    {
                        "connector_id": "polymarket_core",
                        "venue": "polymarket",
                        "ready": False,
                        "latest_data_ts": None,
                        "record_count": 0,
                        "issues": ["missing:data/portfolios/polymarket_quantum_fold"],
                    },
                ],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")
    (portfolios_root / "hedge_validation").mkdir(parents=True)
    (portfolios_root / "hedge_validation" / "heartbeat.json").write_text(
        json.dumps({"ts": recent_ts, "status": "running"}),
        encoding="utf-8",
    )
    (portfolios_root / "betfair_core").mkdir(parents=True)
    (portfolios_root / "betfair_core" / "heartbeat.json").write_text(
        json.dumps({"ts": stale_ts, "status": "running"}),
        encoding="utf-8",
    )
    (portfolios_root / "polymarket_quantum_fold").mkdir(parents=True)

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)

    snapshot = build_dashboard_snapshot_light()
    feed_health = snapshot["factory"]["feed_health"]

    assert feed_health["status"] == "critical"
    assert feed_health["headline"] == "1/3 feeds healthy"
    assert feed_health["healthy_count"] == 1
    assert feed_health["warning_count"] == 1
    assert feed_health["critical_count"] == 1
    by_venue = {row["venue"]: row for row in feed_health["connectors"]}
    assert by_venue["binance"]["status"] == "healthy"
    assert by_venue["betfair"]["status"] == "warning"
    assert by_venue["polymarket"]["status"] == "critical"


def test_feed_health_prefers_live_connector_status_over_runtime_warning(tmp_path, monkeypatch):
    portfolios_root = tmp_path / "portfolios"
    portfolios_root.mkdir(parents=True)
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
    (portfolios_root / "betfair_core").mkdir(parents=True)
    (portfolios_root / "betfair_core" / "heartbeat.json").write_text(
        json.dumps({"ts": stale_ts, "status": "running"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root), raising=False)

    feed_health = _build_feed_health(
        {},
        [
            {
                "connector_id": "betfair_core",
                "venue": "betfair",
                "ready": True,
                "latest_data_ts": datetime.now(timezone.utc).isoformat(),
                "record_count": 128,
                "issues": [],
            }
        ],
    )

    assert feed_health["status"] == "healthy"
    assert feed_health["healthy_count"] == 1
    assert feed_health["warning_count"] == 0
    row = feed_health["connectors"][0]
    assert row["venue"] == "betfair"
    assert row["status"] == "healthy"
    assert row["runtime_status"] == "warning"


def test_dashboard_snapshot_light_compacts_critical_feed_and_excludes_positive_roi_noise(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    agent_runs = tmp_path / "agent_runs"
    agent_runs.mkdir(parents=True)

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {},
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [
                        {
                            "family_id": "binance_funding_contrarian",
                            "lineage_id": "binance_funding_contrarian:challenger:5",
                            "roi_pct": 24.0,
                            "trade_count": 12,
                            "paper_days": 4,
                        }
                    ],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")
    (agent_runs / "failed_run.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "task_type": "post_eval_critique",
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:challenger:12",
                "success": False,
                "error": (
                    "codex:OpenAI Codex v0.115.0-alpha.4 (research preview)\n"
                    "mcp startup: ready: martech-inspector; failed: github\n"
                    'ERROR: { "type": "error", "error": { "type": "invalid_request_error", "code": "invalid_json_schema", '
                    '"message": "Invalid schema for response_format \'codex_output_schema\'" } }'
                ),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(agent_runs))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot_light()
    alerts = snapshot["company"]["alerts"]

    assert len(alerts) == 1
    assert alerts[0]["title"] == "Agent fallback: post_eval_critique"
    assert "structured_output_error" in alerts[0]["detail"]
    assert "OpenAI Codex v" not in alerts[0]["detail"]
    assert all(not alert["title"].startswith("Positive ROI:") for alert in alerts)


def test_dashboard_snapshot_light_suppresses_stale_agent_fallback_after_success(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    agent_runs = tmp_path / "agent_runs"
    agent_runs.mkdir(parents=True)

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {},
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")
    older = datetime.now(timezone.utc) - timedelta(minutes=10)
    newer = datetime.now(timezone.utc) - timedelta(minutes=2)
    (agent_runs / "failed_run.json").write_text(
        json.dumps(
            {
                "generated_at": older.isoformat(),
                "task_type": "runtime_debug_review",
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:champion",
                "success": False,
                "error": 'ERROR: {"type":"error","error":{"type":"invalid_request_error","code":"invalid_json_schema","message":"Invalid schema for response_format \'codex_output_schema\'"}}',
            }
        ),
        encoding="utf-8",
    )
    (agent_runs / "success_run.json").write_text(
        json.dumps(
            {
                "generated_at": newer.isoformat(),
                "task_type": "runtime_debug_review",
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:champion",
                "success": True,
                "result_payload": {"summary": "fixed"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(agent_runs))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot_light()
    assert snapshot["company"]["alerts"] == []


def test_dashboard_snapshot_light_marks_scientific_swarm_model_active_from_successful_run_domains(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    agent_runs = tmp_path / "agent_runs"
    agent_runs.mkdir(parents=True)

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {},
                "lineages": [],
                "queue": [],
                "connectors": [],
                "agent_roles": {},
                "scientific_researchers": ["econometrics", "microstructure"],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")
    (agent_runs / "success_run.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "task_type": "underperformance_tweak",
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:champion",
                "success": True,
                "model_class": "hard_research",
                "result_payload": {"agent_notes": ["ok"]},
                "prompt_payload": {
                    "hypothesis": {
                        "scientific_domains": ["econometrics", "microstructure"]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(agent_runs))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot_light()
    swarm = next(desk for desk in snapshot["company"]["desks"] if desk["desk_id"] == "scientific_swarm")
    econ = next(member for member in swarm["members"] if member["name"] == "Econometrics")

    assert swarm["status"] == "model_active"
    assert swarm["active_count"] == 2
    assert econ["status"] == "model_active"
    assert econ["real_invocation_count"] == 1


def test_dashboard_snapshot_light_exposes_best_execution_performer_from_account_state(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    portfolios_root = tmp_path / "portfolios"
    for portfolio_id, pnl, roi in [
        ("contrarian_legacy", -203.79, -2.0379),
        ("polymarket_quantum_fold", 13544.36664, 135.443666),
    ]:
        portfolio_dir = portfolios_root / portfolio_id
        portfolio_dir.mkdir(parents=True)
        (portfolio_dir / "account.json").write_text(
            json.dumps(
                {
                    "starting_balance": 10000.0,
                    "current_balance": 10000.0 + pnl,
                    "realized_pnl": pnl,
                    "roi_pct": roi,
                    "trade_count": 10,
                    "currency": "USD",
                }
            ),
            encoding="utf-8",
        )
        (portfolio_dir / "heartbeat.json").write_text(
            json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "status": "running"}),
            encoding="utf-8",
        )

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {"paper_pnl": 13340.57664},
                "lineages": [],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root))
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "contrarian_legacy,polymarket_quantum_fold")

    snapshot = build_dashboard_snapshot_light()
    best = snapshot["execution"]["best_performer"]

    assert best["portfolio_id"] == "polymarket_quantum_fold"
    assert best["realized_pnl"] == 13544.3666
    assert best["roi_pct"] == 135.4437


def test_dashboard_snapshot_light_prefers_current_runtime_health_and_exposes_win_rate(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    portfolios_root = tmp_path / "portfolios"
    portfolio_dir = portfolios_root / "polymarket_quantum_fold"
    portfolio_dir.mkdir(parents=True)
    (portfolio_dir / "account.json").write_text(
        json.dumps(
            {
                "starting_balance": 10000.0,
                "current_balance": 23544.36664,
                "realized_pnl": 13544.36664,
                "roi_pct": 135.443666,
                "trade_count": 90,
                "wins": 1,
                "losses": 89,
                "currency": "USD",
            }
        ),
        encoding="utf-8",
    )
    (portfolio_dir / "heartbeat.json").write_text(
        json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "status": "running"}),
        encoding="utf-8",
    )
    (portfolio_dir / "runtime_health.json").write_text(
        json.dumps(
            {
                "status": "running",
                "health_status": "healthy",
                "issue_codes": [],
                "training_state": {
                    "training_progress": {
                        "tracked_examples": 1913,
                        "labeled_examples": 1746,
                        "pending_labels": 6457,
                        "closed_trades": 90,
                    },
                    "trainability": {
                        "status": "warming_up",
                        "required_model_count": 3,
                        "trained_model_count": 0,
                        "blocked_models": ["qm_coherence"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (portfolio_dir / "readiness.json").write_text(
        json.dumps({"status": "paper_validating", "blockers": ["calibration_lift_positive"]}),
        encoding="utf-8",
    )
    (portfolio_dir / "state.json").write_text(
        json.dumps({
            "status": "running",
            "running": True,
            "training_progress": {
                "tracked_examples": 1913,
                "labeled_examples": 1746,
                "pending_labels": 6457,
                "targets": {"closed_trades": 90},
            },
            "trainability": {
                "status": "warming_up",
                "required_model_count": 3,
                "trained_model_count": 0,
                "trainable_model_count": 2,
                "blocked_models": ["qm_coherence"],
            },
        }),
        encoding="utf-8",
    )

    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {"paper_pnl": 13544.36664},
                "lineages": [
                    {
                        "family_id": "polymarket_cross_venue",
                        "lineage_id": "polymarket_cross_venue:champion",
                        "runtime_target_portfolio": "polymarket_quantum_fold",
                        "execution_health_status": "critical",
                        "execution_issue_codes": ["runtime_error", "readiness_blocked"],
                        "paper_days": 0,
                    }
                ],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(portfolios_root))
    monkeypatch.setattr(config, "EXECUTION_TRACKED_PORTFOLIOS", "polymarket_quantum_fold")

    snapshot = build_dashboard_snapshot_light()
    portfolio = snapshot["execution"]["portfolios"][0]

    assert portfolio["execution_health_status"] == "healthy"
    assert portfolio["display_status"] == "running"
    assert portfolio["readiness_status"] == "running"
    assert portfolio["win_rate"] == pytest.approx(0.0111, rel=1e-4)
    assert portfolio["wins"] == 1
    assert portfolio["losses"] == 89
    assert portfolio["trainability"]["status"] == "warming_up"
    assert portfolio["training_progress"]["labeled_examples"] == 1746
    assert portfolio["trainability"]["blocked_models"] == ["qm_coherence"]


def test_operator_signal_view_filters_stale_retired_queue_items_and_recent_duplicate_reviews(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS", 24)
    monkeypatch.setattr(config, "FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY", 2)
    now = datetime.now(timezone.utc).isoformat()
    view = _build_operator_signal_view(
        {
            "lineages": [
                {
                    "family_id": "polymarket_cross_venue",
                    "lineage_id": "polymarket_cross_venue:retired:1",
                    "active": False,
                },
                {
                    "family_id": "polymarket_cross_venue",
                    "lineage_id": "polymarket_cross_venue:champion",
                    "active": True,
                    "last_maintenance_review_at": now,
                    "last_maintenance_review_status": "completed",
                    "last_maintenance_review_action": "replace",
                },
            ],
            "operator_signals": {
                "positive_models": [],
                "research_positive_models": [],
                "first_assessment_candidates": [],
                "escalation_candidates": [],
                "human_action_required": [],
                "action_inbox": [],
                "maintenance_queue": [
                    {
                        "family_id": "polymarket_cross_venue",
                        "lineage_id": "polymarket_cross_venue:retired:1",
                        "action": "replace",
                        "source": "execution_policy",
                        "priority": 2,
                        "execution_health_status": "critical",
                    },
                    {
                        "family_id": "polymarket_cross_venue",
                        "lineage_id": "polymarket_cross_venue:champion",
                        "action": "replace",
                        "source": "execution_policy",
                        "priority": 2,
                        "execution_health_status": "warning",
                    },
                    {
                        "family_id": "polymarket_cross_venue",
                        "lineage_id": "polymarket_cross_venue:champion",
                        "action": "family_autopilot",
                        "source": "family_autopilot",
                        "priority": 3,
                        "execution_health_status": "warning",
                    },
                ],
            },
        }
    )

    queue = view["maintenance_queue"]
    assert len(queue) == 1
    assert queue[0]["action"] == "family_autopilot"


def test_dashboard_snapshot_light_exposes_paper_runtime_intent_summary(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    state_root = factory_root / "state"
    state_root.mkdir(parents=True)
    (state_root / "summary.json").write_text(
        json.dumps(
            {
                "agentic_factory_mode": "full",
                "status": "running",
                "readiness": {"status": "healthy", "score_pct": 81, "checks": []},
                "research_summary": {},
                "families": [
                    {
                        "family_id": "binance_funding_contrarian",
                        "label": "Funding Contrarian",
                        "queue_stage": "paper",
                        "lineage_count": 3,
                        "active_lineage_count": 2,
                        "retired_lineage_count": 1,
                        "primary_incumbent_lineage_id": "binance_funding_contrarian:champion",
                        "isolated_challenger_lineage_id": "binance_funding_contrarian:challenger:1",
                        "curated_rankings": [],
                    }
                ],
                "lineages": [
                    {
                        "family_id": "binance_funding_contrarian",
                        "lineage_id": "binance_funding_contrarian:champion",
                        "active": True,
                        "current_stage": "paper",
                        "runtime_lane_kind": "primary_incumbent",
                        "runtime_lane_selected": True,
                        "execution_has_signal": True,
                        "runtime_target_portfolio": "contrarian_legacy",
                    },
                    {
                        "family_id": "binance_funding_contrarian",
                        "lineage_id": "binance_funding_contrarian:challenger:1",
                        "active": True,
                        "current_stage": "shadow",
                        "runtime_lane_kind": "isolated_challenger",
                        "runtime_lane_selected": True,
                        "activation_status": "ready_to_launch",
                        "runtime_target_portfolio": "factory_lane__contrarian",
                    },
                    {
                        "family_id": "binance_funding_contrarian",
                        "lineage_id": "binance_funding_contrarian:challenger:2",
                        "active": True,
                        "current_stage": "idea",
                        "suppressed_runtime_sibling": True,
                    },
                ],
                "queue": [],
                "connectors": [],
                "manifests": {"pending": [], "live_loadable": []},
                "execution_bridge": {},
                "operator_signals": {
                    "positive_models": [],
                    "research_positive_models": [],
                    "first_assessment_candidates": [],
                    "escalation_candidates": [],
                    "human_action_required": [],
                    "action_inbox": [],
                    "maintenance_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (state_root / "STATE.md").write_text("## Recent Actions\n", encoding="utf-8")

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs"))
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(tmp_path / "portfolios"))

    snapshot = build_dashboard_snapshot_light()
    assert snapshot["factory"]["paper_runtime"]["expected_count"] == 2
    assert snapshot["factory"]["paper_runtime"]["running_count"] == 1
    assert snapshot["factory"]["paper_runtime"]["starting_count"] == 1
    assert snapshot["factory"]["paper_runtime"]["suppressed_count"] == 1
    family = snapshot["factory"]["families"][0]
    assert family["paper_runtime_expected_count"] == 2
    assert family["paper_runtime_running_count"] == 1
    statuses = set(family["paper_runtime_statuses"])
    assert "paper_running" in statuses
    assert "paper_starting" in statuses
    lineage_rows = {row["lineage_id"]: row for row in snapshot["factory"]["lineages"]}
    assert lineage_rows["binance_funding_contrarian:champion"]["paper_runtime_status"] == "paper_running"
    assert lineage_rows["binance_funding_contrarian:challenger:1"]["paper_runtime_status"] == "paper_starting"
