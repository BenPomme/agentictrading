"""Base for local paper-only portfolio runners run inside the factory repo."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import config

from factory.state_store import PortfolioStateStore


def _portfolio_state_root() -> Path:
    root = Path(getattr(config, "PORTFOLIO_STATE_ROOT", "data/portfolios"))
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root


class LocalPortfolioRunner(ABC):
    """
    Abstract base for a single-portfolio paper runner.
    Writes state, heartbeat, account, and optional trades/events under the portfolio state dir.
    """

    def __init__(self, portfolio_id: str) -> None:
        self.portfolio_id = portfolio_id
        root = _portfolio_state_root()
        self._store = PortfolioStateStore(portfolio_id, root=str(root))

    @abstractmethod
    def run_cycle(self) -> Dict[str, Any]:
        """
        One iteration of the runner loop (e.g. poll feeds, update paper book).
        Return a dict of state to merge into state.json (e.g. readiness, last_cycle_ts).
        """
        pass

    def write_heartbeat(self, extra: Dict[str, Any] | None = None) -> None:
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "portfolio_id": self.portfolio_id,
        }
        if extra:
            payload.update(extra)
        self._store.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.heartbeat_path.write_text(
            json.dumps(payload, indent=0), encoding="utf-8"
        )

    def write_state(self, state: Dict[str, Any]) -> None:
        self._store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.state_path.write_text(
            json.dumps(state, indent=0), encoding="utf-8"
        )

    def run(self, cycle_interval_sec: float = 60.0) -> None:
        """Main loop: write heartbeat, run_cycle, merge state, sleep."""
        while True:
            try:
                cycle_out = self.run_cycle()
                state = self._store.read_state()
                state.update(cycle_out or {})
                state["last_heartbeat_ts"] = time.time()
                state["portfolio_id"] = self.portfolio_id
                self.write_state(state)
                self.write_heartbeat({"last_cycle_ts": time.time()})
            except Exception as e:
                self.write_heartbeat({"error": str(e)})
            time.sleep(cycle_interval_sec)


class StubLocalRunner(LocalPortfolioRunner):
    """Minimal runner that only keeps heartbeat and state alive; no real trading logic."""

    def run_cycle(self) -> Dict[str, Any]:
        return {
            "ready": True,
            "mode": "paper",
            "runner": "stub",
        }
