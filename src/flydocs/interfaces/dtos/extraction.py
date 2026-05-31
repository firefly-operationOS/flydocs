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

"""DTOs for the async extraction lifecycle.

Endpoints:

* ``POST   /api/v1/extractions``              -- submit
* ``GET    /api/v1/extractions``              -- list
* ``GET    /api/v1/extractions/{id}``         -- status
* ``GET    /api/v1/extractions/{id}/result``  -- final result envelope
* ``DELETE /api/v1/extractions/{id}``         -- cancel (only while queued)

The main lifecycle is linear: ``queued -> running -> succeeded | failed |
cancelled``. Post-processing (bbox refinement today, more tomorrow) lives in
the additive :class:`PostProcessing` block with its own
:class:`PostProcessingStatus` lifecycle that does not gate the main status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.extract import ExtractionRequest, ExtractionResult
from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus


class SubmitExtractionRequest(ExtractionRequest):
    """Submit shape: full extraction request plus async-only fields."""

    callback_url: AnyHttpUrl | None = Field(
        default=None,
        description="If set, the worker POSTs an EventEnvelope here on terminal status.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionError(BaseModel):
    """Terminal-state error info for a failed extraction."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class BboxRefinementInfo(BaseModel):
    """Lifecycle info for the bbox-refinement post-processing leg."""

    model_config = ConfigDict(extra="forbid")

    status: PostProcessingStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None


class PostProcessing(BaseModel):
    """Container for post-processing legs attached to a succeeded extraction."""

    model_config = ConfigDict(extra="forbid")

    bbox_refinement: BboxRefinementInfo | None = None


class Extraction(BaseModel):
    """Current state snapshot of an async extraction job."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: ExtractionStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    error: ExtractionError | None = None
    post_processing: PostProcessing | None = None


class ExtractionResultEnvelope(BaseModel):
    """``GET /extractions/{id}/result`` body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    result: ExtractionResult


class ExtractionListQuery(BaseModel):
    """Query parameters for ``GET /api/v1/extractions``."""

    model_config = ConfigDict(extra="forbid")

    statuses: list[ExtractionStatus] = Field(default_factory=list)
    post_processing_statuses: list[PostProcessingStatus] = Field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ExtractionListResponse(BaseModel):
    """Paginated list response."""

    model_config = ConfigDict(extra="forbid")

    items: list[Extraction]
    total: int
    limit: int
    offset: int
