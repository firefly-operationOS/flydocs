# Copyright 2026 Firefly Software Solutions Inc
"""``GetJobResultHandler`` -- terminal result for a SUCCEEDED job."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydesk_idp.interfaces.dtos.extract import ExtractionResult
from flydesk_idp.interfaces.dtos.job import JobResult
from flydesk_idp.interfaces.enums.job_status import JobStatus
from flydesk_idp.models.repositories import ExtractionJobRepository


@dataclass(frozen=True)
class GetJobResultQuery(Query[JobResult | None]):
    job_id: str


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
        status = JobStatus(job.status)
        if status != JobStatus.SUCCEEDED:
            raise JobNotReady(job.id, status)
        if not job.result_json:
            raise RuntimeError(f"Job {job.id} is SUCCEEDED but has no result_json")
        return JobResult(
            job_id=job.id,
            result=ExtractionResult.model_validate(job.result_json),
        )
