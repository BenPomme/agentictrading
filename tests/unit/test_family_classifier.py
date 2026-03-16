"""Unit tests for factory.family_classifier — equity vs non-equity classification.

[2026-03-16, agent: gpt-5.1-cursor]
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from factory.family_classifier import (
    family_backtest_venue,
    family_runtime_venue,
    is_equity_family,
    load_family_config,
)


class TestIsEquityFamily:
    def test_empty_config_returns_false(self):
        assert is_equity_family({}) is False

    def test_yahoo_venue_is_equity(self):
        assert is_equity_family({"target_venues": ["yahoo"]}) is True

    def test_alpaca_venue_is_equity(self):
        assert is_equity_family({"target_venues": ["alpaca"]}) is True

    def test_yahoo_connector_is_equity(self):
        assert is_equity_family({"primary_connector_ids": ["yahoo_stocks"]}) is True

    def test_alpaca_connector_is_equity(self):
        assert is_equity_family({"primary_connector_ids": ["alpaca_stocks"]}) is True

    def test_binance_venue_is_not_equity(self):
        assert is_equity_family({"target_venues": ["binance"]}) is False

    def test_polymarket_venue_is_not_equity(self):
        assert is_equity_family({"target_venues": ["polymarket"]}) is False

    def test_betfair_venue_is_not_equity(self):
        assert is_equity_family({"target_venues": ["betfair"]}) is False

    def test_mixed_binance_yahoo_is_not_equity(self):
        """If a family targets both binance and yahoo, the non-equity venue takes priority."""
        assert is_equity_family({"target_venues": ["binance", "yahoo"]}) is False

    def test_mixed_polymarket_binance_is_not_equity(self):
        assert is_equity_family({"target_venues": ["polymarket", "binance"]}) is False

    def test_case_insensitive(self):
        assert is_equity_family({"target_venues": ["Yahoo"]}) is True
        assert is_equity_family({"target_venues": ["ALPACA"]}) is True
        assert is_equity_family({"target_venues": ["Binance"]}) is False


class TestFamilyBacktestVenue:
    def test_equity_returns_yahoo(self):
        cfg = {"target_venues": ["yahoo", "alpaca"], "primary_connector_ids": ["yahoo_stocks"]}
        assert family_backtest_venue(cfg) == "yahoo"

    def test_binance_returns_binance(self):
        cfg = {"target_venues": ["binance"]}
        assert family_backtest_venue(cfg) == "binance"

    def test_polymarket_returns_polymarket(self):
        cfg = {"target_venues": ["polymarket", "binance"]}
        assert family_backtest_venue(cfg) == "polymarket"

    def test_empty_venues_returns_unknown(self):
        assert family_backtest_venue({}) == "unknown"


class TestFamilyRuntimeVenue:
    def test_equity_returns_alpaca(self):
        cfg = {"target_venues": ["yahoo"]}
        assert family_runtime_venue(cfg) == "alpaca"

    def test_non_equity_returns_backtest_venue(self):
        cfg = {"target_venues": ["binance"]}
        assert family_runtime_venue(cfg) == "binance"


class TestLoadFamilyConfig:
    def test_loads_valid_json(self, tmp_path: Path):
        families_dir = tmp_path / "data" / "factory" / "families"
        families_dir.mkdir(parents=True)
        cfg = {"family_id": "test_fam", "target_venues": ["yahoo"]}
        (families_dir / "test_fam.json").write_text(json.dumps(cfg))

        loaded = load_family_config(tmp_path, "test_fam")
        assert loaded["family_id"] == "test_fam"
        assert loaded["target_venues"] == ["yahoo"]

    def test_missing_file_returns_empty_dict(self, tmp_path: Path):
        assert load_family_config(tmp_path, "nonexistent") == {}

    def test_invalid_json_returns_empty_dict(self, tmp_path: Path):
        families_dir = tmp_path / "data" / "factory" / "families"
        families_dir.mkdir(parents=True)
        (families_dir / "broken.json").write_text("not json{{{")

        assert load_family_config(tmp_path, "broken") == {}
