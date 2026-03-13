from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
from factory.agent_runtime import AgentRunResult
from factory.contracts import EvaluationBundle
from factory.experiment_runner import FactoryExperimentRunner
from factory.manifests import candidate_context_refs_for_portfolio, live_manifest_refs_for_portfolio
from factory.orchestrator import FactoryOrchestrator

from tests.unit.conftest import execution_repo_root

execution_root = execution_repo_root()
if execution_root is not None:
    from portfolio.accounting import build_strategy_account
    from portfolio.runner_base import PortfolioRunnerBase
    from portfolio.runners.betfair_runner import BetfairPortfolioRunner
    from portfolio.runners.cascade_alpha_runner import CascadeAlphaPortfolioRunner
    from portfolio.runners.contrarian_runner import ContrarianLegacyPortfolioRunner
    from portfolio.runners.hedge_research_runner import HedgeResearchPortfolioRunner
    from portfolio.runners.hedge_runner import HedgeValidationPortfolioRunner
    from portfolio.runners.polymarket_quantum_fold_runner import PolymarketQuantumFoldPortfolioRunner
    from portfolio.state_store import PortfolioStateStore
    from portfolio.types import PortfolioRunnerSpec
else:
    PortfolioRunnerBase = object  # type: ignore[assignment]
    PortfolioStateStore = None  # type: ignore[assignment]
    PortfolioRunnerSpec = None  # type: ignore[assignment]
    build_strategy_account = None  # type: ignore[assignment]
    BetfairPortfolioRunner = None  # type: ignore[assignment]
    CascadeAlphaPortfolioRunner = None  # type: ignore[assignment]
    ContrarianLegacyPortfolioRunner = None  # type: ignore[assignment]
    HedgeResearchPortfolioRunner = None  # type: ignore[assignment]
    HedgeValidationPortfolioRunner = None  # type: ignore[assignment]
    PolymarketQuantumFoldPortfolioRunner = None  # type: ignore[assignment]


class _DummyRunner(PortfolioRunnerBase):
    def run(self) -> None:
        raise NotImplementedError


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _seed_prediction_examples(project_root: Path, *, count: int = 720) -> None:
    rows: list[dict] = []
    for idx in range(count):
        timestamp = f"2026-03-{1 + (idx // 24):02d}T{idx % 24:02d}:00:00+00:00"
        signal = 1 if idx % 6 in {0, 1, 3, 4} else 0
        base_prob = 0.47 + (0.02 if idx % 9 in {0, 1, 2} else -0.015)
        odds = 2.08 if signal else 1.92
        label = 1 if (signal and idx % 5 != 0) or ((not signal) and idx % 11 == 0) else 0
        rows.append(
            {
                "timestamp": timestamp,
                "base_prob": base_prob,
                "odds": odds,
                "label": label,
                "spread_mean": 0.18 + ((idx % 7) * 0.01),
                "imbalance": 0.65 if signal else -0.55,
                "depth_total_eur": 900.0 + (idx % 8) * 35.0,
                "price_velocity": 0.012 if signal else -0.009,
                "short_volatility": 0.03 + ((idx % 5) * 0.002),
                "time_to_start_sec": float(21600 - ((idx % 24) * 900)),
                "in_play": 1.0 if idx % 10 >= 6 else 0.0,
                "weighted_spread": 0.24 + ((idx % 6) * 0.015),
                "lay_back_ratio": 1.65 if signal else 0.72,
                "top_of_book_concentration": 0.58 + ((idx % 4) * 0.03),
                "selection_count": 2.0 + float(idx % 3),
            }
        )
    _write_jsonl(project_root / "data/prediction/online_examples_implied_market_1.jsonl", rows)


def _seed_portfolio(store: PortfolioStateStore, *, currency: str, starting_balance: float, realized_pnl: float, trade_count: int) -> None:
    balance_history = [
        {"ts": "2026-03-01T00:00:00Z", "balance": starting_balance},
        {"ts": "2026-03-15T00:00:00Z", "balance": starting_balance + realized_pnl + 10.0},
        {"ts": "2026-03-30T00:00:00Z", "balance": starting_balance + realized_pnl},
    ]
    store.write_account(
        build_strategy_account(
            portfolio_id=store.portfolio_id,
            currency=currency,
            starting_balance=starting_balance,
            current_balance=starting_balance + realized_pnl,
            realized_pnl=realized_pnl,
            trade_count=trade_count,
            balance_history=balance_history,
        )
    )
    store.write_trades(
        [
            {
                "trade_id": f"{store.portfolio_id}-{idx}",
                "status": "CLOSED",
                "net_pnl_usd": 1.0,
            }
            for idx in range(trade_count)
        ]
    )


def _prepare_factory_inputs(project_root: Path) -> None:
    if PortfolioStateStore is None or build_strategy_account is None:
        raise RuntimeError("Execution repo integration is required for this test.")
    for rel_path in [
        "data/funding_history",
        "data/funding_models",
        "data/candidates",
        "data/prediction",
        "data/state",
        "data/portfolios/betfair_core",
        "data/portfolios/polymarket_quantum_fold",
    ]:
        (project_root / rel_path).mkdir(parents=True, exist_ok=True)

    (project_root / "data/funding_models/funding_predictor_meta.json").write_text("{}", encoding="utf-8")
    (project_root / "data/portfolios/betfair_core/runtime").mkdir(parents=True, exist_ok=True)
    (project_root / "data/portfolios/betfair_core/runtime/polymarket_binary_research_state.json").write_text(
        "{}",
        encoding="utf-8",
    )
    _write_jsonl(
        project_root / "data/funding/experiments.jsonl",
        [{"metrics": {"rolling_200": {"settled": 120, "roi_pct": 6.5, "brier_lift_abs": 0.025}}}],
    )
    _write_jsonl(
        project_root / "data/prediction/experiments.jsonl",
        [{"metrics": {"rolling_200": {"settled": 120, "roi_pct": 6.2, "brier_lift_abs": 0.018}}}],
    )
    _seed_prediction_examples(project_root)

    _seed_portfolio(PortfolioStateStore("contrarian_legacy"), currency="USD", starting_balance=1000.0, realized_pnl=60.0, trade_count=60)
    _seed_portfolio(PortfolioStateStore("cascade_alpha"), currency="USD", starting_balance=1000.0, realized_pnl=55.0, trade_count=60)
    _seed_portfolio(PortfolioStateStore("betfair_core"), currency="EUR", starting_balance=1000.0, realized_pnl=30.0, trade_count=30)
    _seed_portfolio(PortfolioStateStore("polymarket_quantum_fold"), currency="USD", starting_balance=1000.0, realized_pnl=58.0, trade_count=60)


def _strong_experiment_run_factory(factory_root: Path):
    def _run(self, *, lineage, genome, experiment):
        package_dir = factory_root / "packages" / lineage.lineage_id
        package_dir.mkdir(parents=True, exist_ok=True)
        package_path = package_dir / "package.json"
        package_payload = {
            "artifact_summary": {
                "run_id": f"{lineage.lineage_id}-run",
                "package_path": str(package_path),
                "strict_gate_pass": True,
            },
            "files": {},
        }
        package_path.write_text(json.dumps(package_payload), encoding="utf-8")
        bundles = [
            EvaluationBundle(
                evaluation_id=f"{lineage.lineage_id}:walkforward",
                lineage_id=lineage.lineage_id,
                family_id=lineage.family_id,
                stage="walkforward",
                source="test_strong_artifact",
                monthly_roi_pct=7.8,
                max_drawdown_pct=2.4,
                slippage_headroom_pct=1.2,
                calibration_lift_abs=0.03,
                turnover=0.55,
                capacity_score=0.72,
                failure_rate=0.02,
                regime_robustness=0.76,
                baseline_beaten_windows=4,
                stress_positive=True,
                trade_count=80,
                settled_count=80,
                paper_days=30,
                net_pnl=78.0,
                notes=[f"package_path={package_path}"],
            ),
            EvaluationBundle(
                evaluation_id=f"{lineage.lineage_id}:stress",
                lineage_id=lineage.lineage_id,
                family_id=lineage.family_id,
                stage="stress",
                source="test_strong_artifact",
                monthly_roi_pct=7.1,
                max_drawdown_pct=2.8,
                slippage_headroom_pct=0.9,
                calibration_lift_abs=0.028,
                turnover=0.52,
                capacity_score=0.7,
                failure_rate=0.02,
                regime_robustness=0.74,
                baseline_beaten_windows=4,
                stress_positive=True,
                trade_count=80,
                settled_count=80,
                paper_days=30,
                net_pnl=71.0,
                notes=[f"package_path={package_path}"],
            ),
        ]
        return {
            "mode": "test",
            "bundles": bundles,
            "artifact_summary": dict(package_payload["artifact_summary"]),
        }

    return _run


def test_factory_orchestrator_publishes_pending_manifests_and_promotes_after_approval(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution runners.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(FactoryExperimentRunner, "run", _strong_experiment_run_factory(factory_root))
    _prepare_factory_inputs(project_root)

    orchestrator = FactoryOrchestrator(project_root)

    first_state = orchestrator.run_cycle()

    assert first_state["research_summary"]["family_count"] >= 5
    assert first_state["manifests"]["pending"]
    assert first_state["manifests"]["live_loadable"] == []
    assert "escalation_candidates" in first_state["operator_signals"]
    assert "positive_models" in first_state["operator_signals"]
    assert "maintenance_queue" in first_state["operator_signals"]
    assert any(lineage["current_stage"] == "live_ready" for lineage in first_state["lineages"])
    assert any("budget_weight_pct" in lineage for lineage in first_state["lineages"])
    assert any(manifest["artifact_refs"].get("package") for manifest in first_state["manifests"]["pending"])

    selected_manifest = next(
        manifest
        for manifest in first_state["manifests"]["pending"]
        if manifest["artifact_refs"].get("package")
    )
    pending_manifest_id = selected_manifest["manifest_id"]
    approved = orchestrator.registry.approve_manifest(pending_manifest_id, approved_by="operator")

    assert approved is not None
    assert approved.is_live_loadable() is True

    second_state = orchestrator.run_cycle()

    assert second_state["manifests"]["live_loadable"]
    assert any(lineage["current_stage"] == "approved_live" for lineage in second_state["lineages"])

    refs = live_manifest_refs_for_portfolio(selected_manifest["portfolio_targets"][0])

    assert refs
    assert any(item["manifest_id"] == pending_manifest_id for item in refs)
    assert refs[0]["package"]["package_found"] is True
    assert refs[0]["package_payload"]


def test_factory_orchestrator_creates_bounded_challengers_and_queue_entries(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution data providers.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    _prepare_factory_inputs(project_root)

    orchestrator = FactoryOrchestrator(project_root)

    state = orchestrator.run_cycle()

    challengers = [
        row for row in state["lineages"]
        if row["role"] in {"shadow_challenger", "paper_challenger"} and row.get("parent_lineage_id") is not None
    ]
    prediction_lineages = [
        row for row in state["lineages"] if row["family_id"] == "betfair_prediction_value_league"
    ]
    funding_lineages = [
        row for row in state["lineages"] if row["family_id"] == "binance_funding_contrarian"
    ]

    assert state["research_summary"]["challenge_count"] >= 4
    assert state["research_summary"]["artifact_backed_lineage_count"] >= 2
    assert state["research_summary"]["agent_generated_lineage_count"] >= 1
    assert "positive_models" in state["operator_signals"]
    assert "maintenance_queue" in state["operator_signals"]
    assert "maintenance_queue_count" in state["research_summary"]
    assert challengers
    assert state["queue"]
    assert all(item["family_id"] for item in state["queue"])
    assert prediction_lineages
    assert funding_lineages
    assert any(lineage["latest_artifact_package"] for lineage in prediction_lineages)
    assert any(lineage["latest_artifact_package"] for lineage in funding_lineages)
    assert all("curated_family_rank" in row for row in state["lineages"])
    assert all("curated_rankings" in row for row in state["families"])

    challenger = challengers[0]
    genome = orchestrator.registry.load_genome(challenger["lineage_id"])
    hypothesis = orchestrator.registry.load_hypothesis(challenger["lineage_id"])

    assert challenger["parent_lineage_id"] is not None
    assert genome is not None
    assert hypothesis is not None
    assert challenger["max_tweaks"] == 2
    assert challenger["lead_agent_role"]
    assert challenger["hypothesis_origin"] == "scientific_agent_collective"
    assert challenger["creation_kind"] in {"mutation", "new_model"}
    assert hypothesis.origin == "scientific_agent_collective"
    assert hypothesis.collaborating_agent_roles
    assert genome.parameters["selected_horizon_seconds"] in genome.mutation_bounds.horizons_seconds
    assert genome.parameters["selected_feature_subset"] in genome.mutation_bounds.feature_subsets
    assert genome.parameters["selected_model_class"] in genome.mutation_bounds.model_classes
    min_edge_bounds = genome.mutation_bounds.execution_thresholds["min_edge"]
    assert min_edge_bounds[0] <= genome.parameters["selected_min_edge"] <= min_edge_bounds[-1]

    prediction_experiment = orchestrator.registry.load_experiment("betfair_prediction_value_league:champion")
    latest_run = dict((prediction_experiment.expected_outputs or {}).get("latest_run") or {})
    assert latest_run["mode"] == "prediction_walkforward"
    assert Path(latest_run["package_path"]).exists()

    funding_experiment = orchestrator.registry.load_experiment("binance_funding_contrarian:champion")
    funding_run = dict((funding_experiment.expected_outputs or {}).get("latest_run") or {})
    assert funding_run["mode"] == "funding_contrarian"
    assert Path(funding_run["package_path"]).exists()
    assert funding_run["retrain_action"]
    package_payload = json.loads(Path(funding_run["package_path"]).read_text(encoding="utf-8"))
    assert package_payload["files"]["retrain"]
    assert Path(package_payload["files"]["retrain"]).exists()
    assert all("promotion_scorecard" in row for row in state["lineages"])


def test_challenger_mix_policy_defaults_to_four_mutations_then_one_new_model(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_CHALLENGER_MUTATION_PCT", 80)
    monkeypatch.setattr(config, "FACTORY_CHALLENGER_NEW_MODEL_PCT", 20)

    orchestrator = FactoryOrchestrator(project_root)
    sequence = [orchestrator._proposal_creation_kind([object()] * count) for count in range(5)]

    assert sequence == ["mutation", "mutation", "mutation", "mutation", "new_model"]


def test_scheduled_review_reason_requires_mature_paper_evidence(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_AGENT_REVIEW_ENABLED", True)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert lineage is not None
    lineage.current_stage = "paper"

    immature = EvaluationBundle(
        evaluation_id="immature",
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        stage="paper",
        source="test",
        trade_count=20,
        settled_count=20,
        paper_days=5,
    )
    mature = EvaluationBundle(
        evaluation_id="mature",
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        stage="paper",
        source="test",
        trade_count=60,
        settled_count=60,
        paper_days=30,
    )

    assert orchestrator._scheduled_review_reason(family, lineage, immature, {"issue_codes": []}) == "first_assessment_review"
    assert orchestrator._scheduled_review_reason(family, lineage, mature, {"issue_codes": []}) == "initial_maturity_review"


def test_debug_agent_surfaces_human_resolution_requirements(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_DEBUG_AGENT_ENABLED", True)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert lineage is not None
    lineage.current_stage = "paper"
    orchestrator.registry.save_lineage(lineage)

    evidence = {
        "health_status": "critical",
        "issue_codes": ["runtime_error", "readiness_blocked"],
        "error": "Invalid API-key, IP, or permissions for action",
        "blockers": ["missing_credentials"],
        "issues": [{"detail": "Invalid API-key, IP, or permissions for action"}],
    }
    monkeypatch.setattr(orchestrator, "_execution_validation_snapshot", lambda _lineage: evidence)

    def _fake_debug(**_kwargs):
        return AgentRunResult(
            run_id="runtime_debug_review_123",
            task_type="runtime_debug_review",
            model_class="hard_research",
            provider="codex",
            model="gpt-5.2-codex",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id="binance_funding_contrarian",
            lineage_id=lineage.lineage_id,
            duration_ms=90,
            result_payload={
                "summary": "Binance credentials are invalid for the paper runner.",
                "suspected_root_cause": "Credential or permission problem.",
                "bug_category": "credentials_or_permissions",
                "severity": "critical",
                "recommended_actions": ["Repair the API credentials and rerun the portfolio."],
                "safe_auto_actions": ["Keep the model in paper/debug mode until credentials are fixed."],
                "requires_human": True,
                "human_action": "Repair Binance API credentials or permissions for the affected account.",
                "human_owner": "operator",
                "should_pause_lineage": True,
            },
            artifact_path=str(factory_root / "agent_runs" / "runtime_debug_review_123.json"),
        )

    monkeypatch.setattr(orchestrator.agent_runtime, "diagnose_bug", _fake_debug)
    recent_actions: list[str] = []
    latest_bundle = EvaluationBundle(
        evaluation_id="paper",
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        stage="paper",
        source="test",
        trade_count=60,
        settled_count=60,
        paper_days=20,
    )

    orchestrator._maybe_run_debug_agent(
        family,
        lineage,
        {"paper": latest_bundle},
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(lineage.lineage_id)
    assert refreshed is not None
    assert refreshed.last_debug_review_status == "completed"
    assert refreshed.last_debug_requires_human is True
    assert refreshed.last_debug_bug_category == "credentials_or_permissions"
    assert "credentials" in str(refreshed.last_debug_human_action or "").lower()
    signals = orchestrator._operator_signals(
        [
            {
                "family_id": lineage.family_id,
                "lineage_id": lineage.lineage_id,
                "current_stage": refreshed.current_stage,
                "execution_health_status": evidence["health_status"],
                "execution_issue_codes": evidence["issue_codes"],
                "last_debug_requires_human": refreshed.last_debug_requires_human,
                "last_debug_bug_category": refreshed.last_debug_bug_category,
                "last_debug_human_action": refreshed.last_debug_human_action,
                "last_debug_summary": refreshed.last_debug_summary,
                "last_debug_review_artifact_path": refreshed.last_debug_review_artifact_path,
                "last_debug_review_at": refreshed.last_debug_review_at,
                "curated_paper_closed_trade_count": 0,
                "trade_count": 0,
                "monthly_roi_pct": 0.0,
                "paper_days": 20,
                "strict_gate_pass": False,
                "curated_family_rank": None,
            }
        ]
    )
    assert signals["human_action_required"]
    assert "credentials" in signals["human_action_required"][0]["human_action"].lower()


def test_scheduled_agent_review_drives_maintenance_actions(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", True)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert lineage is not None
    lineage.current_stage = "paper"
    orchestrator.registry.save_lineage(lineage)

    def _fake_review(**_kwargs):
        return AgentRunResult(
            run_id="post_eval_critique_123",
            task_type="post_eval_critique",
            model_class="deep_review",
            provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id=lineage.family_id,
            lineage_id=lineage.lineage_id,
            duration_ms=90,
            result_payload={
                "summary": "The incumbent is stalled and should be replaced by fresher challengers.",
                "risks": ["stale execution evidence"],
                "next_tests": ["launch two new challengers against the same family"],
                "maintenance_action": "replace",
                "maintenance_reason": "Stalled evidence and weak ranking gap versus the family leader.",
                "requires_retrain": False,
                "requires_new_challenger": True,
            },
            artifact_path=str(factory_root / "agent_runs" / "post_eval_critique_123.json"),
        )

    monkeypatch.setattr(orchestrator.agent_runtime, "critique_post_evaluation", _fake_review)
    bundle = EvaluationBundle(
        evaluation_id="paper",
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        stage="paper",
        source="test",
        trade_count=60,
        settled_count=60,
        paper_days=20,
    )
    recent_actions: list[str] = []

    orchestrator._maybe_run_scheduled_agent_review(
        family,
        lineage,
        {"paper": bundle},
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(lineage.lineage_id)
    assert refreshed is not None
    assert refreshed.last_agent_review_status == "completed"
    assert refreshed.last_agent_review_action == "replace"
    assert refreshed.iteration_status == "review_requested_replace"
    assert "review_replace_requested" in refreshed.blockers
    assert refreshed.loss_streak >= refreshed.max_tweaks
    experiment = orchestrator.registry.load_experiment(lineage.lineage_id)
    expected = dict((experiment.expected_outputs or {}).get("scheduled_agent_review") or {})
    assert expected["maintenance_action"] == "replace"
    assert expected["requires_new_challenger"] is True


def test_run_experiment_injects_review_maintenance_request(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    lineage.last_agent_review_status = "completed"
    lineage.last_agent_review_action = "retrain"
    lineage.last_agent_review_summary = "Refresh incumbent artifacts before keeping this model live."
    orchestrator.registry.save_lineage(lineage)

    captured: dict[str, object] = {}

    def _fake_run(*, lineage, genome, experiment):
        captured["inputs"] = dict(experiment.inputs or {})
        return {"mode": "test", "bundles": [], "artifact_summary": None}

    monkeypatch.setattr(orchestrator.experiment_runner, "run", _fake_run)

    orchestrator._run_experiment(lineage)

    inputs = dict(captured["inputs"] or {})
    assert "execution_retrain_context" in inputs
    assert inputs["maintenance_request"]["action"] == "retrain"
    assert inputs["maintenance_request"]["source"] == "agent_review"


def test_maintenance_resolution_review_updates_lineage_and_expected_outputs(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "binance_funding_contrarian")

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert lineage is not None
    lineage.current_stage = "paper"
    orchestrator.registry.save_lineage(lineage)

    def _fake_evidence(_lineage):
        return {
            "health_status": "critical",
            "issue_codes": ["negative_paper_roi", "poor_win_rate"],
            "recommendation_context": ["replace the incumbent now"],
        }

    def _fake_maintenance_review(**_kwargs):
        return AgentRunResult(
            run_id="maintenance_resolution_review_123",
            task_type="maintenance_resolution_review",
            model_class="hard_research",
            provider="codex",
            model="gpt-5.2-codex",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id=lineage.family_id,
            lineage_id=lineage.lineage_id,
            duration_ms=120,
            result_payload={
                "summary": "Current paper evidence is weak enough that challenger replacement pressure should dominate.",
                "maintenance_action": "replace",
                "maintenance_reason": "Negative ROI and weak win rate under live paper evidence.",
                "next_steps": ["launch a fresher challenger", "deprioritize incumbent capital"],
                "requires_new_challenger": True,
                "multi_agent_trace": {
                    "strategy": "parallel_panel",
                    "roles": [
                        {"role": "maintenance_triager", "finding": "Execution evidence is materially weak."},
                        {"role": "replacement_planner", "finding": "Replacement pressure is justified now."},
                    ],
                    "synthesis": "Escalate to replace.",
                },
            },
            artifact_path=str(factory_root / "agent_runs" / "maintenance_resolution_review_123.json"),
        )

    monkeypatch.setattr(orchestrator, "_execution_validation_snapshot", _fake_evidence)
    monkeypatch.setattr(orchestrator.agent_runtime, "resolve_maintenance_item", _fake_maintenance_review)

    recent_actions: list[str] = []
    orchestrator._maybe_run_maintenance_resolution_agent(
        family,
        lineage,
        {"paper": EvaluationBundle(
            evaluation_id="paper",
            lineage_id=lineage.lineage_id,
            family_id=lineage.family_id,
            stage="paper",
            source="test",
            trade_count=60,
            settled_count=60,
            paper_days=10,
        )},
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(lineage.lineage_id)
    assert refreshed is not None
    assert refreshed.last_maintenance_review_status == "completed"
    assert refreshed.last_maintenance_review_action == "replace"
    assert refreshed.iteration_status == "review_requested_replace"
    experiment = orchestrator.registry.load_experiment(lineage.lineage_id)
    expected = dict((experiment.expected_outputs or {}).get("maintenance_resolution_review") or {})
    assert expected["maintenance_action"] == "replace"
    assert expected["provider"] == "codex"


def test_maintenance_request_prefers_completed_maintenance_review(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    lineage.last_maintenance_review_status = "completed"
    lineage.last_maintenance_review_action = "replace"
    lineage.last_maintenance_review_summary = "Maintenance review says the incumbent should be replaced."

    request = orchestrator._maintenance_request(
        lineage,
        {"health_status": "warning", "issue_codes": ["negative_paper_roi"]},
    )

    assert request is not None
    assert request["action"] == "replace"
    assert request["source"] == "maintenance_review"


def test_run_experiment_injects_debug_agent_maintenance_request(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    lineage.last_debug_review_status = "completed"
    lineage.last_debug_requires_human = False
    lineage.last_debug_bug_category = "feature_schema"
    lineage.last_debug_summary = "Live inference schema drift needs a runtime fix and temporary pause."
    lineage.last_debug_safe_auto_actions = ["Patch runtime feature builder", "Pause lineage until fixed"]
    lineage.last_debug_should_pause_lineage = True
    lineage.last_debug_severity = "critical"
    orchestrator.registry.save_lineage(lineage)

    captured: dict[str, object] = {}

    def _fake_run(*, lineage, genome, experiment):
        captured["inputs"] = dict(experiment.inputs or {})
        return {"mode": "test", "bundles": [], "artifact_summary": None}

    monkeypatch.setattr(orchestrator.experiment_runner, "run", _fake_run)

    orchestrator._run_experiment(lineage)

    inputs = dict(captured["inputs"] or {})
    assert inputs["maintenance_request"]["action"] == "rework"
    assert inputs["maintenance_request"]["source"] == "debug_agent"
    assert inputs["maintenance_request"]["should_pause_lineage"] is True


def test_run_experiment_injects_latest_operator_action_context(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    action = orchestrator.registry.open_operator_action(
        action_key="winner_review:binance_funding_contrarian:champion:approve_real_trading_review",
        family_id=lineage.family_id,
        lineage_id=lineage.lineage_id,
        signal_type="winner_review",
        requested_action="approve_real_trading_review",
        summary="Review this winner candidate.",
        context={"source": "test"},
    )
    orchestrator.registry.resolve_operator_action(
        action.action_id,
        decision="instruct",
        resolved_by="ben",
        note="Use the strongest critique path.",
        instruction="Have the review agents focus on live slippage and maturity, not backtest only.",
    )

    captured: dict[str, object] = {}

    def _fake_run(*, lineage, genome, experiment):
        captured["inputs"] = dict(experiment.inputs or {})
        return {"mode": "test", "bundles": [], "artifact_summary": None}

    monkeypatch.setattr(orchestrator.experiment_runner, "run", _fake_run)

    orchestrator._run_experiment(lineage)

    inputs = dict(captured["inputs"] or {})
    assert inputs["operator_action_context"]["decision"] == "instruct"
    assert "live slippage and maturity" in inputs["operator_action_context"]["instruction"]


def test_retrain_payload_honors_maintenance_request(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    genome = orchestrator.registry.load_genome("binance_funding_contrarian:champion")
    experiment = orchestrator.registry.load_experiment("binance_funding_contrarian:champion")
    assert lineage is not None
    assert genome is not None
    assert experiment is not None

    experiment.inputs = {
        "execution_retrain_context": {"issue_codes": [], "recommendation_context": [], "health_status": "warning"},
        "maintenance_request": {
            "source": "agent_review",
            "action": "replace",
            "reason": "Review wants fresh challengers and an incumbent refresh.",
            "requires_new_challenger": True,
        },
    }

    payload = orchestrator.experiment_runner._build_retrain_payload(
        lineage=lineage,
        genome=genome,
        experiment=experiment,
        family_mode="funding",
        requested_model_class="logit",
        resolved_model_engine="logit",
        source_mode="test",
        source_paths=[],
        dataset_rows=123,
    )

    assert payload["retrain_triggered"] is True
    assert payload["retrain_action"] == "agent_requested_replace"
    assert payload["maintenance_request"]["action"] == "replace"


def test_maintenance_request_escalates_weak_execution_to_replace(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None

    request = orchestrator._maintenance_request(
        lineage,
        {
            "issue_codes": ["negative_paper_roi", "poor_win_rate"],
            "health_status": "warning",
        },
    )

    assert request is not None
    assert request["action"] == "replace"
    assert request["source"] == "execution_policy"


def test_maintenance_request_escalates_trainability_contract_breach_to_replace(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_TRAINABILITY_GRACE_HOURS", 6.0)

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None

    request = orchestrator._maintenance_request(
        lineage,
        {
            "issue_codes": ["untrainable_model"],
            "health_status": "warning",
            "runtime_age_hours": 7.5,
            "trainability_status": "blocked",
            "required_model_count": 2,
            "trainable_model_count": 0,
            "trained_model_count": 0,
            "blocked_models": ["xgb_selector", "regime_model"],
        },
    )

    assert request is not None
    assert request["action"] == "replace"
    assert request["source"] == "trainability_contract"
    assert request["requires_new_challenger"] is True


def test_maintenance_request_keeps_early_trainability_issue_in_retrain_mode(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_TRAINABILITY_GRACE_HOURS", 6.0)

    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None

    request = orchestrator._maintenance_request(
        lineage,
        {
            "issue_codes": ["untrainable_model"],
            "health_status": "warning",
            "runtime_age_hours": 1.5,
            "trainability_status": "warming_up",
            "required_model_count": 2,
            "trainable_model_count": 1,
            "trained_model_count": 0,
        },
    )

    assert request is not None
    assert request["action"] == "retrain"
    assert request["source"] == "execution_policy"


def test_operator_signals_require_mature_independent_evidence_for_winner_escalation(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "current_stage": "live_ready",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "monthly_roi_pct": 23.0,
                "research_monthly_roi_pct": 23.0,
                "trade_count": 12,
                "research_trade_count": 12,
                "paper_days": 12,
                "live_paper_target_portfolio_id": "contrarian_legacy",
                "live_paper_roi_pct": 23.0,
                "live_paper_trade_count": 12,
                "curated_family_rank": 1,
                "curated_target_portfolio_id": None,
                "curated_paper_closed_trade_count": 0,
            },
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:2",
                "current_stage": "live_ready",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "monthly_roi_pct": 18.0,
                "research_monthly_roi_pct": 18.0,
                "trade_count": 60,
                "research_trade_count": 60,
                "paper_days": 30,
                "live_paper_target_portfolio_id": "",
                "live_paper_roi_pct": 0.0,
                "live_paper_trade_count": 0,
                "curated_family_rank": 1,
                "curated_paper_roi_pct": 18.0,
                "curated_target_portfolio_id": "contrarian_legacy",
                "curated_paper_closed_trade_count": 60,
            },
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:3",
                "current_stage": "live_ready",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "monthly_roi_pct": 11.0,
                "research_monthly_roi_pct": 11.0,
                "trade_count": 60,
                "research_trade_count": 60,
                "paper_days": 30,
                "live_paper_target_portfolio_id": "contrarian_legacy",
                "live_paper_roi_pct": 11.0,
                "live_paper_trade_count": 60,
                "curated_family_rank": 1,
                "curated_target_portfolio_id": None,
                "curated_paper_closed_trade_count": 0,
            },
        ]
    )

    assert len(signals["positive_models"]) == 2
    assert len(signals["research_positive_models"]) == 3
    assert not any(item["lineage_id"].endswith(":1") for item in signals["potential_winners"])
    assert not any(item["lineage_id"].endswith(":2") for item in signals["potential_winners"])
    assert any(item["lineage_id"].endswith(":3") for item in signals["potential_winners"])
    assert len(signals["escalation_candidates"]) == 1
    assert signals["escalation_candidates"][0]["lineage_id"].endswith(":3")


def test_operator_signals_include_priority_sorted_maintenance_queue(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:champion",
                "current_stage": "paper",
                "execution_health_status": "warning",
                "paper_days": 20,
                "trade_count": 40,
                "monthly_roi_pct": 4.0,
                "maintenance_request_action": "retrain",
                "maintenance_request_reason": "Training stalled",
                "maintenance_request_source": "execution_policy",
                "iteration_status": "review_requested_retrain",
            },
            {
                "family_id": "betfair_prediction_value_league",
                "lineage_id": "betfair_prediction_value_league:champion",
                "current_stage": "paper",
                "execution_health_status": "critical",
                "paper_days": 5,
                "trade_count": 0,
                "monthly_roi_pct": 0.0,
                "maintenance_request_action": "human_action_required",
                "maintenance_request_reason": "Fix venue restriction",
                "maintenance_request_source": "debug_agent",
                "last_debug_requires_human": True,
                "last_debug_human_action": "Fix venue restriction",
                "last_debug_bug_category": "venue_restriction",
                "iteration_status": "awaiting_operator_fix",
            },
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:1",
                "current_stage": "paper",
                "execution_health_status": "warning",
                "paper_days": 30,
                "trade_count": 60,
                "monthly_roi_pct": -2.0,
                "agent_review_due": True,
                "agent_review_due_reason": "scheduled_interval_review",
                "iteration_status": "tweaked",
            },
        ]
    )

    queue = signals["maintenance_queue"]
    assert any(item["action"] == "human_action_required" for item in queue)
    assert not any(item["action"] == "retrain" for item in queue)
    assert not any(item["action"] == "review_due" for item in queue)
    assert any(item["action"] == "family_autopilot" for item in queue)
    assert queue[0]["requires_human"] is True


def test_operator_signals_skip_inactive_lineages_in_maintenance_queue(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:retired:1",
                "active": False,
                "current_stage": "paper",
                "execution_health_status": "critical",
                "execution_issue_codes": ["negative_paper_roi"],
                "execution_validation": {"health_status": "critical", "issue_codes": ["negative_paper_roi"]},
                "maintenance_request_action": "replace",
                "maintenance_request_reason": "bad model",
                "maintenance_request_source": "execution_policy",
                "trade_count": 10,
                "paper_days": 1,
                "live_paper_trade_count": 10,
                "live_paper_days": 1,
                "live_paper_roi_pct": -5.0,
                "monthly_roi_pct": -5.0,
                "iteration_status": "retired",
            }
        ],
        family_summaries=[],
    )

    assert not any(
        item.get("lineage_id") == "polymarket_cross_venue:retired:1" and item.get("action") == "replace"
        for item in signals["maintenance_queue"]
    )
    assert signals["maintenance_queue"] == []


def test_operator_signals_suppress_recently_reviewed_duplicate_maintenance_action(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS", 24)
    orchestrator = FactoryOrchestrator(project_root)

    now = datetime.now(timezone.utc).isoformat()
    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:1",
                "active": True,
                "current_stage": "paper",
                "execution_health_status": "warning",
                "maintenance_request_action": "replace",
                "maintenance_request_reason": "Weak incumbent",
                "maintenance_request_source": "execution_policy",
                "last_maintenance_review_at": now,
                "last_maintenance_review_status": "completed",
                "last_maintenance_review_action": "replace",
                "trade_count": 20,
                "paper_days": 3,
                "live_paper_trade_count": 20,
                "live_paper_days": 3,
                "live_paper_roi_pct": -4.0,
                "monthly_roi_pct": -4.0,
                "iteration_status": "review_requested_replace",
            }
        ],
        family_summaries=[],
    )

    assert signals["maintenance_queue"] == []


def test_operator_signals_prefer_family_autopilot_over_redundant_lineage_replace_items(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY", 2)
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:champion",
                "current_stage": "paper",
                "runtime_lane_kind": "primary_incumbent",
                "execution_health_status": "warning",
                "execution_issue_codes": ["poor_win_rate"],
                "live_paper_trade_count": 90,
                "live_paper_roi_pct": 135.44,
                "live_paper_wins": 1,
                "live_paper_losses": 89,
                "maintenance_request_action": "replace",
                "maintenance_request_reason": "Weak profile",
                "maintenance_request_source": "execution_policy",
                "iteration_status": "review_requested_replace",
                "active": True,
            },
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:challenger:16",
                "current_stage": "paper",
                "execution_health_status": "warning",
                "maintenance_request_action": "replace",
                "maintenance_request_reason": "Weak profile",
                "maintenance_request_source": "execution_policy",
                "live_paper_trade_count": 90,
                "live_paper_roi_pct": 135.44,
                "iteration_status": "new_candidate",
                "active": True,
            },
        ],
        family_summaries=[
            {
                "family_id": "polymarket_cross_venue",
                "queue_stage": "paper",
                "primary_incumbent_lineage_id": "polymarket_cross_venue:champion",
                "isolated_challenger_lineage_id": "polymarket_cross_venue:challenger:16",
                "isolated_evidence_ready": False,
            }
        ],
    )

    queue = signals["maintenance_queue"]
    assert any(item.get("action") == "family_autopilot" for item in queue)
    assert not any(
        item.get("family_id") == "polymarket_cross_venue"
        and item.get("action") == "replace"
        and item.get("source") == "execution_policy"
        for item in queue
    )


def test_operator_signals_flag_isolated_challenger_sharing_incumbent_evidence(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:champion",
                "current_stage": "paper",
                "role": "champion",
                "runtime_lane_kind": "primary_incumbent",
                "live_paper_target_portfolio_id": "contrarian_legacy",
                "execution_health_status": "warning",
                "paper_days": 20,
                "trade_count": 40,
            },
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:1",
                "current_stage": "paper",
                "role": "paper_challenger",
                "runtime_lane_kind": "isolated_challenger",
                "live_paper_target_portfolio_id": "contrarian_legacy",
                "execution_health_status": "healthy",
                "paper_days": 12,
                "trade_count": 18,
                "iteration_status": "tweaked",
            },
        ]
    )

    isolate_item = next(item for item in signals["maintenance_queue"] if item["action"] == "isolate_evidence")
    assert isolate_item["family_id"] == "binance_funding_contrarian"
    assert isolate_item["lineage_id"] == "binance_funding_contrarian:challenger:1"
    assert isolate_item["source"] == "lane_policy"
    assert not any(item["lineage_id"] == "binance_funding_contrarian:challenger:1" for item in signals["potential_winners"])
    assert not any(item["lineage_id"] == "binance_funding_contrarian:challenger:1" for item in signals["escalation_candidates"])


def test_operator_signals_flag_stale_isolated_alias_lane(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0)
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_funding_contrarian",
                "lineage_id": "binance_funding_contrarian:challenger:9",
                "current_stage": "paper",
                "role": "paper_challenger",
                "runtime_lane_kind": "isolated_challenger",
                "iteration_status": "isolated_lane_active",
                "activation_status": "running",
                "alias_runner_running": True,
                "live_paper_trade_count": 0,
                "live_paper_realized_pnl": 0.0,
                "execution_issue_codes": [],
                "execution_validation": {"runtime_age_hours": 6.0, "has_execution_signal": False},
                "execution_health_status": "warning",
                "paper_days": 1,
                "trade_count": 0,
            },
        ]
    )

    isolate_item = next(item for item in signals["maintenance_queue"] if item["action"] == "isolate_evidence_stalled")
    assert isolate_item["family_id"] == "binance_funding_contrarian"
    assert isolate_item["lineage_id"] == "binance_funding_contrarian:challenger:9"
    assert isolate_item["source"] == "execution_bridge"


def test_operator_signals_request_preparing_isolated_lane_when_preferred_challenger_is_not_runnable(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", 2.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", 7)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", 10)
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:champion",
                "current_stage": "paper",
                "role": "champion",
                "execution_health_status": "warning",
                "strict_gate_pass": False,
                "paper_days": 12,
                "trade_count": 20,
                "curated_family_rank": 2,
                "curated_ranking_score": -10.0,
                "fitness_score": -12.0,
                "monthly_roi_pct": -2.0,
                "iteration_status": "champion",
            },
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:8",
                "current_stage": "walkforward",
                "role": "paper_challenger",
                "execution_health_status": "healthy",
                "strict_gate_pass": False,
                "paper_days": 10,
                "trade_count": 18,
                "curated_family_rank": 1,
                "curated_ranking_score": -5.0,
                "fitness_score": -4.0,
                "monthly_roi_pct": 1.0,
                "iteration_status": "tweaked",
            },
        ]
    )

    item = next(item for item in signals["maintenance_queue"] if item["action"] == "prepare_isolated_lane")
    assert item["family_id"] == "binance_cascade_regime"
    assert item["lineage_id"] == "binance_cascade_regime:challenger:8"
    assert item["lane_reason"] == "challenger_materially_stronger"
    assert item["source"] == "lane_policy"


def test_family_autopilot_plan_marks_weak_family_with_replace_and_isolate_actions(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    plan = orchestrator._family_autopilot_plan(
        "polymarket_cross_venue",
        [
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:champion",
                "current_stage": "paper",
                "runtime_lane_kind": "primary_incumbent",
                "execution_issue_codes": ["poor_win_rate"],
                "execution_health_status": "warning",
                "live_paper_trade_count": 90,
                "live_paper_roi_pct": 135.44,
                "live_paper_wins": 1,
                "live_paper_losses": 89,
                "maintenance_request_action": "replace",
                "active": True,
            }
        ],
        family_summary={
            "family_id": "polymarket_cross_venue",
            "queue_stage": "paper",
            "primary_incumbent_lineage_id": "polymarket_cross_venue:champion",
            "isolated_challenger_lineage_id": "polymarket_cross_venue:challenger:11",
            "isolated_evidence_ready": False,
        },
    )

    assert plan["weak_family"] is True
    assert "replace" in plan["autopilot_actions"]
    assert "isolate_evidence" in plan["autopilot_actions"]


def test_family_autopilot_maintenance_request_prefers_replace_then_retrain_then_rework(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)

    request = orchestrator._family_autopilot_maintenance_request(
        {"autopilot_actions": ["replace", "retrain"], "autopilot_reason": "weak family"}
    )
    assert request is not None
    assert request["action"] == "replace"

    request = orchestrator._family_autopilot_maintenance_request(
        {"autopilot_actions": ["retrain"], "autopilot_reason": "stale training"}
    )
    assert request is not None
    assert request["action"] == "retrain"

    request = orchestrator._family_autopilot_maintenance_request(
        {"autopilot_actions": ["isolate_evidence"], "autopilot_reason": "shared evidence"}
    )
    assert request is not None
    assert request["action"] == "rework"


def test_operator_signals_include_family_autopilot_items_for_weak_families(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:champion",
                "current_stage": "paper",
                "runtime_lane_kind": "primary_incumbent",
                "execution_health_status": "warning",
                "execution_issue_codes": ["negative_paper_roi", "poor_win_rate"],
                "live_paper_trade_count": 22,
                "live_paper_roi_pct": -4.0,
                "live_paper_wins": 7,
                "live_paper_losses": 15,
                "active": True,
            },
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:8",
                "current_stage": "shadow",
                "runtime_lane_kind": "isolated_challenger",
                "execution_health_status": "warning",
                "live_paper_trade_count": 0,
                "live_paper_roi_pct": 0.0,
                "live_paper_wins": 0,
                "live_paper_losses": 0,
                "iteration_status": "prepare_isolated_lane",
                "active": True,
            },
        ],
        family_summaries=[
            {
                "family_id": "binance_cascade_regime",
                "queue_stage": "paper",
                "primary_incumbent_lineage_id": "binance_cascade_regime:champion",
                "isolated_challenger_lineage_id": "binance_cascade_regime:challenger:8",
                "prepared_isolated_lane_lineage_id": "binance_cascade_regime:challenger:8",
                "isolated_evidence_ready": False,
            }
        ],
    )

    weak_family = next(item for item in signals["weak_families"] if item["family_id"] == "binance_cascade_regime")
    assert weak_family["autopilot_status"] == "autopilot_active"
    assert "replace" in weak_family["autopilot_actions"]
    assert "isolate_evidence" in weak_family["autopilot_actions"]

    queue_item = next(item for item in signals["maintenance_queue"] if item["action"] == "family_autopilot")
    assert queue_item["family_id"] == "binance_cascade_regime"
    assert queue_item["source"] == "family_autopilot"
    assert "replace" in queue_item["recommended_actions"]


def test_operator_signals_queue_positive_challenger_for_first_paper_read(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", 1.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", 25)
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:champion",
                "current_stage": "paper",
                "role": "champion",
                "execution_health_status": "healthy",
                "strict_gate_pass": True,
                "live_paper_trade_count": 18,
                "live_paper_days": 4,
                "active": True,
            },
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:9",
                "current_stage": "shadow",
                "role": "paper_challenger",
                "execution_health_status": "warning",
                "monthly_roi_pct": 2.8,
                "trade_count": 38,
                "live_paper_trade_count": 0,
                "live_paper_days": 0,
                "iteration_status": "new_candidate",
                "active": True,
            },
        ],
        family_summaries=[],
    )

    queue = signals["paper_qualification_queue"]
    assert len(queue) == 1
    assert queue[0]["family_id"] == "binance_cascade_regime"
    assert queue[0]["lineage_id"] == "binance_cascade_regime:challenger:9"
    assert queue[0]["lane_reason"] == "paper_qualification_needed"


def test_operator_signals_queue_positive_challenger_when_incumbent_trade_is_stalled(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_ROI_PCT", 1.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_FIRST_READ_MIN_RESEARCH_TRADES", 25)
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:champion",
                "current_stage": "paper",
                "role": "champion",
                "execution_health_status": "warning",
                "execution_issue_codes": ["trade_stalled", "stalled_model"],
                "strict_gate_pass": True,
                "live_paper_trade_count": 7,
                "live_paper_days": 2,
                "active": True,
            },
            {
                "family_id": "binance_cascade_regime",
                "lineage_id": "binance_cascade_regime:challenger:10",
                "current_stage": "shadow",
                "role": "paper_challenger",
                "execution_health_status": "warning",
                "monthly_roi_pct": 2.4,
                "trade_count": 42,
                "live_paper_trade_count": 0,
                "live_paper_days": 0,
                "iteration_status": "new_candidate",
                "active": True,
            },
        ],
        family_summaries=[],
    )

    queue = signals["paper_qualification_queue"]
    assert len(queue) == 1
    assert queue[0]["family_id"] == "binance_cascade_regime"
    assert queue[0]["lineage_id"] == "binance_cascade_regime:challenger:10"
    assert queue[0]["lane_reason"] == "incumbent_trade_stalled"


def test_operator_signals_use_live_paper_days_for_live_positive_model_assessment(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:challenger:1",
                "current_stage": "live_ready",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "monthly_roi_pct": 10.0,
                "research_monthly_roi_pct": 10.0,
                "trade_count": 90,
                "research_trade_count": 90,
                "paper_days": 30,
                "live_paper_days": 1,
                "live_paper_target_portfolio_id": "polymarket_quantum_fold",
                "live_paper_roi_pct": 135.44,
                "live_paper_trade_count": 90,
                "curated_family_rank": 1,
                "curated_target_portfolio_id": None,
                "curated_paper_closed_trade_count": 0,
            },
        ]
    )

    positive = signals["positive_models"][0]
    assert positive["paper_days"] == 1
    assert positive["assessment_complete"] is False
    assert signals["potential_winners"] == []
    assert signals["escalation_candidates"] == []


def test_operator_signals_mark_positive_models_with_shared_evidence_and_replacement_pressure(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._operator_signals(
        [
            {
                "family_id": "polymarket_cross_venue",
                "lineage_id": "polymarket_cross_venue:challenger:1",
                "current_stage": "live_ready",
                "strict_gate_pass": True,
                "execution_health_status": "warning",
                "monthly_roi_pct": 18.0,
                "research_monthly_roi_pct": 18.0,
                "trade_count": 18,
                "research_trade_count": 18,
                "paper_days": 8,
                "live_paper_days": 2,
                "live_paper_target_portfolio_id": "polymarket_quantum_fold",
                "live_paper_roi_pct": 18.0,
                "live_paper_trade_count": 18,
                "runtime_lane_kind": "isolated_challenger",
                "runtime_target_portfolio": "polymarket_quantum_fold",
                "canonical_target_portfolio": "polymarket_quantum_fold",
                "maintenance_request_action": "replace",
                "iteration_status": "review_requested_replace",
                "curated_family_rank": 1,
                "manifest_id": "manifest_1",
            }
        ]
    )

    assert signals["positive_models"]
    row = signals["positive_models"][0]
    assert row["independent_live_evidence"] is False
    assert row["shared_evidence_risk"] is True
    assert row["replacement_pressure"] is True
    assert row["replacement_pressure_reason"] in {"replace", "review_requested_replace"}


def test_maintenance_request_retires_persistent_stalled_model_after_tweak_budget(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_STALLED_MODEL_HOURS", 8)
    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    lineage.tweak_count = lineage.max_tweaks

    request = orchestrator._maintenance_request(
        lineage,
        {
            "issue_codes": ["stalled_model"],
            "runtime_age_hours": 9.0,
            "health_status": "warning",
        },
    )

    assert request is not None
    assert request["action"] == "retire"
    assert request["retirement_reason"] == "stalled_model_after_tweaks"


def test_retire_or_update_lineages_retires_persistent_stalled_lineage_after_tweak_budget(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_STALLED_MODEL_HOURS", 8)
    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=22,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.tweak_count = challenger.max_tweaks
    orchestrator.registry.save_lineage(challenger)

    ranked_rows = [
        {
            "lineage_id": champion.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "execution_has_signal": True,
            "fitness_score": 2.0,
            "curated_ranking_score": 1.0,
            "monthly_roi_pct": 1.0,
            "execution_issue_codes": [],
        },
        {
            "lineage_id": challenger.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "execution_has_signal": True,
            "execution_health_status": "warning",
            "execution_issue_codes": ["stalled_model"],
            "execution_validation": {"health_status": "warning", "issue_codes": ["stalled_model"], "runtime_age_hours": 9.0, "targets": []},
            "fitness_score": 0.8,
            "curated_ranking_score": 0.5,
            "monthly_roi_pct": 0.2,
            "hard_vetoes": [],
        },
    ]
    recent_actions: list[str] = []

    orchestrator._retire_or_update_lineages(
        family,
        ranked_rows,
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed is not None
    assert refreshed.active is False
    assert refreshed.retirement_reason == "stalled_model_after_tweaks"
    assert any("Retired stalled lineage" in item for item in recent_actions)


def test_isolated_lane_preparation_reclassifies_best_challenger_as_paper_candidate(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_SCORE_GAP", 2.0)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_PAPER_DAYS", 7)
    monkeypatch.setattr(config, "FACTORY_RUNTIME_LANE_MIN_TRADE_COUNT", 10)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_cascade_regime")
    champion = orchestrator.registry.load_lineage("binance_cascade_regime:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=8,
        budget_bucket="adjacent",
    )
    challenger.current_stage = "walkforward"
    orchestrator.registry.save_lineage(challenger)

    ranked_rows = [
        {
            "family_id": family.family_id,
            "lineage_id": champion.lineage_id,
            "current_stage": "paper",
            "role": "champion",
            "execution_health_status": "warning",
            "strict_gate_pass": False,
            "paper_days": 12,
            "trade_count": 20,
            "curated_family_rank": 2,
            "curated_ranking_score": -10.0,
            "fitness_score": -12.0,
            "monthly_roi_pct": -2.0,
            "active": True,
        },
        {
            "family_id": family.family_id,
            "lineage_id": challenger.lineage_id,
            "current_stage": "walkforward",
            "role": "shadow_challenger",
            "execution_health_status": "healthy",
            "strict_gate_pass": False,
            "paper_days": 10,
            "trade_count": 18,
            "curated_family_rank": 1,
            "curated_ranking_score": -5.0,
            "fitness_score": -4.0,
            "monthly_roi_pct": 1.0,
            "active": True,
        },
    ]
    recent_actions: list[str] = []

    prepared_id = orchestrator._apply_isolated_lane_preparation(
        family,
        ranked_rows,
        recent_actions=recent_actions,
    )
    assert prepared_id == challenger.lineage_id

    orchestrator._reclassify_family(
        family,
        ranked_rows,
        prepared_challenger_id=prepared_id,
        recent_actions=recent_actions,
    )

    refreshed_family = orchestrator.registry.load_family(family.family_id)
    refreshed_challenger = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed_family is not None
    assert refreshed_challenger is not None
    assert challenger.lineage_id in refreshed_family.paper_challenger_ids
    assert refreshed_challenger.role == "paper_challenger"
    assert refreshed_challenger.iteration_status == "prepare_isolated_lane"


def test_activate_prepared_isolated_lane_promotes_runnable_challenger_to_shadow(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_cascade_regime")
    champion = orchestrator.registry.load_lineage("binance_cascade_regime:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=11,
        budget_bucket="adjacent",
    )
    challenger.current_stage = "walkforward"
    challenger.iteration_status = "prepare_isolated_lane"
    orchestrator.registry.save_lineage(challenger)

    monkeypatch.setattr(orchestrator, "_execution_validation_snapshot", lambda lineage: {"issue_codes": []})
    for stage in ("walkforward", "stress"):
        orchestrator.registry.save_evaluation(
            EvaluationBundle(
                evaluation_id=f"{challenger.lineage_id}:{stage}",
                lineage_id=challenger.lineage_id,
                family_id=challenger.family_id,
                stage=stage,
                source="test",
                monthly_roi_pct=5.0,
                max_drawdown_pct=2.0,
                slippage_headroom_pct=1.0,
                calibration_lift_abs=0.02,
                turnover=0.5,
                capacity_score=0.7,
                failure_rate=0.01,
                regime_robustness=0.7,
                baseline_beaten_windows=4,
                stress_positive=True,
                trade_count=40,
                settled_count=40,
                paper_days=20,
                net_pnl=20.0,
                hard_vetoes=[],
            )
        )

    activated = orchestrator._activate_prepared_isolated_lane(
        family,
        challenger.lineage_id,
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert activated == challenger.lineage_id
    assert refreshed is not None
    assert refreshed.current_stage == "shadow"


def test_activate_prepared_isolated_lane_respects_human_debug_blocker(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_cascade_regime")
    champion = orchestrator.registry.load_lineage("binance_cascade_regime:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=12,
        budget_bucket="adjacent",
    )
    challenger.current_stage = "stress"
    challenger.iteration_status = "prepare_isolated_lane"
    challenger.last_debug_requires_human = True
    orchestrator.registry.save_lineage(challenger)

    monkeypatch.setattr(orchestrator, "_execution_validation_snapshot", lambda lineage: {"issue_codes": []})
    for stage in ("walkforward", "stress"):
        orchestrator.registry.save_evaluation(
            EvaluationBundle(
                evaluation_id=f"{challenger.lineage_id}:{stage}",
                lineage_id=challenger.lineage_id,
                family_id=challenger.family_id,
                stage=stage,
                source="test",
                monthly_roi_pct=5.0,
                max_drawdown_pct=2.0,
                slippage_headroom_pct=1.0,
                calibration_lift_abs=0.02,
                turnover=0.5,
                capacity_score=0.7,
                failure_rate=0.01,
                regime_robustness=0.7,
                baseline_beaten_windows=4,
                stress_positive=True,
                trade_count=40,
                settled_count=40,
                paper_days=20,
                net_pnl=20.0,
                hard_vetoes=[],
            )
        )

    activated = orchestrator._activate_prepared_isolated_lane(
        family,
        challenger.lineage_id,
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert activated is None
    assert refreshed is not None
    assert refreshed.current_stage == "stress"


def test_retire_or_update_lineages_retires_failed_isolated_challenger_after_first_assessment(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", "0.0")

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=21,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.iteration_status = "isolated_lane_active"
    orchestrator.registry.save_lineage(challenger)

    ranked_rows = [
        {
            "lineage_id": champion.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "execution_has_signal": True,
            "fitness_score": 2.0,
            "curated_ranking_score": 1.0,
            "monthly_roi_pct": 1.0,
            "execution_issue_codes": [],
        },
        {
            "lineage_id": challenger.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "runtime_lane_kind": "isolated_challenger",
            "iteration_status": "isolated_lane_active",
            "first_assessment_complete": True,
            "execution_has_signal": True,
            "live_paper_roi_pct": -0.5,
            "execution_health_status": "warning",
            "execution_issue_codes": [],
            "fitness_score": 0.5,
            "curated_ranking_score": 0.25,
            "monthly_roi_pct": -0.5,
            "hard_vetoes": [],
            "execution_validation": {"health_status": "warning", "issue_codes": [], "targets": []},
        },
    ]
    recent_actions: list[str] = []

    orchestrator._retire_or_update_lineages(
        family,
        ranked_rows,
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    refreshed_family = orchestrator.registry.load_family(family.family_id)
    assert refreshed is not None
    assert refreshed_family is not None
    assert refreshed.active is False
    assert refreshed.retirement_reason == "isolated_lane_first_assessment_negative_roi"
    assert challenger.lineage_id in refreshed_family.retired_lineage_ids
    assert any("Retired isolated challenger" in item for item in recent_actions)


def test_retire_or_update_lineages_marks_positive_isolated_challenger_first_assessment_passed(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", "0.0")

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=22,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.iteration_status = "isolated_lane_active"
    orchestrator.registry.save_lineage(challenger)

    ranked_rows = [
        {
            "lineage_id": champion.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "champion",
            "execution_has_signal": True,
            "fitness_score": 2.0,
            "curated_ranking_score": 1.0,
            "monthly_roi_pct": 1.0,
            "execution_issue_codes": [],
        },
        {
            "lineage_id": challenger.lineage_id,
            "family_id": family.family_id,
            "active": True,
            "current_stage": "paper",
            "role": "paper_challenger",
            "runtime_lane_kind": "isolated_challenger",
            "iteration_status": "isolated_lane_active",
            "first_assessment_complete": True,
            "execution_has_signal": True,
            "live_paper_roi_pct": 1.25,
            "execution_health_status": "healthy",
            "execution_issue_codes": [],
            "fitness_score": 2.05,
            "curated_family_rank": 1,
            "curated_ranking_score": 1.5,
            "monthly_roi_pct": 1.25,
            "hard_vetoes": [],
            "execution_validation": {"health_status": "healthy", "issue_codes": [], "targets": []},
        },
    ]
    recent_actions: list[str] = []

    orchestrator._retire_or_update_lineages(
        family,
        ranked_rows,
        recent_actions=recent_actions,
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed is not None
    assert refreshed.active is True
    assert refreshed.iteration_status == "isolated_lane_first_assessment_passed"
    assert any("passed first paper assessment" in item for item in recent_actions)


def test_queue_priority_boosts_prepare_isolated_lane(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)
    lineage = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert lineage is not None
    lineage.role = "shadow_challenger"
    lineage.iteration_status = "prepare_isolated_lane"

    assert orchestrator._queue_priority(lineage) == 18


def test_challenger_pressure_treats_prepare_isolated_lane_as_replacement_pressure(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    pressure = orchestrator._challenger_pressure(
        {"issue_codes": []},
        maintenance_actions=["prepare_isolated_lane"],
    )

    assert pressure == 2


def test_apply_execution_bridge_feedback_marks_isolated_lane_active_only_when_alias_runner_live(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    challenger = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert challenger is not None

    challenger.role = "paper_challenger"
    challenger.iteration_status = "prepare_isolated_lane"
    challenger.current_stage = "shadow"
    orchestrator.registry.save_lineage(challenger)

    lineage_summaries = [
        {
            "lineage_id": challenger.lineage_id,
            "family_id": challenger.family_id,
            "current_stage": "shadow",
            "iteration_status": "prepare_isolated_lane",
            "runtime_lane_kind": "isolated_challenger",
            "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-champion",
            "canonical_target_portfolio": "contrarian_legacy",
            "live_paper_trade_count": 0,
            "live_paper_realized_pnl": 0.0,
        }
    ]
    family_summaries = [
        {
            "family_id": challenger.family_id,
            "isolated_challenger_lineage_id": challenger.lineage_id,
            "primary_incumbent_lineage_id": "",
        }
    ]
    bridge_payload = {
        "targets": [
            {
                "portfolio_id": "factory_lane__contrarian_legacy__binance_funding_contrarian-champion",
                "canonical_portfolio_id": "contrarian_legacy",
                "activation_status": "running",
                "running": True,
                "lineages": [{"lineage_id": challenger.lineage_id}],
            }
        ]
    }
    monkeypatch.setattr(
        "factory.orchestrator.build_portfolio_execution_evidence",
        lambda portfolio_id: {
            "running": True,
            "account": {"roi_pct": 3.5, "realized_pnl": 12.0, "trade_count": 4, "wins": 3, "losses": 1, "drawdown_pct": 0.6},
            "health_status": "healthy",
            "issue_codes": [],
        },
    )

    orchestrator._apply_execution_bridge_feedback(
        lineage_summaries=lineage_summaries,
        family_summaries=family_summaries,
        bridge_payload=bridge_payload,
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed is not None
    assert refreshed.iteration_status == "isolated_lane_active"
    assert lineage_summaries[0]["activation_status"] == "running"
    assert lineage_summaries[0]["alias_runner_running"] is True
    assert family_summaries[0]["isolated_evidence_ready"] is True


def test_apply_execution_bridge_feedback_retires_failed_isolated_lane_after_alias_first_assessment(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=19,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.iteration_status = "isolated_lane_active"
    orchestrator.registry.save_lineage(challenger)

    lineage_summaries = [
        {
            "lineage_id": challenger.lineage_id,
            "family_id": challenger.family_id,
            "current_stage": "paper",
            "paper_days": 2,
            "runtime_lane_kind": "isolated_challenger",
            "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-19",
            "canonical_target_portfolio": "contrarian_legacy",
            "iteration_status": "isolated_lane_active",
            "execution_issue_codes": [],
            "execution_health_status": "warning",
            "execution_validation": {},
            "active": True,
        }
    ]
    family_summaries = [
        {
            "family_id": challenger.family_id,
            "isolated_challenger_lineage_id": challenger.lineage_id,
            "primary_incumbent_lineage_id": champion.lineage_id,
            "active_lineage_count": 2,
            "retired_lineage_count": 0,
        }
    ]
    bridge_payload = {
        "targets": [
            {
                "portfolio_id": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-19",
                "canonical_portfolio_id": "contrarian_legacy",
                "activation_status": "running",
                "running": True,
                "lineages": [{"lineage_id": challenger.lineage_id}],
            }
        ]
    }
    monkeypatch.setattr(
        "factory.orchestrator.build_portfolio_execution_evidence",
        lambda portfolio_id: {
            "running": True,
            "account": {"roi_pct": -1.2, "realized_pnl": -6.0, "trade_count": 10, "wins": 4, "losses": 6, "drawdown_pct": 1.2},
            "health_status": "warning",
            "issue_codes": [],
        },
    )

    orchestrator._apply_execution_bridge_feedback(
        lineage_summaries=lineage_summaries,
        family_summaries=family_summaries,
        bridge_payload=bridge_payload,
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed is not None
    assert refreshed.active is False
    assert refreshed.retirement_reason == "isolated_lane_first_assessment_negative_roi"
    assert lineage_summaries[0]["retirement_reason"] == "isolated_lane_first_assessment_negative_roi"
    assert family_summaries[0]["isolated_evidence_ready"] is False


def test_apply_execution_bridge_feedback_keeps_stale_alias_from_counting_as_isolated_evidence_ready(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=21,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.iteration_status = "isolated_lane_active"
    orchestrator.registry.save_lineage(challenger)

    lineage_summaries = [
        {
            "lineage_id": challenger.lineage_id,
            "family_id": challenger.family_id,
            "current_stage": "paper",
            "paper_days": 1,
            "runtime_lane_kind": "isolated_challenger",
            "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-21",
            "canonical_target_portfolio": "contrarian_legacy",
            "iteration_status": "isolated_lane_active",
            "execution_issue_codes": [],
            "execution_health_status": "warning",
            "execution_validation": {},
            "active": True,
        }
    ]
    family_summaries = [
        {
            "family_id": challenger.family_id,
            "isolated_challenger_lineage_id": challenger.lineage_id,
            "primary_incumbent_lineage_id": champion.lineage_id,
            "active_lineage_count": 2,
            "retired_lineage_count": 0,
        }
    ]
    bridge_payload = {
        "targets": [
            {
                "portfolio_id": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-21",
                "canonical_portfolio_id": "contrarian_legacy",
                "activation_status": "running",
                "running": True,
                "lineages": [{"lineage_id": challenger.lineage_id}],
            }
        ]
    }
    monkeypatch.setattr(
        "factory.orchestrator.build_portfolio_execution_evidence",
        lambda portfolio_id: {
            "running": True,
            "account": {"roi_pct": 0.0, "realized_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "drawdown_pct": 0.0},
            "health_status": "warning",
            "issue_codes": [],
            "has_execution_signal": False,
            "runtime_age_hours": 6.0,
        },
    )

    orchestrator._apply_execution_bridge_feedback(
        lineage_summaries=lineage_summaries,
        family_summaries=family_summaries,
        bridge_payload=bridge_payload,
        recent_actions=[],
    )

    assert lineage_summaries[0]["alias_runner_running"] is True
    assert family_summaries[0]["isolated_evidence_ready"] is False


def test_apply_execution_bridge_feedback_marks_isolated_lane_first_assessment_passed(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator.registry.load_family("binance_funding_contrarian")
    champion = orchestrator.registry.load_lineage("binance_funding_contrarian:champion")
    assert family is not None
    assert champion is not None

    challenger = orchestrator._create_challenger(
        family,
        parent_lineage=champion,
        mutation_index=20,
        budget_bucket="adjacent",
    )
    challenger.role = "paper_challenger"
    challenger.current_stage = "paper"
    challenger.iteration_status = "isolated_lane_active"
    orchestrator.registry.save_lineage(challenger)

    lineage_summaries = [
        {
            "lineage_id": challenger.lineage_id,
            "family_id": challenger.family_id,
            "current_stage": "paper",
            "paper_days": 2,
            "runtime_lane_kind": "isolated_challenger",
            "runtime_target_portfolio": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-20",
            "canonical_target_portfolio": "contrarian_legacy",
            "iteration_status": "isolated_lane_active",
            "execution_issue_codes": [],
            "execution_health_status": "healthy",
            "execution_validation": {},
            "active": True,
        }
    ]
    family_summaries = [
        {
            "family_id": challenger.family_id,
            "isolated_challenger_lineage_id": challenger.lineage_id,
            "primary_incumbent_lineage_id": champion.lineage_id,
        }
    ]
    bridge_payload = {
        "targets": [
            {
                "portfolio_id": "factory_lane__contrarian_legacy__binance_funding_contrarian-challenger-20",
                "canonical_portfolio_id": "contrarian_legacy",
                "activation_status": "running",
                "running": True,
                "lineages": [{"lineage_id": challenger.lineage_id}],
            }
        ]
    }
    monkeypatch.setattr(
        "factory.orchestrator.build_portfolio_execution_evidence",
        lambda portfolio_id: {
            "running": True,
            "account": {"roi_pct": 2.2, "realized_pnl": 7.0, "trade_count": 10, "wins": 7, "losses": 3, "drawdown_pct": 0.5},
            "health_status": "healthy",
            "issue_codes": [],
        },
    )

    orchestrator._apply_execution_bridge_feedback(
        lineage_summaries=lineage_summaries,
        family_summaries=family_summaries,
        bridge_payload=bridge_payload,
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_lineage(challenger.lineage_id)
    assert refreshed is not None
    assert refreshed.active is True
    assert refreshed.iteration_status == "isolated_lane_first_assessment_passed"
    assert lineage_summaries[0]["first_assessment_complete"] is True


def test_seed_new_families_creates_incubating_family_from_unused_idea(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"
    (project_root / "IDEAS.md").write_text(
        "1. Cross Venue Entropy Ladder\n"
        "- Summary: Use cross venue event propagation and entropy gaps to create a fresh family.\n"
        "- Tags: cross-venue, information, polymarket\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_INTERVAL_CYCLES", 1)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE", 1)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS", 3)
    monkeypatch.setattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory")

    orchestrator = FactoryOrchestrator(project_root)
    orchestrator._cycle_count = 1
    lineages_by_family = orchestrator._lineages_by_family()
    recent_actions: list[str] = []

    before = {family.family_id for family in orchestrator.registry.families()}
    orchestrator._seed_new_families(
        lineages_by_family=lineages_by_family,
        runtime_mode_value="full",
        recent_actions=recent_actions,
    )
    after_families = orchestrator.registry.families()
    after_ids = {family.family_id for family in after_families}
    new_ids = sorted(after_ids - before)

    assert len(new_ids) == 1
    family = orchestrator.registry.load_family(new_ids[0])
    champion = orchestrator.registry.load_lineage(f"{new_ids[0]}:champion")
    assert family is not None
    assert family.incubation_status == "incubating"
    assert family.origin == "incubated_family"
    assert family.source_idea_id == "idea_001"
    assert family.target_portfolios == ["research_factory"]
    assert champion is not None
    assert champion.current_stage == "idea"
    assert champion.iteration_status == "incubating_family_seed"
    assert any("Incubated new family" in item for item in recent_actions)


def test_seed_new_families_prefers_real_agent_family_proposal(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"
    (project_root / "IDEAS.md").write_text(
        "1. Reflexive Venue Spillovers\n"
        "- Summary: Use cross venue spillovers and reflexive orderflow to seed a fresh family.\n"
        "- Tags: cross-venue, polymarket, binance\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_INTERVAL_CYCLES", 1)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE", 1)
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS", 3)
    monkeypatch.setattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory")

    orchestrator = FactoryOrchestrator(project_root)
    orchestrator._cycle_count = 1
    lineages_by_family = orchestrator._lineages_by_family()
    recent_actions: list[str] = []

    def _fake_generate_family(**_kwargs):
        return AgentRunResult(
            run_id="family_bootstrap_generation_live",
            task_type="family_bootstrap_generation",
            model_class="frontier_research",
            provider="codex",
            model="gpt-5.4",
            reasoning_effort="high",
            success=True,
            fallback_used=False,
            family_id="incubating_family",
            lineage_id=None,
            duration_ms=100,
            result_payload={
                "family_id": "spillover_reflex_lab",
                "label": "Spillover Reflex Lab",
                "thesis": "Exploit reflexive venue spillovers under event compression.",
                "explainer": "Incubate a new cross-venue family from idea intake.",
                "target_venues": ["polymarket", "binance"],
                "primary_connector_ids": ["polymarket_core", "binance_core"],
                "target_portfolios": ["research_factory"],
                "scientific_domains": ["network_epidemiology", "information_theory"],
                "lead_agent_role": "Family Incubator",
                "collaborating_agent_roles": ["Execution Critic"],
                "source_idea_id": "idea_001",
                "incubation_notes": ["agentic family bootstrap"],
            },
            artifact_path=str(tmp_path / "factory" / "agent_runs" / "family_bootstrap_generation_live.json"),
            multi_agent_requested=True,
            multi_agent_roles=["family_thesis_proposer", "venue_connector_planner", "incubation_risk_critic"],
        )

    monkeypatch.setattr(orchestrator.agent_runtime, "generate_family_proposal", _fake_generate_family)

    orchestrator._seed_new_families(
        lineages_by_family=lineages_by_family,
        runtime_mode_value="full",
        recent_actions=recent_actions,
    )

    family = orchestrator.registry.load_family("spillover_reflex_lab")
    champion = orchestrator.registry.load_lineage("spillover_reflex_lab:champion")

    assert family is not None
    assert family.origin == "real_agent_codex"
    assert family.incubation_status == "incubating"
    assert family.source_idea_id == "idea_001"
    assert champion is not None
    assert champion.iteration_status == "incubating_family_seed"
    assert any("Incubated new family spillover_reflex_lab" in item for item in recent_actions)


def test_seed_challengers_skips_early_incubating_family(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator._seed_family_from_spec(
        {
            "family_id": "binance_entropy_ladder",
            "label": "Binance Entropy Ladder Incubator",
            "thesis": "We believe we can create alpha by incubating a fresh cross-venue entropy family.",
            "target_portfolios": ["research_factory"],
            "target_venues": ["binance"],
            "connectors": ["binance_core"],
            "budget_bucket": "moonshot",
            "budget_weight_pct": 8.0,
            "role": "champion",
            "explainer": "Incubating family",
            "scientific_domains": ["information_theory", "microstructure", "control_rl"],
            "lead_agent_role": "Information Theory Researcher",
            "iteration_status": "incubating_family_seed",
        },
        family_origin="incubated_family",
        source_idea_id="idea_x",
        incubation_status="incubating",
        incubation_cycle_created=1,
        incubation_notes=["source=idea_intake"],
    )
    lineages_by_family = orchestrator._lineages_by_family()

    orchestrator._seed_challengers(
        family,
        lineages_by_family,
        runtime_mode_value="full",
        recent_actions=[],
    )

    refreshed = orchestrator.registry.load_family(family.family_id)
    assert refreshed is not None
    assert refreshed.shadow_challenger_ids == []


def test_incubating_family_stays_incubating_before_first_assessment_completes(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator._seed_family_from_spec(
        {
            "family_id": "incubation_holdout_lab",
            "label": "Incubation Holdout Lab",
            "thesis": "We believe we can create alpha by incubating a fresh holdout family.",
            "target_portfolios": ["research_factory"],
            "target_venues": ["binance"],
            "connectors": ["binance_core"],
            "budget_bucket": "moonshot",
            "budget_weight_pct": 8.0,
            "role": "champion",
            "explainer": "Incubating family",
            "scientific_domains": ["information_theory", "control_rl"],
            "lead_agent_role": "Family Incubator",
            "iteration_status": "incubating_family_seed",
        },
        family_origin="incubated_family",
        source_idea_id="idea_hold",
        incubation_status="incubating",
        incubation_cycle_created=1,
        incubation_notes=["source=idea_intake"],
    )
    champion = orchestrator.registry.load_lineage(f"{family.family_id}:champion")
    assert champion is not None
    champion.current_stage = "paper"
    orchestrator.registry.save_lineage(champion)
    family.queue_stage = "paper"

    champion_row = {
        "lineage_id": champion.lineage_id,
        "family_id": family.family_id,
        "current_stage": "paper",
        "active": True,
        "first_assessment_complete": False,
        "live_paper_roi_pct": 3.0,
        "live_paper_trade_count": 11,
        "execution_health_status": "healthy",
    }

    orchestrator._apply_incubating_family_lifecycle(
        family,
        champion_row,
        [champion_row],
        recent_actions=[],
        lineage_summary_by_id={champion.lineage_id: dict(champion_row)},
    )

    assert family.incubation_status == "incubating"
    assert family.incubation_decision_reason is None


def test_incubating_family_graduates_after_positive_first_assessment(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator._seed_family_from_spec(
        {
            "family_id": "incubation_grad_lab",
            "label": "Incubation Graduation Lab",
            "thesis": "We believe we can create alpha by graduating only strong incubators.",
            "target_portfolios": ["research_factory"],
            "target_venues": ["binance"],
            "connectors": ["binance_core"],
            "budget_bucket": "moonshot",
            "budget_weight_pct": 8.0,
            "role": "champion",
            "explainer": "Incubating family",
            "scientific_domains": ["information_theory", "control_rl"],
            "lead_agent_role": "Family Incubator",
            "iteration_status": "incubating_family_seed",
        },
        family_origin="incubated_family",
        source_idea_id="idea_grad",
        incubation_status="incubating",
        incubation_cycle_created=1,
        incubation_notes=["source=idea_intake"],
    )
    champion = orchestrator.registry.load_lineage(f"{family.family_id}:champion")
    assert champion is not None
    champion.current_stage = "paper"
    orchestrator.registry.save_lineage(champion)
    family.queue_stage = "paper"
    recent_actions: list[str] = []

    champion_row = {
        "lineage_id": champion.lineage_id,
        "family_id": family.family_id,
        "current_stage": "paper",
        "active": True,
        "first_assessment_complete": True,
        "live_paper_roi_pct": 3.0,
        "live_paper_trade_count": 12,
        "execution_health_status": "healthy",
    }

    orchestrator._apply_incubating_family_lifecycle(
        family,
        champion_row,
        [champion_row],
        recent_actions=recent_actions,
        lineage_summary_by_id={champion.lineage_id: dict(champion_row)},
    )

    assert family.incubation_status == "graduated"
    assert family.incubation_decision_reason == "graduated_after_positive_first_assessment"
    assert any("Graduated incubating family incubation_grad_lab" in item for item in recent_actions)


def test_graduated_incubating_family_enters_challenger_rotation_same_cycle(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator._seed_family_from_spec(
        {
            "family_id": "incubation_handoff_lab",
            "label": "Incubation Handoff Lab",
            "thesis": "We believe we can create alpha by graduating incubators directly into challenger rotation.",
            "target_portfolios": ["research_factory"],
            "target_venues": ["binance"],
            "connectors": ["binance_core"],
            "budget_bucket": "moonshot",
            "budget_weight_pct": 8.0,
            "role": "champion",
            "explainer": "Incubating family",
            "scientific_domains": ["information_theory", "control_rl"],
            "lead_agent_role": "Family Incubator",
            "iteration_status": "incubating_family_seed",
        },
        family_origin="incubated_family",
        source_idea_id="idea_handoff",
        incubation_status="incubating",
        incubation_cycle_created=1,
        incubation_notes=["source=idea_intake"],
    )
    champion = orchestrator.registry.load_lineage(f"{family.family_id}:champion")
    assert champion is not None
    champion.current_stage = "paper"
    orchestrator.registry.save_lineage(champion)
    family.queue_stage = "paper"
    lineages_by_family = orchestrator._lineages_by_family()
    recent_actions: list[str] = []
    champion_row = {
        "lineage_id": champion.lineage_id,
        "family_id": family.family_id,
        "current_stage": "paper",
        "active": True,
        "first_assessment_complete": True,
        "live_paper_roi_pct": 2.0,
        "live_paper_trade_count": 12,
        "execution_health_status": "healthy",
    }

    transition = orchestrator._apply_incubating_family_lifecycle(
        family,
        champion_row,
        [champion_row],
        recent_actions=recent_actions,
        lineage_summary_by_id={champion.lineage_id: dict(champion_row)},
    )
    assert transition == "graduated"

    def _fake_seed_challengers(_family, _lineages_by_family, *, runtime_mode_value, recent_actions):
        created = orchestrator._create_challenger(
            family,
            parent_lineage=champion,
            mutation_index=1,
            budget_bucket="adjacent",
        )
        created.current_stage = "shadow"
        orchestrator.registry.save_lineage(created)

    monkeypatch.setattr(orchestrator, "_seed_challengers", _fake_seed_challengers)

    orchestrator._seed_post_graduation_challengers(
        family,
        lineages_by_family,
        runtime_mode_value="full",
        recent_actions=recent_actions,
    )

    refreshed_family = orchestrator.registry.load_family(family.family_id)
    active_family_lineages = [lineage for lineage in orchestrator.registry.lineages() if lineage.family_id == family.family_id and lineage.active]
    assert refreshed_family is not None
    assert family.incubation_status == "graduated"
    assert len(active_family_lineages) > 1
    assert any(lineage.role in {"shadow_challenger", "paper_challenger"} for lineage in active_family_lineages if lineage.lineage_id != champion.lineage_id)
    assert any("entered challenger rotation" in item for item in recent_actions)


def test_incubating_family_retires_after_failed_first_assessment(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)

    orchestrator = FactoryOrchestrator(project_root)
    family = orchestrator._seed_family_from_spec(
        {
            "family_id": "incubation_fail_lab",
            "label": "Incubation Failure Lab",
            "thesis": "We believe we can create alpha by killing weak incubators early.",
            "target_portfolios": ["research_factory"],
            "target_venues": ["binance"],
            "connectors": ["binance_core"],
            "budget_bucket": "moonshot",
            "budget_weight_pct": 8.0,
            "role": "champion",
            "explainer": "Incubating family",
            "scientific_domains": ["information_theory", "control_rl"],
            "lead_agent_role": "Family Incubator",
            "iteration_status": "incubating_family_seed",
        },
        family_origin="incubated_family",
        source_idea_id="idea_fail",
        incubation_status="incubating",
        incubation_cycle_created=1,
        incubation_notes=["source=idea_intake"],
    )
    champion = orchestrator.registry.load_lineage(f"{family.family_id}:champion")
    assert champion is not None
    champion.current_stage = "paper"
    orchestrator.registry.save_lineage(champion)
    family.queue_stage = "paper"
    recent_actions: list[str] = []
    lineage_summaries = {
        champion.lineage_id: {
            "lineage_id": champion.lineage_id,
            "family_id": family.family_id,
            "active": True,
        }
    }
    champion_row = {
        "lineage_id": champion.lineage_id,
        "family_id": family.family_id,
        "current_stage": "paper",
        "active": True,
        "first_assessment_complete": True,
        "live_paper_roi_pct": -1.0,
        "live_paper_trade_count": 12,
        "execution_health_status": "warning",
        "monthly_roi_pct": -1.0,
        "fitness_score": -2.0,
        "hard_vetoes": [],
        "execution_validation": {"health_status": "warning", "issue_codes": [], "targets": []},
    }

    orchestrator._apply_incubating_family_lifecycle(
        family,
        champion_row,
        [champion_row],
        recent_actions=recent_actions,
        lineage_summary_by_id=lineage_summaries,
    )

    refreshed = orchestrator.registry.load_lineage(champion.lineage_id)
    assert refreshed is not None
    assert family.incubation_status == "retired"
    assert family.incubation_decision_reason == "incubation_first_assessment_failed"
    assert refreshed.active is False
    assert refreshed.retirement_reason == "incubation_first_assessment_failed"
    assert any("Retired incubating family incubation_fail_lab" in item for item in recent_actions)


def test_sync_operator_actions_creates_inbox_items(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    orchestrator = FactoryOrchestrator(project_root)

    signals = orchestrator._sync_operator_actions(
        {
            "positive_models": [],
            "potential_winners": [],
            "maintenance_queue": [],
            "human_action_required": [
                {
                    "family_id": "betfair_prediction_value_league",
                    "lineage_id": "betfair_prediction_value_league:champion",
                    "summary": "Venue restriction needs operator confirmation.",
                    "human_action": "Confirm venue is now available and rerun the book.",
                    "execution_health_status": "critical",
                }
            ],
            "escalation_candidates": [
                {
                    "family_id": "binance_funding_contrarian",
                    "lineage_id": "binance_funding_contrarian:challenger:9",
                    "reason": "Winner candidate ready for human review.",
                    "current_stage": "live_ready",
                }
            ],
        }
    )

    inbox = signals["action_inbox"]
    assert len(inbox) == 2
    assert inbox[0]["signal_type"] == "human_action_required"
    assert inbox[0]["available_decisions"] == ["approve", "reject", "instruct"]
    assert signals["human_action_required"][0]["operator_action_id"]
    assert signals["escalation_candidates"][0]["operator_action_id"]


def test_factory_orchestrator_records_execution_refresh_results(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution data providers.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    _prepare_factory_inputs(project_root)

    def _fake_refresh(self, **_kwargs):
        return {
            "status": "success",
            "selected_model": "xgboost",
            "artifact_path": str(project_root / "data" / "funding_models" / "contrarian_comparison.json"),
        }

    monkeypatch.setattr(FactoryExperimentRunner, "_run_execution_refresh", _fake_refresh)
    orchestrator = FactoryOrchestrator(project_root)

    state = orchestrator.run_cycle()

    funding_lineage = next(
        row for row in state["lineages"] if row["lineage_id"] == "binance_funding_contrarian:champion"
    )
    assert funding_lineage["latest_execution_refresh_status"] == "success"
    assert funding_lineage["latest_execution_refresh_selected"] == "xgboost"

    funding_experiment = orchestrator.registry.load_experiment("binance_funding_contrarian:champion")
    latest_run = dict((funding_experiment.expected_outputs or {}).get("latest_run") or {})
    assert latest_run["execution_refresh_status"] == "success"
    assert latest_run["execution_refresh_selected"] == "xgboost"
    package_payload = json.loads(Path(latest_run["package_path"]).read_text(encoding="utf-8"))
    assert package_payload["files"]["execution_refresh"]
    assert Path(package_payload["files"]["execution_refresh"]).exists()


def test_factory_orchestrator_retires_repeat_losers_after_three_cycles(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution data providers.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    _prepare_factory_inputs(project_root)

    orchestrator = FactoryOrchestrator(project_root)

    for _ in range(4):
        state = orchestrator.run_cycle()

    retired = [row for row in state["lineages"] if row.get("active") is False]

    assert retired
    assert any(row.get("retirement_reason") for row in retired)
    assert state["research_summary"]["retired_lineage_count"] >= 1
    assert state["research_summary"]["learning_memory_count"] >= 1
    assert state["research_summary"]["tweaked_lineage_count"] >= 1
    assert any(int(row.get("tweak_count", 0) or 0) >= 2 for row in retired)
    assert any(memory["outcome"] == "retired_underperformance" for memory in state["learning_memory"])
    assert all("execution_health_status" in row for row in state["lineages"])
    assert any("execution_evidence" in memory for memory in state["learning_memory"])


def test_factory_orchestrator_cost_saver_preserves_learning_but_freezes_manifests_and_promotions(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution data providers.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "cost_saver")
    _prepare_factory_inputs(project_root)

    orchestrator = FactoryOrchestrator(project_root)

    state = orchestrator.run_cycle()

    assert state["agentic_factory_mode"] == "cost_saver"
    assert state["agentic_tokens_allowed"] is False
    assert state["factory_influence_allowed"] is True
    assert state["research_summary"]["manifest_publication_paused"] is True
    assert state["manifests"]["pending"] == []
    assert state["manifests"]["live_loadable"] == []
    assert state["lineages"]
    assert all(lineage["current_stage"] == "idea" for lineage in state["lineages"])
    assert orchestrator.registry.evaluations("binance_funding_contrarian:champion")
    assert any(lineage["latest_artifact_package"] for lineage in state["lineages"] if lineage["family_id"] == "betfair_prediction_value_league")
    assert any(lineage["latest_artifact_package"] for lineage in state["lineages"] if lineage["family_id"] == "binance_funding_contrarian")
    assert state["research_summary"]["agent_generated_lineage_count"] == 0


def test_factory_manifest_adapters_expose_candidate_contexts_by_portfolio(tmp_path, monkeypatch):
    if execution_root is None:
        pytest.skip("Requires EXECUTION_REPO_ROOT to validate extracted repo against execution runner implementations.")
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    portfolio_root = tmp_path / "portfolio_state"
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "PORTFOLIO_STATE_ROOT", str(portfolio_root))
    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    _prepare_factory_inputs(project_root)

    orchestrator = FactoryOrchestrator(project_root)
    orchestrator.run_cycle()

    betfair_contexts = candidate_context_refs_for_portfolio("betfair_core")
    contrarian_contexts = candidate_context_refs_for_portfolio("contrarian_legacy")
    cascade_contexts = candidate_context_refs_for_portfolio("cascade_alpha")
    polymarket_contexts = candidate_context_refs_for_portfolio("polymarket_quantum_fold")
    hedge_validation_contexts = candidate_context_refs_for_portfolio("hedge_validation")
    hedge_research_contexts = candidate_context_refs_for_portfolio("hedge_research")

    assert betfair_contexts
    assert contrarian_contexts
    assert cascade_contexts
    assert polymarket_contexts
    assert hedge_validation_contexts
    assert hedge_research_contexts

    assert any(item["family_id"] == "betfair_prediction_value_league" for item in betfair_contexts)
    assert all(item["context_source"] == "candidate_lineage" for item in betfair_contexts)
    assert all(item["package"]["package_found"] for item in contrarian_contexts)
    assert any("cascade_alpha" in item["resolved_targets"] for item in cascade_contexts)
    assert any("polymarket_quantum_fold" in item["resolved_targets"] for item in polymarket_contexts)
    assert any("hedge_validation" in item["resolved_targets"] for item in hedge_validation_contexts)
    assert any("hedge_research" in item["resolved_targets"] for item in hedge_research_contexts)


def test_factory_orchestrator_hard_stop_returns_paused_state_without_new_activity(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir(parents=True, exist_ok=True)
    factory_root = tmp_path / "factory"
    goldfish_root = project_root / "research" / "goldfish"

    monkeypatch.setattr(config, "FACTORY_ROOT", str(factory_root))
    monkeypatch.setattr(config, "FACTORY_GOLDFISH_ROOT", str(goldfish_root))
    monkeypatch.setattr(config, "AGENTIC_FACTORY_MODE", "hard_stop")

    orchestrator = FactoryOrchestrator(project_root)

    state = orchestrator.run_cycle()

    assert state["status"] == "paused"
    assert state["running"] is False
    assert state["agentic_factory_mode"] == "hard_stop"
    assert state["factory_influence_allowed"] is False
    assert "agentic_factory_hard_stopped" in (state.get("readiness") or {}).get("blockers", [])
    assert orchestrator.registry.evaluations() == []
