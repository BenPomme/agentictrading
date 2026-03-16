from __future__ import annotations

import sys
from pathlib import Path

import pytest

import config


def execution_repo_root() -> Path | None:
    return None


@pytest.fixture(autouse=True)
def _disable_real_agents_by_default(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_REAL_AGENTS_ENABLED", False)
    monkeypatch.setattr(config, "FACTORY_AGENT_PROVIDER_ORDER", "deterministic")
    monkeypatch.setattr(config, "FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED", False)
    monkeypatch.setattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", False)
