# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``BboxRefineWorker`` -- second-stage EDA worker behaviour."""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fireflyframework_agentic.content.binary import BinaryConfig, BinaryNormalizer
from reportlab.pdfgen import canvas

from flydocs.config import IDPSettings
from flydocs.core.services.bbox import (
    BboxRefiner,
    NoneOcrEngine,
    PyMuPDFWordExtractor,
    ValueMatcher,
    WordRouter,
)
from flydocs.core.services.workers.bbox_refine_worker import BboxRefineWorker
from flydocs.interfaces.dtos.bbox import BboxSource, BoundingBox
from flydocs.interfaces.dtos.extract import Document, ExtractionResult, PipelineMeta
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.enums.extraction_status import ExtractionStatus


def _real_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Customer: Acme Corporation Madrid")
    c.showPage()
    c.save()
    return buf.getvalue()


def _result_with_field(value: str) -> ExtractionResult:
    field_ = ExtractedField(
        name="customer_name",
        value=value,
        pages=[1],
        bbox=BoundingBox(xmin=0.05, ymin=0.05, xmax=0.95, ymax=0.95),
    )
    group = ExtractedFieldGroup(name="customer", fields=[field_])
    doc = Document(
        type="invoice",
        pages=[1],
        field_groups=[group],
        source_file="invoice.pdf",
    )
    return ExtractionResult(
        id="ext_RESULT0000000000000000000000",
        files=[],
        documents=[doc],
        pipeline=PipelineMeta(model="anthropic:claude-sonnet-4-6", latency_ms=1000),
    )


# ----------------------------------------------------------------- stubs


@dataclass
class _StubExtraction:
    id: str = "ext_TEST00000000000000000000001"
    status: str = ExtractionStatus.SUCCEEDED.value
    filename: str = "invoice.pdf"
    schema_json: dict[str, Any] = field(default_factory=dict)
    options_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)
    metadata_json: dict[str, Any] = field(default_factory=dict)
    callback_url: str | None = None
    post_processing_bbox_status: str | None = "pending"
    post_processing_bbox_attempts: int = 0
    post_processing_bbox_started_at: datetime | None = None
    post_processing_bbox_finished_at: datetime | None = None
    post_processing_bbox_error_code: str | None = None
    post_processing_bbox_error_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _StubRepo:
    def __init__(self, ext: _StubExtraction) -> None:
        self.ext = ext
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, ext_id: str) -> _StubExtraction | None:
        return self.ext if self.ext.id == ext_id else None

    async def claim_bbox_refinement(self, ext_id: str, *, lease_seconds: int) -> _StubExtraction | None:
        # Production semantics: claim only when main is succeeded AND the
        # sub-status is pending (or stale running).
        self.calls.append(("claim_bbox_refinement", {"ext_id": ext_id, "lease_seconds": lease_seconds}))
        if self.ext.status != ExtractionStatus.SUCCEEDED.value:
            return None
        if self.ext.post_processing_bbox_status not in ("pending", "running"):
            return None
        self.ext.post_processing_bbox_status = "running"
        self.ext.post_processing_bbox_attempts = (self.ext.post_processing_bbox_attempts or 0) + 1
        return self.ext

    async def complete_bbox_refinement(
        self, ext_id: str, *, result: dict[str, Any]
    ) -> _StubExtraction | None:
        self.ext.post_processing_bbox_status = "succeeded"
        self.ext.result_json = result
        self.calls.append(("complete_bbox_refinement", {"ext_id": ext_id}))
        return self.ext

    async def fail_bbox_refinement(self, ext_id: str, *, code: str, message: str) -> _StubExtraction | None:
        self.ext.post_processing_bbox_status = "failed"
        self.calls.append(("fail_bbox_refinement", {"code": code, "message": message}))
        return self.ext

    async def update(self, ext_id: str, **changes: Any) -> _StubExtraction | None:
        for k, v in changes.items():
            setattr(self.ext, k, v)
        self.calls.append(("update", changes))
        return self.ext

    async def requeue_bbox_refinement(self, ext_id: str) -> _StubExtraction | None:
        if self.ext.post_processing_bbox_status != "running":
            return None
        self.ext.post_processing_bbox_status = "pending"
        self.calls.append(("requeue_bbox_refinement", {"ext_id": ext_id}))
        return self.ext


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
    return BinaryNormalizer(config=BinaryConfig(office_converter="libreoffice", wrap_text_as_pdf=True))


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
async def test_grounds_succeeded_extraction_and_marks_bbox_leg_succeeded() -> None:
    pdf = _real_pdf()
    ext = _StubExtraction(
        schema_json={
            "files": [
                {
                    "filename": "invoice.pdf",  # matches result.documents[0].source_file
                    "content_base64": base64.b64encode(pdf).decode(),
                    "content_type": "application/pdf",
                }
            ],
        },
        result_json=_result_with_field("Acme Corporation").model_dump(mode="json", by_alias=True),
    )
    repo = _StubRepo(ext)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(ext.id)

    # Main status stays succeeded; only the bbox sub-status moves.
    assert ext.status == ExtractionStatus.SUCCEEDED.value
    assert ext.post_processing_bbox_status == "succeeded"
    assert [name for name, _ in repo.calls] == [
        "claim_bbox_refinement",
        "complete_bbox_refinement",
    ]
    # No webhook delivered because the stub has no callback_url.
    assert webhook.delivered == []
    # No retry was scheduled.
    assert publisher.published == []
    # The refined result should now carry source=pdf_text on the field.
    refined = ExtractionResult.model_validate(ext.result_json)
    field_ = refined.documents[0].field_groups[0].fields[0]
    assert field_.bbox.source == BboxSource.PDF_TEXT


@pytest.mark.asyncio
async def test_skips_extractions_whose_bbox_leg_is_not_claimable() -> None:
    """Main status succeeded but bbox sub-status already terminal -> no-op."""
    ext = _StubExtraction(
        status=ExtractionStatus.SUCCEEDED.value,
        post_processing_bbox_status="succeeded",  # already done
        result_json={},
    )
    repo = _StubRepo(ext)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(ext.id)

    # claim_bbox_refinement got called but returned None -- no further work.
    assert [name for name, _ in repo.calls] == ["claim_bbox_refinement"]
    assert webhook.delivered == []
    assert publisher.published == []


@pytest.mark.asyncio
async def test_drops_unknown_extraction_id() -> None:
    repo = _StubRepo(_StubExtraction(id="other"))
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process("missing")

    assert repo.calls == []
    assert webhook.delivered == []
    assert publisher.published == []


@pytest.mark.asyncio
async def test_permanent_error_marks_failed_no_republish() -> None:
    # Empty schema_json triggers a permanent ValueError ("missing 'files'").
    ext = _StubExtraction(
        schema_json={},
        result_json=_result_with_field("Acme").model_dump(mode="json", by_alias=True),
    )
    repo = _StubRepo(ext)
    publisher = _StubPublisher()
    webhook = _StubWebhook()
    worker = _make_worker(repo, publisher, webhook)

    await worker._process(ext.id)

    # Main status untouched.
    assert ext.status == ExtractionStatus.SUCCEEDED.value
    # Bbox leg marked failed.
    assert ext.post_processing_bbox_status == "failed"
    names = [name for name, _ in repo.calls]
    assert "fail_bbox_refinement" in names
    assert publisher.published == []  # never republish on permanent


# ----------------------------------------------- parallel per-document refine


class _RecordingRefiner:
    """Refiner double that records how many ``refine`` calls overlap.

    Each call increments a live counter, yields the event loop while
    "working", then decrements -- so ``max_active`` captures the peak
    concurrency the worker actually drove across the documents.
    """

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.completed = 0

    async def refine(self, *, groups: Any, **_: Any) -> None:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
        finally:
            self.active -= 1
            self.completed += 1


def _result_with_n_docs(n: int) -> ExtractionResult:
    docs = []
    for i in range(n):
        field_ = ExtractedField(
            name="customer_name",
            value="Acme Corporation",
            pages=[1],
            bbox=BoundingBox(xmin=0.05, ymin=0.05, xmax=0.95, ymax=0.95),
        )
        group = ExtractedFieldGroup(name="customer", fields=[field_])
        docs.append(Document(type="invoice", pages=[1], field_groups=[group], source_file=f"doc{i}.pdf"))
    return ExtractionResult(
        id="ext_RESULT0000000000000000000000",
        files=[],
        documents=docs,
        pipeline=PipelineMeta(model="anthropic:claude-sonnet-4-6", latency_ms=1000),
    )


def _ext_with_n_docs(n: int) -> _StubExtraction:
    pdf = _real_pdf()
    files = [
        {
            "filename": f"doc{i}.pdf",
            "content_base64": base64.b64encode(pdf).decode(),
            "content_type": "application/pdf",
        }
        for i in range(n)
    ]
    return _StubExtraction(
        schema_json={"files": files},
        result_json=_result_with_n_docs(n).model_dump(mode="json", by_alias=True),
    )


def _worker_with(repo: _StubRepo, refiner: Any, settings: IDPSettings) -> BboxRefineWorker:
    return BboxRefineWorker(
        repository=repo,  # type: ignore[arg-type]
        event_publisher=_StubPublisher(),  # type: ignore[arg-type]
        webhook=_StubWebhook(),  # type: ignore[arg-type]
        normalizer=_make_normalizer(),
        refiner=refiner,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_documents_are_refined_concurrently() -> None:
    """Several documents in one extraction refine in parallel, not one-by-one."""
    refiner = _RecordingRefiner()
    repo = _StubRepo(_ext_with_n_docs(3))
    worker = _worker_with(repo, refiner, IDPSettings())

    await worker._process(repo.ext.id)

    assert refiner.calls == 3
    assert refiner.max_active > 1  # sequential code peaks at 1


@pytest.mark.asyncio
async def test_document_concurrency_is_capped_by_setting() -> None:
    """Concurrency never exceeds ``bbox_refine_doc_concurrency`` but does reach it."""
    refiner = _RecordingRefiner()
    repo = _StubRepo(_ext_with_n_docs(4))
    worker = _worker_with(repo, refiner, IDPSettings(bbox_refine_doc_concurrency=2))

    await worker._process(repo.ext.id)

    assert refiner.calls == 4
    assert refiner.max_active == 2  # capped at the limit, and reaches it


@pytest.mark.asyncio
async def test_completion_waits_for_every_document_before_callback() -> None:
    """The barrier guarantee: the bbox leg is only completed once EVERY
    document has finished refining -- so the post-processing callback fires
    exactly once, after the last document."""
    refiner = _RecordingRefiner()
    repo = _StubRepo(_ext_with_n_docs(5))
    worker = _worker_with(repo, refiner, IDPSettings())

    await worker._process(repo.ext.id)

    # All five refined, and completion happened (once) only after all of them.
    assert refiner.completed == 5
    complete_calls = [name for name, _ in repo.calls if name == "complete_bbox_refinement"]
    assert complete_calls == ["complete_bbox_refinement"]
    assert repo.ext.post_processing_bbox_status == "succeeded"


class _FlakyRefiner(_RecordingRefiner):
    """Refines normally but raises on the document whose group is poisoned."""

    async def refine(self, *, groups: Any, **kw: Any) -> None:
        await super().refine(groups=groups, **kw)
        if groups and groups[0].name == "boom":
            raise RuntimeError("refiner exploded")


@pytest.mark.asyncio
async def test_one_document_failure_fails_the_job_without_stranding_siblings() -> None:
    """A refine error still fails the leg (so it retries / goes permanent),
    and every sibling document settles first -- the barrier leaves no task
    running after we leave."""
    refiner = _FlakyRefiner()
    repo = _StubRepo(_ext_with_n_docs(3))
    result = ExtractionResult.model_validate(repo.ext.result_json)
    result.documents[1].field_groups[0].name = "boom"  # poison the middle doc
    repo.ext.result_json = result.model_dump(mode="json", by_alias=True)
    worker = _worker_with(repo, refiner, IDPSettings())

    await worker._process(repo.ext.id)

    # All three were awaited (no orphaned in-flight task), but the leg failed.
    assert refiner.completed == 3
    assert refiner.active == 0
    names = [name for name, _ in repo.calls]
    assert "complete_bbox_refinement" not in names
    # First attempt of 3 -> retryable RuntimeError -> republished, not failed.
    assert "fail_bbox_refinement" not in names
