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

"""Smoke tests for the synchronous :class:`flydocs_sdk.Client`.

The sync client wraps the async one; full coverage of the endpoint
shapes lives in :mod:`tests.test_client_async`. The job of these tests
is to prove the sync wrapper plumbs everything through correctly --
each endpoint reachable, each sub-resource accessor wired up, the
context manager closing cleanly.
"""

from __future__ import annotations

import base64

import httpx
import respx

from flydocs_sdk import (
    Client,
    DocumentTypeSpec,
    ExtractionRequest,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
    SubmitExtractionRequest,
)

BASE_URL = "https://flydocs.test"

PDF_B64 = base64.b64encode(b"%PDF-1.4\n").decode()


def _doc_type() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="invoice",
        field_groups=[
            FieldGroup(
                name="g",
                fields=[Field(name="x", type=FieldType.STRING)],
            )
        ],
    )


def _stub_result() -> dict:
    return {
        "id": "ext_1",
        "status": "success",
        "files": [],
        "documents": [],
        "discovered_documents": [],
        "rule_results": [],
        "request_transformations": [],
        "pipeline": {"model": "m", "latency_ms": 1},
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@respx.mock
def test_sync_version() -> None:
    respx.get(f"{BASE_URL}/api/v1/version").mock(
        return_value=httpx.Response(
            200,
            json={
                "service": "flydocs",
                "version": "26.6.0",
                "model": "anthropic:claude-sonnet-4-6",
                "fallback_model": "",
                "eda_adapter": "postgres",
            },
        )
    )
    with Client(BASE_URL) as client:
        info = client.version()
    assert info.service == "flydocs"


# ---------------------------------------------------------------------------
# Sync extraction
# ---------------------------------------------------------------------------


@respx.mock
def test_sync_validate_returns_typed_response() -> None:
    respx.post(f"{BASE_URL}/api/v1/extract:validate").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []},
        )
    )
    req = ExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[_doc_type()],
    )
    with Client(BASE_URL) as client:
        result = client.validate(req)
    assert result.ok is True


@respx.mock
def test_sync_extract_returns_result() -> None:
    respx.post(f"{BASE_URL}/api/v1/extract").mock(return_value=httpx.Response(200, json=_stub_result()))
    req = ExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[_doc_type()],
    )
    with Client(BASE_URL) as client:
        result = client.extract(req)
    assert result.id == "ext_1"
    assert result.pipeline.model == "m"


# ---------------------------------------------------------------------------
# Extractions sub-resource
# ---------------------------------------------------------------------------


@respx.mock
def test_sync_extractions_create() -> None:
    respx.post(f"{BASE_URL}/api/v1/extractions").mock(
        return_value=httpx.Response(
            202,
            json={"id": "ext_1", "status": "queued", "submitted_at": "2026-05-26T10:00:00+00:00"},
        )
    )
    req = SubmitExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[_doc_type()],
    )
    with Client(BASE_URL) as client:
        ext = client.extractions.create(req, idempotency_key="k")
    assert ext.status is ExtractionStatus.QUEUED


@respx.mock
def test_sync_extractions_get_result() -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1/result").mock(
        return_value=httpx.Response(200, json={"id": "ext_1", "result": _stub_result()})
    )
    with Client(BASE_URL) as client:
        env = client.extractions.get_result("ext_1", wait_for_bboxes=True, timeout=10.0)
    assert env.id == "ext_1"
    assert env.result.id == "ext_1"


@respx.mock
def test_sync_extractions_get_status() -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "ext_1", "status": "running", "submitted_at": "2026-05-26T10:00:00+00:00"},
        )
    )
    with Client(BASE_URL) as client:
        ext = client.extractions.get("ext_1")
    assert ext.status is ExtractionStatus.RUNNING


@respx.mock
def test_sync_extractions_cancel() -> None:
    respx.delete(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ext_1",
                "status": "cancelled",
                "submitted_at": "2026-05-26T10:00:00+00:00",
            },
        )
    )
    with Client(BASE_URL) as client:
        ext = client.extractions.cancel("ext_1")
    assert ext.status is ExtractionStatus.CANCELLED


@respx.mock
def test_sync_extractions_list() -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions").mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0, "limit": 50, "offset": 0})
    )
    with Client(BASE_URL) as client:
        listing = client.extractions.list(limit=50)
    assert listing.total == 0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_sync_client_close_idempotent() -> None:
    client = Client(BASE_URL)
    client.close()
    # Second close is a no-op.
    client.close()
