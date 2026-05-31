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

""":class:`SubmitExtractionHandler` -- persistence shape.

These tests pin the contract between the REST DTO and what the worker
later finds in ``Extraction.schema_json``. Every submission writes a
``files`` list (single-file submits are just a 1-element list).
The DB row's ``filename`` column gets a summary ("first.pdf (+N more)")
for multi-file submits and ``content_sha256`` hashes the concatenation
of every file's bytes so idempotency still collapses identical retries.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.core.services.extractions.submit_extraction_handler import (
    SubmitExtractionCommand,
    SubmitExtractionHandler,
)
from flydocs.core.services.validation import ValidationReport
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import FileInput
from flydocs.interfaces.dtos.extraction import SubmitExtractionRequest
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.enums.extraction_status import ExtractionStatus
from flydocs.interfaces.enums.field_type import FieldType


def _pdf_b64(marker: bytes) -> str:
    return base64.b64encode(b"%PDF-1.4\n" + marker + b"\n%%EOF\n").decode()


def _doc_spec() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="invoice",
        description="test",
        field_groups=[
            FieldGroup(
                name="g",
                fields=[
                    Field(name="f", description="x", type=FieldType.STRING),
                ],
            )
        ],
    )


def _handler() -> tuple[SubmitExtractionHandler, MagicMock, dict[str, Any]]:
    repository = MagicMock()
    repository.get_by_idempotency_key = AsyncMock(return_value=None)

    captured: dict[str, Any] = {}

    async def _add(ext: Any) -> Any:
        captured["ext"] = ext
        ext.id = "ext_TEST00000000000000000000000"
        from datetime import UTC, datetime

        ext.submitted_at = datetime.now(UTC)
        # Defaults the projector reads from a real row.
        for attr, default in (
            ("started_at", None),
            ("finished_at", None),
            ("attempts", 0),
            ("error_code", None),
            ("error_message", None),
            ("post_processing_bbox_status", None),
            ("post_processing_bbox_attempts", 0),
            ("post_processing_bbox_started_at", None),
            ("post_processing_bbox_finished_at", None),
            ("post_processing_bbox_error_code", None),
            ("post_processing_bbox_error_message", None),
        ):
            if not hasattr(ext, attr) or getattr(ext, attr) is None:
                setattr(ext, attr, default)
        return ext

    repository.add = AsyncMock(side_effect=_add)

    publisher = MagicMock()
    publisher.publish = AsyncMock()

    validator = MagicMock()
    validator.validate = MagicMock(return_value=ValidationReport(issues=[]))

    settings = MagicMock()
    settings.jobs_topic = "extractions.queue"

    handler = SubmitExtractionHandler(
        repository=repository,
        event_publisher=publisher,
        validator=validator,
        settings=settings,
    )
    return handler, repository, captured


@pytest.mark.asyncio
async def test_single_file_submit_persists_files_list() -> None:
    """A 1-element ``files`` list is the only shape we accept."""
    handler, _, captured = _handler()
    request = SubmitExtractionRequest(
        files=[
            FileInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"alpha"),
                content_type="application/pdf",
            )
        ],
        document_types=[_doc_spec()],
    )
    response = await handler.do_handle(SubmitExtractionCommand(request=request))

    assert response.status is ExtractionStatus.QUEUED
    ext = captured["ext"]
    assert ext.filename == "invoice.pdf"
    assert "files" in ext.schema_json
    assert len(ext.schema_json["files"]) == 1
    assert ext.schema_json["files"][0]["filename"] == "invoice.pdf"
    assert ext.content_bytes > 0


@pytest.mark.asyncio
async def test_multi_file_submit_persists_files_list() -> None:
    handler, _, captured = _handler()
    request = SubmitExtractionRequest(
        files=[
            FileInput(
                filename=f"deed_{i}.pdf",
                content_base64=_pdf_b64(bytes([0x30 + i])),
                content_type="application/pdf",
            )
            for i in range(3)
        ],
        document_types=[_doc_spec()],
    )
    await handler.do_handle(SubmitExtractionCommand(request=request))

    ext = captured["ext"]
    assert ext.filename.startswith("deed_0.pdf")
    assert "(+2 more)" in ext.filename
    assert "files" in ext.schema_json
    assert len(ext.schema_json["files"]) == 3
    for entry in ext.schema_json["files"]:
        assert entry["content_type"] == "application/pdf"
        assert entry["filename"].startswith("deed_")
        assert entry["content_base64"]


@pytest.mark.asyncio
async def test_multi_file_idempotency_hash_includes_every_file() -> None:
    """Same files in the same order produce the same content_sha256."""
    handler_a, _, captured_a = _handler()
    handler_b, _, captured_b = _handler()
    files = [
        FileInput(
            filename=f"d_{i}.pdf",
            content_base64=_pdf_b64(bytes([0x40 + i])),
            content_type="application/pdf",
        )
        for i in range(2)
    ]
    await handler_a.do_handle(
        SubmitExtractionCommand(request=SubmitExtractionRequest(files=files, document_types=[_doc_spec()]))
    )
    await handler_b.do_handle(
        SubmitExtractionCommand(request=SubmitExtractionRequest(files=files, document_types=[_doc_spec()]))
    )
    assert captured_a["ext"].content_sha256 == captured_b["ext"].content_sha256


def test_request_rejects_empty_files() -> None:
    """``files`` is required and must have at least one entry."""
    with pytest.raises(ValueError):
        SubmitExtractionRequest(files=[], document_types=[_doc_spec()])


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

    handler, repository, _ = _handler()

    # First call: SELECT returns None, INSERT raises IntegrityError, then
    # the recovery path SELECTs again and finds the winning row.
    winning_row = MagicMock()
    winning_row.id = "ext_WIN00000000000000000000000"
    winning_row.status = ExtractionStatus.QUEUED.value
    winning_row.submitted_at = datetime.now(UTC)
    winning_row.started_at = None
    winning_row.finished_at = None
    winning_row.attempts = 0
    winning_row.error_code = None
    winning_row.error_message = None
    winning_row.post_processing_bbox_status = None
    winning_row.post_processing_bbox_attempts = 0
    winning_row.post_processing_bbox_started_at = None
    winning_row.post_processing_bbox_finished_at = None
    winning_row.post_processing_bbox_error_code = None
    winning_row.post_processing_bbox_error_message = None

    select_calls = {"n": 0}

    async def _get_by_key(key: str):
        select_calls["n"] += 1
        # First call (before INSERT) returns None; second call (after
        # IntegrityError) returns the winner.
        return None if select_calls["n"] == 1 else winning_row

    repository.get_by_idempotency_key = AsyncMock(side_effect=_get_by_key)
    repository.IntegrityError = IntegrityError
    repository.add = AsyncMock(side_effect=IntegrityError("", None, None))

    request = SubmitExtractionRequest(
        files=[
            FileInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"x"),
                content_type="application/pdf",
            )
        ],
        document_types=[_doc_spec()],
    )
    response = await handler.do_handle(SubmitExtractionCommand(request=request, idempotency_key="dupe-key"))

    assert response.id == "ext_WIN00000000000000000000000"
    assert response.status is ExtractionStatus.QUEUED
    # Two SELECTs: pre-INSERT probe + post-IntegrityError recovery probe.
    assert select_calls["n"] == 2


@pytest.mark.asyncio
async def test_idempotent_submit_reraises_if_no_winner_resolved() -> None:
    """IntegrityError with no recoverable row re-raises (vanishingly rare)."""
    from sqlalchemy.exc import IntegrityError

    handler, repository, _ = _handler()
    repository.get_by_idempotency_key = AsyncMock(return_value=None)
    repository.IntegrityError = IntegrityError
    repository.add = AsyncMock(side_effect=IntegrityError("", None, None))

    request = SubmitExtractionRequest(
        files=[
            FileInput(
                filename="invoice.pdf",
                content_base64=_pdf_b64(b"x"),
                content_type="application/pdf",
            )
        ],
        document_types=[_doc_spec()],
    )
    with pytest.raises(IntegrityError):
        await handler.do_handle(SubmitExtractionCommand(request=request, idempotency_key="k"))
