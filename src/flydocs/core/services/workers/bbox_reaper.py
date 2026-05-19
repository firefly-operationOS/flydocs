# Copyright 2026 Firefly Software Solutions Inc
"""``BboxReaper`` -- periodic sweep for orphaned bbox-refine legs.

Bbox-leg analogue of :class:`JobReaper`. Two orphan classes:

* ``REFINING_BBOXES`` with stale ``bbox_refine_started_at`` -- the
  bbox worker that claimed the leg crashed past its lease.
* ``PARTIAL_SUCCEEDED`` with ``bbox_refine_status='pending'`` -- the
  initial bbox event was never published (main worker crashed
  between ``mark_partial_succeeded`` and ``publisher.publish``), or
  a prior bbox-leg retry's ``_delayed_publish`` task was lost.

Both are revived by republishing a fresh ``IDPBboxRefineRequested``
event; the bbox worker's atomic ``mark_bbox_refining`` claim dedupes
duplicate publishes from concurrent replicas.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.interfaces.dtos.event import (
    IDPBboxRefineRequestedEvent,
    envelope_for_publish,
)
from flydocs.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


class BboxReaper:
    """Periodic sweep for orphaned PARTIAL_SUCCEEDED / REFINING_BBOXES jobs."""

    def __init__(
        self,
        *,
        repository: ExtractionJobRepository,
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
            "BboxReaper %s started (interval=%ds, refine_lease=%ds, partial_threshold=%ds)",
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
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(1, self._settings.reaper_sweep_interval_s),
                )
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def _sweep(self) -> None:
        stale_refining = await self._repository.find_stale_refining_bboxes(
            lease_seconds=self._settings.bbox_refine_lease_s
        )
        for job_id in stale_refining:
            await self._republish(job_id, reason="stale_refining_bboxes")
        pending_orphans = await self._repository.find_pending_bbox_revive(
            partial_threshold_seconds=self._settings.partial_succeeded_orphan_threshold_s,
            bbox_lease_seconds=self._settings.bbox_refine_lease_s,
        )
        for job_id in pending_orphans:
            await self._republish(job_id, reason="orphan_partial_succeeded")

    async def _republish(self, job_id: str, *, reason: str) -> None:
        event = IDPBboxRefineRequestedEvent(job_id=job_id, attempt=1)
        await self._publisher.publish(
            destination=self._settings.bbox_refine_topic,
            event_type=self._settings.bbox_refine_event_type,
            payload=envelope_for_publish(event),
        )
        log_outbound(
            "bbox-reaper",
            op="republish.bbox_refine",
            status="ok",
            latency_ms=0.0,
            job_id=job_id,
            reason=reason,
        )
        logger.info("BboxReaper republished job %s (%s)", job_id, reason)
