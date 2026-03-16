"""Paper trading runner that loads any model implementing StrategyModel from model_code.py."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from factory.data_loader import cycle_interval_for_source, load_data_for_requirements
from factory.local_runner_base import LocalPortfolioRunner
from factory.model_sandbox import load_model_from_code
from factory.paper_book import PaperTradeBook

logger = logging.getLogger(__name__)


def _resolve_price(df: pd.DataFrame) -> float:
    """Resolve latest price from data (close, Close, markPrice, price)."""
    for col in ("close", "Close", "markPrice", "price"):
        if col in df.columns:
            vals = df[col].dropna()
            if len(vals) > 0:
                return float(vals.iloc[-1])
    return 0.0


def _size_pct_from_model(model: Any, signal: int, equity: float) -> float:
    """Convert model.position_size result to a fraction 0..1 for open_position."""
    try:
        raw = float(model.position_size(signal, equity))
    except (AttributeError, TypeError, ValueError):
        return 0.1
    if raw <= 0:
        return 0.0
    if equity <= 0:
        return 0.01
    # If raw > 1, assume notional; else assume fraction
    if raw > 1:
        return min(1.0, raw / equity)
    return min(1.0, max(0.0, raw))


class DynamicModelRunner(LocalPortfolioRunner):
    """Paper runner that loads any StrategyModel from model_code.py, no hardcoded runner types."""

    def __init__(
        self,
        portfolio_id: str,
        *,
        model_code_path: str,
        class_name: str,
        genome_params: dict | None = None,
        runtime_data_source: str | None = None,
    ) -> None:
        super().__init__(portfolio_id)
        self._model_code_path = model_code_path
        self._class_name = class_name
        self._genome_params = genome_params or {}
        self._runtime_data_source = runtime_data_source
        self._book = PaperTradeBook(
            portfolio_dir=self.portfolio_dir,
            initial_balance=10_000.0,
        )
        self._model: Any = None
        self._last_fit_date: date | None = None
        self._retrain_interval_days = 21
        import config as _cfg
        self._project_root = Path(_cfg.__file__).resolve().parent
        self._data: pd.DataFrame | None = None
        self._data_req: dict | None = None
        # Set after first load based on source
        self.requires_market_open = False

    def _ensure_model_loaded(self) -> None:
        """Load model, configure, fit, and set market-open flag if needed."""
        today = date.today()
        needs_load = self._model is None
        needs_retrain = (
            self._last_fit_date is None
            or (today - self._last_fit_date).days >= self._retrain_interval_days
        )
        if not needs_load and not needs_retrain:
            return

        try:
            self._model = load_model_from_code(self._model_code_path, self._class_name)
            self._model.configure(self._genome_params or {})

            self._data_req = self._model.required_data()
            if self._runtime_data_source:
                self._data_req = dict(self._data_req)
                self._data_req["source"] = self._runtime_data_source
            self._data = load_data_for_requirements(self._data_req, self._project_root)

            if self._data is None or len(self._data) == 0:
                logger.warning("No data loaded for model %s", self._class_name)
                self._model = None
                return

            self._model.fit(self._data)
            self._last_fit_date = today

            source = (self._data_req.get("source") or "").strip().lower()
            if source in ("yahoo", "alpaca"):
                self.requires_market_open = True
        except Exception as e:
            logger.warning("Failed to load/fit model %s: %s", self._class_name, e, exc_info=True)
            self._model = None

    def run_cycle(self) -> Dict[str, Any]:
        """Run one cycle: ensure model, reload data, predict, manage positions."""
        try:
            self._ensure_model_loaded()
        except Exception as e:
            logger.warning("_ensure_model_loaded failed: %s", e)
            return {"ready": False, "reason": "model_not_loaded", "error": str(e)}

        if self._model is None:
            return {"ready": False, "reason": "model_not_loaded"}

        book = self._book

        try:
            # Reload latest data (if OHLCV, re-read parquets)
            self._data = load_data_for_requirements(
                self._data_req or self._model.required_data(),
                self._project_root,
            )
        except Exception as e:
            logger.warning("Failed to reload data: %s", e)
            if self._data is None or len(self._data) == 0:
                return {"ready": False, "reason": "data_load_failed", "error": str(e)}

        try:
            signals = self._model.predict(self._data)
        except Exception as e:
            logger.warning("Model predict failed: %s", e)
            return {"ready": False, "reason": "predict_failed", "error": str(e)}

        # Handle NaN in signals
        signals = signals.fillna(0)

        symbols_to_process: list[tuple[str, pd.DataFrame, Any]] = []

        if "symbol" in self._data.columns:
            for symbol, grp in self._data.groupby("symbol"):
                try:
                    grp_clean = grp.copy()
                    grp_clean.index = pd.RangeIndex(len(grp_clean))
                    sym_signals = self._model.predict(grp_clean)
                    last_signal_val = sym_signals.iloc[-1] if len(sym_signals) > 0 else 0
                    sig = int(last_signal_val) if not pd.isna(last_signal_val) else 0
                    if sig not in (-1, 0, 1):
                        sig = 0
                    price = _resolve_price(grp)
                    if price <= 0:
                        continue
                    symbols_to_process.append((str(symbol), grp, (sig, price)))
                except Exception as e:
                    logger.warning("Skipping symbol %s: %s", symbol, e)
        else:
            # Single-instrument: use default symbol from data_req
            default_symbol = "UNKNOWN"
            instruments = self._data_req.get("instruments") if self._data_req else []
            if instruments:
                default_symbol = str(instruments[0])
            last_idx = self._data.index[-1]
            last_signal_val = signals.loc[last_idx] if last_idx in signals.index else 0
            sig = int(last_signal_val) if not pd.isna(last_signal_val) else 0
            if sig not in (-1, 0, 1):
                sig = 0
            price = _resolve_price(self._data)
            if price > 0:
                symbols_to_process.append((default_symbol, self._data, (sig, price)))

        for symbol, _grp, (sig, price) in symbols_to_process:
            try:
                pos = book.positions.get(symbol)
                current_dir = pos.direction if pos else None

                if pos:
                    book.update_mark(symbol, price)

                if sig == 0:
                    if current_dir is not None:
                        book.close_position(symbol, price)
                elif sig in (1, -1):
                    direction = "long" if sig == 1 else "short"
                    size_pct = _size_pct_from_model(self._model, sig, book.equity())
                    if current_dir is None:
                        book.open_position(symbol, direction, size_pct, price)
                    elif current_dir != direction:
                        book.close_position(symbol, price)
                        book.open_position(symbol, direction, size_pct, price)
            except Exception as e:
                logger.warning("Position management failed for %s: %s", symbol, e)

        try:
            book.record_balance_snapshot()
        except Exception as e:
            logger.warning("record_balance_snapshot failed: %s", e)

        return {
            "ready": True,
            "mode": "paper",
            "runner": "dynamic",
            "model": self._class_name,
            "equity": book.equity(),
            "trade_count": book.trade_count,
        }
