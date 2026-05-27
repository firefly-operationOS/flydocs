# Copyright 2026 Firefly Software Solutions Inc
"""Synchronous extraction endpoint -- ``POST /api/v1/extract``."""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field
from pyfly.container import rest_controller

# Depend on the concrete bus class -- pyfly's container resolves by exact
# type and the CQRS auto-config registers ``DefaultCommandBus``, not the
# ``CommandBus`` Protocol.
from pyfly.cqrs import DefaultCommandBus
from pyfly.web import Body, Valid, post_mapping, request_mapping

from flydocs.config import IDPSettings
from flydocs.core.services.extract import ExtractCommand
from flydocs.core.services.extract.extract_handler import ExtractionTimedOut
from flydocs.core.services.validation import RequestValidator, ValidationReport
from flydocs.interfaces.dtos.extract import ExtractionRequest, ExtractionResult


class ValidationResponse(BaseModel):
    """Dry-run result of :class:`RequestValidator`.

    Returned by ``POST /api/v1/extract:validate`` -- always status 200,
    even when errors are present. The caller inspects ``ok`` to decide
    whether to submit the payload to the real ``POST /api/v1/extract``
    or ``POST /api/v1/extractions`` endpoints.
    """

    ok: bool = Field(description="True when the report has zero errors.")
    error_count: int = 0
    warning_count: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


logger = logging.getLogger(__name__)


@rest_controller
@request_mapping("/api/v1")
class ExtractController:
    """REST adapter for the synchronous extraction API.

    Blocks the HTTP connection while the pipeline runs the request.
    Beyond the per-stage timeouts enforced inside the orchestrator,
    the handler wraps the whole call in
    ``asyncio.wait_for(FLYDOCS_SYNC_TIMEOUT_S)``; if that elapses
    the caller gets a 408 ``timeout`` problem-detail and is
    expected to retry through ``POST /api/v1/extractions``.

    Two gates run *before* the request enters the pipeline so a
    malformed call never reaches the LLM provider:

    * size and base64 gates (``_enforce_size_limits``) cap the document
      bytes and validate the encoding,
    * :class:`RequestValidator` runs semantic cross-checks (rules
      reference real fields, no cycles, no duplicate ids, ...).
    """

    def __init__(
        self,
        commands: DefaultCommandBus,
        settings: IDPSettings,
        validator: RequestValidator,
    ) -> None:
        self._commands = commands
        self._settings = settings
        self._validator = validator

    @post_mapping("/extract:validate")
    async def validate(self, request: Valid[Body[ExtractionRequest]]) -> ValidationResponse:
        """Dry-run the semantic validator without executing the pipeline.

        Use this to check a payload from a CI pipeline, a UI before
        submit, or while iterating on rule definitions -- it costs
        nothing (no LLM call, no document load) and returns the same
        error / warning shape that ``POST /api/v1/extract`` would emit
        in a 422.

        Always returns ``200``; the caller inspects ``ok`` to decide
        whether to proceed. Identical schema for both errors and
        warnings: ``[{severity, code, message, path}]``.
        """
        report = self._validator.validate(request)
        return ValidationResponse(
            ok=not report.has_errors,
            error_count=len(report.errors),
            warning_count=len(report.warnings),
            errors=[i.to_dict() for i in report.errors],
            warnings=[i.to_dict() for i in report.warnings],
        )

    @post_mapping("/extract")
    async def extract(self, request: Valid[Body[ExtractionRequest]]) -> ExtractionResult:
        """Extract structured fields from a document.

        Runs the full pipeline -- multimodal extraction with normalised
        bounding boxes, optional structured validation, visual / content
        authenticity checks, LLM judge re-evaluation, and business-rule
        DAG evaluation -- and returns the assembled ``ExtractionResult``.

        Use this endpoint when you can wait for the answer
        (sub-minute, single document). For long-running or fire-and-forget
        workloads, prefer ``POST /api/v1/extractions``.

        Errors map to RFC 7807 problem-details:
        ``408 timeout`` (pipeline exceeded the sync ceiling),
        ``413 file_too_large`` (file over ``FLYDOCS_MAX_BYTES``),
        ``422 invalid_base64`` (``content_base64`` failed strict parsing),
        ``422 validation_failed`` (semantic mismatch detected by the
        :class:`RequestValidator`, e.g. a rule referencing an unknown
        document type -- the response includes a list of every issue
        found so the caller can fix them all at once).
        """
        _enforce_size_limits(request, max_bytes=self._settings.max_bytes)
        _enforce_semantic_validation(request, self._validator)
        try:
            return await self._commands.send(ExtractCommand(request=request))
        except ExtractionTimedOut as exc:
            raise _http_problem(408, "timeout", "Extraction timed out", str(exc)) from exc
        except TimeoutError as exc:
            raise _http_problem(408, "timeout", "Extraction timed out", str(exc)) from exc


def _enforce_size_limits(request: ExtractionRequest, *, max_bytes: int) -> None:
    """Per-file size + base64 sanity."""
    for file in request.files:
        encoded = file.content_base64 or ""
        decoded_size = (len(encoded) * 3) // 4
        if decoded_size > max_bytes:
            raise _http_problem(
                413,
                "file_too_large",
                "File too large",
                f"{file.filename} is {decoded_size} bytes (max {max_bytes})",
            )
        if encoded:
            try:
                base64.b64decode(encoded, validate=True)
            except Exception as exc:  # noqa: BLE001
                raise _http_problem(
                    422, "invalid_base64", "Invalid base64 content", f"{file.filename}: {exc}"
                ) from exc


def _enforce_semantic_validation(request: ExtractionRequest, validator: RequestValidator) -> None:
    """Reject the request with a 422 when the semantic validator finds errors."""
    report: ValidationReport = validator.validate(request)
    if report.has_errors:
        raise _http_problem_with_payload(
            status_code=422,
            code="validation_failed",
            title="Request failed semantic validation",
            detail=(
                f"{len(report.errors)} error(s) and {len(report.warnings)} "
                "warning(s) detected before the pipeline."
            ),
            extra=report.to_payload(),
        )
    if report.warnings:
        for issue in report.warnings:
            logger.warning(
                "request_validation_warning code=%s path=%s message=%s",
                issue.code,
                issue.path,
                issue.message,
            )


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
    """RFC 7807-ish problem-detail that also surfaces the validator's findings."""
    from fastapi import HTTPException

    body = {
        "code": code,
        "title": title,
        "detail": detail,
        **extra,
    }
    return HTTPException(status_code=status_code, detail=body)
