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
