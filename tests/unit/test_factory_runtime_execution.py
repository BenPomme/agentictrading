from __future__ import annotations

import importlib

import config


def _reload_runtime_execution():
    import factory.execution_manager as runtime_execution

    importlib.reload(runtime_execution)
    return runtime_execution


def test_legacy_portfolios_default_enabled_without_environment_flags(monkeypatch):
    monkeypatch.delenv("CASCADE_ALPHA_ENABLED", raising=False)
    monkeypatch.delenv("CONTRARIAN_LEGACY_ENABLED", raising=False)
    monkeypatch.delenv("POLYMARKET_QF_ENABLED", raising=False)
    runtime_execution = _reload_runtime_execution()
    assert runtime_execution.get_runtime_portfolio_spec("cascade_alpha").enabled is True
    assert runtime_execution.get_runtime_portfolio_spec("contrarian_legacy").enabled is True
    assert runtime_execution.get_runtime_portfolio_spec("polymarket_quantum_fold").enabled is True


def test_legacy_portfolios_respect_explicit_disable_flag(monkeypatch):
    monkeypatch.setenv("CASCADE_ALPHA_ENABLED", "false")
    monkeypatch.setenv("CONTRARIAN_LEGACY_ENABLED", "false")
    monkeypatch.setenv("POLYMARKET_QF_ENABLED", "false")
    runtime_execution = _reload_runtime_execution()
    assert runtime_execution.get_runtime_portfolio_spec("cascade_alpha").enabled is False
    assert runtime_execution.get_runtime_portfolio_spec("contrarian_legacy").enabled is False
    assert runtime_execution.get_runtime_portfolio_spec("polymarket_quantum_fold").enabled is False
