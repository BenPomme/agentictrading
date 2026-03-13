from __future__ import annotations

import pytest

import config
from factory.contracts import AcceptedStrategyManifest, LineageRecord, MutationBounds, StrategyGenome
from factory.manifests import candidate_context_refs_for_portfolio, live_manifest_refs_for_portfolio
from factory.orchestrator import FactoryOrchestrator
from factory.registry import FactoryRegistry
from factory.runtime_mode import AgenticFactoryRuntimeMode, normalize_agentic_factory_mode
from tests.unit.conftest import execution_repo_root


def test_runtime_mode_normalizes_and_exposes_flags():
    assert normalize_agentic_factory_mode("FULL") == "full"
    assert normalize_agentic_factory_mode("cost_saver") == "cost_saver"
    assert normalize_agentic_factory_mode("unknown") == "full"

    full = AgenticFactoryRuntimeMode("full")
    cost_saver = AgenticFactoryRuntimeMode("cost_saver")
    hard_stop = AgenticFactoryRuntimeMode("hard_stop")

    assert full.is_full is True
    assert full.agentic_tokens_allowed is True
    assert full.factory_influence_allowed is True

    assert cost_saver.is_cost_saver is True
    assert cost_saver.agentic_tokens_allowed is False
    assert cost_saver.factory_influence_allowed is True

    assert hard_stop.is_hard_stop is True
    assert hard_stop.agentic_tokens_allowed is False
    assert hard_stop.factory_influence_allowed is False


def test_live_manifest_refs_are_hidden_when_factory_is_hard_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTORY_ROOT", str(tmp_path / "factory"))
    registry = FactoryRegistry(tmp_path / "factory")
    manifest = AcceptedStrategyManifest(
        manifest_id="manifest-a",
        lineage_id="lineage-a",
        family_id="family-a",
        portfolio_targets=["betfair_core"],
        venue_targets=["betfair"],
        approved_stage="live_ready",
        status="pending_approval",
        artifact_refs={"workspace": "research/goldfish/family-a"},
        runtime_overrides={"resource_profile": "local-first-hybrid"},
    )
    registry.save_manifest(manifest)
    registry.approve_manifest("manifest-a", approved_by="operator")

    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "cost_saver")
    assert len(live_manifest_refs_for_portfolio("betfair_core")) == 1

    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "hard_stop")
    assert live_manifest_refs_for_portfolio("betfair_core") == []


def test_candidate_contexts_are_visible_in_cost_saver_and_hidden_in_hard_stop(tmp_path, monkeypatch):
    if execution_repo_root() is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to seed execution-backed portfolio inputs.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    from tests.unit.test_factory_orchestrator import _prepare_factory_inputs

    _prepare_factory_inputs(project_root)
    orchestrator = FactoryOrchestrator(project_root)
    orchestrator.run_cycle()

    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "cost_saver")
    assert candidate_context_refs_for_portfolio("betfair_core")

    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "hard_stop")
    assert candidate_context_refs_for_portfolio("betfair_core") == []


def test_candidate_context_refs_select_one_isolated_challenger_lane_per_family(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    registry = FactoryRegistry(factory_root)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")

    lineages = [
        {
            "lineage_id": "binance_funding_contrarian:champion",
            "family_id": "binance_funding_contrarian",
            "label": "Funding Champion",
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "iteration_status": "review_requested_replace",
            "maintenance_request_action": "replace",
            "strict_gate_pass": True,
            "fitness_score": 2.0,
            "monthly_roi_pct": 4.0,
            "target_portfolios": ["contrarian_legacy"],
            "execution_health_status": "warning",
            "curated_family_rank": 1,
            "curated_ranking_score": 0.7,
        },
        {
            "lineage_id": "binance_funding_contrarian:challenger:1",
            "family_id": "binance_funding_contrarian",
            "label": "Funding Challenger 1",
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "iteration_status": "tweaked",
            "maintenance_request_action": None,
            "strict_gate_pass": True,
            "fitness_score": 3.4,
            "monthly_roi_pct": 8.0,
            "target_portfolios": ["contrarian_legacy"],
            "execution_health_status": "healthy",
            "curated_family_rank": 1,
            "curated_ranking_score": 1.2,
        },
        {
            "lineage_id": "binance_funding_contrarian:challenger:2",
            "family_id": "binance_funding_contrarian",
            "label": "Funding Challenger 2",
            "active": True,
            "current_stage": "shadow",
            "role": "shadow_challenger",
            "iteration_status": "tweaked",
            "maintenance_request_action": None,
            "strict_gate_pass": True,
            "fitness_score": 1.5,
            "monthly_roi_pct": 2.0,
            "target_portfolios": ["contrarian_legacy"],
            "execution_health_status": "warning",
            "curated_family_rank": 2,
            "curated_ranking_score": 0.5,
        },
    ]
    registry.write_state({"lineages": lineages})

    for item in lineages:
        (registry.lineages_dir / item["lineage_id"]).mkdir(parents=True, exist_ok=True)
        lineage = LineageRecord(
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            label=item["label"],
            role=item["role"],
            current_stage=item["current_stage"],
            target_portfolios=item["target_portfolios"],
            target_venues=["binance"],
            hypothesis_id="h",
            genome_id="g",
            experiment_id="e",
            budget_bucket="adjacent",
            budget_weight_pct=4.0,
            connector_ids=["binance_core"],
            goldfish_workspace="research/goldfish/binance_funding_contrarian",
        )
        registry.save_lineage(lineage)
        genome = StrategyGenome(
            genome_id=f"{item['lineage_id']}:genome",
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            parent_genome_id=None,
            role=item["role"],
            parameters={"selected_model_class": "logit"},
            mutation_bounds=MutationBounds(),
            scientific_domains=["econometrics"],
            budget_bucket="adjacent",
            resource_profile="local",
            budget_weight_pct=4.0,
        )
        registry.save_genome(item["lineage_id"], genome)

    refs = candidate_context_refs_for_portfolio("contrarian_legacy")

    assert len(refs) == 1
    assert refs[0]["lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert refs[0]["runtime_lane_kind"] == "isolated_challenger"
    assert refs[0]["canonical_target_portfolio"] == "contrarian_legacy"
    assert str(refs[0]["runtime_target_portfolio"]).startswith("factory_lane__contrarian_legacy__")
    assert refs[0]["suppressed_sibling_count"] == 2
    assert "binance_funding_contrarian:champion" in refs[0]["suppressed_sibling_lineage_ids"]


def test_candidate_context_refs_prefer_materially_stronger_challenger_without_explicit_replace(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    registry = FactoryRegistry(factory_root)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", 2.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", 7)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", 10)

    lineages = [
        {
            "lineage_id": "binance_cascade_regime:champion",
            "family_id": "binance_cascade_regime",
            "label": "Cascade Champion",
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "iteration_status": "champion",
            "maintenance_request_action": None,
            "strict_gate_pass": False,
            "fitness_score": -12.0,
            "monthly_roi_pct": -2.0,
            "paper_days": 12,
            "trade_count": 30,
            "target_portfolios": ["cascade_alpha"],
            "execution_health_status": "warning",
            "curated_family_rank": 2,
            "curated_ranking_score": -10.0,
        },
        {
            "lineage_id": "binance_cascade_regime:challenger:7",
            "family_id": "binance_cascade_regime",
            "label": "Cascade Challenger 7",
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "iteration_status": "tweaked",
            "maintenance_request_action": None,
            "strict_gate_pass": False,
            "fitness_score": -4.0,
            "monthly_roi_pct": 1.5,
            "paper_days": 10,
            "trade_count": 18,
            "target_portfolios": ["cascade_alpha"],
            "execution_health_status": "healthy",
            "curated_family_rank": 1,
            "curated_ranking_score": -5.0,
        },
    ]
    registry.write_state({"lineages": lineages})

    for item in lineages:
        (registry.lineages_dir / item["lineage_id"]).mkdir(parents=True, exist_ok=True)
        lineage = LineageRecord(
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            label=item["label"],
            role=item["role"],
            current_stage=item["current_stage"],
            target_portfolios=item["target_portfolios"],
            target_venues=["binance"],
            hypothesis_id="h",
            genome_id="g",
            experiment_id="e",
            budget_bucket="adjacent",
            budget_weight_pct=4.0,
            connector_ids=["binance_core"],
            goldfish_workspace="research/goldfish/binance_cascade_regime",
        )
        registry.save_lineage(lineage)
        genome = StrategyGenome(
            genome_id=f"{item['lineage_id']}:genome",
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            parent_genome_id=None,
            role=item["role"],
            parameters={"selected_model_class": "gbdt"},
            mutation_bounds=MutationBounds(),
            scientific_domains=["microstructure"],
            budget_bucket="adjacent",
            resource_profile="local",
            budget_weight_pct=4.0,
        )
        registry.save_genome(item["lineage_id"], genome)

    refs = candidate_context_refs_for_portfolio("cascade_alpha")

    assert len(refs) == 1
    assert refs[0]["lineage_id"] == "binance_cascade_regime:challenger:7"
    assert refs[0]["runtime_lane_kind"] == "isolated_challenger"
    assert refs[0]["runtime_lane_reason"] == "challenger_materially_stronger"


def test_candidate_context_refs_do_not_flip_on_missing_curated_score_defaults(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    registry = FactoryRegistry(factory_root)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "full")
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", 2.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", 7)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", 10)

    lineages = [
        {
            "lineage_id": "binance_funding_contrarian:champion",
            "family_id": "binance_funding_contrarian",
            "label": "Funding Champion",
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "iteration_status": "champion",
            "maintenance_request_action": None,
            "strict_gate_pass": False,
            "fitness_score": -95.0,
            "monthly_roi_pct": 21.0,
            "paper_days": 13,
            "trade_count": 26,
            "target_portfolios": ["contrarian_legacy"],
            "execution_health_status": "critical",
            "curated_family_rank": 3,
            "curated_ranking_score": -13.6,
        },
        {
            "lineage_id": "binance_funding_contrarian:challenger:7",
            "family_id": "binance_funding_contrarian",
            "label": "Funding Challenger 7",
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "iteration_status": "tweaked",
            "maintenance_request_action": None,
            "strict_gate_pass": False,
            "fitness_score": -95.0,
            "monthly_roi_pct": 0.5,
            "paper_days": 3,
            "trade_count": 8,
            "target_portfolios": ["contrarian_legacy"],
            "execution_health_status": "critical",
            "curated_family_rank": None,
            "curated_ranking_score": None,
        },
    ]
    registry.write_state({"lineages": lineages})

    for item in lineages:
        (registry.lineages_dir / item["lineage_id"]).mkdir(parents=True, exist_ok=True)
        lineage = LineageRecord(
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            label=item["label"],
            role=item["role"],
            current_stage=item["current_stage"],
            target_portfolios=item["target_portfolios"],
            target_venues=["binance"],
            hypothesis_id="h",
            genome_id="g",
            experiment_id="e",
            budget_bucket="adjacent",
            budget_weight_pct=4.0,
            connector_ids=["binance_core"],
            goldfish_workspace="research/goldfish/binance_funding_contrarian",
        )
        registry.save_lineage(lineage)
        genome = StrategyGenome(
            genome_id=f"{item['lineage_id']}:genome",
            lineage_id=item["lineage_id"],
            family_id=item["family_id"],
            parent_genome_id=None,
            role=item["role"],
            parameters={"selected_model_class": "logit"},
            mutation_bounds=MutationBounds(),
            scientific_domains=["econometrics"],
            budget_bucket="adjacent",
            resource_profile="local",
            budget_weight_pct=4.0,
        )
        registry.save_genome(item["lineage_id"], genome)

    refs = candidate_context_refs_for_portfolio("contrarian_legacy")

    assert len(refs) == 1
    assert refs[0]["lineage_id"] == "binance_funding_contrarian:champion"
    assert refs[0]["runtime_lane_kind"] == "primary_incumbent"
    assert refs[0]["runtime_lane_reason"] == "family_primary_incumbent"
