from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from factory.data_loader import load_data_for_requirements


def test_load_data_for_requirements_uses_binance_funding_when_fields_request_funding(tmp_path):
    funding_dir = tmp_path / "data" / "funding_history" / "funding_rates"
    funding_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "fundingRate": [0.0001],
            "fundingTime": [int(datetime.now(timezone.utc).timestamp() * 1000)],
            "markPrice": [65000.0],
        }
    ).to_parquet(funding_dir / "BTCUSDT.parquet", index=False)

    df = load_data_for_requirements(
        {
            "source": "binance",
            "instruments": ["BTCUSDT"],
            "fields": ["fundingRate", "fundingTime", "markPrice"],
        },
        Path(tmp_path),
    )

    assert not df.empty
    assert "fundingRate" in df.columns


def test_load_data_for_requirements_matches_polymarket_token_against_question_text(tmp_path):
    prices_dir = tmp_path / "data" / "polymarket" / "prices_history"
    prices_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": [datetime.now(timezone.utc) - timedelta(minutes=5)],
            "price": [0.61],
            "market_id": ["market-1"],
            "question": ["Will BTC close above 100k this week?"],
        }
    ).to_parquet(prices_dir / "market-1.parquet", index=False)
    pd.DataFrame(
        {
            "timestamp": [datetime.now(timezone.utc) - timedelta(minutes=5)],
            "price": [0.62],
            "market_id": ["market-2"],
            "question": ["Will bitcoin close above 100k this week?"],
        }
    ).to_parquet(prices_dir / "market-2.parquet", index=False)

    df = load_data_for_requirements(
        {
            "source": "polymarket",
            "instruments": ["BTC"],
            "fields": ["ohlcv"],
        },
        Path(tmp_path),
    )

    assert not df.empty
    assert len(set(df["market_id"])) == 1
