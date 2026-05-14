# Copyright 2026 Firefly Software Solutions Inc
"""Asynchronous job endpoints -- ``POST /api/v1/jobs`` + lifecycle."""

from __future__ import annotations

import logging

from pyfly.container import rest_controller
# Depend on the concrete bus classes -- pyfly's container resolves by
# exact type and the CQRS auto-config registers DefaultCommandBus /
# DefaultQueryBus (the Protocols are not registered as bean types).
from pyfly.cqrs import DefaultCommandBus, DefaultQueryBus
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
from flydesk_idp.core.services.jobs.submit_job_handler import InvalidRequestError
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
    """REST adapter for the asynchronous, queue-backed extraction API.

    The four endpoints cover the full job lifecycle: submit (returns
    a job id and 202), poll status, fetch the final result, cancel.
    Submit honours an ``Idempotency-Key`` header so a retried submission
    returns the original response instead of a duplicate job.
    """

    def __init__(self, commands: DefaultCommandBus, queries: DefaultQueryBus) -> None:
        self._commands = commands
        self._queries = queries

    @post_mapping("", status_code=202)
    async def submit(
        self,
        request: Valid[Body[SubmitJobRequest]],
        idempotency_key: Header[str] = "",
    ) -> SubmitJobResponse:
        """Submit a queued extraction job.

        The request body is the same as ``POST /api/v1/extract`` plus
        the optional ``callback_url`` and ``metadata`` fields. The
        endpoint persists the job, publishes it to the queue, and
        returns ``202 Accepted`` with the new ``job_id`` and the
        initial ``QUEUED`` status. The worker drives the same pipeline
        as the sync endpoint behind the scenes.

        Send the same ``Idempotency-Key`` header to replay an existing
        submission instead of creating a duplicate job. The handler also
        runs the semantic ``RequestValidator`` before persisting the job;
        a mismatch -- e.g. a rule referencing an unknown documentType --
        returns ``422 invalid_request`` with every issue surfaced, and
        nothing is written to Postgres or Redis.
        """
        try:
            return await self._commands.send(
                SubmitJobCommand(request=request, idempotency_key=idempotency_key or None)
            )
        except InvalidRequestError as exc:
            raise _http_problem_with_payload(
                status_code=422,
                code="invalid_request",
                title="Request failed semantic validation",
                detail=(
                    f"{len(exc.report.errors)} error(s) and "
                    f"{len(exc.report.warnings)} warning(s) detected before queueing."
                ),
                extra=exc.report.to_payload(),
            ) from exc

    @get_mapping("/{job_id}")
    async def get_status(self, job_id: PathVar[str]) -> JobStatusResponse:
        """Read the current status of a job.

        Returns the job's lifecycle metadata (``QUEUED`` / ``RUNNING``
        / ``SUCCEEDED`` / ``FAILED`` / ``CANCELLED``), the attempt
        counter, and the timestamps for submission / start / finish.
        Returns ``404`` for an unknown ``job_id``.
        """
        status = await self._queries.query(GetJobQuery(job_id=job_id))
        if status is None:
            raise ResourceNotFoundException(
                f"Job {job_id!r} not found", code="JOB_NOT_FOUND", context={"job_id": job_id}
            )
        return status

    @get_mapping("/{job_id}/result")
    async def get_result(self, job_id: PathVar[str]) -> JobResult:
        """Fetch the final ``ExtractionResult`` of a finished job.

        Valid only once the job is ``SUCCEEDED`` -- while it's still
        running or queued the endpoint returns ``409 job_not_ready``.
        Unknown ``job_id`` returns ``404``.
        """
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
        """Cancel a job that hasn't started yet.

        Only valid while ``status == QUEUED``. After the worker has
        started on a job there is no mid-flight cancellation hook --
        the endpoint returns ``409 job_not_cancellable``. Unknown
        ``job_id`` returns ``404``.
        """
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


def _http_problem_with_payload(
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    extra: dict,
) -> Exception:
    """RFC 7807-ish problem-detail that also surfaces the validator findings."""
    from fastapi import HTTPException

    body = {"code": code, "title": title, "detail": detail, **extra}
    return HTTPException(status_code=status_code, detail=body)
