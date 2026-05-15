# Copyright 2026 Firefly Software Solutions Inc
"""``GetJobResultHandler`` -- result reader for jobs with a result available.

Supports both:

* **Fully complete** -- ``status == SUCCEEDED``: returns the grounded
  result (or the LLM-bbox result if bbox_refine was disabled).
* **Partial** -- ``status in {PARTIAL_SUCCEEDED, REFINING_BBOXES}``:
  returns the LLM-bbox result so callers don't have to wait for the
  out-of-band refiner before consuming field values. Callers that need
  the grounded version pass ``wait_for_bboxes=true`` so the handler
  polls until the refiner finishes (or a timeout fires).

Anything earlier than ``PARTIAL_SUCCEEDED`` (``QUEUED`` / ``RUNNING``)
or terminal-without-result (``FAILED`` / ``CANCELLED``) raises
:class:`JobNotReady` so the REST controller can surface an RFC 7807 409.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydesk_idp.interfaces.dtos.extract import ExtractionResult
from flydesk_idp.interfaces.dtos.job import JobResult
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.repositories import ExtractionJobRepository

# Statuses we never block on -- they will never produce a result no
# matter how long we wait.
_TERMINAL_NO_RESULT = (JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass(frozen=True)
class GetJobResultQuery(Query[JobResult | None]):
    job_id: str
    # Long-poll knobs. ``wait_for_bboxes`` blocks the request until the
    # refiner finishes (status -> SUCCEEDED) or ``timeout_s`` elapses;
    # at timeout the handler returns whatever's currently persisted.
    wait_for_bboxes: bool = False
    timeout_s: float = 60.0
    poll_interval_s: float = 1.0


class JobNotReady(RuntimeError):
    def __init__(self, job_id: str, status: JobStatus) -> None:
        super().__init__(f"Job {job_id!r} is in status {status.value}")
        self.job_id = job_id
        self.status = status


@query_handler
@service
class GetJobResultHandler(QueryHandler[GetJobResultQuery, JobResult | None]):
    def __init__(self, repository: ExtractionJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetJobResultQuery) -> JobResult | None:
        job = await self._repository.get(query.job_id)
        if job is None:
            return None

        # Optional long-poll for callers that want grounded bboxes only.
        # We block while the refiner is in flight, returning whatever's
        # in the row at timeout (which is always the partial result --
        # never None, since PARTIAL_SUCCEEDED requires result_json).
        if query.wait_for_bboxes:
            polled = await self._poll_for_terminal(query)
            if polled is None:
                # Job was deleted under us mid-poll; treat as not-found.
                return None
            job = polled

        status = JobStatus(job.status)
        if not status.has_result:
            if status in _TERMINAL_NO_RESULT:
                raise JobNotReady(job.id, status)
            raise JobNotReady(job.id, status)
        if not job.result_json:
            raise RuntimeError(f"Job {job.id} has status {status.value} but no result_json")
        return JobResult(
            job_id=job.id,
            result=ExtractionResult.model_validate(job.result_json),
        )

    async def _poll_for_terminal(self, query: GetJobResultQuery):
        """Block until the job reaches a stable, no-more-progress state.

        Stable states (any one of these stops the loop):
          * Main pipeline is terminal: ``SUCCEEDED`` / ``FAILED`` /
            ``CANCELLED``.
          * Bbox-refine leg has finished one way or the other:
            ``bbox_refine_status in {'succeeded', 'failed'}``. Once the
            refiner has succeeded the job is also ``SUCCEEDED``; once
            it has permanently failed the job stays
            ``PARTIAL_SUCCEEDED`` -- in either case there is no further
            asynchronous progress to wait for, so callers that asked
            for ``wait_for_bboxes`` should be unblocked immediately
            instead of polling until ``timeout_s`` elapses.

        Returns whatever's currently persisted; never raises on
        timeout (the partial result is still a valid response shape).
        """
        deadline = asyncio.get_running_loop().time() + max(0.0, query.timeout_s)
        interval = max(0.1, query.poll_interval_s)
        last = await self._repository.get(query.job_id)
        while last is not None:
            status = JobStatus(last.status)
            bbox_status = getattr(last, "bbox_refine_status", None)
            if status.is_terminal or bbox_status in ("succeeded", "failed"):
                return last
            if asyncio.get_running_loop().time() >= deadline:
                return last
            await asyncio.sleep(interval)
            last = await self._repository.get(query.job_id)
        # Job was deleted under us; fall through to outer handler which
        # will raise JobNotReady on the stale status. ``last`` is None
        # here only when the job disappeared, which today never happens
        # but is correctly defensive.
        return last  # type: ignore[return-value]
