from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from factory.local_runner_base import LocalPortfolioRunner
from factory.paper_book import PaperTradeBook

logger = logging.getLogger(__name__)

project_root = Path(__file__).resolve().parent.parent.parent


class FundingContrarianRunner(LocalPortfolioRunner):
    crypto_venue = True

    def __init__(self, portfolio_id: str) -> None:
        super().__init__(portfolio_id)
        self.book = PaperTradeBook(
            portfolio_dir=self.portfolio_dir,
            initial_balance=1_000.0,
        )
        self.data_root = project_root / "data" / "funding_history"
        self._min_funding_rate = 0.0005
        self._capital_pct = 0.10
        self._processed_timestamps: set = set()

    def _load_funding_rates(self) -> pd.DataFrame:
        rates_dir = self.data_root / "funding_rates"
        if not rates_dir.exists():
            return pd.DataFrame()

        frames = []
        for path in rates_dir.glob("*.csv"):
            try:
                df = pd.read_csv(path)
                frames.append(df)
            except Exception as e:
                logger.warning("Failed to read %s: %s", path, e)

        if not frames:
            return pd.DataFrame()

        out = pd.concat(frames, ignore_index=True)

        col_map = {
            "funding_rate": "fundingRate",
            "funding_time": "fundingTime",
            "mark_price": "markPrice",
        }
        out.rename(columns={k: v for k, v in col_map.items() if k in out.columns}, inplace=True)

        if "fundingTime" not in out.columns:
            return pd.DataFrame()
        out["fundingTime"] = pd.to_numeric(out["fundingTime"], errors="coerce")
        out["fundingRate"] = pd.to_numeric(out.get("fundingRate", 0), errors="coerce")
        out.dropna(subset=["fundingTime"], inplace=True)
        out = out.sort_values("fundingTime", ascending=False)
        return out

    def _get_latest_unprocessed(self, df: pd.DataFrame) -> List[dict]:
        if df.empty:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        mask = ~df["fundingTime"].isin(self._processed_timestamps) & (df["fundingTime"] >= cutoff_ms)
        filtered = df[mask]

        return [
            {
                "symbol": row.get("symbol", "UNKNOWN"),
                "fundingRate": float(row.get("fundingRate", 0)),
                "markPrice": row.get("markPrice", 0),
                "fundingTime": row["fundingTime"],
            }
            for _, row in filtered.iterrows()
        ]

    def run_cycle(self) -> Dict[str, Any]:
        df = self._load_funding_rates()
        events = self._get_latest_unprocessed(df)

        count = 0
        for ev in events:
            funding_rate = float(ev["fundingRate"])
            if abs(funding_rate) < self._min_funding_rate:
                continue

            equity = self.book.equity()
            notional = equity * self._capital_pct
            directional_edge = -funding_rate * 8.0
            pnl = notional * directional_edge

            self.book.apply_funding_pnl(
                ev["symbol"],
                pnl,
                meta={
                    "funding_rate": funding_rate,
                    "directional_edge": directional_edge,
                },
            )
            self._processed_timestamps.add(ev["fundingTime"])
            count += 1

        self.book.record_balance_snapshot()

        return {
            "ready": True,
            "mode": "paper",
            "runner": "funding_contrarian",
            "equity": self.book.equity(),
            "trade_count": self.book.trade_count,
            "events_processed": count,
        }
