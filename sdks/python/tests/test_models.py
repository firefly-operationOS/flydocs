# Copyright 2026 Firefly Software Solutions Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the wire-level models.

The SDK keeps its own copies of the DTOs and the contract here is that
they (a) accept the on-wire shape the service emits and (b) stay
forward-compatible when the service adds new fields. Both halves of
that contract are pinned down below.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import pytest

from flydocs_sdk import (
    DocumentInput,
    ExtractionRequest,
    ExtractionResult,
    JobListResponse,
    JobStatus,
    JobStatusResponse,
    JobWebhookPayload,
    SubmitJobResponse,
)


def test_document_input_from_bytes_roundtrips() -> None:
    doc = DocumentInput.from_bytes(b"hello", filename="hello.txt", content_type="text/plain")
    assert base64.b64decode(doc.content_base64) == b"hello"
    assert doc.filename == "hello.txt"
    assert doc.content_type == "text/plain"


def test_document_input_from_path(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    doc = DocumentInput.from_path(f)
    assert doc.filename == "x.bin"
    assert base64.b64decode(doc.content_base64) == b"abc"


def test_document_input_strips_data_url_prefix() -> None:
    doc = DocumentInput(
        filename="x.pdf",
        content_base64="data:application/pdf;base64,YWJj",
    )
    assert doc.content_base64 == "YWJj"


def test_extraction_request_accepts_dict_docs() -> None:
    # The SDK deliberately keeps ``docs`` permissive so callers can
    # send the rich shape without depending on the service's DTOs.
    req = ExtractionRequest(
        documents=[DocumentInput.from_bytes(b"x", filename="x.pdf")],
        docs=[
            {
                "docType": {"documentType": "invoice", "description": "Test invoice"},
                "groups": [{"fieldGroupName": "totals", "fieldGroupFields": []}],
            }
        ],
    )
    dumped = req.model_dump(mode="json")
    assert dumped["docs"][0]["docType"]["documentType"] == "invoice"
    assert "request_id" in dumped  # auto-generated UUID


def test_extraction_result_tolerates_unknown_fields() -> None:
    # Forward-compat: the service can ship new top-level fields without
    # breaking SDK clients pinned to an older version.
    payload = {
        "request_id": "00000000-0000-0000-0000-000000000000",
        "model": "anthropic:claude-sonnet-4-6",
        "latency_ms": 1234,
        "documents": [],
        # New field invented by the service post-SDK-release:
        "future_field": {"shiny": True},
    }
    result = ExtractionResult.model_validate(payload)
    assert result.model == "anthropic:claude-sonnet-4-6"
    # Unknown field is preserved in ``model_extra`` so callers can still read it.
    assert result.model_extra is not None
    assert result.model_extra["future_field"] == {"shiny": True}


def test_job_status_response_parses_full_shape() -> None:
    payload = {
        "job_id": "job_123",
        "status": "RUNNING",
        "submitted_at": "2026-05-17T10:00:00+00:00",
        "started_at": "2026-05-17T10:00:01+00:00",
        "attempts": 2,
        "bbox_refine_status": "pending",
    }
    resp = JobStatusResponse.model_validate(payload)
    assert resp.status is JobStatus.RUNNING
    assert resp.attempts == 2
    assert resp.started_at == datetime.fromisoformat("2026-05-17T10:00:01+00:00")
    assert resp.bbox_refine_status == "pending"


def test_webhook_payload_parses_full_shape() -> None:
    parsed = JobWebhookPayload.model_validate(
        {
            "event_id": "evt-1",
            "event_type": "IDPJobCompleted",
            "job_id": "job-1",
            "status": "SUCCEEDED",
            "occurred_at": "2026-05-17T10:00:00+00:00",
            "metadata": {"caller": "test"},
        }
    )
    assert parsed.status is JobStatus.SUCCEEDED
    assert parsed.metadata == {"caller": "test"}


def test_submit_job_response_parses() -> None:
    resp = SubmitJobResponse.model_validate(
        {
            "job_id": "job-xyz",
            "status": "QUEUED",
            "submitted_at": "2026-05-17T10:00:00+00:00",
        }
    )
    assert resp.status is JobStatus.QUEUED


def test_job_list_response_parses() -> None:
    resp = JobListResponse.model_validate(
        {
            "items": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
        }
    )
    assert resp.total == 0
    assert resp.items == []


def test_unknown_job_status_raises_on_known_enum() -> None:
    # StrEnum still rejects unknown values when the field is typed
    # against the enum strictly -- only the webhook ``status`` is
    # permissive because we declared it as a string-mode field above.
    # Here we just sanity-check the typed enum still validates known
    # values:
    with pytest.raises(Exception):  # noqa: B017
        JobStatus("ZZZ_UNKNOWN")
