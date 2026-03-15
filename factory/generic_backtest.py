"""Universal backtest harness for models implementing the StrategyModel protocol."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from factory.data_loader import load_data_for_requirements
from factory.model_sandbox import load_model_from_code

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BacktestResult:
    """Result of a generic backtest run."""

    monthly_roi_pct: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    net_pnl: float
    initial_capital: float
    final_equity: float
    failure_rate: float
    capacity_score: float
    regime_robustness: float
    slippage_headroom_pct: float
    calibration_lift_abs: float
    duration_days: int
    model_name: str
    data_source: str
    instruments: list[str]

    @property
    def trade_count(self) -> int:
        """Alias for total_trades for compatibility."""
        return self.total_trades


def _resolve_price_series(df: pd.DataFrame) -> pd.Series:
    """Resolve price column from data."""
    # Handle MultiIndex columns (e.g. from multi-instrument yahoo data)
    if isinstance(df.columns, pd.MultiIndex):
        for field in ("Close", "close", "Adj Close"):
            matches = [
                sym
                for sym in df.columns.get_level_values(0).unique()
                if (sym, field) in df.columns
            ]
            if matches:
                s = pd.to_numeric(df[(matches[0], field)], errors="coerce").ffill().fillna(1.0)
                return pd.Series(s.values, index=df.index)

    for col in ("close", "Close", "markPrice", "mark_price", "price", "midpoint"):
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce").ffill().bfill()
            return pd.Series(s.values, index=df.index)
    raise ValueError(
        "No price column found in data. Expected one of: close, Close, markPrice, mark_price, price"
    )


def _run_simulation(
    test_data: pd.DataFrame,
    signals: pd.Series,
    model: object,
    initial_capital: float,
) -> tuple[list[float], list[dict], float]:
    """Simulate trading, return (equity_curve, trades, net_pnl)."""
    price = _resolve_price_series(test_data)
    equity = initial_capital
    position: float = 0.0  # notional (signed: + long, - short)
    entry_price: float = 0.0
    entry_equity: float = 0.0
    prev_signal: int | None = None
    equity_curve: list[float] = []
    trades: list[dict] = []

    for i in range(len(test_data)):
        try:
            sig_val = signals.iloc[i]
            sig = int(sig_val) if not pd.isna(sig_val) else 0
            if sig not in (-1, 0, 1):
                sig = 0
        except (IndexError, ValueError, TypeError):
            sig = 0

        p = float(price.iloc[i]) if not pd.isna(price.iloc[i]) else 0.0
        if p <= 0:
            p = entry_price if entry_price > 0 else 1.0

        # Close position if we have one AND (signal is 0 or signal changed)
        if position != 0 and (sig == 0 or sig != prev_signal):
            ret = (p - entry_price) / entry_price if entry_price > 0 else 0.0
            if prev_signal == -1:
                ret = -ret
            pnl = position * ret
            equity += pnl
            is_fail = (entry_equity > 0 and pnl < -0.02 * entry_equity)
            trades.append({
                "entry_equity": entry_equity,
                "pnl": pnl,
                "is_failure": is_fail,
                "is_win": pnl > 0,
            })
            position = 0.0
            entry_price = 0.0

        # Open new position if signal != 0
        if sig != 0:
            try:
                size = float(model.position_size(sig, equity))
            except (AttributeError, TypeError, ValueError):
                size = 0.0
            if size > 0:
                position = size * sig
                entry_price = p
                entry_equity = equity

        prev_signal = sig
        equity_curve.append(equity)

    # Close any open position at end
    if position != 0 and len(price) > 0 and entry_price > 0 and prev_signal is not None:
        p = float(price.iloc[-1])
        ret = (p - entry_price) / entry_price
        if prev_signal == -1:
            ret = -ret
        pnl = position * ret
        equity += pnl
        is_fail = (entry_equity > 0 and pnl < -0.02 * entry_equity)
        trades.append({
            "entry_equity": entry_equity,
            "pnl": pnl,
            "is_failure": is_fail,
            "is_win": pnl > 0,
        })
        equity_curve[-1] = equity

    net_pnl = equity - initial_capital
    return equity_curve, trades, net_pnl


def run_generic_backtest(
    model_code_path: str | Path,
    class_name: str,
    genome_params: dict,
    project_root: Path,
    *,
    train_frac: float = 0.7,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Run a generic backtest for any model implementing StrategyModel protocol."""
    path = Path(model_code_path)
    project_root = Path(project_root)

    # a. Load model
    model = load_model_from_code(path, class_name)

    # b. Configure
    model.configure(genome_params)

    # c. Get data requirements
    data_req = model.required_data()
    data_source = (data_req.get("source") or "unknown").strip().lower()
    instruments = list(data_req.get("instruments") or [])

    # d. Load data
    df = load_data_for_requirements(data_req, project_root)

    # For multi-symbol flat DataFrames, simulate on the first instrument only
    # to avoid price-jumping artifacts across symbols with different price ranges.
    if "symbol" in df.columns and instruments:
        primary = instruments[0]
        single = df[df["symbol"] == primary]
        if len(single) >= 10:
            df = single.copy()

    # e. Split train/test
    n = len(df)
    if n < 10:
        raise ValueError(f"Insufficient data: {n} rows")
    split_idx = int(n * train_frac)
    if split_idx < 5:
        split_idx = 5
    if n - split_idx < 5:
        split_idx = n - 5
    train_data = df.iloc[:split_idx]
    test_data = df.iloc[split_idx:].copy()

    # f. Fit
    model.fit(train_data)

    # g. Predict
    signals = model.predict(test_data)
    if not isinstance(signals, pd.Series):
        signals = pd.Series(signals, index=test_data.index)
    # Align signals to test_data index by position (models may reset their index)
    if len(signals) == len(test_data) and not signals.index.equals(test_data.index):
        signals = pd.Series(signals.values, index=test_data.index)
    else:
        signals = signals.reindex(test_data.index, fill_value=0)
    signals = signals.ffill().bfill().fillna(0)

    # Drop rows where price is NaN so simulation doesn't use degenerate fill values
    price_raw = _resolve_price_series(test_data)
    valid_price = price_raw.notna() & (price_raw > 0)
    if not valid_price.all():
        test_data = test_data.loc[valid_price]
        signals = signals.loc[valid_price]
        price_raw = price_raw.loc[valid_price]

    # h. Simulate
    equity_curve, trades, net_pnl = _run_simulation(
        test_data, signals, model, initial_capital
    )
    final_equity = equity_curve[-1] if equity_curve else initial_capital

    # i. Compute metrics
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get("is_win", False))
    failures = sum(1 for t in trades if t.get("is_failure", False))
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    failure_rate = failures / total_trades if total_trades > 0 else 0.0

    total_return_pct = (
        (final_equity - initial_capital) / initial_capital * 100.0
        if initial_capital > 0
        else 0.0
    )

    eq = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1)
    max_drawdown_pct = float(np.max(dd) * 100.0) if len(dd) > 0 else 0.0

    # Duration in days
    idx = test_data.index
    if hasattr(idx, "min") and hasattr(idx, "max"):
        try:
            delta = pd.Timestamp(idx.max()) - pd.Timestamp(idx.min())
            duration_days = max(1, delta.days)
        except Exception:
            duration_days = max(1, len(test_data))
    else:
        duration_days = max(1, len(test_data))

    # Monthly ROI
    monthly_roi_pct = (total_return_pct / max(1, duration_days)) * 30.0

    # Sharpe (annualized)
    if len(eq) >= 2:
        daily_ret = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
        std_ret = np.std(daily_ret)
        if std_ret > 1e-12:
            sharpe_ratio = float(np.mean(daily_ret) / std_ret * np.sqrt(252))
        else:
            sharpe_ratio = 0.0
    else:
        sharpe_ratio = 0.0

    # Regime robustness (positive-return months / total months)
    try:
        test_df = pd.DataFrame({"equity": equity_curve}, index=test_data.index)
        test_df = test_df[~test_df.index.duplicated(keep="first")]
        monthly = test_df.resample("ME").agg({"equity": "last"})
        monthly["ret"] = monthly["equity"].pct_change()
        pos_months = (monthly["ret"] > 0).sum()
        tot_months = max(1, len(monthly) - 1)
        regime_robustness = float(pos_months / tot_months)
    except Exception:
        regime_robustness = 0.0 if total_return_pct <= 0 else 1.0

    # Capacity score
    capacity_score = min(1.0, total_trades / 50.0) if total_trades > 0 else 0.0

    # Slippage headroom
    est_slippage_monthly = (
        total_trades * 0.001 * 100.0 / max(1, duration_days) * 30.0
    )
    slippage_headroom_pct = monthly_roi_pct - est_slippage_monthly

    model_name = model.name() if hasattr(model, "name") else path.stem

    return BacktestResult(
        monthly_roi_pct=monthly_roi_pct,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        total_trades=total_trades,
        net_pnl=net_pnl,
        initial_capital=initial_capital,
        final_equity=final_equity,
        failure_rate=failure_rate,
        capacity_score=capacity_score,
        regime_robustness=regime_robustness,
        slippage_headroom_pct=slippage_headroom_pct,
        calibration_lift_abs=0.0,
        duration_days=duration_days,
        model_name=model_name,
        data_source=data_source,
        instruments=instruments,
    )


def backtest_result_to_evaluation_bundle(
    result: BacktestResult,
    lineage_id: str,
    family_id: str,
    stage: str,
) -> dict:
    """Convert BacktestResult to a dict matching EvaluationBundle schema."""
    from factory.contracts import utc_now_iso

    return {
        "evaluation_id": f"{lineage_id}:{stage}:generic_backtest",
        "lineage_id": lineage_id,
        "family_id": family_id,
        "stage": stage,
        "source": result.data_source,
        "generated_at": utc_now_iso(),
        "windows": [],
        "monthly_roi_pct": result.monthly_roi_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "slippage_headroom_pct": result.slippage_headroom_pct,
        "calibration_lift_abs": result.calibration_lift_abs,
        "failure_rate": result.failure_rate,
        "capacity_score": result.capacity_score,
        "regime_robustness": result.regime_robustness,
        "trade_count": result.trade_count,
        "net_pnl": result.net_pnl,
        "stress_positive": result.total_return_pct > 0,
        "baseline_beaten_windows": 1 if result.monthly_roi_pct > 0 else 0,
        "turnover": 0.0,
        "settled_count": result.trade_count,
        "paper_days": result.duration_days,
        "hard_vetoes": [],
        "notes": [f"model={result.model_name}", f"instruments={result.instruments}"],
    }
