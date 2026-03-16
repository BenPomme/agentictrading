"""Task 05 test suite — observability and trace correlation.

Covers:
- TraceContext: creation, derived copies, serialization
- UsageEvent: to_dict with optional/extra fields
- EventType: all values present, string representation
- RunLogger: structured JSON emission, all convenience methods
- OperatorStatus / build_operator_status: construction, to_dict, defaults
- Integration: correlation IDs in runtime_contracts.AgentRunEnvelope
- Integration: telemetry fired from MobkitRuntime._run_mob/_run_single
- Integration: telemetry fired from ProvenanceService.record_evaluation

Verification commands from task file:
    pytest -q tests -k telemetry
    pytest -q tests -k trace
    pytest -q tests -k observability
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from factory.telemetry.trace_context import TraceContext, _new_id
from factory.telemetry.usage_events import EventType, UsageEvent
from factory.telemetry.run_logger import RunLogger, default_logger, _TELEMETRY_LOGGER
from factory.telemetry.correlation import OperatorStatus, build_operator_status
from factory.telemetry import (
    TraceContext as TraceContextFromPkg,
    EventType as EventTypeFromPkg,
    default_logger as default_logger_from_pkg,
    build_operator_status as build_operator_status_from_pkg,
)

# Contracts / runtime
from factory.runtime.runtime_contracts import AgentRunEnvelope, RuntimeUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _capture_telemetry_logs() -> List[str]:
    """Return a list that will be populated with telemetry log messages."""
    records: List[str] = []
    return records


# ===========================================================================
# TestTraceContext
# ===========================================================================


class TestTraceContext:
    def test_create_generates_unique_ids(self):
        ctx1 = TraceContext.create(family_id="fam-001")
        ctx2 = TraceContext.create(family_id="fam-001")
        assert ctx1.cycle_id != ctx2.cycle_id
        assert ctx1.trace_id != ctx2.trace_id

    def test_create_sets_family_id(self):
        ctx = TraceContext.create(family_id="fam-abc")
        assert ctx.family_id == "fam-abc"

    def test_create_with_lineage_id(self):
        ctx = TraceContext.create(family_id="fam-001", lineage_id="lin-001")
        assert ctx.lineage_id == "lin-001"

    def test_create_without_lineage_is_none(self):
        ctx = TraceContext.create(family_id="fam-001")
        assert ctx.lineage_id is None

    def test_cycle_id_has_prefix(self):
        ctx = TraceContext.create(family_id="fam-001")
        assert ctx.cycle_id.startswith("cyc-")

    def test_trace_id_has_prefix(self):
        ctx = TraceContext.create(family_id="fam-001")
        assert ctx.trace_id.startswith("trc-")

    def test_with_run_returns_copy(self):
        ctx = TraceContext.create(family_id="fam-001")
        ctx2 = ctx.with_run("run-123")
        assert ctx2.runtime_run_id == "run-123"
        assert ctx.runtime_run_id is None  # original unchanged

    def test_with_goldfish_returns_copy(self):
        ctx = TraceContext.create(family_id="fam-001")
        ctx2 = ctx.with_goldfish("rec-456")
        assert ctx2.goldfish_record_id == "rec-456"
        assert ctx.goldfish_record_id is None

    def test_with_lineage_returns_copy(self):
        ctx = TraceContext.create(family_id="fam-001")
        ctx2 = ctx.with_lineage("lin-789")
        assert ctx2.lineage_id == "lin-789"
        assert ctx.lineage_id is None

    def test_with_trace_returns_copy(self):
        ctx = TraceContext.create(family_id="fam-001")
        ctx2 = ctx.with_trace("custom-trace")
        assert ctx2.trace_id == "custom-trace"
        assert ctx.trace_id != "custom-trace"

    def test_to_dict_has_all_keys(self):
        ctx = TraceContext.create(family_id="fam-001", lineage_id="lin-001")
        d = ctx.to_dict()
        for key in ("cycle_id", "trace_id", "family_id", "lineage_id",
                    "runtime_run_id", "goldfish_record_id"):
            assert key in d

    def test_to_dict_runtime_run_id_none_by_default(self):
        ctx = TraceContext.create(family_id="fam-001")
        assert ctx.to_dict()["runtime_run_id"] is None

    def test_frozen_dataclass_immutable(self):
        ctx = TraceContext.create(family_id="fam-001")
        with pytest.raises((AttributeError, TypeError)):
            ctx.family_id = "other"  # type: ignore[misc]

    def test_explicit_ids_respected(self):
        ctx = TraceContext.create(
            family_id="f", cycle_id="cyc-fixed", trace_id="trc-fixed"
        )
        assert ctx.cycle_id == "cyc-fixed"
        assert ctx.trace_id == "trc-fixed"

    def test_package_import_alias(self):
        assert TraceContextFromPkg is TraceContext


# ===========================================================================
# TestEventType
# ===========================================================================


class TestEventType:
    def test_all_required_event_types_present(self):
        required = {
            "BACKEND_SELECTED",
            "WORKFLOW_PLANNED", "WORKFLOW_STARTED",
            "WORKFLOW_FINISHED", "WORKFLOW_FAILED",
            "MEMBER_STARTED", "MEMBER_FINISHED",
            "DOWNGRADE_APPLIED", "FALLBACK_ACTIVATED",
            "GOLDFISH_RUN_CREATED", "GOLDFISH_RUN_FINALIZED",
            "PROMOTION_DECISION", "RETIREMENT_DECISION",
            "CIRCUIT_TRIPPED",
        }
        for name in required:
            assert hasattr(EventType, name), f"Missing EventType.{name}"

    def test_event_type_values_are_strings(self):
        for et in EventType:
            assert isinstance(et.value, str)

    def test_workflow_finished_value(self):
        assert EventType.WORKFLOW_FINISHED.value == "workflow_finished"

    def test_package_import_alias(self):
        assert EventTypeFromPkg is EventType


# ===========================================================================
# TestUsageEvent
# ===========================================================================


class TestUsageEvent:
    def _trace(self) -> TraceContext:
        return TraceContext.create(family_id="fam-001", lineage_id="lin-001")

    def test_to_dict_has_event_type_and_timestamp(self):
        ev = UsageEvent(
            event_type=EventType.WORKFLOW_STARTED,
            timestamp=_utcnow(),
        )
        d = ev.to_dict()
        assert d["event_type"] == "workflow_started"
        assert "timestamp" in d

    def test_to_dict_includes_trace_when_set(self):
        ctx = self._trace()
        ev = UsageEvent(
            event_type=EventType.BACKEND_SELECTED,
            timestamp=_utcnow(),
            trace_ctx=ctx,
            backend="legacy",
        )
        d = ev.to_dict()
        assert "trace" in d
        assert d["trace"]["family_id"] == "fam-001"

    def test_to_dict_omits_none_fields(self):
        ev = UsageEvent(
            event_type=EventType.WORKFLOW_STARTED,
            timestamp=_utcnow(),
        )
        d = ev.to_dict()
        assert "tokens" not in d
        assert "cost_usd" not in d
        assert "member_id" not in d

    def test_to_dict_includes_extra(self):
        ev = UsageEvent(
            event_type=EventType.FALLBACK_ACTIVATED,
            timestamp=_utcnow(),
            extra={"to_backend": "legacy", "from_backend": "mobkit"},
        )
        d = ev.to_dict()
        assert d["to_backend"] == "legacy"

    def test_to_dict_includes_tokens_when_set(self):
        ev = UsageEvent(
            event_type=EventType.WORKFLOW_FINISHED,
            timestamp=_utcnow(),
            tokens=1500,
        )
        d = ev.to_dict()
        assert d["tokens"] == 1500


# ===========================================================================
# TestRunLogger
# ===========================================================================


class TestRunLogger:
    """Tests that RunLogger emits structured JSON to factory.telemetry."""

    def setup_method(self):
        self._logger = RunLogger()
        self._records: List[logging.LogRecord] = []
        self._handler = logging.handlers_list = None

        # Attach a capturing handler to factory.telemetry
        class _Capture(logging.Handler):
            def __init__(inner_self):
                super().__init__()
                inner_self.records = []
            def emit(inner_self, record):
                inner_self.records.append(record)

        self._capture = _Capture()
        _TELEMETRY_LOGGER.addHandler(self._capture)
        _TELEMETRY_LOGGER.setLevel(logging.INFO)

    def teardown_method(self):
        _TELEMETRY_LOGGER.removeHandler(self._capture)

    def _emitted(self) -> List[Dict]:
        results = []
        for r in self._capture.records:
            try:
                results.append(json.loads(r.getMessage()))
            except Exception:
                pass
        return results

    def test_backend_selected_emits_event(self):
        self._logger.backend_selected("legacy")
        events = self._emitted()
        assert len(events) == 1
        assert events[0]["event_type"] == "backend_selected"
        assert events[0]["backend"] == "legacy"

    def test_backend_selected_includes_healthy(self):
        self._logger.backend_selected("mobkit", healthy=True)
        events = self._emitted()
        assert events[0]["healthy"] is True

    def test_workflow_planned_emits_event(self):
        ctx = TraceContext.create(family_id="fam-001")
        self._logger.workflow_planned("generate_proposal", "legacy", trace_ctx=ctx)
        events = self._emitted()
        assert events[0]["event_type"] == "workflow_planned"
        assert events[0]["task_type"] == "generate_proposal"
        assert events[0]["trace"]["family_id"] == "fam-001"

    def test_workflow_planned_includes_planned_tokens(self):
        self._logger.workflow_planned("t", "b", planned_tokens=2048, is_mob=True)
        events = self._emitted()
        assert events[0]["planned_tokens"] == 2048
        assert events[0]["is_mob"] is True

    def test_workflow_started_emits_event(self):
        self._logger.workflow_started("tweak_suggestion", "mobkit")
        events = self._emitted()
        assert events[0]["event_type"] == "workflow_started"

    def test_workflow_finished_emits_success(self):
        self._logger.workflow_finished("t", "legacy", tokens=500, duration_ms=1200)
        events = self._emitted()
        assert events[0]["event_type"] == "workflow_finished"
        assert events[0]["success"] is True
        assert events[0]["tokens"] == 500
        assert events[0]["duration_ms"] == 1200

    def test_workflow_failed_emits_failure(self):
        self._logger.workflow_failed("t", "mobkit", reason="timeout")
        events = self._emitted()
        assert events[0]["event_type"] == "workflow_failed"
        assert events[0]["success"] is False
        assert events[0]["reason"] == "timeout"

    def test_member_started_emits_event(self):
        self._logger.member_started("m-001", "lead_researcher")
        events = self._emitted()
        assert events[0]["event_type"] == "member_started"
        assert events[0]["member_id"] == "m-001"
        assert events[0]["role"] == "lead_researcher"

    def test_member_finished_emits_event(self):
        self._logger.member_finished("m-001", "cheap_critic", tokens=256, success=True)
        events = self._emitted()
        assert events[0]["event_type"] == "member_finished"
        assert events[0]["tokens"] == 256

    def test_downgrade_applied_emits_event(self):
        self._logger.downgrade_applied(
            "generate_proposal", scope="family", reason="over 70%",
            action="remove_reviewers", usage_ratio=0.72,
        )
        events = self._emitted()
        assert events[0]["event_type"] == "downgrade_applied"
        assert events[0]["scope"] == "family"
        assert events[0]["downgrade_action"] == "remove_reviewers"
        assert events[0]["usage_ratio"] == pytest.approx(0.72, abs=1e-3)

    def test_fallback_activated_emits_event(self):
        self._logger.fallback_activated("mobkit", "legacy", reason="gateway_down")
        events = self._emitted()
        assert events[0]["event_type"] == "fallback_activated"
        assert events[0]["backend"] == "mobkit"
        assert events[0]["to_backend"] == "legacy"
        assert events[0]["reason"] == "gateway_down"

    def test_goldfish_run_created_emits_event(self):
        ctx = TraceContext.create(family_id="fam-001")
        self._logger.goldfish_run_created("run-abc", "ws-001", trace_ctx=ctx)
        events = self._emitted()
        assert events[0]["event_type"] == "goldfish_run_created"
        assert events[0]["run_id"] == "run-abc"
        assert events[0]["workspace_id"] == "ws-001"

    def test_goldfish_run_finalized_success(self):
        self._logger.goldfish_run_finalized("run-abc", "ws-001", success=True)
        events = self._emitted()
        assert events[0]["event_type"] == "goldfish_run_finalized"
        assert events[0]["success"] is True

    def test_goldfish_run_finalized_failure(self):
        self._logger.goldfish_run_finalized("r", "w", success=False, reason="write_error")
        events = self._emitted()
        assert events[0]["success"] is False
        assert events[0]["reason"] == "write_error"

    def test_promotion_decision_emits_event(self):
        ctx = TraceContext.create(family_id="fam-001", lineage_id="lin-001")
        self._logger.promotion_decision(
            "lin-001", trace_ctx=ctx,
            from_stage="candidate", to_stage="paper_trading",
        )
        events = self._emitted()
        assert events[0]["event_type"] == "promotion_decision"
        assert events[0]["lineage_id"] == "lin-001"
        assert events[0]["from_stage"] == "candidate"

    def test_retirement_decision_emits_event(self):
        self._logger.retirement_decision("lin-xyz", reason="poor_sharpe")
        events = self._emitted()
        assert events[0]["event_type"] == "retirement_decision"
        assert events[0]["reason"] == "poor_sharpe"

    def test_circuit_tripped_emits_event(self):
        self._logger.circuit_tripped("family", "fam-001", reason="ceiling")
        events = self._emitted()
        assert events[0]["event_type"] == "circuit_tripped"
        assert events[0]["scope"] == "family"
        assert events[0]["scope_id"] == "fam-001"

    def test_emit_silently_handles_errors(self):
        # Should not raise even if internal formatting fails.
        ev = UsageEvent(event_type=EventType.WORKFLOW_STARTED, timestamp=_utcnow())
        # Patch json.dumps to raise — emit must swallow it.
        with patch("factory.telemetry.run_logger.json.dumps", side_effect=RuntimeError("oops")):
            self._logger.emit(ev)  # must not raise

    def test_no_emission_when_logger_disabled(self):
        _TELEMETRY_LOGGER.setLevel(logging.CRITICAL)
        self._logger.backend_selected("legacy")
        assert self._emitted() == []

    def test_default_logger_is_run_logger_instance(self):
        assert isinstance(default_logger, RunLogger)

    def test_default_logger_same_as_package_import(self):
        assert default_logger is default_logger_from_pkg


# ===========================================================================
# TestOperatorStatus
# ===========================================================================


class TestOperatorStatus:
    def _mock_manager(self, backend: str = "legacy", healthy: bool = True):
        m = MagicMock()
        m.backend_name = backend
        m.healthcheck.return_value = healthy
        return m

    def _mock_governor(
        self, usd_today: float = 0.0, budget: float = 10.0,
        strict: bool = False, global_open: bool = False,
        family_open_ids=None,
    ):
        g = MagicMock()
        g.budget_snapshot.return_value = {
            "global_usage": {
                "tokens_today": 1000,
                "usd_today": usd_today,
                "budget_usd": budget,
                "pct_used": round(usd_today / budget * 100, 1) if budget else 0.0,
            },
            "circuit": {
                "global": {"state": "open" if global_open else "closed"},
                "families": {
                    fid: {"state": "open"}
                    for fid in (family_open_ids or [])
                },
            },
            "strict_enforcement": strict,
            "ledger_summary": {
                "downgrade_events_today": 2,
                "stop_events_today": 1,
            },
        }
        return g

    def test_to_dict_has_required_keys(self):
        m = self._mock_manager()
        status = build_operator_status(m)
        d = status.to_dict()
        for key in (
            "active_backend", "backend_healthy", "strict_budgets",
            "budget", "circuit", "recent_fallback_reasons",
            "last_goldfish_record_id", "last_runtime_run_id",
            "downgrade_events_today", "stop_events_today", "as_of",
        ):
            assert key in d, f"Missing key: {key}"

    def test_active_backend_reflects_manager(self):
        m = self._mock_manager(backend="mobkit")
        status = build_operator_status(m)
        assert status.active_backend == "mobkit"

    def test_backend_healthy_from_healthcheck(self):
        m = self._mock_manager(healthy=True)
        status = build_operator_status(m)
        assert status.backend_healthy is True

    def test_backend_unhealthy_when_healthcheck_raises(self):
        m = MagicMock()
        m.backend_name = "legacy"
        m.healthcheck.side_effect = RuntimeError("gone")
        status = build_operator_status(m)
        assert status.backend_healthy is False

    def test_budget_state_from_governor(self):
        m = self._mock_manager()
        g = self._mock_governor(usd_today=5.0, budget=10.0)
        status = build_operator_status(m, governor=g)
        assert status.global_usd_today == pytest.approx(5.0)
        assert status.global_budget_usd == pytest.approx(10.0)

    def test_global_circuit_open_when_tripped(self):
        m = self._mock_manager()
        g = self._mock_governor(global_open=True)
        status = build_operator_status(m, governor=g)
        assert status.circuit_global_open is True

    def test_family_circuit_ids_populated(self):
        m = self._mock_manager()
        g = self._mock_governor(family_open_ids=["fam-001", "fam-002"])
        status = build_operator_status(m, governor=g)
        assert "fam-001" in status.circuit_family_open_ids
        assert "fam-002" in status.circuit_family_open_ids

    def test_downgrade_and_stop_counts(self):
        m = self._mock_manager()
        g = self._mock_governor()
        status = build_operator_status(m, governor=g)
        assert status.downgrade_events_today == 2
        assert status.stop_events_today == 1

    def test_governor_extracted_from_manager(self):
        m = self._mock_manager()
        g = self._mock_governor(usd_today=3.0)
        m.governor = g
        # Don't pass governor explicitly — it should read from manager.governor
        status = build_operator_status(m)
        assert status.global_usd_today == pytest.approx(3.0)

    def test_caller_supplied_record_ids(self):
        m = self._mock_manager()
        status = build_operator_status(
            m,
            last_goldfish_record_id="rec-xyz",
            last_runtime_run_id="run-abc",
        )
        assert status.last_goldfish_record_id == "rec-xyz"
        assert status.last_runtime_run_id == "run-abc"

    def test_no_governor_defaults_to_zero_budget(self):
        m = MagicMock()
        m.backend_name = "legacy"
        m.healthcheck.return_value = True
        # No governor attribute
        del m.governor
        status = build_operator_status(m)
        assert status.global_usd_today == 0.0
        assert status.global_budget_usd == 0.0

    def test_as_of_is_iso_timestamp(self):
        m = self._mock_manager()
        status = build_operator_status(m)
        # Should parse without exception
        datetime.fromisoformat(status.as_of)

    def test_recent_fallback_reasons_passed_through(self):
        m = self._mock_manager()
        status = build_operator_status(
            m, recent_fallback_reasons=["gateway_down", "schema_invalid"]
        )
        assert "gateway_down" in status.recent_fallback_reasons

    def test_governor_error_degrades_gracefully(self):
        m = self._mock_manager()
        g = MagicMock()
        g.budget_snapshot.side_effect = RuntimeError("boom")
        status = build_operator_status(m, governor=g)
        # Should not raise; fields default to safe values.
        assert status.global_usd_today == 0.0
        assert status.circuit_global_open is False

    def test_build_operator_status_from_package(self):
        m = self._mock_manager()
        status = build_operator_status_from_pkg(m)
        assert isinstance(status, OperatorStatus)


# ===========================================================================
# TestAgentRunEnvelopeCorrelationIds
# ===========================================================================


class TestAgentRunEnvelopeCorrelationIds:
    """Verify cycle_id and goldfish_record_id were added to AgentRunEnvelope."""

    def _minimal_envelope(self, **kwargs) -> AgentRunEnvelope:
        now = datetime.now(timezone.utc)
        defaults = dict(
            run_id="r-001", trace_id="t-001", backend="legacy",
            task_type="generate_proposal", success=True, payload={},
            started_at=now, finished_at=now,
        )
        defaults.update(kwargs)
        return AgentRunEnvelope(**defaults)

    def test_cycle_id_defaults_to_empty_string(self):
        env = self._minimal_envelope()
        assert env.cycle_id == ""

    def test_goldfish_record_id_defaults_to_none(self):
        env = self._minimal_envelope()
        assert env.goldfish_record_id is None

    def test_cycle_id_set_explicitly(self):
        env = self._minimal_envelope(cycle_id="cyc-abc")
        assert env.cycle_id == "cyc-abc"

    def test_goldfish_record_id_set_explicitly(self):
        env = self._minimal_envelope(goldfish_record_id="rec-xyz")
        assert env.goldfish_record_id == "rec-xyz"

    def test_to_dict_includes_cycle_id(self):
        env = self._minimal_envelope(cycle_id="cyc-test")
        d = env.to_dict()
        assert d["cycle_id"] == "cyc-test"

    def test_to_dict_includes_goldfish_record_id(self):
        env = self._minimal_envelope(goldfish_record_id="rec-001")
        d = env.to_dict()
        assert d["goldfish_record_id"] == "rec-001"


# ===========================================================================
# TestMobkitRuntimeTelemetryIntegration
# ===========================================================================


class TestMobkitRuntimeTelemetryIntegration:
    """Verify that MobkitRuntime emits telemetry events during workflow execution."""

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []
        def emit(self, record):
            self.records.append(record)

    def setup_method(self):
        self._capture = self._Capture()
        _TELEMETRY_LOGGER.addHandler(self._capture)
        _TELEMETRY_LOGGER.setLevel(logging.INFO)

    def teardown_method(self):
        _TELEMETRY_LOGGER.removeHandler(self._capture)

    def _emitted(self):
        results = []
        for r in self._capture.records:
            try:
                results.append(json.loads(r.getMessage()))
            except Exception:
                pass
        return results

    def _make_runtime(self, backend_result=None, raise_exc=None):
        """Build a MobkitRuntime with a mocked backend."""
        from factory.runtime.mobkit_backend import MobkitRuntime, MobkitOrchestratorBackend

        backend = MagicMock(spec=MobkitOrchestratorBackend)
        if raise_exc:
            from factory.runtime.mobkit_backend import MobkitWorkflowError
            backend.run_mob_workflow.side_effect = MobkitWorkflowError(str(raise_exc))
            backend.run_structured_task.side_effect = MobkitWorkflowError(str(raise_exc))
        else:
            result = backend_result or {"payload": {}, "member_traces": [], "backend": "mobkit"}
            backend.run_mob_workflow.return_value = result
            backend.run_structured_task.return_value = result

        import tempfile
        tmp = tempfile.mkdtemp()
        rt = MobkitRuntime(tmp, backend=backend)
        return rt

    def test_workflow_planned_emitted_on_mob(self):
        rt = self._make_runtime()
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-001", lineage_id=None,
        )
        events = self._emitted()
        types = [e["event_type"] for e in events]
        assert "workflow_planned" in types

    def test_workflow_started_emitted_on_mob(self):
        rt = self._make_runtime()
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-001", lineage_id=None,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "workflow_started" in types

    def test_workflow_finished_emitted_on_success(self):
        rt = self._make_runtime()
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-001", lineage_id=None,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "workflow_finished" in types
        assert "workflow_failed" not in types

    def test_workflow_failed_emitted_on_error(self):
        rt = self._make_runtime(raise_exc="backend_timeout")
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-001", lineage_id=None,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "workflow_failed" in types
        assert "workflow_finished" not in types

    def test_trace_context_ids_in_events(self):
        rt = self._make_runtime()
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-telemetry", lineage_id="lin-001",
        )
        events = self._emitted()
        # Every event that has a trace should carry family_id
        for ev in events:
            if "trace" in ev:
                assert ev["trace"]["family_id"] == "fam-telemetry"

    def test_workflow_planned_emitted_on_single(self):
        rt = self._make_runtime()
        rt._run_single(
            workflow_name="tweak_suggestion",
            task_type="suggest_tweak",
            prompt="test", schema={},
            family_id="fam-001", lineage_id=None,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "workflow_planned" in types

    def test_downgrade_applied_emitted_when_constraints_active(self):
        from factory.governance import BudgetHooks
        from factory.governance.downgrade_policy import DowngradeDecision, DOWNGRADE_REMOVE_REVIEWERS

        hooks = BudgetHooks(
            max_tokens_override=1024,
            removed_member_roles=["cheap_critic"],
            downgrade_decision=DowngradeDecision(
                action=DOWNGRADE_REMOVE_REVIEWERS,
                scope="family", scope_id="fam-001",
                reason="family at 75%", usage_ratio=0.75,
                original_max_tokens=2048, downgraded_max_tokens=1024,
            ),
        )
        rt = self._make_runtime()
        # Pass hooks directly as budget_hooks — governor is None so they're returned as-is
        rt._run_mob(
            workflow_name="proposal_generation",
            task_type="generate_proposal",
            shared_context={}, output_schema={},
            family_id="fam-001", lineage_id=None,
            budget_hooks=hooks,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "downgrade_applied" in types


# ===========================================================================
# TestProvenanceTelemetryIntegration
# ===========================================================================


class TestProvenanceTelemetryIntegration:
    """Verify ProvenanceService.record_evaluation emits Goldfish telemetry events."""

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []
        def emit(self, record):
            self.records.append(record)

    def setup_method(self):
        self._capture = self._Capture()
        _TELEMETRY_LOGGER.addHandler(self._capture)
        _TELEMETRY_LOGGER.setLevel(logging.INFO)

    def teardown_method(self):
        _TELEMETRY_LOGGER.removeHandler(self._capture)

    def _emitted(self):
        results = []
        for r in self._capture.records:
            try:
                results.append(json.loads(r.getMessage()))
            except Exception:
                pass
        return results

    def test_goldfish_run_created_emitted_on_record_evaluation(self):
        from factory.provenance.goldfish_client import ProvenanceService, GoldfishClient

        mock_client = MagicMock(spec=GoldfishClient)
        mock_client.create_run.return_value = "run-001"
        mock_client.finalize_run.return_value = "run-001"

        svc = ProvenanceService(mock_client, enabled=True)
        ctx = TraceContext.create(family_id="fam-001", lineage_id="lin-001")
        svc.record_evaluation(
            workspace_id="fam-001", run_id="run-001",
            lineage_id="lin-001", family_id="fam-001", cycle_id="cyc-abc",
            evaluation_payload={"result": "ok"},
            correlation={"trace_id": ctx.trace_id},
            trace_ctx=ctx,
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "goldfish_run_created" in types

    def test_goldfish_run_finalized_emitted_on_record_evaluation(self):
        from factory.provenance.goldfish_client import ProvenanceService, GoldfishClient

        mock_client = MagicMock(spec=GoldfishClient)
        mock_client.create_run.return_value = "run-001"
        mock_client.finalize_run.return_value = "run-001"

        svc = ProvenanceService(mock_client, enabled=True)
        svc.record_evaluation(
            workspace_id="fam-001", run_id="run-001",
            lineage_id="lin-001", family_id="fam-001", cycle_id="cyc-abc",
            evaluation_payload={}, correlation={},
        )
        types = [e["event_type"] for e in self._emitted()]
        assert "goldfish_run_finalized" in types

    def test_goldfish_events_carry_trace_ctx(self):
        from factory.provenance.goldfish_client import ProvenanceService, GoldfishClient

        mock_client = MagicMock(spec=GoldfishClient)
        mock_client.create_run.return_value = "run-001"
        mock_client.finalize_run.return_value = "run-001"

        svc = ProvenanceService(mock_client, enabled=True)
        ctx = TraceContext.create(family_id="fam-trace", lineage_id="lin-001")
        svc.record_evaluation(
            workspace_id="fam-trace", run_id="run-001",
            lineage_id="lin-001", family_id="fam-trace", cycle_id="cyc-001",
            evaluation_payload={}, correlation={},
            trace_ctx=ctx,
        )
        for ev in self._emitted():
            if "trace" in ev:
                assert ev["trace"]["family_id"] == "fam-trace"

    def test_no_goldfish_events_when_provenance_disabled(self):
        from factory.provenance.goldfish_client import ProvenanceService, NullGoldfishClient

        svc = ProvenanceService(NullGoldfishClient(), enabled=False)
        result = svc.record_evaluation(
            workspace_id="fam-001", run_id="run-001",
            lineage_id="lin-001", family_id="fam-001", cycle_id="cyc-abc",
            evaluation_payload={}, correlation={},
        )
        assert result is None
        types = [e["event_type"] for e in self._emitted()]
        assert "goldfish_run_created" not in types


# ===========================================================================
# TestCycleReconstructibility
# ===========================================================================


class TestCycleReconstructibility:
    """End-to-end: simulate a cycle and confirm it can be reconstructed from emitted events."""

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []
        def emit(self, record):
            self.records.append(record)

    def setup_method(self):
        self._capture = self._Capture()
        _TELEMETRY_LOGGER.addHandler(self._capture)
        _TELEMETRY_LOGGER.setLevel(logging.INFO)

    def teardown_method(self):
        _TELEMETRY_LOGGER.removeHandler(self._capture)

    def _all_events(self):
        results = []
        for r in self._capture.records:
            try:
                results.append(json.loads(r.getMessage()))
            except Exception:
                pass
        return results

    def test_full_cycle_reconstructible_from_events(self):
        """
        Simulate the key event emissions for one factory cycle and confirm:
        - cycle_id is consistent across all events
        - all required event types are present
        - backend, family_id, lineage_id are traceable
        """
        logger = RunLogger()
        ctx = TraceContext.create(family_id="fam-cycle", lineage_id="lin-cycle")

        # 1. Backend selected
        logger.backend_selected("legacy", trace_ctx=ctx)

        # 2. Workflow lifecycle
        logger.workflow_planned("generate_proposal", "legacy",
                                trace_ctx=ctx, planned_tokens=2048, is_mob=False)
        logger.workflow_started("generate_proposal", "legacy", trace_ctx=ctx)
        logger.workflow_finished("generate_proposal", "legacy",
                                 trace_ctx=ctx, tokens=1800, duration_ms=3500)

        # 3. Provenance
        logger.goldfish_run_created("run-001", "fam-cycle", trace_ctx=ctx)
        logger.goldfish_run_finalized("run-001", "fam-cycle", trace_ctx=ctx, success=True)

        # 4. Lineage decision
        logger.promotion_decision("lin-cycle", trace_ctx=ctx,
                                  from_stage="candidate", to_stage="paper_trading")

        events = self._all_events()
        event_types = {e["event_type"] for e in events}

        # All required lifecycle events present
        required_types = {
            "backend_selected", "workflow_planned", "workflow_started",
            "workflow_finished", "goldfish_run_created", "goldfish_run_finalized",
            "promotion_decision",
        }
        assert required_types.issubset(event_types)

        # All events that carry trace share the same cycle_id
        cycle_ids = {
            e["trace"]["cycle_id"]
            for e in events
            if "trace" in e
        }
        assert len(cycle_ids) == 1, f"Multiple cycle_ids found: {cycle_ids}"

        # Family and lineage are traceable
        for ev in events:
            if "trace" in ev:
                assert ev["trace"]["family_id"] == "fam-cycle"
                assert ev["trace"]["lineage_id"] == "lin-cycle"
