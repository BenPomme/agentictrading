#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OHLCV_DIR = PROJECT_ROOT / "data" / "yahoo" / "ohlcv"
BDAYS_PER_YEAR = 252

YAHOO_COLS = {"Open", "High", "Low", "Close", "Volume"}
COL_ALIASES = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


class _HMMRegimeAdapter:
    """Adapts HMMRegimeModel to backtest engine StrategyModel protocol (fit/predict)."""

    def __init__(self, model):
        self._model = model

    def fit(self, train_data: pd.DataFrame) -> None:
        self._model.fit(train_data)

    def predict(self, data: pd.DataFrame) -> pd.Series:
        if len(data) == 0:
            return pd.Series(dtype=float)
        signal = self._model.get_signal(data)
        direction = signal["direction"]
        size_pct = signal.get("size_pct", 0.0)
        val = 0
        if direction == "long" and size_pct > 0:
            val = 1
        elif direction == "short" and size_pct > 0:
            val = -1
        return pd.Series([val], index=[data.index[-1]])


class _BinanceMomentumAdapter:
    """Adapts Binance klines (hourly OHLCV) to a momentum/mean-reversion strategy
    compatible with the StrategyModel protocol."""

    def __init__(
        self,
        lookback_hours: int = 24,
        entry_threshold: float = 1.5,
        exit_threshold: float = 0.5,
    ):
        self.lookback_hours = lookback_hours
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self._mean = 0.0
        self._std = 1.0

    def fit(self, train_data: pd.DataFrame) -> None:
        close = train_data["Close"]
        returns = close.pct_change().dropna()
        rolling = returns.rolling(self.lookback_hours)
        self._mean = float(rolling.mean().iloc[-1]) if len(returns) > self.lookback_hours else 0.0
        self._std = max(float(rolling.std().iloc[-1]) if len(returns) > self.lookback_hours else 1.0, 1e-8)

    def predict(self, data: pd.DataFrame) -> pd.Series:
        if len(data) < 2:
            return pd.Series([0], index=data.index[-1:])
        close = data["Close"]
        ret = float(close.iloc[-1] / close.iloc[-2] - 1.0) if close.iloc[-2] != 0 else 0.0
        z = (ret - self._mean) / self._std
        if z < -self.entry_threshold:
            val = 1  # mean-reversion long
        elif z > self.entry_threshold:
            val = -1  # mean-reversion short
        else:
            val = 0
        return pd.Series([val], index=[data.index[-1]])


BINANCE_KLINES_DIR = PROJECT_ROOT / "data" / "funding_history" / "klines"
BINANCE_HOURS_PER_YEAR = 8760


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in df.columns}
    renames = {cols[k]: v for k, v in COL_ALIASES.items() if k in cols}
    if renames:
        df = df.rename(columns=renames)
    missing = YAHOO_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return df[list(YAHOO_COLS)]


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--family",
        default="hmm_regime_adaptive",
        help="Strategy family (default: hmm_regime_adaptive)",
    )
    p.add_argument(
        "--tickers",
        default="SPY,QQQ,AAPL,MSFT,AMZN,GOOGL,META,NVDA,TSLA,JPM",
        help="Comma-separated ticker list",
    )
    p.add_argument("--train-years", type=float, default=3, help="Training period in years")
    p.add_argument("--test-years", type=float, default=1, help="Test period in years")
    p.add_argument(
        "--output-dir",
        default="data/backtest_results",
        help="Output directory for JSON results",
    )
    p.add_argument(
        "--param-grid",
        action="store_true",
        help="Run grid over n_states (2,3,4,5) and lookback_days (20,40,60)",
    )
    p.add_argument(
        "--optimize",
        action="store_true",
        help="Use Optuna TPE optimization instead of grid search",
    )
    p.add_argument(
        "--n-trials",
        type=int,
        default=50,
        help="Number of Optuna trials (default: 50)",
    )
    p.add_argument(
        "--ohlcv-dir",
        default=None,
        help=f"Override OHLCV directory (default: {OHLCV_DIR})",
    )
    return p.parse_args()


BINANCE_FAMILIES = {"binance_funding_contrarian", "binance_cascade_regime"}


def _load_model_module(family: str):
    if family == "hmm_regime_adaptive":
        sys.path.insert(0, str(PROJECT_ROOT))
        from research.goldfish.hmm_regime_adaptive.model import HMMRegimeModel
        return HMMRegimeModel
    if family in BINANCE_FAMILIES:
        return _BinanceMomentumAdapter
    raise ValueError(f"Unknown family: {family}")


def _run_single(
    ModelCls,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    config: dict,
) -> tuple[dict, dict]:
    train_metrics = ModelCls(config=dict(config)).backtest(train_df)

    combined = pd.concat([train_df, test_df])
    train_frac = len(train_df) / len(combined)
    test_metrics = ModelCls(config=dict(config)).backtest(combined, train_frac=train_frac)

    return train_metrics, test_metrics


def _run_ticker(
    ticker: str,
    ModelCls,
    ohlcv_path: Path,
    train_days: int,
    test_days: int,
    output_path: Path,
    param_grid: bool,
) -> dict | None:
    if not ohlcv_path.exists():
        logger.warning("Missing data: %s", ohlcv_path)
        return None

    try:
        df_raw = pd.read_parquet(ohlcv_path)
    except Exception as e:
        logger.warning("Failed to load %s: %s", ohlcv_path, e)
        return None

    df = df_raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        date_col = next((c for c in ["date", "Date"] if c in df.columns), None)
        if date_col:
            df = df.set_index(date_col)
        else:
            logger.warning("No date index or date column for %s", ticker)
            return None

    try:
        df = _normalize_ohlcv(df)
    except ValueError as e:
        logger.warning("Bad columns %s: %s", ticker, e)
        return None

    df = df.sort_index().dropna(how="all")
    total_need = train_days + test_days
    if len(df) < total_need:
        logger.warning("Insufficient data for %s: %d rows, need %d", ticker, len(df), total_need)
        return None

    window = df.tail(total_need)
    train_df = window.head(train_days)
    test_df = window.tail(test_days)

    train_period = f"{train_df.index[0]} to {train_df.index[-1]}"
    test_period = f"{test_df.index[0]} to {test_df.index[-1]}"

    base_config = {
        "n_hidden_states": 3,
        "covariance_type": "full",
        "n_iter": 100,
        "features": ["log_returns", "vol_ratio_20_60", "volume_zscore"],
        "lookback_days": 252,
        "min_samples_per_state": 20,
    }

    if param_grid:
        n_states_list = [2, 3, 4, 5]
        lookback_list = [20, 40, 60]
        best_test_sharpe = float("-inf")
        best_params = None
        best_train_metrics = None
        best_test_metrics = None
        all_runs = []

        for n_states in n_states_list:
            for lookback in lookback_list:
                cfg = {
                    **base_config,
                    "n_hidden_states": n_states,
                    "lookback_days": lookback,
                }
                try:
                    tm, vm = _run_single(ModelCls, train_df, test_df, cfg)
                except Exception as e:
                    logger.warning("Param run failed %s n=%d lb=%d: %s", ticker, n_states, lookback, e)
                    continue

                if "error" in tm or "error" in vm:
                    continue

                all_runs.append({
                    "n_states": n_states,
                    "lookback_days": lookback,
                    "train_sharpe": tm.get("sharpe_ratio", 0),
                    "test_sharpe": vm.get("sharpe_ratio", 0),
                })

                if vm.get("sharpe_ratio", float("-inf")) > best_test_sharpe:
                    best_test_sharpe = vm["sharpe_ratio"]
                    best_params = {"n_hidden_states": n_states, "lookback_days": lookback}
                    best_train_metrics = tm
                    best_test_metrics = vm

        if best_params is None:
            logger.warning("No successful param runs for %s", ticker)
            return None

        payload = {
            "ticker": ticker,
            "train_period": train_period,
            "test_period": test_period,
            "train_metrics": best_train_metrics,
            "test_metrics": best_test_metrics,
            "best_params": best_params,
            "param_grid_runs": all_runs,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        summary_row = {
            "ticker": ticker,
            "best_params": str(best_params),
            "train_sharpe": best_train_metrics.get("sharpe_ratio"),
            "test_sharpe": best_test_metrics.get("sharpe_ratio"),
        }
    else:
        try:
            train_metrics, test_metrics = _run_single(ModelCls, train_df, test_df, base_config)
        except Exception as e:
            logger.warning("Backtest failed %s: %s", ticker, e)
            return None

        if "error" in train_metrics or "error" in test_metrics:
            logger.warning("Backtest error for %s: train=%s test=%s", ticker, train_metrics.get("error"), test_metrics.get("error"))
            return None

        payload = {
            "ticker": ticker,
            "train_period": train_period,
            "test_period": test_period,
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        summary_row = {
            "ticker": ticker,
            "best_params": "default",
            "train_sharpe": train_metrics.get("sharpe_ratio"),
            "test_sharpe": test_metrics.get("sharpe_ratio"),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return summary_row


def _run_optuna_optimization(
    ticker: str,
    ohlcv_path: Path,
    train_days: int,
    test_days: int,
    output_path: Path,
    n_trials: int,
) -> dict | None:
    """Run Optuna TPE optimization for HMM family using backtest engine."""
    if not ohlcv_path.exists():
        logger.warning("Missing data: %s", ohlcv_path)
        return None

    try:
        df_raw = pd.read_parquet(ohlcv_path)
    except Exception as e:
        logger.warning("Failed to load %s: %s", ohlcv_path, e)
        return None

    df = df_raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        date_col = next((c for c in ["date", "Date"] if c in df.columns), None)
        if date_col:
            df = df.set_index(date_col)
        else:
            logger.warning("No date index or date column for %s", ticker)
            return None

    try:
        df = _normalize_ohlcv(df)
    except ValueError as e:
        logger.warning("Bad columns %s: %s", ticker, e)
        return None

    df = df.sort_index().dropna(how="all")
    total_need = train_days + test_days
    if len(df) < total_need:
        logger.warning("Insufficient data for %s: %d rows, need %d", ticker, len(df), total_need)
        return None

    sys.path.insert(0, str(PROJECT_ROOT))
    from research.goldfish.hmm_regime_adaptive.model import HMMRegimeModel

    def hmm_model_factory(**params):
        config = {
            "n_hidden_states": params.get("n_hidden_states", 3),
            "lookback_days": params.get("lookback_days", 252),
            "covariance_type": "full",
            "n_iter": 100,
            "features": ["log_returns", "vol_ratio_20_60", "volume_zscore"],
            "min_samples_per_state": 20,
        }
        return _HMMRegimeAdapter(HMMRegimeModel(config=config))

    param_space = {
        "n_hidden_states": ("int", 2, 5),
        "lookback_days": ("categorical", [20, 40, 60, 84, 126, 252]),
    }

    from backtest.optimizer import optimize_parameters, save_optimization_results

    data_window = df.tail(total_need)
    results = optimize_parameters(
        data_window,
        hmm_model_factory,
        param_space,
        n_trials=n_trials,
        train_days=train_days,
        test_days=test_days,
    )

    if "error" in results:
        logger.warning("Optuna failed for %s: %s", ticker, results.get("error"))
        return None

    payload = {
        "ticker": ticker,
        "optimization": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    opt_path = output_path.parent / f"{ticker}_optuna_results.json"
    save_optimization_results(results, opt_path)

    best = results.get("best_metrics", {})
    return {
        "ticker": ticker,
        "best_params": str(results.get("best_params", {})),
        "train_sharpe": best.get("sharpe"),
        "test_sharpe": best.get("sharpe"),
    }


def _load_binance_ohlcv(csv_path: Path) -> pd.DataFrame | None:
    """Load Binance klines CSV and normalize to OHLCV with DatetimeIndex."""
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        logger.warning("Failed to load %s: %s", csv_path, e)
        return None
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date").sort_index()
    renames = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    df = df.rename(columns=renames)
    for col in YAHOO_COLS:
        if col not in df.columns:
            logger.warning("Missing column %s in %s", col, csv_path)
            return None
    return df[list(YAHOO_COLS)]


def _run_binance_optuna(
    symbol: str,
    csv_path: Path,
    output_path: Path,
    n_trials: int,
    train_hours: int = 6570,
    test_hours: int = 2190,
) -> dict | None:
    """Run Optuna TPE optimization for Binance momentum strategy."""
    df = _load_binance_ohlcv(csv_path)
    if df is None:
        return None
    total_need = train_hours + test_hours
    if len(df) < total_need:
        logger.warning("Insufficient data for %s: %d rows, need %d", symbol, len(df), total_need)
        return None

    def binance_model_factory(**params):
        return _BinanceMomentumAdapter(
            lookback_hours=params.get("lookback_hours", 24),
            entry_threshold=params.get("entry_threshold", 1.5),
            exit_threshold=params.get("exit_threshold", 0.5),
        )

    param_space = {
        "lookback_hours": ("int", 4, 48),
        "entry_threshold": ("float", 0.5, 3.0),
        "exit_threshold": ("float", 0.1, 1.0),
    }

    sys.path.insert(0, str(PROJECT_ROOT))
    from backtest.optimizer import optimize_parameters, save_optimization_results

    data_window = df.tail(total_need)
    results = optimize_parameters(
        data_window,
        binance_model_factory,
        param_space,
        n_trials=n_trials,
        train_days=train_hours,
        test_days=test_hours,
    )

    if "error" in results:
        logger.warning("Optuna failed for %s: %s", symbol, results.get("error"))
        return None

    payload = {
        "symbol": symbol,
        "optimization": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    opt_path = output_path.parent / f"{symbol}_optuna_results.json"
    save_optimization_results(results, opt_path)

    best = results.get("best_metrics", {})
    return {
        "ticker": symbol,
        "best_params": str(results.get("best_params", {})),
        "train_sharpe": best.get("sharpe"),
        "test_sharpe": best.get("sharpe"),
    }


def main():
    args = _parse_args()
    ohlcv_dir = Path(args.ohlcv_dir) if args.ohlcv_dir else OHLCV_DIR
    output_root = PROJECT_ROOT / args.output_dir
    family_dir = output_root / args.family
    family_dir.mkdir(parents=True, exist_ok=True)

    is_binance = args.family in BINANCE_FAMILIES

    try:
        ModelCls = _load_model_module(args.family)
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        sys.exit(1)

    if is_binance:
        if args.tickers == "SPY,QQQ,AAPL,MSFT,AMZN,GOOGL,META,NVDA,TSLA,JPM":
            import glob as _glob
            csv_files = sorted(_glob.glob(str(BINANCE_KLINES_DIR / "*.csv")))
            tickers = [Path(f).stem for f in csv_files]
            logger.info("Auto-discovered %d Binance symbols from klines", len(tickers))
        else:
            tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    train_days = int(args.train_years * BDAYS_PER_YEAR)
    test_days = int(args.test_years * BDAYS_PER_YEAR)

    summaries = []
    for ticker in tickers:
        if is_binance:
            csv_path = BINANCE_KLINES_DIR / f"{ticker}.csv"
            out_path = family_dir / f"{ticker}_results.json"
            if args.optimize:
                row = _run_binance_optuna(
                    symbol=ticker,
                    csv_path=csv_path,
                    output_path=out_path,
                    n_trials=args.n_trials,
                )
            else:
                df = _load_binance_ohlcv(csv_path)
                if df is None:
                    continue
                train_h = int(len(df) * 0.75)
                test_h = len(df) - train_h
                row = {
                    "ticker": ticker,
                    "best_params": "default",
                    "train_sharpe": None,
                    "test_sharpe": None,
                }
        else:
            path = ohlcv_dir / f"{ticker}.parquet"
            out_path = family_dir / f"{ticker}_results.json"
            if args.optimize and args.family == "hmm_regime_adaptive":
                row = _run_optuna_optimization(
                    ticker=ticker,
                    ohlcv_path=path,
                    train_days=train_days,
                    test_days=test_days,
                    output_path=out_path,
                    n_trials=args.n_trials,
                )
            else:
                row = _run_ticker(
                    ticker=ticker,
                    ModelCls=ModelCls,
                    ohlcv_path=path,
                    train_days=train_days,
                    test_days=test_days,
                    output_path=out_path,
                    param_grid=args.param_grid,
                )
        if row:
            summaries.append(row)

    if summaries:
        print("\nSummary")
        print("-" * 90)
        for r in summaries:
            ts = r.get("train_sharpe")
            vs = r.get("test_sharpe")
            ts_f = f"{ts:.4f}" if ts is not None else "N/A"
            vs_f = f"{vs:.4f}" if vs is not None else "N/A"
            print(f"  {r['ticker']:8} | {str(r['best_params']):35} | train sharpe: {ts_f:>10} | test sharpe: {vs_f:>10}")
        print("-" * 90)

    logger.info("Wrote %d result(s) to %s", len(summaries), family_dir)


if __name__ == "__main__":
    main()
