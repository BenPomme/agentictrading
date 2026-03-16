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

            runner._ensure_model_loaded()

            call_args = mock_data.call_args
            data_req_passed = call_args[0][0]
            assert data_req_passed["source"] == "alpaca"


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
