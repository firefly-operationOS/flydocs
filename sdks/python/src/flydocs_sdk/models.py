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

"""Wire-level Pydantic models for the flydocs HTTP API.

The SDK ships its own copies of the request / response shapes rather
than re-exporting :mod:`flydocs.interfaces` from the service package.
Two reasons:

* **Independence.** Installing ``flydocs-sdk`` should not pull in the
  service runtime (pyfly, agentic, SQLAlchemy, FastAPI, ...). Keeping
  models local keeps the dep graph to ``httpx + pydantic``.
* **Forward-compatibility.** Pydantic v2 with ``model_config =
  ConfigDict(extra="allow")`` lets the SDK tolerate new fields the
  service adds before the SDK is upgraded. Callers can still read the
  new fields out of ``model_extra`` even when the SDK has no typed
  attribute for them.

For deeply-nested shapes that vary by caller schema -- e.g. the inside
of ``DocSpec``, ``ExtractedFieldGroup``, ``RuleSpec``,
``Transformation`` -- the SDK keeps them as opaque dicts. Callers that
want fully-typed traversal should depend on the ``flydocs`` service
package directly and use its ``flydocs.interfaces.dtos`` modules; the
SDK is intentionally lighter.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flydocs_sdk.request import DocSpec, ExtractionOptions, RuleSpec

# ---------------------------------------------------------------------------
# Permissive base
# ---------------------------------------------------------------------------


class _WireBase(BaseModel):
    """Common config for every wire model in the SDK.

    * ``extra="allow"`` -- the SDK doesn't have to be updated in
      lockstep with new fields the service starts emitting.
    * ``populate_by_name=True`` -- callers can construct models with
      either Python-style snake_case or the JSON alias the service uses
      (some fields like ``fieldName`` / ``fieldValueFound`` are
      camelCase on the wire).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    """Lifecycle states a job can occupy.

    Mirrors :class:`flydocs.interfaces.enums.job_status.JobStatus`. We
    keep this as :class:`StrEnum` so unknown future values from the
    service still serialise/deserialise as strings instead of failing
    parsing -- :class:`_WireBase` extras pick those up.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCEEDED = "PARTIAL_SUCCEEDED"
    REFINING_BBOXES = "REFINING_BBOXES"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Document input
# ---------------------------------------------------------------------------


class DocumentInput(_WireBase):
    """One input file for an extraction request."""

    filename: str = Field(..., min_length=1)
    content_base64: str = Field(
        ...,
        description=(
            "Base64-encoded document bytes. Accepts a bare base64 string or a "
            "``data:<media-type>;base64,...`` data URL (the prefix is stripped "
            "server-side)."
        ),
    )
    content_type: str | None = None
    document_type: str | None = None

    @field_validator("content_base64")
    @classmethod
    def _strip_data_url_prefix(cls, value: str) -> str:
        if value.startswith("data:") and "," in value:
            return value.split(",", 1)[1]
        return value

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        filename: str,
        content_type: str | None = None,
        document_type: str | None = None,
    ) -> DocumentInput:
        """Build a :class:`DocumentInput` from raw bytes (encodes to base64)."""
        return cls(
            filename=filename,
            content_base64=base64.b64encode(data).decode("ascii"),
            content_type=content_type,
            document_type=document_type,
        )

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        content_type: str | None = None,
        document_type: str | None = None,
    ) -> DocumentInput:
        """Read a file off disk and produce a :class:`DocumentInput`."""
        path = Path(path)
        return cls.from_bytes(
            path.read_bytes(),
            filename=path.name,
            content_type=content_type,
            document_type=document_type,
        )


# ---------------------------------------------------------------------------
# Extraction request / response
# ---------------------------------------------------------------------------


class ExtractionRequest(_WireBase):
    """Request body for ``POST /api/v1/extract`` and ``POST /api/v1/jobs``.

    ``docs`` / ``rules`` / ``options`` accept either the typed models
    from :mod:`flydocs_sdk.request` or plain dicts -- typed instances
    give autocomplete + validation, dicts give forward-compatibility
    against new service-side fields the SDK has not surfaced yet.
    """

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    intention: str = "Extract structured data from the document."
    documents: list[DocumentInput] = Field(..., min_length=1)
    docs: list[DocSpec | dict[str, Any]] = Field(..., min_length=1)
    rules: list[RuleSpec | dict[str, Any]] = Field(default_factory=list)
    options: ExtractionOptions | dict[str, Any] = Field(default_factory=ExtractionOptions)


class ExtractionResult(_WireBase):
    """Response body for ``POST /api/v1/extract`` and the ``result`` field
    of an async job.

    Top-level scalar identity is typed; the inside of each ``documents``
    entry is kept as a permissive dict so the SDK keeps working when
    the service adds new per-field fields.
    """

    request_id: uuid.UUID
    files: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    additional_documents: list[dict[str, Any]] = Field(default_factory=list)
    rule_results: list[dict[str, Any]] = Field(default_factory=list)
    request_transformations: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    latency_ms: int = Field(..., ge=0)
    pipeline_errors: list[dict[str, Any]] = Field(default_factory=list)
    escalation: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Async-job DTOs
# ---------------------------------------------------------------------------


class SubmitJobRequest(_WireBase):
    """Request body for ``POST /api/v1/jobs``.

    A superset of :class:`ExtractionRequest` -- adds the optional
    ``callback_url`` (for webhook delivery on terminal status) and a
    free-form ``metadata`` dict that the service echoes back on the
    webhook payload.
    """

    intention: str = "Extract structured data from the document."
    documents: list[DocumentInput] = Field(..., min_length=1)
    docs: list[DocSpec | dict[str, Any]] = Field(..., min_length=1)
    rules: list[RuleSpec | dict[str, Any]] = Field(default_factory=list)
    options: ExtractionOptions | dict[str, Any] = Field(default_factory=ExtractionOptions)
    callback_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitJobResponse(_WireBase):
    job_id: str
    status: JobStatus
    submitted_at: datetime


class JobStatusResponse(_WireBase):
    job_id: str
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
    bbox_refine_status: str | None = None
    bbox_refine_attempts: int = 0
    bbox_refine_started_at: datetime | None = None
    bbox_refine_finished_at: datetime | None = None
    bbox_refine_error_code: str | None = None
    bbox_refine_error_message: str | None = None


class JobResult(_WireBase):
    """Response body for ``GET /api/v1/jobs/{id}/result``."""

    job_id: str
    result: ExtractionResult


class JobListResponse(_WireBase):
    """Response body for ``GET /api/v1/jobs``."""

    items: list[JobStatusResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Webhook payload
# ---------------------------------------------------------------------------


class JobWebhookPayload(_WireBase):
    """Body the service POSTs to ``callback_url`` on terminal status.

    Signed with HMAC-SHA256 in the ``X-Flydocs-Signature`` header when
    ``FLYDOCS_WEBHOOK_HMAC_SECRET`` is configured on the service. Use
    :class:`flydocs_sdk.WebhookVerifier` to verify the signature.
    """

    event_id: str
    event_type: str = "IDPJobCompleted"
    version: str = "1.0.0"
    job_id: str
    status: JobStatus
    occurred_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 1
    correlation_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    result: ExtractionResult | None = None
    error_code: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class VersionInfo(_WireBase):
    """Response body for ``GET /api/v1/version``."""

    service: str
    version: str
    model: str
    fallback_model: str = ""
    eda_adapter: str
