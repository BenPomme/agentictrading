from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

import config


def _enabled_families() -> set[str]:
    raw = str(getattr(config, "FACTORY_EXECUTION_REFRESH_FAMILIES", "") or "")
    return {item.strip() for item in raw.split(",") if item.strip()}


class ExecutionRefreshRunner:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)

    def should_run(self, *, family_id: str, role: str) -> bool:
        if not bool(getattr(config, "FACTORY_EXECUTION_REFRESH_ENABLED", True)):
            return False
        enabled_families = _enabled_families()
        if enabled_families and family_id not in enabled_families:
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
