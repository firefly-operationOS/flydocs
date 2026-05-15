# Copyright 2026 Firefly Software Solutions Inc
"""``JobWorker`` -- subscribes to the EDA bus and dispatches into the pipeline.

The worker registers an :func:`event_listener` handler on the configured
``jobs_event_type`` against the pyfly :class:`EventPublisher` bean (the
underlying broker is picked by :class:`pyfly.eda.EdaAutoConfiguration` --
Postgres outbox + LISTEN/NOTIFY by default, but it can be Redis Streams
or Kafka by flipping ``FLYDESK_IDP_EDA_ADAPTER``).

Retry policy
============

A failed attempt is classified into one of two buckets:

* ``permanent`` -- a malformed payload, an unrecoverable provider error
  (content policy, unsupported model). The job goes straight to
  ``FAILED`` so the caller can fix the input.
* ``retryable`` -- a timeout, a 5xx from the LLM provider, a transient
  network glitch. The worker re-publishes the same ``IDPJobSubmitted``
  event on the same bus after a capped-exponential backoff with jitter,
  so the next worker (or this one, after re-delivery) picks it up. The
  ``attempts`` counter is persisted in Postgres so the budget survives
  worker restarts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
from datetime import UTC, datetime
from typing import Any

from pyfly.eda import EventEnvelope, EventPublisher

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.pipeline import PipelineOrchestrator
from flydesk_idp.core.services.webhook import WebhookPublisher
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.event import (
    IDPBboxRefineRequestedEvent,
    IDPJobSubmittedEvent,
    envelope_for_publish,
)
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
        event_publisher: EventPublisher,
        webhook: WebhookPublisher,
        settings: IDPSettings,
        consumer_id: str | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._publisher = event_publisher
        self._webhook = webhook
        self._settings = settings
        self._consumer_id = consumer_id or f"worker-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        # Subscribe BEFORE starting the bus -- the EDA adapters spin up
        # consumer loops at ``start()`` time, and they only do so when at
        # least one handler is registered.
        self._publisher.subscribe(self._settings.jobs_event_type, self._on_event)
        await self._publisher.start()
        logger.info(
            "JobWorker %s started (adapter=%s, destination=%s, event_type=%s)",
            self._consumer_id,
            self._settings.eda_adapter,
            self._settings.jobs_topic,
            self._settings.jobs_event_type,
        )
        try:
            await self._stop.wait()
        finally:
            await self._publisher.stop()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def _on_event(self, envelope: EventEnvelope) -> None:
        job_id = envelope.payload.get("job_id") if isinstance(envelope.payload, dict) else None
        if not job_id:
            logger.warning(
                "Received %s event without job_id: %r -- dropping",
                envelope.event_type,
                envelope.payload,
            )
            return
        await self._process(str(job_id))

    async def _process(self, job_id: str) -> None:
        job = await self._repository.get(job_id)
        if job is None:
            logger.warning("EDA delivered unknown job %s -- dropping", job_id)
            return
        if JobStatus(job.status) in (JobStatus.SUCCEEDED, JobStatus.CANCELLED):
            logger.info("Job %s already in terminal status %s -- skipping", job.id, job.status)
            return

        # mark_running increments attempts atomically in Postgres.
        job = await self._repository.mark_running(job.id) or job
        attempts = job.attempts or 1
        log_outbound(
            "worker",
            op="job.run",
            status="started",
            latency_ms=0.0,
            job_id=job.id,
            attempt=attempts,
        )
        request = self._build_request(job)
        # Capture the original intent BEFORE we mutate the request: we
        # need to know whether the caller wanted bbox refinement so we
        # can publish the IDPBboxRefineRequested event afterwards, even
        # if we skip the inline node below.
        wants_bbox_refine = bool(getattr(request.options.stages, "bbox_refine", False))
        if wants_bbox_refine:
            # Architectural decision: on the async path, skip the inline
            # bbox_refine node entirely. The dedicated BboxRefineWorker
            # picks up the IDPBboxRefineRequested event we publish below
            # and grounds bboxes there. Running both wastes minutes of
            # CPU + LLM tokens on duplicate work — and when the inline
            # step times out (which it does on multi-PDF bundles) the
            # pipeline framework marks the node as failed, which is
            # misleading because the out-of-band path recovers
            # transparently. The :class:`BboxRefiner` is idempotent
            # (already-grounded fields are skipped on re-run), so even
            # if both paths execute the work won't double up — but
            # bypassing inline saves the latency outright.
            stages_skipped = request.options.stages.model_copy(update={"bbox_refine": False})
            options_skipped = request.options.model_copy(update={"stages": stages_skipped})
            request = request.model_copy(update={"options": options_skipped})
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._orchestrator.execute(request), timeout=self._settings.async_timeout_s
            )
            result_payload = result.model_dump(mode="json", by_alias=True)
            # Branch on bbox_refine: when the caller asked for grounded
            # coordinates, the job becomes ``PARTIAL_SUCCEEDED`` here and
            # the actual grounding is delegated to ``BboxRefineWorker`` via
            # a second EDA event. The result is already readable -- only
            # the bboxes change between PARTIAL_SUCCEEDED and SUCCEEDED.
            terminal_status = JobStatus.PARTIAL_SUCCEEDED if wants_bbox_refine else JobStatus.SUCCEEDED
            if wants_bbox_refine:
                await self._repository.mark_partial_succeeded(job.id, result=result_payload)
            else:
                await self._repository.mark_succeeded(job.id, result=result_payload)
            log_outbound(
                "worker",
                op="job.run",
                status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id,
                attempt=attempts,
                terminal=terminal_status.value,
            )
            correlation_headers = _extract_correlation(job.metadata_json)
            await self._fire_webhook(
                job_id=job.id,
                status=terminal_status,
                result=result,
                metadata=job.metadata_json or {},
                callback_url=job.callback_url,
                correlation=correlation_headers,
                started_at=getattr(job, "started_at", None),
                finished_at=datetime.now(UTC),
                attempts=attempts,
            )
            if wants_bbox_refine:
                refine_event = IDPBboxRefineRequestedEvent(
                    job_id=job.id,
                    attempt=1,
                    correlation_id=correlation_headers.get("X-Correlation-Id"),
                    tenant_id=correlation_headers.get("X-Tenant-Id"),
                )
                await self._publisher.publish(
                    destination=self._settings.bbox_refine_topic,
                    event_type=self._settings.bbox_refine_event_type,
                    payload=envelope_for_publish(refine_event),
                    headers=correlation_headers,
                )
                log_outbound(
                    "eda",
                    op="publish.bbox_refine",
                    status="ok",
                    latency_ms=0.0,
                    job_id=job.id,
                    destination=self._settings.bbox_refine_topic,
                )
        except Exception as exc:  # noqa: BLE001
            permanent = _is_permanent(exc)
            exhausted = attempts >= self._settings.job_max_attempts
            terminal = permanent or exhausted
            error_code = "PERMANENT_ERROR" if permanent else "EXTRACTION_FAILED"
            log_outbound(
                "worker",
                op="job.run",
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id,
                attempt=attempts,
                permanent=permanent,
                exhausted=exhausted,
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
                    started_at=getattr(job, "started_at", None),
                    finished_at=datetime.now(UTC),
                    attempts=attempts,
                )
            else:
                delay = self._backoff_delay(attempts)
                logger.warning(
                    "Job %s failed attempt %d (%s); re-publishing in %.1fs",
                    job.id,
                    attempts,
                    exc,
                    delay,
                )
                # Mark QUEUED again so the API surface reflects the retry,
                # then schedule the re-publish without blocking the handler.
                await self._repository.update(job.id, status=JobStatus.QUEUED.value)
                asyncio.create_task(self._delayed_publish(job.id, delay))

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
            # We don't have the original correlation context here (this
            # runs in a detached task after the handler returned), so
            # emit the envelope without correlation. The dedupe-by-event-id
            # guarantee still holds for clients tracking re-deliveries.
            republish_event = IDPJobSubmittedEvent(
                job_id=job_id,
                attempt=2,  # any republish is at least attempt 2 from the worker's POV
            )
            await self._publisher.publish(
                destination=self._settings.jobs_topic,
                event_type=self._settings.jobs_event_type,
                payload=envelope_for_publish(republish_event),
            )
            log_outbound(
                "eda",
                op="republish",
                status="ok",
                latency_ms=delay_s * 1000,
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to re-publish job %s after backoff: %s", job_id, exc)

    def _build_request(self, job: Any) -> ExtractionRequest:
        schema = job.schema_json or {}
        intention = schema.get("intention", "Extract structured data from the document.")
        docs = [DocSpec.model_validate(d) for d in schema.get("docs", [])]
        rules = [RuleSpec.model_validate(r) for r in schema.get("rules", [])]
        options = ExtractionOptions.model_validate(job.options_json or {})
        documents_payload = schema.get("documents") or []
        if not documents_payload:
            raise ValueError(
                f"job {job.id} schema_json missing 'documents' — cannot rebuild ExtractionRequest"
            )
        return ExtractionRequest(
            intention=intention,
            documents=[
                DocumentInput(
                    filename=d.get("filename", job.filename),
                    content_base64=d.get("content_base64", ""),
                    content_type=d.get("content_type"),
                    document_type=d.get("document_type"),
                )
                for d in documents_payload
            ],
            docs=docs,
            rules=rules,
            options=options,
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
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        attempts: int = 1,
    ) -> None:
        if not callback_url:
            return
        clean_metadata = {k: v for k, v in (metadata or {}).items() if not k.startswith("_")}
        corr = correlation or {}
        payload = JobWebhookPayload(
            job_id=job_id,
            status=status,
            occurred_at=datetime.now(UTC),
            started_at=started_at,
            finished_at=finished_at,
            attempts=attempts,
            correlation_id=corr.get("X-Correlation-Id"),
            tenant_id=corr.get("X-Tenant-Id"),
            metadata=clean_metadata,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )
        await self._webhook.deliver(callback_url, payload, extra_headers=corr)


def _extract_correlation(metadata: dict[str, Any] | None) -> dict[str, str]:
    """Pull the propagation context the submit handler stored in ``_correlation``."""
    if not metadata:
        return {}
    raw = metadata.get("_correlation")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}
