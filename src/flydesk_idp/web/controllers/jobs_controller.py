# Copyright 2026 Firefly Software Solutions Inc
"""Asynchronous job endpoints -- ``POST /api/v1/jobs`` + lifecycle."""

from __future__ import annotations

import logging

from pyfly.container import rest_controller
from pyfly.cqrs import CommandBus, QueryBus
from pyfly.kernel import ResourceNotFoundException
from pyfly.web import (
    Body,
    Header,
    PathVar,
    Valid,
    delete_mapping,
    get_mapping,
    post_mapping,
    request_mapping,
)

from flydesk_idp.core.services.jobs import (
    CancelJobCommand,
    GetJobQuery,
    GetJobResultQuery,
    SubmitJobCommand,
)
from flydesk_idp.core.services.jobs.cancel_job_handler import JobNotCancellable
from flydesk_idp.core.services.jobs.get_job_result_handler import JobNotReady
from flydesk_idp.interfaces.dtos.job import (
    JobResult,
    JobStatusResponse,
    SubmitJobRequest,
    SubmitJobResponse,
)

logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1/jobs")
class JobsController:
    def __init__(self, commands: CommandBus, queries: QueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping("", status_code=202)
    async def submit(
        self,
        request: Valid[Body[SubmitJobRequest]],
        idempotency_key: Header[str] = "",
    ) -> SubmitJobResponse:
        return await self._commands.send(
            SubmitJobCommand(request=request, idempotency_key=idempotency_key or None)
        )

    @get_mapping("/{job_id}")
    async def get_status(self, job_id: PathVar[str]) -> JobStatusResponse:
        status = await self._queries.query(GetJobQuery(job_id=job_id))
        if status is None:
            raise ResourceNotFoundException(
                f"Job {job_id!r} not found", code="JOB_NOT_FOUND", context={"job_id": job_id}
            )
        return status

    @get_mapping("/{job_id}/result")
    async def get_result(self, job_id: PathVar[str]) -> JobResult:
        try:
            result = await self._queries.query(GetJobResultQuery(job_id=job_id))
        except JobNotReady as exc:
            raise _http_problem(409, "job_not_ready", "Job not ready", str(exc)) from exc
        if result is None:
            raise ResourceNotFoundException(
                f"Job {job_id!r} not found", code="JOB_NOT_FOUND", context={"job_id": job_id}
            )
        return result

    @delete_mapping("/{job_id}")
    async def cancel(self, job_id: PathVar[str]) -> JobStatusResponse:
        try:
            cancelled = await self._commands.send(CancelJobCommand(job_id=job_id))
        except JobNotCancellable as exc:
            raise _http_problem(
                409, "job_not_cancellable", "Job cannot be cancelled", str(exc)
            ) from exc
        if cancelled is None:
            raise ResourceNotFoundException(
                f"Job {job_id!r} not found", code="JOB_NOT_FOUND", context={"job_id": job_id}
            )
        return cancelled


def _http_problem(status_code: int, code: str, title: str, detail: str) -> Exception:
    from fastapi import HTTPException

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "title": title, "detail": detail},
    )
