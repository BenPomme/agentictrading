from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config

from factory.execution_targets import parse_runtime_portfolio_alias, resolve_target_portfolio
from factory.state_store import AccountSnapshot, PortfolioStateStore

HEARTBEAT_STALE_WARNING_SECONDS = 120.0
HEARTBEAT_STALE_CRITICAL_SECONDS = 300.0
LOSS_STREAK_WARNING = 3
SLIPPAGE_WARNING_BPS = 8.0
SLIPPAGE_CRITICAL_BPS = 15.0
REJECTION_WARNING_RATE = 0.8
REJECTION_WARNING_COUNT = 25
NO_TRADE_SCAN_MINIMUM = 25


_US_EASTERN = ZoneInfo("America/New_York")


def is_stock_market_open_now() -> bool:
    """True when US equities can trade (Mon-Fri 09:30-16:00 ET)."""
    now_et = datetime.now(_US_EASTERN)
    if now_et.weekday() >= 5:
        return False
    from datetime import time as dtime
    return dtime(9, 30) <= now_et.time() <= dtime(16, 0)


def _validation_profile() -> str:
    raw = (getattr(config, "FACTORY_VALIDATION_PROFILE", "paper") or "paper").strip().lower()
    return raw if raw in ("dev", "paper", "prod") else "paper"


def _validation_thresholds() -> Dict[str, Any]:
    """Thresholds for execution evidence; relaxed in dev/paper, strict in prod."""
    profile = _validation_profile()
    if profile == "dev":
        return {
            "heartbeat_stale_warning_sec": 300.0,
            "heartbeat_stale_critical_sec": 600.0,
            "rejection_warning_rate": 0.95,
            "rejection_warning_count": 50,
            "no_trade_scan_minimum": 50,
        }
    if profile == "paper":
        return {
            "heartbeat_stale_warning_sec": 180.0,
            "heartbeat_stale_critical_sec": 420.0,
            "rejection_warning_rate": 0.9,
            "rejection_warning_count": 35,
            "no_trade_scan_minimum": 35,
        }
    return {
        "heartbeat_stale_warning_sec": HEARTBEAT_STALE_WARNING_SECONDS,
        "heartbeat_stale_critical_sec": HEARTBEAT_STALE_CRITICAL_SECONDS,
        "rejection_warning_rate": REJECTION_WARNING_RATE,
        "rejection_warning_count": REJECTION_WARNING_COUNT,
        "no_trade_scan_minimum": NO_TRADE_SCAN_MINIMUM,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _heartbeat_age_seconds(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return round(max(0.0, (datetime.now(timezone.utc) - ts).total_seconds()), 1)


def _parse_iso_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _age_hours(value: Any) -> float | None:
    ts = _parse_iso_ts(value)
    if ts is None:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - ts).total_seconds()) / 3600.0, 3)


def _portfolio_state_root(root: str | None = None) -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        return Path(explicit)
    execution_root = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if execution_root:
        return Path(execution_root) / "data" / "portfolios"
    return Path("data/portfolios")


def _portfolio_alias_candidates(base_root: Path, canonical_portfolio_id: str) -> List[str]:
    if not base_root.exists():
        return []
    alias_prefix = f"factory_lane__{canonical_portfolio_id}__"
    aliases: List[str] = []
    for path in base_root.iterdir():
        if not path.is_dir():
            continue
        if path.name.startswith(alias_prefix):
            aliases.append(path.name)
    return sorted(aliases)


def _choose_evidence_store(requested_portfolio_id: str, root: str | None = None) -> Tuple[str, str, str]:
    base_root = Path(root) if root else _portfolio_state_root(root=root)
    parsed_alias = parse_runtime_portfolio_alias(str(requested_portfolio_id))
    resolved_target = resolve_target_portfolio(str(requested_portfolio_id))

    if parsed_alias:
        runtime_target = str(requested_portfolio_id)
        store_target = runtime_target
        selected_source = "requested_alias"
    else:
        candidates: List[str] = [resolved_target]
        candidates.extend(_portfolio_alias_candidates(base_root, resolved_target))

        def _score(candidate: str) -> tuple[int, float, int]:
            candidate_store = PortfolioStateStore(candidate, root=str(base_root))
            if not candidate_store.base_dir.exists():
                return (0, float("inf"), 0)
            runtime_health = candidate_store.read_runtime_health()
            heartbeat_ts = (
                dict(runtime_health.get("heartbeat") or {}).get("ts")
                or runtime_health.get("last_publish_at")
                or dict(runtime_health.get("publication") or {}).get("last_publish_at")
                or _parse_iso_ts((runtime_health.get("heartbeat") or {}).get("ts"))
            )
            if isinstance(heartbeat_ts, datetime):
                heartbeat_ts = heartbeat_ts.isoformat()
            heartbeat_age = _heartbeat_age_seconds(heartbeat_ts) if heartbeat_ts is not None else None
            age_score = heartbeat_age if heartbeat_age is not None else float("inf")
            running = bool(runtime_health.get("running") or dict(runtime_health.get("process") or {}).get("running"))
            account = dict(runtime_health.get("account") or {})
            trade_count = int(account.get("trade_count", 0) or 0)
            return (1 if running else 0, -age_score if age_score != float("inf") else -1e12, trade_count)

        ranked = sorted(((candidate, _score(candidate)) for candidate in candidates), key=lambda item: item[1], reverse=True)
        runtime_target = str(ranked[0][0])
        store_target = str(ranked[0][0])
        selected_source = "canonical_with_alias_preference"

    return runtime_target, store_target, selected_source


def _readiness_state(state: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(state.get("readiness_v2"), dict):
        return dict(state.get("readiness_v2") or {})
    if isinstance(state.get("readiness"), dict):
        return dict(state.get("readiness") or {})
    return {}


def _blockers_from_state(state: Dict[str, Any]) -> List[str]:
    readiness = _readiness_state(state)
    blockers = [str(item) for item in (readiness.get("blockers") or []) if str(item).strip()]
    health = dict(state.get("health") or {})
    blockers.extend(str(item) for item in (health.get("feed_degradation_reasons") or []) if str(item).strip())
    return list(dict.fromkeys(blockers))


def _error_from_state(state: Dict[str, Any]) -> str:
    for candidate in [
        state.get("error"),
        dict(state.get("health") or {}).get("primary_failure_reason"),
        dict(dict(state.get("source_health") or {}).get("clob") or {}).get("last_error"),
    ]:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _open_position_count(state: Dict[str, Any]) -> int:
    positions = state.get("open_positions")
    if isinstance(positions, list):
        return len(positions)
    positions = state.get("positions")
    if isinstance(positions, list):
        return sum(1 for item in positions if str((item or {}).get("status") or "").upper() == "OPEN")
    return 0


def _trade_pnl(trade: Dict[str, Any]) -> float:
    for key in ["net_pnl_usd", "net_pnl", "realized_pnl", "pnl", "realized_pnl_usd"]:
        if key in trade:
            return _safe_float(trade.get(key))
    return 0.0


def _trade_is_closed(trade: Dict[str, Any]) -> bool:
    return str(trade.get("status") or "").upper() in {"CLOSED", "STOPPED", "SETTLED"}


def _trade_last_activity_ts(trades: Iterable[Dict[str, Any]]) -> datetime | None:
    latest: datetime | None = None
    for trade in trades:
        for key in ["closed_at", "exit_time", "settled_at", "updated_at", "timestamp", "ts", "last_updated"]:
            ts = _parse_iso_ts(trade.get(key))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
    return latest


def _recent_trade_stats(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [trade for trade in trades if _trade_is_closed(trade)]
    closed_pnls = [_trade_pnl(trade) for trade in closed]
    losses = [pnl for pnl in closed_pnls if pnl < 0.0]
    wins = [pnl for pnl in closed_pnls if pnl > 0.0]
    recent_loss_streak = 0
    for trade in reversed(closed):
        if _trade_pnl(trade) < 0.0:
            recent_loss_streak += 1
            continue
        break
    slippages = [
        _safe_float(trade.get("slippage_bps"))
        for trade in trades
        if trade.get("slippage_bps") is not None
    ]
    return {
        "closed_count": len(closed),
        "winning_count": len(wins),
        "losing_count": len(losses),
        "win_rate_pct": round((len(wins) / len(closed)) * 100.0, 2) if closed else 0.0,
        "avg_closed_net_pnl": round(sum(closed_pnls) / len(closed_pnls), 6) if closed_pnls else 0.0,
        "recent_closed_net_pnl": round(sum(closed_pnls[-5:]), 6) if closed_pnls else 0.0,
        "recent_loss_streak": recent_loss_streak,
        "avg_slippage_bps": round(sum(slippages) / len(slippages), 4) if slippages else 0.0,
        "max_slippage_bps": round(max(slippages), 4) if slippages else 0.0,
        "recent_close_reasons": list(
            dict.fromkeys(str(trade.get("close_reason") or "").strip() for trade in closed[-5:] if str(trade.get("close_reason") or "").strip())
        ),
    }


def _event_summary(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    kind_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for event in events:
        kind = str(event.get("kind") or event.get("type") or "").strip() or "unknown"
        kind_counts[kind] += 1
        data = dict(event.get("data") or {})
        reason = str(event.get("reason") or data.get("reason") or "").strip()
        if reason:
            reason_counts[reason] += 1
    return {
        "kind_counts": dict(kind_counts),
        "reason_counts": dict(reason_counts),
        "top_reason": reason_counts.most_common(1)[0][0] if reason_counts else "",
    }


def _quality_metrics(state: Dict[str, Any], trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    execution_quality = dict(state.get("execution_quality") or {})
    paper_rejections = dict(state.get("paper_rejections") or {})
    rejections = dict(state.get("rejections") or {})
    trade_stats = _recent_trade_stats(trades)
    return {
        "avg_modeled_slippage_bps": _safe_float(execution_quality.get("avg_modeled_slippage_bps")),
        "avg_realized_slippage_bps": _safe_float(execution_quality.get("avg_realized_slippage_bps")),
        "recent_avg_slippage_bps": _safe_float(trade_stats.get("avg_slippage_bps")),
        "rejection_rate": _safe_float(
            paper_rejections.get("rate", execution_quality.get("rejection_rate"))
        ),
        "rejection_count": _safe_int(
            paper_rejections.get("count", execution_quality.get("rejection_count"))
        ),
        "rejection_reasons": {
            str(key): _safe_int(value)
            for key, value in dict(paper_rejections.get("reasons") or rejections).items()
            if str(key).strip()
        },
        "stale_quote_halts": _safe_int(execution_quality.get("stale_quote_halts", dict(state.get("risk") or {}).get("stale_quote_halts"))),
        "zero_simulated_fills": bool(execution_quality.get("zero_simulated_fills")),
        "drawdown_halt_active": bool(
            execution_quality.get("drawdown_halt_active", dict(state.get("risk") or {}).get("drawdown_halt_active"))
        ),
    }


def _training_state(state: Dict[str, Any]) -> Dict[str, Any]:
    training_progress = dict(state.get("training_progress") or {})
    trainability = dict(state.get("trainability") or {})
    research_summary = dict(state.get("research_summary") or {})
    model_league = dict(state.get("model_league") or {})
    prediction_summary = dict(state.get("prediction_summary") or {})
    readiness = _readiness_state(state)
    ranked_models = list(model_league.get("ranked_models") or [])
    leader = ranked_models[0] if ranked_models else {}
    last_training_activity: datetime | None = None
    for row in list(trainability.get("models") or []):
        ts = _parse_iso_ts(dict(row).get("last_retrain_time"))
        if ts is not None and (last_training_activity is None or ts > last_training_activity):
            last_training_activity = ts
    for learner_key in ["online_learner", "contrarian_learner"]:
        learner = dict(state.get(learner_key) or {})
        ts = _parse_iso_ts(learner.get("last_retrain_time"))
        if ts is not None and (last_training_activity is None or ts > last_training_activity):
            last_training_activity = ts
    return {
        "tracked_examples": _safe_int(training_progress.get("tracked_examples")),
        "labeled_examples": _safe_int(training_progress.get("labeled_examples", research_summary.get("labeled_examples"))),
        "pending_labels": _safe_int(training_progress.get("pending_labels", research_summary.get("pending_labels"))),
        "closed_trades_target": _safe_int(dict(training_progress.get("targets") or {}).get("closed_trades")),
        "leader_model_id": str(model_league.get("leader_model_id") or prediction_summary.get("leader_model_id") or ""),
        "leader_shadow_realized_pnl": _safe_float(leader.get("shadow_realized_pnl")),
        "leader_strict_gate_pass": bool(leader.get("strict_gate_pass", prediction_summary.get("strict_gate_pass"))),
        "avg_realized_edge": _safe_float(research_summary.get("avg_realized_edge")),
        "rolling_roi_pct": _safe_float(readiness.get("avg_rolling_200_roi_pct")),
        "rolling_brier_lift": _safe_float(readiness.get("avg_rolling_200_brier_lift")),
        "trainability_status": str(trainability.get("status") or ""),
        "training_required": bool(trainability.get("training_required", False)),
        "required_model_count": _safe_int(trainability.get("required_model_count")),
        "trainable_model_count": _safe_int(trainability.get("trainable_model_count")),
        "trained_model_count": _safe_int(trainability.get("trained_model_count")),
        "strict_pass_model_count": _safe_int(trainability.get("strict_pass_model_count")),
        "blocked_models": list(trainability.get("blocked_models") or []),
        "last_training_activity_at": last_training_activity.isoformat() if last_training_activity is not None else None,
    }


def _issue(severity: str, code: str, detail: str) -> Dict[str, str]:
    return {"severity": severity, "code": code, "detail": detail}


def build_portfolio_execution_evidence(
    portfolio_id: str,
    *,
    requested_target: str | None = None,
    root: str | None = None,
    trade_limit: int = 20,
    event_limit: int = 20,
    market_schedule: Optional[str] = None,
) -> Dict[str, Any]:
    requested_target_id = str(requested_target or portfolio_id)
    runtime_alias = parse_runtime_portfolio_alias(str(portfolio_id))
    resolved_target = resolve_target_portfolio(str(portfolio_id))
    runtime_target_id, store_portfolio_id, evidence_source = _choose_evidence_store(str(portfolio_id), root=root)
    canonical_target_id = str(runtime_alias["canonical_portfolio_id"]) if runtime_alias else resolved_target
    store = PortfolioStateStore(store_portfolio_id, root=root)
    runtime_health = store.read_runtime_health()
    evidence_store_exists = bool(store.base_dir.exists())
    heartbeat = dict(runtime_health.get("heartbeat") or store.read_heartbeat())
    state = dict(runtime_health.get("raw_state") or store.read_state())
    account = AccountSnapshot.from_dict(dict(runtime_health.get("account") or {})) or store.read_account()
    trades = store.read_trades(limit=trade_limit)
    events = store.read_events(limit=event_limit)
    trade_stats = _recent_trade_stats(trades)
    event_summary = _event_summary(events)
    quality = _quality_metrics(state, trades)
    training = _training_state(state)
    if runtime_health:
        trade_stats = dict(runtime_health.get("recent_trade_stats") or trade_stats)
        event_summary = dict(runtime_health.get("event_summary") or event_summary)
        runtime_training = dict(runtime_health.get("training_state") or {})
        if runtime_training:
            state = dict(state)
            state["training_progress"] = dict(runtime_training.get("training_progress") or state.get("training_progress") or {})
            state["trainability"] = dict(runtime_training.get("trainability") or state.get("trainability") or {})
            state["research_summary"] = dict(runtime_training.get("research_summary") or state.get("research_summary") or {})
            state["model_league"] = dict(runtime_training.get("model_league") or state.get("model_league") or {})
            state["prediction_summary"] = dict(runtime_training.get("prediction_summary") or state.get("prediction_summary") or {})
            state["online_learner"] = dict(runtime_training.get("online_learner") or state.get("online_learner") or {})
            state["contrarian_learner"] = dict(runtime_training.get("contrarian_learner") or state.get("contrarian_learner") or {})
            training = _training_state(state)
    blockers = _blockers_from_state(state)
    error = _error_from_state(state)
    contract_health = dict(runtime_health.get("health") or {})
    if not blockers:
        blockers = [str(item) for item in (contract_health.get("blockers") or []) if str(item).strip()]
    if not error:
        error = str(contract_health.get("error") or "").strip()
    heartbeat_age = None
    heartbeat_ts = (
        heartbeat.get("ts")
        or runtime_health.get("last_publish_at")
        or dict(runtime_health.get("publication") or {}).get("last_publish_at")
        or (account.last_updated if isinstance(account, AccountSnapshot) else None)
    )
    if heartbeat_ts:
        heartbeat_age = _heartbeat_age_seconds(heartbeat_ts)
    process = dict(runtime_health.get("process") or {})
    publication = dict(runtime_health.get("publication") or {})
    status = str(
        runtime_health.get("status")
        or state.get("status")
        or heartbeat.get("status")
        or ("running" if state.get("running") else "idle")
        or "idle"
    )
    running = bool(runtime_health.get("running")) or bool(process.get("running")) or bool(state.get("running")) or str(heartbeat.get("status") or "").lower() == "running"
    runtime_started_at = (
        _parse_iso_ts(process.get("started_at"))
        or _parse_iso_ts(runtime_health.get("started_at"))
        or
        _parse_iso_ts(state.get("fresh_book_started_at"))
        or _parse_iso_ts(dict(state.get("build_info") or {}).get("started_at"))
        or _parse_iso_ts(state.get("started_at"))
    )
    runtime_age_hours = (
        round(max(0.0, (datetime.now(timezone.utc) - runtime_started_at).total_seconds()) / 3600.0, 3)
        if runtime_started_at is not None
        else None
    )
    last_trade_activity = _trade_last_activity_ts(trades) or _parse_iso_ts(trade_stats.get("last_trade_activity_at"))
    last_trade_activity_at = last_trade_activity.isoformat() if last_trade_activity is not None else None
    last_trade_activity_age_hours = (
        round(max(0.0, (datetime.now(timezone.utc) - last_trade_activity).total_seconds()) / 3600.0, 3)
        if last_trade_activity is not None
        else None
    )
    last_training_activity_age_hours = _age_hours(training.get("last_training_activity_at"))

    market_closed_idle = (
        market_schedule == "stock_market" and not is_stock_market_open_now()
    )

    issues: List[Dict[str, str]] = []
    if error:
        issues.append(_issue("critical", "runtime_error", error))
    if blockers:
        issues.append(_issue("warning", "readiness_blocked", ", ".join(blockers[:4])))
    th = _validation_thresholds()
    if not market_closed_idle:
        if heartbeat_age is not None and heartbeat_age >= th["heartbeat_stale_critical_sec"]:
            issues.append(_issue("critical", "heartbeat_stale", f"heartbeat age {heartbeat_age:.1f}s"))
        elif heartbeat_age is not None and heartbeat_age >= th["heartbeat_stale_warning_sec"]:
            issues.append(_issue("warning", "heartbeat_slow", f"heartbeat age {heartbeat_age:.1f}s"))
    if quality["drawdown_halt_active"]:
        issues.append(_issue("critical", "drawdown_halt_active", "risk controls halted execution"))
    if not market_closed_idle and quality["stale_quote_halts"] > 0:
        issues.append(_issue("warning", "stale_quote_halts", f"{quality['stale_quote_halts']} stale quote halts"))
    if quality["rejection_count"] >= th["rejection_warning_count"] and quality["rejection_rate"] >= th["rejection_warning_rate"]:
        issues.append(_issue("warning", "excessive_rejections", f"{quality['rejection_count']} rejections at rate {quality['rejection_rate']:.2f}"))
    avg_slippage = max(
        quality["avg_modeled_slippage_bps"],
        quality["avg_realized_slippage_bps"],
        quality["recent_avg_slippage_bps"],
    )
    if avg_slippage >= SLIPPAGE_CRITICAL_BPS:
        issues.append(_issue("critical", "severe_slippage", f"average slippage {avg_slippage:.2f} bps"))
    elif avg_slippage >= SLIPPAGE_WARNING_BPS:
        issues.append(_issue("warning", "slippage_pressure", f"average slippage {avg_slippage:.2f} bps"))
    account_trade_count = _safe_int((account.trade_count if isinstance(account, AccountSnapshot) else state.get("trade_count")) or state.get("trade_count"))
    roi_pct = _safe_float(account.roi_pct if isinstance(account, AccountSnapshot) else state.get("realized_roi_pct"))
    drawdown_pct = _safe_float(account.drawdown_pct if isinstance(account, AccountSnapshot) else dict(state.get("risk") or {}).get("drawdown_pct"))
    realized_pnl = _safe_float(account.realized_pnl if isinstance(account, AccountSnapshot) else state.get("realized_pnl_usd"))
    if account_trade_count >= 5 and realized_pnl < 0.0:
        issues.append(_issue("warning", "negative_realized_pnl", f"realized pnl {realized_pnl:.4f}"))
    if account_trade_count >= 5 and roi_pct < 0.0:
        issues.append(_issue("warning", "negative_paper_roi", f"roi {roi_pct:.4f}%"))
    if trade_stats["closed_count"] >= 5 and trade_stats["win_rate_pct"] <= 35.0:
        issues.append(_issue("warning", "poor_win_rate", f"win rate {trade_stats['win_rate_pct']:.2f}%"))
    if trade_stats["recent_loss_streak"] >= LOSS_STREAK_WARNING:
        issues.append(_issue("warning", "recent_loss_streak", f"{trade_stats['recent_loss_streak']} consecutive losing closes"))
    no_trade_context = (
        running
        and account_trade_count == 0
        and _safe_int(state.get("scan_count") or state.get("signal_count") or state.get("opportunity_count")) >= th["no_trade_scan_minimum"]
    )
    if no_trade_context and not market_closed_idle:
        issues.append(_issue("warning", "no_trade_syndrome", "runner is scanning but not converting into paper trades"))
    if quality["zero_simulated_fills"] and not market_closed_idle:
        issues.append(_issue("warning", "zero_simulated_fills", "execution quality reports zero simulated fills"))
    if training["leader_model_id"] and not training["leader_strict_gate_pass"]:
        issues.append(_issue("warning", "leader_not_strict_pass", f"leader {training['leader_model_id']} is not strict-pass"))
    if training["training_required"] and training["trainable_model_count"] < training["required_model_count"]:
        issues.append(
            _issue(
                "warning",
                "untrainable_model",
                f"{training['trainable_model_count']}/{training['required_model_count']} required models are trainable",
            )
        )
    elif training["training_required"] and training["trained_model_count"] < training["required_model_count"]:
        issues.append(
            _issue(
                "warning",
                "training_warmup",
                f"{training['trained_model_count']}/{training['required_model_count']} required models have completed a train/retrain cycle",
            )
        )
    stalled_hours = max(1, int(getattr(config, "FACTORY_STALLED_MODEL_HOURS", 8) or 8))
    stalled_scan_floor = max(1, int(getattr(config, "FACTORY_STALLED_MODEL_MIN_SCANS", th["no_trade_scan_minimum"]) or th["no_trade_scan_minimum"]))
    scan_count = _safe_int(state.get("scan_count") or state.get("signal_count") or state.get("opportunity_count"))
    trade_stalled = False
    training_stalled = False
    if running and (runtime_age_hours is not None and runtime_age_hours >= stalled_hours):
        if _open_position_count(state) == 0:
            if account_trade_count == 0 and scan_count >= stalled_scan_floor:
                trade_stalled = True
            elif last_trade_activity_age_hours is not None and last_trade_activity_age_hours >= stalled_hours:
                trade_stalled = True
        if training["training_required"] and training["trained_model_count"] < training["required_model_count"]:
            if last_training_activity_age_hours is None:
                training_stalled = True
            elif last_training_activity_age_hours >= stalled_hours:
                training_stalled = True
    if trade_stalled:
        issues.append(
            _issue(
                "warning",
                "trade_stalled",
                f"no trading progress for {runtime_age_hours:.1f}h while the model is running",
            )
        )
    if training_stalled:
        issues.append(
            _issue(
                "warning",
                "training_stalled",
                f"required learners have not made training progress for at least {stalled_hours}h",
            )
        )
    if trade_stalled or training_stalled:
        stalled_parts = []
        if trade_stalled:
            stalled_parts.append("trading")
        if training_stalled:
            stalled_parts.append("training")
        issues.append(
            _issue(
                "warning",
                "stalled_model",
                f"model has been stalled on {' and '.join(stalled_parts)} for at least {stalled_hours}h",
            )
        )

    recommendation_context: List[str] = []
    issue_codes = [item["code"] for item in issues]
    if "negative_paper_roi" in issue_codes or "poor_win_rate" in issue_codes:
        recommendation_context.append("tighten entry logic and retrain for better trade selection quality")
    if "slippage_pressure" in issue_codes or "severe_slippage" in issue_codes:
        recommendation_context.append("reduce execution aggressiveness and prefer lower-friction symbols or markets")
    if "no_trade_syndrome" in issue_codes or "excessive_rejections" in issue_codes:
        recommendation_context.append("review filters and execution thresholds because the runner is failing to convert opportunities")
    if "leader_not_strict_pass" in issue_codes or "readiness_blocked" in issue_codes:
        recommendation_context.append("improve model quality and data coverage before promoting this family")
    if "untrainable_model" in issue_codes or "training_warmup" in issue_codes:
        recommendation_context.append("fix data or learner bootstrapping so every required model can train before relying on paper results")
    if "trade_stalled" in issue_codes or "training_stalled" in issue_codes or "stalled_model" in issue_codes:
        recommendation_context.append("trigger a maintenance cycle now: debug the runner, rework the lineage, and retire it if the stall persists after tweaks")
    if "runtime_error" in issue_codes or "heartbeat_stale" in issue_codes:
        recommendation_context.append("debug runtime stability before trusting paper performance")

    severity_order = {"critical": 2, "warning": 1}
    health_status = "healthy"
    if issues:
        if any(item["severity"] == "critical" for item in issues):
            health_status = "critical"
        elif any(item["severity"] == "warning" for item in issues):
            health_status = "warning"

    return {
        "requested_target": requested_target_id,
        "resolved_target": resolved_target,
        "store_target": store_portfolio_id,
        "canonical_target": canonical_target_id,
        "runtime_target": str(runtime_target_id),
        "evidence_source": evidence_source,
        "evidence_store_exists": evidence_store_exists,
        "is_runtime_alias": bool(runtime_alias),
        "contract_source": "runtime_health" if runtime_health else "legacy",
        "status": status,
        "running": running,
        "heartbeat_ts": heartbeat_ts,
        "heartbeat_age_seconds": heartbeat_age,
        "recent_trade_count": len(trades),
        "recent_event_count": len(events),
        "has_execution_signal": bool(running or trades or events),
        "market_closed_idle": market_closed_idle,
        "market_schedule": market_schedule or "always_on",
        "health_status": health_status,
        "issues": sorted(issues, key=lambda item: severity_order.get(item["severity"], 0), reverse=True),
        "issue_codes": issue_codes,
        "error": error,
        "blockers": blockers,
        "account": {
            "currency": account.currency if isinstance(account, AccountSnapshot) else str(state.get("currency") or "USD"),
            "current_balance": _safe_float(account.current_balance if isinstance(account, AccountSnapshot) else state.get("current_balance_usd")),
            "realized_pnl": realized_pnl,
            "roi_pct": roi_pct,
            "drawdown_pct": drawdown_pct,
            "trade_count": account_trade_count,
            "wins": _safe_int(account.wins if isinstance(account, AccountSnapshot) else state.get("wins")),
            "losses": _safe_int(account.losses if isinstance(account, AccountSnapshot) else state.get("losses")),
            "open_position_count": _open_position_count(state),
        },
        "recent_trade_stats": trade_stats,
        "event_summary": event_summary,
        "execution_quality": quality,
        "training_state": training,
        "runtime_started_at": runtime_started_at.isoformat() if runtime_started_at is not None else None,
        "runtime_age_hours": runtime_age_hours,
        "last_trade_activity_at": last_trade_activity_at,
        "last_trade_activity_age_hours": last_trade_activity_age_hours,
        "last_training_activity_age_hours": last_training_activity_age_hours,
        "recommendation_context": recommendation_context,
    }


def summarize_execution_targets(
    targets: Iterable[str],
    *,
    root: str | None = None,
    market_schedule: Optional[str] = None,
) -> Dict[str, Any]:
    target_rows = [
        build_portfolio_execution_evidence(
            target, requested_target=str(target), root=root, market_schedule=market_schedule,
        )
        for target in list(targets)
    ]
    issue_codes = list(
        dict.fromkeys(
            code
            for row in target_rows
            for code in row.get("issue_codes") or []
        )
    )
    recommendation_context = list(
        dict.fromkeys(
            note
            for row in target_rows
            for note in row.get("recommendation_context") or []
        )
    )
    blocked_target_count = sum(
        1 for row in target_rows if row.get("error") or row.get("health_status") == "critical"
    )
    critical_issue_count = sum(
        1 for row in target_rows for item in (row.get("issues") or []) if item.get("severity") == "critical"
    )
    warning_issue_count = sum(
        1 for row in target_rows for item in (row.get("issues") or []) if item.get("severity") == "warning"
    )
    health_status = "healthy"
    if critical_issue_count:
        health_status = "critical"
    elif warning_issue_count:
        health_status = "warning"
    summary_parts: List[str] = []
    if issue_codes:
        summary_parts.append(", ".join(issue_codes[:4]))
    if recommendation_context:
        summary_parts.append(recommendation_context[0])
    any_market_closed = any(bool(row.get("market_closed_idle")) for row in target_rows)
    return {
        "targets": target_rows,
        "running_target_count": sum(1 for row in target_rows if row.get("running")),
        "recent_trade_count": sum(_safe_int(row.get("recent_trade_count")) for row in target_rows),
        "recent_event_count": sum(_safe_int(row.get("recent_event_count")) for row in target_rows),
        "blocked_target_count": blocked_target_count,
        "critical_issue_count": critical_issue_count,
        "warning_issue_count": warning_issue_count,
        "health_status": health_status,
        "issue_codes": issue_codes,
        "recommendation_context": recommendation_context,
        "summary": " | ".join(summary_parts),
        "has_execution_signal": any(bool(row.get("has_execution_signal")) for row in target_rows),
        "market_closed_idle": any_market_closed,
        "market_schedule": market_schedule or "always_on",
    }
