"""Fallback paper trading runner for families without a dedicated runner.

Uses the StrategyModel protocol (fit/predict returning +1/-1/0) loaded from
the lineage genome, or falls back to a trivial momentum signal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from factory.local_runner_base import LocalPortfolioRunner
from factory.paper_book import PaperTradeBook

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class _SimpleMomentumModel:
    """Minimal fallback: z-score of rolling returns."""

    def __init__(self, lookback: int = 20, entry_z: float = 1.5):
        self._lookback = lookback
        self._entry_z = entry_z

    def fit(self, train_data: pd.DataFrame) -> None:
        pass

    def predict(self, data: pd.DataFrame) -> pd.Series:
        close = data["Close"] if "Close" in data.columns else data["close"]
        returns = close.pct_change()
        roll_mean = returns.rolling(self._lookback).mean()
        roll_std = returns.rolling(self._lookback).std().replace(0, np.nan)
        z = (returns - roll_mean) / roll_std
        signals = pd.Series(0, index=data.index, dtype=int)
        signals[z < -self._entry_z] = 1   # mean-reversion long
        signals[z > self._entry_z] = -1   # mean-reversion short
        return signals


class GenericSignalRunner(LocalPortfolioRunner):
    """Paper runner that works with any family by using a simple signal model."""

    def __init__(self, portfolio_id: str) -> None:
        super().__init__(portfolio_id)
        self._book = PaperTradeBook(
            portfolio_dir=self.portfolio_dir,
            initial_balance=5_000.0,
        )
        self._model = _SimpleMomentumModel()
        self._universe = self._detect_universe()

    def _detect_universe(self) -> list[str]:
        """Try to determine a sensible ticker universe from available data."""
        yahoo_dir = _PROJECT_ROOT / "data" / "yahoo" / "ohlcv"
        if yahoo_dir.exists():
            parquets = sorted(yahoo_dir.glob("*.parquet"))[:10]
            return [p.stem for p in parquets]
        return ["SPY"]

    def _load_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        path = _PROJECT_ROOT / "data" / "yahoo" / "ohlcv" / f"{symbol}.parquet"
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as e:
            logger.warning("Failed to load %s: %s", symbol, e)
            return None

    def run_cycle(self) -> Dict[str, Any]:
        book = self._book
        trades_this_cycle = 0

        for symbol in self._universe:
            df = self._load_ohlcv(symbol)
            if df is None or len(df) < 30:
                continue

            close_col = "Close" if "Close" in df.columns else "close"
            price = float(df[close_col].iloc[-1])
            signals = self._model.predict(df)
            current_signal = int(signals.iloc[-1])

            pos = book.positions.get(symbol)
            current_dir = pos.direction if pos else None

            if pos:
                book.update_mark(symbol, price)

            if current_signal == 1:
                if current_dir is None:
                    book.open_position(symbol, "long", 0.1, price)
                    trades_this_cycle += 1
                elif current_dir == "short":
                    book.close_position(symbol, price)
                    book.open_position(symbol, "long", 0.1, price)
                    trades_this_cycle += 2
            elif current_signal == -1:
                if current_dir is None:
                    book.open_position(symbol, "short", 0.1, price)
                    trades_this_cycle += 1
                elif current_dir == "long":
                    book.close_position(symbol, price)
                    book.open_position(symbol, "short", 0.1, price)
                    trades_this_cycle += 2
            elif current_signal == 0 and current_dir is not None:
                book.close_position(symbol, price)
                trades_this_cycle += 1

        book.record_balance_snapshot()
        return {
            "ready": True,
            "mode": "paper",
            "runner": "generic_signal",
            "equity": book.equity(),
            "trade_count": book.trade_count,
            "trades_this_cycle": trades_this_cycle,
        }
