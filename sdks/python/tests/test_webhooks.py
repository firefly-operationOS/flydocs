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

#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the v1 HMAC webhook verifier.

Pins three behaviours:

1. A correctly-signed body verifies and is parsed into a typed
   :class:`EventEnvelope`.
2. Common failure modes -- missing header, wrong scheme prefix, digest
   mismatch -- raise :class:`WebhookVerificationError`.
3. :meth:`WebhookVerifier.sign` produces the canonical
   ``sha256=<hex>`` form so callers can pin parity with the service.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from flydocs_sdk import (
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EventEnvelope,
    WebhookVerificationError,
    WebhookVerifier,
)

SECRET = "topsecret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _envelope_body() -> bytes:
    return json.dumps(
        {
            "event_id": "e1",
            "event_type": EVENT_TYPE_EXTRACTION_COMPLETED,
            "version": "1.0.0",
            "occurred_at": "2026-05-26T00:00:00Z",
            "extraction": {
                "id": "ext_1",
                "status": "succeeded",
                "submitted_at": "2026-05-26T00:00:00Z",
            },
        }
    ).encode()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_sign_and_verify_round_trip() -> None:
    body = _envelope_body()
    sig = WebhookVerifier(SECRET).sign(body)
    assert sig.startswith("sha256=")
    env = WebhookVerifier(SECRET).verify(body, sig)
    assert isinstance(env, EventEnvelope)
    assert env.event_type == EVENT_TYPE_EXTRACTION_COMPLETED
    assert env.extraction.id == "ext_1"


def test_verify_accepts_bare_hex() -> None:
    body = _envelope_body()
    sig = _sign(body)
    bare = sig.split("=", 1)[1]
    env = WebhookVerifier(SECRET).verify(body, bare)
    assert isinstance(env, EventEnvelope)


def test_sign_is_deterministic() -> None:
    body = _envelope_body()
    a = WebhookVerifier(SECRET).sign(body)
    b = WebhookVerifier(SECRET).sign(body)
    assert a == b


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_verify_missing_header_raises() -> None:
    with pytest.raises(WebhookVerificationError, match="signature header missing"):
        WebhookVerifier(SECRET).verify(_envelope_body(), "")


def test_verify_wrong_scheme_raises() -> None:
    with pytest.raises(WebhookVerificationError, match="unsupported signature scheme"):
        WebhookVerifier(SECRET).verify(_envelope_body(), "md5=deadbeef")


def test_verify_digest_mismatch_raises() -> None:
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        WebhookVerifier(SECRET).verify(_envelope_body(), "sha256=" + "00" * 32)


def test_verify_tampered_body_raises() -> None:
    body = _envelope_body()
    sig = _sign(body)
    tampered = body.replace(b'"succeeded"', b'"failed"   ')
    with pytest.raises(WebhookVerificationError):
        WebhookVerifier(SECRET).verify(tampered, sig)


def test_verify_wrong_secret_raises() -> None:
    body = _envelope_body()
    sig = _sign(body, secret="someoneelses")
    with pytest.raises(WebhookVerificationError):
        WebhookVerifier(SECRET).verify(body, sig)


# ---------------------------------------------------------------------------
# Verifier construction
# ---------------------------------------------------------------------------


def test_empty_secret_rejected() -> None:
    with pytest.raises(ValueError):
        WebhookVerifier("")
