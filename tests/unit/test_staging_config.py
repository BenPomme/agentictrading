"""Tests for staging config parsing and safe defaults (Prompt 04).

Validates:
- All new env vars are parsed from config.py with correct defaults
- StagingGuards safe-default values match first-run spec
- Dangerous overrides (live trading) are blocked by hard-disable guard
- Configurable downgrade thresholds are read into _STEP_THRESHOLDS
- OperatorStatus renders staging_guards when provided
- is_safe_for_preflight logic
"""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**overrides: Any) -> types.SimpleNamespace:
    """Build a minimal config namespace with staging defaults + overrides."""
    defaults = {
        "FACTORY_IDEA_SOURCE_FILE": "./IDEAS.md",
        "FACTORY_ENABLE_WEB_IDEA_RESEARCH": False,
        "FACTORY_ENABLE_PAPER_TRADING": False,
        "FACTORY_ENABLE_LIVE_TRADING": False,
        "FACTORY_LIVE_TRADING_HARD_DISABLE": True,
        "FACTORY_MAX_ACTIVE_FAMILIES": 1,
        "FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY": 1,
        "FACTORY_MAX_CHALLENGERS_PER_FAMILY": 1,
        "FACTORY_ALLOW_AUTONOMOUS_MUTATION": False,
        "FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION": False,
        "FACTORY_BACKTEST_PASS_MONTHLY_ROI": 0.05,
        "FACTORY_REQUIRE_FORWARDTEST_PASS": True,
        "FACTORY_REQUIRE_PAPER_EVIDENCE_FOR_CHALLENGER": True,
        "FACTORY_DAILY_INFERENCE_BUDGET_USD": 15.0,
        "FACTORY_WEEKLY_INFERENCE_BUDGET_USD": 75.0,
        "FACTORY_STRICT_BUDGETS": False,
        "FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO": 0.70,
        "FACTORY_BUDGET_FORCE_CHEAP_RATIO": 0.80,
        "FACTORY_BUDGET_SINGLE_AGENT_RATIO": 0.90,
        "FACTORY_LOG_LEVEL": "INFO",
        "FACTORY_LOG_JSON": True,
        "FACTORY_OPERATOR_STATUS_PATH": "artifacts/operator_status.json",
        "FACTORY_PROMOTION_REPORT_PATH": "artifacts/trade_ready_models.md",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Safe defaults
# ---------------------------------------------------------------------------

class TestStagingGuardsSafeDefaults:

    def test_paper_trading_disabled_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.paper_trading_enabled is False

    def test_live_trading_disabled_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.live_trading_enabled is False

    def test_live_trading_hard_disabled_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.live_trading_hard_disabled is True

    def test_autonomous_mutation_off_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.allow_autonomous_mutation is False

    def test_autonomous_paper_promotion_off_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.allow_autonomous_paper_promotion is False

    def test_max_active_families_is_1_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.max_active_families == 1

    def test_max_active_models_per_family_is_1_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.max_active_models_per_family == 1

    def test_max_challengers_per_family_is_1_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.max_challengers_per_family == 1

    def test_strict_budgets_off_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.strict_budgets is False

    def test_require_forwardtest_pass_on_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.require_forwardtest_pass is True

    def test_require_paper_evidence_for_challenger_on_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.require_paper_evidence_for_challenger is True

    def test_web_idea_research_disabled_by_default(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.web_idea_research_enabled is False


# ---------------------------------------------------------------------------
# Config value parsing
# ---------------------------------------------------------------------------

class TestStagingGuardsConfigParsing:

    def test_idea_source_file_read(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_IDEA_SOURCE_FILE="./MY_IDEAS.md"))
        assert g.idea_source_file == "./MY_IDEAS.md"

    def test_daily_budget_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_DAILY_INFERENCE_BUDGET_USD=20.0))
        assert g.daily_inference_budget_usd == pytest.approx(20.0)

    def test_weekly_budget_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_WEEKLY_INFERENCE_BUDGET_USD=100.0))
        assert g.weekly_inference_budget_usd == pytest.approx(100.0)

    def test_backtest_roi_threshold_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_BACKTEST_PASS_MONTHLY_ROI=0.08))
        assert g.backtest_pass_monthly_roi == pytest.approx(0.08)

    def test_scope_caps_override(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(
                FACTORY_MAX_ACTIVE_FAMILIES=3,
                FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY=2,
                FACTORY_MAX_CHALLENGERS_PER_FAMILY=2,
            )
        )
        assert g.max_active_families == 3
        assert g.max_active_models_per_family == 2
        assert g.max_challengers_per_family == 2

    def test_downgrade_ratios_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(
                FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO=0.65,
                FACTORY_BUDGET_FORCE_CHEAP_RATIO=0.75,
                FACTORY_BUDGET_SINGLE_AGENT_RATIO=0.85,
            )
        )
        assert g.budget_reviewer_removal_ratio == pytest.approx(0.65)
        assert g.budget_force_cheap_ratio == pytest.approx(0.75)
        assert g.budget_single_agent_ratio == pytest.approx(0.85)

    def test_log_level_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_LOG_LEVEL="DEBUG"))
        assert g.log_level == "DEBUG"

    def test_operator_status_path_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(FACTORY_OPERATOR_STATUS_PATH="/tmp/op_status.json")
        )
        assert g.operator_status_path == "/tmp/op_status.json"

    def test_promotion_report_path_parsed(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(FACTORY_PROMOTION_REPORT_PATH="/tmp/trade_ready.md")
        )
        assert g.promotion_report_path == "/tmp/trade_ready.md"


# ---------------------------------------------------------------------------
# Live trading hard-disable guard
# ---------------------------------------------------------------------------

class TestLiveTradingHardDisable:

    def test_live_trading_blocked_when_hard_disabled(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(
                FACTORY_ENABLE_LIVE_TRADING=True,   # operator tries to enable
                FACTORY_LIVE_TRADING_HARD_DISABLE=True,  # guard still blocks
            )
        )
        assert g.live_trading_blocked is True

    def test_live_trading_blocked_when_flag_off(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(FACTORY_ENABLE_LIVE_TRADING=False, FACTORY_LIVE_TRADING_HARD_DISABLE=False)
        )
        assert g.live_trading_blocked is True

    def test_live_trading_allowed_only_when_both_enabled(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(
                FACTORY_ENABLE_LIVE_TRADING=True,
                FACTORY_LIVE_TRADING_HARD_DISABLE=False,
            )
        )
        assert g.live_trading_blocked is False


# ---------------------------------------------------------------------------
# is_safe_for_preflight
# ---------------------------------------------------------------------------

class TestIsSafeForPreflight:

    def test_safe_with_all_defaults(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg())
        assert g.is_safe_for_preflight is True

    def test_unsafe_when_paper_enabled(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_ENABLE_PAPER_TRADING=True))
        assert g.is_safe_for_preflight is False

    def test_unsafe_when_autonomous_paper_promotion_on(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION=True))
        assert g.is_safe_for_preflight is False

    def test_unsafe_when_too_many_families(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(_make_cfg(FACTORY_MAX_ACTIVE_FAMILIES=10))
        assert g.is_safe_for_preflight is False

    def test_unsafe_when_live_not_blocked(self):
        from factory.config.staging_guards import load_staging_guards
        g = load_staging_guards(
            _make_cfg(
                FACTORY_ENABLE_LIVE_TRADING=True,
                FACTORY_LIVE_TRADING_HARD_DISABLE=False,
            )
        )
        assert g.is_safe_for_preflight is False


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------

class TestStagingGuardsToDict:

    def test_to_dict_contains_trading_section(self):
        from factory.config.staging_guards import load_staging_guards
        d = load_staging_guards(_make_cfg()).to_dict()
        assert "trading" in d
        assert d["trading"]["live_hard_disabled"] is True
        assert d["trading"]["paper_enabled"] is False

    def test_to_dict_contains_scope_section(self):
        from factory.config.staging_guards import load_staging_guards
        d = load_staging_guards(_make_cfg()).to_dict()
        assert d["scope"]["max_active_families"] == 1
        assert d["scope"]["max_challengers_per_family"] == 1

    def test_to_dict_contains_budget_section(self):
        from factory.config.staging_guards import load_staging_guards
        d = load_staging_guards(_make_cfg()).to_dict()
        assert "budget" in d
        assert d["budget"]["daily_usd"] == pytest.approx(15.0)
        assert d["budget"]["strict"] is False

    def test_to_dict_contains_is_safe_flag(self):
        from factory.config.staging_guards import load_staging_guards
        d = load_staging_guards(_make_cfg()).to_dict()
        assert d["is_safe_for_preflight"] is True


# ---------------------------------------------------------------------------
# OperatorStatus staging_guards integration
# ---------------------------------------------------------------------------

class TestOperatorStatusStagingGuards:

    def _make_rm(self, backend: str = "mobkit", healthy: bool = True) -> MagicMock:
        rm = MagicMock()
        rm.backend_name = backend
        rm.healthcheck.return_value = healthy
        del rm.governor
        return rm

    def test_staging_guards_absent_by_default(self):
        from factory.telemetry.correlation import build_operator_status
        status = build_operator_status(self._make_rm())
        assert status.staging_guards is None
        d = status.to_dict()
        assert "staging_guards" not in d

    def test_staging_guards_present_when_provided(self):
        from factory.config.staging_guards import load_staging_guards
        from factory.telemetry.correlation import build_operator_status
        guards = load_staging_guards(_make_cfg())
        status = build_operator_status(self._make_rm(), staging_guards=guards)
        assert status.staging_guards is not None
        d = status.to_dict()
        assert "staging_guards" in d
        assert d["staging_guards"]["is_safe_for_preflight"] is True

    def test_staging_guards_trading_visible_in_operator_status(self):
        from factory.config.staging_guards import load_staging_guards
        from factory.telemetry.correlation import build_operator_status
        guards = load_staging_guards(
            _make_cfg(FACTORY_ENABLE_PAPER_TRADING=False, FACTORY_LIVE_TRADING_HARD_DISABLE=True)
        )
        status = build_operator_status(self._make_rm(), staging_guards=guards)
        d = status.to_dict()
        trading = d["staging_guards"]["trading"]
        assert trading["paper_enabled"] is False
        assert trading["live_hard_disabled"] is True
        assert trading["live_blocked"] is True

    def test_staging_guards_error_does_not_break_status(self):
        from factory.telemetry.correlation import build_operator_status
        bad_guards = MagicMock()
        bad_guards.to_dict.side_effect = RuntimeError("broken")
        status = build_operator_status(self._make_rm(), staging_guards=bad_guards)
        # Should not raise; staging_guards becomes None
        assert status.staging_guards is None


# ---------------------------------------------------------------------------
# Downgrade threshold config wiring
# ---------------------------------------------------------------------------

class TestDowngradeThresholdsConfigurable:

    def test_reviewer_removal_ratio_reads_from_config(self):
        import importlib
        import factory.governance.downgrade_policy as dp_mod
        with patch.object(dp_mod, "_STEP_THRESHOLDS",
                          dp_mod._build_step_thresholds()):
            thresholds = dp_mod._build_step_thresholds()
        assert thresholds[dp_mod.DOWNGRADE_REMOVE_REVIEWERS] == pytest.approx(0.70)

    def test_force_cheap_ratio_reads_from_config(self):
        import factory.governance.downgrade_policy as dp_mod
        thresholds = dp_mod._build_step_thresholds()
        assert thresholds[dp_mod.DOWNGRADE_CHEAP_TIERS] == pytest.approx(0.80)

    def test_single_agent_ratio_reads_from_config(self):
        import factory.governance.downgrade_policy as dp_mod
        thresholds = dp_mod._build_step_thresholds()
        assert thresholds[dp_mod.DOWNGRADE_SINGLE_TASK] == pytest.approx(0.90)

    def test_custom_thresholds_applied(self):
        import types
        import factory.governance.downgrade_policy as dp_mod
        fake_cfg = types.SimpleNamespace(
            FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO=0.60,
            FACTORY_BUDGET_FORCE_CHEAP_RATIO=0.72,
            FACTORY_BUDGET_SINGLE_AGENT_RATIO=0.85,
        )
        with patch.object(dp_mod, "_cfg", fake_cfg):
            thresholds = dp_mod._build_step_thresholds()
        assert thresholds[dp_mod.DOWNGRADE_REMOVE_REVIEWERS] == pytest.approx(0.60)
        assert thresholds[dp_mod.DOWNGRADE_CHEAP_TIERS] == pytest.approx(0.72)
        assert thresholds[dp_mod.DOWNGRADE_SINGLE_TASK] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# config.py module-level vars
# ---------------------------------------------------------------------------

class TestConfigModuleVars:

    def test_new_vars_present_in_config_module(self):
        import config as cfg
        # Staging guards
        assert hasattr(cfg, "FACTORY_IDEA_SOURCE_FILE")
        assert hasattr(cfg, "FACTORY_ENABLE_WEB_IDEA_RESEARCH")
        assert hasattr(cfg, "FACTORY_ENABLE_PAPER_TRADING")
        assert hasattr(cfg, "FACTORY_ENABLE_LIVE_TRADING")
        assert hasattr(cfg, "FACTORY_LIVE_TRADING_HARD_DISABLE")
        assert hasattr(cfg, "FACTORY_MAX_ACTIVE_FAMILIES")
        assert hasattr(cfg, "FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY")
        assert hasattr(cfg, "FACTORY_MAX_CHALLENGERS_PER_FAMILY")
        assert hasattr(cfg, "FACTORY_ALLOW_AUTONOMOUS_MUTATION")
        assert hasattr(cfg, "FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION")
        assert hasattr(cfg, "FACTORY_BACKTEST_PASS_MONTHLY_ROI")
        assert hasattr(cfg, "FACTORY_REQUIRE_FORWARDTEST_PASS")
        assert hasattr(cfg, "FACTORY_REQUIRE_PAPER_EVIDENCE_FOR_CHALLENGER")
        assert hasattr(cfg, "FACTORY_DAILY_INFERENCE_BUDGET_USD")
        assert hasattr(cfg, "FACTORY_WEEKLY_INFERENCE_BUDGET_USD")
        assert hasattr(cfg, "FACTORY_STRICT_BUDGETS")
        assert hasattr(cfg, "FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO")
        assert hasattr(cfg, "FACTORY_BUDGET_FORCE_CHEAP_RATIO")
        assert hasattr(cfg, "FACTORY_BUDGET_SINGLE_AGENT_RATIO")
        assert hasattr(cfg, "FACTORY_LOG_LEVEL")
        assert hasattr(cfg, "FACTORY_LOG_JSON")
        assert hasattr(cfg, "FACTORY_OPERATOR_STATUS_PATH")
        assert hasattr(cfg, "FACTORY_PROMOTION_REPORT_PATH")
        # Goldfish identity
        assert hasattr(cfg, "GOLDFISH_PROJECT_NAME")
        assert hasattr(cfg, "GOLDFISH_WORKSPACE_ROOT")
        assert hasattr(cfg, "GOLDFISH_ARTEFACT_ROOT")
        assert hasattr(cfg, "FACTORY_GOLDFISH_STRICT_MODE")

    def test_safe_defaults_in_config_module(self):
        import config as cfg
        assert cfg.FACTORY_ENABLE_PAPER_TRADING is False
        assert cfg.FACTORY_ENABLE_LIVE_TRADING is False
        assert cfg.FACTORY_LIVE_TRADING_HARD_DISABLE is True
        assert cfg.FACTORY_MAX_ACTIVE_FAMILIES == 1
        assert cfg.FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY == 1
        assert cfg.FACTORY_MAX_CHALLENGERS_PER_FAMILY == 1
        assert cfg.FACTORY_ALLOW_AUTONOMOUS_MUTATION is False
        assert cfg.FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION is False
        assert cfg.FACTORY_STRICT_BUDGETS is False
        assert cfg.FACTORY_GOLDFISH_STRICT_MODE is False
        assert cfg.FACTORY_LOG_LEVEL == "INFO"
        assert cfg.FACTORY_LOG_JSON is True
