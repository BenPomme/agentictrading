#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
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
from factory.operator_dashboard import build_dashboard_snapshot  # noqa: E402


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
    """Prefer React build (dashboard-ui/dist), fall back to legacy dashboard."""
    react_dist = project_root / "dashboard-ui" / "dist"
    if react_dist.is_dir() and (react_dist / "index.html").exists():
        return react_dist
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
        payload = build_dashboard_snapshot()
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
            flag_path = pathlib.Path(project_root) / "data" / "factory" / "factory_paused.flag"
            action = body.get("action", "")
            if action == "pause":
                flag_path.parent.mkdir(parents=True, exist_ok=True)
                flag_path.write_text(datetime.utcnow().isoformat())
            elif action == "resume":
                flag_path.unlink(missing_ok=True)
            running = not flag_path.exists()
            self._serve_json({"factory_running": running})
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
