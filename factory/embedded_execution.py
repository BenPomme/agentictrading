"""Embedded execution manager: run paper portfolio runners inside this repo."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

from factory.state_store import PortfolioStateStore


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _embedded_state_root() -> Path:
    root = Path(getattr(config, "PORTFOLIO_STATE_ROOT", "data/portfolios"))
    if not root.is_absolute():
        root = _project_root() / root
    return root


def _embedded_portfolio_ids() -> List[str]:
    """Portfolio IDs that have embedded runners (same set as runtime_execution._KNOWN_PORTFOLIOS)."""
    return [
        "betfair_core",
        "hedge_validation",
        "hedge_research",
        "cascade_alpha",
        "contrarian_legacy",
        "polymarket_quantum_fold",
    ]


class EmbeddedExecutionManager:
    """
    Starts/stops local portfolio runner processes in this repo.
    Status shape matches RuntimeProcessManager so FactoryExecutionBridge can use either.
    """

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
        pid = store.read_pid()
        heartbeat = store.read_heartbeat()
        return {
            "running": self._pid_running(pid),
            "pid": pid,
            "heartbeat": heartbeat,
        }

    def start(self, portfolio_id: str) -> Dict[str, object]:
        if portfolio_id not in _embedded_portfolio_ids():
            return {"ok": False, "error": "unknown_portfolio"}
        current = self.status(portfolio_id)
        if current.get("running"):
            return {"ok": False, "error": "already_running", "pid": current.get("pid")}
        state_dir = self._state_root / portfolio_id
        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = state_dir / "runner.log"
        log_handle = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            ["python3", "-m", "factory.local_runner_main", "--portfolio", portfolio_id],
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
