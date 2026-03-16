"""Structured event types for the factory telemetry layer.

Every observable transition in the factory lifecycle corresponds to one
EventType.  Events are emitted via RunLogger and written as structured
JSON to the ``factory.telemetry`` logger.  The format is stable enough
for future log indexing (Loki, CloudWatch, etc.) without requiring a
dedicated sink today.

Event lifecycle (typical cycle):
  BACKEND_SELECTED
  → WORKFLOW_PLANNED → WORKFLOW_STARTED
    → MEMBER_STARTED → MEMBER_FINISHED (× N)
    → (DOWNGRADE_APPLIED) → (FALLBACK_ACTIVATED)
  → WORKFLOW_FINISHED | WORKFLOW_FAILED
  → GOLDFISH_RUN_CREATED → GOLDFISH_RUN_FINALIZED
  → PROMOTION_DECISION | RETIREMENT_DECISION
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from factory.telemetry.trace_context import TraceContext


# ---------------------------------------------------------------------------
# EventType
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """All observable event types in the factory telemetry stream."""

    # Runtime / backend lifecycle
    BACKEND_SELECTED = "backend_selected"

    # Workflow lifecycle
    WORKFLOW_PLANNED = "workflow_planned"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_FINISHED = "workflow_finished"
    WORKFLOW_FAILED = "workflow_failed"

    # Member / mob lifecycle
    MEMBER_STARTED = "member_started"
    MEMBER_FINISHED = "member_finished"

    # Budget governance
    DOWNGRADE_APPLIED = "downgrade_applied"
    CIRCUIT_TRIPPED = "circuit_tripped"

    # Fallback / degraded mode
    FALLBACK_ACTIVATED = "fallback_activated"

    # Provenance
    GOLDFISH_RUN_CREATED = "goldfish_run_created"
    GOLDFISH_RUN_FINALIZED = "goldfish_run_finalized"

    # Lineage decisions
    PROMOTION_DECISION = "promotion_decision"
    RETIREMENT_DECISION = "retirement_decision"


# ---------------------------------------------------------------------------
# UsageEvent
# ---------------------------------------------------------------------------


@dataclass
class UsageEvent:
    """
    One structured telemetry event.

    Only non-None optional fields are included in ``to_dict()`` to keep
    log lines compact.  The ``extra`` dict allows ad-hoc extension without
    schema changes.
    """

    event_type: EventType
    timestamp: datetime

    # Correlation context (may be None for early-lifecycle events)
    trace_ctx: Optional[TraceContext] = None

    # Common payload fields
    backend: Optional[str] = None
    task_type: Optional[str] = None
    tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    reason: Optional[str] = None
    scope: Optional[str] = None
    success: Optional[bool] = None

    # Member-level fields
    member_id: Optional[str] = None
    role: Optional[str] = None

    # Free-form extension (never overrides named fields)
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Produce a compact dict suitable for JSON serialization."""
        d: Dict[str, Any] = {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.trace_ctx is not None:
            d["trace"] = self.trace_ctx.to_dict()
        # Only emit non-None named fields to keep logs compact.
        for attr in (
            "backend", "task_type", "tokens", "cost_usd",
            "reason", "scope", "success", "member_id", "role",
        ):
            val = getattr(self, attr)
            if val is not None:
                d[attr] = val
        if self.extra:
            d.update(self.extra)
        return d
