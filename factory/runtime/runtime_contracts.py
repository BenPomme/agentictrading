"""Typed data contracts for the runtime adapter layer.

These are the canonical envelopes for all runtime task results.
The legacy runtime maps AgentRunResult into AgentRunEnvelope so the
rest of the system has a single result type to depend on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class RuntimeUsage:
    """Token and cost accounting for one runtime invocation."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class RuntimeMemberTrace:
    """Trace record for one member in a mob/multi-agent workflow."""
    member_id: str
    role: str
    model: Optional[str]
    success: bool
    usage: Optional[RuntimeUsage] = None
    fallback_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role": self.role,
            "model": self.model,
            "success": self.success,
            "usage": self.usage.to_dict() if self.usage else None,
            "fallback_reason": self.fallback_reason,
        }


@dataclass
class RuntimeBudgetDecision:
    """Budget gate result attached to a runtime envelope."""
    allowed: bool
    reason: Optional[str] = None
    downgrade_applied: bool = False
    scope: Optional[str] = None  # "global" | "family" | "lineage" | "task" | "member"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "downgrade_applied": self.downgrade_applied,
            "scope": self.scope,
        }


@dataclass
class AgentRunEnvelope:
    """
    Canonical result envelope for all runtime task invocations.

    The legacy runtime populates a subset of these fields from AgentRunResult.
    Future mobkit/Meerkat backends will populate the full set.
    """
    run_id: str
    trace_id: str
    backend: str              # "legacy" | "mobkit" | ...
    task_type: str
    success: bool
    payload: Dict[str, Any]
    started_at: datetime
    finished_at: datetime

    # Provider / model identity
    provider: Optional[str] = None
    model: Optional[str] = None
    model_class: Optional[str] = None    # e.g. TASK_CHEAP, TASK_STANDARD

    # Raw output
    raw_text: Optional[str] = None

    # Cost / usage
    usage: Optional[RuntimeUsage] = None

    # Multi-agent traces (empty for single-agent legacy tasks)
    member_traces: List[RuntimeMemberTrace] = field(default_factory=list)

    # Budget governance
    budget_decision: Optional[RuntimeBudgetDecision] = None

    # Fallback
    fallback_reason: Optional[str] = None
    fallback_used: bool = False

    # Correlation IDs for downstream provenance (Goldfish, lineage, etc.)
    family_id: str = ""
    lineage_id: Optional[str] = None

    # Error detail when success=False
    error: Optional[str] = None

    def duration_ms(self) -> int:
        delta = self.finished_at - self.started_at
        return max(0, int(delta.total_seconds() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "backend": self.backend,
            "task_type": self.task_type,
            "success": self.success,
            "payload": self.payload,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms(),
            "provider": self.provider,
            "model": self.model,
            "model_class": self.model_class,
            "raw_text": self.raw_text,
            "usage": self.usage.to_dict() if self.usage else None,
            "member_traces": [t.to_dict() for t in self.member_traces],
            "budget_decision": self.budget_decision.to_dict() if self.budget_decision else None,
            "fallback_reason": self.fallback_reason,
            "fallback_used": self.fallback_used,
            "family_id": self.family_id,
            "lineage_id": self.lineage_id,
            "error": self.error,
        }

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
