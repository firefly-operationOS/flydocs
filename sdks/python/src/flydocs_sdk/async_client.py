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

The sync :class:`flydocs_sdk.client.Client` wraps this class -- there
is no separate sync implementation. Adding an endpoint means adding
one method here.

API surface (mirrors the spec §12.1):

* ``client.extract(req)``                          -- POST /api/v1/extract
* ``client.validate(req)``                         -- POST /api/v1/extract:validate
* ``client.extractions.create(req, idempotency_key=...)`` -- POST /api/v1/extractions
* ``client.extractions.list(...)``                 -- GET  /api/v1/extractions
* ``client.extractions.get(id)``                   -- GET  /api/v1/extractions/{id}
* ``client.extractions.get_result(id, wait_for_bboxes=, timeout=)`` -- GET  /api/v1/extractions/{id}/result
* ``client.extractions.cancel(id)``                -- DELETE /api/v1/extractions/{id}
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import TracebackType
from typing import Any, BinaryIO

import httpx
from pydantic import BaseModel

from flydocs_sdk._transport import build_headers, decode_problem_detail, map_transport_error
from flydocs_sdk.errors import FlydocsClientError
from flydocs_sdk.models import (
    EventEnvelope,
    Extraction,
    ExtractionListResponse,
    ExtractionRequest,
    ExtractionResult,
    ExtractionResultEnvelope,
    ExtractionStatus,
    PostProcessingStatus,
    SubmitExtractionRequest,
    ValidationResponse,
    VersionInfo,
)

DEFAULT_TIMEOUT_S = 60.0


class AsyncClient:
    """Async client for the flydocs HTTP API.

    Construct once per logical caller; the underlying
    :class:`httpx.AsyncClient` is owned by the SDK and re-used across
    calls. Pass an external :class:`httpx.AsyncClient` if you need to
    share a connection pool with the rest of your app -- the SDK will
    not close transports it did not create.

        async with AsyncClient("http://localhost:8400") as flydocs:
            result = await flydocs.extract(ExtractionRequest(...))
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        default_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
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
        self._extractions = AsyncExtractionsResource(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying transport if the SDK owns it (idempotent)."""
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Identity / health (kept for operational integrations)
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

    async def validate(self, request: ExtractionRequest | dict[str, Any]) -> ValidationResponse:
        """``POST /api/v1/extract:validate`` -- dry-run the semantic validator.

        Always returns 200 with a :class:`ValidationResponse` body.
        Inspect ``response.ok`` to decide whether to submit.
        """
        payload = _to_jsonable(request)
        data = await self._request_json("POST", "/api/v1/extract:validate", json=payload)
        return ValidationResponse.model_validate(data)

    async def extract(
        self,
        request: ExtractionRequest | dict[str, Any],
        *,
        files: list[BinaryIO] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """``POST /api/v1/extract`` -- run the full pipeline synchronously.

        When ``files`` is provided, the SDK switches to multipart upload:
        the file binaries ride as ``files`` parts, the JSON body (with
        ``files`` removed) rides under the ``request`` part. Otherwise
        the JSON body is posted as ``application/json``.

        Raises :class:`FlydocsHttpError(408, code='timeout')` when the
        service signals an extraction timeout; fall back to
        :meth:`AsyncExtractionsResource.create` for long-running workloads.
        """
        return await self._post_for_result(
            "/api/v1/extract",
            request=request,
            files=files,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            model_cls=ExtractionResult,
        )

    @property
    def extractions(self) -> AsyncExtractionsResource:
        """Sub-resource handle covering the async extraction endpoints."""
        return self._extractions

    # ------------------------------------------------------------------
    # Convenience: webhook deserialization
    # ------------------------------------------------------------------

    @staticmethod
    def parse_event(raw_body: bytes) -> EventEnvelope:
        """Deserialise a raw webhook body into a typed :class:`EventEnvelope`.

        Convenience that pairs with :class:`flydocs_sdk.WebhookVerifier`.
        """
        return EventEnvelope.model_validate_json(raw_body)

    # ------------------------------------------------------------------
    # Polling helper
    # ------------------------------------------------------------------

    async def wait_for_completion(
        self,
        extraction_id: str,
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ) -> Extraction:
        """Poll an extraction until it reaches a terminal status, then return.

        Waits at most ``timeout`` seconds, polling every
        ``poll_interval`` seconds. Returns the final :class:`Extraction`
        whether it succeeded, failed, or was cancelled. Raises
        :class:`TimeoutError` if the deadline elapses while the worker
        is still in flight.

            async with AsyncClient("http://localhost:8400") as flydocs:
                ext = await flydocs.extractions.create(req)
                final = await flydocs.wait_for_completion(ext.id)
                if final.status == ExtractionStatus.SUCCEEDED:
                    envelope = await flydocs.extractions.get_result(ext.id)
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            status = await self.extractions.get(extraction_id)
            if status.status.is_terminal:
                return status
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"extraction {extraction_id!r} did not reach a terminal status within "
                    f"{timeout}s (last status: {status.status!s})"
                )
            await asyncio.sleep(min(poll_interval, max(remaining, 0.01)))

    # ------------------------------------------------------------------
    # Internal: low-level transport
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
            api_key=self._api_key,
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

    async def _request_multipart_json(
        self,
        method: str,
        path: str,
        *,
        json_part: dict[str, Any],
        files: list[BinaryIO],
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        import json as _json

        headers = build_headers(
            extra=self._default_headers,
            api_key=self._api_key,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        multipart: list[tuple[str, tuple[str | None, Any, str]]] = [
            ("request", (None, _json.dumps(json_part), "application/json"))
        ]
        for f in files:
            name = getattr(f, "name", "upload")
            multipart.append(("files", (str(name).rsplit("/", 1)[-1], f, "application/octet-stream")))
        try:
            response = await self._http.request(
                method,
                path,
                files=multipart,
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

    async def _post_for_result(
        self,
        path: str,
        *,
        request: BaseModel | dict[str, Any],
        files: list[BinaryIO] | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        model_cls: type[BaseModel],
    ) -> Any:
        payload = _to_jsonable(request)
        if files:
            # Strip ``files`` from the JSON part so the binaries don't show up
            # twice on the wire; the multipart ``files`` field carries the bytes.
            payload = {k: v for k, v in payload.items() if k != "files"}
            data = await self._request_multipart_json(
                "POST",
                path,
                json_part=payload,
                files=files,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        else:
            data = await self._request_json(
                "POST",
                path,
                json=payload,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        return model_cls.model_validate(data)


# ---------------------------------------------------------------------------
# Sub-resource: extractions
# ---------------------------------------------------------------------------


class AsyncExtractionsResource:
    """Async sub-resource for the ``/api/v1/extractions`` endpoint family."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(
        self,
        request: SubmitExtractionRequest | dict[str, Any],
        *,
        files: list[BinaryIO] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Extraction:
        """``POST /api/v1/extractions`` -- enqueue an extraction.

        Same multipart semantics as :meth:`AsyncClient.extract` when
        ``files`` is non-empty.
        """
        return await self._client._post_for_result(
            "/api/v1/extractions",
            request=request,
            files=files,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            model_cls=Extraction,
        )

    async def get(self, extraction_id: str) -> Extraction:
        """``GET /api/v1/extractions/{id}`` -- read the current status."""
        data = await self._client._request_json("GET", f"/api/v1/extractions/{extraction_id}")
        return Extraction.model_validate(data)

    async def get_result(
        self,
        extraction_id: str,
        *,
        wait_for_bboxes: bool = False,
        timeout: float = 60.0,
    ) -> ExtractionResultEnvelope:
        """``GET /api/v1/extractions/{id}/result`` -- fetch the result envelope.

        ``wait_for_bboxes=True`` long-polls until the bbox refiner
        finishes or ``timeout`` seconds elapse. The server's query
        parameter name is ``wait_for_post_processing`` (the bbox leg is
        the only post-processing case today); the SDK keeps the more
        intuitive ``wait_for_bboxes`` kwarg.
        """
        params = {
            "wait_for_post_processing": "true" if wait_for_bboxes else "false",
            "timeout": str(timeout),
        }
        data = await self._client._request_json(
            "GET",
            f"/api/v1/extractions/{extraction_id}/result",
            params=params,
        )
        return ExtractionResultEnvelope.model_validate(data)

    async def cancel(self, extraction_id: str) -> Extraction:
        """``DELETE /api/v1/extractions/{id}`` -- cancel a queued extraction.

        Raises :class:`FlydocsHttpError(409, code='not_cancellable')`
        once the worker has started the extraction.
        """
        data = await self._client._request_json("DELETE", f"/api/v1/extractions/{extraction_id}")
        return Extraction.model_validate(data)

    async def list(
        self,
        *,
        status: list[ExtractionStatus | str] | ExtractionStatus | str | None = None,
        post_processing_status: (list[PostProcessingStatus | str] | PostProcessingStatus | str | None) = None,
        idempotency_key: str | None = None,
        created_after: datetime | str | None = None,
        created_before: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ExtractionListResponse:
        """``GET /api/v1/extractions`` -- paginated listing with filters.

        ``status`` and ``post_processing_status`` accept either a single
        value or a list; a list is joined with commas to match the
        controller's CSV decoding.
        """
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status is not None:
            params["status"] = _csv(status)
        if post_processing_status is not None:
            params["post_processing_status"] = _csv(post_processing_status)
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        if created_after is not None:
            params["created_after"] = _to_iso(created_after)
        if created_before is not None:
            params["created_before"] = _to_iso(created_before)
        data = await self._client._request_json("GET", "/api/v1/extractions", params=params)
        return ExtractionListResponse.model_validate(data)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _to_jsonable(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Pydantic instance -> dict via JSON serialisation (preserves aliases)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    return dict(value)


def _to_iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _csv(value: list[Any] | Any) -> str:
    if isinstance(value, list | tuple):
        return ",".join(str(v) for v in value)
    return str(value)


__all__ = ["AsyncClient", "AsyncExtractionsResource", "DEFAULT_TIMEOUT_S"]
