# Copyright 2026 Firefly Software Solutions Inc
"""``JobReaper`` -- periodic sweep to revive orphaned async jobs.

The job pipeline is at-least-once: every triggering event flows from
the API/worker → EDA outbox → drain → worker handler. When part of that
chain crashes we lose the event:

* Submit handler crashed between the row INSERT and the outbox INSERT.
* Worker crashed mid-extraction, leaving the row in ``RUNNING``.
* Worker's failure-path ``_delayed_publish`` task was killed before its
  delay completed, leaving the row in ``QUEUED`` after a ``requeue_for_retry``.

In any of those cases the row sits stuck because the bus has nothing to
deliver. The reaper closes the gap: it periodically queries for rows
whose state has been "frozen" longer than the lease / threshold,
republishes a fresh ``IDPJobSubmitted`` event, and lets the worker's
atomic ``mark_running`` claim decide the winner. Duplicate publishes
across replicas are deduped at claim time, so running this in every
worker container is safe.

Recovery time is bounded by ``settings.reaper_sweep_interval_s`` +
``settings.job_run_lease_s`` (for RUNNING orphans) or
``settings.queued_orphan_threshold_s`` (for QUEUED orphans).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings
from flydocs.core.observability import log_outbound
from flydocs.interfaces.dtos.event import IDPJobSubmittedEvent, envelope_for_publish
from flydocs.models.repositories import ExtractionJobRepository

logger = logging.getLogger(__name__)


class JobReaper:
    """Periodic sweep for orphaned QUEUED / RUNNING jobs."""

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
        self._consumer_id = consumer_id or f"reaper-{socket.gethostname()}"
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info(
            "JobReaper %s started (interval=%ds, run_lease=%ds, queued_threshold=%ds)",
            self._consumer_id,
            self._settings.reaper_sweep_interval_s,
            self._settings.job_run_lease_s,
            self._settings.queued_orphan_threshold_s,
        )
        while not self._stop.is_set():
            try:
                await self._sweep()
            except Exception:  # noqa: BLE001
                logger.exception("JobReaper sweep failed; will retry next interval")
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
        """One pass: re-publish for every job stuck past its threshold."""
        # Order matters very little -- duplicate publishes for the same
        # job are deduped at claim time. We still run them in two
        # distinct queries so a partial failure (one query OK, the other
        # raising) doesn't lose the half that succeeded.
        stale_running = await self._repository.find_stale_running(
            lease_seconds=self._settings.job_run_lease_s
        )
        for job_id in stale_running:
            await self._republish(job_id, reason="stale_running")
        stale_queued = await self._repository.find_stale_queued(
            older_than_seconds=self._settings.queued_orphan_threshold_s
        )
        for job_id in stale_queued:
            await self._republish(job_id, reason="orphan_queued")

    async def _republish(self, job_id: str, *, reason: str) -> None:
        event = IDPJobSubmittedEvent(job_id=job_id, attempt=1)
        await self._publisher.publish(
            destination=self._settings.jobs_topic,
            event_type=self._settings.jobs_event_type,
            payload=envelope_for_publish(event),
        )
        log_outbound(
            "reaper",
            op="republish.job",
            status="ok",
            latency_ms=0.0,
            job_id=job_id,
            reason=reason,
        )
        logger.info("JobReaper republished job %s (%s)", job_id, reason)
