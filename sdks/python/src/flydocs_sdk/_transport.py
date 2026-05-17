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

"""Internal transport helpers shared between the sync and async clients.

Both clients delegate URL building, header assembly, and response
decoding to functions here so the public client classes stay focused
on the per-endpoint signature and the cross-cutting concerns (error
mapping, timeout, base URL) only have to be expressed once.
"""

from __future__ import annotations

from typing import Any

import httpx

from flydocs_sdk._version import __version__
from flydocs_sdk.errors import FlydocsHTTPError, FlydocsTimeoutError

DEFAULT_USER_AGENT = f"flydocs-sdk-python/{__version__}"


def build_headers(
    *,
    extra: dict[str, str] | None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    """Compose the headers a request will go out with.

    ``Accept`` and ``User-Agent`` are always set. The client's default
    headers (configured at construction time) layer underneath any
    per-call ``extra`` and the two well-known optional headers, which
    win.
    """
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if extra:
        for name, value in extra.items():
            if value:
                headers[name] = value
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id
    return headers


def decode_problem_detail(response: httpx.Response) -> FlydocsHTTPError:
    """Turn a non-2xx :class:`httpx.Response` into a typed :class:`FlydocsHTTPError`.

    The flydocs ``ExceptionAdvice`` always emits an RFC 7807-ish body
    with ``code`` / ``title`` / ``detail`` keys. We try hard to extract
    those, fall back to raw text when the body is not JSON, and never
    let a decode failure mask the underlying HTTP error.
    """
    code: str | None = None
    title: str | None = None
    detail: str | None = None
    payload: dict[str, Any] = {}
    raw_text = ""
    try:
        raw_text = response.text
    except Exception:  # noqa: BLE001 -- httpx can raise here for streaming responses
        raw_text = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        payload = data
        # FastAPI's HTTPException wrapper nests the dict under ``detail``.
        # ExceptionAdvice in flydocs emits ``code`` at the top level OR
        # under ``detail`` -- handle both.
        nested = data.get("detail") if isinstance(data.get("detail"), dict) else None
        sources: tuple[dict[str, Any], ...] = (data, nested) if nested else (data,)
        for src in sources:
            if code is None and isinstance(src.get("code"), str):
                code = src["code"]
            if title is None and isinstance(src.get("title"), str):
                title = src["title"]
            if detail is None and isinstance(src.get("detail"), str):
                detail = src["detail"]
    return FlydocsHTTPError(
        status_code=response.status_code,
        code=code,
        title=title,
        detail=detail,
        payload=payload,
        raw_text=raw_text,
    )


def map_transport_error(exc: httpx.RequestError) -> Exception:
    """Translate an httpx transport failure into our typed hierarchy."""
    if isinstance(exc, httpx.TimeoutException):
        return FlydocsTimeoutError(str(exc) or "request timed out")
    # Importing FlydocsClientError lazily would buy nothing here -- the
    # symbol is already in the import chain via the package init.
    from flydocs_sdk.errors import FlydocsClientError

    return FlydocsClientError(str(exc) or type(exc).__name__)
