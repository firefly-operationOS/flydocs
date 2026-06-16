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

"""``BboxRefineWorker`` -- post-processing EDA worker for grounded bbox refinement.

Subscribes to ``extraction.post_processing.requested`` events.
Each event carries one extraction whose main pipeline has already
finished ``succeeded`` AND whose ``options.stages.bbox_refine`` was
``true``. The bbox-leg sub-status on the row is ``pending`` when we
get to here -- :meth:`ExtractionRepository.mark_succeeded` set it
atomically with the main success transition.

Per-event lifecycle:

1. Load the extraction row.
2. Skip if the bbox leg is already past ``pending`` / stale
   ``running`` (idempotent re-delivery from at-least-once buses is
   normal).
3. Transition ``pending`` -> ``running`` on
   ``post_processing_bbox_status`` (atomic claim with a lease).
4. Re-run :class:`BinaryNormalizer` on the saved input bytes to recover
   the per-file LLM-renderable rows. (Deterministic; cheaper than
   persisting the normalised bytes alongside the row.)
5. For each :class:`Document` in the persisted result, find the
   matching normalised binary by ``source_file`` and call
   :class:`BboxRefiner.refine` against that document's field groups.
6. Re-serialise the mutated result and transition the bbox leg to
   ``succeeded`` (the main extraction status was already ``succeeded``).
   Fire the post-processing-completed webhook.

Failures degrade gracefully: the result is **never** dropped.
Retryable errors (timeouts, transient OCR engine failures) re-publish
the same event with exponential backoff up to
``IDPSettings.bbox_refine_max_attempts``; permanent errors mark the
bbox leg ``failed`` and the main extraction stays ``succeeded`` with
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

from fireflyframework_agentic.content.binary import BinaryArtifact, BinaryNormalizer
from pyfly.eda import EventEnvelope as EdaEnvelope
from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.bbox import BboxRefiner
from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.core.services.webhook import WebhookPublisher
from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.models.repositories import ExtractionRepository

logger = logging.getLogger(__name__)


# Same permanent-error hints the ExtractionWorker uses; the refiner can
# hit the same provider-side failure classes via OCR adapters.
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
    """Post-processing EDA consumer: ground bboxes after main extraction."""

    def __init__(
        self,
        *,
        repository: ExtractionRepository,
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
        self._publisher.subscribe(EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED, self._on_event)
        await self._publisher.start()
        logger.info(
            "BboxRefineWorker %s started (adapter=%s, destination=%s, event_type=%s)",
            self._consumer_id,
            self._settings.eda_adapter,
            self._settings.bbox_refine_topic,
            EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
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
        # Atomic claim: precondition matches pending (first delivery) or
        # stale running (previous claimant crashed past its lease).
        # Anything else (succeeded / failed bbox leg, fresh running)
        # returns None so we treat the event as already handled and bail.
        claimed = await self._repository.claim_bbox_refinement(
            row.id, lease_seconds=self._settings.bbox_refine_lease_s
        )
        if claimed is None:
            logger.info(
                "Bbox refine for extraction %s could not be claimed (bbox_status=%s) -- "
                "another worker owns it or the leg already finished",
                row.id,
                row.post_processing_bbox_status,
            )
            return
        row = claimed
        attempts = row.post_processing_bbox_attempts or 1
        log_outbound(
            "bbox-worker",
            op="bbox.refine",
            status="started",
            latency_ms=0.0,
            extraction_id=row.id,
            attempt=attempts,
        )

        started = time.monotonic()
        try:
            refined = await asyncio.wait_for(
                self._refine_extraction_result(row),
                timeout=self._settings.bbox_refine_timeout_s,
            )
            finalised = await self._repository.complete_bbox_refinement(
                row.id, result=refined.model_dump(mode="json", by_alias=True)
            )
            if finalised is None:
                logger.info(
                    "Bbox refine for extraction %s no longer in running -- "
                    "another worker finalised it, discarding our result",
                    row.id,
                )
                return
            log_outbound(
                "bbox-worker",
                op="bbox.refine",
                status="ok",
                latency_ms=(time.monotonic() - started) * 1000,
                extraction_id=row.id,
                attempt=attempts,
            )
            await self._fire_webhook(
                extraction=row_to_extraction(finalised),
                result=refined,
                metadata=row.metadata_json or {},
                callback_url=row.callback_url,
                correlation=_extract_correlation(row.metadata_json),
            )
        except Exception as exc:  # noqa: BLE001
            permanent = _is_permanent(exc)
            exhausted = attempts >= self._settings.bbox_refine_max_attempts
            terminal = permanent or exhausted
            error_code = "permanent_error" if permanent else "bbox_refine_failed"
            log_outbound(
                "bbox-worker",
                op="bbox.refine",
                status="error",
                latency_ms=(time.monotonic() - started) * 1000,
                extraction_id=row.id,
                attempt=attempts,
                permanent=permanent,
                exhausted=exhausted,
                error=type(exc).__name__,
            )
            if terminal:
                failed = await self._repository.fail_bbox_refinement(
                    row.id, code=error_code, message=str(exc)
                )
                if failed is None:
                    logger.info(
                        "Bbox refine for extraction %s already past running -- "
                        "another worker handled the terminal transition",
                        row.id,
                    )
                # No webhook on bbox-refine permanent failure: the caller
                # already received the ``extraction.completed`` payload
                # with the LLM-bbox result; nothing new to deliver.
            else:
                delay = self._backoff_delay(attempts)
                logger.warning(
                    "Bbox refine for extraction %s failed attempt %d (%s); re-publishing in %.1fs",
                    row.id,
                    attempts,
                    exc,
                    delay,
                )
                # Atomically revert running -> pending so the next
                # delivery's claim precondition passes. If we lost the
                # row (another worker advanced it), skip the republish.
                requeued = await self._repository.requeue_bbox_refinement(row.id)
                if requeued is None:
                    logger.info(
                        "Bbox refine for extraction %s not requeueable -- skipping retry",
                        row.id,
                    )
                else:
                    asyncio.create_task(self._delayed_publish(row.id, delay))

    # ------------------------------------------------------------------

    async def _refine_extraction_result(self, row: Any) -> ExtractionResult:
        """Reconstruct the per-document bytes + run the refiner per document."""
        if not row.result_json:
            raise ValueError(f"extraction {row.id} has no result_json to refine")
        result = ExtractionResult.model_validate(row.result_json)

        schema = row.schema_json or {}
        # ``schema_json.files`` carries every input file the submit
        # handler stored: a list of ``{filename, content_base64,
        # content_type, expected_type}``. We normalise each one
        # independently so the refiner has one :class:`NormalisedBinary`
        # row per ``source_file`` to look up.
        files_payload = schema.get("files") or []
        if not files_payload:
            raise ValueError(f"extraction {row.id} schema_json missing 'files'")
        sources: list[tuple[bytes, str | None, str]] = [
            (
                base64.b64decode(entry.get("content_base64") or ""),
                entry.get("content_type"),
                entry.get("filename") or row.filename,
            )
            for entry in files_payload
            if entry.get("content_base64")
        ]
        if not sources:
            raise ValueError(f"extraction {row.id} has no decodable file bytes in schema_json")

        normalised: list[BinaryArtifact] = []
        for raw_bytes, media_type, name in sources:
            normalised.extend(
                await self._normalizer.normalise(
                    raw_bytes,
                    declared_media_type=media_type,
                    filename=name,
                )
            )
        # Index by filename for source_file lookups. Multi-row inputs
        # carry their normalised filename in ``row.filename``.
        by_filename: dict[str, BinaryArtifact] = {row.filename: row for row in normalised}

        language_hint = (row.options_json or {}).get("language_hint")

        # Refine the documents concurrently rather than strictly one-by-one:
        # each document is independent (its own field_groups, mutated in
        # place), and the refiner pushes its CPU-bound word collection to a
        # thread, so overlapping docs cut the wall-clock from the sum of
        # per-doc latencies to ~the slowest one. A semaphore bounds the fan-out
        # (OCR is CPU-bound; each doc multiplies in-flight LLM calls).
        # ``gather`` is the barrier that makes "the last document finished"
        # well-defined: it returns only once every task has settled, so the
        # caller fires the completion + webhook exactly once, afterwards.
        semaphore = asyncio.Semaphore(max(1, self._settings.bbox_refine_doc_concurrency))

        async def _refine_one(document: Any) -> None:
            if not document.field_groups:
                return
            mapped = by_filename.get(document.source_file or "")
            if mapped is None:
                return
            async with semaphore:
                await self._refiner.refine(
                    document_bytes=mapped.bytes,
                    media_type=mapped.media_type,
                    page_count=mapped.page_count,
                    groups=document.field_groups,
                    language_hint=language_hint,
                )

        # ``return_exceptions=True`` so one document's failure never strands
        # its siblings mid-flight; we await them all (the barrier) and then
        # re-raise the first error, preserving the existing per-job
        # retry/permanent-failure semantics (and the original exception type
        # that ``_is_permanent`` inspects).
        outcomes = await asyncio.gather(
            *(_refine_one(document) for document in result.documents),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return result

    def _backoff_delay(self, attempts: int) -> float:
        base = self._settings.retry_base_delay_s
        ceiling = self._settings.retry_max_delay_s
        raw = base * (2 ** max(0, attempts - 1))
        capped = min(ceiling, raw)
        jitter = capped * 0.2 * random.random()
        return capped + jitter

    async def _delayed_publish(self, extraction_id: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
            row = await self._repository.get(extraction_id)
            if row is None:
                logger.warning("Delayed republish: extraction %s vanished", extraction_id)
                return
            envelope = EventEnvelope(
                event_type=EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
                extraction=row_to_extraction(row),
            )
            await self._publisher.publish(
                destination=self._settings.bbox_refine_topic,
                event_type=EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
                payload=envelope_for_publish(envelope),
            )
            log_outbound(
                "eda",
                op="republish.post_processing",
                status="ok",
                latency_ms=delay_s * 1000,
                extraction_id=extraction_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to re-publish bbox refine extraction %s after backoff: %s",
                extraction_id,
                exc,
            )

    async def _fire_webhook(
        self,
        *,
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
            event_type=EVENT_TYPE_EXTRACTION_POST_PROCESSING_COMPLETED,
            occurred_at=datetime.now(UTC),
            correlation_id=corr.get("X-Correlation-Id"),
            tenant_id=corr.get("X-Tenant-Id"),
            extraction=extraction,
            result=result,
            metadata=clean_metadata,
        )
        await self._webhook.deliver(callback_url, envelope, extra_headers=corr)


def _extraction_id_from_payload(payload: Any) -> str | None:
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
    if not metadata:
        return {}
    raw = metadata.get("_correlation")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


__all__ = ["BboxRefineWorker"]
