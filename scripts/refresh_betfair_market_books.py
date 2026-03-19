#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

try:
    import betfairlightweight  # type: ignore
    from betfairlightweight import filters  # type: ignore
except ImportError:  # pragma: no cover
    betfairlightweight = None
    filters = None


DEFAULT_MARKET_TYPES = ("MATCH_ODDS", "WIN")
OUTPUT_DIR = PROJECT_ROOT / "data" / "betfair" / "market_books"
MAX_ROWS_PER_MARKET = 5000
BETTING_JSON_RPC_URLS = {
    "new_zealand": "https://api.betfair.com.au/exchange/betting/json-rpc/v1",
}
IDENTITY_CERT_URLS = {
    "spain": "https://identitysso-cert.betfair.es/api/certlogin",
    "italy": "https://identitysso-cert.betfair.it/api/certlogin",
    "romania": "https://identitysso-cert.betfair.ro/api/certlogin",
    "sweden": "https://identitysso-cert.betfair.se/api/certlogin",
}
DEFAULT_IDENTITY_CERT_URL = "https://identitysso-cert.betfair.com/api/certlogin"
DEFAULT_BETTING_JSON_RPC_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
USER_AGENT = "AgenticTrading/1.0"


def _get_value(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _certs_ready(cert_dir: Path) -> bool:
    return (any(cert_dir.glob("*.crt")) and any(cert_dir.glob("*.key"))) or any(cert_dir.glob("*.pem"))


def _cert_argument(cert_dir: Path):
    cert = next(iter(sorted(cert_dir.glob("*.crt"))), None) or next(iter(sorted(cert_dir.glob("*.cert"))), None)
    key = next(iter(sorted(cert_dir.glob("*.key"))), None)
    if cert is not None and key is not None:
        return (str(cert), str(key))
    pem = next(iter(sorted(cert_dir.glob("*.pem"))), None)
    if pem is not None:
        return str(pem)
    raise RuntimeError(f"Betfair certs not found in {cert_dir}")


def _locale_key() -> str:
    return str(getattr(config, "BF_LOCALE", "") or "").strip().lower().replace("-", "_")


def _identity_cert_url(locale: str) -> str:
    return IDENTITY_CERT_URLS.get(locale, DEFAULT_IDENTITY_CERT_URL)


def _betting_json_rpc_url(locale: str) -> str:
    return BETTING_JSON_RPC_URLS.get(locale, DEFAULT_BETTING_JSON_RPC_URL)


class _RequestsBetfairClient:
    def __init__(self, *, username: str, password: str, app_key: str, certs_path: Path, locale: str) -> None:
        self.username = username
        self.password = password
        self.app_key = app_key
        self.certs_path = certs_path
        self.locale = locale
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "User-Agent": USER_AGENT,
            }
        )
        self.session_token: str | None = None
        self.betting = self

    def login(self):
        response = self.session.post(
            _identity_cert_url(self.locale),
            data={"username": self.username, "password": self.password},
            headers={
                "Accept": "application/json",
                "X-Application": self.app_key,
                "content-type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            cert=_cert_argument(self.certs_path),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        status = str(payload.get("loginStatus") or "").strip().upper()
        token = str(payload.get("sessionToken") or "").strip()
        if status != "SUCCESS" or not token:
            raise RuntimeError(f"Betfair cert login failed: {status or payload}")
        self.session_token = token
        return self

    def logout(self):
        self.session_token = None

    def _rpc(self, method: str, params: Dict[str, object]) -> list[dict]:
        if not self.session_token:
            raise RuntimeError("Betfair session token missing")
        response = self.session.post(
            _betting_json_rpc_url(self.locale),
            json=[{"jsonrpc": "2.0", "method": method, "params": params, "id": 1}],
            headers={
                "X-Application": self.app_key,
                "X-Authentication": self.session_token,
                "content-type": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "User-Agent": USER_AGENT,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return []
        item = payload[0]
        if "error" in item:
            raise RuntimeError(f"Betfair {method} failed: {item['error']}")
        return list(item.get("result") or [])

    def list_event_types(self, filter: Dict[str, object] | None = None):
        return self._rpc("SportsAPING/v1.0/listEventTypes", {"filter": filter or {}})

    def list_market_catalogue(
        self,
        filter: Dict[str, object],
        max_results: int,
        sort: str,
        market_projection: List[str] | None = None,
    ):
        return self._rpc(
            "SportsAPING/v1.0/listMarketCatalogue",
            {
                "filter": filter,
                "maxResults": str(max_results),
                "sort": sort,
                "marketProjection": market_projection or [],
            },
        )

    def list_market_book(self, market_ids: List[str], price_projection: Dict[str, object] | None = None):
        return self._rpc(
            "SportsAPING/v1.0/listMarketBook",
            {
                "marketIds": market_ids,
                "priceProjection": price_projection or {"priceData": ["EX_BEST_OFFERS"]},
            },
        )


def _market_filter(**kwargs):
    cleaned = {key: value for key, value in kwargs.items() if value not in (None, [], "", {})}
    if filters is None:
        return cleaned
    return filters.market_filter(**cleaned)


def _price_projection(price_data: List[str]):
    if filters is None:
        return {"priceData": price_data}
    return filters.price_projection(price_data=price_data)


def _login_client():
    certs_path = Path(str(getattr(config, "BF_CERTS_PATH", "") or "")).expanduser()
    if not certs_path.is_absolute():
        certs_path = (PROJECT_ROOT / certs_path).resolve()
    if not _certs_ready(certs_path):
        raise RuntimeError(f"Betfair certs not found in {certs_path}")
    locale = _locale_key() or "spain"
    if betfairlightweight is None:
        return _RequestsBetfairClient(
            username=config.BF_USERNAME,
            password=config.BF_PASSWORD,
            app_key=config.BF_APP_KEY,
            certs_path=certs_path,
            locale=locale,
        ).login()
    client = betfairlightweight.APIClient(
        config.BF_USERNAME,
        config.BF_PASSWORD,
        app_key=config.BF_APP_KEY,
        certs=str(certs_path),
        locale=locale,
    )
    client.login()
    return client


def _discover_markets(client, max_markets: int, market_types: Iterable[str]) -> Dict[str, Dict[str, str]]:
    event_types = client.betting.list_event_types(filter=_market_filter())
    event_type_ids = []
    for item in event_types:
        event_type = _get_value(item, "event_type") or _get_value(item, "eventType")
        event_type_id = _get_value(event_type, "id")
        if event_type_id is not None:
            event_type_ids.append(str(event_type_id))
    market_filter = _market_filter(
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
        market_id = str(_get_value(item, "market_id", _get_value(item, "marketId", "")) or "").strip()
        if not market_id:
            continue
        event_type = _get_value(item, "event_type") or _get_value(item, "eventType")
        event = _get_value(item, "event")
        sport = str(
            _get_value(event_type, "name", "")
            or _get_value(event, "country_code", _get_value(event, "countryCode", ""))
            or "unknown"
        )
        discovered[market_id] = {
            "sport": sport,
            "market_name": str(_get_value(item, "market_name", _get_value(item, "marketName", "")) or ""),
        }
    return discovered


def _chunked(items: List[str], chunk_size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), chunk_size):
        yield items[index:index + chunk_size]


def _runner_rows(market_book, *, timestamp: str, sport: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    market_id = str(_get_value(market_book, "market_id", _get_value(market_book, "marketId", "")) or "")
    market_status = str(_get_value(market_book, "status", "") or "")
    inplay = bool(_get_value(market_book, "inplay", False))
    total_matched = float(_get_value(market_book, "total_matched", _get_value(market_book, "totalMatched", 0.0)) or 0.0)
    for runner in list(_get_value(market_book, "runners", []) or []):
        ex = _get_value(runner, "ex", {})
        backs = list(_get_value(ex, "available_to_back", _get_value(ex, "availableToBack", [])) or [])
        lays = list(_get_value(ex, "available_to_lay", _get_value(ex, "availableToLay", [])) or [])
        best_back = float(_get_value(backs[0], "price")) if backs else None
        best_lay = float(_get_value(lays[0], "price")) if lays else None
        midpoint = None
        if best_back is not None and best_lay is not None:
            midpoint = round((best_back + best_lay) / 2.0, 6)
        rows.append(
            {
                "timestamp": timestamp,
                "market_id": market_id,
                "selection_id": str(_get_value(runner, "selection_id", _get_value(runner, "selectionId", "")) or ""),
                "best_back": best_back,
                "best_lay": best_lay,
                "midpoint": midpoint,
                "available_to_back": float(_get_value(backs[0], "size")) if backs else 0.0,
                "runner_status": str(_get_value(runner, "status", "") or ""),
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
                price_projection=_price_projection(price_data=["EX_BEST_OFFERS"]),
            )
            for market_book in books:
                market_id = str(_get_value(market_book, "market_id", _get_value(market_book, "marketId", "")) or "").strip()
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
