from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request

import config
from factory.contracts import (
    EvaluationBundle,
    FactoryFamily,
    LearningMemoryEntry,
    LineageRecord,
    ResearchHypothesis,
    StrategyGenome,
    utc_now_iso,
)
from factory.idea_intake import relevant_ideas_for_family
from factory.strategy_inventor import ScientificAgentProposal, build_proposal_title, normalize_alpha_thesis


TASK_CHEAP = "cheap_structured"
TASK_STANDARD = "standard_research"
TASK_HARD = "hard_research"
TASK_FRONTIER = "frontier_research"
TASK_DEEP = "deep_review"
OVERRIDE_KEYS = [
    "selected_horizon_seconds",
    "selected_feature_subset",
    "selected_model_class",
    "selected_min_edge",
    "selected_stake_fraction",
    "selected_learning_rate",
    "selected_lookback_hours",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _factory_root(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
    if not factory_root.is_absolute():
        factory_root = root / factory_root
    return factory_root


def _resolve_log_dir(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    configured = str(getattr(config, "FACTORY_AGENT_LOG_DIR", "data/factory/agent_runs") or "").strip()
    if not configured or configured == "data/factory/agent_runs":
        base = _factory_root(root) / "agent_runs"
    else:
        base = Path(configured)
        if not base.is_absolute():
            base = root / base
    base.mkdir(parents=True, exist_ok=True)
    return base


def _provider_order() -> List[str]:
    raw = str(getattr(config, "FACTORY_AGENT_PROVIDER_ORDER", "codex,deterministic") or "")
    providers = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return providers or ["deterministic"]


def _demo_family() -> str:
    return str(getattr(config, "FACTORY_AGENT_DEMO_FAMILY", "binance_funding_contrarian") or "").strip()


def _enabled_families() -> List[str]:
    raw = str(getattr(config, "FACTORY_AGENT_ENABLED_FAMILIES", "") or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ollama_fallback_enabled() -> bool:
    return bool(getattr(config, "FACTORY_AGENT_OLLAMA_FALLBACK_ENABLED", False))


def _post_eval_critique_enabled() -> bool:
    return bool(getattr(config, "FACTORY_AGENT_POST_EVAL_CRITIQUE_ENABLED", False))


def _task_model(task_class: str) -> str:
    mapping = {
        TASK_CHEAP: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_CHEAP", "gpt-5.1-codex-mini")),
        TASK_STANDARD: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")),
        TASK_HARD: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_HARD", "gpt-5.2-codex")),
        TASK_FRONTIER: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_FRONTIER", "gpt-5.3-codex")),
        TASK_DEEP: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_DEEP", "gpt-5.4")),
    }
    return mapping.get(task_class, str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")))


def _task_reasoning(task_class: str) -> str:
    mapping = {
        TASK_CHEAP: str(getattr(config, "FACTORY_AGENT_REASONING_CHEAP", "medium")),
        TASK_STANDARD: str(getattr(config, "FACTORY_AGENT_REASONING_STANDARD", "medium")),
        TASK_HARD: str(getattr(config, "FACTORY_AGENT_REASONING_HARD", "high")),
        TASK_FRONTIER: str(getattr(config, "FACTORY_AGENT_REASONING_FRONTIER", "high")),
        TASK_DEEP: str(getattr(config, "FACTORY_AGENT_REASONING_DEEP", "high")),
    }
    return mapping.get(task_class, "medium")


def _proposal_model() -> str:
    return str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_PROPOSAL", "gpt-5.4") or "gpt-5.4")


def _proposal_reasoning() -> str:
    return str(getattr(config, "FACTORY_AGENT_REASONING_PROPOSAL", "high") or "high")


def _idea_excerpt(project_root: Path, *, max_lines: int = 12, max_chars: int = 1600) -> str:
    for name in ("ideas.md", "IDEAS.md"):
        path = project_root / name
        if path.exists():
            lines = [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            excerpt = "\n".join(lines[:max_lines])
            return excerpt[:max_chars]
    return ""


def _truncate_memories(memories: Sequence[LearningMemoryEntry], limit: int = 6) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for memory in list(memories)[-limit:]:
        execution_evidence = dict(memory.execution_evidence or {})
        rows.append(
            {
                "outcome": memory.outcome,
                "summary": memory.summary,
                "domains": list(memory.scientific_domains),
                "recommendations": list(memory.recommendations),
                "metrics": dict(memory.metrics),
                "execution_evidence": {
                    "health_status": execution_evidence.get("health_status"),
                    "issue_codes": list(execution_evidence.get("issue_codes") or []),
                    "recommendation_context": list(execution_evidence.get("recommendation_context") or []),
                    "summary": execution_evidence.get("summary"),
                },
            }
        )
    return rows


def _truncate_execution_evidence(evidence: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(evidence or {})
    targets = []
    for row in list(payload.get("targets") or [])[:3]:
        targets.append(
            {
                "requested_target": row.get("requested_target"),
                "resolved_target": row.get("resolved_target"),
                "status": row.get("status"),
                "running": bool(row.get("running")),
                "health_status": row.get("health_status"),
                "issue_codes": list(row.get("issue_codes") or []),
                "error": row.get("error"),
                "blockers": list(row.get("blockers") or []),
                "recent_trade_stats": dict(row.get("recent_trade_stats") or {}),
                "execution_quality": dict(row.get("execution_quality") or {}),
                "training_state": dict(row.get("training_state") or {}),
            }
        )
    return {
        "health_status": payload.get("health_status"),
        "issue_codes": list(payload.get("issue_codes") or []),
        "recommendation_context": list(payload.get("recommendation_context") or []),
        "summary": payload.get("summary"),
        "recent_trade_count": payload.get("recent_trade_count"),
        "recent_event_count": payload.get("recent_event_count"),
        "blocked_target_count": payload.get("blocked_target_count"),
        "critical_issue_count": payload.get("critical_issue_count"),
        "warning_issue_count": payload.get("warning_issue_count"),
        "targets": targets,
    }


def _budget_bucket_default(family: FactoryFamily) -> str:
    split = dict(family.budget_split or {})
    if not split:
        return "incumbent"
    return max(split.items(), key=lambda item: float(item[1] or 0.0))[0]


def _proposal_schema() -> Dict[str, Any]:
    parameter_override_properties = {
        "selected_horizon_seconds": {"type": ["integer", "null"]},
        "selected_feature_subset": {"type": ["string", "null"]},
        "selected_model_class": {"type": ["string", "null"]},
        "selected_min_edge": {"type": ["number", "null"]},
        "selected_stake_fraction": {"type": ["number", "null"]},
        "selected_learning_rate": {"type": ["number", "null"]},
        "selected_lookback_hours": {"type": ["number", "null"]},
    }
    parameter_override_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": parameter_override_properties,
        "required": list(parameter_override_properties),
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "thesis",
            "scientific_domains",
            "lead_agent_role",
            "collaborating_agent_roles",
            "budget_bucket",
            "proposal_kind",
            "source_idea_id",
            "parameter_overrides",
            "agent_notes",
        ],
        "properties": {
            "title": {"type": "string"},
            "thesis": {"type": "string"},
            "scientific_domains": {"type": "array", "items": {"type": "string"}},
            "lead_agent_role": {"type": "string"},
            "collaborating_agent_roles": {"type": "array", "items": {"type": "string"}},
            "budget_bucket": {"type": "string", "enum": ["incumbent", "adjacent", "moonshot"]},
            "proposal_kind": {"type": "string", "enum": ["mutation", "new_model"]},
            "source_idea_id": {"type": ["string", "null"]},
            "parameter_overrides": parameter_override_schema,
            "agent_notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def _tweak_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["parameter_overrides", "agent_notes"],
        "properties": {
            "parameter_overrides": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "selected_horizon_seconds": {"type": ["integer", "null"]},
                    "selected_feature_subset": {"type": ["string", "null"]},
                    "selected_model_class": {"type": ["string", "null"]},
                    "selected_min_edge": {"type": ["number", "null"]},
                    "selected_stake_fraction": {"type": ["number", "null"]},
                    "selected_learning_rate": {"type": ["number", "null"]},
                    "selected_lookback_hours": {"type": ["number", "null"]},
                },
                "required": list(OVERRIDE_KEYS),
            },
            "agent_notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def _critique_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "risks",
            "next_tests",
            "maintenance_action",
            "maintenance_reason",
            "requires_retrain",
            "requires_new_challenger",
        ],
        "properties": {
            "summary": {"type": "string"},
            "risks": {"type": "array", "items": {"type": "string"}},
            "next_tests": {"type": "array", "items": {"type": "string"}},
            "maintenance_action": {
                "type": "string",
                "enum": ["hold", "retrain", "rework", "replace", "retire"],
            },
            "maintenance_reason": {"type": "string"},
            "requires_retrain": {"type": "boolean"},
            "requires_new_challenger": {"type": "boolean"},
        },
    }


def _debug_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "suspected_root_cause",
            "bug_category",
            "severity",
            "recommended_actions",
            "safe_auto_actions",
            "requires_human",
            "human_action",
            "human_owner",
            "should_pause_lineage",
        ],
        "properties": {
            "summary": {"type": "string"},
            "suspected_root_cause": {"type": "string"},
            "bug_category": {"type": "string"},
            "severity": {"type": "string", "enum": ["warning", "critical"]},
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
            "safe_auto_actions": {"type": "array", "items": {"type": "string"}},
            "requires_human": {"type": "boolean"},
            "human_action": {"type": ["string", "null"]},
            "human_owner": {"type": ["string", "null"]},
            "should_pause_lineage": {"type": "boolean"},
        },
    }


@dataclass
class AgentRunResult:
    run_id: str
    task_type: str
    model_class: str
    provider: str
    model: str
    reasoning_effort: str
    success: bool
    fallback_used: bool
    family_id: str
    lineage_id: Optional[str]
    duration_ms: int
    result_payload: Dict[str, Any] = field(default_factory=dict)
    prompt_payload: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    error: Optional[str] = None
    artifact_path: Optional[str] = None
    attempted_providers: List[str] = field(default_factory=list)

    def to_lineage_decision(self, *, kind: str, used_real_agent: bool) -> Dict[str, Any]:
        return {
            "kind": kind,
            "provider": self.provider,
            "model": self.model,
            "task_type": self.task_type,
            "model_class": self.model_class,
            "reasoning_effort": self.reasoning_effort,
            "success": self.success,
            "used_real_agent": used_real_agent,
            "fallback_used": self.fallback_used,
            "artifact_path": self.artifact_path,
            "generated_at": utc_now_iso(),
        }


def recent_agent_runs(project_root: Path | None = None, *, limit: int = 20) -> List[Dict[str, Any]]:
    log_dir = _resolve_log_dir(project_root)
    rows: List[Dict[str, Any]] = []
    for path in sorted(log_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        payload["artifact_path"] = str(path)
        rows.append(payload)
    rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    return rows[:limit]


class RealResearchAgentRuntime:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.log_dir = _resolve_log_dir(self.project_root)

    def generate_proposal(
        self,
        *,
        family: FactoryFamily,
        champion_hypothesis: ResearchHypothesis | None,
        champion_genome: StrategyGenome,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Dict[str, Any] | None,
        cycle_count: int,
        proposal_index: int,
        desired_creation_kind: str = "mutation",
        idea_candidates: Sequence[Dict[str, Any]] | None = None,
    ) -> AgentRunResult | None:
        if not self._family_enabled(family.family_id):
            return None
        task_class = self._proposal_task_class(family, learning_memory, execution_evidence)
        prompt_payload = {
            "cycle_count": cycle_count,
            "proposal_index": proposal_index,
            "family": {
                "family_id": family.family_id,
                "label": family.label,
                "thesis": family.thesis,
                "target_portfolios": list(family.target_portfolios),
                "target_venues": list(family.target_venues),
                "budget_split": dict(family.budget_split or {}),
            },
            "champion_hypothesis": {
                "title": champion_hypothesis.title if champion_hypothesis else family.label,
                "thesis": champion_hypothesis.thesis if champion_hypothesis else family.thesis,
                "scientific_domains": list((champion_hypothesis.scientific_domains if champion_hypothesis else []) or []),
                "lead_agent_role": champion_hypothesis.lead_agent_role if champion_hypothesis else "Director",
            },
            "champion_parameters": dict(champion_genome.parameters),
            "mutation_bounds": champion_genome.mutation_bounds.to_dict(),
            "learning_memory": _truncate_memories(learning_memory),
            "execution_evidence": _truncate_execution_evidence(execution_evidence),
            "idea_intake_excerpt": _idea_excerpt(self.project_root),
            "relevant_ideas": list(idea_candidates or relevant_ideas_for_family(self.project_root, family.family_id, limit=3)),
            "desired_creation_kind": desired_creation_kind,
            "target_mix_policy": {
                "mutation_pct": int(getattr(config, "FACTORY_CHALLENGER_MUTATION_PCT", 80)),
                "new_model_pct": int(getattr(config, "FACTORY_CHALLENGER_NEW_MODEL_PCT", 20)),
            },
        }
        prompt = self._proposal_prompt(prompt_payload, task_class=task_class)
        return self._run_structured(
            task_type="proposal_generation",
            task_class=task_class,
            family_id=family.family_id,
            lineage_id=champion_genome.lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_proposal_schema(),
            model_override=_proposal_model(),
            reasoning_override=_proposal_reasoning(),
        )

    def suggest_tweak(
        self,
        *,
        lineage: LineageRecord,
        hypothesis: ResearchHypothesis | None,
        genome: StrategyGenome,
        row: Dict[str, Any],
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Dict[str, Any] | None,
    ) -> AgentRunResult | None:
        if not self._family_enabled(lineage.family_id):
            return None
        task_class = self._tweak_task_class(lineage, row, execution_evidence)
        prompt_payload = {
            "lineage": {
                "lineage_id": lineage.lineage_id,
                "family_id": lineage.family_id,
                "role": lineage.role,
                "tweak_count": int(lineage.tweak_count or 0),
                "loss_streak": int(lineage.loss_streak or 0),
                "iteration_status": lineage.iteration_status,
            },
            "hypothesis": {
                "title": hypothesis.title if hypothesis else lineage.label,
                "thesis": hypothesis.thesis if hypothesis else "",
                "scientific_domains": list((hypothesis.scientific_domains if hypothesis else []) or []),
            },
            "current_parameters": dict(genome.parameters),
            "mutation_bounds": genome.mutation_bounds.to_dict(),
            "latest_metrics": {
                key: row.get(key)
                for key in [
                    "fitness_score",
                    "monthly_roi_pct",
                    "calibration_lift_abs",
                    "trade_count",
                    "paper_days",
                    "hard_vetoes",
                    "execution_has_signal",
                ]
            },
            "learning_memory": _truncate_memories(learning_memory, limit=4),
            "execution_evidence": _truncate_execution_evidence(execution_evidence),
        }
        prompt = self._tweak_prompt(prompt_payload, task_class=task_class)
        return self._run_structured(
            task_type="underperformance_tweak",
            task_class=task_class,
            family_id=lineage.family_id,
            lineage_id=lineage.lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_tweak_schema(),
        )

    def critique_post_evaluation(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: StrategyGenome | None,
        latest_bundle: EvaluationBundle | None,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Dict[str, Any] | None,
        review_context: Dict[str, Any] | None = None,
        force: bool = False,
    ) -> AgentRunResult | None:
        if (not force and not _post_eval_critique_enabled()) or not self._family_enabled(family.family_id):
            return None
        prompt_payload = {
            "family": {"family_id": family.family_id, "label": family.label, "thesis": family.thesis},
            "lineage": {
                "lineage_id": lineage.lineage_id,
                "role": lineage.role,
                "current_stage": lineage.current_stage,
                "iteration_status": lineage.iteration_status,
            },
            "genome_parameters": dict((genome.parameters if genome else {}) or {}),
            "latest_evaluation": dict((latest_bundle.to_dict() if latest_bundle else {}) or {}),
            "learning_memory": _truncate_memories(learning_memory, limit=8),
            "execution_evidence": _truncate_execution_evidence(execution_evidence),
            "review_context": dict(review_context or {}),
        }
        prompt = self._critique_prompt(prompt_payload, task_class=TASK_DEEP)
        return self._run_structured(
            task_type="post_eval_critique",
            task_class=TASK_DEEP,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_critique_schema(),
        )

    def diagnose_bug(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: StrategyGenome | None,
        latest_bundle: EvaluationBundle | None,
        execution_evidence: Dict[str, Any] | None,
        debug_context: Dict[str, Any] | None = None,
    ) -> AgentRunResult | None:
        if not self._family_enabled(family.family_id):
            return None
        task_class = self._debug_task_class(lineage, execution_evidence)
        prompt_payload = {
            "family": {"family_id": family.family_id, "label": family.label, "thesis": family.thesis},
            "lineage": {
                "lineage_id": lineage.lineage_id,
                "role": lineage.role,
                "current_stage": lineage.current_stage,
                "iteration_status": lineage.iteration_status,
            },
            "genome_parameters": dict((genome.parameters if genome else {}) or {}),
            "latest_evaluation": dict((latest_bundle.to_dict() if latest_bundle else {}) or {}),
            "execution_evidence": _truncate_execution_evidence(execution_evidence),
            "debug_context": dict(debug_context or {}),
        }
        prompt = self._debug_prompt(prompt_payload, task_class=task_class)
        return self._run_structured(
            task_type="runtime_debug_review",
            task_class=task_class,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_debug_schema(),
        )

    def _family_enabled(self, family_id: str) -> bool:
        if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
            return False
        enabled = _enabled_families()
        if enabled:
            return family_id in enabled
        demo_family = _demo_family()
        return not demo_family or family_id == demo_family

    def _proposal_task_class(
        self,
        family: FactoryFamily,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Dict[str, Any] | None,
    ) -> str:
        retired_count = sum(1 for memory in learning_memory if str(memory.outcome).startswith("retired"))
        issue_codes = {str(item) for item in ((execution_evidence or {}).get("issue_codes") or [])}
        health_status = str((execution_evidence or {}).get("health_status") or "")
        model_quality_issues = {
            "negative_paper_roi",
            "poor_win_rate",
            "no_trade_syndrome",
            "zero_simulated_fills",
            "excessive_rejections",
            "slippage_pressure",
            "severe_slippage",
            "untrainable_model",
            "trade_stalled",
            "training_stalled",
            "stalled_model",
        }
        if health_status == "critical" and len(issue_codes) >= 4:
            return TASK_FRONTIER
        if retired_count >= 4:
            return TASK_FRONTIER
        if retired_count >= 2 or self._contradictory_memories(learning_memory) or issue_codes.intersection(model_quality_issues):
            return TASK_HARD
        return TASK_STANDARD

    def _tweak_task_class(
        self,
        lineage: LineageRecord,
        row: Dict[str, Any],
        execution_evidence: Dict[str, Any] | None,
    ) -> str:
        contradictory_metrics = (
            (float(row.get("monthly_roi_pct", 0.0) or 0.0) > 0.0 and bool(row.get("hard_vetoes")))
            or (
                float(row.get("calibration_lift_abs", 0.0) or 0.0) > 0.0
                and float(row.get("fitness_score", 0.0) or 0.0) < 0.0
            )
        )
        issue_codes = {str(item) for item in ((execution_evidence or {}).get("issue_codes") or [])}
        if (
            int(lineage.tweak_count or 0) >= 1
            or contradictory_metrics
            or str((execution_evidence or {}).get("health_status") or "") == "critical"
            or bool(
                issue_codes.intersection(
                    {
                        "negative_paper_roi",
                        "poor_win_rate",
                        "no_trade_syndrome",
                        "zero_simulated_fills",
                        "untrainable_model",
                        "trade_stalled",
                        "training_stalled",
                        "stalled_model",
                    }
                )
            )
        ):
            return TASK_HARD
        return TASK_CHEAP

    def _debug_task_class(
        self,
        lineage: LineageRecord,
        execution_evidence: Dict[str, Any] | None,
    ) -> str:
        issue_codes = {str(item) for item in ((execution_evidence or {}).get("issue_codes") or [])}
        critical_bug_codes = {"runtime_error", "heartbeat_stale", "untrainable_model", "training_stalled", "stalled_model"}
        repeated_debug = bool(lineage.last_debug_issue_signature)
        if (
            str((execution_evidence or {}).get("health_status") or "") == "critical"
            or issue_codes.intersection(critical_bug_codes)
            or repeated_debug
        ):
            return TASK_HARD
        return TASK_CHEAP

    def _contradictory_memories(self, learning_memory: Sequence[LearningMemoryEntry]) -> bool:
        rois = [
            float(memory.metrics.get("monthly_roi_pct", 0.0) or 0.0)
            for memory in learning_memory
            if isinstance(memory.metrics, dict) and "monthly_roi_pct" in memory.metrics
        ]
        if rois and any(value > 0 for value in rois) and any(value < 0 for value in rois):
            return True
        recommended_models = set()
        recommended_features = set()
        for memory in learning_memory:
            text = " ".join(str(item) for item in (memory.recommendations or []))
            for model_class in ["logit", "gbdt", "transformer", "tft", "rules"]:
                if model_class in text:
                    recommended_models.add(model_class)
            for feature in ["baseline", "microstructure", "cross_science", "regime"]:
                if feature in text:
                    recommended_features.add(feature)
        return len(recommended_models) > 1 or len(recommended_features) > 1

    def _run_structured(
        self,
        *,
        task_type: str,
        task_class: str,
        family_id: str,
        lineage_id: Optional[str],
        prompt: str,
        prompt_payload: Dict[str, Any],
        schema: Dict[str, Any],
        model_override: str | None = None,
        reasoning_override: str | None = None,
    ) -> AgentRunResult:
        errors: List[str] = []
        attempted: List[str] = []
        providers = _provider_order()
        for provider in providers:
            if provider == "ollama" and (not _ollama_fallback_enabled() or task_class != TASK_CHEAP):
                continue
            attempted.append(provider)
            if provider == "deterministic":
                result = AgentRunResult(
                    run_id=self._new_run_id(task_type),
                    task_type=task_type,
                    model_class=task_class,
                    provider="deterministic",
                    model=model_override or "none",
                    reasoning_effort=reasoning_override or "none",
                    success=False,
                    fallback_used=bool(errors),
                    family_id=family_id,
                    lineage_id=lineage_id,
                    duration_ms=0,
                    prompt_payload=prompt_payload,
                    result_payload={},
                    raw_text="",
                    error="; ".join(errors) if errors else "deterministic fallback selected",
                    attempted_providers=list(attempted),
                )
                return self._write_run_artifact(result)
            try:
                if provider == "codex":
                    result = self._run_codex(
                        task_type=task_type,
                        task_class=task_class,
                        family_id=family_id,
                        lineage_id=lineage_id,
                        prompt=prompt,
                        prompt_payload=prompt_payload,
                        schema=schema,
                        model_override=model_override,
                        reasoning_override=reasoning_override,
                        fallback_used=bool(errors),
                        attempted=list(attempted),
                    )
                    return self._write_run_artifact(result)
                if provider == "ollama":
                    result = self._run_ollama(
                        task_type=task_type,
                        task_class=task_class,
                        family_id=family_id,
                        lineage_id=lineage_id,
                        prompt=prompt,
                        prompt_payload=prompt_payload,
                        schema=schema,
                        model_override=model_override,
                        reasoning_override=reasoning_override,
                        fallback_used=bool(errors),
                        attempted=list(attempted),
                    )
                    return self._write_run_artifact(result)
            except Exception as exc:
                errors.append(f"{provider}:{exc}")
        failure = AgentRunResult(
            run_id=self._new_run_id(task_type),
            task_type=task_type,
            model_class=task_class,
            provider=attempted[-1] if attempted else "codex",
            model=model_override or _task_model(task_class),
            reasoning_effort=reasoning_override or _task_reasoning(task_class),
            success=False,
            fallback_used=bool(errors),
            family_id=family_id,
            lineage_id=lineage_id,
            duration_ms=0,
            prompt_payload=prompt_payload,
            result_payload={},
            raw_text="",
            error="; ".join(errors) if errors else "no providers attempted",
            attempted_providers=list(attempted),
        )
        return self._write_run_artifact(failure)

    def _run_codex(
        self,
        *,
        task_type: str,
        task_class: str,
        family_id: str,
        lineage_id: Optional[str],
        prompt: str,
        prompt_payload: Dict[str, Any],
        schema: Dict[str, Any],
        model_override: str | None,
        reasoning_override: str | None,
        fallback_used: bool,
        attempted: List[str],
    ) -> AgentRunResult:
        model = model_override or _task_model(task_class)
        reasoning = reasoning_override or _task_reasoning(task_class)
        start = time.perf_counter()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as schema_file:
            json.dump(schema, schema_file)
            schema_path = schema_file.name
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as output_file:
            output_path = output_file.name
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            schema_path,
            "-o",
            output_path,
            "-C",
            str(self.project_root),
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning}"',
            prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=420,
            )
            if proc.returncode != 0:
                details = proc.stderr.strip() or proc.stdout.strip() or f"codex exited {proc.returncode}"
                raise RuntimeError(details)
            raw_text = Path(output_path).read_text(encoding="utf-8").strip()
            payload = json.loads(raw_text)
            return AgentRunResult(
                run_id=self._new_run_id(task_type),
                task_type=task_type,
                model_class=task_class,
                provider="codex",
                model=model,
                reasoning_effort=reasoning,
                success=True,
                fallback_used=fallback_used,
                family_id=family_id,
                lineage_id=lineage_id,
                duration_ms=int((time.perf_counter() - start) * 1000),
                prompt_payload=prompt_payload,
                result_payload=payload,
                raw_text=proc.stdout.strip(),
                attempted_providers=list(attempted),
            )
        finally:
            Path(schema_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def _run_ollama(
        self,
        *,
        task_type: str,
        task_class: str,
        family_id: str,
        lineage_id: Optional[str],
        prompt: str,
        prompt_payload: Dict[str, Any],
        schema: Dict[str, Any],
        model_override: str | None,
        reasoning_override: str | None,
        fallback_used: bool,
        attempted: List[str],
    ) -> AgentRunResult:
        model = model_override or str(getattr(config, "FACTORY_AGENT_OLLAMA_MODEL", "qwen2.5:32b") or "qwen2.5:32b")
        reasoning = reasoning_override or "local"
        start = time.perf_counter()
        body = {
            "model": model,
            "prompt": f"{prompt}\n\nReturn a JSON object that matches this schema exactly:\n{json.dumps(schema)}",
            "stream": False,
            "format": "json",
        }
        req = urllib_request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib_request.urlopen(req, timeout=60) as response:
                raw_payload = json.loads(response.read().decode("utf-8"))
        except urllib_error.URLError as exc:
            raise RuntimeError(f"ollama unavailable: {exc}") from exc
        raw_text = str(raw_payload.get("response") or "").strip()
        payload = json.loads(raw_text)
        return AgentRunResult(
            run_id=self._new_run_id(task_type),
            task_type=task_type,
            model_class=task_class,
            provider="ollama",
            model=model,
            reasoning_effort=reasoning,
            success=True,
            fallback_used=fallback_used,
            family_id=family_id,
            lineage_id=lineage_id,
            duration_ms=int((time.perf_counter() - start) * 1000),
            prompt_payload=prompt_payload,
            result_payload=payload,
            raw_text=raw_text,
            attempted_providers=list(attempted),
        )

    def _write_run_artifact(self, result: AgentRunResult) -> AgentRunResult:
        payload = {
            "generated_at": utc_now_iso(),
            "run_id": result.run_id,
            "task_type": result.task_type,
            "model_class": result.model_class,
            "provider": result.provider,
            "model": result.model,
            "reasoning_effort": result.reasoning_effort,
            "family_id": result.family_id,
            "lineage_id": result.lineage_id,
            "success": result.success,
            "fallback_used": result.fallback_used,
            "duration_ms": result.duration_ms,
            "prompt_payload": result.prompt_payload,
            "result_payload": result.result_payload,
            "error": result.error,
            "attempted_providers": list(result.attempted_providers),
            "raw_text": result.raw_text,
        }
        path = self.log_dir / f"{result.run_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        result.artifact_path = str(path)
        return result

    def _new_run_id(self, task_type: str) -> str:
        stamp = utc_now_iso().replace(":", "").replace("+", "_").replace(".", "_")
        return f"{task_type}_{stamp}"

    def _proposal_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are a research agent inside a paper-only trading strategy factory.\n"
            "Produce one bounded challenger hypothesis for the specified strategy family.\n"
            "Constraints:\n"
            "- Stay inside the provided mutation bounds.\n"
            "- Do not invent live trading, credentials, or uncapped risk changes.\n"
            "- Prefer sharp, testable changes over broad vague ideas.\n"
            "- Respect the desired_creation_kind field. Use mutation for local bounded variants and new_model for more novel but still bounded alternatives.\n"
            f"- Research tier for this task: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        )

    def _tweak_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are a bounded trading-research agent.\n"
            "Suggest the smallest credible parameter change set for an underperforming lineage.\n"
            "Constraints:\n"
            "- Only return parameter overrides that fit inside the supplied mutation bounds.\n"
            "- Prefer 1-4 surgical changes.\n"
            "- Do not change credentials, live flags, or hard caps.\n"
            f"- Research tier for this task: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        )

    def _critique_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are a deep-review agent inside a paper-only trading factory.\n"
            "Review the latest evaluation and propose the next tests without changing promotion policy.\n"
            "You must also choose one maintenance_action:\n"
            "- hold: keep running, no special maintenance\n"
            "- retrain: same model family, refresh incumbent artifacts or training state\n"
            "- rework: keep lineage alive but request bounded changes or debugging\n"
            "- replace: lineage is weak enough that fresh challengers should replace it\n"
            "- retire: evidence is bad enough that the lineage should exit active rotation\n"
            "Prefer retrain for stale or drifted models, rework for fixable execution/data issues, replace for structurally weak models, and retire only when confidence is high.\n"
            f"Research tier: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        )

    def _debug_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are a debugging agent inside a paper-only trading factory.\n"
            "Diagnose runtime or configuration bugs for a trading lineage.\n"
            "Focus on actionable root causes, especially whether the issue is code/data related or requires human/operator intervention.\n"
            "If the issue looks like credentials, API permissions, certificates, venue restriction, or account/jurisdiction setup, set requires_human=true and give a concrete human_action.\n"
            "Do not propose live trading changes.\n"
            f"Research tier: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        )


def apply_real_agent_proposal(
    *,
    result: AgentRunResult,
    family: FactoryFamily,
    proposal_index: int,
) -> ScientificAgentProposal:
    payload = dict(result.result_payload)
    parameter_overrides = {
        key: value
        for key, value in dict(payload.get("parameter_overrides") or {}).items()
        if value is not None
    }
    proposal_kind = str(payload.get("proposal_kind") or "mutation")
    scientific_domains = [str(item) for item in (payload.get("scientific_domains") or []) if str(item).strip()]
    title = build_proposal_title(
        family=family,
        proposal_kind=proposal_kind,
        proposal_index=proposal_index,
        scientific_domains=scientific_domains,
        model_class=str(parameter_overrides.get("selected_model_class") or ""),
        raw_title=str(payload.get("title") or f"{family.label} Agent Proposal {proposal_index}"),
        source_idea_title=None,
    )
    thesis = normalize_alpha_thesis(
        str(payload.get("thesis") or family.thesis),
        family=family,
        proposal_kind=proposal_kind,
    )
    return ScientificAgentProposal(
        proposal_id=f"{family.family_id}:proposal:{proposal_index}:{result.run_id}",
        family_id=family.family_id,
        title=title,
        thesis=thesis,
        scientific_domains=scientific_domains,
        lead_agent_role=str(payload.get("lead_agent_role") or "Research Agent"),
        collaborating_agent_roles=[
            str(item) for item in (payload.get("collaborating_agent_roles") or []) if str(item).strip()
        ],
        parameter_overrides=parameter_overrides,
        budget_bucket=str(payload.get("budget_bucket") or _budget_bucket_default(family)),
        proposal_kind=proposal_kind,
        source_idea_id=str(payload.get("source_idea_id") or "") or None,
        origin=f"real_agent_{result.provider}",
        agent_notes=list(payload.get("agent_notes") or [])
        + [
            f"provider={result.provider}",
            f"model={result.model}",
            f"task_type={result.task_type}",
            f"task_class={result.model_class}",
        ],
        agent_metadata={
            "run_id": result.run_id,
            "artifact_path": result.artifact_path,
            "provider": result.provider,
            "model": result.model,
            "task_type": result.task_type,
            "task_class": result.model_class,
            "reasoning_effort": result.reasoning_effort,
            "fallback_used": result.fallback_used,
        },
    )
