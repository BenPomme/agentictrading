from __future__ import annotations

import sys
from pathlib import Path

import pytest

import config


def execution_repo_root() -> Path | None:
    raw = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.exists():
        return None
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


@pytest.fixture(autouse=True)
def _disable_real_agents_by_default(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", False)
    monkeypatch.setattr(config, "FACTORY_AGENT_PROVIDER_ORDER", "deterministic")
    monkeypatch.setattr(config, "FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED", False)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", False)
