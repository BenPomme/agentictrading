#!/usr/bin/env python3
"""
Alpaca Data Refresher for NEBULA
==================================
Fetches latest stock bars and quotes from Alpaca Data API
and stores them in data/alpaca/ for the factory connector.

Requires ALPACA_API_KEY and ALPACA_API_SECRET in .env

Usage:
    python scripts/refresh_alpaca_data.py [--days 5] [--output-dir data/alpaca]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN",
    "NVDA", "META", "TSLA", "JPM", "V", "UNH", "XOM", "JNJ",
    "PG", "MA", "HD", "COST", "ABBV", "CVX", "MRK", "AVGO",
    "PEP", "KO", "LLY", "NFLX", "AMD", "CRM",
]


def refresh_bars(client, tickers: list[str], days: int, output_dir: Path, timeframe_str: str = "1Hour") -> int:
    """Fetch recent bars for the given tickers."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    bars_dir = output_dir / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)

    end = datetime.now()
    start = end - timedelta(days=days)

    # Map string shortcuts to TimeFrame objects
    _tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame(1, TimeFrameUnit.Day),
    }
    timeframe = _tf_map.get(timeframe_str)
    if timeframe is None:
        logger.error("Invalid timeframe: %s. Use one of: %s", timeframe_str, list(_tf_map.keys()))
        return 0

    success = 0
    for ticker in tickers:
        try:
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=timeframe,
                start=start,
                end=end,
                feed="iex",
            )
            bars = client.get_stock_bars(request)
            df = bars.df
            if df is not None and len(df) > 0:
                if hasattr(df.index, "droplevel"):
                    try:
                        df = df.droplevel("symbol")
                    except (KeyError, ValueError):
                        pass
                out_path = bars_dir / f"{ticker}.parquet"

                if out_path.exists():
                    import pandas as pd
                    existing = pd.read_parquet(out_path)
                    combined = pd.concat([existing, df])
                    combined = combined[~combined.index.duplicated(keep="last")]
                    combined.sort_index(inplace=True)
                    combined.to_parquet(out_path, engine="pyarrow")
                else:
                    df.to_parquet(out_path, engine="pyarrow")
                success += 1
        except Exception as e:
            logger.warning("Failed to fetch bars for %s: %s", ticker, e)

    return success


def refresh_quotes(client, tickers: list[str], output_dir: Path) -> int:
    """Fetch latest quotes for the given tickers."""
    from alpaca.data.requests import StockLatestQuoteRequest

    quotes_dir = output_dir / "quotes"
    quotes_dir.mkdir(parents=True, exist_ok=True)

    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=tickers)
        quotes = client.get_stock_latest_quote(request)

        quotes_data = {}
        for symbol, quote in quotes.items():
            quotes_data[symbol] = {
                "ask_price": float(quote.ask_price) if quote.ask_price else None,
                "ask_size": float(quote.ask_size) if quote.ask_size else None,
                "bid_price": float(quote.bid_price) if quote.bid_price else None,
                "bid_size": float(quote.bid_size) if quote.bid_size else None,
                "timestamp": str(quote.timestamp) if quote.timestamp else None,
            }

        out_path = quotes_dir / "latest_quotes.json"
        with open(out_path, "w") as f:
            json.dump({"fetched_at": datetime.now().isoformat(), "quotes": quotes_data}, f, indent=2)

        return len(quotes_data)
    except Exception as e:
        logger.warning("Failed to fetch quotes: %s", e)
        return 0


def main():
    parser = argparse.ArgumentParser(description="Refresh Alpaca stock data for NEBULA")
    parser.add_argument("--days", type=int, default=2, help="Days of bar history (default: 2 for intraday)")
    parser.add_argument("--timeframe", type=str, default="1Hour", help="Bar timeframe (default: 1Hour, e.g. '1Min', '5Min', '15Min', '1Hour', '1Day')")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory (default: data/alpaca)")
    parser.add_argument("--universe", type=str, default=None, help="Comma-separated ticker list")
    args = parser.parse_args()

    # Fallback chain: try ALPACA_API_KEY first, then ALPACA_PAPER_API_KEY
    api_key = os.getenv("ALPACA_API_KEY", "").strip() or os.getenv("ALPACA_PAPER_API_KEY", "").strip()
    api_secret = os.getenv("ALPACA_API_SECRET", "").strip() or os.getenv("ALPACA_PAPER_API_SECRET", "").strip()

    if not api_key or not api_secret:
        logger.error("API credentials not found. Set either ALPACA_API_KEY/ALPACA_API_SECRET or ALPACA_PAPER_API_KEY/ALPACA_PAPER_API_SECRET in .env")
        logger.info("Get free paper trading keys at https://app.alpaca.markets/")
        return 1

    try:
        from alpaca.data import StockHistoricalDataClient
    except ImportError:
        logger.error("alpaca-py not installed. Run: pip install alpaca-py")
        return 1

    client = StockHistoricalDataClient(api_key, api_secret)
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "data" / "alpaca"
    output_dir.mkdir(parents=True, exist_ok=True)

    tickers = args.universe.split(",") if args.universe else DEFAULT_UNIVERSE
    logger.info("Refreshing %d tickers from Alpaca (last %d days, timeframe: %s)", len(tickers), args.days, args.timeframe)

    bars_ok = refresh_bars(client, tickers, args.days, output_dir, args.timeframe)
    quotes_ok = refresh_quotes(client, tickers, output_dir)

    metadata = {
        "last_refresh": datetime.now().isoformat(),
        "bars_success": bars_ok,
        "quotes_success": quotes_ok,
        "tickers": tickers,
        "days": args.days,
    }
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Alpaca refresh complete: %d bars, %d quotes updated", bars_ok, quotes_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
