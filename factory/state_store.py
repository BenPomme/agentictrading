from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


@dataclass(frozen=True)
class AccountSnapshot:
    portfolio_id: str
    currency: str
    current_balance: float
    realized_pnl: float
    roi_pct: float
    drawdown_pct: float

    @classmethod
    def from_dict(cls, payload: Dict[str, Any] | None) -> "AccountSnapshot | None":
        if not payload:
            return None
        return cls(
            portfolio_id=str(payload.get("portfolio_id") or ""),
            currency=str(payload.get("currency") or "USD"),
            current_balance=float(payload.get("current_balance", 0.0) or 0.0),
            realized_pnl=float(payload.get("realized_pnl", 0.0) or 0.0),
            roi_pct=float(payload.get("roi_pct", 0.0) or 0.0),
            drawdown_pct=float(payload.get("drawdown_pct", 0.0) or 0.0),
        )


def _portfolio_state_root() -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        return Path(explicit)
    execution_root = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if execution_root:
        return Path(execution_root) / "data" / "portfolios"
    return Path("data/portfolios")


class PortfolioStateStore:
    def __init__(self, portfolio_id: str, root: Optional[str] = None):
        self.portfolio_id = portfolio_id
        base_root = Path(root) if root else _portfolio_state_root()
        self.base_dir = base_root / portfolio_id
        self.account_path = self.base_dir / "account.json"
        self.trades_path = self.base_dir / "trades.jsonl"
        self.events_path = self.base_dir / "events.jsonl"
        self.heartbeat_path = self.base_dir / "heartbeat.json"
        self.state_path = self.base_dir / "state.json"
        self.pid_path = self.base_dir / "runner.pid"

    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _read_jsonl(self, path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
        if limit is not None:
            return rows[-limit:]
        return rows

    def read_account(self) -> AccountSnapshot | None:
        return AccountSnapshot.from_dict(self._read_json(self.account_path, default={}) or {})

    def read_trades(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self._read_jsonl(self.trades_path, limit=limit)

    def read_events(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self._read_jsonl(self.events_path, limit=limit)

    def read_heartbeat(self) -> Dict[str, Any]:
        return self._read_json(self.heartbeat_path, default={}) or {}

    def read_state(self) -> Dict[str, Any]:
        return self._read_json(self.state_path, default={}) or {}

    def read_pid(self) -> int | None:
        if not self.pid_path.exists():
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            return None
