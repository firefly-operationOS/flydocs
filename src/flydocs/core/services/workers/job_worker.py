# Copyright 2026 Firefly Software Solutions Inc
"""``ExtractionWorker`` -- subscribes to the EDA bus and dispatches into the pipeline.

The worker registers an :func:`event_listener` handler on the configured
``jobs_event_type`` against the pyfly :class:`EventPublisher` bean (the
underlying broker is picked by :class:`pyfly.eda.EdaAutoConfiguration` --
Postgres outbox + LISTEN/NOTIFY by default, but it can be Redis Streams
or Kafka by flipping ``FLYDOCS_EDA_ADAPTER``).

Retry policy
============

A failed attempt is classified into one of two buckets:

* ``permanent`` -- a malformed payload, an unrecoverable provider error
  (content policy, unsupported model). The extraction goes straight to
  ``failed`` so the caller can fix the input.
* ``retryable`` -- a timeout, a 5xx from the LLM provider, a transient
  network glitch. The worker re-publishes the same submitted event on
  the same bus after a capped-exponential backoff with jitter, so the
  next worker (or this one, after re-delivery) picks it up. The
  ``attempts`` counter is persisted in Postgres so the budget survives
  worker restarts.

When the caller asked for bbox refinement, the worker calls
``repository.mark_succeeded(..., request_bbox_refinement=True)`` to
flip the post-processing bbox status to ``pending`` atomically with
the main success transition, then publishes a separate
``extraction.post_processing.requested`` event the dedicated
:class:`BboxRefineWorker` consumes out of band. The main extraction is
``succeeded`` immediately -- the bbox leg is purely additive.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
from datetime import UTC, datetime
from typing import Any

from pyfly.eda import EventEnvelope as EdaEnvelope
from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.core.services.pipeline import PipelineOrchestrator
from flydocs.core.services.webhook import WebhookPublisher
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    ExtractionResult,
    FileInput,
)
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.interfaces.dtos.rule import RuleSpec
from flydocs.interfaces.enums.extraction_status import ExtractionStatus
from flydocs.models.repositories import ExtractionRepository

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


class ExtractionWorker:
    def __init__(
        self,
        *,
        orchestrator: PipelineOrchestrator,
        repository: ExtractionRepository,
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
        self._publisher.subscribe(EVENT_TYPE_EXTRACTION_SUBMITTED, self._on_event)
        await self._publisher.start()
        logger.info(
            "ExtractionWorker %s started (adapter=%s, destination=%s, event_type=%s)",
            self._consumer_id,
            self._settings.eda_adapter,
            self._settings.jobs_topic,
            EVENT_TYPE_EXTRACTION_SUBMITTED,
        )
        try:
            await self._stop.wait()
        finally:
            await self._publisher.stop()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def _on_event(self, envelope: EdaEnvelope) -> None:
        extraction_id = _extraction_id_from_payload(envelope.payload)
        if not extraction_id:
            logger.warning(
                "Received %s event without extraction id: %r -- dropping",
                envelope.event_type,
                envelope.payload,
            )
            return
        await self._process(extraction_id)

    async def _process(self, extraction_id: str) -> None:
        row = await self._repository.get(extraction_id)
        if row is None:
            logger.warning("EDA delivered unknown extraction %s -- dropping", extraction_id)
            return
        if ExtractionStatus(row.status) in (
            ExtractionStatus.SUCCEEDED,
            ExtractionStatus.CANCELLED,
            ExtractionStatus.FAILED,
        ):
            logger.info(
                "Extraction %s already in terminal status %s -- skipping",
                row.id,
                row.status,
            )
            return

        # Atomic compare-and-swap: only one worker can claim a QUEUED
        # (or stale-RUNNING) extraction. ``None`` means another worker
        # beat us to it or the row was cancelled between our ``get``
        # and this claim -- both are silent no-ops.
        claimed = await self._repository.mark_running(row.id, lease_seconds=self._settings.job_run_lease_s)
        if claimed is None:
            logger.info(
                "Extraction %s could not be claimed -- already owned by another worker or "
                "no longer in a claimable state. Skipping at-least-once redelivery.",
                row.id,
            )
            return
        row = claimed
        attempts = row.attempts or 1
        log_outbound(
            "worker",
            op="extraction.run",
            status="started",
            latency_ms=0.0,
            extraction_id=row.id,
            attempt=attempts,
        )
        request = self._build_request(row)
        # Capture the original intent BEFORE we mutate the request: we
        # need to know whether the caller wanted bbox refinement so we
        # can publish the post-processing event afterwards, even if we
        # skip the inline node below.
        wants_bbox_refine = bool(getattr(request.options.stages, "bbox_refine", False))
        if wants_bbox_refine:
            # Architectural decision: on the async path, skip the inline
            # bbox_refine node entirely. The dedicated BboxRefineWorker
            # picks up the post-processing event we publish below and
            # grounds bboxes there. Running both wastes minutes of CPU
            # and LLM tokens on duplicate work — and when the inline
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
                self._orchestrator.execute(request, extraction_id=row.id),
                timeout=self._settings.async_timeout_s,
            )
            result_payload = result.model_dump(mode="json", by_alias=True)
            # Branch on bbox_refine: when the caller asked for grounded
            # coordinates the main pipeline is still ``succeeded`` here
            # -- bbox refinement is additive post-processing -- but the
            # bbox leg flips to ``pending`` atomically so the bbox
            # worker can pick it up.
            finalised = await self._repository.mark_succeeded(
                row.id,
                result=result_payload,
                request_bbox_refinement=wants_bbox_refine,
            )
            if finalised is None:
                # Another worker (or the bbox leg) already advanced the
                # row past RUNNING. Our work is duplicate -- don't fire
                # the webhook a second time, don't republish.
                logger.info(
                    "Extraction %s already finalised by another worker -- discarding our duplicate result",
                    row.id,
                )
                return
            log_outbound(
                "worker",
                op="extraction.run",
                status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                extraction_id=row.id,
                attempt=attempts,
                terminal=ExtractionStatus.SUCCEEDED.value,
            )
            correlation_headers = _extract_correlation(row.metadata_json)
            extraction_dto = row_to_extraction(finalised)
            await self._fire_webhook(
                event_type=EVENT_TYPE_EXTRACTION_COMPLETED,
                extraction=extraction_dto,
                result=result,
                metadata=row.metadata_json or {},
                callback_url=row.callback_url,
                correlation=correlation_headers,
            )
            if wants_bbox_refine:
                # Publish the post-processing event using the SAME
                # EventEnvelope shape that the SDK / webhook consumers
                # see -- the EDA bus and the webhook delivery now agree
                # on a single model.
                refine_envelope = EventEnvelope(
                    event_type=EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
                    correlation_id=correlation_headers.get("X-Correlation-Id"),
                    tenant_id=correlation_headers.get("X-Tenant-Id"),
                    extraction=extraction_dto,
                )
                await self._publisher.publish(
                    destination=self._settings.bbox_refine_topic,
                    event_type=EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
                    payload=envelope_for_publish(refine_envelope),
                    headers=correlation_headers,
                )
                log_outbound(
                    "eda",
                    op="publish.post_processing",
                    status="ok",
                    latency_ms=0.0,
                    extraction_id=row.id,
                    destination=self._settings.bbox_refine_topic,
                )
        except Exception as exc:  # noqa: BLE001
            permanent = _is_permanent(exc)
            exhausted = attempts >= self._settings.job_max_attempts
            terminal = permanent or exhausted
            error_code = "permanent_error" if permanent else "extraction_failed"
            log_outbound(
                "worker",
                op="extraction.run",
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                extraction_id=row.id,
                attempt=attempts,
                permanent=permanent,
                exhausted=exhausted,
                error=type(exc).__name__,
            )

            if terminal:
                failed = await self._repository.mark_failed(row.id, code=error_code, message=str(exc))
                if failed is None:
                    logger.info(
                        "Extraction %s no longer in RUNNING -- another worker handled the "
                        "terminal transition, skipping our webhook",
                        row.id,
                    )
                    return
                failed_dto = row_to_extraction(failed)
                await self._fire_webhook(
                    event_type=EVENT_TYPE_EXTRACTION_COMPLETED,
                    extraction=failed_dto,
                    result=None,
                    metadata=row.metadata_json or {},
                    callback_url=row.callback_url,
                    correlation=_extract_correlation(row.metadata_json),
                )
            else:
                delay = self._backoff_delay(attempts)
                logger.warning(
                    "Extraction %s failed attempt %d (%s); re-publishing in %.1fs",
                    row.id,
                    attempts,
                    exc,
                    delay,
                )
                # Atomic RUNNING -> QUEUED so the API surface reflects
                # the retry. If the row is no longer in RUNNING (e.g. a
                # cancel won the race against our claim, or another
                # worker took over after our lease expired) we skip the
                # republish: someone else owns the next step.
                requeued = await self._repository.requeue_for_retry(row.id)
                if requeued is None:
                    logger.info(
                        "Extraction %s not requeueable (status changed under us) -- skipping retry publish",
                        row.id,
                    )
                else:
                    asyncio.create_task(self._delayed_publish(row.id, delay))

    def _backoff_delay(self, attempts: int) -> float:
        """Capped exponential backoff with a 20% jitter."""
        base = self._settings.retry_base_delay_s
        ceiling = self._settings.retry_max_delay_s
        raw = base * (2 ** max(0, attempts - 1))
        capped = min(ceiling, raw)
        jitter = capped * 0.2 * random.random()
        return capped + jitter

    async def _delayed_publish(self, extraction_id: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            # Re-resolve the row so we can publish a fresh envelope
            # carrying the current resource snapshot.
            row = await self._repository.get(extraction_id)
            if row is None:
                logger.warning("Delayed republish: extraction %s vanished", extraction_id)
                return
            envelope = EventEnvelope(
                event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
                extraction=row_to_extraction(row),
            )
            await self._publisher.publish(
                destination=self._settings.jobs_topic,
                event_type=EVENT_TYPE_EXTRACTION_SUBMITTED,
                payload=envelope_for_publish(envelope),
            )
            log_outbound(
                "eda",
                op="republish",
                status="ok",
                latency_ms=delay_s * 1000,
                extraction_id=extraction_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to re-publish extraction %s after backoff: %s", extraction_id, exc)

    def _build_request(self, row: Any) -> ExtractionRequest:
        schema = row.schema_json or {}
        intention = schema.get("intention", "Extract structured data from the document.")
        document_types = [DocumentTypeSpec.model_validate(d) for d in schema.get("document_types", [])]
        rules = [RuleSpec.model_validate(r) for r in schema.get("rules", [])]
        options = ExtractionOptions.model_validate(row.options_json or {})
        files_payload = schema.get("files") or []
        if not files_payload:
            raise ValueError(
                f"extraction {row.id} schema_json missing 'files' — cannot rebuild ExtractionRequest"
            )
        return ExtractionRequest(
            intention=intention,
            files=[
                FileInput(
                    filename=d.get("filename", row.filename),
                    content_base64=d.get("content_base64", ""),
                    content_type=d.get("content_type"),
                    expected_type=d.get("expected_type"),
                )
                for d in files_payload
            ],
            document_types=document_types,
            rules=rules,
            options=options,
        )

    async def _fire_webhook(
        self,
        *,
        event_type: str,
        extraction: Extraction,
        result: ExtractionResult | None,
        metadata: dict[str, Any],
        callback_url: str | None,
        correlation: dict[str, str] | None = None,
    ) -> None:
        if not callback_url:
            return
        clean_metadata = {k: v for k, v in (metadata or {}).items() if not k.startswith("_")}
        corr = correlation or {}
        envelope = EventEnvelope(
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            correlation_id=corr.get("X-Correlation-Id"),
            tenant_id=corr.get("X-Tenant-Id"),
            extraction=extraction,
            result=result if extraction.status == ExtractionStatus.SUCCEEDED else None,
            metadata=clean_metadata,
        )
        await self._webhook.deliver(callback_url, envelope, extra_headers=corr)


def _extraction_id_from_payload(payload: Any) -> str | None:
    """Pull the extraction id out of an inbound EDA payload.

    Accepts both the v1 ``EventEnvelope`` shape (``extraction.id``) and
    the bare ``extraction_id`` / ``job_id`` keys still produced by
    legacy republishers on the bus during the migration window.
    """
    if not isinstance(payload, dict):
        return None
    extraction = payload.get("extraction")
    if isinstance(extraction, dict) and extraction.get("id"):
        return str(extraction["id"])
    for key in ("extraction_id", "job_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _extract_correlation(metadata: dict[str, Any] | None) -> dict[str, str]:
    """Pull the propagation context the submit handler stored in ``_correlation``."""
    if not metadata:
        return {}
    raw = metadata.get("_correlation")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


# Backwards-compat alias for callers (CLI, tests) that still import the
# old name. New code should use :class:`ExtractionWorker`.
JobWorker = ExtractionWorker


__all__ = ["ExtractionWorker", "JobWorker"]
