from __future__ import annotations

from pathlib import Path

from factory.idea_intake import (
    annotate_idea_statuses,
    parse_ideas_markdown,
    relevant_ideas_for_family,
    split_active_and_archived_ideas,
)


def test_parse_ideas_markdown_extracts_ranked_items(tmp_path: Path) -> None:
    (tmp_path / "IDEAS.md").write_text(
        """# Ideas

1. The Citadel Market Regime Classifier
"Classify market conditions before trading."

2. The IMC Trading Earnings Theta Crusher
"Systematically trade around earnings announcements."
""",
        encoding="utf-8",
    )

    ideas = parse_ideas_markdown(tmp_path)

    assert len(ideas) == 2
    assert ideas[0]["idea_id"] == "idea_001"
    assert ideas[0]["family_candidates"]
    assert "prediction_markets" in ideas[1]["tags"]


def test_relevant_ideas_for_family_prefers_direct_matches(tmp_path: Path) -> None:
    (tmp_path / "IDEAS.md").write_text(
        """1. The Citadel Market Regime Classifier
"Classify market conditions before trading."

2. The Tastytrade 0DTE SPX Credit Spread Scanner
"Scan daily theta opportunities."
""",
        encoding="utf-8",
    )

    ideas = relevant_ideas_for_family(tmp_path, "binance_cascade_regime", limit=2)

    assert ideas
    assert ideas[0]["idea_id"] == "idea_001"


def test_annotate_idea_statuses_uses_linked_lineages() -> None:
    ideas = [
        {"idea_id": "idea_001", "title": "Regime", "family_candidates": ["binance_cascade_regime"]},
        {"idea_id": "idea_002", "title": "Earnings", "family_candidates": []},
    ]
    lineages = [
        {"lineage_id": "a", "source_idea_id": "idea_001", "family_id": "binance_cascade_regime", "current_stage": "paper", "active": True},
        {"lineage_id": "b", "source_idea_id": "idea_002", "family_id": "polymarket_cross_venue", "current_stage": "approved_live", "active": True},
    ]

    annotated = annotate_idea_statuses(ideas, lineages)

    assert annotated[0]["status"] == "tested"
    assert annotated[1]["status"] == "promoted"


def test_split_active_and_archived_ideas_filters_processed_items() -> None:
    buckets = split_active_and_archived_ideas(
        [
            {"idea_id": "a", "status": "adapted"},
            {"idea_id": "b", "status": "tested"},
            {"idea_id": "c", "status": "promoted"},
        ]
    )

    assert [row["idea_id"] for row in buckets["active"]] == ["a"]
    assert [row["idea_id"] for row in buckets["archived"]] == ["b", "c"]
