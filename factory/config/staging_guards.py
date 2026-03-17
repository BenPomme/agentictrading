"""Staging scope guards and safe-default enforcement.

``StagingGuards`` is a read-only snapshot of all operating-mode flags that
control what the factory is allowed to do at runtime.  It is built once from
the live config module and exposed via ``OperatorStatus`` so the operator can
verify the current safety posture at a glance.

Safe defaults (first bring-up):
- 1 active family
- 1 active model per family
- 1 challenger per family
- paper trading disabled
- live trading hard-disabled
- autonomous mutation disabled
- autonomous paper promotion disabled

Usage::

    from factory.config import load_staging_guards

    guards = load_staging_guards()
    if guards.live_trading_hard_disabled:
        ...
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class StagingGuards:
    """Immutable snapshot of operating-mode flags for one factory run."""

    # --- Idea sources ---
    idea_source_file: str
    web_idea_research_enabled: bool

    # --- Trading mode ---
    paper_trading_enabled: bool
    live_trading_enabled: bool
    live_trading_hard_disabled: bool

    # --- Scope caps ---
    max_active_families: int
    max_active_models_per_family: int
    max_challengers_per_family: int

    # --- Autonomy ---
    allow_autonomous_mutation: bool
    allow_autonomous_paper_promotion: bool

    # --- Evaluation gates ---
    backtest_pass_monthly_roi: float
    require_forwardtest_pass: bool
    require_paper_evidence_for_challenger: bool

    # --- Budget limits ---
    daily_inference_budget_usd: float
    weekly_inference_budget_usd: float
    strict_budgets: bool

    # --- Downgrade thresholds ---
    budget_reviewer_removal_ratio: float
    budget_force_cheap_ratio: float
    budget_single_agent_ratio: float

    # --- Observability ---
    log_level: str
    log_json: bool
    operator_status_path: str
    promotion_report_path: str

    # ------------------------------------------------------------------
    # Safety checks
    # ------------------------------------------------------------------

    @property
    def live_trading_blocked(self) -> bool:
        """True when live trading is blocked by any guard."""
        return self.live_trading_hard_disabled or not self.live_trading_enabled

    @property
    def is_safe_for_preflight(self) -> bool:
        """True when all hard safety constraints for first bring-up are met."""
        return (
            self.live_trading_blocked
            and not self.paper_trading_enabled
            and not self.allow_autonomous_paper_promotion
            and self.max_active_families <= 3
            and self.max_challengers_per_family <= 3
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "idea_source_file": self.idea_source_file,
            "web_idea_research_enabled": self.web_idea_research_enabled,
            "trading": {
                "paper_enabled": self.paper_trading_enabled,
                "live_enabled": self.live_trading_enabled,
                "live_hard_disabled": self.live_trading_hard_disabled,
                "live_blocked": self.live_trading_blocked,
            },
            "scope": {
                "max_active_families": self.max_active_families,
                "max_active_models_per_family": self.max_active_models_per_family,
                "max_challengers_per_family": self.max_challengers_per_family,
            },
            "autonomy": {
                "allow_mutation": self.allow_autonomous_mutation,
                "allow_paper_promotion": self.allow_autonomous_paper_promotion,
            },
            "evaluation_gates": {
                "backtest_pass_monthly_roi": self.backtest_pass_monthly_roi,
                "require_forwardtest_pass": self.require_forwardtest_pass,
                "require_paper_evidence_for_challenger": self.require_paper_evidence_for_challenger,
            },
            "budget": {
                "daily_usd": self.daily_inference_budget_usd,
                "weekly_usd": self.weekly_inference_budget_usd,
                "strict": self.strict_budgets,
                "reviewer_removal_ratio": self.budget_reviewer_removal_ratio,
                "force_cheap_ratio": self.budget_force_cheap_ratio,
                "single_agent_ratio": self.budget_single_agent_ratio,
            },
            "observability": {
                "log_level": self.log_level,
                "log_json": self.log_json,
                "operator_status_path": self.operator_status_path,
                "promotion_report_path": self.promotion_report_path,
            },
            "is_safe_for_preflight": self.is_safe_for_preflight,
        }


def load_staging_guards(cfg: Optional[Any] = None) -> StagingGuards:
    """Build a ``StagingGuards`` snapshot from the config module.

    Parameters
    ----------
    cfg:
        Config module or object.  Defaults to the top-level ``config`` module.
        Accepts any object with attributes for testability.
    """
    if cfg is None:
        import config as cfg  # type: ignore[assignment]

    def _b(name: str, default: bool) -> bool:
        return bool(getattr(cfg, name, default))

    def _f(name: str, default: float) -> float:
        return float(getattr(cfg, name, default))

    def _i(name: str, default: int) -> int:
        return int(getattr(cfg, name, default))

    def _s(name: str, default: str) -> str:
        return str(getattr(cfg, name, default))

    return StagingGuards(
        # Idea sources
        idea_source_file=_s("FACTORY_IDEA_SOURCE_FILE", "./IDEAS.md"),
        web_idea_research_enabled=_b("FACTORY_ENABLE_WEB_IDEA_RESEARCH", False),
        # Trading mode
        paper_trading_enabled=_b("FACTORY_ENABLE_PAPER_TRADING", False),
        live_trading_enabled=_b("FACTORY_ENABLE_LIVE_TRADING", False),
        live_trading_hard_disabled=_b("FACTORY_LIVE_TRADING_HARD_DISABLE", True),
        # Scope caps
        max_active_families=_i("FACTORY_MAX_ACTIVE_FAMILIES", 1),
        max_active_models_per_family=_i("FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY", 1),
        max_challengers_per_family=_i("FACTORY_MAX_CHALLENGERS_PER_FAMILY", 1),
        # Autonomy
        allow_autonomous_mutation=_b("FACTORY_ALLOW_AUTONOMOUS_MUTATION", False),
        allow_autonomous_paper_promotion=_b("FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION", False),
        # Evaluation gates
        backtest_pass_monthly_roi=_f("FACTORY_BACKTEST_PASS_MONTHLY_ROI", 0.05),
        require_forwardtest_pass=_b("FACTORY_REQUIRE_FORWARDTEST_PASS", True),
        require_paper_evidence_for_challenger=_b(
            "FACTORY_REQUIRE_PAPER_EVIDENCE_FOR_CHALLENGER", True
        ),
        # Budget
        daily_inference_budget_usd=_f("FACTORY_DAILY_INFERENCE_BUDGET_USD", 15.0),
        weekly_inference_budget_usd=_f("FACTORY_WEEKLY_INFERENCE_BUDGET_USD", 75.0),
        strict_budgets=_b("FACTORY_STRICT_BUDGETS", False),
        budget_reviewer_removal_ratio=_f("FACTORY_BUDGET_REVIEWER_REMOVAL_RATIO", 0.70),
        budget_force_cheap_ratio=_f("FACTORY_BUDGET_FORCE_CHEAP_RATIO", 0.80),
        budget_single_agent_ratio=_f("FACTORY_BUDGET_SINGLE_AGENT_RATIO", 0.90),
        # Observability
        log_level=_s("FACTORY_LOG_LEVEL", "INFO"),
        log_json=_b("FACTORY_LOG_JSON", True),
        operator_status_path=_s(
            "FACTORY_OPERATOR_STATUS_PATH", "artifacts/operator_status.json"
        ),
        promotion_report_path=_s(
            "FACTORY_PROMOTION_REPORT_PATH", "artifacts/trade_ready_models.md"
        ),
    )
