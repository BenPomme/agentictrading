from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config
from factory.contracts import FactoryFamily, LineageRecord, MutationBounds, StrategyGenome
from factory.orchestrator import FactoryOrchestrator


def _orchestrator(tmp_path, monkeypatch) -> FactoryOrchestrator:
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(project_root / "research" / "goldfish"))
    monkeypatch.setattr(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", False)
    monkeypatch.setenv("FACTORY_ENABLE_GOLDFISH_PROVENANCE", "false")
    return FactoryOrchestrator(project_root)


def _save_pack(orchestrator: FactoryOrchestrator, family: FactoryFamily, lineage: LineageRecord, genome: StrategyGenome) -> None:
    orchestrator.registry.save_family(family)
    orchestrator.registry.save_research_pack(
        hypothesis=type("Hyp", (), {"to_dict": lambda self: {"hypothesis_id": "h", "family_id": family.family_id, "title": "t", "thesis": "x", "scientific_domains": [], "lead_agent_role": "Director", "success_metric": "m", "guardrails": [], "origin": "seeded_family", "agent_notes": []}})(),
        genome=genome,
        experiment=type("Exp", (), {"to_dict": lambda self: {"experiment_id": "e", "lineage_id": lineage.lineage_id, "family_id": family.family_id, "hypothesis_id": "h", "genome_id": genome.genome_id, "goldfish_workspace": "w", "pipeline_stages": [], "backend_mode": "goldfish_sidecar", "resource_profile": "local-first-hybrid", "inputs": {}}})(),
        lineage=lineage,
    )


def _fresh_alpaca_data(project_root):
    bars_dir = project_root / "data" / "alpaca" / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range(start=datetime.now(timezone.utc) - timedelta(minutes=3), periods=4, freq="1min", tz="UTC")
    pd.DataFrame({"open": [1, 2, 3, 4], "high": [2, 3, 4, 5], "low": [0, 1, 2, 3], "close": [1.5, 2.5, 3.5, 4.5]}, index=idx).to_parquet(bars_dir / "SPY.parquet")
    (project_root / "data" / "alpaca" / "metadata.json").write_text(
        json.dumps({"last_refresh": datetime.now(timezone.utc).isoformat(), "timeframe": "1Min"}),
        encoding="utf-8",
    )


def _seed_runner_state(project_root: Path, portfolio_id: str, *, ready: bool = True, reason: str = "", heartbeat_age_seconds: int = 30) -> None:
    portfolio_dir = project_root / "data" / "portfolios" / portfolio_id
    portfolio_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_ts = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)
    (portfolio_dir / "state.json").write_text(
        json.dumps(
            {
                "running": True,
                "ready": ready,
                "reason": reason,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    (portfolio_dir / "heartbeat.json").write_text(
        json.dumps({"ts": heartbeat_ts.isoformat().replace("+00:00", "Z"), "status": "running"}),
        encoding="utf-8",
    )


def _family_and_lineage() -> tuple[FactoryFamily, LineageRecord, StrategyGenome]:
    family = FactoryFamily(
        family_id="alpaca_family",
        label="Alpaca Family",
        thesis="Test",
        target_portfolios=["alpaca_paper"],
        target_venues=["alpaca"],
        primary_connector_ids=["alpaca_stocks"],
        champion_lineage_id="alpaca_family:champion",
        shadow_challenger_ids=[],
        paper_challenger_ids=[],
        budget_split={"incumbent": 1.0},
        queue_stage="paper",
        explainer="Test family",
    )
    lineage = LineageRecord(
        lineage_id="alpaca_family:champion",
        family_id=family.family_id,
        label="Champion",
        role="champion",
        current_stage="paper",
        target_portfolios=["alpaca_paper"],
        target_venues=["alpaca"],
        hypothesis_id="h",
        genome_id="g",
        experiment_id="e",
        budget_bucket="incumbent",
        budget_weight_pct=1.0,
        connector_ids=["alpaca_stocks"],
        goldfish_workspace="research/goldfish/alpaca_family",
    )
    genome = StrategyGenome(
        genome_id="g",
        lineage_id=lineage.lineage_id,
        family_id=family.family_id,
        parent_genome_id=None,
        role="champion",
        parameters={
            "paper_data_contract": {
                "requirements": [
                    {"source": "alpaca", "venue": "alpaca", "instruments": ["SPY"], "fields": ["close"], "feed_type": "bars", "raw_cadence_seconds": 60, "freshness_sla_seconds": 300, "required_bar_seconds": 60}
                ]
            }
        },
        mutation_bounds=MutationBounds(),
        scientific_domains=[],
        budget_bucket="incumbent",
        resource_profile="local-first-hybrid",
        budget_weight_pct=1.0,
    )
    return family, lineage, genome


def test_champion_paper_state_reports_active_when_runner_and_data_are_ready(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr("factory.orchestrator.is_stock_market_open", lambda: True)
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(orchestrator.project_root / "data" / "portfolios"), raising=False)
    family, lineage, genome = _family_and_lineage()
    _save_pack(orchestrator, family, lineage, genome)
    _fresh_alpaca_data(orchestrator.project_root)
    _seed_runner_state(orchestrator.project_root, "alpaca_paper")

    result = orchestrator._champion_paper_state(
        family,
        {"champion": {"lineage_id": lineage.lineage_id, "current_stage": "paper"}},
        [{"lineage_id": lineage.lineage_id, "current_stage": "paper", "activation_status": "running", "alias_runner_running": True}],
    )

    assert result["state"] == "paper_active"


def test_champion_paper_state_reports_blocked_when_data_is_stale(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(orchestrator.project_root / "data" / "portfolios"), raising=False)
    family, lineage, genome = _family_and_lineage()
    _save_pack(orchestrator, family, lineage, genome)
    _seed_runner_state(orchestrator.project_root, "alpaca_paper")

    result = orchestrator._champion_paper_state(
        family,
        {"champion": {"lineage_id": lineage.lineage_id, "current_stage": "paper"}},
        [{"lineage_id": lineage.lineage_id, "current_stage": "paper", "activation_status": "running"}],
    )

    assert result["state"] == "paper_blocked"
    assert "blocked" in result["reason"]


def test_champion_paper_state_reports_blocked_when_runner_is_missing(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr("factory.orchestrator.is_stock_market_open", lambda: True)
    family, lineage, genome = _family_and_lineage()
    _save_pack(orchestrator, family, lineage, genome)
    _fresh_alpaca_data(orchestrator.project_root)

    result = orchestrator._champion_paper_state(
        family,
        {"champion": {"lineage_id": lineage.lineage_id, "current_stage": "paper"}},
        [{"lineage_id": lineage.lineage_id, "current_stage": "paper", "activation_status": "running"}],
    )

    assert result["state"] == "paper_blocked"
    assert "no runner bound" in result["reason"]


def test_champion_paper_state_reports_blocked_when_runner_not_ready(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr("factory.orchestrator.is_stock_market_open", lambda: True)
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(orchestrator.project_root / "data" / "portfolios"), raising=False)
    family, lineage, genome = _family_and_lineage()
    _save_pack(orchestrator, family, lineage, genome)
    _fresh_alpaca_data(orchestrator.project_root)
    _seed_runner_state(orchestrator.project_root, "alpaca_paper", ready=False, reason="model_not_loaded")

    result = orchestrator._champion_paper_state(
        family,
        {"champion": {"lineage_id": lineage.lineage_id, "current_stage": "paper"}},
        [{"lineage_id": lineage.lineage_id, "current_stage": "paper", "activation_status": "running"}],
    )

    assert result["state"] == "paper_blocked"
    assert "model_not_loaded" in result["reason"]


def test_champion_paper_state_reports_stuck_on_stall_issue_codes(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", str(orchestrator.project_root / "data" / "portfolios"), raising=False)
    family, lineage, genome = _family_and_lineage()
    _save_pack(orchestrator, family, lineage, genome)
    _fresh_alpaca_data(orchestrator.project_root)
    _seed_runner_state(orchestrator.project_root, "alpaca_paper")

    result = orchestrator._champion_paper_state(
        family,
        {"champion": {"lineage_id": lineage.lineage_id, "current_stage": "paper"}},
        [{"lineage_id": lineage.lineage_id, "current_stage": "paper", "activation_status": "running", "execution_issue_codes": ["trade_stalled"]}],
    )

    assert result["state"] == "paper_stuck"
