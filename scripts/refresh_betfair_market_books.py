#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

try:
    import betfairlightweight  # type: ignore
    from betfairlightweight import filters  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"betfairlightweight is required: {exc}")


DEFAULT_MARKET_TYPES = ("MATCH_ODDS", "WIN")
OUTPUT_DIR = PROJECT_ROOT / "data" / "betfair" / "market_books"
MAX_ROWS_PER_MARKET = 5000


def _certs_ready(cert_dir: Path) -> bool:
    return (any(cert_dir.glob("*.crt")) and any(cert_dir.glob("*.key"))) or any(cert_dir.glob("*.pem"))


def _login_client():
    certs_path = Path(str(getattr(config, "BF_CERTS_PATH", "") or "")).expanduser()
    if not certs_path.is_absolute():
        certs_path = (PROJECT_ROOT / certs_path).resolve()
    if not _certs_ready(certs_path):
        raise RuntimeError(f"Betfair certs not found in {certs_path}")
    client = betfairlightweight.APIClient(
        config.BF_USERNAME,
        config.BF_PASSWORD,
        app_key=config.BF_APP_KEY,
        certs=str(certs_path),
        locale=str(getattr(config, "BF_LOCALE", "spain") or "spain"),
    )
    client.login()
    return client


def _discover_markets(client, max_markets: int, market_types: Iterable[str]) -> Dict[str, Dict[str, str]]:
    event_types = client.betting.list_event_types(filter=filters.market_filter())
    event_type_ids = [str(item.event_type.id) for item in event_types if getattr(item, "event_type", None) is not None]
    market_filter = filters.market_filter(
        event_type_ids=event_type_ids,
        market_type_codes=[str(item) for item in market_types if str(item).strip()],
        market_start_time={"from": datetime.now(timezone.utc).isoformat()},
    )
    catalogues = client.betting.list_market_catalogue(
        filter=market_filter,
        max_results=max_markets,
        sort="FIRST_TO_START",
        market_projection=["EVENT", "RUNNER_DESCRIPTION"],
    )
    discovered: Dict[str, Dict[str, str]] = {}
    for item in catalogues:
        market_id = str(getattr(item, "market_id", "") or "").strip()
        if not market_id:
            continue
        event_type = getattr(item, "event_type", None)
        sport = str(getattr(event_type, "name", "") or getattr(getattr(item, "event", None), "country_code", "") or "unknown")
        discovered[market_id] = {
            "sport": sport,
            "market_name": str(getattr(item, "market_name", "") or ""),
        }
    return discovered


def _chunked(items: List[str], chunk_size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), chunk_size):
        yield items[index:index + chunk_size]


def _runner_rows(market_book, *, timestamp: str, sport: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    market_id = str(getattr(market_book, "market_id", "") or "")
    market_status = str(getattr(market_book, "status", "") or "")
    inplay = bool(getattr(market_book, "inplay", False))
    total_matched = float(getattr(market_book, "total_matched", 0.0) or 0.0)
    for runner in list(getattr(market_book, "runners", []) or []):
        ex = getattr(runner, "ex", None)
        backs = list(getattr(ex, "available_to_back", []) or [])
        lays = list(getattr(ex, "available_to_lay", []) or [])
        best_back = float(backs[0].price) if backs else None
        best_lay = float(lays[0].price) if lays else None
        midpoint = None
        if best_back is not None and best_lay is not None:
            midpoint = round((best_back + best_lay) / 2.0, 6)
        rows.append(
            {
                "timestamp": timestamp,
                "market_id": market_id,
                "selection_id": str(getattr(runner, "selection_id", "") or ""),
                "best_back": best_back,
                "best_lay": best_lay,
                "midpoint": midpoint,
                "available_to_back": float(backs[0].size) if backs else 0.0,
                "runner_status": str(getattr(runner, "status", "") or ""),
                "market_status": market_status,
                "inplay": inplay,
                "total_matched": total_matched,
                "sport": sport,
            }
        )
    return rows


def _append_market_rows(path: Path, rows: List[Dict[str, object]]) -> None:
    incoming = pd.DataFrame(rows)
    if path.exists():
        existing = pd.read_parquet(path)
        incoming = pd.concat([existing, incoming], ignore_index=True)
    incoming = incoming.tail(MAX_ROWS_PER_MARKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming.to_parquet(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Betfair market-book snapshots into data/betfair/market_books.")
    parser.add_argument("--max-markets", type=int, default=40)
    parser.add_argument("--market-types", default=",".join(DEFAULT_MARKET_TYPES))
    parser.add_argument("--market-ids", default="")
    args = parser.parse_args()

    explicit_market_ids = [item.strip() for item in str(args.market_ids or "").split(",") if item.strip()]
    market_types = [item.strip() for item in str(args.market_types or "").split(",") if item.strip()]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    client = _login_client()
    try:
        discovered = (
            {market_id: {"sport": "unknown", "market_name": ""} for market_id in explicit_market_ids}
            if explicit_market_ids
            else _discover_markets(client, args.max_markets, market_types or DEFAULT_MARKET_TYPES)
        )
        market_ids = list(discovered.keys())
        if not market_ids:
            raise RuntimeError("No open Betfair markets discovered for refresh")
        refreshed = 0
        for chunk in _chunked(market_ids, 30):
            books = client.betting.list_market_book(
                market_ids=chunk,
                price_projection=filters.price_projection(price_data=["EX_BEST_OFFERS"]),
            )
            for market_book in books:
                market_id = str(getattr(market_book, "market_id", "") or "").strip()
                if not market_id:
                    continue
                metadata = discovered.get(market_id, {})
                rows = _runner_rows(
                    market_book,
                    timestamp=now,
                    sport=str(metadata.get("sport", "unknown") or "unknown"),
                )
                if not rows:
                    continue
                _append_market_rows(OUTPUT_DIR / f"{market_id}.parquet", rows)
                refreshed += 1
        metadata = {
            "last_refresh": now,
            "interval": "1m",
            "market_count": refreshed,
            "market_ids": market_ids,
            "market_types": market_types or list(DEFAULT_MARKET_TYPES),
            "source": "betfair_authenticated_api",
        }
        (OUTPUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        print(json.dumps(metadata, indent=2))
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
