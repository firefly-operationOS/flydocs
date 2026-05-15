# Copyright 2026 Firefly Software Solutions Inc
"""DTOs for the async (queue-backed) API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.extract import DocumentInput, ExtractionOptions, ExtractionResult
from flydesk_idp.interfaces.dtos.rule import RuleSpec
from flydesk_idp.interfaces.enums.job_status import JobStatus


class SubmitJobRequest(BaseModel):
    """Async-job submit payload -- single-file or multi-file.

    Mirrors :class:`flydesk_idp.interfaces.dtos.extract.ExtractionRequest`
    in accepting either the legacy ``document`` (single file) shape or
    the multi-file ``documents`` shape. The two are mutually exclusive;
    use the :meth:`files` accessor to read the input as a uniform list.
    """

    intention: str = "Extract structured data from the document."
    document: DocumentInput | None = Field(
        default=None,
        description=(
            "Legacy single-file shape. Mutually exclusive with ``documents``. "
            "Internally promoted to ``documents = [document]`` by the handler."
        ),
    )
    documents: list[DocumentInput] = Field(
        default_factory=list,
        description=(
            "Multi-file input. Each entry is processed independently by the "
            "pipeline. Mutually exclusive with ``document``."
        ),
    )
    docs: list[DocSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)
    callback_url: AnyHttpUrl | None = Field(
        default=None,
        description="If set, the worker POSTs a JobWebhookPayload here on terminal status.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalise_documents(self) -> SubmitJobRequest:
        if self.document is not None and self.documents:
            raise ValueError(
                "request can carry either ``document`` (legacy single-file) "
                "or ``documents`` (multi-file) but not both"
            )
        if self.document is None and not self.documents:
            raise ValueError("request must carry either ``document`` or at least one entry in ``documents``")
        return self

    @property
    def files(self) -> list[DocumentInput]:
        """Return every input file as a uniform list, regardless of shape."""
        if self.documents:
            return list(self.documents)
        assert self.document is not None  # guaranteed by the model_validator
        return [self.document]


class SubmitJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: datetime


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
    bbox_refine_status: str | None = Field(
        default=None,
        description=(
            "Sub-state of the bbox-refine leg when ``options.stages.bbox_refine`` "
            "was enabled at submit time. One of ``pending`` (event published, "
            "worker hasn't picked it up), ``running``, ``succeeded``, ``failed``. "
            "``null`` when the job didn't ask for refinement."
        ),
    )
    bbox_refine_attempts: int = 0
    bbox_refine_started_at: datetime | None = None
    bbox_refine_finished_at: datetime | None = None
    bbox_refine_error_code: str | None = None
    bbox_refine_error_message: str | None = None


class JobResult(BaseModel):
    job_id: str
    result: ExtractionResult


class JobListQuery(BaseModel):
    """Query parameters for ``GET /api/v1/jobs``.

    All filters are optional and combine with ``AND``. ``statuses`` and
    ``bbox_refine_statuses`` are repeated query params (e.g.
    ``?status=SUCCEEDED&status=PARTIAL_SUCCEEDED``). ``created_after`` /
    ``created_before`` are RFC 3339 timestamps inclusive on both ends.
    """

    statuses: list[JobStatus] = Field(default_factory=list)
    bbox_refine_statuses: list[str] = Field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    items: list[JobStatusResponse]
    total: int
    limit: int
    offset: int
