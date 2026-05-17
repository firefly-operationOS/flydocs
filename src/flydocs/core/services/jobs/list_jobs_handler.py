# Copyright 2026 Firefly Software Solutions Inc
"""``ListJobsHandler`` -- paginated, filterable listing of extraction jobs.

Exposed at ``GET /api/v1/jobs`` by :class:`JobsController`. Filters are
optional and combine with ``AND``; the response is paginated and the
total reflects the FILTERED set (not the table size). Rows come back
ordered ``created_at DESC`` so the most recent activity surfaces first
in dashboards / operator tooling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydocs.interfaces.dtos.job import JobListResponse, JobStatusResponse
from flydocs.interfaces.enums.job_status import JobStatus
from flydocs.models.repositories import ExtractionJobRepository


@dataclass(frozen=True)
class ListJobsQuery(Query[JobListResponse]):
    """Filters + pagination for ``GET /api/v1/jobs``."""

    statuses: tuple[JobStatus, ...] = ()
    bbox_refine_statuses: tuple[str, ...] = ()
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = 50
    offset: int = 0


@query_handler
@service
class ListJobsHandler(QueryHandler[ListJobsQuery, JobListResponse]):
    def __init__(self, repository: ExtractionJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListJobsQuery) -> JobListResponse:
        rows, total = await self._repository.list_jobs(
            statuses=[s.value for s in query.statuses] or None,
            bbox_refine_statuses=list(query.bbox_refine_statuses) or None,
            created_after=query.created_after,
            created_before=query.created_before,
            idempotency_key=query.idempotency_key,
            limit=query.limit,
            offset=query.offset,
        )
        items = [
            JobStatusResponse(
                job_id=r.id,
                status=JobStatus(r.status),
                submitted_at=r.created_at,
                started_at=r.started_at,
                finished_at=r.finished_at,
                attempts=r.attempts or 0,
                error_code=r.error_code,
                error_message=r.error_message,
                bbox_refine_status=r.bbox_refine_status,
                bbox_refine_attempts=r.bbox_refine_attempts or 0,
                bbox_refine_started_at=r.bbox_refine_started_at,
                bbox_refine_finished_at=r.bbox_refine_finished_at,
                bbox_refine_error_code=r.bbox_refine_error_code,
                bbox_refine_error_message=r.bbox_refine_error_message,
            )
            for r in rows
        ]
        return JobListResponse(items=items, total=total, limit=query.limit, offset=query.offset)


__all__ = ["ListJobsQuery", "ListJobsHandler"]
