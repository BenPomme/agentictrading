#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
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


def _preferred_python(loop_root: Path) -> str:
    override = str(os.environ.get("FACTORY_REFRESH_PYTHON") or "").strip()
    if override:
        return override
    preferred = loop_root / ".venv312" / "bin" / "python"
    if preferred.exists():
        return str(preferred)
    return sys.executable


def _pid_running(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _launch_background_process(
    loop_root: Path,
    *,
    script_path: Path,
    pid_path: Path,
    log_path: Path,
    args: list[str] | None = None,
) -> subprocess.Popen[str] | None:
    if _pid_running(pid_path):
        return None
    if not script_path.exists():
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python_executable = _preferred_python(loop_root)
    with open(log_path, "a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [python_executable, str(script_path), *(args or [])],
            cwd=str(loop_root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return proc


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
    _provider_order = os.getenv("FACTORY_AGENT_PROVIDER_ORDER", "codex,deterministic")
    print("[factory-loop] NEBULA standalone mode", flush=True)
    print(f"[factory-loop] OPENAI_API_KEY configured: {bool(_api_key)} (len={len(_api_key)})", flush=True)
    print(f"[factory-loop] Provider order: {_provider_order}", flush=True)

    if "openai_api" in _provider_order and not _api_key:
        print(
            "[factory-loop] FATAL: openai_api is in FACTORY_AGENT_PROVIDER_ORDER "
            "but OPENAI_API_KEY is empty in .env. Agent runs will silently fail "
            "to deterministic (no-op). Fix .env and restart.",
            flush=True,
        )
        sys.exit(1)

    if "openai_api" not in _provider_order:
        print(
            "[factory-loop] WARNING: openai_api is NOT in FACTORY_AGENT_PROVIDER_ORDER. "
            "If codex CLI is unavailable, agents will fall to deterministic (no-op).",
            flush=True,
        )

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

    support_processes: list[tuple[str, subprocess.Popen[str], Path]] = []

    def _start_support_processes() -> None:
        if bool(getattr(config, "FACTORY_DATA_REFRESH_AUTOSTART_ENABLED", True)):
            scheduler_proc = _launch_background_process(
                loop_root,
                script_path=loop_root / "scripts" / "data_refresh_scheduler.py",
                pid_path=loop_root / "data" / "factory" / "data_refresh_scheduler.pid",
                log_path=loop_root / "data" / "factory" / "data_refresh_scheduler.log",
            )
            if scheduler_proc is not None:
                support_processes.append(("data refresh scheduler", scheduler_proc, loop_root / "data" / "factory" / "data_refresh_scheduler.pid"))
                print(f"[factory-loop] Data refresh scheduler started (pid={scheduler_proc.pid})", flush=True)

        if bool(getattr(config, "FACTORY_DASHBOARD_AUTOSTART_ENABLED", True)):
            dashboard_port = int(getattr(config, "FACTORY_DASHBOARD_PORT", 8787) or 8787)
            dashboard_proc = _launch_background_process(
                loop_root,
                script_path=loop_root / "scripts" / "factory_dashboard.py",
                pid_path=loop_root / "data" / "factory" / "dashboard.pid",
                log_path=loop_root / "data" / "factory" / f"dashboard_{dashboard_port}.log",
                args=["--port", str(dashboard_port)],
            )
            if dashboard_proc is not None:
                support_processes.append(("dashboard", dashboard_proc, loop_root / "data" / "factory" / "dashboard.pid"))
                print(f"[factory-loop] Dashboard started (pid={dashboard_proc.pid}, port={dashboard_port})", flush=True)

    def _cleanup_support_processes():
        for name, proc, pid_path in reversed(support_processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                print(f"[factory-loop] {name} stopped", flush=True)
            try:
                if pid_path.exists():
                    pid_path.unlink()
            except OSError:
                pass

    _start_support_processes()
    atexit.register(_cleanup_support_processes)
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup_support_processes(), sys.exit(0)))

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
