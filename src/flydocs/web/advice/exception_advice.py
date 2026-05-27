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
from flydocs.core.services.extract.extract_handler import ExtractionTimedOut
from flydocs.core.services.extractions.cancel_extraction_handler import (
    ExtractionNotCancellable,
)
from flydocs.core.services.extractions.get_extraction_result_handler import (
    ExtractionNotReady,
)
from flydocs.interfaces.dtos.error import ProblemDetails


@controller_advice
class ExceptionAdvice:
    @exception_handler(ExtractionNotReady)
    async def extraction_not_ready(self, exc: ExtractionNotReady) -> dict[str, Any]:
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/not-ready",
            title="Extraction not ready",
            status=409,
            code="not_ready",
            detail=str(exc),
            extensions={
                "extraction_id": exc.extraction_id,
                "status": exc.status.value,
            },
        )
        return problem.model_dump(exclude_none=True)

    @exception_handler(ExtractionTimedOut)
    async def extraction_timed_out(self, exc: ExtractionTimedOut) -> dict[str, Any]:
        """Map ``ExtractionTimedOut`` (sync ceiling exceeded) to HTTP 408.

        The handler raises this when the in-process orchestrator exceeds
        ``FLYDOCS_SYNC_TIMEOUT_S``. Callers expecting long-running
        extractions should switch to ``POST /api/v1/extractions``.
        """
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/timeout",
            title="Extraction timed out",
            status=408,
            code="timeout",
            detail=str(exc),
            extensions={"timeout_s": exc.timeout_s},
        )
        return problem.model_dump(exclude_none=True)

    @exception_handler(ExtractionNotCancellable)
    async def extraction_not_cancellable(
        self, exc: ExtractionNotCancellable
    ) -> dict[str, Any]:
        problem = ProblemDetails(
            type="https://flydocs.dev/problems/not-cancellable",
            title="Extraction cannot be cancelled",
            status=409,
            code="not_cancellable",
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
