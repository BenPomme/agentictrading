#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

import config  # noqa: E402
from factory.orchestrator import FactoryOrchestrator  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_log_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return project_root / path


def _append_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AgenticTrading factory continuously.")
    parser.add_argument("--project-root", default=str(project_root))
    parser.add_argument("--interval-seconds", type=int, default=int(config.FACTORY_LOOP_INTERVAL_SECONDS))
    parser.add_argument("--max-cycles", type=int, default=0, help="Stop after N cycles. Zero means run forever.")
    parser.add_argument("--log-path", default=str(config.FACTORY_LOOP_LOG_PATH))
    parser.add_argument("--json", action="store_true", help="Emit compact JSON progress to stdout.")
    args = parser.parse_args(argv)

    loop_root = Path(args.project_root)

    # Startup diagnostics
    _api_key = os.getenv("OPENAI_API_KEY", "")
    _exec_root = os.getenv("EXECUTION_REPO_ROOT", "")
    print(f"[factory-loop] NEBULA standalone mode: EXECUTION_REPO_ROOT={'<empty>' if not _exec_root else _exec_root}", flush=True)
    print(f"[factory-loop] OPENAI_API_KEY configured: {bool(_api_key)} (len={len(_api_key)})", flush=True)
    print(f"[factory-loop] Provider order: {os.getenv('FACTORY_AGENT_PROVIDER_ORDER', 'codex,deterministic')}", flush=True)

    # Log market hours status
    try:
        from factory.orchestrator import is_stock_market_open

        print(f"[factory-loop] Stock market open: {is_stock_market_open()}", flush=True)
    except Exception:
        pass

    orchestrator = FactoryOrchestrator(loop_root)
    log_path = _resolve_log_path(args.log_path)
    cycle_limit = max(0, int(args.max_cycles))
    cycles_completed = 0
    interval = max(1, int(args.interval_seconds))

    while True:
        pause_flag = loop_root / "data" / "factory" / "factory_paused.flag"
        if pause_flag.exists():
            print(
                f"[factory-loop] Factory paused by operator (flag file present). Sleeping {interval}s.",
                flush=True,
            )
            time.sleep(interval)
            continue

        started_at = time.time()
        cycle_started = _utc_now()

        # Log market hours each cycle
        try:
            from factory.orchestrator import is_stock_market_open

            market_status = "OPEN" if is_stock_market_open() else "CLOSED"
            print(f"[factory-loop] Cycle {cycles_completed + 1} | Market: {market_status}", flush=True)
        except Exception:
            pass

        try:
            state = orchestrator.run_cycle()
            research_summary = dict(state.get("research_summary") or {})
            payload = {
                "ts": cycle_started,
                "status": "ok",
                "cycle_count": state.get("cycle_count"),
                "factory_status": state.get("status"),
                "readiness_status": dict(state.get("readiness") or {}).get("status"),
                "lineage_count": len(state.get("lineages") or []),
                "family_count": len(state.get("families") or []),
                "positive_model_count": research_summary.get("positive_model_count"),
                "operator_escalation_count": research_summary.get("operator_escalation_count"),
                "duration_seconds": round(time.time() - started_at, 3),
            }
        except Exception as exc:
            payload = {
                "ts": cycle_started,
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
                "duration_seconds": round(time.time() - started_at, 3),
            }
        _append_log(log_path, payload)
        if args.json:
            print(json.dumps(payload), flush=True)
        else:
            if payload["status"] == "ok":
                print(
                    "[factory-loop] {ts} cycle={cycle_count} status={factory_status} readiness={readiness_status} "
                    "families={family_count} lineages={lineage_count} positives={positive_model_count} "
                    "escalations={operator_escalation_count} duration={duration_seconds}s".format(**payload),
                    flush=True,
                )
            else:
                print(
                    "[factory-loop] {ts} error={error} duration={duration_seconds}s".format(**payload),
                    flush=True,
                )
        cycles_completed += 1
        if cycle_limit and cycles_completed >= cycle_limit:
            return 0 if payload["status"] == "ok" else 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
