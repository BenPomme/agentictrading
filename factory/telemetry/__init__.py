"""Telemetry package — trace correlation, structured events, operator status.

Public surface:

    from factory.telemetry import TraceContext, RunLogger, default_logger
    from factory.telemetry import OperatorStatus, build_operator_status
    from factory.telemetry import EventType, UsageEvent

The ``default_logger`` singleton is the recommended import for emitting
events from runtime and provenance code:

    from factory.telemetry import default_logger as tel
    tel.workflow_started("generate_proposal", "legacy")
"""
from __future__ import annotations

from factory.telemetry.correlation import OperatorStatus, build_operator_status
from factory.telemetry.run_logger import RunLogger, default_logger
from factory.telemetry.trace_context import TraceContext
from factory.telemetry.usage_events import EventType, UsageEvent

__all__ = [
    "TraceContext",
    "EventType",
    "UsageEvent",
    "RunLogger",
    "default_logger",
    "OperatorStatus",
    "build_operator_status",
]
