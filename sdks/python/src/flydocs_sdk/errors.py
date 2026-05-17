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

"""Typed exception hierarchy.

Every error the SDK raises subclasses :class:`FlydocsError`. We split
along two axes the caller actually wants to branch on:

* **Transport vs. application** -- ``FlydocsClientError`` (timeouts,
  network) vs. ``FlydocsHTTPError`` (the service answered with a 4xx/5xx).
* **Problem code** -- the RFC 7807-ish ``code`` field that the service's
  ``ExceptionAdvice`` puts on every error response (``extraction_timeout``,
  ``document_too_large``, ``invalid_base64``, ``invalid_request``,
  ``job_not_ready``, ``job_not_cancellable``, ``JOB_NOT_FOUND``, ...).

``FlydocsAPIError`` is an alias for :class:`FlydocsHTTPError` kept for
readability at call-sites where the caller wants to discriminate on the
``code``.
"""

from __future__ import annotations

from typing import Any


class FlydocsError(Exception):
    """Root exception for everything this SDK raises."""


class FlydocsClientError(FlydocsError):
    """Transport-level failure: network unreachable, DNS, connect timeout.

    The HTTP request never completed in a way the service could answer.
    Retrying with backoff is almost always the right move.
    """


class FlydocsTimeoutError(FlydocsClientError):
    """The HTTP request exceeded the configured timeout.

    Distinct from :class:`FlydocsHTTPError(408)` -- the latter is the
    service telling the SDK that the extraction pipeline itself timed
    out and the caller should retry through the async API. This one
    means the HTTP request did not complete on the wire.
    """


class FlydocsHTTPError(FlydocsError):
    """The service returned a non-2xx response.

    Holds the HTTP status, the parsed RFC 7807 problem-detail body when
    one was returned, and the raw response text as a fallback.
    """

    def __init__(
        self,
        status_code: int,
        *,
        code: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        payload: dict[str, Any] | None = None,
        raw_text: str = "",
    ) -> None:
        self.status_code = status_code
        self.code = code or ""
        self.title = title or ""
        self.detail = detail or ""
        self.payload = payload or {}
        self.raw_text = raw_text
        message = f"HTTP {status_code}"
        if self.code:
            message += f" {self.code}"
        if self.detail:
            message += f": {self.detail}"
        elif self.title:
            message += f": {self.title}"
        super().__init__(message)


# Readability alias -- some call-sites prefer the "API" framing.
FlydocsAPIError = FlydocsHTTPError
