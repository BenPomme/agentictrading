from __future__ import annotations

from factory.contracts import EvaluationBundle, LineageRecord, PromotionStage
from factory.promotion import PromotionController, PromotionGateConfig


def _lineage() -> LineageRecord:
    return LineageRecord(
        lineage_id="lineage-a",
        family_id="binance_funding_contrarian",
        label="Funding Champion",
        role="champion",
        current_stage=PromotionStage.IDEA.value,
        target_portfolios=["contrarian_legacy"],
        target_venues=["binance"],
        hypothesis_id="hypothesis-a",
        genome_id="genome-a",
        experiment_id="experiment-a",
        budget_bucket="incumbent",
        budget_weight_pct=20.0,
        connector_ids=["binance_core"],
        goldfish_workspace="research/goldfish/binance_funding_contrarian",
    )


def _bundle(stage: str = "paper", *, paper_days: int = 30, trade_count: int = 60, settled_count: int = 60) -> EvaluationBundle:
    return EvaluationBundle(
        evaluation_id=f"eval-{stage}",
        lineage_id="lineage-a",
        family_id="binance_funding_contrarian",
        stage=stage,
        source="test",
        monthly_roi_pct=6.0,
        max_drawdown_pct=3.0,
        slippage_headroom_pct=1.0,
        calibration_lift_abs=0.02,
        turnover=0.4,
        capacity_score=0.6,
        failure_rate=0.01,
        regime_robustness=0.7,
        baseline_beaten_windows=3,
        stress_positive=True,
        trade_count=trade_count,
        settled_count=settled_count,
        paper_days=paper_days,
        net_pnl=6.0,
    )


def test_paper_gate_blockers_require_sufficient_days_and_evidence():
    controller = PromotionController(
        PromotionGateConfig(
            monthly_roi_pct=5.0,
            max_drawdown_pct=8.0,
            min_paper_days=30,
            min_fast_trades=50,
            min_slow_settled=10,
        )
    )

    blockers = controller.paper_gate_blockers(
        _bundle(paper_days=12, trade_count=25, settled_count=25),
        slow_strategy=False,
    )

    assert "insufficient_paper_days" in blockers
    assert "insufficient_trade_count" in blockers


def test_decide_requires_human_signoff_before_live():
    controller = PromotionController()
    lineage = _lineage()

    decision = controller.decide(
        lineage,
        data_ready=True,
        workspace_ready=True,
        walkforward_bundle=_bundle("walkforward"),
        incumbent_walkforward_bundle=None,
        stress_bundle=_bundle("stress"),
        paper_bundle=_bundle("paper"),
        incumbent_paper_bundle=None,
        manifest_status="pending_approval",
        approved_by=None,
    )

    assert decision.next_stage == PromotionStage.LIVE_READY.value
    assert decision.requires_human_signoff is True
    assert "human_signoff_required" in decision.blockers


def test_decide_marks_lineage_approved_when_manifest_is_approved():
    controller = PromotionController()
    lineage = _lineage()

    decision = controller.decide(
        lineage,
        data_ready=True,
        workspace_ready=True,
        walkforward_bundle=_bundle("walkforward"),
        incumbent_walkforward_bundle=None,
        stress_bundle=_bundle("stress"),
        paper_bundle=_bundle("paper"),
        incumbent_paper_bundle=None,
        manifest_status="approved_live",
        approved_by="operator",
    )

    assert decision.next_stage == PromotionStage.APPROVED_LIVE.value
    assert decision.requires_human_signoff is False
    assert decision.blockers == []


def test_decide_blocks_challenger_that_does_not_beat_incumbent_scorecard():
    controller = PromotionController()
    lineage = _lineage()
    lineage.lineage_id = "lineage-b"
    lineage.role = "paper_challenger"

    incumbent = _bundle("paper")
    incumbent.lineage_id = "lineage-a"
    challenger = _bundle("paper")
    challenger.lineage_id = "lineage-b"
    challenger.monthly_roi_pct = incumbent.monthly_roi_pct + 0.05
    challenger.calibration_lift_abs = incumbent.calibration_lift_abs
    challenger.regime_robustness = incumbent.regime_robustness

    incumbent_walkforward = _bundle("walkforward")
    incumbent_walkforward.lineage_id = "lineage-a"
    challenger_walkforward = _bundle("walkforward")
    challenger_walkforward.lineage_id = "lineage-b"
    challenger_walkforward.monthly_roi_pct = incumbent_walkforward.monthly_roi_pct + 0.05
    challenger_walkforward.calibration_lift_abs = incumbent_walkforward.calibration_lift_abs
    challenger_walkforward.regime_robustness = incumbent_walkforward.regime_robustness

    decision = controller.decide(
        lineage,
        data_ready=True,
        workspace_ready=True,
        walkforward_bundle=challenger_walkforward,
        incumbent_walkforward_bundle=incumbent_walkforward,
        stress_bundle=_bundle("stress"),
        paper_bundle=challenger,
        incumbent_paper_bundle=incumbent,
        manifest_status="pending_approval",
        approved_by=None,
    )

    assert decision.next_stage == PromotionStage.STRESS.value
    assert "challenger_roi_delta_below_scorecard" in decision.blockers
    assert decision.scorecard["backtest"]["comparison_required"] is True
    assert decision.scorecard["backtest"]["comparison_passed"] is False


def test_decide_keeps_existing_paper_lineage_in_paper_when_walkforward_is_missing():
    controller = PromotionController()
    lineage = _lineage()
    lineage.current_stage = PromotionStage.PAPER.value

    decision = controller.decide(
        lineage,
        data_ready=True,
        workspace_ready=True,
        walkforward_bundle=None,
        incumbent_walkforward_bundle=None,
        stress_bundle=None,
        paper_bundle=None,
        incumbent_paper_bundle=None,
        manifest_status="pending_approval",
        approved_by=None,
    )

    assert decision.next_stage == PromotionStage.PAPER.value
    assert "missing_walkforward_evidence" not in decision.blockers


def test_decide_promotes_sparse_venue_shadow_lineage_to_paper_without_walkforward():
    controller = PromotionController()
    lineage = _lineage()
    lineage.current_stage = PromotionStage.SHADOW.value

    decision = controller.decide(
        lineage,
        data_ready=True,
        workspace_ready=True,
        walkforward_bundle=None,
        incumbent_walkforward_bundle=None,
        stress_bundle=None,
        paper_bundle=None,
        incumbent_paper_bundle=None,
        manifest_status="pending_approval",
        approved_by=None,
    )

    assert decision.next_stage == PromotionStage.PAPER.value
