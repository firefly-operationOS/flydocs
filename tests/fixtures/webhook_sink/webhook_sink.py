# Copyright 2026 Firefly Software Solutions Inc
"""Tiny HTTP server that captures every inbound POST so the integration
tests can assert webhook delivery."""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("WEBHOOK_SINK_DATA", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/captured":
            captured = [json.loads(p.read_text()) for p in sorted(DATA_DIR.glob("*.json"))]
            return self._json(200, captured)
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        record = {
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": body,
        }
        with _LOCK:
            (DATA_DIR / f"{uuid.uuid4()}.json").write_text(json.dumps(record))
        return self._json(200, {"received": True})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return  # quiet

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = HTTPServer(("0.0.0.0", 8600), _Handler)
    print("webhook-sink listening on :8600", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
