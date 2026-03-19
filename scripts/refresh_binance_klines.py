#!/usr/bin/env python3
"""
Fetch latest Binance kline bars and store them locally for paper trading.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://api.binance.com/api/v3/klines"
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    response = requests.get(
        API_BASE,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_asset_volume", "trade_count", "taker_buy_base_volume",
        "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(rows, columns=columns)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Binance kline bars.")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--interval", type=str, default="1m")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in str(args.symbols).split(",") if item.strip()]
    interval = str(args.interval).strip()
    output_dir = PROJECT_ROOT / "data" / "binance" / "klines" / interval
    output_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for symbol in symbols:
        try:
            df = fetch_klines(symbol, interval, limit=max(1, min(args.limit, 1000)))
            out_path = output_dir / f"{symbol}.parquet"
            if out_path.exists():
                existing = pd.read_parquet(out_path)
                combined = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
                combined.to_parquet(out_path, index=False)
            else:
                df.to_parquet(out_path, index=False)
            saved += 1
        except Exception as exc:
            logger.warning("Failed to refresh %s: %s", symbol, exc)
        time.sleep(0.2)

    metadata = {
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "interval": interval,
        "symbols": symbols,
        "saved_count": saved,
    }
    meta_path = PROJECT_ROOT / "data" / "binance" / "klines" / "metadata.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Binance kline refresh complete: %d/%d symbols", saved, len(symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
