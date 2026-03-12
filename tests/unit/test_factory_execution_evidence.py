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
