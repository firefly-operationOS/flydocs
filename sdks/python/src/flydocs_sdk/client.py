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

"""Synchronous client.

A thin wrapper around :class:`flydocs_sdk.AsyncFlydocsClient` that
drives it with a per-instance asyncio event loop. The async client is
the source of truth for endpoint signatures; this class only delegates
and exposes a sync surface.

Why this design rather than two parallel implementations:

* The endpoint table grows; keeping it in one place means new endpoints
  show up in both APIs the moment they ship.
* httpx already supports both flavours, but each ``httpx.Client``
  carries its own connection pool. Sharing one async client + one
  dedicated loop is cheaper than juggling two transports.

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
from typing import Any

import httpx

from flydocs_sdk.async_client import DEFAULT_TIMEOUT_S, AsyncFlydocsClient
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


class FlydocsClient:
    """Synchronous client over the same endpoint set as :class:`AsyncFlydocsClient`.

        with FlydocsClient("http://localhost:8400") as flydocs:
            print(flydocs.version().version)

    Calling :meth:`close` (or using the context manager) shuts the
    background event loop down cleanly. After ``close()`` the instance
    cannot be reused; create a new one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        default_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._inner = AsyncFlydocsClient(
            base_url,
            timeout=timeout,
            default_headers=default_headers,
            transport=transport,
        )
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> FlydocsClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying transport and tear down the event loop.

        Idempotent. After close the instance must not be used.
        """
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
    # Public API mirror
    # ------------------------------------------------------------------

    def version(self) -> VersionInfo:
        return self._run(self._inner.version())

    def health(self, probe: str = "readiness") -> dict[str, Any]:
        return self._run(self._inner.health(probe))

    def validate(self, request: ExtractionRequest | dict[str, Any]) -> dict[str, Any]:
        return self._run(self._inner.validate(request))

    def extract(
        self,
        request: ExtractionRequest | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        return self._run(
            self._inner.extract(
                request,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        )

    def submit_job(
        self,
        request: SubmitJobRequest | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SubmitJobResponse:
        return self._run(
            self._inner.submit_job(
                request,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        )

    def get_job(self, job_id: str) -> JobStatusResponse:
        return self._run(self._inner.get_job(job_id))

    def get_job_result(
        self,
        job_id: str,
        *,
        wait_for_bboxes: bool = False,
        timeout: float = 60.0,
    ) -> JobResult:
        return self._run(
            self._inner.get_job_result(
                job_id, wait_for_bboxes=wait_for_bboxes, timeout=timeout
            )
        )

    def list_jobs(
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
        return self._run(
            self._inner.list_jobs(
                status=status,
                bbox_refine_status=bbox_refine_status,
                idempotency_key=idempotency_key,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
        )

    def cancel_job(self, job_id: str) -> JobStatusResponse:
        return self._run(self._inner.cancel_job(job_id))

    # ------------------------------------------------------------------
    # Internal: drive coroutines on the dedicated loop
    # ------------------------------------------------------------------

    def _run(self, coro: Any) -> Any:
        if self._closed:
            raise RuntimeError("FlydocsClient is closed; construct a new instance")
        with self._lock:
            return self._loop.run_until_complete(coro)
