# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
