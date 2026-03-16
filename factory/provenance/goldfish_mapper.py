"""goldfish_mapper — deterministic mapping from AgenticTrading types to Goldfish record payloads.

Every factory domain concept maps to a typed descriptor that the GoldfishClient
consumes.  The mapping is kept in one place so that future schema changes only
require editing this file.

AgenticTrading → Goldfish concept map
--------------------------------------
family_id          →  workspace_id
lineage_id         →  run grouping key / tag
experiment eval    →  run + finalized record
promotion          →  record tag + thought
retirement         →  record tag + thought
learning memory    →  thought log entry
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from factory.contracts import (
    EvaluationBundle,
    LearningMemoryEntry,
    LineageRecord,
    StrategyGenome,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# Typed metadata descriptors
# ---------------------------------------------------------------------------


@dataclass
class GoldfishRunMetadata:
    """
    Metadata attached to a Goldfish run when it is created.
    Carries all correlation IDs needed for traceability.
    """
    run_id: str
    workspace_id: str           # == family_id
    lineage_id: str
    family_id: str
    cycle_id: str
    evaluation_id: str
    stage: str
    backend: str = "legacy"
    model_code_hash: Optional[str] = None
    parameter_genome_hash: Optional[str] = None
    dataset_fingerprint: Optional[str] = None
    budget_snapshot: Optional[Dict[str, Any]] = None
    orchestration_backend: str = "legacy"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "lineage_id": self.lineage_id,
            "family_id": self.family_id,
            "cycle_id": self.cycle_id,
            "evaluation_id": self.evaluation_id,
            "stage": self.stage,
            "backend": self.backend,
            "model_code_hash": self.model_code_hash,
            "parameter_genome_hash": self.parameter_genome_hash,
            "dataset_fingerprint": self.dataset_fingerprint,
            "budget_snapshot": self.budget_snapshot or {},
            "orchestration_backend": self.orchestration_backend,
            "created_at": self.created_at,
        }


@dataclass
class GoldfishRetirementRecord:
    """Metadata written when a lineage is retired."""
    lineage_id: str
    family_id: str
    workspace_id: str
    reason: str
    best_metrics: Dict[str, Any]
    cost_summary: Dict[str, Any]
    lessons: List[str]
    retired_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "family_id": self.family_id,
            "workspace_id": self.workspace_id,
            "reason": self.reason,
            "best_metrics": self.best_metrics,
            "cost_summary": self.cost_summary,
            "lessons": self.lessons,
            "retired_at": self.retired_at,
        }


@dataclass
class GoldfishPromotionRecord:
    """Metadata written when a lineage is promoted."""
    lineage_id: str
    family_id: str
    workspace_id: str
    from_stage: str
    to_stage: str
    decision: Dict[str, Any]
    evidence_ids: List[str]
    promoted_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "family_id": self.family_id,
            "workspace_id": self.workspace_id,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "decision": self.decision,
            "evidence_ids": self.evidence_ids,
            "promoted_at": self.promoted_at,
        }


@dataclass
class GoldfishLearningNote:
    """A durable research note derived from a LearningMemoryEntry."""
    workspace_id: str
    lineage_id: str
    family_id: str
    outcome: str
    summary: str
    scientific_domains: List[str]
    recommendations: List[str]
    memory_id: str
    created_at: str = field(default_factory=utc_now_iso)

    def to_thought_text(self) -> str:
        return (
            f"LEARNING [{self.family_id}/{self.lineage_id}] outcome={self.outcome}\n"
            f"{self.summary}\n"
            f"domains={', '.join(self.scientific_domains)}\n"
            f"recommendations={'; '.join(self.recommendations)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "lineage_id": self.lineage_id,
            "family_id": self.family_id,
            "outcome": self.outcome,
            "summary": self.summary,
            "scientific_domains": self.scientific_domains,
            "recommendations": self.recommendations,
            "memory_id": self.memory_id,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def _genome_hash(genome: Optional[StrategyGenome]) -> Optional[str]:
    """Stable hash of a genome's parameter dict."""
    if genome is None:
        return None
    raw = str(sorted((genome.parameters or {}).items()))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _evaluation_run_id(lineage_id: str, evaluation_id: str) -> str:
    """Deterministic run_id from lineage and evaluation IDs."""
    combined = f"{lineage_id}:{evaluation_id}"
    return "run-" + hashlib.sha1(combined.encode()).hexdigest()[:12]


def build_evaluation_run_metadata(
    *,
    bundle: EvaluationBundle,
    lineage: LineageRecord,
    genome: Optional[StrategyGenome] = None,
    cycle_id: str,
    orchestration_backend: str = "legacy",
    budget_snapshot: Optional[Dict[str, Any]] = None,
) -> GoldfishRunMetadata:
    """
    Map an EvaluationBundle + LineageRecord into a GoldfishRunMetadata.

    This is the canonical mapping for experiment evaluation → Goldfish run.
    """
    run_id = _evaluation_run_id(lineage.lineage_id, bundle.evaluation_id)
    return GoldfishRunMetadata(
        run_id=run_id,
        workspace_id=lineage.family_id,
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        cycle_id=cycle_id,
        evaluation_id=bundle.evaluation_id,
        stage=bundle.stage,
        model_code_hash=_genome_hash(genome),
        parameter_genome_hash=_genome_hash(genome),
        dataset_fingerprint=None,  # TODO Task 05: attach dataset fingerprint
        budget_snapshot=budget_snapshot,
        orchestration_backend=orchestration_backend,
    )


def build_evaluation_result_payload(bundle: EvaluationBundle) -> Dict[str, Any]:
    """
    Flatten an EvaluationBundle into a dict suitable for Goldfish run finalization.
    """
    return {
        "evaluation_id": bundle.evaluation_id,
        "stage": bundle.stage,
        "source": bundle.source,
        "monthly_roi_pct": bundle.monthly_roi_pct,
        "max_drawdown_pct": bundle.max_drawdown_pct,
        "calibration_lift_abs": bundle.calibration_lift_abs,
        "fitness_score": bundle.fitness_score,
        "trade_count": bundle.trade_count,
        "paper_days": bundle.paper_days,
        "hard_vetoes": list(bundle.hard_vetoes),
        "notes": list(bundle.notes),
        "windows": [w.to_dict() for w in bundle.windows],
        "generated_at": bundle.generated_at,
    }


def build_retirement_metadata(
    *,
    lineage: LineageRecord,
    reason: str,
    best_metrics: Dict[str, Any],
    lessons: List[str],
    cost_summary: Optional[Dict[str, Any]] = None,
) -> GoldfishRetirementRecord:
    """Map a lineage retirement into a GoldfishRetirementRecord."""
    return GoldfishRetirementRecord(
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        workspace_id=lineage.family_id,
        reason=reason,
        best_metrics=best_metrics,
        cost_summary=cost_summary or {},
        lessons=lessons,
    )


def build_promotion_metadata(
    *,
    lineage: LineageRecord,
    from_stage: str,
    to_stage: str,
    decision: Dict[str, Any],
    evidence_ids: Optional[List[str]] = None,
) -> GoldfishPromotionRecord:
    """Map a lineage promotion event into a GoldfishPromotionRecord."""
    return GoldfishPromotionRecord(
        lineage_id=lineage.lineage_id,
        family_id=lineage.family_id,
        workspace_id=lineage.family_id,
        from_stage=from_stage,
        to_stage=to_stage,
        decision=decision,
        evidence_ids=evidence_ids or [],
    )


def build_learning_note_metadata(
    *,
    memory: LearningMemoryEntry,
) -> GoldfishLearningNote:
    """Map a LearningMemoryEntry into a GoldfishLearningNote."""
    return GoldfishLearningNote(
        workspace_id=memory.family_id,
        lineage_id=memory.lineage_id,
        family_id=memory.family_id,
        outcome=memory.outcome,
        summary=memory.summary,
        scientific_domains=list(memory.scientific_domains),
        recommendations=list(memory.recommendations),
        memory_id=memory.memory_id,
        created_at=memory.created_at,
    )
