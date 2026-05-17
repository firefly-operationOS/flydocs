# Copyright 2026 Firefly Software Solutions Inc
"""``GetJobHandler`` -- status lookup for an async job."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydocs.interfaces.dtos.job import JobStatusResponse
from flydocs.interfaces.enums.job_status import JobStatus
from flydocs.models.repositories import ExtractionJobRepository


@dataclass(frozen=True)
class GetJobQuery(Query[JobStatusResponse | None]):
    job_id: str


@query_handler
@service
class GetJobHandler(QueryHandler[GetJobQuery, JobStatusResponse | None]):
    def __init__(self, repository: ExtractionJobRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetJobQuery) -> JobStatusResponse | None:
        job = await self._repository.get(query.job_id)
        if job is None:
            return None
        return JobStatusResponse(
            job_id=job.id,
            status=JobStatus(job.status),
            submitted_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            attempts=job.attempts,
            error_code=job.error_code,
            error_message=job.error_message,
            bbox_refine_status=job.bbox_refine_status,
            bbox_refine_attempts=job.bbox_refine_attempts or 0,
            bbox_refine_started_at=job.bbox_refine_started_at,
            bbox_refine_finished_at=job.bbox_refine_finished_at,
            bbox_refine_error_code=job.bbox_refine_error_code,
            bbox_refine_error_message=job.bbox_refine_error_message,
        )
