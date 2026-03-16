"""Runtime adapter package — Task 01 scaffolding.

Provides the stable boundary between AgenticTrading business logic and any
concrete runtime/orchestration implementation (legacy Codex path, mobkit, etc.).
"""
from factory.runtime.runtime_contracts import (
    AgentRunEnvelope,
    RuntimeBudgetDecision,
    RuntimeMemberTrace,
    RuntimeUsage,
)
from factory.runtime.runtime_manager import RuntimeManager

__all__ = [
    "AgentRunEnvelope",
    "RuntimeBudgetDecision",
    "RuntimeMemberTrace",
    "RuntimeUsage",
    "RuntimeManager",
]
