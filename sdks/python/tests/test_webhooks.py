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

"""Tests for the HMAC webhook verifier.

Covers the three failure modes the SDK promises to detect: missing
signature header, wrong scheme prefix, and digest mismatch. Plus a
roundtrip with :meth:`WebhookVerifier.sign` so the two helpers can't
drift out of sync.
"""

from __future__ import annotations

import pytest

from flydocs_sdk import WebhookVerificationError, WebhookVerifier


def test_sign_and_verify_roundtrip() -> None:
    verifier = WebhookVerifier("topsecret")
    body = b'{"event_id":"abc","job_id":"job1","status":"SUCCEEDED"}'
    sig = verifier.sign(body)
    assert sig.startswith("sha256=")
    # round-trips with the scheme prefix
    verified = verifier.verify(body, sig)
    assert verified is body
    # and also accepts a bare hex digest (some proxies strip the scheme)
    bare = sig.split("=", 1)[1]
    assert verifier.verify(body, bare) is body


def test_verify_missing_header_raises() -> None:
    verifier = WebhookVerifier("topsecret")
    with pytest.raises(WebhookVerificationError, match="signature header missing"):
        verifier.verify(b"{}", "")


def test_verify_bad_scheme_raises() -> None:
    verifier = WebhookVerifier("topsecret")
    with pytest.raises(WebhookVerificationError, match="unsupported signature scheme"):
        verifier.verify(b"{}", "md5=deadbeef")


def test_verify_digest_mismatch_raises() -> None:
    verifier = WebhookVerifier("topsecret")
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        verifier.verify(b"{}", "sha256=" + "00" * 32)


def test_empty_secret_rejected() -> None:
    with pytest.raises(ValueError):
        WebhookVerifier("")


def test_signing_is_deterministic() -> None:
    # Two verifiers with the same secret must produce the same digest
    # for the same body. Acts as a regression for any future change to
    # ``sign``.
    a = WebhookVerifier("s")
    b = WebhookVerifier("s")
    assert a.sign(b"hello") == b.sign(b"hello")
