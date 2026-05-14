# Copyright 2026 Firefly Software Solutions Inc
"""``JobWorker`` -- consumes the queue and dispatches into the pipeline.

The worker is built around dependencies injected by the pyfly DI
container (orchestrator, queue, repository, webhook, settings). It is
NOT a stereotyped bean because we want the CLI to start it explicitly:
``flydesk-idp worker`` boots a minimal pyfly application and pulls the
worker out of the context.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timezone
from typing import Any

from flydesk_idp.config import IDPSettings
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

        await self._repository.mark_running(job.id)
        request = self._build_request(job)
        try:
            result = await asyncio.wait_for(
                self._orchestrator.execute(request), timeout=self._settings.async_timeout_s
            )
            await self._repository.mark_succeeded(
                job.id, result=result.model_dump(mode="json", by_alias=True)
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
            attempts = (job.attempts or 0) + 1
            if attempts >= self._settings.job_max_attempts:
                await self._repository.mark_failed(
                    job.id, code="EXTRACTION_FAILED", message=str(exc)
                )
                await self._fire_webhook(
                    job_id=job.id,
                    status=JobStatus.FAILED,
                    result=None,
                    metadata=job.metadata_json or {},
                    callback_url=job.callback_url,
                    error_code="EXTRACTION_FAILED",
                    error_message=str(exc),
                    correlation=_extract_correlation(job.metadata_json),
                )
            else:
                logger.warning(
                    "Job %s failed attempt %d: %s -- re-queueing", job.id, attempts, exc
                )
                await self._repository.update(job.id, status=JobStatus.QUEUED.value)
                await self._queue.publish(job.id)
        finally:
            await self._queue.ack(message)

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
        # Strip internal-only entries from metadata before serialising to the
        # webhook -- _correlation is propagated as headers, not as body fields.
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
