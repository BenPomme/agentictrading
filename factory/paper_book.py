"""Paper trade execution book for NEBULA embedded runners.

Tracks simulated positions, cash balance, equity. Logs trades and balance
snapshots so the dashboard can render live P&L charts.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    direction: str  # "long" | "short"
    size: float  # notional or qty
    entry_price: float
    entry_ts: str
    unrealized_pnl: float = 0.0


@dataclass
class TradeRecord:
    ts: str
    symbol: str
    side: str  # "buy" | "sell"
    direction: str  # "long" | "short" | "flat"
    qty: float
    price: float
    notional: float
    pnl: float
    fees: float
    balance_after: float
    meta: Dict[str, Any] = field(default_factory=dict)


class PaperTradeBook:
    """Simulated paper trading book with persistence to JSONL files.

    Args:
        portfolio_dir: Path to ``data/portfolios/<portfolio_id>/``.
        initial_balance: Starting cash.
        fee_bps: Round-trip fee in basis points (default 10 bps = 0.1%).
        max_position_pct: Max fraction of equity in a single position.
    """

    def __init__(
        self,
        portfolio_dir: Path,
        initial_balance: float = 10_000.0,
        fee_bps: float = 10.0,
        max_position_pct: float = 1.0,
    ) -> None:
        self.portfolio_dir = Path(portfolio_dir)
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)

        self.fee_rate = fee_bps / 10_000.0
        self.max_position_pct = max(0.0, min(1.0, max_position_pct))

        self.trades_path = self.portfolio_dir / "trades.jsonl"
        self.balance_path = self.portfolio_dir / "balance_history.jsonl"
        self.account_path = self.portfolio_dir / "account.json"

        self.positions: Dict[str, Position] = {}
        self.cash: float = initial_balance
        self.initial_balance = initial_balance
        self.peak_equity: float = initial_balance
        self.realized_pnl: float = 0.0
        self.wins: int = 0
        self.losses: int = 0
        self.trade_count: int = 0

        self._load_persisted_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_persisted_state(self) -> None:
        """Restore state from account.json if it exists."""
        if self.account_path.exists():
            try:
                data = json.loads(self.account_path.read_text(encoding="utf-8"))
                self.cash = float(data.get("cash", self.initial_balance))
                self.realized_pnl = float(data.get("realized_pnl", 0.0))
                self.peak_equity = float(data.get("peak_equity", self.initial_balance))
                self.wins = int(data.get("wins", 0))
                self.losses = int(data.get("losses", 0))
                self.trade_count = int(data.get("trade_count", 0))
                self.initial_balance = float(data.get("initial_balance", self.initial_balance))
                for pos_d in data.get("positions", []):
                    pos = Position(**pos_d)
                    self.positions[pos.symbol] = pos
                logger.info("Restored paper book: cash=%.2f pnl=%.2f trades=%d", self.cash, self.realized_pnl, self.trade_count)
            except Exception as exc:
                logger.warning("Could not restore paper book state: %s", exc)

    def _save_account(self) -> None:
        equity = self.equity()
        self.peak_equity = max(self.peak_equity, equity)
        drawdown_pct = ((self.peak_equity - equity) / self.peak_equity * 100.0) if self.peak_equity > 0 else 0.0
        roi_pct = ((equity - self.initial_balance) / self.initial_balance * 100.0) if self.initial_balance > 0 else 0.0
        payload = {
            "portfolio_id": self.portfolio_dir.name,
            "currency": "USD",
            "current_balance": round(equity, 4),
            "cash": round(self.cash, 4),
            "initial_balance": round(self.initial_balance, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "roi_pct": round(roi_pct, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "peak_equity": round(self.peak_equity, 4),
            "wins": self.wins,
            "losses": self.losses,
            "trade_count": self.trade_count,
            "last_updated": _now_iso(),
            "positions": [asdict(p) for p in self.positions.values()],
        }
        self.account_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _append_trade(self, rec: TradeRecord) -> None:
        with self.trades_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def record_balance_snapshot(self) -> None:
        """Append a timestamped balance point for the P&L chart."""
        equity = self.equity()
        point = {"ts": _now_iso(), "balance": round(equity, 4)}
        with self.balance_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(point) + "\n")

    # ------------------------------------------------------------------
    # Portfolio math
    # ------------------------------------------------------------------

    def equity(self) -> float:
        return self.cash + sum(p.unrealized_pnl for p in self.positions.values())

    def update_mark(self, symbol: str, current_price: float) -> None:
        """Mark-to-market an open position."""
        pos = self.positions.get(symbol)
        if pos is None:
            return
        if pos.direction == "long":
            pos.unrealized_pnl = (current_price - pos.entry_price) / pos.entry_price * pos.size
        elif pos.direction == "short":
            pos.unrealized_pnl = (pos.entry_price - current_price) / pos.entry_price * pos.size

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        direction: str,
        size_pct: float,
        price: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[TradeRecord]:
        """Open a new paper position. ``size_pct`` is fraction of equity."""
        if symbol in self.positions:
            logger.debug("Already positioned in %s, skipping open", symbol)
            return None

        equity = self.equity()
        alloc_pct = min(size_pct, self.max_position_pct)
        notional = equity * alloc_pct
        if notional <= 0 or price <= 0:
            return None

        fees = notional * self.fee_rate
        self.cash -= fees

        side = "buy" if direction == "long" else "sell"
        self.positions[symbol] = Position(
            symbol=symbol,
            direction=direction,
            size=notional,
            entry_price=price,
            entry_ts=_now_iso(),
        )

        rec = TradeRecord(
            ts=_now_iso(),
            symbol=symbol,
            side=side,
            direction=direction,
            qty=notional / price,
            price=price,
            notional=notional,
            pnl=0.0,
            fees=fees,
            balance_after=round(self.equity(), 4),
            meta=meta or {},
        )
        self._append_trade(rec)
        self.trade_count += 1
        self._save_account()
        logger.info("OPEN %s %s %.2f @ %.4f (fees=%.4f)", direction, symbol, notional, price, fees)
        return rec

    def close_position(
        self,
        symbol: str,
        price: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[TradeRecord]:
        """Close an existing paper position at ``price``."""
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        if pos.direction == "long":
            pnl = (price - pos.entry_price) / pos.entry_price * pos.size
        else:
            pnl = (pos.entry_price - price) / pos.entry_price * pos.size

        fees = pos.size * self.fee_rate
        net_pnl = pnl - fees
        self.cash += pos.size + net_pnl
        self.realized_pnl += net_pnl

        if net_pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

        side = "sell" if pos.direction == "long" else "buy"
        rec = TradeRecord(
            ts=_now_iso(),
            symbol=symbol,
            side=side,
            direction="flat",
            qty=pos.size / price,
            price=price,
            notional=pos.size,
            pnl=round(net_pnl, 4),
            fees=fees,
            balance_after=round(self.equity(), 4),
            meta=meta or {},
        )
        self._append_trade(rec)
        self.trade_count += 1
        self._save_account()
        self.record_balance_snapshot()
        logger.info("CLOSE %s pnl=%.4f (fees=%.4f) balance=%.2f", symbol, net_pnl, fees, self.equity())
        return rec

    def apply_funding_pnl(
        self,
        symbol: str,
        pnl: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> TradeRecord:
        """Record a funding-rate P&L event (no position open/close)."""
        fees = abs(pnl) * self.fee_rate
        net = pnl - fees
        self.cash += net
        self.realized_pnl += net
        if net > 0:
            self.wins += 1
        else:
            self.losses += 1

        rec = TradeRecord(
            ts=_now_iso(),
            symbol=symbol,
            side="funding",
            direction="funding",
            qty=0,
            price=0,
            notional=abs(pnl),
            pnl=round(net, 4),
            fees=round(fees, 4),
            balance_after=round(self.equity(), 4),
            meta=meta or {},
        )
        self._append_trade(rec)
        self.trade_count += 1
        self._save_account()
        self.record_balance_snapshot()
        return rec


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
