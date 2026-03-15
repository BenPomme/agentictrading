from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import config

from factory.execution_targets import parse_runtime_portfolio_alias
from factory.state_store import PortfolioStateStore


def get_process_manager():
    """Return the execution process manager: embedded (in-repo) or external."""
    if getattr(config, "FACTORY_EMBEDDED_EXECUTION_ENABLED", False):
        from factory.embedded_execution import EmbeddedExecutionManager
        return EmbeddedExecutionManager()
    return RuntimeProcessManager()


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


def _execution_repo_root() -> Path | None:
    return None


def _load_from_execution_repo(module_name: str):
    return None


def _resolve_portfolio_from_registry(portfolio_id: str) -> Optional[RuntimePortfolioSpec]:
    """Dynamically resolve a portfolio spec from the factory registry.

    Any active lineage whose target_portfolios includes *portfolio_id* qualifies
    the portfolio as a valid, enabled runner target -- no hardcoding required.
    """
    try:
        from factory.registry import FactoryRegistry

        project_root = Path(__file__).resolve().parent.parent
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
    registry = _load_from_execution_repo("monitoring.portfolio_registry")
    if registry is not None and hasattr(registry, "get_portfolio_spec"):
        spec = getattr(registry, "get_portfolio_spec")(canonical_portfolio_id)
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=str(getattr(spec, "label", canonical_portfolio_id)),
            enabled=bool(getattr(spec, "enabled", True)),
            control_mode=str(getattr(spec, "control_mode", "local_managed")),
            canonical_portfolio_id=canonical_portfolio_id,
        )
    if canonical_portfolio_id in _KNOWN_PORTFOLIOS:
        spec = _KNOWN_PORTFOLIOS[canonical_portfolio_id]
        return RuntimePortfolioSpec(
            portfolio_id=portfolio_id,
            label=spec.label,
            enabled=spec.enabled,
            control_mode=spec.control_mode,
            canonical_portfolio_id=canonical_portfolio_id,
        )
    dynamic_spec = _resolve_portfolio_from_registry(canonical_portfolio_id)
    if dynamic_spec is not None:
        return dynamic_spec
    raise KeyError(f"Unknown portfolio: {portfolio_id}")


class RuntimeProcessManager:
    def __init__(self) -> None:
        self._execution_root = _execution_repo_root()

    @staticmethod
    def _pid_running(pid: Optional[int]) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def status(self, portfolio_id: str) -> Dict[str, object]:
        store = PortfolioStateStore(portfolio_id)
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
        spec = get_runtime_portfolio_spec(portfolio_id)
        if spec.control_mode == "disabled" or not spec.enabled:
            return {"ok": False, "error": "portfolio_disabled"}
        if self._execution_root is None:
            return {"ok": False, "error": "execution_repo_not_configured"}
        current = self.status(portfolio_id)
        if current.get("running"):
            return {"ok": False, "error": "already_running", "pid": current.get("pid")}
        state_dir = self._execution_root / "data" / "portfolios" / portfolio_id
        state_dir.mkdir(parents=True, exist_ok=True)
        log_handle = (state_dir / "runner.log").open("a", encoding="utf-8")
        canonical_portfolio_id = str(spec.canonical_portfolio_id or portfolio_id)
        cmd = ["nohup", "python3", "scripts/run_portfolio.py", "--portfolio", canonical_portfolio_id]
        if canonical_portfolio_id != portfolio_id:
            cmd.extend(["--runtime-portfolio-id", portfolio_id])
        proc = subprocess.Popen(
            cmd,
            cwd=str(self._execution_root),
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
