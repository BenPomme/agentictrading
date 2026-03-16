"""Cost governance package — Task 04.

Entry point: CostGovernor combines policy, ledger, downgrade cascade,
and circuit breakers into a single facade.

Usage (from RuntimeManager or MobkitRuntime)::

    governor = CostGovernor.create()

    # Before dispatching a task:
    hooks = governor.check_and_plan(
        family_id="fam-001",
        lineage_id="lin-001",
        task_type="generate_proposal",
        planned_tokens=2048,
        is_mob=True,
    )
    if hooks is None:
        return None  # hard stop

    # Pass hooks to the backend:
    result = backend.run_mob_workflow(..., budget_hooks=hooks)

    # After completion:
    governor.record_usage(
        family_id="fam-001",
        lineage_id="lin-001",
        task_type="generate_proposal",
        tokens=result_tokens,
        cost_usd=result_cost,
        success=True,
    )
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from factory.governance.budget_ledger import (
    EVENT_ACTUAL,
    SCOPE_FAMILY,
    SCOPE_GLOBAL,
    SCOPE_LINEAGE,
    BudgetLedger,
    LedgerEntry,
    _utcnow,
)
from factory.governance.cost_policy import CostPolicyConfig
from factory.governance.downgrade_policy import (
    DOWNGRADE_NONE,
    DOWNGRADE_STOP,
    DowngradeCascade,
    DowngradeDecision,
)
from factory.governance.safety_circuit import CircuitBreaker

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetHooks",
    "CostGovernor",
    "GovernorStopError",
]


# ---------------------------------------------------------------------------
# BudgetHooks — passed to MobkitOrchestratorBackend
# ---------------------------------------------------------------------------

@dataclass
class BudgetHooks:
    """
    Per-task / per-workflow budget instructions for the mobkit backend.

    Populated by CostGovernor.check_and_plan() and consumed in
    MobkitOrchestratorBackend.run_structured_task() /
    MobkitOrchestratorBackend.run_mob_workflow().
    """
    max_tokens_override: Optional[int] = None
    removed_member_roles: List[str] = field(default_factory=list)
    force_cheap_tiers: bool = False         # replace non-lead tiers with tier1_cheap
    force_single_task: bool = False         # collapse mob to single structured task
    downgrade_decision: Optional[DowngradeDecision] = None
    strict: bool = False                    # if True, stop on breacher; else warn-only

    def has_constraints(self) -> bool:
        """True if any downgrade constraint is active."""
        return bool(
            self.max_tokens_override is not None
            or self.removed_member_roles
            or self.force_cheap_tiers
            or self.force_single_task
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_tokens_override": self.max_tokens_override,
            "removed_member_roles": self.removed_member_roles,
            "force_cheap_tiers": self.force_cheap_tiers,
            "force_single_task": self.force_single_task,
            "strict": self.strict,
            "downgrade": self.downgrade_decision.to_dict() if self.downgrade_decision else None,
        }


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class GovernorStopError(RuntimeError):
    """Raised when strict enforcement blocks a task due to budget breach."""

    def __init__(self, message: str, scope: str = "", scope_id: str = "") -> None:
        super().__init__(message)
        self.scope = scope
        self.scope_id = scope_id


# ---------------------------------------------------------------------------
# CostGovernor
# ---------------------------------------------------------------------------

class CostGovernor:
    """
    Central cost governance facade.

    Combines:
    - CostPolicyConfig: what the limits are
    - BudgetLedger:     what has been spent
    - DowngradeCascade: how to respond to pressure
    - CircuitBreaker:   hard stops
    """

    def __init__(
        self,
        policy: CostPolicyConfig,
        ledger: BudgetLedger,
        circuit: CircuitBreaker,
    ) -> None:
        self._policy = policy
        self._ledger = ledger
        self._circuit = circuit
        self._cascade = DowngradeCascade()

    @classmethod
    def create(cls) -> "CostGovernor":
        """Build from config module with default constructors."""
        return cls(
            policy=CostPolicyConfig.load(),
            ledger=BudgetLedger(),
            circuit=CircuitBreaker(),
        )

    # ------------------------------------------------------------------
    # Properties for external access
    # ------------------------------------------------------------------

    @property
    def policy(self) -> CostPolicyConfig:
        return self._policy

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    @property
    def circuit(self) -> CircuitBreaker:
        return self._circuit

    # ------------------------------------------------------------------
    # Pre-task gate
    # ------------------------------------------------------------------

    def check_and_plan(
        self,
        *,
        family_id: str,
        lineage_id: Optional[str] = None,
        task_type: str,
        planned_tokens: int,
        is_mob: bool = True,
        reviewer_roles: Optional[List[str]] = None,
    ) -> Optional[BudgetHooks]:
        """
        Evaluate circuit breakers and downgrade cascade.

        Returns:
        - ``BudgetHooks`` with downgrade constraints if the task may proceed.
        - ``None`` if strict enforcement is on and the task should be hard-stopped.

        When ``FACTORY_ENABLE_STRICT_BUDGETS=false`` (default): downgrades are
        computed and returned in BudgetHooks, but a stop decision results in a
        logged warning rather than a hard block (returns hooks with stop metadata).
        """
        strict = self._policy.global_policy.strict_enforcement

        # ---- Circuit breaker checks ------------------------------------
        if self._circuit.is_tripped_global():
            msg = "Global circuit breaker is OPEN — halting task"
            self._ledger.record_stop(
                scope=SCOPE_GLOBAL, scope_id="global", task_type=task_type, reason=msg,
            )
            if strict:
                raise GovernorStopError(msg, scope=SCOPE_GLOBAL, scope_id="global")
            logger.warning("CostGovernor (observe-only): %s", msg)
            return BudgetHooks(strict=strict, downgrade_decision=DowngradeDecision(
                action=DOWNGRADE_STOP, stopped=True, reason=msg,
                scope=SCOPE_GLOBAL, scope_id="global",
            ))

        if self._circuit.is_tripped_family(family_id):
            msg = f"Family {family_id!r} circuit breaker is OPEN — halting task"
            self._ledger.record_stop(
                scope=SCOPE_FAMILY, scope_id=family_id, task_type=task_type, reason=msg,
            )
            if strict:
                raise GovernorStopError(msg, scope=SCOPE_FAMILY, scope_id=family_id)
            logger.warning("CostGovernor (observe-only): %s", msg)
            return BudgetHooks(strict=strict, downgrade_decision=DowngradeDecision(
                action=DOWNGRADE_STOP, stopped=True, reason=msg,
                scope=SCOPE_FAMILY, scope_id=family_id,
            ))

        # ---- Downgrade cascade -----------------------------------------
        decision = self._cascade.evaluate(
            policy_config=self._policy,
            ledger=self._ledger,
            family_id=family_id,
            lineage_id=lineage_id,
            task_type=task_type,
            planned_tokens=planned_tokens,
            is_mob=is_mob,
            reviewer_roles=reviewer_roles,
        )

        # ---- Hard stop -------------------------------------------------
        if decision.action == DOWNGRADE_STOP:
            self._ledger.record_stop(
                scope=decision.scope, scope_id=decision.scope_id,
                task_type=task_type, reason=decision.reason,
            )
            # Auto-trip circuit if hard ceiling reached.
            if decision.scope == SCOPE_GLOBAL:
                self._circuit.trip_global(decision.reason)
                self._ledger.record_circuit_trip(
                    scope=SCOPE_GLOBAL, scope_id="global", reason=decision.reason,
                )
            elif decision.scope == SCOPE_FAMILY:
                self._circuit.trip_family(family_id, decision.reason)
                self._ledger.record_circuit_trip(
                    scope=SCOPE_FAMILY, scope_id=family_id, reason=decision.reason,
                )

            if strict:
                raise GovernorStopError(
                    decision.reason,
                    scope=decision.scope,
                    scope_id=decision.scope_id,
                )
            logger.warning(
                "CostGovernor (observe-only): STOP — %s. Task will proceed without enforcement.",
                decision.reason,
            )

        # ---- Record downgrade event if any ------------------------------
        if decision.action not in (DOWNGRADE_NONE, DOWNGRADE_STOP):
            self._ledger.record_downgrade(
                scope=decision.scope,
                scope_id=decision.scope_id,
                task_type=task_type,
                reason=decision.reason,
            )

        # ---- Plan record -----------------------------------------------
        self._ledger.record_planned(
            scope=SCOPE_FAMILY, scope_id=family_id,
            task_type=task_type, tokens=planned_tokens,
        )
        self._ledger.record_planned(
            scope=SCOPE_GLOBAL, scope_id="global",
            task_type=task_type, tokens=planned_tokens,
        )

        # ---- Build hooks -----------------------------------------------
        hooks = BudgetHooks(
            max_tokens_override=(
                decision.downgraded_max_tokens
                if decision.action != DOWNGRADE_NONE and decision.downgraded_max_tokens < planned_tokens
                else None
            ),
            removed_member_roles=list(decision.removed_roles),
            force_cheap_tiers=decision.force_cheap_tiers,
            force_single_task=decision.force_single_task,
            downgrade_decision=decision if decision.action != DOWNGRADE_NONE else None,
            strict=strict,
        )
        return hooks

    # ------------------------------------------------------------------
    # Post-task accounting
    # ------------------------------------------------------------------

    def record_usage(
        self,
        *,
        family_id: str,
        lineage_id: Optional[str] = None,
        task_type: str,
        tokens: int,
        cost_usd: float = 0.0,
        success: bool = True,
    ) -> None:
        """Record actual usage after a task completes."""
        now = _utcnow()
        meta = {"success": success}

        self._ledger.record_actual(
            scope=SCOPE_GLOBAL, scope_id="global",
            task_type=task_type, tokens=tokens, estimated_cost_usd=cost_usd,
            metadata=meta,
        )
        self._ledger.record_actual(
            scope=SCOPE_FAMILY, scope_id=family_id,
            task_type=task_type, tokens=tokens, estimated_cost_usd=cost_usd,
            metadata=meta,
        )
        if lineage_id:
            self._ledger.record_actual(
                scope=SCOPE_LINEAGE, scope_id=lineage_id,
                task_type=task_type, tokens=tokens, estimated_cost_usd=cost_usd,
                metadata=meta,
            )

        # Auto-trip family circuit if hard threshold crossed.
        _, fam_usd = self._ledger.get_daily_usage(SCOPE_FAMILY, family_id)
        fp = self._policy.family_policy
        if fam_usd >= fp.daily_budget_usd * fp.hard_threshold_pct:
            reason = (
                f"Family {family_id!r} daily budget {fam_usd:.4f} USD "
                f">= ceiling {fp.daily_budget_usd:.4f} USD"
            )
            self._circuit.trip_family(family_id, reason)
            self._ledger.record_circuit_trip(
                scope=SCOPE_FAMILY, scope_id=family_id, reason=reason,
            )

        # Auto-trip global circuit.
        _, glob_usd = self._ledger.get_daily_usage(SCOPE_GLOBAL, "global")
        gp = self._policy.global_policy
        if glob_usd >= gp.daily_budget_usd * gp.hard_threshold_pct:
            reason = (
                f"Global daily budget {glob_usd:.4f} USD "
                f">= ceiling {gp.daily_budget_usd:.4f} USD"
            )
            self._circuit.trip_global(reason)
            self._ledger.record_circuit_trip(
                scope=SCOPE_GLOBAL, scope_id="global", reason=reason,
            )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def budget_snapshot(self) -> Dict[str, Any]:
        """Full state snapshot for dashboard / operator visibility."""
        g_tokens, g_usd = self._ledger.get_daily_usage(SCOPE_GLOBAL, "global")
        gp = self._policy.global_policy
        return {
            "policy": self._policy.to_dict(),
            "circuit": self._circuit.state_summary(),
            "global_usage": {
                "tokens_today": g_tokens,
                "usd_today": round(g_usd, 6),
                "token_limit": gp.daily_token_limit,
                "budget_usd": gp.daily_budget_usd,
                "pct_used": round(g_usd / gp.daily_budget_usd * 100, 1) if gp.daily_budget_usd else 0.0,
            },
            "ledger_summary": self._ledger.snapshot(),
            "strict_enforcement": gp.strict_enforcement,
        }
