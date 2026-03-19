#!/usr/bin/env python3
"""Fetch Polymarket historical price data for backtesting.

Sources:
1. Polymarket CLOB API /prices-history endpoint (free, per-market)
2. Gamma API for market discovery (active + recently resolved)

Stores data as Parquet files in data/polymarket/prices_history/
Run daily to accumulate history over time.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "polymarket" / "prices_history"
METADATA_PATH = PROJECT_ROOT / "data" / "polymarket" / "markets_metadata.json"

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

REQUEST_TIMEOUT = 15
RATE_LIMIT_DELAY = 0.5  # seconds between CLOB requests


def fetch_markets(closed: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch markets from Gamma API."""
    params: Dict[str, Any] = {"limit": limit}
    if closed:
        params["closed"] = "true"
        params["order"] = "end_date_iso"
        params["ascending"] = "false"
    else:
        params["closed"] = "false"

    try:
        resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch markets (closed=%s): %s", closed, e)
        return []


def fetch_price_history(
    market_id: str,
    interval: str = "1h",
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> List[Dict[str, Any]]:
    """Fetch price history for a single market from CLOB API."""
    params: Dict[str, Any] = {
        "market": market_id,
        "interval": interval,
    }
    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts

    try:
        resp = requests.get(f"{CLOB_API}/prices-history", params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", [])
    except Exception as e:
        logger.debug("Failed to fetch history for %s: %s", market_id, e)
        return []


def save_market_history(
    market_id: str,
    history: List[Dict[str, Any]],
    output_dir: Path,
    market_meta: Dict[str, Any] | None = None,
) -> bool:
    """Save market price history as Parquet."""
    if not history:
        return False

    df = pd.DataFrame(history)
    if "t" in df.columns:
        df["timestamp"] = pd.to_datetime(df["t"], unit="s", utc=True)
        df = df.rename(columns={"p": "price"})
        df = df[["timestamp", "price"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    else:
        return False

    if market_meta:
        df["market_id"] = market_id
        df["question"] = str(market_meta.get("question", ""))[:200]

    safe_id = market_id.replace("/", "_").replace("\\", "_")[:80]
    out_path = output_dir / f"{safe_id}.parquet"

    # Merge with existing data if present
    if out_path.exists():
        try:
            existing = pd.read_parquet(out_path)
            df = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        except Exception:
            pass

    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return True


def run(
    max_markets: int = 200,
    interval: str = "1h",
    include_resolved: bool = True,
) -> Dict[str, Any]:
    """Main pipeline: discover markets, fetch history, save as Parquet."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Discover markets
    active_markets = fetch_markets(closed=False, limit=min(max_markets, 100))
    resolved_markets = []
    if include_resolved:
        resolved_markets = fetch_markets(closed=True, limit=min(max_markets, 100))

    all_markets = active_markets + resolved_markets
    logger.info(
        "Discovered %d active + %d resolved = %d total markets",
        len(active_markets),
        len(resolved_markets),
        len(all_markets),
    )

    # Save metadata
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "interval": interval,
                "active_count": len(active_markets),
                "resolved_count": len(resolved_markets),
                "markets": [
                    {
                        "id": m.get("id", m.get("condition_id", "")),
                        "question": str(m.get("question", ""))[:200],
                        "closed": m.get("closed", False),
                        "volume": m.get("volume", 0),
                    }
                    for m in all_markets[:max_markets]
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Fetch price history for each market
    success_count = 0
    skip_count = 0
    fail_count = 0

    for i, market in enumerate(all_markets[:max_markets]):
        market_id = market.get("condition_id") or market.get("id", "")
        if not market_id:
            skip_count += 1
            continue

        # Also try clobTokenIds if available (Gamma API returns this as a JSON string)
        clob_token_ids = market.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str):
            try:
                import json as _json
                clob_token_ids = _json.loads(clob_token_ids)
            except Exception:
                clob_token_ids = []
        tokens_to_try = clob_token_ids if clob_token_ids else [market_id]

        for token_id in tokens_to_try:
            history = fetch_price_history(str(token_id), interval=interval)
            if history:
                saved = save_market_history(
                    str(token_id),
                    history,
                    OUTPUT_DIR,
                    market_meta=market,
                )
                if saved:
                    success_count += 1
                else:
                    fail_count += 1
            else:
                fail_count += 1

            time.sleep(RATE_LIMIT_DELAY)

        if (i + 1) % 20 == 0:
            logger.info(
                "Progress: %d/%d markets processed (%d saved)",
                i + 1,
                min(max_markets, len(all_markets)),
                success_count,
            )

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "markets_discovered": len(all_markets),
        "markets_processed": min(max_markets, len(all_markets)),
        "histories_saved": success_count,
        "skipped": skip_count,
        "failed": fail_count,
        "output_dir": str(OUTPUT_DIR),
    }
    logger.info("Done: %d histories saved, %d failed, %d skipped", success_count, fail_count, skip_count)
    return result


def main():
    parser = argparse.ArgumentParser(description="Fetch Polymarket historical data")
    parser.add_argument("--max-markets", type=int, default=200, help="Max markets to fetch")
    parser.add_argument("--interval", default="1h", choices=["1h", "1d", "6h", "1m", "1w", "max", "all"])
    parser.add_argument("--no-resolved", action="store_true", help="Skip resolved markets")
    args = parser.parse_args()

    result = run(
        max_markets=args.max_markets,
        interval=args.interval,
        include_resolved=not args.no_resolved,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
