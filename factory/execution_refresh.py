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
        execution_root_raw = str(getattr(config, "EXECUTION_REPO_ROOT", "") or "").strip()
        if not execution_root_raw:
            return {
                "status": "skipped",
                "reason": "execution_repo_not_configured",
                "request_path": str(request_path),
                "output_path": str(output_path),
            }
        execution_root = Path(execution_root_raw)
        script_path = execution_root / "scripts" / "factory_refresh_models.py"
        if not script_path.exists():
            return {
                "status": "skipped",
                "reason": "execution_refresh_script_missing",
                "request_path": str(request_path),
                "output_path": str(output_path),
                "script_path": str(script_path),
            }

        cmd: List[str] = [
            str(getattr(config, "FACTORY_EXECUTION_REFRESH_PYTHON_BIN", "python3")),
            str(script_path),
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ]
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(execution_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=int(getattr(config, "FACTORY_EXECUTION_REFRESH_TIMEOUT_SECONDS", 900)),
            )
        except Exception as exc:
            return {
                "status": "failed",
                "reason": "execution_refresh_invocation_failed",
                "error": str(exc),
                "request_path": str(request_path),
                "output_path": str(output_path),
                "script_path": str(script_path),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }

        duration_ms = int((time.perf_counter() - start) * 1000)
        payload: Dict[str, Any] = {}
        if output_path.exists():
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        payload["duration_ms"] = duration_ms
        payload["request_path"] = str(request_path)
        payload["output_path"] = str(output_path)
        payload["script_path"] = str(script_path)
        payload["returncode"] = int(proc.returncode)
        payload["stdout"] = proc.stdout.strip()
        payload["stderr"] = proc.stderr.strip()
        if proc.returncode != 0 and not payload.get("status"):
            payload["status"] = "failed"
            payload["reason"] = "execution_refresh_command_failed"
        return payload
