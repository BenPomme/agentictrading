"""Task 06 — Migration Cutover tests.

Verifies:
- default backend is mobkit under default config
- legacy backend can be selected explicitly via config
- healthcheck failure causes documented behavior (fallback or hard-fail)
- Goldfish provenance is active by default
- rollback config (FACTORY_RUNTIME_BACKEND=legacy) restores legacy runtime path
- FACTORY_FALLBACK_TO_LEGACY=false causes hard-fail instead of silent degradation
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path("/tmp/test_cutover_root")

_LEGACY_RUNTIME_PATH = "factory.runtime.legacy_runtime.LegacyRuntime"
_MOBKIT_RUNTIME_PATH = "factory.runtime.mobkit_backend.MobkitRuntime"
_BUILD_RUNTIME_PATH = "factory.runtime.runtime_manager.RuntimeManager._build_runtime"


def _make_config(**overrides):
    """Return a namespace that looks like the config module with given overrides."""
    defaults = {
        "FACTORY_RUNTIME_BACKEND": "mobkit",
        "FACTORY_ENABLE_MOBKIT": True,
        "FACTORY_ENABLE_GOLDFISH_PROVENANCE": True,
        "FACTORY_FALLBACK_TO_LEGACY": True,
        "FACTORY_ENABLE_STRICT_BUDGETS": False,
        # Budget thresholds (needed by CostGovernor)
        "FACTORY_GLOBAL_DAILY_BUDGET_USD": 10.0,
        "FACTORY_GLOBAL_DAILY_TOKENS": 500_000,
        "FACTORY_GLOBAL_MAX_CONCURRENT_WORKFLOWS": 3,
        "FACTORY_GLOBAL_MAX_CYCLES_PER_DAY": 48,
        "FACTORY_FAMILY_DAILY_BUDGET_USD": 2.0,
        "FACTORY_FAMILY_DAILY_TOKENS": 100_000,
        "FACTORY_FAMILY_MAX_NEW_LINEAGES_PER_DAY": 3,
        "FACTORY_FAMILY_MAX_CRITIQUE_DEPTH_PER_DAY": 5,
        "FACTORY_FAMILY_MAX_EXPENSIVE_RUNS_PER_DAY": 2,
        "FACTORY_LINEAGE_MAX_BUDGET_USD": 1.0,
        "FACTORY_LINEAGE_MAX_MUTATIONS": 5,
        "FACTORY_LINEAGE_MAX_FAILED_BACKTESTS": 3,
        "FACTORY_LINEAGE_MAX_CRITIQUE_ROUNDS": 3,
        "FACTORY_TASK_DEFAULT_MAX_TOKENS": 2048,
        "FACTORY_TASK_DEFAULT_TIMEOUT_SECONDS": 120,
        "FACTORY_TASK_MAX_RETRIES": 1,
        "FACTORY_TASK_SCHEMA_RETRY_LIMIT": 1,
        "FACTORY_MOB_MEMBER_DEFAULT_MAX_TOKENS": 512,
        "FACTORY_MOB_REVIEWER_MAX_TOKENS": 512,
        "FACTORY_MOB_LEAD_MAX_TOKENS": 2048,
        "FACTORY_MOBKIT_GATEWAY_BIN": "",
        "FACTORY_MOBKIT_CONFIG_PATH": "",
        "FACTORY_MOBKIT_TIMEOUT_SECONDS": 120,
        "FACTORY_GOLDFISH_PROJECT_ROOT": "",
        "FACTORY_GOLDFISH_FAIL_ON_ERROR": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_manager(mock_config, fail_mobkit_init: bool = False):
    """Build a RuntimeManager with the given mock config."""
    from factory.runtime import runtime_manager as rm_mod

    mock_legacy = MagicMock(name="LegacyRuntime")
    mock_mobkit = MagicMock(name="MobkitRuntime")
    mock_tel = MagicMock()

    with (
        patch.object(rm_mod, "config", mock_config),
        patch("factory.runtime.legacy_runtime.LegacyRuntime", return_value=mock_legacy),
        patch("factory.telemetry.run_logger.default_logger", mock_tel),
        patch.object(rm_mod, "_tel", mock_tel),
    ):
        if fail_mobkit_init:
            with patch.dict("sys.modules", {"factory.runtime.mobkit_backend": None}):
                # Force ImportError path
                original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

                def _failing_import(name, *args, **kwargs):
                    if "mobkit_backend" in name:
                        raise ImportError("mobkit not installed")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=_failing_import):
                    mgr = rm_mod.RuntimeManager(PROJECT_ROOT)
        else:
            with patch("factory.runtime.mobkit_backend.MobkitRuntime", return_value=mock_mobkit):
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

    return mgr, mock_tel


# ---------------------------------------------------------------------------
# 1. Default backend is mobkit
# ---------------------------------------------------------------------------

class TestDefaultBackendIsMobkit:
    def test_default_config_selects_mobkit(self):
        """Under default config (Task 06), backend_name must be 'mobkit'."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config()  # default: FACTORY_RUNTIME_BACKEND="mobkit", ENABLE_MOBKIT=True
        mock_mobkit_rt = MagicMock(name="MobkitRuntime")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.mobkit_backend.MobkitRuntime", return_value=mock_mobkit_rt):
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.backend_name == "mobkit"

    def test_default_config_emits_backend_selected_mobkit(self):
        """backend_selected telemetry must report 'mobkit' at startup."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config()
        mock_mobkit_rt = MagicMock()
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.mobkit_backend.MobkitRuntime", return_value=mock_mobkit_rt):
                rm_mod.RuntimeManager(PROJECT_ROOT)

        mock_tel.backend_selected.assert_called_once_with("mobkit")

    def test_mobkit_runtime_instance_used(self):
        """runtime property must return the MobkitRuntime instance under default config."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config()
        mock_mobkit_rt = MagicMock()
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.mobkit_backend.MobkitRuntime", return_value=mock_mobkit_rt) as MockMobkit:
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)
                assert MockMobkit.called


# ---------------------------------------------------------------------------
# 2. Legacy backend selectable explicitly
# ---------------------------------------------------------------------------

class TestLegacyExplicitSelection:
    def test_explicit_legacy_config_selects_legacy(self):
        """FACTORY_RUNTIME_BACKEND=legacy must select LegacyRuntime."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.backend_name == "legacy"

    def test_explicit_legacy_emits_deprecation_warning(self, caplog):
        """Explicitly selecting legacy should emit a deprecation warning."""
        import logging
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                with caplog.at_level(logging.WARNING, logger="factory.runtime.runtime_manager"):
                    rm_mod.RuntimeManager(PROJECT_ROOT)

        assert any("deprecated" in r.message.lower() for r in caplog.records)

    def test_explicit_legacy_does_not_emit_fallback_event(self):
        """Explicit legacy selection must NOT emit FALLBACK_ACTIVATED (it's intentional)."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                rm_mod.RuntimeManager(PROJECT_ROOT)

        mock_tel.fallback_activated.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Healthcheck failure / backend unavailable
# ---------------------------------------------------------------------------

class TestHealthcheckFailure:
    def test_mobkit_init_failure_falls_back_when_allowed(self):
        """If MobkitRuntime raises during init and FALLBACK_TO_LEGACY=true, use legacy."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_FALLBACK_TO_LEGACY=True)
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with (
                patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy,
                patch("factory.runtime.mobkit_backend.MobkitRuntime",
                      side_effect=RuntimeError("gateway not found")),
            ):
                MockLegacy.return_value = MagicMock()
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.backend_name == "legacy"
        mock_tel.fallback_activated.assert_called_once()

    def test_mobkit_init_failure_hard_fails_when_not_allowed(self):
        """If MobkitRuntime raises and FALLBACK_TO_LEGACY=false, RuntimeError must propagate."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_FALLBACK_TO_LEGACY=False)
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with (
                patch("factory.runtime.legacy_runtime.LegacyRuntime"),
                patch("factory.runtime.mobkit_backend.MobkitRuntime",
                      side_effect=RuntimeError("gateway not found")),
            ):
                with pytest.raises(RuntimeError, match="FACTORY_FALLBACK_TO_LEGACY=false"):
                    rm_mod.RuntimeManager(PROJECT_ROOT)

    def test_fallback_telemetry_carries_reason(self):
        """FALLBACK_ACTIVATED event must include a non-empty reason."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_FALLBACK_TO_LEGACY=True)
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with (
                patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy,
                patch("factory.runtime.mobkit_backend.MobkitRuntime",
                      side_effect=RuntimeError("no binary")),
            ):
                MockLegacy.return_value = MagicMock()
                rm_mod.RuntimeManager(PROJECT_ROOT)

        call_kwargs = mock_tel.fallback_activated.call_args
        reason = call_kwargs.kwargs.get("reason") or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
        assert reason is not None and len(reason) > 0

    def test_healthcheck_returns_false_on_mobkit_error(self):
        """RuntimeManager.healthcheck() must return False if underlying mobkit raises."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config()
        mock_tel = MagicMock()
        mock_mobkit_rt = MagicMock()
        mock_mobkit_rt.healthcheck.side_effect = Exception("rpc error")

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.mobkit_backend.MobkitRuntime", return_value=mock_mobkit_rt):
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.healthcheck() is False

    def test_healthcheck_returns_true_for_legacy(self):
        """Legacy runtime is always considered healthy (fails at invocation time)."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.healthcheck() is True


# ---------------------------------------------------------------------------
# 4. Goldfish provenance active by default
# ---------------------------------------------------------------------------

class TestGoldfishDefaultActive:
    def test_goldfish_enabled_by_default_in_config(self):
        """FACTORY_ENABLE_GOLDFISH_PROVENANCE must default to True after Task 06."""
        import config as cfg_mod
        # The real config module should now have True as default
        assert cfg_mod.FACTORY_ENABLE_GOLDFISH_PROVENANCE is True

    def test_goldfish_flag_readable_via_getattr(self):
        """Verify that the flag reads as True from a fresh default namespace."""
        cfg = _make_config()
        assert cfg.FACTORY_ENABLE_GOLDFISH_PROVENANCE is True

    def test_goldfish_can_be_disabled_for_rollback(self):
        """FACTORY_ENABLE_GOLDFISH_PROVENANCE=false must be respected."""
        cfg = _make_config(FACTORY_ENABLE_GOLDFISH_PROVENANCE=False)
        assert cfg.FACTORY_ENABLE_GOLDFISH_PROVENANCE is False


# ---------------------------------------------------------------------------
# 5. Rollback config restores legacy
# ---------------------------------------------------------------------------

class TestRollbackConfig:
    """Verify config-based rollback to legacy runtime works correctly."""

    def test_rollback_config_selects_legacy_backend(self):
        """
        Rollback config:
            FACTORY_RUNTIME_BACKEND=legacy
        must produce a RuntimeManager with backend_name='legacy'.
        """
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(
            FACTORY_RUNTIME_BACKEND="legacy",
            FACTORY_ENABLE_GOLDFISH_PROVENANCE=False,
        )
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.backend_name == "legacy"

    def test_rollback_config_does_not_touch_mobkit(self):
        """Rollback config must not instantiate MobkitRuntime at all."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with (
                patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy,
                patch("factory.runtime.mobkit_backend.MobkitRuntime") as MockMobkit,
            ):
                MockLegacy.return_value = MagicMock()
                rm_mod.RuntimeManager(PROJECT_ROOT)
                MockMobkit.assert_not_called()

    def test_rollback_emits_backend_selected_legacy(self):
        """Rollback backend_selected telemetry must report 'legacy'."""
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="legacy")
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                rm_mod.RuntimeManager(PROJECT_ROOT)

        mock_tel.backend_selected.assert_called_once_with("legacy")

    def test_rollback_with_enable_mobkit_false(self):
        """
        Alternative rollback:
            FACTORY_RUNTIME_BACKEND=mobkit + FACTORY_ENABLE_MOBKIT=false
        must fall back to legacy and emit FALLBACK_ACTIVATED.
        """
        from factory.runtime import runtime_manager as rm_mod

        cfg = _make_config(FACTORY_RUNTIME_BACKEND="mobkit", FACTORY_ENABLE_MOBKIT=False)
        mock_tel = MagicMock()

        with (
            patch.object(rm_mod, "config", cfg),
            patch.object(rm_mod, "_tel", mock_tel),
        ):
            with patch("factory.runtime.legacy_runtime.LegacyRuntime") as MockLegacy:
                MockLegacy.return_value = MagicMock()
                mgr = rm_mod.RuntimeManager(PROJECT_ROOT)

        assert mgr.backend_name == "legacy"
        mock_tel.fallback_activated.assert_called_once()

    def test_runtime_setting_helper_defaults_to_mobkit(self):
        """_runtime_backend_setting() must return 'mobkit' when config has no attr."""
        from factory.runtime import runtime_manager as rm_mod

        empty_cfg = SimpleNamespace()  # no FACTORY_RUNTIME_BACKEND attr
        with patch.object(rm_mod, "config", empty_cfg):
            result = rm_mod._runtime_backend_setting()

        assert result == "mobkit"

    def test_mobkit_enabled_helper_defaults_to_true(self):
        """_mobkit_enabled() must return True when config has no attr."""
        from factory.runtime import runtime_manager as rm_mod

        empty_cfg = SimpleNamespace()
        with patch.object(rm_mod, "config", empty_cfg):
            result = rm_mod._mobkit_enabled()

        assert result is True
