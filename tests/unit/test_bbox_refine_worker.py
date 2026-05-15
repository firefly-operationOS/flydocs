# Copyright 2026 Firefly Software Solutions Inc
"""``BboxRefineWorker`` -- second-stage EDA worker behaviour."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.bbox import (
    BboxRefiner,
    NoneOcrEngine,
    PyMuPDFWordExtractor,
    ValueMatcher,
    WordRouter,
)
from flydesk_idp.core.services.binary import BinaryNormalizer
from flydesk_idp.core.services.binary.archive import ArchiveUnpacker
from flydesk_idp.core.services.binary.email import EmailUnpacker
from flydesk_idp.core.services.binary.image import ImageNormalizer
from flydesk_idp.core.services.binary.libreoffice import LibreOfficeConverter
from flydesk_idp.core.services.binary.pdf_guard import PdfGuard
from flydesk_idp.core.services.workers.bbox_refine_worker import BboxRefineWorker
from flydesk_idp.interfaces.dtos.bbox import BboxSource, BoundingBox
from flydesk_idp.interfaces.dtos.extract import ExtractedDocument, ExtractionResult
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.enums.job_status import JobStatus


def _real_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Customer: Acme Corporation Madrid")
    c.showPage()
    c.save()
    return buf.getvalue()


def _result_with_field(value: str) -> ExtractionResult:
    field_ = ExtractedField(
        fieldName="customer_name",
        fieldValueFound=value,
        pagesFound=[1],
        bbox=BoundingBox(xmin=0.05, ymin=0.05, xmax=0.95, ymax=0.95),
    )
    group = ExtractedFieldGroup(fieldGroupName="customer", fieldGroupFields=[field_])
    doc = ExtractedDocument(
        document_type="invoice",
        pages=[1],
        fields=[group],
        source_file="invoice.pdf",
    )
    return ExtractionResult(
        request_id="00000000-0000-0000-0000-000000000001",
        documents=[doc],
        model="anthropic:claude-sonnet-4-6",
        latency_ms=1000,
    )


# ----------------------------------------------------------------- stubs


@dataclass
class _StubJob:
    id: str = "job-1"
    status: str = JobStatus.PARTIAL_SUCCEEDED.value
    filename: str = "invoice.pdf"
    schema_json: dict[str, Any] = field(default_factory=dict)
    options_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)
    metadata_json: dict[str, Any] = field(default_factory=dict)
    callback_url: str | None = None
    bbox_refine_status: str | None = "pending"
    bbox_refine_attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _StubRepo:
    def __init__(self, job: _StubJob) -> None:
        self.job = job
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, job_id: str) -> _StubJob | None:
        return self.job if self.job.id == job_id else None

    async def mark_bbox_refining(self, job_id: str) -> _StubJob | None:
        self.job.status = JobStatus.REFINING_BBOXES.value
        self.job.bbox_refine_status = "running"
        self.job.bbox_refine_attempts = (self.job.bbox_refine_attempts or 0) + 1
        self.calls.append(("mark_bbox_refining", {"job_id": job_id}))
        return self.job

    async def mark_bbox_refined(self, job_id: str, *, result: dict[str, Any]) -> _StubJob | None:
        self.job.status = JobStatus.SUCCEEDED.value
        self.job.bbox_refine_status = "succeeded"
        self.job.result_json = result
        self.calls.append(("mark_bbox_refined", {"job_id": job_id}))
        return self.job

    async def mark_bbox_refine_failed(self, job_id: str, *, code: str, message: str) -> _StubJob | None:
        self.job.status = JobStatus.PARTIAL_SUCCEEDED.value
        self.job.bbox_refine_status = "failed"
        self.calls.append(("mark_bbox_refine_failed", {"code": code, "message": message}))
        return self.job

    async def update(self, job_id: str, **changes: Any) -> _StubJob | None:
        for k, v in changes.items():
            setattr(self.job, k, v)
        self.calls.append(("update", changes))
        return self.job


class _StubPublisher:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


class _StubWebhook:
    def __init__(self) -> None:
        self.delivered: list[tuple[str, Any]] = []

    async def deliver(self, url: str, payload: Any, *, extra_headers: dict[str, str]) -> None:
        self.delivered.append((url, payload))


def _make_normalizer() -> BinaryNormalizer:
    settings = IDPSettings(office_converter="libreoffice")
    return BinaryNormalizer(
        settings=settings,
        pdf_guard=PdfGuard(),
        image=ImageNormalizer(),
        office=LibreOfficeConverter(settings=settings),
        archive=ArchiveUnpacker(settings=settings),
        email_=EmailUnpacker(),
    )


def _make_refiner() -> BboxRefiner:
    settings = IDPSettings(bbox_refine_threshold=0.85, bbox_refine_min_text_words=3)
    return BboxRefiner(
        router=WordRouter(pymupdf=PyMuPDFWordExtractor(settings), ocr=NoneOcrEngine()),
        matcher=ValueMatcher(settings),
    )


def _make_worker(repo: _StubRepo, publisher: _StubPublisher, webhook: _StubWebhook) -> BboxRefineWorker:
    return BboxRefineWorker(
        repository=repo,  # type: ignore[arg-type]
        event_publisher=publisher,  # type: ignore[arg-type]
        webhook=webhook,  # type: ignore[arg-type]
        normalizer=_make_normalizer(),
        refiner=_make_refiner(),
        settings=IDPSettings(),
    )


# ----------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_grounds_partial_succeeded_job_and_transitions_to_succeeded() -> None:
    pdf = _real_pdf()
    job = _StubJob(
        schema_json={
            "document_content_base64": base64.b64encode(pdf).decode(),
            "document_content_type": "application/pdf",
        },
        result_json=_result_with_field("Acme Corporation").model_dump(mode="json", by_alias=True),
    )
    repo = _StubRepo(job)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(job.id)

    assert job.status == JobStatus.SUCCEEDED.value
    assert job.bbox_refine_status == "succeeded"
    assert [name for name, _ in repo.calls] == ["mark_bbox_refining", "mark_bbox_refined"]
    # No webhook delivered because the stub job has no callback_url.
    assert webhook.delivered == []
    # No retry was scheduled.
    assert publisher.published == []
    # The refined result should now carry source=pdf_text on the field.
    refined = ExtractionResult.model_validate(job.result_json)
    field_ = refined.documents[0].fields[0].fieldGroupFields[0]
    assert field_.bbox.source == BboxSource.PDF_TEXT


@pytest.mark.asyncio
async def test_skips_jobs_not_in_partial_succeeded() -> None:
    job = _StubJob(status=JobStatus.SUCCEEDED.value, result_json={})
    repo = _StubRepo(job)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(job.id)

    # No state transitions, no retries, no webhooks.
    assert repo.calls == []
    assert webhook.delivered == []
    assert publisher.published == []


@pytest.mark.asyncio
async def test_drops_unknown_job_id() -> None:
    repo = _StubRepo(_StubJob(id="other"))
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process("missing")

    assert repo.calls == []
    assert webhook.delivered == []
    assert publisher.published == []


@pytest.mark.asyncio
async def test_permanent_error_marks_failed_no_republish() -> None:
    # Empty schema_json triggers a permanent ValueError ("no document_content_base64").
    job = _StubJob(
        schema_json={},
        result_json=_result_with_field("Acme").model_dump(mode="json", by_alias=True),
    )
    repo = _StubRepo(job)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(job.id)

    assert job.status == JobStatus.PARTIAL_SUCCEEDED.value
    assert job.bbox_refine_status == "failed"
    names = [name for name, _ in repo.calls]
    assert "mark_bbox_refine_failed" in names
    assert publisher.published == []  # never republish on permanent
