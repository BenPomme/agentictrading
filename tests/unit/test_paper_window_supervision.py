from __future__ import annotations

from pathlib import Path


def test_paper_window_launcher_uses_dashboard_server_script():
    source = Path("scripts/run_autonomous_paper_window.py").read_text(encoding="utf-8")

    assert 'project_root / "scripts" / "factory_dashboard.py"' in source
    assert 'project_root / "factory" / "operator_dashboard.py"' not in source
