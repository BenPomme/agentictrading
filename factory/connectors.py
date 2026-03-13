from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

import config
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
    execution_root = Path(str(getattr(config, "EXECUTION_REPO_ROOT", "")).strip()).expanduser()
    exec_data_root = execution_root / "data" if execution_root.is_dir() else root / "data"
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
                exec_data_root / "funding_history",
                exec_data_root / "funding",
                exec_data_root / "funding_models",
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
                exec_data_root / "candidates",
                exec_data_root / "prediction",
                exec_data_root / "state",
                exec_data_root / "portfolios" / "betfair_core",
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
                exec_data_root / "portfolios" / "polymarket_quantum_fold",
                exec_data_root / "portfolios" / "betfair_core" / "runtime" / "polymarket_binary_research_state.json",
                factory_data_root / "portfolios" / "polymarket_quantum_fold",
                factory_data_root / "portfolios" / "betfair_core" / "runtime" / "polymarket_binary_research_state.json",
            ],
        ),
    ]
