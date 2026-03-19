from __future__ import annotations

from pathlib import Path

import config
from factory.contracts import (
    ExperimentSpec,
    FactoryFamily,
    LineageRecord,
    MutationBounds,
    PromotionStage,
    ResearchHypothesis,
    StrategyGenome,
)
from factory.orchestrator import FactoryOrchestrator
from factory.registry import FactoryRegistry
from factory.runtime_admission import assess_lineage_runtime_admission


def _seed_lineage(
    root: Path,
    *,
    family_id: str,
    lineage_id: str,
    venues: list[str],
    genome_params: dict | None = None,
) -> FactoryRegistry:
    factory_root = root / "data" / "factory"
    registry = FactoryRegistry(factory_root)
    family = FactoryFamily(
        family_id=family_id,
        label=family_id,
        thesis="thesis",
        target_portfolios=[family_id],
        target_venues=venues,
        primary_connector_ids=[],
        champion_lineage_id=lineage_id,
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"research": 1.0},
        queue_stage=PromotionStage.WALKFORWARD.value,
        explainer="explainer",
    )
    registry.save_family(family)
    hypothesis = ResearchHypothesis(
        hypothesis_id=f"{lineage_id}:hypothesis",
        family_id=family_id,
        title="title",
        thesis="thesis",
        scientific_domains=["econ"],
        lead_agent_role="lead",
        success_metric="roi",
        guardrails=[],
    )
    genome = StrategyGenome(
        genome_id=f"{lineage_id}:genome",
        lineage_id=lineage_id,
        family_id=family_id,
        parent_genome_id=None,
        role="champion",
        parameters=dict(genome_params or {}),
        mutation_bounds=MutationBounds(),
        scientific_domains=["econ"],
        budget_bucket="standard",
        resource_profile="local",
        budget_weight_pct=1.0,
    )
    experiment = ExperimentSpec(
        experiment_id=f"{lineage_id}:experiment",
        lineage_id=lineage_id,
        family_id=family_id,
        hypothesis_id=hypothesis.hypothesis_id,
        genome_id=genome.genome_id,
        goldfish_workspace=str(root / "research" / "goldfish" / family_id),
        pipeline_stages=["dataset", "train"],
        backend_mode="goldfish_sidecar",
        resource_profile="local",
    )
    lineage = LineageRecord(
        lineage_id=lineage_id,
        family_id=family_id,
        label="lineage",
        role="champion",
        current_stage=PromotionStage.WALKFORWARD.value,
        target_portfolios=[family_id],
        target_venues=venues,
        hypothesis_id=hypothesis.hypothesis_id,
        genome_id=genome.genome_id,
        experiment_id=experiment.experiment_id,
        budget_bucket="standard",
        budget_weight_pct=1.0,
        connector_ids=[],
        goldfish_workspace=experiment.goldfish_workspace,
    )
    registry.save_research_pack(
        hypothesis=hypothesis,
        genome=genome,
        experiment=experiment,
        lineage=lineage,
    )
    return registry


def test_runtime_admission_blocks_stock_hmm_fallback(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(project_root / "data" / "factory"))
    registry = _seed_lineage(
        project_root,
        family_id="oil_family",
        lineage_id="oil_family:champion",
        venues=["yahoo", "polymarket"],
    )
    lineage = registry.load_lineage("oil_family:champion")
    assert lineage is not None

    result = assess_lineage_runtime_admission(project_root, registry, lineage)

    assert result.admitted is False
    assert result.runner_kind == "stock_hmm_fallback"
    assert result.reason == "runtime_model_missing:stock_fallback_requires_explicit_runtime_model"


def test_runtime_admission_accepts_explicit_sandbox_safe_model(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(project_root / "data" / "factory"))
    model_path = project_root / "models" / "safe_model.py"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(
        "\n".join(
            [
                "import pandas as pd",
                "",
                "class SafeModel:",
                "    def name(self):",
                "        return 'safe'",
                "    def configure(self, genome):",
                "        self.genome = genome",
                "    def required_data(self):",
                "        return {'source': 'yahoo', 'instruments': ['SPY']}",
                "    def fit(self, df):",
                "        return self",
                "    def predict(self, df):",
                "        return pd.Series([0] * len(df), index=df.index)",
                "    def position_size(self, signal, equity):",
                "        return 0.0",
            ]
        ),
        encoding="utf-8",
    )
    registry = _seed_lineage(
        project_root,
        family_id="safe_family",
        lineage_id="safe_family:champion",
        venues=["yahoo"],
        genome_params={
            "model_code_path": str(model_path),
            "model_class_name": "SafeModel",
        },
    )
    lineage = registry.load_lineage("safe_family:champion")
    assert lineage is not None

    result = assess_lineage_runtime_admission(project_root, registry, lineage)

    assert result.admitted is True
    assert result.runner_kind == "dynamic_model"
    assert result.model_class_name == "SafeModel"


def test_shadow_activation_eligibility_requires_runtime_admission(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "FACTORY_ROOT", str(project_root / "data" / "factory"))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(project_root / "research" / "goldfish"))
    registry = _seed_lineage(
        project_root,
        family_id="oil_family",
        lineage_id="oil_family:champion",
        venues=["yahoo", "polymarket"],
    )
    lineage = registry.load_lineage("oil_family:champion")
    assert lineage is not None
    orchestrator = FactoryOrchestrator(project_root)
    monkeypatch.setattr(
        orchestrator.registry,
        "latest_evaluation_by_stage",
        lambda _lineage_id: {
            PromotionStage.WALKFORWARD.value: object(),
            PromotionStage.STRESS.value: object(),
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_execution_validation_snapshot",
        lambda _lineage: {"issue_codes": []},
    )

    assert orchestrator._isolated_lane_activation_eligible(lineage) is False
