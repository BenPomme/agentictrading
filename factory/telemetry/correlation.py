"""Operator-visible system status snapshot.

OperatorStatus is a point-in-time view of the factory's observable state:
- which backend is active and whether it's healthy
- current global budget consumption
- open circuit breakers
- recent fallback/downgrade activity
- latest provenance record references

``build_operator_status()`` assembles this from a RuntimeManager and an
optional CostGovernor instance, both of which are already in scope for
the orchestrator.  It is safe to call frequently; all fields degrade
gracefully when a component is unavailable.

Usage::

    from factory.telemetry.correlation import build_operator_status

    status = build_operator_status(runtime_manager)
    print(json.dumps(status.to_dict(), indent=2))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# OperatorStatus
# ---------------------------------------------------------------------------


@dataclass
class OperatorStatus:
    """
    Point-in-time snapshot of observable factory system state.

    All fields have safe defaults so the snapshot is always renderable
    even when components are unavailable.
    """

    # Runtime identity
    active_backend: str
    backend_healthy: Optional[bool]

    # Budget mode
    strict_budgets: bool

    # Global budget usage
    global_tokens_today: int
    global_usd_today: float
    global_budget_usd: float
    global_pct_used: float

    # Circuit breaker state
    circuit_global_open: bool
    circuit_family_open_ids: List[str]

    # Recent activity indicators
    recent_fallback_reasons: List[str]
    last_goldfish_record_id: Optional[str]
    last_runtime_run_id: Optional[str]
    downgrade_events_today: int
    stop_events_today: int

    # Timestamp
    as_of: str

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_backend": self.active_backend,
            "backend_healthy": self.backend_healthy,
            "strict_budgets": self.strict_budgets,
            "budget": {
                "global_tokens_today": self.global_tokens_today,
                "global_usd_today": round(self.global_usd_today, 6),
                "global_budget_usd": self.global_budget_usd,
                "global_pct_used": self.global_pct_used,
            },
            "circuit": {
                "global_open": self.circuit_global_open,
                "family_open_ids": self.circuit_family_open_ids,
            },
            "recent_fallback_reasons": self.recent_fallback_reasons,
            "last_goldfish_record_id": self.last_goldfish_record_id,
            "last_runtime_run_id": self.last_runtime_run_id,
            "downgrade_events_today": self.downgrade_events_today,
            "stop_events_today": self.stop_events_today,
            "as_of": self.as_of,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_operator_status(
    runtime_manager: Any,
    *,
    governor: Optional[Any] = None,
    last_goldfish_record_id: Optional[str] = None,
    last_runtime_run_id: Optional[str] = None,
    recent_fallback_reasons: Optional[List[str]] = None,
) -> OperatorStatus:
    """
    Assemble an OperatorStatus snapshot from live system components.

    Parameters
    ----------
    runtime_manager:
        RuntimeManager instance (or any object with ``backend_name`` and
        ``healthcheck()``).
    governor:
        Optional CostGovernor.  If None, the function tries
        ``runtime_manager.governor`` before giving up gracefully.
    last_goldfish_record_id:
        Caller-supplied latest Goldfish record ID (not stored in governor).
    last_runtime_run_id:
        Caller-supplied latest runtime run ID from most recent envelope.
    recent_fallback_reasons:
        Caller-supplied list of recent fallback reason strings.
    """
    # --- Backend identity -------------------------------------------------
    backend_name: str = getattr(runtime_manager, "backend_name", "unknown")

    backend_healthy: Optional[bool] = None
    try:
        backend_healthy = bool(runtime_manager.healthcheck())
    except Exception:
        backend_healthy = False

    # --- Locate governor --------------------------------------------------
    if governor is None:
        try:
            governor = runtime_manager.governor
        except AttributeError:
            pass

    # --- Budget state from governor ---------------------------------------
    strict_budgets = False
    global_tokens = 0
    global_usd = 0.0
    global_budget = 0.0
    global_pct = 0.0
    circuit_global_open = False
    circuit_family_ids: List[str] = []
    downgrade_count = 0
    stop_count = 0

    if governor is not None:
        try:
            snap = governor.budget_snapshot()

            usage = snap.get("global_usage", {})
            global_tokens = int(usage.get("tokens_today", 0) or 0)
            global_usd = float(usage.get("usd_today", 0.0) or 0.0)
            global_budget = float(usage.get("budget_usd", 0.0) or 0.0)
            global_pct = float(usage.get("pct_used", 0.0) or 0.0)
            strict_budgets = bool(snap.get("strict_enforcement", False))

            circuit = snap.get("circuit", {})
            circuit_global_open = (
                circuit.get("global", {}).get("state") == "open"
            )
            circuit_family_ids = [
                k
                for k, v in circuit.get("families", {}).items()
                if isinstance(v, dict) and v.get("state") == "open"
            ]

            ledger_snap = snap.get("ledger_summary", {})
            downgrade_count = int(ledger_snap.get("downgrade_events_today", 0) or 0)
            stop_count = int(ledger_snap.get("stop_events_today", 0) or 0)
        except Exception:
            pass  # Governor errors must not break operator status

    return OperatorStatus(
        active_backend=backend_name,
        backend_healthy=backend_healthy,
        strict_budgets=strict_budgets,
        global_tokens_today=global_tokens,
        global_usd_today=global_usd,
        global_budget_usd=global_budget,
        global_pct_used=global_pct,
        circuit_global_open=circuit_global_open,
        circuit_family_open_ids=circuit_family_ids,
        recent_fallback_reasons=list(recent_fallback_reasons or []),
        last_goldfish_record_id=last_goldfish_record_id,
        last_runtime_run_id=last_runtime_run_id,
        downgrade_events_today=downgrade_count,
        stop_events_today=stop_count,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
