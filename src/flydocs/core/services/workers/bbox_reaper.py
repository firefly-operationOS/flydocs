# Copyright 2026 Firefly Software Solutions Inc
"""``BboxReaper`` -- periodic sweep for orphaned bbox-refine legs.

Bbox-leg analogue of :class:`ExtractionReaper`. Two orphan classes:

* ``post_processing_bbox_status=running`` with stale
  ``post_processing_bbox_started_at`` -- the bbox worker that claimed
  the leg crashed past its lease.
* ``post_processing_bbox_status=pending`` -- the initial bbox event
  was never published (main worker crashed between ``mark_succeeded``
  and ``publisher.publish``), or a prior bbox-leg retry's
  ``_delayed_publish`` task was lost.

Both are revived by republishing a fresh
``extraction.post_processing.requested`` event; the bbox worker's
atomic ``claim_bbox_refinement`` dedupes duplicate publishes from
concurrent replicas.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket

from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.interfaces.dtos.event import (
    EVENT_TYPE_EXTRACTION_POST_PROCESSING_REQUESTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.models.repositories import ExtractionRepository

logger = logging.getLogger(__name__)


class BboxReaper:
    """Periodic sweep for orphaned bbox-refine legs."""

    def __init__(
        self,
        *,
        repository: ExtractionRepository,
        event_publisher: EventPublisher,
        settings: IDPSettings,
        consumer_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = event_publisher
        self._settings = settings
        self._consumer_id = consumer_id or f"bbox-reaper-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info(
            "BboxReaper %s started (interval=%ds, refine_lease=%ds, pending_threshold=%ds)",
            self._consumer_id,
            self._settings.reaper_sweep_interval_s,
            self._settings.bbox_refine_lease_s,
            self._settings.partial_succeeded_orphan_threshold_s,
        )
        while not self._stop.is_set():
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                logger.exception("BboxReaper sweep failed; will retry next interval")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(1, self._settings.reaper_sweep_interval_s),
                )

    def stop(self) -> None:
        self._stop.set()

    async def _sweep(self) -> None:
        stale_refining = await self._repository.find_stale_bbox_refining(
            lease_seconds=self._settings.bbox_refine_lease_s
        )
        for extraction_id in stale_refining:
            await self._republish(extraction_id, reason="stale_running_bbox")
        pending_orphans = await self._repository.find_pending_bbox_revive(
            pending_threshold_seconds=self._settings.partial_succeeded_orphan_threshold_s,
            bbox_lease_seconds=self._settings.bbox_refine_lease_s,
        )
        for extraction_id in pending_orphans:
            await self._republish(extraction_id, reason="orphan_pending_bbox")

    async def _republish(self, extraction_id: str, *, reason: str) -> None:
        row = await self._repository.get(extraction_id)
        if row is None:
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
            "bbox-reaper",
            op="republish.post_processing",
            status="ok",
            latency_ms=0.0,
            extraction_id=extraction_id,
            reason=reason,
        )
        logger.info("BboxReaper republished extraction %s (%s)", extraction_id, reason)


__all__ = ["BboxReaper"]
