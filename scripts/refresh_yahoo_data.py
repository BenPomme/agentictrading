#!/usr/bin/env python3
"""
Yahoo Data Incremental Refresher for NEBULA
=============================================
Fetches the last N days of data for all tickers in data/yahoo/ohlcv/
and appends/deduplicates. Can be run daily by the factory cycle or standalone.

Usage:
    python scripts/refresh_yahoo_data.py [--days 7] [--data-dir data/yahoo/ohlcv]
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def refresh_ticker(ticker: str, parquet_path: Path, days: int) -> bool:
    """Refresh a single ticker's Parquet file with recent data."""
    try:
        import yfinance as yf

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        new_data = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
        if new_data is None or len(new_data) == 0:
            logger.debug("No new data for %s", ticker)
            return False

        if isinstance(new_data.columns, pd.MultiIndex):
            new_data.columns = new_data.columns.get_level_values(0)

        if parquet_path.exists():
            existing = pd.read_parquet(parquet_path)
            combined = pd.concat([existing, new_data])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
        else:
            combined = new_data

        combined.to_parquet(parquet_path, engine="pyarrow")
        logger.debug("Refreshed %s: %d total rows", ticker, len(combined))
        return True
    except Exception as e:
        logger.warning("Failed to refresh %s: %s", ticker, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Incremental Yahoo data refresh for NEBULA")
    parser.add_argument("--days", type=int, default=7, help="Days of history to fetch (default: 7)")
    parser.add_argument("--data-dir", type=str, default=None, help="Parquet directory (default: data/yahoo/ohlcv)")
    parser.add_argument("--batch-delay", type=float, default=1.0, help="Seconds between batches of 20")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else project_root / "data" / "yahoo" / "ohlcv"

    if not data_dir.exists():
        logger.error("Data directory does not exist: %s. Run download_stock_data.py first.", data_dir)
        return 1

    parquet_files = sorted(data_dir.glob("*.parquet"))
    if not parquet_files:
        logger.error("No parquet files found in %s. Run download_stock_data.py first.", data_dir)
        return 1

    logger.info("Refreshing %d tickers with last %d days of data", len(parquet_files), args.days)

    success = 0
    failed = 0

    for i, pf in enumerate(parquet_files):
        ticker = pf.stem
        if ticker.startswith("_"):
            ticker = "^" + ticker[1:]

        ok = refresh_ticker(ticker, pf, args.days)
        if ok:
            success += 1
        else:
            failed += 1

        if (i + 1) % 20 == 0:
            logger.info("Progress: %d/%d", i + 1, len(parquet_files))
            time.sleep(args.batch_delay)

    # Update metadata
    meta_path = data_dir.parent / "metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)
    else:
        metadata = {}

    metadata["last_refresh"] = datetime.now().isoformat()
    metadata["last_refresh_days"] = args.days
    metadata["last_refresh_success"] = success
    metadata["last_refresh_failed"] = failed

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Refresh complete: %d success, %d failed out of %d", success, failed, len(parquet_files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
