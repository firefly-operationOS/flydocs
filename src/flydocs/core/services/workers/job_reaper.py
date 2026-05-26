# Copyright 2026 Firefly Software Solutions Inc
"""``ExtractionReaper`` -- periodic sweep to revive orphaned async extractions.

The extraction pipeline is at-least-once: every triggering event flows
from the API/worker → EDA outbox → drain → worker handler. When part
of that chain crashes we lose the event:

* Submit handler crashed between the row INSERT and the outbox INSERT.
* Worker crashed mid-extraction, leaving the row in ``running``.
* Worker's failure-path ``_delayed_publish`` task was killed before its
  delay completed, leaving the row in ``queued`` after a
  ``requeue_for_retry``.

In any of those cases the row sits stuck because the bus has nothing to
deliver. The reaper closes the gap: it periodically queries for rows
whose state has been "frozen" longer than the lease / threshold,
republishes a fresh ``extraction.submitted`` event, and lets the
worker's atomic ``mark_running`` claim decide the winner. Duplicate
publishes across replicas are deduped at claim time, so running this
in every worker container is safe.

Recovery time is bounded by ``settings.reaper_sweep_interval_s`` +
``settings.job_run_lease_s`` (for running orphans) or
``settings.queued_orphan_threshold_s`` (for queued orphans).
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
    EVENT_TYPE_EXTRACTION_SUBMITTED,
    EventEnvelope,
    envelope_for_publish,
)
from flydocs.models.repositories import ExtractionRepository

logger = logging.getLogger(__name__)


class ExtractionReaper:
    """Periodic sweep for orphaned queued / running extractions."""

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
        self._consumer_id = consumer_id or f"reaper-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info(
            "ExtractionReaper %s started (interval=%ds, run_lease=%ds, queued_threshold=%ds)",
            self._consumer_id,
            self._settings.reaper_sweep_interval_s,
            self._settings.job_run_lease_s,
            self._settings.queued_orphan_threshold_s,
        )
        while not self._stop.is_set():
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                logger.exception("ExtractionReaper sweep failed; will retry next interval")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(1, self._settings.reaper_sweep_interval_s),
                )

    def stop(self) -> None:
        self._stop.set()

    async def _sweep(self) -> None:
        """One pass: re-publish for every extraction stuck past its threshold."""
        # Order matters very little -- duplicate publishes for the same
        # row are deduped at claim time. We still run them in two
        # distinct queries so a partial failure (one query OK, the other
        # raising) doesn't lose the half that succeeded.
        stale_running = await self._repository.find_stale_running(
            lease_seconds=self._settings.job_run_lease_s
        )
        for extraction_id in stale_running:
            await self._republish(extraction_id, reason="stale_running")
        stale_queued = await self._repository.find_stale_queued(
            older_than_seconds=self._settings.queued_orphan_threshold_s
        )
        for extraction_id in stale_queued:
            await self._republish(extraction_id, reason="orphan_queued")

    async def _republish(self, extraction_id: str, *, reason: str) -> None:
        row = await self._repository.get(extraction_id)
        if row is None:
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
            "reaper",
            op="republish.extraction",
            status="ok",
            latency_ms=0.0,
            extraction_id=extraction_id,
            reason=reason,
        )
        logger.info("ExtractionReaper republished extraction %s (%s)", extraction_id, reason)


# Backwards-compat alias for callers (CLI) that still import the old name.
JobReaper = ExtractionReaper


__all__ = ["ExtractionReaper", "JobReaper"]
