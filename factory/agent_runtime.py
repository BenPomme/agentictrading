from __future__ import annotations

import json
import logging
import os
import re
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
from factory.strategy_inventor import (
    ScientificAgentProposal,
    ScientificFamilyProposal,
    build_proposal_title,
    normalize_alpha_thesis,
    normalize_family_thesis,
)

logger = logging.getLogger(__name__)

TASK_CHEAP = "cheap_structured"
TASK_STANDARD = "standard_research"
TASK_HARD = "hard_research"
TASK_FRONTIER = "frontier_research"
TASK_DEEP = "deep_review"
TASK_LOCAL = "local"  # Pure computation, no LLM provider needed
OVERRIDE_KEYS = [
    "selected_horizon_seconds",
    "selected_feature_subset",
    "selected_model_class",
    "selected_min_edge",
    "selected_stake_fraction",
    "selected_learning_rate",
    "selected_lookback_hours",
]

MULTI_AGENT_TASK_PROFILES: Dict[str, Dict[str, Any]] = {
    "proposal_generation": {
        "strategy": "parallel_panel",
        "child_roles": [
            "alpha_hypothesis_proposer",
            "falsification_critic",
            "execution_microstructure_reviewer",
        ],
        "instruction": (
            "Use Codex child agents to split the work into three bounded passes: "
            "one proposer for the alpha hypothesis, one critic looking for overfit "
            "or broken assumptions, and one execution/microstructure reviewer to "
            "reject ideas that look untradeable in paper execution."
        ),
    },
    "post_eval_critique": {
        "strategy": "parallel_panel",
        "child_roles": [
            "performance_reviewer",
            "retrain_planner",
            "risk_assessor",
        ],
        "instruction": (
            "Use Codex child agents to separate performance diagnosis, retrain planning, "
            "and risk assessment before writing the final structured critique."
        ),
    },
    "runtime_debug_review": {
        "strategy": "parallel_panel",
        "child_roles": [
            "runtime_debugger",
            "data_pipeline_debugger",
            "operator_escalation_classifier",
        ],
        "instruction": (
            "Use Codex child agents to inspect runtime failure modes, data/training failure "
            "modes, and whether this needs explicit human intervention."
        ),
    },
    "maintenance_resolution_review": {
        "strategy": "parallel_panel",
        "child_roles": [
            "maintenance_triager",
            "replacement_planner",
            "execution_realism_reviewer",
        ],
        "instruction": (
            "Use Codex child agents to separate maintenance triage, replacement/retrain planning, "
            "and execution-realism review before choosing the best maintenance action."
        ),
    },
    "family_bootstrap_generation": {
        "strategy": "parallel_panel",
        "child_roles": [
            "family_thesis_proposer",
            "venue_connector_planner",
            "incubation_risk_critic",
        ],
        "instruction": (
            "Use Codex child agents to separate family-thesis invention, venue/connector planning, "
            "and incubation-risk critique before synthesizing one bounded new-family proposal."
        ),
    },
}


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


def _codex_multi_agent_enabled() -> bool:
    return bool(getattr(config, "FACTORY_AGENT_CODEX_MULTI_AGENT_ENABLED", True))


def _codex_multi_agent_tasks() -> List[str]:
    raw = str(
        getattr(
            config,
            "FACTORY_AGENT_CODEX_MULTI_AGENT_TASKS",
            "proposal_generation,post_eval_critique,runtime_debug_review,family_bootstrap_generation",
        )
        or ""
    ).strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _task_model(task_class: str) -> str:
    mapping = {
        TASK_CHEAP: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_CHEAP", "gpt-5.1-codex-mini")),
        TASK_STANDARD: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")),
        TASK_HARD: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_HARD", "gpt-5.2-codex")),
        TASK_FRONTIER: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_FRONTIER", "gpt-5.3-codex")),
        TASK_DEEP: str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_DEEP", "gpt-5.4")),
    }
    return mapping.get(task_class, str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_STANDARD", "gpt-5.1-codex")))


def _openai_api_model(task_class: str) -> str:
    mapping = {
        TASK_CHEAP: str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_CHEAP", "gpt-4.1-nano")),
        TASK_STANDARD: str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_STANDARD", "gpt-4.1-mini")),
        TASK_HARD: str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_HARD", "gpt-5-mini-2025-08-07")),
        TASK_FRONTIER: str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_FRONTIER", "gpt-5-mini-2025-08-07")),
        TASK_DEEP: str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_DEEP", "gpt-5.4")),
    }
    return mapping.get(task_class, str(getattr(config, "FACTORY_AGENT_OPENAI_MODEL_STANDARD", "gpt-4.1-mini")))


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
    return str(getattr(config, "FACTORY_AGENT_CODEX_MODEL_PROPOSAL", "gpt-5.2-codex") or "gpt-5.2-codex")


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


def _multi_agent_trace_schema() -> Dict[str, Any]:
    return {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["strategy", "roles", "synthesis"],
        "properties": {
            "strategy": {"type": "string"},
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["role", "finding"],
                    "properties": {
                        "role": {"type": "string"},
                        "finding": {"type": "string"},
                    },
                },
            },
            "synthesis": {"type": "string"},
        },
    }


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
    multi_agent_trace_schema = _multi_agent_trace_schema()
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
            "multi_agent_trace",
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
            "multi_agent_trace": multi_agent_trace_schema,
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
    multi_agent_trace_schema = _multi_agent_trace_schema()
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
            "multi_agent_trace",
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
            "multi_agent_trace": multi_agent_trace_schema,
        },
    }


def _debug_schema() -> Dict[str, Any]:
    multi_agent_trace_schema = _multi_agent_trace_schema()
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
            "multi_agent_trace",
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
            "multi_agent_trace": multi_agent_trace_schema,
        },
    }


def _maintenance_resolution_schema() -> Dict[str, Any]:
    multi_agent_trace_schema = _multi_agent_trace_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "maintenance_action",
            "maintenance_reason",
            "next_steps",
            "requires_new_challenger",
            "multi_agent_trace",
        ],
        "properties": {
            "summary": {"type": "string"},
            "maintenance_action": {
                "type": "string",
                "enum": ["hold", "retrain", "rework", "replace", "retire"],
            },
            "maintenance_reason": {"type": "string"},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "requires_new_challenger": {"type": "boolean"},
            "multi_agent_trace": multi_agent_trace_schema,
        },
    }


_MODEL_DESIGN_PROTOCOL_REF = """
class StrategyModel(Protocol):
    def name(self) -> str: ...
    def configure(self, genome: dict) -> None: ...
    def required_data(self) -> dict:
        # Return {"source": "yahoo"|"binance"|"betfair"|"polymarket"|"alpaca",
        #         "instruments": ["SPY", ...], "fields": ["ohlcv"]}
    def fit(self, df: pd.DataFrame) -> None: ...
    def predict(self, df: pd.DataFrame) -> pd.Series:
        # Returns +1 (long), 0 (flat), -1 (short) for each row
    def position_size(self, signal: int, equity: float) -> float:
        # Return notional size or fraction of equity
""".strip()

_MODEL_DESIGN_ALLOWED_IMPORTS = (
    "numpy, pandas, scipy, sklearn, hmmlearn, statsmodels, ta, math, "
    "statistics, collections, dataclasses, typing, logging, functools, "
    "itertools, json, datetime, enum, abc"
)

_MODEL_DESIGN_EXAMPLE = '''
import numpy as np
import pandas as pd

class MomentumMeanReversionModel:
    """Mean-reversion on z-score of rolling returns."""

    def name(self) -> str:
        return "momentum_mean_reversion"

    def configure(self, genome: dict) -> None:
        self._lookback = int(genome.get("lookback", 20))
        self._entry_z = float(genome.get("entry_z", 1.5))
        self._size_frac = float(genome.get("size_frac", 0.1))

    def required_data(self) -> dict:
        return {"source": "yahoo", "instruments": ["SPY", "QQQ"], "fields": ["ohlcv"]}

    def fit(self, df: pd.DataFrame) -> None:
        pass

    def predict(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"] if "Close" in df.columns else df["close"]
        returns = close.pct_change()
        roll_mean = returns.rolling(self._lookback).mean()
        roll_std = returns.rolling(self._lookback).std().replace(0, np.nan)
        z = (returns - roll_mean) / roll_std
        signals = pd.Series(0, index=df.index, dtype=int)
        signals[z < -self._entry_z] = 1
        signals[z > self._entry_z] = -1
        return signals

    def position_size(self, signal: int, equity: float) -> float:
        return equity * self._size_frac
'''.strip()

_MODEL_DATA_SOURCES = """
Available data sources and their schemas:
- yahoo: OHLCV (Open, High, Low, Close, Volume) parquet files for US stocks.
  Instruments: SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM, USO, XLE, GLD, TLT, etc.
- alpaca: Same OHLCV format as yahoo, US stocks and ETFs.
- binance: Funding rate CSV files with columns: fundingRate, fundingTime, markPrice, symbol.
  Crypto perpetual futures funding rates (BTCUSDT, ETHUSDT, etc.).
- betfair: JSONL event prediction files with market odds and outcomes.
- polymarket: Parquet price history files for prediction markets.
""".strip()


def _model_design_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["model_code", "class_name", "rationale", "data_requirements"],
        "properties": {
            "model_code": {"type": "string", "description": "Complete Python file implementing StrategyModel"},
            "class_name": {"type": "string", "description": "Name of the class in model_code"},
            "rationale": {"type": "string", "description": "Brief explanation of the model's approach"},
            "data_requirements": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["yahoo", "binance", "betfair", "polymarket", "alpaca"]},
                    "instruments": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source", "instruments", "fields"],
            },
        },
    }


def _model_mutate_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["model_code", "class_name", "mutation_description"],
        "properties": {
            "model_code": {"type": "string", "description": "Complete modified Python file"},
            "class_name": {"type": "string", "description": "Name of the class in model_code"},
            "mutation_description": {"type": "string", "description": "What was changed and why"},
        },
    }


def _fallback_family_venues(idea: Dict[str, Any]) -> List[str]:
    haystack = " ".join(
        [
            str(idea.get("title") or ""),
            str(idea.get("summary") or ""),
            " ".join(str(item) for item in (idea.get("tags") or [])),
        ]
    ).lower()
    venues: List[str] = []
    if "polymarket" in haystack:
        venues.append("polymarket")
    if "betfair" in haystack or "sports" in haystack:
        venues.append("betfair")
    if "binance" in haystack or not venues:
        venues.append("binance")
    unique: List[str] = []
    for venue in venues:
        if venue not in unique:
            unique.append(venue)
    return unique


def _fallback_family_connectors(target_venues: Sequence[str]) -> List[str]:
    connectors: List[str] = []
    for venue in target_venues:
        venue_text = str(venue).strip().lower()
        if venue_text == "polymarket":
            connectors.append("polymarket_core")
        elif venue_text == "betfair":
            connectors.append("betfair_core")
        elif venue_text == "binance":
            connectors.append("binance_core")
    return connectors or ["binance_core"]


def _family_proposal_schema() -> Dict[str, Any]:
    multi_agent_trace_schema = _multi_agent_trace_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "family_id",
            "label",
            "thesis",
            "explainer",
            "target_venues",
            "primary_connector_ids",
            "target_portfolios",
            "scientific_domains",
            "lead_agent_role",
            "collaborating_agent_roles",
            "source_idea_id",
            "incubation_notes",
            "multi_agent_trace",
        ],
        "properties": {
            "family_id": {"type": "string"},
            "label": {"type": "string"},
            "thesis": {"type": "string"},
            "explainer": {"type": "string"},
            "target_venues": {"type": "array", "items": {"type": "string"}},
            "primary_connector_ids": {"type": "array", "items": {"type": "string"}},
            "target_portfolios": {"type": "array", "items": {"type": "string"}},
            "scientific_domains": {"type": "array", "items": {"type": "string"}},
            "lead_agent_role": {"type": "string"},
            "collaborating_agent_roles": {"type": "array", "items": {"type": "string"}},
            "source_idea_id": {"type": ["string", "null"]},
            "incubation_notes": {"type": "array", "items": {"type": "string"}},
            "multi_agent_trace": multi_agent_trace_schema,
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
    multi_agent_requested: bool = False
    multi_agent_roles: List[str] = field(default_factory=list)

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
            "multi_agent_requested": self.multi_agent_requested,
            "multi_agent_roles": list(self.multi_agent_roles),
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


def _compact_agent_error(provider: str, details: str) -> str:
    text = str(details or "").strip()
    if not text:
        return f"{provider}:unknown_error"
    if "invalid_json_schema" in text:
        match = re.search(r'"message":\s*"([^"]+)"', text)
        message = match.group(1) if match else "invalid_json_schema"
        return f"{provider}:structured_output_error: {message}"
    if "The hubspot MCP server is not logged in" in text:
        return f"{provider}:mcp_auth_missing: hubspot"
    if "The meta-ads MCP server is not logged in" in text:
        return f"{provider}:mcp_auth_missing: meta-ads"
    env_match = re.search(r"Environment variable ([A-Z0-9_]+) .* is not set", text)
    if env_match:
        return f"{provider}:missing_env: {env_match.group(1)}"
    if "OpenAI Codex v" in text or "mcp startup:" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("ERROR:"):
                return f"{provider}:{line.removeprefix('ERROR:').strip()[:240]}"
            if "invalid_json_schema" in line or "failed:" in line or "not logged in" in line:
                return f"{provider}:{line[:240]}"
        return f"{provider}:codex_exec_failed"
    first_line = text.splitlines()[0].strip()
    return f"{provider}:{first_line[:240]}"


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
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="proposal_generation",
            task_class=task_class,
        )
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

    def generate_family_proposal(
        self,
        *,
        idea: Dict[str, Any],
        existing_family_ids: Sequence[str],
        cycle_count: int,
        proposal_index: int,
        research_portfolio_id: str,
        active_incubation_count: int = 0,
    ) -> AgentRunResult | None:
        if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
            return None
        task_class = TASK_DEEP
        prompt_payload = {
            "cycle_count": cycle_count,
            "proposal_index": proposal_index,
            "idea": dict(idea),
            "existing_family_ids": [str(item) for item in existing_family_ids if str(item).strip()],
            "active_incubation_count": int(active_incubation_count),
            "research_portfolio_id": str(research_portfolio_id),
            "idea_intake_excerpt": _idea_excerpt(self.project_root),
            "family_creation_policy": {
                "goal": "Create a genuinely new incubating family, not just another lineage inside an existing family.",
                "constraints": [
                    "Return one unique snake_case family_id not already present in existing_family_ids.",
                    "Keep this paper-only and research-first.",
                    "Target a plausible venue/connector set already represented in the idea or current factory context.",
                    "Write the thesis in the form 'We believe we can create alpha by ...'.",
                    "Prefer bounded, testable families that can incubate locally before runtime promotion.",
                ],
            },
        }
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="family_bootstrap_generation",
            task_class=task_class,
        )
        prompt = self._family_bootstrap_prompt(prompt_payload, task_class=task_class)
        return self._run_structured(
            task_type="family_bootstrap_generation",
            task_class=task_class,
            family_id="incubating_family",
            lineage_id=None,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_family_proposal_schema(),
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
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="underperformance_tweak",
            task_class=task_class,
        )
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
            "family": {
                "family_id": family.family_id,
                "label": family.label,
                "thesis": family.thesis,
                "scientific_domains": list((genome.scientific_domains if genome else []) or []),
            },
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
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="post_eval_critique",
            task_class=TASK_CHEAP,
        )
        prompt = self._critique_prompt(prompt_payload, task_class=TASK_CHEAP)
        return self._run_structured(
            task_type="post_eval_critique",
            task_class=TASK_CHEAP,
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
            "family": {
                "family_id": family.family_id,
                "label": family.label,
                "thesis": family.thesis,
                "scientific_domains": list((genome.scientific_domains if genome else []) or []),
            },
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
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="runtime_debug_review",
            task_class=task_class,
        )
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

    def resolve_maintenance_item(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        genome: StrategyGenome | None,
        latest_bundle: EvaluationBundle | None,
        learning_memory: Sequence[LearningMemoryEntry],
        execution_evidence: Dict[str, Any] | None,
        maintenance_request: Dict[str, Any],
        review_context: Dict[str, Any] | None = None,
    ) -> AgentRunResult | None:
        if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
            return None
        task_class = self._maintenance_task_class(lineage, execution_evidence, maintenance_request)
        prompt_payload = {
            "family": {
                "family_id": family.family_id,
                "label": family.label,
                "thesis": family.thesis,
                "scientific_domains": list((genome.scientific_domains if genome else []) or []),
            },
            "lineage": {
                "lineage_id": lineage.lineage_id,
                "role": lineage.role,
                "current_stage": lineage.current_stage,
                "iteration_status": lineage.iteration_status,
                "tweak_count": int(lineage.tweak_count or 0),
                "loss_streak": int(lineage.loss_streak or 0),
            },
            "genome_parameters": dict((genome.parameters if genome else {}) or {}),
            "latest_evaluation": dict((latest_bundle.to_dict() if latest_bundle else {}) or {}),
            "learning_memory": _truncate_memories(learning_memory, limit=6),
            "execution_evidence": _truncate_execution_evidence(execution_evidence),
            "maintenance_request": dict(maintenance_request or {}),
            "review_context": dict(review_context or {}),
        }
        prompt_payload["codex_multi_agent_plan"] = self._codex_multi_agent_plan(
            task_type="maintenance_resolution_review",
            task_class=task_class,
        )
        prompt = self._maintenance_prompt(prompt_payload, task_class=task_class)
        return self._run_structured(
            task_type="maintenance_resolution_review",
            task_class=task_class,
            family_id=family.family_id,
            lineage_id=lineage.lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_maintenance_resolution_schema(),
        )

    def design_model(
        self,
        *,
        idea: Dict[str, Any],
        family_id: str,
        target_venues: Sequence[str],
        thesis: str,
        cycle_count: int,
    ) -> AgentRunResult | None:
        """Ask a FRONTIER agent to write a complete model_code.py from an idea."""
        if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
            return None
        task_class = TASK_DEEP
        prompt_payload = {
            "cycle_count": cycle_count,
            "idea": dict(idea),
            "family_id": family_id,
            "target_venues": list(target_venues),
            "thesis": thesis,
            "protocol_reference": _MODEL_DESIGN_PROTOCOL_REF,
            "allowed_imports": _MODEL_DESIGN_ALLOWED_IMPORTS,
            "data_sources": _MODEL_DATA_SOURCES,
            "example_model": _MODEL_DESIGN_EXAMPLE,
        }
        prompt = (
            "You are a quantitative model designer inside a paper-only trading factory.\n"
            "Write a complete Python file implementing the StrategyModel protocol for the idea below.\n\n"
            "IDEA:\n"
            f"  Title: {idea.get('title', 'unknown')}\n"
            f"  Summary: {idea.get('summary', '')}\n"
            f"  Thesis: {thesis}\n"
            f"  Target venues: {', '.join(target_venues)}\n\n"
            "PROTOCOL (your class MUST implement ALL these methods):\n"
            f"{_MODEL_DESIGN_PROTOCOL_REF}\n\n"
            f"ALLOWED IMPORTS: {_MODEL_DESIGN_ALLOWED_IMPORTS}\n"
            "Do NOT import os, sys, subprocess, pathlib, requests, or any I/O library.\n\n"
            f"AVAILABLE DATA:\n{_MODEL_DATA_SOURCES}\n\n"
            "EXAMPLE MODEL (for reference style only, create something DIFFERENT):\n"
            f"{_MODEL_DESIGN_EXAMPLE}\n\n"
            "REQUIREMENTS:\n"
            "- The model must be genuinely novel, tailored to the idea.\n"
            "- configure() must accept a genome dict and use its params.\n"
            "- required_data() must return a valid source + instruments.\n"
            "- fit() must train on historical data.\n"
            "- predict() must return pd.Series of +1/0/-1.\n"
            "- position_size() must return a sensible notional amount.\n"
            "- The model MUST be different from a simple momentum/mean-reversion model.\n\n"
            "Return ONLY the structured JSON with model_code, class_name, rationale, data_requirements."
        )
        return self._run_structured(
            task_type="model_design",
            task_class=task_class,
            family_id=family_id,
            lineage_id=None,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_model_design_schema(),
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
    ) -> AgentRunResult | None:
        """Ask a CHEAP/STANDARD agent to make a small code edit to an existing model."""
        if not bool(getattr(config, "FACTORY_REAL_AGENTS_ENABLED", True)):
            return None
        task_class = TASK_STANDARD if tweak_count >= 2 else TASK_CHEAP
        prompt_payload = {
            "family_id": family_id,
            "lineage_id": lineage_id,
            "current_model_code": current_model_code,
            "class_name": class_name,
            "backtest_results": dict(backtest_results),
            "thesis": thesis,
            "tweak_count": tweak_count,
        }
        prompt = (
            "You are a model mutation agent inside a paper-only trading factory.\n"
            "Make a SMALL, targeted improvement to the model code below.\n\n"
            f"FAMILY: {family_id}\n"
            f"THESIS: {thesis}\n"
            f"TWEAK COUNT: {tweak_count}\n\n"
            "CURRENT BACKTEST RESULTS:\n"
            f"{json.dumps(backtest_results, indent=2, default=str)}\n\n"
            "CURRENT MODEL CODE:\n"
            f"```python\n{current_model_code}\n```\n\n"
            "REQUIREMENTS:\n"
            "- Make 1-3 targeted changes to improve performance.\n"
            "- Keep the same class name and protocol interface.\n"
            "- Ideas: adjust thresholds, add a feature, change position sizing, "
            "add a filter, modify the signal logic.\n"
            "- Do NOT rewrite from scratch unless tweak_count >= 2.\n"
            "- The model must still satisfy the StrategyModel protocol.\n"
            f"- Allowed imports: {_MODEL_DESIGN_ALLOWED_IMPORTS}\n\n"
            "Return the COMPLETE modified Python file as model_code, plus class_name and mutation_description."
        )
        return self._run_structured(
            task_type="model_mutate",
            task_class=task_class,
            family_id=family_id,
            lineage_id=lineage_id,
            prompt=prompt,
            prompt_payload=prompt_payload,
            schema=_model_mutate_schema(),
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
            return TASK_HARD
        if retired_count >= 4:
            return TASK_HARD
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
        model_quality_issues = {
            "untrainable_model", "training_stalled", "stalled_model",
        }
        if (
            int(lineage.tweak_count or 0) >= 2
            or (contradictory_metrics and str((execution_evidence or {}).get("health_status") or "") == "critical")
            or issue_codes.intersection(model_quality_issues)
        ):
            return TASK_HARD
        if (
            int(lineage.tweak_count or 0) >= 1
            or contradictory_metrics
            or issue_codes.intersection({"negative_paper_roi", "poor_win_rate", "no_trade_syndrome", "zero_simulated_fills", "trade_stalled"})
        ):
            return TASK_STANDARD
        return TASK_CHEAP

    def _debug_task_class(
        self,
        lineage: LineageRecord,
        execution_evidence: Dict[str, Any] | None,
    ) -> str:
        issue_codes = {str(item) for item in ((execution_evidence or {}).get("issue_codes") or [])}
        critical_bug_codes = {"runtime_error", "heartbeat_stale", "untrainable_model", "training_stalled", "stalled_model"}
        is_critical = str((execution_evidence or {}).get("health_status") or "") == "critical"
        repeated_debug = bool(lineage.last_debug_issue_signature)
        if is_critical and (repeated_debug or issue_codes.intersection(critical_bug_codes)):
            return TASK_HARD
        if is_critical or repeated_debug:
            return TASK_STANDARD
        return TASK_CHEAP

    def _maintenance_task_class(
        self,
        lineage: LineageRecord,
        execution_evidence: Dict[str, Any] | None,
        maintenance_request: Dict[str, Any] | None,
    ) -> str:
        action = str((maintenance_request or {}).get("action") or "").strip().lower()
        iteration_status = str(lineage.iteration_status or "").strip().lower()
        if (
            action in {"replace", "retire"}
            or iteration_status in {"review_requested_replace", "review_recommended_retire"}
        ):
            return TASK_HARD
        return TASK_STANDARD

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

    def _codex_multi_agent_plan(self, *, task_type: str, task_class: str) -> Dict[str, Any]:
        enabled = _codex_multi_agent_enabled() and task_type in set(_codex_multi_agent_tasks())
        profile = dict(MULTI_AGENT_TASK_PROFILES.get(task_type) or {})
        if not enabled:
            return {
                "enabled": False,
                "strategy": "single_agent",
                "task_class": task_class,
                "child_roles": [],
                "instruction": "",
            }
        return {
            "enabled": True,
            "strategy": str(profile.get("strategy") or "parallel_panel"),
            "task_class": task_class,
            "child_roles": [str(item) for item in (profile.get("child_roles") or []) if str(item).strip()],
            "instruction": str(profile.get("instruction") or "").strip(),
        }

    def _codex_multi_agent_prompt_suffix(self, prompt_payload: Dict[str, Any]) -> str:
        plan = dict(prompt_payload.get("codex_multi_agent_plan") or {})
        if not bool(plan.get("enabled")):
            return ""
        roles = [str(item) for item in (plan.get("child_roles") or []) if str(item).strip()]
        role_text = ", ".join(roles) if roles else "specialized child agents"
        instruction = str(plan.get("instruction") or "").strip()
        return (
            "\n\nCodex multi-agent execution:\n"
            f"- {instruction}\n"
            f"- Use these child-agent roles if helpful: {role_text}.\n"
            "- Synthesize the child-agent conclusions into one final structured answer.\n"
            "- If the schema allows `multi_agent_trace`, include one concise finding per child role plus a short synthesis.\n"
            "- Return only the requested final object, not free-form child-agent transcripts.\n"
        )

    def _apply_cost_guard(
        self,
        task_type: str,
        task_class: str,
        model_override: str | None,
        reasoning_override: str | None,
    ) -> tuple:
        """Auto-downgrade TASK_FRONTIER/TASK_DEEP to TASK_HARD when expensive-model
        usage exceeds the configured budget cap (default 10% of recent runs).
        Family-bootstrap tasks are exempt -- they need frontier quality."""
        expensive_tiers = {TASK_FRONTIER, TASK_DEEP}
        if task_class not in expensive_tiers:
            return task_class, model_override, reasoning_override
        if task_type in {"family_bootstrap_generation", "model_design"}:
            return task_class, model_override, reasoning_override

        cap_pct = float(getattr(config, "FACTORY_AGENT_EXPENSIVE_CAP_PCT", 10))
        window = int(getattr(config, "FACTORY_AGENT_COST_WINDOW", 50))

        runs = recent_agent_runs(self.project_root, limit=window)
        if len(runs) < 5:
            return task_class, model_override, reasoning_override

        expensive_count = sum(
            1 for r in runs if r.get("model_class") in expensive_tiers
        )
        current_pct = (expensive_count / len(runs)) * 100

        if current_pct >= cap_pct:
            logger.warning(
                "Cost guard: expensive tier at %.1f%% (cap %.1f%%), "
                "downgrading %s/%s from %s to %s",
                current_pct, cap_pct, task_type, task_class,
                task_class, TASK_HARD,
            )
            return (
                TASK_HARD,
                None,
                None,
            )

        return task_class, model_override, reasoning_override

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
        if task_class == TASK_LOCAL:
            logger.info("TASK_LOCAL: %s/%s bypasses agent runtime (pure computation)", task_type, task_class)
            result = AgentRunResult(
                run_id=self._new_run_id(task_type),
                task_type=task_type,
                model_class=TASK_LOCAL,
                provider="local",
                model="none",
                reasoning_effort="none",
                success=True,
                fallback_used=False,
                family_id=family_id,
                lineage_id=lineage_id,
                duration_ms=0,
                prompt_payload=prompt_payload,
                result_payload={"output": "Task executed locally without LLM"},
                raw_text="Task executed locally without LLM",
                attempted_providers=["local"],
            )
            return self._write_run_artifact(result)
        task_class, model_override, reasoning_override = self._apply_cost_guard(
            task_type, task_class, model_override, reasoning_override,
        )
        errors: List[str] = []
        attempted: List[str] = []
        providers = _provider_order()
        logger.info("Agent run [%s/%s]: provider order=%s", task_type, task_class, providers)
        for provider in providers:
            if provider == "ollama" and (not _ollama_fallback_enabled() or task_class != TASK_CHEAP):
                continue
            attempted.append(provider)
            logger.info("Agent run [%s]: trying provider '%s'", task_type, provider)
            if provider == "deterministic":
                multi_agent_plan = dict(prompt_payload.get("codex_multi_agent_plan") or {})
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
                    multi_agent_requested=bool(multi_agent_plan.get("enabled")),
                    multi_agent_roles=list(multi_agent_plan.get("child_roles") or []),
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
                if provider == "openai_api":
                    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or str(getattr(config, "FACTORY_AGENT_OPENAI_API_KEY", "") or "").strip()
                    if not api_key:
                        logger.warning("Agent run [%s]: openai_api skipped — OPENAI_API_KEY not configured", task_type)
                        errors.append("openai_api:no_api_key")
                        continue
                    logger.info("Agent run [%s]: openai_api key present (len=%d), proceeding", task_type, len(api_key))
                    # Don't carry codex-specific model names to the OpenAI API
                    api_model_override = model_override
                    if api_model_override and "codex" in api_model_override.lower():
                        api_model_override = None
                    result = self._run_openai_api(
                        task_type=task_type,
                        task_class=task_class,
                        family_id=family_id,
                        lineage_id=lineage_id,
                        prompt=prompt,
                        prompt_payload=prompt_payload,
                        schema=schema,
                        model_override=api_model_override,
                        reasoning_override=reasoning_override,
                        fallback_used=bool(errors),
                        attempted=list(attempted),
                        api_key=api_key,
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
                err_msg = _compact_agent_error(provider, str(exc))
                logger.warning("Agent run [%s]: provider '%s' failed: %s", task_type, provider, err_msg)
                errors.append(err_msg)
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
            multi_agent_requested=bool((prompt_payload.get("codex_multi_agent_plan") or {}).get("enabled")),
            multi_agent_roles=list((prompt_payload.get("codex_multi_agent_plan") or {}).get("child_roles") or []),
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
        multi_agent_plan = dict(prompt_payload.get("codex_multi_agent_plan") or {})
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
        if bool(multi_agent_plan.get("enabled")):
            cmd[5:5] = ["--enable", "multi_agent"]
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
                multi_agent_requested=bool(multi_agent_plan.get("enabled")),
                multi_agent_roles=list(multi_agent_plan.get("child_roles") or []),
            )
        finally:
            Path(schema_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)

    def _run_openai_api(
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
        api_key: str,
    ) -> AgentRunResult:
        model = model_override or _openai_api_model(task_class)
        reasoning = reasoning_override or _task_reasoning(task_class)
        start = time.perf_counter()
        try:
            import urllib.request

            is_reasoning_model = model.startswith("o1") or model.startswith("o3") or model.startswith("o4")
            no_temperature = is_reasoning_model or model.startswith("gpt-5")

            messages = [
                {"role": "system" if not is_reasoning_model else "user", "content": (
                    "You are a quantitative trading research agent. "
                    "Respond ONLY with valid JSON matching the provided schema. No markdown, no commentary."
                )},
            ]
            messages.append({"role": "user", "content": (
                f"Task:\n{prompt}\n\n"
                f"Required JSON output schema:\n{json.dumps(schema, indent=2)}\n\n"
                "Respond with ONLY the JSON object."
            )})

            body: Dict[str, Any] = {"model": model, "messages": messages}
            if not no_temperature:
                body["temperature"] = 0.2
            if not is_reasoning_model:
                body["response_format"] = {"type": "json_object"}

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            raw_content = resp_data["choices"][0]["message"]["content"].strip()
            if raw_content.startswith("```"):
                lines = raw_content.split("\n")
                lines = lines[1:] if lines[0].startswith("```") else lines
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()

            payload = json.loads(raw_content)
            return AgentRunResult(
                run_id=self._new_run_id(task_type),
                task_type=task_type,
                model_class=task_class,
                provider="openai_api",
                model=model,
                reasoning_effort=reasoning,
                success=True,
                fallback_used=fallback_used,
                family_id=family_id,
                lineage_id=lineage_id,
                duration_ms=int((time.perf_counter() - start) * 1000),
                prompt_payload=prompt_payload,
                result_payload=payload,
                raw_text=raw_content,
                attempted_providers=list(attempted),
                multi_agent_requested=False,
                multi_agent_roles=[],
            )
        except Exception as exc:
            raise RuntimeError(f"openai_api:{model}:{exc}") from exc

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
            multi_agent_requested=False,
            multi_agent_roles=[],
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
            "multi_agent_requested": result.multi_agent_requested,
            "multi_agent_roles": list(result.multi_agent_roles),
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
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)

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
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)

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
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)

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
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)

    def _maintenance_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are a maintenance-resolution agent inside a paper-only trading factory.\n"
            "Review the active maintenance pressure on this lineage and choose the strongest credible maintenance action.\n"
            "You must choose exactly one maintenance_action:\n"
            "- hold: current maintenance pressure is not yet strong enough to act\n"
            "- retrain: keep the lineage, refresh the incumbent model/training state\n"
            "- rework: keep the lineage but require bounded fixes or algorithm changes\n"
            "- replace: lineage is weak enough that fresher challengers should take over\n"
            "- retire: evidence is bad enough that the lineage should leave active rotation\n"
            "Prefer realism over optimism. Account for execution quality, fee/slippage realism, trainability, and whether the current evidence is actually trustworthy.\n"
            f"Research tier: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)

    def _family_bootstrap_prompt(self, prompt_payload: Dict[str, Any], *, task_class: str) -> str:
        return (
            "You are an incubation agent inside a paper-only trading strategy factory.\n"
            "Create one genuinely new strategy family from the supplied idea.\n"
            "Constraints:\n"
            "- This must be a new family, not just a challenger mutation inside an existing family.\n"
            "- The family_id must be unique versus existing_family_ids and must be snake_case.\n"
            "- Keep it bounded, local-first, and incubatable before any runtime promotion.\n"
            "- Do not invent live trading permissions, credentials, or unsupported venues.\n"
            "- Thesis must begin with 'We believe we can create alpha by ...'.\n"
            f"- Research tier: {task_class}.\n\n"
            "Context JSON:\n"
            f"{json.dumps(prompt_payload, indent=2, default=str)}\n\n"
            "Return only the structured object requested by the schema."
        ) + self._codex_multi_agent_prompt_suffix(prompt_payload)


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
            f"multi_agent_requested={str(bool(result.multi_agent_requested)).lower()}",
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
            "multi_agent_requested": result.multi_agent_requested,
            "multi_agent_roles": list(result.multi_agent_roles),
        },
    )


def apply_real_family_proposal(
    *,
    result: AgentRunResult,
    idea: Dict[str, Any],
    existing_family_ids: Sequence[str],
    cycle_count: int,
    proposal_index: int,
    research_portfolio_id: str,
) -> ScientificFamilyProposal:
    payload = dict(result.result_payload)
    requested_family_id = str(payload.get("family_id") or "").strip().lower().replace("-", "_")
    clean_family_id = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in requested_family_id).strip("_")
    if not clean_family_id:
        clean_family_id = f"incubated_family_{cycle_count}_{proposal_index}"
    family_id = clean_family_id
    existing = {str(item).strip() for item in existing_family_ids if str(item).strip()}
    suffix = 2
    while family_id in existing:
        family_id = f"{clean_family_id}_{suffix}"
        suffix += 1
    idea_id = str(payload.get("source_idea_id") or idea.get("idea_id") or "").strip() or None
    target_venues = [str(item) for item in (payload.get("target_venues") or []) if str(item).strip()]
    if not target_venues:
        target_venues = _fallback_family_venues(idea)
    primary_connector_ids = [str(item) for item in (payload.get("primary_connector_ids") or []) if str(item).strip()]
    if not primary_connector_ids:
        primary_connector_ids = _fallback_family_connectors(target_venues)
    target_portfolios = [str(item) for item in (payload.get("target_portfolios") or []) if str(item).strip()]
    if not target_portfolios:
        target_portfolios = [str(research_portfolio_id)]
    scientific_domains = [str(item) for item in (payload.get("scientific_domains") or []) if str(item).strip()]
    incubation_notes = [str(item) for item in (payload.get("incubation_notes") or []) if str(item).strip()]
    incubation_notes.extend(
        [
            f"provider={result.provider}",
            f"model={result.model}",
            f"task_type={result.task_type}",
            f"task_class={result.model_class}",
            f"multi_agent_requested={str(bool(result.multi_agent_requested)).lower()}",
        ]
    )
    return ScientificFamilyProposal(
        proposal_id=f"{family_id}:family_proposal:{cycle_count}:{proposal_index}:{result.run_id}",
        family_id=family_id,
        label=str(payload.get("label") or str(idea.get("title") or family_id).strip()),
        thesis=normalize_family_thesis(str(payload.get("thesis") or str(idea.get("summary") or ""))),
        explainer=str(payload.get("explainer") or f"Incubating family proposed from idea {idea_id or 'unknown'}."),
        target_venues=target_venues,
        primary_connector_ids=primary_connector_ids,
        target_portfolios=target_portfolios,
        scientific_domains=scientific_domains,
        lead_agent_role=str(payload.get("lead_agent_role") or "Family Incubator"),
        collaborating_agent_roles=[
            str(item) for item in (payload.get("collaborating_agent_roles") or []) if str(item).strip()
        ],
        source_idea_id=idea_id,
        origin=f"real_agent_{result.provider}",
        incubation_notes=incubation_notes,
    )
