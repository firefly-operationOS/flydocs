# Copyright 2026 Firefly Software Solutions Inc
"""Global exception advice -- maps domain errors to RFC 7807 problem details.

``pyfly`` already converts most standard exceptions; this advice handles
the domain-specific ones flydocs throws so the API speaks
``application/problem+json`` end-to-end.
"""

from __future__ import annotations

from typing import Any

from pyfly.web import controller_advice, exception_handler

from flydocs.core.services.binary import BinaryNormalizationError
from flydocs.core.services.jobs.cancel_job_handler import JobNotCancellable
from flydocs.core.services.jobs.get_job_result_handler import JobNotReady
from flydocs.interfaces.dtos.error import ProblemDetails


@controller_advice
class ExceptionAdvice:
    @exception_handler(JobNotReady)
    async def job_not_ready(self, exc: JobNotReady) -> dict[str, Any]:
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/job-not-ready",
            title="Job not ready",
            status=409,
            code="job_not_ready",
            detail=str(exc),
            extensions={"job_id": exc.job_id, "status": exc.status.value},
        )
        return problem.model_dump(exclude_none=True)

    @exception_handler(JobNotCancellable)
    async def job_not_cancellable(self, exc: JobNotCancellable) -> dict[str, Any]:
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/job-not-cancellable",
            title="Job cannot be cancelled",
            status=409,
            code="job_not_cancellable",
            detail=str(exc),
        )
        return problem.model_dump(exclude_none=True)

    @exception_handler(BinaryNormalizationError)
    async def binary_normalization_failed(self, exc: BinaryNormalizationError) -> dict[str, Any]:
        """Map every BinaryNormalizationError subclass to a 422 problem-detail.

        ``code`` carries the subclass-specific stable identifier
        (``encrypted_pdf``, ``office_conversion_failed``, ...) so callers
        can branch on the failure mode without parsing ``detail``.
        Registered BEFORE the generic ``ValueError`` handler so the more
        specific one wins.
        """
        extensions: dict[str, Any] = {}
        if getattr(exc, "filename", None):
            extensions["filename"] = exc.filename
        problem = ProblemDetails(
            type=f"https://flydocs.dev/problems/{exc.code.replace('_', '-')}",
            title="Binary could not be normalised",
            status=exc.http_status,
            code=exc.code,
            detail=str(exc),
            extensions=extensions or None,
        )
        return problem.model_dump(exclude_none=True)

    @exception_handler(ValueError)
    async def invalid_request(self, exc: ValueError) -> dict[str, Any]:
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/invalid-request",
            title="Invalid request",
            status=400,
            code="invalid_request",
            detail=str(exc),
        )
        return problem.model_dump(exclude_none=True)
