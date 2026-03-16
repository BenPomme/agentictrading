"""Tests for Task 04: cost governance and budget isolation.

Covers:
- CostPolicyConfig loads from config with correct defaults
- BudgetLedger: record, get_daily_usage, snapshot, thread safety
- DowngradeCascade: correct step activated at each threshold
- CircuitBreaker: trip, query, reset, state_summary
- CostGovernor.check_and_plan: observe-only vs strict mode, circuit blocking
- CostGovernor.record_usage: auto-trips circuit on hard ceiling
- BudgetHooks has_constraints / to_dict
- RuntimeManager.governor property exists and is a CostGovernor
- MobkitRuntime receives governor via constructor
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import config
from factory.governance import BudgetHooks, CostGovernor, GovernorStopError
from factory.governance.budget_ledger import (
    EVENT_ACTUAL,
    EVENT_CIRCUIT_TRIP,
    EVENT_DOWNGRADE,
    EVENT_PLANNED,
    EVENT_STOP,
    SCOPE_FAMILY,
    SCOPE_GLOBAL,
    SCOPE_LINEAGE,
    BudgetLedger,
    LedgerEntry,
    _utcnow,
)
from factory.governance.cost_policy import (
    CostPolicyConfig,
    FamilyPolicy,
    GlobalPolicy,
    LineagePolicy,
    MemberPolicy,
    TaskPolicy,
)
from factory.governance.downgrade_policy import (
    DOWNGRADE_CHEAP_TIERS,
    DOWNGRADE_NONE,
    DOWNGRADE_REDUCE_TOKENS,
    DOWNGRADE_REMOVE_REVIEWERS,
    DOWNGRADE_SINGLE_TASK,
    DOWNGRADE_STOP,
    DowngradeCascade,
)
from factory.governance.safety_circuit import CircuitBreaker, CircuitState
from factory.runtime.runtime_manager import RuntimeManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_policy() -> CostPolicyConfig:
    """Policy with small ceilings for testing."""
    return CostPolicyConfig(
        global_policy=GlobalPolicy(
            daily_budget_usd=1.0,
            daily_token_limit=10_000,
            strict_enforcement=False,
        ),
        family_policy=FamilyPolicy(
            daily_budget_usd=0.5,
            daily_token_limit=5_000,
        ),
        lineage_policy=LineagePolicy(
            max_budget_usd=0.2,
            max_mutations=3,
        ),
        task_policy=TaskPolicy(
            default_max_tokens=512,
        ),
        member_policy=MemberPolicy(
            default_max_tokens=256,
        ),
    )


def _tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "factory" / "agent_runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _governor(*, strict: bool = False, budget_usd: float = 1.0, family_usd: float = 0.5) -> CostGovernor:
    policy = CostPolicyConfig(
        global_policy=GlobalPolicy(
            daily_budget_usd=budget_usd,
            daily_token_limit=100_000,
            strict_enforcement=strict,
        ),
        family_policy=FamilyPolicy(
            daily_budget_usd=family_usd,
            daily_token_limit=50_000,
        ),
        lineage_policy=LineagePolicy(max_budget_usd=0.2),
        task_policy=TaskPolicy(),
        member_policy=MemberPolicy(),
    )
    return CostGovernor(policy=policy, ledger=BudgetLedger(), circuit=CircuitBreaker())


# ---------------------------------------------------------------------------
# CostPolicyConfig
# ---------------------------------------------------------------------------

class TestCostPolicyConfig:
    def test_load_uses_config_values(self):
        with patch.object(config, "FACTORY_GLOBAL_DAILY_BUDGET_USD", 25.0), \
             patch.object(config, "FACTORY_FAMILY_DAILY_BUDGET_USD", 5.0), \
             patch.object(config, "FACTORY_LINEAGE_MAX_BUDGET_USD", 0.5), \
             patch.object(config, "FACTORY_TASK_DEFAULT_MAX_TOKENS", 4096), \
             patch.object(config, "FACTORY_MOB_MEMBER_DEFAULT_MAX_TOKENS", 1024), \
             patch.object(config, "FACTORY_ENABLE_STRICT_BUDGETS", False):
            policy = CostPolicyConfig.load()
        assert policy.global_policy.daily_budget_usd == 25.0
        assert policy.family_policy.daily_budget_usd == 5.0
        assert policy.lineage_policy.max_budget_usd == 0.5
        assert policy.task_policy.default_max_tokens == 4096
        assert policy.member_policy.default_max_tokens == 1024

    def test_strict_enforcement_from_config(self):
        with patch.object(config, "FACTORY_ENABLE_STRICT_BUDGETS", True):
            policy = CostPolicyConfig.load()
        assert policy.global_policy.strict_enforcement is True

    def test_strict_enforcement_false_by_default(self):
        with patch.object(config, "FACTORY_ENABLE_STRICT_BUDGETS", False):
            policy = CostPolicyConfig.load()
        assert policy.global_policy.strict_enforcement is False

    def test_to_dict_has_required_keys(self):
        policy = _default_policy()
        d = policy.to_dict()
        assert "global" in d
        assert "family" in d
        assert "lineage" in d
        assert "task" in d
        assert "member" in d
        assert d["global"]["daily_budget_usd"] == 1.0


# ---------------------------------------------------------------------------
# BudgetLedger
# ---------------------------------------------------------------------------

class TestBudgetLedger:
    def test_record_and_daily_usage(self):
        ledger = BudgetLedger()
        ledger.record_actual(
            scope=SCOPE_FAMILY, scope_id="fam-001",
            task_type="generate_proposal", tokens=1000, estimated_cost_usd=0.003,
        )
        tokens, usd = ledger.get_daily_usage(SCOPE_FAMILY, "fam-001")
        assert tokens == 1000
        assert pytest.approx(usd) == 0.003

    def test_multiple_records_summed(self):
        ledger = BudgetLedger()
        for _ in range(5):
            ledger.record_actual(
                scope=SCOPE_FAMILY, scope_id="fam-A",
                task_type="t", tokens=200, estimated_cost_usd=0.001,
            )
        tokens, usd = ledger.get_daily_usage(SCOPE_FAMILY, "fam-A")
        assert tokens == 1000
        assert pytest.approx(usd) == 0.005

    def test_different_scope_ids_isolated(self):
        ledger = BudgetLedger()
        ledger.record_actual(scope=SCOPE_FAMILY, scope_id="fam-1", task_type="t", tokens=500)
        ledger.record_actual(scope=SCOPE_FAMILY, scope_id="fam-2", task_type="t", tokens=100)
        t1, _ = ledger.get_daily_usage(SCOPE_FAMILY, "fam-1")
        t2, _ = ledger.get_daily_usage(SCOPE_FAMILY, "fam-2")
        assert t1 == 500
        assert t2 == 100

    def test_planned_events_not_counted_in_daily_usage(self):
        ledger = BudgetLedger()
        ledger.record_planned(scope=SCOPE_FAMILY, scope_id="fam-001", task_type="t", tokens=999)
        tokens, _ = ledger.get_daily_usage(SCOPE_FAMILY, "fam-001")
        assert tokens == 0  # planned ≠ actual

    def test_record_downgrade_event(self):
        ledger = BudgetLedger()
        ledger.record_downgrade(
            scope=SCOPE_FAMILY, scope_id="fam-001", task_type="t",
            reason="test", prior_tier="tier3", new_tier="tier1",
        )
        recent = ledger.recent_entries(limit=5)
        assert any(e.event_type == EVENT_DOWNGRADE for e in recent)

    def test_record_stop_event(self):
        ledger = BudgetLedger()
        ledger.record_stop(scope=SCOPE_GLOBAL, scope_id="global", task_type="t", reason="test")
        recent = ledger.recent_entries(limit=5)
        assert any(e.event_type == EVENT_STOP for e in recent)

    def test_snapshot_counts_downgrades_and_stops(self):
        ledger = BudgetLedger()
        ledger.record_downgrade(scope=SCOPE_FAMILY, scope_id="f", task_type="t", reason="r")
        ledger.record_stop(scope=SCOPE_GLOBAL, scope_id="global", task_type="t", reason="s")
        snap = ledger.snapshot()
        assert snap["downgrade_events_today"] >= 1
        assert snap["stop_events_today"] >= 1

    def test_max_entries_cap_evicts_oldest(self):
        ledger = BudgetLedger(max_entries=10)
        for i in range(15):
            ledger.record_actual(scope=SCOPE_GLOBAL, scope_id="global", task_type="t", tokens=i)
        # Should have <= max_entries entries after eviction
        assert len(ledger._entries) <= 10

    def test_thread_safe_concurrent_writes(self):
        ledger = BudgetLedger()
        errors = []

        def write():
            try:
                for _ in range(50):
                    ledger.record_actual(
                        scope=SCOPE_FAMILY, scope_id="fam-t",
                        task_type="task", tokens=10, estimated_cost_usd=0.001,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        tokens, _ = ledger.get_daily_usage(SCOPE_FAMILY, "fam-t")
        assert tokens == 4 * 50 * 10


# ---------------------------------------------------------------------------
# DowngradeCascade
# ---------------------------------------------------------------------------

class TestDowngradeCascade:
    def _cascade(self) -> DowngradeCascade:
        return DowngradeCascade()

    def _policy(self, global_usd: float = 1.0, family_usd: float = 0.5) -> CostPolicyConfig:
        return CostPolicyConfig(
            global_policy=GlobalPolicy(daily_budget_usd=global_usd, daily_token_limit=10_000),
            family_policy=FamilyPolicy(daily_budget_usd=family_usd, daily_token_limit=5_000),
            lineage_policy=LineagePolicy(max_budget_usd=0.2),
            task_policy=TaskPolicy(),
            member_policy=MemberPolicy(),
        )

    def _ledger_with_usage(self, scope, scope_id, usd) -> BudgetLedger:
        ledger = BudgetLedger()
        ledger.record_actual(
            scope=scope, scope_id=scope_id,
            task_type="t", tokens=0, estimated_cost_usd=usd,
        )
        return ledger

    def test_no_downgrade_at_low_usage(self):
        cascade = self._cascade()
        ledger = BudgetLedger()  # empty
        decision = cascade.evaluate(
            policy_config=self._policy(),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
        )
        assert decision.action == DOWNGRADE_NONE
        assert not decision.stopped

    def test_reduce_tokens_at_60pct_global(self):
        cascade = self._cascade()
        ledger = self._ledger_with_usage(SCOPE_GLOBAL, "global", 0.60)  # 60% of 1.0
        decision = cascade.evaluate(
            policy_config=self._policy(global_usd=1.0),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=1024,
        )
        assert decision.action == DOWNGRADE_REDUCE_TOKENS
        assert decision.downgraded_max_tokens < 1024
        assert not decision.stopped

    def test_remove_reviewers_at_70pct_family(self):
        cascade = self._cascade()
        ledger = self._ledger_with_usage(SCOPE_FAMILY, "fam-001", 0.35)  # 70% of 0.5
        decision = cascade.evaluate(
            policy_config=self._policy(family_usd=0.5),
            ledger=ledger,
            family_id="fam-001",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
            reviewer_roles=["cheap_critic", "overfitting_skeptic"],
        )
        assert decision.action == DOWNGRADE_REMOVE_REVIEWERS
        assert set(decision.removed_roles) == {"cheap_critic", "overfitting_skeptic"}

    def test_cheap_tiers_at_80pct(self):
        cascade = self._cascade()
        ledger = self._ledger_with_usage(SCOPE_GLOBAL, "global", 0.80)  # 80%
        decision = cascade.evaluate(
            policy_config=self._policy(global_usd=1.0),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
        )
        assert decision.action == DOWNGRADE_CHEAP_TIERS
        assert decision.force_cheap_tiers is True

    def test_single_task_at_90pct(self):
        cascade = self._cascade()
        ledger = self._ledger_with_usage(SCOPE_GLOBAL, "global", 0.90)  # 90%
        decision = cascade.evaluate(
            policy_config=self._policy(global_usd=1.0),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
            is_mob=True,
        )
        assert decision.action == DOWNGRADE_SINGLE_TASK
        assert decision.force_single_task is True

    def test_stop_at_100pct(self):
        cascade = self._cascade()
        ledger = self._ledger_with_usage(SCOPE_GLOBAL, "global", 1.01)  # over ceiling
        decision = cascade.evaluate(
            policy_config=self._policy(global_usd=1.0),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
        )
        assert decision.action == DOWNGRADE_STOP
        assert decision.stopped is True

    def test_worst_scope_wins(self):
        """Family over 80% but global under 60% → DOWNGRADE_CHEAP_TIERS from family."""
        cascade = self._cascade()
        ledger = BudgetLedger()
        ledger.record_actual(scope=SCOPE_GLOBAL, scope_id="global", task_type="t", tokens=0, estimated_cost_usd=0.3)
        ledger.record_actual(scope=SCOPE_FAMILY, scope_id="fam", task_type="t", tokens=0, estimated_cost_usd=0.45)  # 90% of 0.5
        decision = cascade.evaluate(
            policy_config=self._policy(global_usd=1.0, family_usd=0.5),
            ledger=ledger,
            family_id="fam",
            lineage_id=None,
            task_type="t",
            planned_tokens=512,
            is_mob=True,
        )
        # Family at 90% → DOWNGRADE_SINGLE_TASK
        assert decision.action == DOWNGRADE_SINGLE_TASK

    def test_lineage_budget_triggers_downgrade(self):
        cascade = self._cascade()
        ledger = BudgetLedger()
        ledger.record_actual(scope=SCOPE_LINEAGE, scope_id="lin-001", task_type="t",
                             tokens=0, estimated_cost_usd=0.182)  # 91% of 0.2 (>= 90% threshold)
        decision = cascade.evaluate(
            policy_config=self._policy(),
            ledger=ledger,
            family_id="fam",
            lineage_id="lin-001",
            task_type="t",
            planned_tokens=512,
            is_mob=True,
        )
        assert decision.action == DOWNGRADE_SINGLE_TASK


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert not cb.is_tripped_global()
        assert not cb.is_tripped_family("fam-001")
        assert cb.state_global() == CircuitState.CLOSED

    def test_trip_global(self):
        cb = CircuitBreaker()
        cb.trip_global("daily budget exceeded")
        assert cb.is_tripped_global()
        assert cb.state_global() == CircuitState.OPEN

    def test_trip_family(self):
        cb = CircuitBreaker()
        cb.trip_family("fam-001", "overspend")
        assert cb.is_tripped_family("fam-001")
        assert not cb.is_tripped_family("fam-002")

    def test_reset_global(self):
        cb = CircuitBreaker()
        cb.trip_global("r")
        cb.reset_global()
        assert not cb.is_tripped_global()

    def test_reset_family(self):
        cb = CircuitBreaker()
        cb.trip_family("fam-001", "r")
        cb.reset_family("fam-001")
        assert not cb.is_tripped_family("fam-001")

    def test_idempotent_trip(self):
        """Tripping twice should not add duplicate events."""
        cb = CircuitBreaker()
        cb.trip_global("r")
        cb.trip_global("r again")
        events = [e for e in cb.recent_events() if e.state.value == "open"]
        assert len(events) == 1  # only one trip

    def test_state_summary_contains_required_keys(self):
        cb = CircuitBreaker()
        cb.trip_family("fam-001", "test")
        summary = cb.state_summary()
        assert "global" in summary
        assert "open_families" in summary
        assert "fam-001" in summary["open_families"]

    def test_thread_safe_concurrent_trips(self):
        cb = CircuitBreaker()
        errors = []

        def trip(fam):
            try:
                cb.trip_family(fam, "concurrent")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=trip, args=(f"fam-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


# ---------------------------------------------------------------------------
# CostGovernor
# ---------------------------------------------------------------------------

class TestCostGovernorCheckAndPlan:
    def test_returns_hooks_when_under_budget(self):
        gov = _governor()
        hooks = gov.check_and_plan(
            family_id="fam-001",
            task_type="generate_proposal",
            planned_tokens=1000,
        )
        assert hooks is not None
        assert isinstance(hooks, BudgetHooks)

    def test_hooks_no_constraints_at_zero_usage(self):
        gov = _governor()
        hooks = gov.check_and_plan(
            family_id="fam-001",
            task_type="t",
            planned_tokens=500,
        )
        assert not hooks.has_constraints()

    def test_observe_only_does_not_raise_on_global_circuit_open(self):
        gov = _governor(strict=False)
        gov.circuit.trip_global("test")
        # Should not raise in observe-only mode
        hooks = gov.check_and_plan(family_id="fam-001", task_type="t", planned_tokens=500)
        assert hooks is not None
        assert hooks.downgrade_decision is not None
        assert hooks.downgrade_decision.stopped is True

    def test_strict_raises_on_global_circuit_open(self):
        gov = _governor(strict=True)
        gov.circuit.trip_global("test")
        with pytest.raises(GovernorStopError, match="Global circuit breaker is OPEN"):
            gov.check_and_plan(family_id="fam-001", task_type="t", planned_tokens=500)

    def test_observe_only_does_not_raise_on_family_circuit_open(self):
        gov = _governor(strict=False)
        gov.circuit.trip_family("fam-001", "test")
        hooks = gov.check_and_plan(family_id="fam-001", task_type="t", planned_tokens=500)
        assert hooks is not None
        assert hooks.downgrade_decision.stopped is True

    def test_strict_raises_on_family_circuit_open(self):
        gov = _governor(strict=True)
        gov.circuit.trip_family("fam-001", "test")
        with pytest.raises(GovernorStopError, match="Family.*circuit breaker is OPEN"):
            gov.check_and_plan(family_id="fam-001", task_type="t", planned_tokens=500)

    def test_downgrade_hooks_returned_when_over_soft_threshold(self):
        gov = _governor(family_usd=0.5)
        # Inject 80% usage for the family to trigger cheap_tiers downgrade
        gov.ledger.record_actual(
            scope=SCOPE_FAMILY, scope_id="fam-001",
            task_type="t", tokens=0, estimated_cost_usd=0.40,  # 80% of 0.5
        )
        hooks = gov.check_and_plan(
            family_id="fam-001", task_type="t", planned_tokens=2048,
        )
        assert hooks is not None
        assert hooks.force_cheap_tiers is True

    def test_token_override_set_when_reduce_tokens_step(self):
        gov = _governor(family_usd=0.5)
        gov.ledger.record_actual(
            scope=SCOPE_FAMILY, scope_id="fam-001",
            task_type="t", tokens=0, estimated_cost_usd=0.31,  # 62% of 0.5 → reduce tokens
        )
        hooks = gov.check_and_plan(
            family_id="fam-001", task_type="t", planned_tokens=2048,
        )
        assert hooks.max_tokens_override is not None
        assert hooks.max_tokens_override < 2048

    def test_reviewer_roles_removed_when_remove_reviewers_step(self):
        gov = _governor(family_usd=0.5)
        gov.ledger.record_actual(
            scope=SCOPE_FAMILY, scope_id="fam-001",
            task_type="t", tokens=0, estimated_cost_usd=0.36,  # 72% of 0.5
        )
        hooks = gov.check_and_plan(
            family_id="fam-001", task_type="t", planned_tokens=512,
            reviewer_roles=["cheap_critic"],
        )
        assert "cheap_critic" in hooks.removed_member_roles

    def test_records_planned_entry(self):
        gov = _governor()
        gov.check_and_plan(
            family_id="fam-001", task_type="generate_proposal", planned_tokens=800,
        )
        entries = gov.ledger.recent_entries(limit=10)
        planned = [e for e in entries if e.event_type == EVENT_PLANNED]
        assert len(planned) >= 1


class TestCostGovernorRecordUsage:
    def test_record_usage_adds_actual_entries(self):
        gov = _governor()
        gov.record_usage(
            family_id="fam-001", lineage_id="lin-001",
            task_type="generate_proposal", tokens=1500, cost_usd=0.004, success=True,
        )
        t_g, c_g = gov.ledger.get_daily_usage(SCOPE_GLOBAL, "global")
        t_f, c_f = gov.ledger.get_daily_usage(SCOPE_FAMILY, "fam-001")
        t_l, c_l = gov.ledger.get_daily_usage(SCOPE_LINEAGE, "lin-001")
        assert t_g == 1500
        assert t_f == 1500
        assert t_l == 1500
        assert pytest.approx(c_g) == 0.004

    def test_family_circuit_trips_when_hard_ceiling_exceeded(self):
        gov = _governor(family_usd=0.5)
        # Add usage that exceeds the family ceiling
        gov.record_usage(
            family_id="fam-001", task_type="t",
            tokens=0, cost_usd=0.51, success=True,  # > 0.5 ceiling
        )
        assert gov.circuit.is_tripped_family("fam-001")

    def test_global_circuit_trips_when_hard_ceiling_exceeded(self):
        gov = _governor(budget_usd=1.0)
        gov.record_usage(
            family_id="fam-001", task_type="t",
            tokens=0, cost_usd=1.01, success=True,  # > 1.0 ceiling
        )
        assert gov.circuit.is_tripped_global()

    def test_circuit_does_not_trip_below_ceiling(self):
        gov = _governor(family_usd=0.5)
        gov.record_usage(
            family_id="fam-001", task_type="t",
            tokens=0, cost_usd=0.40, success=True,
        )
        assert not gov.circuit.is_tripped_family("fam-001")

    def test_lineage_not_recorded_when_lineage_id_none(self):
        gov = _governor()
        gov.record_usage(
            family_id="fam-001", lineage_id=None,
            task_type="t", tokens=100, success=True,
        )
        t, _ = gov.ledger.get_daily_usage(SCOPE_LINEAGE, "")
        assert t == 0


class TestCostGovernorBudgetSnapshot:
    def test_snapshot_has_required_keys(self):
        gov = _governor()
        snap = gov.budget_snapshot()
        assert "policy" in snap
        assert "circuit" in snap
        assert "global_usage" in snap
        assert "ledger_summary" in snap
        assert "strict_enforcement" in snap

    def test_pct_used_updates_after_record(self):
        gov = _governor(budget_usd=1.0)
        gov.record_usage(family_id="f", task_type="t", tokens=0, cost_usd=0.5, success=True)
        snap = gov.budget_snapshot()
        assert snap["global_usage"]["pct_used"] == pytest.approx(50.0, abs=0.1)


# ---------------------------------------------------------------------------
# BudgetHooks
# ---------------------------------------------------------------------------

class TestBudgetHooks:
    def test_no_constraints_when_empty(self):
        hooks = BudgetHooks()
        assert not hooks.has_constraints()

    def test_has_constraints_token_override(self):
        hooks = BudgetHooks(max_tokens_override=512)
        assert hooks.has_constraints()

    def test_has_constraints_removed_roles(self):
        hooks = BudgetHooks(removed_member_roles=["critic"])
        assert hooks.has_constraints()

    def test_has_constraints_force_single_task(self):
        hooks = BudgetHooks(force_single_task=True)
        assert hooks.has_constraints()

    def test_to_dict_all_fields(self):
        hooks = BudgetHooks(
            max_tokens_override=256,
            removed_member_roles=["critic"],
            force_cheap_tiers=True,
            force_single_task=False,
            strict=True,
        )
        d = hooks.to_dict()
        assert d["max_tokens_override"] == 256
        assert d["removed_member_roles"] == ["critic"]
        assert d["force_cheap_tiers"] is True
        assert d["strict"] is True


# ---------------------------------------------------------------------------
# RuntimeManager.governor
# ---------------------------------------------------------------------------

class TestRuntimeManagerGovernor:
    def test_governor_property_returns_cost_governor(self, tmp_path):
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(root)
        assert isinstance(manager.governor, CostGovernor)

    def test_governor_policy_loaded(self, tmp_path):
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False), \
             patch.object(config, "FACTORY_GLOBAL_DAILY_BUDGET_USD", 42.0):
            manager = RuntimeManager.create(root)
        assert manager.governor.policy.global_policy.daily_budget_usd == 42.0

    def test_governor_is_same_instance_after_repeated_access(self, tmp_path):
        root = _tmp_root(tmp_path)
        with patch.object(config, "FACTORY_RUNTIME_BACKEND", "legacy"), \
             patch.object(config, "FACTORY_ENABLE_MOBKIT", False):
            manager = RuntimeManager.create(root)
        assert manager.governor is manager.governor


# ---------------------------------------------------------------------------
# MobkitRuntime governor injection
# ---------------------------------------------------------------------------

class TestMobkitRuntimeGovernor:
    def test_mobkit_runtime_accepts_governor(self, tmp_path):
        from factory.runtime.mobkit_backend import MobkitRuntime
        mock_backend = MagicMock()
        mock_backend.BACKEND_NAME = "mobkit"
        gov = _governor()
        rt = MobkitRuntime(tmp_path, backend=mock_backend, governor=gov)
        assert rt._governor is gov

    def test_mobkit_runtime_governor_none_by_default_when_no_create(self, tmp_path):
        """When backend is passed directly, governor must be injected separately."""
        from factory.runtime.mobkit_backend import MobkitRuntime
        mock_backend = MagicMock()
        mock_backend.BACKEND_NAME = "mobkit"
        rt = MobkitRuntime(tmp_path, backend=mock_backend)
        # _governor defaults to None when backend passed without governor
        assert rt._governor is None

    def test_check_and_plan_called_in_run_mob(self, tmp_path):
        from factory.runtime.mobkit_backend import MobkitRuntime, MobkitWorkflowError
        mock_backend = MagicMock()
        mock_backend.run_mob_workflow.return_value = {
            "payload": {"ok": True},
            "member_traces": [],
            "backend": "mobkit",
        }
        gov = MagicMock(spec=CostGovernor)
        gov.check_and_plan.return_value = BudgetHooks()
        gov.record_usage.return_value = None

        rt = MobkitRuntime(tmp_path, backend=mock_backend, governor=gov)
        from unittest.mock import patch as _patch
        from factory.contracts import FactoryFamily, StrategyGenome
        fam = MagicMock(spec=FactoryFamily)
        fam.family_id = "fam-001"
        fam.thesis = "test"
        genome = MagicMock(spec=StrategyGenome)
        genome.genome_id = "g1"
        genome.parameters = {}

        rt.generate_proposal(
            family=fam,
            champion_hypothesis=None,
            champion_genome=genome,
            learning_memory=[],
            execution_evidence=None,
            cycle_count=1,
            proposal_index=0,
        )
        gov.check_and_plan.assert_called_once()
        gov.record_usage.assert_called_once()

    def test_governor_stop_returns_none_in_strict_mode(self, tmp_path):
        """A GovernorStopError in strict mode causes _run_mob to return None."""
        from factory.governance import GovernorStopError
        from factory.runtime.mobkit_backend import MobkitRuntime
        mock_backend = MagicMock()

        gov = MagicMock(spec=CostGovernor)
        gov.check_and_plan.side_effect = GovernorStopError("hard stop", scope="global")

        rt = MobkitRuntime(tmp_path, backend=mock_backend, governor=gov)
        from factory.contracts import FactoryFamily, StrategyGenome
        fam = MagicMock(spec=FactoryFamily)
        fam.family_id = "fam-001"
        fam.thesis = "test"
        genome = MagicMock(spec=StrategyGenome)
        genome.genome_id = "g1"
        genome.parameters = {}

        with pytest.raises(GovernorStopError):
            rt.generate_proposal(
                family=fam,
                champion_hypothesis=None,
                champion_genome=genome,
                learning_memory=[],
                execution_evidence=None,
                cycle_count=1,
                proposal_index=0,
            )
        mock_backend.run_mob_workflow.assert_not_called()


# ---------------------------------------------------------------------------
# Full integration: governor blocks task in strict mode
# ---------------------------------------------------------------------------

class TestGovernorBlocksTaskStrictMode:
    def test_tripped_global_circuit_blocks_check_and_plan(self):
        gov = _governor(strict=True)
        gov.circuit.trip_global("test hard stop")
        with pytest.raises(GovernorStopError):
            gov.check_and_plan(
                family_id="fam-001",
                task_type="generate_proposal",
                planned_tokens=1000,
            )

    def test_family_over_budget_trips_family_circuit(self):
        gov = _governor(family_usd=0.5, strict=False)
        gov.record_usage(family_id="fam-001", task_type="t", tokens=0, cost_usd=0.55)
        assert gov.circuit.is_tripped_family("fam-001")
        # Next check_and_plan returns a stopped hooks (observe mode)
        hooks = gov.check_and_plan(family_id="fam-001", task_type="t", planned_tokens=500)
        assert hooks.downgrade_decision.stopped is True

    def test_repeated_usage_accumulates_correctly(self):
        gov = _governor(family_usd=1.0, budget_usd=10.0)
        for _ in range(5):
            gov.record_usage(family_id="fam-001", task_type="t", tokens=100, cost_usd=0.1)
        t, c = gov.ledger.get_daily_usage(SCOPE_FAMILY, "fam-001")
        assert t == 500
        assert pytest.approx(c) == 0.5
