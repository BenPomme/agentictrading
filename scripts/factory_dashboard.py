#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import pathlib
import signal
import subprocess
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", override=True)
except ImportError:
    pass

import config  # noqa: E402
from factory.operator_dashboard import build_dashboard_snapshot, build_snapshot_v2  # noqa: E402

DASHBOARD_PID_PATH = project_root / "data" / "factory" / "dashboard.pid"
FACTORY_LOOP_PID_PATH = project_root / "data" / "factory" / "factory_loop.pid"
REFRESH_SCHEDULER_PID_PATH = project_root / "data" / "factory" / "data_refresh_scheduler.pid"


def _preferred_python() -> str:
    override = str(os.environ.get("FACTORY_REFRESH_PYTHON") or "").strip()
    if override:
        return override
    preferred = project_root / ".venv312" / "bin" / "python"
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


def _factory_paused_flag() -> Path:
    return project_root / "data" / "factory" / "factory_paused.flag"


def _control_state() -> dict:
    paused = _factory_paused_flag().exists()
    factory_running = _pid_running(FACTORY_LOOP_PID_PATH)
    scheduler_running = _pid_running(REFRESH_SCHEDULER_PID_PATH)
    dashboard_running = True
    system_running = factory_running and scheduler_running
    return {
        "factory_paused": paused,
        "factory_running": factory_running,
        "refresh_scheduler_running": scheduler_running,
        "dashboard_running": dashboard_running,
        "system_running": system_running,
    }


def _launch_factory_loop() -> bool:
    if _pid_running(FACTORY_LOOP_PID_PATH):
        return False
    loop_script = project_root / "scripts" / "factory_loop.py"
    if not loop_script.exists():
        raise RuntimeError(f"Factory loop script not found: {loop_script}")
    log_path = project_root / "data" / "factory" / "factory_loop.stdout.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python_executable = _preferred_python()
    with open(log_path, "a", encoding="utf-8") as log_fh:
        subprocess.Popen(
            [python_executable, str(loop_script)],
            cwd=str(project_root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    return True


def _terminate_pid(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    return True


def _apply_control_action(action: str) -> dict:
    action = str(action or "").strip().lower()
    flag_path = _factory_paused_flag()
    flag_path.parent.mkdir(parents=True, exist_ok=True)

    if action in {"resume", "start"}:
        flag_path.unlink(missing_ok=True)
        started = _launch_factory_loop()
        state = _control_state()
        state["action"] = "start"
        state["started_factory_loop"] = started
        return state

    if action in {"pause", "stop"}:
        flag_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        _terminate_pid(FACTORY_LOOP_PID_PATH)
        _terminate_pid(REFRESH_SCHEDULER_PID_PATH)
        state = _control_state()
        state["action"] = "stop"
        return state

    raise ValueError(f"Unsupported control action: {action}")


def _with_control_state(payload: dict) -> dict:
    payload = dict(payload)
    payload.update(_control_state())
    return payload


def _chart_execution_root() -> Path:
    explicit = str(getattr(config, "EXECUTION_PORTFOLIO_STATE_ROOT", "") or "").strip()
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = project_root / p
        return p
    return project_root / "data" / "portfolios"


def _build_chart_payload(portfolio_id: str) -> dict | None:
    root = _chart_execution_root()
    portfolio_dir = root / portfolio_id
    if not portfolio_dir.is_dir():
        return None

    account_path = portfolio_dir / "account.json"
    starting_balance = 0.0
    if account_path.exists():
        with open(account_path) as fh:
            account = json.loads(fh.read())
        starting_balance = float(account.get("starting_balance") or account.get("initial_balance") or 0)

    points: list[dict] = []
    balance_path = portfolio_dir / "balance_history.jsonl"
    if balance_path.exists():
        raw_points: list[dict] = []
        with open(balance_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw_points.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        max_points = 500
        if len(raw_points) > max_points:
            step = len(raw_points) / (max_points - 1)
            indices = {0, len(raw_points) - 1}
            i = 0.0
            while i < len(raw_points):
                indices.add(int(i))
                i += step
            raw_points = [raw_points[idx] for idx in sorted(indices)]
        for pt in raw_points:
            bal = float(pt.get("balance", 0))
            points.append({
                "ts": pt.get("ts", ""),
                "balance": round(bal, 2),
                "pnl": round(bal - starting_balance, 2),
            })

    trades: list[dict] = []
    trades_path = portfolio_dir / "trades.jsonl"
    if trades_path.exists():
        with open(trades_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                trade_id = row.get("trade_id") or row.get("id", "")
                symbol = row.get("symbol", "")
                side = row.get("side", "")
                status = str(row.get("status", "")).upper()
                opened_at = row.get("opened_at") or row.get("entry_time", "")
                closed_at = row.get("closed_at") or row.get("exit_time", "")
                raw_pnl = row.get("realized_pnl") or row.get("net_pnl_usd") or row.get("net_pnl")
                pnl = round(float(raw_pnl), 2) if raw_pnl is not None else None
                if opened_at:
                    trades.append({
                        "ts": opened_at,
                        "kind": "trade_opened",
                        "trade_id": str(trade_id),
                        "symbol": symbol,
                        "side": side,
                        "pnl": None,
                    })
                if closed_at and status not in ("OPEN", "PENDING"):
                    trades.append({
                        "ts": closed_at,
                        "kind": "trade_closed",
                        "trade_id": str(trade_id),
                        "symbol": symbol,
                        "side": side,
                        "pnl": pnl,
                        "status": status,
                    })
        trades.sort(key=lambda t: t.get("ts", ""))

    if not trades:
        events_path = portfolio_dir / "events.jsonl"
        if events_path.exists():
            with open(events_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = evt.get("kind", "")
                    if kind not in ("trade_opened", "trade_closed"):
                        continue
                    data = evt.get("data", {})
                    ts = data.get("closed_at") if kind == "trade_closed" else data.get("opened_at")
                    raw_pnl = data.get("net_pnl_usd") or data.get("realized_pnl")
                    pnl = round(float(raw_pnl), 2) if raw_pnl is not None else None
                    trades.append({
                        "ts": ts or "",
                        "kind": kind,
                        "trade_id": data.get("trade_id", ""),
                        "symbol": data.get("symbol", ""),
                        "side": data.get("side", ""),
                        "pnl": pnl,
                    })

    return {
        "portfolio_id": portfolio_id,
        "starting_balance": starting_balance,
        "points": points,
        "trades": trades,
    }


def _resolve_static_dir() -> Path:
    """Prefer React build (dashboard-ui/dist), fall back to legacy dashboard.

    DEPRECATED: The legacy `dashboard/` HTML frontend is kept only as a last-
    resort fallback when the React build is absent.  It is no longer maintained
    and does not support snapshot v2, the 8-zone navigation, or any feature
    added after 2026-03-18.  Run `npm run build` inside `dashboard-ui/` to
    produce a fresh React build and make this fallback path unreachable.
    """
    react_dist = project_root / "dashboard-ui" / "dist"
    if react_dist.is_dir() and (react_dist / "index.html").exists():
        return react_dist
    # LEGACY FALLBACK — deprecated, not maintained
    return project_root / "dashboard"


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        static_dir = _resolve_static_dir()
        super().__init__(*args, directory=str(static_dir if directory is None else directory), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            self._serve_snapshot()
            return
        if parsed.path == "/api/snapshot/v2":
            self._serve_snapshot_v2()
            return
        if parsed.path == "/api/healthz":
            self._serve_json({"ok": True}, status=HTTPStatus.OK)
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "portfolio" and parts[3] == "chart":
            self._serve_portfolio_chart(parts[2])
            return
        if parsed.path in {"/", ""}:
            self.path = "/index.html"
            super().do_GET()
            return
        # SPA fallback: if file not found, serve index.html
        static_dir = _resolve_static_dir()
        file_path = static_dir / parsed.path.lstrip("/")
        if not file_path.exists() and not parsed.path.startswith("/api"):
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[factory-dashboard] {format % args}\n")

    def _serve_snapshot(self) -> None:
        payload = _with_control_state(build_dashboard_snapshot())
        self._serve_json(payload, status=HTTPStatus.OK)

    def _serve_snapshot_v2(self) -> None:
        payload = _with_control_state(build_snapshot_v2())
        self._serve_json(payload, status=HTTPStatus.OK)

    def _serve_portfolio_chart(self, portfolio_id: str) -> None:
        payload = _build_chart_payload(portfolio_id)
        if payload is None:
            self._serve_json({"error": "portfolio not found"}, status=HTTPStatus.NOT_FOUND)
            return
        self._serve_json(payload, status=HTTPStatus.OK)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT.value)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/factory/control":
            import json as _json

            length = int(self.headers.get("Content-Length", 0))
            body = _json.loads(self.rfile.read(length)) if length else {}
            action = body.get("action", "")
            try:
                payload = _apply_control_action(action)
            except ValueError as exc:
                self._serve_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._serve_json(payload)
        else:
            self.send_error(404)

    def _serve_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the NEBULA Control Room dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    DASHBOARD_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    def _cleanup_pid() -> None:
        try:
            if DASHBOARD_PID_PATH.exists():
                DASHBOARD_PID_PATH.unlink()
        except OSError:
            pass

    atexit.register(_cleanup_pid)
    print(f"http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
