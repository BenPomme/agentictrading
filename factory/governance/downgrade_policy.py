"""Deterministic downgrade cascade for over-budget tasks.

Downgrade order (from COST_CONTROL_AND_MULTIAGENT_POLICY.md §Downgrade cascade):
1. lower output token limit
2. remove nonessential (non-required) reviewer members
3. switch reviewer model tiers to tier1_cheap
4. collapse mob to single structured task
5. stop the task and log a policy stop

Each step is applied when the usage/ceiling ratio for the relevant scope
crosses the step's activation threshold.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from factory.governance.budget_ledger import (
    SCOPE_FAMILY,
    SCOPE_GLOBAL,
    SCOPE_LINEAGE,
    BudgetLedger,
)
from factory.governance.cost_policy import CostPolicyConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Downgrade action names
# ---------------------------------------------------------------------------

DOWNGRADE_NONE = "none"
DOWNGRADE_REDUCE_TOKENS = "reduce_tokens"
DOWNGRADE_REMOVE_REVIEWERS = "remove_reviewers"
DOWNGRADE_CHEAP_TIERS = "cheap_tiers"
DOWNGRADE_SINGLE_TASK = "single_task"
DOWNGRADE_STOP = "stop"

# Activation thresholds (fraction of soft ceiling).
# A usage ratio above each threshold activates that downgrade step.
_STEP_THRESHOLDS = {
    DOWNGRADE_REDUCE_TOKENS:  0.60,   # > 60 % → reduce tokens
    DOWNGRADE_REMOVE_REVIEWERS: 0.70, # > 70 % → remove non-required reviewers
    DOWNGRADE_CHEAP_TIERS:    0.80,   # > 80 % → force cheap tiers
    DOWNGRADE_SINGLE_TASK:    0.90,   # > 90 % → collapse to single task
    DOWNGRADE_STOP:           1.00,   # > 100 % (hard ceiling) → stop
}

# Token reduction factor at DOWNGRADE_REDUCE_TOKENS step.
_TOKEN_REDUCTION_FACTOR = 0.5


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------

@dataclass
class DowngradeDecision:
    """Result of the cascade evaluation for one task invocation."""

    action: str                         # highest activated downgrade step
    stopped: bool = False               # True when action == DOWNGRADE_STOP
    reason: str = ""
    scope: str = ""                     # which scope triggered the decision
    scope_id: str = ""

    # Token limit adjustments.
    original_max_tokens: int = 0
    downgraded_max_tokens: int = 0

    # Member-level adjustments.
    removed_roles: List[str] = field(default_factory=list)
    force_cheap_tiers: bool = False     # replace tier2/tier3 with tier1_cheap
    force_single_task: bool = False     # collapse mob to single structured task

    # Observability fields for ledger / telemetry.
    usage_ratio: float = 0.0
    triggering_budget_usd: float = 0.0
    triggering_used_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "stopped": self.stopped,
            "reason": self.reason,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "original_max_tokens": self.original_max_tokens,
            "downgraded_max_tokens": self.downgraded_max_tokens,
            "removed_roles": self.removed_roles,
            "force_cheap_tiers": self.force_cheap_tiers,
            "force_single_task": self.force_single_task,
            "usage_ratio": self.usage_ratio,
        }


# ---------------------------------------------------------------------------
# Cascade evaluator
# ---------------------------------------------------------------------------

class DowngradeCascade:
    """
    Evaluates which downgrade step to apply for an upcoming task.

    Only the *highest* step reached by any active scope is returned; the
    caller applies the corresponding constraints before dispatching.
    """

    def evaluate(
        self,
        *,
        policy_config: CostPolicyConfig,
        ledger: BudgetLedger,
        family_id: str,
        lineage_id: Optional[str],
        task_type: str,
        planned_tokens: int,
        is_mob: bool = True,
        reviewer_roles: Optional[List[str]] = None,
    ) -> DowngradeDecision:
        """
        Return the appropriate DowngradeDecision for this task context.

        Evaluation order: global → family → lineage.
        The most severe downgrade across all scopes wins.
        """
        best = DowngradeDecision(
            action=DOWNGRADE_NONE,
            original_max_tokens=planned_tokens,
            downgraded_max_tokens=planned_tokens,
        )

        # ---- Global scope ------------------------------------------------
        g_tokens, g_usd = ledger.get_daily_usage(SCOPE_GLOBAL, "global")
        g_ratio = _ratio(g_usd, policy_config.global_policy.daily_budget_usd)
        global_decision = _cascade_for_ratio(
            ratio=g_ratio,
            scope=SCOPE_GLOBAL,
            scope_id="global",
            reason_prefix="global daily budget",
            planned_tokens=planned_tokens,
            is_mob=is_mob,
            reviewer_roles=reviewer_roles or [],
        )
        if _severity(global_decision.action) > _severity(best.action):
            best = global_decision

        # ---- Family scope ------------------------------------------------
        f_tokens, f_usd = ledger.get_daily_usage(SCOPE_FAMILY, family_id)
        f_ratio = _ratio(f_usd, policy_config.family_policy.daily_budget_usd)
        family_decision = _cascade_for_ratio(
            ratio=f_ratio,
            scope=SCOPE_FAMILY,
            scope_id=family_id,
            reason_prefix=f"family {family_id!r} daily budget",
            planned_tokens=planned_tokens,
            is_mob=is_mob,
            reviewer_roles=reviewer_roles or [],
        )
        if _severity(family_decision.action) > _severity(best.action):
            best = family_decision

        # ---- Lineage scope -----------------------------------------------
        if lineage_id:
            lin_tokens, lin_usd = ledger.get_daily_usage(SCOPE_LINEAGE, lineage_id)
            lin_ratio = _ratio(lin_usd, policy_config.lineage_policy.max_budget_usd)
            lineage_decision = _cascade_for_ratio(
                ratio=lin_ratio,
                scope=SCOPE_LINEAGE,
                scope_id=lineage_id,
                reason_prefix=f"lineage {lineage_id!r} lifetime budget",
                planned_tokens=planned_tokens,
                is_mob=is_mob,
                reviewer_roles=reviewer_roles or [],
            )
            if _severity(lineage_decision.action) > _severity(best.action):
                best = lineage_decision

        if best.action != DOWNGRADE_NONE:
            logger.info(
                "DowngradeCascade: %s → action=%s scope=%s:%s reason=%s",
                task_type, best.action, best.scope, best.scope_id, best.reason,
            )

        return best


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ratio(used: float, ceiling: float) -> float:
    """Safe usage ratio; returns 0.0 if ceiling is zero."""
    if ceiling <= 0.0:
        return 0.0
    return used / ceiling


_SEVERITY_ORDER = [
    DOWNGRADE_NONE,
    DOWNGRADE_REDUCE_TOKENS,
    DOWNGRADE_REMOVE_REVIEWERS,
    DOWNGRADE_CHEAP_TIERS,
    DOWNGRADE_SINGLE_TASK,
    DOWNGRADE_STOP,
]


def _severity(action: str) -> int:
    try:
        return _SEVERITY_ORDER.index(action)
    except ValueError:
        return 0


def _cascade_for_ratio(
    *,
    ratio: float,
    scope: str,
    scope_id: str,
    reason_prefix: str,
    planned_tokens: int,
    is_mob: bool,
    reviewer_roles: List[str],
) -> DowngradeDecision:
    """Apply the downgrade cascade for a single scope ratio."""
    action = DOWNGRADE_NONE
    for step, threshold in sorted(_STEP_THRESHOLDS.items(), key=lambda kv: kv[1]):
        if ratio >= threshold:
            action = step

    if action == DOWNGRADE_NONE:
        return DowngradeDecision(
            action=DOWNGRADE_NONE,
            original_max_tokens=planned_tokens,
            downgraded_max_tokens=planned_tokens,
            scope=scope,
            scope_id=scope_id,
            usage_ratio=ratio,
        )

    sev = _severity(action)
    reduced_tokens = planned_tokens
    removed: List[str] = []
    force_cheap = False
    force_single = False
    stopped = False
    reason = f"{reason_prefix} at {ratio:.0%}"

    if sev >= _severity(DOWNGRADE_REDUCE_TOKENS):
        reduced_tokens = max(256, int(planned_tokens * _TOKEN_REDUCTION_FACTOR))

    if sev >= _severity(DOWNGRADE_REMOVE_REVIEWERS) and reviewer_roles:
        removed = list(reviewer_roles)

    if sev >= _severity(DOWNGRADE_CHEAP_TIERS):
        force_cheap = True

    if sev >= _severity(DOWNGRADE_SINGLE_TASK) and is_mob:
        force_single = True

    if sev >= _severity(DOWNGRADE_STOP):
        stopped = True

    return DowngradeDecision(
        action=action,
        stopped=stopped,
        reason=reason,
        scope=scope,
        scope_id=scope_id,
        original_max_tokens=planned_tokens,
        downgraded_max_tokens=reduced_tokens,
        removed_roles=removed,
        force_cheap_tiers=force_cheap,
        force_single_task=force_single,
        usage_ratio=ratio,
    )
