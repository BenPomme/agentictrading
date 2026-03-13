from __future__ import annotations

import json
import sys
from pathlib import Path

import config
from factory.execution_refresh import ExecutionRefreshRunner


def test_execution_refresh_runner_invokes_explicit_adapter(monkeypatch, tmp_path):
    execution_root = tmp_path / "execution"
    script_dir = execution_root / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "factory_refresh_models.py"
    script_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import argparse",
                "import json",
                "from pathlib import Path",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--request', required=True)",
                "parser.add_argument('--output', required=True)",
                "args = parser.parse_args()",
                "request = json.loads(Path(args.request).read_text(encoding='utf-8'))",
                "payload = {'status': 'success', 'family_id': request.get('family_id'), 'selected_model': 'xgboost', 'artifact_path': 'data/funding_models/contrarian_comparison.json'}",
                "Path(args.output).write_text(json.dumps(payload), encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "output.json"
    request_path.write_text(json.dumps({"family_id": "binance_funding_contrarian"}), encoding="utf-8")

    monkeypatch.setattr(config, "EXECUTION_REPO_ROOT", str(execution_root))
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "binance_funding_contrarian")
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_PYTHON_BIN", sys.executable)
    runner = ExecutionRefreshRunner(tmp_path)

    assert runner.should_run(family_id="binance_funding_contrarian", role="champion") is True
    result = runner.run(request_path=request_path, output_path=output_path)

    assert result["status"] == "success"
    assert result["selected_model"] == "xgboost"
    assert result["returncode"] == 0
    assert output_path.exists()


def test_execution_refresh_skips_embedded_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXECUTION_REPO_ROOT", "")
    monkeypatch.setattr(config, "FACTORY_EMBEDDED_EXECUTION_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "binance_funding_contrarian")
    runner = ExecutionRefreshRunner(tmp_path)
    result = runner.run(request_path=tmp_path / "req.json", output_path=tmp_path / "out.json")
    assert result["status"] == "skipped"
    assert result["reason"] == "embedded_mode_refresh_skipped"


def test_execution_refresh_skips_no_execution_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EXECUTION_REPO_ROOT", "")
    monkeypatch.setattr(config, "FACTORY_EMBEDDED_EXECUTION_ENABLED", False)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "binance_funding_contrarian")
    runner = ExecutionRefreshRunner(tmp_path)
    result = runner.run(request_path=tmp_path / "req.json", output_path=tmp_path / "out.json")
    assert result["status"] == "skipped"
    assert result["reason"] == "execution_repo_not_configured"
