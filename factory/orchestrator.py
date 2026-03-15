from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
from factory.agent_runtime import (
    AgentRunResult,
    RealResearchAgentRuntime,
    apply_real_agent_proposal,
    apply_real_family_proposal,
)
from factory.assessment import assessment_progress
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
from factory.execution_bridge import FactoryExecutionBridge
from factory.execution_evidence import build_portfolio_execution_evidence, summarize_execution_targets
from factory.evaluation import assign_pareto_ranks, compute_hard_vetoes
from factory.experiment_runner import FactoryExperimentRunner
from factory.experiment_sources import family_model_rankings, portfolio_scorecards
from factory.goldfish_bridge import GoldfishBridge
from factory.idea_intake import all_ideas, annotate_idea_statuses, maybe_run_manual_idea_watch, relevant_ideas_for_family
from factory.idea_scout import maybe_run_idea_scout
from factory.promotion import PromotionController
from factory.registry import FactoryRegistry
from factory.runtime_lanes import decide_runtime_lane_policy, runtime_lane_selection_key
from factory.runtime_mode import current_agentic_factory_runtime_mode
from factory.state_store import PortfolioStateStore
from factory.strategy_inventor import ScientificAgentProposal, ScientificFamilyProposal, ScientificStrategyInventor


logger = logging.getLogger(__name__)

_US_EASTERN = ZoneInfo("America/New_York")
_STOCK_MARKET_OPEN_HOUR = 9
_STOCK_MARKET_OPEN_MIN = 30
_STOCK_MARKET_CLOSE_HOUR = 16
_STOCK_MARKET_CLOSE_MIN = 0


def is_stock_market_open() -> bool:
    """Return True if US stock market is currently open (Mon-Fri 9:30-16:00 ET)."""
    now_et = datetime.now(_US_EASTERN)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(
        hour=_STOCK_MARKET_OPEN_HOUR,
        minute=_STOCK_MARKET_OPEN_MIN,
        second=0,
        microsecond=0,
    )
    market_close = now_et.replace(
        hour=_STOCK_MARKET_CLOSE_HOUR,
        minute=_STOCK_MARKET_CLOSE_MIN,
        second=0,
        microsecond=0,
    )
    return market_open <= now_et < market_close


def venue_schedule_class(family_spec: dict) -> str:
    """Classify a family as 'stock_market' or 'always_on' based on target venues."""
    venues = {str(v).lower() for v in (family_spec.get("target_venues") or [])}
    if any(v.startswith("yahoo") or v.startswith("alpaca") for v in venues):
        return "stock_market"
    return "always_on"


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


def _observed_live_paper_days(runtime_age_hours: Any) -> int:
    try:
        hours = float(runtime_age_hours or 0.0)
    except Exception:
        hours = 0.0
    if hours <= 0.0:
        return 0
    return max(1, int((hours / 24.0) + 0.9999))


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
        self.execution_bridge = FactoryExecutionBridge()
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

    _VENUE_TO_CONNECTORS: Dict[str, List[str]] = {
        "binance": ["binance_core"],
        "binance_perpetuals": ["binance_core"],
        "binance_perp": ["binance_core"],
        "crypto": ["binance_core"],
        "betfair": ["betfair_core"],
        "polymarket": ["polymarket_core", "polymarket_history"],
        "yahoo": ["yahoo_stocks"],
        "stock": ["yahoo_stocks"],
        "equity": ["yahoo_stocks"],
        "equity_options": ["yahoo_stocks"],
        "us_equities_etf": ["yahoo_stocks"],
        "alpaca": ["alpaca_stocks"],
        "multi": ["yahoo_stocks", "binance_core"],
    }

    @staticmethod
    def _normalize_venues_and_connectors(
        venues: List[str], connectors: List[str],
    ) -> tuple[List[str], List[str]]:
        canonical_venues = []
        resolved_connectors = set()
        for v in venues:
            vl = v.strip().lower()
            if vl in FactoryOrchestrator._VENUE_TO_CONNECTORS:
                canonical_venues.append(vl)
                resolved_connectors.update(FactoryOrchestrator._VENUE_TO_CONNECTORS[vl])
            elif any(kw in vl for kw in ("binance", "perp", "funding", "crypto", "btc", "eth")):
                canonical_venues.append("binance")
                resolved_connectors.add("binance_core")
            elif any(kw in vl for kw in ("betfair", "sport", "football")):
                canonical_venues.append("betfair")
                resolved_connectors.add("betfair_core")
            elif any(kw in vl for kw in ("polymarket", "prediction", "event")):
                canonical_venues.append("polymarket")
                resolved_connectors.update(["polymarket_core", "polymarket_history"])
            elif any(kw in vl for kw in ("stock", "equity", "spy", "etf", "vix", "oil", "yahoo")):
                canonical_venues.append("yahoo")
                resolved_connectors.add("yahoo_stocks")
            elif any(kw in vl for kw in ("alpaca",)):
                canonical_venues.append("alpaca")
                resolved_connectors.add("alpaca_stocks")
            else:
                canonical_venues.append(vl)
                resolved_connectors.add("yahoo_stocks")
        if not resolved_connectors:
            resolved_connectors.add("yahoo_stocks")
        return list(dict.fromkeys(canonical_venues)) or ["yahoo"], sorted(resolved_connectors)

    def _seed_family_from_spec(
        self,
        spec: Dict[str, Any],
        *,
        family_origin: str,
        source_idea_id: str | None = None,
        incubation_status: str = "core",
        incubation_cycle_created: int = 0,
        incubation_notes: List[str] | None = None,
    ) -> FactoryFamily:
        family_id = str(spec["family_id"])
        norm_venues, norm_connectors = self._normalize_venues_and_connectors(
            list(spec.get("target_venues") or []),
            list(spec.get("connectors") or []),
        )
        spec["target_venues"] = norm_venues
        spec["connectors"] = norm_connectors
        spec["target_portfolios"] = [family_id]
        hypothesis_id = f"{family_id}:hypothesis"
        lineage_id = f"{family_id}:champion"
        genome_id = f"{family_id}:genome:champion"
        experiment_id = f"{family_id}:experiment:champion"
        hypothesis = ResearchHypothesis(
            hypothesis_id=hypothesis_id,
            family_id=family_id,
            title=spec["label"],
            thesis=spec["thesis"],
            scientific_domains=list(spec.get("scientific_domains") or _scientific_researchers()[:4]),
            lead_agent_role=str(spec.get("lead_agent_role") or "Director"),
            success_metric="paper_monthly_roi_pct",
            guardrails=[
                "No live promotion without human approval.",
                "Mutation bounds may not touch credentials or hard risk caps.",
                "Paper-first and net-of-costs only.",
            ],
            origin=family_origin,
            agent_notes=list(spec.get("agent_notes") or ["Initial seeded champion for family bootstrap."]),
        )
        genome = StrategyGenome(
            genome_id=genome_id,
            lineage_id=lineage_id,
            family_id=family_id,
            parent_genome_id=None,
            role=str(spec.get("role") or LineageRole.CHAMPION.value),
            parameters={
                "resource_profile": "local-first-hybrid",
                "budget_mix": _budget_split(),
                "max_shadow_challengers": 5,
                "max_paper_challengers": 2,
                "source_idea_id": source_idea_id,
                "family_origin": family_origin,
                "incubation_status": incubation_status,
            },
            mutation_bounds=MutationBounds(
                horizons_seconds=[120, 600, 1800, 14400],
                feature_subsets=["baseline", "microstructure", "cross_science", "regime"],
                model_classes=["logit", "gbdt", "tft", "transformer", "rules"],
                execution_thresholds={"min_edge": [0.01, 0.10], "stake_fraction": [0.01, 0.10]},
                hyperparameter_ranges={"learning_rate": [0.001, 0.1], "lookback_hours": [6, 168]},
            ),
            scientific_domains=list(spec.get("scientific_domains") or _scientific_researchers()),
            budget_bucket=str(spec["budget_bucket"]),
            resource_profile="local-first-hybrid",
            budget_weight_pct=float(spec["budget_weight_pct"]),
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
            inputs={"source_idea_id": source_idea_id} if source_idea_id else {},
        )
        lineage = LineageRecord(
            lineage_id=lineage_id,
            family_id=family_id,
            label=f"{spec['label']} Champion",
            role=str(spec.get("role") or LineageRole.CHAMPION.value),
            current_stage=PromotionStage.IDEA.value,
            target_portfolios=list(spec["target_portfolios"]),
            target_venues=list(spec["target_venues"]),
            hypothesis_id=hypothesis_id,
            genome_id=genome_id,
            experiment_id=experiment_id,
            budget_bucket=str(spec["budget_bucket"]),
            budget_weight_pct=float(spec["budget_weight_pct"]),
            connector_ids=list(spec["connectors"]),
            goldfish_workspace=str(self.bridge.workspace_path(family_id)),
            iteration_status=str(spec.get("iteration_status") or "seeded_champion"),
            creation_kind="new_model" if incubation_status == "incubating" else "seeded",
        )
        family = FactoryFamily(
            family_id=family_id,
            label=str(spec["label"]),
            thesis=str(spec["thesis"]),
            target_portfolios=list(spec["target_portfolios"]),
            target_venues=list(spec["target_venues"]),
            primary_connector_ids=list(spec["connectors"]),
            champion_lineage_id=lineage_id,
            shadow_challenger_ids=[],
            paper_challenger_ids=[],
            budget_split=_budget_split(),
            queue_stage=PromotionStage.IDEA.value,
            explainer=str(spec["explainer"]),
            origin=family_origin,
            source_idea_id=source_idea_id,
            incubation_status=incubation_status,
            incubation_cycle_created=int(incubation_cycle_created or 0),
            incubation_notes=list(incubation_notes or []),
        )
        self.registry.save_family(family)
        self.registry.save_research_pack(
            hypothesis=hypothesis,
            genome=genome,
            experiment=experiment,
            lineage=lineage,
        )
        return family

    def _design_model_for_family(
        self,
        family: FactoryFamily,
        idea: Dict[str, Any],
        proposal: Any,
    ) -> bool:
        """Call model_design agent to generate model_code.py. Returns True on success."""
        for attempt in range(2):
            design_result = self.agent_runtime.design_model(
                idea=idea,
                family_id=family.family_id,
                target_venues=list(family.target_venues),
                thesis=family.thesis,
                cycle_count=self._cycle_count,
            )
            model_code = None
            class_name = None
            if design_result is not None and design_result.success:
                payload = dict(design_result.result_payload)
                model_code = str(payload.get("model_code") or "").strip()
                class_name = str(payload.get("class_name") or "").strip()

            if not model_code or not class_name:
                logger.warning(
                    "design_model attempt %d for %s returned no code, retrying",
                    attempt + 1, family.family_id,
                )
                continue

            from factory.model_sandbox import validate_code
            val_errors = validate_code(model_code)
            if val_errors:
                logger.warning(
                    "Model code for %s failed validation (attempt %d): %s",
                    family.family_id, attempt + 1, val_errors,
                )
                continue

            models_dir = self.project_root / "data" / "factory" / "models" / family.champion_lineage_id
            models_dir.mkdir(parents=True, exist_ok=True)
            code_path = models_dir / "model_code.py"
            code_path.write_text(model_code, encoding="utf-8")

            champion = self.registry.load_lineage(family.champion_lineage_id)
            if champion is not None:
                genome = self.registry.load_genome(champion.lineage_id)
                if genome is not None:
                    genome.parameters["model_code_path"] = str(code_path)
                    genome.parameters["model_class_name"] = class_name
                    self.registry.save_genome(champion.lineage_id, genome)
            return True

        logger.error(
            "design_model FAILED for %s after 2 attempts -- family will NOT be created with template code",
            family.family_id,
        )
        return False

    def _mutate_model_code(
        self,
        *,
        parent_genome: StrategyGenome,
        lineage_id: str,
        family: FactoryFamily,
        backtest_results: Dict[str, Any],
    ) -> tuple:
        """Mutate model code via CHEAP/STANDARD agent. Returns (model_code, class_name)."""
        code_path = str(parent_genome.parameters.get("model_code_path") or "")
        class_name = str(parent_genome.parameters.get("model_class_name") or "")
        if not code_path or not Path(code_path).exists():
            return None, None

        current_code = Path(code_path).read_text(encoding="utf-8")
        tweak_count = int(parent_genome.parameters.get("tweak_count", 0))

        mutate_result = self.agent_runtime.mutate_model(
            family_id=family.family_id,
            lineage_id=lineage_id,
            current_model_code=current_code,
            class_name=class_name,
            backtest_results=backtest_results,
            thesis=family.thesis,
            tweak_count=tweak_count,
        )

        if mutate_result is not None and mutate_result.success:
            payload = dict(mutate_result.result_payload)
            new_code = str(payload.get("model_code") or "").strip()
            new_class = str(payload.get("class_name") or class_name).strip()
            if new_code:
                from factory.model_sandbox import validate_code
                if not validate_code(new_code):
                    return new_code, new_class
                logger.warning("Mutated code failed validation for %s", lineage_id)

        return None, None

    def _new_family_candidate_ideas(self) -> List[Dict[str, Any]]:
        used_idea_ids = {
            str(family.source_idea_id or "").strip()
            for family in self.registry.families()
            if str(family.source_idea_id or "").strip()
        }
        lineage_rows = [lineage.to_dict() for lineage in self.registry.lineages()]
        candidates: List[Dict[str, Any]] = []
        for row in annotate_idea_statuses(all_ideas(self.project_root), lineage_rows):
            idea_id = str(row.get("idea_id") or "").strip()
            if not idea_id or idea_id in used_idea_ids:
                continue
            if str(row.get("status") or "") not in {"new"}:
                continue
            candidates.append(dict(row))
        return candidates

    def _seed_new_families(
        self,
        *,
        lineages_by_family: Dict[str, List[LineageRecord]],
        runtime_mode_value: str,
        recent_actions: List[str],
    ) -> None:
        if runtime_mode_value != "full" or not bool(getattr(config, "FACTORY_NEW_FAMILY_ENABLED", True)):
            return
        interval = max(1, int(getattr(config, "FACTORY_NEW_FAMILY_INTERVAL_CYCLES", 2)))
        if (max(1, self._cycle_count) - 1) % interval != 0:
            return
        max_active = max(0, int(getattr(config, "FACTORY_NEW_FAMILY_MAX_ACTIVE_INCUBATIONS", 3)))
        proposals_per_cycle = max(0, int(getattr(config, "FACTORY_NEW_FAMILY_PROPOSALS_PER_CYCLE", 1)))
        incubating_families = [
            family
            for family in self.registry.families()
            if str(family.incubation_status or "") == "incubating"
        ]
        remaining_slots = max(0, max_active - len(incubating_families))
        if remaining_slots <= 0 or proposals_per_cycle <= 0:
            return
        existing_family_ids = [family.family_id for family in self.registry.families()]
        research_portfolio_id = str(getattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory"))
        idea_candidates = self._new_family_candidate_ideas()[: min(remaining_slots, proposals_per_cycle)]
        for offset, idea in enumerate(idea_candidates, start=1):
            real_result = self.agent_runtime.generate_family_proposal(
                idea=idea,
                existing_family_ids=existing_family_ids,
                cycle_count=self._cycle_count,
                proposal_index=offset,
                research_portfolio_id=research_portfolio_id,
                active_incubation_count=len(incubating_families),
            )
            if real_result is not None and real_result.success and real_result.provider != "deterministic":
                proposal = apply_real_family_proposal(
                    result=real_result,
                    idea=idea,
                    existing_family_ids=existing_family_ids,
                    cycle_count=self._cycle_count,
                    proposal_index=offset,
                    research_portfolio_id=research_portfolio_id,
                )
            else:
                proposal = self.strategy_inventor.generate_family_proposal(
                    idea=idea,
                    existing_family_ids=existing_family_ids,
                    cycle_count=self._cycle_count,
                    proposal_index=offset,
                    research_portfolio_id=research_portfolio_id,
                )
            family = self._seed_family_from_spec(
                {
                    "family_id": proposal.family_id,
                    "label": proposal.label,
                    "thesis": proposal.thesis,
                    "target_portfolios": list(proposal.target_portfolios),
                    "target_venues": list(proposal.target_venues),
                    "connectors": list(proposal.primary_connector_ids),
                    "budget_bucket": "moonshot",
                    "budget_weight_pct": 8.0,
                    "role": LineageRole.CHAMPION.value,
                    "explainer": proposal.explainer,
                    "scientific_domains": list(proposal.scientific_domains),
                    "lead_agent_role": proposal.lead_agent_role,
                    "agent_notes": list(proposal.incubation_notes) + ["Incubating new family from idea intake."],
                    "iteration_status": "incubating_family_seed",
                },
                family_origin=proposal.origin,
                source_idea_id=proposal.source_idea_id,
                incubation_status="incubating",
                incubation_cycle_created=self._cycle_count,
                incubation_notes=list(proposal.incubation_notes),
            )
            if not self._design_model_for_family(family, idea, proposal):
                logger.warning("Skipping family %s -- model design failed", family.family_id)
                continue
            champion = self.registry.load_lineage(family.champion_lineage_id)
            if champion is not None:
                lineages_by_family.setdefault(family.family_id, []).append(champion)
            existing_family_ids.append(family.family_id)
            _append_recent_action(
                recent_actions,
                (
                    f"[cycle {self._cycle_count}] Incubated new family {family.family_id} from idea "
                    f"{proposal.source_idea_id or 'unknown'} targeting {','.join(proposal.target_venues)}."
                ),
            )

    def bootstrap(self) -> None:
        if self.registry.families():
            return

        ideas = all_ideas(self.project_root)
        if not ideas:
            logger.warning("No ideas found for bootstrap. Add ideas to IDEAS.md first.")
            return

        seeded_count = 0
        max_bootstrap = min(len(ideas), 4)
        research_portfolio_id = str(getattr(config, "RESEARCH_FACTORY_PORTFOLIO_ID", "research_factory"))

        for idea in ideas[:max_bootstrap]:
            existing_family_ids = [f.family_id for f in self.registry.families()]
            real_result = self.agent_runtime.generate_family_proposal(
                idea=idea,
                existing_family_ids=existing_family_ids,
                cycle_count=0,
                proposal_index=seeded_count + 1,
                research_portfolio_id=research_portfolio_id,
                active_incubation_count=0,
            )
            if real_result is not None and real_result.success and real_result.provider != "deterministic":
                proposal = apply_real_family_proposal(
                    result=real_result,
                    idea=idea,
                    existing_family_ids=existing_family_ids,
                    cycle_count=0,
                    proposal_index=seeded_count + 1,
                    research_portfolio_id=research_portfolio_id,
                )
            else:
                proposal = self.strategy_inventor.generate_family_proposal(
                    idea=idea,
                    existing_family_ids=existing_family_ids,
                    cycle_count=0,
                    proposal_index=seeded_count + 1,
                    research_portfolio_id=research_portfolio_id,
                )

            family = self._seed_family_from_spec(
                {
                    "family_id": proposal.family_id,
                    "label": proposal.label,
                    "thesis": proposal.thesis,
                    "target_portfolios": list(proposal.target_portfolios),
                    "target_venues": list(proposal.target_venues),
                    "connectors": list(proposal.primary_connector_ids),
                    "budget_bucket": "moonshot",
                    "budget_weight_pct": 8.0,
                    "role": LineageRole.CHAMPION.value,
                    "explainer": proposal.explainer,
                },
                family_origin=proposal.origin,
                source_idea_id=proposal.source_idea_id,
                incubation_status="incubating",
                incubation_cycle_created=0,
                incubation_notes=list(proposal.incubation_notes),
            )

            if self._design_model_for_family(family, idea, proposal):
                seeded_count += 1
                logger.info("Bootstrap: created family %s from idea %s", family.family_id, idea.get("title", "?"))
            else:
                logger.warning("Bootstrap: design failed for idea %s, skipping", idea.get("title", "?"))

        if seeded_count == 0:
            logger.error("Bootstrap failed to create any families from ideas.")
            return

        self.registry.write_journal(
            FactoryJournal(
                active_goal="Build a reproducible strategy factory with LLM-designed models and approval-gated promotion.",
                recent_actions=[f"[bootstrap] Created {seeded_count} families from IDEAS.md via LLM model design."],
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
        parent_code_path = str(parent_genome.parameters.get("model_code_path") or "")
        if parent_code_path and Path(parent_code_path).exists():
            last_eval = {}
            try:
                last_eval_id = parent_lineage.last_evaluation_id
                if last_eval_id:
                    eval_data = self.registry.load_evaluation(last_eval_id)
                    if eval_data:
                        last_eval = eval_data.to_dict()
            except Exception:
                pass
            new_code, new_class = self._mutate_model_code(
                parent_genome=parent_genome,
                lineage_id=lineage_id,
                family=family,
                backtest_results=last_eval,
            )
            if new_code and new_class:
                new_model_dir = self.project_root / "data" / "factory" / "models" / lineage_id
                new_model_dir.mkdir(parents=True, exist_ok=True)
                new_code_path = new_model_dir / "model_code.py"
                new_code_path.write_text(new_code, encoding="utf-8")
                genome.parameters["model_code_path"] = str(new_code_path)
                genome.parameters["model_class_name"] = new_class
            else:
                genome.parameters["model_code_path"] = parent_code_path
                genome.parameters["model_class_name"] = str(parent_genome.parameters.get("model_class_name") or "")
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
        if str(family.incubation_status or "") == "incubating" and str(family.queue_stage or "") in {
            PromotionStage.IDEA.value,
            PromotionStage.SPEC.value,
            PromotionStage.DATA_CHECK.value,
            PromotionStage.GOLDFISH_RUN.value,
            PromotionStage.WALKFORWARD.value,
            PromotionStage.STRESS.value,
        }:
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
        if maintenance_tokens.intersection(
            {"review_requested_replace", "replace", "review_requested_rework", "rework", "prepare_isolated_lane"}
        ):
            pressure = max(pressure, 2)
        elif maintenance_tokens.intersection({"review_requested_retrain", "retrain"}):
            pressure = max(pressure, 1)
        return pressure

    def _family_autopilot_plan(
        self,
        family_id: str,
        family_rows: Sequence[Dict[str, Any]],
        *,
        family_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        issue_codes: set[str] = set()
        maintenance_actions: set[str] = set()
        health_statuses: List[str] = []
        leader: Dict[str, Any] = {}
        if family_rows:
            leader = sorted(
                [dict(row) for row in family_rows],
                key=lambda row: (
                    0 if row.get("runtime_lane_kind") == "primary_incumbent" else 1,
                    0 if row.get("active", True) else 1,
                    row.get("curated_family_rank") if row.get("curated_family_rank") is not None else 999,
                    -float(row.get("live_paper_trade_count", 0) or 0),
                    -float(row.get("live_paper_roi_pct", 0.0) or 0.0),
                ),
            )[0]
        for row in family_rows:
            execution_validation = dict(row.get("execution_validation") or {})
            issue_codes.update(
                str(item).strip().lower()
                for item in list(row.get("execution_issue_codes") or []) + list(execution_validation.get("issue_codes") or [])
                if str(item).strip()
            )
            maintenance_action = str(row.get("maintenance_request_action") or "").strip().lower()
            if maintenance_action:
                maintenance_actions.add(maintenance_action)
            review_action = str(row.get("last_agent_review_action") or "").strip().lower()
            if str(row.get("last_agent_review_status") or "") == "completed" and review_action:
                maintenance_actions.add(f"review_requested_{review_action}")
            if bool(row.get("agent_review_due")):
                maintenance_actions.add("review_due")
            iteration_status = str(row.get("iteration_status") or "").strip().lower()
            if iteration_status:
                maintenance_actions.add(iteration_status)
            health_text = str(row.get("execution_health_status") or execution_validation.get("health_status") or "").strip().lower()
            if health_text:
                health_statuses.append(health_text)

        live_trade_count = int(leader.get("live_paper_trade_count", 0) or 0)
        live_roi_pct = float(leader.get("live_paper_roi_pct", 0.0) or 0.0)
        wins = int(leader.get("live_paper_wins", 0) or 0)
        losses = int(leader.get("live_paper_losses", 0) or 0)
        total_outcomes = wins + losses
        live_win_rate = (wins / total_outcomes) if total_outcomes > 0 else 0.0
        if live_trade_count >= 8 and live_roi_pct < 0.0:
            issue_codes.add("negative_paper_roi")
        if live_trade_count >= 12 and total_outcomes >= 12 and live_win_rate < 0.2:
            issue_codes.add("poor_win_rate")

        isolated_needed = False
        if family_summary is not None:
            has_isolated_candidate = bool(
                family_summary.get("isolated_challenger_lineage_id") or family_summary.get("prepared_isolated_lane_lineage_id")
            )
            isolated_needed = has_isolated_candidate and not bool(family_summary.get("isolated_evidence_ready"))
        if isolated_needed:
            maintenance_actions.add("isolate_evidence")

        actions: List[str] = []
        if maintenance_actions.intersection({"human_action_required"}):
            actions.append("human_action_required")
        if issue_codes.intersection({"negative_paper_roi", "poor_win_rate"}) or maintenance_actions.intersection(
            {"replace", "review_requested_replace", "retire"}
        ):
            actions.append("replace")
        if issue_codes.intersection({"untrainable_model", "training_stalled", "zero_simulated_fills", "no_trade_syndrome"}) or maintenance_actions.intersection(
            {"retrain", "review_requested_retrain"}
        ):
            actions.append("retrain")
        if issue_codes.intersection({"stalled_model", "trade_stalled", "slippage_pressure", "severe_slippage", "excessive_rejections"}) or maintenance_actions.intersection(
            {"rework", "review_requested_rework", "isolated_lane_active", "isolated_evidence_stalled", "isolate_evidence_start_failed"}
        ):
            actions.append("rework")
        if maintenance_actions.intersection({"prepare_isolated_lane", "isolate_evidence", "isolate_evidence_stalled", "isolate_evidence_start_failed"}):
            actions.append("isolate_evidence")
        if not actions and maintenance_actions.intersection({"review_due"}):
            actions.append("review")

        deduped_actions = list(dict.fromkeys(actions))
        weak_family = bool(deduped_actions) or bool(
            issue_codes.intersection(
                {
                    "negative_paper_roi",
                    "poor_win_rate",
                    "no_trade_syndrome",
                    "zero_simulated_fills",
                    "untrainable_model",
                    "training_stalled",
                    "stalled_model",
                    "trade_stalled",
                    "slippage_pressure",
                    "severe_slippage",
                    "excessive_rejections",
                }
            )
        )

        reason_tokens: List[str] = []
        if issue_codes:
            reason_tokens.append(f"issue codes: {', '.join(sorted(issue_codes)[:4])}")
        if maintenance_actions:
            reason_tokens.append(f"maintenance: {', '.join(sorted(maintenance_actions)[:4])}")
        if isolated_needed:
            reason_tokens.append("isolated evidence still not distinct")
        if not reason_tokens and weak_family:
            reason_tokens.append("family still needs active maintenance follow-through")

        worst_health = "healthy"
        if any(status == "critical" for status in health_statuses):
            worst_health = "critical"
        elif any(status == "warning" for status in health_statuses):
            worst_health = "warning"
        elif any(status in {"validation_blocked", "paper_validating", "research_only"} for status in health_statuses):
            worst_health = "validation_blocked"

        return {
            "family_id": family_id,
            "weak_family": weak_family,
            "autopilot_status": (
                "autopilot_active"
                if deduped_actions
                else ("monitoring" if weak_family else "healthy")
            ),
            "autopilot_actions": deduped_actions,
            "autopilot_reason": "; ".join(reason_tokens),
            "autopilot_issue_codes": sorted(issue_codes),
            "autopilot_maintenance_actions": sorted(maintenance_actions),
            "autopilot_trade_count": live_trade_count,
            "autopilot_live_roi_pct": round(live_roi_pct, 4),
            "autopilot_live_win_rate": round(live_win_rate, 4),
            "autopilot_health_status": worst_health,
            "autopilot_target_lineage_id": str(
                family_summary.get("primary_incumbent_lineage_id") if family_summary is not None else leader.get("lineage_id") or ""
            ),
            "autopilot_target_stage": str(
                family_summary.get("queue_stage") if family_summary is not None else leader.get("current_stage") or ""
            ),
        }

    def _family_autopilot_maintenance_request(self, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        actions = {str(item).strip().lower() for item in (plan.get("autopilot_actions") or []) if str(item).strip()}
        if not actions or "human_action_required" in actions:
            return None
        action = ""
        if "replace" in actions:
            action = "replace"
        elif "retrain" in actions:
            action = "retrain"
        elif "rework" in actions or "isolate_evidence" in actions or "review" in actions:
            action = "rework"
        if not action:
            return None
        return {
            "source": "family_autopilot",
            "action": action,
            "reason": str(plan.get("autopilot_reason") or "family autopilot maintenance"),
            "requires_human": False,
            "requires_new_challenger": action == "replace",
            "recommended_actions": list(plan.get("autopilot_actions") or []),
        }

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
        if execution_evidence.get("market_closed_idle"):
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
        if execution_evidence.get("market_closed_idle"):
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
        labels = [family.family_id, lineage.current_stage, *(family.target_portfolios or [])]
        first_assessment = assessment_progress(
            paper_days=paper_days,
            trade_count=evidence_trades,
            labels=labels,
            current_stage=lineage.current_stage,
            phase="first",
        )
        full_assessment = assessment_progress(
            paper_days=paper_days,
            trade_count=evidence_trades,
            labels=labels,
            current_stage=lineage.current_stage,
            phase="full",
        )
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
            if not bool(first_assessment.get("complete")):
                return None
        last_review_at = _parse_iso_dt(lineage.last_agent_review_at)
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
        if last_review_at is None:
            if bool(full_assessment.get("complete")):
                return "initial_maturity_review"
            return "first_assessment_review"
        if not bool(full_assessment.get("complete")):
            return None
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
        operator_action_context = self._latest_operator_action_context(lineage)
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
                "operator_action_context": operator_action_context,
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
        operator_action_context = self._latest_operator_action_context(lineage)
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
                "operator_action_context": operator_action_context,
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
            lineage.last_debug_safe_auto_actions = []
            lineage.last_debug_should_pause_lineage = False
            lineage.last_debug_severity = None
            self.registry.save_lineage(lineage)
            return
        payload = dict(debug_result.result_payload or {})
        lineage.last_debug_review_status = "completed" if debug_result.success else "failed"
        lineage.last_debug_review_artifact_path = debug_result.artifact_path
        lineage.last_debug_requires_human = bool(payload.get("requires_human", heuristic_human.get("requires_human")))
        lineage.last_debug_human_action = str(payload.get("human_action") or heuristic_human.get("human_action") or "") or None
        lineage.last_debug_bug_category = str(payload.get("bug_category") or heuristic_human.get("bug_category") or "") or None
        lineage.last_debug_summary = str(payload.get("summary") or heuristic_human.get("summary") or "") or None
        lineage.last_debug_safe_auto_actions = [
            str(item).strip()
            for item in (payload.get("safe_auto_actions") or [])
            if str(item).strip()
        ]
        lineage.last_debug_should_pause_lineage = bool(payload.get("should_pause_lineage"))
        lineage.last_debug_severity = str(payload.get("severity") or "").strip() or None
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
                "safe_auto_actions": list(lineage.last_debug_safe_auto_actions or []),
                "should_pause_lineage": bool(lineage.last_debug_should_pause_lineage),
                "severity": lineage.last_debug_severity,
                "reviewed_at": lineage.last_debug_review_at,
            }
            self.registry.save_experiment(lineage.lineage_id, experiment)
        debug_tail = f" human_action={lineage.last_debug_human_action}" if lineage.last_debug_requires_human else ""
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Debug agent ran for {lineage.lineage_id} ({debug_reason}) via {debug_result.provider} {debug_result.model}.{debug_tail}",
        )

    def _maintenance_review_signature(
        self,
        maintenance_request: Dict[str, Any],
        execution_evidence: Dict[str, Any],
    ) -> str:
        issue_codes = sorted({str(item) for item in (execution_evidence.get("issue_codes") or []) if str(item).strip()})
        return "|".join(
            [
                str(maintenance_request.get("action") or "").strip().lower(),
                str(maintenance_request.get("source") or "").strip().lower(),
                ",".join(issue_codes),
                str(bool(maintenance_request.get("requires_new_challenger"))).lower(),
            ]
        )

    def _maybe_run_maintenance_resolution_agent(
        self,
        family: FactoryFamily,
        lineage: LineageRecord,
        latest_by_stage: Dict[str, EvaluationBundle],
        *,
        recent_actions: List[str],
        maintenance_request_override: Optional[Dict[str, Any]] = None,
    ) -> bool:
        latest_bundle = (
            latest_by_stage.get(EvaluationStage.PAPER.value)
            or latest_by_stage.get(EvaluationStage.STRESS.value)
            or latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
        )
        execution_evidence = self._execution_validation_snapshot(lineage)
        maintenance_request = dict(maintenance_request_override or self._maintenance_request(lineage, execution_evidence) or {})
        if not maintenance_request or bool(maintenance_request.get("requires_human")):
            return False
        action = str(maintenance_request.get("action") or "").strip().lower()
        if action not in {"retrain", "rework", "replace", "retire"}:
            return False
        signature = self._maintenance_review_signature(maintenance_request, execution_evidence)
        if str(lineage.last_maintenance_review_signature or "").strip() == signature:
            last_at = _parse_iso_dt(lineage.last_maintenance_review_at)
            if last_at is not None:
                interval_hours = max(1, int(getattr(config, "FACTORY_MAINTENANCE_AGENT_REVIEW_INTERVAL_HOURS", 12) or 12))
                if datetime.now(timezone.utc) - last_at < timedelta(hours=interval_hours):
                    return False
        result = self.agent_runtime.resolve_maintenance_item(
            family=family,
            lineage=lineage,
            genome=self.registry.load_genome(lineage.lineage_id),
            latest_bundle=latest_bundle,
            learning_memory=self.registry.learning_memories(family_id=lineage.family_id, limit=12),
            execution_evidence=execution_evidence,
            maintenance_request=maintenance_request,
            review_context={
                "trigger_reason": str(maintenance_request.get("reason") or action or "maintenance_request"),
                "maintenance_signature": signature,
            },
        )
        now = datetime.now(timezone.utc)
        lineage.last_maintenance_review_at = now.isoformat()
        lineage.last_maintenance_review_reason = str(maintenance_request.get("reason") or action or "maintenance_request")
        lineage.last_maintenance_review_signature = signature
        lineage.next_maintenance_review_at = (
            now + timedelta(hours=max(1, int(getattr(config, "FACTORY_MAINTENANCE_AGENT_REVIEW_INTERVAL_HOURS", 12) or 12)))
        ).isoformat()
        if result is None:
            lineage.last_maintenance_review_status = "skipped_agent_disabled"
            lineage.last_maintenance_review_artifact_path = None
            lineage.last_maintenance_review_action = action
            lineage.last_maintenance_review_summary = "Maintenance review skipped because real agents are disabled for this family."
            self.registry.save_lineage(lineage)
            return False
        lineage.last_maintenance_review_status = "completed" if result.success else "failed"
        lineage.last_maintenance_review_artifact_path = result.artifact_path
        payload = dict(result.result_payload or {})
        lineage.last_maintenance_review_action = str(payload.get("maintenance_action") or action or "hold")
        lineage.last_maintenance_review_summary = str(payload.get("summary") or payload.get("maintenance_reason") or maintenance_request.get("reason") or "") or None
        if result.success:
            self._apply_review_maintenance(
                family=family,
                lineage=lineage,
                latest_bundle=latest_bundle,
                critique_payload=payload,
                recent_actions=recent_actions,
            )
        self.registry.save_lineage(lineage)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if experiment is not None:
            experiment.expected_outputs = dict(experiment.expected_outputs or {})
            experiment.expected_outputs["maintenance_resolution_review"] = {
                "artifact_path": result.artifact_path,
                "provider": result.provider,
                "model": result.model,
                "success": result.success,
                "fallback_used": result.fallback_used,
                "review_reason": lineage.last_maintenance_review_reason,
                "maintenance_action": lineage.last_maintenance_review_action,
                "maintenance_summary": lineage.last_maintenance_review_summary,
                "maintenance_signature": signature,
                "reviewed_at": lineage.last_maintenance_review_at,
            }
            self.registry.save_experiment(lineage.lineage_id, experiment)
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Maintenance review ran for {lineage.lineage_id} via {result.provider} {result.model} -> {lineage.last_maintenance_review_action}.",
        )
        return True

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

    def _should_skip_paper_dispatch(self, lineage: LineageRecord) -> bool:
        """Skip paper/shadow dispatch for stock-market families outside market hours."""
        if lineage.current_stage not in {PromotionStage.PAPER.value, PromotionStage.SHADOW.value}:
            return False
        family = self.registry.load_family(lineage.family_id)
        if family is None:
            return False
        spec = {"target_venues": list(family.target_venues)}
        if venue_schedule_class(spec) == "stock_market" and not is_stock_market_open():
            return True
        return False

    def _queue_priority(self, lineage: LineageRecord) -> int:
        base = {
            LineageRole.CHAMPION.value: 10,
            LineageRole.PAPER_CHALLENGER.value: 20,
            LineageRole.SHADOW_CHALLENGER.value: 30,
            LineageRole.MOONSHOT.value: 40,
        }.get(lineage.role, 50)
        if lineage.iteration_status == "prepare_isolated_lane":
            base = min(base, 18)
        elif lineage.iteration_status == "isolated_lane_active":
            base = min(base, 16)
        if not lineage.active:
            base += 50

        # Market-hours scheduling boost
        family = self.registry.load_family(lineage.family_id)
        if family is not None:
            spec = {"target_venues": list(family.target_venues)}
            sched_class = venue_schedule_class(spec)
            market_open = is_stock_market_open()
            if sched_class == "stock_market":
                if market_open:
                    base -= 20  # priority boost during market hours
                else:
                    # Stock families deprioritized outside market hours
                    base += 30
            else:  # always_on
                if not market_open:
                    base -= 20  # priority boost outside market hours

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
        prepared_challenger_id: str | None = None,
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
        paper_candidates: List[str] = []
        if prepared_challenger_id and prepared_challenger_id != family.champion_lineage_id:
            prepared_row = next(
                (
                    row
                    for row in active_ranked[1:]
                    if str(row.get("lineage_id") or "") == prepared_challenger_id
                ),
                None,
            )
            if prepared_row is not None:
                paper_candidates.append(str(prepared_row["lineage_id"]))
        paper_candidates.extend(
            [
                row["lineage_id"]
                for row in active_ranked[1:]
                if row.get("current_stage") in {
                    PromotionStage.PAPER.value,
                    PromotionStage.CANARY_READY.value,
                    PromotionStage.LIVE_READY.value,
                    PromotionStage.APPROVED_LIVE.value,
                }
                and row["lineage_id"] not in paper_candidates
            ]
        )
        paper_candidates = paper_candidates[:2]
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
                if lineage.iteration_status in {"new_candidate", "shadow_candidate"}:
                    lineage.iteration_status = "paper_candidate"
                if lineage.lineage_id == prepared_challenger_id:
                    lineage.iteration_status = "prepare_isolated_lane"
            else:
                lineage.role = LineageRole.SHADOW_CHALLENGER.value
                if lineage.lineage_id == prepared_challenger_id:
                    lineage.iteration_status = "prepare_isolated_lane"
                elif lineage.iteration_status in {"prepare_isolated_lane", "isolated_lane_active"}:
                    lineage.iteration_status = "shadow_candidate"
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

    def _isolated_challenger_first_assessment_failure_reason(self, row: Dict[str, Any]) -> str | None:
        if str(row.get("runtime_lane_kind") or "").strip() != "isolated_challenger":
            return None
        if str(row.get("iteration_status") or "").strip() != "isolated_lane_active":
            return None
        if not bool(row.get("first_assessment_complete")):
            return None
        health_status = str(row.get("execution_health_status") or "").strip().lower()
        issue_codes = {
            str(item)
            for item in (row.get("execution_issue_codes") or [])
            if str(item).strip()
        }
        if health_status == "critical":
            return "isolated_lane_first_assessment_critical_health"
        severe_issue_codes = {
            "runtime_error",
            "heartbeat_stale",
            "trade_stalled",
            "training_stalled",
            "stalled_model",
            "no_trade_syndrome",
            "zero_simulated_fills",
            "excessive_rejections",
            "severe_slippage",
            "negative_paper_roi",
            "negative_realized_pnl",
            "poor_win_rate",
            "drawdown_halt_active",
        }
        if issue_codes.intersection(severe_issue_codes):
            return "isolated_lane_first_assessment_execution_failure"
        min_roi_pct = float(
            getattr(config, "FACTORY_ISOLATED_CHALLENGER_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0)
            or 0.0
        )
        live_roi_pct = float(row.get("live_paper_roi_pct", 0.0) or 0.0)
        if live_roi_pct <= min_roi_pct:
            return "isolated_lane_first_assessment_negative_roi"
        return None

    def _isolated_lane_stale_without_fresh_evidence(self, row: Dict[str, Any]) -> bool:
        if str(row.get("runtime_lane_kind") or "").strip() != "isolated_challenger":
            return False
        activation_status = str(row.get("activation_status") or "").strip().lower()
        if activation_status not in {"ready_to_launch", "started", "running"}:
            return False
        issue_codes = {
            str(item).strip()
            for item in (row.get("execution_issue_codes") or [])
            if str(item).strip()
        }
        if issue_codes.intersection({"trade_stalled", "training_stalled", "stalled_model"}):
            return True
        execution_validation = dict(row.get("execution_validation") or {})
        runtime_age_hours = float(execution_validation.get("runtime_age_hours", 0.0) or 0.0)
        stale_hours = float(getattr(config, "FACTORY_RUNTIME_ALIAS_STALE_HOURS", 4.0) or 4.0)
        has_distinct_progress = (
            int(row.get("live_paper_trade_count", 0) or 0) > 0
            or abs(float(row.get("live_paper_realized_pnl", 0.0) or 0.0)) > 0.0
            or bool(execution_validation.get("has_execution_signal"))
        )
        return runtime_age_hours >= stale_hours and not has_distinct_progress

    def _persistent_stall_retirement_reason(
        self,
        lineage: LineageRecord,
        row: Dict[str, Any],
    ) -> str | None:
        issue_codes = {
            str(item).strip().lower()
            for item in (row.get("execution_issue_codes") or [])
            if str(item).strip()
        }
        if not issue_codes:
            return None
        if int(lineage.tweak_count or 0) < int(lineage.max_tweaks or 2):
            return None
        execution_validation = dict(row.get("execution_validation") or {})
        runtime_age_hours = float(execution_validation.get("runtime_age_hours", 0.0) or 0.0)
        stalled_hours = float(getattr(config, "FACTORY_STALLED_MODEL_HOURS", 8) or 8)
        if runtime_age_hours < stalled_hours:
            return None
        if "untrainable_model" in issue_codes:
            return "untrainable_model_persisted_after_tweaks"
        if "training_stalled" in issue_codes:
            return "training_stalled_after_tweaks"
        if issue_codes.intersection({"stalled_model", "trade_stalled"}):
            return "stalled_model_after_tweaks"
        if issue_codes.intersection({"no_trade_syndrome", "zero_simulated_fills"}):
            return "no_trade_syndrome_after_tweaks"
        return None

    def _trainability_contract_request(
        self,
        lineage: LineageRecord,
        execution_validation: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        issue_codes = {
            str(item).strip().lower()
            for item in (execution_validation.get("issue_codes") or [])
            if str(item).strip()
        }
        if not issue_codes.intersection({"untrainable_model", "training_stalled"}):
            return None
        runtime_age_hours = float(execution_validation.get("runtime_age_hours", 0.0) or 0.0)
        grace_hours = float(getattr(config, "FACTORY_TRAINABILITY_GRACE_HOURS", 6.0) or 6.0)
        trainability_status = str(execution_validation.get("trainability_status") or "").strip().lower()
        required_model_count = int(execution_validation.get("required_model_count", 0) or 0)
        trainable_model_count = int(execution_validation.get("trainable_model_count", 0) or 0)
        trained_model_count = int(execution_validation.get("trained_model_count", 0) or 0)
        blocked_models = [str(item).strip() for item in (execution_validation.get("blocked_models") or []) if str(item).strip()]
        severe_breach = (
            "training_stalled" in issue_codes
            or trainability_status == "blocked"
            or (required_model_count > 0 and trainable_model_count <= 0)
        )
        if not severe_breach and runtime_age_hours < grace_hours:
            return None
        action = "replace" if severe_breach or int(lineage.tweak_count or 0) > 0 else "retrain"
        if lineage.role == LineageRole.CHAMPION.value and action == "replace":
            action = "replace"
        return {
            "source": "trainability_contract",
            "action": action,
            "reason": (
                "Trainability contract breached: required learners are blocked or not progressing fast enough "
                "to justify keeping this lineage in the active paper loop."
            ),
            "requires_human": False,
            "requires_new_challenger": action == "replace",
            "issue_codes": sorted(issue_codes.intersection({"untrainable_model", "training_stalled"})),
            "trainability_status": trainability_status,
            "required_model_count": required_model_count,
            "trainable_model_count": trainable_model_count,
            "trained_model_count": trained_model_count,
            "blocked_models": blocked_models[:6],
            "runtime_age_hours": runtime_age_hours,
        }

    def _mark_isolated_challenger_first_assessment_passed(
        self,
        lineage: LineageRecord,
        row: Dict[str, Any],
        *,
        recent_actions: List[str],
    ) -> None:
        if str(row.get("runtime_lane_kind") or "").strip() != "isolated_challenger":
            return
        if not bool(row.get("first_assessment_complete")):
            return
        if str(lineage.iteration_status or "").strip() != "isolated_lane_active":
            return
        if self._isolated_challenger_first_assessment_failure_reason(row):
            return
        lineage.iteration_status = "isolated_lane_first_assessment_passed"
        self.registry.save_lineage(lineage)
        _append_recent_action(
            recent_actions,
            (
                f"[cycle {self._cycle_count}] Isolated challenger {lineage.lineage_id} passed first paper "
                f"assessment on its alias lane."
            ),
        )

    def _trigger_auto_optimization(
        self,
        families: List[FactoryFamily],
        recent_actions: List[Dict[str, Any]],
    ) -> None:
        """Spawn background Optuna optimization for families lacking backtest results.

        Runs once per family per day via a timestamp file. Pure computation (TASK_LOCAL).
        """
        import subprocess as _sp

        results_root = self.project_root / "data" / "backtest_results"
        stamp_dir = self.project_root / "data" / "factory" / "state"
        stamp_dir.mkdir(parents=True, exist_ok=True)
        stamp_path = stamp_dir / "last_auto_optimize.json"

        stamps: Dict[str, str] = {}
        if stamp_path.exists():
            try:
                stamps = json.loads(stamp_path.read_text(encoding="utf-8"))
            except Exception:
                stamps = {}

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        optimizable = {"hmm_regime_adaptive", "binance_funding_contrarian", "binance_cascade_regime"}
        families_to_optimize = []

        for family in families:
            if family.family_id not in optimizable:
                continue
            if stamps.get(family.family_id) == today:
                continue
            family_dir = results_root / family.family_id
            if family_dir.exists() and len(list(family_dir.glob("*_optuna_results.json"))) > 0:
                stamps[family.family_id] = today
                continue
            families_to_optimize.append(family.family_id)

        if not families_to_optimize:
            return

        for fam_id in families_to_optimize:
            try:
                cmd = [
                    sys.executable,
                    str(self.project_root / "scripts" / "optimize_all_champions.py"),
                    "--families", fam_id,
                    "--n-trials", "30",
                ]
                _sp.Popen(
                    cmd,
                    stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL,
                    cwd=str(self.project_root),
                )
                stamps[fam_id] = today
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Spawned background optimization for {fam_id}.",
                )
                logger.info("Spawned optimization subprocess for %s", fam_id)
            except Exception as exc:
                logger.warning("Failed to spawn optimization for %s: %s", fam_id, exc)

        stamp_path.write_text(json.dumps(stamps, indent=2), encoding="utf-8")

    def _promote_optimized_lineages(
        self,
        families: List[FactoryFamily],
        recent_actions: List[Dict[str, Any]],
    ) -> None:
        """Re-evaluate lineages stuck in early stages after optimization results land."""
        results_root = self.project_root / "data" / "backtest_results"

        for family in families:
            family_dir = results_root / family.family_id
            if not family_dir.exists():
                continue
            optuna_files = list(family_dir.glob("*_optuna_results.json"))
            if not optuna_files:
                continue

            best_roi = 0.0
            best_params: Dict[str, Any] = {}
            for fp in optuna_files:
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    roi = float((data.get("best_metrics") or {}).get("total_return_pct", data.get("best_score", 0)) or 0)
                    if roi > best_roi:
                        best_roi = roi
                        best_params = data.get("best_params", {})
                except Exception:
                    continue

            if best_roi <= 0:
                continue

            lineages = [l for l in self.registry.lineages() if l.family_id == family.family_id]
            champion = None
            for lin in lineages:
                if lin.role == "champion" and lin.active:
                    champion = lin
                    break

            if champion is not None:
                latest_evals = self.registry.latest_evaluation_by_stage(champion.lineage_id)
                champ_fitness = max(
                    (float(getattr(b, "fitness_score", 0) or 0) for b in latest_evals.values()),
                    default=0.0,
                )
                if champ_fitness <= 0:
                    _append_recent_action(
                        recent_actions,
                        f"[cycle {self._cycle_count}] Optimization found positive ROI ({best_roi:.1f}%) for {family.family_id}; "
                        f"champion {champion.lineage_id[:20]} has fitness {champ_fitness:.2f}.",
                    )

    def _backtest_positive_gate(self, lineage: LineageRecord, family: FactoryFamily) -> tuple[bool, str]:
        """Check if a lineage passes the backtest-positive gate for paper promotion.

        Returns (passes, reason).
        """
        spec = {"target_venues": list(family.target_venues)}
        sched_class = venue_schedule_class(spec)
        venues_set = {str(v).lower() for v in family.target_venues}

        # Polymarket: exempt from backtest gate (no historical data yet)
        if "polymarket" in venues_set and len(venues_set) <= 2:
            return (True, "polymarket_exempt_no_backtest_data")

        # Check walkforward evidence
        latest_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
        walkforward_bundle = latest_by_stage.get("walkforward")
        if walkforward_bundle is None:
            has_any_trades = any(
                int(getattr(b, "trade_count", 0) or 0) > 0
                for b in latest_by_stage.values()
            )
            if not has_any_trades:
                return (True, "no_walkforward_paper_trial_allowed")
            return (False, "no_walkforward_evidence")

        stress_positive = bool(
            getattr(walkforward_bundle, "stress_positive", False)
            if hasattr(walkforward_bundle, "stress_positive")
            else (walkforward_bundle.metrics or {}).get("stress_positive", False)
        )

        # Betfair: relaxed gate (limited historical data)
        if "betfair" in venues_set:
            if walkforward_bundle is not None:
                return (True, "betfair_relaxed_walkforward_only")
            return (False, "betfair_no_walkforward")

        # Full gate for Yahoo/Binance families
        fitness = float(
            getattr(walkforward_bundle, "fitness_score", 0)
            if hasattr(walkforward_bundle, "fitness_score")
            else (walkforward_bundle.metrics or {}).get("fitness_score", 0) or 0
        )
        if fitness <= 0:
            return (False, f"negative_fitness_score_{fitness:.4f}")

        # Check for batch backtest results
        backtest_results_dir = self.project_root / "data" / "backtest_results" / family.family_id
        if backtest_results_dir.exists():
            result_files = list(backtest_results_dir.glob("*.json"))
            if result_files:
                for rf in result_files:
                    try:
                        data = json.loads(rf.read_text(encoding="utf-8"))
                        test_metrics = data.get("test_metrics") or data.get("best_metrics") or {}
                        test_roi = float(
                            test_metrics.get("total_return_pct", test_metrics.get("total_return", 0)) or 0
                        )
                        if test_roi > 0:
                            return (True, f"backtest_positive_roi_{test_roi:.2f}pct")
                    except Exception:
                        continue
                return (False, "all_backtest_results_negative")

        # No backtest results yet -- check if optimization results exist
        opt_path = self.project_root / "data" / "backtest_results" / family.family_id / "optimized_params.json"
        if opt_path.exists():
            try:
                opt_data = json.loads(opt_path.read_text(encoding="utf-8"))
                best_return = float((opt_data.get("best_metrics") or {}).get("total_return_pct", 0) or 0)
                if best_return > 0:
                    return (True, f"optimization_positive_roi_{best_return:.2f}pct")
                return (False, f"optimization_negative_roi_{best_return:.2f}pct")
            except Exception:
                pass

        has_any_trades = any(
            int(getattr(b, "trade_count", 0) or 0) > 0
            for b in latest_by_stage.values()
        )
        if not has_any_trades:
            return (True, "no_data_paper_trial_allowed")

        return (False, "no_backtest_or_optimization_results")

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
            isolated_first_assessment_failure = self._isolated_challenger_first_assessment_failure_reason(row)
            if isolated_first_assessment_failure:
                lineage.active = False
                lineage.retired_at = utc_now_iso()
                lineage.iteration_status = "retired"
                lineage.retirement_reason = isolated_first_assessment_failure
                lineage.blockers = list(dict.fromkeys(list(lineage.blockers) + [lineage.retirement_reason]))
                self._record_learning_memory(lineage, row, reason=isolated_first_assessment_failure)
                retired_ids.add(lineage.lineage_id)
                self.registry.save_lineage(lineage)
                _append_recent_action(
                    recent_actions,
                    (
                        f"[cycle {self._cycle_count}] Retired isolated challenger {lineage.lineage_id} after failed "
                        f"first paper assessment: {isolated_first_assessment_failure}."
                    ),
                )
                continue
            persistent_stall_retirement = self._persistent_stall_retirement_reason(lineage, row)
            if persistent_stall_retirement:
                lineage.active = False
                lineage.retired_at = utc_now_iso()
                lineage.iteration_status = "retired"
                lineage.retirement_reason = persistent_stall_retirement
                lineage.blockers = list(dict.fromkeys(list(lineage.blockers) + [lineage.retirement_reason]))
                self._record_learning_memory(lineage, row, reason=persistent_stall_retirement)
                retired_ids.add(lineage.lineage_id)
                self.registry.save_lineage(lineage)
                _append_recent_action(
                    recent_actions,
                    (
                        f"[cycle {self._cycle_count}] Retired stalled lineage {lineage.lineage_id} from "
                        f"{family.family_id}: {persistent_stall_retirement}."
                    ),
                )
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
            # Backtest-positive gate: block paper/shadow promotion for negative backtest
            if lineage.current_stage in {PromotionStage.WALKFORWARD.value, PromotionStage.STRESS.value}:
                gate_pass, gate_reason = self._backtest_positive_gate(lineage, family)
                if not gate_pass:
                    if not any(str(b).startswith("backtest_gate:") for b in lineage.blockers):
                        lineage.blockers = list(dict.fromkeys(list(lineage.blockers) + [f"backtest_gate:{gate_reason}"]))
                        self.registry.save_lineage(lineage)
                        _append_recent_action(
                            recent_actions,
                            f"[cycle {self._cycle_count}] Backtest gate blocked {lineage.lineage_id}: {gate_reason}",
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
                self._mark_isolated_challenger_first_assessment_passed(
                    lineage,
                    row,
                    recent_actions=recent_actions,
                )
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

    def _retire_by_loss_streak(
        self,
        family: FactoryFamily,
        ranked_rows: List[Dict[str, Any]],
        *,
        recent_actions: List[str],
    ) -> None:
        """Retire lineages with consecutive negative evaluations."""
        max_streak = int(getattr(config, "FACTORY_MAX_LOSS_STREAK", 3))
        retired_ids = set(family.retired_lineage_ids)

        for row in ranked_rows:
            lineage = self.registry.load_lineage(str(row["lineage_id"]))
            if lineage is None or not lineage.active:
                continue
            # Don't retire champions via loss streak
            if lineage.lineage_id == family.champion_lineage_id:
                continue

            loss_streak = int(lineage.loss_streak or 0)
            if loss_streak >= max_streak:
                lineage.active = False
                lineage.retired_at = utc_now_iso()
                lineage.iteration_status = "retired"
                lineage.retirement_reason = f"loss_streak_{loss_streak}_exceeded_max_{max_streak}"
                lineage.blockers = list(dict.fromkeys(
                    list(lineage.blockers) + [lineage.retirement_reason]
                ))
                self._record_learning_memory(
                    lineage, row,
                    reason="retired_never_positive",
                )
                retired_ids.add(lineage.lineage_id)
                self.registry.save_lineage(lineage)
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Retired {lineage.lineage_id}: loss streak {loss_streak} >= {max_streak}",
                )

        family.retired_lineage_ids = sorted(retired_ids)
        family.shadow_challenger_ids = [lid for lid in family.shadow_challenger_ids if lid not in retired_ids]
        family.paper_challenger_ids = [lid for lid in family.paper_challenger_ids if lid not in retired_ids]
        self.registry.save_family(family)

    def _retire_by_backtest_ttl(
        self,
        family: FactoryFamily,
        *,
        recent_actions: List[str],
    ) -> None:
        """Retire lineages stuck in backtest stages too long without positive results.

        Models that have never traded are promoted to paper trial instead of
        being retired -- you cannot judge a model that has never executed.
        """
        ttl_hours = float(getattr(config, "FACTORY_BACKTEST_TTL_HOURS", 48))
        paper_trial_days = float(getattr(config, "FACTORY_PAPER_TRIAL_DAYS", 7))
        retired_ids = set(family.retired_lineage_ids)
        now = datetime.now(timezone.utc)

        for lineage in self.registry.lineages():
            if lineage.family_id != family.family_id or not lineage.active:
                continue
            if lineage.current_stage not in {
                PromotionStage.GOLDFISH_RUN.value,
                PromotionStage.WALKFORWARD.value,
            }:
                continue

            created_dt = _parse_iso_dt(lineage.created_at)
            if created_dt is None:
                continue

            age_hours = (now - created_dt).total_seconds() / 3600
            if age_hours < ttl_hours:
                continue

            gate_pass, gate_reason = self._backtest_positive_gate(lineage, family)
            if gate_pass:
                continue

            eval_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
            has_traded = any(
                int(getattr(b, "trade_count", 0) or 0) > 0
                for b in eval_by_stage.values()
            )

            if not has_traded:
                lineage.current_stage = PromotionStage.PAPER.value
                lineage.iteration_status = "paper_trial_no_backtest"
                lineage.blockers = list(dict.fromkeys(
                    list(lineage.blockers) + [f"paper_trial:{gate_reason}"]
                ))
                self.registry.save_lineage(lineage)
                _append_recent_action(
                    recent_actions,
                    f"[cycle {self._cycle_count}] Promoted {lineage.lineage_id} to paper trial (no backtest data, no trades yet)",
                )
                continue

            lineage.active = False
            lineage.retired_at = utc_now_iso()
            lineage.iteration_status = "retired"
            lineage.retirement_reason = f"backtest_ttl_{age_hours:.0f}h_exceeded_{ttl_hours:.0f}h"
            lineage.blockers = list(dict.fromkeys(
                list(lineage.blockers) + [lineage.retirement_reason]
            ))
            retired_ids.add(lineage.lineage_id)
            self.registry.save_lineage(lineage)
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Retired {lineage.lineage_id}: backtest TTL {age_hours:.0f}h > {ttl_hours:.0f}h without positive results",
            )

        paper_trial_ttl_hours = paper_trial_days * 24
        for lineage in self.registry.lineages():
            if lineage.family_id != family.family_id or not lineage.active:
                continue
            if lineage.current_stage != PromotionStage.PAPER.value:
                continue
            if lineage.iteration_status != "paper_trial_no_backtest":
                continue
            created_dt = _parse_iso_dt(lineage.created_at)
            if created_dt is None:
                continue
            age_hours = (now - created_dt).total_seconds() / 3600
            if age_hours < paper_trial_ttl_hours:
                continue
            eval_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
            has_traded = any(
                int(getattr(b, "trade_count", 0) or 0) > 0
                for b in eval_by_stage.values()
            )
            if has_traded:
                gate_pass, _ = self._backtest_positive_gate(lineage, family)
                if gate_pass:
                    lineage.iteration_status = "paper_trial_graduated"
                    lineage.blockers = [b for b in lineage.blockers if not b.startswith("paper_trial:")]
                    self.registry.save_lineage(lineage)
                    continue
            lineage.active = False
            lineage.retired_at = utc_now_iso()
            lineage.iteration_status = "retired"
            lineage.retirement_reason = f"paper_trial_expired_{age_hours:.0f}h"
            retired_ids.add(lineage.lineage_id)
            self.registry.save_lineage(lineage)
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Retired {lineage.lineage_id}: paper trial expired after {age_hours:.0f}h",
            )

        family.retired_lineage_ids = sorted(retired_ids)
        family.shadow_challenger_ids = [lid for lid in family.shadow_challenger_ids if lid not in retired_ids]
        family.paper_challenger_ids = [lid for lid in family.paper_challenger_ids if lid not in retired_ids]
        self.registry.save_family(family)

    _DATA_REFRESH_SCRIPTS: Dict[str, str] = {
        "binance_core": "scripts/refresh_binance_funding.py",
        "polymarket_core": "scripts/fetch_polymarket_history.py",
        "polymarket_history": "scripts/fetch_polymarket_history.py",
        "yahoo_stocks": "scripts/refresh_yahoo_data.py",
        "alpaca_stocks": "scripts/refresh_alpaca_data.py",
    }
    _data_refresh_cooldowns: Dict[str, float] = {}
    _DATA_REFRESH_COOLDOWN_SECS = 1800  # 30 minutes

    def _maybe_trigger_data_refresh(self, connector_snapshots: List[Dict[str, Any]]) -> None:
        now = time.time()
        triggered = set()
        for snapshot in connector_snapshots:
            if snapshot.get("ready"):
                continue
            cid = str(snapshot.get("connector_id") or "")
            script = self._DATA_REFRESH_SCRIPTS.get(cid)
            if not script or script in triggered:
                continue
            last = self._data_refresh_cooldowns.get(script, 0)
            if now - last < self._DATA_REFRESH_COOLDOWN_SECS:
                continue
            script_path = self.project_root / script
            if not script_path.exists():
                continue
            try:
                import subprocess as _sp
                _sp.Popen(
                    [sys.executable, str(script_path)],
                    cwd=str(self.project_root),
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
                self._data_refresh_cooldowns[script] = now
                triggered.add(script)
                logger.info("Triggered data refresh for %s via %s", cid, script)
            except Exception as exc:
                logger.warning("Failed to trigger data refresh for %s: %s", cid, exc)

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
        schedule = venue_schedule_class({"target_venues": lineage.target_venues})
        snapshot = summarize_execution_targets(lineage.target_portfolios, market_schedule=schedule)
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

    def _apply_execution_bridge_feedback(
        self,
        *,
        lineage_summaries: List[Dict[str, Any]],
        family_summaries: List[Dict[str, Any]],
        bridge_payload: Dict[str, Any],
        recent_actions: List[str],
    ) -> None:
        targets_by_lineage: Dict[str, Dict[str, Any]] = {}
        for target in list(bridge_payload.get("targets") or []):
            target_row = dict(target)
            for row in list(target_row.get("lineages") or []):
                lineage_id = str(row.get("lineage_id") or "").strip()
                if lineage_id:
                    targets_by_lineage[lineage_id] = target_row
        summaries_by_id = {
            str(item.get("lineage_id") or ""): item
            for item in lineage_summaries
            if str(item.get("lineage_id") or "").strip()
        }
        family_by_id = {
            str(item.get("family_id") or ""): item
            for item in family_summaries
            if str(item.get("family_id") or "").strip()
        }
        for lineage_id, target in targets_by_lineage.items():
            summary = summaries_by_id.get(lineage_id)
            if summary is None:
                continue
            activation_status = str(target.get("activation_status") or "").strip()
            if activation_status:
                summary["activation_status"] = activation_status
            summary["alias_runner_running"] = bool(target.get("running"))
            summary["bridge_target_portfolio"] = str(target.get("portfolio_id") or "")
            if str(summary.get("runtime_lane_kind") or "") == "isolated_challenger":
                runtime_target_portfolio = str(summary.get("runtime_target_portfolio") or target.get("portfolio_id") or "").strip()
                canonical_target_portfolio = str(summary.get("canonical_target_portfolio") or target.get("canonical_portfolio_id") or "").strip()
                if runtime_target_portfolio and runtime_target_portfolio != canonical_target_portfolio:
                    alias_evidence = build_portfolio_execution_evidence(runtime_target_portfolio)
                    summary["alias_evidence_issue_codes"] = list(alias_evidence.get("issue_codes") or [])
                    summary["live_paper_target_portfolio_id"] = runtime_target_portfolio
                    summary["live_paper_running"] = bool(alias_evidence.get("running"))
                    account = dict(alias_evidence.get("account") or {})
                    summary["live_paper_roi_pct"] = float(account.get("roi_pct", 0.0) or 0.0)
                    summary["live_paper_realized_pnl"] = float(account.get("realized_pnl", 0.0) or 0.0)
                    summary["live_paper_trade_count"] = int(account.get("trade_count", 0) or 0)
                    summary["live_paper_wins"] = int(account.get("wins", 0) or 0)
                    summary["live_paper_losses"] = int(account.get("losses", 0) or 0)
                    summary["live_paper_drawdown_pct"] = float(account.get("drawdown_pct", 0.0) or 0.0)
                    summary["execution_health_status"] = str(alias_evidence.get("health_status") or summary.get("execution_health_status") or "")
                    summary["execution_issue_codes"] = list(alias_evidence.get("issue_codes") or summary.get("execution_issue_codes") or [])
                    summary["execution_validation"] = dict(alias_evidence)
                    summary["alias_runner_running"] = bool(alias_evidence.get("running"))
                    summary["first_assessment"] = assessment_progress(
                        paper_days=int(summary.get("paper_days", 0) or 0),
                        trade_count=int(summary.get("live_paper_trade_count", 0) or 0),
                        labels=[
                            summary.get("family_id"),
                            summary.get("current_stage"),
                            runtime_target_portfolio,
                        ],
                        realized_roi_pct=float(summary.get("live_paper_roi_pct", 0.0) or 0.0),
                        current_stage=str(summary.get("current_stage") or ""),
                        phase="first",
                    )
                    summary["first_assessment_complete"] = bool(summary["first_assessment"].get("complete"))
                    summary["assessment"] = assessment_progress(
                        paper_days=int(summary.get("paper_days", 0) or 0),
                        trade_count=int(summary.get("live_paper_trade_count", 0) or 0),
                        labels=[
                            summary.get("family_id"),
                            summary.get("current_stage"),
                            runtime_target_portfolio,
                        ],
                        realized_roi_pct=float(summary.get("live_paper_roi_pct", 0.0) or 0.0),
                        current_stage=str(summary.get("current_stage") or ""),
                        phase="full",
                    )
                    lineage = self.registry.load_lineage(lineage_id)
                    if lineage is not None:
                        if bool(alias_evidence.get("running")) and lineage.iteration_status == "prepare_isolated_lane":
                            lineage.iteration_status = "isolated_lane_active"
                            self.registry.save_lineage(lineage)
                            summary["iteration_status"] = lineage.iteration_status
                            _append_recent_action(
                                recent_actions,
                                f"[cycle {self._cycle_count}] Isolated lane runner is active for {lineage.lineage_id} on {runtime_target_portfolio}.",
                            )
                        failure_reason = self._isolated_challenger_first_assessment_failure_reason(summary)
                        if failure_reason and lineage.active:
                            lineage.active = False
                            lineage.retired_at = utc_now_iso()
                            lineage.iteration_status = "retired"
                            lineage.retirement_reason = failure_reason
                            lineage.blockers = list(dict.fromkeys(list(lineage.blockers or []) + [failure_reason]))
                            self._record_learning_memory(lineage, summary, reason=failure_reason)
                            self.registry.save_lineage(lineage)
                            summary["active"] = False
                            summary["iteration_status"] = "retired"
                            summary["retired_at"] = lineage.retired_at
                            summary["retirement_reason"] = failure_reason
                            family_row = family_by_id.get(str(summary.get("family_id") or ""))
                            if family_row is not None:
                                family_row["isolated_evidence_ready"] = False
                                family_row["active_lineage_count"] = max(0, int(family_row.get("active_lineage_count", 0) or 0) - 1)
                                family_row["retired_lineage_count"] = int(family_row.get("retired_lineage_count", 0) or 0) + 1
                            _append_recent_action(
                                recent_actions,
                                (
                                    f"[cycle {self._cycle_count}] Retired isolated challenger {lineage.lineage_id} after failed "
                                    f"alias first assessment: {failure_reason}."
                                ),
                            )
                            continue
                        if (
                            bool(summary.get("first_assessment_complete"))
                            and str(lineage.iteration_status or "").strip() == "isolated_lane_active"
                            and not self._isolated_challenger_first_assessment_failure_reason(summary)
                        ):
                            lineage.iteration_status = "isolated_lane_first_assessment_passed"
                            self.registry.save_lineage(lineage)
                            summary["iteration_status"] = lineage.iteration_status
                            _append_recent_action(
                                recent_actions,
                                (
                                    f"[cycle {self._cycle_count}] Isolated challenger {lineage.lineage_id} passed first paper "
                                    f"assessment on alias lane {runtime_target_portfolio}."
                                ),
                            )
                        issue_codes = {str(item) for item in (alias_evidence.get('issue_codes') or []) if str(item).strip()}
                        if bool(alias_evidence.get("running")) and issue_codes.intersection({"trade_stalled", "training_stalled", "stalled_model"}):
                            next_status = "review_requested_replace" if int(lineage.tweak_count or 0) >= int(lineage.max_tweaks or 2) else "review_requested_rework"
                            if lineage.iteration_status not in {next_status, "review_requested_replace"}:
                                lineage.iteration_status = next_status
                                self.registry.save_lineage(lineage)
                                summary["iteration_status"] = lineage.iteration_status
                        if activation_status == "start_failed":
                            summary["isolate_evidence_start_failed"] = True
        for family_id, family in family_by_id.items():
            challenger_id = str(family.get("isolated_challenger_lineage_id") or "")
            challenger_summary = summaries_by_id.get(challenger_id, {})
            family["activation_status"] = str(challenger_summary.get("activation_status") or "")
            family["alias_runner_running"] = bool(challenger_summary.get("alias_runner_running"))
            if challenger_summary:
                runtime_target = str(challenger_summary.get("runtime_target_portfolio") or "").strip()
                canonical_target = str(challenger_summary.get("canonical_target_portfolio") or "").strip()
                family["isolated_evidence_ready"] = bool(
                    runtime_target
                    and runtime_target != canonical_target
                    and bool(challenger_summary.get("active", True))
                    and not self._isolated_lane_stale_without_fresh_evidence(challenger_summary)
                    and (
                        bool(challenger_summary.get("alias_runner_running"))
                        or int(challenger_summary.get("live_paper_trade_count", 0) or 0) > 0
                        or abs(float(challenger_summary.get("live_paper_realized_pnl", 0.0) or 0.0)) > 0.0
                    )
                )

    def _operator_signals(
        self,
        lineage_summaries: List[Dict[str, Any]],
        family_summaries: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        def _maintenance_review_recent(payload: Dict[str, Any]) -> bool:
            reviewed_at = _parse_iso_dt(payload.get("last_maintenance_review_at"))
            if reviewed_at is None:
                return False
            cooldown_hours = max(
                0,
                int(getattr(config, "FACTORY_MAINTENANCE_QUEUE_REVIEW_COOLDOWN_HOURS", 12) or 12),
            )
            return reviewed_at >= datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)

        def _should_suppress_recent_review(item: Dict[str, Any], lineage_payload: Dict[str, Any]) -> bool:
            if not lineage_payload or not _maintenance_review_recent(lineage_payload):
                return False
            status = str(lineage_payload.get("last_maintenance_review_status") or "").strip().lower()
            if status not in {"completed", "success", "succeeded"}:
                return False
            action = str(item.get("action") or "").strip().lower()
            reviewed_action = str(lineage_payload.get("last_maintenance_review_action") or "").strip().lower()
            if action == "review_due":
                return True
            if reviewed_action and action == reviewed_action:
                return True
            if action == "family_autopilot" and reviewed_action:
                return True
            return False

        def _compact_maintenance_queue(
            queue: List[Dict[str, Any]],
            *,
            rows_by_lineage_map: Dict[str, Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
            family_autopilot_actions = {
                str(item.get("family_id") or "").strip()
                for item in queue
                if str(item.get("action") or "").strip() == "family_autopilot"
            }
            filtered: List[Dict[str, Any]] = []
            for raw in queue:
                item = dict(raw)
                family_id = str(item.get("family_id") or "").strip()
                lineage_id = str(item.get("lineage_id") or "").strip()
                lineage_payload = dict(rows_by_lineage_map.get(lineage_id) or {})
                if lineage_payload and not bool(lineage_payload.get("active", True)):
                    continue
                if lineage_payload and _should_suppress_recent_review(item, lineage_payload):
                    continue
                action = str(item.get("action") or "").strip().lower()
                if (
                    family_id in family_autopilot_actions
                    and action in {"replace", "retrain", "rework", "retire", "review_due"}
                    and str(item.get("source") or "").strip() != "family_autopilot"
                    and not bool(item.get("requires_human"))
                ):
                    continue
                filtered.append(item)

            deduped_by_lineage: Dict[str, Dict[str, Any]] = {}
            family_scoped: List[Dict[str, Any]] = []
            for item in filtered:
                lineage_id = str(item.get("lineage_id") or "").strip()
                if not lineage_id:
                    family_scoped.append(item)
                    continue
                current = deduped_by_lineage.get(lineage_id)
                candidate_key = (
                    int(item.get("priority", 9) if item.get("priority") is not None else 9),
                    0 if item.get("requires_human") else 1,
                    0 if str(item.get("source") or "") == "family_autopilot" else 1,
                )
                if current is None:
                    deduped_by_lineage[lineage_id] = item
                    continue
                current_key = (
                    int(current.get("priority", 9) if current.get("priority") is not None else 9),
                    0 if current.get("requires_human") else 1,
                    0 if str(current.get("source") or "") == "family_autopilot" else 1,
                )
                if candidate_key < current_key:
                    deduped_by_lineage[lineage_id] = item

            per_family_counts: Dict[str, int] = defaultdict(int)
            per_family_cap = max(1, int(getattr(config, "FACTORY_MAINTENANCE_QUEUE_MAX_PER_FAMILY", 3) or 3))
            compacted: List[Dict[str, Any]] = []
            ordered = sorted(
                list(deduped_by_lineage.values()) + family_scoped,
                key=lambda item: (
                    int(item.get("priority", 9) if item.get("priority") is not None else 9),
                    0 if item.get("execution_health_status") == "critical" else 1,
                    item.get("family_id") or "",
                    item.get("lineage_id") or "",
                ),
            )
            for item in ordered:
                family_id = str(item.get("family_id") or "").strip()
                if family_id:
                    if per_family_counts[family_id] >= per_family_cap and str(item.get("action") or "") != "family_autopilot":
                        continue
                    if str(item.get("action") or "") != "family_autopilot":
                        per_family_counts[family_id] += 1
                compacted.append(item)
            return compacted

        def _target_portfolio_id(payload: Dict[str, Any]) -> str:
            return str(
                payload.get("runtime_target_portfolio")
                or payload.get("live_paper_target_portfolio_id")
                or payload.get("curated_target_portfolio_id")
                or ""
            ).strip()

        def _has_independent_live_evidence(payload: Dict[str, Any]) -> bool:
            if str(payload.get("evidence_source_type") or "") != "current_live_paper":
                return False
            if str(payload.get("runtime_lane_kind") or "").strip() != "isolated_challenger":
                return True
            target = _target_portfolio_id(payload)
            if not target:
                return False
            if self._isolated_lane_stale_without_fresh_evidence(payload):
                return False
            lane_rows = lane_rows_by_family.get(str(payload.get("family_id") or ""), {})
            incumbent_target = _target_portfolio_id(dict(lane_rows.get("primary_incumbent") or {}))
            if incumbent_target and target == incumbent_target:
                return False
            canonical_target = str(payload.get("canonical_target_portfolio") or "").strip()
            runtime_target = str(payload.get("runtime_target_portfolio") or "").strip()
            if runtime_target and canonical_target and runtime_target == canonical_target:
                return False
            return True

        def _replacement_pressure(payload: Dict[str, Any]) -> tuple[bool, str | None]:
            maintenance_action = str(payload.get("maintenance_request_action") or "").strip().lower()
            iteration_status = str(payload.get("iteration_status") or "").strip().lower()
            if maintenance_action in {"replace", "retire"}:
                return True, maintenance_action
            if iteration_status in {"review_requested_replace", "review_recommended_retire"}:
                return True, iteration_status
            return False, maintenance_action or iteration_status or None

        positive_models: List[Dict[str, Any]] = []
        research_positive_models: List[Dict[str, Any]] = []
        paper_qualification_queue: List[Dict[str, Any]] = []
        first_assessment_candidates: List[Dict[str, Any]] = []
        potential_winners: List[Dict[str, Any]] = []
        escalation_candidates: List[Dict[str, Any]] = []
        winner_candidates: List[Dict[str, Any]] = []
        human_action_required: List[Dict[str, Any]] = []
        maintenance_queue: List[Dict[str, Any]] = []
        lane_rows_by_family: Dict[str, Dict[str, Dict[str, Any]]] = {}
        rows_by_family: Dict[str, List[Dict[str, Any]]] = {}
        rows_by_lineage: Dict[str, Dict[str, Any]] = {}
        for row in lineage_summaries:
            family_id = str(row.get("family_id") or "")
            lineage_id = str(row.get("lineage_id") or "")
            if family_id:
                rows_by_family.setdefault(family_id, []).append(dict(row))
            if lineage_id:
                rows_by_lineage[lineage_id] = dict(row)
            if not bool(row.get("active", True)):
                continue
            runtime_lane_kind = str(row.get("runtime_lane_kind") or "").strip()
            if family_id and runtime_lane_kind in {"primary_incumbent", "isolated_challenger"}:
                lane_rows_by_family.setdefault(family_id, {})[runtime_lane_kind] = dict(row)
            live_roi = float(row.get("live_paper_roi_pct", 0.0) or 0.0)
            live_trade_count = int(row.get("live_paper_trade_count", 0) or 0)
            live_paper_days = int(row.get("live_paper_days", row.get("paper_days", 0)) or 0)
            research_roi = float(row.get("research_monthly_roi_pct", row.get("monthly_roi_pct", 0.0)) or 0.0)
            research_trade_count = int(row.get("research_trade_count", row.get("trade_count", 0)) or 0)
            health_status = str(row.get("execution_health_status") or "")
            full_assessment = assessment_progress(
                paper_days=live_paper_days,
                trade_count=live_trade_count,
                labels=[row.get("family_id"), row.get("current_stage"), row.get("live_paper_target_portfolio_id")],
                realized_roi_pct=live_roi,
                current_stage=str(row.get("current_stage") or ""),
                phase="full",
            )
            first_assessment = assessment_progress(
                paper_days=live_paper_days,
                trade_count=live_trade_count,
                labels=[row.get("family_id"), row.get("current_stage"), row.get("live_paper_target_portfolio_id")],
                realized_roi_pct=live_roi,
                current_stage=str(row.get("current_stage") or ""),
                phase="first",
            )
            assessment_complete = bool(full_assessment.get("complete"))
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
                        "paper_days": live_paper_days,
                        "execution_health_status": health_status,
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": live_target_portfolio_id or None,
                        "evidence_source_type": evidence_source_type,
                        "first_assessment_complete": bool(first_assessment.get("complete")),
                        "first_assessment_status": first_assessment.get("status"),
                        "assessment_complete": assessment_complete,
                        "independent_live_evidence": False,
                        "replacement_pressure": False,
                        "replacement_pressure_reason": None,
                        "runtime_lane_kind": str(row.get("runtime_lane_kind") or ""),
                        "runtime_target_portfolio": row.get("runtime_target_portfolio"),
                        "canonical_target_portfolio": row.get("canonical_target_portfolio"),
                        "maintenance_request_action": str(row.get("maintenance_request_action") or ""),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "manifest_id": row.get("manifest_id"),
                        "research_roi_pct": round(research_roi, 4),
                        "research_trade_count": research_trade_count,
                    }
                )
            if (
                evidence_source_type == "current_live_paper"
                and bool(first_assessment.get("complete"))
                and not assessment_complete
            ):
                first_assessment_candidates.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "roi_pct": round(live_roi, 4),
                        "trade_count": live_trade_count,
                        "paper_days": live_paper_days,
                        "execution_health_status": health_status,
                        "first_assessment_status": first_assessment.get("status"),
                        "assessment_complete": False,
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": live_target_portfolio_id or None,
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
                winner_candidates.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "roi_pct": round(live_roi, 4),
                        "trade_count": live_trade_count,
                        "paper_days": live_paper_days,
                        "curated_family_rank": row.get("curated_family_rank"),
                        "curated_target_portfolio_id": live_target_portfolio_id or None,
                        "evidence_source_type": evidence_source_type,
                        "runtime_lane_kind": str(row.get("runtime_lane_kind") or ""),
                        "runtime_target_portfolio": row.get("runtime_target_portfolio"),
                        "canonical_target_portfolio": row.get("canonical_target_portfolio"),
                        "manifest_id": row.get("manifest_id"),
                        "assessment_complete": True,
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
            activation_status = str(row.get("activation_status") or "").strip().lower()
            if activation_status == "start_failed":
                maintenance_queue.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "action": "isolate_evidence_start_failed",
                        "reason": "Isolated challenger alias runner failed to start and needs follow-through.",
                        "source": "execution_bridge",
                        "priority": 2,
                        "execution_health_status": health_status,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "trade_count": live_trade_count,
                        "roi_pct": round(live_roi, 4),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "requires_human": bool(row.get("last_debug_requires_human")),
                    }
                )
            elif self._isolated_lane_stale_without_fresh_evidence(row):
                maintenance_queue.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "action": "isolate_evidence_stalled",
                        "reason": "Isolated challenger lane is consuming paper capacity without publishing fresh distinct evidence.",
                        "source": "execution_bridge",
                        "priority": 3,
                        "execution_health_status": health_status,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "trade_count": live_trade_count,
                        "roi_pct": round(live_roi, 4),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "requires_human": bool(row.get("last_debug_requires_human")),
                    }
                )
            elif (
                str(row.get("runtime_lane_kind") or "").strip() == "isolated_challenger"
                and str(row.get("iteration_status") or "").strip() == "isolated_lane_active"
            ):
                maintenance_queue.append(
                    {
                        "family_id": str(row.get("family_id") or ""),
                        "lineage_id": str(row.get("lineage_id") or ""),
                        "current_stage": str(row.get("current_stage") or ""),
                        "action": "isolated_lane_active",
                        "reason": "Isolated challenger lane is active and now needs distinct live paper evidence.",
                        "source": "execution_bridge",
                        "priority": 7,
                        "execution_health_status": health_status,
                        "paper_days": int(row.get("paper_days", 0) or 0),
                        "trade_count": live_trade_count,
                        "roi_pct": round(live_roi, 4),
                        "iteration_status": str(row.get("iteration_status") or ""),
                        "requires_human": False,
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
        runtime_candidate_stages = {"shadow", "paper", "canary_ready", "live_ready"}
        for family_id, family_rows in rows_by_family.items():
            prefer_challenger, lane_reason = decide_runtime_lane_policy(family_rows)
            if not prefer_challenger:
                continue
            best_challenger = None
            eligible_challengers = [
                row
                for row in family_rows
                if str(row.get("role") or "").strip().lower() in {"paper_challenger", "shadow_challenger", "moonshot"}
                and str(row.get("current_stage") or "").strip() in runtime_candidate_stages
            ]
            if eligible_challengers:
                best_challenger = sorted(
                    eligible_challengers,
                    key=lambda item: self._runtime_family_lane_selection_key(item, prefer_challenger=True),
                )[0]
                if lane_reason in {"paper_qualification_needed", "incumbent_trade_stalled"}:
                    paper_qualification_queue.append(
                        {
                            "family_id": family_id,
                            "lineage_id": str(best_challenger.get("lineage_id") or ""),
                            "current_stage": str(best_challenger.get("current_stage") or ""),
                            "reason": (
                                "Stalled incumbent should yield a paper lane to a qualified challenger."
                                if lane_reason == "incumbent_trade_stalled"
                                else "Positive research evidence deserves a first live paper read on the real feed."
                            ),
                            "source": "lane_policy",
                            "priority": 2 if lane_reason == "incumbent_trade_stalled" else 3,
                            "lane_reason": lane_reason,
                            "execution_health_status": str(best_challenger.get("execution_health_status") or ""),
                            "research_roi_pct": round(float(best_challenger.get("monthly_roi_pct", 0.0) or 0.0), 4),
                            "research_trade_count": int(best_challenger.get("trade_count", 0) or 0),
                            "live_trade_count": int(best_challenger.get("live_paper_trade_count", 0) or 0),
                            "paper_days": int(best_challenger.get("paper_days", 0) or 0),
                            "iteration_status": str(best_challenger.get("iteration_status") or ""),
                        }
                    )
            if eligible_challengers:
                continue
            fallback_challengers = [
                row
                for row in family_rows
                if str(row.get("role") or "").strip().lower() in {"paper_challenger", "shadow_challenger", "moonshot"}
            ]
            if not fallback_challengers:
                continue
            best_challenger = sorted(
                fallback_challengers,
                key=lambda item: self._runtime_family_lane_selection_key(item, prefer_challenger=True),
            )[0]
            maintenance_queue.append(
                {
                    "family_id": family_id,
                    "lineage_id": str(best_challenger.get("lineage_id") or ""),
                    "current_stage": str(best_challenger.get("current_stage") or ""),
                    "action": "prepare_isolated_lane",
                    "reason": (
                        "Family policy prefers an isolated challenger lane, but no challenger has reached a runnable "
                        "shadow/paper stage yet."
                    ),
                    "source": "lane_policy",
                    "priority": 4,
                    "execution_health_status": str(best_challenger.get("execution_health_status") or ""),
                    "paper_days": int(best_challenger.get("paper_days", 0) or 0),
                    "trade_count": int(best_challenger.get("trade_count", 0) or 0),
                    "roi_pct": round(float(best_challenger.get("monthly_roi_pct", 0.0) or 0.0), 4),
                    "iteration_status": str(best_challenger.get("iteration_status") or ""),
                    "requires_human": False,
                    "lane_reason": lane_reason,
                }
            )
        for family_id, lane_rows in lane_rows_by_family.items():
            incumbent = dict(lane_rows.get("primary_incumbent") or {})
            challenger = dict(lane_rows.get("isolated_challenger") or {})
            if not incumbent or not challenger:
                continue
            incumbent_target = str(
                incumbent.get("runtime_target_portfolio")
                or incumbent.get("live_paper_target_portfolio_id")
                or incumbent.get("curated_target_portfolio_id")
                or ""
            ).strip()
            challenger_target = str(
                challenger.get("runtime_target_portfolio")
                or challenger.get("live_paper_target_portfolio_id")
                or challenger.get("curated_target_portfolio_id")
                or ""
            ).strip()
            if not challenger_target:
                maintenance_queue.append(
                    {
                        "family_id": family_id,
                        "lineage_id": str(challenger.get("lineage_id") or ""),
                        "current_stage": str(challenger.get("current_stage") or ""),
                        "action": "isolate_evidence",
                        "reason": "Selected isolated challenger still has no dedicated paper evidence target.",
                        "source": "lane_policy",
                        "priority": 5,
                        "execution_health_status": str(challenger.get("execution_health_status") or ""),
                        "paper_days": int(challenger.get("paper_days", 0) or 0),
                        "trade_count": int(challenger.get("live_paper_trade_count", 0) or 0),
                        "roi_pct": round(float(challenger.get("live_paper_roi_pct", 0.0) or 0.0), 4),
                        "iteration_status": str(challenger.get("iteration_status") or ""),
                        "requires_human": False,
                    }
                )
            elif incumbent_target and challenger_target == incumbent_target:
                maintenance_queue.append(
                    {
                        "family_id": family_id,
                        "lineage_id": str(challenger.get("lineage_id") or ""),
                        "current_stage": str(challenger.get("current_stage") or ""),
                        "action": "isolate_evidence",
                        "reason": "Selected isolated challenger is still sharing the incumbent paper evidence target.",
                        "source": "lane_policy",
                        "priority": 5,
                        "execution_health_status": str(challenger.get("execution_health_status") or ""),
                        "paper_days": int(challenger.get("paper_days", 0) or 0),
                        "trade_count": int(challenger.get("live_paper_trade_count", 0) or 0),
                        "roi_pct": round(float(challenger.get("live_paper_roi_pct", 0.0) or 0.0), 4),
                        "iteration_status": str(challenger.get("iteration_status") or ""),
                        "requires_human": False,
                    }
                )
        weak_families: List[Dict[str, Any]] = []
        family_summary_by_id = {
            str(item.get("family_id") or ""): dict(item)
            for item in (family_summaries or [])
            if str(item.get("family_id") or "").strip()
        }
        for family_id, family_rows in rows_by_family.items():
            plan = self._family_autopilot_plan(
                family_id,
                family_rows,
                family_summary=family_summary_by_id.get(family_id),
            )
            if not plan.get("weak_family"):
                continue
            weak_families.append(
                {
                    "family_id": family_id,
                    "autopilot_status": plan.get("autopilot_status"),
                    "autopilot_actions": list(plan.get("autopilot_actions") or []),
                    "autopilot_reason": str(plan.get("autopilot_reason") or ""),
                    "autopilot_issue_codes": list(plan.get("autopilot_issue_codes") or []),
                    "autopilot_trade_count": int(plan.get("autopilot_trade_count", 0) or 0),
                    "autopilot_live_roi_pct": float(plan.get("autopilot_live_roi_pct", 0.0) or 0.0),
                    "autopilot_live_win_rate": float(plan.get("autopilot_live_win_rate", 0.0) or 0.0),
                    "autopilot_health_status": str(plan.get("autopilot_health_status") or ""),
                }
            )
            if not plan.get("autopilot_actions"):
                continue
            maintenance_queue.append(
                {
                    "family_id": family_id,
                    "lineage_id": str(plan.get("autopilot_target_lineage_id") or ""),
                    "current_stage": str(plan.get("autopilot_target_stage") or ""),
                    "action": "family_autopilot",
                    "reason": str(plan.get("autopilot_reason") or "family needs autonomous maintenance"),
                    "source": "family_autopilot",
                    "priority": (
                        1
                        if "human_action_required" in (plan.get("autopilot_actions") or [])
                        else 3
                        if "replace" in (plan.get("autopilot_actions") or [])
                        else 4
                    ),
                    "execution_health_status": str(plan.get("autopilot_health_status") or ""),
                    "paper_days": 0,
                    "trade_count": int(plan.get("autopilot_trade_count", 0) or 0),
                    "roi_pct": round(float(plan.get("autopilot_live_roi_pct", 0.0) or 0.0), 4),
                    "iteration_status": ",".join(list(plan.get("autopilot_actions") or [])),
                    "requires_human": "human_action_required" in (plan.get("autopilot_actions") or []),
                    "scope": "family",
                    "recommended_actions": list(plan.get("autopilot_actions") or []),
                    "live_win_rate": float(plan.get("autopilot_live_win_rate", 0.0) or 0.0),
                }
            )
        for candidate in winner_candidates:
            if not _has_independent_live_evidence(candidate):
                continue
            potential_winners.append(
                {
                    "family_id": candidate["family_id"],
                    "lineage_id": candidate["lineage_id"],
                    "current_stage": candidate["current_stage"],
                    "roi_pct": candidate["roi_pct"],
                    "trade_count": candidate["trade_count"],
                    "paper_days": candidate["paper_days"],
                    "curated_family_rank": candidate["curated_family_rank"],
                    "manifest_id": candidate["manifest_id"],
                    "assessment_complete": True,
                }
            )
            escalation_candidates.append(
                {
                    "family_id": candidate["family_id"],
                    "lineage_id": candidate["lineage_id"],
                    "current_stage": candidate["current_stage"],
                    "target_action": "operator_review_for_real_trading",
                    "reason": "live_ready leader with positive live paper ROI and healthy execution",
                    "roi_pct": candidate["roi_pct"],
                    "trade_count": candidate["trade_count"],
                    "paper_days": candidate["paper_days"],
                    "curated_family_rank": candidate["curated_family_rank"],
                    "curated_target_portfolio_id": candidate["curated_target_portfolio_id"],
                    "evidence_source_type": candidate["evidence_source_type"],
                    "manifest_id": candidate["manifest_id"],
                }
            )
        for item in positive_models:
            independent = _has_independent_live_evidence(item)
            replacement_pressure, replacement_reason = _replacement_pressure(item)
            item["independent_live_evidence"] = independent
            item["shared_evidence_risk"] = not independent
            item["replacement_pressure"] = replacement_pressure
            item["replacement_pressure_reason"] = replacement_reason
        positive_models.sort(
            key=lambda item: (
                0 if item.get("independent_live_evidence") else 1,
                0 if not item.get("replacement_pressure") else 1,
                -float(item.get("roi_pct", 0.0) or 0.0),
                -int(item.get("trade_count", 0) or 0),
            )
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
        for item in maintenance_queue:
            lineage_payload = rows_by_lineage.get(str(item.get("lineage_id") or ""))
            if not lineage_payload:
                continue
            item["last_maintenance_review_at"] = lineage_payload.get("last_maintenance_review_at")
            item["last_maintenance_review_status"] = lineage_payload.get("last_maintenance_review_status")
            item["last_maintenance_review_action"] = lineage_payload.get("last_maintenance_review_action")
            item["last_maintenance_review_summary"] = lineage_payload.get("last_maintenance_review_summary")
            item["last_maintenance_review_artifact_path"] = lineage_payload.get("last_maintenance_review_artifact_path")
        maintenance_queue = _compact_maintenance_queue(
            maintenance_queue,
            rows_by_lineage_map=rows_by_lineage,
        )
        maintenance_queue.sort(
            key=lambda item: (
                int(item.get("priority", 9)) if item.get("priority") is not None else 9,
                0 if item.get("execution_health_status") == "critical" else 1,
                item.get("family_id") or "",
                item.get("lineage_id") or "",
            )
        )
        weak_families.sort(
            key=lambda item: (
                0 if item.get("autopilot_health_status") == "critical" else 1,
                -len(list(item.get("autopilot_actions") or [])),
                -float(item.get("autopilot_live_roi_pct", 0.0) or 0.0),
                item.get("family_id") or "",
            )
        )
        paper_qualification_queue.sort(
            key=lambda item: (
                int(item.get("priority", 9) if item.get("priority") is not None else 9),
                0 if item.get("execution_health_status") in {"healthy", "warning"} else 1,
                -float(item.get("research_roi_pct", 0.0) or 0.0),
                -int(item.get("research_trade_count", 0) or 0),
                item.get("family_id") or "",
                item.get("lineage_id") or "",
            )
        )
        return {
            "positive_models": positive_models[:12],
            "research_positive_models": research_positive_models[:12],
            "paper_qualification_queue": paper_qualification_queue[:12],
            "first_assessment_candidates": first_assessment_candidates[:12],
            "potential_winners": potential_winners[:8],
            "escalation_candidates": escalation_candidates[:8],
            "human_action_required": human_action_required[:8],
            "weak_families": weak_families[:8],
            "maintenance_queue": maintenance_queue[:16],
        }

    def _runtime_family_lane_selection_key(
        self,
        row: Dict[str, Any],
        *,
        prefer_challenger: bool,
    ) -> tuple[Any, ...]:
        return runtime_lane_selection_key(row, prefer_challenger=prefer_challenger)

    def _runtime_family_lanes(
        self,
        family: FactoryFamily,
        ranked: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        active = [dict(item) for item in ranked if item.get("active", True)]
        champion = next(
            (item for item in active if str(item.get("lineage_id") or "") == family.champion_lineage_id),
            active[0] if active else None,
        )
        prefer_challenger, lane_reason = decide_runtime_lane_policy(active)
        challengers = [
            item
            for item in active
            if str(item.get("lineage_id") or "") != str((champion or {}).get("lineage_id") or "")
        ]
        challengers = sorted(
            challengers,
            key=lambda item: self._runtime_family_lane_selection_key(item, prefer_challenger=True),
        )
        isolated = challengers[0] if challengers else None
        return {
            "primary_incumbent": champion,
            "isolated_challenger": isolated,
            "prefer_challenger_lane": prefer_challenger,
            "runtime_lane_reason": lane_reason,
        }

    def _prepared_isolated_lane_candidate(
        self,
        family: FactoryFamily,
        ranked: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        runtime_lanes = self._runtime_family_lanes(family, ranked)
        if not bool(runtime_lanes.get("prefer_challenger_lane")):
            return {}
        isolated = dict(runtime_lanes.get("isolated_challenger") or {})
        if not isolated:
            return {}
        if str(isolated.get("current_stage") or "").strip() in {
            PromotionStage.SHADOW.value,
            PromotionStage.PAPER.value,
            PromotionStage.CANARY_READY.value,
            PromotionStage.LIVE_READY.value,
        }:
            return {}
        isolated["runtime_lane_reason"] = str(runtime_lanes.get("runtime_lane_reason") or "")
        return isolated

    def _apply_isolated_lane_preparation(
        self,
        family: FactoryFamily,
        ranked_rows: Sequence[Dict[str, Any]],
        *,
        recent_actions: List[str],
    ) -> str | None:
        candidate = self._prepared_isolated_lane_candidate(family, ranked_rows)
        lineage_id = str(candidate.get("lineage_id") or "").strip()
        if not lineage_id:
            return None
        lineage = self.registry.load_lineage(lineage_id)
        if lineage is None or not lineage.active:
            return None
        blockers = list(lineage.blockers or [])
        if "prepare_isolated_lane" not in blockers:
            blockers.append("prepare_isolated_lane")
        lineage.blockers = list(dict.fromkeys(blockers))
        previous_status = str(lineage.iteration_status or "")
        lineage.iteration_status = "prepare_isolated_lane"
        self.registry.save_lineage(lineage)
        if previous_status != "prepare_isolated_lane":
            reason = str(candidate.get("runtime_lane_reason") or "lane_policy")
            _append_recent_action(
                recent_actions,
                (
                    f"[cycle {self._cycle_count}] Prepared isolated lane candidate {lineage.lineage_id} for "
                    f"{family.family_id} ({reason}) while it advances toward runnable shadow/paper stages."
                ),
            )
        return lineage.lineage_id

    def _isolated_lane_activation_eligible(self, lineage: LineageRecord) -> bool:
        if not lineage.active or bool(lineage.last_debug_requires_human):
            return False
        latest_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
        walkforward = latest_by_stage.get(EvaluationStage.WALKFORWARD.value)
        stress = latest_by_stage.get(EvaluationStage.STRESS.value)
        if walkforward is None or stress is None:
            return False
        if list(walkforward.hard_vetoes or []) or list(stress.hard_vetoes or []):
            return False
        execution_validation = self._execution_validation_snapshot(lineage)
        issue_codes = {str(item) for item in (execution_validation.get("issue_codes") or []) if str(item).strip()}
        if issue_codes.intersection({"untrainable_model", "training_stalled"}):
            return False
        return True

    def _activate_prepared_isolated_lane(
        self,
        family: FactoryFamily,
        prepared_lineage_id: str | None,
        *,
        recent_actions: List[str],
    ) -> str | None:
        lineage_id = str(prepared_lineage_id or "").strip()
        if not lineage_id:
            return None
        lineage = self.registry.load_lineage(lineage_id)
        if lineage is None or not self._isolated_lane_activation_eligible(lineage):
            return None
        if lineage.current_stage not in {PromotionStage.WALKFORWARD.value, PromotionStage.STRESS.value}:
            return lineage.lineage_id if lineage.current_stage in {
                PromotionStage.SHADOW.value,
                PromotionStage.PAPER.value,
                PromotionStage.CANARY_READY.value,
                PromotionStage.LIVE_READY.value,
            } else None
        transitioned = self.registry.cas_transition(
            lineage.lineage_id,
            expected_stage=lineage.current_stage,
            next_stage=PromotionStage.SHADOW.value,
            blockers=[item for item in (lineage.blockers or []) if str(item) != "prepare_isolated_lane"],
            decision={
                "source": "isolated_lane_activation",
                "reason": "prepared isolated challenger satisfied pre-paper evidence and is now shadow-runnable",
                "next_stage": PromotionStage.SHADOW.value,
            },
        )
        if transitioned:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Activated prepared isolated challenger {lineage.lineage_id} to shadow for {family.family_id}.",
            )
            return lineage.lineage_id
        refreshed = self.registry.load_lineage(lineage.lineage_id)
        if refreshed is not None and refreshed.current_stage == PromotionStage.SHADOW.value:
            return refreshed.lineage_id
        return None

    def _mark_incubating_family_graduated(
        self,
        family: FactoryFamily,
        *,
        reason: str,
        recent_actions: List[str],
    ) -> None:
        family.incubation_status = "graduated"
        family.incubation_decided_at = utc_now_iso()
        family.incubation_decision_reason = reason
        notes = list(family.incubation_notes or [])
        if "graduated_to_runtime_family" not in notes:
            notes.append("graduated_to_runtime_family")
        if reason not in notes:
            notes.append(reason)
        family.incubation_notes = notes
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Graduated incubating family {family.family_id}: {reason}.",
        )

    def _retire_incubating_family(
        self,
        family: FactoryFamily,
        ranked_rows: Sequence[Dict[str, Any]],
        *,
        reason: str,
        recent_actions: List[str],
        lineage_summary_by_id: Dict[str, Dict[str, Any]],
    ) -> None:
        retired_ids = set(family.retired_lineage_ids)
        for row in ranked_rows:
            lineage_id = str(row.get("lineage_id") or "").strip()
            if not lineage_id:
                continue
            lineage = self.registry.load_lineage(lineage_id)
            if lineage is None or not lineage.active:
                continue
            lineage.active = False
            lineage.retired_at = utc_now_iso()
            lineage.iteration_status = "retired"
            lineage.retirement_reason = reason
            lineage.blockers = list(dict.fromkeys(list(lineage.blockers or []) + [reason]))
            self.registry.save_lineage(lineage)
            retired_ids.add(lineage.lineage_id)
            summary = lineage_summary_by_id.get(lineage.lineage_id)
            if summary is not None:
                summary["active"] = False
                summary["retired_at"] = lineage.retired_at
                summary["retirement_reason"] = reason
            self._record_learning_memory(lineage, row, reason=reason)
            self.registry.save_lineage(lineage)
        family.incubation_status = "retired"
        family.incubation_decided_at = utc_now_iso()
        family.incubation_decision_reason = reason
        family.retired_lineage_ids = sorted(retired_ids)
        family.shadow_challenger_ids = [lineage_id for lineage_id in family.shadow_challenger_ids if lineage_id not in retired_ids]
        family.paper_challenger_ids = [lineage_id for lineage_id in family.paper_challenger_ids if lineage_id not in retired_ids]
        notes = list(family.incubation_notes or [])
        if "retired_from_incubation" not in notes:
            notes.append("retired_from_incubation")
        if reason not in notes:
            notes.append(reason)
        family.incubation_notes = notes
        _append_recent_action(
            recent_actions,
            f"[cycle {self._cycle_count}] Retired incubating family {family.family_id}: {reason}.",
        )

    def _apply_incubating_family_lifecycle(
        self,
        family: FactoryFamily,
        champion: Dict[str, Any],
        ranked_rows: Sequence[Dict[str, Any]],
        *,
        recent_actions: List[str],
        lineage_summary_by_id: Dict[str, Dict[str, Any]],
    ) -> str | None:
        if str(family.incubation_status or "") != "incubating":
            return None
        if family.incubation_decision_reason:
            return None
        queue_stage = str(family.queue_stage or champion.get("current_stage") or "")
        if queue_stage in {
            PromotionStage.CANARY_READY.value,
            PromotionStage.LIVE_READY.value,
            PromotionStage.APPROVED_LIVE.value,
        }:
            self._mark_incubating_family_graduated(
                family,
                reason="graduated_after_advanced_stage",
                recent_actions=recent_actions,
            )
            return "graduated"
        if queue_stage != PromotionStage.PAPER.value or not bool(champion.get("first_assessment_complete")):
            return None
        min_roi_pct = float(getattr(config, "FACTORY_NEW_FAMILY_FIRST_ASSESSMENT_MIN_ROI_PCT", 0.0))
        live_roi_pct = float(champion.get("live_paper_roi_pct", 0.0) or 0.0)
        health_status = str(champion.get("execution_health_status") or "").strip().lower()
        if live_roi_pct > min_roi_pct and health_status not in {"critical"}:
            self._mark_incubating_family_graduated(
                family,
                reason="graduated_after_positive_first_assessment",
                recent_actions=recent_actions,
            )
            return "graduated"
        self._retire_incubating_family(
            family,
            ranked_rows,
            reason="incubation_first_assessment_failed",
            recent_actions=recent_actions,
            lineage_summary_by_id=lineage_summary_by_id,
        )
        return "retired"

    def _seed_post_graduation_challengers(
        self,
        family: FactoryFamily,
        lineages_by_family: Dict[str, List[LineageRecord]],
        *,
        runtime_mode_value: str,
        recent_actions: List[str],
    ) -> None:
        if runtime_mode_value != "full":
            return
        existing = [lineage for lineage in lineages_by_family.get(family.family_id, []) if lineage.active]
        if any(lineage.role in {LineageRole.SHADOW_CHALLENGER.value, LineageRole.PAPER_CHALLENGER.value, LineageRole.MOONSHOT.value} for lineage in existing):
            return
        before_ids = {lineage.lineage_id for lineage in existing}
        self._seed_challengers(
            family,
            lineages_by_family,
            runtime_mode_value=runtime_mode_value,
            recent_actions=recent_actions,
        )
        refreshed = [
            lineage
            for lineage in self.registry.lineages()
            if lineage.family_id == family.family_id and lineage.active
        ]
        lineages_by_family[family.family_id] = refreshed
        after_ids = {lineage.lineage_id for lineage in refreshed}
        new_ids = sorted(after_ids - before_ids)
        if new_ids:
            _append_recent_action(
                recent_actions,
                (
                    f"[cycle {self._cycle_count}] Newly graduated family {family.family_id} entered challenger rotation "
                    f"with {len(new_ids)} fresh challenger(s)."
                ),
            )

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

    def _latest_operator_action_context(self, lineage: LineageRecord) -> Optional[Dict[str, Any]]:
        action = self.registry.latest_operator_action(lineage.lineage_id, status="resolved")
        if action is None:
            return None
        lineage.last_operator_action_id = action.action_id
        lineage.last_operator_action_at = action.resolved_at or action.updated_at or action.created_at
        lineage.last_operator_signal_type = action.signal_type
        lineage.last_operator_decision = action.decision
        lineage.last_operator_note = action.note
        lineage.last_operator_instruction = action.instruction
        return {
            "action_id": action.action_id,
            "signal_type": action.signal_type,
            "requested_action": action.requested_action,
            "decision": action.decision,
            "summary": action.summary,
            "note": action.note,
            "instruction": action.instruction,
            "resolved_at": action.resolved_at,
            "resolved_by": action.resolved_by,
            "context": dict(action.context or {}),
        }

    def _run_experiment(self, lineage: LineageRecord) -> Dict[str, Any]:
        genome = self.registry.load_genome(lineage.lineage_id)
        experiment = self.registry.load_experiment(lineage.lineage_id)
        if genome is None or experiment is None:
            return {"mode": "missing_inputs", "bundles": [], "artifact_summary": None}
        experiment.inputs = dict(experiment.inputs or {})
        operator_action_context = self._latest_operator_action_context(lineage)
        if operator_action_context:
            experiment.inputs["operator_action_context"] = operator_action_context
            self.registry.save_lineage(lineage)
        else:
            experiment.inputs.pop("operator_action_context", None)
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
        def _debug_auto_action() -> Optional[Dict[str, Any]]:
            if str(lineage.last_debug_review_status or "") != "completed":
                return None
            safe_actions = [str(item).strip().lower() for item in (lineage.last_debug_safe_auto_actions or []) if str(item).strip()]
            safe_text = " | ".join(safe_actions)
            bug_category = str(lineage.last_debug_bug_category or "").strip().lower()
            summary = str(lineage.last_debug_summary or lineage.last_debug_review_reason or "debug review").strip()
            severity = str(lineage.last_debug_severity or "").strip().lower()
            should_pause = bool(lineage.last_debug_should_pause_lineage)

            action = ""
            if any(token in safe_text for token in ["retire", "decommission", "disable lineage"]):
                action = "retire"
            elif any(token in safe_text for token in ["replace", "launch new challenger", "fresh challengers", "new challenger"]):
                action = "replace"
            elif any(token in safe_text for token in ["retrain", "refresh incumbent", "rebuild model", "refresh artifacts"]):
                action = "retrain"
            elif any(token in safe_text for token in ["rework", "fix feature", "fix schema", "debug runner", "patch runtime", "pause lineage"]):
                action = "rework"
            elif bug_category in {
                "feature_schema",
                "data_pipeline",
                "runtime_bug",
                "execution_bug",
                "model_input_drift",
                "data_quality",
            }:
                action = "rework"
            elif bug_category in {"training_quality", "stalled_model", "model_quality"}:
                action = "replace" if severity == "critical" else "retrain"
            elif should_pause:
                action = "rework"

            if not action:
                return None
            return {
                "source": "debug_agent",
                "action": action,
                "reason": summary,
                "requires_human": False,
                "requires_new_challenger": action == "replace",
                "should_pause_lineage": should_pause,
                "bug_category": lineage.last_debug_bug_category,
                "safe_auto_actions": list(lineage.last_debug_safe_auto_actions or []),
            }

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
        debug_auto_request = _debug_auto_action()
        if debug_auto_request is not None:
            return debug_auto_request
        maintenance_review_action = str(lineage.last_maintenance_review_action or "").strip().lower()
        if str(lineage.last_maintenance_review_status or "") == "completed" and maintenance_review_action in {
            "retrain",
            "rework",
            "replace",
            "retire",
        }:
            return {
                "source": "maintenance_review",
                "action": maintenance_review_action,
                "reason": str(lineage.last_maintenance_review_summary or lineage.last_maintenance_review_reason or "maintenance review"),
                "requires_human": False,
                "requires_new_challenger": maintenance_review_action == "replace",
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
        persistent_stall_retirement = self._persistent_stall_retirement_reason(
            lineage,
            {"execution_validation": execution_validation, "execution_issue_codes": list(execution_validation.get("issue_codes") or [])},
        )
        if persistent_stall_retirement:
            return {
                "source": "execution_policy",
                "action": "retire",
                "reason": "Execution evidence says the model stayed stalled or untrainable after exhausting its tweak budget.",
                "requires_human": False,
                "issue_codes": sorted({str(item) for item in (execution_validation.get("issue_codes") or []) if str(item).strip()}),
                "retirement_reason": persistent_stall_retirement,
            }
        trainability_contract_request = self._trainability_contract_request(lineage, execution_validation)
        if trainability_contract_request is not None:
            return trainability_contract_request
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
        if issue_codes.intersection({"negative_paper_roi", "poor_win_rate", "zero_simulated_fills", "no_trade_syndrome"}):
            action = "replace" if issue_codes.intersection({"negative_paper_roi", "poor_win_rate"}) else "retrain"
            return {
                "source": "execution_policy",
                "action": action,
                "reason": "Execution evidence says the incumbent is weak enough that maintenance pressure should escalate now.",
                "requires_human": False,
                "requires_new_challenger": action == "replace",
                "issue_codes": sorted(issue_codes.intersection({"negative_paper_roi", "poor_win_rate", "zero_simulated_fills", "no_trade_syndrome"})),
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

        # Rule: if backtest failed due to missing data, promote directly to paper trading
        if experiment_result.get("mode") == "model_code_backtest_failed":
            err = str(experiment_result.get("error") or "")
            is_data_missing = any(kw in err.lower() for kw in (
                "no data", "filenotfounderror", "no binance", "no yahoo",
                "no polymarket", "no betfair", "insufficient data",
                "no alpaca", "not found",
            ))
            if is_data_missing and lineage.current_stage != PromotionStage.PAPER.value:
                logger.info(
                    "No backtest data for %s (%s), promoting directly to paper trading",
                    lineage.lineage_id, err,
                )
                lineage.current_stage = PromotionStage.PAPER.value
                lineage.iteration_status = "paper_trial_no_backtest"
                lineage.blockers = [b for b in lineage.blockers if "backtest" not in b.lower()]
                self.registry.save_lineage(lineage)

        raw_bundles = list(experiment_result.get("bundles") or [])
        experiment_bundles: List[EvaluationBundle] = []
        for item in raw_bundles:
            if isinstance(item, EvaluationBundle):
                experiment_bundles.append(item)
            elif isinstance(item, dict):
                try:
                    from dataclasses import fields as dc_fields
                    known = {f.name for f in dc_fields(EvaluationBundle)}
                    filtered = {k: v for k, v in item.items() if k in known}
                    experiment_bundles.append(EvaluationBundle(**filtered))
                except Exception:
                    logger.warning("Could not coerce bundle dict for %s: %s", lineage.lineage_id, list(item.keys())[:5])
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
        else:
            bundles.extend(experiment_bundles)
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
        manual_idea_watch_result = maybe_run_manual_idea_watch(self.project_root)
        idea_scout_result = maybe_run_idea_scout(self.project_root)
        families = self.registry.families()
        family_by_id = {family.family_id: family for family in families}
        workspace_status = self._workspace_status(families)
        connector_snapshots = self._connector_snapshots()
        self._maybe_trigger_data_refresh(connector_snapshots)
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
        if manual_idea_watch_result.get("ran") and int(manual_idea_watch_result.get("new_count", 0) or 0) > 0:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Manual idea watcher ingested {int(manual_idea_watch_result.get('new_count', 0) or 0)} new ideas from {manual_idea_watch_result.get('path') or 'ideas.md'}.",
            )
        if idea_scout_result.get("ran") and int(idea_scout_result.get("new_count", 0) or 0) > 0:
            _append_recent_action(
                recent_actions,
                f"[cycle {self._cycle_count}] Low-value idea scout added {int(idea_scout_result.get('new_count', 0) or 0)} new online ideas.",
            )
        self._seed_new_families(
            lineages_by_family=lineages_by_family,
            runtime_mode_value=runtime_mode.value,
            recent_actions=recent_actions,
        )
        families = self.registry.families()
        family_by_id = {family.family_id: family for family in families}
        workspace_status = self._workspace_status(families)
        curated_family_rankings = {
            family.family_id: self._curated_family_ranking_summary(family.family_id)
            for family in families
        }
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
        maintenance_reviews_run = 0
        maintenance_review_cap = max(0, int(getattr(config, "FACTORY_MAINTENANCE_AGENT_MAX_ITEMS_PER_CYCLE", 3) or 3))
        for lineage in self.registry.lineages():
            if lineage.active:
                if self._should_skip_paper_dispatch(lineage):
                    latest_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
                    _append_recent_action(
                        recent_actions,
                        f"[cycle {self._cycle_count}] Skipped paper/shadow dispatch for {lineage.lineage_id} ({lineage.family_id}): stock market closed.",
                    )
                else:
                    latest_by_stage = self._save_evidence(lineage)
            else:
                latest_by_stage = self.registry.latest_evaluation_by_stage(lineage.lineage_id)
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
                if maintenance_reviews_run < maintenance_review_cap:
                    if self._maybe_run_maintenance_resolution_agent(
                        family,
                        lineage,
                        latest_by_stage,
                        recent_actions=recent_actions,
                    ):
                        maintenance_reviews_run += 1
                    refreshed_lineage = self.registry.load_lineage(lineage.lineage_id)
                    if refreshed_lineage is not None:
                        lineage = refreshed_lineage
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
                "last_debug_safe_auto_actions": list(refreshed.last_debug_safe_auto_actions or []),
                "last_debug_should_pause_lineage": bool(refreshed.last_debug_should_pause_lineage),
                "last_debug_severity": refreshed.last_debug_severity,
                "last_operator_action_id": refreshed.last_operator_action_id,
                "last_operator_action_at": refreshed.last_operator_action_at,
                "last_operator_signal_type": refreshed.last_operator_signal_type,
                "last_operator_decision": refreshed.last_operator_decision,
                "last_operator_note": refreshed.last_operator_note,
                "last_operator_instruction": refreshed.last_operator_instruction,
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
                "live_paper_days": _observed_live_paper_days(execution_validation.get("runtime_age_hours")),
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
                "activation_status": None,
                "alias_runner_running": False,
                "prepared_isolated_lane": False,
                "maintenance_request_action": maintenance_request.get("action") if maintenance_request else None,
                "maintenance_request_reason": maintenance_request.get("reason") if maintenance_request else None,
                "maintenance_request_source": maintenance_request.get("source") if maintenance_request else None,
                "last_maintenance_review_at": refreshed.last_maintenance_review_at,
                "last_maintenance_review_status": refreshed.last_maintenance_review_status,
                "last_maintenance_review_action": refreshed.last_maintenance_review_action,
                "last_maintenance_review_summary": refreshed.last_maintenance_review_summary,
                "last_maintenance_review_artifact_path": refreshed.last_maintenance_review_artifact_path,
                "curated_family_rank": curated_summary.get("family_rank"),
                "curated_ranking_score": float(curated_summary.get("ranking_score", 0.0) or 0.0),
                "curated_target_portfolio_id": curated_summary.get("target_portfolio_id"),
                "curated_paper_roi_pct": float(curated_summary.get("paper_roi_pct", 0.0) or 0.0),
                "curated_paper_realized_pnl": float(curated_summary.get("paper_realized_pnl", 0.0) or 0.0),
                "curated_paper_win_rate": float(curated_summary.get("paper_win_rate", 0.0) or 0.0),
                "curated_paper_closed_trade_count": int(curated_summary.get("paper_closed_trade_count", 0) or 0),
            }
            summary_family = family_by_id.get(refreshed.family_id)
            observed_live_days = int(lineage_summary.get("live_paper_days", 0) or 0)
            first_assessment = assessment_progress(
                paper_days=observed_live_days,
                trade_count=int(live_account.get("trade_count", 0) or 0),
                labels=[refreshed.family_id, refreshed.current_stage, live_target.get("resolved_target") or ""],
                realized_roi_pct=float(live_account.get("roi_pct", 0.0) or 0.0),
                current_stage=refreshed.current_stage,
                phase="first",
            )
            full_assessment = assessment_progress(
                paper_days=observed_live_days,
                trade_count=int(live_account.get("trade_count", 0) or 0),
                labels=[refreshed.family_id, refreshed.current_stage, live_target.get("resolved_target") or ""],
                realized_roi_pct=float(live_account.get("roi_pct", 0.0) or 0.0),
                current_stage=refreshed.current_stage,
                phase="full",
            )
            lineage_summary["agent_review_due_reason"] = (
                self._scheduled_review_reason(summary_family, refreshed, latest_bundle, execution_validation)
                if summary_family is not None
                else None
            )
            lineage_summary["agent_review_due"] = bool(lineage_summary["agent_review_due_reason"])
            lineage_summary["first_assessment"] = first_assessment
            lineage_summary["first_assessment_complete"] = bool(first_assessment.get("complete"))
            lineage_summary["assessment"] = full_assessment
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
            prepared_challenger_id: str | None = None
            if runtime_mode.is_full and ranked:
                self._retire_or_update_lineages(family, ranked, recent_actions=recent_actions)
                self._retire_by_loss_streak(family, ranked, recent_actions=recent_actions)
                self._retire_by_backtest_ttl(family, recent_actions=recent_actions)
                if family == families[-1]:
                    self._trigger_auto_optimization(families, recent_actions=recent_actions)
                    self._promote_optimized_lineages(families, recent_actions=recent_actions)
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
                        summary["last_debug_safe_auto_actions"] = list(lineage.last_debug_safe_auto_actions or [])
                        summary["last_debug_should_pause_lineage"] = bool(lineage.last_debug_should_pause_lineage)
                        summary["last_debug_severity"] = lineage.last_debug_severity
                    refreshed_ranked.append(dict(summary or row))
                ranked = sorted(
                    refreshed_ranked,
                    key=lambda item: (
                        0 if item.get("active", True) else 1,
                        item.get("pareto_rank") if item.get("pareto_rank") is not None else 999,
                        -float(item.get("fitness_score", 0.0) or 0.0),
                    ),
                )
                prepared_challenger_id = self._apply_isolated_lane_preparation(
                    family,
                    ranked,
                    recent_actions=recent_actions,
                )
                prepared_challenger_id = self._activate_prepared_isolated_lane(
                    family,
                    prepared_challenger_id,
                    recent_actions=recent_actions,
                ) or prepared_challenger_id
                self._reclassify_family(
                    family,
                    ranked,
                    prepared_challenger_id=prepared_challenger_id,
                    recent_actions=recent_actions,
                )
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
                        summary["last_debug_safe_auto_actions"] = list(lineage.last_debug_safe_auto_actions or [])
                        summary["last_debug_should_pause_lineage"] = bool(lineage.last_debug_should_pause_lineage)
                        summary["last_debug_severity"] = lineage.last_debug_severity
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
            runtime_lanes = self._runtime_family_lanes(family, ranked)
            primary_incumbent = dict(runtime_lanes.get("primary_incumbent") or {})
            isolated_challenger = dict(runtime_lanes.get("isolated_challenger") or {})
            lane_reason = str(runtime_lanes.get("runtime_lane_reason") or "")
            selected_ids = {
                str(primary_incumbent.get("lineage_id") or ""),
                str(isolated_challenger.get("lineage_id") or ""),
            } - {""}
            for row in ranked:
                summary = lineage_summary_by_id.get(str(row.get("lineage_id") or ""))
                if summary is None:
                    continue
                lineage_id = str(summary.get("lineage_id") or "")
                summary["prepared_isolated_lane"] = bool(prepared_challenger_id and lineage_id == prepared_challenger_id)
                if lineage_id == str(primary_incumbent.get("lineage_id") or ""):
                    summary["runtime_lane_selected"] = True
                    summary["runtime_lane_kind"] = "primary_incumbent"
                    summary["runtime_lane_reason"] = lane_reason
                    summary["runtime_target_portfolio"] = primary_incumbent.get("runtime_target_portfolio")
                    summary["canonical_target_portfolio"] = primary_incumbent.get("canonical_target_portfolio")
                elif lineage_id == str(isolated_challenger.get("lineage_id") or ""):
                    summary["runtime_lane_selected"] = True
                    summary["runtime_lane_kind"] = "isolated_challenger"
                    summary["runtime_lane_reason"] = lane_reason
                    summary["runtime_target_portfolio"] = isolated_challenger.get("runtime_target_portfolio")
                    summary["canonical_target_portfolio"] = isolated_challenger.get("canonical_target_portfolio")
                else:
                    summary["runtime_lane_selected"] = False
                    summary["runtime_lane_kind"] = None
                    summary["runtime_lane_reason"] = None
                    summary["runtime_target_portfolio"] = None
                    summary["canonical_target_portfolio"] = None
                summary["suppressed_runtime_sibling"] = bool(selected_ids) and lineage_id not in selected_ids
            family.queue_stage = str(champion.get("current_stage") or family.queue_stage)
            incubation_transition = self._apply_incubating_family_lifecycle(
                family,
                champion,
                ranked,
                recent_actions=recent_actions,
                lineage_summary_by_id=lineage_summary_by_id,
            )
            if incubation_transition == "graduated":
                self._seed_post_graduation_challengers(
                    family,
                    lineages_by_family,
                    runtime_mode_value=runtime_mode.value,
                    recent_actions=recent_actions,
                )
            family.last_cycle_at = utc_now_iso()
            self.registry.save_family(family)
            family_summaries.append(
                {
                    "family_id": family.family_id,
                    "label": family.label,
                    "explainer": family.explainer,
                    "origin": family.origin,
                    "source_idea_id": family.source_idea_id,
                    "incubation_status": family.incubation_status,
                    "incubation_cycle_created": family.incubation_cycle_created,
                    "incubation_notes": list(family.incubation_notes or []),
                    "incubation_decided_at": family.incubation_decided_at,
                    "incubation_decision_reason": family.incubation_decision_reason,
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
                    "primary_incumbent_lineage_id": primary_incumbent.get("lineage_id"),
                    "isolated_challenger_lineage_id": isolated_challenger.get("lineage_id"),
                    "prepared_isolated_lane_lineage_id": prepared_challenger_id,
                    "runtime_lane_reason": lane_reason or None,
                    "runtime_target_portfolio": isolated_challenger.get("runtime_target_portfolio")
                    or primary_incumbent.get("runtime_target_portfolio"),
                    "canonical_target_portfolio": isolated_challenger.get("canonical_target_portfolio")
                    or primary_incumbent.get("canonical_target_portfolio"),
                    "activation_status": None,
                    "alias_runner_running": False,
                    "weak_family": False,
                    "autopilot_status": "healthy",
                    "autopilot_actions": [],
                    "autopilot_reason": "",
                    "autopilot_issue_codes": [],
                    "autopilot_live_roi_pct": 0.0,
                    "autopilot_live_win_rate": 0.0,
                    "autopilot_trade_count": 0,
                    "isolated_evidence_ready": bool(
                        isolated_challenger
                        and str(
                            isolated_challenger.get("runtime_target_portfolio")
                            or isolated_challenger.get("live_paper_target_portfolio_id")
                            or isolated_challenger.get("curated_target_portfolio_id")
                            or ""
                        ).strip()
                        and str(
                            isolated_challenger.get("runtime_target_portfolio")
                            or isolated_challenger.get("live_paper_target_portfolio_id")
                            or isolated_challenger.get("curated_target_portfolio_id")
                            or ""
                        ).strip()
                        != str(
                            primary_incumbent.get("runtime_target_portfolio")
                            or primary_incumbent.get("live_paper_target_portfolio_id")
                            or primary_incumbent.get("curated_target_portfolio_id")
                            or ""
                        ).strip()
                    ),
                }
            )
        execution_bridge_payload = self.execution_bridge.sync({"lineages": lineage_summaries})
        self._apply_execution_bridge_feedback(
            lineage_summaries=lineage_summaries,
            family_summaries=family_summaries,
            bridge_payload=execution_bridge_payload,
            recent_actions=recent_actions,
        )
        rows_by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in lineage_summaries:
            family_id = str(item.get("family_id") or "").strip()
            if family_id:
                rows_by_family[family_id].append(dict(item))
        for family_summary in family_summaries:
            family_id = str(family_summary.get("family_id") or "").strip()
            if not family_id:
                continue
            autopilot = self._family_autopilot_plan(
                family_id,
                rows_by_family.get(family_id, []),
                family_summary=family_summary,
            )
            family_summary["weak_family"] = bool(autopilot.get("weak_family"))
            family_summary["autopilot_status"] = autopilot.get("autopilot_status")
            family_summary["autopilot_actions"] = list(autopilot.get("autopilot_actions") or [])
            family_summary["autopilot_reason"] = autopilot.get("autopilot_reason")
            family_summary["autopilot_issue_codes"] = list(autopilot.get("autopilot_issue_codes") or [])
            family_summary["autopilot_live_roi_pct"] = float(autopilot.get("autopilot_live_roi_pct", 0.0) or 0.0)
            family_summary["autopilot_live_win_rate"] = float(autopilot.get("autopilot_live_win_rate", 0.0) or 0.0)
            family_summary["autopilot_trade_count"] = int(autopilot.get("autopilot_trade_count", 0) or 0)
            if maintenance_reviews_run < maintenance_review_cap:
                maintenance_request = self._family_autopilot_maintenance_request(autopilot)
                target_lineage_id = str(autopilot.get("autopilot_target_lineage_id") or "").strip()
                target_family = family_by_id.get(family_id)
                if maintenance_request and target_lineage_id and target_family is not None:
                    target_lineage = self.registry.load_lineage(target_lineage_id)
                    if target_lineage is not None and bool(target_lineage.active):
                        latest_by_stage = self.registry.latest_evaluation_by_stage(target_lineage.lineage_id)
                        if self._maybe_run_maintenance_resolution_agent(
                            target_family,
                            target_lineage,
                            latest_by_stage,
                            recent_actions=recent_actions,
                            maintenance_request_override=maintenance_request,
                        ):
                            maintenance_reviews_run += 1
                            refreshed_target = self.registry.load_lineage(target_lineage.lineage_id)
                            if refreshed_target is not None:
                                summary_row = lineage_summary_by_id.get(refreshed_target.lineage_id)
                                if summary_row is not None:
                                    summary_row["iteration_status"] = refreshed_target.iteration_status
                                    summary_row["last_maintenance_review_at"] = refreshed_target.last_maintenance_review_at
                                    summary_row["last_maintenance_review_status"] = refreshed_target.last_maintenance_review_status
                                    summary_row["last_maintenance_review_action"] = refreshed_target.last_maintenance_review_action
                                    summary_row["last_maintenance_review_summary"] = refreshed_target.last_maintenance_review_summary
                                    summary_row["last_maintenance_review_artifact_path"] = refreshed_target.last_maintenance_review_artifact_path
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
        operator_signals = self._sync_operator_actions(self._operator_signals(lineage_summaries, family_summaries))
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
            "execution_bridge": execution_bridge_payload,
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
                "paper_qualification_count": len(operator_signals.get("paper_qualification_queue") or []),
                "operator_escalation_count": len(operator_signals["escalation_candidates"]),
                "human_action_required_count": len(operator_signals.get("human_action_required") or []),
                "maintenance_queue_count": len(operator_signals.get("maintenance_queue") or []),
                "weak_family_count": sum(1 for item in family_summaries if bool(item.get("weak_family"))),
                "prepared_isolated_lane_count": sum(1 for item in lineage_summaries if item.get("prepared_isolated_lane")),
                "isolated_evidence_ready_family_count": sum(
                    1 for item in family_summaries if bool(item.get("isolated_evidence_ready"))
                ),
                "incubating_family_count": sum(
                    1 for item in family_summaries if str(item.get("incubation_status") or "") == "incubating"
                ),
                "generated_family_count": sum(
                    1 for item in family_summaries if str(item.get("origin") or "") != "seeded_family"
                ),
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
