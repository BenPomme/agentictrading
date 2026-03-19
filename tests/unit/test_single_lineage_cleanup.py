from __future__ import annotations

from factory.contracts import FactoryFamily, LineageRecord
from factory.registry import FactoryRegistry
from scripts.cleanup_single_lineage_policy import cleanup_single_lineage_policy


def _family() -> FactoryFamily:
    return FactoryFamily(
        family_id="family_a",
        label="Family A",
        thesis="Test",
        target_portfolios=["alpaca_paper"],
        target_venues=["alpaca"],
        primary_connector_ids=["alpaca_stocks"],
        champion_lineage_id="family_a:champion",
        shadow_challenger_ids=["family_a:shadow"],
        paper_challenger_ids=["family_a:paper"],
        budget_split={"incumbent": 1.0},
        queue_stage="paper",
        explainer="Test family",
    )


def _lineage(lineage_id: str, *, role: str, stage: str, active: bool = True) -> LineageRecord:
    return LineageRecord(
        lineage_id=lineage_id,
        family_id="family_a",
        label=lineage_id,
        role=role,
        current_stage=stage,
        target_portfolios=["alpaca_paper"],
        target_venues=["alpaca"],
        hypothesis_id=f"{lineage_id}:h",
        genome_id=f"{lineage_id}:g",
        experiment_id=f"{lineage_id}:e",
        budget_bucket="incumbent",
        budget_weight_pct=1.0,
        connector_ids=["alpaca_stocks"],
        goldfish_workspace="research/goldfish/family_a",
        active=active,
    )


def test_cleanup_single_lineage_policy_keeps_one_champion_and_backfills_metadata(tmp_path):
    registry = FactoryRegistry(tmp_path / "factory")
    family = _family()
    registry.save_family(family)
    for lineage in [
        _lineage("family_a:champion", role="champion", stage="paper"),
        _lineage("family_a:paper", role="paper_challenger", stage="paper"),
        _lineage("family_a:shadow", role="shadow_challenger", stage="walkforward"),
    ]:
        (registry.lineages_dir / lineage.lineage_id).mkdir(parents=True, exist_ok=True)
        registry.save_lineage(lineage)

    changes = cleanup_single_lineage_policy(registry, dry_run=False)

    refreshed_family = registry.load_family("family_a")
    refreshed_paper = registry.load_lineage("family_a:paper")
    refreshed_shadow = registry.load_lineage("family_a:shadow")
    assert changes[0]["kept_champion_lineage_id"] == "family_a:champion"
    assert refreshed_family is not None
    assert refreshed_family.paper_challenger_ids == []
    assert refreshed_family.shadow_challenger_ids == []
    assert "promotion_scorecard" in refreshed_family.metadata
    assert "inventor_swarms" in refreshed_family.metadata
    assert refreshed_paper is not None and refreshed_paper.active is False
    assert refreshed_shadow is not None and refreshed_shadow.active is False
