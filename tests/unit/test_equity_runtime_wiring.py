"""Unit tests for equity Yahoo-backtest / Alpaca-runtime wiring.

Validates that:
- DynamicModelRunner accepts and applies runtime_data_source override
- local_runner_main routes equity families to Alpaca runtime
- Orchestrator assigns alpaca_paper portfolio for equity families

[2026-03-16, agent: gpt-5.1-cursor]
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestDynamicModelRunnerDataSourceOverride:
    """DynamicModelRunner should override the data source at runtime for equity families."""

    def test_runtime_data_source_stored(self):
        with patch("factory.runners.dynamic_runner.PaperTradeBook"), \
             patch("factory.runners.dynamic_runner.LocalPortfolioRunner.__init__", return_value=None):
            from factory.runners.dynamic_runner import DynamicModelRunner

            runner = DynamicModelRunner.__new__(DynamicModelRunner)
            runner.portfolio_id = "alpaca_paper"
            runner._model_code_path = "/fake/model.py"
            runner._class_name = "TestModel"
            runner._genome_params = {}
            runner._runtime_data_source = "alpaca"
            runner._model = None
            runner._last_fit_date = None
            runner._retrain_interval_days = 21
            runner._data = None
            runner._data_req = None
            runner.requires_market_open = False

            assert runner._runtime_data_source == "alpaca"

    def test_data_req_overridden_when_runtime_source_set(self):
        """When _runtime_data_source is set, _ensure_model_loaded should override data_req source."""
        with patch("factory.runners.dynamic_runner.PaperTradeBook"), \
             patch("factory.runners.dynamic_runner.LocalPortfolioRunner.__init__", return_value=None), \
             patch("factory.runners.dynamic_runner.load_model_from_code") as mock_load, \
             patch("factory.runners.dynamic_runner.assess_paper_data_readiness") as mock_readiness, \
             patch("factory.runners.dynamic_runner.load_data_for_requirements") as mock_data:
            from factory.runners.dynamic_runner import DynamicModelRunner
            from pathlib import Path
            import pandas as pd

            mock_model = MagicMock()
            mock_model.required_data.return_value = {
                "source": "yahoo",
                "instruments": ["SPY"],
                "fields": ["Close"],
            }
            mock_model.fit.return_value = None
            mock_load.return_value = mock_model
            mock_readiness.return_value = type(
                "Ready",
                (),
                {
                    "ready": True,
                    "blocking_reason": "",
                    "to_dict": lambda self: {"ready": True, "blocking_reason": ""},
                },
            )()
            mock_data.return_value = pd.DataFrame({"Close": [100, 101, 102]})

            runner = DynamicModelRunner.__new__(DynamicModelRunner)
            runner.portfolio_id = "alpaca_paper"
            runner._model_code_path = "/fake/model.py"
            runner._class_name = "TestModel"
            runner._genome_params = {}
            runner._runtime_data_source = "alpaca"
            runner._model = None
            runner._last_fit_date = None
            runner._retrain_interval_days = 21
            runner._project_root = Path("/fake")
            runner._data = None
            runner._data_req = None
            runner._paper_data_contract = None
            runner._last_readiness = None
            runner._last_model_load_error = None
            runner._last_model_load_issue_codes = []
            runner._last_model_load_blockers = []
            runner._last_model_load_failed_at = None
            runner._next_model_load_retry_at = None
            runner.requires_market_open = False
            runner.write_runtime_health = MagicMock()

            runner._ensure_model_loaded()

            call_args = mock_data.call_args
            data_req_passed = call_args[0][0]
            assert data_req_passed["source"] == "alpaca"

    def test_data_readiness_failure_surfaces_in_runner_state(self):
        with patch("factory.runners.dynamic_runner.PaperTradeBook"), \
             patch("factory.runners.dynamic_runner.LocalPortfolioRunner.__init__", return_value=None), \
             patch("factory.runners.dynamic_runner.load_model_from_code") as mock_load, \
             patch("factory.runners.dynamic_runner.assess_paper_data_readiness") as mock_readiness:
            from factory.runners.dynamic_runner import DynamicModelRunner
            from pathlib import Path

            mock_model = MagicMock()
            mock_model.required_data.return_value = {
                "source": "alpaca",
                "instruments": ["SPY"],
                "fields": ["close"],
                "cadence": "2m",
            }
            mock_load.return_value = mock_model
            mock_readiness.return_value = type(
                "Ready",
                (),
                {
                    "ready": False,
                    "blocking_reason": "blocked: Alpaca 1-minute bars stale",
                    "to_dict": lambda self: {"ready": False, "blocking_reason": "blocked: Alpaca 1-minute bars stale"},
                },
            )()

            runner = DynamicModelRunner.__new__(DynamicModelRunner)
            runner.portfolio_id = "alpaca_paper"
            runner._model_code_path = "/fake/model.py"
            runner._class_name = "TestModel"
            runner._genome_params = {}
            runner._runtime_data_source = "alpaca"
            runner._model = None
            runner._last_fit_date = None
            runner._retrain_interval_days = 21
            runner._project_root = Path("/fake")
            runner._data = None
            runner._data_req = None
            runner._paper_data_contract = None
            runner._last_readiness = None
            runner._last_model_load_error = None
            runner._last_model_load_issue_codes = []
            runner._last_model_load_blockers = []
            runner._last_model_load_failed_at = None
            runner._next_model_load_retry_at = None
            runner.requires_market_open = False
            runner.write_runtime_health = MagicMock()

            payload = runner.run_cycle()

            assert payload["ready"] is False
            assert payload["reason"] in {"model_not_loaded", "data_not_ready"}
            assert payload["runtime_health"]["issue_codes"] == ["data_not_ready"]

    def test_model_load_failure_surfaces_runtime_error_and_retry_cooldown(self, monkeypatch):
        with patch("factory.runners.dynamic_runner.PaperTradeBook"), \
             patch("factory.runners.dynamic_runner.LocalPortfolioRunner.__init__", return_value=None), \
             patch("factory.runners.dynamic_runner.load_model_from_code", side_effect=ValueError("Code validation failed")), \
             patch("factory.runners.dynamic_runner.assess_paper_data_readiness"):
            from factory.runners.dynamic_runner import DynamicModelRunner
            from pathlib import Path

            monkeypatch.setattr("config.FACTORY_MODEL_LOAD_RETRY_COOLDOWN_SECONDS", 600, raising=False)

            runner = DynamicModelRunner.__new__(DynamicModelRunner)
            runner.portfolio_id = "alpaca_paper"
            runner._model_code_path = "/fake/model.py"
            runner._class_name = "TestModel"
            runner._genome_params = {}
            runner._runtime_data_source = "alpaca"
            runner._model = None
            runner._last_fit_date = None
            runner._retrain_interval_days = 21
            runner._project_root = Path("/fake")
            runner._data = None
            runner._data_req = None
            runner._paper_data_contract = None
            runner._last_readiness = None
            runner._last_model_load_error = None
            runner._last_model_load_issue_codes = []
            runner._last_model_load_blockers = []
            runner._last_model_load_failed_at = None
            runner._next_model_load_retry_at = None
            runner.requires_market_open = False
            runner.write_runtime_health = MagicMock()

            payload = runner.run_cycle()

            assert payload["ready"] is False
            assert payload["reason"] == "model_not_loaded"
            assert payload["runtime_health"]["issue_codes"] == [
                "runtime_error",
                "readiness_blocked",
                "model_not_loaded",
            ]
            assert "Code validation failed" in str(payload["runtime_health"]["error"])
            assert payload["runtime_health"]["model_load_retry_after"] is not None


class TestOrchestratorEquityPortfolioAssignment:
    """Orchestrator should route equity execution through Alpaca."""

    def test_orchestrator_normalizes_yahoo_equity_to_alpaca_execution(self):
        from factory.orchestrator import FactoryOrchestrator

        venues, connectors = FactoryOrchestrator._normalize_venues_and_connectors(["yahoo"], [])

        assert venues == ["alpaca"]
        assert connectors == ["alpaca_stocks"]

    def test_orchestrator_preserves_cross_venue_but_moves_equity_leg_to_alpaca(self):
        from factory.orchestrator import FactoryOrchestrator

        venues, connectors = FactoryOrchestrator._normalize_venues_and_connectors(["yahoo", "polymarket"], [])

        assert venues == ["alpaca", "polymarket"]
        assert connectors == ["alpaca_stocks", "polymarket_core", "polymarket_history"]

    def test_non_equity_family_does_not_get_alpaca(self):
        from factory.family_classifier import is_equity_family

        non_equity_probe = {"target_venues": ["binance"], "primary_connector_ids": ["binance_core"]}
        assert is_equity_family(non_equity_probe) is False

    def test_local_runner_blocks_stock_fallback_without_explicit_runtime_model(self, tmp_path, monkeypatch):
        import json

        from factory.contracts import (
            ExperimentSpec,
            FactoryFamily,
            LineageRecord,
            MutationBounds,
            PromotionStage,
            ResearchHypothesis,
            StrategyGenome,
        )
        from factory.local_runner_base import BlockedLocalRunner
        from factory.local_runner_main import get_runner
        from factory.registry import FactoryRegistry

        project_root = tmp_path / "repo"
        project_root.mkdir(parents=True, exist_ok=True)
        factory_root = project_root / "data" / "factory"
        monkeypatch.setattr("config.FACTORY_ROOT", str(factory_root))

        registry = FactoryRegistry(factory_root)
        family = FactoryFamily(
            family_id="oil_family",
            label="oil",
            thesis="thesis",
            target_portfolios=["oil_portfolio"],
            target_venues=["yahoo", "polymarket"],
            primary_connector_ids=["yahoo_stocks", "polymarket_history"],
            champion_lineage_id="oil_family:champion",
            shadow_challenger_ids=[],
            paper_challenger_ids=[],
            budget_split={"research": 1.0},
            queue_stage=PromotionStage.SHADOW.value,
            explainer="explainer",
        )
        registry.save_family(family)
        registry.save_research_pack(
            hypothesis=ResearchHypothesis(
                hypothesis_id="oil:h",
                family_id="oil_family",
                title="oil",
                thesis="thesis",
                scientific_domains=["econ"],
                lead_agent_role="lead",
                success_metric="roi",
                guardrails=[],
            ),
            genome=StrategyGenome(
                genome_id="oil:g",
                lineage_id="oil_family:champion",
                family_id="oil_family",
                parent_genome_id=None,
                role="champion",
                parameters={},
                mutation_bounds=MutationBounds(),
                scientific_domains=["econ"],
                budget_bucket="standard",
                resource_profile="local",
                budget_weight_pct=1.0,
            ),
            experiment=ExperimentSpec(
                experiment_id="oil:e",
                lineage_id="oil_family:champion",
                family_id="oil_family",
                hypothesis_id="oil:h",
                genome_id="oil:g",
                goldfish_workspace=str(project_root / "research" / "goldfish" / "oil_family"),
                pipeline_stages=["dataset", "train"],
                backend_mode="goldfish_sidecar",
                resource_profile="local",
                expected_outputs={
                    "latest_run": {
                        "mode": "hmm_regime_adaptive",
                        "resolved_model_engine": "hmm_regime",
                    }
                },
            ),
            lineage=LineageRecord(
                lineage_id="oil_family:champion",
                family_id="oil_family",
                label="oil",
                role="champion",
                current_stage=PromotionStage.SHADOW.value,
                target_portfolios=["oil_portfolio"],
                target_venues=["yahoo", "polymarket"],
                hypothesis_id="oil:h",
                genome_id="oil:g",
                experiment_id="oil:e",
                budget_bucket="standard",
                budget_weight_pct=1.0,
                connector_ids=[],
                goldfish_workspace=str(project_root / "research" / "goldfish" / "oil_family"),
            ),
        )

        runner = get_runner("oil_portfolio")

        assert isinstance(runner, BlockedLocalRunner)

    def test_local_runner_uses_alpaca_runtime_source_for_equity_models_even_without_alpaca_files(self, tmp_path, monkeypatch):
        from factory.contracts import (
            ExperimentSpec,
            FactoryFamily,
            LineageRecord,
            MutationBounds,
            PromotionStage,
            ResearchHypothesis,
            StrategyGenome,
        )
        from factory.local_runner_main import get_runner
        from factory.registry import FactoryRegistry

        project_root = tmp_path / "repo"
        project_root.mkdir(parents=True, exist_ok=True)
        factory_root = project_root / "data" / "factory"
        monkeypatch.setattr("config.FACTORY_ROOT", str(factory_root))

        registry = FactoryRegistry(factory_root)
        family = FactoryFamily(
            family_id="spy_family",
            label="spy",
            thesis="thesis",
            target_portfolios=["alpaca_paper"],
            target_venues=["alpaca"],
            primary_connector_ids=["alpaca_stocks"],
            champion_lineage_id="spy_family:champion",
            shadow_challenger_ids=[],
            paper_challenger_ids=[],
            budget_split={"research": 1.0},
            queue_stage=PromotionStage.PAPER.value,
            explainer="explainer",
            metadata={"research_venues": ["yahoo"], "research_connector_ids": ["yahoo_stocks"]},
        )
        registry.save_family(family)
        model_path = project_root / "model_code.py"
        model_path.write_text("class TestModel: pass\n", encoding="utf-8")
        registry.save_research_pack(
            hypothesis=ResearchHypothesis(
                hypothesis_id="spy:h",
                family_id="spy_family",
                title="spy",
                thesis="thesis",
                scientific_domains=["econ"],
                lead_agent_role="lead",
                success_metric="roi",
                guardrails=[],
            ),
            genome=StrategyGenome(
                genome_id="spy:g",
                lineage_id="spy_family:champion",
                family_id="spy_family",
                parent_genome_id=None,
                role="champion",
                parameters={"model_code_path": str(model_path), "model_class_name": "TestModel"},
                mutation_bounds=MutationBounds(),
                scientific_domains=["econ"],
                budget_bucket="standard",
                resource_profile="local",
                budget_weight_pct=1.0,
            ),
            experiment=ExperimentSpec(
                experiment_id="spy:e",
                lineage_id="spy_family:champion",
                family_id="spy_family",
                hypothesis_id="spy:h",
                genome_id="spy:g",
                goldfish_workspace=str(project_root / "research" / "goldfish" / "spy_family"),
                pipeline_stages=["dataset", "train"],
                backend_mode="goldfish_sidecar",
                resource_profile="local",
            ),
            lineage=LineageRecord(
                lineage_id="spy_family:champion",
                family_id="spy_family",
                label="spy",
                role="champion",
                current_stage=PromotionStage.PAPER.value,
                target_portfolios=["alpaca_paper"],
                target_venues=["alpaca"],
                hypothesis_id="spy:h",
                genome_id="spy:g",
                experiment_id="spy:e",
                budget_bucket="standard",
                budget_weight_pct=1.0,
                connector_ids=["alpaca_stocks"],
                goldfish_workspace=str(project_root / "research" / "goldfish" / "spy_family"),
            ),
        )

        with patch("factory.local_runner_main.assess_lineage_runtime_admission") as mock_admission, \
             patch("factory.family_classifier.load_family_config", return_value={"target_venues": ["alpaca"]}), \
             patch("factory.family_classifier.family_runtime_venue", return_value="alpaca"), \
             patch("factory.runners.dynamic_runner.DynamicModelRunner") as mock_runner_cls:
            mock_admission.return_value = type("Admission", (), {"admitted": True, "reason": ""})()
            mock_runner_cls.return_value = MagicMock()
            _runner = get_runner("alpaca_paper")

        kwargs = mock_runner_cls.call_args.kwargs
        assert kwargs["runtime_data_source"] == "alpaca"
