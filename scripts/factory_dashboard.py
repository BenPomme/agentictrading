#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
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

from factory.operator_dashboard import build_dashboard_snapshot_light  # noqa: E402


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        static_dir = project_root / "dashboard"
        super().__init__(*args, directory=str(static_dir if directory is None else directory), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            self._serve_snapshot()
            return
        if parsed.path == "/api/healthz":
            self._serve_json({"ok": True}, status=HTTPStatus.OK)
            return
        if parsed.path in {"/", ""}:
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[factory-dashboard] {format % args}\n")

    def _serve_snapshot(self) -> None:
        payload = build_dashboard_snapshot_light()
        self._serve_json(payload, status=HTTPStatus.OK)

    def _serve_json(self, payload: dict, *, status: HTTPStatus) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the AgenticTrading operator dashboard.")
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
