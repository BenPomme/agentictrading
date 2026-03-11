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

from factory.state_store import PortfolioStateStore


@dataclass(frozen=True)
class RuntimePortfolioSpec:
    portfolio_id: str
    label: str
    enabled: bool
    control_mode: str = "local_managed"


_KNOWN_PORTFOLIOS: Dict[str, RuntimePortfolioSpec] = {
    "betfair_core": RuntimePortfolioSpec("betfair_core", "Betfair Core", True),
    "hedge_validation": RuntimePortfolioSpec("hedge_validation", "Hedge Validation", True),
    "hedge_research": RuntimePortfolioSpec("hedge_research", "Hedge Research", True),
    "cascade_alpha": RuntimePortfolioSpec(
        "cascade_alpha",
        "Cascade Alpha",
        os.getenv("CASCADE_ALPHA_ENABLED", "false").lower() == "true",
    ),
    "contrarian_legacy": RuntimePortfolioSpec(
        "contrarian_legacy",
        "Contrarian Legacy",
        os.getenv("CONTRARIAN_LEGACY_ENABLED", "false").lower() == "true",
    ),
    "polymarket_quantum_fold": RuntimePortfolioSpec(
        "polymarket_quantum_fold",
        "Polymarket Quantum-Fold",
        os.getenv("POLYMARKET_QF_ENABLED", "false").lower() == "true",
    ),
}


def _execution_repo_root() -> Path | None:
    raw = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    return root if root.exists() else None


def _load_from_execution_repo(module_name: str):
    root = _execution_repo_root()
    if root is None:
        return None
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def get_runtime_portfolio_spec(portfolio_id: str) -> RuntimePortfolioSpec:
    registry = _load_from_execution_repo("monitoring.portfolio_registry")
    if registry is not None and hasattr(registry, "get_portfolio_spec"):
        return getattr(registry, "get_portfolio_spec")(portfolio_id)
    if portfolio_id not in _KNOWN_PORTFOLIOS:
        raise KeyError(f"Unknown portfolio: {portfolio_id}")
    return _KNOWN_PORTFOLIOS[portfolio_id]


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
        pid = store.read_pid()
        heartbeat = store.read_heartbeat()
        return {
            "running": self._pid_running(pid),
            "pid": pid,
            "heartbeat": heartbeat,
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
        proc = subprocess.Popen(
            ["nohup", "python3", "scripts/run_portfolio.py", "--portfolio", portfolio_id],
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
