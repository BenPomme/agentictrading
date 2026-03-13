from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

import config

_CHALLENGER_ROLES = {"paper_challenger", "shadow_challenger", "moonshot"}
_REPLACEMENT_PRESSURE_STATUSES = {
    "review_requested_replace",
    "review_requested_rework",
    "review_replace_requested",
    "review_rework_requested",
    "replace",
    "rework",
}
_ISOLATED_LANE_PROGRESS_STATUSES = {
    "prepare_isolated_lane",
    "isolated_lane_active",
    "isolated_lane_first_assessment_passed",
}
_STAGE_PRIORITY = {
    "approved_live": 0,
    "live_ready": 1,
    "canary_ready": 2,
    "paper": 3,
    "shadow": 4,
    "stress": 5,
    "walkforward": 6,
}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def runtime_lane_selection_key(item: Dict[str, object], *, prefer_challenger: bool) -> Tuple[Any, ...]:
    role = str(item.get("role") or "").strip().lower()
    is_challenger = role in _CHALLENGER_ROLES
    if prefer_challenger:
        lane_bias = 0 if is_challenger else 1
    else:
        lane_bias = 0 if role == "champion" else (1 if is_challenger else 2)
    stage = str(item.get("current_stage") or item.get("approved_stage") or "")
    health = str(item.get("execution_health_status") or "")
    curated_rank = item.get("curated_family_rank")
    curated_rank_value = _int_value(curated_rank, default=999) if curated_rank is not None else 999
    return (
        lane_bias,
        0 if bool(item.get("strict_gate_pass")) else 1,
        _STAGE_PRIORITY.get(stage, 999),
        0 if health in {"healthy", "warning"} else 1,
        curated_rank_value,
        -_float_value(item.get("curated_ranking_score"), 0.0),
        -_float_value(item.get("fitness_score"), 0.0),
        str(item.get("lineage_id") or ""),
    )


def _materially_stronger_challenger(champion: Dict[str, object], challenger: Dict[str, object]) -> bool:
    min_score_gap = float(getattr(config, "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", 3.0))
    min_paper_days = int(getattr(config, "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", 7))
    min_trade_count = int(getattr(config, "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", 10))
    champion_rank_raw = champion.get("curated_family_rank")
    challenger_rank_raw = challenger.get("curated_family_rank")
    champion_score_raw = champion.get("curated_ranking_score")
    challenger_score_raw = challenger.get("curated_ranking_score")
    champion_rank = _int_value(champion_rank_raw, default=999) if champion_rank_raw is not None else None
    challenger_rank = _int_value(challenger_rank_raw, default=999) if challenger_rank_raw is not None else None
    champion_score = _float_value(champion_score_raw, 0.0) if champion_score_raw is not None else None
    challenger_score = _float_value(challenger_score_raw, 0.0) if challenger_score_raw is not None else None
    champion_fitness = _float_value(champion.get("fitness_score"), 0.0)
    challenger_fitness = _float_value(challenger.get("fitness_score"), 0.0)
    challenger_trade_count = max(
        _int_value(challenger.get("live_paper_trade_count"), 0),
        _int_value(challenger.get("curated_paper_closed_trade_count"), 0),
        _int_value(challenger.get("trade_count"), 0),
    )
    challenger_paper_days = _int_value(challenger.get("paper_days"), 0)
    enough_evidence = challenger_paper_days >= min_paper_days or challenger_trade_count >= min_trade_count
    clearly_better_rank = (
        challenger_rank is not None
        and champion_rank is not None
        and challenger_rank < champion_rank
    )
    clearly_better_score = (
        challenger_score is not None
        and champion_score is not None
        and (challenger_score - champion_score) >= min_score_gap
    )
    clearly_better_fitness = challenger_fitness > champion_fitness and (
        challenger_rank is None or champion_rank is None or challenger_rank <= champion_rank
    )
    return enough_evidence and (clearly_better_rank or clearly_better_score or clearly_better_fitness)


def _needs_first_paper_read(challenger: Dict[str, object]) -> bool:
    live_trade_count = _int_value(challenger.get("live_paper_trade_count"), 0)
    live_paper_days = _int_value(challenger.get("live_paper_days"), 0)
    if live_trade_count > 0 or live_paper_days > 0:
        return False
    current_stage = str(challenger.get("current_stage") or "").strip().lower()
    if current_stage not in {"shadow", "paper", "canary_ready", "live_ready", "stress", "walkforward"}:
        return False
    research_roi = max(
        _float_value(challenger.get("curated_paper_roi_pct"), 0.0),
        _float_value(challenger.get("monthly_roi_pct"), 0.0),
    )
    research_trades = max(
        _int_value(challenger.get("curated_paper_closed_trade_count"), 0),
        _int_value(challenger.get("trade_count"), 0),
    )
    min_roi = float(getattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", 1.0))
    min_research_trades = int(getattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", 25))
    return research_roi >= min_roi and research_trades >= min_research_trades


def _execution_issue_codes(row: Dict[str, object]) -> set[str]:
    issue_codes = {
        str(item).strip().lower()
        for item in (row.get("execution_issue_codes") or [])
        if str(item).strip()
    }
    validation = dict(row.get("execution_validation") or {})
    issue_codes.update(
        str(item).strip().lower()
        for item in (validation.get("issue_codes") or [])
        if str(item).strip()
    )
    return issue_codes


def decide_runtime_lane_policy(rows: Iterable[Dict[str, object]]) -> Tuple[bool, str]:
    family_rows: List[Dict[str, object]] = [dict(item) for item in rows]
    if not family_rows:
        return False, "family_primary_incumbent"
    champion = next(
        (
            item
            for item in family_rows
            if str(item.get("role") or "").strip().lower() == "champion"
        ),
        sorted(family_rows, key=lambda item: runtime_lane_selection_key(item, prefer_challenger=False))[0],
    )
    challengers = [
        item
        for item in family_rows
        if str(item.get("lineage_id") or "") != str(champion.get("lineage_id") or "")
        and str(item.get("role") or "").strip().lower() in _CHALLENGER_ROLES
    ]
    if not challengers:
        return False, "family_primary_incumbent"
    champion_pressure = str(champion.get("maintenance_request_action") or "").strip().lower()
    champion_status = str(champion.get("iteration_status") or "").strip().lower()
    if champion_pressure in _REPLACEMENT_PRESSURE_STATUSES or champion_status in _REPLACEMENT_PRESSURE_STATUSES:
        return True, "family_replacement_pressure"
    if any(
        str(item.get("iteration_status") or "").strip().lower() in _ISOLATED_LANE_PROGRESS_STATUSES
        for item in challengers
    ):
        return True, "isolated_lane_progress"
    best_challenger = sorted(
        challengers,
        key=lambda item: runtime_lane_selection_key(item, prefer_challenger=True),
    )[0]
    champion_issue_codes = _execution_issue_codes(champion)
    if champion_issue_codes.intersection({"trade_stalled", "training_stalled", "stalled_model", "no_trade_syndrome"}) and _needs_first_paper_read(best_challenger):
        return True, "incumbent_trade_stalled"
    if _needs_first_paper_read(best_challenger):
        return True, "paper_qualification_needed"
    champion_health = str(champion.get("execution_health_status") or "").strip().lower()
    if champion_health == "critical" and _materially_stronger_challenger(champion, best_challenger):
        return True, "incumbent_critical_health"
    if not bool(champion.get("strict_gate_pass")) and _materially_stronger_challenger(champion, best_challenger):
        return True, "challenger_materially_stronger"
    return False, "family_primary_incumbent"
