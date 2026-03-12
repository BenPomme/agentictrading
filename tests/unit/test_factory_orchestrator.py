from __future__ import annotations

import json
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

    assert first_state["research_summary"]["family_count"] == 5
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
        paper_days=20,
    )

    assert orchestrator._scheduled_review_reason(family, lineage, immature, {"issue_codes": []}) is None
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
    assert len(queue) == 3
    assert queue[0]["action"] == "human_action_required"
    assert queue[1]["action"] == "retrain"
    assert queue[2]["action"] == "review_due"
    assert queue[0]["requires_human"] is True


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
