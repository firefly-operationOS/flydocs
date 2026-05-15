# Copyright 2026 Firefly Software Solutions Inc
"""``BboxRefineWorker`` -- second-stage EDA worker for grounded bbox refinement.

Subscribes to ``IDPSettings.bbox_refine_event_type`` on
``IDPSettings.bbox_refine_topic``. Each event carries one ``job_id``
whose main extraction has already finished with
``JobStatus.PARTIAL_SUCCEEDED`` and whose ``options.stages.bbox_refine``
was ``true``.

Per-event lifecycle:

1. Load the job row.
2. Skip if the job is already past ``REFINING_BBOXES`` (idempotent
   re-delivery from at-least-once buses is normal).
3. Transition ``PARTIAL_SUCCEEDED -> REFINING_BBOXES`` and bump the
   refine attempts counter atomically.
4. Re-run :class:`BinaryNormalizer` on the saved input bytes to recover
   the per-file LLM-renderable rows. (Deterministic; cheaper than
   persisting the normalised bytes alongside the job.)
5. For each :class:`ExtractedDocument` in the persisted result, find
   the matching normalised binary by ``source_file`` and call
   :class:`BboxRefiner.refine` against that document's field groups.
6. Re-serialise the mutated result, transition the job to
   :class:`JobStatus.SUCCEEDED`, and fire the final webhook.

Failures degrade gracefully: the partial result is **never** dropped.
Retryable errors (timeouts, transient OCR engine failures) re-publish
the same event with exponential backoff up to
``IDPSettings.bbox_refine_max_attempts``; permanent errors mark the
refine leg ``failed`` and the job reverts to ``PARTIAL_SUCCEEDED`` with
its LLM-bbox result intact.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import socket
import time
from datetime import UTC, datetime
from typing import Any

from pyfly.eda import EventEnvelope, EventPublisher

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import log_outbound
from flydesk_idp.core.services.bbox import BboxRefiner
from flydesk_idp.core.services.binary import BinaryNormalizer, NormalisedBinary
from flydesk_idp.core.services.webhook import WebhookPublisher
from flydesk_idp.interfaces.dtos.extract import ExtractionResult
from flydesk_idp.interfaces.dtos.webhook import JobWebhookPayload
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


# Same permanent-error hints the JobWorker uses; the refiner can hit
# the same provider-side failure classes via OCR adapters.
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
    if isinstance(exc, (ValueError, TypeError)):
        return True
    message = str(exc).lower()
    return any(hint in message for hint in _PERMANENT_ERROR_HINTS)


class BboxRefineWorker:
    """Second-stage EDA consumer: ground bboxes after main extraction."""

    def __init__(
        self,
        *,
        repository: ExtractionJobRepository,
        event_publisher: EventPublisher,
        webhook: WebhookPublisher,
        normalizer: BinaryNormalizer,
        refiner: BboxRefiner,
        settings: IDPSettings,
        consumer_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = event_publisher
        self._webhook = webhook
        self._normalizer = normalizer
        self._refiner = refiner
        self._settings = settings
        self._consumer_id = consumer_id or f"bbox-worker-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        # Subscribe before start() -- the EDA adapters only spin up the
        # consumer loop when at least one handler is registered.
        self._publisher.subscribe(self._settings.bbox_refine_event_type, self._on_event)
        await self._publisher.start()
        logger.info(
            "BboxRefineWorker %s started (adapter=%s, destination=%s, event_type=%s)",
            self._consumer_id,
            self._settings.eda_adapter,
            self._settings.bbox_refine_topic,
            self._settings.bbox_refine_event_type,
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
            logger.warning("EDA delivered unknown bbox-refine job %s -- dropping", job_id)
            return
        current = JobStatus(job.status)
        # Idempotent re-delivery guard: only PARTIAL_SUCCEEDED is the
        # legal entry state. SUCCEEDED / FAILED / CANCELLED / REFINING
        # mean someone else handled this already.
        if current != JobStatus.PARTIAL_SUCCEEDED:
            logger.info(
                "Skipping bbox refine for job %s: status=%s (not PARTIAL_SUCCEEDED)",
                job.id,
                current.value,
            )
            return

        job = await self._repository.mark_bbox_refining(job.id) or job
        attempts = job.bbox_refine_attempts or 1
        log_outbound(
            "bbox-worker",
            op="bbox.refine",
            status="started",
            latency_ms=0.0,
            job_id=job.id,
            attempt=attempts,
        )

        started = time.monotonic()
        try:
            refined = await asyncio.wait_for(
                self._refine_job_result(job),
                timeout=self._settings.bbox_refine_timeout_s,
            )
            await self._repository.mark_bbox_refined(
                job.id, result=refined.model_dump(mode="json", by_alias=True)
            )
            log_outbound(
                "bbox-worker",
                op="bbox.refine",
                status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id,
                attempt=attempts,
            )
            await self._fire_webhook(
                job_id=job.id,
                status=JobStatus.SUCCEEDED,
                result=refined,
                metadata=job.metadata_json or {},
                callback_url=job.callback_url,
                correlation=_extract_correlation(job.metadata_json),
            )
        except Exception as exc:  # noqa: BLE001
            permanent = _is_permanent(exc)
            exhausted = attempts >= self._settings.bbox_refine_max_attempts
            terminal = permanent or exhausted
            error_code = "PERMANENT_ERROR" if permanent else "BBOX_REFINE_FAILED"
            log_outbound(
                "bbox-worker",
                op="bbox.refine",
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                job_id=job.id,
                attempt=attempts,
                permanent=permanent,
                exhausted=exhausted,
                error=type(exc).__name__,
            )
            if terminal:
                await self._repository.mark_bbox_refine_failed(job.id, code=error_code, message=str(exc))
                # No webhook on bbox-refine permanent failure: the caller
                # already received the ``idp.job.partial`` payload with
                # the LLM-bbox result; nothing new to deliver.
            else:
                delay = self._backoff_delay(attempts)
                logger.warning(
                    "Bbox refine for job %s failed attempt %d (%s); re-publishing in %.1fs",
                    job.id,
                    attempts,
                    exc,
                    delay,
                )
                # Revert to PARTIAL_SUCCEEDED so the next delivery's
                # status check passes.
                await self._repository.update(
                    job.id,
                    status=JobStatus.PARTIAL_SUCCEEDED.value,
                    bbox_refine_status="pending",
                )
                asyncio.create_task(self._delayed_publish(job.id, delay))

    # ------------------------------------------------------------------

    async def _refine_job_result(self, job: Any) -> ExtractionResult:
        """Reconstruct the per-document bytes + run the refiner per doc."""
        if not job.result_json:
            raise ValueError(f"job {job.id} has no result_json to refine")
        result = ExtractionResult.model_validate(job.result_json)

        schema = job.schema_json or {}
        # ``schema_json`` carries the original document bytes the submit
        # handler stored. Single-file shape is the only one persisted
        # today (multi-file requests fan out at orchestrator time).
        encoded = schema.get("document_content_base64") or ""
        if not encoded:
            raise ValueError(f"job {job.id} has no document_content_base64 in schema_json")
        document_bytes = base64.b64decode(encoded)

        normalised: list[NormalisedBinary] = await self._normalizer.normalise(
            document_bytes,
            declared_media_type=schema.get("document_content_type"),
            filename=job.filename,
        )
        # Index by filename for source_file lookups. Multi-row inputs
        # carry their normalised filename in ``row.filename``.
        by_filename: dict[str, NormalisedBinary] = {row.filename: row for row in normalised}
        # Fallback row when ``source_file`` is null (legacy single-doc).
        fallback = normalised[0] if normalised else None

        language_hint = (job.options_json or {}).get("language_hint")

        for document in result.documents:
            if not document.fields:
                continue
            row = by_filename.get(document.source_file or "", fallback)
            if row is None:
                continue
            await self._refiner.refine(
                document_bytes=row.bytes,
                media_type=row.media_type,
                page_count=row.page_count,
                groups=document.fields,
                language_hint=language_hint,
            )
        return result

    def _backoff_delay(self, attempts: int) -> float:
        base = self._settings.retry_base_delay_s
        ceiling = self._settings.retry_max_delay_s
        raw = base * (2 ** max(0, attempts - 1))
        capped = min(ceiling, raw)
        jitter = capped * 0.2 * random.random()
        return capped + jitter

    async def _delayed_publish(self, job_id: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            await self._publisher.publish(
                destination=self._settings.bbox_refine_topic,
                event_type=self._settings.bbox_refine_event_type,
                payload={"job_id": job_id},
            )
            log_outbound(
                "eda",
                op="republish.bbox_refine",
                status="ok",
                latency_ms=delay_s * 1000,
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to re-publish bbox refine job %s after backoff: %s", job_id, exc)

    async def _fire_webhook(
        self,
        *,
        job_id: str,
        status: JobStatus,
        result: ExtractionResult | None,
        metadata: dict[str, Any],
        callback_url: str | None,
        correlation: dict[str, str] | None = None,
    ) -> None:
        if not callback_url:
            return
        clean_metadata = {k: v for k, v in (metadata or {}).items() if not k.startswith("_")}
        payload = JobWebhookPayload(
            job_id=job_id,
            status=status,
            occurred_at=datetime.now(UTC),
            metadata=clean_metadata,
            result=result,
        )
        await self._webhook.deliver(callback_url, payload, extra_headers=correlation or {})


def _extract_correlation(metadata: dict[str, Any] | None) -> dict[str, str]:
    if not metadata:
        return {}
    raw = metadata.get("_correlation")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}
