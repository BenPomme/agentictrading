from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

import config
from factory.contracts import PromotionStage


def compact_number(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except Exception:
        return 0.0


def uses_slow_thresholds(*labels: str) -> bool:
    normalized = [str(item or "").strip().lower() for item in labels if str(item or "").strip()]
    return any(token.startswith("betfair") or token.startswith("polymarket") for token in normalized)


def assessment_thresholds(*, labels: Iterable[str], phase: str = "full") -> Tuple[bool, int, int]:
    joined_labels = [str(item or "") for item in labels]
    slow = uses_slow_thresholds(*joined_labels)
    if phase == "first":
        required_days = int(
            getattr(
                config,
                "FACTORY_FIRST_ASSESSMENT_DAYS",
                2,
            )
            or 2
        )
        required_trades = int(
            getattr(
                config,
                "FACTORY_FIRST_ASSESSMENT_SLOW_SETTLED" if slow else "FACTORY_FIRST_ASSESSMENT_FAST_TRADES",
                4 if slow else 10,
            )
            or (4 if slow else 10)
        )
        return slow, required_days, required_trades
    required_days = int(
        getattr(
            config,
            "FACTORY_PAPER_GATE_MIN_DAYS",
            30,
        )
        or 30
    )
    required_trades = int(
        getattr(
            config,
            "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED" if slow else "FACTORY_PAPER_GATE_MIN_FAST_TRADES",
            10 if slow else 50,
        )
        or (10 if slow else 50)
    )
    return slow, required_days, required_trades


def assessment_progress(
    *,
    paper_days: int,
    trade_count: int,
    labels: Iterable[str],
    realized_roi_pct: float | None = None,
    current_stage: str | None = None,
    phase: str = "full",
) -> Dict[str, Any]:
    slow, required_days, required_trades = assessment_thresholds(labels=labels, phase=phase)
    observed_days = max(0, int(paper_days or 0))
    observed_trades = max(0, int(trade_count or 0))
    days_progress = min(1.0, observed_days / max(required_days, 1))
    trades_progress = min(1.0, observed_trades / max(required_trades, 1))
    completion_pct = round(((days_progress + trades_progress) / 2.0) * 100.0, 1)
    days_remaining = max(0, required_days - observed_days)
    trades_remaining = max(0, required_trades - observed_trades)
    trades_per_day = (observed_trades / observed_days) if observed_days > 0 else 0.0
    complete = days_remaining <= 0 and trades_remaining <= 0
    if complete:
        eta = "complete"
        status = "complete"
    else:
        eta_days_candidates: List[int] = []
        if days_remaining > 0:
            eta_days_candidates.append(days_remaining)
        if trades_remaining > 0:
            if trades_per_day > 0.0:
                eta_days_candidates.append(max(1, int((trades_remaining / trades_per_day) + 0.999)))
            else:
                eta_days_candidates.append(required_days)
        eta_days = max(eta_days_candidates) if eta_days_candidates else 0
        eta = f"~{eta_days}d left"
        if phase == "first":
            status = "complete" if completion_pct >= 100.0 else ("warming_up" if completion_pct < 50.0 else "first_read_ready")
        else:
            status = "complete" if completion_pct >= 100.0 else ("early" if completion_pct < 50.0 else "maturing")
    roi = compact_number(realized_roi_pct) if realized_roi_pct is not None else None
    promoted = str(current_stage or "") in {
        PromotionStage.CANARY_READY.value,
        PromotionStage.LIVE_READY.value,
        PromotionStage.APPROVED_LIVE.value,
    }
    if promoted and completion_pct >= 100.0:
        status = "complete"
    return {
        "phase": phase,
        "status": status,
        "complete": complete,
        "completion_pct": completion_pct,
        "paper_days_observed": observed_days,
        "paper_days_required": required_days,
        "days_remaining": days_remaining,
        "trade_count_observed": observed_trades,
        "trade_count_required": required_trades,
        "trades_remaining": trades_remaining,
        "eta": eta,
        "slow_strategy": slow,
        "roi_pct": roi,
    }
