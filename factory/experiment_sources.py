from __future__ import annotations

import importlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd

import config


def _execution_repo_root() -> Path | None:
    raw = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    return root if root.exists() else None


def _external_module(module_name: str):
    root = _execution_repo_root()
    if root is None:
        return None
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _clip(value: float) -> float:
    return max(1e-6, min(1.0 - 1e-6, float(value)))


@dataclass
class PredictionExample:
    timestamp: str
    base_prob: float
    odds: float
    label: int
    features: Dict[str, float]


@dataclass
class PredictionMetrics:
    bets: int
    roi: float
    brier: float
    pnl_units: float


class MarketCalibratedModel:
    def __init__(self) -> None:
        self.offset = 0.0

    def fit(self, examples: List[PredictionExample], epochs: int = 1, lr: float = 0.01) -> None:
        if not examples:
            self.offset = 0.0
            return
        self.offset = sum(float(item.label) - float(item.base_prob) for item in examples) / max(1, len(examples))
        self.offset *= min(1.0, max(0.1, float(lr) * max(1, epochs)))

    def predict_proba(self, base_prob: float) -> float:
        return _clip(float(base_prob) + self.offset)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "market_calibrated", "offset": round(self.offset, 6)}


class _LinearFeatureModel:
    kind = "linear_feature"

    def __init__(self, feature_names: Iterable[str]):
        self.feature_names = [str(item) for item in feature_names]
        self.weights: Dict[str, float] = {name: 0.0 for name in self.feature_names}
        self.bias = 0.0

    def fit(self, examples: List[PredictionExample], epochs: int = 1, lr: float = 0.01) -> None:
        if not examples:
            return
        scale = min(0.25, max(0.01, float(lr) * max(1, epochs)))
        self.bias = sum(float(item.label) - float(item.base_prob) for item in examples) / max(1, len(examples))
        for name in self.feature_names:
            values = [float(item.features.get(name, 0.0)) for item in examples]
            centered = sum(values) / max(1, len(values))
            signal = sum((value - centered) * (float(item.label) - float(item.base_prob)) for value, item in zip(values, examples))
            self.weights[name] = scale * signal / max(1, len(examples))

    def _feature_shift(self, features: Dict[str, float]) -> float:
        shift = self.bias
        for name, weight in self.weights.items():
            shift += float(weight) * float(features.get(name, 0.0))
        return math.tanh(shift) * 0.12

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "bias": round(self.bias, 6), "weights": self.weights}


class PureLogitModel(_LinearFeatureModel):
    kind = "pure_logit"

    def predict_proba(self, features: Dict[str, float]) -> float:
        return _clip(0.5 + self._feature_shift(features))


class HybridLogitModel(_LinearFeatureModel):
    kind = "hybrid_logit"

    def predict_proba(self, base_prob: float, features: Dict[str, float]) -> float:
        return _clip(float(base_prob) + self._feature_shift(features))


class ResidualLogitModel(_LinearFeatureModel):
    kind = "residual_logit"

    def predict_proba(self, base_prob: float, features: Dict[str, float]) -> float:
        return _clip(float(base_prob) + (self._feature_shift(features) * 0.75))


def evaluate_predictions(
    *,
    probs: List[float],
    labels: List[int],
    odds: List[float],
    edge_threshold: float,
    stake: float,
) -> PredictionMetrics:
    if not probs:
        return PredictionMetrics(bets=0, roi=0.0, brier=0.0, pnl_units=0.0)
    brier = sum((float(prob) - int(label)) ** 2 for prob, label in zip(probs, labels)) / max(1, len(probs))
    bets = 0
    total_stake = 0.0
    pnl_units = 0.0
    for prob, label, offered_odds in zip(probs, labels, odds):
        implied = 1.0 / max(1.000001, float(offered_odds))
        if float(prob) - implied < float(edge_threshold):
            continue
        bets += 1
        total_stake += float(stake)
        pnl_units += (float(offered_odds) - 1.0) * float(stake) if int(label) == 1 else -float(stake)
    roi = pnl_units / total_stake if total_stake else 0.0
    return PredictionMetrics(bets=bets, roi=roi, brier=brier, pnl_units=pnl_units)


def pooled_examples(prediction_root: str | Path) -> List[PredictionExample]:
    module = _external_module("strategy.prediction_bootstrap")
    if module is not None and hasattr(module, "pooled_examples"):
        try:
            return list(getattr(module, "pooled_examples")(str(prediction_root)))
        except Exception:
            pass
    root = Path(prediction_root)
    rows: List[PredictionExample] = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if not {"timestamp", "base_prob", "odds", "label"}.issubset(payload.keys()):
                continue
            rows.append(
                PredictionExample(
                    timestamp=str(payload.get("timestamp") or ""),
                    base_prob=float(payload.get("base_prob", 0.5) or 0.5),
                    odds=float(payload.get("odds", 2.0) or 2.0),
                    label=int(payload.get("label", 0) or 0),
                    features={str(k): float(v) for k, v in dict(payload.get("features") or {}).items()},
                )
            )
    rows.sort(key=lambda item: item.timestamp)
    return rows


def build_contrarian_features_all(symbols: List[str], data_dir: str | Path) -> pd.DataFrame:
    store_module = _external_module("data.research_store")
    if store_module is not None and hasattr(store_module, "maybe_query_curated_funding_contrarian"):
        try:
            curated = getattr(store_module, "maybe_query_curated_funding_contrarian")(symbols)
            if isinstance(curated, pd.DataFrame) and not curated.empty:
                curated = curated.copy()
                if "funding_time_dt" in curated.columns:
                    curated["funding_time_dt"] = pd.to_datetime(curated["funding_time_dt"], utc=True, errors="coerce")
                    curated = curated.set_index("funding_time_dt").sort_index()
                return curated
        except Exception:
            pass
    module = _external_module("funding.ml.contrarian_features")
    if module is not None and hasattr(module, "build_contrarian_features_all"):
        try:
            return getattr(module, "build_contrarian_features_all")(symbols, data_dir=data_dir)
        except Exception:
            pass
    rows: List[Dict[str, Any]] = []
    root = Path(data_dir)
    for symbol in symbols:
        path = root / f"{symbol}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payload["symbol"] = symbol
            rows.append(payload)
    return pd.DataFrame(rows)


def get_contrarian_feature_columns(df: pd.DataFrame) -> List[str]:
    module = _external_module("funding.ml.contrarian_features")
    if module is not None and hasattr(module, "get_contrarian_feature_columns"):
        try:
            return list(getattr(module, "get_contrarian_feature_columns")(df))
        except Exception:
            pass
    ignored = {"symbol", "timestamp", "ts", "close_time"}
    return [str(column) for column in df.columns if str(column) not in ignored]


def build_cascade_features(symbols: List[str], data_dir: str | Path) -> pd.DataFrame:
    store_module = _external_module("data.research_store")
    if store_module is not None and hasattr(store_module, "maybe_query_curated_cascade"):
        try:
            curated = getattr(store_module, "maybe_query_curated_cascade")(symbols)
            if isinstance(curated, pd.DataFrame) and not curated.empty:
                curated = curated.copy()
                if "timestamp_dt" in curated.columns:
                    curated["timestamp_dt"] = pd.to_datetime(curated["timestamp_dt"], utc=True, errors="coerce")
                    curated = curated.set_index("timestamp_dt").sort_index()
                return curated
        except Exception:
            pass
    module = _external_module("funding.ml.cascade_features")
    if module is not None and hasattr(module, "build_cascade_features"):
        try:
            return getattr(module, "build_cascade_features")(symbols, data_dir=data_dir)
        except Exception:
            pass
    rows: List[pd.DataFrame] = []
    root = Path(data_dir)
    for symbol in symbols:
        kline_path = root / "klines" / f"{symbol}.csv"
        if not kline_path.exists():
            continue
        try:
            df = pd.read_csv(kline_path)
        except Exception:
            continue
        if df.empty or "close" not in df.columns:
            continue
        close = pd.to_numeric(df["close"], errors="coerce").fillna(method="ffill").fillna(method="bfill")
        volume = pd.to_numeric(df.get("volume", 0.0), errors="coerce").fillna(0.0)
        returns = close.pct_change().fillna(0.0)
        frame = pd.DataFrame(
            {
                "symbol": symbol,
                "liq_count_1h": 0.0,
                "liq_volume_usd_1h": volume.rolling(3, min_periods=1).mean() * close * 0.02,
                "funding_extremity_max_abs": returns.abs().rolling(8, min_periods=1).max() * 10.0,
                "volume_surge_zscore": ((volume - volume.rolling(12, min_periods=2).mean()) / volume.rolling(12, min_periods=2).std().replace(0, np.nan)).fillna(0.0),
                "price_acceleration": returns.diff().fillna(0.0),
                "cross_asset_pc1_loading": returns.rolling(6, min_periods=1).mean().fillna(0.0),
                "leverage_proxy_oi_vol": ((close.abs() * 0.1) / volume.replace(0, np.nan)).fillna(0.0),
                "oi_concentration_hhi": 0.1,
                "future_return_4h": returns.shift(-4).rolling(4, min_periods=1).sum().fillna(0.0),
            }
        )
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def get_cascade_feature_columns(df: pd.DataFrame) -> List[str]:
    module = _external_module("funding.ml.cascade_features")
    if module is not None and hasattr(module, "get_cascade_feature_columns"):
        try:
            return list(getattr(module, "get_cascade_feature_columns")(df))
        except Exception:
            pass
    ignored = {"symbol", "future_return_4h"}
    return [str(column) for column in df.columns if str(column) not in ignored]


def label_cascade_events(df: pd.DataFrame) -> pd.Series:
    module = _external_module("funding.ml.cascade_features")
    if module is not None and hasattr(module, "label_cascade_events"):
        try:
            return getattr(module, "label_cascade_events")(df)
        except Exception:
            pass
    if df.empty:
        return pd.Series(dtype=int)
    if "future_return_4h" in df.columns:
        return (pd.to_numeric(df["future_return_4h"], errors="coerce").fillna(0.0) <= -0.05).astype(int)
    return pd.Series([0] * len(df), index=df.index, dtype=int)


class ContrarianBacktester:
    def __init__(self, initial_balance: float = 1000.0):
        self.initial_balance = float(initial_balance)

    def backtest(
        self,
        df: pd.DataFrame,
        *,
        strategy: str,
        model: Any,
        stop_loss_pct: float,
        take_profit_ratio: float,
        capital_pct: float,
        max_hold_periods: int,
        min_funding_rate: float,
    ) -> Dict[str, Any]:
        balance = float(self.initial_balance)
        peak = balance
        max_drawdown = 0.0
        wins = 0
        losses = 0
        timeouts = 0
        total_trades = 0
        total_pnl = 0.0
        funding_col = "funding_rate" if "funding_rate" in df.columns else None
        return_col = next((name for name in ["future_return", "forward_return", "price_return", "return"] if name in df.columns), None)
        for _, row in df.iterrows():
            funding_rate = float(row.get(funding_col, 0.0) or 0.0) if funding_col else 0.0
            if abs(funding_rate) < float(min_funding_rate):
                continue
            total_trades += 1
            notional = balance * float(capital_pct)
            directional_edge = -funding_rate * 8.0
            if return_col:
                directional_edge += float(row.get(return_col, 0.0) or 0.0) * 0.35
            pnl = notional * directional_edge
            total_pnl += pnl
            balance += pnl
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            else:
                timeouts += 1
        return {
            "final_balance": balance,
            "max_drawdown": max_drawdown,
            "losses": losses,
            "timeouts": timeouts,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "win_rate": wins / max(1, total_trades),
        }


def family_model_rankings(family_id: str) -> pd.DataFrame:
    store_module = _external_module("data.research_store")
    if store_module is not None and hasattr(store_module, "maybe_query_family_model_rankings"):
        try:
            rankings = getattr(store_module, "maybe_query_family_model_rankings")(family_id)
            if isinstance(rankings, pd.DataFrame):
                return rankings
        except Exception:
            pass
    return pd.DataFrame()


def portfolio_scorecards(portfolio_ids: Iterable[str]) -> pd.DataFrame:
    store_module = _external_module("data.research_store")
    if store_module is not None and hasattr(store_module, "maybe_query_portfolio_scorecards"):
        try:
            scorecards = getattr(store_module, "maybe_query_portfolio_scorecards")(list(portfolio_ids))
            if isinstance(scorecards, pd.DataFrame):
                return scorecards
        except Exception:
            pass
    return pd.DataFrame()
