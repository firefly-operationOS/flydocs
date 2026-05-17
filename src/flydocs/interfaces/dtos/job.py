# Copyright 2026 Firefly Software Solutions Inc
"""DTOs for the async (queue-backed) API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field

from flydocs.interfaces.dtos.doc import DocSpec
from flydocs.interfaces.dtos.extract import DocumentInput, ExtractionOptions, ExtractionResult
from flydocs.interfaces.dtos.rule import RuleSpec
from flydocs.interfaces.enums.job_status import JobStatus


class SubmitJobRequest(BaseModel):
    """Async-job submit payload.

    Mirrors :class:`flydocs.interfaces.dtos.extract.ExtractionRequest`:
    every submission carries a non-empty ``documents`` list. A single
    file is just a one-element list — the worker pipeline never branches
    on cardinality.
    """

    intention: str = "Extract structured data from the document."
    documents: list[DocumentInput] = Field(
        ...,
        min_length=1,
        description="Input files. Each entry is processed independently by the pipeline.",
    )
    docs: list[DocSpec] = Field(..., min_length=1)
    rules: list[RuleSpec] = Field(default_factory=list)
    options: ExtractionOptions = Field(default_factory=ExtractionOptions)
    callback_url: AnyHttpUrl | None = Field(
        default=None,
        description="If set, the worker POSTs a JobWebhookPayload here on terminal status.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


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
