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

"""Async client over httpx.

The sync :class:`flydocs_sdk.FlydocsClient` wraps this class -- there
is no separate sync implementation. Adding an endpoint means adding
one method here.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter

from flydocs_sdk._transport import build_headers, decode_problem_detail, map_transport_error
from flydocs_sdk.errors import FlydocsClientError
from flydocs_sdk.models import (
    ExtractionRequest,
    ExtractionResult,
    JobListResponse,
    JobResult,
    JobStatusResponse,
    SubmitJobRequest,
    SubmitJobResponse,
    VersionInfo,
)

DEFAULT_TIMEOUT_S = 60.0

#: Statuses that mean the worker is done -- success or failure -- and
#: the SDK's :meth:`AsyncFlydocsClient.wait_for_completion` polling
#: loop can stop.
TERMINAL_JOB_STATUSES = frozenset({"SUCCEEDED", "PARTIAL_SUCCEEDED", "FAILED", "CANCELLED"})


class AsyncFlydocsClient:
    """Async client for the flydocs HTTP API.

    Construct once per logical caller; the underlying
    :class:`httpx.AsyncClient` is owned by the SDK and re-used across
    calls. Pass an external :class:`httpx.AsyncClient` if you need to
    share a connection pool with the rest of your app -- the SDK will
    not close transports it did not create.

        async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
            result = await flydocs.extract(ExtractionRequest(...))
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        default_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_headers = dict(default_headers or {})
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=timeout,
                transport=transport,
            )
            self._owns_http = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncFlydocsClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying transport if the SDK owns it.

        Idempotent. Safe to call from cleanup hooks.
        """
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Identity / health
    # ------------------------------------------------------------------

    async def version(self) -> VersionInfo:
        """``GET /api/v1/version``."""
        data = await self._request_json("GET", "/api/v1/version")
        return VersionInfo.model_validate(data)

    async def health(self, probe: str = "readiness") -> dict[str, Any]:
        """``GET /actuator/health/{probe}``.

        ``probe`` is typically ``readiness`` or ``liveness``. Returns
        the raw actuator JSON since the shape is owned by pyfly, not
        the flydocs DTOs.
        """
        data = await self._request_json("GET", f"/actuator/health/{probe}")
        if not isinstance(data, dict):
            raise FlydocsClientError(f"unexpected /actuator/health/{probe} response: {data!r}")
        return data

    # ------------------------------------------------------------------
    # Sync extraction
    # ------------------------------------------------------------------

    async def validate(self, request: ExtractionRequest | dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v1/extract:validate`` -- dry-run the semantic validator.

        Always returns a dict with ``ok`` / ``error_count`` /
        ``warning_count`` / ``errors`` / ``warnings``. Never raises on
        validation failure -- that is a normal outcome of this
        endpoint.
        """
        payload = _to_jsonable(request)
        data = await self._request_json("POST", "/api/v1/extract:validate", json=payload)
        if not isinstance(data, dict):
            raise FlydocsClientError(f"unexpected validate response: {data!r}")
        return data

    async def extract(
        self,
        request: ExtractionRequest | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """``POST /api/v1/extract`` -- run the full pipeline synchronously.

        Raises :class:`FlydocsHTTPError` (status 408) when the service
        signals an extraction timeout; the caller should fall back to
        :meth:`submit_job` for long-running workloads.
        """
        payload = _to_jsonable(request)
        data = await self._request_json(
            "POST",
            "/api/v1/extract",
            json=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return ExtractionResult.model_validate(data)

    # ------------------------------------------------------------------
    # Async-job lifecycle
    # ------------------------------------------------------------------

    async def submit_job(
        self,
        request: SubmitJobRequest | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SubmitJobResponse:
        """``POST /api/v1/jobs`` -- enqueue an extraction job."""
        payload = _to_jsonable(request)
        data = await self._request_json(
            "POST",
            "/api/v1/jobs",
            json=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return SubmitJobResponse.model_validate(data)

    async def get_job(self, job_id: str) -> JobStatusResponse:
        """``GET /api/v1/jobs/{job_id}`` -- read the current status."""
        data = await self._request_json("GET", f"/api/v1/jobs/{job_id}")
        return JobStatusResponse.model_validate(data)

    async def get_job_result(
        self,
        job_id: str,
        *,
        wait_for_bboxes: bool = False,
        timeout: float = 60.0,
    ) -> JobResult:
        """``GET /api/v1/jobs/{job_id}/result`` -- fetch the result.

        ``wait_for_bboxes=True`` long-polls until the bbox refiner
        finishes or ``timeout`` seconds elapse.
        """
        params = {
            "wait_for_bboxes": "true" if wait_for_bboxes else "false",
            "timeout": str(timeout),
        }
        data = await self._request_json("GET", f"/api/v1/jobs/{job_id}/result", params=params)
        return JobResult.model_validate(data)

    async def list_jobs(
        self,
        *,
        status: list[str] | str | None = None,
        bbox_refine_status: list[str] | str | None = None,
        idempotency_key: str | None = None,
        created_after: datetime | str | None = None,
        created_before: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JobListResponse:
        """``GET /api/v1/jobs`` -- paginated listing with filters.

        ``status`` and ``bbox_refine_status`` accept either a single
        string or a list -- a list is joined with commas to match the
        controller's CSV decoding.
        """
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status is not None:
            params["status"] = ",".join(status) if isinstance(status, list) else status
        if bbox_refine_status is not None:
            params["bbox_refine_status"] = (
                ",".join(bbox_refine_status) if isinstance(bbox_refine_status, list) else bbox_refine_status
            )
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        if created_after is not None:
            params["created_after"] = _to_iso(created_after)
        if created_before is not None:
            params["created_before"] = _to_iso(created_before)
        data = await self._request_json("GET", "/api/v1/jobs", params=params)
        return JobListResponse.model_validate(data)

    async def wait_for_completion(
        self,
        job_id: str,
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ) -> JobStatusResponse:
        """Poll a job until it reaches a terminal status, then return.

        Waits at most ``timeout`` seconds, polling every
        ``poll_interval`` seconds. Returns the final
        :class:`JobStatusResponse` whether the job succeeded or failed
        -- inspect ``.status`` to decide what to do next. Raises
        :class:`TimeoutError` if the deadline elapses before the
        worker finishes.

            async with AsyncFlydocsClient("http://localhost:8400") as flydocs:
                submit = await flydocs.submit_job(req)
                final = await flydocs.wait_for_completion(submit.job_id)
                if final.status == JobStatus.SUCCEEDED:
                    result = await flydocs.get_job_result(submit.job_id)
        """
        import asyncio

        loop = asyncio.get_event_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            status = await self.get_job(job_id)
            if str(status.status) in TERMINAL_JOB_STATUSES:
                return status
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"job {job_id!r} did not reach a terminal status within "
                    f"{timeout}s (last status: {status.status!s})"
                )
            await asyncio.sleep(min(poll_interval, max(remaining, 0.01)))

    async def cancel_job(self, job_id: str) -> JobStatusResponse:
        """``DELETE /api/v1/jobs/{job_id}`` -- cancel a queued job.

        Raises :class:`FlydocsHTTPError(409, code='job_not_cancellable')`
        once the worker has started the job.
        """
        data = await self._request_json("DELETE", f"/api/v1/jobs/{job_id}")
        return JobStatusResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        headers = build_headers(
            extra=self._default_headers,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        try:
            response = await self._http.request(
                method,
                path,
                json=json,
                params=params,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise map_transport_error(exc) from exc
        if response.status_code >= 400:
            raise decode_problem_detail(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise FlydocsClientError(
                f"expected JSON response, got {response.headers.get('content-type', 'unknown')}"
            ) from exc


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _to_jsonable(value: BaseModel | dict[str, Any]) -> Any:
    """Pydantic instance -> dict via JSON serialisation (preserves field aliases)."""
    if isinstance(value, BaseModel):
        # mode="json" makes UUID / datetime serialise to their JSON form
        # instead of leaving them as Python objects, which httpx would
        # then have to handle via its default encoder.
        return value.model_dump(mode="json", by_alias=True)
    return value


def _to_iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ``TypeAdapter`` is only imported above so that downstream type-checkers
# can resolve it when this module is read in isolation; nothing in the
# class body uses it at runtime.
__all__ = ["AsyncFlydocsClient", "DEFAULT_TIMEOUT_S"]
_ = TypeAdapter  # noqa: F841 -- silence unused-import in older linters
