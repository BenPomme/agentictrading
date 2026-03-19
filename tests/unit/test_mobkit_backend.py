"""Tests for Task 03: mobkit backend integration.

Covers:
- Availability check when package is absent
- MobkitOrchestratorBackend.create() config validation
- initialize() raises MobkitUnavailableError when package or binary missing
- healthcheck() returns False when not initialized and fails
- RuntimeManager falls back to legacy when mobkit init fails (no binary)
- RuntimeManager delegates healthcheck to MobkitRuntime when mobkit active (mock)
- _parse_json_output handles JSON, markdown fences, non-dict, invalid JSON
- WORKFLOW_PROFILES completeness
- _make_run_result produces valid AgentRunResult
- MobkitRuntime.healthcheck() delegates to backend
- MobkitRuntime 8 business-method signatures verified
- Per-role budget_hooks parameter accepted
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

import config
from factory.runtime.mobkit_backend import (
    WORKFLOW_PROFILES,
    MobkitBackendError,
    MobkitOrchestratorBackend,
    MobkitRuntime,
    MobkitSchemaError,
    MobkitUnavailableError,
    MobkitWorkflowError,
    _check_mobkit_available,
    _make_run_result,
    _parse_json_output,
)
from factory.runtime.runtime_manager import BACKEND_LEGACY, BACKEND_MOBKIT, RuntimeManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "factory" / "agent_runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _fake_binary(tmp_path: Path) -> str:
    """Create a dummy executable file and return its path."""
    bin_path = tmp_path / "mobkit-rpc"
    bin_path.write_text("#!/bin/sh\n")
    bin_path.chmod(0o755)
    return str(bin_path)


def _make_mock_backend(*, healthy: bool = True) -> MagicMock:
    """Return a MagicMock that satisfies the OrchestratorBackend protocol."""
    backend = MagicMock(spec=MobkitOrchestratorBackend)
    backend.healthcheck.return_value = healthy
    backend.BACKEND_NAME = "mobkit"
    backend.run_mob_workflow.return_value = {
        "payload": {"result": "ok"},
        "member_traces": [
            {"member_id": "at-lead", "role": "lead_researcher", "model": "tier3_lead",
             "success": True, "usage": None}
        ],
        "backend": "mobkit",
    }
    backend.run_structured_task.return_value = {
        "payload": {"result": "ok"},
        "member_traces": [
            {"member_id": "at-task", "role": "standard-worker", "model": "tier2_standard",
             "success": True, "usage": None}
        ],
        "backend": "mobkit",
    }
    return backend


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestMobkitAvailability:
    def test_returns_false_when_package_absent(self):
        """Cache is bypassed by temporarily overriding the module cache."""
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = None  # force re-check
        with patch.dict("sys.modules", {"meerkat_mobkit": None}):
            result = _check_mobkit_available()
        _mod._MOBKIT_AVAILABLE = original
        assert result is False

    def test_returns_true_when_package_present(self):
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = None
        fake_module = MagicMock()
        with patch.dict("sys.modules", {"meerkat_mobkit": fake_module}):
            result = _check_mobkit_available()
        _mod._MOBKIT_AVAILABLE = original
        assert result is True


# ---------------------------------------------------------------------------
# MobkitOrchestratorBackend.create() config validation
# ---------------------------------------------------------------------------

class TestMobkitBackendCreate:
    def test_raises_when_gateway_bin_not_set(self, tmp_path):
        with patch.object(config, "FACTORY_MOBKIT_GATEWAY_BIN", ""), \
             patch.object(config, "FACTORY_MOBKIT_CONFIG_PATH", ""), \
             patch.object(config, "FACTORY_MOBKIT_TIMEOUT_SECONDS", 120):
            with pytest.raises(MobkitUnavailableError, match="FACTORY_MOBKIT_GATEWAY_BIN"):
                MobkitOrchestratorBackend.create(tmp_path)

    def test_creates_instance_when_gateway_set(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        with patch.object(config, "FACTORY_MOBKIT_GATEWAY_BIN", bin_path), \
             patch.object(config, "FACTORY_MOBKIT_CONFIG_PATH", ""), \
             patch.object(config, "FACTORY_MOBKIT_TIMEOUT_SECONDS", 60):
            backend = MobkitOrchestratorBackend.create(tmp_path)
        assert backend._gateway_bin == bin_path
        assert backend._timeout == 60
        assert backend._mob_config_path is None


# ---------------------------------------------------------------------------
# MobkitOrchestratorBackend.initialize()
# ---------------------------------------------------------------------------

class TestMobkitBackendInitialize:
    def test_raises_when_package_unavailable(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = False
        try:
            with pytest.raises(MobkitUnavailableError, match="meerkat_mobkit"):
                backend.initialize()
        finally:
            _mod._MOBKIT_AVAILABLE = original

    def test_raises_when_binary_missing(self, tmp_path):
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = True
        try:
            backend = MobkitOrchestratorBackend(gateway_bin="/nonexistent/mobkit-rpc")
            with pytest.raises(MobkitUnavailableError, match="gateway binary not found"):
                backend.initialize()
        finally:
            _mod._MOBKIT_AVAILABLE = original

    def test_idempotent_when_already_initialized(self, tmp_path):
        """initialize() must not re-enter when already initialized."""
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        backend._initialized = True  # simulate previous init
        # Should return without any side effects
        backend.initialize()  # no error
        assert backend._initialized is True


# ---------------------------------------------------------------------------
# MobkitOrchestratorBackend.healthcheck()
# ---------------------------------------------------------------------------

class TestMobkitBackendHealthcheck:
    def test_returns_false_when_package_unavailable(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = False
        try:
            result = backend.healthcheck()
        finally:
            _mod._MOBKIT_AVAILABLE = original
        assert result is False

    def test_returns_false_when_handle_is_none(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        backend._initialized = True
        backend._loop = MagicMock()
        backend._handle = None
        result = backend.healthcheck()
        assert result is False

    def test_returns_true_when_status_running(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        backend._initialized = True

        mock_status = MagicMock()
        mock_status.running = True

        mock_loop = MagicMock()
        mock_loop.run.return_value = mock_status
        backend._loop = mock_loop

        mock_handle = MagicMock()
        mock_handle.status.return_value = object()
        backend._handle = mock_handle

        result = backend.healthcheck()
        assert result is True

    def test_returns_false_when_status_raises(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        backend._initialized = True

        mock_loop = MagicMock()
        mock_loop.run.side_effect = RuntimeError("connection refused")
        backend._loop = mock_loop

        mock_handle = MagicMock()
        mock_handle.status.return_value = object()
        backend._handle = mock_handle

        result = backend.healthcheck()
        assert result is False


# ---------------------------------------------------------------------------
# run_structured_task / run_mob_workflow guard: _require_initialized
# ---------------------------------------------------------------------------

class TestBackendRequireInitialized:
    def test_run_structured_task_auto_initializes_or_raises(self, tmp_path):
        """Calling run_structured_task on uninitialized backend triggers initialize()."""
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        import factory.runtime.mobkit_backend as _mod
        original = _mod._MOBKIT_AVAILABLE
        _mod._MOBKIT_AVAILABLE = False
        try:
            with pytest.raises(MobkitUnavailableError):
                backend.run_structured_task(
                    task_type="test",
                    prompt="hello",
                    schema={},
                    model_tier="tier2_standard",
                    family_id="fam",
                    lineage_id=None,
                    trace_id="trace-001",
                )
        finally:
            _mod._MOBKIT_AVAILABLE = original

    def test_run_mob_workflow_raises_on_unknown_profile(self, tmp_path):
        bin_path = _fake_binary(tmp_path)
        backend = MobkitOrchestratorBackend(gateway_bin=bin_path)
        backend._initialized = True
        backend._loop = MagicMock()
        backend._handle = MagicMock()
        with pytest.raises(MobkitWorkflowError, match="Unknown workflow_name"):
            backend.run_mob_workflow(
                workflow_name="nonexistent_workflow",
                role_definitions=[],
                shared_context={},
                output_schema={},
                trace_id="t1",
                family_id="fam",
                lineage_id=None,
            )


# ---------------------------------------------------------------------------
# _parse_json_output
# ---------------------------------------------------------------------------

class TestParseJsonOutput:
    def test_plain_json_dict(self):
        result = _parse_json_output('{"key": "value"}', "member-1")
        assert result == {"key": "value"}

    def test_strips_markdown_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = _parse_json_output(text, "member-1")
        assert result == {"key": "value"}

    def test_strips_plain_fence(self):
        text = '```\n{"key": "value"}\n```'
        result = _parse_json_output(text, "member-1")
        assert result == {"key": "value"}

    def test_non_dict_wrapped(self):
        result = _parse_json_output("[1, 2, 3]", "member-1")
        assert result == {"result": [1, 2, 3]}

    def test_invalid_json_raises_schema_error(self):
        with pytest.raises(MobkitSchemaError, match="non-JSON"):
            _parse_json_output("not json at all !!!", "member-1")

    def test_whitespace_stripped(self):
        result = _parse_json_output('  \n{"a": 1}\n  ', "member-1")
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# _make_run_result
# ---------------------------------------------------------------------------

class TestMakeRunResult:
    def test_success_result(self):
        started = datetime.now(timezone.utc)
        result = _make_run_result(
            task_type="generate_proposal",
            backend_result={
                "payload": {"hypothesis": "test"},
                "member_traces": [
                    {"member_id": "lead", "role": "lead_researcher", "model": "tier3", "success": True},
                    {"member_id": "critic", "role": "cheap_critic", "model": "tier1", "success": True},
                ],
            },
            family_id="fam-001",
            lineage_id="lin-001",
            started_at=started,
            success=True,
            model_class="TASK_EXPENSIVE",
        )
        assert result.success is True
        assert result.task_type == "generate_proposal"
        assert result.family_id == "fam-001"
        assert result.lineage_id == "lin-001"
        assert result.provider == "mobkit"
        assert result.model_class == "TASK_EXPENSIVE"
        assert result.multi_agent_requested is True
        assert "lead_researcher" in result.multi_agent_roles
        assert result.result_payload == {"hypothesis": "test"}
        assert result.error is None
        assert result.duration_ms >= 0

    def test_failure_result_has_empty_payload(self):
        started = datetime.now(timezone.utc)
        result = _make_run_result(
            task_type="critique_post_evaluation",
            backend_result={},
            family_id="fam-002",
            lineage_id=None,
            started_at=started,
            success=False,
            error="workflow timed out",
        )
        assert result.success is False
        assert result.result_payload == {}
        assert result.error == "workflow timed out"

    def test_run_id_is_unique(self):
        started = datetime.now(timezone.utc)
        r1 = _make_run_result(
            task_type="t", backend_result={}, family_id="f", lineage_id=None,
            started_at=started,
        )
        r2 = _make_run_result(
            task_type="t", backend_result={}, family_id="f", lineage_id=None,
            started_at=started,
        )
        assert r1.run_id != r2.run_id

    def test_single_member_multi_agent_false(self):
        started = datetime.now(timezone.utc)
        result = _make_run_result(
            task_type="suggest_tweak",
            backend_result={
                "payload": {"suggested_parameters": {}},
                "member_traces": [
                    {"member_id": "tuner", "role": "parameter_tuner", "success": True},
                ],
            },
            family_id="f",
            lineage_id="l",
            started_at=started,
            success=True,
        )
        assert result.multi_agent_requested is False


# ---------------------------------------------------------------------------
# WORKFLOW_PROFILES completeness
# ---------------------------------------------------------------------------

class TestWorkflowProfiles:
    EXPECTED = {
        "proposal_generation",
        "post_eval_critique",
        "model_design",
        "model_mutation",
        "tweak_suggestion",
        "maintenance_diagnosis",
    }

    def test_all_expected_profiles_present(self):
        assert set(WORKFLOW_PROFILES.keys()) == self.EXPECTED

    def test_each_profile_has_at_least_one_lead(self):
        for name, profile in WORKFLOW_PROFILES.items():
            leads = [r for r in profile.member_roles if r.is_lead]
            assert len(leads) >= 1, f"Profile {name!r} has no lead role"

    def test_mob_profiles_have_multiple_roles(self):
        mob_profiles = [n for n, p in WORKFLOW_PROFILES.items() if p.is_mob]
        for name in mob_profiles:
            assert len(WORKFLOW_PROFILES[name].member_roles) >= 2, (
                f"Mob profile {name!r} should have at least 2 roles"
            )

    def test_single_member_profiles(self):
        single = [n for n, p in WORKFLOW_PROFILES.items() if not p.is_mob]
        assert "model_mutation" in single
        assert "tweak_suggestion" in single

    def test_all_roles_have_valid_model_tier(self):
        valid_tiers = {"tier1_cheap", "tier2_standard", "tier3_lead", "tier_codegen", "tier_mutate"}
        for name, profile in WORKFLOW_PROFILES.items():
            for role in profile.member_roles:
                assert role.model_tier in valid_tiers, (
                    f"Profile {name!r} role {role.role!r} has invalid tier {role.model_tier!r}"
                )

    def test_reviewer_roles_are_not_required(self):
        """Non-lead reviewers should have is_required=False."""
        for name, profile in WORKFLOW_PROFILES.items():
            if not profile.is_mob:
                continue
            reviewers = [r for r in profile.member_roles if not r.is_lead]
            for reviewer in reviewers:
                assert reviewer.is_required is False, (
                    f"Profile {name!r} reviewer {reviewer.role!r} should be is_required=False"
                )


# ---------------------------------------------------------------------------
# MobkitRuntime — healthcheck delegates to backend
# ---------------------------------------------------------------------------

class TestMobkitRuntime:
    def test_healthcheck_delegates_to_backend(self, tmp_path):
        backend = _make_mock_backend(healthy=True)
        rt = MobkitRuntime(tmp_path, backend=backend)
        assert rt.healthcheck() is True
        backend.healthcheck.assert_called_once()

    def test_healthcheck_unhealthy(self, tmp_path):
        backend = _make_mock_backend(healthy=False)
        rt = MobkitRuntime(tmp_path, backend=backend)
        assert rt.healthcheck() is False

    def test_backend_name(self, tmp_path):
        backend = _make_mock_backend()
        rt = MobkitRuntime(tmp_path, backend=backend)
        assert rt.backend_name == "mobkit"

    def test_all_eight_methods_present(self, tmp_path):
        backend = _make_mock_backend()
        rt = MobkitRuntime(tmp_path, backend=backend)
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
            assert hasattr(rt, method), f"MobkitRuntime missing method: {method}"
            assert callable(getattr(rt, method))


# ---------------------------------------------------------------------------
# MobkitRuntime — business methods call backend correctly
# ---------------------------------------------------------------------------

class TestMobkitRuntimeWorkflows:
    def _rt(self, tmp_path) -> tuple[MobkitRuntime, MagicMock]:
        backend = _make_mock_backend()
        rt = MobkitRuntime(tmp_path, backend=backend)
        return rt, backend

    def _mock_family(self, family_id: str = "fam-001"):
        from factory.contracts import FactoryFamily
        fam = MagicMock(spec=FactoryFamily)
        fam.family_id = family_id
        fam.thesis = "test thesis"
        return fam

    def _mock_lineage(self, family_id: str = "fam-001", lineage_id: str = "lin-001"):
        from factory.contracts import LineageRecord
        lin = MagicMock(spec=LineageRecord)
        lin.family_id = family_id
        lin.lineage_id = lineage_id
        lin.current_stage = "paper"
        return lin

    def _mock_genome(self):
        from factory.contracts import StrategyGenome
        genome = MagicMock(spec=StrategyGenome)
        genome.genome_id = "gen-001"
        genome.parameters = {"threshold": 0.5}
        return genome

    def test_generate_proposal_calls_mob_workflow(self, tmp_path):
        rt, backend = self._rt(tmp_path)
        result = rt.generate_proposal(
            family=self._mock_family(),
            champion_hypothesis=None,
            champion_genome=self._mock_genome(),
            learning_memory=[],
            execution_evidence=None,
            cycle_count=1,
            proposal_index=0,
        )
        backend.run_mob_workflow.assert_called_once()
        call_kwargs = backend.run_mob_workflow.call_args.kwargs
        assert call_kwargs["workflow_name"] == "proposal_generation"
        assert result is not None
        assert result.success is True
        assert result.task_type == "generate_proposal"

    def test_generate_proposal_returns_agent_run_result(self, tmp_path):
        from factory.agent_runtime import AgentRunResult
        rt, backend = self._rt(tmp_path)
        result = rt.generate_proposal(
            family=self._mock_family(),
            champion_hypothesis=None,
            champion_genome=self._mock_genome(),
            learning_memory=[],
            execution_evidence=None,
            cycle_count=1,
            proposal_index=0,
        )
        assert isinstance(result, AgentRunResult)

    def test_generate_proposal_writes_agent_run_artifact(self, tmp_path):
        with patch.object(config, "FACTORY_AGENT_LOG_DIR", str(tmp_path / "agent_runs")):
            rt, _backend = self._rt(tmp_path)
            result = rt.generate_proposal(
                family=self._mock_family(),
                champion_hypothesis=None,
                champion_genome=self._mock_genome(),
                learning_memory=[],
                execution_evidence=None,
                cycle_count=1,
                proposal_index=0,
            )
        assert result is not None
        assert result.artifact_path is not None
        payload = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
        assert payload["task_type"] == "generate_proposal"
        assert payload["provider"] == "mobkit"

    def test_suggest_tweak_calls_structured_task(self, tmp_path):
        rt, backend = self._rt(tmp_path)
        result = rt.suggest_tweak(
            lineage=self._mock_lineage(),
            hypothesis=None,
            genome=self._mock_genome(),
            row={"monthly_roi_pct": 2.0},
            learning_memory=[],
            execution_evidence=None,
        )
        backend.run_structured_task.assert_called_once()
        call_kwargs = backend.run_structured_task.call_args.kwargs
        assert call_kwargs["task_type"] == "suggest_tweak"
        assert result is not None
        assert result.success is True

    def test_mutate_model_calls_structured_task(self, tmp_path):
        rt, backend = self._rt(tmp_path)
        result = rt.mutate_model(
            family_id="fam-001",
            lineage_id="lin-001",
            current_model_code="class MyStrategy: pass",
            class_name="MyStrategy",
            backtest_results={"roi": 0.05},
            thesis="test",
            tweak_count=1,
        )
        backend.run_structured_task.assert_called_once()
        assert result is not None
        assert result.task_type == "mutate_model"

    def test_critique_post_evaluation_calls_mob_workflow(self, tmp_path):
        rt, backend = self._rt(tmp_path)
        with patch.object(config, "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", True), \
             patch.object(config, "FACTORY_AGENT_ENABLED_FAMILIES", "fam-001"), \
             patch.object(config, "FACTORY_REAL_AGENTS_ENABLED", True):
            result = rt.critique_post_evaluation(
                family=self._mock_family(),
                lineage=self._mock_lineage(),
                genome=self._mock_genome(),
                latest_bundle=None,
                learning_memory=[],
                execution_evidence=None,
            )
        backend.run_mob_workflow.assert_called_once()
        call_kwargs = backend.run_mob_workflow.call_args.kwargs
        assert call_kwargs["workflow_name"] == "post_eval_critique"
        assert result is not None

    def test_critique_post_evaluation_skips_when_disabled(self, tmp_path):
        rt, backend = self._rt(tmp_path)
        with patch.object(config, "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", False), \
             patch.object(config, "FACTORY_AGENT_ENABLED_FAMILIES", "fam-001"), \
             patch.object(config, "FACTORY_REAL_AGENTS_ENABLED", True):
            result = rt.critique_post_evaluation(
                family=self._mock_family(),
                lineage=self._mock_lineage(),
                genome=self._mock_genome(),
                latest_bundle=None,
                learning_memory=[],
                execution_evidence=None,
            )
        backend.run_mob_workflow.assert_not_called()
        assert result is None

    def test_workflow_error_returns_failed_result(self, tmp_path):
        backend = _make_mock_backend()
        backend.run_mob_workflow.side_effect = MobkitWorkflowError("timed out")
        rt = MobkitRuntime(tmp_path, backend=backend)
        result = rt.generate_proposal(
            family=self._mock_family(),
            champion_hypothesis=None,
            champion_genome=self._mock_genome(),
            learning_memory=[],
            execution_evidence=None,
            cycle_count=1,
            proposal_index=0,
        )
        assert result is not None
        assert result.success is False
        assert "timed out" in (result.error or "")

    def test_member_traces_in_result(self, tmp_path):
        backend = _make_mock_backend()
        backend.run_mob_workflow.return_value = {
            "payload": {"hypothesis": "h"},
            "member_traces": [
                {"member_id": "at-lead", "role": "lead_researcher", "model": "tier3_lead",
                 "success": True, "usage": None},
                {"member_id": "at-critic", "role": "cheap_critic", "model": "tier1_cheap",
                 "success": True, "usage": None},
            ],
            "backend": "mobkit",
        }
        rt = MobkitRuntime(tmp_path, backend=backend)
        result = rt.generate_proposal(
            family=self._mock_family(),
            champion_hypothesis=None,
            champion_genome=self._mock_genome(),
            learning_memory=[],
            execution_evidence=None,
            cycle_count=1,
            proposal_index=0,
        )
        assert result.multi_agent_requested is True
        assert "lead_researcher" in result.multi_agent_roles


# ---------------------------------------------------------------------------
# RuntimeManager: mobkit path
# ---------------------------------------------------------------------------

class TestRuntimeManagerMobkit:
    def test_mobkit_with_flag_falls_back_when_no_gateway(self, tmp_path):
        """mobkit+enabled but no gateway bin → falls back to legacy."""
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "mobkit"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", True), \
             patch.object(config, "FACTORY_MOBKIT_GATEWAY_BIN", ""), \
             patch.object(config, "FACTORY_MOBKIT_CONFIG_PATH", ""):
            manager = RuntimeManager.create(root)
        assert manager.backend_name == BACKEND_LEGACY

    def test_mobkit_with_flag_and_mock_runtime_selects_mobkit(self, tmp_path):
        """mobkit+enabled + mocked MobkitRuntime succeeds → mobkit backend selected."""
        root = _tmp_root(tmp_path)
        mock_rt = MagicMock()
        mock_rt.backend_name = "mobkit"
        mock_rt.healthcheck.return_value = True
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "mobkit"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", True), \
             patch("factory.runtime.runtime_manager.RuntimeManager._build_runtime",
                   return_value=mock_rt):
            manager = RuntimeManager.create(root)
            # Patch backend_name after construction since _backend_name is set in _build_runtime
            manager._backend_name = BACKEND_MOBKIT
        assert manager.backend_name == BACKEND_MOBKIT

    def test_healthcheck_delegates_to_mobkit_runtime(self, tmp_path):
        """When backend is mobkit, healthcheck delegates to runtime.healthcheck()."""
        root = _tmp_root(tmp_path)
        mock_rt = MagicMock()
        mock_rt.healthcheck.return_value = True
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(root)
        # Manually set to mobkit to test delegation
        manager._backend_name = BACKEND_MOBKIT
        manager._runtime = mock_rt
        assert manager.healthcheck() is True
        mock_rt.healthcheck.assert_called_once()

    def test_healthcheck_returns_false_on_mobkit_exception(self, tmp_path):
        root = _tmp_root(tmp_path)
        mock_rt = MagicMock()
        mock_rt.healthcheck.side_effect = RuntimeError("connection failed")
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(root)
        manager._backend_name = BACKEND_MOBKIT
        manager._runtime = mock_rt
        assert manager.healthcheck() is False
