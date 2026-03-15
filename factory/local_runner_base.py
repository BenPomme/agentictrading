"""Base for local paper-only portfolio runners run inside the factory repo."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import config

from factory.state_store import PortfolioStateStore

logger = logging.getLogger(__name__)


def _portfolio_state_root() -> Path:
    root = Path(getattr(config, "PORTFOLIO_STATE_ROOT", "data/portfolios"))
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root


def is_us_market_open() -> bool:
    """Rough check: US equity markets open Mon-Fri 09:30-16:00 ET."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    from datetime import time as dtime
    return dtime(9, 30) <= t <= dtime(16, 0)


class LocalPortfolioRunner(ABC):
    """
    Abstract base for a single-portfolio paper runner.
    Writes state, heartbeat, account, and optional trades/events under the portfolio state dir.
    """

    # Subclasses can set to True to skip run_cycle when US market is closed.
    requires_market_open: bool = False
    # Subclasses can set to True to skip run_cycle when crypto is not running (always on).
    crypto_venue: bool = False

    def __init__(self, portfolio_id: str) -> None:
        self.portfolio_id = portfolio_id
        root = _portfolio_state_root()
        self._store = PortfolioStateStore(portfolio_id, root=str(root))
        self.portfolio_dir = root / portfolio_id
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def run_cycle(self) -> Dict[str, Any]:
        """
        One iteration of the runner loop (e.g. poll feeds, update paper book).
        Return a dict of state to merge into state.json (e.g. readiness, last_cycle_ts).
        """
        pass

    def should_skip_cycle(self) -> bool:
        """Return True if the runner should idle this cycle (e.g. market closed)."""
        if self.requires_market_open and not is_us_market_open():
            return True
        return False

    def write_heartbeat(self, extra: Dict[str, Any] | None = None) -> None:
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "portfolio_id": self.portfolio_id,
            "status": "running",
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

    def write_runtime_health(self, health_status: str = "healthy", error: str | None = None) -> None:
        payload = {
            "schema_version": 1,
            "portfolio_id": self.portfolio_id,
            "process": {"pid": None, "running": True, "status": "running"},
            "publication": {"status": "publishing", "last_publish_at": datetime.now(timezone.utc).isoformat()},
            "health": {"status": health_status, "issue_codes": [], "error": error},
            "status": "running",
            "running": True,
        }
        self._store.runtime_health_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.runtime_health_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _sleep_with_heartbeat(self, total_seconds: float, heartbeat_interval: float = 30.0) -> None:
        """Sleep for *total_seconds* while refreshing the heartbeat every *heartbeat_interval*."""
        remaining = total_seconds
        while remaining > 0:
            nap = min(remaining, heartbeat_interval)
            time.sleep(nap)
            remaining -= nap
            if remaining > 0:
                self.write_heartbeat({"idle_until_next_cycle": True})

    def run(self, cycle_interval_sec: float = 60.0) -> None:
        """Main loop: write heartbeat, run_cycle, merge state, sleep."""
        logger.info("Runner started for portfolio=%s interval=%.1fs", self.portfolio_id, cycle_interval_sec)
        while True:
            try:
                self.write_heartbeat()
                self.write_runtime_health()

                if self.should_skip_cycle():
                    logger.debug("Skipping cycle (market closed) for %s", self.portfolio_id)
                    self.write_heartbeat({"skipped": "market_closed"})
                    self._sleep_with_heartbeat(cycle_interval_sec)
                    continue

                cycle_out = self.run_cycle()
                state = self._store.read_state()
                state.update(cycle_out or {})
                state["last_heartbeat_ts"] = time.time()
                state["portfolio_id"] = self.portfolio_id
                state["running"] = True
                state["mode"] = "paper"
                self.write_state(state)
                self.write_heartbeat({"last_cycle_ts": time.time()})
                self.write_runtime_health()
            except Exception as e:
                logger.exception("Runner cycle error for %s: %s", self.portfolio_id, e)
                self.write_heartbeat({"error": str(e)})
                self.write_runtime_health(health_status="warning", error=str(e))
            self._sleep_with_heartbeat(cycle_interval_sec)


class StubLocalRunner(LocalPortfolioRunner):
    """Minimal runner that only keeps heartbeat and state alive; no real trading logic."""

    def run_cycle(self) -> Dict[str, Any]:
        return {
            "ready": True,
            "mode": "paper",
            "runner": "stub",
        }
