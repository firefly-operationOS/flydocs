# Copyright 2026 Firefly Software Solutions Inc
""":class:`SubmitJobHandler` -- persistence shape.

These tests pin the contract between the REST DTO and what the worker
later finds in ``ExtractionJob.schema_json``. Every submission writes
a ``documents`` list (single-file submits are just a 1-element list).
The DB row's ``filename`` column gets a summary ("first.pdf (+N more)")
for multi-file submits and ``content_sha256`` hashes the concatenation
of every file's bytes so idempotency still collapses identical retries.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.core.services.jobs.submit_job_handler import (
    SubmitJobCommand,
    SubmitJobHandler,
)
from flydocs.core.services.validation import ValidationReport
from flydocs.interfaces.dtos.doc import DocSpec, DocType
from flydocs.interfaces.dtos.extract import DocumentInput
from flydocs.interfaces.dtos.job import SubmitJobRequest
from flydocs.interfaces.enums.job_status import JobStatus


def _pdf_b64(marker: bytes) -> str:
    return base64.b64encode(b"%PDF-1.4\n" + marker + b"\n%%EOF\n").decode()


def _doc_spec() -> DocSpec:
    return DocSpec(
        docType=DocType(documentType="invoice", description="test"),
        fieldGroups=[
            {
                "fieldGroupName": "g",
                "fieldGroupFields": [{"fieldName": "f", "fieldDescription": "x", "fieldType": "string"}],
            }
        ],
    )


def _handler() -> tuple[SubmitJobHandler, MagicMock, MagicMock]:
    repository = MagicMock()
    repository.get_by_idempotency_key = AsyncMock(return_value=None)

    captured: dict[str, Any] = {}

    async def _add(job: Any) -> Any:
        captured["job"] = job
        job.id = "test-job-id"
        from datetime import UTC, datetime

        job.created_at = datetime.now(UTC)
        return job

    repository.add = AsyncMock(side_effect=_add)

    publisher = MagicMock()
    publisher.publish = AsyncMock()

    validator = MagicMock()
    validator.validate = MagicMock(return_value=ValidationReport(issues=[]))

    settings = MagicMock()
    settings.jobs_topic = "jobs.extract"
    settings.jobs_event_type = "job.submitted"

    handler = SubmitJobHandler(
        repository=repository,
        event_publisher=publisher,
        validator=validator,
        settings=settings,
    )
    return handler, repository, captured  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_single_file_submit_persists_documents_list() -> None:
    """A 1-element ``documents`` list is the only shape we accept."""
    handler, _, captured = _handler()
    request = SubmitJobRequest(
        documents=[
            DocumentInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"alpha"),
                content_type="application/pdf",
            )
        ],
        docs=[_doc_spec()],
    )
    response = await handler.do_handle(SubmitJobCommand(request=request))

    assert response.status is JobStatus.QUEUED
    job = captured["job"]
    assert job.filename == "invoice.pdf"
    assert "documents" in job.schema_json
    assert len(job.schema_json["documents"]) == 1
    assert job.schema_json["documents"][0]["filename"] == "invoice.pdf"
    assert job.content_bytes > 0


@pytest.mark.asyncio
async def test_multi_file_submit_persists_documents_list() -> None:
    handler, _, captured = _handler()
    request = SubmitJobRequest(
        documents=[
            DocumentInput(
                filename=f"deed_{i}.pdf",
                content_base64=_pdf_b64(bytes([0x30 + i])),
                content_type="application/pdf",
            )
            for i in range(3)
        ],
        docs=[_doc_spec()],
    )
    await handler.do_handle(SubmitJobCommand(request=request))

    job = captured["job"]
    assert job.filename.startswith("deed_0.pdf")
    assert "(+2 more)" in job.filename
    assert "documents" in job.schema_json
    assert len(job.schema_json["documents"]) == 3
    for entry in job.schema_json["documents"]:
        assert entry["content_type"] == "application/pdf"
        assert entry["filename"].startswith("deed_")
        assert entry["content_base64"]


@pytest.mark.asyncio
async def test_multi_file_idempotency_hash_includes_every_file() -> None:
    """Same files in the same order produce the same content_sha256."""
    handler_a, _, captured_a = _handler()
    handler_b, _, captured_b = _handler()
    files = [
        DocumentInput(
            filename=f"d_{i}.pdf",
            content_base64=_pdf_b64(bytes([0x40 + i])),
            content_type="application/pdf",
        )
        for i in range(2)
    ]
    await handler_a.do_handle(SubmitJobCommand(request=SubmitJobRequest(documents=files, docs=[_doc_spec()])))
    await handler_b.do_handle(SubmitJobCommand(request=SubmitJobRequest(documents=files, docs=[_doc_spec()])))
    assert captured_a["job"].content_sha256 == captured_b["job"].content_sha256


def test_request_rejects_empty_documents() -> None:
    """``documents`` is required and must have at least one entry."""
    with pytest.raises(ValueError):
        SubmitJobRequest(documents=[], docs=[_doc_spec()])


@pytest.mark.asyncio
async def test_concurrent_idempotent_submit_resolves_winning_row() -> None:
    """Two submits with the same key racing past the SELECT must not 500.

    Concrete scenario: the SELECT-by-key inside ``do_handle`` returns
    ``None`` (no existing row), so the handler proceeds to INSERT. The
    partial unique index on the DB raises ``IntegrityError`` for the
    loser. The handler must catch it, re-resolve the winning row by
    key, and return its identifier with the idempotent shape.
    """
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    from flydocs.interfaces.dtos.job import SubmitJobResponse  # noqa: F401

    handler, repository, captured = _handler()

    # First call: SELECT returns None, INSERT raises IntegrityError, then
    # the recovery path SELECTs again and finds the winning row.
    winning_row = MagicMock()
    winning_row.id = "winner-job-id"
    winning_row.status = JobStatus.QUEUED.value
    winning_row.created_at = datetime.now(UTC)

    select_calls = {"n": 0}

    async def _get_by_key(key: str):
        select_calls["n"] += 1
        # First call (before INSERT) returns None; second call (after
        # IntegrityError) returns the winner.
        return None if select_calls["n"] == 1 else winning_row

    repository.get_by_idempotency_key = AsyncMock(side_effect=_get_by_key)
    repository.IntegrityError = IntegrityError
    repository.add = AsyncMock(side_effect=IntegrityError("", None, None))

    request = SubmitJobRequest(
        documents=[
            DocumentInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"x"),
                content_type="application/pdf",
            )
        ],
        docs=[_doc_spec()],
    )
    response = await handler.do_handle(SubmitJobCommand(request=request, idempotency_key="dupe-key"))

    assert response.job_id == "winner-job-id"
    assert response.status is JobStatus.QUEUED
    # Two SELECTs: pre-INSERT probe + post-IntegrityError recovery probe.
    assert select_calls["n"] == 2


@pytest.mark.asyncio
async def test_idempotent_submit_reraises_if_no_winner_resolved() -> None:
    """IntegrityError with no recoverable row re-raises (vanishingly rare)."""
    from sqlalchemy.exc import IntegrityError

    handler, repository, captured = _handler()
    repository.get_by_idempotency_key = AsyncMock(return_value=None)
    repository.IntegrityError = IntegrityError
    repository.add = AsyncMock(side_effect=IntegrityError("", None, None))

    request = SubmitJobRequest(
        documents=[
            DocumentInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"x"),
                content_type="application/pdf",
            )
        ],
        docs=[_doc_spec()],
    )
    with pytest.raises(IntegrityError):
        await handler.do_handle(SubmitJobCommand(request=request, idempotency_key="k"))
