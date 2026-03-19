#!/usr/bin/env python3
"""Supervised autonomous paper-learning window.

Runs a bounded number of factory cycles with paper trading enabled,
autonomous paper promotion active, and live trading hard-disabled.

Usage:
    .venv312/bin/python scripts/run_autonomous_paper_window.py
    .venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 3 --dry-run
    .venv312/bin/python scripts/run_autonomous_paper_window.py --max-cycles 10 --interval 120

Stop:
    Ctrl+C, or: touch data/factory/factory_paused.flag

Rollback:
    FACTORY_ENABLE_PAPER_TRADING=false
    FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION=false
    (or simply stop the script — it is not a daemon)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root and inject into sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Paper window environment overrides — set BEFORE importing config
# These override .env / .env.staging values for this window only.
# ---------------------------------------------------------------------------
_PAPER_WINDOW_OVERRIDES = {
    # --- Paper mode ON, live mode OFF ---
    "FACTORY_ENABLE_PAPER_TRADING": "true",
    "FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION": "true",
    "FACTORY_ALLOW_AUTONOMOUS_MUTATION": "true",
    "FACTORY_ENABLE_LIVE_TRADING": "false",
    "FACTORY_LIVE_TRADING_HARD_DISABLE": "true",
    "PAPER_TRADING": "true",

    # --- Scope caps: one active champion per family ---
    "FACTORY_MAX_ACTIVE_FAMILIES": "4",
    "FACTORY_MAX_ACTIVE_MODELS_PER_FAMILY": "1",
    "FACTORY_MAX_CHALLENGERS_PER_FAMILY": "1",
    "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES": "4",
    "FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES_PER_FAMILY": "1",

    # --- Capital controls: per-lineage sizing governs; global stake_fraction is fallback ---
    "STAKE_FRACTION": "0.02",
    "INITIAL_BALANCE_EUR": "1000.00",
    "FACTORY_PAPER_LINEAGE_INITIAL_BALANCE": "1000.00",

    # --- Cost governance (strict) ---
    "FACTORY_STRICT_BUDGETS": "true",

    # --- Runtime: mobkit + Goldfish ---
    "FACTORY_RUNTIME_BACKEND": "mobkit",
    "FACTORY_ENABLE_GOLDFISH_PROVENANCE": "true",
    "FACTORY_REAL_AGENTS_ENABLED": "true",
    "AGENTIC_FACTORY_MODE": "full",

    # --- Multi-venue scope: all technically ready venues ---
    "FACTORY_PRIMARY_MARKET_DATA_PROVIDER": "binance",
    "FACTORY_ACTIVE_EXECUTION_VENUES": "betfair,binance,polymarket,alpaca,yahoo",

    # --- Paper holdoff: don't churn healthy paper models ---
    "FACTORY_PAPER_HOLDOFF_ENABLED": "true",

    # --- Venue scope enforcement: all ready venues ---
    # Scope = betfair + binance + polymarket + yahoo + alpaca
    # Families must target only venues within this set.
    # cross_venue_probability_elasticity (polymarket+binance) → IN scope
    # vol_surface_dispersion_rotation (yahoo+alpaca) → IN scope
    # Betfair families → IN scope once authenticated refresh is healthy
    "FACTORY_PAPER_WINDOW_VENUE_SCOPE": "betfair,binance,polymarket,yahoo,alpaca",
}

# Apply overrides. os.environ takes precedence over .env files in config.py.
# Only set if not already explicitly overridden by the caller's shell.
for key, val in _PAPER_WINDOW_OVERRIDES.items():
    os.environ.setdefault(key, val)

# Now load .env (for credentials) and import config
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env.staging", override=False)
except ImportError:
    pass

import config  # noqa: E402
from factory.orchestrator import FactoryOrchestrator  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _write_operator_status(state: dict) -> None:
    """Write operator status snapshot for monitoring."""
    try:
        from factory.telemetry.correlation import build_operator_status
        from factory.runtime.runtime_manager import RuntimeManager
        from factory.config.staging_guards import load_staging_guards

        status_path = Path(getattr(config, "FACTORY_OPERATOR_STATUS_PATH",
                                    "artifacts/operator_status.json"))
        if not status_path.is_absolute():
            status_path = PROJECT_ROOT / status_path
        status_path.parent.mkdir(parents=True, exist_ok=True)

        guards = load_staging_guards()
        status_dict = {
            "as_of": _utc_now(),
            "window_mode": "autonomous_paper",
            "cycle_count": state.get("cycle_count"),
            "factory_status": state.get("status"),
            "families": len(state.get("families") or []),
            "lineages": len(state.get("lineages") or []),
            "readiness": state.get("readiness"),
            "staging_guards": guards.to_dict(),
        }
        status_path.write_text(json.dumps(status_dict, indent=2, default=str))
    except Exception as exc:
        print(f"[paper-window] operator status write failed: {exc}", flush=True)


def _ensure_dashboard_running(project_root: Path) -> None:
    """Start the dashboard server if not already running, then open browser."""
    import subprocess
    import webbrowser

    port = int(getattr(config, "FACTORY_DASHBOARD_PORT", 8787) or 8787)
    pid_path = project_root / "data" / "factory" / "dashboard.pid"

    # Check if already running via PID file
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            os.kill(existing_pid, 0)  # Check if process exists
            print(f"[paper-window] Dashboard already running (PID {existing_pid}, port {port})", flush=True)
            webbrowser.open(f"http://localhost:{port}")
            return
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # Stale PID file, proceed to start

    # Start dashboard server as background process
    dashboard_script = project_root / "factory" / "operator_dashboard.py"
    if not dashboard_script.exists():
        print("[paper-window] Dashboard script not found, skipping auto-launch", flush=True)
        return

    log_path = project_root / "data" / "factory" / f"dashboard_{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_path, "a") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, str(dashboard_script), "--port", str(port)],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(project_root),
                start_new_session=True,
            )
        pid_path.write_text(str(proc.pid))
        print(f"[paper-window] Dashboard started (PID {proc.pid}, port {port})", flush=True)

        # Brief pause to let server bind, then open browser
        time.sleep(2)
        webbrowser.open(f"http://localhost:{port}")
    except Exception as exc:
        print(f"[paper-window] Dashboard auto-launch failed: {exc}", flush=True)


def _safety_preflight() -> list[str]:
    """Run pre-flight safety checks. Return list of blockers (empty = safe)."""
    blockers: list[str] = []

    # Live trading must be blocked
    if getattr(config, "FACTORY_ENABLE_LIVE_TRADING", False):
        blockers.append("FACTORY_ENABLE_LIVE_TRADING is true — live trading NOT blocked")
    if not getattr(config, "FACTORY_LIVE_TRADING_HARD_DISABLE", True):
        blockers.append("FACTORY_LIVE_TRADING_HARD_DISABLE is false — hard guard removed")

    # Paper trading must be enabled
    if not getattr(config, "FACTORY_ENABLE_PAPER_TRADING", False):
        blockers.append("FACTORY_ENABLE_PAPER_TRADING is false")

    # Autonomous paper promotion must be enabled
    if not getattr(config, "FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION", False):
        blockers.append("FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION is false")

    # Goldfish must be enabled
    if not getattr(config, "FACTORY_ENABLE_GOLDFISH_PROVENANCE", True):
        blockers.append("FACTORY_ENABLE_GOLDFISH_PROVENANCE is false")

    # Strict budgets should be on
    strict = getattr(config, "FACTORY_STRICT_BUDGETS", False) or \
             getattr(config, "FACTORY_ENABLE_STRICT_BUDGETS", False)
    if not strict:
        blockers.append("FACTORY_STRICT_BUDGETS is false (observe-only mode)")

    # Mobkit runtime
    if getattr(config, "FACTORY_RUNTIME_BACKEND", "legacy") != "mobkit":
        blockers.append(f"FACTORY_RUNTIME_BACKEND={config.FACTORY_RUNTIME_BACKEND} (expected mobkit)")

    # OpenAI API key present
    if not os.getenv("OPENAI_API_KEY"):
        blockers.append("OPENAI_API_KEY not set")

    return blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a supervised autonomous paper-learning window."
    )
    parser.add_argument(
        "--max-cycles", type=int, default=5,
        help="Maximum factory cycles to run. Default: 5"
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Seconds between cycles. Default: 60"
    )
    parser.add_argument(
        "--log-path", default="data/factory/paper_window.log",
        help="JSONL log path"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preflight checks only — do not run any cycles"
    )
    parser.add_argument(
        "--binance-only", action="store_true",
        help="Restrict to Binance-only scope (overrides multi-venue default)"
    )
    args = parser.parse_args(argv)

    # Apply venue override before importing config (already set above, but allow flag override)
    if args.binance_only:
        os.environ["FACTORY_PAPER_WINDOW_VENUE_SCOPE"] = "binance"
        os.environ["FACTORY_MAX_ACTIVE_FAMILIES"] = "2"
        os.environ["FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES"] = "3"

    print("=" * 60, flush=True)
    print("AUTONOMOUS PAPER-LEARNING WINDOW", flush=True)
    print(f"  started: {_utc_now()}", flush=True)
    print(f"  max_cycles: {args.max_cycles}", flush=True)
    print(f"  interval: {args.interval}s", flush=True)
    print(f"  dry_run: {args.dry_run}", flush=True)
    print("=" * 60, flush=True)

    # ---- Safety preflight ----
    blockers = _safety_preflight()
    if blockers:
        print("\n[PREFLIGHT FAILED] Cannot start paper window:", flush=True)
        for b in blockers:
            print(f"  - {b}", flush=True)
        return 1

    print("\n[PREFLIGHT PASSED]", flush=True)
    print(f"  paper_trading: {config.FACTORY_ENABLE_PAPER_TRADING}", flush=True)
    print(f"  autonomous_paper: {config.FACTORY_ALLOW_AUTONOMOUS_PAPER_PROMOTION}", flush=True)
    print(f"  live_blocked: {config.FACTORY_LIVE_TRADING_HARD_DISABLE}", flush=True)
    print(f"  strict_budgets: {getattr(config, 'FACTORY_STRICT_BUDGETS', False)}", flush=True)
    print(f"  runtime: {config.FACTORY_RUNTIME_BACKEND}", flush=True)
    print(f"  goldfish: {getattr(config, 'FACTORY_ENABLE_GOLDFISH_PROVENANCE', True)}", flush=True)
    print(f"  families_cap: {config.FACTORY_MAX_ACTIVE_FAMILIES}", flush=True)
    print(f"  challengers_cap: {config.FACTORY_MAX_CHALLENGERS_PER_FAMILY}", flush=True)
    print(f"  paper_lanes: {config.FACTORY_RUNTIME_MAX_ACTIVE_PAPER_LANES}", flush=True)
    print(f"  stake_fraction: {config.STAKE_FRACTION}", flush=True)
    print(f"  initial_balance: {config.INITIAL_BALANCE_EUR}", flush=True)
    print(f"  venue_scope: {getattr(config, 'FACTORY_PAPER_WINDOW_VENUE_SCOPE', '(all)') or '(all)'}", flush=True)

    if args.dry_run:
        print("\n[DRY RUN] Preflight passed. Exiting without running cycles.", flush=True)
        return 0

    # ---- Auto-launch dashboard ----
    _ensure_dashboard_running(PROJECT_ROOT)

    # ---- Initialize orchestrator ----
    orchestrator = FactoryOrchestrator(PROJECT_ROOT)
    log_path = PROJECT_ROOT / args.log_path
    pause_flag = PROJECT_ROOT / "data" / "factory" / "factory_paused.flag"

    # ---- Graceful shutdown ----
    _stop_requested = False

    def _handle_signal(signum, frame):
        nonlocal _stop_requested
        print(f"\n[paper-window] Signal {signum} received — stopping after current cycle.", flush=True)
        _stop_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ---- Run cycles ----
    window_started = _utc_now()
    cycles_completed = 0

    for cycle_num in range(1, args.max_cycles + 1):
        if _stop_requested:
            print("[paper-window] Stop requested. Exiting.", flush=True)
            break

        if pause_flag.exists():
            print("[paper-window] Paused by operator flag. Exiting.", flush=True)
            break

        t0 = time.time()
        cycle_ts = _utc_now()
        print(f"\n[paper-window] Cycle {cycle_num}/{args.max_cycles} started at {cycle_ts}", flush=True)

        try:
            state = orchestrator.run_cycle()
            research = dict(state.get("research_summary") or {})
            payload = {
                "ts": cycle_ts,
                "window": "autonomous_paper",
                "cycle": cycle_num,
                "status": "ok",
                "cycle_count": state.get("cycle_count"),
                "factory_status": state.get("status"),
                "readiness": dict(state.get("readiness") or {}).get("status"),
                "families": len(state.get("families") or []),
                "lineages": len(state.get("lineages") or []),
                "positives": research.get("positive_model_count"),
                "escalations": research.get("operator_escalation_count"),
                "duration_s": round(time.time() - t0, 1),
            }
            _write_operator_status(state)
            print(
                f"  status={payload['factory_status']} families={payload['families']} "
                f"lineages={payload['lineages']} positives={payload['positives']} "
                f"duration={payload['duration_s']}s",
                flush=True,
            )
        except Exception as exc:
            payload = {
                "ts": cycle_ts,
                "window": "autonomous_paper",
                "cycle": cycle_num,
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
                "duration_s": round(time.time() - t0, 1),
            }
            print(f"  ERROR: {exc}", flush=True)

        _append_log(log_path, payload)
        cycles_completed += 1

        if cycle_num < args.max_cycles and not _stop_requested:
            time.sleep(args.interval)

    # ---- Window summary ----
    print("\n" + "=" * 60, flush=True)
    print("PAPER WINDOW COMPLETE", flush=True)
    print(f"  started: {window_started}", flush=True)
    print(f"  finished: {_utc_now()}", flush=True)
    print(f"  cycles: {cycles_completed}/{args.max_cycles}", flush=True)
    print(f"  log: {log_path}", flush=True)
    print("=" * 60, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
