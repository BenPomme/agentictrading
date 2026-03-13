from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from factory.contracts import ConnectorSnapshot


def _latest_mtime(paths: Iterable[Path]) -> str | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    if not mtimes:
        return None
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).isoformat()


@dataclass
class FileConnectorAdapter:
    connector_id: str
    venue: str
    data_products: List[str]
    paths: List[Path]

    def snapshot(self) -> ConnectorSnapshot:
        existing: List[Path] = []
        record_count = 0
        issues: List[str] = []
        for path in self.paths:
            if not path.exists():
                issues.append(f"missing:{path}")
                continue
            existing.append(path)
            if path.is_dir():
                record_count += len(list(path.rglob("*")))
            else:
                record_count += 1
        if existing:
            issues = []
        return ConnectorSnapshot(
            connector_id=self.connector_id,
            venue=self.venue,
            data_products=list(self.data_products),
            ready=bool(existing),
            latest_data_ts=_latest_mtime(existing),
            record_count=record_count,
            source_paths=[str(path) for path in self.paths],
            issues=issues,
        )


def default_connector_catalog(project_root: str | Path) -> List[FileConnectorAdapter]:
    root = Path(project_root)
    factory_data_root = root / "data"

    return [
        FileConnectorAdapter(
            connector_id="binance_core",
            venue="binance",
            data_products=[
                "futures_funding_rates",
                "spot_perp_features",
                "open_interest_history",
                "liquidation_logs",
            ],
            paths=[
                factory_data_root / "funding_history",
                factory_data_root / "funding",
                factory_data_root / "funding_models",
            ],
        ),
        FileConnectorAdapter(
            connector_id="betfair_core",
            venue="betfair",
            data_products=[
                "candidate_logs",
                "paper_trades",
                "prediction_experiments",
                "information_books",
            ],
            paths=[
                factory_data_root / "candidates",
                factory_data_root / "prediction",
                factory_data_root / "state",
                factory_data_root / "portfolios" / "betfair_core",
            ],
        ),
        FileConnectorAdapter(
            connector_id="polymarket_core",
            venue="polymarket",
            data_products=[
                "gamma_snapshots",
                "clob_quotes",
                "model_league_state",
                "binary_research_state",
            ],
            paths=[
                factory_data_root / "portfolios" / "polymarket_quantum_fold",
                factory_data_root / "portfolios" / "betfair_core" / "runtime" / "polymarket_binary_research_state.json",
            ],
        ),
        FileConnectorAdapter(
            connector_id="yahoo_stocks",
            venue="yahoo",
            data_products=[
                "sp500_daily_ohlcv",
                "etf_daily_ohlcv",
                "vix_history",
                "treasury_yields",
            ],
            paths=[
                factory_data_root / "yahoo" / "ohlcv",
                factory_data_root / "yahoo" / "sp500_components.json",
                factory_data_root / "yahoo" / "metadata.json",
            ],
        ),
        FileConnectorAdapter(
            connector_id="alpaca_stocks",
            venue="alpaca",
            data_products=[
                "stock_bars",
                "stock_quotes",
                "positions",
                "orders",
            ],
            paths=[
                factory_data_root / "alpaca" / "bars",
                factory_data_root / "alpaca" / "quotes",
                factory_data_root / "alpaca" / "metadata.json",
            ],
        ),
    ]
