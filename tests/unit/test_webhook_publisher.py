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

"""Black-box tests for :class:`WebhookPublisher`.

Spins up a tiny in-process HTTP server, fires a real webhook through
the real publisher, and asserts on the body, the HMAC signature, and
any extra correlation headers we propagate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from flydocs.core.services.webhook import WebhookPublisher
from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EventEnvelope,
)
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.interfaces.enums.extraction_status import ExtractionStatus

# ---------------------------------------------------------------------------
# In-process webhook receiver
# ---------------------------------------------------------------------------


class _Capture:
    """Thread-safe holder for the most-recent inbound request."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.body: bytes = b""
        self.path: str = ""
        self.response_code: int = 200
        self.responses_for_next_calls: list[int] = []
        self._lock = threading.Lock()
        self.received_count = 0

    def push_response(self, code: int) -> None:
        with self._lock:
            self.responses_for_next_calls.append(code)

    def next_code(self) -> int:
        with self._lock:
            if self.responses_for_next_calls:
                return self.responses_for_next_calls.pop(0)
            return self.response_code


def _make_handler(capture: _Capture):
    class _H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b""
            with capture._lock:
                capture.headers = {k: v for k, v in self.headers.items()}
                capture.body = body
                capture.path = self.path
                capture.received_count += 1
            code = capture.next_code()
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args, **kwargs) -> None:  # noqa: A003
            return  # silence the BaseHTTPServer default logger

    return _H


@pytest.fixture
def receiver():
    """Yield (url, capture, stop). Stops the thread on test teardown."""
    capture = _Capture()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = HTTPServer(("127.0.0.1", port), _make_handler(capture))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/webhook", capture
    finally:
        server.shutdown()
        server.server_close()


def _payload() -> EventEnvelope:
    return EventEnvelope(
        event_type=EVENT_TYPE_EXTRACTION_COMPLETED,
        occurred_at=datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
        extraction=Extraction(
            id="ext_TEST00000000000000000000000",
            status=ExtractionStatus.SUCCEEDED,
            submitted_at=datetime(2026, 5, 14, 11, 59, 0, tzinfo=UTC),
        ),
        metadata={"tenant_id": "acme"},
        result=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_delivers_and_signs(receiver) -> None:
    """End-to-end: real HTTP POST + verify HMAC signature on the body."""
    url, capture = receiver
    pub = WebhookPublisher(timeout_s=5, max_attempts=2, hmac_secret="s3cret")

    ok = await pub.deliver(url, _payload())

    assert ok is True
    assert capture.received_count == 1
    assert capture.path == "/webhook"
    # The body is the JSON-encoded payload.
    body = json.loads(capture.body.decode("utf-8"))
    assert body["event_type"] == "extraction.completed"
    assert body["extraction"]["id"] == "ext_TEST00000000000000000000000"
    assert body["extraction"]["status"] == "succeeded"
    # The signature header carries an HMAC-SHA256 of the body.
    sig = capture.headers.get("X-Flydocs-Signature", "")
    assert sig.startswith("sha256=")
    expected = hmac.new(b"s3cret", capture.body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


@pytest.mark.asyncio
async def test_webhook_propagates_extra_headers(receiver) -> None:
    """Correlation headers from the original request reach the receiver."""
    url, capture = receiver
    pub = WebhookPublisher(timeout_s=5, max_attempts=2, hmac_secret=None)

    extra = {
        "X-Correlation-Id": "corr-123",
        "X-Request-Id": "req-456",
        "X-Tenant-Id": "acme",
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    }
    ok = await pub.deliver(url, _payload(), extra_headers=extra)

    assert ok is True
    assert capture.headers.get("X-Correlation-Id") == "corr-123"
    assert capture.headers.get("X-Request-Id") == "req-456"
    assert capture.headers.get("X-Tenant-Id") == "acme"
    assert capture.headers.get("traceparent") == extra["traceparent"]


@pytest.mark.asyncio
async def test_webhook_does_not_overwrite_content_type(receiver) -> None:
    """Caller-supplied Content-Type is ignored -- the publisher owns it."""
    url, capture = receiver
    pub = WebhookPublisher(timeout_s=5, max_attempts=2)

    await pub.deliver(
        url,
        _payload(),
        extra_headers={"content-type": "text/plain", "User-Agent": "evil"},
    )
    assert capture.headers.get("Content-Type") == "application/json"
    assert capture.headers.get("User-Agent") == "flydocs/26.5.1"


@pytest.mark.asyncio
async def test_webhook_retries_on_5xx(receiver) -> None:
    """A 503 should trigger a retry; a follow-up 200 should succeed."""
    url, capture = receiver
    capture.push_response(503)  # first attempt
    capture.push_response(200)  # second attempt
    pub = WebhookPublisher(timeout_s=5, max_attempts=3)

    ok = await pub.deliver(url, _payload())

    assert ok is True
    assert capture.received_count == 2


@pytest.mark.asyncio
async def test_webhook_gives_up_on_4xx(receiver) -> None:
    """A 400 is permanent; the publisher returns False without retrying."""
    url, capture = receiver
    capture.push_response(400)
    pub = WebhookPublisher(timeout_s=5, max_attempts=3)

    ok = await pub.deliver(url, _payload())

    assert ok is False
    assert capture.received_count == 1
