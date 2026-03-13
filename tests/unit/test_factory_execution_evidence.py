from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import config
from factory.execution_evidence import build_portfolio_execution_evidence, summarize_execution_targets


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_build_portfolio_execution_evidence_surfaces_runtime_and_quality_issues(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    base = tmp_path / "portfolios" / "cascade_alpha"
    _write_json(
        base / "account.json",
        {
            "portfolio_id": "cascade_alpha",
            "currency": "USD",
            "current_balance": 9950.0,
            "realized_pnl": -50.0,
            "roi_pct": -0.5,
            "drawdown_pct": 3.2,
            "wins": 2,
            "losses": 8,
            "trade_count": 10,
            "last_updated": now,
        },
    )
    _write_json(
        base / "heartbeat.json",
        {
            "ts": now,
            "status": "running",
        },
    )
    _write_json(
        base / "state.json",
        {
            "portfolio_id": "cascade_alpha",
            "running": True,
            "status": "running",
            "scan_count": 320,
            "open_positions": [],
            "execution_quality": {
                "avg_modeled_slippage_bps": 9.5,
                "rejection_rate": 0.0,
                "rejection_count": 0,
                "zero_simulated_fills": False,
            },
            "paper_rejections": {
                "count": 40,
                "rate": 0.92,
                "reasons": {"signal_score_below_policy": 35},
            },
            "readiness": {
                "status": "paper_validating",
                "blockers": ["closed_trades_minimum"],
            },
        },
    )
    _write_jsonl(
        base / "trades.jsonl",
        [
            {"trade_id": "t1", "status": "CLOSED", "net_pnl_usd": -1.2, "slippage_bps": 10.0},
            {"trade_id": "t2", "status": "CLOSED", "net_pnl_usd": -0.7, "slippage_bps": 9.0},
            {"trade_id": "t3", "status": "CLOSED", "net_pnl_usd": -0.4, "slippage_bps": 8.5},
            {"trade_id": "t4", "status": "CLOSED", "net_pnl_usd": -0.6, "slippage_bps": 9.2},
            {"trade_id": "t5", "status": "CLOSED", "net_pnl_usd": -0.3, "slippage_bps": 8.8},
        ],
    )
    _write_jsonl(
        base / "events.jsonl",
        [
            {"kind": "signal_rejected", "data": {"reason": "signal_score_below_policy"}},
            {"kind": "signal_rejected", "data": {"reason": "signal_score_below_policy"}},
        ],
    )

    evidence = build_portfolio_execution_evidence("cascade_alpha", root=str(tmp_path / "portfolios"))

    assert evidence["health_status"] == "warning"
    assert "negative_paper_roi" in evidence["issue_codes"]
    assert "poor_win_rate" in evidence["issue_codes"]
    assert "slippage_pressure" in evidence["issue_codes"]
    assert "excessive_rejections" in evidence["issue_codes"]
    assert evidence["recent_trade_stats"]["recent_loss_streak"] == 5
    assert evidence["event_summary"]["top_reason"] == "signal_score_below_policy"
    assert evidence["recommendation_context"]


def test_summarize_execution_targets_aggregates_target_health(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    for name, roi in [("contrarian_legacy", -1.4), ("polymarket_quantum_fold", 0.0)]:
        base = tmp_path / "portfolios" / name
        _write_json(
            base / "account.json",
            {
                "portfolio_id": name,
                "currency": "USD",
                "current_balance": 1000.0,
                "realized_pnl": -14.0 if roi < 0 else 0.0,
                "roi_pct": roi,
                "drawdown_pct": 0.0,
                "wins": 0,
                "losses": 6 if roi < 0 else 0,
                "trade_count": 6 if roi < 0 else 0,
                "last_updated": now,
            },
        )
        _write_json(base / "heartbeat.json", {"ts": now, "status": "running"})
        _write_json(base / "state.json", {"portfolio_id": name, "running": True, "status": "running", "scan_count": 80})
        _write_jsonl(
            base / "trades.jsonl",
            [{"trade_id": "t1", "status": "CLOSED", "net_pnl_usd": -1.0}] if roi < 0 else [],
        )
        _write_jsonl(base / "events.jsonl", [])

    summary = summarize_execution_targets(
        ["contrarian_legacy", "polymarket_quantum_fold"],
        root=str(tmp_path / "portfolios"),
    )

    assert summary["running_target_count"] == 2
    assert summary["recent_trade_count"] == 1
    assert summary["has_execution_signal"] is True
    assert summary["health_status"] == "warning"
    assert "negative_paper_roi" in summary["issue_codes"]


def test_build_portfolio_execution_evidence_surfaces_untrainable_models(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    base = tmp_path / "portfolios" / "hedge_research"
    _write_json(
        base / "account.json",
        {
            "portfolio_id": "hedge_research",
            "currency": "USD",
            "current_balance": 10000.0,
            "realized_pnl": 0.0,
            "roi_pct": 0.0,
            "drawdown_pct": 0.0,
            "wins": 0,
            "losses": 0,
            "trade_count": 0,
            "last_updated": now,
        },
    )
    _write_json(base / "heartbeat.json", {"ts": now, "status": "running"})
    _write_json(
        base / "state.json",
        {
            "portfolio_id": "hedge_research",
            "running": True,
            "status": "running",
            "scan_count": 120,
            "training_progress": {
                "tracked_models": 2,
                "trainable_models": 1,
                "trained_models": 1,
                "targets": {"trainable_models": 2, "trained_models": 2},
            },
            "trainability": {
                "status": "blocked",
                "training_required": True,
                "required_model_count": 2,
                "trainable_model_count": 1,
                "trained_model_count": 1,
                "blocked_models": [{"model_id": "contrarian_online_learner", "reason": "learner_missing"}],
            },
        },
    )
    _write_jsonl(base / "trades.jsonl", [])
    _write_jsonl(base / "events.jsonl", [])

    evidence = build_portfolio_execution_evidence("hedge_research", root=str(tmp_path / "portfolios"))

    assert "untrainable_model" in evidence["issue_codes"]
    assert evidence["training_state"]["trainability_status"] == "blocked"


def test_build_portfolio_execution_evidence_surfaces_stalled_models(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTORY_STALLED_MODEL_HOURS", 8)
    monkeypatch.setattr(config, "FACTORY_STALLED_MODEL_MIN_SCANS", 25)
    now = datetime.now(timezone.utc)
    started_ts = now - timedelta(hours=9)
    old_train_ts = now - timedelta(hours=10)
    started = started_ts.isoformat()
    base = tmp_path / "portfolios" / "research_factory"
    _write_json(
        base / "account.json",
        {
            "portfolio_id": "research_factory",
            "currency": "USD",
            "current_balance": 10000.0,
            "realized_pnl": 0.0,
            "roi_pct": 0.0,
            "drawdown_pct": 0.0,
            "wins": 0,
            "losses": 0,
            "trade_count": 0,
            "last_updated": now.isoformat(),
        },
    )
    _write_json(base / "heartbeat.json", {"ts": now.isoformat(), "status": "running"})
    _write_json(
        base / "state.json",
        {
            "portfolio_id": "research_factory",
            "running": True,
            "status": "running",
            "scan_count": 240,
            "build_info": {"started_at": started},
            "open_positions": [],
            "training_progress": {
                "tracked_models": 2,
                "trainable_models": 2,
                "trained_models": 1,
                "targets": {"trainable_models": 2, "trained_models": 2},
            },
            "trainability": {
                "status": "warming_up",
                "training_required": True,
                "required_model_count": 2,
                "trainable_model_count": 2,
                "trained_model_count": 1,
                "strict_pass_model_count": 0,
                "models": [
                    {"model_id": "model_a", "last_retrain_time": started_ts.isoformat()},
                    {"model_id": "model_b", "last_retrain_time": old_train_ts.isoformat()},
                ],
                "blocked_models": [],
            },
        },
    )
    _write_jsonl(base / "trades.jsonl", [])
    _write_jsonl(base / "events.jsonl", [])

    evidence = build_portfolio_execution_evidence("research_factory", root=str(tmp_path / "portfolios"))

    assert "trade_stalled" in evidence["issue_codes"]
    assert "training_stalled" in evidence["issue_codes"]
    assert "stalled_model" in evidence["issue_codes"]


def test_build_portfolio_execution_evidence_reads_runtime_alias_store_distinctly(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    canonical = tmp_path / "portfolios" / "contrarian_legacy"
    alias = tmp_path / "portfolios" / "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-9"

    _write_json(
        canonical / "account.json",
        {
            "portfolio_id": "contrarian_legacy",
            "currency": "USD",
            "current_balance": 1010.0,
            "realized_pnl": 10.0,
            "roi_pct": 1.0,
            "drawdown_pct": 0.5,
            "wins": 2,
            "losses": 1,
            "trade_count": 3,
            "last_updated": now,
        },
    )
    _write_json(canonical / "heartbeat.json", {"ts": now, "status": "running"})
    _write_json(canonical / "state.json", {"portfolio_id": "contrarian_legacy", "running": True, "status": "running", "scan_count": 50})
    _write_jsonl(canonical / "trades.jsonl", [{"trade_id": "c1", "status": "CLOSED", "net_pnl_usd": 10.0}])
    _write_jsonl(canonical / "events.jsonl", [])

    _write_json(
        alias / "account.json",
        {
            "portfolio_id": "contrarian_legacy",
            "currency": "USD",
            "current_balance": 1250.0,
            "realized_pnl": 250.0,
            "roi_pct": 25.0,
            "drawdown_pct": 1.2,
            "wins": 12,
            "losses": 2,
            "trade_count": 14,
            "last_updated": now,
        },
    )
    _write_json(alias / "heartbeat.json", {"ts": now, "status": "running"})
    _write_json(alias / "state.json", {"portfolio_id": "contrarian_legacy", "running": True, "status": "running", "scan_count": 75})
    _write_jsonl(alias / "trades.jsonl", [{"trade_id": "a1", "status": "CLOSED", "net_pnl_usd": 25.0}])
    _write_jsonl(alias / "events.jsonl", [])

    evidence = build_portfolio_execution_evidence(
        "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-9",
        root=str(tmp_path / "portfolios"),
    )

    assert evidence["is_runtime_alias"] is True
    assert evidence["store_target"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-9"
    assert evidence["canonical_target"] == "contrarian_legacy"
    assert evidence["runtime_target"] == "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-9"
    assert evidence["account"]["realized_pnl"] == 250.0
    assert evidence["account"]["trade_count"] == 14


def test_build_portfolio_execution_evidence_prefers_runtime_health_contract(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    base = tmp_path / "portfolios" / "hedge_validation"
    _write_json(
        base / "runtime_health.json",
        {
            "schema_version": 1,
            "portfolio_id": "hedge_validation",
            "canonical_portfolio_id": "hedge_validation",
            "runtime_portfolio_id": "hedge_validation",
            "status": "running",
            "running": True,
            "heartbeat": {"ts": now, "status": "running"},
            "process": {"pid": 1234, "running": True, "status": "running", "started_at": now},
            "publication": {"status": "publishing", "first_publish_at": now, "last_publish_at": now},
            "health": {"status": "warning", "issue_codes": [], "error": None, "blockers": ["closed_hedges_minimum"]},
            "readiness": {"status": "paper_validating", "blockers": ["closed_hedges_minimum"]},
            "account": {
                "portfolio_id": "hedge_validation",
                "currency": "USD",
                "starting_balance": 50000.0,
                "current_balance": 50025.0,
                "realized_pnl": 25.0,
                "roi_pct": 0.05,
                "drawdown_pct": 0.0,
                "wins": 2,
                "losses": 1,
                "trade_count": 3,
                "last_updated": now,
            },
            "recent_trade_count": 1,
            "recent_event_count": 1,
            "recent_trade_stats": {"closed_count": 1, "winning_count": 1, "losing_count": 0, "win_rate_pct": 100.0, "recent_loss_streak": 0},
            "event_summary": {"kind_counts": {"hedge_opened": 1}, "reason_counts": {}, "top_reason": ""},
            "raw_state": {
                "portfolio_id": "hedge_validation",
                "running": True,
                "status": "running",
                "mode": "paper",
                "scan_count": 50,
                "execution_quality": {"avg_modeled_slippage_bps": 1.2},
                "training_progress": {"tracked_examples": 12},
                "trainability": {"status": "warming_up", "training_required": False},
            },
        },
    )

    evidence = build_portfolio_execution_evidence("hedge_validation", root=str(tmp_path / "portfolios"))

    assert evidence["contract_source"] == "runtime_health"
    assert evidence["running"] is True
    assert evidence["account"]["realized_pnl"] == 25.0
    assert evidence["recent_trade_stats"]["closed_count"] == 1
    assert evidence["health_status"] == "warning"
    assert "readiness_blocked" in evidence["issue_codes"]
