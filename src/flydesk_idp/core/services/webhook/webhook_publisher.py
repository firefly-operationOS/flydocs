# Copyright 2026 Firefly Software Solutions Inc
"""``WebhookPublisher`` -- HTTP POST with HMAC signing and retry/backoff."""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from flydesk_idp.interfaces.dtos.webhook import JobWebhookPayload

logger = logging.getLogger(__name__)


class WebhookPublisher:
    def __init__(
        self,
        *,
        timeout_s: int = 15,
        max_attempts: int = 5,
        hmac_secret: str | None = None,
        signature_header: str = "X-Flydesk-Signature",
        signature_scheme: str = "sha256",
    ) -> None:
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)
        self._hmac_secret = hmac_secret.encode("utf-8") if hmac_secret else None
        self._signature_header = signature_header
        self._signature_scheme = signature_scheme

    async def deliver(self, url: str, payload: JobWebhookPayload) -> bool:
        """Return True on success, False on permanent failure (after retries)."""
        body = payload.model_dump_json(by_alias=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "flydesk-idp/0.1.0",
        }
        if self._hmac_secret is not None:
            digest = hmac.new(self._hmac_secret, body, hashlib.sha256).hexdigest()
            headers[self._signature_header] = f"{self._signature_scheme}={digest}"

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=1, max=30),
            retry=retry_if_exception_type(_RetryableWebhook),
        )
        async def _do_post() -> bool:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(url, content=body, headers=headers)
                if 500 <= response.status_code < 600 or response.status_code == 429:
                    raise _RetryableWebhook(
                        f"webhook {url} returned retryable status {response.status_code}"
                    )
                if response.status_code >= 400:
                    logger.error(
                        "Webhook %s returned non-retryable %d: %s",
                        url,
                        response.status_code,
                        response.text[:500],
                    )
                    return False
                return True

        try:
            return await _do_post()
        except RetryError as exc:
            logger.error("Webhook %s exhausted retries: %s", url, exc)
            return False
        except httpx.RequestError as exc:
            logger.error("Webhook %s transport error: %s", url, exc)
            return False


class _RetryableWebhook(RuntimeError):
    """Raised internally to drive tenacity's retry policy."""
