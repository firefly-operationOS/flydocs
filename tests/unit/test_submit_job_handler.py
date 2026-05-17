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
