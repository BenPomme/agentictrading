"""Tests for Task 01: runtime adapter scaffolding.

Covers:
- RuntimeManager selects legacy backend by default
- RuntimeManager stays on legacy when mobkit flag is off
- RuntimeManager falls back to legacy when mobkit is not yet implemented
- AgentRunEnvelope serializes all required fields
- LegacyRuntime satisfies the AgentRuntime protocol
- Orchestrator obtains its runtime via RuntimeManager (not direct import)
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from factory.runtime.legacy_runtime import BACKEND_NAME as LEGACY_BACKEND_NAME
from factory.runtime.legacy_runtime import LegacyRuntime
from factory.runtime.runtime_contracts import (
    AgentRunEnvelope,
    RuntimeBudgetDecision,
    RuntimeMemberTrace,
    RuntimeUsage,
)
from factory.runtime.runtime_manager import (
    BACKEND_LEGACY,
    BACKEND_MOBKIT,
    RuntimeManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "factory" / "agent_runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# RuntimeManager selection tests
# ---------------------------------------------------------------------------

class TestRuntimeManagerSelection:
    def test_default_is_legacy(self, tmp_path):
        """No config overrides → legacy backend selected."""
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        assert manager.backend_name == BACKEND_LEGACY

    def test_legacy_healthcheck_is_true(self, tmp_path):
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        assert manager.healthcheck() is True

    def test_mobkit_without_enable_flag_falls_back_to_legacy(self, tmp_path):
        """FACTORY_RUNTIME_BACKEND=mobkit but FACTORY_ENABLE_MOBKIT=false → still legacy."""
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "mobkit"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        assert manager.backend_name == BACKEND_LEGACY

    def test_mobkit_with_enable_flag_falls_back_until_task03(self, tmp_path):
        """mobkit + enable=true → falls back to legacy because Task 03 not done yet."""
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "mobkit"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", True):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        # Task 03 not implemented → still legacy
        assert manager.backend_name == BACKEND_LEGACY

    def test_unknown_backend_falls_back_to_legacy(self, tmp_path):
        """Unknown backend name → legacy with warning."""
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "does_not_exist"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        assert manager.backend_name == BACKEND_LEGACY

    def test_runtime_property_returns_legacy_instance(self, tmp_path):
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(_tmp_root(tmp_path))
        assert isinstance(manager.runtime, LegacyRuntime)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestLegacyRuntimeProtocol:
    def test_legacy_runtime_has_all_required_methods(self, tmp_path):
        runtime = LegacyRuntime(_tmp_root(tmp_path))
        required = [
            "generate_proposal",
            "generate_family_proposal",
            "suggest_tweak",
            "critique_post_evaluation",
            "diagnose_bug",
            "resolve_maintenance_item",
            "design_model",
            "mutate_model",
        ]
        for method in required:
            assert hasattr(runtime, method), f"LegacyRuntime missing method: {method}"
            assert callable(getattr(runtime, method))

    def test_legacy_runtime_backend_name(self, tmp_path):
        runtime = LegacyRuntime(_tmp_root(tmp_path))
        assert runtime.backend_name == LEGACY_BACKEND_NAME == "legacy"


# ---------------------------------------------------------------------------
# AgentRunEnvelope contract tests
# ---------------------------------------------------------------------------

class TestAgentRunEnvelope:
    def _sample(self) -> AgentRunEnvelope:
        now = datetime.now(timezone.utc)
        return AgentRunEnvelope(
            run_id="run-001",
            trace_id="trace-abc",
            backend="legacy",
            task_type="proposal_generation",
            success=True,
            payload={"hypothesis": "test"},
            started_at=now,
            finished_at=now,
        )

    def test_required_fields_present_in_to_dict(self):
        env = self._sample()
        d = env.to_dict()
        required_keys = [
            "run_id", "trace_id", "backend", "task_type", "success",
            "payload", "started_at", "finished_at", "duration_ms",
            "provider", "model", "raw_text", "usage", "member_traces",
            "budget_decision", "fallback_reason", "fallback_used",
            "family_id", "lineage_id", "error",
        ]
        for key in required_keys:
            assert key in d, f"AgentRunEnvelope.to_dict() missing key: {key}"

    def test_duration_ms_is_non_negative(self):
        env = self._sample()
        assert env.duration_ms() >= 0

    def test_member_traces_default_empty(self):
        env = self._sample()
        assert env.member_traces == []

    def test_fallback_fields_default_false_none(self):
        env = self._sample()
        assert env.fallback_used is False
        assert env.fallback_reason is None

    def test_to_dict_with_usage(self):
        env = self._sample()
        env.usage = RuntimeUsage(input_tokens=100, output_tokens=50, total_tokens=150)
        d = env.to_dict()
        assert d["usage"]["total_tokens"] == 150

    def test_to_dict_with_member_trace(self):
        env = self._sample()
        env.member_traces = [
            RuntimeMemberTrace(
                member_id="m1",
                role="critic",
                model="gpt-4.1-nano",
                success=True,
                usage=RuntimeUsage(total_tokens=80),
            )
        ]
        d = env.to_dict()
        assert len(d["member_traces"]) == 1
        assert d["member_traces"][0]["role"] == "critic"

    def test_to_dict_with_budget_decision(self):
        env = self._sample()
        env.budget_decision = RuntimeBudgetDecision(allowed=True, scope="task")
        d = env.to_dict()
        assert d["budget_decision"]["allowed"] is True
        assert d["budget_decision"]["scope"] == "task"


# ---------------------------------------------------------------------------
# RuntimeUsage contract
# ---------------------------------------------------------------------------

class TestRuntimeUsage:
    def test_defaults_zero(self):
        u = RuntimeUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.total_tokens == 0
        assert u.estimated_cost_usd is None

    def test_to_dict(self):
        u = RuntimeUsage(input_tokens=10, output_tokens=20, total_tokens=30, estimated_cost_usd=0.001)
        d = u.to_dict()
        assert d["total_tokens"] == 30
        assert d["estimated_cost_usd"] == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# Orchestrator wiring check
# ---------------------------------------------------------------------------

class TestOrchestratorWiring:
    def test_orchestrator_uses_runtime_manager(self, tmp_path):
        """
        FactoryOrchestrator must obtain agent_runtime via RuntimeManager,
        not by directly instantiating RealResearchAgentRuntime.
        """
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False), \
             patch.object(config, "FACTORY_ROOT", str(root / "data" / "factory")), \
             patch.object(config, "FACTORY_GOLDFISH_ROOT", str(root / "research" / "goldfish")):
            from factory.orchestrator import FactoryOrchestrator
            orch = FactoryOrchestrator(root)

        # agent_runtime must be a LegacyRuntime (not raw RealResearchAgentRuntime)
        assert isinstance(orch.agent_runtime, LegacyRuntime), (
            f"Expected LegacyRuntime, got {type(orch.agent_runtime)}"
        )
        # _runtime_manager must be a RuntimeManager
        assert isinstance(orch._runtime_manager, RuntimeManager)

    def test_orchestrator_backend_name_is_legacy_by_default(self, tmp_path):
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False), \
             patch.object(config, "FACTORY_ROOT", str(root / "data" / "factory")), \
             patch.object(config, "FACTORY_GOLDFISH_ROOT", str(root / "research" / "goldfish")):
            from factory.orchestrator import FactoryOrchestrator
            orch = FactoryOrchestrator(root)
        assert orch._runtime_manager.backend_name == "legacy"
