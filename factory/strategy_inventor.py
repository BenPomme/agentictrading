from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Dict, List, Sequence

from factory.contracts import FactoryFamily, LearningMemoryEntry, MutationBounds, ResearchHypothesis, StrategyGenome


@dataclass(frozen=True)
class _DomainAgentProfile:
    domain: str
    role: str
    angle: str
    feature_subset: str
    model_class: str
    horizon_seconds: int
    lookback_hours: float
    min_edge: float
    stake_fraction: float


@dataclass(frozen=True)
class ScientificAgentProposal:
    proposal_id: str
    family_id: str
    title: str
    thesis: str
    scientific_domains: List[str]
    lead_agent_role: str
    collaborating_agent_roles: List[str]
    parameter_overrides: Dict[str, Any]
    budget_bucket: str
    proposal_kind: str = "mutation"
    source_idea_id: str | None = None
    origin: str = "scientific_agent_collective"
    agent_notes: List[str] = field(default_factory=list)
    agent_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScientificFamilyProposal:
    proposal_id: str
    family_id: str
    label: str
    thesis: str
    explainer: str
    target_venues: List[str]
    primary_connector_ids: List[str]
    target_portfolios: List[str]
    scientific_domains: List[str]
    lead_agent_role: str
    collaborating_agent_roles: List[str]
    source_idea_id: str | None = None
    origin: str = "incubated_family"
    incubation_notes: List[str] = field(default_factory=list)


_DOMAIN_PROFILES: Dict[str, _DomainAgentProfile] = {
    "econometrics": _DomainAgentProfile(
        domain="econometrics",
        role="Econometrics Researcher",
        angle="stability-aware factor combinations and regime-conditioned edge persistence",
        feature_subset="baseline",
        model_class="logit",
        horizon_seconds=1800,
        lookback_hours=72.0,
        min_edge=0.03,
        stake_fraction=0.03,
    ),
    "microstructure": _DomainAgentProfile(
        domain="microstructure",
        role="Microstructure Researcher",
        angle="order-book stress, queue pressure, and execution-quality asymmetry",
        feature_subset="microstructure",
        model_class="gbdt",
        horizon_seconds=600,
        lookback_hours=36.0,
        min_edge=0.025,
        stake_fraction=0.025,
    ),
    "bayesian_causal": _DomainAgentProfile(
        domain="bayesian_causal",
        role="Bayesian/Causal Researcher",
        angle="causal priors and uncertainty-aware edge filtering",
        feature_subset="cross_science",
        model_class="logit",
        horizon_seconds=1800,
        lookback_hours=96.0,
        min_edge=0.035,
        stake_fraction=0.03,
    ),
    "statistical_physics": _DomainAgentProfile(
        domain="statistical_physics",
        role="Statistical Physics Researcher",
        angle="phase-transition detection and cascade susceptibility before dislocations accelerate",
        feature_subset="regime",
        model_class="transformer",
        horizon_seconds=120,
        lookback_hours=24.0,
        min_edge=0.04,
        stake_fraction=0.02,
    ),
    "network_epidemiology": _DomainAgentProfile(
        domain="network_epidemiology",
        role="Network/Epidemiology Researcher",
        angle="contagion paths, information spread, and lag propagation across correlated books",
        feature_subset="cross_science",
        model_class="transformer",
        horizon_seconds=600,
        lookback_hours=48.0,
        min_edge=0.035,
        stake_fraction=0.025,
    ),
    "ecology_evolution": _DomainAgentProfile(
        domain="ecology_evolution",
        role="Ecology/Evolution Researcher",
        angle="survival pressure, niche competition, and adaptive response to decaying edges",
        feature_subset="cross_science",
        model_class="gbdt",
        horizon_seconds=600,
        lookback_hours=84.0,
        min_edge=0.03,
        stake_fraction=0.025,
    ),
    "information_theory": _DomainAgentProfile(
        domain="information_theory",
        role="Information Theory Researcher",
        angle="entropy compression, surprise detection, and cross-feed information gain",
        feature_subset="cross_science",
        model_class="gbdt",
        horizon_seconds=600,
        lookback_hours=48.0,
        min_edge=0.03,
        stake_fraction=0.025,
    ),
    "control_rl": _DomainAgentProfile(
        domain="control_rl",
        role="Control/RL Researcher",
        angle="feedback-aware thresholding and action policies that stay robust under execution drift",
        feature_subset="regime",
        model_class="tft",
        horizon_seconds=600,
        lookback_hours=60.0,
        min_edge=0.035,
        stake_fraction=0.02,
    ),
    "game_theory_behavioral": _DomainAgentProfile(
        domain="game_theory_behavioral",
        role="Game Theory/Behavioral Researcher",
        angle="opponent adaptation, crowd reflexivity, and exploitability of consensus breakdowns",
        feature_subset="cross_science",
        model_class="rules",
        horizon_seconds=1800,
        lookback_hours=60.0,
        min_edge=0.04,
        stake_fraction=0.02,
    ),
    "signal_processing_neuroscience": _DomainAgentProfile(
        domain="signal_processing_neuroscience",
        role="Signal Processing/Neuroscience Researcher",
        angle="multi-timescale filtering, burst detection, and low-latency state transitions",
        feature_subset="microstructure",
        model_class="gbdt",
        horizon_seconds=120,
        lookback_hours=24.0,
        min_edge=0.03,
        stake_fraction=0.02,
    ),
}


_FAMILY_SWARMS: Dict[str, List[List[str]]] = {
    "binance_funding_contrarian": [
        ["econometrics", "microstructure", "ecology_evolution"],
        ["bayesian_causal", "information_theory", "control_rl"],
        ["statistical_physics", "network_epidemiology", "microstructure"],
    ],
    "binance_cascade_regime": [
        ["statistical_physics", "network_epidemiology", "signal_processing_neuroscience"],
        ["microstructure", "control_rl", "information_theory"],
        ["econometrics", "statistical_physics", "control_rl"],
    ],
    "betfair_prediction_value_league": [
        ["bayesian_causal", "information_theory", "econometrics"],
        ["microstructure", "signal_processing_neuroscience", "control_rl"],
        ["game_theory_behavioral", "bayesian_causal", "ecology_evolution"],
    ],
    "betfair_information_lag": [
        ["network_epidemiology", "game_theory_behavioral", "information_theory"],
        ["signal_processing_neuroscience", "microstructure", "bayesian_causal"],
        ["ecology_evolution", "network_epidemiology", "control_rl"],
    ],
    "polymarket_cross_venue": [
        ["information_theory", "signal_processing_neuroscience", "network_epidemiology"],
        ["microstructure", "game_theory_behavioral", "control_rl"],
        ["bayesian_causal", "information_theory", "econometrics"],
    ],
}

_FAMILY_TUNING: Dict[str, Dict[str, Any]] = {
    "binance_funding_contrarian": {
        "feature_subset": "regime",
        "model_class": "gbdt",
        "horizon_seconds": 600,
        "lookback_hours": 96.0,
        "min_edge": 0.05,
        "stake_fraction": 0.015,
    },
    "binance_cascade_regime": {
        "feature_subset": "microstructure",
        "model_class": "gbdt",
        "horizon_seconds": 120,
        "lookback_hours": 36.0,
        "min_edge": 0.06,
        "stake_fraction": 0.012,
    },
    "betfair_prediction_value_league": {
        "feature_subset": "microstructure",
        "model_class": "gbdt",
        "horizon_seconds": 1800,
        "lookback_hours": 120.0,
        "min_edge": 0.05,
        "stake_fraction": 0.015,
    },
    "betfair_information_lag": {
        "feature_subset": "cross_science",
        "model_class": "gbdt",
        "horizon_seconds": 600,
        "lookback_hours": 96.0,
        "min_edge": 0.055,
        "stake_fraction": 0.012,
    },
    "polymarket_cross_venue": {
        "feature_subset": "cross_science",
        "model_class": "gbdt",
        "horizon_seconds": 600,
        "lookback_hours": 84.0,
        "min_edge": 0.055,
        "stake_fraction": 0.012,
    },
}


class ScientificStrategyInventor:
    def generate_family_proposal(
        self,
        *,
        idea: Dict[str, Any],
        existing_family_ids: Sequence[str],
        cycle_count: int,
        proposal_index: int,
        research_portfolio_id: str,
    ) -> ScientificFamilyProposal:
        venue = self._idea_primary_venue(idea)
        scientific_domains = self._idea_domains(idea)
        lead_domain = scientific_domains[0]
        lead_profile = _DOMAIN_PROFILES.get(lead_domain, _DOMAIN_PROFILES["econometrics"])
        collaborators = [_DOMAIN_PROFILES[domain].role for domain in scientific_domains[1:] if domain in _DOMAIN_PROFILES]
        family_id = self._family_id_for_idea(idea, venue=venue, existing_family_ids=existing_family_ids)
        label = self._family_label_for_idea(idea, venue=venue)
        idea_title = str(idea.get("title") or "").strip()
        summary = str(idea.get("summary") or "").strip()
        explainer = (
            summary
            or f"Incubates a new {venue.title()} family from idea intake using {lead_profile.role.lower()} guidance."
        )
        raw_thesis = (
            f"Exploring a new {venue} strategy family built from the idea '{idea_title or label}', using "
            f"{lead_profile.role.lower()} guidance to test {lead_profile.angle} in a bounded incubation lane."
        )
        thesis = normalize_family_thesis(raw_thesis)
        return ScientificFamilyProposal(
            proposal_id=f"{family_id}:family_proposal:{cycle_count}:{proposal_index}",
            family_id=family_id,
            label=label,
            thesis=thesis,
            explainer=explainer,
            target_venues=[venue],
            primary_connector_ids=[self._connector_for_venue(venue)],
            target_portfolios=[str(research_portfolio_id or "research_factory")],
            scientific_domains=scientific_domains,
            lead_agent_role=lead_profile.role,
            collaborating_agent_roles=collaborators,
            source_idea_id=str(idea.get("idea_id") or "") or None,
            incubation_notes=[
                f"source=idea_intake",
                f"idea_title={idea_title or 'untitled'}",
                f"venue={venue}",
            ],
        )

    def generate_proposal(
        self,
        *,
        family: FactoryFamily,
        champion_hypothesis: ResearchHypothesis | None,
        champion_genome: StrategyGenome,
        learning_memory: Sequence[LearningMemoryEntry],
        cycle_count: int,
        proposal_index: int,
        desired_creation_kind: str = "mutation",
        idea_candidates: Sequence[Dict[str, Any]] | None = None,
    ) -> ScientificAgentProposal:
        swarms = list(_FAMILY_SWARMS.get(family.family_id) or [["econometrics", "microstructure", "information_theory"]])
        recent_signatures = {
            tuple(sorted(memory.scientific_domains))
            for memory in learning_memory
            if memory.outcome.startswith("retired")
        }
        selected_domains: List[str] = []
        start_index = (cycle_count + proposal_index - 1) % max(1, len(swarms))
        if desired_creation_kind == "new_model" and len(swarms) > 1:
            start_index = (start_index + max(1, len(swarms) // 2)) % len(swarms)
        for offset in range(len(swarms)):
            candidate = list(swarms[(start_index + offset) % len(swarms)])
            if tuple(sorted(candidate)) not in recent_signatures:
                selected_domains = candidate
                break
        if not selected_domains:
            selected_domains = list(swarms[start_index % len(swarms)])
        profiles = [_DOMAIN_PROFILES[domain] for domain in selected_domains]
        feature_subset = self._dominant_value([profile.feature_subset for profile in profiles], fallback="baseline")
        model_class = self._dominant_value([profile.model_class for profile in profiles], fallback="logit")
        horizons = [profile.horizon_seconds for profile in profiles]
        lookbacks = [profile.lookback_hours for profile in profiles]
        min_edges = [profile.min_edge for profile in profiles]
        stake_fractions = [profile.stake_fraction for profile in profiles]
        bounds = champion_genome.mutation_bounds
        tuning = dict(_FAMILY_TUNING.get(family.family_id) or {})
        if tuning:
            feature_subset = str(tuning.get("feature_subset", feature_subset) or feature_subset)
            model_class = str(tuning.get("model_class", model_class) or model_class)
        memory_adjustments = self._memory_adjustments(learning_memory)
        parameter_overrides = {
            "selected_horizon_seconds": self._nearest_choice(
                bounds.horizons_seconds,
                int(tuning.get("horizon_seconds") or int(round(sum(horizons) / len(horizons)))),
            ) or horizons[0],
            "selected_feature_subset": self._allowed_choice(bounds.feature_subsets, feature_subset, fallback="baseline"),
            "selected_model_class": self._allowed_choice(bounds.model_classes, model_class, fallback="logit"),
            "selected_lookback_hours": self._clip_range(
                bounds.hyperparameter_ranges.get("lookback_hours"),
                float(tuning.get("lookback_hours") or (sum(lookbacks) / len(lookbacks))),
                fallback=48.0,
            ),
            "selected_min_edge": self._clip_range(
                bounds.execution_thresholds.get("min_edge"),
                max(float(tuning.get("min_edge", 0.0) or 0.0), (sum(min_edges) / len(min_edges)) + memory_adjustments["edge_bump"]),
                fallback=0.03,
            ),
            "selected_stake_fraction": self._clip_range(
                bounds.execution_thresholds.get("stake_fraction"),
                min(float(tuning.get("stake_fraction", 1.0) or 1.0), max(0.01, (sum(stake_fractions) / len(stake_fractions)) - memory_adjustments["stake_reduction"])),
                fallback=0.03,
            ),
            "selected_learning_rate": self._learning_rate_for_model(
                self._allowed_choice(bounds.model_classes, model_class, fallback="logit"),
                bounds,
            ),
        }
        if memory_adjustments["prefer_information"]:
            parameter_overrides["selected_feature_subset"] = self._allowed_choice(
                bounds.feature_subsets,
                "microstructure" if "microstructure" in bounds.feature_subsets else "cross_science",
                fallback=parameter_overrides["selected_feature_subset"],
            )
        if memory_adjustments["avoid_high_failure_models"]:
            parameter_overrides["selected_model_class"] = self._allowed_choice(
                bounds.model_classes,
                "gbdt",
                fallback=parameter_overrides["selected_model_class"],
            )
        lead_profile = profiles[0]
        collaborator_roles = [profile.role for profile in profiles[1:]]
        thesis_parts = [profile.angle for profile in profiles]
        thesis = (
            f"{family.thesis} This variant fuses {lead_profile.role.lower()} guidance with "
            f"{', '.join(role.lower() for role in collaborator_roles)} to test "
            f"{'; '.join(thesis_parts)}."
        )
        memory_hint = self._memory_hint(learning_memory)
        notes = [
            f"proposal_kind={desired_creation_kind}",
            f"lead_agent={lead_profile.role}",
            f"collaborators={','.join(collaborator_roles) or 'none'}",
        ]
        selected_idea = dict((list(idea_candidates or []) or [None])[0] or {})
        if selected_idea.get("idea_id"):
            notes.append(f"source_idea_id={selected_idea['idea_id']}")
        if memory_hint:
            notes.append(memory_hint)
        title_suffix = "New Model" if desired_creation_kind == "new_model" else "Scientific Collaboration"
        if desired_creation_kind == "new_model":
            thesis = (
                f"{family.thesis} This new model deliberately explores a less-local variant that fuses "
                f"{lead_profile.role.lower()} guidance with "
                f"{', '.join(role.lower() for role in collaborator_roles) or 'cross-disciplinary collaborators'} "
                f"to test {'; '.join(thesis_parts)}."
            )
        if selected_idea.get("title"):
            thesis = f"{thesis} Adapt the idea '{selected_idea['title']}' into a bounded {family.label.lower()} variant."
        title = build_proposal_title(
            family=family,
            proposal_kind=desired_creation_kind,
            proposal_index=proposal_index,
            scientific_domains=selected_domains,
            model_class=str(parameter_overrides.get("selected_model_class") or model_class),
            raw_title=f"{family.label} {title_suffix} {proposal_index}",
            source_idea_title=str(selected_idea.get("title") or "") or None,
        )
        thesis = normalize_alpha_thesis(
            thesis,
            family=family,
            proposal_kind=desired_creation_kind,
        )
        return ScientificAgentProposal(
            proposal_id=f"{family.family_id}:proposal:{proposal_index}",
            family_id=family.family_id,
            title=title,
            thesis=thesis,
            scientific_domains=selected_domains,
            lead_agent_role=lead_profile.role,
            collaborating_agent_roles=collaborator_roles,
            parameter_overrides=parameter_overrides,
            budget_bucket=(
                "adjacent"
                if desired_creation_kind == "new_model" and self._budget_bucket(selected_domains, family.budget_split) == "incumbent"
                else self._budget_bucket(selected_domains, family.budget_split)
            ),
            proposal_kind=desired_creation_kind,
            source_idea_id=str(selected_idea.get("idea_id") or "") or None,
            agent_notes=notes,
        )

    def _budget_bucket(self, domains: Sequence[str], budget_split: Dict[str, float]) -> str:
        if any(domain in {"statistical_physics", "network_epidemiology", "game_theory_behavioral"} for domain in domains):
            return "moonshot" if budget_split.get("moonshot", 0.0) > 0.0 else "adjacent"
        if any(domain in {"control_rl", "information_theory", "signal_processing_neuroscience"} for domain in domains):
            return "adjacent" if budget_split.get("adjacent", 0.0) > 0.0 else "incumbent"
        return "incumbent"

    def _memory_hint(self, learning_memory: Sequence[LearningMemoryEntry]) -> str:
        if not learning_memory:
            return ""
        latest = learning_memory[-1]
        if latest.recommendations:
            return f"memory_hint={latest.recommendations[0]}"
        return f"memory_hint=avoid repeating {','.join(latest.scientific_domains)} without a structural change"

    def _memory_adjustments(self, learning_memory: Sequence[LearningMemoryEntry]) -> Dict[str, Any]:
        edge_bump = 0.0
        stake_reduction = 0.0
        prefer_information = False
        avoid_high_failure_models = False
        for memory in learning_memory[-5:]:
            recs = " | ".join(memory.recommendations).lower()
            blockers = " | ".join(memory.blockers).lower()
            if "tighten edge thresholds" in recs:
                edge_bump = max(edge_bump, 0.01)
            if "reduce stake fraction" in recs:
                stake_reduction = max(stake_reduction, 0.01)
            if "prefer higher-information or microstructure features" in recs:
                prefer_information = True
            if "failure_rate_above_15pct" in blockers:
                avoid_high_failure_models = True
        return {
            "edge_bump": edge_bump,
            "stake_reduction": stake_reduction,
            "prefer_information": prefer_information,
            "avoid_high_failure_models": avoid_high_failure_models,
        }

    def _dominant_value(self, values: Sequence[str], *, fallback: str) -> str:
        if not values:
            return fallback
        counts = Counter(values)
        return counts.most_common(1)[0][0]

    def _nearest_choice(self, options: Sequence[int], value: int) -> int | None:
        if not options:
            return None
        return min((int(option) for option in options), key=lambda option: abs(option - int(value)))

    def _allowed_choice(self, options: Sequence[str], candidate: str, *, fallback: str) -> str:
        normalized = [str(option) for option in options]
        if candidate in normalized:
            return candidate
        if fallback in normalized:
            return fallback
        return normalized[0] if normalized else fallback

    def _clip_range(self, bounds: Sequence[float] | None, value: float, *, fallback: float) -> float:
        if not bounds:
            return round(float(value if value else fallback), 6)
        low = float(bounds[0])
        high = float(bounds[-1])
        return round(min(max(float(value), low), high), 6)

    def _learning_rate_for_model(self, model_class: str, bounds: MutationBounds) -> float:
        preferred = {
            "logit": 0.02,
            "gbdt": 0.04,
            "tft": 0.01,
            "transformer": 0.008,
            "rules": 0.015,
        }.get(str(model_class or "logit").lower(), 0.02)
        return self._clip_range(bounds.hyperparameter_ranges.get("learning_rate"), preferred, fallback=0.02)

    def _idea_primary_venue(self, idea: Dict[str, Any]) -> str:
        tokens = " ".join(
            [
                str(idea.get("title") or ""),
                str(idea.get("summary") or ""),
                " ".join(str(item) for item in (idea.get("tags") or [])),
                " ".join(str(item) for item in (idea.get("family_candidates") or [])),
            ]
        ).lower()
        if any(token in tokens for token in ["polymarket", "prediction market", "event contract"]):
            return "polymarket"
        if any(token in tokens for token in ["betfair", "horse racing", "football odds", "book synchronization"]):
            return "betfair"
        return "binance"

    def _idea_domains(self, idea: Dict[str, Any]) -> List[str]:
        tokens = " ".join(
            [
                str(idea.get("title") or ""),
                str(idea.get("summary") or ""),
                " ".join(str(item) for item in (idea.get("tags") or [])),
            ]
        ).lower()
        domains: List[str] = []
        if any(token in tokens for token in ["regime", "cascade", "phase", "liquidation"]):
            domains.append("statistical_physics")
        if any(token in tokens for token in ["microstructure", "depth", "order book", "queue", "lag"]):
            domains.append("microstructure")
        if any(token in tokens for token in ["cross venue", "cross-market", "propagation", "network"]):
            domains.append("network_epidemiology")
        if any(token in tokens for token in ["entropy", "information", "signal"]):
            domains.append("information_theory")
        if any(token in tokens for token in ["control", "policy", "adaptive", "feedback"]):
            domains.append("control_rl")
        if any(token in tokens for token in ["causal", "bayesian", "probability", "calibration"]):
            domains.append("bayesian_causal")
        if any(token in tokens for token in ["crowd", "behavior", "consensus", "game"]):
            domains.append("game_theory_behavioral")
        if not domains:
            domains = ["econometrics", "microstructure", "information_theory"]
        while len(domains) < 3:
            for fallback in ["econometrics", "microstructure", "information_theory", "control_rl"]:
                if fallback not in domains:
                    domains.append(fallback)
                if len(domains) >= 3:
                    break
        return domains[:3]

    def _connector_for_venue(self, venue: str) -> str:
        return {
            "binance": "binance_core",
            "betfair": "betfair_core",
            "polymarket": "polymarket_core",
        }.get(str(venue or "binance").lower(), "binance_core")

    def _family_label_for_idea(self, idea: Dict[str, Any], *, venue: str) -> str:
        title = str(idea.get("title") or "").strip()
        stem = _compact_title_fragment(title or "Incubation")
        venue_label = {
            "binance": "Binance",
            "betfair": "Betfair",
            "polymarket": "Polymarket",
        }.get(venue, venue.title())
        return f"{venue_label} {stem} Incubator"

    def _family_id_for_idea(self, idea: Dict[str, Any], *, venue: str, existing_family_ids: Sequence[str]) -> str:
        title = str(idea.get("title") or "") or str(idea.get("idea_id") or "incubator")
        base_slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        base_slug = "_".join(base_slug.split("_")[:3]) or "incubator"
        candidate = f"{venue}_{base_slug}"
        existing = {str(item) for item in existing_family_ids}
        if candidate not in existing:
            return candidate
        digest = hashlib.sha1(f"{title}|{idea.get('idea_id')}".encode("utf-8")).hexdigest()[:6]
        return f"{candidate}_{digest}"


def build_proposal_title(
    *,
    family: FactoryFamily,
    proposal_kind: str,
    proposal_index: int,
    scientific_domains: Sequence[str],
    model_class: str,
    raw_title: str | None = None,
    source_idea_title: str | None = None,
) -> str:
    if proposal_kind != "new_model":
        return str(raw_title or f"{family.label} Challenger {proposal_index}").strip()
    domain_token = _domain_title(scientific_domains[0] if scientific_domains else "adaptive")
    model_token = str(model_class or "model").strip().replace("_", " ").title()
    if source_idea_title:
        stem = _compact_title_fragment(source_idea_title)
    else:
        stem = domain_token
    return f"{family.label} {stem} {model_token} Model {proposal_index}"


def normalize_alpha_thesis(
    thesis: str,
    *,
    family: FactoryFamily,
    proposal_kind: str,
) -> str:
    text = " ".join(str(thesis or "").strip().split())
    if not text:
        text = family.thesis
    prefix = "we believe we can create alpha by "
    lowered = text.lower()
    if lowered.startswith(prefix):
        body = text[len(prefix) :].strip()
    else:
        body = text
        for lead in [
            family.thesis,
            "This new model deliberately explores",
            "This variant fuses",
            "This new model",
            "This variant",
        ]:
            if body.startswith(lead):
                body = body[len(lead) :].strip(" .,:;")
                break
    if proposal_kind == "new_model" and "new model" not in body.lower():
        body = f"exploring a new model that {body}".strip()
    body = body.rstrip(".")
    if body:
        body = body[0].lower() + body[1:]
    return f"We believe we can create alpha by {body}."


def normalize_family_thesis(thesis: str) -> str:
    text = " ".join(str(thesis or "").strip().split()).rstrip(".")
    if not text:
        text = "exploring a new bounded strategy family with a testable alpha thesis"
    prefix = "we believe we can create alpha by "
    if text.lower().startswith(prefix):
        body = text[len(prefix) :].strip()
    else:
        body = text
    if body:
        body = body[0].lower() + body[1:]
    return f"We believe we can create alpha by {body}."


def _domain_title(value: str) -> str:
    text = str(value or "adaptive").replace("_", " ").strip()
    return "".join(part.capitalize() for part in text.split()) or "Adaptive"


def _compact_title_fragment(value: str) -> str:
    words = [word.strip(" ,.:;!?'\"") for word in str(value or "").split()]
    words = [word for word in words if word]
    if not words:
        return "Adaptive"
    return "".join(word.capitalize() for word in words[:2])
