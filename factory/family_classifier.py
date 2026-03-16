from __future__ import annotations

from pathlib import Path
from typing import Dict, Any


def _family_path(project_root: Path, family_id: str) -> Path:
    return project_root / "data" / "factory" / "families" / f"{family_id}.json"


def load_family_config(project_root: Path | str, family_id: str) -> Dict[str, Any]:
    """Load family config JSON from data/factory/families.

    Returns {} if the file is missing or invalid. This helper is intentionally
    conservative – callers must handle empty dicts.
    """
    import json

    root = Path(project_root)
    path = _family_path(root, family_id)
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_equity_family(cfg: Dict[str, Any]) -> bool:
    """Heuristic: treat families that clearly target stocks/ETFs as equity.

    Positive signals (any match → equity):
    - target_venues includes "yahoo" or "alpaca"
    - primary_connector_ids includes "yahoo_stocks" or "alpaca_stocks"

    Negative signals (override → not equity even if yahoo present):
    - target_venues includes "binance", "betfair", or "polymarket"
    """
    if not cfg:
        return False
    venues = {str(v).lower() for v in (cfg.get("target_venues") or [])}
    connectors = {str(c).lower() for c in (cfg.get("primary_connector_ids") or [])}
    non_equity_venues = {"binance", "betfair", "polymarket"}
    if venues.intersection(non_equity_venues):
        return False
    if venues.intersection({"yahoo", "alpaca"}):
        return True
    if connectors.intersection({"yahoo_stocks", "alpaca_stocks"}):
        return True
    return False


def family_backtest_venue(cfg: Dict[str, Any]) -> str:
    """Return the preferred backtest venue for a family."""
    if is_equity_family(cfg):
        return "yahoo"
    venues = cfg.get("target_venues") or []
    return str(venues[0]) if venues else "unknown"


def family_runtime_venue(cfg: Dict[str, Any]) -> str:
    """Return the preferred runtime venue for a family."""
    if is_equity_family(cfg):
        return "alpaca"
    return family_backtest_venue(cfg)

