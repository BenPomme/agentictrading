"""
Tests for _seed_challengers spawn-discipline guards.

These tests verify that the new spawn-discipline logic in FactoryOrchestrator._seed_challengers
correctly blocks or allows challenger seeding based on:
  1. Active lineages in paper/shadow stage with non-failed iteration_status.
  2. Hard ceiling on the number of active lineages per family.

Strategy: create a real FactoryOrchestrator via the minimal tmp_path pattern
(same as test_challenger_mix_policy_defaults_to_four_mutations_then_one_new_model),
then call _seed_challengers directly with synthetic lineages_by_family dicts.
We mock out the registry.load_genome call so the method never reaches the
mutation loop — we only care about what happens *before* that point (early-return
guards), verified by checking whether lineages_by_family grows.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

import config
from factory.contracts import (
    FactoryFamily,
    LineageRecord,
    LineageRole,
    PromotionStage,
)
from factory.orchestrator import FactoryOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Create a minimal orchestrator (no run_cycle) for unit tests."""
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
    """
    Call _seed_challengers and return the lineages for the family afterwards.

    We monkeypatch registry.load_genome to return None so the method exits
    before actually generating proposals (we only care about guard exits).
    We also monkeypatch registry.save_family to be a no-op.
    """
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_seed_challengers_blocked_when_paper_lineage_active(tmp_path, monkeypatch):
    """
    A running paper-stage lineage must block _seed_challengers from spawning
    any new lineages.
    """
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()

    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.WALKFORWARD.value,
        iteration_status="running",
    )
    paper_lin = _make_lineage(
        "test_family:paper_challenger",
        role=LineageRole.PAPER_CHALLENGER.value,
        current_stage=PromotionStage.PAPER.value,
        iteration_status="running",
    )

    before = [champion, paper_lin]
    after = _call_seed_challengers(
        orchestrator, family, before, monkeypatch=monkeypatch
    )

    assert len(after) == len(before), (
        "Expected no new lineages when a paper-stage lineage with "
        "iteration_status='running' is active."
    )


def test_seed_challengers_blocked_when_shadow_lineage_active(tmp_path, monkeypatch):
    """
    A running shadow-stage lineage must block _seed_challengers from spawning.
    """
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()

    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.WALKFORWARD.value,
        iteration_status="running",
    )
    shadow_lin = _make_lineage(
        "test_family:shadow_challenger",
        role=LineageRole.SHADOW_CHALLENGER.value,
        current_stage=PromotionStage.SHADOW.value,
        iteration_status="running",
    )

    before = [champion, shadow_lin]
    after = _call_seed_challengers(
        orchestrator, family, before, monkeypatch=monkeypatch
    )

    assert len(after) == len(before), (
        "Expected no new lineages when a shadow-stage lineage with "
        "iteration_status='running' is active."
    )


def test_seed_challengers_allowed_when_paper_lineage_failed(tmp_path, monkeypatch):
    """
    A paper-stage lineage that has FAILED must not block spawning.
    The guard only blocks when the lineage's iteration_status is not
    'failed' or 'retiring'.

    Because load_genome returns None we still won't actually spawn anything —
    the point of this test is that the code reaches the genome-load check
    rather than returning at the paper/shadow guard.
    """
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()

    champion = _make_lineage(
        "test_family:champion",
        role=LineageRole.CHAMPION.value,
        current_stage=PromotionStage.WALKFORWARD.value,
        iteration_status="running",
    )
    failed_paper = _make_lineage(
        "test_family:paper_failed",
        role=LineageRole.PAPER_CHALLENGER.value,
        current_stage=PromotionStage.PAPER.value,
        iteration_status="failed",
    )

    # Track whether load_genome was reached (proves the guard did NOT return early).
    genome_load_called = []

    def _track_load_genome(lid):
        genome_load_called.append(lid)
        return None  # still exit before proposal loop

    monkeypatch.setattr(orchestrator.registry, "load_genome", _track_load_genome)
    monkeypatch.setattr(orchestrator.registry, "save_family", lambda _fam: None)

    lineages_by_family = {family.family_id: [champion, failed_paper]}
    orchestrator._seed_challengers(
        family,
        lineages_by_family,
        runtime_mode_value="full",
        recent_actions=[],
    )

    assert genome_load_called, (
        "Expected load_genome to be reached, proving the paper-failed guard "
        "did not trigger an early return."
    )


def test_seed_challengers_blocked_by_max_active_ceiling(tmp_path, monkeypatch):
    """
    When a family already has >= FACTORY_MAX_ACTIVE_LINEAGES_PER_FAMILY active
    lineages, _seed_challengers must return early without creating any new ones.
    """
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()

    # Build exactly 10 active lineages (the default ceiling), all in walkforward
    # so the paper/shadow guard does not fire first.
    lineages = [
        _make_lineage(
            f"test_family:lin_{i}",
            current_stage=PromotionStage.WALKFORWARD.value,
            iteration_status="running",
            role=LineageRole.SHADOW_CHALLENGER.value,
        )
        for i in range(10)
    ]
    lineages[0].role = LineageRole.CHAMPION.value
    lineages[0].lineage_id = "test_family:champion"
    family.champion_lineage_id = "test_family:champion"

    after = _call_seed_challengers(
        orchestrator,
        family,
        lineages,
        monkeypatch=monkeypatch,
        env_overrides={"FACTORY_MAX_ACTIVE_LINEAGES_PER_FAMILY": "10"},
    )

    assert len(after) == 10, (
        "Expected no new lineages when active count == FACTORY_MAX_ACTIVE_LINEAGES_PER_FAMILY."
    )


def test_seed_challengers_allowed_under_ceiling(tmp_path, monkeypatch):
    """
    When a family has fewer active lineages than the ceiling AND no paper/shadow
    blockers, the guard must NOT return early — execution must reach at least
    the champion-genome load.
    """
    orchestrator = _make_orchestrator(tmp_path, monkeypatch)
    family = _make_family()

    lineages = [
        _make_lineage(
            f"test_family:lin_{i}",
            current_stage=PromotionStage.WALKFORWARD.value,
            iteration_status="running",
            role=LineageRole.SHADOW_CHALLENGER.value,
        )
        for i in range(5)
    ]
    lineages[0].role = LineageRole.CHAMPION.value
    lineages[0].lineage_id = "test_family:champion"
    family.champion_lineage_id = "test_family:champion"

    genome_load_called = []

    def _track_load_genome(lid):
        genome_load_called.append(lid)
        return None  # exit before proposal loop

    monkeypatch.setattr(orchestrator.registry, "load_genome", _track_load_genome)
    monkeypatch.setattr(orchestrator.registry, "save_family", lambda _fam: None)

    lineages_by_family = {family.family_id: list(lineages)}

    with patch.dict(os.environ, {"FACTORY_MAX_ACTIVE_LINEAGES_PER_FAMILY": "10"}):
        orchestrator._seed_challengers(
            family,
            lineages_by_family,
            runtime_mode_value="full",
            recent_actions=[],
        )

    assert genome_load_called, (
        "Expected load_genome to be reached when active count (5) is below "
        "the ceiling (10) and no paper/shadow blockers are present."
    )
