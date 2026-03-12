from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import config
from factory.agent_runtime import AgentRunResult, RealResearchAgentRuntime, apply_real_agent_proposal
from factory.connectors import default_connector_catalog
from factory.contracts import (
    EvaluationBundle,
    EvaluationStage,
    ExperimentQueueEntry,
    EvaluationWindow,
    ExecutionTier,
    ExperimentSpec,
    FactoryFamily,
    FactoryJournal,
    LearningMemoryEntry,
    LineageRecord,
    LineageRole,
    MutationBounds,
    PromotionStage,
    ResearchHypothesis,
    StrategyGenome,
    utc_now_iso,
)
from factory.execution_evidence import summarize_execution_targets
from factory.evaluation import assign_pareto_ranks, compute_hard_vetoes
from factory.experiment_runner import FactoryExperimentRunner
from factory.experiment_sources import family_model_rankings, portfolio_scorecards
from factory.goldfish_bridge import GoldfishBridge
from factory.idea_intake import annotate_idea_statuses, parse_ideas_markdown, relevant_ideas_for_family
from factory.idea_scout import maybe_run_idea_scout
from factory.promotion import PromotionController
from factory.registry import FactoryRegistry
from factory.runtime_mode import current_agentic_factory_runtime_mode
from factory.state_store import PortfolioStateStore
from factory.strategy_inventor import ScientificAgentProposal, ScientificStrategyInventor


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _budget_split() -> Dict[str, float]:
    return {"incumbent": 70.0, "adjacent": 20.0, "moonshot": 10.0}


def _factory_roles() -> Dict[str, List[str]]:
    return {
        ExecutionTier.TIER0.value: [
            "Director",
            "Budget Allocator",
            "Venue/Data Curator",
            "Genome Mutator",
            "Evaluator",
            "Risk Governor",
            "Promotion Arbiter",
            "Goldfish Bridge",
        ],
        ExecutionTier.TIER1.value: [
            "hypothesis_author",
            "feature_ideator",
            "test_scaffold",
            "doc_scribe",
        ],
        ExecutionTier.TIER2.value: [
            "pipeline_assembler",
            "genome_mutation_runner",
            "evaluation_integrator",
        ],
        ExecutionTier.TIER3.value: [
            "capital_risk_reviewer",
            "promotion_policy_reviewer",
            "execution_path_reviewer",
        ],
    }


def _scientific_researchers() -> List[str]:
    return [
        "econometrics",
        "microstructure",
        "bayesian_causal",
        "statistical_physics",
        "network_epidemiology",
        "ecology_evolution",
        "information_theory",
        "control_rl",
        "game_theory_behavioral",
        "signal_processing_neuroscience",
    ]


def _format_recent_action(message: str) -> str:
    return f"[{utc_now_iso()}] {str(message).strip()}"


def _append_recent_action(recent_actions: List[str], message: str) -> None:
    recent_actions.append(_format_recent_action(message))


class FactoryOrchestrator:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
        goldfish_root = Path(getattr(config, "FACTORY_GOLDFISH_ROOT", "research/goldfish"))
        if not factory_root.is_absolute():
            factory_root = self.project_root / factory_root
        if not goldfish_root.is_absolute():
            goldfish_root = self.project_root / goldfish_root
        self.registry = FactoryRegistry(factory_root)
        self.bridge = GoldfishBridge(goldfish_root)
        self.experiment_runner = FactoryExperimentRunner(self.project_root)
        self.strategy_inventor = ScientificStrategyInventor()
        self.agent_runtime = RealResearchAgentRuntime(self.project_root)
        self.promotion = PromotionController()
        self.connectors = default_connector_catalog(self.project_root)
        self._events: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._cycle_count = 0
        self._last_state: Dict[str, Any] = {}
        self.bootstrap()

    def _runtime_mode(self):
        return current_agentic_factory_runtime_mode()

    def _with_runtime_mode(self, state: Dict[str, Any], *, pause_reason: str | None = None) -> Dict[str, Any]:
        runtime_mode = self._runtime_mode()
        state.update(runtime_mode.to_dict())
        if pause_reason:
            state["pause_reason"] = pause_reason
        return state

    def _latest_manifest_by_lineage(self) -> Dict[str, Any]:
        latest: Dict[str, Any] = {}
        for manifest in self.registry.manifests():
            previous = latest.get(manifest.lineage_id)
            if previous is None or str(manifest.created_at) > str(previous.created_at):
                latest[manifest.lineage_id] = manifest
        return latest

    def _hard_stop_state(self) -> Dict[str, Any]:
        state = dict(self.registry.read_state() or {})
        state.setdefault("portfolio_id", getattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory"))
        state.setdefault("mode", "research")
        state.setdefault(
            "explainer",
            "Research-only control plane for multi-family strategy discovery, evaluation, and approval-gated promotion.",
        )
        state["running"] = False
        state["status"] = "paused"
        readiness = dict(state.get("readiness") or {})
        checks = list(readiness.get("checks") or [])
        if not any(item.get("name") == "agentic_factory_runtime_mode" for item in checks):
            checks.append(
                {
                    "name": "agentic_factory_runtime_mode",
                    "ok": False,
                    "reason": "Runtime mode is hard_stop, so factory orchestration and runner influence are paused intentionally.",
                }
            )
        readiness["status"] = "research_only"
        readiness["blockers"] = list(dict.fromkeys(list(readiness.get("blockers") or []) + ["agentic_factory_hard_stopped"]))
        readiness["checks"] = checks
        readiness["eta_to_readiness"] = "hard_stop"
        readiness["score_pct"] = round(
            (sum(1 for item in checks if item.get("ok")) / len(checks)) * 100.0,
            2,
        ) if checks else 0.0
        state["readiness"] = readiness
        research_summary = dict(state.get("research_summary") or {})
        research_summary["hard_stop_active"] = True
        state["research_summary"] = research_summary
        self._last_state = self._with_runtime_mode(
            state,
            pause_reason="agentic_factory_hard_stopped",
        )
        return dict(self._last_state)

    def bootstrap(self) -> None:
        if self.registry.families():
            return
        family_specs = [
            {
                "family_id": "binance_funding_contrarian",
                "label": "Binance Funding Contrarian",
                "thesis": "Exploit funding extremes, regime shifts, and cross-science features to improve directional funding trades.",
                "target_portfolios": ["hedge_validation", "hedge_research", "contrarian_legacy"],
                "target_venues": ["binance"],
                "connectors": ["binance_core"],
                "budget_bucket": "incumbent",
                "budget_weight_pct": 14.0,
                "role": LineageRole.CHAMPION.value,
                "explainer": "Uses funding, basis, open interest, and regime features to rank contrarian directional setups.",
            },
            {
                "family_id": "binance_cascade_regime",
                "label": "Binance Cascade/Regime",
                "thesis": "Detect fragile market states and liquidation cascades early enough to survive and exploit dislocations.",
                "target_portfolios": ["cascade_alpha"],
                "target_venues": ["binance"],
                "connectors": ["binance_core"],
                "budget_bucket": "adjacent",
                "budget_weight_pct": 18.0,
                "role": LineageRole.CHAMPION.value,
                "explainer": "Uses liquidation, depth collapse, and regime features for short-horizon cascade alpha.",
            },
            {
                "family_id": "betfair_prediction_value_league",
                "label": "Betfair Prediction/Value League",
                "thesis": "Evolve parallel probability models and policy gates until the best value-betting league earns execution rights.",
                "target_portfolios": ["betfair_core"],
                "target_venues": ["betfair"],
                "connectors": ["betfair_core"],
                "budget_bucket": "incumbent",
                "budget_weight_pct": 16.0,
                "role": LineageRole.CHAMPION.value,
                "explainer": "Compares prediction, calibration, and policy-gated value-betting lineages on the same snapshots.",
            },
            {
                "family_id": "betfair_information_lag",
                "label": "Betfair Information-Lag Books",
                "thesis": "Cross external event signals, time-zone maintenance patterns, and book synchronization delays to find stale pricing.",
                "target_portfolios": ["betfair_execution_book", "betfair_suspension_lag", "betfair_crossbook_consensus", "betfair_timezone_decay"],
                "target_venues": ["betfair", "polymarket"],
                "connectors": ["betfair_core", "polymarket_core"],
                "budget_bucket": "moonshot",
                "budget_weight_pct": 22.0,
                "role": LineageRole.CHAMPION.value,
                "explainer": "Tracks related-market lag and cross-book stale pricing patterns before they are trusted for execution.",
            },
            {
                "family_id": "polymarket_cross_venue",
                "label": "Polymarket Cross-Venue Signals",
                "thesis": "Use Polymarket microstructure and cross-venue event matching to produce robust paper-only signal layers.",
                "target_portfolios": ["polymarket_quantum_fold", "polymarket_binary_research"],
                "target_venues": ["polymarket", "betfair"],
                "connectors": ["polymarket_core", "betfair_core"],
                "budget_bucket": "adjacent",
                "budget_weight_pct": 30.0,
                "role": LineageRole.CHAMPION.value,
                "explainer": "Runs signal leagues on Polymarket quotes and cross-venue confirmations to rank paper-only opportunities.",
            },
        ]
        for spec in family_specs:
            family_id = spec["family_id"]
            hypothesis_id = f"{family_id}:hypothesis"
            lineage_id = f"{family_id}:champion"
            genome_id = f"{family_id}:genome:champion"
            experiment_id = f"{family_id}:experiment:champion"
            hypothesis = ResearchHypothesis(
                hypothesis_id=hypothesis_id,
                family_id=family_id,
                title=spec["label"],
                thesis=spec["thesis"],
                scientific_domains=_scientific_researchers()[:4],
                lead_agent_role="Director",
                success_metric="paper_monthly_roi_pct",
                guardrails=[
                    "No live promotion without human approval.",
                    "Mutation bounds may not touch credentials or hard risk caps.",
                    "Paper-first and net-of-costs only.",
                ],
                origin="seeded_family",
                agent_notes=["Initial seeded champion for family bootstrap."],
            )
            genome = StrategyGenome(
                genome_id=genome_id,
                lineage_id=lineage_id,
                family_id=family_id,
                parent_genome_id=None,
                role=spec["role"],
                parameters={
                    "resource_profile": "local-first-hybrid",
                    "budget_mix": _budget_split(),
                    "max_shadow_challengers": 5,
                    "max_paper_challengers": 2,
                },
                mutation_bounds=MutationBounds(
                    horizons_seconds=[120, 600, 1800, 14400],
                    feature_subsets=["baseline", "microstructure", "cross_science", "regime"],
                    model_classes=["logit", "gbdt", "tft", "transformer", "rules"],
                    execution_thresholds={"min_edge": [0.01, 0.10], "stake_fraction": [0.01, 0.10]},
                    hyperparameter_ranges={"learning_rate": [0.001, 0.1], "lookback_hours": [6, 168]},
                ),
                scientific_domains=_scientific_researchers(),
                budget_bucket=spec["budget_bucket"],
                resource_profile="local-first-hybrid",
                budget_weight_pct=spec["budget_weight_pct"],
            )
            experiment = ExperimentSpec(
                experiment_id=experiment_id,
                lineage_id=lineage_id,
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                genome_id=genome_id,
                goldfish_workspace=str(self.bridge.workspace_path(family_id)),
                pipeline_stages=["dataset", "features", "train", "walkforward", "stress", "package"],
                backend_mode="goldfish_sidecar",
                resource_profile="local-first-hybrid",
            )
            lineage = LineageRecord(
                lineage_id=lineage_id,
                family_id=family_id,
                label=f"{spec['label']} Champion",
                role=spec["role"],
                current_stage=PromotionStage.IDEA.value,
                target_portfolios=list(spec["target_portfolios"]),
                target_venues=list(spec["target_venues"]),
                hypothesis_id=hypothesis_id,
                genome_id=genome_id,
                experiment_id=experiment_id,
                budget_bucket=spec["budget_bucket"],
                budget_weight_pct=spec["budget_weight_pct"],
                connector_ids=list(spec["connectors"]),
                goldfish_workspace=str(self.bridge.workspace_path(family_id)),
                iteration_status="seeded_champion",
            )
            family = FactoryFamily(
                family_id=family_id,
                label=spec["label"],
                thesis=spec["thesis"],
                target_portfolios=list(spec["target_portfolios"]),
                target_venues=list(spec["target_venues"]),
                primary_connector_ids=list(spec["connectors"]),
                champion_lineage_id=lineage_id,
                shadow_challenger_ids=[],
                paper_challenger_ids=[],
                budget_split=_budget_split(),
                queue_stage=PromotionStage.IDEA.value,
                explainer=spec["explainer"],
            )
            self.registry.save_family(family)
            self.registry.save_research_pack(
                hypothesis=hypothesis,
                genome=genome,
                experiment=experiment,
                lineage=lineage,
            )
        self.registry.write_journal(
            FactoryJournal(
                active_goal="Build a reproducible strategy factory with shadow, paper, and approval-gated live promotion.",
                recent_actions=["[bootstrap] Seeded default factory families and champion lineages."],
            )
        )

    def _lineages_by_family(self) -> Dict[str, List[LineageRecord]]:
        grouped: Dict[str, List[LineageRecord]] = defaultdict(list)
        for lineage in self.registry.lineages():
            grouped[lineage.family_id].append(lineage)
        return grouped

    def _mutation_choice(self, options: List[Any], mutation_index: int, offset: int = 0) -> Any:
        if not options:
            return None
        return options[(mutation_index + offset) % len(options)]

    def _mutation_range_value(self, bounds: List[float], mutation_index: int, *, divisor: int = 5) -> float:
        if not bounds:
            return 0.0
        if len(bounds) == 1:
            return float(bounds[0])
        low = float(bounds[0])
        high = float(bounds[-1])
        slots = max(1, divisor - 1)
        position = (mutation_index % divisor) / slots
        return round(low + ((high - low) * position), 6)

    def _preferred_horizon(self, family_id: str) -> int:
        mapping = {
            "binance_funding_contrarian": 600,
            "binance_cascade_regime": 120,
            "betfair_prediction_value_league": 1800,
            "betfair_information_lag": 600,
            "polymarket_cross_venue": 600,
        }
        return mapping.get(family_id, 600)

    def _family_budget_weight(self, family_id: str) -> float:
        family = self.registry.load_family(family_id)
        if family is None:
            return 5.0
        champion = self.registry.load_lineage(family.champion_lineage_id)
        if champion is None:
            return 5.0
        return float(champion.budget_weight_pct or 5.0)

    def _active_lineages(self, family_id: str) -> List[LineageRecord]:
        return [
            lineage
            for lineage in self.registry.lineages()
            if lineage.family_id == family_id and lineage.active
        ]

    def _nearest_choice(self, options: List[int], value: int) -> int:
        if not options:
            return int(value)
        normalized = [int(option) for option in options]
        return min(normalized, key=lambda option: abs(option - int(value)))

    def _allowed_choice(self, options: List[str], value: str, *, fallback: str) -> str:
        normalized = [str(option) for option in options]
        if str(value) in normalized:
            return str(value)
        if fallback in normalized:
            return fallback
        return normalized[0] if normalized else fallback

    def _rotate_choice(self, options: List[str], current: str, *, step: int = 1, fallback: str) -> str:
        normalized = [str(option) for option in options]
        if not normalized:
            return fallback
        if current not in normalized:
            return normalized[0]
        index = normalized.index(current)
        return normalized[(index + step) % len(normalized)]

    def _clip_bounds(self, bounds: List[float], value: float, *, fallback: float) -> float:
        if not bounds:
            return round(float(value if value else fallback), 6)
        low = float(bounds[0])
        high = float(bounds[-1])
        return round(min(max(float(value), low), high), 6)

    def _sanitize_parameter_overrides(self, genome: StrategyGenome, overrides: Dict[str, Any]) -> Dict[str, Any]:
        bounds = genome.mutation_bounds
        clean = dict(overrides or {})
        if "selected_horizon_seconds" in clean:
            clean["selected_horizon_seconds"] = self._nearest_choice(
                bounds.horizons_seconds,
                int(clean["selected_horizon_seconds"]),
            )
        if "selected_feature_subset" in clean:
            clean["selected_feature_subset"] = self._allowed_choice(
                bounds.feature_subsets,
                str(clean["selected_feature_subset"]),
                fallback=str(genome.parameters.get("selected_feature_subset", "baseline") or "baseline"),
            )
        if "selected_model_class" in clean:
            clean["selected_model_class"] = self._allowed_choice(
                bounds.model_classes,
                str(clean["selected_model_class"]),
                fallback=str(genome.parameters.get("selected_model_class", "logit") or "logit"),
            )
        if "selected_min_edge" in clean:
            clean["selected_min_edge"] = self._clip_bounds(
                bounds.execution_thresholds.get("min_edge") or [0.01, 0.1],
                float(clean["selected_min_edge"]),
                fallback=float(genome.parameters.get("selected_min_edge", 0.03) or 0.03),
            )
        if "selected_stake_fraction" in clean:
            clean["selected_stake_fraction"] = self._clip_bounds(
                bounds.execution_thresholds.get("stake_fraction") or [0.01, 0.1],
                float(clean["selected_stake_fraction"]),
                fallback=float(genome.parameters.get("selected_stake_fraction", 0.03) or 0.03),
            )
        if "selected_learning_rate" in clean:
            clean["selected_learning_rate"] = self._clip_bounds(
                bounds.hyperparameter_ranges.get("learning_rate") or [0.001, 0.1],
                float(clean["selected_learning_rate"]),
                fallback=float(genome.parameters.get("selected_learning_rate", 0.02) or 0.02),
            )
        if "selected_lookback_hours" in clean:
            clean["selected_lookback_hours"] = self._clip_bounds(
                bounds.hyperparameter_ranges.get("lookback_hours") or [6.0, 168.0],
                float(clean["selected_lookback_hours"]),
                fallback=float(genome.parameters.get("selected_lookback_hours", 48.0) or 48.0),
            )
        return clean

    def _real_agent_used(self, result: AgentRunResult | None) -> bool:
        return bool(result is not None and result.success and result.provider not in {"deterministic", "fallback"})

    def _lineage_agent_decision(
        self,
        *,
        result: AgentRunResult,
        kind: str,
        used_real_agent: bool,
    ) -> Dict[str, Any]:
        decision = result.to_lineage_decision(kind=kind, used_real_agent=used_real_agent)
        decision["cycle_count"] = self._cycle_count
        return decision

    def _mutate_genome(
        self,
        *,
        parent_genome: StrategyGenome,
        lineage_id: str,
        role: str,
        mutation_index: int,
        budget_bucket: str,
        budget_weight_pct: float,
        scientific_domains: Optional[List[str]] = None,
        parameter_overrides: Optional[Dict[str, Any]] = None,
        mutation_source: str = "deterministic_factory_mutation",
    ) -> StrategyGenome:
        bounds = parent_genome.mutation_bounds
        selected_horizon = int(self._mutation_choice(bounds.horizons_seconds, mutation_index) or self._preferred_horizon(parent_genome.family_id))
        selected_feature_subset = str(self._mutation_choice(bounds.feature_subsets, mutation_index, offset=1) or "baseline")
        selected_model_class = str(self._mutation_choice(bounds.model_classes, mutation_index, offset=2) or "logit")
        selected_min_edge = self._mutation_range_value(bounds.execution_thresholds.get("min_edge") or [0.01, 0.1], mutation_index)
        selected_stake_fraction = self._mutation_range_value(
            bounds.execution_thresholds.get("stake_fraction") or [0.01, 0.1],
            mutation_index + 1,
        )
        selected_learning_rate = self._mutation_range_value(
            bounds.hyperparameter_ranges.get("learning_rate") or [0.001, 0.1],
            mutation_index + 2,
        )
        selected_lookback_hours = self._mutation_range_value(
            bounds.hyperparameter_ranges.get("lookback_hours") or [6.0, 168.0],
            mutation_index + 3,
            divisor=7,
        )
        parameters = dict(parent_genome.parameters)
        parameters.update(
            {
                "mutation_index": mutation_index,
                "mutation_source": mutation_source,
                "selected_horizon_seconds": selected_horizon,
                "selected_feature_subset": selected_feature_subset,
                "selected_model_class": selected_model_class,
                "selected_min_edge": selected_min_edge,
                "selected_stake_fraction": selected_stake_fraction,
                "selected_learning_rate": selected_learning_rate,
                "selected_lookback_hours": selected_lookback_hours,
            }
        )
        for key, value in dict(parameter_overrides or {}).items():
            parameters[key] = value
        parameters["selected_horizon_seconds"] = self._nearest_choice(
            bounds.horizons_seconds,
            int(parameters.get("selected_horizon_seconds", selected_horizon) or selected_horizon),
        )
        parameters["selected_feature_subset"] = self._allowed_choice(
            bounds.feature_subsets,
            str(parameters.get("selected_feature_subset", selected_feature_subset) or selected_feature_subset),
            fallback="baseline",
        )
        parameters["selected_model_class"] = self._allowed_choice(
            bounds.model_classes,
            str(parameters.get("selected_model_class", selected_model_class) or selected_model_class),
            fallback="logit",
        )
        parameters["selected_min_edge"] = self._clip_bounds(
            bounds.execution_thresholds.get("min_edge") or [0.01, 0.1],
            float(parameters.get("selected_min_edge", selected_min_edge) or selected_min_edge),
            fallback=0.03,
        )
        parameters["selected_stake_fraction"] = self._clip_bounds(
            bounds.execution_thresholds.get("stake_fraction") or [0.01, 0.1],
            float(parameters.get("selected_stake_fraction", selected_stake_fraction) or selected_stake_fraction),
            fallback=0.03,
        )
        parameters["selected_learning_rate"] = self._clip_bounds(
            bounds.hyperparameter_ranges.get("learning_rate") or [0.001, 0.1],
            float(parameters.get("selected_learning_rate", selected_learning_rate) or selected_learning_rate),
            fallback=0.02,
        )
        parameters["selected_lookback_hours"] = self._clip_bounds(
            bounds.hyperparameter_ranges.get("lookback_hours") or [6.0, 168.0],
            float(parameters.get("selected_lookback_hours", selected_lookback_hours) or selected_lookback_hours),
            fallback=48.0,
        )
        return StrategyGenome(
            genome_id=f"{lineage_id}:genome",
            lineage_id=lineage_id,
            family_id=parent_genome.family_id,
            parent_genome_id=parent_genome.genome_id,
            role=role,
            parameters=parameters,
            mutation_bounds=bounds,
            scientific_domains=list(scientific_domains or parent_genome.scientific_domains),
            budget_bucket=budget_bucket,
            resource_profile=parent_genome.resource_profile,
            budget_weight_pct=budget_weight_pct,
        )

    def _create_challenger(
        self,
        family: FactoryFamily,
        *,
        parent_lineage: LineageRecord,
        mutation_index: int,
        budget_bucket: str,
        proposal: Optional[ScientificAgentProposal] = None,
        creation_kind: str = "mutation",
    ) -> LineageRecord:
        lineage_id = f"{family.family_id}:challenger:{mutation_index}"
        hypothesis_id = f"{lineage_id}:hypothesis"
        experiment_id = f"{lineage_id}:experiment"
        parent_genome = self.registry.load_genome(parent_lineage.lineage_id)
        if parent_genome is None:
            raise ValueError(f"Missing genome for {parent_lineage.lineage_id}")
        budget_weight_pct = round(max(2.0, self._family_budget_weight(family.family_id) / 3.0), 2)
        role = LineageRole.MOONSHOT.value if budget_bucket == "moonshot" else LineageRole.SHADOW_CHALLENGER.value
        genome = self._mutate_genome(
            parent_genome=parent_genome,
            lineage_id=lineage_id,
            role=role,
            mutation_index=mutation_index,
            budget_bucket=budget_bucket,
            budget_weight_pct=budget_weight_pct,
            scientific_domains=list(proposal.scientific_domains) if proposal else None,
            parameter_overrides=dict(proposal.parameter_overrides) if proposal else None,
            mutation_source=proposal.origin if proposal else "deterministic_factory_mutation",
        )
        if proposal and proposal.agent_metadata:
            genome.parameters["proposal_agent"] = dict(proposal.agent_metadata)
            genome.parameters["last_agent_decision"] = {
                **dict(proposal.agent_metadata),
                "kind": "proposal",
                "used_real_agent": proposal.origin.startswith("real_agent_"),
            }
        genome.parameters["creation_kind"] = creation_kind
        genome.parameters["source_idea_id"] = proposal.source_idea_id if proposal else None
        hypothesis = ResearchHypothesis(
            hypothesis_id=hypothesis_id,
            family_id=family.family_id,
            title=proposal.title if proposal else f"{family.label} Challenger {mutation_index}",
            thesis=proposal.thesis if proposal else f"{family.thesis} Mutated challenger {mutation_index} probes bounded changes to horizon, features, model class, and execution thresholds.",
            scientific_domains=list(proposal.scientific_domains if proposal else (genome.scientific_domains[(mutation_index - 1) % max(1, len(genome.scientific_domains)) :] or genome.scientific_domains)),
            lead_agent_role=proposal.lead_agent_role if proposal else "Genome Mutator",
            success_metric="paper_monthly_roi_pct",
            guardrails=[
                "Mutation remains inside declared bounds.",
                "No live promotion without human approval.",
                "No credentials or hard caps may be mutated.",
            ],
            collaborating_agent_roles=list(proposal.collaborating_agent_roles) if proposal else [],
            origin=proposal.origin if proposal else "deterministic_mutation",
            agent_notes=list(proposal.agent_notes) if proposal else [f"mutation_index={mutation_index}"],
        )
        experiment = ExperimentSpec(
            experiment_id=experiment_id,
            lineage_id=lineage_id,
            family_id=family.family_id,
            hypothesis_id=hypothesis_id,
            genome_id=genome.genome_id,
            goldfish_workspace=str(self.bridge.workspace_path(family.family_id)),
            pipeline_stages=["dataset", "features", "train", "walkforward", "stress", "package"],
            backend_mode="goldfish_sidecar",
            resource_profile=genome.resource_profile,
            inputs={
                "parent_lineage_id": parent_lineage.lineage_id,
                "mutation_index": mutation_index,
                "budget_bucket": budget_bucket,
                "proposal_id": proposal.proposal_id if proposal else None,
                "proposal_kind": proposal.proposal_kind if proposal else creation_kind,
                "source_idea_id": proposal.source_idea_id if proposal else None,
                "lead_agent_role": proposal.lead_agent_role if proposal else "Genome Mutator",
                "collaborating_agent_roles": list(proposal.collaborating_agent_roles) if proposal else [],
                "proposal_agent": dict(proposal.agent_metadata) if proposal else {},
            },
        )
        lineage = LineageRecord(
            lineage_id=lineage_id,
            family_id=family.family_id,
            label=proposal.title if proposal else f"{family.label} Challenger {mutation_index}",
            role=role,
            current_stage=PromotionStage.IDEA.value,
            target_portfolios=list(family.target_portfolios),
            target_venues=list(family.target_venues),
            hypothesis_id=hypothesis_id,
            genome_id=genome.genome_id,
            experiment_id=experiment_id,
            budget_bucket=budget_bucket,
            budget_weight_pct=budget_weight_pct,
            connector_ids=list(family.primary_connector_ids),
            goldfish_workspace=str(self.bridge.workspace_path(family.family_id)),
            creation_kind=creation_kind,
            parent_lineage_id=parent_lineage.lineage_id,
            iteration_status="new_model_candidate" if creation_kind == "new_model" else "new_candidate",
        )
        self.registry.save_research_pack(
            hypothesis=hypothesis,
            genome=genome,
            experiment=experiment,
            lineage=lineage,
        )
        return lineage

    def _proposal_mix_cycle_size(self) -> int:
        mutation_pct = max(0, int(getattr(config, "FACTORY_CHALLENGER_MUTATION_PCT", 80) or 0))
        new_pct = max(0, int(getattr(config, "FACTORY_CHALLENGER_NEW_MODEL_PCT", 20) or 0))
        total = max(1, mutation_pct + new_pct)
        mutation_ratio = mutation_pct / total
        if mutation_ratio >= 0.95:
            return 20
        if mutation_ratio >= 0.9:
            return 10
        if mutation_ratio >= 0.8:
            return 5
        if mutation_ratio >= 0.75:
            return 4
        return 2

    def _proposal_creation_kind(self, existing_shadow: List[LineageRecord]) -> str:
        mutation_pct = max(0, int(getattr(config, "FACTORY_CHALLENGER_MUTATION_PCT", 80) or 0))
        new_pct = max(0, int(getattr(config, "FACTORY_CHALLENGER_NEW_MODEL_PCT", 20) or 0))
        if new_pct <= 0:
            return "mutation"
        if mutation_pct <= 0:
            return "new_model"
        cycle_size = self._proposal_mix_cycle_size()
        new_slots = max(1, round((new_pct / max(1, mutation_pct + new_pct)) * cycle_size))
        slot = len(existing_shadow) % cycle_size
        return "new_model" if slot >= cycle_size - new_slots else "mutation"

    def _seed_challengers(
        self,
        family: FactoryFamily,
        lineages_by_family: Dict[str, List[LineageRecord]],
        *,
        runtime_mode_value: str,
        recent_actions: List[str],
    ) -> None:
        if runtime_mode_value != "full":
            return
        active = [lineage for lineage in lineages_by_family.get(family.family_id, []) if lineage.active]
        champion = next((lineage for lineage in active if lineage.lineage_id == family.champion_lineage_id), None)
        if champion is None and active:
            champion = active[0]
        if champion is None:
            return
        champion_genome = self.registry.load_genome(champion.lineage_id)
        if champion_genome is None:
            return
        max_shadow = int(champion_genome.parameters.get("max_shadow_challengers", 5) or 5)
        execution_evidence = summarize_execution_targets(family.target_portfolios)
        maintenance_actions = {
            str(lineage.iteration_status or "").strip()
            for lineage in active
            if str(lineage.iteration_status or "").strip()
        }
        maintenance_actions.update(
            str(lineage.last_agent_review_action or "").strip().lower()
            for lineage in active
            if str(lineage.last_agent_review_status or "") == "completed"
            and str(lineage.last_agent_review_action or "").strip()
        )
        extra_shadow = self._challenger_pressure(execution_evidence, maintenance_actions=maintenance_actions)
        desired_shadow = min(max_shadow, max(1, self._cycle_count) + extra_shadow)
        existing_shadow = [
            lineage
            for lineage in active
            if lineage.role in {LineageRole.SHADOW_CHALLENGER.value, LineageRole.MOONSHOT.value}
        ]
        if len(existing_shadow) >= desired_shadow:
            return
        budget_sequence = ["incumbent", "adjacent", "moonshot", "incumbent", "adjacent"]
        champion_hypothesis = self.registry.load_hypothesis(champion.lineage_id)
        learning_memory = self.registry.learning_memories(family_id=family.family_id, limit=12)
        while len(existing_shadow) < desired_shadow:
            mutation_index = len(lineages_by_family.get(family.family_id, []))
            creation_kind = self._proposal_creation_kind(existing_shadow)
            idea_candidates = relevant_ideas_for_family(
                self.project_root,
                family.family_id,
                limit=3,
                existing_lineages=lineages_by_family.get(family.family_id, []),
            )
            agent_result = self.agent_runtime.generate_proposal(
                family=family,
                champion_hypothesis=champion_hypothesis,
                champion_genome=champion_genome,
                learning_memory=learning_memory,
                execution_evidence=execution_evidence,
                cycle_count=self._cycle_count,
                proposal_index=mutation_index,
                desired_creation_kind=creation_kind,
            )
            proposal = (
                apply_real_agent_proposal(
                    result=agent_result,
                    family=family,
                    proposal_index=mutation_index,
                )
                if self._real_agent_used(agent_result)
                else self.strategy_inventor.generate_proposal(
                    family=family,
                    champion_hypothesis=champion_hypothesis,
                    champion_genome=champion_genome,
                    learning_memory=learning_memory,
                    cycle_count=self._cycle_count,
                    proposal_index=mutation_index,
                    desired_creation_kind=creation_kind,
                    idea_candidates=idea_candidates,
                )
            )
            budget_bucket = proposal.budget_bucket or budget_sequence[(mutation_index - 1) % len(budget_sequence)]
            created = self._create_challenger(
                family,
                parent_lineage=champion,
                mutation_index=mutation_index,
                budget_bucket=budget_bucket,
                proposal=proposal,
                creation_kind=str(proposal.proposal_kind or creation_kind),
            )
            family.shadow_challenger_ids = sorted(set(list(family.shadow_challenger_ids) + [created.lineage_id]))
            self.registry.save_family(family)
            lineages_by_family[family.family_id].append(created)
            existing_shadow.append(created)
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Seeded {created.creation_kind} challenger {created.lineage_id} for {family.family_id} from {proposal.lead_agent_role} with collaborators {','.join(proposal.collaborating_agent_roles) or 'none'} via {agent_result.provider if agent_result is not None else 'deterministic'}.",
            )

    def _challenger_pressure(
        self,
        execution_evidence: Dict[str, Any],
        *,
        maintenance_actions: Iterable[str] = (),
    ) -> int:
        issue_codes = {str(item) for item in (execution_evidence.get("issue_codes") or [])}
        model_build_pressure = {
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
        pressure = 1 if issue_codes.intersection(model_build_pressure) else 0
        maintenance_tokens = {str(item).strip().lower() for item in maintenance_actions if str(item).strip()}
        if maintenance_tokens.intersection({"review_requested_replace", "replace", "review_requested_rework", "rework"}):
            pressure = max(pressure, 2)
        elif maintenance_tokens.intersection({"review_requested_retrain", "retrain"}):
            pressure = max(pressure, 1)
        return pressure

    def _review_uses_slow_thresholds(self, family: FactoryFamily, lineage: LineageRecord) -> bool:
        labels = {
            family.family_id.lower(),
            *(str(item).lower() for item in family.target_venues),
            *(str(item).lower() for item in lineage.target_venues),
            *(str(item).lower() for item in lineage.target_portfolios),
        }
        return any(token.startswith("betfair") or token.startswith("polymarket") for token in labels)

    def _debug_issue_signature(self, execution_evidence: Dict[str, Any]) -> str:
        issue_codes = sorted(str(item) for item in (execution_evidence.get("issue_codes") or []) if str(item).strip())
        blockers = sorted(str(item) for item in (execution_evidence.get("blockers") or []) if str(item).strip())
        error = str(execution_evidence.get("error") or "").strip().lower()
        return "|".join(issue_codes + blockers + ([error] if error else []))

    def _human_resolution_from_evidence(self, execution_evidence: Dict[str, Any]) -> Dict[str, Any]:
        texts: List[str] = []
        if execution_evidence.get("error"):
            texts.append(str(execution_evidence.get("error") or ""))
        texts.extend(str(item) for item in (execution_evidence.get("blockers") or []) if str(item).strip())
        texts.extend(
            str(item.get("detail") or "")
            for item in (execution_evidence.get("issues") or [])
            if str(item.get("detail") or "").strip()
        )
        haystack = " | ".join(texts).lower()
        if any(token in haystack for token in ["betting_restricted_location", "restricted location", "restricted_location", "jurisdiction"]):
            return {
                "requires_human": True,
                "bug_category": "venue_restriction",
                "human_action": "Resolve the venue/account location restriction or disable this venue for the affected model.",
                "summary": "Venue or account restriction is blocking execution and needs operator intervention.",
            }
        if any(
            token in haystack
            for token in [
                "missing_credentials",
                "missing credential",
                "api key",
                "api-key",
                "invalid api",
                "permissions for action",
                "authentication",
                "auth failed",
                "signature",
                "certificate",
                "cert",
                "secret",
                "-2015",
            ]
        ):
            return {
                "requires_human": True,
                "bug_category": "credentials_or_permissions",
                "human_action": "Fix or rotate the affected venue credentials, permissions, or certificate paths, then rerun the model.",
                "summary": "Execution is blocked by credentials, permissions, or certificates that require operator action.",
            }
        return {
            "requires_human": False,
            "bug_category": "runtime_or_data",
            "human_action": None,
            "summary": "Bug appears actionable by the debug agent without immediate operator intervention.",
        }

    def _debug_review_reason(
        self,
        lineage: LineageRecord,
        execution_evidence: Dict[str, Any],
    ) -> Optional[str]:
        if not bool(getattr(config, "FACTORY_DEBUG_AGENT_ENABLED", True)):
            return None
        if not lineage.active:
            return None
        if lineage.current_stage not in {
            PromotionStage.PAPER.value,
            PromotionStage.CANARY_READY.value,
            PromotionStage.LIVE_READY.value,
            PromotionStage.APPROVED_LIVE.value,
        }:
            return None
        issue_codes = {str(item) for item in (execution_evidence.get("issue_codes") or [])}
        debug_triggered = bool(execution_evidence.get("error")) or bool(
            issue_codes.intersection(
                {"runtime_error", "heartbeat_stale", "readiness_blocked", "untrainable_model", "training_stalled", "stalled_model"}
            )
        )
        if not debug_triggered:
            return None
        signature = self._debug_issue_signature(execution_evidence)
        if not signature:
            return None
        if str(lineage.last_debug_issue_signature or "") != signature:
            return "new_bug_signature"
        next_review = _parse_iso_dt(lineage.next_debug_review_at)
        if next_review is None or datetime.now(timezone.utc) >= next_review:
            return "persistent_bug_followup"
        return None

    def _scheduled_review_reason(
        self,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_bundle: Optional[EvaluationBundle],
        execution_evidence: Dict[str, Any],
    ) -> Optional[str]:
        if not bool(getattr(config, "FACTORY_AGENT_REVIEW_ENABLED", True)):
            return None
        if not lineage.active:
            return None
        if lineage.current_stage not in {
            PromotionStage.PAPER.value,
            PromotionStage.CANARY_READY.value,
            PromotionStage.LIVE_READY.value,
            PromotionStage.APPROVED_LIVE.value,
        }:
            return None
        if latest_bundle is None:
            return None
        paper_days = int(latest_bundle.paper_days or 0)
        evidence_trades = int(max(latest_bundle.trade_count or 0, latest_bundle.settled_count or 0))
        use_slow = self._review_uses_slow_thresholds(family, lineage)
        min_days = int(
            getattr(
                config,
                "FACTORY_AGENT_REVIEW_MIN_SLOW_DAYS" if use_slow else "FACTORY_AGENT_REVIEW_MIN_FAST_DAYS",
                21 if use_slow else 14,
            )
        )
        min_trades = int(
            getattr(
                config,
                "FACTORY_AGENT_REVIEW_MIN_SLOW_SETTLED" if use_slow else "FACTORY_AGENT_REVIEW_MIN_FAST_TRADES",
                10 if use_slow else 50,
            )
        )
        if paper_days < min_days or evidence_trades < min_trades:
            return None
        last_review_at = _parse_iso_dt(lineage.last_agent_review_at)
        if last_review_at is None:
            return "initial_maturity_review"
        issue_codes = {str(item) for item in (execution_evidence.get("issue_codes") or [])}
        urgent_issue_codes = {
            "negative_paper_roi",
            "negative_realized_pnl",
            "poor_win_rate",
            "no_trade_syndrome",
            "zero_simulated_fills",
            "excessive_rejections",
            "runtime_error",
            "heartbeat_stale",
            "severe_slippage",
            "drawdown_halt_active",
            "untrainable_model",
            "trade_stalled",
            "training_stalled",
            "stalled_model",
        }
        if issue_codes.intersection(urgent_issue_codes):
            return "performance_review"
        now = datetime.now(timezone.utc)
        interval_days = max(1, int(getattr(config, "FACTORY_AGENT_REVIEW_INTERVAL_DAYS", 7) or 7))
        if (now - last_review_at) >= timedelta(days=interval_days):
            return "scheduled_interval_review"
        incremental_trades = max(1, int(getattr(config, "FACTORY_AGENT_REVIEW_INCREMENTAL_TRADES", 25) or 25))
        if evidence_trades >= int(lineage.last_agent_review_trade_count or 0) + incremental_trades:
            return "new_trade_batch_review"
        return None

    def _maybe_run_scheduled_agent_review(
        self,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_by_stage: Dict[str, EvaluationBundle],
        *,
        recent_actions: List[str],
    ) -> None:
        latest_bundle = (
            latest_by_stage.get(EvaluationStage.PAPER.value)
            or latest_by_stage.get(EvaluationStage.STRESS.value)
            or latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
        )
        execution_evidence = self._execution_validation_snapshot(lineage)
        review_reason = self._scheduled_review_reason(family, lineage, latest_bundle, execution_evidence)
        if not review_reason:
            return
        critique = self.agent_runtime.critique_post_evaluation(
            family=family,
            lineage=lineage,
            genome=self.registry.load_genome(lineage.lineage_id),
            latest_bundle=latest_bundle,
            learning_memory=self.registry.learning_memories(family_id=lineage.family_id, limit=12),
            execution_evidence=execution_evidence,
            review_context={
                "trigger_reason": review_reason,
                "scheduled_review": True,
                "policy": {
                    "interval_days": int(getattr(config, "FACTORY_AGENT_REVIEW_INTERVAL_DAYS", 7) or 7),
                    "incremental_trades": int(getattr(config, "FACTORY_AGENT_REVIEW_INCREMENTAL_TRADES", 25) or 25),
                },
            },
            force=True,
        )
        now = datetime.now(timezone.utc)
        lineage.last_agent_review_at = now.isoformat()
        lineage.last_agent_review_reason = review_reason
        lineage.last_agent_review_trade_count = int(max((latest_bundle.trade_count if latest_bundle else 0) or 0, (latest_bundle.settled_count if latest_bundle else 0) or 0))
        lineage.next_agent_review_at = (now + timedelta(days=max(1, int(getattr(config, "FACTORY_AGENT_REVIEW_INTERVAL_DAYS", 7) or 7)))).isoformat()
        if critique is None:
            lineage.last_agent_review_status = "skipped_agent_disabled"
            lineage.last_agent_review_artifact_path = None
            lineage.last_agent_review_action = "hold"
            lineage.last_agent_review_summary = "Agent review skipped because real agents are disabled for this family."
            self.registry.save_lineage(lineage)
            return
        lineage.last_agent_review_status = "completed" if critique.success else "failed"
        lineage.last_agent_review_artifact_path = critique.artifact_path
        critique_payload = dict(critique.result_payload or {})
        lineage.last_agent_review_action = str(critique_payload.get("maintenance_action") or "hold")
        lineage.last_agent_review_summary = str(critique_payload.get("summary") or "") or None
        self._apply_review_maintenance(
            family=family,
            lineage=lineage,
            latest_bundle=latest_bundle,
            critique_payload=critique_payload,
            recent_actions=recent_actions,
        )
        self.registry.save_lineage(lineage)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if experiment is not None:
            experiment.expected_outputs = dict(experiment.expected_outputs or {})
            experiment.expected_outputs["scheduled_agent_review"] = {
                "artifact_path": critique.artifact_path,
                "provider": critique.provider,
                "model": critique.model,
                "success": critique.success,
                "fallback_used": critique.fallback_used,
                "review_reason": review_reason,
                "maintenance_action": lineage.last_agent_review_action,
                "maintenance_summary": lineage.last_agent_review_summary,
                "maintenance_reason": critique_payload.get("maintenance_reason"),
                "requires_retrain": bool(critique_payload.get("requires_retrain")),
                "requires_new_challenger": bool(critique_payload.get("requires_new_challenger")),
                "reviewed_at": lineage.last_agent_review_at,
            }
            self.registry.save_experiment(lineage.lineage_id, experiment)
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Scheduled agent review ran for {lineage.lineage_id} ({review_reason}) via {critique.provider} {critique.model}.",
        )

    def _apply_review_maintenance(
        self,
        *,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_bundle: EvaluationBundle | None,
        critique_payload: Dict[str, Any],
        recent_actions: List[str],
    ) -> None:
        action = str(critique_payload.get("maintenance_action") or "hold").strip().lower() or "hold"
        reason = str(critique_payload.get("maintenance_reason") or critique_payload.get("summary") or "agent_review").strip()
        blockers = list(lineage.blockers or [])
        blocker_map = {
            "retrain": "review_retrain_requested",
            "rework": "review_rework_requested",
            "replace": "review_replace_requested",
            "retire": "review_retire_recommended",
        }
        blocker = blocker_map.get(action)
        if blocker:
            blockers.append(blocker)
        lineage.blockers = list(dict.fromkeys(blockers))
        if action == "hold":
            if lineage.iteration_status in {"review_requested_retrain", "review_requested_rework", "review_requested_replace"}:
                lineage.iteration_status = "review_hold"
            return
        if action == "retrain":
            lineage.iteration_status = "review_requested_retrain"
            _append_recent_action(recent_actions, f"[cycle {self._cycle_count}] Review marked {lineage.lineage_id} for retrain: {reason}.")
            return
        if action == "rework":
            lineage.iteration_status = "review_requested_rework"
            lineage.loss_streak = max(int(lineage.loss_streak or 0), 1)
            _append_recent_action(recent_actions, f"[cycle {self._cycle_count}] Review marked {lineage.lineage_id} for rework: {reason}.")
            return
        if action == "replace":
            lineage.iteration_status = "review_requested_replace"
            lineage.loss_streak = max(int(lineage.loss_streak or 0), int(lineage.max_tweaks or 2))
            _append_recent_action(recent_actions, f"[cycle {self._cycle_count}] Review marked {lineage.lineage_id} for replacement pressure: {reason}.")
            return
        if action == "retire":
            if lineage.lineage_id != family.champion_lineage_id and lineage.current_stage not in {
                PromotionStage.CANARY_READY.value,
                PromotionStage.LIVE_READY.value,
                PromotionStage.APPROVED_LIVE.value,
            }:
                lineage.active = False
                lineage.retired_at = utc_now_iso()
                lineage.iteration_status = "retired"
                lineage.retirement_reason = "agent_review_retire_recommended"
                retired_ids = set(family.retired_lineage_ids)
                retired_ids.add(lineage.lineage_id)
                family.retired_lineage_ids = sorted(retired_ids)
                self._record_learning_memory(
                    lineage,
                    {
                        "monthly_roi_pct": float((latest_bundle.monthly_roi_pct if latest_bundle else 0.0) or 0.0),
                        "paper_days": int((latest_bundle.paper_days if latest_bundle else 0) or 0),
                        "trade_count": int((latest_bundle.trade_count if latest_bundle else 0) or 0),
                        "execution_issue_codes": [],
                    },
                    reason=reason or "agent_review_retire_recommended",
                )
                self.registry.save_family(family)
                _append_recent_action(recent_actions, f"[cycle {self._cycle_count}] Review retired {lineage.lineage_id}: {reason}.")
            else:
                lineage.iteration_status = "review_recommended_retire"
                lineage.loss_streak = max(int(lineage.loss_streak or 0), int(lineage.max_tweaks or 2))
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Review recommended retirement for protected lineage {lineage.lineage_id}: {reason}.",
                )

    def _maybe_run_debug_agent(
        self,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_by_stage: Dict[str, EvaluationBundle],
        *,
        recent_actions: List[str],
    ) -> None:
        latest_bundle = (
            latest_by_stage.get(EvaluationStage.PAPER.value)
            or latest_by_stage.get(EvaluationStage.STRESS.value)
            or latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
        )
        execution_evidence = self._execution_validation_snapshot(lineage)
        debug_reason = self._debug_review_reason(lineage, execution_evidence)
        if not debug_reason:
            return
        issue_signature = self._debug_issue_signature(execution_evidence)
        heuristic_human = self._human_resolution_from_evidence(execution_evidence)
        debug_result = self.agent_runtime.diagnose_bug(
            family=family,
            lineage=lineage,
            genome=self.registry.load_genome(lineage.lineage_id),
            latest_bundle=latest_bundle,
            execution_evidence=execution_evidence,
            debug_context={
                "trigger_reason": debug_reason,
                "issue_signature": issue_signature,
                "heuristic_human_resolution": heuristic_human,
            },
        )
        now = datetime.now(timezone.utc)
        lineage.last_debug_review_at = now.isoformat()
        lineage.next_debug_review_at = (
            now + timedelta(hours=max(1, int(getattr(config, "FACTORY_DEBUG_AGENT_REVIEW_INTERVAL_HOURS", 12) or 12)))
        ).isoformat()
        lineage.last_debug_review_reason = debug_reason
        lineage.last_debug_issue_signature = issue_signature
        if debug_result is None:
            lineage.last_debug_review_status = "skipped_agent_disabled"
            lineage.last_debug_review_artifact_path = None
            lineage.last_debug_requires_human = bool(heuristic_human.get("requires_human"))
            lineage.last_debug_human_action = heuristic_human.get("human_action")
            lineage.last_debug_bug_category = heuristic_human.get("bug_category")
            lineage.last_debug_summary = heuristic_human.get("summary")
            self.registry.save_lineage(lineage)
            return
        payload = dict(debug_result.result_payload or {})
        lineage.last_debug_review_status = "completed" if debug_result.success else "failed"
        lineage.last_debug_review_artifact_path = debug_result.artifact_path
        lineage.last_debug_requires_human = bool(payload.get("requires_human", heuristic_human.get("requires_human")))
        lineage.last_debug_human_action = str(payload.get("human_action") or heuristic_human.get("human_action") or "") or None
        lineage.last_debug_bug_category = str(payload.get("bug_category") or heuristic_human.get("bug_category") or "") or None
        lineage.last_debug_summary = str(payload.get("summary") or heuristic_human.get("summary") or "") or None
        self.registry.save_lineage(lineage)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if experiment is not None:
            experiment.expected_outputs = dict(experiment.expected_outputs or {})
            experiment.expected_outputs["runtime_debug_review"] = {
                "artifact_path": debug_result.artifact_path,
                "provider": debug_result.provider,
                "model": debug_result.model,
                "success": debug_result.success,
                "fallback_used": debug_result.fallback_used,
                "review_reason": debug_reason,
                "requires_human": lineage.last_debug_requires_human,
                "human_action": lineage.last_debug_human_action,
                "bug_category": lineage.last_debug_bug_category,
                "reviewed_at": lineage.last_debug_review_at,
            }
            self.registry.save_experiment(lineage.lineage_id, experiment)
        debug_tail = f" human_action={lineage.last_debug_human_action}" if lineage.last_debug_requires_human else ""
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Debug agent ran for {lineage.lineage_id} ({debug_reason}) via {debug_result.provider} {debug_result.model}.{debug_tail}",
        )

    def _lineage_variant(self, lineage: LineageRecord) -> Dict[str, float]:
        genome = self.registry.load_genome(lineage.lineage_id)
        if genome is None:
            return {
                "roi_delta": 0.0,
                "drawdown_delta": 0.0,
                "slippage_delta": 0.0,
                "calibration_delta": 0.0,
                "capacity_delta": 0.0,
                "failure_delta": 0.0,
                "regime_delta": 0.0,
                "baseline_delta": 0.0,
            }
        parameters = dict(genome.parameters)
        mutation_index = int(parameters.get("mutation_index", 0) or 0)
        if mutation_index <= 0:
            return {
                "roi_delta": 0.0,
                "drawdown_delta": 0.0,
                "slippage_delta": 0.0,
                "calibration_delta": 0.0,
                "capacity_delta": 0.0,
                "failure_delta": 0.0,
                "regime_delta": 0.0,
                "baseline_delta": 0.0,
            }
        feature_bonus = {
            "baseline": 0.0,
            "microstructure": 0.25,
            "cross_science": 0.55,
            "regime": 0.2,
        }.get(str(parameters.get("selected_feature_subset", "baseline")), 0.0)
        model_bonus = {
            "logit": 0.0,
            "gbdt": 0.2,
            "tft": 0.35,
            "transformer": 0.1,
            "rules": -0.25,
        }.get(str(parameters.get("selected_model_class", "logit")), 0.0)
        horizon = int(parameters.get("selected_horizon_seconds", self._preferred_horizon(lineage.family_id)) or self._preferred_horizon(lineage.family_id))
        preferred_horizon = self._preferred_horizon(lineage.family_id)
        horizon_bonus = max(-0.35, 0.25 - abs(horizon - preferred_horizon) / max(preferred_horizon, 1200))
        min_edge = float(parameters.get("selected_min_edge", 0.02) or 0.02)
        stake_fraction = float(parameters.get("selected_stake_fraction", 0.03) or 0.03)
        learning_rate = float(parameters.get("selected_learning_rate", 0.01) or 0.01)
        lookback_hours = float(parameters.get("selected_lookback_hours", 24.0) or 24.0)
        edge_penalty = max(0.0, (min_edge - 0.04) * 25.0)
        stake_penalty = max(0.0, (stake_fraction - 0.05) * 18.0)
        lr_penalty = max(0.0, abs(learning_rate - 0.02) * 12.0)
        lookback_bonus = max(-0.25, 0.25 - abs(lookback_hours - 48.0) / 96.0)
        slot_bias = ((mutation_index % 5) - 2) * 0.18
        digest = int(hashlib.sha1(lineage.lineage_id.encode("utf-8")).hexdigest()[:8], 16)
        hash_bias = ((digest % 11) - 5) / 40.0
        roi_delta = round(feature_bonus + model_bonus + horizon_bonus + lookback_bonus + slot_bias + hash_bias - edge_penalty - stake_penalty - lr_penalty, 4)
        calibration_delta = round((feature_bonus + model_bonus + lookback_bonus + hash_bias) / 20.0, 4)
        drawdown_delta = round(max(-1.0, stake_penalty + lr_penalty - horizon_bonus - 0.2), 4)
        slippage_delta = round((feature_bonus * 0.6) - (edge_penalty * 0.8) - 0.15 + hash_bias, 4)
        capacity_delta = round((lookback_bonus * 0.4) + (0.2 if str(parameters.get("selected_model_class")) != "transformer" else -0.15), 4)
        failure_delta = round(max(-0.02, 0.005 + (stake_penalty * 0.01) + (lr_penalty * 0.01) - (feature_bonus * 0.008)), 4)
        regime_delta = round(max(-0.2, min(0.2, horizon_bonus + (feature_bonus * 0.2) - 0.05)), 4)
        baseline_delta = 1.0 if roi_delta > 0.45 else (-1.0 if roi_delta < -0.35 else 0.0)
        return {
            "roi_delta": roi_delta,
            "drawdown_delta": drawdown_delta,
            "slippage_delta": slippage_delta,
            "calibration_delta": calibration_delta,
            "capacity_delta": capacity_delta,
            "failure_delta": failure_delta,
            "regime_delta": regime_delta,
            "baseline_delta": baseline_delta,
        }

    def _adjust_bundle_for_lineage(self, lineage: LineageRecord, bundle: EvaluationBundle) -> EvaluationBundle:
        variant = self._lineage_variant(lineage)
        monthly_roi = round(float(bundle.monthly_roi_pct) + variant["roi_delta"], 4)
        drawdown = round(max(0.0, float(bundle.max_drawdown_pct) + variant["drawdown_delta"]), 4)
        slippage = round(float(bundle.slippage_headroom_pct) + variant["slippage_delta"], 4)
        calibration = round(float(bundle.calibration_lift_abs) + variant["calibration_delta"], 4)
        capacity = round(max(0.0, min(1.0, float(bundle.capacity_score) + variant["capacity_delta"])), 4)
        failure_rate = round(max(0.0, min(1.0, float(bundle.failure_rate) + variant["failure_delta"])), 4)
        regime = round(max(0.0, min(1.0, float(bundle.regime_robustness) + variant["regime_delta"])), 4)
        baseline_beaten_windows = max(0, min(3, int(bundle.baseline_beaten_windows + variant["baseline_delta"])))
        windows = [
            replace(
                window,
                monthly_roi_pct=round(float(window.monthly_roi_pct) + variant["roi_delta"], 4),
                brier_lift_abs=round(float(window.brier_lift_abs) + variant["calibration_delta"], 4),
                drawdown_pct=round(max(0.0, float(window.drawdown_pct) + variant["drawdown_delta"]), 4),
                slippage_headroom_pct=round(float(window.slippage_headroom_pct) + variant["slippage_delta"], 4),
                failure_rate=failure_rate,
                regime_robustness=regime,
            )
            for window in bundle.windows
        ]
        return replace(
            bundle,
            evaluation_id=f"{bundle.evaluation_id}:{lineage.lineage_id.split(':')[-1]}",
            windows=windows,
            monthly_roi_pct=monthly_roi,
            max_drawdown_pct=drawdown,
            slippage_headroom_pct=slippage,
            calibration_lift_abs=calibration,
            capacity_score=capacity,
            failure_rate=failure_rate,
            regime_robustness=regime,
            baseline_beaten_windows=baseline_beaten_windows,
            stress_positive=bool(bundle.stress_positive and (monthly_roi > 0.0) and (slippage > 0.0)),
            net_pnl=round(float(bundle.net_pnl) + variant["roi_delta"], 4),
            notes=list(bundle.notes) + [f"variant_applied={lineage.lineage_id}"],
        )

    def _queue_status(self, lineage: LineageRecord) -> str:
        if not lineage.active:
            return "retired"
        if lineage.current_stage in {PromotionStage.CANARY_READY.value, PromotionStage.LIVE_READY.value}:
            return "promotion_candidate"
        if lineage.current_stage == PromotionStage.APPROVED_LIVE.value:
            return "approved_live"
        if lineage.current_stage == PromotionStage.PAPER.value:
            return "paper"
        if lineage.current_stage == PromotionStage.SHADOW.value:
            return "shadow"
        return "queued"

    def _queue_priority(self, lineage: LineageRecord) -> int:
        base = {
            LineageRole.CHAMPION.value: 10,
            LineageRole.PAPER_CHALLENGER.value: 20,
            LineageRole.SHADOW_CHALLENGER.value: 30,
            LineageRole.MOONSHOT.value: 40,
        }.get(lineage.role, 50)
        if not lineage.active:
            base += 50
        return base

    def _refresh_queue_entries(self, entries: List[ExperimentQueueEntry]) -> List[ExperimentQueueEntry]:
        refreshed_entries: List[ExperimentQueueEntry] = []
        for entry in entries:
            lineage = self.registry.load_lineage(entry.lineage_id)
            if lineage is None:
                refreshed_entries.append(entry)
                continue
            refreshed_entries.append(
                replace(
                    entry,
                    role=lineage.role,
                    current_stage=lineage.current_stage,
                    status=self._queue_status(lineage),
                    priority=self._queue_priority(lineage),
                    updated_at=utc_now_iso(),
                    notes=[
                        f"loss_streak={int(lineage.loss_streak or 0)}",
                        f"tweak_count={int(lineage.tweak_count or 0)}/{int(lineage.max_tweaks or 2)}",
                    ],
                )
            )
        return refreshed_entries

    def _reclassify_family(
        self,
        family: FactoryFamily,
        ranked_rows: List[Dict[str, Any]],
        *,
        recent_actions: List[str],
    ) -> None:
        active_ranked = [row for row in ranked_rows if row.get("active", True)]
        if not active_ranked:
            return
        new_champion_id = str(active_ranked[0]["lineage_id"])
        if family.champion_lineage_id != new_champion_id:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Champion rotated for {family.family_id}: {family.champion_lineage_id} -> {new_champion_id}.",
            )
        family.champion_lineage_id = new_champion_id
        paper_candidates = [
            row["lineage_id"]
            for row in active_ranked[1:]
            if row.get("current_stage") in {
                PromotionStage.PAPER.value,
                PromotionStage.CANARY_READY.value,
                PromotionStage.LIVE_READY.value,
                PromotionStage.APPROVED_LIVE.value,
            }
        ][:2]
        shadow_candidates = [
            row["lineage_id"]
            for row in active_ranked[1:]
            if row["lineage_id"] not in paper_candidates
        ][:5]
        family.paper_challenger_ids = paper_candidates
        family.shadow_challenger_ids = shadow_candidates
        self.registry.save_family(family)

        for row in ranked_rows:
            lineage = self.registry.load_lineage(str(row["lineage_id"]))
            if lineage is None:
                continue
            if not lineage.active:
                continue
            if lineage.lineage_id == family.champion_lineage_id:
                lineage.role = LineageRole.CHAMPION.value
                lineage.loss_streak = 0
                lineage.iteration_status = "champion"
            elif lineage.lineage_id in family.paper_challenger_ids:
                lineage.role = LineageRole.PAPER_CHALLENGER.value
                if lineage.iteration_status == "new_candidate":
                    lineage.iteration_status = "paper_candidate"
            else:
                lineage.role = LineageRole.SHADOW_CHALLENGER.value
            self.registry.save_lineage(lineage)
            genome = self.registry.load_genome(lineage.lineage_id)
            if genome is not None and genome.role != lineage.role:
                genome.role = lineage.role
                self.registry.save_genome(lineage.lineage_id, genome)

    def _tweak_lineage_for_underperformance(
        self,
        lineage: LineageRecord,
        row: Dict[str, Any],
        *,
        recent_actions: List[str],
    ) -> None:
        genome = self.registry.load_genome(lineage.lineage_id)
        hypothesis = self.registry.load_hypothesis(lineage.lineage_id)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if genome is None:
            return
        tweak_number = int(lineage.tweak_count or 0) + 1
        hard_vetoes = list(row.get("hard_vetoes") or [])
        reason = hard_vetoes[0] if hard_vetoes else "underperforming_vs_champion"
        parameters = dict(genome.parameters)
        current_feature = str(parameters.get("selected_feature_subset", "baseline") or "baseline")
        current_model = str(parameters.get("selected_model_class", "logit") or "logit")
        current_horizon = int(parameters.get("selected_horizon_seconds", self._preferred_horizon(lineage.family_id)) or self._preferred_horizon(lineage.family_id))
        current_lookback = float(parameters.get("selected_lookback_hours", 48.0) or 48.0)
        current_min_edge = float(parameters.get("selected_min_edge", 0.03) or 0.03)
        current_stake = float(parameters.get("selected_stake_fraction", 0.03) or 0.03)
        execution_evidence = dict(row.get("execution_validation") or {})
        agent_result = self.agent_runtime.suggest_tweak(
            lineage=lineage,
            hypothesis=hypothesis,
            genome=genome,
            row=row,
            learning_memory=self.registry.learning_memories(family_id=lineage.family_id, limit=12),
            execution_evidence=execution_evidence,
        )
        agent_overrides = self._sanitize_parameter_overrides(
            genome,
            {
                key: value
                for key, value in dict(
                    (agent_result.result_payload.get("parameter_overrides") if self._real_agent_used(agent_result) else {}) or {}
                ).items()
                if value is not None
            },
        )
        if agent_overrides:
            parameters.update(agent_overrides)
        elif "drawdown" in reason.lower() or float(row.get("max_drawdown_pct", 0.0) or 0.0) > float(getattr(config, "FACTORY_PAPER_GATE_MAX_DRAWDOWN_PCT", 8.0)):
            parameters["selected_stake_fraction"] = self._clip_bounds(
                genome.mutation_bounds.execution_thresholds.get("stake_fraction") or [0.01, 0.1],
                current_stake * 0.75,
                fallback=current_stake,
            )
            parameters["selected_min_edge"] = self._clip_bounds(
                genome.mutation_bounds.execution_thresholds.get("min_edge") or [0.01, 0.1],
                current_min_edge + 0.01,
                fallback=current_min_edge,
            )
            parameters["selected_feature_subset"] = self._allowed_choice(
                genome.mutation_bounds.feature_subsets,
                "regime",
                fallback=current_feature,
            )
        elif float(row.get("calibration_lift_abs", 0.0) or 0.0) > 0.0:
            parameters["selected_model_class"] = self._rotate_choice(
                genome.mutation_bounds.model_classes,
                current_model,
                step=tweak_number,
                fallback="logit",
            )
            parameters["selected_lookback_hours"] = self._clip_bounds(
                genome.mutation_bounds.hyperparameter_ranges.get("lookback_hours") or [6.0, 168.0],
                current_lookback + 12.0,
                fallback=current_lookback,
            )
            parameters["selected_min_edge"] = self._clip_bounds(
                genome.mutation_bounds.execution_thresholds.get("min_edge") or [0.01, 0.1],
                max(0.01, current_min_edge - 0.005),
                fallback=current_min_edge,
            )
        else:
            parameters["selected_feature_subset"] = self._rotate_choice(
                genome.mutation_bounds.feature_subsets,
                current_feature,
                step=tweak_number,
                fallback="baseline",
            )
            parameters["selected_horizon_seconds"] = self._nearest_choice(
                genome.mutation_bounds.horizons_seconds,
                current_horizon + (300 * tweak_number),
            )
            parameters["selected_lookback_hours"] = self._clip_bounds(
                genome.mutation_bounds.hyperparameter_ranges.get("lookback_hours") or [6.0, 168.0],
                max(6.0, current_lookback - 6.0),
                fallback=current_lookback,
            )
        parameters["mutation_source"] = "underperformance_tweak"
        parameters["last_tweak_reason"] = reason
        parameters["last_tweak_cycle"] = self._cycle_count
        agent_decision = None
        if agent_result is not None:
            parameters["last_tweak_agent_provider"] = agent_result.provider
            parameters["last_tweak_agent_model"] = agent_result.model
            parameters["last_tweak_agent_task_type"] = agent_result.task_type
            parameters["last_tweak_agent_task_class"] = agent_result.model_class
            parameters["last_tweak_agent_artifact_path"] = agent_result.artifact_path
            agent_decision = self._lineage_agent_decision(
                result=agent_result,
                kind="tweak",
                used_real_agent=self._real_agent_used(agent_result),
            )
            parameters["last_agent_decision"] = agent_decision
        genome.parameters = parameters
        self.registry.save_genome(lineage.lineage_id, genome)
        lineage.tweak_count = tweak_number
        lineage.loss_streak = int(lineage.loss_streak or 0) + 1
        lineage.iteration_status = "tweaked"
        lineage.updated_at = utc_now_iso()
        self.registry.save_lineage(lineage)
        if hypothesis is not None:
            hypothesis.agent_notes = list(hypothesis.agent_notes) + [
                f"tweak_{tweak_number}: {reason}",
            ] + list((agent_result.result_payload.get("agent_notes") if self._real_agent_used(agent_result) else []) or [])
            self.registry.save_hypothesis(lineage.lineage_id, hypothesis)
        if experiment is not None:
            experiment.inputs = dict(experiment.inputs or {})
            experiment.inputs["tweak_count"] = lineage.tweak_count
            experiment.inputs["last_tweak_reason"] = reason
            if agent_decision is not None:
                experiment.inputs["last_tweak_agent"] = agent_decision
            self.registry.save_experiment(lineage.lineage_id, experiment)
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Tweaked {lineage.lineage_id} ({tweak_number}/{int(lineage.max_tweaks or 2)}) after {reason} via {agent_result.provider if agent_result is not None else 'deterministic'}.",
        )

    def _record_learning_memory(
        self,
        lineage: LineageRecord,
        row: Dict[str, Any],
        *,
        reason: str,
    ) -> None:
        hypothesis = self.registry.load_hypothesis(lineage.lineage_id)
        execution_evidence = dict(row.get("execution_validation") or {})
        summary = (
            f"{lineage.lineage_id} retired after {int(lineage.tweak_count or 0)} tweaks. "
            f"ROI={float(row.get('monthly_roi_pct', 0.0) or 0.0):.4f}, "
            f"fitness={float(row.get('fitness_score', 0.0) or 0.0):.4f}, "
            f"reason={reason}."
        )
        recommendations = []
        if row.get("hard_vetoes"):
            recommendations.append(f"avoid veto pattern {list(row.get('hard_vetoes') or [reason])[0]}")
        if float(row.get("calibration_lift_abs", 0.0) or 0.0) <= 0.0:
            recommendations.append("prefer higher-information or microstructure features next")
        if float(row.get("monthly_roi_pct", 0.0) or 0.0) < 0.0:
            recommendations.append("tighten edge thresholds and reduce stake fraction in successor")
        recommendations.extend(str(item) for item in (execution_evidence.get("recommendation_context") or []) if str(item).strip())
        memory = LearningMemoryEntry(
            memory_id=f"{lineage.lineage_id}:memory:{int(lineage.tweak_count or 0)}",
            family_id=lineage.family_id,
            lineage_id=lineage.lineage_id,
            hypothesis_id=lineage.hypothesis_id,
            outcome="retired_underperformance",
            summary=summary,
            scientific_domains=list((hypothesis.scientific_domains if hypothesis else []) or []),
            lead_agent_role=str((hypothesis.lead_agent_role if hypothesis else "unknown") or "unknown"),
            tweak_count=int(lineage.tweak_count or 0),
            decision_stage=lineage.current_stage,
            metrics={
                "monthly_roi_pct": float(row.get("monthly_roi_pct", 0.0) or 0.0),
                "fitness_score": float(row.get("fitness_score", 0.0) or 0.0),
                "pareto_rank": row.get("pareto_rank"),
            },
            execution_evidence={
                "health_status": execution_evidence.get("health_status"),
                "issue_codes": list(execution_evidence.get("issue_codes") or []),
                "recommendation_context": list(execution_evidence.get("recommendation_context") or []),
                "summary": execution_evidence.get("summary"),
            },
            blockers=list(row.get("hard_vetoes") or []),
            recommendations=recommendations or ["change scientific collaboration mix before retrying"],
            evidence_sources=[str(item.get("resolved_target") or item.get("requested_target") or "") for item in (execution_evidence.get("targets") or []) if str(item.get("resolved_target") or item.get("requested_target") or "").strip()],
        )
        self.registry.save_learning_memory(memory)
        lineage.last_memory_id = memory.memory_id

    def _retire_or_update_lineages(
        self,
        family: FactoryFamily,
        ranked_rows: List[Dict[str, Any]],
        *,
        recent_actions: List[str],
    ) -> None:
        active_ranked = [row for row in ranked_rows if row.get("active", True)]
        if not active_ranked:
            return
        champion_row = active_ranked[0]
        champion_score = float(champion_row.get("fitness_score", 0.0) or 0.0)
        champion_curated_score = float(champion_row.get("curated_ranking_score", 0.0) or 0.0)
        retired_ids = set(family.retired_lineage_ids)
        for row in active_ranked[1:]:
            lineage = self.registry.load_lineage(str(row["lineage_id"]))
            if lineage is None or not lineage.active:
                continue
            execution_signal_ready = bool(row.get("execution_has_signal"))
            if not execution_signal_ready:
                lineage.blockers = list(dict.fromkeys(list(lineage.blockers) + ["awaiting_execution_validation"]))
                lineage.iteration_status = "awaiting_execution_validation"
                self.registry.save_lineage(lineage)
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Deferred tweak/retirement for {lineage.lineage_id} until execution validation is present.",
                )
                continue
            monthly_roi = float(row.get("monthly_roi_pct", 0.0) or 0.0)
            hard_vetoes = list(row.get("hard_vetoes") or [])
            score = float(row.get("fitness_score", 0.0) or 0.0)
            execution_issue_codes = {str(item) for item in (row.get("execution_issue_codes") or []) if str(item).strip()}
            curated_rank = int(row.get("curated_family_rank", 0) or 0)
            curated_score = float(row.get("curated_ranking_score", 0.0) or 0.0)
            curated_paper_roi = float(row.get("curated_paper_roi_pct", 0.0) or 0.0)
            ranking_gap = (champion_curated_score - curated_score) if champion_curated_score and curated_score else 0.0
            forced_maintenance = lineage.iteration_status in {"review_requested_rework", "review_requested_replace"}
            underperforming = (
                forced_maintenance
                or bool(hard_vetoes)
                or (score < (champion_score - 0.25))
                or monthly_roi < 0.0
                or curated_paper_roi < -0.25
                or (curated_rank > 1 and ranking_gap >= 4.0)
                or bool(execution_issue_codes.intersection({"stalled_model", "trade_stalled", "training_stalled", "untrainable_model"}))
            )
            if underperforming:
                if int(lineage.tweak_count or 0) < int(lineage.max_tweaks or 2):
                    self._tweak_lineage_for_underperformance(
                        lineage,
                        row,
                        recent_actions=recent_actions,
                    )
                    continue
                lineage.loss_streak = int(lineage.loss_streak or 0) + 1
            else:
                lineage.loss_streak = 0
                if lineage.iteration_status == "tweaked":
                    lineage.iteration_status = "stabilized_after_tweak"
            if underperforming and int(lineage.tweak_count or 0) >= int(lineage.max_tweaks or 2):
                lineage.active = False
                lineage.retired_at = utc_now_iso()
                lineage.iteration_status = "retired"
                lineage.retirement_reason = "max_tweaks_exhausted_underperforming"
                lineage.blockers = list(dict.fromkeys(list(lineage.blockers) + [lineage.retirement_reason]))
                self._record_learning_memory(
                    lineage,
                    row,
                    reason=hard_vetoes[0] if hard_vetoes else "score_and_roi_underperformance",
                )
                retired_ids.add(lineage.lineage_id)
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Retired {lineage.lineage_id} from {family.family_id}: {lineage.retirement_reason}.",
                )
            self.registry.save_lineage(lineage)
        family.retired_lineage_ids = sorted(retired_ids)
        family.shadow_challenger_ids = [lineage_id for lineage_id in family.shadow_challenger_ids if lineage_id not in retired_ids]
        family.paper_challenger_ids = [lineage_id for lineage_id in family.paper_challenger_ids if lineage_id not in retired_ids]
        self.registry.save_family(family)

    def _connector_snapshots(self) -> List[Dict[str, Any]]:
        return [adapter.snapshot().to_dict() for adapter in self.connectors]

    def _curated_family_ranking_summary(self, family_id: str) -> Dict[str, Any]:
        rankings = family_model_rankings(family_id)
        if rankings.empty:
            return {"by_lineage": {}, "top_lineages": []}
        grouped: Dict[str, Dict[str, Any]] = {}
        for _, row in rankings.iterrows():
            lineage_id = str(row.get("lineage_id") or "")
            if not lineage_id:
                continue
            summary = grouped.setdefault(
                lineage_id,
                {
                    "lineage_id": lineage_id,
                    "family_rank": None,
                    "ranking_score": float("-inf"),
                    "target_portfolio_id": "",
                    "paper_roi_pct": 0.0,
                    "paper_realized_pnl": 0.0,
                    "paper_win_rate": 0.0,
                    "paper_closed_trade_count": 0,
                    "current_stage": str(row.get("current_stage") or ""),
                    "role": str(row.get("role") or ""),
                    "strict_gate_pass": bool(row.get("strict_gate_pass", False)),
                },
            )
            rank_value = row.get("family_rank")
            if rank_value is not None:
                rank_int = int(rank_value)
                prior_rank = summary.get("family_rank")
                if prior_rank is None or rank_int < int(prior_rank):
                    summary["family_rank"] = rank_int
            ranking_score = float(row.get("ranking_score") or 0.0)
            if ranking_score >= float(summary.get("ranking_score", float("-inf"))):
                summary.update(
                    {
                        "ranking_score": ranking_score,
                        "target_portfolio_id": str(row.get("target_portfolio_id") or ""),
                        "paper_roi_pct": float(row.get("paper_roi_pct") or 0.0),
                        "paper_realized_pnl": float(row.get("paper_realized_pnl") or 0.0),
                        "paper_win_rate": float(row.get("paper_win_rate") or 0.0),
                        "paper_closed_trade_count": int(row.get("paper_closed_trade_count") or 0),
                        "current_stage": str(row.get("current_stage") or summary.get("current_stage") or ""),
                        "role": str(row.get("role") or summary.get("role") or ""),
                        "strict_gate_pass": bool(row.get("strict_gate_pass", summary.get("strict_gate_pass", False))),
                    }
                )
        summaries = sorted(
            grouped.values(),
            key=lambda item: (
                item.get("family_rank") if item.get("family_rank") is not None else 999,
                -float(item.get("ranking_score", 0.0) or 0.0),
            ),
        )
        return {
            "by_lineage": {str(item["lineage_id"]): dict(item) for item in summaries},
            "top_lineages": [dict(item) for item in summaries[:5]],
        }

    def _execution_validation_snapshot(self, lineage: LineageRecord) -> Dict[str, Any]:
        snapshot = summarize_execution_targets(lineage.target_portfolios)
        rankings = family_model_rankings(lineage.family_id)
        if not rankings.empty:
            snapshot["family_rankings"] = [
                {
                    "family_rank": int(row.get("family_rank") or 0),
                    "lineage_id": str(row.get("lineage_id") or ""),
                    "target_portfolio_id": str(row.get("target_portfolio_id") or ""),
                    "role": str(row.get("role") or ""),
                    "current_stage": str(row.get("current_stage") or ""),
                    "strict_gate_pass": bool(row.get("strict_gate_pass", False)),
                    "lineage_monthly_roi_pct": float(row.get("lineage_monthly_roi_pct") or 0.0),
                    "lineage_fitness_score": float(row.get("lineage_fitness_score") or 0.0),
                    "paper_roi_pct": float(row.get("paper_roi_pct") or 0.0),
                    "paper_win_rate": float(row.get("paper_win_rate") or 0.0),
                    "ranking_score": float(row.get("ranking_score") or 0.0),
                }
                for _, row in rankings.head(5).iterrows()
            ]
        scorecards = portfolio_scorecards(lineage.target_portfolios)
        if not scorecards.empty:
            snapshot["portfolio_scorecards"] = [
                {
                    "portfolio_id": str(row.get("portfolio_id") or ""),
                    "status": str(row.get("status") or ""),
                    "roi_pct": float(row.get("roi_pct") or 0.0),
                    "realized_pnl": float(row.get("realized_pnl") or 0.0),
                    "closed_trade_count": int(row.get("closed_trade_count") or 0),
                    "win_rate": float(row.get("win_rate") or 0.0),
                    "last_trade_ts": str(row.get("last_trade_ts") or ""),
                }
                for _, row in scorecards.iterrows()
            ]
        return snapshot

    def _select_live_paper_target(
        self,
        *,
        lineage: LineageRecord,
        execution_validation: Dict[str, Any],
        curated_target_portfolio_id: str | None,
    ) -> Dict[str, Any]:
        targets = [dict(item) for item in (execution_validation.get("targets") or []) if isinstance(item, dict)]
        preferred_ids = [
            str(curated_target_portfolio_id or "").strip(),
            *[str(item or "").strip() for item in list(lineage.target_portfolios or [])],
        ]
        preferred_ids = [item for item in preferred_ids if item]
        for preferred_id in preferred_ids:
            for row in targets:
                requested = str(row.get("requested_target") or "").strip()
                resolved = str(row.get("resolved_target") or "").strip()
                if preferred_id in {requested, resolved}:
                    return row
        if len(targets) == 1:
            return targets[0]
        for row in targets:
            if bool(row.get("running")):
                return row
        return {}

    def _operator_signals(self, lineage_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        positive_models: List[Dict[str, Any]] = []
        research_positive_models: List[Dict[str, Any]] = []
        potential_winners: List[Dict[str, Any]] = []
        escalation_candidates: List[Dict[str, Any]] = []
        human_action_required: List[Dict[str, Any]] = []
        maintenance_queue: List[Dict[str, Any]] = []
        min_paper_days = int(getattr(config, "FACTORY_PAPER_GATE_MIN_DAYS", 30))
        min_fast_trades = int(getattr(config, "FACTORY_PAPER_GATE_MIN_FAST_TRADES", 50))
        min_slow_settled = int(getattr(config, "FACTORY_PAPER_GATE_MIN_SLOW_SETTLED", 10))
        for row in lineage_summaries:
            slow_strategy = "slow" in str(row.get("family_id") or "") or "polymarket" in str(row.get("family_id") or "")
            live_roi = float(row.get("live_paper_roi_pct", 0.0) or 0.0)
            live_trade_count = int(row.get("live_paper_trade_count", 0) or 0)
            research_roi = float(row.get("research_monthly_roi_pct", row.get("monthly_roi_pct", 0.0)) or 0.0)
            research_trade_count = int(row.get("research_trade_count", row.get("trade_count", 0)) or 0)
            health_status = str(row.get("execution_health_status") or "")
            required_trades = min_slow_settled if slow_strategy else min_fast_trades
            assessment_complete = int(row.get("paper_days", 0) or 0) >= min_paper_days and live_trade_count >= required_trades
            curated_target_portfolio_id = str(row.get("curated_target_portfolio_id") or "")
            live_target_portfolio_id = str(row.get("live_paper_target_portfolio_id") or "")
            evidence_source_type = "current_live_paper" if live_target_portfolio_id else "no_live_paper_evidence"
            if live_roi > 0.0:
                positive_models.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "roi_pct": round(live_roi, 4),
                        "trade_count": live_trade_count,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "execution_health_status": health_status,
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": live_target_portfolio_id or None,
                        "evidence_source_type": evidence_source_type,
                        "assessment_complete": assessment_complete,
                        "manifest_id": row.get("manifest_id"),
                        "research_roi_pct": round(research_roi, 4),
                        "research_trade_count": research_trade_count,
                    }
                )
            if research_roi > 0.0:
                research_positive_models.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "roi_pct": round(research_roi, 4),
                        "trade_count": research_trade_count,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "execution_health_status": health_status,
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": curated_target_portfolio_id or None,
                        "evidence_source_type": (
                            "shared_portfolio_scorecard"
                            if curated_target_portfolio_id and int(row.get("curated_paper_closed_trade_count", 0) or 0) > 0
                            else "lineage_evaluation"
                        ),
                        "assessment_complete": False,
                        "manifest_id": row.get("manifest_id"),
                        "live_roi_pct": round(live_roi, 4),
                        "live_trade_count": live_trade_count,
                    }
                )
            is_potential_winner = (
                str(row.get("current_stage") or "") in {PromotionStage.CANARY_READY.value, PromotionStage.LIVE_READY.value}
                and bool(row.get("strict_gate_pass"))
                and assessment_complete
                and live_roi > 0.0
                and health_status not in {"critical"}
                and (row.get("curated_family_rank") in {1, None})
                and evidence_source_type == "current_live_paper"
            )
            if is_potential_winner:
                potential_winners.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "roi_pct": round(live_roi, 4),
                        "trade_count": live_trade_count,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "curated_family_rank": row.get("curated_family_rank"),
                        "manifest_id": row.get("manifest_id"),
                        "assessment_complete": True,
                    }
                )
                escalation_candidates.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "target_action": "operator_review_for_real_trading",
                        "reason": "live_ready leader with positive live paper ROI and healthy execution",
                        "roi_pct": round(live_roi, 4),
                        "trade_count": live_trade_count,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": live_target_portfolio_id or None,
                        "evidence_source_type": evidence_source_type,
                        "manifest_id": row.get("manifest_id"),
                    }
                )
            if bool(row.get("last_debug_requires_human")):
                human_action_required.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "execution_health_status": health_status,
                        "bug_category": str(row.get("last_debug_bug_category") or ""),
                        "summary": str(row.get("last_debug_summary") or ""),
                        "human_action": str(row.get("last_debug_human_action") or ""),
                        "issue_codes": list(row.get("execution_issue_codes") or []),
                        "artifact_path": row.get("last_debug_review_artifact_path"),
                        "reviewed_at": row.get("last_debug_review_at"),
                    }
                )
            maintenance_action = str(row.get("maintenance_request_action") or "").strip().lower()
            if maintenance_action:
                maintenance_queue.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "action": maintenance_action,
                        "reason": str(row.get("maintenance_request_reason") or ""),
                        "source": str(row.get("maintenance_request_source") or ""),
                        "priority": {
                            "human_action_required": 0,
                            "retire": 1,
                            "replace": 2,
                            "rework": 3,
                            "retrain": 4,
                        }.get(maintenance_action, 5),
                        "execution_health_status": health_status,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "trade_count": live_trade_count,
                        "roi_pct": round(live_roi, 4),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "requires_human": maintenance_action == "human_action_required",
                    }
                )
            elif bool(row.get("agent_review_due")):
                maintenance_queue.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "action": "review_due",
                        "reason": str(row.get("agent_review_due_reason") or "scheduled review due"),
                        "source": "review_scheduler",
                        "priority": 6,
                        "execution_health_status": health_status,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "trade_count": live_trade_count,
                        "roi_pct": round(live_roi, 4),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "requires_human": False,
                    }
                )
        positive_models.sort(
            key=lambda item: (-float(item.get("roi_pct", 0.0) or 0.0), -int(item.get("trade_count", 0) or 0))
        )
        research_positive_models.sort(
            key=lambda item: (-float(item.get("roi_pct", 0.0) or 0.0), -int(item.get("trade_count", 0) or 0))
        )
        potential_winners.sort(
            key=lambda item: (-float(item.get("roi_pct", 0.0) or 0.0), -int(item.get("trade_count", 0) or 0))
        )
        escalation_candidates.sort(
            key=lambda item: (-float(item.get("roi_pct", 0.0) or 0.0), -int(item.get("paper_days", 0) or 0))
        )
        human_action_required.sort(
            key=lambda item: (
                0 if item.get("execution_health_status") == "critical" else 1,
                item.get("family_id") or "",
                item.get("lineage_id") or "",
            )
        )
        maintenance_queue.sort(
            key=lambda item: (
                int(item.get("priority", 9)) if item.get("priority") is not None else 9,
                0 if item.get("execution_health_status") == "critical" else 1,
                item.get("family_id") or "",
                item.get("lineage_id") or "",
            )
        )
        return {
            "positive_models": positive_models[:12],
            "research_positive_models": research_positive_models[:12],
            "potential_winners": potential_winners[:8],
            "escalation_candidates": escalation_candidates[:8],
            "human_action_required": human_action_required[:8],
            "maintenance_queue": maintenance_queue[:16],
        }

    def _operator_action_key(self, *, signal_type: str, lineage_id: str, requested_action: str) -> str:
        digest = hashlib.sha1(f"{signal_type}|{lineage_id}|{requested_action}".encode("utf-8")).hexdigest()[:12]
        return f"{signal_type}:{lineage_id}:{requested_action}:{digest}"

    def _sync_operator_actions(self, operator_signals: Dict[str, Any]) -> Dict[str, Any]:
        inbox: List[Dict[str, Any]] = []
        for item in list(operator_signals.get("human_action_required") or []):
            action = self.registry.open_operator_action(
                action_key=self._operator_action_key(
                    signal_type="human_action_required",
                    lineage_id=str(item.get("lineage_id") or ""),
                    requested_action="human_action_required",
                ),
                family_id=str(item.get("family_id") or ""),
                lineage_id=str(item.get("lineage_id") or ""),
                signal_type="human_action_required",
                requested_action="human_action_required",
                summary=str(item.get("human_action") or item.get("summary") or "Operator action required"),
                context=dict(item),
            )
            row = action.to_dict()
            row["available_decisions"] = ["approve", "reject", "instruct"]
            inbox.append(row)
            item["operator_action_id"] = action.action_id
        for item in list(operator_signals.get("escalation_candidates") or []):
            action = self.registry.open_operator_action(
                action_key=self._operator_action_key(
                    signal_type="winner_review",
                    lineage_id=str(item.get("lineage_id") or ""),
                    requested_action="approve_real_trading_review",
                ),
                family_id=str(item.get("family_id") or ""),
                lineage_id=str(item.get("lineage_id") or ""),
                signal_type="winner_review",
                requested_action="approve_real_trading_review",
                summary=str(item.get("reason") or "Review this model before any real-trading push."),
                context=dict(item),
            )
            row = action.to_dict()
            row["available_decisions"] = ["approve", "reject", "instruct"]
            inbox.append(row)
            item["operator_action_id"] = action.action_id
        operator_signals["action_inbox"] = sorted(
            inbox,
            key=lambda item: (
                0 if item.get("signal_type") == "human_action_required" else 1,
                str(item.get("family_id") or ""),
                str(item.get("lineage_id") or ""),
            ),
        )[:24]
        return operator_signals

    def _run_experiment(self, lineage: LineageRecord) -> Dict[str, Any]:
        genome = self.registry.load_genome(lineage.lineage_id)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if genome is None or experiment is None:
            return {"mode": "missing_inputs", "bundles": [], "artifact_summary": None}
        experiment.inputs = dict(experiment.inputs or {})
        execution_validation = self._execution_validation_snapshot(lineage)
        experiment.inputs["execution_retrain_context"] = execution_validation
        maintenance_request = self._maintenance_request(lineage, execution_validation)
        if maintenance_request:
            experiment.inputs["maintenance_request"] = maintenance_request
        else:
            experiment.inputs.pop("maintenance_request", None)
        self.registry.save_experiment(lineage.lineage_id, experiment)
        result = self.experiment_runner.run(
            lineage=lineage,
            genome=genome,
            experiment=experiment,
        )
        artifact_summary = dict(result.get("artifact_summary") or {})
        if artifact_summary:
            experiment.expected_outputs = dict(experiment.expected_outputs or {})
            experiment.expected_outputs["latest_run"] = artifact_summary
            self.registry.save_experiment(lineage.lineage_id, experiment)
        return result

    def _maintenance_request(
        self,
        lineage: LineageRecord,
        execution_validation: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if bool(lineage.last_debug_requires_human):
            return {
                "source": "debug_agent",
                "action": "human_action_required",
                "reason": str(
                    lineage.last_debug_human_action
                    or lineage.last_debug_summary
                    or lineage.last_debug_bug_category
                    or "operator intervention required"
                ),
                "requires_human": True,
                "bug_category": lineage.last_debug_bug_category,
            }
        review_action = str(lineage.last_agent_review_action or "").strip().lower()
        if str(lineage.last_agent_review_status or "") == "completed" and review_action in {
            "retrain",
            "rework",
            "replace",
            "retire",
        }:
            return {
                "source": "agent_review",
                "action": review_action,
                "reason": str(lineage.last_agent_review_summary or lineage.last_agent_review_reason or "agent review"),
                "requires_human": False,
                "requires_new_challenger": review_action == "replace",
            }
        issue_codes = {str(item) for item in (execution_validation.get("issue_codes") or []) if str(item).strip()}
        if issue_codes.intersection({"untrainable_model", "training_stalled"}):
            return {
                "source": "execution_policy",
                "action": "retrain",
                "reason": "Execution evidence says the required learner is untrainable or training has stalled.",
                "requires_human": False,
                "issue_codes": sorted(issue_codes.intersection({"untrainable_model", "training_stalled"})),
            }
        if issue_codes.intersection({"stalled_model", "trade_stalled"}):
            return {
                "source": "execution_policy",
                "action": "rework",
                "reason": "Execution evidence says the model has stalled and needs a maintenance rework cycle now.",
                "requires_human": False,
                "issue_codes": sorted(issue_codes.intersection({"stalled_model", "trade_stalled"})),
            }
        return None

    def _blend_runtime_and_offline_bundle(
        self,
        *,
        lineage: LineageRecord,
        runtime_bundle: EvaluationBundle,
        walkforward_bundle: Optional[EvaluationBundle],
        stress_bundle: Optional[EvaluationBundle],
        stage: str,
    ) -> EvaluationBundle:
        if walkforward_bundle is None and stress_bundle is None:
            return runtime_bundle
        walkforward = walkforward_bundle or runtime_bundle
        stress = stress_bundle or walkforward
        artifact_notes = [
            note
            for note in list(walkforward.notes) + list(stress.notes)
            if "package_path=" in str(note)
        ]
        return replace(
            runtime_bundle,
            evaluation_id=f"{runtime_bundle.evaluation_id}:{stage}:artifact",
            stage=stage,
            source="factory_runtime_plus_artifact",
            windows=list(walkforward.windows or runtime_bundle.windows),
            monthly_roi_pct=round(
                (float(runtime_bundle.monthly_roi_pct) * 0.65) + (float(walkforward.monthly_roi_pct) * 0.35),
                4,
            ),
            max_drawdown_pct=round(
                max(float(runtime_bundle.max_drawdown_pct), float(walkforward.max_drawdown_pct)),
                4,
            ),
            slippage_headroom_pct=round(
                min(float(runtime_bundle.slippage_headroom_pct), float(stress.slippage_headroom_pct)),
                4,
            ),
            calibration_lift_abs=round(
                float(runtime_bundle.calibration_lift_abs) + (float(walkforward.calibration_lift_abs) * 0.5),
                6,
            ),
            turnover=round(
                max(float(runtime_bundle.turnover), float(walkforward.turnover)),
                4,
            ),
            capacity_score=round(
                min(1.0, (float(runtime_bundle.capacity_score) * 0.5) + (float(walkforward.capacity_score) * 0.5)),
                4,
            ),
            failure_rate=round(
                max(float(runtime_bundle.failure_rate), float(stress.failure_rate)),
                4,
            ),
            regime_robustness=round(
                min(1.0, (float(runtime_bundle.regime_robustness) * 0.5) + (float(walkforward.regime_robustness) * 0.5)),
                4,
            ),
            baseline_beaten_windows=max(
                int(runtime_bundle.baseline_beaten_windows),
                int(walkforward.baseline_beaten_windows),
            ),
            stress_positive=bool(stress.stress_positive and runtime_bundle.slippage_headroom_pct > 0.0),
            trade_count=max(int(runtime_bundle.trade_count), int(walkforward.trade_count)),
            settled_count=max(int(runtime_bundle.settled_count), int(walkforward.settled_count)),
            paper_days=max(int(runtime_bundle.paper_days), int(walkforward.paper_days)),
            net_pnl=round(
                float(runtime_bundle.net_pnl) + (float(walkforward.net_pnl) * 0.25),
                4,
            ),
            notes=list(dict.fromkeys(list(runtime_bundle.notes) + artifact_notes + [f"artifact_lineage={lineage.lineage_id}"])),
        )

    def _workspace_status(self, families: Iterable[FactoryFamily]) -> Dict[str, Dict[str, Any]]:
        status: Dict[str, Dict[str, Any]] = {}
        for family in families:
            status[family.family_id] = self.bridge.ensure_workspace(
                family.family_id,
                thesis=family.thesis,
                pipeline_stages=["dataset", "features", "train", "walkforward", "stress", "package"],
            )
        return status

    def _prediction_paper_bundle(self, lineage: LineageRecord) -> Optional[EvaluationBundle]:
        log_path = self.project_root / "data/prediction/experiments.jsonl"
        rows = _load_jsonl(log_path)
        if not rows:
            return None
        rows = rows[-25:]
        last = rows[-1]
        metrics = dict(last.get("metrics") or {})
        rolling = metrics.get("rolling_200") or metrics.get("rolling_100") or {}
        settled = int(rolling.get("settled", 0) or 0)
        monthly_roi = float(rolling.get("roi_pct", 0.0) or 0.0)
        brier_lift = float(rolling.get("brier_lift_abs", 0.0) or 0.0)
        windows = [
            EvaluationWindow(
                label="prediction_rolling",
                settled_count=settled,
                monthly_roi_pct=monthly_roi,
                baseline_roi_pct=0.0,
                brier_lift_abs=brier_lift,
                drawdown_pct=abs(min(0.0, monthly_roi)) * 0.8,
                slippage_headroom_pct=max(-5.0, monthly_roi - 1.5),
                failure_rate=0.02,
                regime_robustness=0.35,
            )
        ]
        stage = EvaluationStage.PAPER.value if settled else EvaluationStage.WALKFORWARD.value
        return EvaluationBundle(
            evaluation_id=f"{lineage.lineage_id}:prediction:{settled}",
            lineage_id=lineage.lineage_id,
            family_id=lineage.family_id,
            stage=stage,
            source="repo_prediction_experiments",
            windows=windows,
            monthly_roi_pct=monthly_roi,
            max_drawdown_pct=abs(min(0.0, monthly_roi)) * 0.8,
            slippage_headroom_pct=max(-5.0, monthly_roi - 1.5),
            calibration_lift_abs=brier_lift,
            turnover=min(1.0, settled / 200.0),
            capacity_score=0.45,
            failure_rate=0.02,
            regime_robustness=0.35,
            baseline_beaten_windows=3 if brier_lift > 0 and monthly_roi > 0 else 0,
            stress_positive=monthly_roi > 2.0,
            trade_count=settled,
            settled_count=settled,
            paper_days=min(30, max(1, settled // 4)),
            net_pnl=monthly_roi,
        )

    def _funding_bundle(self, lineage: LineageRecord) -> Optional[EvaluationBundle]:
        log_path = self.project_root / "data/funding/experiments.jsonl"
        rows = _load_jsonl(log_path)
        if not rows:
            return None
        rows = rows[-25:]
        last = rows[-1]
        metrics = dict(last.get("metrics") or {})
        rolling = metrics.get("rolling_200") or metrics.get("rolling_100") or {}
        settled = int(rolling.get("settled", 0) or 0)
        monthly_roi = float(rolling.get("roi_pct", 0.0) or 0.0)
        brier_lift = float(rolling.get("brier_lift_abs", 0.0) or 0.0)
        windows = [
            EvaluationWindow(
                label="funding_rolling",
                settled_count=settled,
                monthly_roi_pct=monthly_roi,
                baseline_roi_pct=0.0,
                brier_lift_abs=brier_lift,
                drawdown_pct=max(0.0, 6.0 - monthly_roi),
                slippage_headroom_pct=max(-2.0, monthly_roi - 0.5),
                failure_rate=0.01,
                regime_robustness=0.60,
            )
        ]
        return EvaluationBundle(
            evaluation_id=f"{lineage.lineage_id}:funding:{settled}",
            lineage_id=lineage.lineage_id,
            family_id=lineage.family_id,
            stage=EvaluationStage.PAPER.value if settled else EvaluationStage.WALKFORWARD.value,
            source="repo_funding_experiments",
            windows=windows,
            monthly_roi_pct=monthly_roi,
            max_drawdown_pct=max(0.0, 6.0 - monthly_roi),
            slippage_headroom_pct=max(-2.0, monthly_roi - 0.5),
            calibration_lift_abs=brier_lift,
            turnover=min(1.0, settled / 150.0),
            capacity_score=0.65,
            failure_rate=0.01,
            regime_robustness=0.60,
            baseline_beaten_windows=3 if brier_lift > 0 and monthly_roi >= 5.0 else 1,
            stress_positive=monthly_roi > 0.0,
            trade_count=settled,
            settled_count=settled,
            paper_days=min(30, max(1, settled // 4)),
            net_pnl=monthly_roi,
        )

    def _portfolio_state_bundle(
        self,
        lineage: LineageRecord,
        *,
        portfolio_id: str,
        stage: str,
        capacity_score: float,
        regime_robustness: float,
    ) -> Optional[EvaluationBundle]:
        store = PortfolioStateStore(portfolio_id)
        account = store.read_account()
        if account is None:
            return None
        trades = store.read_trades(limit=500)
        settled = sum(1 for trade in trades if str(trade.get("status", "")).upper() in {"CLOSED", "SETTLED"})
        trade_count = len(trades)
        monthly_roi = float(account.roi_pct)
        drawdown = float(account.drawdown_pct)
        windows = [
            EvaluationWindow(
                label=f"{portfolio_id}_runtime",
                settled_count=max(settled, trade_count),
                monthly_roi_pct=monthly_roi,
                baseline_roi_pct=0.0,
                brier_lift_abs=0.0,
                drawdown_pct=drawdown,
                slippage_headroom_pct=max(-5.0, monthly_roi - 2.0),
                failure_rate=0.01 if trade_count else 0.05,
                regime_robustness=regime_robustness,
            )
        ]
        return EvaluationBundle(
            evaluation_id=f"{lineage.lineage_id}:{portfolio_id}:{trade_count}",
            lineage_id=lineage.lineage_id,
            family_id=lineage.family_id,
            stage=stage,
            source=f"portfolio_state:{portfolio_id}",
            windows=windows,
            monthly_roi_pct=monthly_roi,
            max_drawdown_pct=drawdown,
            slippage_headroom_pct=max(-5.0, monthly_roi - 2.0),
            calibration_lift_abs=0.0,
            turnover=min(1.0, trade_count / 100.0),
            capacity_score=capacity_score,
            failure_rate=0.01 if trade_count else 0.05,
            regime_robustness=regime_robustness,
            baseline_beaten_windows=3 if monthly_roi > 0 else 0,
            stress_positive=monthly_roi > 0.0,
            trade_count=trade_count,
            settled_count=max(settled, trade_count),
            paper_days=min(30, max(1, trade_count // 2)) if trade_count else 0,
            net_pnl=float(account.realized_pnl),
        )

    def _collect_evidence(self, lineage: LineageRecord) -> List[EvaluationBundle]:
        bundles: List[EvaluationBundle] = []
        experiment_result = self._run_experiment(lineage)
        experiment_bundles = list(experiment_result.get("bundles") or [])
        experiment_by_stage = {bundle.stage: bundle for bundle in experiment_bundles}
        if lineage.family_id == "binance_funding_contrarian":
            bundles.extend(experiment_bundles)
            bundle = self._funding_bundle(lineage)
            runtime_bundle = self._portfolio_state_bundle(
                lineage,
                portfolio_id="contrarian_legacy",
                stage=EvaluationStage.PAPER.value,
                capacity_score=0.55,
                regime_robustness=0.55,
            )
            if runtime_bundle:
                bundles.extend(
                    [
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=runtime_bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.SHADOW.value,
                        ),
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=runtime_bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.PAPER.value,
                        ),
                    ]
                )
            elif bundle:
                bundles.extend(
                    [
                        replace(bundle, stage=EvaluationStage.WALKFORWARD.value, evaluation_id=f"{bundle.evaluation_id}:wf"),
                        replace(bundle, stage=EvaluationStage.STRESS.value, evaluation_id=f"{bundle.evaluation_id}:stress"),
                        replace(bundle, stage=EvaluationStage.PAPER.value, evaluation_id=f"{bundle.evaluation_id}:paper"),
                    ]
                )
        elif lineage.family_id == "binance_cascade_regime":
            bundles.extend(experiment_bundles)
            runtime_bundle = self._portfolio_state_bundle(
                lineage,
                portfolio_id="cascade_alpha",
                stage=EvaluationStage.PAPER.value,
                capacity_score=0.50,
                regime_robustness=0.65,
            )
            if runtime_bundle:
                bundles.extend(
                    [
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=runtime_bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.SHADOW.value,
                        ),
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=runtime_bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.PAPER.value,
                        ),
                    ]
                )
        elif lineage.family_id == "betfair_prediction_value_league":
            bundles.extend(experiment_bundles)
            bundle = self._prediction_paper_bundle(lineage)
            if bundle:
                shadow_bundle = self._blend_runtime_and_offline_bundle(
                    lineage=lineage,
                    runtime_bundle=bundle,
                    walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                    stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                    stage=EvaluationStage.SHADOW.value,
                )
                paper_bundle = self._blend_runtime_and_offline_bundle(
                    lineage=lineage,
                    runtime_bundle=bundle,
                    walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                    stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                    stage=EvaluationStage.PAPER.value,
                )
                bundles.extend(
                    [
                        shadow_bundle,
                        paper_bundle,
                    ]
                )
        elif lineage.family_id == "betfair_information_lag":
            bundles.extend(experiment_bundles)
            bundle = self._portfolio_state_bundle(
                lineage,
                portfolio_id="betfair_core",
                stage=EvaluationStage.SHADOW.value,
                capacity_score=0.30,
                regime_robustness=0.45,
            )
            if bundle:
                bundles.extend(
                    [
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=replace(bundle, stage=EvaluationStage.PAPER.value),
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.SHADOW.value,
                        ),
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=replace(bundle, stage=EvaluationStage.PAPER.value),
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.PAPER.value,
                        ),
                    ]
                )
        elif lineage.family_id == "polymarket_cross_venue":
            bundles.extend(experiment_bundles)
            bundle = self._portfolio_state_bundle(
                lineage,
                portfolio_id="polymarket_quantum_fold",
                stage=EvaluationStage.PAPER.value,
                capacity_score=0.40,
                regime_robustness=0.40,
            )
            if bundle:
                bundles.extend(
                    [
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.SHADOW.value,
                        ),
                        self._blend_runtime_and_offline_bundle(
                            lineage=lineage,
                            runtime_bundle=bundle,
                            walkforward_bundle=experiment_by_stage.get(EvaluationStage.WALKFORWARD.value),
                            stress_bundle=experiment_by_stage.get(EvaluationStage.STRESS.value),
                            stage=EvaluationStage.PAPER.value,
                        ),
                    ]
                )
        if lineage.family_id not in {
            "betfair_prediction_value_league",
            "binance_funding_contrarian",
            "binance_cascade_regime",
            "betfair_information_lag",
            "polymarket_cross_venue",
        }:
            bundles = [self._adjust_bundle_for_lineage(lineage, bundle) for bundle in bundles]
        ranked = assign_pareto_ranks(bundles)
        for bundle in ranked:
            bundle.hard_vetoes = compute_hard_vetoes(bundle)
        return ranked

    def _save_evidence(self, lineage: LineageRecord) -> Dict[str, EvaluationBundle]:
        by_stage: Dict[str, EvaluationBundle] = {}
        for bundle in self._collect_evidence(lineage):
            self.registry.save_evaluation(bundle)
            previous = by_stage.get(bundle.stage)
            if previous is None or str(bundle.generated_at) > str(previous.generated_at):
                by_stage[bundle.stage] = bundle
        return by_stage

    def _maybe_run_post_eval_critique(
        self,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_by_stage: Dict[str, EvaluationBundle],
    ) -> None:
        critique = self.agent_runtime.critique_post_evaluation(
            family=family,
            lineage=lineage,
            genome=self.registry.load_genome(lineage.lineage_id),
            latest_bundle=(
                latest_by_stage.get(EvaluationStage.PAPER.value)
                or latest_by_stage.get(EvaluationStage.STRESS.value)
                or latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
            ),
            learning_memory=self.registry.learning_memories(family_id=lineage.family_id, limit=12),
            execution_evidence=self._execution_validation_snapshot(lineage),
        )
        if critique is None:
            return
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if experiment is None:
            return
        experiment.expected_outputs = dict(experiment.expected_outputs or {})
        experiment.expected_outputs["post_eval_critique"] = {
            "artifact_path": critique.artifact_path,
            "provider": critique.provider,
            "model": critique.model,
            "success": critique.success,
            "fallback_used": critique.fallback_used,
        }
        self.registry.save_experiment(lineage.lineage_id, experiment)

    def _maybe_publish_manifest(
        self,
        lineage: LineageRecord,
        walkforward_bundle: Optional[EvaluationBundle],
        incumbent_walkforward_bundle: Optional[EvaluationBundle],
        paper_bundle: Optional[EvaluationBundle],
        incumbent_paper_bundle: Optional[EvaluationBundle],
    ) -> Optional[str]:
        if paper_bundle is None:
            return None
        blockers = list(
            self.promotion.compare_to_incumbent(
                walkforward_bundle,
                incumbent_walkforward_bundle,
            )["blockers"]
        ) if walkforward_bundle is not None else ["missing_walkforward_evidence"]
        blockers.extend(
            self.promotion.paper_gate_blockers(
                paper_bundle,
                slow_strategy="polymarket" in lineage.family_id or "information" in lineage.family_id,
            )
        )
        blockers.extend(
            list(
                self.promotion.compare_to_incumbent(
                    paper_bundle,
                    incumbent_paper_bundle,
                )["blockers"]
            )
        )
        if blockers:
            return None
        existing = [
            manifest for manifest in self.registry.manifests()
            if manifest.lineage_id == lineage.lineage_id
        ]
        if existing:
            return existing[-1].manifest_id
        artifact_refs = {
            "workspace": lineage.goldfish_workspace,
            "paper_bundle_id": paper_bundle.evaluation_id,
        }
        experiment = self.registry.load_experiment(lineage.lineage_id)
        latest_run = dict((experiment.expected_outputs or {}).get("latest_run") or {}) if experiment else {}
        if latest_run.get("package_path"):
            artifact_refs["package"] = str(latest_run["package_path"])
            artifact_refs["run_id"] = latest_run.get("run_id")
        if lineage.family_id == "binance_funding_contrarian":
            artifact_refs["model_meta"] = "data/funding_models/funding_predictor_meta.json"
        elif lineage.family_id == "betfair_prediction_value_league":
            artifact_refs["policy_gate"] = str(getattr(config, "PREDICTION_POLICY_GATE_PATH", "data/models/prediction_policy_gate_v1.json"))
        manifest = self.bridge.publish_candidate_manifest(
            lineage_id=lineage.lineage_id,
            family_id=lineage.family_id,
            portfolio_targets=list(lineage.target_portfolios),
            venue_targets=list(lineage.target_venues),
            artifact_refs=artifact_refs,
            runtime_overrides={"resource_profile": "local-first-hybrid"},
            notes=["Candidate manifest published by Goldfish sidecar bridge; human approval required for live use."],
        )
        self.registry.save_manifest(manifest)
        return manifest.manifest_id

    def run_cycle(self) -> Dict[str, Any]:
        runtime_mode = self._runtime_mode()
        if runtime_mode.is_hard_stop:
            return self._hard_stop_state()
        self._cycle_count += 1
        idea_scout_result = maybe_run_idea_scout(self.project_root)
        families = self.registry.families()
        family_by_id = {family.family_id: family for family in families}
        workspace_status = self._workspace_status(families)
        connector_snapshots = self._connector_snapshots()
        manifests_by_lineage = self._latest_manifest_by_lineage()
        connector_ready = {
            snapshot["connector_id"]: bool(snapshot.get("ready"))
            for snapshot in connector_snapshots
        }
        lineages_by_family = self._lineages_by_family()
        family_rankings: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        curated_family_rankings = {
            family.family_id: self._curated_family_ranking_summary(family.family_id)
            for family in families
        }
        total_paper_pnl = 0.0
        live_loadable_manifests = []
        lineage_summaries: List[Dict[str, Any]] = []
        lineage_summary_by_id: Dict[str, Dict[str, Any]] = {}
        queue_entries: List[ExperimentQueueEntry] = []
        recent_actions = [_format_recent_action(f"[cycle {self._cycle_count}] Connector snapshots refreshed and factory evidence re-evaluated.")]
        if idea_scout_result.get("ran") and int(idea_scout_result.get("new_count", 0) or 0) > 0:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Low-value idea scout added {int(idea_scout_result.get('new_count', 0) or 0)} new online ideas.",
            )
        if runtime_mode.is_cost_saver:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Runtime mode cost_saver kept deterministic evaluation active while token-consuming agentic work stayed paused.",
            )
        for family in families:
            self._seed_challengers(
                family,
                lineages_by_family,
                runtime_mode_value=runtime_mode.value,
                recent_actions=recent_actions,
            )
        for lineage in self.registry.lineages():
            latest_by_stage = (
                self._save_evidence(lineage)
                if lineage.active
                else self.registry.latest_evaluation_by_stage(lineage.lineage_id)
            )
            family = family_by_id.get(lineage.family_id)
            incumbent_walkforward_bundle: Optional[EvaluationBundle] = None
            incumbent_paper_bundle: Optional[EvaluationBundle] = None
            if family is not None and family.champion_lineage_id != lineage.lineage_id:
                incumbent_latest = self.registry.latest_evaluation_by_stage(family.champion_lineage_id)
                incumbent_walkforward_bundle = incumbent_latest.get(EvaluationStage.WALKFORWARD.value)
                incumbent_paper_bundle = incumbent_latest.get(EvaluationStage.PAPER.value)
            if family is not None and lineage.active:
                self._maybe_run_debug_agent(
                    family,
                    lineage,
                    latest_by_stage,
                    recent_actions=recent_actions,
                )
                self._maybe_run_scheduled_agent_review(
                    family,
                    lineage,
                    latest_by_stage,
                    recent_actions=recent_actions,
                )
                self._maybe_run_post_eval_critique(family, lineage, latest_by_stage)
            data_ready = all(connector_ready.get(connector_id, False) for connector_id in lineage.connector_ids)
            workspace_ready = bool(workspace_status.get(lineage.family_id, {}).get("ready"))
            manifest = manifests_by_lineage.get(lineage.lineage_id)
            if runtime_mode.is_full and lineage.active:
                manifest_id = self._maybe_publish_manifest(
                    lineage,
                    latest_by_stage.get(EvaluationStage.WALKFORWARD.value),
                    incumbent_walkforward_bundle,
                    latest_by_stage.get(EvaluationStage.PAPER.value),
                    incumbent_paper_bundle,
                )
                if manifest_id:
                    manifest = self.registry.load_manifest(manifest_id) or manifest
                    if manifest is not None:
                        manifests_by_lineage[lineage.lineage_id] = manifest
                decision = self.promotion.decide(
                    lineage,
                    data_ready=data_ready,
                    workspace_ready=workspace_ready,
                    walkforward_bundle=latest_by_stage.get(EvaluationStage.WALKFORWARD.value),
                    incumbent_walkforward_bundle=incumbent_walkforward_bundle,
                    stress_bundle=latest_by_stage.get(EvaluationStage.STRESS.value),
                    paper_bundle=latest_by_stage.get(EvaluationStage.PAPER.value),
                    incumbent_paper_bundle=incumbent_paper_bundle,
                    manifest_status=manifest.status if manifest is not None else None,
                    approved_by=manifest.approved_by if manifest is not None else None,
                )
                self.registry.cas_transition(
                    lineage.lineage_id,
                    expected_stage=lineage.current_stage,
                    next_stage=decision.next_stage,
                    blockers=decision.blockers,
                    decision=decision.to_dict(),
                )
                refreshed = self.registry.load_lineage(lineage.lineage_id) or lineage
            else:
                refreshed = self.registry.load_lineage(lineage.lineage_id) or lineage
            manifest_id = manifest.manifest_id if manifest is not None else None
            experiment = self.registry.load_experiment(refreshed.lineage_id)
            hypothesis = self.registry.load_hypothesis(refreshed.lineage_id)
            genome = self.registry.load_genome(refreshed.lineage_id)
            latest_run = dict((experiment.expected_outputs or {}).get("latest_run") or {}) if experiment else {}
            latest_bundle = (
                latest_by_stage.get(EvaluationStage.PAPER.value)
                or latest_by_stage.get(EvaluationStage.STRESS.value)
                or latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
            )
            execution_validation = self._execution_validation_snapshot(refreshed)
            maintenance_request = self._maintenance_request(refreshed, execution_validation)
            curated_summary = dict(
                (curated_family_rankings.get(refreshed.family_id, {}).get("by_lineage") or {}).get(refreshed.lineage_id)
                or {}
            )
            live_target = self._select_live_paper_target(
                lineage=refreshed,
                execution_validation=execution_validation,
                curated_target_portfolio_id=str(curated_summary.get("target_portfolio_id") or ""),
            )
            live_account = dict(live_target.get("account") or {})
            if latest_bundle is not None and lineage.active:
                total_paper_pnl += float(latest_bundle.net_pnl or 0.0)
            lineage_summary = {
                "lineage_id": refreshed.lineage_id,
                "family_id": refreshed.family_id,
                "label": refreshed.label,
                "role": refreshed.role,
                "current_stage": refreshed.current_stage,
                "active": bool(refreshed.active),
                "loss_streak": int(refreshed.loss_streak or 0),
                "tweak_count": int(refreshed.tweak_count or 0),
                "max_tweaks": int(refreshed.max_tweaks or 2),
                "iteration_status": refreshed.iteration_status,
                "creation_kind": refreshed.creation_kind,
                "parent_lineage_id": refreshed.parent_lineage_id,
                "last_agent_review_at": refreshed.last_agent_review_at,
                "next_agent_review_at": refreshed.next_agent_review_at,
                "last_agent_review_reason": refreshed.last_agent_review_reason,
                "last_agent_review_status": refreshed.last_agent_review_status,
                "last_agent_review_trade_count": int(refreshed.last_agent_review_trade_count or 0),
                "last_agent_review_artifact_path": refreshed.last_agent_review_artifact_path,
                "last_agent_review_action": refreshed.last_agent_review_action,
                "last_agent_review_summary": refreshed.last_agent_review_summary,
                "last_debug_review_at": refreshed.last_debug_review_at,
                "next_debug_review_at": refreshed.next_debug_review_at,
                "last_debug_review_reason": refreshed.last_debug_review_reason,
                "last_debug_review_status": refreshed.last_debug_review_status,
                "last_debug_issue_signature": refreshed.last_debug_issue_signature,
                "last_debug_review_artifact_path": refreshed.last_debug_review_artifact_path,
                "last_debug_requires_human": bool(refreshed.last_debug_requires_human),
                "last_debug_human_action": refreshed.last_debug_human_action,
                "last_debug_bug_category": refreshed.last_debug_bug_category,
                "last_debug_summary": refreshed.last_debug_summary,
                "retired_at": refreshed.retired_at,
                "retirement_reason": refreshed.retirement_reason,
                "last_memory_id": refreshed.last_memory_id,
                "budget_bucket": refreshed.budget_bucket,
                "budget_weight_pct": float(refreshed.budget_weight_pct or 0.0),
                "connector_ids": list(refreshed.connector_ids),
                "target_portfolios": list(refreshed.target_portfolios),
                "lead_agent_role": hypothesis.lead_agent_role if hypothesis else None,
                "collaborating_agent_roles": list((hypothesis.collaborating_agent_roles if hypothesis else []) or []),
                "scientific_domains": list((hypothesis.scientific_domains if hypothesis else []) or []),
                "hypothesis_origin": hypothesis.origin if hypothesis else None,
                "last_decision": dict(refreshed.last_decision or {}),
                "promotion_scorecard": dict(((refreshed.last_decision or {}).get("scorecard")) or {}),
                "latest_agent_decision": dict((genome.parameters.get("last_agent_decision") if genome is not None else {}) or {}),
                "proposal_agent": dict((genome.parameters.get("proposal_agent") if genome is not None else {}) or {}),
                "proposal_kind": (
                    genome.parameters.get("creation_kind")
                    if genome is not None
                    else refreshed.creation_kind
                ),
                "source_idea_id": (
                    genome.parameters.get("source_idea_id")
                    if genome is not None
                    else None
                ),
                "last_tweak_agent_provider": (
                    genome.parameters.get("last_tweak_agent_provider")
                    if genome is not None
                    else None
                ),
                "last_tweak_agent_model": (
                    genome.parameters.get("last_tweak_agent_model")
                    if genome is not None
                    else None
                ),
                "last_tweak_agent_task_class": (
                    genome.parameters.get("last_tweak_agent_task_class")
                    if genome is not None
                    else None
                ),
                "blockers": list(refreshed.blockers),
                "fitness_score": float((latest_bundle.fitness_score if latest_bundle else 0.0) or 0.0),
                "pareto_rank": latest_bundle.pareto_rank if latest_bundle else None,
                "monthly_roi_pct": float((latest_bundle.monthly_roi_pct if latest_bundle else 0.0) or 0.0),
                "research_monthly_roi_pct": float((latest_bundle.monthly_roi_pct if latest_bundle else 0.0) or 0.0),
                "calibration_lift_abs": float((latest_bundle.calibration_lift_abs if latest_bundle else 0.0) or 0.0),
                "net_pnl": float((latest_bundle.net_pnl if latest_bundle else 0.0) or 0.0),
                "research_net_pnl": float((latest_bundle.net_pnl if latest_bundle else 0.0) or 0.0),
                "trade_count": int((latest_bundle.trade_count if latest_bundle else 0) or 0),
                "research_trade_count": int((latest_bundle.trade_count if latest_bundle else 0) or 0),
                "settled_count": int((latest_bundle.settled_count if latest_bundle else 0) or 0),
                "paper_days": int((latest_bundle.paper_days if latest_bundle else 0) or 0),
                "research_paper_days": int((latest_bundle.paper_days if latest_bundle else 0) or 0),
                "hard_vetoes": list((latest_bundle.hard_vetoes if latest_bundle else []) or []),
                "latest_artifact_mode": latest_run.get("mode"),
                "latest_artifact_package": latest_run.get("package_path"),
                "latest_artifact_run_id": latest_run.get("run_id"),
                "latest_retrain_action": latest_run.get("retrain_action"),
                "latest_retrain_triggered": bool(latest_run.get("retrain_triggered")),
                "latest_execution_refresh_status": latest_run.get("execution_refresh_status"),
                "latest_execution_refresh_reason": latest_run.get("execution_refresh_reason"),
                "latest_execution_refresh_selected": latest_run.get("execution_refresh_selected"),
                "latest_execution_refresh_artifact": latest_run.get("execution_refresh_artifact"),
                "strict_gate_pass": refreshed.current_stage in {
                    PromotionStage.CANARY_READY.value,
                    PromotionStage.LIVE_READY.value,
                    PromotionStage.APPROVED_LIVE.value,
                },
                "manifest_id": manifest_id,
                "execution_validation": execution_validation,
                "execution_running_target_count": int(execution_validation["running_target_count"]),
                "execution_recent_trade_count": int(execution_validation["recent_trade_count"]),
                "execution_recent_event_count": int(execution_validation["recent_event_count"]),
                "execution_has_signal": bool(execution_validation["has_execution_signal"]),
                "execution_health_status": execution_validation.get("health_status"),
                "execution_issue_codes": list(execution_validation.get("issue_codes") or []),
                "execution_recommendation_context": list(execution_validation.get("recommendation_context") or []),
                "live_paper_target_portfolio_id": str(
                    live_target.get("resolved_target")
                    or live_target.get("requested_target")
                    or ""
                ),
                "live_paper_running": bool(live_target.get("running")),
                "live_paper_roi_pct": float(live_account.get("roi_pct", 0.0) or 0.0),
                "live_paper_realized_pnl": float(live_account.get("realized_pnl", 0.0) or 0.0),
                "live_paper_trade_count": int(live_account.get("trade_count", 0) or 0),
                "live_paper_wins": int(live_account.get("wins", 0) or 0),
                "live_paper_losses": int(live_account.get("losses", 0) or 0),
                "live_paper_drawdown_pct": float(live_account.get("drawdown_pct", 0.0) or 0.0),
                "maintenance_request_action": maintenance_request.get("action") if maintenance_request else None,
                "maintenance_request_reason": maintenance_request.get("reason") if maintenance_request else None,
                "maintenance_request_source": maintenance_request.get("source") if maintenance_request else None,
                "curated_family_rank": curated_summary.get("family_rank"),
                "curated_ranking_score": float(curated_summary.get("ranking_score", 0.0) or 0.0),
                "curated_target_portfolio_id": curated_summary.get("target_portfolio_id"),
                "curated_paper_roi_pct": float(curated_summary.get("paper_roi_pct", 0.0) or 0.0),
                "curated_paper_realized_pnl": float(curated_summary.get("paper_realized_pnl", 0.0) or 0.0),
                "curated_paper_win_rate": float(curated_summary.get("paper_win_rate", 0.0) or 0.0),
                "curated_paper_closed_trade_count": int(curated_summary.get("paper_closed_trade_count", 0) or 0),
            }
            summary_family = family_by_id.get(refreshed.family_id)
            lineage_summary["agent_review_due_reason"] = (
                self._scheduled_review_reason(summary_family, refreshed, latest_bundle, execution_validation)
                if summary_family is not None
                else None
            )
            lineage_summary["agent_review_due"] = bool(lineage_summary["agent_review_due_reason"])
            lineage_summaries.append(lineage_summary)
            lineage_summary_by_id[refreshed.lineage_id] = lineage_summary
            family_rankings[refreshed.family_id].append(lineage_summary)
            queue_entries.append(
                ExperimentQueueEntry(
                    queue_id=f"{refreshed.lineage_id}:{refreshed.current_stage}",
                    family_id=refreshed.family_id,
                    lineage_id=refreshed.lineage_id,
                    experiment_id=refreshed.experiment_id,
                    role=refreshed.role,
                    current_stage=refreshed.current_stage,
                    status=self._queue_status(refreshed),
                    priority=self._queue_priority(refreshed),
                    notes=[
                        f"loss_streak={int(refreshed.loss_streak or 0)}",
                        f"tweak_count={int(refreshed.tweak_count or 0)}/{int(refreshed.max_tweaks or 2)}",
                    ],
                )
            )
            if manifest is not None and manifest.is_live_loadable() and runtime_mode.factory_influence_allowed:
                live_loadable_manifests.append(manifest.to_dict())
        family_summaries: List[Dict[str, Any]] = []
        for family in families:
            ranked = sorted(
                family_rankings.get(family.family_id, []),
                key=lambda item: (
                    0 if item.get("active", True) else 1,
                    item.get("pareto_rank") if item.get("pareto_rank") is not None else 999,
                    -float(item.get("fitness_score", 0.0) or 0.0),
                ),
            )
            if runtime_mode.is_full and ranked:
                self._retire_or_update_lineages(family, ranked, recent_actions=recent_actions)
                refreshed_ranked: List[Dict[str, Any]] = []
                for row in ranked:
                    lineage = self.registry.load_lineage(str(row["lineage_id"]))
                    if lineage is None:
                        continue
                    summary = lineage_summary_by_id.get(lineage.lineage_id)
                    if summary is not None:
                        summary["active"] = bool(lineage.active)
                        summary["loss_streak"] = int(lineage.loss_streak or 0)
                        summary["tweak_count"] = int(lineage.tweak_count or 0)
                        summary["max_tweaks"] = int(lineage.max_tweaks or 2)
                        summary["iteration_status"] = lineage.iteration_status
                        summary["creation_kind"] = lineage.creation_kind
                        summary["role"] = lineage.role
                        summary["retired_at"] = lineage.retired_at
                        summary["retirement_reason"] = lineage.retirement_reason
                        summary["last_memory_id"] = lineage.last_memory_id
                        summary["last_debug_review_at"] = lineage.last_debug_review_at
                        summary["last_debug_review_status"] = lineage.last_debug_review_status
                        summary["last_debug_review_artifact_path"] = lineage.last_debug_review_artifact_path
                        summary["last_debug_requires_human"] = bool(lineage.last_debug_requires_human)
                        summary["last_debug_human_action"] = lineage.last_debug_human_action
                        summary["last_debug_bug_category"] = lineage.last_debug_bug_category
                        summary["last_debug_summary"] = lineage.last_debug_summary
                    refreshed_ranked.append(dict(summary or row))
                ranked = sorted(
                    refreshed_ranked,
                    key=lambda item: (
                        0 if item.get("active", True) else 1,
                        item.get("pareto_rank") if item.get("pareto_rank") is not None else 999,
                        -float(item.get("fitness_score", 0.0) or 0.0),
                    ),
                )
                self._reclassify_family(family, ranked, recent_actions=recent_actions)
                refreshed_ranked = []
                for row in ranked:
                    lineage = self.registry.load_lineage(str(row["lineage_id"]))
                    if lineage is None:
                        continue
                    summary = lineage_summary_by_id.get(lineage.lineage_id)
                    if summary is not None:
                        summary["role"] = lineage.role
                        summary["active"] = bool(lineage.active)
                        summary["loss_streak"] = int(lineage.loss_streak or 0)
                        summary["tweak_count"] = int(lineage.tweak_count or 0)
                        summary["max_tweaks"] = int(lineage.max_tweaks or 2)
                        summary["iteration_status"] = lineage.iteration_status
                        summary["creation_kind"] = lineage.creation_kind
                        summary["last_memory_id"] = lineage.last_memory_id
                        summary["last_debug_review_at"] = lineage.last_debug_review_at
                        summary["last_debug_review_status"] = lineage.last_debug_review_status
                        summary["last_debug_review_artifact_path"] = lineage.last_debug_review_artifact_path
                        summary["last_debug_requires_human"] = bool(lineage.last_debug_requires_human)
                        summary["last_debug_human_action"] = lineage.last_debug_human_action
                        summary["last_debug_bug_category"] = lineage.last_debug_bug_category
                        summary["last_debug_summary"] = lineage.last_debug_summary
                    refreshed_ranked.append(dict(summary or row))
                ranked = sorted(
                    refreshed_ranked,
                    key=lambda item: (
                        0 if item.get("active", True) else 1,
                        item.get("pareto_rank") if item.get("pareto_rank") is not None else 999,
                        -float(item.get("fitness_score", 0.0) or 0.0),
                    ),
                )
            champion = next((item for item in ranked if item.get("active", True)), None) or {"lineage_id": family.champion_lineage_id, "current_stage": family.queue_stage}
            family.queue_stage = str(champion.get("current_stage") or family.queue_stage)
            family.last_cycle_at = utc_now_iso()
            self.registry.save_family(family)
            family_summaries.append(
                {
                    "family_id": family.family_id,
                    "label": family.label,
                    "explainer": family.explainer,
                    "queue_stage": family.queue_stage,
                    "champion": champion,
                    "lineage_count": len(ranked),
                    "active_lineage_count": sum(1 for item in ranked if item.get("active", True)),
                    "retired_lineage_count": len(family.retired_lineage_ids),
                    "shadow_challenger_ids": list(family.shadow_challenger_ids),
                    "paper_challenger_ids": list(family.paper_challenger_ids),
                    "target_portfolios": list(family.target_portfolios),
                    "budget_split": dict(family.budget_split),
                    "curated_rankings": list((curated_family_rankings.get(family.family_id, {}).get("top_lineages")) or []),
                }
            )
        queue_entries = self._refresh_queue_entries(queue_entries)
        if runtime_mode.factory_influence_allowed:
            active_lineage_ids = {
                item["lineage_id"]
                for item in lineage_summaries
                if item.get("active", True)
            }
            live_loadable_manifests = [
                manifest.to_dict()
                for manifest in self.registry.manifests()
                if manifest.is_live_loadable() and manifest.lineage_id in active_lineage_ids
            ]
        else:
            live_loadable_manifests = []
        readiness_checks = [
            {
                "name": "connector_catalog_ready",
                "ok": all(item.get("ready") for item in connector_snapshots),
                "reason": "All venue connector snapshots should resolve at least one local evidence source.",
            },
            {
                "name": "goldfish_workspaces_ready",
                "ok": all(item.get("ready") for item in workspace_status.values()),
                "reason": "Each family should have a Goldfish sidecar workspace scaffold.",
            },
            {
                "name": "no_live_without_human_signoff",
                "ok": all(bool(item.get("approved_by")) and bool(item.get("approved_at")) for item in live_loadable_manifests),
                "reason": "Any live-loadable manifest must include explicit human approval metadata.",
            },
            {
                "name": "agentic_factory_runtime_mode",
                "ok": True,
                "reason": (
                    "Runtime mode is full."
                    if runtime_mode.is_full
                    else "Runtime mode is cost_saver, so token-consuming agentic work and new manifest publication are paused intentionally."
                ),
            },
        ]
        readiness_status = "research_only"
        if any(summary.get("strict_gate_pass") for summary in lineage_summaries):
            readiness_status = "paper_validating"
        if live_loadable_manifests:
            readiness_status = "live_ready"
        journal = FactoryJournal(
            active_goal="Promote only reproducible, net-of-costs lineages through paper gates and human-approved live manifests.",
            recent_actions=(self.registry.read_journal().recent_actions + recent_actions)[-20:],
        )
        self.registry.write_journal(journal)
        learning_memory = [memory.to_dict() for memory in self.registry.learning_memories(limit=20)]
        operator_signals = self._sync_operator_actions(self._operator_signals(lineage_summaries))
        state = {
            "portfolio_id": getattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory"),
            "running": True,
            "mode": "research",
            "status": "running",
            "explainer": "Research-only control plane for multi-family strategy discovery, evaluation, and approval-gated promotion.",
            "cycle_count": self._cycle_count,
            "last_cycle_at": utc_now_iso(),
            "budget_policy": {
                "split": _budget_split(),
                "max_shadow_challengers_per_family": 5,
                "max_paper_challengers_per_family": 2,
                "compute_posture": "local_first_hybrid",
            },
            "agent_roles": _factory_roles(),
            "scientific_researchers": _scientific_researchers(),
            "connectors": connector_snapshots,
            "goldfish": {
                "mode": "sidecar",
                "workspaces": workspace_status,
            },
            "families": family_summaries,
            "lineages": lineage_summaries,
            "manifests": {
                "pending": [manifest.to_dict() for manifest in self.registry.manifests() if not manifest.is_live_loadable()],
                "live_loadable": live_loadable_manifests,
            },
            "queue": [
                entry.to_dict()
                for entry in sorted(
                    queue_entries,
                    key=lambda item: (item.priority, item.family_id, item.lineage_id),
                )
            ],
            "learning_memory": learning_memory,
            "operator_signals": operator_signals,
            "readiness": {
                "status": readiness_status,
                "blockers": (
                    ["human_signoff_required_for_live"]
                    if readiness_status != "live_ready" and not live_loadable_manifests and not runtime_mode.is_cost_saver
                    else []
                ),
                "warnings": ["agentic_factory_cost_saver"] if runtime_mode.is_cost_saver else [],
                "checks": readiness_checks,
                "score_pct": round((sum(1 for item in readiness_checks if item["ok"]) / len(readiness_checks)) * 100.0, 2),
                "eta_to_readiness": (
                    "agentic_tokens_paused"
                    if runtime_mode.is_cost_saver
                    else ("human_signoff_required" if readiness_status == "paper_validating" else "research_continuous")
                ),
            },
            "research_summary": {
                "family_count": len(family_summaries),
                "lineage_count": len(lineage_summaries),
                "active_lineage_count": sum(1 for item in lineage_summaries if item.get("active", True)),
                "artifact_backed_lineage_count": sum(1 for item in lineage_summaries if item.get("latest_artifact_package")),
                "challenge_count": sum(
                    1
                    for item in lineage_summaries
                    if item.get("role") in {LineageRole.SHADOW_CHALLENGER.value, LineageRole.PAPER_CHALLENGER.value}
                ),
                "agent_generated_lineage_count": sum(
                    1
                    for item in lineage_summaries
                    if str(item.get("hypothesis_origin") or "").startswith("scientific_agent")
                    or str(item.get("hypothesis_origin") or "").startswith("real_agent_")
                ),
                "real_agent_lineage_count": sum(
                    1
                    for item in lineage_summaries
                    if str(item.get("hypothesis_origin") or "").startswith("real_agent_")
                ),
                "mutation_lineage_count": sum(1 for item in lineage_summaries if item.get("creation_kind") == "mutation"),
                "new_model_lineage_count": sum(1 for item in lineage_summaries if item.get("creation_kind") == "new_model"),
                "review_due_count": sum(1 for item in lineage_summaries if item.get("agent_review_due")),
                "reviewed_lineage_count": sum(1 for item in lineage_summaries if item.get("last_agent_review_at")),
                "debug_reviewed_lineage_count": sum(1 for item in lineage_summaries if item.get("last_debug_review_at")),
                "tweaked_lineage_count": sum(1 for item in lineage_summaries if int(item.get("tweak_count", 0) or 0) > 0),
                "retired_lineage_count": sum(1 for item in lineage_summaries if not item.get("active", True)),
                "learning_memory_count": len(learning_memory),
                "paper_pnl": round(total_paper_pnl, 4),
                "positive_model_count": len(operator_signals["positive_models"]),
                "research_positive_model_count": len(operator_signals.get("research_positive_models") or []),
                "operator_escalation_count": len(operator_signals["escalation_candidates"]),
                "human_action_required_count": len(operator_signals.get("human_action_required") or []),
                "maintenance_queue_count": len(operator_signals.get("maintenance_queue") or []),
                "operator_action_inbox_count": len(operator_signals.get("action_inbox") or []),
                "ready_for_canary": sum(1 for item in lineage_summaries if item.get("current_stage") == PromotionStage.CANARY_READY.value),
                "live_loadable_manifest_count": len(live_loadable_manifests),
                "manifest_publication_paused": runtime_mode.is_cost_saver,
            },
        }
        state = self._with_runtime_mode(state)
        self.registry.write_state(state)
        self._last_state = state
        return state

    def get_state(self) -> Dict[str, Any]:
        if not self._last_state:
            self._last_state = self.registry.read_state()
        return dict(self._last_state)
