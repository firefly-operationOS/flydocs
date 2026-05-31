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
from flydocs_sdk.errors import FlydocsHttpError, FlydocsTimeoutError

DEFAULT_USER_AGENT = f"flydocs-sdk-python/{__version__}"


def build_headers(
    *,
    extra: dict[str, str] | None,
    api_key: str | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    """Compose the headers a request will go out with.

    ``Accept`` and ``User-Agent`` are always set. ``Authorization`` is
    added when ``api_key`` is non-empty. The client's default headers
    (configured at construction time) layer underneath any per-call
    ``extra`` and the two well-known optional headers, which win.
    """
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra:
        for name, value in extra.items():
            if value:
                headers[name] = value
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id
    return headers


def decode_problem_detail(response: httpx.Response) -> FlydocsHttpError:
    """Turn a non-2xx :class:`httpx.Response` into a typed :class:`FlydocsHttpError`.

    The flydocs ``ExceptionAdvice`` emits an RFC 7807 body with
    ``type`` / ``title`` / ``status`` / ``code`` / ``detail`` /
    ``instance`` / ``extensions`` keys. FastAPI's ``HTTPException``
    wrapper occasionally nests it under ``detail``; we walk both and
    take the first match per field.
    """
    code: str | None = None
    title: str | None = None
    detail: str | None = None
    type_: str | None = None
    instance: str | None = None
    extensions: dict[str, Any] | None = None
    payload: dict[str, Any] = {}
    raw_text = ""
    try:
        raw_text = response.text
    except Exception:  # noqa: BLE001 -- httpx can raise for streaming responses
        raw_text = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        payload = data
        nested = data.get("detail") if isinstance(data.get("detail"), dict) else None
        sources: tuple[dict[str, Any], ...] = (data, nested) if nested else (data,)
        for src in sources:
            if code is None and isinstance(src.get("code"), str):
                code = src["code"]
            if title is None and isinstance(src.get("title"), str):
                title = src["title"]
            if detail is None and isinstance(src.get("detail"), str):
                detail = src["detail"]
            if type_ is None and isinstance(src.get("type"), str):
                type_ = src["type"]
            if instance is None and isinstance(src.get("instance"), str):
                instance = src["instance"]
            if extensions is None and isinstance(src.get("extensions"), dict):
                extensions = src["extensions"]
    return FlydocsHttpError(
        status_code=response.status_code,
        code=code,
        title=title,
        detail=detail,
        type=type_,
        instance=instance,
        extensions=extensions,
        payload=payload,
        raw_text=raw_text,
    )


def map_transport_error(exc: httpx.RequestError) -> Exception:
    """Translate an httpx transport failure into our typed hierarchy."""
    if isinstance(exc, httpx.TimeoutException):
        return FlydocsTimeoutError(str(exc) or "request timed out")
    from flydocs_sdk.errors import FlydocsClientError

    return FlydocsClientError(str(exc) or type(exc).__name__)
