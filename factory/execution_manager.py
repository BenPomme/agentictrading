"""Execution Manager: Manages local portfolio runner processes."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import config
from factory.execution_targets import parse_runtime_portfolio_alias
from factory.paper_data import runner_interval_for_portfolio
from factory.registry import FactoryRegistry
from factory.state_store import PortfolioStateStore


@dataclass(frozen=True)
class RuntimePortfolioSpec:
    portfolio_id: str
    label: str
    enabled: bool
    control_mode: str = "local_managed"
    canonical_portfolio_id: Optional[str] = None


_KNOWN_PORTFOLIOS: Dict[str, RuntimePortfolioSpec] = {
    "betfair_core": RuntimePortfolioSpec("betfair_core", "Betfair Core", True),
    "hedge_validation": RuntimePortfolioSpec("hedge_validation", "Hedge Validation", True),
    "hedge_research": RuntimePortfolioSpec("hedge_research", "Hedge Research", True),
    "cascade_alpha": RuntimePortfolioSpec(
        "cascade_alpha",
        "Cascade Alpha",
        os.getenv("CASCADE_ALPHA_ENABLED", "true").lower() == "true",
    ),
    "contrarian_legacy": RuntimePortfolioSpec(
        "contrarian_legacy",
        "Contrarian Legacy",
        os.getenv("CONTRARIAN_LEGACY_ENABLED", "true").lower() == "true",
    ),
    "polymarket_quantum_fold": RuntimePortfolioSpec(
        "polymarket_quantum_fold",
        "Polymarket Quantum-Fold",
        os.getenv("POLYMARKET_QF_ENABLED", "true").lower() == "true",
    ),
    "alpaca_paper": RuntimePortfolioSpec(
        "alpaca_paper",
        "Alpaca Paper (Stocks/ETFs)",
        os.getenv("ALPACA_PAPER_ENABLED", "true").lower() == "true",
    ),
}


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _embedded_state_root() -> Path:
    root = Path(getattr(config, "PORTFOLIO_STATE_ROOT", "data/portfolios"))
    if not root.is_absolute():
        root = _project_root() / root
    return root


def _resolve_portfolio_from_registry(portfolio_id: str) -> Optional[RuntimePortfolioSpec]:
    """Dynamically resolve a portfolio spec from the factory registry."""
    try:
        from factory.registry import FactoryRegistry

        project_root = _project_root()
        factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
        if not factory_root.is_absolute():
            factory_root = project_root / factory_root

        registry = FactoryRegistry(str(factory_root))
        for lineage in registry.lineages():
            if not lineage.active:
                continue
            if portfolio_id in lineage.target_portfolios:
                label = lineage.family_id.replace("_", " ").title()
                return RuntimePortfolioSpec(
                    portfolio_id=portfolio_id,
                    label=label,
                    enabled=True,
                    control_mode="local_managed",
                    canonical_portfolio_id=portfolio_id,
                )
    except Exception:
        pass
    return None


def get_runtime_portfolio_spec(portfolio_id: str) -> RuntimePortfolioSpec:
    parsed_alias = parse_runtime_portfolio_alias(portfolio_id)
    canonical_portfolio_id = parsed_alias["canonical_portfolio_id"] if parsed_alias else portfolio_id

    # Check known portfolios first
    if canonical_portfolio_id in _KNOWN_PORTFOLIOS:
        spec = _KNOWN_PORTFOLIOS[canonical_portfolio_id]
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=spec.label,
            enabled=spec.enabled,
            control_mode=spec.control_mode,
            canonical_portfolio_id=canonical_portfolio_id,
        )

    # Try dynamic resolution
    dynamic_spec = _resolve_portfolio_from_registry(canonical_portfolio_id)
    if dynamic_spec is not None:
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=dynamic_spec.label,
            enabled=dynamic_spec.enabled,
            control_mode=dynamic_spec.control_mode,
            canonical_portfolio_id=canonical_portfolio_id,
        )

    raise KeyError(f"Unknown portfolio: {portfolio_id}")


def _cycle_interval_for_portfolio(portfolio_id: str) -> float:
    """Derive cycle interval from active model data contracts, with legacy fallbacks."""
    try:
        project_root = _project_root()
        factory_root = Path(getattr(config, "FACTORY_ROOT", "data/factory"))
        if not factory_root.is_absolute():
            factory_root = project_root / factory_root

        registry = FactoryRegistry(str(factory_root))
        interval = runner_interval_for_portfolio(portfolio_id, registry)
        if interval > 0:
            return interval
    except Exception:
        pass

    # Legacy fallback
    _LEGACY_INTERVALS: Dict[str, float] = {
        "alpaca_paper": 3600.0,
        "contrarian_legacy": 28800.0,
        "cascade_alpha": 28800.0,
        "hedge_validation": 28800.0,
        "hedge_research": 28800.0,
        "polymarket_quantum_fold": 300.0,
        "betfair_core": 300.0,
    }
    return _LEGACY_INTERVALS.get(portfolio_id, 300.0)


class ExecutionManager:
    """Manages local portfolio runner processes."""

    def __init__(self) -> None:
        self._project_root = _project_root()
        self._state_root = _embedded_state_root()

    @staticmethod
    def _pid_running(pid: Optional[int]) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def status(self, portfolio_id: str) -> Dict[str, Any]:
        store = PortfolioStateStore(portfolio_id, root=str(self._state_root))
        runtime_health = store.read_runtime_health()
        pid = store.read_pid()
        heartbeat = dict(runtime_health.get("heartbeat") or store.read_heartbeat())
        process = dict(runtime_health.get("process") or {})
        publication = dict(runtime_health.get("publication") or {})
        health = dict(runtime_health.get("health") or {})

        return {
            "running": bool(process.get("running")) if process else self._pid_running(pid),
            "pid": process.get("pid") if process.get("pid") is not None else pid,
            "heartbeat": heartbeat,
            "runtime_health": runtime_health,
            "runtime_status": str(process.get("status") or runtime_health.get("runtime_status") or ""),
            "publish_status": str(publication.get("status") or ""),
            "health_status": str(health.get("status") or ""),
            "issue_codes": list(health.get("issue_codes") or []),
            "contract_source": "runtime_health" if runtime_health else "legacy",
            "last_publish_ts": publication.get("last_publish_at") or runtime_health.get("last_publish_at"),
        }

    def start(self, portfolio_id: str) -> Dict[str, object]:
        try:
            # Validate portfolio exists
            get_runtime_portfolio_spec(portfolio_id)
        except KeyError:
            return {"ok": False, "error": "unknown_portfolio"}

        current = self.status(portfolio_id)
        if current.get("running"):
            return {"ok": False, "error": "already_running", "pid": current.get("pid")}

        state_dir = self._state_root / portfolio_id
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / "runner.log"
        log_handle = log_path.open("a", encoding="utf-8")
        interval = _cycle_interval_for_portfolio(portfolio_id)

        proc = subprocess.Popen(
            [
                "python3", "-m", "factory.local_runner_main",
                "--portfolio", portfolio_id,
                "--interval", str(interval),
            ],
            cwd=str(self._project_root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        (state_dir / "runner.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
        return {"ok": True, "pid": proc.pid}

    def stop(self, portfolio_id: str) -> Dict[str, object]:
        current = self.status(portfolio_id)
        pid = current.get("pid")
        if not self._pid_running(pid if isinstance(pid, int) else None):
            return {"ok": False, "error": "not_running"}
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}


# Backwards compatibility alias
EmbeddedExecutionManager = ExecutionManager
RuntimeProcessManager = ExecutionManager


def get_process_manager() -> ExecutionManager:
    return ExecutionManager()
