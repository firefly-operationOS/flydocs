# Copyright 2026 Firefly Software Solutions Inc
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

"""Helpers for verifying outbound flydocs webhooks.

The service signs every outbound webhook body with HMAC-SHA256 using
the secret configured via ``FLYDOCS_WEBHOOK_HMAC_SECRET``. The
signature is delivered in the ``X-Flydocs-Signature`` header with the
literal form ``sha256=<hex-digest>``.

Verification rules implemented here:

* Constant-time comparison (``hmac.compare_digest``) to avoid timing
  side-channels.
* Scheme prefix is optional; ``WebhookVerifier`` accepts both
  ``sha256=<hex>`` and a bare ``<hex>``.
* The raw request body must be passed in unchanged -- decoding /
  re-encoding the JSON before verifying will change the bytes and
  break the signature.
* On success, the body is parsed into a typed :class:`EventEnvelope`.
"""

from __future__ import annotations

import hashlib
import hmac

from flydocs_sdk.errors import FlydocsError
from flydocs_sdk.models import EventEnvelope


class WebhookVerificationError(FlydocsError):
    """Raised when a webhook signature does not match the body."""


class WebhookVerifier:
    """Verify ``X-Flydocs-Signature`` HMACs and parse the body.

        verifier = WebhookVerifier(secret="...")
        try:
            envelope = verifier.verify(raw_body, signature_header)
        except WebhookVerificationError:
            return 403

    The argument to ``verify`` is the raw bytes the service sent. If
    your web framework already deserialised the JSON, re-encoding it
    will change the digest -- ask the framework for the original body
    bytes instead.
    """

    def __init__(self, secret: str, *, header_scheme: str = "sha256") -> None:
        if not secret:
            raise ValueError("HMAC secret cannot be empty")
        self._secret = secret.encode("utf-8")
        self._scheme = header_scheme

    def sign(self, body: bytes) -> str:
        """Compute the ``X-Flydocs-Signature`` value for ``body``.

        Returned in the canonical ``sha256=<hex>`` form. Useful for
        tests and for asserting parity with the service.
        """
        digest = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return f"{self._scheme}={digest}"

    def verify(self, body: bytes, signature_header: str) -> EventEnvelope:
        """Verify the signature and parse the body into an :class:`EventEnvelope`.

        Both ``sha256=<hex>`` and a bare ``<hex>`` are accepted, since
        some intermediate proxies strip the scheme prefix.

        Raises :class:`WebhookVerificationError` on signature mismatch,
        missing header, or unsupported scheme.
        """
        if not signature_header:
            raise WebhookVerificationError("signature header missing")
        provided = signature_header.strip()
        if "=" in provided:
            scheme, _, candidate = provided.partition("=")
            if scheme.lower() != self._scheme.lower():
                raise WebhookVerificationError(f"unsupported signature scheme: {scheme!r}")
        else:
            candidate = provided
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(candidate, expected):
            raise WebhookVerificationError("signature mismatch")
        return EventEnvelope.model_validate_json(body)


__all__ = ["WebhookVerificationError", "WebhookVerifier"]
