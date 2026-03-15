"""Paper trading runner for the HMM Regime Adaptive family."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from factory.local_runner_base import LocalPortfolioRunner
from factory.paper_book import PaperTradeBook

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class HMMRegimeRunner(LocalPortfolioRunner):
    requires_market_open = True

    def __init__(self, portfolio_id: str) -> None:
        super().__init__(portfolio_id)
        self._book = PaperTradeBook(
            portfolio_dir=self.portfolio_dir,
            initial_balance=10_000.0,
        )
        self.data_root = _project_root() / "data" / "yahoo" / "ohlcv"
        self._model = None
        self._last_fit_date: Optional[date] = None
        self._universe = [
            "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
            "GOOGL", "AMZN", "META", "TSLA", "JPM",
        ]
        self._retrain_interval_days = 21

    def _load_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        path = self.data_root / f"{symbol}.parquet"
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            required = ["Open", "High", "Low", "Close", "Volume"]
            if not all(c in df.columns for c in required):
                return None
            return df[required]
        except Exception as e:
            logger.warning("Failed to load OHLCV for %s: %s", symbol, e)
            return None

    def _ensure_model_fitted(self) -> None:
        today = date.today()
        needs_retrain = (
            self._model is None
            or self._last_fit_date is None
            or (today - self._last_fit_date).days >= self._retrain_interval_days
        )
        if not needs_retrain:
            return

        df = self._load_ohlcv("SPY")
        if df is None or len(df) < 100:
            logger.warning("Insufficient SPY data for HMM fit")
            return

        try:
            from research.goldfish.hmm_regime_adaptive.model import HMMRegimeModel
        except ImportError as e:
            logger.warning("Cannot import HMMRegimeModel (hmmlearn?): %s", e)
            return

        try:
            self._model = HMMRegimeModel()
            self._model.fit(df)
            self._last_fit_date = today
        except Exception as e:
            logger.warning("HMM fit failed: %s", e)
            self._model = None

    def run_cycle(self) -> Dict[str, Any]:
        self._ensure_model_fitted()
        if self._model is None or not getattr(self._model, "is_fitted", False):
            return {"ready": False, "reason": "model_not_fitted"}

        book = self._book
        last_regime: Optional[str] = None

        for symbol in self._universe:
            df = self._load_ohlcv(symbol)
            if df is None or len(df) < 50:
                continue

            signal = self._model.get_signal(df)
            last_regime = signal.get("regime", last_regime)
            direction = signal.get("direction", "flat")
            size_pct = signal.get("size_pct", 0.0)
            price = float(df["Close"].iloc[-1])

            pos = book.positions.get(symbol)
            current_dir = pos.direction if pos else None

            if pos:
                book.update_mark(symbol, price)

            if direction in ("long", "short"):
                if current_dir is None:
                    book.open_position(symbol, direction, size_pct, price)
                elif current_dir != direction:
                    book.close_position(symbol, price)
                    book.open_position(symbol, direction, size_pct, price)
            elif direction == "flat" and current_dir is not None:
                book.close_position(symbol, price)

        book.record_balance_snapshot()
        return {
            "ready": True,
            "mode": "paper",
            "runner": "hmm_regime",
            "last_regime": last_regime,
            "equity": book.equity(),
            "trade_count": book.trade_count,
        }
