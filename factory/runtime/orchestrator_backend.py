"""OrchestratorBackend protocol — boundary for mob/workflow orchestrators.

This is the interface that a mobkit backend will implement in Task 03.
Defined here so the RuntimeManager has a stable type to reference and
tests can stub the backend without depending on any external SDK.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class OrchestratorBackend(Protocol):
    """
    Canonical interface for a multi-agent orchestration backend.

    Implementations:
    - Task 03: MobkitOrchestratorBackend
    - Stub: NullOrchestratorBackend (used when mobkit is disabled)
    """

    @property
    def backend_name(self) -> str:
        """Identifies this backend in logs and envelopes."""
        ...

    def healthcheck(self) -> bool:
        """
        Verify backend connectivity and capability surface.
        Must be fast enough for startup validation.
        Returns True if the backend is available and functional.
        """
        ...

    def run_structured_task(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: Dict[str, Any],
        model_tier: str,
        family_id: str,
        lineage_id: Optional[str],
        trace_id: str,
        max_tokens: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute a single structured task with schema enforcement.
        Returns the validated payload dict.
        Raises RuntimeError if the task fails after retries.
        """
        ...

    def run_mob_workflow(
        self,
        *,
        workflow_name: str,
        role_definitions: List[Dict[str, Any]],
        shared_context: Dict[str, Any],
        output_schema: Dict[str, Any],
        trace_id: str,
        family_id: str,
        lineage_id: Optional[str],
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute a coordinated multi-member workflow.
        Returns the final validated output dict plus member_traces.
        Raises RuntimeError if the workflow fails.
        """
        ...

    def cancel_run(self, run_id: str) -> None:
        """
        Cooperatively cancel a long-running workflow.
        Best-effort; implementations may ignore if already complete.
        """
        ...


class NullOrchestratorBackend:
    """
    No-op stub used when mobkit is disabled or unavailable.
    Every call raises RuntimeError so callers know explicitly that the
    backend is not active — no silent fallthrough.
    """

    @property
    def backend_name(self) -> str:
        return "null"

    def healthcheck(self) -> bool:
        return False

    def run_structured_task(self, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("NullOrchestratorBackend: mobkit backend is not enabled")

    def run_mob_workflow(self, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("NullOrchestratorBackend: mobkit backend is not enabled")

    def cancel_run(self, run_id: str) -> None:
        pass
