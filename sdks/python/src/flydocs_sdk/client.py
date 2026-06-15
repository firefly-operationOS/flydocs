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

"""Synchronous client.

A thin wrapper around :class:`flydocs_sdk.AsyncClient` that drives it
with a per-instance asyncio event loop. The async client is the source
of truth for endpoint signatures; this class only delegates and
exposes a sync surface.

Caveat: instances are not safe to share across threads. Construct one
per thread (or use the async client directly if you are already
inside an event loop).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from datetime import datetime
from types import TracebackType
from typing import Any, BinaryIO

import httpx

from flydocs_sdk.async_client import (
    DEFAULT_TIMEOUT_S,
    AsyncClient,
    AsyncExtractionsResource,
)
from flydocs_sdk.models import (
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


class Client:
    """Synchronous client over the same endpoint set as :class:`AsyncClient`.

        with Client("http://localhost:8080") as flydocs:
            result = flydocs.extract(request)

    Calling :meth:`close` (or using the context manager) shuts the
    background event loop down cleanly. After ``close()`` the instance
    cannot be reused; create a new one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        management_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        default_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._inner = AsyncClient(
            base_url,
            api_key=api_key,
            management_url=management_url,
            timeout=timeout,
            default_headers=default_headers,
            transport=transport,
        )
        self._closed = False
        self._extractions = ExtractionsResource(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying transport and tear down the event loop."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run_until_complete(self._inner.aclose())
        finally:
            self._loop.close()

    def __del__(self) -> None:  # pragma: no cover -- best-effort cleanup
        with contextlib.suppress(Exception):
            self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def version(self) -> VersionInfo:
        """``GET /api/v1/version``."""
        return self._run(self._inner.version())

    def health(self, probe: str = "readiness") -> dict[str, Any]:
        """``GET /actuator/health/{probe}``."""
        return self._run(self._inner.health(probe))

    def validate(self, request: ExtractionRequest | dict[str, Any]) -> ValidationResponse:
        """``POST /api/v1/extract:validate`` -- dry-run the semantic validator."""
        return self._run(self._inner.validate(request))

    def extract(
        self,
        request: ExtractionRequest | dict[str, Any],
        *,
        files: list[BinaryIO] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """``POST /api/v1/extract`` -- run the full pipeline synchronously.

        Pass ``files=[...]`` to switch to multipart upload; see
        :meth:`AsyncClient.extract` for the contract.
        """
        return self._run(
            self._inner.extract(
                request,
                files=files,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        )

    @property
    def extractions(self) -> ExtractionsResource:
        """Sub-resource handle covering the async extraction endpoints."""
        return self._extractions

    def wait_for_completion(
        self,
        extraction_id: str,
        *,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ) -> Extraction:
        """Synchronous wrapper around :meth:`AsyncClient.wait_for_completion`."""
        return self._run(
            self._inner.wait_for_completion(
                extraction_id,
                poll_interval=poll_interval,
                timeout=timeout,
            )
        )

    # ------------------------------------------------------------------
    # Internal: drive coroutines on the dedicated loop
    # ------------------------------------------------------------------

    def _run(self, coro: Any) -> Any:
        if self._closed:
            raise RuntimeError("Client is closed; construct a new instance")
        with self._lock:
            return self._loop.run_until_complete(coro)

    @property
    def _async_extractions(self) -> AsyncExtractionsResource:
        return self._inner.extractions


class ExtractionsResource:
    """Synchronous sub-resource for ``/api/v1/extractions``."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def create(
        self,
        request: SubmitExtractionRequest | dict[str, Any],
        *,
        files: list[BinaryIO] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Extraction:
        return self._client._run(
            self._client._async_extractions.create(
                request,
                files=files,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        )

    def get(self, extraction_id: str) -> Extraction:
        return self._client._run(self._client._async_extractions.get(extraction_id))

    def get_result(
        self,
        extraction_id: str,
        *,
        wait_for_bboxes: bool = False,
        timeout: float = 60.0,
    ) -> ExtractionResultEnvelope:
        return self._client._run(
            self._client._async_extractions.get_result(
                extraction_id,
                wait_for_bboxes=wait_for_bboxes,
                timeout=timeout,
            )
        )

    def cancel(self, extraction_id: str) -> Extraction:
        return self._client._run(self._client._async_extractions.cancel(extraction_id))

    def list(
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
        return self._client._run(
            self._client._async_extractions.list(
                status=status,
                post_processing_status=post_processing_status,
                idempotency_key=idempotency_key,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
        )


__all__ = ["Client", "ExtractionsResource"]
