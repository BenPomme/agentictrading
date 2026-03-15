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
    wins: int = 0
    losses: int = 0
    trade_count: int = 0
    last_updated: str = ""

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
            wins=int(payload.get("wins", 0) or 0),
            losses=int(payload.get("losses", 0) or 0),
            trade_count=int(payload.get("trade_count", 0) or 0),
            last_updated=str(payload.get("last_updated") or ""),
        )


def _portfolio_state_root() -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        return p
    execution_root = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if execution_root:
        return Path(execution_root) / "data" / "portfolios"
    portfolio_root = str(getattr(config, "PORTFOLIO_STATE_ROOT", "") or "").strip()
    if portfolio_root:
        return Path(portfolio_root)
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
        self.readiness_path = self.base_dir / "readiness.json"
        self.config_snapshot_path = self.base_dir / "config_snapshot.json"
        self.runtime_health_path = self.base_dir / "runtime_health.json"
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
        if limit is not None and limit > 0:
            try:
                with path.open("rb") as handle:
                    handle.seek(0, 2)
                    end = handle.tell()
                    block = 4096
                    buffer = b""
                    pos = end
                    while pos > 0 and buffer.count(b"\n") <= limit * 2:
                        read_size = min(block, pos)
                        pos -= read_size
                        handle.seek(pos)
                        buffer = handle.read(read_size) + buffer
                lines = buffer.decode("utf-8", errors="ignore").splitlines()[-limit * 2 :]
            except Exception:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        else:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines:
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

    def read_readiness(self) -> Dict[str, Any]:
        return self._read_json(self.readiness_path, default={}) or {}

    def read_config_snapshot(self) -> Dict[str, Any]:
        return self._read_json(self.config_snapshot_path, default={}) or {}

    def read_runtime_health(self) -> Dict[str, Any]:
        payload = self._read_json(self.runtime_health_path, default={}) or {}
        if payload:
            return payload
        heartbeat = self.read_heartbeat()
        state = self.read_state()
        readiness = self.read_readiness()
        account = self.read_account()
        pid = self.read_pid()
        running = bool(state.get("running")) or str(heartbeat.get("status") or "").lower() == "running"
        status = str(state.get("status") or heartbeat.get("status") or ("running" if running else "idle") or "idle")
        blockers = list(readiness.get("blockers_v2") or readiness.get("blockers") or [])
        error = str(state.get("error") or "") or None
        heartbeat_ts = str(heartbeat.get("ts") or (account.last_updated if account is not None else "")).strip() or None
        account_payload = (
            {
                "portfolio_id": account.portfolio_id,
                "currency": account.currency,
                "current_balance": account.current_balance,
                "realized_pnl": account.realized_pnl,
                "roi_pct": account.roi_pct,
                "drawdown_pct": account.drawdown_pct,
                "wins": account.wins,
                "losses": account.losses,
                "trade_count": account.trade_count,
                "last_updated": account.last_updated,
            }
            if account is not None
            else {}
        )
        return {
            "schema_version": 0,
            "portfolio_id": self.portfolio_id,
            "canonical_portfolio_id": self.portfolio_id,
            "runtime_portfolio_id": self.portfolio_id,
            "process": {
                "pid": pid,
                "running": bool(pid),
                "status": "running" if bool(pid) else "idle",
                "started_at": None,
            },
            "publication": {
                "status": "publishing" if running else "idle",
                "first_publish_at": heartbeat_ts,
                "last_publish_at": heartbeat_ts,
            },
            "health": {
                "status": "critical" if error else ("warning" if blockers else "healthy"),
                "issue_codes": [],
                "error": error,
                "blockers": blockers,
            },
            "readiness": {
                "status": str(readiness.get("status") or ""),
                "score_pct": readiness.get("score_pct"),
                "blockers": blockers,
            },
            "account": account_payload,
            "heartbeat": heartbeat,
            "status": status,
            "running": running,
            "pid": pid,
            "last_publish_at": heartbeat_ts,
            "raw_state": state,
            "readiness_payload": readiness,
            "config_snapshot": self.read_config_snapshot(),
        }

    def read_pid(self) -> int | None:
        if not self.pid_path.exists():
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            return None
