"""AgentRuntime protocol — the stable local interface for all runtime backends.

Business logic in factory/orchestrator.py must depend only on this interface,
not on any concrete provider or backend implementation.

Return type uses AgentRunResult (the existing envelope) so the orchestrator
can adopt the interface without changes to its result-handling code during
the migration. The richer AgentRunEnvelope in runtime_contracts.py is used
by future mobkit-backed implementations.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Sequence, runtime_checkable

from factory.agent_runtime import AgentRunResult
from factory.contracts import (
    EvaluationBundle,
    FactoryFamily,
    LearningMemoryEntry,
    LineageRecord,
    ResearchHypothesis,
    StrategyGenome,
)


@runtime_checkable
class AgentRuntime(Protocol):
    """
    Canonical interface for all agent runtime backends.

    Every method maps to one task type in the factory orchestration loop.
    Implementors must return AgentRunResult | None; None means the task
    was skipped (e.g., family disabled, runtime in hard-stop mode).
    """

    def generate_proposal(
        self,
        *,
        family: FactoryFamily,
        champion_hypothesis: Optional[ResearchHypothesis],
        champion_genome: StrategyGenome,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        cycle_count: int,
        proposal_index: int,
        desired_creation_kind: str = "mutation",
        idea_candidates: Optional[Sequence[Dict[str, Any]]] = None,
        dna_summary: Optional[str] = None,
    ) -> Optional[AgentRunResult]: ...

    def generate_family_proposal(
        self,
        *,
        idea: Dict[str, Any],
        existing_family_ids: Sequence[str],
        cycle_count: int,
        proposal_index: int,
        research_portfolio_id: str,
        active_incubation_count: int = 0,
    ) -> Optional[AgentRunResult]: ...

    def suggest_tweak(
        self,
        *,
        lineage: LineageRecord,
        hypothesis: Optional[ResearchHypothesis],
        genome: StrategyGenome,
        row: Dict[str, Any],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
    ) -> Optional[AgentRunResult]: ...

    def critique_post_evaluation(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        review_context: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[AgentRunResult]: ...

    def diagnose_bug(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        execution_evidence: Optional[Dict[str, Any]],
        debug_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunResult]: ...

    def resolve_maintenance_item(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
        maintenance_request: Dict[str, Any],
        review_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunResult]: ...

    def design_model(
        self,
        *,
        idea: Dict[str, Any],
        family_id: str,
        target_venues: Sequence[str],
        thesis: str,
        cycle_count: int,
    ) -> Optional[AgentRunResult]: ...

    def mutate_model(
        self,
        *,
        family_id: str,
        lineage_id: str,
        current_model_code: str,
        class_name: str,
        backtest_results: Dict[str, Any],
        thesis: str,
        tweak_count: int = 0,
    ) -> Optional[AgentRunResult]: ...
