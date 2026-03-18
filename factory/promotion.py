from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import config
from factory.contracts import (
    EvaluationBundle,
    LineageRecord,
    ManifestStatus,
    PromotionDecision,
    PromotionStage,
)


_STAGE_ORDER = [
    PromotionStage.IDEA.value,
    PromotionStage.SPEC.value,
    PromotionStage.DATA_CHECK.value,
    PromotionStage.GOLDFISH_RUN.value,
    PromotionStage.WALKFORWARD.value,
    PromotionStage.STRESS.value,
    PromotionStage.SHADOW.value,
    PromotionStage.PAPER.value,
    PromotionStage.CANARY_READY.value,
    PromotionStage.LIVE_READY.value,
    PromotionStage.APPROVED_LIVE.value,
]


_FAMILY_SCORECARDS: Dict[str, Dict[str, float]] = {
    "default": {
        "min_roi_delta_pct": 0.25,
        "min_calibration_delta_abs": 0.0,
        "max_drawdown_delta_pct": 0.0,
        "min_capacity_delta": 0.0,
        "min_regime_delta": 0.0,
        "max_failure_rate_delta": 0.0,
    },
    "binance_funding_contrarian": {
        "min_roi_delta_pct": 0.35,
        "min_calibration_delta_abs": 0.002,
        "max_drawdown_delta_pct": 0.0,
        "min_capacity_delta": 0.0,
        "min_regime_delta": 0.02,
        "max_failure_rate_delta": 0.0,
    },
    "binance_cascade_regime": {
        "min_roi_delta_pct": 0.30,
        "min_calibration_delta_abs": 0.0,
        "max_drawdown_delta_pct": -0.25,
        "min_capacity_delta": 0.0,
        "min_regime_delta": 0.03,
        "max_failure_rate_delta": -0.01,
    },
    "betfair_prediction_value_league": {
        "min_roi_delta_pct": 0.20,
        "min_calibration_delta_abs": 0.005,
        "max_drawdown_delta_pct": 0.0,
        "min_capacity_delta": 0.0,
        "min_regime_delta": 0.0,
        "max_failure_rate_delta": 0.0,
    },
    "polymarket_cross_venue": {
        "min_roi_delta_pct": 0.20,
        "min_calibration_delta_abs": 0.004,
        "max_drawdown_delta_pct": -0.10,
        "min_capacity_delta": 0.0,
        "min_regime_delta": 0.0,
        "max_failure_rate_delta": -0.01,
    },
}


def _is_sparse_venue(target_venues: list, family_id: str) -> bool:
    """True for venues with sparse/weak backtest data (Binance, Polymarket, Betfair).

    These venues get more permissive pre-paper entry gates because their
    backtest data is structurally weaker than equity markets.
    """
    venues = {str(v).lower() for v in target_venues}
    sparse = {"binance", "polymarket", "betfair"}
    # If ANY target venue is sparse and none are rich, classify as sparse
    rich = {"yahoo", "alpaca"}
    has_sparse = bool(venues & sparse)
    has_rich = bool(venues & rich)
    # Mixed venue: treat as sparse (more permissive) since sparse data limits overall quality
    return has_sparse and not has_rich


@dataclass
class PromotionGateConfig:
    """Gate thresholds for paper promotion lifecycle.

    PRE-PAPER thresholds control entry into paper testing.
    POST-PAPER thresholds control advancement beyond paper (to canary/live).
    Venue-differentiated: sparse venues get permissive pre-paper entry.
    """
    # --- Pre-paper entry: rich-history venues (yahoo/alpaca) ---
    pre_paper_rich_min_roi_pct: float = 3.0
    pre_paper_rich_max_drawdown_pct: float = 10.0
    pre_paper_rich_min_beaten_windows: int = 2
    pre_paper_rich_require_stress: bool = True

    # --- Pre-paper entry: sparse-history venues (binance/polymarket/betfair) ---
    pre_paper_sparse_min_roi_pct: float = 0.0  # any positive backtest
    pre_paper_sparse_max_drawdown_pct: float = 15.0
    pre_paper_sparse_min_beaten_windows: int = 0
    pre_paper_sparse_require_stress: bool = False

    # --- Post-paper validation (for advancement beyond paper) ---
    monthly_roi_pct: float = float(getattr(config, "FACTORY_PAPER_GATE_MONTHLY_ROI_PCT", 5.0))
    max_drawdown_pct: float = float(getattr(config, "FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT", 8.0))
    min_paper_days: int = int(getattr(config, "FACTORY_PAPER_GATE_MIN_DAYS", 30))
    min_fast_trades: int = int(getattr(config, "FACTORY_PAPER_GATE_MIN_FAST_TRADES", 50))
    min_slow_settled: int = int(getattr(config, "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED", 10))


class PromotionController:
    def __init__(self, gate_config: Optional[PromotionGateConfig] = None):
        self.gates = gate_config or PromotionGateConfig()

    def scorecard_for_family(self, family_id: str) -> Dict[str, float]:
        return dict(_FAMILY_SCORECARDS.get(family_id, _FAMILY_SCORECARDS["default"]))

    def pre_paper_entry_blockers(
        self,
        walkforward_bundle: Optional['EvaluationBundle'],
        stress_bundle: Optional['EvaluationBundle'],
        *,
        sparse_venue: bool,
    ) -> List[str]:
        """Determine if a model is promising enough to START paper testing.

        This gate uses backtest/stress evidence only. It must NOT require
        paper_days, paper trade_count, or any metric that can only exist
        after paper trading has begun.
        """
        blockers: List[str] = []

        if walkforward_bundle is None:
            blockers.append("no_walkforward_evidence_for_paper_entry")
            return blockers

        if sparse_venue:
            # Sparse venues: permissive entry — paper is where real learning happens
            if walkforward_bundle.monthly_roi_pct <= self.gates.pre_paper_sparse_min_roi_pct:
                blockers.append("backtest_roi_non_positive")
            if walkforward_bundle.max_drawdown_pct > self.gates.pre_paper_sparse_max_drawdown_pct:
                blockers.append("backtest_drawdown_above_15pct")
            if walkforward_bundle.baseline_beaten_windows < self.gates.pre_paper_sparse_min_beaten_windows:
                blockers.append("baseline_windows_insufficient_sparse")
            if self.gates.pre_paper_sparse_require_stress:
                if stress_bundle is None or not stress_bundle.stress_positive:
                    blockers.append("stress_not_positive")
        else:
            # Rich venues: stricter entry — backtest data is meaningful
            if walkforward_bundle.monthly_roi_pct < self.gates.pre_paper_rich_min_roi_pct:
                blockers.append("backtest_roi_below_3pct")
            if walkforward_bundle.max_drawdown_pct > self.gates.pre_paper_rich_max_drawdown_pct:
                blockers.append("backtest_drawdown_above_10pct")
            if walkforward_bundle.baseline_beaten_windows < self.gates.pre_paper_rich_min_beaten_windows:
                blockers.append("baseline_windows_insufficient_rich")
            if self.gates.pre_paper_rich_require_stress:
                if stress_bundle is None or not stress_bundle.stress_positive:
                    blockers.append("stress_not_positive")

        # Universal hard vetoes
        if walkforward_bundle.hard_vetoes:
            blockers.extend(list(walkforward_bundle.hard_vetoes))

        return blockers

    def compare_to_incumbent(
        self,
        challenger: EvaluationBundle,
        incumbent: Optional[EvaluationBundle],
    ) -> Dict[str, object]:
        if incumbent is None or incumbent.lineage_id == challenger.lineage_id:
            return {
                "required": False,
                "passed": True,
                "blockers": [],
                "deltas": {},
                "scorecard": self.scorecard_for_family(challenger.family_id),
                "incumbent_lineage_id": None,
            }
        scorecard = self.scorecard_for_family(challenger.family_id)
        deltas = {
            "roi_delta_pct": round(challenger.monthly_roi_pct - incumbent.monthly_roi_pct, 6),
            "calibration_delta_abs": round(challenger.calibration_lift_abs - incumbent.calibration_lift_abs, 6),
            "drawdown_delta_pct": round(challenger.max_drawdown_pct - incumbent.max_drawdown_pct, 6),
            "capacity_delta": round(challenger.capacity_score - incumbent.capacity_score, 6),
            "regime_delta": round(challenger.regime_robustness - incumbent.regime_robustness, 6),
            "failure_rate_delta": round(challenger.failure_rate - incumbent.failure_rate, 6),
        }
        blockers: List[str] = []
        if deltas["roi_delta_pct"] < float(scorecard["min_roi_delta_pct"]):
            blockers.append("challenger_roi_delta_below_scorecard")
        if deltas["calibration_delta_abs"] < float(scorecard["min_calibration_delta_abs"]):
            blockers.append("challenger_calibration_delta_below_scorecard")
        if deltas["drawdown_delta_pct"] > float(scorecard["max_drawdown_delta_pct"]):
            blockers.append("challenger_drawdown_regression")
        if deltas["capacity_delta"] < float(scorecard["min_capacity_delta"]):
            blockers.append("challenger_capacity_delta_below_scorecard")
        if deltas["regime_delta"] < float(scorecard["min_regime_delta"]):
            blockers.append("challenger_regime_delta_below_scorecard")
        if deltas["failure_rate_delta"] > float(scorecard["max_failure_rate_delta"]):
            blockers.append("challenger_failure_rate_regression")
        return {
            "required": True,
            "passed": not blockers,
            "blockers": blockers,
            "deltas": deltas,
            "scorecard": scorecard,
            "incumbent_lineage_id": incumbent.lineage_id,
        }

    def post_paper_validation_blockers(self, bundle: EvaluationBundle, *, slow_strategy: bool) -> List[str]:
        """Determine if a model has survived paper testing well enough to advance.

        This gate requires paper-period evidence: paper_days, trade_count, etc.
        It runs AFTER the model has been paper trading, not before.
        """
        blockers: List[str] = []
        if bundle.baseline_beaten_windows < 3:
            blockers.append("baseline_not_beaten_on_3_windows")
        if bundle.paper_days < self.gates.min_paper_days:
            blockers.append("insufficient_paper_days")
        if bundle.monthly_roi_pct < self.gates.monthly_roi_pct:
            blockers.append("monthly_roi_below_5pct")
        if bundle.max_drawdown_pct > self.gates.max_drawdown_pct:
            blockers.append("drawdown_above_8pct")
        if bundle.slippage_headroom_pct <= 0.0:
            blockers.append("slippage_stress_non_positive")
        if slow_strategy:
            if bundle.settled_count < self.gates.min_slow_settled:
                blockers.append("insufficient_settled_events")
        elif bundle.trade_count < self.gates.min_fast_trades:
            blockers.append("insufficient_trade_count")
        if bundle.hard_vetoes:
            blockers.extend(list(bundle.hard_vetoes))
        return blockers

    # Backward-compatible alias
    paper_gate_blockers = post_paper_validation_blockers

    def decide(
        self,
        lineage: LineageRecord,
        *,
        data_ready: bool,
        workspace_ready: bool,
        walkforward_bundle: Optional[EvaluationBundle],
        incumbent_walkforward_bundle: Optional[EvaluationBundle],
        stress_bundle: Optional[EvaluationBundle],
        paper_bundle: Optional[EvaluationBundle],
        incumbent_paper_bundle: Optional[EvaluationBundle],
        manifest_status: Optional[str],
        approved_by: Optional[str],
        autonomous_paper_allowed: bool = False,
    ) -> PromotionDecision:
        current_index = _STAGE_ORDER.index(lineage.current_stage)
        target_stage = lineage.current_stage
        blockers: List[str] = []

        if current_index < _STAGE_ORDER.index(PromotionStage.SPEC.value):
            target_stage = PromotionStage.SPEC.value
        if data_ready:
            target_stage = PromotionStage.DATA_CHECK.value
        else:
            blockers.append("connector_data_not_ready")
        if workspace_ready and data_ready:
            target_stage = PromotionStage.GOLDFISH_RUN.value
        else:
            blockers.append("goldfish_workspace_not_ready")
        backtest_review: Dict[str, object] = {
            "required": False,
            "passed": True,
            "blockers": [],
            "deltas": {},
            "scorecard": self.scorecard_for_family(lineage.family_id),
            "incumbent_lineage_id": None,
        }
        if walkforward_bundle is not None:
            target_stage = PromotionStage.WALKFORWARD.value
            if walkforward_bundle.baseline_beaten_windows < 3:
                blockers.append("walkforward_window_coverage_insufficient")
            backtest_review = self.compare_to_incumbent(
                walkforward_bundle,
                incumbent_walkforward_bundle,
            )
        else:
            blockers.append("missing_walkforward_evidence")
        # Venue-aware stress gating: sparse venues can bypass stress requirement
        sparse_venue = _is_sparse_venue(
            lineage.target_venues if hasattr(lineage, "target_venues") else [],
            lineage.family_id,
        )
        _stress_required = not sparse_venue or self.gates.pre_paper_sparse_require_stress
        if stress_bundle is not None and stress_bundle.stress_positive:
            target_stage = PromotionStage.STRESS.value
            if bool(backtest_review["passed"]):
                target_stage = PromotionStage.SHADOW.value
            else:
                blockers.extend(list(backtest_review["blockers"]))
        elif not _stress_required and bool(backtest_review.get("passed", True)):
            # Sparse venue: stress not required — advance to SHADOW on backtest alone
            target_stage = PromotionStage.SHADOW.value
        elif stress_bundle is not None and not stress_bundle.stress_positive:
            blockers.append("stress_eval_negative")
        else:
            blockers.append("missing_stress_evidence")
        # --- PRE-PAPER ENTRY: decide if model can START paper testing ---
        # This runs when the model has reached SHADOW and has backtest evidence.
        # It must NOT require paper-period evidence (paper_days, trade_count).
        if target_stage == PromotionStage.SHADOW.value:
            entry_blockers = self.pre_paper_entry_blockers(
                walkforward_bundle, stress_bundle, sparse_venue=sparse_venue,
            )
            if not entry_blockers:
                target_stage = PromotionStage.PAPER.value
            else:
                blockers.extend(entry_blockers)

        # --- POST-PAPER VALIDATION: has paper period been strong enough? ---
        # This runs when paper_bundle exists (model has been paper trading).
        incumbent_review: Dict[str, object] = {
            "required": False,
            "passed": True,
            "blockers": [],
            "deltas": {},
            "scorecard": self.scorecard_for_family(lineage.family_id),
            "incumbent_lineage_id": None,
        }
        if paper_bundle is not None and target_stage == PromotionStage.PAPER.value:
            slow_strategy = "slow" in lineage.family_id or "polymarket" in lineage.family_id
            paper_blockers = self.post_paper_validation_blockers(paper_bundle, slow_strategy=slow_strategy)
            incumbent_review = self.compare_to_incumbent(paper_bundle, incumbent_paper_bundle)
            if not paper_blockers and bool(incumbent_review["passed"]):
                if autonomous_paper_allowed:
                    # Autonomous paper mode: stay at PAPER — do not advance to
                    # CANARY/LIVE which require human signoff. Paper trading
                    # proceeds autonomously; live remains blocked.
                    target_stage = PromotionStage.PAPER.value
                else:
                    target_stage = PromotionStage.CANARY_READY.value
                    target_stage = PromotionStage.LIVE_READY.value
            elif paper_blockers:
                # Paper is running but validation not yet met — this is normal.
                # Don't add these as blockers since the model should CONTINUE paper.
                pass
        requires_human_signoff = target_stage in {
            PromotionStage.CANARY_READY.value,
            PromotionStage.LIVE_READY.value,
            PromotionStage.APPROVED_LIVE.value,
        }
        if manifest_status == ManifestStatus.APPROVED_LIVE.value and approved_by:
            target_stage = PromotionStage.APPROVED_LIVE.value
            blockers = []
            requires_human_signoff = False
        elif target_stage == PromotionStage.LIVE_READY.value:
            blockers.append("human_signoff_required")
        unique_blockers = list(dict.fromkeys(blockers))
        reasons = [f"target_stage={target_stage}"]
        if backtest_review["required"]:
            reasons.append(f"backtest_compare={dict(backtest_review['deltas'])}")
        if incumbent_review["required"]:
            reasons.append(f"incumbent_compare={dict(incumbent_review['deltas'])}")
        return PromotionDecision(
            lineage_id=lineage.lineage_id,
            current_stage=lineage.current_stage,
            next_stage=target_stage,
            allowed=target_stage != lineage.current_stage,
            requires_human_signoff=requires_human_signoff,
            blockers=unique_blockers,
            reasons=reasons,
            scorecard={
                "family_id": lineage.family_id,
                "backtest": {
                    "comparison_required": bool(backtest_review["required"]),
                    "comparison_passed": bool(backtest_review["passed"]),
                    "incumbent_lineage_id": backtest_review["incumbent_lineage_id"],
                    "thresholds": dict(backtest_review["scorecard"]),
                    "deltas": dict(backtest_review["deltas"]),
                },
                "paper": {
                    "comparison_required": bool(incumbent_review["required"]),
                    "comparison_passed": bool(incumbent_review["passed"]),
                    "incumbent_lineage_id": incumbent_review["incumbent_lineage_id"],
                    "thresholds": dict(incumbent_review["scorecard"]),
                    "deltas": dict(incumbent_review["deltas"]),
                },
            },
        )
