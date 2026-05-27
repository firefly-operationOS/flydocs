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

"""Typed exception hierarchy + RFC 7807 :class:`ProblemDetails` model.

Every error the SDK raises subclasses :class:`FlydocsError`. We split
along two axes the caller actually wants to branch on:

* **Transport vs. application** -- :class:`FlydocsClientError` (timeouts,
  network) vs. :class:`FlydocsHttpError` (the service answered with a
  4xx/5xx).
* **Problem code** -- the RFC 7807 ``code`` field on the response body.
  In v1 the server emits ``not_found``, ``not_ready``, ``not_cancellable``,
  ``timeout``, ``file_too_large``, ``unsupported_file``,
  ``validation_failed``, ``invalid_base64``, ``invalid_request``,
  ``encrypted_pdf``, ``office_conversion_failed``,
  ``archive_extraction_failed``, ``image_conversion_failed``,
  ``unauthorized``. The SDK doesn't pin to that set; it just exposes
  whatever the server sends as :attr:`FlydocsHttpError.code`.

:class:`FlydocsHttpError` carries every field of :class:`ProblemDetails`
(``type``, ``title``, ``status``, ``code``, ``detail``, ``instance``,
``extensions``) plus the raw response text for forensic debugging.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import Field as _F  # noqa: N814  -- private alias avoids shadowing


class FlydocsError(Exception):
    """Root exception for everything this SDK raises."""


class FlydocsClientError(FlydocsError):
    """Transport-level failure: network unreachable, DNS, connect failure.

    The HTTP request never completed in a way the service could answer.
    Retrying with backoff is almost always the right move.
    """


class FlydocsTimeoutError(FlydocsClientError):
    """The HTTP request exceeded the configured timeout.

    Distinct from :class:`FlydocsHttpError(408)` -- the latter is the
    service telling the SDK that the extraction pipeline itself timed
    out and the caller should retry through the async API. This one
    means the HTTP request did not complete on the wire.
    """


class ProblemDetails(BaseModel):
    """RFC 7807 ``application/problem+json`` body.

    Mirrors :class:`flydocs.interfaces.dtos.error.ProblemDetails`. The
    SDK ``extra="allow"`` lets callers read any forward-compat fields
    the service might add.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str = "about:blank"
    title: str = ""
    status: int = 0
    detail: str | None = None
    instance: str | None = None
    code: str | None = None
    extensions: dict[str, Any] | None = _F(default=None)


class FlydocsHttpError(FlydocsError):
    """The service returned a non-2xx response.

    Carries the HTTP status, the parsed RFC 7807 problem-detail body
    (when one was returned), and the raw response text as a fallback.

    Exposed attributes mirror :class:`ProblemDetails`:

    * :attr:`status_code` -- the HTTP status (int).
    * :attr:`code` -- the application error code (snake_case string), or empty.
    * :attr:`title` -- short human-readable summary, or empty.
    * :attr:`detail` -- longer human-readable explanation, or empty.
    * :attr:`type` -- problem-type URI, defaulting to ``about:blank``.
    * :attr:`instance` -- problem-instance URI, when the server sets one.
    * :attr:`extensions` -- per-occurrence extension dict, when present.
    * :attr:`payload` -- the full raw decoded JSON body (or ``{}``).
    * :attr:`raw_text` -- the response text (for non-JSON bodies).
    """

    def __init__(
        self,
        status_code: int,
        *,
        code: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        type: str | None = None,
        instance: str | None = None,
        extensions: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        raw_text: str = "",
    ) -> None:
        self.status_code = status_code
        self.code = code or ""
        self.title = title or ""
        self.detail = detail or ""
        self.type = type or "about:blank"
        self.instance = instance or ""
        self.extensions = extensions or {}
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

    def as_problem_details(self) -> ProblemDetails:
        """Return the typed :class:`ProblemDetails` view of this error."""
        return ProblemDetails(
            type=self.type,
            title=self.title,
            status=self.status_code,
            detail=self.detail or None,
            instance=self.instance or None,
            code=self.code or None,
            extensions=self.extensions or None,
        )


# Legacy alias kept so v0 callers' ``except FlydocsHTTPError`` / ``except
# FlydocsAPIError`` lines do not break. The canonical class name in v1 is
# :class:`FlydocsHttpError` (lowercase ``ttp``) to match standard Python
# naming style and the docs.
FlydocsHTTPError = FlydocsHttpError
FlydocsAPIError = FlydocsHttpError
