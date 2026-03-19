from __future__ import annotations

import config
from factory.execution_refresh import ExecutionRefreshRunner


def test_execution_refresh_always_skips_in_standalone_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "binance_funding_contrarian")
    
    runner = ExecutionRefreshRunner(tmp_path)
    result = runner.run(request_path=tmp_path / "req.json", output_path=tmp_path / "out.json")
    
    assert result["status"] == "skipped"
    assert result["reason"] == "standalone_embedded_mode"


def test_execution_refresh_allows_new_families_when_no_allowlist_is_set(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "")

    runner = ExecutionRefreshRunner(tmp_path)

    assert runner.should_run(family_id="fam_crypto_basis_carry_001", role="champion") is True
    assert runner.should_run(family_id="fam_crypto_basis_carry_001", role="challenger") is False
