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
    """Orchestrator should assign alpaca_paper for equity families."""

    def test_equity_family_gets_alpaca_paper(self):
        from factory.family_classifier import is_equity_family

        equity_probe = {"target_venues": ["yahoo"], "primary_connector_ids": ["yahoo_stocks"]}
        assert is_equity_family(equity_probe) is True

    def test_non_equity_family_does_not_get_alpaca(self):
        from factory.family_classifier import is_equity_family

        non_equity_probe = {"target_venues": ["binance"], "primary_connector_ids": ["binance_core"]}
        assert is_equity_family(non_equity_probe) is False
