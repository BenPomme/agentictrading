from __future__ import annotations

import re
from typing import Iterable


_RUNTIME_ALIAS_PREFIX = "factory_lane__"


TARGET_PORTFOLIO_ALIASES = {
    "betfair_execution_book": "betfair_core",
    "betfair_prediction_league": "betfair_core",
    "betfair_suspension_lag": "betfair_core",
    "betfair_crossbook_consensus": "betfair_core",
    "betfair_timezone_decay": "betfair_core",
    "polymarket_binary_research": "polymarket_quantum_fold",
}


def resolve_target_portfolio(portfolio_id: str) -> str:
    value = str(portfolio_id or "")
    parsed = parse_runtime_portfolio_alias(value)
    if parsed:
        return parsed["canonical_portfolio_id"]
    return TARGET_PORTFOLIO_ALIASES.get(value, value)


def portfolio_target_matches(targets: Iterable[str], portfolio_id: str) -> bool:
    resolved = str(portfolio_id or "")
    return any(resolve_target_portfolio(target) == resolved for target in targets)


def build_runtime_portfolio_alias(canonical_portfolio_id: str, lineage_id: str) -> str:
    canonical = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(canonical_portfolio_id or "").strip()).strip("-")
    lineage_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(lineage_id or "").strip()).strip("-")
    if not canonical or not lineage_slug:
        raise ValueError("canonical_portfolio_id and lineage_id are required for runtime aliases")
    return f"{_RUNTIME_ALIAS_PREFIX}{canonical}__{lineage_slug}"


def parse_runtime_portfolio_alias(portfolio_id: str) -> dict[str, str] | None:
    value = str(portfolio_id or "").strip()
    if not value.startswith(_RUNTIME_ALIAS_PREFIX):
        return None
    remainder = value[len(_RUNTIME_ALIAS_PREFIX) :]
    canonical, separator, lineage_slug = remainder.partition("__")
    if not canonical or not separator or not lineage_slug:
        return None
    return {
        "canonical_portfolio_id": canonical,
        "lineage_slug": lineage_slug,
        "runtime_portfolio_id": value,
    }
