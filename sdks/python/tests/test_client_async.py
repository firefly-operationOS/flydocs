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

"""End-to-end mock tests for the async :class:`flydocs_sdk.AsyncClient`.

Each test stands up a respx route that mimics what the v1 service
returns, calls the SDK, and asserts both halves:

* the request the SDK put on the wire matches the controller's
  contract (path, method, headers, body),
* the response the SDK decoded into a model has the values from the
  mocked body.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from flydocs_sdk import (
    AsyncClient,
    DocumentTypeSpec,
    ExtractionRequest,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
    FlydocsHttpError,
    PostProcessingStatus,
    SubmitExtractionRequest,
)

BASE_URL = "https://flydocs.test"

PDF_B64 = base64.b64encode(b"%PDF-1.4\n").decode()


def _now_iso() -> str:
    return "2026-05-26T10:00:00+00:00"


def _bare_doc_type() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="invoice",
        field_groups=[
            FieldGroup(
                name="totals",
                fields=[Field(name="total", type=FieldType.NUMBER)],
            )
        ],
    )


def _bare_request() -> ExtractionRequest:
    return ExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[_bare_doc_type()],
    )


def _bare_submit_request() -> SubmitExtractionRequest:
    return SubmitExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[_bare_doc_type()],
    )


def _stub_extraction_result() -> dict:
    return {
        "id": "ext_1",
        "status": "success",
        "files": [],
        "documents": [],
        "discovered_documents": [],
        "rule_results": [],
        "request_transformations": [],
        "pipeline": {"model": "anthropic:claude-sonnet-4-6", "latency_ms": 1234},
    }


# ---------------------------------------------------------------------------
# Identity / health
# ---------------------------------------------------------------------------


@respx.mock
async def test_version(async_client: AsyncClient) -> None:
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
    info = await async_client.version()
    assert info.service == "flydocs"
    assert info.eda_adapter == "postgres"


@respx.mock
async def test_health(async_client: AsyncClient) -> None:
    respx.get(f"{BASE_URL}/actuator/health/readiness").mock(
        return_value=httpx.Response(200, json={"status": "UP"})
    )
    payload = await async_client.health()
    assert payload["status"] == "UP"


# ---------------------------------------------------------------------------
# Sync extraction
# ---------------------------------------------------------------------------


@respx.mock
async def test_validate_returns_typed_response(async_client: AsyncClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/extract:validate").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []},
        )
    )
    report = await async_client.validate(_bare_request())
    assert report.ok is True
    assert report.error_count == 0


@respx.mock
async def test_extract_posts_v1_body(async_client: AsyncClient) -> None:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_stub_extraction_result())

    respx.post(f"{BASE_URL}/api/v1/extract").mock(side_effect=_handler)
    result = await async_client.extract(
        _bare_request(),
        idempotency_key="idem-1",
        correlation_id="corr-1",
    )
    assert result.id == "ext_1"
    assert result.pipeline.model == "anthropic:claude-sonnet-4-6"
    assert captured["headers"]["idempotency-key"] == "idem-1"
    assert captured["headers"]["x-correlation-id"] == "corr-1"
    # v1 body uses ``files`` / ``document_types`` keys.
    assert "files" in captured["body"]
    assert "document_types" in captured["body"]
    assert "documents" not in captured["body"]
    assert "docs" not in captured["body"]


@respx.mock
async def test_extract_timeout_maps_to_typed_error(async_client: AsyncClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/extract").mock(
        return_value=httpx.Response(
            408,
            json={
                "type": "about:blank",
                "title": "Extraction timed out",
                "status": 408,
                "code": "timeout",
                "detail": "Pipeline exceeded 60s sync ceiling",
            },
        )
    )
    with pytest.raises(FlydocsHttpError) as excinfo:
        await async_client.extract(_bare_request())
    err = excinfo.value
    assert err.status_code == 408
    assert err.code == "timeout"
    assert "Pipeline exceeded" in err.detail


@respx.mock
async def test_extract_413_file_too_large(async_client: AsyncClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/extract").mock(
        return_value=httpx.Response(
            413,
            json={
                "code": "file_too_large",
                "title": "File too large",
                "status": 413,
                "detail": "x.pdf is 5000000 bytes",
            },
        )
    )
    with pytest.raises(FlydocsHttpError) as excinfo:
        await async_client.extract(_bare_request())
    assert excinfo.value.code == "file_too_large"


@respx.mock
async def test_extract_supports_multipart_upload(async_client: AsyncClient) -> None:
    """Posting ``files=[...]`` should switch the wire format to multipart."""
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body_size"] = len(request.content)
        return httpx.Response(200, json=_stub_extraction_result())

    respx.post(f"{BASE_URL}/api/v1/extract").mock(side_effect=_handler)
    import io

    buf = io.BytesIO(b"%PDF-1.4-binary-bytes")
    buf.name = "invoice.pdf"
    result = await async_client.extract(_bare_request(), files=[buf])
    assert result.id == "ext_1"
    assert captured["content_type"].startswith("multipart/form-data")


# ---------------------------------------------------------------------------
# Async extraction lifecycle
# ---------------------------------------------------------------------------


@respx.mock
async def test_extractions_create_returns_extraction(async_client: AsyncClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/extractions").mock(
        return_value=httpx.Response(
            202,
            json={"id": "ext_1", "status": "queued", "submitted_at": _now_iso()},
        )
    )
    ext = await async_client.extractions.create(_bare_submit_request(), idempotency_key="submit-1")
    assert ext.id == "ext_1"
    assert ext.status is ExtractionStatus.QUEUED


@respx.mock
async def test_extractions_get_returns_status(async_client: AsyncClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ext_1",
                "status": "succeeded",
                "submitted_at": _now_iso(),
                "finished_at": _now_iso(),
            },
        )
    )
    ext = await async_client.extractions.get("ext_1")
    assert ext.status is ExtractionStatus.SUCCEEDED
    assert ext.finished_at is not None


@respx.mock
async def test_extractions_get_result_envelope(async_client: AsyncClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1/result").mock(
        return_value=httpx.Response(
            200,
            json={"id": "ext_1", "result": _stub_extraction_result()},
        )
    )
    env = await async_client.extractions.get_result("ext_1", wait_for_bboxes=True, timeout=10.0)
    assert env.id == "ext_1"
    assert env.result.id == "ext_1"
    # Long-poll params went on the wire under the server's name.
    call = respx.calls.last
    assert "wait_for_post_processing=true" in str(call.request.url)
    assert "timeout=10.0" in str(call.request.url)


@respx.mock
async def test_extractions_cancel_returns_status(async_client: AsyncClient) -> None:
    respx.delete(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "ext_1", "status": "cancelled", "submitted_at": _now_iso()},
        )
    )
    ext = await async_client.extractions.cancel("ext_1")
    assert ext.status is ExtractionStatus.CANCELLED


@respx.mock
async def test_extractions_cancel_not_cancellable_raises(async_client: AsyncClient) -> None:
    respx.delete(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            409,
            json={
                "code": "not_cancellable",
                "title": "Extraction cannot be cancelled",
                "status": 409,
                "detail": "Extraction is already running",
            },
        )
    )
    with pytest.raises(FlydocsHttpError) as excinfo:
        await async_client.extractions.cancel("ext_1")
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "not_cancellable"


@respx.mock
async def test_extractions_list_csv_filters(async_client: AsyncClient) -> None:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [{"id": "ext_1", "status": "succeeded", "submitted_at": _now_iso()}],
                "total": 1,
                "limit": 25,
                "offset": 0,
            },
        )

    respx.get(f"{BASE_URL}/api/v1/extractions").mock(side_effect=_handler)
    resp = await async_client.extractions.list(
        status=[ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED],
        post_processing_status=[PostProcessingStatus.PENDING],
        limit=25,
    )
    assert resp.total == 1
    assert resp.items[0].id == "ext_1"
    # Comma-encoded list params on the wire.
    assert "status=succeeded%2Cfailed" in captured["url"]
    assert "post_processing_status=pending" in captured["url"]
    assert "limit=25" in captured["url"]


# ---------------------------------------------------------------------------
# Polling helper
# ---------------------------------------------------------------------------


@respx.mock
async def test_wait_for_completion_loops_until_terminal(async_client: AsyncClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        side_effect=[
            httpx.Response(200, json={"id": "ext_1", "status": "queued", "submitted_at": _now_iso()}),
            httpx.Response(200, json={"id": "ext_1", "status": "running", "submitted_at": _now_iso()}),
            httpx.Response(
                200,
                json={"id": "ext_1", "status": "succeeded", "submitted_at": _now_iso()},
            ),
        ]
    )
    final = await async_client.wait_for_completion("ext_1", poll_interval=0.001, timeout=5.0)
    assert final.status is ExtractionStatus.SUCCEEDED


@respx.mock
async def test_wait_for_completion_times_out(async_client: AsyncClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/extractions/ext_1").mock(
        return_value=httpx.Response(
            200, json={"id": "ext_1", "status": "running", "submitted_at": _now_iso()}
        )
    )
    with pytest.raises(TimeoutError):
        await async_client.wait_for_completion("ext_1", poll_interval=0.001, timeout=0.05)


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------


@respx.mock
async def test_api_key_sets_bearer_header() -> None:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "service": "flydocs",
                "version": "26.6.0",
                "model": "m",
                "fallback_model": "",
                "eda_adapter": "memory",
            },
        )

    respx.get(f"{BASE_URL}/api/v1/version").mock(side_effect=_handler)
    async with AsyncClient(BASE_URL, api_key="topsecret") as client:
        await client.version()
    assert captured["auth"] == "Bearer topsecret"


def test_pdf_b64_helper_is_valid() -> None:
    """Sanity: the test fixture's base64 decodes back to the PDF magic bytes."""
    assert base64.b64decode(PDF_B64).startswith(b"%PDF-1.4")
