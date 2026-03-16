"""Runtime adapter package.

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
    "MobkitOrchestratorBackend",
    "MobkitRuntime",
]


def __getattr__(name: str):
    """Lazy-load mobkit classes to avoid import errors when meerkat_mobkit is absent."""
    if name in ("MobkitOrchestratorBackend", "MobkitRuntime"):
        from factory.runtime import mobkit_backend as _mb
        return getattr(_mb, name)
    raise AttributeError(f"module 'factory.runtime' has no attribute {name!r}")
