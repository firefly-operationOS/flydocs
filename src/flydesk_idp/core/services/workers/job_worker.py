# Copyright 2026 Firefly Software Solutions Inc
"""``JobWorker`` -- consumes the queue and dispatches into the pipeline.

The worker is built around dependencies injected by the pyfly DI
container (orchestrator, queue, repository, webhook, settings). It is
NOT a stereotyped bean because we want the CLI to start it explicitly:
``flydesk-idp worker`` boots a minimal pyfly application and pulls the
worker out of the context.

Retry policy
============

A failed attempt is classified into one of two buckets:

* ``permanent`` -- a malformed payload, an unrecoverable provider error
  (content policy, unsupported model). The job goes straight to
  ``FAILED`` so the caller can fix the input.
* ``retryable`` -- a timeout, a 5xx from the LLM provider, a transient
  network glitch. The worker schedules the next attempt at
  ``min(retry_max_delay_s, retry_base_delay_s * 2^(attempt-1))`` plus
  jitter and lets the queue redeliver. The ``attempts`` counter is
  persisted in Postgres so the budget survives worker restarts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
from datetime import datetime, timezone
from typing import Any

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.pipeline import PipelineOrchestrator
from flydesk_idp.core.services.queue import JobQueue, JobQueueMessage
from flydesk_idp.core.services.webhook import WebhookPublisher
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
)
from flydesk_idp.interfaces.dtos.rule import RuleSpec
from flydesk_idp.interfaces.dtos.webhook import JobWebhookPayload
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


# Substrings that flag a provider response as non-retryable. They cover
# the common content-policy / quota / invalid-model classes across the
# Anthropic and OpenAI SDKs without trying to be exhaustive.
_PERMANENT_ERROR_HINTS: tuple[str, ...] = (
    "content policy",
    "content_filter",
    "moderation",
    "invalid api key",
    "incorrect api key",
    "unsupported model",
    "model_not_found",
    "input_validation_error",
    "invalid_request_error",
)


def _is_permanent(exc: Exception) -> bool:
    """Return True when *exc* should NOT be retried."""
    # ValueError covers our RequestValidator failures and most pydantic
    # validation errors that bubble up through the orchestrator.
    if isinstance(exc, (ValueError, TypeError)):
        return True
    message = str(exc).lower()
    return any(hint in message for hint in _PERMANENT_ERROR_HINTS)


class JobWorker:
    def __init__(
        self,
        *,
        orchestrator: PipelineOrchestrator,
        repository: ExtractionJobRepository,
        queue: JobQueue,
        webhook: WebhookPublisher,
        settings: IDPSettings,
        consumer_id: str | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._queue = queue
        self._webhook = webhook
        self._settings = settings
        self._consumer_id = consumer_id or f"worker-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        await self._queue.start()
        logger.info(
            "JobWorker %s started (adapter=%s)", self._consumer_id, self._settings.eda_adapter
        )
        try:
            async for message in self._queue.consume(self._consumer_id):
                if self._stop.is_set():
                    break
                await self._process(message)
        finally:
            await self._queue.stop()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def _process(self, message: JobQueueMessage) -> None:
        job = await self._repository.get(message.job_id)
        if job is None:
            logger.warning(
                "Queue delivered unknown job %s -- acking and skipping", message.job_id
            )
            await self._queue.ack(message)
            return
        if JobStatus(job.status) in (JobStatus.SUCCEEDED, JobStatus.CANCELLED):
            logger.info("Job %s already in terminal status %s -- skipping", job.id, job.status)
            await self._queue.ack(message)
            return

        # mark_running increments attempts atomically in Postgres.
        job = await self._repository.mark_running(job.id) or job
        attempts = job.attempts or 1
        log_outbound(
            "worker", op="job.run", status="started",
            latency_ms=0.0, job_id=job.id, attempt=attempts,
        )
        request = self._build_request(job)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._orchestrator.execute(request), timeout=self._settings.async_timeout_s
            )
            await self._repository.mark_succeeded(
                job.id, result=result.model_dump(mode="json", by_alias=True)
            )
            log_outbound(
                "worker", op="job.run", status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id, attempt=attempts,
            )
            await self._fire_webhook(
                job_id=job.id,
                status=JobStatus.SUCCEEDED,
                result=result,
                metadata=job.metadata_json or {},
                callback_url=job.callback_url,
                correlation=_extract_correlation(job.metadata_json),
            )
        except Exception as exc:  # noqa: BLE001
            permanent = _is_permanent(exc)
            exhausted = attempts >= self._settings.job_max_attempts
            terminal = permanent or exhausted
            error_code = "PERMANENT_ERROR" if permanent else "EXTRACTION_FAILED"
            log_outbound(
                "worker", op="job.run", status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id, attempt=attempts,
                permanent=permanent, exhausted=exhausted,
                error=type(exc).__name__,
            )

            if terminal:
                await self._repository.mark_failed(job.id, code=error_code, message=str(exc))
                await self._fire_webhook(
                    job_id=job.id,
                    status=JobStatus.FAILED,
                    result=None,
                    metadata=job.metadata_json or {},
                    callback_url=job.callback_url,
                    error_code=error_code,
                    error_message=str(exc),
                    correlation=_extract_correlation(job.metadata_json),
                )
            else:
                delay = self._backoff_delay(attempts)
                logger.warning(
                    "Job %s failed attempt %d (%s); re-queueing in %.1fs",
                    job.id, attempts, exc, delay,
                )
                # Mark QUEUED again so the API surface reflects the retry,
                # then schedule the re-publish without blocking the worker
                # (the consumer keeps draining the stream meanwhile).
                await self._repository.update(job.id, status=JobStatus.QUEUED.value)
                asyncio.create_task(self._delayed_publish(job.id, delay))
        finally:
            await self._queue.ack(message)

    def _backoff_delay(self, attempts: int) -> float:
        """Capped exponential backoff with a 20% jitter."""
        base = self._settings.retry_base_delay_s
        ceiling = self._settings.retry_max_delay_s
        raw = base * (2 ** max(0, attempts - 1))
        capped = min(ceiling, raw)
        jitter = capped * 0.2 * random.random()
        return capped + jitter

    async def _delayed_publish(self, job_id: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            await self._queue.publish(job_id)
            log_outbound(
                "queue", op="republish", status="ok",
                latency_ms=delay_s * 1000, job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to re-publish job %s after backoff: %s", job_id, exc)

    def _build_request(self, job: Any) -> ExtractionRequest:
        schema = job.schema_json or {}
        return ExtractionRequest(
            intention=schema.get("intention", "Extract structured data from the document."),
            document=DocumentInput(
                filename=job.filename,
                content_base64=schema.get("document_content_base64", ""),
                content_type=schema.get("document_content_type"),
            ),
            docs=[DocSpec.model_validate(d) for d in schema.get("docs", [])],
            rules=[RuleSpec.model_validate(r) for r in schema.get("rules", [])],
            options=ExtractionOptions.model_validate(job.options_json or {}),
        )

    async def _fire_webhook(
        self,
        *,
        job_id: str,
        status: JobStatus,
        result: ExtractionResult | None,
        metadata: dict[str, Any],
        callback_url: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        correlation: dict[str, str] | None = None,
    ) -> None:
        if not callback_url:
            return
        clean_metadata = {k: v for k, v in (metadata or {}).items() if not k.startswith("_")}
        payload = JobWebhookPayload(
            job_id=job_id,
            status=status,
            occurred_at=datetime.now(timezone.utc),
            metadata=clean_metadata,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )
        await self._webhook.deliver(callback_url, payload, extra_headers=correlation or {})


def _extract_correlation(metadata: dict[str, Any] | None) -> dict[str, str]:
    """Pull the propagation context the submit handler stored in ``_correlation``."""
    if not metadata:
        return {}
    raw = metadata.get("_correlation")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}
