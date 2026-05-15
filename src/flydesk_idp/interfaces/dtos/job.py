# Copyright 2026 Firefly Software Solutions Inc
"""DTOs for the async (queue-backed) API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field

from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.extract import DocumentInput, ExtractionOptions, ExtractionResult
from flydesk_idp.interfaces.dtos.rule import RuleSpec
from flydesk_idp.interfaces.enums.job_status import JobStatus


class SubmitJobRequest(BaseModel):
    intention: str = "Extract structured data from the document."
    document: DocumentInput
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
