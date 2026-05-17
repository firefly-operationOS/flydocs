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

"""End-to-end mock tests for the async client.

Each test stands up a respx route that mimics what the real service
would return, calls the SDK, and asserts both halves:

* the request the SDK put on the wire matches the controller's
  contract (path, query, headers, body),
* the response the SDK decoded into a model has the values from the
  mocked body.

Together this exercises the URL builder, header assembly, problem-
detail decoding, and the typed model layer in one pass.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from flydocs_sdk import (
    AsyncFlydocsClient,
    DocumentInput,
    ExtractionRequest,
    FlydocsHTTPError,
    JobStatus,
    SubmitJobRequest,
)

BASE_URL = "http://flydocs.test"


def _now_iso() -> str:
    return datetime(2026, 5, 17, 10, 0, 0, tzinfo=UTC).isoformat()


# ---------------------------------------------------------------------------
# Identity / health
# ---------------------------------------------------------------------------


@respx.mock
async def test_version(async_client: AsyncFlydocsClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/version").mock(
        return_value=httpx.Response(
            200,
            json={
                "service": "flydocs",
                "version": "0.1.0",
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
async def test_health(async_client: AsyncFlydocsClient) -> None:
    respx.get(f"{BASE_URL}/actuator/health/readiness").mock(
        return_value=httpx.Response(200, json={"status": "UP", "components": {"db": "UP"}})
    )
    payload = await async_client.health()
    assert payload["status"] == "UP"


# ---------------------------------------------------------------------------
# Sync extraction
# ---------------------------------------------------------------------------


@respx.mock
async def test_validate_returns_report(async_client: AsyncFlydocsClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/extract:validate").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "error_count": 0, "warning_count": 0, "errors": [], "warnings": []},
        )
    )
    request = ExtractionRequest(
        documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        docs=[{"docType": {"documentType": "invoice"}}],
    )
    report = await async_client.validate(request)
    assert report["ok"] is True


@respx.mock
async def test_extract_decodes_result_and_sends_idempotency_header(
    async_client: AsyncFlydocsClient,
) -> None:
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "request_id": "00000000-0000-0000-0000-000000000001",
                "model": "anthropic:claude-sonnet-4-6",
                "latency_ms": 4321,
                "documents": [],
            },
        )

    respx.post(f"{BASE_URL}/api/v1/extract").mock(side_effect=_handler)
    result = await async_client.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
            docs=[{"docType": {"documentType": "invoice"}}],
        ),
        idempotency_key="abc-123",
        correlation_id="corr-1",
    )
    assert result.model == "anthropic:claude-sonnet-4-6"
    assert result.latency_ms == 4321
    assert captured["headers"]["idempotency-key"] == "abc-123"
    assert captured["headers"]["x-correlation-id"] == "corr-1"
    # The body should be a JSON object that includes our document.
    assert captured["body"]["documents"][0]["filename"] == "x.pdf"


@respx.mock
async def test_extract_timeout_maps_to_typed_error(
    async_client: AsyncFlydocsClient,
) -> None:
    respx.post(f"{BASE_URL}/api/v1/extract").mock(
        return_value=httpx.Response(
            408,
            json={
                "detail": {
                    "code": "extraction_timeout",
                    "title": "Extraction timed out",
                    "detail": "Pipeline exceeded 60s sync ceiling",
                }
            },
        )
    )
    with pytest.raises(FlydocsHTTPError) as excinfo:
        await async_client.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
                docs=[{"docType": {"documentType": "invoice"}}],
            )
        )
    err = excinfo.value
    assert err.status_code == 408
    assert err.code == "extraction_timeout"
    assert "Pipeline exceeded" in err.detail


@respx.mock
async def test_extract_problem_detail_at_top_level(
    async_client: AsyncFlydocsClient,
) -> None:
    # Some flydocs error paths emit ``code`` at the top level rather
    # than nested under ``detail``. Decoder should handle both.
    respx.post(f"{BASE_URL}/api/v1/extract").mock(
        return_value=httpx.Response(
            413,
            json={
                "code": "document_too_large",
                "title": "Document too large",
                "detail": "x.pdf is 50000000 bytes",
            },
        )
    )
    with pytest.raises(FlydocsHTTPError) as excinfo:
        await async_client.extract(
            ExtractionRequest(
                documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
                docs=[{"docType": {"documentType": "invoice"}}],
            )
        )
    assert excinfo.value.code == "document_too_large"


# ---------------------------------------------------------------------------
# Async-job lifecycle
# ---------------------------------------------------------------------------


@respx.mock
async def test_submit_job_returns_queued(async_client: AsyncFlydocsClient) -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-1", "status": "QUEUED", "submitted_at": _now_iso()},
        )
    )
    resp = await async_client.submit_job(
        SubmitJobRequest(
            documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
            docs=[{"docType": {"documentType": "invoice"}}],
            callback_url="https://example.com/webhook",
            metadata={"caller": "test"},
        ),
        idempotency_key="submit-once",
    )
    assert resp.job_id == "job-1"
    assert resp.status is JobStatus.QUEUED


@respx.mock
async def test_get_job_status(async_client: AsyncFlydocsClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "status": "SUCCEEDED",
                "submitted_at": _now_iso(),
                "finished_at": _now_iso(),
            },
        )
    )
    status = await async_client.get_job("job-1")
    assert status.status is JobStatus.SUCCEEDED
    assert status.finished_at is not None


@respx.mock
async def test_get_job_result(async_client: AsyncFlydocsClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1/result").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-1",
                "result": {
                    "request_id": "00000000-0000-0000-0000-000000000002",
                    "model": "anthropic:claude-sonnet-4-6",
                    "latency_ms": 1500,
                    "documents": [],
                },
            },
        )
    )
    result = await async_client.get_job_result("job-1", wait_for_bboxes=True, timeout=10.0)
    assert result.job_id == "job-1"
    assert result.result.model == "anthropic:claude-sonnet-4-6"
    # Verify the long-poll query params went on the wire.
    call = respx.calls.last
    assert "wait_for_bboxes=true" in str(call.request.url)
    assert "timeout=10.0" in str(call.request.url)


@respx.mock
async def test_list_jobs_csv_filters(async_client: AsyncFlydocsClient) -> None:
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "job_id": "job-1",
                        "status": "SUCCEEDED",
                        "submitted_at": _now_iso(),
                    }
                ],
                "total": 1,
                "limit": 25,
                "offset": 0,
            },
        )

    respx.get(f"{BASE_URL}/api/v1/jobs").mock(side_effect=_handler)
    resp = await async_client.list_jobs(
        status=["SUCCEEDED", "PARTIAL_SUCCEEDED"],
        limit=25,
    )
    assert resp.total == 1
    # The list-of-statuses argument joins with comma to match the
    # controller's CSV splitter.
    assert "status=SUCCEEDED%2CPARTIAL_SUCCEEDED" in captured["url"]
    assert "limit=25" in captured["url"]


@respx.mock
async def test_cancel_job_returns_status(async_client: AsyncFlydocsClient) -> None:
    respx.delete(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(
            200,
            json={"job_id": "job-1", "status": "CANCELLED", "submitted_at": _now_iso()},
        )
    )
    resp = await async_client.cancel_job("job-1")
    assert resp.status is JobStatus.CANCELLED


@respx.mock
async def test_cancel_job_not_cancellable(async_client: AsyncFlydocsClient) -> None:
    respx.delete(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": {
                    "code": "job_not_cancellable",
                    "title": "Job cannot be cancelled",
                    "detail": "Job is RUNNING",
                }
            },
        )
    )
    with pytest.raises(FlydocsHTTPError) as excinfo:
        await async_client.cancel_job("job-1")
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "job_not_cancellable"
