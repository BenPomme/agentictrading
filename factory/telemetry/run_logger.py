"""Structured telemetry logger for factory runtime events.

RunLogger emits UsageEvents as JSON to the ``factory.telemetry`` named
logger.  Callers configure that logger (handlers, level, formatters)
via standard Python logging configuration; the factory code itself only
emits events.

Key properties:
- Zero overhead when ``factory.telemetry`` effective level > INFO.
- All methods are safe to call with partial/None context — they never
  raise.
- The module-level ``default_logger`` singleton is used throughout the
  codebase so callers don't need to construct instances.

Usage::

    from factory.telemetry.run_logger import default_logger as tel

    tel.backend_selected("mobkit")
    tel.workflow_started("generate_proposal", "mobkit", trace_ctx=ctx)
    tel.workflow_finished("generate_proposal", "mobkit",
                          trace_ctx=ctx, tokens=1500, duration_ms=3200)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from factory.telemetry.trace_context import TraceContext
from factory.telemetry.usage_events import EventType, UsageEvent

_TELEMETRY_LOGGER = logging.getLogger("factory.telemetry")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _emit(event: UsageEvent) -> None:
    """Write a structured JSON event to the factory.telemetry logger."""
    if _TELEMETRY_LOGGER.isEnabledFor(logging.INFO):
        try:
            _TELEMETRY_LOGGER.info(
                "%s",
                json.dumps(event.to_dict(), default=str),
                extra={"event_type": event.event_type.value},
            )
        except Exception:
            # Telemetry must never break the caller.
            pass


# ---------------------------------------------------------------------------
# RunLogger
# ---------------------------------------------------------------------------


class RunLogger:
    """
    Convenience façade over ``_emit``.

    Provides one method per EventType with typed, named parameters so that
    call sites are readable and consistent.  All methods catch exceptions
    internally — telemetry failures never propagate.
    """

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def emit(self, event: UsageEvent) -> None:
        """Emit an arbitrary UsageEvent."""
        _emit(event)

    # ------------------------------------------------------------------
    # Backend lifecycle
    # ------------------------------------------------------------------

    def backend_selected(
        self,
        backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        healthy: Optional[bool] = None,
    ) -> None:
        """Emit BACKEND_SELECTED when RuntimeManager resolves the active backend."""
        extra: Dict[str, Any] = {}
        if healthy is not None:
            extra["healthy"] = healthy
        _emit(UsageEvent(
            event_type=EventType.BACKEND_SELECTED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            backend=backend,
            extra=extra,
        ))

    # ------------------------------------------------------------------
    # Workflow lifecycle
    # ------------------------------------------------------------------

    def workflow_planned(
        self,
        task_type: str,
        backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        planned_tokens: Optional[int] = None,
        is_mob: Optional[bool] = None,
    ) -> None:
        """Emit WORKFLOW_PLANNED before budget gate / dispatch."""
        extra: Dict[str, Any] = {}
        if planned_tokens is not None:
            extra["planned_tokens"] = planned_tokens
        if is_mob is not None:
            extra["is_mob"] = is_mob
        _emit(UsageEvent(
            event_type=EventType.WORKFLOW_PLANNED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            task_type=task_type,
            backend=backend,
            extra=extra,
        ))

    def workflow_started(
        self,
        task_type: str,
        backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
    ) -> None:
        """Emit WORKFLOW_STARTED immediately before calling the backend."""
        _emit(UsageEvent(
            event_type=EventType.WORKFLOW_STARTED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            task_type=task_type,
            backend=backend,
        ))

    def workflow_finished(
        self,
        task_type: str,
        backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Emit WORKFLOW_FINISHED after successful backend return."""
        extra: Dict[str, Any] = {}
        if duration_ms is not None:
            extra["duration_ms"] = duration_ms
        _emit(UsageEvent(
            event_type=EventType.WORKFLOW_FINISHED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            task_type=task_type,
            backend=backend,
            tokens=tokens,
            cost_usd=cost_usd,
            success=True,
            extra=extra,
        ))

    def workflow_failed(
        self,
        task_type: str,
        backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit WORKFLOW_FAILED when backend raises or returns error."""
        _emit(UsageEvent(
            event_type=EventType.WORKFLOW_FAILED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            task_type=task_type,
            backend=backend,
            reason=reason,
            success=False,
        ))

    # ------------------------------------------------------------------
    # Member lifecycle
    # ------------------------------------------------------------------

    def member_started(
        self,
        member_id: str,
        role: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        backend: Optional[str] = None,
    ) -> None:
        """Emit MEMBER_STARTED when a mob member begins work."""
        _emit(UsageEvent(
            event_type=EventType.MEMBER_STARTED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            backend=backend,
            member_id=member_id,
            role=role,
        ))

    def member_finished(
        self,
        member_id: str,
        role: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        backend: Optional[str] = None,
        tokens: Optional[int] = None,
        success: bool = True,
        reason: Optional[str] = None,
    ) -> None:
        """Emit MEMBER_FINISHED when a mob member completes or fails."""
        _emit(UsageEvent(
            event_type=EventType.MEMBER_FINISHED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            backend=backend,
            member_id=member_id,
            role=role,
            tokens=tokens,
            success=success,
            reason=reason,
        ))

    # ------------------------------------------------------------------
    # Budget governance
    # ------------------------------------------------------------------

    def downgrade_applied(
        self,
        task_type: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        scope: Optional[str] = None,
        reason: Optional[str] = None,
        action: Optional[str] = None,
        usage_ratio: Optional[float] = None,
    ) -> None:
        """Emit DOWNGRADE_APPLIED when a budget constraint modifies the workflow."""
        extra: Dict[str, Any] = {}
        if action is not None:
            extra["downgrade_action"] = action
        if usage_ratio is not None:
            extra["usage_ratio"] = round(usage_ratio, 4)
        _emit(UsageEvent(
            event_type=EventType.DOWNGRADE_APPLIED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            task_type=task_type,
            scope=scope,
            reason=reason,
            extra=extra,
        ))

    def circuit_tripped(
        self,
        scope: str,
        scope_id: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit CIRCUIT_TRIPPED when a budget circuit breaker opens."""
        _emit(UsageEvent(
            event_type=EventType.CIRCUIT_TRIPPED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            scope=scope,
            reason=reason,
            extra={"scope_id": scope_id},
        ))

    # ------------------------------------------------------------------
    # Fallback / degraded mode
    # ------------------------------------------------------------------

    def fallback_activated(
        self,
        from_backend: str,
        to_backend: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit FALLBACK_ACTIVATED when runtime degrades to a fallback backend."""
        _emit(UsageEvent(
            event_type=EventType.FALLBACK_ACTIVATED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            backend=from_backend,
            reason=reason,
            extra={"to_backend": to_backend},
        ))

    # ------------------------------------------------------------------
    # Provenance (Goldfish)
    # ------------------------------------------------------------------

    def goldfish_run_created(
        self,
        run_id: str,
        workspace_id: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
    ) -> None:
        """Emit GOLDFISH_RUN_CREATED after a run record is opened in Goldfish."""
        _emit(UsageEvent(
            event_type=EventType.GOLDFISH_RUN_CREATED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            extra={"run_id": run_id, "workspace_id": workspace_id},
        ))

    def goldfish_run_finalized(
        self,
        run_id: str,
        workspace_id: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        success: bool = True,
        reason: Optional[str] = None,
    ) -> None:
        """Emit GOLDFISH_RUN_FINALIZED after a run is closed in Goldfish."""
        _emit(UsageEvent(
            event_type=EventType.GOLDFISH_RUN_FINALIZED,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            success=success,
            reason=reason,
            extra={"run_id": run_id, "workspace_id": workspace_id},
        ))

    # ------------------------------------------------------------------
    # Lineage decisions
    # ------------------------------------------------------------------

    def promotion_decision(
        self,
        lineage_id: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        from_stage: Optional[str] = None,
        to_stage: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit PROMOTION_DECISION when a lineage is promoted."""
        extra: Dict[str, Any] = {"lineage_id": lineage_id}
        if from_stage is not None:
            extra["from_stage"] = from_stage
        if to_stage is not None:
            extra["to_stage"] = to_stage
        _emit(UsageEvent(
            event_type=EventType.PROMOTION_DECISION,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            reason=reason,
            extra=extra,
        ))

    def retirement_decision(
        self,
        lineage_id: str,
        *,
        trace_ctx: Optional[TraceContext] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Emit RETIREMENT_DECISION when a lineage is retired."""
        _emit(UsageEvent(
            event_type=EventType.RETIREMENT_DECISION,
            timestamp=_utcnow(),
            trace_ctx=trace_ctx,
            reason=reason,
            extra={"lineage_id": lineage_id},
        ))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Default RunLogger instance — import this in all call sites.
default_logger: RunLogger = RunLogger()
