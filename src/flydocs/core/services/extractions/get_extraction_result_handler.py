# Copyright 2026 Firefly Software Solutions Inc
"""``GetExtractionResultHandler`` -- result reader for extractions with a result available.

Only :class:`ExtractionStatus.SUCCEEDED` carries a readable result.
``QUEUED`` / ``RUNNING`` raises :class:`ExtractionNotReady` (mapped to
RFC 7807 409). ``FAILED`` / ``CANCELLED`` likewise return 409 -- they
will never produce a result no matter how long the caller waits.

The optional ``wait_for_post_processing`` long-poll waits for the
additive bbox-refinement leg to finish before returning the result so
callers that need grounded coordinates can block instead of polling.
The main extraction status is already terminal at the point we enter
this code path; only the post-processing block changes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydocs.interfaces.dtos.extract import ExtractionResult
from flydocs.interfaces.dtos.extraction import ExtractionResultEnvelope
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)
from flydocs.models.repositories import ExtractionRepository

# Statuses we never block on -- they will never produce a result no
# matter how long we wait.
_TERMINAL_NO_RESULT = (ExtractionStatus.FAILED, ExtractionStatus.CANCELLED)


@dataclass(frozen=True)
class GetExtractionResultQuery(Query[ExtractionResultEnvelope | None]):
    extraction_id: str
    # Long-poll knobs. ``wait_for_post_processing`` blocks the request
    # until the post-processing leg finishes or ``timeout_s`` elapses;
    # at timeout the handler returns whatever's currently persisted.
    wait_for_post_processing: bool = False
    timeout_s: float = 60.0
    poll_interval_s: float = 1.0


class ExtractionNotReady(RuntimeError):
    def __init__(self, extraction_id: str, status: ExtractionStatus) -> None:
        super().__init__(f"Extraction {extraction_id!r} is in status {status.value}")
        self.extraction_id = extraction_id
        self.status = status


@query_handler
@service
class GetExtractionResultHandler(QueryHandler[GetExtractionResultQuery, ExtractionResultEnvelope | None]):
    def __init__(self, repository: ExtractionRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetExtractionResultQuery) -> ExtractionResultEnvelope | None:
        row = await self._repository.get(query.extraction_id)
        if row is None:
            return None

        # Optional long-poll: block until the additive post-processing
        # leg finishes. The main pipeline is already terminal at this
        # point; the bbox-leg may still progress from pending/running
        # to succeeded/failed in the background.
        if query.wait_for_post_processing:
            polled = await self._poll_for_terminal(query)
            if polled is None:
                # Row was deleted under us mid-poll; treat as not-found.
                return None
            row = polled

        status = ExtractionStatus(row.status)
        if not status.has_result:
            if status in _TERMINAL_NO_RESULT:
                raise ExtractionNotReady(row.id, status)
            raise ExtractionNotReady(row.id, status)
        if not row.result_json:
            raise RuntimeError(f"Extraction {row.id} has status {status.value} but no result_json")
        return ExtractionResultEnvelope(
            id=row.id,
            result=ExtractionResult.model_validate(row.result_json),
        )

    async def _poll_for_terminal(self, query: GetExtractionResultQuery):
        """Block until the post-processing leg reaches a stable state.

        Stable states (any one of these stops the loop):
          * Main pipeline is in a non-success terminal state: ``FAILED`` /
            ``CANCELLED`` -- there's nothing to wait for.
          * Post-processing bbox status is ``succeeded`` or ``failed``.
          * No post-processing leg exists (``post_processing_bbox_status``
            is NULL) -- result is already final.

        Returns whatever's currently persisted; never raises on
        timeout (the result is still a valid response shape).
        """
        deadline = asyncio.get_running_loop().time() + max(0.0, query.timeout_s)
        interval = max(0.1, query.poll_interval_s)
        last = await self._repository.get(query.extraction_id)
        while last is not None:
            status = ExtractionStatus(last.status)
            bbox_status = last.post_processing_bbox_status
            if status in _TERMINAL_NO_RESULT:
                return last
            if bbox_status is None:
                return last
            if bbox_status in (
                PostProcessingStatus.SUCCEEDED.value,
                PostProcessingStatus.FAILED.value,
            ):
                return last
            if asyncio.get_running_loop().time() >= deadline:
                return last
            await asyncio.sleep(interval)
            last = await self._repository.get(query.extraction_id)
        return last  # type: ignore[return-value]


__all__ = [
    "ExtractionNotReady",
    "GetExtractionResultHandler",
    "GetExtractionResultQuery",
]
