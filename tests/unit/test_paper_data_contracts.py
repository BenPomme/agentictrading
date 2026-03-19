from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from factory.contracts import LineageRecord, MutationBounds, StrategyGenome
from factory.paper_data import (
    assess_paper_data_readiness,
    build_paper_data_contract,
    build_refresh_plan,
)
from factory.registry import FactoryRegistry


def _minute_bars(start: datetime, periods: int = 6) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=periods, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [100 + i for i in range(periods)],
            "high": [101 + i for i in range(periods)],
            "low": [99 + i for i in range(periods)],
            "close": [100.5 + i for i in range(periods)],
            "volume": [10 + i for i in range(periods)],
        },
        index=idx,
    )


def test_model_requiring_2m_bars_passes_with_fresh_1m_alpaca_data(tmp_path):
    bars_dir = tmp_path / "data" / "alpaca" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    _minute_bars(datetime.now(timezone.utc) - timedelta(minutes=5)).to_parquet(bars_dir / "SPY.parquet")
    (tmp_path / "data" / "alpaca" / "metadata.json").write_text(
        json.dumps(
            {
                "last_refresh": datetime.now(timezone.utc).isoformat(),
                "timeframe": "1Min",
            }
        ),
        encoding="utf-8",
    )

    contract = build_paper_data_contract(
        {},
        model_requirement={
            "source": "alpaca",
            "instruments": ["SPY"],
            "fields": ["close"],
            "cadence": "2m",
            "raw_cadence_seconds": 60,
            "freshness_sla_seconds": 300,
        },
    )
    result = assess_paper_data_readiness(contract, tmp_path)

    assert result.ready is True
    assert "ready" in result.blocking_reason


def test_model_requiring_1m_bars_is_blocked_when_only_5m_alpaca_data_exists(tmp_path):
    bars_dir = tmp_path / "data" / "alpaca" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range(start=datetime.now(timezone.utc) - timedelta(minutes=25), periods=5, freq="5min", tz="UTC")
    pd.DataFrame(
        {"open": [1, 2, 3, 4, 5], "high": [2, 3, 4, 5, 6], "low": [0, 1, 2, 3, 4], "close": [1.5, 2.5, 3.5, 4.5, 5.5]},
        index=idx,
    ).to_parquet(bars_dir / "SPY.parquet")
    (tmp_path / "data" / "alpaca" / "metadata.json").write_text(
        json.dumps(
            {
                "last_refresh": datetime.now(timezone.utc).isoformat(),
                "timeframe": "5Min",
            }
        ),
        encoding="utf-8",
    )

    contract = build_paper_data_contract(
        {},
        model_requirement={
            "source": "alpaca",
            "instruments": ["SPY"],
            "fields": ["close"],
            "cadence": "1m",
            "raw_cadence_seconds": 60,
            "freshness_sla_seconds": 300,
        },
    )
    result = assess_paper_data_readiness(contract, tmp_path)

    assert result.ready is False
    assert "only has 5Min bars" in result.blocking_reason


def test_cross_venue_contract_blocks_when_one_required_feed_is_stale(tmp_path):
    bars_dir = tmp_path / "data" / "alpaca" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    _minute_bars(datetime.now(timezone.utc) - timedelta(minutes=3)).to_parquet(bars_dir / "SPY.parquet")
    (tmp_path / "data" / "alpaca" / "metadata.json").write_text(
        json.dumps({"last_refresh": datetime.now(timezone.utc).isoformat(), "timeframe": "1Min"}),
        encoding="utf-8",
    )
    prices_dir = tmp_path / "data" / "polymarket" / "prices_history"
    prices_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": [datetime.now(timezone.utc) - timedelta(hours=2)],
            "price": [0.54],
        }
    ).to_parquet(prices_dir / "market1.parquet", index=False)
    (tmp_path / "data" / "polymarket" / "markets_metadata.json").write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "interval": "1m"}),
        encoding="utf-8",
    )

    contract = build_paper_data_contract(
        {
            "paper_data_contract": {
                "cross_venue_required": True,
                "requirements": [
                    {"source": "alpaca", "venue": "alpaca", "instruments": ["SPY"], "fields": ["close"], "feed_type": "bars", "raw_cadence_seconds": 60, "freshness_sla_seconds": 300},
                    {"source": "polymarket", "venue": "polymarket", "instruments": ["market1"], "fields": ["price"], "feed_type": "prediction_history", "raw_cadence_seconds": 60, "freshness_sla_seconds": 300},
                ],
            }
        }
    )
    result = assess_paper_data_readiness(contract, tmp_path)

    assert result.ready is False
    assert "cross-venue" in result.blocking_reason


def test_refresh_plan_prefers_fast_schedule_for_active_1m_models(tmp_path, monkeypatch):
    factory_root = tmp_path / "factory"
    monkeypatch.setattr("config.FACTORY_ROOT", str(factory_root))
    registry = FactoryRegistry(factory_root)
    lineage = LineageRecord(
        lineage_id="fam:lin:1",
        family_id="fam",
        label="Fam",
        role="paper_challenger",
        current_stage="paper",
        target_portfolios=["alpaca_paper"],
        target_venues=["alpaca"],
        hypothesis_id="h",
        genome_id="g",
        experiment_id="e",
        budget_bucket="incumbent",
        budget_weight_pct=1.0,
        connector_ids=["alpaca_stocks"],
        goldfish_workspace="research/goldfish/fam",
    )
    genome = StrategyGenome(
        genome_id="g",
        lineage_id=lineage.lineage_id,
        family_id="fam",
        parent_genome_id=None,
        role="paper_challenger",
        parameters={
            "paper_data_contract": {
                "requirements": [
                    {"source": "alpaca", "venue": "alpaca", "instruments": ["SPY"], "fields": ["close"], "feed_type": "bars", "raw_cadence_seconds": 60, "required_bar_seconds": 60}
                ]
            }
        },
        mutation_bounds=MutationBounds(),
        scientific_domains=[],
        budget_bucket="incumbent",
        resource_profile="local-first-hybrid",
        budget_weight_pct=1.0,
    )
    registry.save_research_pack(
        hypothesis=type("Hyp", (), {"to_dict": lambda self: {"hypothesis_id": "h", "family_id": "fam", "title": "t", "thesis": "x", "scientific_domains": [], "lead_agent_role": "Director", "success_metric": "m", "guardrails": [], "origin": "seeded_family", "agent_notes": []}})(),
        genome=genome,
        experiment=type("Exp", (), {"to_dict": lambda self: {"experiment_id": "e", "lineage_id": lineage.lineage_id, "family_id": "fam", "hypothesis_id": "h", "genome_id": "g", "goldfish_workspace": "w", "pipeline_stages": [], "backend_mode": "goldfish_sidecar", "resource_profile": "local-first-hybrid", "inputs": {}}})(),
        lineage=lineage,
    )

    plan = build_refresh_plan(tmp_path)
    alpaca = next(item for item in plan if item.source == "alpaca")

    assert alpaca.interval_seconds == 60


def test_binance_bar_model_is_blocked_when_only_funding_history_exists(tmp_path):
    funding_dir = tmp_path / "data" / "funding_history" / "funding_rates"
    funding_dir.mkdir(parents=True, exist_ok=True)
    (funding_dir / "metadata.json").write_text(
        json.dumps({"last_refresh": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    contract = build_paper_data_contract(
        {},
        model_requirement={
            "source": "binance",
            "venue": "binance",
            "instruments": ["BTCUSDT"],
            "fields": ["close"],
            "feed_type": "bars",
            "raw_cadence_seconds": 60,
            "freshness_sla_seconds": 300,
        },
    )

    result = assess_paper_data_readiness(contract, tmp_path)

    assert result.ready is False
    assert "intraday bars missing" in result.blocking_reason
