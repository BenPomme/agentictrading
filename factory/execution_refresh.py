from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

import config


SUPPORTED_FAMILIES = {
    "binance_funding_contrarian",
    "binance_cascade_regime",
    "polymarket_cross_venue",
}


def _enabled_families() -> set[str]:
    raw = str(getattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "") or "")
    families = {item.strip() for item in raw.split(",") if item.strip()}
    return families or set(SUPPORTED_FAMILIES)


class ExecutionRefreshRunner:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)

    def should_run(self, *, family_id: str, role: str) -> bool:
        if not bool(getattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)):
            return False
        if family_id not in SUPPORTED_FAMILIES:
            return False
        if family_id not in _enabled_families():
            return False
        return str(role or "") == "champion"

    def run(
        self,
        *,
        request_path: Path,
        output_path: Path,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "standalone_embedded_mode",
            "request_path": str(request_path),
            "output_path": str(output_path),
        }
