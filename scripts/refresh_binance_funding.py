#!/usr/bin/env python3
"""
Fetch latest funding rates from Binance perpetual futures public API and append
them to local CSV files. Each symbol's history is deduplicated by fundingTime
and stored in data/funding_history/funding_rates/.
"""

import argparse
import json
import logging
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "LINKUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "ARBUSDT",
    "OPUSDT",
]
API_BASE = "https://fapi.binance.com/fapi/v1/fundingRate"


def fetch_funding_rates(symbol: str, limit: int = 100) -> list[dict]:
    resp = requests.get(API_BASE, params={"symbol": symbol, "limit": limit}, timeout=30)
    resp.raise_for_status()
    return resp.json()


CSV_COLUMNS = ("symbol", "funding_rate", "funding_time", "mark_price")

# Maps API camelCase keys to our snake_case CSV columns
_API_KEY_MAP = {
    "fundingRate": "funding_rate",
    "fundingTime": "funding_time",
    "markPrice": "mark_price",
}


def _normalize_row(row: dict) -> dict:
    """Convert API camelCase keys to snake_case CSV format."""
    out = {}
    for k, v in row.items():
        out[_API_KEY_MAP.get(k, k)] = v
    return out


def merge_and_save(output_dir: Path, symbol: str, new_rows: list[dict]) -> int:
    out_path = output_dir / f"{symbol}.csv"
    existing: list[dict] = []
    if out_path.exists():
        lines = out_path.read_text().strip().splitlines()
        if lines:
            headers = lines[0].split(",")
            for line in lines[1:]:
                vals = line.split(",")
                existing.append(dict(zip(headers, vals)))

    time_key = "funding_time"
    seen_times = set()
    for r in existing:
        t = r.get(time_key, r.get("fundingTime", ""))
        if t:
            seen_times.add(int(t))

    for r in new_rows:
        nr = _normalize_row(r)
        ts = int(nr.get(time_key, 0))
        if ts and ts not in seen_times:
            existing.append(nr)
            seen_times.add(ts)

    existing.sort(key=lambda x: int(x.get(time_key, x.get("fundingTime", 0))))
    csv_lines = [",".join(CSV_COLUMNS)]
    for row in existing:
        csv_lines.append(",".join(str(row.get(c, "")) for c in CSV_COLUMNS))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(csv_lines) + "\n")
    return len(existing)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Binance perpetual funding rates.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory for CSV files",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated list of symbols to fetch",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Number of funding rate records per symbol (default: 100)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or (PROJECT_ROOT / "data" / "funding_history" / "funding_rates")
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else DEFAULT_SYMBOLS
    limit = min(max(args.limit, 1), 1000)

    counts: dict[str, int] = {}
    for symbol in symbols:
        try:
            data = fetch_funding_rates(symbol, limit=limit)
            total = merge_and_save(output_dir, symbol, data)
            counts[symbol] = total
            logger.info("%s: saved %d rows (fetched %d)", symbol, total, len(data))
        except Exception as e:
            logger.warning("%s: skipped - %s", symbol, e)

        time.sleep(0.3)

    metadata = {
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": counts,
    }
    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    logger.info("Wrote metadata to %s", meta_path)


if __name__ == "__main__":
    main()
