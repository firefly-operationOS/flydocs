# Copyright 2026 Firefly Software Solutions Inc
"""``WebhookPublisher`` -- HTTP POST with HMAC signing and retry/backoff.

Every attempt is logged through
:func:`flydocs.core.observability.log_outbound` so the operator can
audit every outbound delivery: URL, status, latency, attempt number,
final outcome. The publisher signs the body with HMAC-SHA256 when a
secret is configured, and propagates any ``extra_headers`` supplied by
the caller (the worker uses this to forward correlation IDs).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from flydocs.core.observability import log_outbound
from flydocs.interfaces.dtos.webhook import JobWebhookPayload

logger = logging.getLogger(__name__)


class WebhookPublisher:
    def __init__(
        self,
        *,
        timeout_s: int = 15,
        max_attempts: int = 5,
        hmac_secret: str | None = None,
        signature_header: str = "X-Flydocs-Signature",
        signature_scheme: str = "sha256",
    ) -> None:
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)
        self._hmac_secret = hmac_secret.encode("utf-8") if hmac_secret else None
        self._signature_header = signature_header
        self._signature_scheme = signature_scheme

    async def deliver(
        self,
        url: str,
        payload: JobWebhookPayload,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bool:
        """Return True on success, False on permanent failure (after retries).

        Extra headers (typically the inbound X-Correlation-Id /
        X-Request-Id / X-Tenant-Id / traceparent / tracestate the caller
        supplied at submit time) are merged onto every outbound POST so
        the downstream webhook receiver can correlate the delivery with
        the original HTTP request.
        """
        body = payload.model_dump_json(by_alias=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "flydocs/0.1.0",
        }
        if extra_headers:
            for name, value in extra_headers.items():
                if not value:
                    continue
                # Caller-supplied headers can't stomp on the publisher's own.
                if name.lower() in ("content-type", "user-agent"):
                    continue
                headers[name] = value
        if self._hmac_secret is not None:
            digest = hmac.new(self._hmac_secret, body, hashlib.sha256).hexdigest()
            headers[self._signature_header] = f"{self._signature_scheme}={digest}"

        attempt_counter = {"n": 0}

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=1, max=30),
            retry=retry_if_exception_type(_RetryableWebhook),
        )
        async def _do_post() -> bool:
            attempt_counter["n"] += 1
            attempt = attempt_counter["n"]
            started = time.monotonic()
            correlation_id = headers.get("X-Correlation-Id", "")
            try:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(url, content=body, headers=headers)
                latency_ms = (time.monotonic() - started) * 1000
            except httpx.RequestError as exc:
                latency_ms = (time.monotonic() - started) * 1000
                log_outbound(
                    "webhook",
                    op="deliver",
                    status="error",
                    latency_ms=latency_ms,
                    url=url,
                    attempt=attempt,
                    job_id=payload.job_id,
                    correlation_id=correlation_id,
                    error=type(exc).__name__,
                )
                raise

            http_status = response.status_code
            if 500 <= http_status < 600 or http_status == 429:
                log_outbound(
                    "webhook",
                    op="deliver",
                    status="retry",
                    latency_ms=latency_ms,
                    url=url,
                    attempt=attempt,
                    http_status=http_status,
                    job_id=payload.job_id,
                    correlation_id=correlation_id,
                )
                raise _RetryableWebhook(f"webhook {url} returned retryable status {http_status}")
            if http_status >= 400:
                log_outbound(
                    "webhook",
                    op="deliver",
                    status="permanent_failure",
                    latency_ms=latency_ms,
                    url=url,
                    attempt=attempt,
                    http_status=http_status,
                    job_id=payload.job_id,
                    correlation_id=correlation_id,
                )
                logger.error(
                    "Webhook %s returned non-retryable %d: %s",
                    url,
                    http_status,
                    response.text[:500],
                )
                return False
            log_outbound(
                "webhook",
                op="deliver",
                status="ok",
                latency_ms=latency_ms,
                url=url,
                attempt=attempt,
                http_status=http_status,
                job_id=payload.job_id,
                correlation_id=correlation_id,
            )
            return True

        try:
            return await _do_post()
        except RetryError as exc:
            log_outbound(
                "webhook",
                op="deliver",
                status="exhausted",
                latency_ms=0.0,
                url=url,
                attempts=attempt_counter["n"],
                job_id=payload.job_id,
                error=type(exc).__name__,
            )
            logger.error("Webhook %s exhausted retries: %s", url, exc)
            return False
        except httpx.RequestError as exc:
            log_outbound(
                "webhook",
                op="deliver",
                status="transport_error",
                latency_ms=0.0,
                url=url,
                attempts=attempt_counter["n"],
                job_id=payload.job_id,
                error=type(exc).__name__,
            )
            logger.error("Webhook %s transport error: %s", url, exc)
            return False


class _RetryableWebhook(RuntimeError):
    """Raised internally to drive tenacity's retry policy."""
