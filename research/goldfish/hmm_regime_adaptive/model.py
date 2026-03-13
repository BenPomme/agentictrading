"""
HMM Regime-Adaptive Trading Model
==================================
Instrument-agnostic strategy using Hidden Markov Models for market regime
detection and adaptive position sizing.

Family: hmm_regime_adaptive
Compatible with: goldfish research runner
"""

import logging
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class HMMRegimeModel:
    """Hidden Markov Model for market regime detection and trading signals."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or self._default_config()
        self.model = None
        self.state_labels = []
        self.is_fitted = False

    @staticmethod
    def _default_config() -> dict:
        genome_path = (
            Path(__file__).parent.parent.parent
            / "data"
            / "factory"
            / "families"
            / "hmm_regime_adaptive"
            / "genome.json"
        )
        if genome_path.exists():
            with open(genome_path) as f:
                genome = json.load(f)
            return genome.get("hyperparameters", {})
        return {
            "n_hidden_states": 3,
            "covariance_type": "full",
            "n_iter": 100,
            "features": ["log_returns", "vol_ratio_20_60", "volume_zscore"],
            "lookback_days": 252,
            "retrain_frequency_days": 21,
            "min_samples_per_state": 20,
        }

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build feature matrix from OHLCV DataFrame.

        Expects columns: Open, High, Low, Close, Volume (standard Yahoo/Alpaca format).
        """
        features = pd.DataFrame(index=df.index)

        if "log_returns" in self.config.get("features", []):
            features["log_returns"] = np.log(df["Close"] / df["Close"].shift(1))

        if "vol_ratio_20_60" in self.config.get("features", []):
            vol_20 = df["Close"].pct_change().rolling(20).std()
            vol_60 = df["Close"].pct_change().rolling(60).std()
            features["vol_ratio_20_60"] = vol_20 / vol_60.replace(0, np.nan)

        if "vol_ratio_5_20" in self.config.get("features", []):
            vol_5 = df["Close"].pct_change().rolling(5).std()
            vol_20 = df["Close"].pct_change().rolling(20).std()
            features["vol_ratio_5_20"] = vol_5 / vol_20.replace(0, np.nan)

        if "volume_zscore" in self.config.get("features", []):
            vol_mean = df["Volume"].rolling(60).mean()
            vol_std = df["Volume"].rolling(60).std()
            features["volume_zscore"] = (
                (df["Volume"] - vol_mean) / vol_std.replace(0, np.nan)
            )

        if "vix_level" in self.config.get("features", []) and "VIX" in df.columns:
            features["vix_level"] = df["VIX"]

        if "rsi_14" in self.config.get("features", []):
            delta = df["Close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            features["rsi_14"] = 100 - (100 / (1 + rs))

        return features.dropna()

    def fit(self, df: pd.DataFrame) -> "HMMRegimeModel":
        """Fit the HMM on historical OHLCV data."""
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            raise ImportError("hmmlearn is required: pip install hmmlearn")

        features = self.build_features(df)
        n_states = self.config.get("n_hidden_states", 3)
        min_per_state = self.config.get("min_samples_per_state", 20)

        if len(features) < min_per_state * n_states:
            raise ValueError(
                f"Insufficient data: {len(features)} rows, "
                f"need at least {min_per_state * n_states}"
            )

        X = features.values
        self.feature_names = list(features.columns)

        self.model = GaussianHMM(
            n_components=n_states,
            covariance_type=self.config.get("covariance_type", "full"),
            n_iter=self.config.get("n_iter", 100),
            random_state=42,
        )
        self.model.fit(X)

        # Order states by mean return (first feature assumed to be log_returns)
        means = self.model.means_[:, 0]
        order = np.argsort(means)

        if n_states == 2:
            self.state_labels = ["bear", "bull"]
        elif n_states == 3:
            self.state_labels = ["bear", "sideways", "bull"]
        elif n_states == 4:
            self.state_labels = ["bear", "turbulent", "sideways", "bull"]
        else:
            self.state_labels = [f"state_{i}" for i in range(n_states)]

        self.state_order = order
        self.is_fitted = True
        logger.info(
            "HMM fitted: %d states, %d samples, features=%s",
            n_states,
            len(X),
            self.feature_names,
        )
        return self

    def predict_regime(self, df: pd.DataFrame) -> str:
        """Predict current market regime from recent data."""
        if not self.is_fitted:
            raise RuntimeError("Model not fitted. Call fit() first.")

        features = self.build_features(df)
        if len(features) == 0:
            return "unknown"

        X = features.values
        hidden_states = self.model.predict(X)
        current_raw_state = hidden_states[-1]
        ordered_idx = list(self.state_order).index(current_raw_state)
        return self.state_labels[ordered_idx]

    def get_signal(
        self, df: pd.DataFrame, position_sizing: Optional[dict] = None
    ) -> dict:
        """Generate trading signal based on current regime.

        Returns:
            dict with keys: regime, direction, size_pct, confidence
        """
        regime = self.predict_regime(df)

        if position_sizing is None:
            genome_path = (
                Path(__file__).parent.parent.parent
                / "data"
                / "factory"
                / "families"
                / "hmm_regime_adaptive"
                / "genome.json"
            )
            if genome_path.exists():
                with open(genome_path) as f:
                    position_sizing = json.load(f).get("position_sizing", {})
            else:
                position_sizing = {
                    "bull": {"direction": "long", "size_pct": 1.0},
                    "bear": {"direction": "flat", "size_pct": 0.0},
                    "sideways": {"direction": "long", "size_pct": 0.3},
                    "turbulent": {"direction": "flat", "size_pct": 0.0},
                }

        sizing = position_sizing.get(regime, {"direction": "flat", "size_pct": 0.0})

        features = self.build_features(df)
        if len(features) > 0:
            proba = self.model.predict_proba(features.values[-1:])
            confidence = float(np.max(proba))
        else:
            confidence = 0.0

        return {
            "regime": regime,
            "direction": sizing.get("direction", "flat"),
            "size_pct": sizing.get("size_pct", 0.0),
            "confidence": confidence,
        }

    def backtest(self, df: pd.DataFrame, train_frac: float = 0.7) -> dict:
        """Simple walk-forward backtest.

        Returns dict with equity curve and metrics.
        """
        split = int(len(df) * train_frac)
        train_df = df.iloc[:split]
        test_df = df.iloc[split:]

        self.fit(train_df)

        results = []
        lookback = self.config.get("lookback_days", 252)

        for i in range(lookback, len(test_df)):
            window = test_df.iloc[max(0, i - lookback) : i + 1]
            signal = self.get_signal(window)
            daily_return = float(
                test_df["Close"].iloc[i] / test_df["Close"].iloc[i - 1] - 1
            )

            position = (
                signal["size_pct"]
                if signal["direction"] == "long"
                else (
                    -signal["size_pct"]
                    if signal["direction"] == "short"
                    else 0.0
                )
            )
            pnl = position * daily_return

            results.append(
                {
                    "date": str(test_df.index[i]),
                    "regime": signal["regime"],
                    "direction": signal["direction"],
                    "size_pct": signal["size_pct"],
                    "daily_return": daily_return,
                    "strategy_return": pnl,
                    "confidence": signal["confidence"],
                }
            )

        if not results:
            return {"error": "no results", "n_test": len(test_df), "lookback": lookback}

        results_df = pd.DataFrame(results)
        cumulative = (1 + results_df["strategy_return"]).cumprod()
        buy_hold = (1 + results_df["daily_return"]).cumprod()

        total_return = (
            float(cumulative.iloc[-1] - 1) if len(cumulative) > 0 else 0
        )
        sharpe = (
            float(
                results_df["strategy_return"].mean()
                / results_df["strategy_return"].std()
                * np.sqrt(252)
            )
            if results_df["strategy_return"].std() > 0
            else 0
        )
        max_dd = float((cumulative / cumulative.cummax() - 1).min())

        return {
            "total_return": total_return,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "buy_hold_return": (
                float(buy_hold.iloc[-1] - 1) if len(buy_hold) > 0 else 0
            ),
            "n_trades": len(results),
            "regime_distribution": results_df["regime"].value_counts().to_dict(),
            "avg_confidence": float(results_df["confidence"].mean()),
        }


if __name__ == "__main__":
    np.random.seed(42)
    dates = pd.date_range("2021-01-01", periods=1260, freq="B")
    price = 100 * np.exp(
        np.cumsum(np.random.normal(0.0003, 0.015, len(dates)))
    )
    demo_df = pd.DataFrame(
        {
            "Open": price * (1 + np.random.normal(0, 0.005, len(dates))),
            "High": price * (1 + abs(np.random.normal(0, 0.01, len(dates)))),
            "Low": price * (1 - abs(np.random.normal(0, 0.01, len(dates)))),
            "Close": price,
            "Volume": np.random.lognormal(15, 1, len(dates)),
        },
        index=dates,
    )

    model = HMMRegimeModel()
    results = model.backtest(demo_df)
    print(json.dumps(results, indent=2))
