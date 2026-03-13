from __future__ import annotations

from pathlib import Path

import config
from factory.idea_intake import (
    annotate_idea_statuses,
    maybe_run_manual_idea_watch,
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


def test_parse_ideas_markdown_accepts_colon_entries_and_dedupes_ids(tmp_path: Path) -> None:
    (tmp_path / "IDEAS.md").write_text(
        """10. Existing idea
"Old one."

10: new idea: Polymarket Prices lag Binance
"Fresh one."

11: new idea: war in iran
"Oil thesis."
""",
        encoding="utf-8",
    )

    ideas = parse_ideas_markdown(tmp_path)

    assert [row["idea_id"] for row in ideas] == ["idea_010", "idea_010_2", "idea_011"]
    assert ideas[1]["title"] == "Polymarket Prices lag Binance"
    assert ideas[2]["title"] == "war in iran"


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


def test_annotate_idea_statuses_marks_new_model_lineage_as_incubated() -> None:
    ideas = [{"idea_id": "idea_011", "title": "Polymarket lag", "family_candidates": []}]
    lineages = [
        {
            "lineage_id": "polymarket_cross_venue:challenger:7",
            "source_idea_id": "idea_011",
            "family_id": "polymarket_cross_venue",
            "current_stage": "paper",
            "creation_kind": "new_model",
            "active": True,
        }
    ]

    annotated = annotate_idea_statuses(ideas, lineages)

    assert annotated[0]["status"] == "incubated"


def test_split_active_and_archived_ideas_filters_processed_items() -> None:
    buckets = split_active_and_archived_ideas(
        [
            {"idea_id": "a", "status": "adapted"},
            {"idea_id": "n", "status": "new", "rank": 11, "source": "manual"},
            {"idea_id": "b", "status": "tested"},
            {"idea_id": "d", "status": "incubated"},
            {"idea_id": "c", "status": "promoted"},
        ]
    )

    assert [row["idea_id"] for row in buckets["active"]] == ["n", "a"]
    assert [row["idea_id"] for row in buckets["archived"]] == ["d", "b", "c"]


def test_manual_idea_watch_detects_new_manual_ideas(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    (project_root / "IDEAS.md").write_text(
        """1. First idea
"One."
""",
        encoding="utf-8",
    )

    first = maybe_run_manual_idea_watch(project_root)
    second = maybe_run_manual_idea_watch(project_root)

    assert first["ran"] is True
    assert first["new_count"] == 1
    assert second["ran"] is True
    assert second["new_count"] == 0

    (project_root / "IDEAS.md").write_text(
        """1. First idea
"One."

2: new idea: Second idea
"Two."
""",
        encoding="utf-8",
    )

    third = maybe_run_manual_idea_watch(project_root)

    assert third["new_count"] == 1
    assert third["new_items"][0]["title"] == "Second idea"
