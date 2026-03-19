from __future__ import annotations

from pathlib import Path


def test_scheduler_prefers_repo_python_when_present(tmp_path, monkeypatch):
    from scripts import data_refresh_scheduler as scheduler

    preferred = tmp_path / ".venv312" / "bin" / "python"
    preferred.parent.mkdir(parents=True, exist_ok=True)
    preferred.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(scheduler, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("FACTORY_REFRESH_PYTHON", raising=False)

    assert scheduler._preferred_python() == str(preferred)


def test_default_connector_catalog_uses_live_binance_and_polymarket_data(tmp_path):
    from factory.connectors import default_connector_catalog

    catalog = {adapter.connector_id: adapter for adapter in default_connector_catalog(tmp_path)}

    binance_paths = {str(path) for path in catalog["binance_core"].paths}
    assert str(tmp_path / "data" / "binance" / "klines") in binance_paths
    assert str(tmp_path / "data" / "funding_history" / "funding_rates" / "metadata.json") in binance_paths

    polymarket_paths = {str(path) for path in catalog["polymarket_core"].paths}
    assert str(tmp_path / "data" / "polymarket" / "prices_history") in polymarket_paths
    assert str(tmp_path / "data" / "polymarket" / "markets_metadata.json") in polymarket_paths


def test_betfair_refresh_falls_back_to_requests_client_without_library(tmp_path, monkeypatch):
    from scripts import refresh_betfair_market_books as refresh

    cert_dir = tmp_path / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / "client-2048.crt").write_text("crt", encoding="utf-8")
    (cert_dir / "client-2048.key").write_text("key", encoding="utf-8")

    monkeypatch.setattr(refresh, "betfairlightweight", None)
    monkeypatch.setattr(refresh.config, "BF_CERTS_PATH", str(cert_dir))
    monkeypatch.setattr(refresh.config, "BF_USERNAME", "user")
    monkeypatch.setattr(refresh.config, "BF_PASSWORD", "pass")
    monkeypatch.setattr(refresh.config, "BF_APP_KEY", "app")
    monkeypatch.setattr(refresh.config, "BF_LOCALE", "spain")

    captured = {}

    class FakeRequestsClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def login(self):
            return "requests-client"

    monkeypatch.setattr(refresh, "_RequestsBetfairClient", FakeRequestsClient)

    assert refresh._login_client() == "requests-client"
    assert captured["certs_path"] == cert_dir
    assert captured["locale"] == "spain"


def test_betfair_runner_rows_accept_json_rpc_dict_payload():
    from scripts import refresh_betfair_market_books as refresh

    rows = refresh._runner_rows(
        {
            "marketId": "1.2",
            "status": "OPEN",
            "inplay": False,
            "totalMatched": 12.5,
            "runners": [
                {
                    "selectionId": 10,
                    "status": "ACTIVE",
                    "ex": {
                        "availableToBack": [{"price": 2.0, "size": 11.0}],
                        "availableToLay": [{"price": 2.2, "size": 9.0}],
                    },
                }
            ],
        },
        timestamp="2026-03-19T14:00:00+00:00",
        sport="SOCCER",
    )

    assert rows[0]["market_id"] == "1.2"
    assert rows[0]["selection_id"] == "10"
    assert rows[0]["best_back"] == 2.0
    assert rows[0]["best_lay"] == 2.2


def test_polymarket_refresh_falls_back_to_midpoint(monkeypatch):
    from scripts import fetch_polymarket_history as poly

    monkeypatch.setattr(poly, "fetch_markets", lambda closed=False, limit=100: [{
        "id": "1",
        "conditionId": "cond-1",
        "clobTokenIds": "[\"token-1\"]",
        "question": "Example?",
        "closed": False,
        "volume": "10",
    }])
    monkeypatch.setattr(poly, "fetch_price_history", lambda market_id, interval="1h", start_ts=None, end_ts=None: [])
    monkeypatch.setattr(poly, "fetch_midpoint", lambda token_id: 0.42)

    saved = []

    def fake_save(market_id, history, output_dir, market_meta=None):
        saved.append((market_id, history))
        return True

    monkeypatch.setattr(poly, "save_market_history", fake_save)
    monkeypatch.setattr(poly, "RATE_LIMIT_DELAY", 0)

    result = poly.run(max_markets=1, interval="1m", include_resolved=False)

    assert result["histories_saved"] == 1
    assert result["midpoint_fallbacks"] == 1
    assert saved[0][0] == "token-1"
    assert saved[0][1][0]["p"] == 0.42
