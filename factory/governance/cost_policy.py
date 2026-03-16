"""Cost policy configuration objects — five budget levels.

All thresholds are loaded from config with conservative defaults.
Policy objects are immutable (frozen=True); re-create to change policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import config


@dataclass(frozen=True)
class GlobalPolicy:
    """Daily factory-wide budget ceilings."""

    daily_budget_usd: float = 10.0
    daily_token_limit: int = 500_000
    max_concurrent_workflows: int = 3
    max_cycles_per_day: int = 48

    # Fraction of ceiling that triggers soft vs hard threshold.
    soft_threshold_pct: float = 0.75  # → throttle mode
    hard_threshold_pct: float = 1.0   # → circuit trip

    # Feature flag from config — observe-only when False.
    strict_enforcement: bool = False


@dataclass(frozen=True)
class FamilyPolicy:
    """Per-family daily budget ceilings (applied to every family by default)."""

    daily_budget_usd: float = 2.0
    daily_token_limit: int = 100_000
    max_new_lineages_per_day: int = 3
    max_critique_depth_per_day: int = 5
    max_expensive_runs_per_day: int = 2

    soft_threshold_pct: float = 0.75
    hard_threshold_pct: float = 1.0


@dataclass(frozen=True)
class LineagePolicy:
    """Lifetime limits for a single lineage candidate."""

    max_budget_usd: float = 1.0
    max_mutations: int = 5
    max_failed_backtests: int = 3
    max_critique_rounds: int = 3


@dataclass(frozen=True)
class TaskPolicy:
    """Limits applied to one structured task or mob workflow execution."""

    default_max_tokens: int = 2048
    default_timeout_seconds: int = 120
    max_retries: int = 1
    schema_retry_limit: int = 1
    max_tool_calls: int = 20


@dataclass(frozen=True)
class MemberPolicy:
    """Limits applied to a single mob member."""

    default_max_tokens: int = 512
    reviewer_max_tokens: int = 512
    lead_max_tokens: int = 2048
    timeout_seconds: int = 60


@dataclass
class CostPolicyConfig:
    """Aggregated policy for all five budget levels."""

    global_policy: GlobalPolicy
    family_policy: FamilyPolicy
    lineage_policy: LineagePolicy
    task_policy: TaskPolicy
    member_policy: MemberPolicy

    @classmethod
    def load(cls) -> "CostPolicyConfig":
        """Construct from config module, applying defaults where keys are absent."""
        strict = bool(getattr(config, "FACTORY_ENABLE_STRICT_BUDGETS", False))

        global_policy = GlobalPolicy(
            daily_budget_usd=float(getattr(config, "FACTORY_GLOBAL_DAILY_BUDGET_USD", 10.0)),
            daily_token_limit=int(getattr(config, "FACTORY_GLOBAL_DAILY_TOKENS", 500_000)),
            max_concurrent_workflows=int(getattr(config, "FACTORY_GLOBAL_MAX_CONCURRENT_WORKFLOWS", 3)),
            max_cycles_per_day=int(getattr(config, "FACTORY_GLOBAL_MAX_CYCLES_PER_DAY", 48)),
            strict_enforcement=strict,
        )

        family_policy = FamilyPolicy(
            daily_budget_usd=float(getattr(config, "FACTORY_FAMILY_DAILY_BUDGET_USD", 2.0)),
            daily_token_limit=int(getattr(config, "FACTORY_FAMILY_DAILY_TOKENS", 100_000)),
            max_new_lineages_per_day=int(getattr(config, "FACTORY_FAMILY_MAX_NEW_LINEAGES_PER_DAY", 3)),
            max_critique_depth_per_day=int(getattr(config, "FACTORY_FAMILY_MAX_CRITIQUE_DEPTH_PER_DAY", 5)),
            max_expensive_runs_per_day=int(getattr(config, "FACTORY_FAMILY_MAX_EXPENSIVE_RUNS_PER_DAY", 2)),
        )

        lineage_policy = LineagePolicy(
            max_budget_usd=float(getattr(config, "FACTORY_LINEAGE_MAX_BUDGET_USD", 1.0)),
            max_mutations=int(getattr(config, "FACTORY_LINEAGE_MAX_MUTATIONS", 5)),
            max_failed_backtests=int(getattr(config, "FACTORY_LINEAGE_MAX_FAILED_BACKTESTS", 3)),
            max_critique_rounds=int(getattr(config, "FACTORY_LINEAGE_MAX_CRITIQUE_ROUNDS", 3)),
        )

        task_policy = TaskPolicy(
            default_max_tokens=int(getattr(config, "FACTORY_TASK_DEFAULT_MAX_TOKENS", 2048)),
            default_timeout_seconds=int(getattr(config, "FACTORY_TASK_DEFAULT_TIMEOUT_SECONDS", 120)),
            max_retries=int(getattr(config, "FACTORY_TASK_MAX_RETRIES", 1)),
            schema_retry_limit=int(getattr(config, "FACTORY_TASK_SCHEMA_RETRY_LIMIT", 1)),
        )

        member_policy = MemberPolicy(
            default_max_tokens=int(getattr(config, "FACTORY_MOB_MEMBER_DEFAULT_MAX_TOKENS", 512)),
            reviewer_max_tokens=int(getattr(config, "FACTORY_MOB_REVIEWER_MAX_TOKENS", 512)),
            lead_max_tokens=int(getattr(config, "FACTORY_MOB_LEAD_MAX_TOKENS", 2048)),
        )

        return cls(
            global_policy=global_policy,
            family_policy=family_policy,
            lineage_policy=lineage_policy,
            task_policy=task_policy,
            member_policy=member_policy,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Snapshot for observability / dashboard output."""
        return {
            "global": {
                "daily_budget_usd": self.global_policy.daily_budget_usd,
                "daily_token_limit": self.global_policy.daily_token_limit,
                "strict_enforcement": self.global_policy.strict_enforcement,
            },
            "family": {
                "daily_budget_usd": self.family_policy.daily_budget_usd,
                "daily_token_limit": self.family_policy.daily_token_limit,
                "max_new_lineages_per_day": self.family_policy.max_new_lineages_per_day,
            },
            "lineage": {
                "max_budget_usd": self.lineage_policy.max_budget_usd,
                "max_mutations": self.lineage_policy.max_mutations,
            },
            "task": {
                "default_max_tokens": self.task_policy.default_max_tokens,
                "max_retries": self.task_policy.max_retries,
            },
            "member": {
                "default_max_tokens": self.member_policy.default_max_tokens,
                "lead_max_tokens": self.member_policy.lead_max_tokens,
            },
        }
