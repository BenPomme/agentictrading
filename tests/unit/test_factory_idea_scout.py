from __future__ import annotations

import json
from pathlib import Path

import config
from factory.idea_intake import load_generated_ideas
from factory.idea_scout import maybe_run_idea_scout


def test_idea_scout_writes_generated_ideas(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_IDEA_SCOUT_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_IDEA_SCOUT_INTERVAL_HOURS", 48)
    monkeypatch.setattr(config, "FACTORY_IDEA_SCOUT_MAX_NEW_PER_RUN", 2)

    import factory.idea_scout as scout

    monkeypatch.setattr(
        scout,
        "_fetch_feed_items",
        lambda query, limit=5: [
            {
                "title": f"{query} alpha idea",
                "link": f"https://example.com/{query.replace(' ', '-')}",
                "summary": "A new systematic trading idea around market regimes and probability.",
                "query": query,
            }
        ],
    )

    result = maybe_run_idea_scout(project_root)

    assert result["ran"] is True
    assert result["new_count"] == 2
    rows = load_generated_ideas(project_root)
    assert len(rows) == 2
    assert all(row["source"] == "scout" for row in rows)


def test_idea_scout_respects_interval(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_IDEA_SCOUT_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_IDEA_SCOUT_INTERVAL_HOURS", 48)

    state_path = factory_root / "ideas" / "scout_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_run_at": "2026-03-11T12:00:00+00:00"}), encoding="utf-8")

    import factory.idea_scout as scout

    monkeypatch.setattr(scout, "_fetch_feed_items", lambda query, limit=5: [])
    result = maybe_run_idea_scout(project_root)

    assert result["ran"] is False
    assert result["reason"] == "interval_not_elapsed"
