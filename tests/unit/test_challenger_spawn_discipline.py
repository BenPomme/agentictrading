"""
Tests for _seed_challengers single-lineage guards.

These tests verify that the current FactoryOrchestrator._seed_challengers logic
blocks replacement creation while any lineage is still active in the family, and
only reaches the replacement path once the old champion has been retired.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List
from unittest.mock import patch

import config
from factory.contracts import (
    FactoryFamily,
    LineageRecord,
    LineageRole,
    PromotionStage,
)
from factory.orchestrator import FactoryOrchestrator


def _make_family(family_id: str = "test_family") -> FactoryFamily:
    return FactoryFamily(
        family_id=family_id,
        label="Test Family",
        thesis="Test thesis.",
        target_portfolios=["research_factory"],
        target_venues=["binance"],
        primary_connector_ids=["binance_core"],
        champion_lineage_id=f"{family_id}:champion",
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"incumbent": 0.6, "adjacent": 0.3, "moonshot": 0.1},
        queue_stage=PromotionStage.SHADOW.value,
        explainer="Test explainer.",
        incubation_status="core",
    )


def _make_lineage(
    lineage_id: str,
    family_id: str = "test_family",
    *,
    current_stage: str = PromotionStage.WALKFORWARD.value,
    iteration_status: str = "running",
    role: str = LineageRole.SHADOW_CHALLENGER.value,
    active: bool = True,
) -> LineageRecord:
    return LineageRecord(
        lineage_id=lineage_id,
        family_id=family_id,
        label=lineage_id,
        role=role,
        current_stage=current_stage,
        target_portfolios=["research_factory"],
        target_venues=["binance"],
        hypothesis_id=f"{lineage_id}:hyp",
        genome_id=f"{lineage_id}:genome",
        experiment_id=f"{lineage_id}:exp",
        budget_bucket="incumbent",
        budget_weight_pct=1.0,
        connector_ids=["binance_core"],
        goldfish_workspace=lineage_id,
        active=active,
        iteration_status=iteration_status,
    )


def _make_orchestrator(tmp_path: Path, monkeypatch) -> FactoryOrchestrator:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    return FactoryOrchestrator(project_root)


def _call_seed_challengers(
    orchestrator: FactoryOrchestrator,
    family: FactoryFamily,
    lineages: List[LineageRecord],
    *,
    monkeypatch,
    env_overrides: dict | None = None,
) -> List[LineageRecord]:
    lineages_by_family = {family.family_id: list(lineages)}
    monkeypatch.setattr(orchestrator.registry, "load_genome", lambda _lid: None)
    monkeypatch.setattr(orchestrator.registry, "save_family", lambda _fam: None)
    env_overrides = env_overrides or {}
    with patch.dict(os.environ, env_overrides):
        orchestrator._seed_challengers(
            family,
            lineages_by_family,
            runtime_mode_value="full",
            recent_actions=[],
        )
    return lineages_by_family[family.family_id]


def test_seed_challengers_blocked_when_champion_active(tmp_path, monkeypatch):
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()
    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.PAPER.value,
        iteration_status="running",
    )

    after = _call_seed_challengers(orchestrator, family, [champion], monkeypatch=monkeypatch)

    assert len(after) == 1


def test_seed_challengers_blocked_when_any_extra_variant_is_active(tmp_path, monkeypatch):
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()
    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.WALKFORWARD.value,
    )
    shadow = _make_lineage(
        "test_family:shadow",
        role=LineageRole.SHADOW_CHALLENGER.value,
        current_stage=PromotionStage.SHADOW.value,
    )

    after = _call_seed_challengers(orchestrator, family, [champion, shadow], monkeypatch=monkeypatch)

    assert len(after) == 2


def test_seed_challengers_uses_retired_champion_once_family_is_empty(tmp_path, monkeypatch):
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()
    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.PAPER.value,
        iteration_status="retired",
        active=False,
    )

    genome_load_called = []

    def _track_load_genome(lid):
        genome_load_called.append(lid)
        return None

    monkeypatch.setattr(orchestrator.registry, "load_genome", _track_load_genome)
    monkeypatch.setattr(orchestrator.registry, "load_lineage", lambda lineage_id: champion if lineage_id == champion.lineage_id else None)
    monkeypatch.setattr(orchestrator.registry, "save_family", lambda _fam: None)

    orchestrator._seed_challengers(
        family,
        {family.family_id: [champion]},
        runtime_mode_value="full",
        recent_actions=[],
    )

    assert genome_load_called == ["test_family:champion"]
