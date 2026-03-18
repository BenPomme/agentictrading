"""LegacyRuntime — wraps RealResearchAgentRuntime behind the AgentRuntime interface.

This is a thin delegation layer. It adds no new logic; it only makes the
existing Codex/OpenAI runtime satisfy the AgentRuntime protocol so the
RuntimeManager can treat it interchangeably with future backends.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from factory.agent_runtime import AgentRunResult, RealResearchAgentRuntime
from factory.contracts import (
    EvaluationBundle,
    FactoryFamily,
    LearningMemoryEntry,
    LineageRecord,
    ResearchHypothesis,
    StrategyGenome,
)

logger = logging.getLogger(__name__)

BACKEND_NAME = "legacy"


class LegacyRuntime:
    """
    Adapter that wraps RealResearchAgentRuntime and implements AgentRuntime.

    The inner runtime drives Codex CLI / OpenAI API provider chains exactly
    as before. This class is unchanged in behavior; it only adds the
    interface conformance needed for RuntimeManager.
    """

    def __init__(self, project_root: str | Path) -> None:
        self._inner = RealResearchAgentRuntime(project_root)
        logger.debug("LegacyRuntime initialized at %s", project_root)

    @property
    def backend_name(self) -> str:
        return BACKEND_NAME

    # ------------------------------------------------------------------
    # AgentRuntime interface — all methods delegate directly to _inner
    # ------------------------------------------------------------------

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
    ) -> Optional[AgentRunResult]:
        return self._inner.generate_proposal(
            family=family,
            champion_hypothesis=champion_hypothesis,
            champion_genome=champion_genome,
            learning_memory=learning_memory,
            execution_evidence=execution_evidence,
            cycle_count=cycle_count,
            proposal_index=proposal_index,
            desired_creation_kind=desired_creation_kind,
            idea_candidates=idea_candidates,
            dna_summary=dna_summary,
        )

    def generate_family_proposal(
        self,
        *,
        idea: Dict[str, Any],
        existing_family_ids: Sequence[str],
        cycle_count: int,
        proposal_index: int,
        research_portfolio_id: str,
        active_incubation_count: int = 0,
    ) -> Optional[AgentRunResult]:
        return self._inner.generate_family_proposal(
            idea=idea,
            existing_family_ids=existing_family_ids,
            cycle_count=cycle_count,
            proposal_index=proposal_index,
            research_portfolio_id=research_portfolio_id,
            active_incubation_count=active_incubation_count,
        )

    def suggest_tweak(
        self,
        *,
        lineage: LineageRecord,
        hypothesis: Optional[ResearchHypothesis],
        genome: StrategyGenome,
        row: Dict[str, Any],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Optional[Dict[str, Any]],
    ) -> Optional[AgentRunResult]:
        return self._inner.suggest_tweak(
            lineage=lineage,
            hypothesis=hypothesis,
            genome=genome,
            row=row,
            learning_memory=learning_memory,
            execution_evidence=execution_evidence,
        )

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
    ) -> Optional[AgentRunResult]:
        return self._inner.critique_post_evaluation(
            family=family,
            lineage=lineage,
            genome=genome,
            latest_bundle=latest_bundle,
            learning_memory=learning_memory,
            execution_evidence=execution_evidence,
            review_context=review_context,
            force=force,
        )

    def diagnose_bug(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: Optional[StrategyGenome],
        latest_bundle: Optional[EvaluationBundle],
        execution_evidence: Optional[Dict[str, Any]],
        debug_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRunResult]:
        return self._inner.diagnose_bug(
            family=family,
            lineage=lineage,
            genome=genome,
            latest_bundle=latest_bundle,
            execution_evidence=execution_evidence,
            debug_context=debug_context,
        )

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
    ) -> Optional[AgentRunResult]:
        return self._inner.resolve_maintenance_item(
            family=family,
            lineage=lineage,
            genome=genome,
            latest_bundle=latest_bundle,
            learning_memory=learning_memory,
            execution_evidence=execution_evidence,
            maintenance_request=maintenance_request,
            review_context=review_context,
        )

    def design_model(
        self,
        *,
        idea: Dict[str, Any],
        family_id: str,
        target_venues: Sequence[str],
        thesis: str,
        cycle_count: int,
    ) -> Optional[AgentRunResult]:
        return self._inner.design_model(
            idea=idea,
            family_id=family_id,
            target_venues=target_venues,
            thesis=thesis,
            cycle_count=cycle_count,
        )

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
    ) -> Optional[AgentRunResult]:
        return self._inner.mutate_model(
            family_id=family_id,
            lineage_id=lineage_id,
            current_model_code=current_model_code,
            class_name=class_name,
            backtest_results=backtest_results,
            thesis=thesis,
            tweak_count=tweak_count,
        )
